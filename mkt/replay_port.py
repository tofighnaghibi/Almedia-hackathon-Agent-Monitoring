"""EvidenceAccessPort over REAL Olist data (phase 2: real-only integration, no simulator).

Serves v3 Observations from the canonical Olist events (phase-1 semantics). Read-only and as-of:
nothing whose available_at is later than the request's as_of is ever returned.

Assumption (recorded in the EngineScope): Olist has no ingestion/arrival times, so in this
real-replay environment available_at = event_time. Phase 4 replaces this with an observation model.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
from typing import Union

import polars as pl

from . import pipeline, registry
from .config import Config
from .engine_types import (CapabilitySpec, EnvironmentKind, EngineScope, EngineEvidence, EvidenceRequest, EvidenceResult,
                 FailureCode, InterventionEvent, InterventionSpec, LineageRoot, EngineMetric, EngineObservation, OriginClass,
                 EngineLineage, Refusal, EngineVariable, VariableKind)

EVENT_VARS = {   # variable id -> (canonical event_type, kind, value schema, missingness note)
    "order_placed": ("order_created", VariableKind.EVENT, {"amount": "float[BRL]", "promised_days": "float[days]"}, "none known"),
    "item_sold": ("order_item", VariableKind.EVENT, {"seller_id": "id", "product_id": "id", "price": "float[BRL]", "freight": "float[BRL]"}, "none known"),
    "payment_approved": ("payment_approved", VariableKind.EVENT, {"amount": "float[BRL]"}, "absent for some canceled orders"),
    "handed_to_carrier": ("handed_to_carrier", VariableKind.EVENT, {}, "absent for undelivered/canceled orders; 1,359 precede approval"),
    "delivered": ("delivered", VariableKind.EVENT, {"days_late": "float[days]"}, "censored: orders not yet delivered at the cut"),
    "survey_sent": ("survey_sent", VariableKind.EVENT, {}, "date only"),
    "review_score": ("review_submitted", VariableKind.CATEGORICAL, {"score": "int[1-5]"}, "~1% of orders have no answered survey"),
}
METRICS = [
    EngineMetric(id="orders_count", value_schema={"value": "int"}, unit="orders", scope="orders", formula="count(order_placed)",
                     grain=["day", "week", "month"], population="all orders",
                     empirical_identities=["orders_count ~ payments_count (each order is paid once; Olist: 1 of 99,441 without payment)"]),
    EngineMetric(id="payments_count", value_schema={"value": "int"}, unit="orders", scope="orders", formula="count(payment_approved)",
                     grain=["day", "week", "month"], population="orders with an approved payment (by approval time)",
                     empirical_identities=["payments_count ~ orders_count"]),
    EngineMetric(id="gmv", value_schema={"value": "float"}, unit="BRL", scope="orders", formula="sum(order_placed.amount)",
                     grain=["day", "week", "month"], population="all orders", empirical_identities=["gmv = sum(item_sold.price + item_sold.freight)"]),
    EngineMetric(id="late_share", value_schema={"value": "float"}, unit="share", scope="delivered orders",
                     formula="count(delivered[days_late>0]) / count(delivered)", grain=["week", "month"],
                     population="orders delivered within the window (by delivery time)"),
    EngineMetric(id="late_share_cohort", value_schema={"value": "float"}, unit="share", scope="orders",
                     formula="count(order_placed with delivered.days_late>0) / count(order_placed delivered by as_of)",
                     grain=["week", "month"], population="orders PLACED within the window and delivered by as_of (purchase cohort)"),
    EngineMetric(id="mean_promised_days", value_schema={"value": "float"}, unit="days", scope="orders",
                     formula="mean(order_placed.promised_days)", grain=["week", "month"], population="orders placed within the window"),
    EngineMetric(id="mean_delivery_days", value_schema={"value": "float"}, unit="days", scope="delivered orders",
                     formula="mean(delivered.t - order_placed.t)", grain=["week", "month"],
                     population="orders placed within the window that were delivered by as_of"),
    EngineMetric(id="mean_review_score", value_schema={"value": "float"}, unit="1-5", scope="answered surveys",
                     formula="mean(review_score)", grain=["week", "month"], population="surveys answered within the window"),
]
# component counts so that derived shares are declared ratios (taxonomy.py)
METRICS += [
    EngineMetric(id="deliveries_count", value_schema={"value": "int"}, unit="orders", scope="delivered orders",
                     formula="count(delivered)", grain=["day", "week", "month"], population="deliveries in the window"),
    EngineMetric(id="late_deliveries_count", value_schema={"value": "int"}, unit="orders", scope="delivered orders",
                     formula="count(delivered[days_late>0])", grain=["day", "week", "month"], population="deliveries in the window"),
    EngineMetric(id="cohort_delivered_count", value_schema={"value": "int"}, unit="orders", scope="orders",
                     formula="count(order_placed delivered by as_of)", grain=["week", "month"], population="order cohort"),
    EngineMetric(id="cohort_late_count", value_schema={"value": "int"}, unit="orders", scope="orders",
                     formula="count(order_placed delivered late by as_of)", grain=["week", "month"], population="order cohort"),
]
METRIC_BY_ID = {m.id: m for m in METRICS}
# Session-layer metrics (served only by ports whose frame has session events). Funnel order is declared
# through identities "funnel_after: <previous stage>" (provisional notation, see P1_2_SESSION_LAYER_PREREG.md).
SESSION_METRICS = [
    EngineMetric(id="sessions_count", value_schema={"value": "int"}, unit="sessions", scope="sessions", formula="count(session_started)",
                     grain=["day", "week", "month"], population="all sessions (buying, research, non-buying)"),
    EngineMetric(id="cart_adds_count", value_schema={"value": "int"}, unit="sessions", scope="sessions", formula="count(cart_added)",
                     grain=["day", "week", "month"], population="sessions with a cart add", empirical_identities=["funnel_after: sessions_count"]),
    EngineMetric(id="checkouts_count", value_schema={"value": "int"}, unit="sessions", scope="sessions", formula="count(checkout_started)",
                     grain=["day", "week", "month"], population="sessions that started checkout", empirical_identities=["funnel_after: cart_adds_count"]),
]
SESSION_METRICS.append(EngineMetric(id="views_count", value_schema={"value": "int"}, unit="views", scope="sessions",
                                        formula="count(item_viewed)", grain=["day", "week", "month"], population="items viewed"))
SESSION_EVENT = {"sessions_count": "session_started", "cart_adds_count": "cart_added", "checkouts_count": "checkout_started",
                 "views_count": "item_viewed"}
TRUNC = {"day": "1d", "week": "1w", "month": "1mo"}


class EventFramePort:
    """Shared query engine over a canonical event frame (real replay or emulator).

    The frame needs: event_id, event_type, event_time, available_at, order_id, seller_id, product_id, region,
    amount, value, metric; optional record_key + revision for late corrections (supersession).
    """
    event_origin = OriginClass.REAL
    metric_origin = OriginClass.DERIVED
    source_id = "olist"
    observation_mechanism = "Olist order-system export (Kaggle 2018 release)"

    def __init__(self, events: pl.DataFrame, env: EngineScope, root: LineageRoot, event_rows_max: int = 200_000,
                 trained_through: datetime | None = None):
        self.events = events
        self.env = env
        self.root = root
        self.event_rows_max = event_rows_max
        self.cut = env.data_cut or events["event_time"].max()
        self.trained_through = trained_through

    # -------------------------------------------------------------------------------------------
    metrics: list = METRICS

    def variables(self) -> list[EngineVariable]:
        out = [EngineVariable(id=k, kind=v[1], value_schema=v[2], scope="orders", description=f"Olist {v[0]} event")
               for k, v in EVENT_VARS.items()]
        return out + list(self.metrics)

    def capabilities(self) -> CapabilitySpec:
        shapes = {k: ["event_sequence"] for k in EVENT_VARS} | {m.id: ["series", "aggregate"] for m in self.metrics}
        return CapabilitySpec(
            source_id=self.source_id, environment_id=self.env.id, variables=list(shapes), accepted_shapes=shapes,
            supported_requests=["query"], required_metadata=["as_of"],
            assumptions=self.env.assumptions + ["observational data only: no randomized studies in this source"],
            cost={"event_rows_per_query_max": float(self.event_rows_max)},
            failure_codes=[FailureCode.UNSUPPORTED_SHAPE, FailureCode.UNKNOWN_VARIABLE, FailureCode.NO_STUDIES_IN_SOURCE,
                           FailureCode.BUDGET_EXCEEDED])

    # -------------------------------------------------------------------------------------------
    def _evidence(self, origin: OriginClass, transforms: list[str]) -> EngineEvidence:
        eid = "ev:" + hashlib.sha1("|".join([origin.value, self.env.id, *transforms]).encode()).hexdigest()[:16]
        return EngineEvidence(id=eid, origin=origin, environment_id=self.env.id,
                              lineage=EngineLineage(roots=[self.root], transformations=transforms, trained_through=self.trained_through))

    def _visible(self, as_of: datetime) -> dict[str, pl.DataFrame]:
        """Everything knowable at as_of (arrived by then; latest revision of each record), split by event type.
        Cached per as_of: the event frame is immutable, and a core asks many questions at the same data cut."""
        cache = self.__dict__.setdefault("_vis_cache", {})
        if as_of not in cache:
            v = self.events.filter(pl.col("available_at") <= as_of)
            if "revision" in v.columns:
                v = v.sort("revision").unique(subset=["record_key"], keep="last", maintain_order=True)
            cache.clear()                                       # keep one data cut: memory stays bounded
            cache[as_of] = {k[0]: g.sort("event_time") for k, g in v.partition_by("event_type", as_dict=True).items()}
        return cache[as_of]

    def visible_of(self, as_of: datetime, event_type: str) -> pl.DataFrame:
        return self._visible(as_of).get(event_type, self.events.clear())

    def query(self, req: EvidenceRequest) -> Union[EvidenceResult, Refusal]:
        cap = self.capabilities()
        if req.variable_id not in cap.accepted_shapes:
            return Refusal(code=FailureCode.UNKNOWN_VARIABLE, reason=f"{req.variable_id!r} is not served by this source")
        if req.shape == "assignment":
            return Refusal(code=FailureCode.NO_STUDIES_IN_SOURCE, reason="Olist contains no randomized assignments")
        if req.shape not in cap.accepted_shapes[req.variable_id]:
            return Refusal(code=FailureCode.UNSUPPORTED_SHAPE,
                           reason=f"{req.variable_id} is served as {cap.accepted_shapes[req.variable_id]}, not {req.shape}")
        vis = self._visible(req.as_of)
        flags = []
        if req.window_end and req.window_end > req.as_of:
            flags.append("window extends past as_of: values after as_of are not included (incomplete, not zero)")
        if req.variable_id in EVENT_VARS:
            return self._events(req, vis, flags)
        return self._metric(req, vis, flags)

    def _window(self, df: pl.DataFrame, req: EvidenceRequest) -> pl.DataFrame:
        if req.window_start:
            df = df.filter(pl.col("event_time") >= req.window_start)
        if req.window_end:
            df = df.filter(pl.col("event_time") < req.window_end)
        for k, v in req.filters.items():
            df = df.filter(pl.col(k) == v)
        return df

    def _events(self, req, vis, flags):
        etype, _, schema, missing = EVENT_VARS[req.variable_id]
        df = self._window(vis.get(etype, self.events.clear()), req)
        if df.height > self.event_rows_max:
            return Refusal(code=FailureCode.BUDGET_EXCEEDED, reason=f"{df.height:,} rows; narrow the window or use a metric")
        cols = ["event_id", "event_time", "order_id", "seller_id", "product_id", "region", "amount", "value", "metric"]
        ev = self._evidence(self.event_origin, [f"{self.source_id} {self.env.mechanism_versions}", f"select {etype}"])
        obs = EngineObservation(id=f"obs:{req.variable_id}:{ev.id}:{req.as_of.isoformat()}", variable_id=req.variable_id, shape="event_sequence",
                          payload=df.select(cols).to_dicts(), window_start=req.window_start, window_end=req.window_end,
                          available_at=df["available_at"].max() if df.height else req.as_of, population="orders",
                          observation_mechanism=self.observation_mechanism, missingness=missing,
                          quality_flags=flags, evidence_id=ev.id)
        return EvidenceResult(observations=[obs], evidence=[ev])

    def _metric(self, req, vis, flags):
        m = {x.id: x for x in self.metrics}[req.variable_id]
        grain = req.grain or "week"
        if req.shape == "series" and grain not in m.grain:
            return Refusal(code=FailureCode.UNSUPPORTED_SHAPE, reason=f"{m.id} grains are {m.grain}")
        base, val = METRIC_RECIPES[m.id](_Frames(self, vis, req))
        flags = flags + [f for f in [METRIC_FLAGS.get(m.id)] if f]
        ev = self._evidence(self.metric_origin, [f"{self.source_id} {self.env.mechanism_versions}", f"metric {m.id}: {m.formula}"])
        if req.shape == "aggregate":
            payload = {"value": base.select(val).item() if base.height else None, "n": base.height}
        else:
            s = (base.with_columns(pl.col("event_time").dt.truncate(TRUNC[grain]).alias("t"))
                 .group_by("t").agg(val.alias("value"), pl.len().alias("n")).sort("t"))
            payload = [{"t": r["t"].isoformat(), "value": r["value"], "n": r["n"]} for r in s.to_dicts()]
        obs = EngineObservation(id=f"obs:{m.id}:{grain}:{ev.id}:{req.as_of.isoformat()}", variable_id=m.id, shape=req.shape, payload=payload,
                          window_start=req.window_start, window_end=req.window_end,
                          available_at=base["available_at"].max() if base.height else req.as_of, population=m.population,
                          observation_mechanism=f"derived from {self.source_id} events by {m.formula}", missingness="see source variables",
                          quality_flags=flags, evidence_id=ev.id)
        return EvidenceResult(observations=[obs], evidence=[ev])


class _Frames:
    """What a metric recipe may read: windowed events of one type, and the purchase cohort joined to deliveries."""

    def __init__(self, port: EventFramePort, vis: dict, req: EvidenceRequest):
        self.port, self.vis, self.req = port, vis, req

    def of(self, event_type: str) -> pl.DataFrame:
        return self.port._window(self.vis.get(event_type, self.port.events.clear()), self.req)

    def cohort(self) -> pl.DataFrame:
        """Orders placed in the window that were delivered by as_of, with days_late and delivery time."""
        d = self.vis.get("delivered", self.port.events.clear()).select("order_id", pl.col("value").alias("days_late"),
                                                                       pl.col("event_time").alias("t_del"))
        return self.of("order_created").join(d, on="order_id", how="inner")


COUNT = pl.len()
LATE = pl.col("value") > 0
METRIC_RECIPES = {   # metric id -> frames -> (rows, value expression); one line per metric
    "orders_count": lambda f: (f.of("order_created"), COUNT),
    "payments_count": lambda f: (f.of("payment_approved"), COUNT),
    "gmv": lambda f: (f.of("order_created"), pl.col("amount").sum()),
    "mean_promised_days": lambda f: (f.of("order_created"), pl.col("value").mean()),
    "late_share": lambda f: (f.of("delivered"), LATE.mean()),
    "deliveries_count": lambda f: (f.of("delivered"), COUNT),
    "late_deliveries_count": lambda f: (f.of("delivered").filter(LATE), COUNT),
    "cohort_delivered_count": lambda f: (f.cohort(), COUNT),
    "cohort_late_count": lambda f: (f.cohort().filter(pl.col("days_late") > 0), COUNT),
    "late_share_cohort": lambda f: (f.cohort(), (pl.col("days_late") > 0).mean()),
    "mean_delivery_days": lambda f: (f.cohort(), ((pl.col("t_del") - pl.col("event_time")).dt.total_hours() / 24).mean()),
    "mean_review_score": lambda f: (f.of("review_submitted"), pl.col("value").mean()),
    **{mid: (lambda f, e=etype: (f.of(e), COUNT)) for mid, etype in SESSION_EVENT.items()},
}
METRIC_FLAGS = {
    "late_share": "denominator = deliveries completed by as_of; undelivered orders are censored, not on time",
    "late_share_cohort": "purchase cohort: only orders delivered by as_of; recent cohorts are biased toward fast (on-time) deliveries",
    "mean_delivery_days": "only orders delivered by as_of: recent weeks are biased toward fast deliveries",
}


class OlistReplayPort(EventFramePort):
    """Real Olist data. Olist has no arrival times, so available_at = event_time (recorded assumption)."""

    def __init__(self, cfg: Config | None = None, event_rows_max: int = 200_000):
        cfg = cfg or Config.load()
        canon = pipeline.canonical_olist(cfg)
        self.manifest = registry.load("olist", cfg.artifacts)
        ev = pl.read_parquet(canon / "events.parquet").filter(pl.col("event_time").is_not_null())
        events = ev.with_columns(pl.col("event_time").alias("available_at"))
        env = EngineScope(
            id="olist-real-replay", kind=EnvironmentKind.REAL_REPLAY, data_cut=events["event_time"].max(),
            mechanism_versions={"adapter": canon.name},
            assumptions=["available_at = event_time (Olist exports have no arrival times; phase 4 adds an observation model)",
                         "timestamps are naive local time (assumed America/Sao_Paulo)"])
        root = LineageRoot(source="olist", source_version=self.manifest.version, files=self.manifest.files)
        super().__init__(events, env, root, event_rows_max)
