"""Phase 3: minimal synthetic chain  demand -> orders -> fulfilment -> survey/review.

Only Olist-fitted mechanisms (no transferred data in P0). Every parameter is fitted on purchases
up to `trained_through` (inclusive date) and the simulation must start after it (gate S3).
Mechanism design and tolerances were preregistered in docs/p0/PHASE3_TOLERANCES.md.

Randomness is counter-based and keyed by (seed, mechanism, order index): an order's draws never
depend on how many other orders exist, so factual/twin comparisons stay aligned (gate S7).
"""
from __future__ import annotations

import hashlib
import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta

import duckdb
import numpy as np
import polars as pl
from scipy.special import ndtri

QS = np.linspace(0.0, 1.0, 201)
EPOCH = date(2017, 1, 1)     # origin of global day indices for keyed randomness
BUCKETS = ["on_time", "late_1_3", "late_4_10", "late_gt10", "undelivered"]
H = 3600.0


# ------------------------------------------------------------------ keyed randomness
def _key(seed: int, name: str) -> np.uint64:
    return np.uint64(int.from_bytes(hashlib.blake2b(f"{seed}:{name}".encode(), digest_size=8).digest(), "little"))


def uniform(seed: int, name: str, idx: np.ndarray) -> np.ndarray:
    """Counter-based uniform in (0,1) for each entity index: splitmix64(key ^ idx * golden)."""
    with np.errstate(over="ignore"):
        x = _key(seed, name) ^ (idx.astype(np.uint64) * np.uint64(0x9E3779B97F4A7C15))
        x = (x ^ (x >> np.uint64(30))) * np.uint64(0xBF58476D1CE4E5B9)
        x = (x ^ (x >> np.uint64(27))) * np.uint64(0x94D049BB133111EB)
        x = x ^ (x >> np.uint64(31))
    return ((x >> np.uint64(11)).astype(np.float64) + 0.5) / float(1 << 53)


def qdraw(qvals: np.ndarray, u: np.ndarray) -> np.ndarray:
    """Inverse empirical CDF from 201 quantiles."""
    return np.interp(u, QS, qvals)


# ------------------------------------------------------------------ fitting
@dataclass
class ChainParams:
    trained_through: date
    level_a: float                 # daily orders at day 0 of 2018 (trend intercept)
    level_b: float                 # change per day
    dow: list[float]               # Monday-first shares
    hour: list[float]
    buyer_regions: list[str]
    buyer_cum: list[float]
    sellers: list[str]
    seller_region: list[str]
    seller_cum: list[float]
    seller_mu: list[float]         # shrunk log handling hours
    handling_sigma: float
    approval_q: list[float]
    transit_q: dict[str, list[float]]
    promise_q: dict[str, list[float]]
    p_delivered: float
    p_answer: float
    lag_q: list[float]             # hours from survey sent to answer
    score_cpt: dict[str, list[float]]
    notes: list[str] = field(default_factory=list)
    # attempt 1 (21 Nov, fitted on 2017 only): locality + destination-specific fulfilment + finer lateness bins
    seller_cum_by_region: dict[str, list[float]] = field(default_factory=dict)
    transit_q_by_dest: dict[str, list[float]] = field(default_factory=dict)     # key "<same|diff>:<buyer_region>"
    promise_q_by_dest: dict[str, list[float]] = field(default_factory=dict)
    late_edges: list[float] = field(default_factory=list)
    score_cpt_fine: list[list[float]] = field(default_factory=list)             # per late bin, then 'undelivered' last
    # attempt 2 (fitted on 2017 only): drift of log transit time per day, by same/diff state, centred on 2017-07-01
    transit_drift_per_day: dict[str, float] = field(default_factory=dict)


def _q(c, sql) -> list[float]:
    return [float(x) for x in c.execute(f"select quantile_cont(x, {QS.tolist()}) from ({sql}) where x is not null").fetchone()[0]]


def fit(canon, trained_through: date = date(2017, 12, 31)) -> ChainParams:
    c = duckdb.connect()
    for t in ["orders", "order_items", "sellers", "reviews"]:
        c.execute(f"create view {t} as select * from '{canon}/{t}.parquet'")
    lo, hi = "2017-01-01", (trained_through + timedelta(days=1)).isoformat()
    c.execute(f"create view fo as select * from orders where purchased_at >= '{lo}' and purchased_at < '{hi}'")
    c.execute("""create view head as select o.*, i.seller_id, s.region seller_region, (o.buyer_region = s.region) same
                 from fo o join (select * from order_items where item_no = 1) i using(order_id) join sellers s using(seller_id)""")
    # demand: Theil-Sen slope over weekly medians of daily counts (robust to Black Friday)
    wk = c.execute("""select date_trunc('week', d) w, median(n) m from (select cast(purchased_at as date) d, count(*) n from fo group by 1)
                      group by 1 order by 1""").fetchall()
    x = np.array([(w - wk[0][0]).days for w, _ in wk], float)
    y = np.array([m for _, m in wk], float)
    slopes = [(y[j] - y[i]) / (x[j] - x[i]) for i in range(len(x)) for j in range(i + 1, len(x)) if x[j] > x[i]]
    b = float(np.median(slopes))
    a0 = float(np.median(y - b * x))
    day0 = (date(2018, 1, 1) - wk[0][0].date() if hasattr(wk[0][0], "date") else date(2018, 1, 1) - wk[0][0]).days
    a = a0 + b * day0
    dow = np.array([r[1] for r in c.execute("select isodow(purchased_at), count(*) from fo group by 1 order by 1").fetchall()], float)
    hour = np.array([r[1] for r in c.execute("select hour(purchased_at), count(*) from fo group by 1 order by 1").fetchall()], float)
    br = c.execute("select buyer_region, count(*) from fo group by 1 order by 2 desc").fetchall()
    sel = c.execute("""select seller_id, any_value(seller_region), count(*) n,
                       avg(ln(greatest(date_diff('minute', approved_at, shipped_at) / 60.0, 0.1))) filter (where shipped_at > approved_at) m,
                       count(*) filter (where shipped_at > approved_at) k
                       from head group by 1 order by 1""").fetchall()
    g_mu, g_sd = c.execute("""select avg(ln(x)), stddev(ln(x)) from (select date_diff('minute', approved_at, shipped_at) / 60.0 x
                              from head where shipped_at > approved_at) where x > 0.1""").fetchone()
    K = 5.0
    mus = [((k or 0) * (m if m is not None else g_mu) + K * g_mu) / ((k or 0) + K) for _, _, _, m, k in sel]
    between = float(np.std([m for *_, m, k in sel if k and k >= 5])) if sel else 0.3
    w = np.array([n for _, _, n, _, _ in sel], float)
    p = ChainParams(
        trained_through=trained_through, level_a=a, level_b=b,
        dow=(dow / dow.sum()).tolist(), hour=(hour / hour.sum()).tolist(),
        buyer_regions=[r for r, _ in br], buyer_cum=np.cumsum([n for _, n in br]).astype(float).tolist(),
        sellers=[s for s, *_ in sel], seller_region=[r for _, r, *_ in sel], seller_cum=np.cumsum(w).tolist(),
        seller_mu=mus, handling_sigma=float(math.sqrt(max(g_sd ** 2 - between ** 2, 0.05))),
        approval_q=_q(c, "select greatest(date_diff('second', purchased_at, approved_at) / 3600.0, 0) x from fo"),
        transit_q={k: _q(c, f"select date_diff('second', shipped_at, delivered_at) / 86400.0 x from head where same = {v} and delivered_at >= shipped_at")
                   for k, v in (("same", "true"), ("diff", "false"))},
        promise_q={k: _q(c, f"select date_diff('day', cast(purchased_at as date), promised_at) x from head where same = {v}")
                   for k, v in (("same", "true"), ("diff", "false"))},
        p_delivered=float(c.execute("select avg((delivered_at is not null)::int) from fo").fetchone()[0]),
        p_answer=float(c.execute("""select avg((r.order_id is not null)::int) from fo left join
                                    (select distinct order_id from reviews where answered_at is not null) r using(order_id)""").fetchone()[0]),
        lag_q=_q(c, "select date_diff('second', created_at, answered_at) / 3600.0 x from reviews r join fo using(order_id)"),
        score_cpt={},
        notes=["fitted on purchases 2017-01-01..trained_through; multi-item orders use the first item's seller"],
    )
    rows = c.execute(f"""select case when o.delivered_at is null then 'undelivered'
                              when date_diff('day', o.promised_at, o.delivered_at) <= 0 then 'on_time'
                              when date_diff('day', o.promised_at, o.delivered_at) <= 3 then 'late_1_3'
                              when date_diff('day', o.promised_at, o.delivered_at) <= 10 then 'late_4_10' else 'late_gt10' end b,
                         r.score, count(*) from fo o join reviews r using(order_id) where r.answered_at is not null group by 1, 2""").fetchall()
    for bkt in BUCKETS:
        cnt = np.array([sum(n for bb, s, n in rows if bb == bkt and s == k) for k in range(1, 6)], float)
        p.score_cpt[bkt] = (cnt / cnt.sum()).tolist() if cnt.sum() else [0.2] * 5
    # --- attempt 1 refinements -------------------------------------------------------------------
    # (a) locality: P(seller | buyer region) = mix of the region's own 2017 choices and the global mix
    gw = np.array([n for _, _, n, _, _ in sel], float)
    pos = {s_: i for i, s_ in enumerate(p.sellers)}
    for r, n_r in br:
        cnt = np.zeros(len(p.sellers))
        for s_, k in c.execute(f"select seller_id, count(*) from head where buyer_region = '{r}' group by 1").fetchall():
            cnt[pos[s_]] = k
        pi = n_r / (n_r + 50.0)
        mix = pi * cnt / max(cnt.sum(), 1) + (1 - pi) * gw / gw.sum()
        p.seller_cum_by_region[r] = np.cumsum(mix).tolist()
    # (b) destination-specific transit and promise (fallback to same/diff when < 200 orders)
    for v, lab in (("true", "same"), ("false", "diff")):
        for r, k in c.execute(f"select buyer_region, count(*) from head where same = {v} and delivered_at >= shipped_at group by 1").fetchall():
            if k >= 200:
                p.transit_q_by_dest[f"{lab}:{r}"] = _q(c, f"""select date_diff('second', shipped_at, delivered_at) / 86400.0 x from head
                                                             where same = {v} and buyer_region = '{r}' and delivered_at >= shipped_at""")
        for r, k in c.execute(f"select buyer_region, count(*) from head where same = {v} group by 1").fetchall():
            if k >= 200:
                p.promise_q_by_dest[f"{lab}:{r}"] = _q(c, f"""select date_diff('day', cast(purchased_at as date), promised_at) x from head
                                                             where same = {v} and buyer_region = '{r}'""")
    # (c) finer lateness bins for the score (early arrival matters too)
    p.late_edges = [-20, -10, -5, 0, 3, 10]
    lab_sql = "case " + " ".join(f"when date_diff('day', o.promised_at, o.delivered_at) <= {e} then {i}" for i, e in enumerate(p.late_edges)) + f" else {len(p.late_edges)} end"
    fine = c.execute(f"""select case when o.delivered_at is null then {len(p.late_edges) + 1} else {lab_sql} end b, r.score, count(*)
                         from fo o join reviews r using(order_id) where r.answered_at is not null group by 1, 2""").fetchall()
    for bi in range(len(p.late_edges) + 2):
        cnt = np.array([sum(n for bb, s_, n in fine if bb == bi and s_ == k) for k in range(1, 6)], float)
        p.score_cpt_fine.append((cnt / cnt.sum()).tolist() if cnt.sum() else [0.2] * 5)
    # (d) attempt 2: Theil-Sen slope of monthly median log transit over 2017, by same/diff
    for v, lab in (("true", "same"), ("false", "diff")):
        mm = c.execute(f"""select date_trunc('month', purchased_at) m, median(ln(greatest(date_diff('second', shipped_at, delivered_at) / 86400.0, 0.05)))
                           from head where same = {v} and delivered_at >= shipped_at group by 1 order by 1""").fetchall()
        xs = np.array([(m - mm[0][0]).days for m, _ in mm], float)
        ys = np.array([y for _, y in mm], float)
        sl = [(ys[j] - ys[i]) / (xs[j] - xs[i]) for i in range(len(xs)) for j in range(i + 1, len(xs)) if xs[j] > xs[i]]
        p.transit_drift_per_day[lab] = float(np.median(sl)) if sl else 0.0
    p.notes.append(f"attempt 2: transit drift per day (log scale) {p.transit_drift_per_day}, centred 2017-07-01")
    p.notes.append("attempt 1: seller | buyer region (pi = n/(n+50)), transit/promise | destination (>=200 orders, else same/diff), score | 7 lateness bins")
    return p


# ------------------------------------------------------------------ simulation
def daily_rate(p: ChainParams, day: date, demand_multiplier=None) -> float:
    """Expected orders on a day (trend x weekday share), times an optional demand multiplier. Shared with the session layer."""
    lam = max(p.level_a + p.level_b * (day - date(2018, 1, 1)).days, 0.0) * 7 * p.dow[day.isoweekday() - 1]
    return lam * (demand_multiplier(day) if demand_multiplier is not None else 1.0)


def simulate(p: ChainParams, start: date, end: date, seed: int = 1, cut: datetime = datetime(2018, 10, 17, 23, 59),
             transit_multiplier=None, demand_multiplier=None) -> pl.DataFrame:
    """Orders purchased in [start, end) and their fulfilment/survey outcomes, events after `cut` censored.
    transit_multiplier: optional callable(order_frame) -> array, the phase-5 intervention hook."""
    if start <= p.trained_through:
        raise ValueError(f"gate S3: simulation start {start} must be after trained_through {p.trained_through}")
    days = (end - start).days
    gen_counts = []
    for d in range(days):
        day = start + timedelta(days=d)
        lam_day = daily_rate(p, day, demand_multiplier)
        gday = (day - EPOCH).days                                          # global day index: independent of the run's start
        g = np.random.Generator(np.random.Philox(key=int(_key(seed, "arrivals")), counter=[gday, 0, 0, 0]))
        gen_counts.append(g.poisson(lam_day * np.array(p.hour)))
    counts = np.array(gen_counts)                                   # days x 24
    n = int(counts.sum())
    day_i = np.repeat(np.repeat(np.arange(days), 24), counts.ravel())
    hour_i = np.repeat(np.tile(np.arange(24), days), counts.ravel())
    # GLOBAL order key = (global day, hour, k-th order in that hour): identical whether the run is split, chunked or extended
    within = np.concatenate([np.arange(c) for c in counts.ravel()]) if n else np.zeros(0, np.int64)
    gday0 = (start - EPOCH).days
    idx = (((gday0 + day_i).astype(np.int64) * 24 + hour_i) * 100_000 + within).astype(np.int64)
    t0 = datetime.combine(start, datetime.min.time())
    # exact integer microseconds from a fixed origin: identical whether the run is split or not
    epoch_us = int(np.datetime64(EPOCH, "us").astype(np.int64))
    t0_us = int(np.datetime64(t0, "us").astype(np.int64))
    purchase_us = (epoch_us + ((gday0 + day_i).astype(np.int64) * 86400 + hour_i.astype(np.int64) * 3600) * 1_000_000
                   + np.floor(uniform(seed, "minute", idx) * 3_600_000_000).astype(np.int64))
    purchase_s = (purchase_us - t0_us) / 1e6                       # run-relative, used only for durations below
    reg = np.array(p.buyer_regions)[np.searchsorted(np.array(p.buyer_cum) / p.buyer_cum[-1], uniform(seed, "region", idx))]
    u_sel = uniform(seed, "seller", idx)
    if p.seller_cum_by_region:
        s_i = np.empty(n, dtype=np.int64)
        for r in np.unique(reg):
            m = reg == r
            cum = np.array(p.seller_cum_by_region.get(r, (np.array(p.seller_cum) / p.seller_cum[-1]).tolist()))
            s_i[m] = np.minimum(np.searchsorted(cum / cum[-1], u_sel[m]), len(p.sellers) - 1)
    else:
        s_i = np.searchsorted(np.array(p.seller_cum) / p.seller_cum[-1], u_sel)
    s_reg = np.array(p.seller_region)[s_i]
    same = reg == s_reg
    approval_s = qdraw(np.array(p.approval_q), uniform(seed, "approval", idx)) * H
    handling_s = np.exp(np.array(p.seller_mu)[s_i] + p.handling_sigma * ndtri(uniform(seed, "handling", idx))) * H
    tq_same, tq_diff = np.array(p.transit_q["same"]), np.array(p.transit_q["diff"])
    u_tr = uniform(seed, "transit", idx)
    transit_s = np.where(same, qdraw(tq_same, u_tr), qdraw(tq_diff, u_tr)) * 86400.0
    for key, q in p.transit_q_by_dest.items():
        lab, r = key.split(":", 1)
        m = (reg == r) & (same if lab == "same" else ~same)
        transit_s[m] = qdraw(np.array(q), u_tr[m]) * 86400.0
    if p.transit_drift_per_day:
        t_rel = (purchase_us - int(np.datetime64("2017-07-01", "us").astype(np.int64))) / 86_400_000_000   # absolute, chunk-invariant
        drift = np.where(same, p.transit_drift_per_day.get("same", 0.0), p.transit_drift_per_day.get("diff", 0.0))
        transit_s = transit_s * np.exp(drift * t_rel)
    df = pl.DataFrame({"order_idx": idx, "buyer_region": reg, "seller_id": np.array(p.sellers)[s_i], "seller_region": s_reg,
                       "same": same})
    shipped_us = purchase_us + np.round(approval_s + handling_s).astype(np.int64) * 1_000_000
    if transit_multiplier is not None:
        transit_s = transit_s * transit_multiplier(df.with_columns(pl.Series("shipped_us", shipped_us)))
    delivered_ok = uniform(seed, "delivered", idx) < p.p_delivered
    pq_same, pq_diff = np.array(p.promise_q["same"]), np.array(p.promise_q["diff"])
    u_pr = uniform(seed, "promise", idx)
    promise_raw = np.where(same, qdraw(pq_same, u_pr), qdraw(pq_diff, u_pr))
    for key, q in p.promise_q_by_dest.items():
        lab, r = key.split(":", 1)
        m = (reg == r) & (same if lab == "same" else ~same)
        promise_raw[m] = qdraw(np.array(q), u_pr[m])
    promise_days = np.round(promise_raw)
    purchase = pl.Series(purchase_us).cast(pl.Datetime("us"))
    df = df.with_columns(
        purchased_at=purchase,
        approved_at=purchase + pl.duration(seconds=pl.Series(approval_s.round())),
        shipped_at=purchase + pl.duration(seconds=pl.Series((approval_s + handling_s).round())),
    ).with_columns(
        delivered_at=pl.when(pl.Series(delivered_ok)).then(pl.col("shipped_at") + pl.duration(seconds=pl.Series(transit_s.round()))),
        promised_at=pl.col("purchased_at").dt.truncate("1d") + pl.duration(days=pl.Series(promise_days.astype(np.int64))),
    )
    late = (df["delivered_at"].dt.truncate("1d") - df["promised_at"]).dt.total_days().to_numpy()
    bucket = np.where(~delivered_ok, 4, np.select([late <= 0, late <= 3, late <= 10], [0, 1, 2], 3))
    if p.score_cpt_fine:
        fine_b = np.where(~delivered_ok, len(p.late_edges) + 1, np.searchsorted(np.array(p.late_edges, float), np.nan_to_num(late, nan=0.0), side="left"))
        cpt = np.cumsum(np.array(p.score_cpt_fine), axis=1)
        score = (uniform(seed, "score", idx)[:, None] > cpt[fine_b]).sum(1) + 1
    else:
        cpt = np.cumsum(np.array([p.score_cpt[b] for b in BUCKETS]), axis=1)
        score = (uniform(seed, "score", idx)[:, None] > cpt[bucket]).sum(1) + 1
    lag_s = qdraw(np.array(p.lag_q), uniform(seed, "lag", idx)) * H
    answered = uniform(seed, "answer", idx) < p.p_answer
    df = df.with_columns(
        sent_at=pl.min_horizontal(pl.col("delivered_at").dt.truncate("1d"), pl.col("promised_at")),
        score=pl.Series(score.astype(np.int64)),
        bucket=pl.Series(np.array(BUCKETS)[bucket]),
    ).with_columns(
        answered_at=pl.when(pl.Series(answered)).then(pl.col("sent_at") + pl.duration(seconds=pl.Series(lag_s.round()))),
    )
    # censor at the data cut, exactly like the real export
    for col in ("approved_at", "shipped_at", "delivered_at", "sent_at", "answered_at"):
        df = df.with_columns(pl.when(pl.col(col) <= cut).then(pl.col(col)).alias(col))
    return df.with_columns(pl.when(pl.col("answered_at").is_not_null()).then(pl.col("score")).alias("score"))


# ------------------------------------------------------------------ fidelity statistics (same code for real and simulated)
def real_frame(canon) -> pl.DataFrame:
    c = duckdb.connect()
    for t in ["orders", "order_items", "sellers", "reviews"]:
        c.execute(f"create view {t} as select * from '{canon}/{t}.parquet'")
    return c.sql("""select o.purchased_at, o.approved_at, o.shipped_at, o.delivered_at, o.promised_at, o.buyer_region,
                        i.seller_id, s.region seller_region, (o.buyer_region = s.region) same,
                        r.created_at sent_at, r.answered_at, r.score
                        from orders o join (select * from order_items where item_no = 1) i using(order_id) join sellers s using(seller_id)
                        left join (select order_id, arg_max(created_at, answered_at) created_at, max(answered_at) answered_at,
                                   arg_max(score, answered_at) score from reviews group by 1) r using(order_id)""").pl()


def stats(df: pl.DataFrame, start: datetime, end: datetime) -> dict:
    d = df.filter((pl.col("purchased_at") >= start) & (pl.col("purchased_at") < end))
    n_days = (end - start).days
    dlv = d.filter(pl.col("delivered_at").is_not_null()).with_columns(
        days=(pl.col("delivered_at") - pl.col("purchased_at")).dt.total_seconds() / 86400,
        late=(pl.col("delivered_at").dt.truncate("1d") - pl.col("promised_at")).dt.total_days())
    dlv = dlv.with_columns(bucket=pl.when(pl.col("late") <= 0).then(pl.lit("on_time")).when(pl.col("late") <= 3).then(pl.lit("late_1_3"))
                           .when(pl.col("late") <= 10).then(pl.lit("late_4_10")).otherwise(pl.lit("late_gt10")))
    rev = dlv.filter(pl.col("score").is_not_null())
    by_b = {b: rev.filter(pl.col("bucket") == b)["score"].mean() for b in BUCKETS[:4]}
    ans = d.filter(pl.col("answered_at").is_not_null() & pl.col("sent_at").is_not_null())
    lag_days = ((ans["answered_at"] - ans["sent_at"]).dt.total_seconds() / 86400).median()
    sent_before = dlv.filter(pl.col("sent_at").is_not_null()).select((pl.col("sent_at") < pl.col("delivered_at").dt.truncate("1d")).mean()).item()
    top = d.group_by("seller_id").len().sort("len", descending=True)
    k = max(1, int(round(0.1 * top.height)))
    same_late = dlv.filter(pl.col("same"))["late"].gt(0).mean()
    diff_late = dlv.filter(~pl.col("same"))["late"].gt(0).mean()
    wd = d.group_by(pl.col("purchased_at").dt.weekday()).len().sort("purchased_at")
    hr = d.group_by(pl.col("purchased_at").dt.hour()).len().sort("purchased_at")
    return {
        "F1_daily_orders": d.height / n_days,
        "F2_weekday": (wd["len"] / wd["len"].sum()).to_list(),
        "F3_hour": (hr["len"] / hr["len"].sum()).to_list(),
        "F4_delivery_days_q": [dlv["days"].quantile(q) for q in (0.25, 0.5, 0.75)],
        "F5_late_share": dlv["late"].gt(0).mean(),
        "F6_score_by_bucket": by_b,
        "F7_lag_days_median": lag_days, "F7_sent_before_delivery": sent_before,
        "F8_spearman_late_score": rev.select(pl.corr("late", "score", method="spearman")).item(),
        "F9_top10pct_seller_share": top["len"][:k].sum() / d.height,
        "F10_diff_state_share": d["same"].not_().mean(), "F10_late_gap_diff_minus_same": diff_late - same_late,
    }


def compare(real: dict, sim: dict) -> list[dict]:
    out = []

    def add(fid, desc, r, s, ok):
        out.append({"id": fid, "statistic": desc, "real": r, "sim": s, "pass": bool(ok)})

    add("F1", "mean daily orders (±20%)", real["F1_daily_orders"], sim["F1_daily_orders"],
        abs(sim["F1_daily_orders"] / real["F1_daily_orders"] - 1) <= 0.20)
    add("F2", "weekday shares (max |diff| ≤ 0.02)", real["F2_weekday"], sim["F2_weekday"],
        max(abs(a - b) for a, b in zip(real["F2_weekday"], sim["F2_weekday"])) <= 0.02)
    tv = 0.5 * sum(abs(a - b) for a, b in zip(real["F3_hour"], sim["F3_hour"]))
    add("F3", f"hour profile (TV ≤ 0.05; TV={tv:.3f})", real["F3_hour"], sim["F3_hour"], tv <= 0.05)
    add("F4", "delivery days q25/q50/q75 (each ±20%)", real["F4_delivery_days_q"], sim["F4_delivery_days_q"],
        all(abs(s / r - 1) <= 0.20 for r, s in zip(real["F4_delivery_days_q"], sim["F4_delivery_days_q"])))
    add("F5", "late share (±0.03)", real["F5_late_share"], sim["F5_late_share"], abs(real["F5_late_share"] - sim["F5_late_share"]) <= 0.03)
    add("F6", "mean score per lateness bucket (each ±0.25)", real["F6_score_by_bucket"], sim["F6_score_by_bucket"],
        all(abs(real["F6_score_by_bucket"][b] - sim["F6_score_by_bucket"][b]) <= 0.25 for b in BUCKETS[:4]))
    add("F7", "answer lag median (±1 day) and share sent before delivery (±0.03)",
        [real["F7_lag_days_median"], real["F7_sent_before_delivery"]], [sim["F7_lag_days_median"], sim["F7_sent_before_delivery"]],
        abs(real["F7_lag_days_median"] - sim["F7_lag_days_median"]) <= 1 and abs(real["F7_sent_before_delivery"] - sim["F7_sent_before_delivery"]) <= 0.03)
    add("F8", "Spearman late ↔ score (±0.05)", real["F8_spearman_late_score"], sim["F8_spearman_late_score"],
        abs(real["F8_spearman_late_score"] - sim["F8_spearman_late_score"]) <= 0.05)
    add("F9", "top-10% seller share (±0.05)", real["F9_top10pct_seller_share"], sim["F9_top10pct_seller_share"],
        abs(real["F9_top10pct_seller_share"] - sim["F9_top10pct_seller_share"]) <= 0.05)
    add("F10", "different-state share (±0.05) and late gap diff−same (±0.03)",
        [real["F10_diff_state_share"], real["F10_late_gap_diff_minus_same"]], [sim["F10_diff_state_share"], sim["F10_late_gap_diff_minus_same"]],
        abs(real["F10_diff_state_share"] - sim["F10_diff_state_share"]) <= 0.05
        and abs(real["F10_late_gap_diff_minus_same"] - sim["F10_late_gap_diff_minus_same"]) <= 0.03)
    return out
