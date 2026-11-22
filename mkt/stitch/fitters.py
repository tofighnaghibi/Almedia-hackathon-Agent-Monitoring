"""One fitter per stitching row (mkt/stitch/plan.py). Each reads only the columns it needs (DuckDB scans) and returns
a card body: estimate, held-out comparison, trained_through, assumptions, and whether it was fitted on the target
(Olist) or transferred from another population. Levels always come from Olist; other sources give shapes and ratios.
"""
from __future__ import annotations

import json
import mmap
import pathlib
import re

import duckdb
import numpy as np
import polars as pl

RAW = pathlib.Path.home() / "data/mkt/raw"
CACHE = pathlib.Path.home() / "data/mkt/stitch/cache"
BRIDGES = pathlib.Path.home() / "data/mkt/stitch/bridges"
O = RAW / "olist"


def db(threads: int = 2) -> duckdb.DuckDBPyConnection:
    c = duckdb.connect()
    c.execute(f"set threads={threads}; set memory_limit='6GB'")
    return c


def q(c, sql):
    return c.execute(sql).fetchall()


def halves(name, a, b, tol, rel=True):
    """Held-out comparison of two halves: relative (|a-b|/|a|) or absolute difference against a preregistered tolerance."""
    d = abs(a - b) / abs(a) if rel and a else abs(a - b)
    return {"name": name, "halves": [a, b], "diff": d, "tol": tol, "rel": rel, "pass": bool(d <= tol)}


def out(estimate, heldout, trained_through, target, assumptions=(), bridges=(), status=None):
    return {"estimate": estimate, "heldout": heldout, "trained_through": trained_through, "target": target,
            "assumptions": list(assumptions), "bridges": [str(b) for b in bridges], "status_override": status}


ORD = f"read_csv('{O}/olist_orders_dataset.csv')"
ITEMS = f"read_csv('{O}/olist_order_items_dataset.csv')"
YEAR = "year(order_purchase_timestamp)"


# ------------------------------------------------------------------ L0
def seasonality(c):
    dow = {y: dict(q(c, f"select dayofweek(order_purchase_timestamp), count(*)*1.0/sum(count(*)) over () from {ORD} "
                         f"where {YEAR}={y} group by 1")) for y in (2017, 2018)}
    hol = f"read_csv('{RAW}/holidays/holidays_br_2016_2018.csv')"

    def hol_ratio(y):
        r = q(c, f"""with d as (select cast(order_purchase_timestamp as date) d, count(*) n from {ORD} where {YEAR}={y} group by 1)
                     select avg(n) filter (where d in (select date from {hol} where type='national_holiday')) /
                            avg(n) filter (where d not in (select date from {hol})) from d""")[0][0]
        return float(r)
    bf = q(c, f"""with d as (select cast(order_purchase_timestamp as date) d, count(*) n from {ORD} group by 1)
                  select max(n) filter (where d = date '2017-11-24') / median(n) filter (where d between '2017-10-01' and '2017-12-31') from d""")[0][0]
    h17, h18 = hol_ratio(2017), hol_ratio(2018)
    shape_diff = max(abs(dow[2017][k] - dow[2018].get(k, 0)) for k in dow[2017])
    return out({"weekday_share_2017": dow[2017], "holiday_vs_normal_day": {"2017": h17, "2018": h18}, "black_friday_vs_q4_median": float(bf)},
               [halves("holiday/normal-day order ratio 2017 vs 2018", h17, h18, 0.25),
                halves("max weekday-share difference 2017 vs 2018", 0, shape_diff, 0.02, rel=False)], "2017-12-31", True,
               ["holiday effect is national-level; regional holidays ignored"])


def macro(c):
    est = {}
    for name in ("usd_brl", "selic_target_pct", "ipca_monthly_pct"):
        v = json.loads((RAW / f"bcb/{name}.json").read_text())
        xs = [float(r["valor"]) for r in v]
        est[name] = {"n": len(xs), "first": v[0]["data"], "last": v[-1]["data"], "min": min(xs), "max": max(xs)}
    table = pl.concat([pl.DataFrame(json.loads((RAW / f"bcb/{n}.json").read_text())).with_columns(pl.lit(n).alias("series"))
                       for n in est])
    BRIDGES.mkdir(parents=True, exist_ok=True)
    p = BRIDGES / "L0-macro.parquet"
    table.with_columns(pl.col("data").str.to_date("%d/%m/%Y"), pl.col("valor").cast(pl.Float64)).write_parquet(p)
    return out(est, [], "2018-12-31", True, ["exogenous context joined by date; not fitted"], [p], status="joined")


def strike(c):
    s = json.loads((RAW / "public_record/truckers_strike_2018.json").read_text())
    r = q(c, f"""select avg(n) filter (where d between '{s['start']}' and '{s['effective_end']}'),
                        avg(n) filter (where d between date '{s['start']}' - 28 and date '{s['start']}' - 1)
                 from (select cast(order_purchase_timestamp as date) d, count(*) n from {ORD} group by 1)""")[0]
    late = q(c, f"""select avg(case when order_delivered_customer_date > order_estimated_delivery_date then 1.0 else 0 end)
                    filter (where order_purchase_timestamp between date '{s['start']}' - 7 and '{s['effective_end']}'),
                    avg(case when order_delivered_customer_date > order_estimated_delivery_date then 1.0 else 0 end)
                    filter (where order_purchase_timestamp between date '{s['start']}' - 56 and date '{s['start']}' - 29)
                    from {ORD} where order_status='delivered'""")[0]
    return out({"window": [s["start"], s["effective_end"]], "daily_orders_during_vs_before": r[0] / r[1],
                "late_share_during": late[0], "late_share_before": late[1]},
               [halves("daily orders during vs 4 weeks before (expected drop > 20%)", r[1], r[0], 10.0)], s["formal_end"], True,
               ["one event: an effect size, not a fitted law"], status="evidence only")


# ------------------------------------------------------------------ L1
def buyers(c):
    sh = {y: dict(q(c, f"""select customer_state, count(*)*1.0/sum(count(*)) over () from {ORD} o
                          join read_csv('{O}/olist_customers_dataset.csv') using(customer_id) where {YEAR}={y} group by 1""")) for y in (2017, 2018)}
    d = max(abs(sh[2017][k] - sh[2018].get(k, 0)) for k in sh[2017])
    return out({"region_share_2017": sh[2017]}, [halves("max region-share difference 2017 vs 2018", 0, d, 0.03, rel=False)], "2017-12-31", True)


def sellers(c):
    def qs(y):
        return [float(x) for x in q(c, f"""select quantile_cont(n, [0.5, 0.9, 0.99]) from (select seller_id, count(*) n from {ITEMS} i
                                          join {ORD} o using(order_id) where {YEAR}={y} group by 1)""")[0][0]]
    a, b = qs(2017), qs(2018)
    return out({"items_per_seller_q50_q90_q99_2017": a, "2018": b}, [halves("median items per seller 2017 vs 2018", a[0], b[0], 0.5)],
               "2017-12-31", True, ["2018 is 8 months: per-seller counts are lower by construction (compare shape, not level)"])


def products(c):
    sh = {y: dict(q(c, f"""select coalesce(product_category_name,'unknown'), count(*)*1.0/sum(count(*)) over () from {ITEMS} i
                          join {ORD} o using(order_id) join read_csv('{O}/olist_products_dataset.csv') using(product_id) where {YEAR}={y} group by 1"""))
          for y in (2017, 2018)}
    d = max(abs(sh[2017][k] - sh[2018].get(k, 0)) for k in sh[2017])
    # bridge: ESCI US products with an Amazon category (memory-mapped scan of the 1.2 GB category map, cached)
    cat = esci_categories(c)
    n = q(c, f"select count(*), count(distinct category) from '{cat}'")[0]
    top = q(c, f"select category, count(*) from '{cat}' group by 1 order by 2 desc limit 8")
    return out({"category_share_2017_top": dict(sorted(sh[2017].items(), key=lambda kv: -kv[1])[:10]),
                "bridge_esci_products_with_category": n[0], "bridge_categories": n[1], "bridge_top": top},
               [halves("max category-share difference 2017 vs 2018", 0, d, 0.03, rel=False)], "2017-12-31", True,
               ["titles are bridged from Amazon ESCI by mapped category: a labelled stand-in, not Olist's real titles"], [cat])


def esci_categories(c) -> pathlib.Path:
    p = CACHE / "esci_us_categories.parquet"
    if p.exists():
        return p
    CACHE.mkdir(parents=True, exist_ok=True)
    ids = {r[0] for r in q(c, f"select distinct product_id from '{RAW}/esci/shopping_queries_dataset_examples.parquet' where product_locale='us'")}
    rx = re.compile(rb'"([A-Z0-9]{10})": "([^"]+)"')
    rows = []
    with open(RAW / "reviews2023/asin2category.json", "rb") as f, mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ) as m:
        for mt in rx.finditer(m):
            a = mt.group(1).decode()
            if a in ids:
                rows.append((a, mt.group(2).decode()))
    pl.DataFrame(rows, schema=["product_id", "category"], orient="row").write_parquet(p)
    return p


def devices(c):
    from . import cards
    a, b = cards.dv1(), cards.dv2(c)
    return out({"statcounter": a["estimate"], "ga": b["estimate"]}, [{"name": k, **v} for k, v in b["heldout"].items()],
               b["trained_through"], False, b["assumptions"], status=b["status"])


# ------------------------------------------------------------------ L2
def prices(c):
    olist = q(c, f"select avg(case when k > 1 then 1.0 else 0 end) from (select product_id, count(distinct price) k from {ITEMS} group by 1)")[0][0]
    m5 = f"read_csv('{RAW}/m5/sell_prices.csv')"

    def change(lo, hi):
        return q(c, f"""with x as (select item_id, store_id, wm_yr_wk, sell_price,
                        lag(sell_price) over (partition by item_id, store_id order by wm_yr_wk) p0 from {m5} where wm_yr_wk between {lo} and {hi})
                        select avg(case when sell_price <> p0 then 1.0 else 0 end), median(abs(sell_price/p0 - 1)) filter (where sell_price <> p0)
                        from x where p0 is not null""")[0]
    lo, hi = q(c, f"select min(wm_yr_wk), max(wm_yr_wk) from {m5}")[0]
    mid = (lo + hi) // 2
    a, b = change(lo, mid), change(mid, hi)
    return out({"olist_products_sold_at_several_prices": olist, "m5_weekly_change_prob": [a[0], b[0]], "m5_median_change_size": [a[1], b[1]]},
               [halves("M5 weekly price-change probability, first vs second half", a[0], b[0], 0.3)], "2016-06-19", False,
               ["change frequency and size transfer; price levels come from Olist", "no promotion flags exist in any source"])


def offers(c):
    def simul(y):
        return q(c, f"""select avg(case when s > 1 then 1.0 else 0 end) from (select product_id, week(order_purchase_timestamp) w, count(distinct seller_id) s
                       from {ITEMS} join {ORD} using(order_id) where {YEAR}={y} group by 1, 2)""")[0][0]
    a, b = simul(2017), simul(2018)
    lists = q(c, f"select avg(n), median(n) from (select srch_id, count(*) n from read_csv('{RAW}/expedia/train.csv') group by 1)")[0]
    return out({"olist_product_weeks_with_2plus_sellers": [a, b], "expedia_results_per_list_mean_median": lists},
               [halves("simultaneous-seller share 2017 vs 2018", a, b, 0.5)], "2017-12-31", True,
               ["displayed offers per product and their ranking are synthetic (Olist shows sales, not listings)"])


def stock(c):
    p = f"read_csv(['{RAW}/retailrocket/item_properties_part1.csv','{RAW}/retailrocket/item_properties_part2.csv'], union_by_name=true)"
    c.execute(f"create or replace temp table av as select itemid, timestamp t, value='0' out from {p} where property='available'")
    mid = q(c, "select median(t) from av")[0][0]

    def share(cond):
        return q(c, f"select avg(case when out then 1.0 else 0 end) from av where {cond}")[0][0]
    a, b = share(f"t < {mid}"), share(f"t >= {mid}")
    spells = q(c, """with x as (select itemid, t, out, lag(out) over (partition by itemid order by t) p from av)
                     select count(*) filter (where out and (p is null or not p)) from x""")[0][0]
    return out({"stockout_snapshot_share": [a, b], "stockout_starts": spells, "items": q(c, "select count(distinct itemid) from av")[0][0]},
               [halves("stock-out snapshot share, first vs second half", a, b, 0.2)], "2015-09-18", False,
               ["availability snapshots are weekly-ish: spell lengths are interval-censored"])


def search(c):
    ex = f"'{RAW}/esci/shopping_queries_dataset_examples.parquet'"
    mix = lambda cond: dict(q(c, f"select esci_label, count(*)*1.0/sum(count(*)) over () from {ex} where product_locale='us' and {cond} group by 1"))
    a, b = mix("query_id % 2 = 0"), mix("query_id % 2 = 1")
    per_q = q(c, f"select avg(n), median(n) from (select query_id, count(*) n from {ex} where product_locale='us' group by 1)")[0]
    cat = esci_categories(c)
    BRIDGES.mkdir(parents=True, exist_ok=True)
    br = BRIDGES / "L2-search.parquet"
    c.execute(f"""copy (select e.query_id, e.query, e.product_id, e.esci_label, k.category from {ex} e join '{cat}' k using(product_id)
                  where e.product_locale='us') to '{br}' (format parquet)""")
    return out({"label_mix": a, "judged_products_per_query": per_q, "bridge_rows": q(c, f"select count(*) from '{br}'")[0][0]},
               [halves("Exact-label share, even vs odd query ids", a.get("E", 0), b.get("E", 0), 0.05)], "2022-06-01", False,
               ["English queries bridged onto a Portuguese-category catalog: labelled mixed-language stand-in"], [br])


# ------------------------------------------------------------------ L3
def handling(c):
    h = lambda y: q(c, f"""select median(epoch(order_delivered_carrier_date - order_approved_at)/3600) from {ORD}
                           where {YEAR}={y} and order_delivered_carrier_date > order_approved_at""")[0][0]
    a, b = h(2017), h(2018)
    return out({"median_handling_hours": [a, b]}, [halves("median approval-to-carrier hours 2017 vs 2018", a, b, 0.25)], "2017-12-31", True)


def entry(c):
    deals = f"read_csv('{RAW}/olist_funnel/olist_closed_deals_dataset.csv')"
    c.execute(f"""create or replace temp table seller_lag as select d.seller_id, d.won_date, d.business_segment,
                  date_diff('day', d.won_date, min(o.order_purchase_timestamp)) lag_days from {deals} d
                  join {ITEMS} i on i.seller_id = d.seller_id join {ORD} o using(order_id)
                  where o.order_purchase_timestamp >= d.won_date group by 1, 2, 3""")
    mid = q(c, "select median(won_date) from seller_lag")[0][0]
    a = q(c, f"select median(lag_days) from seller_lag where won_date < '{mid}'")[0][0]
    b = q(c, f"select median(lag_days) from seller_lag where won_date >= '{mid}'")[0][0]
    return out({"deals_joined": q(c, "select count(*) from seller_lag")[0][0], "median_days_to_first_sale": [a, b],
                "by_segment_top": q(c, "select business_segment, count(*), median(lag_days) from seller_lag group by 1 order by 2 desc limit 5")},
               [halves("deal-to-first-sale median days, first vs second half of deals", a, b, 0.5)], "2018-08-31", True,
               ["only 380 of 842 deals join to sales; later deals are right-censored"])


def exit_(c):
    def hazard(lo, hi):
        return q(c, f"""with s as (select seller_id, min(order_purchase_timestamp) f, max(order_purchase_timestamp) l, count(*) n
                        from {ITEMS} join {ORD} using(order_id) group by 1)
                        select avg(case when l < date '{hi}' + interval 180 day then 1.0 else 0 end) from s where f between '{lo}' and '{hi}'""")[0][0]
    a, b = hazard("2017-01-01", "2017-06-30"), hazard("2017-07-01", "2017-12-31")
    return out({"share_inactive_within_180d_after_cohort": [a, b]},
               [halves("180-day inactivity share, 2017H1 vs 2017H2 entry cohorts", a, b, 0.3)], "2018-08-31", True,
               ["exit is inferred from silence; the data ends Aug 2018 (censoring)"])


def fees(c):
    return out({"commission_rate_range": [0.10, 0.20]}, [], None, False, ["A-10: no public Olist fee or payout data; range from general sources"],
               status="assumed")


# ------------------------------------------------------------------ L4
def traffic(c):
    lvl = {y: q(c, f"select count(*)*1.0/count(distinct cast(order_purchase_timestamp as date)) from {ORD} where {YEAR}={y}")[0][0]
           for y in (2017, 2018)}
    return out({"orders_per_day": lvl}, [halves("orders per day 2017 vs 2018 (growth expected; tolerance wide)", lvl[2017], lvl[2018], 0.8)],
               "2017-12-31", True, ["visit level is anchored: orders x visits per buyer (TC-RR1)"])


def sessions(c):
    from . import cards
    t = cards.tb1(c)
    return out(t["estimate"], [{"name": k, **v} for k, v in t["heldout"].items()], t["trained_through"], False, t["assumptions"], status=t["status"])


def funnel(c):
    from . import cards
    t = cards.rr1(c)
    return out(t["estimate"], [{"name": k, **v} for k, v in t["heldout"].items()], t["trained_through"], False, t["assumptions"], status=t["status"])


def exposure(c):
    from . import cards
    t = cards.ex1(c)
    return out(t["estimate"], [{"name": k, **v} for k, v in t["heldout"].items()], t["trained_through"], False, t["assumptions"], status=t["status"])


def recs(c):
    r = f"read_csv('{RAW}/open_bandit/random/all.csv')"
    mid = q(c, f"select median(timestamp) from {r}")[0][0]
    a = q(c, f"select avg(click) from {r} where timestamp < '{mid}'")[0][0]
    b = q(c, f"select avg(click) from {r} where timestamp >= '{mid}'")[0][0]
    bts = q(c, f"""select min(propensity_score), median(propensity_score), max(propensity_score), count(*)
                   from read_csv('{RAW}/open_bandit/bts/all.csv', header=true)""")[0]
    return out({"random_ctr_halves": [a, b], "random_propensity": 1 / 80, "bts_propensity_min_median_max_n": bts},
               [halves("random-policy CTR, first vs second half of days", a, b, 0.2)], "2019-12-01", False,
               ["propensities show how a logging policy behaves; the CTR level is ZOZOTOWN fashion, not Olist"])


def repeat(c):
    olist = q(c, f"""select avg(case when n > 1 then 1.0 else 0 end) from (select customer_unique_id, count(*) n from {ORD}
                     join read_csv('{O}/olist_customers_dataset.csv') using(customer_id) group by 1)""")[0][0]
    ins = f"read_csv('{RAW}/instacart/orders.csv')"
    a = q(c, f"select median(days_since_prior_order) from {ins} where user_id % 2 = 0")[0][0]
    b = q(c, f"select median(days_since_prior_order) from {ins} where user_id % 2 = 1")[0][0]
    return out({"olist_repeat_buyer_share": olist, "instacart_median_gap_days": [a, b]},
               [halves("Instacart median inter-order gap, even vs odd users", a, b, 0.15)], "2017-12-31", False,
               ["groceries repeat far more often than Olist goods: only the gap SHAPE transfers, scaled to Olist's 3.1% repeat share"])


def ads(c):
    cr = f"read_csv('{RAW}/criteo/criteo-research-uplift-v2.1.csv.gz')"

    def lift(cond):
        r = q(c, f"select avg(visit) filter (where treatment=1), avg(visit) filter (where treatment=0) from {cr} where {cond}")[0]
        return r[0] - r[1]
    a, b = lift("hash(f0, f1) % 2 = 0"), lift("hash(f0, f1) % 2 = 1")
    arms = q(c, f"select treatment, count(*), avg(exposure), avg(visit), avg(conversion) from {cr} group by 1 order by 1")
    hill = q(c, f"select segment, count(*), avg(visit), avg(conversion) from read_csv('{RAW}/hillstrom/hillstrom.csv') group by 1 order by 1")
    return out({"criteo_arms": arms, "criteo_visit_uplift_halves": [a, b], "hillstrom_arms": hill},
               [halves("Criteo visit uplift, random halves", a, b, 0.2)], "2021-01-01", False,
               ["only treated users can be exposed (one-sided non-compliance)", "lift is relative; levels come from Olist sessions"])


# ------------------------------------------------------------------ L5
def orders(c):
    pay = f"read_csv('{O}/olist_order_payments_dataset.csv')"
    ipo = {y: q(c, f"select avg(n) from (select i.order_id, count(*) n from {ITEMS} i join {ORD} o using(order_id) where {YEAR}={y} group by 1)")[0][0]
           for y in (2017, 2018)}
    mix = dict(q(c, f"select payment_type, count(*)*1.0/sum(count(*)) over () from {pay} group by 1"))
    return out({"items_per_order": ipo, "payment_type_share": mix}, [halves("items per order 2017 vs 2018", ipo[2017], ipo[2018], 0.1)],
               "2017-12-31", True)


def delivery(c):
    late = {y: q(c, f"""select avg(case when order_delivered_customer_date > order_estimated_delivery_date then 1.0 else 0 end),
                         median(date_diff('day', order_purchase_timestamp, order_estimated_delivery_date)) from {ORD}
                         where {YEAR}={y} and order_status='delivered'""")[0] for y in (2017, 2018)}
    return out({"late_share_promise_days": late}, [halves("late share 2017 vs 2018 (2018 includes the strike)", late[2017][0], late[2018][0], 0.6)],
               "2017-12-31", True, ["the chain uses the full fitted delivery model (phase 3); this card is its summary"])


def returns(c):
    p = CACHE / "uci.parquet"
    if not p.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        sheets = pl.read_excel(RAW / "uci/online_retail_II.xlsx", sheet_id=0, engine="calamine")
        pl.concat([s.with_columns(pl.col("Invoice").cast(pl.Utf8), pl.col("InvoiceDate").cast(pl.Datetime))
                   .select("Invoice", "Quantity", "InvoiceDate", "Price", "Customer ID") for s in sheets.values()]).write_parquet(p)
    r = lambda y: q(c, f"select avg(case when Invoice like 'C%' then 1.0 else 0 end) from '{p}' where year(InvoiceDate)={y}")[0][0]
    a, b = r(2010), r(2011)
    canc = q(c, f"select avg(case when order_status in ('canceled','unavailable') then 1.0 else 0 end) from {ORD}")[0][0]
    return out({"olist_canceled_or_unavailable_share": canc, "uci_return_line_share": [a, b]},
               [halves("UCI return-line share 2010 vs 2011", a, b, 0.3)], "2011-12-09", False,
               ["Olist has no returns: return rate shape from a UK gift wholesaler (labelled transfer)"])


# ------------------------------------------------------------------ L6
REV = f"read_csv('{O}/olist_order_reviews_dataset.csv', quote='\"', escape='\"', ignore_errors=true)"


def score(c):
    gap = {y: q(c, f"""select avg(review_score) filter (where order_delivered_customer_date <= order_estimated_delivery_date)
                        - avg(review_score) filter (where order_delivered_customer_date > order_estimated_delivery_date)
                        from {ORD} join {REV} using(order_id) where {YEAR}={y}""")[0][0] for y in (2017, 2018)}
    return out({"score_gap_on_time_minus_late": gap}, [halves("score gap 2017 vs 2018", gap[2017], gap[2018], 0.25)], "2017-12-31", True)


def text(c):
    sh = {y: dict(q(c, f"""select review_score, avg(case when review_comment_message is not null then 1.0 else 0 end)
                         from {ORD} join {REV} using(order_id) where {YEAR}={y} group by 1""")) for y in (2017, 2018)}
    d = max(abs(sh[2017][k] - sh[2018].get(k, 0)) for k in sh[2017])
    return out({"text_share_by_score_2017": sh[2017]}, [halves("max text-share difference by score 2017 vs 2018", 0, d, 0.1, rel=False)],
               "2017-12-31", True, ["texts stay in ~/data (not in the repo); pooled by score x delivery outcome"])


def timing(c):
    lag = {y: q(c, f"""select median(epoch(review_answer_timestamp - review_creation_date)/3600) from {ORD} join {REV} using(order_id)
                        where {YEAR}={y}""")[0][0] for y in (2017, 2018)}
    return out({"answer_lag_median_hours": lag}, [halves("answer-lag median 2017 vs 2018", lag[2017], lag[2018], 0.3)], "2017-12-31", True)


def mnar(c):
    def load(name):
        return np.loadtxt(RAW / f"coat/coat/{name}.ascii")
    tr, te = load("train"), load("test")
    rng = np.random.default_rng(5)
    users = rng.permutation(tr.shape[0])
    h = [float(tr[u][tr[u] > 0].mean() - te[u][te[u] > 0].mean()) for u in (users[: len(users) // 2], users[len(users) // 2:])]
    pres = q(c, f"""select avg(case when r.order_id is not null then 1.0 else 0 end) from {ORD} o left join (select distinct order_id from {REV}) r
                    using(order_id) where order_status='delivered'""")[0][0]
    return out({"olist_review_presence": pres, "coat_self_selected_minus_random_mean": h},
               [halves("Coat self-selection gap, random user halves", h[0], h[1], 0.3)], "2017-12-31", False,
               ["Olist ~99% reviewed (survey-driven): the selection SHAPE comes from Coat (ratings, not purchases)"])


def fraud(c):
    from scipy.io import loadmat
    out_ = {}
    for name, rel, f in (("yelpchi", "net_rur", "YelpChi.mat"), ("amazon_fraud", "net_upu", "Amazon.mat")):
        m = loadmat(RAW / name / f)
        lab = np.asarray(m["label"]).ravel()
        deg = np.asarray(m[rel].sum(axis=1)).ravel()
        rng = np.random.default_rng(7)
        idx = rng.permutation(len(lab))
        parts = [idx[: len(idx) // 2], idx[len(idx) // 2:]]
        ratio = [float(deg[p][lab[p] == 1].mean() / max(deg[p][lab[p] == 0].mean(), 1e-9)) for p in parts]
        out_[name] = {"fraud_share": float(lab.mean()), "degree_ratio_fraud_vs_genuine_halves": ratio}
    y = out_["yelpchi"]["degree_ratio_fraud_vs_genuine_halves"]
    return out(out_, [halves("YelpChi fraud/genuine degree ratio, random halves", y[0], y[1], 0.2)], None, False,
               ["no timestamps in either graph: campaign timing is an assumption", "true labels stay privileged (L8)"])


# ------------------------------------------------------------------ L7, L9
def telemetry(c):
    w = json.loads((RAW / "nab_loghub/NAB/labels/combined_windows.json").read_text())
    real = [k for k in w if k.startswith("real")]
    art = [k for k in w if k.startswith("artificial")]
    n = lambda keys: float(np.mean([len(w[k]) for k in keys]))
    m0 = pathlib.Path(__file__).resolve().parents[2] / "docs/experiments/m0_results.json"
    e1 = json.loads(m0.read_text())["E1"] if m0.exists() else {}
    return out({"nab_windows_per_series_real_vs_artificial": [n(real), n(art)], "series": len(w),
                "astronomy_checkouts_per_s": e1.get("checkouts_per_s")},
               [halves("NAB anomaly windows per series, real vs artificial", n(real), n(art), 1.0)], None, False,
               ["live telemetry comes from the real shop (M5); NAB only shapes synthetic fallbacks"], status="partially supported (live + shape)")


def experiments(c):
    r = f"read_csv('{RAW}/open_bandit/random/all.csv')"
    items = q(c, f"select count(*), stddev(n)/avg(n) from (select item_id, count(*) n from {r} group by 1)")[0]
    cr = q(c, f"select avg(treatment) from read_csv('{RAW}/criteo/criteo-research-uplift-v2.1.csv.gz')")[0][0]
    return out({"open_bandit_items_and_cv": items, "criteo_treated_share": cr},
               [halves("Criteo treated share vs design 0.85", 0.85, cr, 0.01, rel=False)], None, False,
               ["assignment designs transfer as StudyDesign shapes; outcomes come from the simulator"])


def logging(c):
    ev = f"read_csv('{RAW}/retailrocket/events.csv')"
    mid = q(c, f"select median(timestamp) from {ev}")[0][0]

    def noview(cond):
        return q(c, f"""with b as (select visitorid, itemid, min(timestamp) t from {ev} where event='transaction' and {cond} group by 1, 2),
                         v as (select visitorid, itemid, min(timestamp) t from {ev} where event='view' group by 1, 2)
                         select avg(case when v.t is null or v.t > b.t then 1.0 else 0 end) from b left join v using(visitorid, itemid)""")[0][0]
    a, b = noview(f"timestamp < {mid}"), noview(f"timestamp >= {mid}")
    ooo = q(c, f"select count(*) from {ORD} where order_delivered_carrier_date < order_approved_at")[0][0]
    return out({"purchases_without_prior_view": [a, b], "olist_handover_before_approval": ooo},
               [halves("purchases-without-view share, first vs second half", a, b, 0.25)], "2015-09-18", False,
               ["delays and correction rates stay assumptions (A-11, A-12); these numbers are evidence that gaps exist"],
               status="evidence only")
