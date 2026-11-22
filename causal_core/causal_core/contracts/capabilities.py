"""Typed evidence requests and provider manifests (v3.2 §4.6, F06, I17).

A finite union. There is no fetch_anything(**kwargs). Each request carries
its budget, scope, data cut, limits, purpose and privacy class. A response
explains unsupported or partial results instead of silently returning less.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from .common import UtcDatetime
from .common import Contract, EnvironmentScope, Grain, Id, Probability, SchemaVersion, Sha256, Text, TimeWindow
from .observations import Observation

RequestName = Literal["get_series", "get_aggregate", "get_strata", "get_panel", "get_event_batch",
                      "get_assignment_outcomes", "get_intervention_history", "get_measurement",
                      "get_source_metadata"]


class _Request(Contract):
    schema_version: SchemaVersion
    request_id: Id
    variables: tuple[Id, ...] = Field(min_length=1)
    scope: EnvironmentScope
    data_cut: UtcDatetime
    window: TimeWindow | None = None
    max_rows: int = Field(default=10_000, gt=0)
    max_bytes: int = Field(default=10_000_000, gt=0)
    deadline_ms: int = Field(default=5_000, gt=0)
    purpose: Text
    privacy_class: Literal["public", "internal", "confidential", "restricted"] = "internal"


class GetSeries(_Request):
    request: Literal["get_series"]
    grain: Grain
    max_buckets: int = Field(default=168, gt=0)


class GetAggregate(_Request):
    request: Literal["get_aggregate"]


class GetStrata(_Request):
    request: Literal["get_strata"]
    dimension: Id
    top_k: int | None = Field(default=None, gt=0)
    include_other: bool = True
    bucket_grain: Grain | None = None
    max_buckets: int = Field(default=168, gt=0)


class GetPanel(_Request):
    request: Literal["get_panel"]
    unit_dimension: Id
    grain: Grain
    max_buckets: int = Field(default=168, gt=0)


class GetEventBatch(_Request):
    request: Literal["get_event_batch"]
    max_events: int = Field(default=10_000, gt=0)


class GetAssignmentOutcomes(_Request):
    request: Literal["get_assignment_outcomes"]
    study_id: Id
    include_covariance: bool = False


class GetInterventionHistory(_Request):
    request: Literal["get_intervention_history"]


class GetMeasurement(_Request):
    request: Literal["get_measurement"]
    mechanism_id: Id | None = None


class GetSourceMetadata(_Request):
    request: Literal["get_source_metadata"]


CapabilityRequest = Annotated[
    Union[GetSeries, GetAggregate, GetStrata, GetPanel, GetEventBatch, GetAssignmentOutcomes,
          GetInterventionHistory, GetMeasurement, GetSourceMetadata],
    Field(discriminator="request")]


class CapabilityResponse(Contract):
    schema_version: SchemaVersion
    request_id: Id
    request_fingerprint: Sha256
    status: Literal["OK", "PARTIAL", "UNSUPPORTED", "FAILED", "TIMEOUT", "BUDGET_EXCEEDED"]
    observations: tuple[Observation, ...] = ()
    completeness: Probability | None = None
    truncated: bool = False
    reason: Text | None = None
    available_at: UtcDatetime | None = None

    @model_validator(mode="after")
    def _explained(self) -> "CapabilityResponse":
        if self.status != "OK" and self.reason is None:
            raise ValueError(f"status {self.status} must say why")
        if self.status in {"UNSUPPORTED", "FAILED", "TIMEOUT"} and self.observations:
            raise ValueError(f"status {self.status} cannot carry observations")
        if self.status == "OK" and self.truncated:
            raise ValueError("a truncated response is PARTIAL, not OK")
        return self


class ProviderManifest(Contract):
    schema_version: SchemaVersion
    provider_id: Id
    manifest_version: Text
    supported_requests: tuple[RequestName, ...] = Field(min_length=1)
    supports_covariance: bool = False
    max_rows: int = Field(gt=0)
    scopes: tuple[EnvironmentScope, ...] = Field(min_length=1)
    # None = UNKNOWN. A planner must treat unknown latency/cost as a penalty or veto, never as free (CP-002).
    expected_latency_ms: int | None = Field(default=None, gt=0)
    cost_units_per_request: float | None = Field(default=None, ge=0)
