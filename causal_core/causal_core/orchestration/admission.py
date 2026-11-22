"""GraphAdmissionCoordinator: the eight-step admission of v3.2 §3.3 (plan §D.4).

Shell component: it reads a pinned snapshot, calls pure checks, issues at most ONE bounded typed
evidence query, persists one atomic delta, then updates the in-memory index and notifies only
subscribed parties. It never issues causal certificates and never auto-monitors new variables.

Steps
  1 dedupe by (source_id, record_id)          5 temporal/observation/causal edges + certificates + cycles
  2 subtype, scope, provenance                6 one bounded empirical identity query (exact identities only)
  3 entity and dimension mapping              7 assign ACTIVE / QUARANTINED with reasons
  4 metric formula semantics (or N/A)         8 atomic commit, then index update, then subscriber notify
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timedelta, timezone
from typing import Callable

from pydantic import TypeAdapter

from ..api import replay_visibility
from ..contracts.admission import AdmissionRequest, AdmissionVerdict, EdgeOutcome, StepResult
from ..contracts.capabilities import CapabilityRequest
from ..contracts.common import ProvenanceLineage
from ..contracts.graph import GraphEdge, GraphNode, StructuralCertificate, Validity
from ..contracts.ports import EvidenceAccessPort
from ..contracts.variables import MetricDefinition
from ..graph.store import ClaimRecord, GraphDelta, GraphStore, NodeRecord, Snapshot
from ..graph.validation import endpoint_problems, find_instantaneous_cycle, is_instantaneous
from ..semantics.metrics import check_formula, verify_identity
from ..storage.graph_log import GraphLog

_EDGE = TypeAdapter(GraphEdge)
_REQ = TypeAdapter(CapabilityRequest)
STEP_NAMES = {1: "dedupe source record", 2: "subtype, scope and provenance", 3: "entity and dimension mapping",
              4: "metric formula semantics", 5: "edge structure, certificates and cycles",
              6: "empirical identity check", 7: "assign statuses", 8: "atomic commit and notify"}


def _edge_with(edge: GraphEdge, **update) -> GraphEdge:
    """Rebuild through validation (model_copy would skip validators)."""
    return _EDGE.validate_json(json.dumps({**json.loads(edge.model_dump_json()), **update}))


def _value(payload) -> float | None:
    if payload.shape == "scalar":
        return payload.value
    if payload.shape == "aggregate_stats":
        if payload.numerator is not None and payload.denominator:
            return payload.numerator / payload.denominator
        return payload.total if payload.total is not None else payload.mean
    return None


class GraphAdmissionCoordinator:
    def __init__(self, store: GraphStore, log: GraphLog, evidence: EvidenceAccessPort | None = None, *,
                 clock: Callable[[], datetime] | None = None, identity_min_windows: int = 2,
                 identity_lookback: timedelta = timedelta(days=7), default_tolerance: float = 1e-4):
        if log.head() != store.version:
            raise ValueError(f"store version {store.version} != log head {log.head()}: rebuild before admitting")
        self.store, self.log, self.evidence = store, log, evidence
        self.clock = clock or (lambda: datetime.now(timezone.utc))
        self.identity_min_windows, self.identity_lookback = identity_min_windows, identity_lookback
        self.default_tolerance = default_tolerance
        self._subscribers: dict[str, tuple[frozenset[str], Callable | None]] = {}
        self.queries_issued = 0

    def subscribe(self, subscriber_id: str, variables: set[str], callback: Callable | None = None) -> None:
        """Explicit interest only: there is no 'all variables' subscription and no auto-onboarding (§3.3 step 8)."""
        if not variables:
            raise ValueError("a subscription names the variables it cares about")
        self._subscribers[subscriber_id] = (frozenset(variables), callback)

    # ── the eight steps ────────────────────────────────────────────────────
    def admit(self, req: AdmissionRequest) -> AdmissionVerdict:
        fp = hashlib.sha256(json.dumps(req.model_dump(mode="json", exclude={"request_id"}),
                                       sort_keys=True).encode()).hexdigest()
        snap = self.store.pin()
        steps: list[StepResult] = []

        def done(outcome: str, version: int | None, edges=(), notified=(), downstream=()) -> AdmissionVerdict:
            listed = {s.step for s in steps}
            filler = [StepResult(step=i, name=STEP_NAMES[i], status="SKIPPED") for i in range(1, 9) if i not in listed]
            return AdmissionVerdict(schema_version="2.0", request_id=req.request_id, outcome=outcome,
                                    graph_version=version, steps=tuple(sorted(steps + filler, key=lambda s: s.step)),
                                    edges=tuple(edges), notified_subscribers=tuple(notified),
                                    downstream_reverify=tuple(downstream))

        def step(i: int, status: str, *details: str) -> None:
            steps.append(StepResult(step=i, name=STEP_NAMES[i], status=status, details=tuple(details)))

        # 1 ─ dedupe
        prior = self.log.source_record(req.source_id, req.record_id)
        if prior is not None:
            if prior[0] == fp:
                step(1, "PASS", f"identical record already admitted at version {prior[1]}: no-op")
                return done("IDEMPOTENT_NOOP", prior[1])
            step(1, "REJECT", "CONFLICTING_RECORD: same (source_id, record_id) with different content")
            return done("REJECTED", None)
        step(1, "PASS", "new source record")

        # 2 ─ subtype, scope, provenance (subtype already enforced by contract parsing)
        reject, quarantine_all = [], []
        if req.attestation.source_id != req.source_id:
            reject.append("ATTESTATION_SOURCE_MISMATCH")
        if req.definition is not None and req.definition.scope.environment_id != req.scope.environment_id:
            reject.append("DEFINITION_SCOPE_MISMATCH: definition and request scopes differ")
        if req.attestation.method == "none":
            quarantine_all.append("MISSING_PROVENANCE: attestation method 'none'")
        if req.scope.environment_kind == "legacy_unscoped":
            quarantine_all.append("LEGACY_UNSCOPED: no claim from this record can become ACTIVE")
        if reject:
            step(2, "REJECT", *reject)
            return done("REJECTED", None)
        step(2, "QUARANTINE" if quarantine_all else "PASS", *(quarantine_all or ["attested, scoped"]))

        node_records, new_nodes = self._node_records(req, snap)
        known = {n.node.node_id: n.node for n in node_records}
        var_kind = {n.node.node_id: n.definition.kind for n in node_records if n.definition is not None}

        def node_of(nid: str) -> GraphNode | None:
            if nid in known:
                return known[nid]
            rec = snap.node(nid)
            return rec.node if rec else None

        def kind_of(nid: str) -> str | None:
            if nid in var_kind:
                return var_kind[nid]
            rec = snap.node(nid)
            return rec.definition.kind if rec and rec.definition is not None else None

        outcomes: dict[str, EdgeOutcome] = {}
        accepted: list[GraphEdge] = []

        # 3 ─ entities and dimensions
        details3 = []
        complete_dims = [d for d in req.dimension_specs if d.complete_partition]
        for e in req.edges:
            if e.kind == "ENTITY_RELATIONSHIP" and (e.entity_match_confidence or 0) < 1.0:
                details3.append(f"UNCERTAIN_MATCH: {e.edge_id} (confidence {e.entity_match_confidence}) "
                                "kept out of exact closure")
            if e.kind == "DIMENSIONAL_DECOMPOSITION" and not complete_dims:
                outcomes[e.edge_id] = EdgeOutcome(edge_id=e.edge_id, kind=e.kind, outcome="QUARANTINED",
                                                  status="QUARANTINED", reasons=("INCOMPLETE_PARTITION_UNDECLARED: "
                                                  "no complete DimensionSpec in the request",))
        relevant3 = details3 or req.entity_specs or req.dimension_specs or any(
            e.kind in {"ENTITY_RELATIONSHIP", "DIMENSIONAL_DECOMPOSITION"} for e in req.edges)
        if relevant3:
            q = [o for o in outcomes.values() if o.outcome == "QUARANTINED"]
            step(3, "QUARANTINE" if q else "PASS", *(details3 + [f"{o.edge_id}: {o.reasons[0]}" for o in q]
                                                     or ["entity/dimension declarations consistent"]))
        else:
            step(3, "NOT_APPLICABLE", "no entity or dimension declarations")

        # 4 ─ metric formula semantics
        d = req.definition
        derived: list[tuple[str, str]] = []
        if isinstance(d, MetricDefinition) and d.formula_ast is not None:
            parents = {}
            for ref in sorted(_refs(d)):
                rec = snap.node(ref)
                if rec is not None and rec.definition is not None:
                    parents[ref] = rec.definition
            check = check_formula(d, parents)
            if check.missing_parents:
                step(4, "QUARANTINE", *check.problems, "metric admitted standalone; no structural edges")
            elif not check.ok:
                derived = [(p, k) for p, k in _naive_edges(d)]
                for p, k in derived:
                    eid = f"struct:{k}:{p}/{d.variable_id}"
                    outcomes[eid] = EdgeOutcome(edge_id=eid, kind=k, outcome="QUARANTINED", status="QUARANTINED",
                                                reasons=tuple(f"SEMANTIC_VIOLATION: {x}" for x in check.problems))
                step(4, "QUARANTINE", *check.problems)
            else:
                derived = list(check.derived_edges)
                step(4, "PASS", *[f"derived {k} {p} -> {d.variable_id}" for p, k in derived])
        else:
            step(4, "NOT_APPLICABLE", f"{d.kind} definition: no formula" if d else "no variable definition")

        derived_edges = [self._derived_edge(req, fp, p, k, d.variable_id) for p, k in derived]

        # 5 ─ declared edges: endpoints, layers, certificates, validity, instantaneous cycles
        problems5 = []
        certs = {c.certificate_id: c for c in req.certificates}
        for e in req.edges:
            if e.edge_id in outcomes:
                continue
            reasons = endpoint_problems(e, node_of(e.source), node_of(e.target), kind_of(e.source), kind_of(e.target))
            v = e.validity
            if v.valid_from and v.valid_to and v.valid_to <= v.valid_from:
                reasons.append("INVALID_VALIDITY: valid_to must follow valid_from")
            if e.scope.environment_id != req.scope.environment_id:
                reasons.append(f"EDGE_SCOPE_MISMATCH: edge scoped to {e.scope.environment_id}, request attested for "
                               f"{req.scope.environment_id} (no silent transport)")
            if e.kind == "CAUSALLY_IDENTIFIED":
                reasons += _certificate_problems(e, certs.get(e.identification_ref) or snap.certificate(e.identification_ref))
            if reasons:
                outcomes[e.edge_id] = EdgeOutcome(edge_id=e.edge_id, kind=e.kind, outcome="REJECTED", reasons=tuple(reasons))
                problems5.append(f"{e.edge_id}: {reasons[0]}")
            else:
                accepted.append(e)
        for c in req.certificates:
            missing = [n for n in (c.source_node, c.target_node) if node_of(n) is None]
            if missing or c.scope.environment_id != req.scope.environment_id:
                problems5.append(f"certificate {c.certificate_id} refused: "
                                 + (f"UNKNOWN_ENDPOINT {missing}" if missing else "SCOPE_MISMATCH"))
        for a in req.effect_claims:
            missing = [n for n in (a.treatment_node, a.outcome_node) if node_of(n) is None]
            if missing:
                problems5.append(f"effect claim {a.claim.claim_id} refused: UNKNOWN_ENDPOINT {missing}")
        candidate_instant = [e for e in accepted + derived_edges if is_instantaneous(e)]
        cycle = find_instantaneous_cycle(
            candidate_instant, lambda n: (x.target for x in snap.out_edges(n) if is_instantaneous(x) and x.status == "ACTIVE"))
        if cycle:
            for e in list(accepted):
                if is_instantaneous(e) and e.source in cycle and e.target in cycle:
                    accepted.remove(e)
                    outcomes[e.edge_id] = EdgeOutcome(edge_id=e.edge_id, kind=e.kind, outcome="REJECTED",
                                                      reasons=(f"INSTANTANEOUS_CYCLE: {' -> '.join(cycle)} "
                                                               "(zero-lag equilibrium unsupported in P0)",))
                    problems5.append(f"{e.edge_id}: INSTANTANEOUS_CYCLE")
        if problems5:
            step(5, "REJECT", *problems5)
        elif req.edges or req.certificates or req.effect_claims:
            step(5, "PASS", f"{len(accepted)} declared edge(s), {len(req.certificates)} certificate(s), "
                            f"{len(req.effect_claims)} effect claim(s) structurally valid")
        else:
            step(5, "NOT_APPLICABLE", "no declared edges, certificates or effect claims")

        # 6 ─ one bounded empirical identity query, only for exact identities that passed step 4
        if derived and isinstance(d, MetricDefinition) and all(e.edge_id not in outcomes for e in derived_edges):
            verdict6, details6, evidence_ids = self._identity(req, d, [p for p, _ in derived])
            for e in derived_edges:
                if verdict6 == "VERIFIED_IDENTITY":
                    accepted.append(e)
                else:
                    outcomes[e.edge_id] = EdgeOutcome(edge_id=e.edge_id, kind=e.kind, outcome="QUARANTINED",
                                                      status="QUARANTINED", reasons=tuple(details6))
            step(6, "PASS" if verdict6 == "VERIFIED_IDENTITY" else "QUARANTINE", *details6)
        else:
            step(6, "NOT_APPLICABLE", "no exact identity to verify")

        # 7 ─ statuses
        final_edges: list[GraphEdge] = []
        for e in accepted:
            if quarantine_all:
                e = _edge_with(e, status="QUARANTINED", status_reasons=list(quarantine_all))
                outcomes[e.edge_id] = EdgeOutcome(edge_id=e.edge_id, kind=e.kind, outcome="QUARANTINED",
                                                  status="QUARANTINED", reasons=tuple(quarantine_all))
            else:
                outcomes[e.edge_id] = EdgeOutcome(edge_id=e.edge_id, kind=e.kind, outcome="ADMITTED", status=e.status)
            final_edges.append(e)
        for eid, o in list(outcomes.items()):
            if o.outcome == "QUARANTINED" and all(x.edge_id != eid for x in final_edges):
                src = next((x for x in list(req.edges) + derived_edges if x.edge_id == eid), None)
                if src is not None and node_of(src.source) and node_of(src.target):
                    final_edges.append(_edge_with(src, status="QUARANTINED", status_reasons=list(o.reasons)))
        step(7, "PASS", f"{sum(o.outcome == 'ADMITTED' for o in outcomes.values())} admitted, "
                        f"{sum(o.outcome == 'QUARANTINED' for o in outcomes.values())} quarantined, "
                        f"{sum(o.outcome == 'REJECTED' for o in outcomes.values())} rejected")

        # 8 ─ atomic commit, then index, then subscriber-only notification
        ok_certs = tuple(c for c in req.certificates if all(node_of(n) for n in (c.source_node, c.target_node))
                         and c.scope.environment_id == req.scope.environment_id)
        ok_claims = tuple(ClaimRecord(a.treatment_node, a.outcome_node, a.claim) for a in req.effect_claims
                          if node_of(a.treatment_node) and node_of(a.outcome_node))
        version = self.store.version + 1
        downstream = self._downstream(snap, d) if d is not None and d.variable_id in {n.node.node_id for n in new_nodes} else ()
        affected = {e.source for e in final_edges} | {e.target for e in final_edges} | \
                   {n.node.node_id for n in new_nodes}
        notified = [sid for sid, (vars_, _) in sorted(self._subscribers.items()) if vars_ & affected]
        edge_outcomes = sorted(outcomes.values(), key=lambda o: o.edge_id)
        step(8, "PASS", f"committed version {version}", f"notified {notified or 'no subscribers'}")
        verdict = done("COMMITTED", version, edge_outcomes, notified, downstream)
        delta = GraphDelta(version=version, request_id=req.request_id, request_fingerprint=fp, source_id=req.source_id,
                           record_id=req.record_id, committed_at=self.clock(), nodes=tuple(new_nodes),
                           edges=tuple(final_edges), certificates=ok_certs, claims=ok_claims,
                           verdict_json=verdict.model_dump_json())
        self.log.commit(delta)          # durable first; raises on failure, leaving memory untouched
        self.store.apply(delta)
        for sid in notified:
            cb = self._subscribers[sid][1]
            if cb is not None:
                cb(version, sorted(self._subscribers[sid][0] & affected))
        return verdict

    # ── helpers ────────────────────────────────────────────────────────────
    def _node_records(self, req: AdmissionRequest, snap: Snapshot) -> tuple[list[NodeRecord], list[NodeRecord]]:
        """Records describing this request's nodes, and the subset that is new or changed (to persist)."""
        records, changed = [], []
        for n in req.nodes:
            rec = NodeRecord(n, None, Validity())
            records.append(rec)
            cur = snap.node(n.node_id)
            if cur is None or cur.node != n:
                changed.append(rec)
        d = req.definition
        if d is not None:
            node = GraphNode(schema_version="2.0", node_id=d.variable_id, kind="VARIABLE", variable_id=d.variable_id)
            segs = snap.node_segments(d.variable_id)
            current = segs[-1] if segs else None
            if current is not None and current.definition == d:
                records.append(current)
            else:
                t0 = req.effective_from
                new = NodeRecord(node, d, Validity(valid_from=t0))
                if segs and t0 is not None:     # keep the history before t0 in domain time
                    kept = [NodeRecord(s.node, s.definition, Validity(valid_from=s.validity.valid_from, valid_to=t0))
                            for s in segs if s.validity.valid_from is None or s.validity.valid_from < t0]
                    changed.extend(kept)
                changed.append(new)
                records.append(new)
        return records, changed

    def _derived_edge(self, req: AdmissionRequest, fp: str, parent: str, kind: str, child: str) -> GraphEdge:
        return _EDGE.validate_json(json.dumps({
            "schema_version": "2.0", "edge_id": f"struct:{kind}:{parent}/{child}", "kind": kind,
            "source": parent, "target": child, "status": "ACTIVE",
            "lineage": json.loads(ProvenanceLineage(source_id=req.source_id, origin_kind="original",
                                                    lineage_root_id=f"admission:{req.request_id}",
                                                    content_hash=fp).model_dump_json()),
            "scope": json.loads(req.scope.model_dump_json()),
            "validity": {"valid_from": req.effective_from.isoformat()} if req.effective_from else {}}))

    def _identity(self, req: AdmissionRequest, d: MetricDefinition, parents: list[str]):
        tol = d.identity_tolerance or self.default_tolerance
        if self.evidence is None:
            return "UNVERIFIED", ["IDENTITY_UNVERIFIED: no evidence port configured"], []
        start = req.data_cut - self.identity_lookback
        request = _REQ.validate_json(json.dumps({
            "schema_version": "2.0", "request": "get_aggregate", "request_id": f"identity:{req.request_id}",
            "variables": [d.variable_id, *parents], "scope": json.loads(req.scope.model_dump_json()),
            "data_cut": req.data_cut.isoformat(), "window": {"start": start.isoformat(), "end": req.data_cut.isoformat()},
            "max_rows": 1000, "purpose": "graph admission identity check (v3.2 §3.3 step 6)"}))
        self.queries_issued += 1
        resp = self.evidence.query(request)
        if resp.status not in {"OK", "PARTIAL"}:
            return "UNVERIFIED", [f"IDENTITY_UNVERIFIED: evidence {resp.status}: {resp.reason}"], []
        rows: dict[str, dict[str, float | None]] = {}
        used, leaked = [], 0
        for obs in resp.observations:
            if replay_visibility(obs, req.data_cut)[0] != "VISIBLE":
                leaked += 1                       # provider returned something not available by the data cut
                continue
            p = obs.payload
            key = p.window.start.isoformat() if getattr(p, "window", None) else obs.record_key
            rows.setdefault(key, {})[obs.variable_id] = _value(p)
            used.append(obs.observation_id)
        res = verify_identity(d.formula_ast, d.variable_id, sorted(rows.items()), tol, self.identity_min_windows)
        note = [f"{res.verdict}: {res.windows_checked} window(s) as of {req.data_cut.isoformat()}, "
                f"max |residual| {res.max_abs_residual if res.max_abs_residual is not None else 'n/a'} vs tol {tol}"]
        if res.verdict == "UNVERIFIED":
            note.append(f"IDENTITY_UNVERIFIED: needs >= {self.identity_min_windows} comparable windows")
        if leaked:
            note.append(f"ignored {leaked} observation(s) not available by the data cut")
        return res.verdict, note, used

    def _downstream(self, snap: Snapshot, d) -> tuple[str, ...]:
        """Children whose structural identity used this variable: they must be re-verified (not silently trusted)."""
        return tuple(sorted({e.target for e in snap.out_edges(d.variable_id)
                             if e.kind in {"EXACT_ARITHMETIC", "AGGREGATION_COMPOSITION"} and e.status == "ACTIVE"}))


def _refs(d: MetricDefinition) -> set[str]:
    from ..contracts.variables import formula_refs
    return set(formula_refs(d.formula_ast))


def _naive_edges(d: MetricDefinition) -> list[tuple[str, str]]:
    from ..semantics.metrics import derive_structural_edges
    return list(derive_structural_edges(d))


def _certificate_problems(edge: GraphEdge, cert: StructuralCertificate | None) -> list[str]:
    """Certificate-to-edge identity (v3.2 §4.5 item 2; plan §D.2; C01)."""
    if cert is None:
        return [f"CERTIFICATE_NOT_FOUND: {edge.identification_ref}"]
    out = []
    if (cert.source_node, cert.target_node) != (edge.source, edge.target):
        out.append(f"RELATION_MISMATCH: certificate {cert.certificate_id} identifies "
                   f"{cert.source_node} -> {cert.target_node}, not {edge.source} -> {edge.target}")
    if not cert.can_support_edge:
        out.append(f"EFFECT_ONLY_CERTIFICATE: {cert.certificate_id} identifies a total effect "
                   f"({cert.basis}); record it as an EffectClaim, not a structural edge")
    if cert.scope.environment_id != edge.scope.environment_id:
        out.append(f"SCOPE_MISMATCH: certificate scoped to {cert.scope.environment_id}, edge to "
                   f"{edge.scope.environment_id} (no silent transport)")
    return out
