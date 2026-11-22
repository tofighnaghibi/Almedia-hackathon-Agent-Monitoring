"""Phase 4: observation process for the chain simulator, and the emulator EvidenceAccessPort.

World truth (mkt.sim.chain.simulate) -> what a monitor could see: arrival delays, timestamp
precision, missing records and late corrections (revisions that supersede earlier records).
Each property is labelled MEASURED (from the Olist audit) or ASSUMED (no source; documented range).
All randomness is keyed per record, so extending the horizon never changes what was already seen.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from datetime import datetime

import numpy as np
import polars as pl

from ..replay_port import EventFramePort
from ..engine_types import EnvironmentKind, EngineScope, LineageRoot, OriginClass
from .chain import ChainParams, uniform

S = 1_000_000  # microseconds per second


@dataclass(frozen=True)
class ObservationModel:
    web_delay_median_s: float = 20.0          # ASSUMED (A-11): order capture latency; range 5-120 s
    payments_delay_median_s: float = 60.0     # ASSUMED (A-11): range 10-600 s
    logistics_report_hour: int = 6            # ASSUMED (A-11): carrier events arrive in a daily batch at 06:00 next day
    review_delay_s: float = 0.0               # MEASURED (phase 1): the score exists when the customer answers
    carrier_minute_rounding: float = 0.295    # MEASURED: 29.5% of Olist carrier timestamps end at :00 seconds
    delivered_missing: float = 8.3e-5         # MEASURED: 8 of 96,478 delivered orders lack a delivery time
    correction_rate: float = 0.01             # ASSUMED (A-12): delivery time first reported +/-1 day off; range 0-5%
    correction_after_days: tuple[float, float] = (2.0, 7.0)   # ASSUMED (A-12)
    labels: dict = field(default_factory=lambda: {
        "web_delay_median_s": "assumed", "payments_delay_median_s": "assumed", "logistics_report_hour": "assumed",
        "review_delay_s": "measured", "carrier_minute_rounding": "measured", "delivered_missing": "measured",
        "correction_rate": "assumed", "correction_after_days": "assumed"})

    def version(self) -> str:
        return hashlib.sha1(json.dumps(asdict(self), sort_keys=True, default=str).encode()).hexdigest()[:12]


def _us(col: str) -> pl.Expr:
    return pl.col(col).cast(pl.Int64)


def observe(world: pl.DataFrame, om: ObservationModel = ObservationModel(), seed: int = 1, drops: list | None = None) -> pl.DataFrame:
    """drops: [(event_type, window_start, window_end, rate)] observation-layer logging failures (world unchanged)."""
    """Canonical observed events (same vocabulary as the real adapter) with available_at and revisions."""
    idx = world["order_idx"].to_numpy()
    oid = pl.Series([f"sim:o{i}" for i in idx])

    def u(name):
        return uniform(seed, f"observe:{name}", idx)

    def lognorm_us(name, median_s):
        from scipy.special import ndtri
        return (median_s * np.exp(ndtri(u(name))) * S).astype(np.int64)

    base = world.select(pl.Series("order_id", oid), pl.col("buyer_region").alias("region"), "seller_id")
    late = ((world["delivered_at"].dt.truncate("1d") - world["promised_at"]).dt.total_days()).cast(pl.Float64)
    promised_days = ((world["promised_at"] - world["purchased_at"].dt.truncate("1d")).dt.total_days()).cast(pl.Float64)
    parts = []

    def add(etype, t_true: pl.Series, delay_us: np.ndarray, value=None, keep=None):
        t_obs = t_true
        f = base.with_columns(pl.lit(etype).alias("event_type"), t_obs.alias("event_time"),
                              (t_obs.cast(pl.Int64) + pl.Series(delay_us)).cast(pl.Datetime("us")).alias("available_at"),
                              (value if value is not None else pl.lit(None, pl.Float64)).alias("value"))
        if keep is not None:
            f = f.filter(pl.Series(keep))
        parts.append(f.filter(pl.col("event_time").is_not_null()))

    add("order_created", world["purchased_at"], lognorm_us("web", om.web_delay_median_s), promised_days)
    add("payment_approved", world["approved_at"], lognorm_us("pay", om.payments_delay_median_s))
    # carrier handover: a share is recorded at minute precision (MEASURED); arrives in the next 06:00 batch (ASSUMED)
    rounded = u("round") < om.carrier_minute_rounding
    hand = world["shipped_at"]
    hand_obs = pl.Series(np.where(rounded, hand.dt.truncate("1m").cast(pl.Int64).fill_null(0).to_numpy(),
                                  hand.cast(pl.Int64).fill_null(0).to_numpy())).cast(pl.Datetime("us"))
    hand_obs = pl.select(pl.when(hand.is_null()).then(None).otherwise(hand_obs)).to_series()

    def batch(t: pl.Series) -> np.ndarray:
        """Delay until the next daily batch (next day, report hour). Nulls give 0 (their events are dropped anyway)."""
        ti = t.cast(pl.Int64).fill_null(0).to_numpy()
        day0 = t.dt.truncate("1d").cast(pl.Int64).fill_null(0).to_numpy()
        return day0 + (86400 + om.logistics_report_hour * 3600) * S - ti

    add("handed_to_carrier", hand_obs, batch(hand_obs))
    # delivery: rare missing records (MEASURED); 1% first reported a day off, corrected later (ASSUMED)
    keep = u("dmiss") >= om.delivered_missing
    corr = (u("corr") < om.correction_rate) & keep
    dt_true = world["delivered_at"]
    shift = np.where(u("corr_sign") < 0.5, -86400, 86400) * S
    dti = dt_true.cast(pl.Int64).fill_null(0).to_numpy()
    first = pl.Series(np.where(corr, dti + shift, dti)).cast(pl.Datetime("us"))
    first = pl.select(pl.when(dt_true.is_null()).then(None).otherwise(first)).to_series()
    first_late = ((first.dt.truncate("1d") - world["promised_at"]).dt.total_days()).cast(pl.Float64)
    add("delivered", first, batch(dt_true), first_late, keep)
    lo, hi = om.correction_after_days
    fix_delay = batch(dt_true) + ((lo + (hi - lo) * u("corr_after")) * 86400 * S).astype(np.int64)
    add("delivered", dt_true, fix_delay, late, corr)
    add("survey_sent", world["sent_at"], np.zeros(len(idx), np.int64))
    add("review_submitted", world["answered_at"], np.full(len(idx), int(om.review_delay_s * S)), world["score"].cast(pl.Float64))

    ev = pl.concat(parts, how="diagonal_relaxed")
    for etype, w0, w1, rate in (drops or []):
        oidx = ev["order_id"].str.slice(5).cast(pl.Int64).to_numpy()
        u_drop = uniform(seed, f"observe:drop:{etype}", oidx)
        hit = ((ev["event_type"] == etype) & (ev["event_time"] >= w0) & (ev["event_time"] < w1)).to_numpy() & (u_drop < rate)
        ev = ev.filter(pl.Series(~hit))
    ev = ev.with_columns(
        (pl.col("event_type") + pl.lit(":") + pl.col("order_id")).alias("record_key"),
        pl.lit(None, pl.Utf8).alias("product_id"), pl.lit(None, pl.Float64).alias("amount"), pl.lit(None, pl.Utf8).alias("metric"),
    )
    # revision numbers: the corrected delivery is revision 1 and supersedes revision 0
    ev = ev.sort(["record_key", "available_at"]).with_columns(pl.int_range(pl.len()).over("record_key").alias("revision"))
    ev = ev.with_columns((pl.col("record_key") + ":r" + pl.col("revision").cast(pl.Utf8)).alias("event_id"))
    ev = ev.with_columns(pl.when(pl.col("revision") > 0).then(pl.col("record_key") + ":r" + (pl.col("revision") - 1).cast(pl.Utf8))
                         .alias("supersedes"))
    return ev.sort("available_at")


class EmulatorPort(EventFramePort):
    """Same contract as the real-data port; everything it serves is SYNTHETIC evidence from an EMULATOR scope."""
    event_origin = OriginClass.SYNTHETIC
    metric_origin = OriginClass.SYNTHETIC
    source_id = "emulator-chain"
    observation_mechanism = "simulated chain + observation model (delays, precision, missingness, corrections)"

    def __init__(self, observed: pl.DataFrame, params: ChainParams, om: ObservationModel, sim_start: datetime, seed: int,
                 event_rows_max: int = 200_000):
        pv = hashlib.sha1(json.dumps(asdict(params), sort_keys=True, default=str).encode()).hexdigest()[:12]
        env = EngineScope(
            id=f"emulator-chain-s{seed}", kind=EnvironmentKind.EMULATOR, data_cut=observed["available_at"].max(),
            mechanism_versions={"chain_params": pv, "observation_model": om.version(), "seed": str(seed),
                                "sim_start": sim_start.isoformat()},
            assumptions=[f"{k}: {v}" for k, v in om.labels.items() if v == "assumed"] + ["ground truth is simulator-relative"])
        root = LineageRoot(source="emulator-chain", source_version=pv, files={})
        tt = datetime.combine(params.trained_through, datetime.max.time())
        if tt >= sim_start:
            raise ValueError("gate S3: mechanisms trained through a date at/after the simulation start")
        super().__init__(observed, env, root, event_rows_max, trained_through=tt)


class SessionEmulatorPort(EmulatorPort):
    """Emulator port whose frame also holds session-layer events; serves the funnel metrics.
    Transferred shapes are referenced by transport-card id and status in the environment scope."""
    def __init__(self, observed, params, om, sim_start, seed, session_params, event_rows_max: int = 200_000):
        from ..replay_port import METRICS, SESSION_METRICS
        orders = next(m for m in METRICS if m.id == "orders_count")
        orders = orders.model_copy(update={"empirical_identities": orders.empirical_identities + ["funnel_after: checkouts_count"]})
        self.metrics = [orders if m.id == "orders_count" else m for m in METRICS] + SESSION_METRICS
        super().__init__(observed, params, om, sim_start, seed, event_rows_max)
        sp = session_params
        tt = datetime.fromisoformat(sp.trained_through)
        if tt >= sim_start:
            raise ValueError("gate S3: a session card was trained through a date at/after the simulation start")
        self.env = self.env.model_copy(update={
            "mechanism_versions": self.env.mechanism_versions | {f"card:{k}": v for k, v in sp.cards.items()},
            "assumptions": self.env.assumptions + ["session shapes transferred (see transport cards); levels anchored to the chain",
                                                   "A-13 checkout lead 1-10 min (assumed)", "A-14 non-buying visit length (assumed)"]})
