"""Mechanism calibration (docs §6-§7).

Fits simulator parameters from canonical Olist tables, using only purchases before
`fit_until`; the rest is held out for fidelity checks (validate.stats). Funnel rates are
transferred from Retailrocket as *relative* structure and re-anchored on Olist order volume.

Every parameter group records its provenance class and the assumptions it relies on
(coverage/assumptions.toml).
"""
from __future__ import annotations

import math
import pathlib

import duckdb
import numpy as np

LATE_BUCKETS = ["on_time", "late_1_3", "late_4_10", "late_gt10"]
BUCKET_SQL = """case when late_days<=0 then 'on_time' when late_days<=3 then 'late_1_3'
                     when late_days<=10 then 'late_4_10' else 'late_gt10' end"""


def _lognorm(c, sql: str) -> dict:
    mu, sd, n = c.execute(f"select avg(ln(x)), stddev(ln(x)), count(*) from ({sql}) where x>0").fetchone()
    return {"mu": float(mu), "sigma": float(sd), "n": int(n)}


def fit_olist(canon: pathlib.Path, fit_until: str) -> dict:
    c = duckdb.connect()
    for t in ["orders", "order_items", "payments", "reviews", "sellers", "products"]:
        c.execute(f"create view {t} as select * from '{canon}/{t}.parquet'")
    c.execute(f"create view fo as select * from orders where purchased_at < '{fit_until}' and purchased_at >= '2017-01-01'")
    c.execute("""create view fi as select i.*, o.purchased_at, o.buyer_region, s.region seller_region, p.category
             from order_items i join fo o using(order_id) join sellers s using(seller_id) join products p using(product_id)""")
    P: dict = {"_provenance": {}}

    # calendar profiles (hour of day, day of week) of purchases
    hour = np.array([r[1] for r in c.execute("select hour(purchased_at) h, count(*) from fo group by 1 order by 1").fetchall()], float)
    dow = np.array([r[1] for r in c.execute("select isodow(purchased_at) d, count(*) from fo group by 1 order by 1").fetchall()], float)
    P["calendar"] = {"hour": (hour / hour.sum()).tolist(), "dow_mon_first": (dow / dow.sum()).tolist()}
    # level: median orders/day over the last 3 fitted months (the series grows in 2017)
    P["daily_orders"] = float(c.execute(f"""select median(n) from (select cast(purchased_at as date) d, count(*) n from fo
                                       where purchased_at >= date '{fit_until}' - interval 90 day group by 1)""").fetchone()[0])
    P["_provenance"]["calendar"] = P["_provenance"]["daily_orders"] = "calibrated:olist"

    # categories: share of items, price lognormal, freight/price ratio
    cats = c.execute("""select category, count(*) n, avg(ln(price)) mu, stddev(ln(price)) sd, median(freight/price) fr
                    from fi where price>0 group by 1 order by n desc""").fetchall()
    top, rest = cats[:20], cats[20:]
    tot = sum(r[1] for r in cats)
    P["categories"] = [{"name": r[0], "share": r[1] / tot, "price_mu": r[2], "price_sigma": r[3] or 0.5, "freight_ratio": r[4]}
                       for r in top]
    if rest:
        n = sum(r[1] for r in rest)
        P["categories"].append({"name": "other", "share": n / tot,
                                "price_mu": sum(r[2] * r[1] for r in rest) / n,
                                "price_sigma": float(np.nanmean([r[3] or 0.5 for r in rest])),
                                "freight_ratio": float(np.median([r[4] for r in rest]))})
    P["_provenance"]["categories"] = "calibrated:olist"

    # sellers: size (items sold) distribution, region shares, handling-time hierarchy
    P["seller_sizes"] = [int(r[0]) for r in c.execute("select count(*) from fi group by seller_id").fetchall()]
    P["seller_regions"] = {r[0]: r[1] for r in c.execute(
        "select seller_region, count(distinct seller_id)/(select count(distinct seller_id) from fi) from fi group by 1").fetchall()}
    P["buyer_regions"] = {r[0]: r[1] for r in c.execute(
        "select buyer_region, count(*)/(select count(*) from fo) from fo group by 1").fetchall()}
    # handling = approved -> handed to carrier (hours); partial pooling across sellers
    c.execute("""create view h as select i.seller_id, date_diff('minute', o.approved_at, o.shipped_at)/60.0 x
             from fo o join (select distinct order_id, seller_id from order_items) i using(order_id)
             where o.shipped_at > o.approved_at""")
    g = _lognorm(c, "select x from h")
    between = c.execute("select stddev(m) from (select avg(ln(x)) m from h where x>0 group by seller_id having count(*)>=5)").fetchone()[0]
    P["handling_hours"] = {**g, "between_seller_sd": float(between or 0.3)}
    P["approval_hours"] = _lognorm(c, "select date_diff('minute', purchased_at, approved_at)/60.0 x from fo")
    # transit (carrier -> delivered), days, same vs different state
    c.execute("""create view tr as select (o.buyer_region = s.region) same_state,
             date_diff('minute', o.shipped_at, o.delivered_at)/1440.0 x,
             date_diff('day', o.purchased_at, o.promised_at) promise
             from fo o join (select order_id, min(seller_id) seller_id from order_items group by 1) i using(order_id)
             join sellers s using(seller_id) where o.delivered_at is not null and o.shipped_at is not null""")
    P["transit_days"] = {k: _lognorm(c, f"select x from tr where same_state = {v}") for k, v in [("same", "true"), ("diff", "false")]}
    P["promise_days"] = {k: dict(zip(["mean", "sd"], map(float, c.execute(
        f"select avg(promise), stddev(promise) from tr where same_state = {v}").fetchone()))) for k, v in [("same", "true"), ("diff", "false")]}
    P["_provenance"]["fulfilment"] = "calibrated:olist (M-08)"

    # review score | lateness bucket (M-09), review presence/text/time (M-10)
    c.execute(f"""create view rl as select r.score, r.has_text, r.created_at, o.delivered_at, o.promised_at,
              date_diff('day', o.promised_at, o.delivered_at) late_days
              from fo o join reviews r using(order_id) where o.delivered_at is not null""")
    cpt = {}
    for b in LATE_BUCKETS:
        rows = dict(c.execute(f"select score, count(*) from rl where {BUCKET_SQL} = '{b}' group by 1").fetchall())
        n = sum(rows.values())
        cpt[b] = [rows.get(s, 0) / n for s in range(1, 6)]
    P["score_given_late"] = cpt
    delivered = c.execute("select count(*) from fo where delivered_at is not null").fetchone()[0]
    reviewed = c.execute("select count(distinct order_id) from fo join reviews using(order_id) where delivered_at is not null").fetchone()[0]
    P["review"] = {
        "p_review": reviewed / delivered,
        "p_text": float(c.execute("select avg(has_text::int) from rl").fetchone()[0]),
        # offset of review creation from delivery, in days (empirical quantiles; includes negatives)
        "offset_days_q": [float(x) for x in c.execute(
            "select quantile_cont(date_diff('hour', delivered_at, created_at)/24.0, [0.01,0.05,0.1,0.25,0.5,0.75,0.9,0.95,0.99]) from rl").fetchone()[0]],
    }
    P["_provenance"]["reviews"] = "calibrated:olist (M-09, M-10)"

    # competition structure (M-05): share of products with >1 seller
    P["multi_seller_product_share"] = float(c.execute(
        "select avg((s>1)::int) from (select product_id, count(distinct seller_id) s from order_items group by 1)").fetchone()[0])
    P["payment_types"] = dict(c.execute(
        "select payment_type, count(*)/(select count(*) from payments) from payments group by 1").fetchall())
    P["_provenance"]["competition"] = "calibrated:olist (structure only; choice model assumed A-05)"
    return _clean(P)


def holdout_stats(canon: pathlib.Path, fit_until: str) -> dict:
    """Same statistics on the held-out period, for validate.stats."""
    c = duckdb.connect()
    for t in ["orders", "order_items", "reviews", "sellers"]:
        c.execute(f"create view {t} as select * from '{canon}/{t}.parquet'")
    c.execute(f"""create view ho as select * from orders where purchased_at >= '{fit_until}' and purchased_at < '2018-09-01'""")
    late = c.execute(f"""select {BUCKET_SQL} b, count(*) n, avg(r.score) s from (select *, date_diff('day', promised_at, delivered_at) late_days
                     from ho where delivered_at is not null) o join reviews r using(order_id) group by 1""").fetchall()
    return {
        "delivery_days_q": [float(x) for x in c.execute(
            "select quantile_cont(date_diff('hour', purchased_at, delivered_at)/24.0, [0.1,0.25,0.5,0.75,0.9]) from ho where delivered_at is not null").fetchone()[0]],
        "late_share": float(c.execute("select avg((delivered_at>promised_at)::int) from ho where delivered_at is not null").fetchone()[0]),
        "score_by_bucket": {b: {"n": n, "mean": s} for b, n, s in late},
        "hour_profile": [r[1] for r in c.execute("select hour(purchased_at), count(*) from ho group by 1 order by 1").fetchall()],
    }


TRANSFER_DEFAULTS = {  # used only when Retailrocket is not registered (assumption A-02b)
    "sessions": None, "views_mean": 2.0, "p_cart": 0.02, "p_buy_given_cart": 0.3, "p_buy_direct": 0.002, "p_buy": 0.008,
}


def fit(canon_olist: pathlib.Path, fit_until: str, funnel: dict | None) -> dict:
    P = fit_olist(canon_olist, fit_until)
    P["funnel"] = funnel or TRANSFER_DEFAULTS
    P["_provenance"]["funnel"] = "transferred:retailrocket (A-02)" if funnel else "assumed (A-02b)"
    # assumed mechanism parameters (each refers to coverage/assumptions.toml)
    P["assumed"] = {
        "price_elasticity": -1.5,            # A-04
        "choice_price_beta": 2.0,            # A-05: offer-choice logit on log price
        "choice_rating_beta": 0.8,           # A-05: on (rating - 4)
        "mobile_share": 0.6,                 # A-06
        "checkout_error_base": 0.005,        # A-07
        "latency_p95_ms_base": 350.0,        # A-07
        "restock_lead_days": {"mu": math.log(7), "sigma": 0.5},   # A-08
        "stock_cover_days": 21,              # A-08: order-up-to level in days of expected demand
        "reorder_point_days": 5,             # A-08
        "defect_prob": 0.03,                 # A-09: latent product quality; defect -> score 1
        "commission_rate": 0.16,             # A-10
    }
    P["_provenance"]["assumed"] = "assumed (A-04..A-10)"
    return P


def _clean(x):
    if isinstance(x, dict):
        return {str(k): _clean(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_clean(v) for v in x]
    if isinstance(x, (np.floating, float)):
        return None if (isinstance(x, float) and math.isnan(x)) else float(x)
    if isinstance(x, np.integer):
        return int(x)
    return x
