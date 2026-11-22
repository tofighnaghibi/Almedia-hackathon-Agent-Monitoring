"""Observations with typed payloads (v3.2 §2.1 ValuePayload, §2.2 clocks).

No universal numerator/denominator/time_bucket: each payload shape carries
only what that shape means. AggregateStats never defaults to IID and has no
covariance number at all, only an optional reference to a covariance record.
Absent covariance is UNKNOWN, never zero (I10, C05).
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import AwareDatetime, Field, model_validator

from .common import (Clock, Contract, EnvironmentScope, Id, Probability, ProvenanceLineage,
                     SchemaVersion, Text, TimeWindow)


class Scalar(Contract):
    shape: Literal["scalar"]
    value: float


class CategoricalValue(Contract):
    shape: Literal["categorical"]
    category: Text


class AggregateStats(Contract):
    shape: Literal["aggregate_stats"]
    window: TimeWindow | None = None
    n: int = Field(ge=0)
    numerator: float | None = None
    denominator: float | None = Field(default=None, ge=0)
    total: float | None = None
    mean: float | None = None
    sum_sq: float | None = Field(default=None, ge=0)
    se: float | None = Field(default=None, ge=0)
    iid: Literal["UNKNOWN", "ASSERTED_BY_SOURCE", "NOT_IID"] = "UNKNOWN"
    covariance_ref: Id | None = None   # pointer to a covariance record; absence = UNKNOWN

    @model_validator(mode="after")
    def _coherent(self) -> "AggregateStats":
        if (self.numerator is None) != (self.denominator is None):
            raise ValueError("numerator and denominator come together or not at all")
        if self.numerator is None and self.total is None and self.mean is None:
            raise ValueError("aggregate needs numerator/denominator, total or mean")
        if self.mean is not None and self.n == 0:
            raise ValueError("a mean over n = 0 is undefined")
        return self


class TimePoint(Contract):
    t: AwareDatetime
    value: float | None


class TimeSeries(Contract):
    shape: Literal["time_series"]
    points: tuple[TimePoint, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _increasing(self) -> "TimeSeries":
        ts = [p.t for p in self.points]
        if any(b <= a for a, b in zip(ts, ts[1:])):
            raise ValueError("time series timestamps must be strictly increasing (duplicate or DST-folded time?)")
        return self


class PanelCell(Contract):
    unit: Id
    period: int
    value: float | None


class Panel(Contract):
    shape: Literal["panel"]
    cells: tuple[PanelCell, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> "Panel":
        keys = [(c.unit, c.period) for c in self.cells]
        if len(set(keys)) != len(keys):
            raise ValueError("duplicate (unit, period) cell")
        return self


Attribute = str | int | float | bool | None


class EventRecord(Contract):
    event_id: Id
    occurred_at: Clock | None = None
    attributes: dict[Id, Attribute] = {}


class EventBatch(Contract):
    shape: Literal["event_batch"]
    events: tuple[EventRecord, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _unique(self) -> "EventBatch":
        ids = [e.event_id for e in self.events]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate event_id in batch")
        return self


class AssignedUnit(Contract):
    unit_id: Id
    arm: Id
    outcome: float | None = None            # None = attrited / not observed
    exposure: Literal[0, 1] | None = None   # recorded take-up, if any
    cluster_id: Id | None = None


class AssignmentOutcomes(Contract):
    shape: Literal["assignment_outcomes"]
    study_id: Id
    arms: tuple[Id, ...] = Field(min_length=2)
    units: tuple[AssignedUnit, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> "AssignmentOutcomes":
        if len(set(self.arms)) != len(self.arms):
            raise ValueError("arms must be unique")
        ids = [u.unit_id for u in self.units]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate unit_id")
        stray = {u.arm for u in self.units} - set(self.arms)
        if stray:
            raise ValueError(f"units assigned to undeclared arms: {sorted(stray)}")
        with_cluster = sum(u.cluster_id is not None for u in self.units)
        if 0 < with_cluster < len(self.units):
            raise ValueError("cluster_id must be present for all units or none")
        return self


class StateSample(Contract):
    t: AwareDatetime
    state: float | Text


class StateTrajectory(Contract):
    shape: Literal["state_trajectory"]
    samples: tuple[StateSample, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _increasing(self) -> "StateTrajectory":
        ts = [s.t for s in self.samples]
        if any(b <= a for a, b in zip(ts, ts[1:])):
            raise ValueError("state samples must be strictly increasing in time")
        return self


ValuePayload = Annotated[
    Union[Scalar, CategoricalValue, AggregateStats, TimeSeries, Panel, EventBatch,
          AssignmentOutcomes, StateTrajectory],
    Field(discriminator="shape")]

# which shapes a variable kind may carry (anything else is a coercion)
ALLOWED_SHAPES: dict[str, frozenset[str]] = {
    "metric": frozenset({"scalar", "aggregate_stats", "time_series", "panel", "assignment_outcomes"}),
    "state": frozenset({"scalar", "categorical", "time_series", "state_trajectory", "panel"}),
    "event": frozenset({"event_batch"}),
    "categorical": frozenset({"categorical"}),
}


class DataQuality(Contract):
    completeness: Probability | None = None
    maturity: Literal["MATURE", "IMMATURE", "UNKNOWN"] = "UNKNOWN"
    missingness: Literal["none_declared", "declared", "unknown"] = "unknown"
    selection: Literal["none_declared", "declared", "unknown"] = "unknown"


class ObservationClocks(Contract):
    observed_at: Clock | None = None    # None = not applicable (timeless/static)
    available_at: Clock | None = None
    as_of: AwareDatetime | None = None
    ingested_at: AwareDatetime | None = None
    source_timezone: Text | None = None

    @model_validator(mode="after")
    def _causal_order(self) -> "ObservationClocks":
        o, a = self.observed_at, self.available_at
        if o and a and o.quality == a.quality == "KNOWN" and a.value < o.value:
            raise ValueError("available_at precedes observed_at")
        return self


class ObservationMechanismSpec(Contract):
    mechanism_id: Id
    kind: Literal["SAMPLING", "SELECTION", "CENSORING", "MEASUREMENT", "REPORTING_LAG"]
    affects: tuple[Id, ...] = Field(min_length=1)
    description: Text


class Observation(Contract):
    schema_version: SchemaVersion
    observation_id: Id
    variable_id: Id
    payload: ValuePayload
    source_id: Id
    record_key: Id
    revision: int = Field(ge=1)
    lineage: ProvenanceLineage
    scope: EnvironmentScope
    clocks: ObservationClocks | None = None
    dq: DataQuality = DataQuality()
    observation_mechanisms: tuple[Id, ...] = ()

    @model_validator(mode="after")
    def _source_consistent(self) -> "Observation":
        if self.lineage.source_id != self.source_id:
            raise ValueError("lineage.source_id differs from source_id")
        return self
