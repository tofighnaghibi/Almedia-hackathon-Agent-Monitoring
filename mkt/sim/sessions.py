"""P1-2 session layer: sessions wrap the order chain (docs/p0/P1_2_SESSION_LAYER_PREREG.md).

Levels come from the chain (one buying session per order; non-buying sessions follow the same hourly
rate times k). Shapes come from the transport cards in docs/p0/cards/. Every draw is keyed by a global
entity key (order_idx, or global hour slot + index), so the chain is untouched (V1) and runs can be
chunked (V2). Nothing here reads the oracle.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, timedelta

import numpy as np
import polars as pl
from scipy.special import ndtri

from ..stitch import cards as C
from .chain import EPOCH, ChainParams, _key, daily_rate, uniform

S = 1_000_000
GAP_S = 1800
QS = np.linspace(0, 1, 101)
DEVICES = ("desktop", "mobile", "tablet")


@dataclass(frozen=True)
class SessionParams:
    pmf_views_buying: tuple
    pmf_views_nonbuying: tuple
    cart_share_buying: float
    cart_share_nonbuying: float
    k_nonbuying_per_buying: float
    q_lag_view_s: tuple
    q_lag_cart_s: tuple
    rel_ctr: tuple
    device_month_share: dict
    conv_ratio: dict
    checkout_lead_s: tuple = (60.0, 600.0)        # ASSUMED (A-13): checkout starts 1-10 min before the order is created
    cards: dict = field(default_factory=dict)      # card id -> status (for lineage)
    trained_through: str = ""

    @staticmethod
    def from_cards() -> "SessionParams":
        rr, tb, ex, dv1, dv2 = (C.load(c) for c in ("TC-RR1", "TC-TB1", "TC-EX1", "TC-DV1", "TC-DV2"))
        e = dv2["estimate"]
        return SessionParams(
            pmf_views_buying=tuple(rr["pmf_views_buying"]), pmf_views_nonbuying=tuple(rr["pmf_views_nonbuying"]),
            cart_share_buying=rr["estimate"]["cart_share_buying"], cart_share_nonbuying=rr["estimate"]["cart_share_nonbuying"],
            k_nonbuying_per_buying=rr["estimate"]["k_nonbuying_per_buying"],
            q_lag_view_s=tuple(tb["q_lag_view_s"]), q_lag_cart_s=tuple(tb["q_lag_cart_s"]), rel_ctr=tuple(ex["estimate"]["rel_ctr"]),
            device_month_share=dv1["estimate"]["monthly_share"],
            conv_ratio={"desktop": 1.0, "mobile": e["ratio_mobile_desktop"], "tablet": e["ratio_tablet_desktop"]},
            cards={c["id"]: c["status"] for c in (rr, tb, ex, dv1, dv2)},
            trained_through=max(x for x in (rr["trained_through"], tb["trained_through"], ex["trained_through"]) if x))

    @property
    def p_research(self) -> float:
        """Share of purchases whose bought item was first viewed > 30 min earlier (in a separate research visit)."""
        return float(1 - np.interp(GAP_S, np.array(self.q_lag_view_s), QS))


def _pmf_draw(pmf, u):
    return np.searchsorted(np.cumsum(pmf) / np.sum(pmf), u, side="right").clip(0, len(pmf) - 1)


def _device(sp: SessionParams, start_us: np.ndarray, u: np.ndarray, buying: bool) -> np.ndarray:
    month = pl.Series(start_us).cast(pl.Datetime("us")).dt.strftime("%Y-%m").to_numpy()
    out = np.empty(len(u), dtype=object)
    K = sp.k_nonbuying_per_buying
    for m in np.unique(month):
        sh = sp.device_month_share.get(m) or sp.device_month_share[max(sp.device_month_share)]
        s = np.array([sh[d] for d in DEVICES])
        pb = s * np.array([sp.conv_ratio[d] for d in DEVICES]); pb /= pb.sum()
        # non-buying mix solved so that ALL sessions match the Statcounter share (F5)
        pn = np.clip((s * (K + 1) - pb) / K, 0, None); pn /= pn.sum()
        cum = np.cumsum(pb if buying else pn)
        sel = month == m
        out[sel] = np.array(DEVICES)[np.searchsorted(cum, u[sel], side="right").clip(0, 2)]
    return out


def _views(sp, key, n, t_lo, t_hi, mech, seed, first_at_lo):
    """One row per view: times in [t_lo, t_hi]; the first at t_lo when first_at_lo; clicked position from the Expedia curve."""
    rep = np.repeat(np.arange(len(n)), n)
    j = np.concatenate([np.arange(c) for c in n]) if len(n) else np.zeros(0, int)
    vkey = key[rep].astype(np.int64) * 1024 + j
    u_t = uniform(seed, f"{mech}:view_t", vkey)
    t = t_lo[rep] + (u_t * (t_hi[rep] - t_lo[rep])).astype(np.int64)
    t = np.where(first_at_lo[rep] & (j == 0), t_lo[rep], t)
    rel = np.array(sp.rel_ctr)
    pos = np.searchsorted(np.cumsum(rel) / rel.sum(), uniform(seed, f"{mech}:pos", vkey), side="right").clip(0, 9) + 1
    return rep, vkey, t, pos


def build(chain: pl.DataFrame, sp: SessionParams, p: ChainParams, start: date, end: date, seed: int,
          failed: np.ndarray | None = None, demand_multiplier=None) -> tuple[pl.DataFrame, pl.DataFrame]:
    """World-level sessions and views. `chain` = all orders that reached checkout (incl. failed ones);
    `failed` marks checkouts that did not become orders (checkout.failure_rate lever)."""
    oidx = chain["order_idx"].to_numpy().astype(np.int64)
    buy_us = chain["purchased_at"].cast(pl.Int64).to_numpy()
    nb_ = len(oidx)
    failed = np.zeros(nb_, bool) if failed is None else np.asarray(failed)
    # failed: bool mask (checkout failure lever) or per-order outcome strings ("order" = committed)
    outcome_b = np.where(failed, "checkout_failed", "order") if failed.dtype == bool else failed.astype(str)
    qv, qc = np.array(sp.q_lag_view_s), np.array(sp.q_lag_cart_s)

    # ---- buying sessions (one per checkout), keyed by order_idx
    lag = np.interp(uniform(seed, "sess:lag_view", oidx), QS, qv)                        # first view of bought item -> buy
    research = lag > GAP_S
    f_gap = np.interp(GAP_S, qv, QS)
    lag_in = np.interp(uniform(seed, "sess:lag_in", oidx) * f_gap, QS, qv)               # in-session part (<= 30 min)
    dur = np.where(research, lag_in, lag)
    b_start = buy_us - (dur * S).astype(np.int64)
    n_b = np.maximum(_pmf_draw(sp.pmf_views_buying, uniform(seed, "sess:nviews_b", oidx)), 1)   # decision: >= 1 view
    has_cart = uniform(seed, "sess:cart_b", oidx) < sp.cart_share_buying
    f_dur = np.interp(dur, qc, QS)
    cart_lag = np.interp(uniform(seed, "sess:cart_lag", oidx) * f_dur, QS, qc)
    lo, hi = sp.checkout_lead_s
    co_lead = np.minimum(lo + (hi - lo) * uniform(seed, "sess:co_lead", oidx), dur)
    co_us = buy_us - (co_lead * S).astype(np.int64)
    cart_us = np.where(has_cart, buy_us - (np.minimum(cart_lag, dur) * S).astype(np.int64), -1)
    cart_us = np.where(has_cart, np.minimum(cart_us, co_us), -1)
    reg = chain["buyer_region"].to_numpy()
    dev_b = _device(sp, b_start, uniform(seed, "sess:device_b", oidx), buying=True)

    # ---- research visits (bought item first viewed > 30 min earlier), keyed by order_idx
    r = np.flatnonzero(research)
    r_start = buy_us[r] - (lag[r] * S).astype(np.int64)
    n_r = np.maximum(_pmf_draw(sp.pmf_views_nonbuying, uniform(seed, "sess:nviews_r", oidx[r])), 1)
    r_cart = uniform(seed, "sess:cart_r", oidx[r]) < sp.cart_share_nonbuying
    dev_r = _device(sp, r_start, uniform(seed, "sess:device_r", oidx[r]), buying=False)

    # ---- non-buying sessions: Poisson per hour, rate = order rate x (k - p_research); global hour-slot keys
    k_eff = max(sp.k_nonbuying_per_buying - sp.p_research, 0.0)
    counts = []
    for d in range((end - start).days):
        day = start + timedelta(days=d)
        g = np.random.Generator(np.random.Philox(key=int(_key(seed, "sess:arrivals")), counter=[(day - EPOCH).days, 1, 0, 0]))
        counts.append(g.poisson(daily_rate(p, day, demand_multiplier) * k_eff * np.array(p.hour)))
    counts = np.array(counts)
    days, hrs = np.nonzero(np.ones_like(counts))
    rep = np.repeat(np.arange(counts.size), counts.ravel())
    within = np.concatenate([np.arange(c) for c in counts.ravel()]) if counts.sum() else np.zeros(0, int)
    gday = (start - EPOCH).days + days[rep]
    nkey = ((gday.astype(np.int64) * 24 + hrs[rep]) * 1_000_000 + within).astype(np.int64)
    epoch_us = int(np.datetime64(EPOCH, "us").astype(np.int64))
    n_start = epoch_us + (gday.astype(np.int64) * 86400 + hrs[rep] * 3600) * S + (uniform(seed, "sess:n_t", nkey) * 3600 * S).astype(np.int64)
    n_n = _pmf_draw(sp.pmf_views_nonbuying, uniform(seed, "sess:nviews_n", nkey))
    n_cart = uniform(seed, "sess:cart_n", nkey) < sp.cart_share_nonbuying
    n_dur = np.exp(np.log(120) + 1.0 * ndtri(uniform(seed, "sess:n_dur", nkey))).clip(1, GAP_S)   # ASSUMED (A-14): non-buying visit length
    n_reg = np.array(p.buyer_regions)[np.searchsorted(np.array(p.buyer_cum) / p.buyer_cum[-1], uniform(seed, "sess:n_region", nkey))]
    dev_n = _device(sp, n_start, uniform(seed, "sess:device_n", nkey), buying=False)

    sessions = pl.concat([
        pl.DataFrame({"session_id": [f"b{x}" for x in oidx], "kind": "buying", "key": oidx, "order_idx": oidx, "start_us": b_start,
                      "end_us": buy_us, "n_views": n_b, "cart_us": np.where(has_cart, cart_us, None).astype(float), "checkout_us": co_us.astype(float),
                      "outcome": outcome_b, "device": dev_b.astype(str), "region": reg,
                      "first_view_bought_us": (buy_us - (lag * S).astype(np.int64))}),
        pl.DataFrame({"session_id": [f"r{x}" for x in oidx[r]], "kind": "research", "key": oidx[r], "order_idx": oidx[r], "start_us": r_start,
                      "end_us": r_start + (n_r * 60 * S), "n_views": n_r, "cart_us": np.where(r_cart, r_start + 30 * S, None).astype(float),
                      "checkout_us": np.full(len(r), np.nan), "outcome": "left", "device": dev_r.astype(str), "region": reg[r],
                      "first_view_bought_us": r_start}),
        pl.DataFrame({"session_id": [f"n{x}" for x in nkey], "kind": "nonbuying", "key": nkey, "order_idx": None, "start_us": n_start,
                      "end_us": n_start + (n_dur * S).astype(np.int64), "n_views": n_n,
                      "cart_us": np.where(n_cart, n_start + (n_dur * S / 2).astype(np.int64), None).astype(float),
                      "checkout_us": np.full(len(nkey), np.nan), "outcome": "left", "device": dev_n.astype(str), "region": n_reg,
                      "first_view_bought_us": np.full(len(nkey), -1)}),
    ], how="diagonal_relaxed").with_columns(pl.col("cart_us").fill_nan(None).cast(pl.Int64), pl.col("checkout_us").fill_nan(None).cast(pl.Int64))

    # ---- views
    parts = []
    for kind, mech in (("buying", "sess:vb"), ("research", "sess:vr"), ("nonbuying", "sess:vn")):
        sdf = sessions.filter(pl.col("kind") == kind)
        key = sdf["key"].to_numpy()
        t_lo, t_hi = sdf["start_us"].to_numpy(), sdf["end_us"].to_numpy()
        # buying rows are in oidx order (filter keeps order): the bought item is viewed first unless it had a research visit
        first = ~research if kind == "buying" else np.full(len(key), kind == "research")
        rep_, vkey, t, pos = _views(sp, key, sdf["n_views"].to_numpy(), t_lo, np.maximum(t_hi - S, t_lo), mech, seed, first)
        parts.append(pl.DataFrame({"session_id": sdf["session_id"].to_numpy()[rep_], "vkey": vkey, "t_us": t, "position": pos,
                                   "bought_item": first[rep_] & (np.concatenate([np.arange(c) for c in sdf["n_views"].to_numpy()]) == 0)
                                   if len(rep_) else np.zeros(0, bool),
                                   "device": sdf["device"].to_numpy()[rep_], "region": sdf["region"].to_numpy()[rep_]}))
    views = pl.concat(parts)
    return sessions, views


def observe_sessions(sessions: pl.DataFrame, views: pl.DataFrame, seed: int, web_delay_median_s: float = 20.0) -> pl.DataFrame:
    """Observed session events, same columns as observe_chain.observe (web capture delay, ASSUMED A-11)."""
    def delay(name, key):
        return (web_delay_median_s * np.exp(ndtri(uniform(seed, f"observe:{name}", key.astype(np.int64)))) * S).astype(np.int64)

    def frame(etype, sid, t_us, key, value, device, region):
        t = pl.Series(t_us.astype(np.int64)).cast(pl.Datetime("us"))
        return pl.DataFrame({"order_id": sid, "event_type": etype, "event_time": t,
                             "available_at": (pl.Series(t_us.astype(np.int64) + delay(etype, key))).cast(pl.Datetime("us")),
                             "value": value.astype(float), "device": device, "region": region})
    s = sessions
    k = s["key"].to_numpy() * 4 + s["kind"].replace_strict({"buying": 0, "research": 1, "nonbuying": 2}).to_numpy()
    parts = [frame("session_started", s["session_id"], s["start_us"].to_numpy(), k, s["n_views"].to_numpy(), s["device"], s["region"])]
    c = s.filter(pl.col("cart_us").is_not_null())
    kc = c["key"].to_numpy() * 4 + c["kind"].replace_strict({"buying": 0, "research": 1, "nonbuying": 2}).to_numpy()
    parts.append(frame("cart_added", c["session_id"], c["cart_us"].to_numpy(), kc, np.zeros(c.height), c["device"], c["region"]))
    o = s.filter(pl.col("checkout_us").is_not_null())
    parts.append(frame("checkout_started", o["session_id"], o["checkout_us"].to_numpy(), o["key"].to_numpy(),
                       (o["outcome"] == "order").cast(pl.Float64).to_numpy(), o["device"], o["region"]))
    parts.append(frame("item_viewed", views["session_id"], views["t_us"].to_numpy(), views["vkey"].to_numpy(),
                       views["position"].to_numpy(), views["device"], views["region"]))
    ev = pl.concat(parts).with_columns(
        (pl.col("event_type") + ":" + pl.col("order_id")).alias("record_key"), pl.lit(0).cast(pl.Int64).alias("revision"),
        pl.lit(None, pl.Utf8).alias("seller_id"), pl.lit(None, pl.Utf8).alias("product_id"),
        pl.lit(None, pl.Float64).alias("amount"), pl.lit(None, pl.Utf8).alias("metric"), pl.lit(None, pl.Utf8).alias("supersedes"))
    # item_viewed: several per session -> make record keys unique per view
    ev = ev.with_columns(pl.when(pl.col("event_type") == "item_viewed")
                         .then(pl.col("record_key") + ":" + pl.int_range(pl.len()).over("record_key").cast(pl.Utf8))
                         .otherwise(pl.col("record_key")).alias("record_key"))
    return ev.with_columns((pl.col("record_key") + ":r0").alias("event_id"))
