"""M5 telemetry adapter: redacted collector span export -> Contracts observations (SANDBOX_OBSERVED).

Per minute and service: requests (server spans), errors (status ERROR), error_rate, latency_p95_ms.
Spans carry wall-clock time; storefront windows run compressed, so each window records a linear
wall -> simulated-time mapping (declared as an observation mechanism, not hidden).
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import polars as pl
from pydantic import TypeAdapter

from causal_core.contracts.capabilities import CapabilityRequest, CapabilityResponse, ProviderManifest
from causal_core.contracts.common import SCHEMA_VERSION, Clock, EnvironmentScope, ProvenanceLineage, SourceAttestation
from causal_core.contracts.evidence import EvidenceRecord
from causal_core.contracts.observations import AggregateStats, DataQuality, Observation, ObservationClocks, TimePoint, TimeSeries
from causal_core.contracts.variables import MetricDefinition, MetricTimeSemantics, NumericValueSchema, PopulationCoverage

U = timezone.utc
_REQ = TypeAdapter(CapabilityRequest)
STATS = ("requests", "errors", "error_rate", "latency_p95_ms")


@dataclass(frozen=True)
class WindowMap:
    wall_start: datetime
    wall_end: datetime
    sim_start: datetime
    sim_end: datetime

    def to_sim(self, wall: datetime) -> datetime:
        f = (wall - self.wall_start) / (self.wall_end - self.wall_start)
        return self.sim_start + f * (self.sim_end - self.sim_start)


def load_spans(path: pathlib.Path) -> pl.DataFrame:
    rows = []
    for line in open(path):
        rec = json.loads(line)
        for rs in rec.get("resourceSpans", []):
            svc = next((a["value"].get("stringValue") for a in rs.get("resource", {}).get("attributes", []) if a["key"] == "service.name"), "?")
            for ss in rs.get("scopeSpans", []):
                for sp in ss.get("spans", []):
                    if sp.get("kind") != 2:                      # SERVER spans = requests a service handled
                        continue
                    t0, t1 = int(sp["startTimeUnixNano"]), int(sp["endTimeUnixNano"])
                    rows.append((svc, t0, (t1 - t0) / 1e6, sp.get("status", {}).get("code", 0) == 2))
    return pl.DataFrame(rows, schema=["service", "t_ns", "dur_ms", "error"], orient="row")


class TelemetryPort:
    """Contracts.ports.EvidenceAccessPort over storefront telemetry (read-only)."""

    def __init__(self, spans: pl.DataFrame, windows: list[WindowMap], seed: int, generator_version: str):
        self.scope = EnvironmentScope(environment_id=f"emulator-storefront-s{seed}", environment_kind="emulator",
                                      generator_version=generator_version)
        wall = spans.with_columns(pl.from_epoch("t_ns", time_unit="ns").dt.replace_time_zone("UTC").alias("wall"))
        parts = []
        for w in windows:
            x = wall.filter((pl.col("wall") >= w.wall_start) & (pl.col("wall") < w.wall_end))
            parts.append(x.with_columns(pl.col("wall").map_elements(w.to_sim, return_dtype=pl.Datetime("us", "UTC")).alias("t")))
        self.df = pl.concat(parts) if parts else wall.with_columns(pl.col("wall").alias("t")).head(0)
        self.services = sorted(self.df["service"].unique().to_list())
        self._gen = generator_version

    def manifest(self) -> ProviderManifest:
        return ProviderManifest(schema_version="2.0", provider_id="storefront-telemetry", manifest_version="mkt-telemetry-1",
                                supported_requests=("get_series", "get_aggregate"), max_rows=100_000, scopes=(self.scope,))

    def definitions(self) -> tuple:
        att = SourceAttestation(source_id="storefront-telemetry", method="adapter_declared")
        out = []
        for svc in self.services:
            for st in STATS:
                out.append(MetricDefinition(schema_version=SCHEMA_VERSION, variable_id=f"{svc}.{st}", scope=self.scope, provenance=att,
                                            entity_ref="service",
                                            population_coverage=PopulationCoverage(population_id=f"{svc}_server_spans", coverage="full"),
                                            kind="metric", aggregation_kind={"requests": "count", "errors": "count",
                                                                             "error_rate": "mean", "latency_p95_ms": "quantile"}[st],
                                            value_schema=NumericValueSchema(type="numeric", dtype={"requests": "count", "errors": "count",
                                                                            "error_rate": "proportion", "latency_p95_ms": "real"}[st], minimum=0.0),
                                            time_semantics=MetricTimeSemantics(grain="1m", window="tumbling"),
                                            description=f"{st} of {svc} server spans (redacted OTLP export)"))
        return tuple(out)

    def query(self, request) -> CapabilityResponse:
        req = _REQ.validate_python(request) if isinstance(request, dict) else request
        fp = hashlib.sha256(req.model_dump_json().encode()).hexdigest()

        def resp(status, reason=None, obs=()):
            return CapabilityResponse(schema_version="2.0", request_id=req.request_id, request_fingerprint=fp, status=status, observations=tuple(obs), reason=reason)
        if req.request not in ("get_series", "get_aggregate"):
            return resp("UNSUPPORTED", f"{req.request} not served by storefront telemetry")
        known = {d.variable_id for d in self.definitions()}
        if any(v not in known for v in req.variables):
            return resp("UNSUPPORTED", f"unknown variables {[v for v in req.variables if v not in known]}")
        obs = []
        for vid in req.variables:
            svc, st = vid.rsplit(".", 1)
            d = self.df.filter(pl.col("service") == svc)
            if req.window:
                d = d.filter((pl.col("t") >= req.window.start) & (pl.col("t") < req.window.end))
            g = (d.with_columns(pl.col("t").dt.truncate("1m").alias("m")).group_by("m")
                 .agg(pl.len().alias("requests"), pl.col("error").sum().alias("errors"), pl.col("error").mean().alias("error_rate"),
                      pl.col("dur_ms").quantile(0.95).alias("latency_p95_ms")).sort("m"))
            if g.height == 0:
                return resp("UNSUPPORTED", f"no telemetry for {vid} in the window")
            if req.request == "get_series":
                if req.grain != "1m":
                    return resp("UNSUPPORTED", "telemetry is served at grain 1m")
                payload = TimeSeries(shape="time_series", points=tuple(TimePoint(t=r["m"], value=float(r[st])) for r in g.to_dicts()))
            else:
                tot = d.height
                payload = (AggregateStats(shape="aggregate_stats", n=tot, total=float(d["error"].sum() if st == "errors" else tot))
                           if st in ("requests", "errors") else
                           AggregateStats(shape="aggregate_stats", n=tot, numerator=float(d["error"].sum()), denominator=float(tot))
                           if st == "error_rate" else AggregateStats(shape="aggregate_stats", n=tot, mean=float(d["dur_ms"].quantile(0.95))))
            body = payload.model_dump(mode="json")
            h = hashlib.sha256(json.dumps(body, sort_keys=True, default=str).encode()).hexdigest()
            lin = ProvenanceLineage(source_id="storefront-telemetry", origin_kind="original", lineage_root_id=f"otel:{self.scope.environment_id}",
                                    content_hash=h)
            cut = req.data_cut
            obs.append(Observation(schema_version=SCHEMA_VERSION, observation_id=f"obs:{h[:24]}", variable_id=vid, payload=payload,
                                   source_id="storefront-telemetry", record_key=f"{vid}:{req.request}", revision=1, lineage=lin, scope=self.scope,
                                   clocks=ObservationClocks(observed_at=Clock(quality="KNOWN", value=cut),
                                                            available_at=Clock(quality="ESTIMATED", value=cut), as_of=cut),
                                   dq=DataQuality(missingness="declared", selection="declared"),
                                   observation_mechanisms=("wall_to_sim_linear_map", "otel_redaction_hidden_faults")))
        return resp("OK", None, obs)

    def evidence_record(self, obs: Observation) -> EvidenceRecord:
        return EvidenceRecord(schema_version="2.0", evidence_id=f"ev:{obs.observation_id}", lineage=obs.lineage, scope=obs.scope,
                              provenance_role="SANDBOX_OBSERVED", content_hash=obs.lineage.content_hash)
