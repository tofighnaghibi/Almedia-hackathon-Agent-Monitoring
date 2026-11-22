"""EvidenceAccessPort backed by an emulator World (read-only adapter; outside the core).

Serves only the *visible* observations, never `World.truth`. It honours the data cut on the
provider side: nothing is returned unless its availability clock is KNOWN and at or before the
request's data cut. The core still re-checks this (defence in depth).
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime

from causal_core.api import parse_observation
from causal_core.contracts.capabilities import CapabilityResponse, ProviderManifest


class EmulatorEvidencePort:
    SUPPORTED = ("get_aggregate", "get_series")

    def __init__(self, world, provider_id: str = "emu-coldchain-port", *, leak: bool = False):
        self._observations = [parse_observation(o) for o in world.observations]
        self.provider_id = provider_id
        self.leak = leak                      # test hook: a faulty provider that ignores the data cut
        self.requests: list[str] = []

    def manifest(self) -> ProviderManifest:
        scope = self._observations[0].scope
        return ProviderManifest(schema_version="2.0", provider_id=self.provider_id, manifest_version="1",
                                supported_requests=self.SUPPORTED, max_rows=10_000, scopes=(scope,),
                                expected_latency_ms=5, cost_units_per_request=0.0)

    def query(self, request) -> CapabilityResponse:
        fp = hashlib.sha256(request.model_dump_json().encode()).hexdigest()
        self.requests.append(request.request_id)
        if request.request not in self.SUPPORTED:
            return CapabilityResponse(schema_version="2.0", request_id=request.request_id, request_fingerprint=fp,
                                      status="UNSUPPORTED", reason=f"{request.request} not offered by {self.provider_id}")
        cut: datetime = request.data_cut
        hits = []
        for o in self._observations:
            if o.variable_id not in request.variables:
                continue
            p = o.payload
            w = getattr(p, "window", None)
            if request.window and w and not (w.start >= request.window.start and w.end <= request.window.end):
                continue
            avail = o.clocks.available_at if o.clocks else None
            if not self.leak and not (avail and avail.quality == "KNOWN" and avail.value <= cut):
                continue
            hits.append(o)
        truncated = len(hits) > request.max_rows
        hits = hits[:request.max_rows]
        return CapabilityResponse(schema_version="2.0", request_id=request.request_id, request_fingerprint=fp,
                                  status="PARTIAL" if truncated else "OK", observations=tuple(hits),
                                  truncated=truncated, reason="row cap reached" if truncated else None)


def admission_requests(world, data_cut: datetime, *, scope_override: dict | None = None) -> list[dict]:
    """Translate the emulator's declared world into admission-request wire dicts, parents before children."""
    from emulator.coldchain import declared_structure  # adapter side only; the core never sees this import

    order = ["orders", "dispatched_on_time", "on_time_dispatch_rate", "freezer_temp_c", "door_open", "compressor_mode"]
    out = []
    for vid in order:
        d = world.definitions[vid]
        out.append({"schema_version": "2.0", "request_id": f"adm-{vid}", "source_id": "emu-coldchain",
                    "record_id": f"def:{vid}:v1", "attestation": {"source_id": "emu-coldchain", "method": "adapter_declared"},
                    "scope": scope_override or d["scope"], "data_cut": data_cut.isoformat(), "definition": d})
    decl = declared_structure(data_cut)
    out.append({"schema_version": "2.0", "request_id": "adm-structure", "source_id": "emu-coldchain",
                "record_id": "structure:v1", "attestation": {"source_id": "emu-coldchain", "method": "adapter_declared"},
                "scope": world.definitions["orders"]["scope"], "data_cut": data_cut.isoformat(),
                "nodes": decl["nodes"], "edges": decl["edges"]})
    return out


def _canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True)
