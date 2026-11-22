"""The port the causal core sees, conforming to `Contracts/` (schema 2.0).

Wraps the internal EventFramePort engine (real Olist replay or emulator) and speaks only
Contracts types: manifest() -> ProviderManifest, query(CapabilityRequest) -> CapabilityResponse.
Read-only by construction (I15): there is no act/write/intervene method here.
Design: docs/p0/CONFORMANCE_AND_M1_M5_DESIGN.md.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import polars as pl
from pydantic import TypeAdapter

from causal_core.contracts.capabilities import CapabilityRequest, CapabilityResponse, ProviderManifest
from causal_core.contracts.common import SCHEMA_VERSION, Clock, EnvironmentScope, ProvenanceLineage, SourceAttestation
from causal_core.contracts.evidence import EvidenceRecord
from causal_core.contracts.observations import (AggregateStats, DataQuality, EventBatch, EventRecord, Observation,
                                    ObservationClocks, TimePoint, TimeSeries)
from causal_core.contracts.variables import (EventPayloadSchema, EventVariableDefinition, FormulaBinOp, FormulaRef,
                                 MetricDefinition, MetricTimeSemantics, NumericValueSchema, PopulationCoverage)


from . import taxonomy as TX

RATIOS = TX.RATIOS
from .engine_types import EvidenceRequest as _InternalRequest  # internal engine type only, never exposed
from .engine_types import Refusal as _InternalRefusal

LOCAL = ZoneInfo("America/Sao_Paulo")      # Olist and the simulated clock are local Brazil time
GRAINS = {"1d": "day", "1w": "week"}
SERVED = ("get_series", "get_aggregate", "get_event_batch", "get_source_metadata")
_REQ = TypeAdapter(CapabilityRequest)

def to_utc(t: datetime) -> datetime:
    return t.replace(tzinfo=LOCAL).astimezone(timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)


def to_local(t: datetime) -> datetime:
    return t.astimezone(LOCAL).replace(tzinfo=None)


def _sha(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


class ContractPort:
    """Contracts.ports.EvidenceAccessPort over an internal EventFramePort."""

    def __init__(self, engine, *, provider_id: str, origin_kind: str, role: str, scope: EnvironmentScope,
                 available_clock: str, lineage_root: str, generator_version: str | None = None,
                 hidden_variables: frozenset[str] = frozenset(), extensions: dict | None = None):
        self._e = engine
        self.provider_id, self.origin_kind, self.role, self.scope = provider_id, origin_kind, role, scope
        self.available_clock, self.lineage_root, self.generator_version = available_clock, lineage_root, generator_version
        self.hidden = set(hidden_variables)
        self._ext = extensions or {}
        self._metric_ids = [m.id for m in engine.metrics]

    # -------------------------------------------------------------- catalogue
    def expose(self, variable_id: str) -> None:
        """Make a hidden variable visible (used only by the emulator's measurement policy, never by the core)."""
        self.hidden.discard(variable_id)

    def _served_metrics(self) -> list[str]:
        engine = {m.id for m in self._e.metrics}
        base = [v for v in TX.BASE_METRICS if v in engine and v not in self.hidden]
        ratios = [r for r, m in TX.RATIOS.items() if set(m.formula) <= set(base) and r not in self.hidden]
        return base + ratios

    def _served_events(self) -> list[str]:
        types = set(self._e.events["event_type"].unique().to_list())
        return [e.variable_id for e in TX.EVENTS if e.event_type in types and e.variable_id not in self.hidden]

    def definitions(self) -> tuple:
        """Read-only variable catalogue built from mkt/taxonomy.py (gap G1: the manifest has no catalogue field)."""
        att = SourceAttestation(source_id=self.provider_id, method="adapter_declared")
        base = dict(schema_version=SCHEMA_VERSION, scope=self.scope, provenance=att)
        out = []
        for vid in self._served_metrics():
            m = TX.METRIC_BY_ID[vid]
            vs = NumericValueSchema(type="numeric", dtype=m.dtype, minimum=0.0, maximum=1.0 if m.dtype == "proportion" else None)
            f = (FormulaBinOp(node="binop", op="div", left=FormulaRef(node="ref", variable_id=m.formula[0]),
                              right=FormulaRef(node="ref", variable_id=m.formula[1])) if m.formula else None)
            out.append(MetricDefinition(**base, kind="metric", variable_id=vid, entity_ref=m.entity, description=m.description,
                                        value_schema=vs, unit=m.unit, aggregation_kind=m.aggregation,
                                        population_coverage=PopulationCoverage(population_id=m.population, coverage=m.coverage),
                                        time_semantics=MetricTimeSemantics(grain="1w", window="tumbling", timezone="America/Sao_Paulo"),
                                        formula_ast=f, identity_tolerance=m.identity_tolerance, extensions=self._ext.get(vid, {})))
        for vid in self._served_events():
            e = TX.EVENT_BY_ID[vid]
            fields = {k: t for k, (t, _) in e.fields.items()}
            ident = next(k for k in e.fields if k.endswith("_id"))
            out.append(EventVariableDefinition(**base, kind="event", variable_id=vid, entity_ref=e.entity, description=e.description,
                                               value_schema=EventPayloadSchema(type="event", fields=fields, identity_fields=(ident,)),
                                               occurrence_time="event_time", extensions=self._ext.get(vid, {})))
        return tuple(out)

    def manifest(self) -> ProviderManifest:
        return ProviderManifest(schema_version="2.0", provider_id=self.provider_id, manifest_version="mkt-core-port-1", supported_requests=SERVED,
                                max_rows=self._e.event_rows_max, scopes=(self.scope,))

    # -------------------------------------------------------------- queries
    def query(self, request) -> CapabilityResponse:
        req = _REQ.validate_python(request) if isinstance(request, dict) else request
        fp = _sha(req.model_dump(mode="json"))

        def resp(status, reason=None, obs=(), truncated=False):
            return CapabilityResponse(schema_version="2.0", request_id=req.request_id, request_fingerprint=fp, status=status, observations=tuple(obs),
                                      reason=reason, truncated=truncated, available_at=req.data_cut)
        if req.request not in SERVED:
            return resp("UNSUPPORTED", f"{req.request} is not served by {self.provider_id}: no studies, panels, strata or "
                                       "intervention history in this source")
        if req.scope.environment_id != self.scope.environment_id:
            return resp("UNSUPPORTED", f"scope {req.scope.environment_id} is not served here ({self.scope.environment_id})")
        if req.request == "get_source_metadata":
            return resp("UNSUPPORTED", "source metadata is served through manifest() and definitions()")
        known = set(self._served_metrics()) | set(self._served_events())
        unknown = [v for v in req.variables if v not in known]
        if unknown:
            return resp("UNSUPPORTED", f"variables not served in this environment: {unknown}")
        obs = []
        for vid in req.variables:
            if req.request == "get_event_batch":
                if vid not in self._served_events():
                    return resp("UNSUPPORTED", f"{vid} is a metric; ask get_series or get_aggregate")
                o = self._events(req, vid)
            else:
                if vid not in self._served_metrics():
                    return resp("UNSUPPORTED", f"{vid} is an event variable; ask get_event_batch")
                o = self._metric(req, vid)
            if isinstance(o, str):
                return resp("BUDGET_EXCEEDED" if o.startswith("budget") else "UNSUPPORTED", o)
            obs.append(o)
        return resp("OK", None, obs)

    def _internal(self, req, vid, shape):
        w = req.window
        return _InternalRequest(variable_id=vid, shape=shape, as_of=to_local(req.data_cut),
                                window_start=to_local(w.start) if w else None, window_end=to_local(w.end) if w else None,
                                grain=GRAINS.get(getattr(req, "grain", None) or "1w", "week"))

    def _metric(self, req, vid):
        if getattr(req, "grain", None) and req.grain not in GRAINS:
            return f"grain {req.grain} not served (served: {sorted(GRAINS)})"
        shape = "series" if req.request == "get_series" else "aggregate"
        if vid in RATIOS:
            a, b = RATIOS[vid].formula
            ra, rb = (self._e.query(self._internal(req, x, shape)) for x in (a, b))
            if isinstance(ra, _InternalRefusal) or isinstance(rb, _InternalRefusal):
                return "component metric refused"
            pa, pb = ra.observations[0].payload, rb.observations[0].payload
            if shape == "series":
                na = {p["t"]: p["value"] or 0 for p in pa}                       # no numerator rows in a bucket = 0
                pts = [(p["t"], na.get(p["t"], 0) / p["value"] if p["value"] else None) for p in pb]
                payload = self._series(pts, req)
            else:
                payload = AggregateStats(shape="aggregate_stats", n=int(pb["n"] or 0), numerator=float(pa["value"] or 0),
                                         denominator=float(pb["value"] or 0), window=req.window)
        else:
            r = self._e.query(self._internal(req, vid, shape))
            if isinstance(r, _InternalRefusal):
                return r.reason
            p = r.observations[0].payload
            if shape == "series":
                payload = self._series([(x["t"], x["value"]) for x in p], req)
            else:
                agg = TX.METRIC_BY_ID[vid].aggregation
                n = int(p["n"] or 0)
                v = p["value"]
                payload = (AggregateStats(shape="aggregate_stats", n=n, total=float(v or 0), window=req.window) if agg in ("count", "sum")
                           else AggregateStats(shape="aggregate_stats", n=n, mean=float(v), window=req.window) if n and v is not None
                           else AggregateStats(shape="aggregate_stats", n=0, total=0.0, window=req.window))
        if isinstance(payload, str):
            return payload
        return self._observation(req, vid, payload, f"{vid}:{req.request}:{getattr(req, 'grain', '')}:"
                                 f"{req.window.start.isoformat() if req.window else ''}")

    def _series(self, pts, req):
        if not pts:
            return "no data in the window"
        if len(pts) > req.max_buckets:
            return f"budget: {len(pts)} buckets > max_buckets {req.max_buckets}"
        return TimeSeries(shape="time_series", points=tuple(
            TimePoint(t=to_utc(datetime.fromisoformat(t)), value=None if v is None else float(v)) for t, v in sorted(pts)))

    def _events(self, req, vid):
        e = TX.EVENT_BY_ID[vid]
        df = self._e.visible_of(to_local(req.data_cut), e.event_type)
        if req.window:
            df = df.filter((pl.col("event_time") >= to_local(req.window.start)) & (pl.col("event_time") < to_local(req.window.end)))
        cap = min(req.max_rows, req.max_events)
        if df.height > cap:
            return f"budget: {df.height:,} events > limit {cap:,}; narrow the window or ask a metric"
        if df.height == 0:
            return "no events in the window"
        cols = {c for _, c in e.fields.values()}
        df = df.with_columns([pl.lit(None).alias(c) for c in cols if c not in df.columns])
        cast = {"real": float, "integer": lambda v: int(round(v)), "boolean": lambda v: bool(v), "text": str, "category": str}

        def attrs(r):
            return {k: (None if r[c] is None else cast[t](r[c])) for k, (t, c) in e.fields.items()}
        evs = tuple(EventRecord(event_id=_safe(r["event_id"]), occurred_at=Clock(quality="KNOWN", value=to_utc(r["event_time"])), attributes=attrs(r))
                    for r in df.select("event_id", "event_time", *sorted(cols)).to_dicts())
        return self._observation(req, vid, EventBatch(shape="event_batch", events=evs), f"{vid}:events:{req.window.start.isoformat() if req.window else ''}")

    def _observation(self, req, vid, payload, record_key):
        body = payload.model_dump(mode="json")
        lineage = ProvenanceLineage(source_id=self.provider_id, origin_kind=self.origin_kind, lineage_root_id=self.lineage_root,
                                    generator_version=self.generator_version, content_hash=_sha(body),
                                    transformation_hash=_sha({"variable": vid, "request": req.request}))
        cut = to_utc(self._e.cut) if self._e.cut else req.data_cut
        av = min(req.data_cut, cut)
        observed = min(req.window.end, av) if req.window else av
        clocks = ObservationClocks(observed_at=Clock(quality="KNOWN", value=observed),
                                   available_at=Clock(quality=self.available_clock, value=av),
                                   as_of=req.data_cut, source_timezone="America/Sao_Paulo")
        return Observation(schema_version=SCHEMA_VERSION, observation_id=f"obs:{_sha([record_key, req.data_cut.isoformat(), body])[:24]}",
                           variable_id=vid, payload=payload, source_id=self.provider_id, record_key=_safe(record_key), revision=1,
                           lineage=lineage, scope=self.scope, clocks=clocks,
                           dq=DataQuality(maturity="IMMATURE" if req.window and req.window.end > av else "UNKNOWN",
                                          missingness="declared", selection="unknown"))

    def evidence_record(self, obs: Observation) -> EvidenceRecord:
        return EvidenceRecord(schema_version="2.0", evidence_id=f"ev:{obs.observation_id}", lineage=obs.lineage, scope=obs.scope,
                              provenance_role=self.role, content_hash=obs.lineage.content_hash)


def _safe(s: str) -> str:
    import re
    s = re.sub(r"[^A-Za-z0-9_.:\-/]", "_", s)
    return s if s[:1].isalnum() else "k" + s


# ------------------------------------------------------------------ constructors
def olist_port(cfg=None) -> ContractPort:
    """Real Olist data replayed offline. available_at is ESTIMATED: Olist has no arrival times (assumed = event time)."""
    from .replay_port import OlistReplayPort
    e = OlistReplayPort(cfg)
    files = e.root.files
    # G2 (decided 22 Nov by the contracts' owner): replayed real history is NOT production -> offline_replay.
    # The contract then allows role SANDBOX_OBSERVED (REAL_OBSERVED is reserved for production scopes).
    scope = EnvironmentScope(environment_id="olist-offline-replay-2018", environment_kind="offline_replay", data_cut=to_utc(e.cut),
                             dataset_version=f"olist-kaggle-{e.root.source_version}")
    return ContractPort(e, provider_id="olist", origin_kind="original", role="SANDBOX_OBSERVED", scope=scope,
                        available_clock="ESTIMATED", lineage_root=f"olist:{_sha(files)[:16]}")


def emulator_port(engine, session_params=None, hidden_variables=frozenset()) -> ContractPort:
    """Emulator (synthetic). generator_version pins the chain params, the observation model, the seed and the cards."""
    mv = engine.env.mechanism_versions
    gen = "chain-" + _sha(mv)[:16]
    scope = EnvironmentScope(environment_id=_safe(engine.env.id), environment_kind="emulator", data_cut=to_utc(engine.cut),
                             generator_version=gen)
    ext = {}
    if session_params is not None:
        card_for = {"sessions_count": "TC-RR1", "cart_adds_count": "TC-RR1", "views_count": "TC-RR1", "checkouts_count": "TC-TB1",
                    "item_viewed": "TC-EX1", "session_started": "TC-DV1"}
        for vid, card in card_for.items():
            ext[vid] = {"mkt:transport_card": card, "mkt:transport_status": session_params.cards[card][:60]}
    return ContractPort(engine, provider_id="emulator-chain", origin_kind="synthetic_generated", role="SYNTHETIC_GENERATED",
                        scope=scope, available_clock="KNOWN", lineage_root=f"emulator:{gen}", generator_version=gen,
                        hidden_variables=frozenset(hidden_variables), extensions=ext)
