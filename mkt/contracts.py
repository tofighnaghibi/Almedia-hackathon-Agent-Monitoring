"""Canonical contracts (docs/ARCHITECTURE.md §9).

Events are stored column-wise: one wide, typed table where entity references are role
columns (buyer_id, seller_id, ...) and the payload is a few typed value columns. This keeps
Parquet/DuckDB efficient while staying domain-neutral: the causal engine only needs
event_type, times, entity roles and values.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Literal

import polars as pl

PROV_CLASSES = ("observed", "derived", "matched", "calibrated", "mechanism", "assumed", "unknown")
ProvClass = Literal["observed", "derived", "matched", "calibrated", "mechanism", "assumed", "unknown"]

# Event columns shared by world events and observable events.
EVENT_SCHEMA: dict[str, pl.DataType] = {
    "event_id": pl.Utf8,
    "event_type": pl.Utf8,
    "event_time": pl.Datetime("us"),     # when it happened (marketplace local time, naive)
    "available_at": pl.Datetime("us"),   # earliest time a monitor may see it (observable only)
    "stream": pl.Utf8,                   # logging pipeline the event travels through
    "buyer_id": pl.Utf8,
    "session_id": pl.Utf8,
    "seller_id": pl.Utf8,
    "product_id": pl.Utf8,
    "order_id": pl.Utf8,
    "device": pl.Utf8,
    "region": pl.Utf8,
    "category": pl.Utf8,
    "amount": pl.Float64,                # money, in `currency`
    "currency": pl.Utf8,
    "value": pl.Float64,                 # type-specific scalar: score, error rate, promised days...
    "metric": pl.Utf8,                   # for event_type == 'metric_sample': metric name
    "supersedes": pl.Utf8,               # late correction of an earlier event_id
    "prov_class": pl.Utf8,
    "prov_id": pl.Utf8,
}

# Event types and the stream each travels through by default.
EVENT_STREAMS = {
    "session_start": "web", "product_view": "web", "add_to_cart": "web",
    "checkout_start": "web", "checkout_error": "web", "order_created": "web",
    "order_item": "web", "order_unavailable": "web", "payment_approved": "payments",
    "handed_to_carrier": "logistics", "delivered": "logistics",
    "survey_sent": "reviews", "review_submitted": "reviews", "order_status_snapshot": "ops",
    "price_change": "catalog", "stock_out": "catalog", "restock": "catalog",
    "metric_sample": "telemetry", "announcement": "ops",
}

# Columns that must never appear in observable data (latent / privileged).
TRUTH_ONLY_COLUMNS = {"quality", "reliability", "latent", "true_cause", "counterfactual", "effect"}


def empty_events(with_available: bool = True) -> pl.DataFrame:
    schema = dict(EVENT_SCHEMA)
    if not with_available:
        schema.pop("available_at")
    return pl.DataFrame(schema=schema)


def conform(df: pl.DataFrame, with_available: bool = True) -> pl.DataFrame:
    """Add missing canonical columns as nulls and order/cast columns canonically."""
    schema = dict(EVENT_SCHEMA)
    if not with_available:
        schema.pop("available_at")
    cols = []
    for name, dtype in schema.items():
        cols.append(pl.col(name).cast(dtype) if name in df.columns else pl.lit(None, dtype).alias(name))
    return df.select(cols)


@dataclass
class Intervention:
    id: str
    layer: Literal["world", "observation"]
    target: str
    operator: Literal["set", "shift", "scale"]
    value: float
    selector: str = ""
    window: tuple[datetime, datetime] | None = None
    observable: bool = False             # announced to the monitor (e.g. a release note)
    note: str = ""

    @classmethod
    def from_dict(cls, d: dict) -> "Intervention":
        w = d.get("window")
        win = (datetime.fromisoformat(w[0]), datetime.fromisoformat(w[1])) if w else None
        return cls(id=d["id"], layer=d.get("layer", "world"), target=d["target"],
                   operator=d.get("operator", "set"), value=float(d["value"]),
                   selector=d.get("selector", ""), window=win,
                   observable=bool(d.get("observable", False)), note=d.get("note", ""))

    def to_dict(self) -> dict:
        d = asdict(self)
        if self.window:
            d["window"] = [self.window[0].isoformat(), self.window[1].isoformat()]
        return d


@dataclass
class GroundTruthRecord:
    scenario_id: str
    kind: Literal["cause", "effect", "onset", "latent", "identifiability", "counterfactual", "limitation"]
    target: str
    value: Any = None
    segment: str = ""
    window_start: datetime | None = None
    window_end: datetime | None = None
    identifiable: bool | None = None
    notes: str = ""


@dataclass
class ProvenanceRecord:
    prov_id: str
    prov_class: ProvClass
    transform: str
    transform_v: str
    inputs: list[str] = field(default_factory=list)
    params_hash: str = ""
    seed: int | None = None
    assumptions: list[str] = field(default_factory=list)
