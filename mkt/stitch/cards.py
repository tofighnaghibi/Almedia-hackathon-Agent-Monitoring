"""P1-2 transport cards (docs/p0/P1_2_SESSION_LAYER_PREREG.md §2): fitted on source data only.

Each card: estimate (+95% CI), a quantile grid or pmf the simulator samples from, trained_through,
a held-out check (H1-H5, tolerances preregistered) and a status. Levels never come from here.
"""
from __future__ import annotations

import json
import pathlib
from datetime import datetime, timezone

import duckdb
import numpy as np

RAW = pathlib.Path.home() / "data/mkt/raw"
OUT = pathlib.Path(__file__).resolve().parents[2] / "docs/p0/cards"
GAP_S = 1800          # session cut: 30 min idle
QS = np.linspace(0, 1, 101)


def _ci_mean(x: np.ndarray, rng: np.random.Generator, reps: int = 400) -> list[float]:
    bs = [x[rng.integers(0, len(x), len(x))].mean() for _ in range(reps)]
    return [float(np.quantile(bs, 0.025)), float(np.quantile(bs, 0.975))]


def _card(cid, **kw) -> dict:
    kw = {"id": cid, "fitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds"), **kw}
    (OUT / f"{cid}.json").write_text(json.dumps(kw, indent=1, default=float))
    return kw


def _status(ok: bool) -> str:
    return "partially supported (transferred shape; held-out stable)" if ok else "unsupported transfer (held-out unstable; sensitivity = both halves)"


# ------------------------------------------------------------------ TC-RR1 Retailrocket sessions
def rr1(c) -> dict:
    c.execute(f"""
      create or replace temp table rr_s as
      with e as (select visitorid v, timestamp/1000 t, event from read_csv('{RAW}/retailrocket/events.csv')),
      g as (select *, case when t - lag(t) over (partition by v order by t) > {GAP_S} or lag(t) over (partition by v order by t) is null
                           then 1 else 0 end new from e),
      s as (select *, sum(new) over (partition by v order by t rows unbounded preceding) sid from g)
      select v, sid, min(t) t0, count(*) filter (event='view') views, bool_or(event='addtocart') cart,
             bool_or(event='transaction') buy from s group by v, sid""")
    rows = c.execute("select views, cart, buy, t0 from rr_s").fetchnumpy()
    views, cart, buy, t0 = rows["views"].astype(int), rows["cart"].astype(bool), rows["buy"].astype(bool), rows["t0"]
    rng = np.random.default_rng(0)
    nb, b = ~buy, buy

    def pmf(x):
        x = np.minimum(x, 1000)                       # cap far in the tail: the mean must survive (F1)
        return (np.bincount(x) / len(x)).tolist()

    half = t0 < np.median(t0)
    est = {"views_mean_buying": float(views[b].mean()), "views_mean_nonbuying": float(views[nb].mean()),
           "cart_share_nonbuying": float(cart[nb].mean()), "cart_share_buying": float(cart[b].mean()),
           "k_nonbuying_per_buying": float(nb.sum() / b.sum())}
    h1 = {p: float(views[m & sel].mean()) for p, m in (("first", half), ("second", ~half)) for sel in [nb]}
    h1_rel = abs(h1["first"] - h1["second"]) / h1["first"]
    h2 = {p: float(cart[nb & m].mean()) for p, m in (("first", half), ("second", ~half))}
    h2_abs = abs(h2["first"] - h2["second"])
    ok = h1_rel <= 0.10 and h2_abs <= 0.02
    return _card("TC-RR1", source="Retailrocket events (30-min idle sessions)", source_population="visitors of one e-commerce site, May-Sep 2015",
                 target_population="synthetic Olist-calibrated shoppers, Brazil 2018", estimate=est,
                 ci95={"views_mean_buying": _ci_mean(views[b], rng), "views_mean_nonbuying": _ci_mean(views[nb], rng),
                       "cart_share_nonbuying": _ci_mean(cart[nb].astype(float), rng)},
                 pmf_views_buying=pmf(views[b]), pmf_views_nonbuying=pmf(views[nb]),
                 n_sessions=int(len(views)), n_buying=int(b.sum()),
                 trained_through=datetime.fromtimestamp(float(t0.max()), timezone.utc).date().isoformat(),
                 heldout={"H1 views/session non-buying (rel diff <= 10%)": {"halves": h1, "rel_diff": h1_rel, "pass": h1_rel <= 0.10},
                          "H2 non-buying cart share (abs diff <= 0.02)": {"halves": h2, "abs_diff": h2_abs, "pass": h2_abs <= 0.02}},
                 assumptions=["ratios carry over, levels come from Olist",
                              "k (non-buying sessions per buying session) is site-specific: least transferable; scenarios should vary it"],
                 status=_status(ok))


# ------------------------------------------------------------------ TC-TB1 Taobao lags
def tb1(c) -> dict:
    lo, hi = 1511539200, 1512316800          # 2017-11-25 00:00 .. 2017-12-04 00:00 (UTC+8 dataset days, as documented)
    c.execute(f"""
      create or replace temp table tb as select column0 u, column1 i, column3 typ, column4 ts
      from read_csv('{RAW}/taobao/UserBehavior.csv', header=false) where column4 >= {lo} and column4 < {hi}""")
    c.execute("""
      create or replace temp table tb_b as
      with b as (select u, i, min(ts) bts from tb where typ='buy' group by all),
           p as (select u, i, min(ts) pts from tb where typ='pv' group by all),
           k as (select u, i, max(ts) cts from tb where typ='cart' group by all)
      select b.u, b.i, bts, bts - pts lag_view, case when cts <= bts then bts - cts end lag_cart
      from b left join p on b.u=p.u and b.i=p.i and pts <= bts left join k on b.u=k.u and b.i=k.i""")
    d = c.execute("select bts, lag_view, lag_cart from tb_b").fetchnumpy()
    bts, lv, lcart = d["bts"], d["lag_view"], d["lag_cart"]
    has_v = ~np.ma.getmaskarray(lv)                       # NULL = no prior view of the bought item
    lvv = np.asarray(np.ma.getdata(lv)[has_v], float)
    has_c = ~np.ma.getmaskarray(lcart)                    # NULL = no cart add before the buy
    lcv = np.asarray(np.ma.getdata(lcart)[has_c], float)
    mid = lo + (hi - lo) / 2
    first, second = lvv[np.asarray(bts[has_v]) < mid], lvv[np.asarray(bts[has_v]) >= mid]
    h3 = {"first": float(np.median(first)), "second": float(np.median(second))}
    h3_rel = abs(h3["first"] - h3["second"]) / h3["first"]
    rng = np.random.default_rng(1)
    bs = [np.median(lvv[rng.integers(0, len(lvv), len(lvv))]) for _ in range(200)]
    return _card("TC-TB1", source="Taobao user behaviour (pv/cart/buy)", source_population="Taobao users, 25 Nov - 3 Dec 2017 (9 days)",
                 target_population="synthetic Olist-calibrated shoppers, Brazil 2018",
                 estimate={"share_buys_with_prior_view": float(has_v.mean()), "lag_view_median_s": float(np.median(lvv)),
                           "lag_view_p90_s": float(np.quantile(lvv, 0.9)), "share_lag_over_30min": float((lvv > GAP_S).mean()),
                           "lag_cart_median_s": float(np.median(lcv)), "share_buys_with_cart": float(has_c.mean())},
                 ci95={"lag_view_median_s": [float(np.quantile(bs, 0.025)), float(np.quantile(bs, 0.975))]},
                 q_lag_view_s=np.quantile(lvv, QS).tolist(), q_lag_cart_s=np.quantile(lcv, QS).tolist(),
                 n_buys=int(len(bts)), trained_through="2017-12-03",
                 heldout={"H3 median first-view->buy lag, halves (rel diff <= 25%)": {"halves": h3, "rel_diff": h3_rel, "pass": h3_rel <= 0.25}},
                 assumptions=["lag shape carries over", "truncated by the 9-day window (long lags under-represented)",
                              "H3 halves are confounded by left truncation (early buys have less visible history)",
                              "window includes the run-up to Double 12"],
                 status=_status(h3_rel <= 0.25))


# ------------------------------------------------------------------ TC-EX1 Expedia position curve
def ex1(c) -> dict:
    d = c.execute(f"""
      with r as (select srch_id, cast(date_time as date) dd, position, click_bool
                 from read_csv_auto('{RAW}/expedia/train.csv') where random_bool = 1 and position <= 10)
      select srch_id, (datediff('day', date '2012-01-01', dd) % 2) = 0 even, position, click_bool from r""").fetchnumpy()
    sid, even, pos, clk = d["srch_id"], d["even"].astype(bool), d["position"].astype(int), d["click_bool"].astype(float)
    # search x position matrix (missing slot = not shown)
    u, inv = np.unique(sid, return_inverse=True)
    shown = np.zeros((len(u), 10)); clicks = np.zeros((len(u), 10))
    np.add.at(shown, (inv, pos - 1), 1); np.add.at(clicks, (inv, pos - 1), clk)
    ev = np.zeros(len(u), bool); ev[inv] = even

    def rel(rows):
        ctr = clicks[rows].sum(0) / np.maximum(shown[rows].sum(0), 1)
        return ctr / ctr[0]
    allr = np.arange(len(u))
    rng = np.random.default_rng(2)
    bs = np.array([rel(rng.integers(0, len(u), len(u))) for _ in range(200)])
    ci = np.quantile(bs, [0.025, 0.975], axis=0)

    def ci_of(rows):
        b = np.array([rel(rows[rng.integers(0, len(rows), len(rows))]) for _ in range(200)])
        return np.quantile(b, [0.025, 0.975], axis=0)
    e_rows, o_rows = allr[ev], allr[~ev]
    ce, co = ci_of(e_rows), ci_of(o_rows)
    overlap = [bool(ce[0][p] <= co[1][p] and co[0][p] <= ce[1][p]) for p in range(1, 10)]
    return _card("TC-EX1", source="Expedia hotel search, randomly ordered searches (random_bool = 1)",
                 source_population="Expedia searches 2012-2013, lodging", target_population="synthetic results pages, Brazil 2018",
                 estimate={"rel_ctr": rel(allr).tolist()}, ci95={"rel_ctr_lo": ci[0].tolist(), "rel_ctr_hi": ci[1].tolist()},
                 n_searches=int(len(u)), trained_through="2013-06-30",
                 heldout={"H4 rel CTR positions 2-10, odd vs even dates (CIs overlap)": {
                     "even": rel(e_rows).tolist(), "odd": rel(o_rows).tolist(), "overlap": overlap, "pass": all(overlap)}},
                 assumptions=["relative decay over positions 1-10 carries over; absolute click level not used",
                              "lodging, desktop-heavy population"],
                 status=_status(all(overlap)))


# ------------------------------------------------------------------ TC-DV1 Statcounter + TC-DV2 GA
def dv1() -> dict:
    import csv
    rows = list(csv.DictReader(open(RAW / "statcounter/brazil_device_share_2016_2018.csv")))
    m = {r["Date"]: {"desktop": float(r["Desktop"]) / 100, "mobile": float(r["Mobile"]) / 100, "tablet": float(r["Tablet"]) / 100} for r in rows}
    for v in m.values():
        s = sum(v.values()); [v.__setitem__(k, x / s) for k, x in list(v.items())]
    return _card("TC-DV1", source="Statcounter Brazil desktop/mobile/tablet share (monthly)",
                 source_population="page views on sites using Statcounter, Brazil 2016-2018",
                 target_population="all synthetic sessions, Brazil 2018", estimate={"monthly_share": m},
                 trained_through=None, join="on date (exogenous monthly series, not fitted; available_through 2018-12)",
                 heldout={"none": "joined by month, not fitted"},
                 assumptions=["page-view share approximates session share"], status="partially supported (exogenous prior; page views, not shoppers)")


def dv2(c) -> dict:
    d = c.execute(f"""
      select cast(date as int) d, json_extract_string(device, '$.deviceCategory') dev,
             coalesce(try_cast(json_extract_string(totals, '$.transactionRevenue') as double), 0) > 0 buy
      from read_csv('{RAW}/ga/train.csv', all_varchar=true)""").fetchnumpy()
    day, dev, buy = d["d"].astype(int), d["dev"].astype(str), d["buy"].astype(bool)

    def conv(sel):
        return {k: float(buy[sel & (dev == k)].mean()) for k in ("desktop", "mobile", "tablet")}
    allc = conv(np.ones(len(day), bool))
    med = np.median(day)
    h = {"first": conv(day < med), "second": conv(day >= med)}
    r = {p: h[p]["mobile"] / h[p]["desktop"] for p in h}
    rel_diff = abs(r["first"] - r["second"]) / r["first"]
    rng = np.random.default_rng(3)
    idx_d, idx_m = np.flatnonzero(dev == "desktop"), np.flatnonzero(dev == "mobile")
    bs = [buy[rng.choice(idx_m, len(idx_m))].mean() / buy[rng.choice(idx_d, len(idx_d))].mean() for _ in range(200)]
    return _card("TC-DV2", source="Google Analytics sample (merchandise store), sessions with revenue",
                 source_population="one US online store, Aug 2016 - Aug 2017", target_population="synthetic sessions, Brazil 2018",
                 estimate={"conversion": allc, "ratio_mobile_desktop": allc["mobile"] / allc["desktop"],
                           "ratio_tablet_desktop": allc["tablet"] / allc["desktop"]},
                 ci95={"ratio_mobile_desktop": [float(np.quantile(bs, 0.025)), float(np.quantile(bs, 0.975))]},
                 n_sessions=int(len(day)), trained_through=str(int(day.max())),
                 heldout={"H5 mobile:desktop conversion ratio, halves (rel diff <= 30%)": {"halves": r, "rel_diff": rel_diff, "pass": rel_diff <= 0.30}},
                 assumptions=["the ratio carries over, the level does not"], status=_status(rel_diff <= 0.30))


def fit_all(only: list[str] | None = None) -> dict:
    OUT.mkdir(parents=True, exist_ok=True)
    c = duckdb.connect()
    c.execute("set memory_limit='12GB'")
    fns = {"TC-RR1": lambda: rr1(c), "TC-TB1": lambda: tb1(c), "TC-EX1": lambda: ex1(c), "TC-DV1": dv1, "TC-DV2": lambda: dv2(c)}
    return {k: f() for k, f in fns.items() if not only or k in only}


def load(cid: str) -> dict:
    return json.loads((OUT / f"{cid}.json").read_text())
