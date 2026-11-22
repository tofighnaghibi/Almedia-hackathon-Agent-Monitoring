"""A read-only EvidenceAccessPort over canonical observation wire dicts (adapter side; outside the core).

Each slice adapter translates its source into canonical observations and hands them to this port, so the core
always sees the same contracts. The port honours the data cut itself (the core re-checks), serves only the
requests its manifest declares, and has test hooks for a slow/hung provider and a failing one.
"""
from __future__ import annotations

import hashlib
import time

from causal_core.api import parse_observation
from causal_core.contracts.capabilities import CapabilityResponse, ProviderManifest

REQUEST_SHAPES = {"get_assignment_outcomes": ("assignment_outcomes",), "get_series": ("time_series", "state_trajectory"),
                  "get_aggregate": ("aggregate_stats", "scalar"), "get_strata": ("aggregate_stats",),
                  "get_event_batch": ("event_batch",), "get_panel": ("panel",)}


class CanonicalEvidencePort:
    def __init__(self, observations, provider_id: str, *, supported: tuple[str, ...] = tuple(REQUEST_SHAPES),
                 latency_ms: int | None = 5, cost_units: float | None = 0.0, hang_s: float = 0.0, fail: str | None = None):
        self._obs = [parse_observation(o) for o in observations]
        self.provider_id, self.supported = provider_id, supported
        self.latency_ms, self.cost_units, self.hang_s, self.fail = latency_ms, cost_units, hang_s, fail
        self.calls: list[str] = []

    def manifest(self) -> ProviderManifest:
        scopes = {o.scope.environment_id: o.scope for o in self._obs}
        return ProviderManifest(schema_version="2.0", provider_id=self.provider_id, manifest_version="1",
                                supported_requests=self.supported, max_rows=100_000, scopes=tuple(scopes.values()),
                                expected_latency_ms=self.latency_ms, cost_units_per_request=self.cost_units)

    def query(self, request) -> CapabilityResponse:
        self.calls.append(request.request_id)
        if self.hang_s:
            time.sleep(self.hang_s)
        if self.fail:
            raise RuntimeError(self.fail)
        fp = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        if request.request not in self.supported:
            return CapabilityResponse(schema_version="2.0", request_id=request.request_id, request_fingerprint=fp,
                                      status="UNSUPPORTED", reason=f"{request.request} not offered by {self.provider_id}")
        shapes = REQUEST_SHAPES[request.request]
        hits = []
        for o in self._obs:
            if o.variable_id not in request.variables or o.payload.shape not in shapes:
                continue
            if o.scope.environment_id != request.scope.environment_id:
                continue
            if request.request == "get_assignment_outcomes" and o.payload.study_id != request.study_id:
                continue
            dim = getattr(request, "dimension", None)
            if dim is not None and dim not in {x.dimension_id for x in o.segment}:
                continue
            w = getattr(o.payload, "window", None)
            if request.window and w and not (w.start >= request.window.start and w.end <= request.window.end):
                continue
            avail = o.clocks.available_at if o.clocks else None
            if not (avail and avail.quality == "KNOWN" and avail.value <= request.data_cut):
                continue
            hits.append(o)
        truncated = len(hits) > request.max_rows
        hits = hits[:request.max_rows]
        return CapabilityResponse(schema_version="2.0", request_id=request.request_id, request_fingerprint=fp,
                                  status="PARTIAL" if truncated else "OK", observations=tuple(hits), truncated=truncated,
                                  reason="row cap reached" if truncated else None)
