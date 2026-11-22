"""Phase 2 gates: G04 edge correctness, G05 bitemporal replay + admission, G09 (identity part),
G11 effect-vs-edge; plus read-only views, bounded traversal and subscriber-only notification.

    python -m unittest discover -s tests -t . -v
"""
from __future__ import annotations

import ast
import json
import os
import tempfile
import unittest
from datetime import timedelta

from pydantic import TypeAdapter

from adapters.sources.emulator_port import EmulatorEvidencePort, admission_requests
from causal_core.contracts.admission import AdmissionRequest
from causal_core.contracts.graph import GraphEdge, GraphNode, Validity
from causal_core.graph import validation
from causal_core.graph.store import GraphDelta, GraphStore, NodeRecord
from causal_core.graph.views import CausalEvidenceView, StructuralView, TemporalView, TraversalBound
from causal_core.orchestration.admission import GraphAdmissionCoordinator
from causal_core.semantics.metrics import check_formula, parse_unit, verify_identity
from causal_core.storage.graph_log import GraphLog, InjectedCrash, rebuild
from emulator import coldchain
from evaluation import fixture_io as io

R, E, N = TypeAdapter(AdmissionRequest), TypeAdapter(GraphEdge), TypeAdapter(GraphNode)
START = coldchain.START


def coordinator(world, *, port=True, leak=False) -> GraphAdmissionCoordinator:
    log = GraphLog(os.path.join(tempfile.mkdtemp(), "graph.db"))
    return GraphAdmissionCoordinator(GraphStore(), log, EmulatorEvidencePort(world, leak=leak) if port else None)


def admit(adm, raw):
    return adm.admit(R.validate_json(json.dumps(raw)))


def admitted_world(days=5, cut_day=5, **emit_kw):
    world = coldchain.emit(days=days, **emit_kw)
    adm = coordinator(world)
    cut = START + timedelta(days=cut_day, hours=12)
    verdicts = {raw["request_id"]: admit(adm, raw) for raw in admission_requests(world, cut)}
    return world, adm, verdicts, cut


def base_request(world, rid, cut, **parts):
    scope = world.definitions["orders"]["scope"]
    return {"schema_version": "2.0", "request_id": rid, "source_id": "emu-coldchain", "record_id": f"rec:{rid}",
            "attestation": {"source_id": "emu-coldchain", "method": "adapter_declared"},
            "scope": parts.pop("scope", scope), "data_cut": cut.isoformat(), **parts}


def edge(eid, kind, src, dst, scope, **extra):
    e = {"schema_version": "2.0", "edge_id": eid, "kind": kind, "source": src, "target": dst, "status": "ACTIVE",
         "scope": scope, "lineage": {"source_id": "emu-coldchain", "origin_kind": "original",
                                     "lineage_root_id": f"t:{eid}", "content_hash": "c" * 64}}
    e.update(extra)
    return e


class AdmissionG05(unittest.TestCase):
    def test_world_admits_with_one_identity_query(self):
        _, adm, v, _ = admitted_world()
        self.assertTrue(all(x.outcome == "COMMITTED" for x in v.values()))
        rate = v["adm-on_time_dispatch_rate"]
        self.assertEqual({e.outcome for e in rate.edges}, {"ADMITTED"})
        self.assertEqual(rate.steps[5].status, "PASS")          # step 6 verified
        self.assertEqual(adm.queries_issued, 1)                  # one bounded query, only for the identity
        for nonmetric in ("adm-freezer_temp_c", "adm-door_open", "adm-compressor_mode"):
            self.assertEqual([v[nonmetric].steps[i].status for i in (3, 5)], ["NOT_APPLICABLE", "NOT_APPLICABLE"])

    def test_idempotent_and_conflicting_records(self):
        world, adm, _, cut = admitted_world()
        raw = admission_requests(world, cut)[0]
        before = adm.store.version
        self.assertEqual(admit(adm, {**raw, "request_id": "again"}).outcome, "IDEMPOTENT_NOOP")
        changed = {**raw, "request_id": "changed", "definition": {**raw["definition"], "description": "edited"}}
        self.assertEqual(admit(adm, changed).outcome, "REJECTED")
        self.assertEqual(adm.store.version, before)

    def test_drift_quarantines_and_history_is_kept(self):
        world = coldchain.emit(days=6, drift_from_day=3)
        adm = coordinator(world)
        early = START + timedelta(days=3, hours=7)               # windows 0..2 available, before drift
        for raw in admission_requests(world, early)[:3]:
            admit(adm, raw)
        v_before = adm.store.version
        late = START + timedelta(days=6, hours=12)
        rate_raw = admission_requests(world, late)[2]
        v = admit(adm, {**rate_raw, "request_id": "reverify", "record_id": "def:on_time_dispatch_rate:reattest-1"})
        self.assertEqual({e.outcome for e in v.edges}, {"QUARANTINED"})
        self.assertIn("IDENTITY_MISMATCH", v.edges[0].reasons[0])
        rel = lambda ver: StructuralView(adm.store.pin(ver)).relationship("orders", "on_time_dispatch_rate")
        self.assertEqual(rel(v_before).status, "KNOWN")          # historical belief reconstructed
        self.assertEqual(rel(adm.store.version).status, "UNKNOWN")
        self.assertEqual(rel(adm.store.version).inactive[0].status, "QUARANTINED")

    def test_unverifiable_identity_is_quarantined_not_trusted(self):
        world = coldchain.emit(days=3)
        for adm, cut, expect in ((coordinator(world), START + timedelta(days=1, hours=3), "needs >= 2"),
                                 (coordinator(world, port=False), START + timedelta(days=3, hours=12), "no evidence port")):
            for raw in admission_requests(world, cut)[:3]:
                v = admit(adm, raw)
            self.assertEqual({e.outcome for e in v.edges}, {"QUARANTINED"})
            self.assertTrue(any(expect in r for e in v.edges for r in e.reasons), v.edges[0].reasons)

    def test_leaky_provider_cannot_verify_with_future_data(self):
        world = coldchain.emit(days=3)
        adm = coordinator(world, leak=True)
        cut = START + timedelta(days=1, hours=3)                 # nothing is available yet
        for raw in admission_requests(world, cut)[:3]:
            v = admit(adm, raw)
        step6 = v.steps[5]
        self.assertEqual(step6.status, "QUARANTINE")
        self.assertTrue(any("not available by the data cut" in d for d in step6.details))

    def test_population_change_and_missing_parent(self):
        world = coldchain.emit(days=5)
        cut = START + timedelta(days=5, hours=12)
        reqs = admission_requests(world, cut)
        adm = coordinator(world)
        v = admit(adm, reqs[2])                                   # rate before its parents
        self.assertEqual(v.steps[3].status, "QUARANTINE")
        self.assertIn("PARENT_NOT_ADMITTED", v.steps[3].details[0])
        self.assertEqual(v.edges, ())
        adm = coordinator(world)
        admit(adm, reqs[0]); admit(adm, reqs[1])
        d = dict(reqs[2]["definition"], population_coverage={"population_id": "orders:site_7:excl_cancelled", "coverage": "subset"})
        v = admit(adm, {**reqs[2], "definition": d})
        self.assertTrue(all("POPULATION_MISMATCH" in e.reasons[0] for e in v.edges))

    def test_redefinition_keeps_domain_time_history(self):
        world, adm, _, cut = admitted_world()
        raw = admission_requests(world, cut)[3]                   # freezer_temp_c
        t0 = START + timedelta(days=2)
        new_def = dict(raw["definition"], sampling_cadence_s=60)
        v_old = adm.store.version
        admit(adm, {**raw, "request_id": "redef", "record_id": "def:freezer_temp_c:v2", "definition": new_def,
                    "effective_from": t0.isoformat()})
        latest = adm.store.version
        self.assertEqual(adm.store.pin(latest, valid_at=t0 - timedelta(hours=1)).node("freezer_temp_c").definition.sampling_cadence_s, 300)
        self.assertEqual(adm.store.pin(latest, valid_at=t0 + timedelta(hours=1)).node("freezer_temp_c").definition.sampling_cadence_s, 60)
        self.assertEqual(len(adm.store.pin(v_old).node_segments("freezer_temp_c")), 1)

    def test_crash_mid_commit_leaves_no_partial_version(self):
        world, adm, _, cut = admitted_world()
        head, digest = adm.log.head(), adm.store.pin().digest()
        adm.log.fail_after_rows = 1
        structure = base_request(world, "crashy", cut, edges=[
            edge("x1", "CAUSAL_HYPOTHESIS", "door_open", "compressor_mode", world.definitions["orders"]["scope"]),
            edge("x2", "OBSERVATIONAL_ASSOCIATION", "door_open", "on_time_dispatch_rate", world.definitions["orders"]["scope"])])
        with self.assertRaises(InjectedCrash):
            admit(adm, structure)
        self.assertEqual((adm.log.head(), adm.store.version, adm.store.pin().digest()), (head, head, digest))
        self.assertEqual(rebuild(adm.log).pin().digest(), digest)
        adm.log.fail_after_rows = None
        self.assertEqual(admit(adm, structure).outcome, "COMMITTED")   # retry after recovery succeeds once

    def test_rebuild_matches_live_store_at_every_version(self):
        _, adm, _, _ = admitted_world()
        rebuilt = rebuild(adm.log)
        for ver in range(adm.store.version + 1):
            self.assertEqual(rebuilt.pin(ver).digest(), adm.store.pin(ver).digest(), f"version {ver}")

    def test_pinned_snapshot_is_stable_across_later_commits(self):
        world, adm, _, cut = admitted_world()
        pin = adm.store.pin(3)
        before = pin.digest()
        admit(adm, base_request(world, "later", cut, edges=[
            edge("later", "OBSERVATIONAL_ASSOCIATION", "door_open", "compressor_mode", world.definitions["orders"]["scope"])]))
        self.assertEqual(pin.digest(), before)


class EdgesG04(unittest.TestCase):
    NODES = {k: N.validate_json(json.dumps({"schema_version": "2.0", "node_id": k.lower(), "kind": k,
                                            "layer": "OBSERVATION" if k == "OBSERVATION_MECHANISM" else "DOMAIN",
                                            **({"variable_id": "v"} if k == "VARIABLE" else {})}))
             for k in ("VARIABLE", "ENTITY", "INTERVENTION", "OBSERVATION_MECHANISM", "LATENT")}
    SCOPE = {"environment_id": "e", "environment_kind": "staging"}

    def _edge(self, kind, layer="DOMAIN", **extra):
        if kind in {"TEMPORAL_LAG", "TEMPORAL_FEEDBACK"}:
            extra.setdefault("lag", {"min_s": 60, "max_s": 120})
        if kind == "ENTITY_RELATIONSHIP":
            extra.setdefault("entity_match_confidence", 1.0)
        if kind == "CAUSALLY_IDENTIFIED":
            extra.setdefault("identification_ref", "cert")
        return E.validate_json(json.dumps(edge(f"e-{kind}", kind, "a", "b", self.SCOPE, layer=layer, **extra)))

    def test_all_13_kinds_have_a_valid_and_an_invalid_endpoint_pair(self):
        n = self.NODES
        valid = {"ENTITY_RELATIONSHIP": ("ENTITY", "ENTITY"), "OBSERVATION_EFFECT": ("OBSERVATION_MECHANISM", "VARIABLE")}
        layer = {"OBSERVATION_EFFECT": "OBSERVATION"}
        self.assertEqual(len(validation.ENDPOINTS), 13)
        for kind in validation.ENDPOINTS:
            with self.subTest(kind=kind):
                src, dst = valid.get(kind, ("VARIABLE", "VARIABLE"))
                e = self._edge(kind, layer.get(kind, "DOMAIN"))
                self.assertEqual(validation.endpoint_problems(e, n[src], n[dst], "metric", "metric"), [])
                bad_src = "ENTITY" if src != "ENTITY" else "VARIABLE"
                self.assertTrue(validation.endpoint_problems(e, n[bad_src], n[dst], "metric", "metric"))

    def test_metric_only_kinds_refuse_non_metrics_and_firewall_holds(self):
        n = self.NODES
        e = self._edge("EXACT_ARITHMETIC")
        self.assertIn("NOT_A_METRIC", validation.endpoint_problems(e, n["VARIABLE"], n["VARIABLE"], "state", "metric")[0])
        hyp = self._edge("CAUSAL_HYPOTHESIS")
        probs = validation.endpoint_problems(hyp, n["OBSERVATION_MECHANISM"], n["VARIABLE"])
        self.assertTrue(any("OBSERVATION_FIREWALL" in p for p in probs))

    def test_missing_adjacency_is_unknown_never_absent(self):
        _, adm, _, _ = admitted_world()
        rel = CausalEvidenceView(adm.store.pin()).relationship("door_open", "on_time_dispatch_rate")
        self.assertEqual((rel.status, rel.active, rel.inactive), ("UNKNOWN", (), ()))
        unknowns = CausalEvidenceView(adm.store.pin()).unknowns_relevant_to("freezer_temp_c", "compressor_mode")
        self.assertIn(("freezer_temp_c", "compressor_mode"), unknowns)
        self.assertIn(("door_alarm_active", "compressor_mode"), unknowns)   # a cause of the treatment: back-door relevant

    def test_instantaneous_cycle_rejected_positive_lag_feedback_kept(self):
        world, adm, v, cut = admitted_world()
        self.assertEqual({e.outcome for e in v["adm-structure"].edges}, {"ADMITTED"})   # includes positive-lag loop
        scope = world.definitions["orders"]["scope"]
        r = admit(adm, base_request(world, "cyc", cut, edges=[
            edge("back", "CAUSAL_HYPOTHESIS", "on_time_dispatch_rate", "freezer_temp_c", scope)]))
        self.assertEqual(r.edges[0].outcome, "REJECTED")
        self.assertIn("INSTANTANEOUS_CYCLE", r.edges[0].reasons[0])
        with self.assertRaises(Exception):
            E.validate_json(json.dumps(edge("fb0", "TEMPORAL_FEEDBACK", "a", "b", scope, lag={"min_s": 0, "max_s": 5})))

    def test_uncertain_entity_links_stay_out_of_exact_closure(self):
        world, adm, _, cut = admitted_world()
        scope = world.definitions["orders"]["scope"]
        nodes = [{"schema_version": "2.0", "node_id": n, "kind": "ENTITY"} for n in ("site_7", "region_eu", "site_7b")]
        r = admit(adm, base_request(world, "ent", cut, nodes=nodes, edges=[
            edge("exact", "ENTITY_RELATIONSHIP", "site_7", "region_eu", scope, entity_match_confidence=1.0),
            edge("fuzzy", "ENTITY_RELATIONSHIP", "site_7b", "region_eu", scope, entity_match_confidence=0.7)]))
        self.assertIn("UNCERTAIN_MATCH", r.steps[2].details[0])
        closure = {e.edge_id for e in StructuralView(adm.store.pin()).exact_closure_edges("region_eu")}
        self.assertEqual(closure, {"exact"})

    def test_decomposition_needs_a_complete_partition(self):
        world, adm, _, cut = admitted_world()
        scope = world.definitions["orders"]["scope"]
        dim = {"schema_version": "2.0", "dimension_id": "door", "scope": scope,
               "provenance": {"source_id": "emu-coldchain", "method": "adapter_declared"}}
        dec = edge("dec", "DIMENSIONAL_DECOMPOSITION", "dispatched_on_time", "orders", scope)
        r = admit(adm, base_request(world, "dec1", cut, edges=[dec],
                                    dimension_specs=[{**dim, "partition_policy": "open_unknown"}]))
        self.assertEqual(r.edges[0].outcome, "QUARANTINED")
        r = admit(adm, base_request(world, "dec2", cut, edges=[{**dec, "edge_id": "dec-b"}],
                                    dimension_specs=[{**dim, "partition_policy": "exhaustive_declared",
                                                      "values": ["d1", "d2", "d3"]}]))
        self.assertEqual(r.edges[0].outcome, "ADMITTED")

    def test_scope_forgery_and_bad_validity_rejected(self):
        world, adm, _, cut = admitted_world()
        scope = world.definitions["orders"]["scope"]
        prod = {"environment_id": "prod-site7", "environment_kind": "production"}
        r = admit(adm, base_request(world, "forge", cut, edges=[
            edge("f1", "OBSERVATIONAL_ASSOCIATION", "door_open", "compressor_mode", prod),
            edge("f2", "OBSERVATIONAL_ASSOCIATION", "door_open", "freezer_temp_c", scope,
                 validity={"valid_from": "2026-11-20T00:00:00Z", "valid_to": "2026-11-19T00:00:00Z"})]))
        reasons = {e.edge_id: e.reasons[0] for e in r.edges}
        self.assertIn("EDGE_SCOPE_MISMATCH", reasons["f1"])
        self.assertIn("INVALID_VALIDITY", reasons["f2"])


class EffectVsEdgeG11(unittest.TestCase):
    def setUp(self):
        self.world, self.adm, _, self.cut = admitted_world()
        self.scope = self.world.definitions["orders"]["scope"]

    def cert(self, cid, src, dst, identifies, scope=None, basis="randomized_assignment"):
        return {"schema_version": "2.0", "certificate_id": cid, "source_node": src, "target_node": dst, "basis": basis,
                "identifies": identifies, "assessment_id": f"a-{cid}", "assessment_status": "CONDITIONAL_IDENTIFICATION",
                "scope": scope or self.scope, "evidence_ids": ["ev1"], "assumptions": ["randomization integrity"]}

    def claim(self):
        return {"schema_version": "2.0", "claim_id": "itt-1", "assessment_id": "a-itt", "assessment_status": "CONDITIONAL_IDENTIFICATION",
                "estimate": 0.012, "uncertainty": {"se": 0.004}, "dependence_model": "sites independent",
                "scope": self.scope, "evidence_ids": ["ev1"], "method": {"method_id": "randomized_effect_v1", "method_version": "0"},
                "estimand": {"outcome": "on_time_dispatch_rate", "contrast": {"treatment": "alarm assigned", "control": "not assigned"},
                             "population": "sites", "horizon": "14d", "functional": "ITT",
                             "source_scope": self.scope, "target_scope": self.scope}}

    def test_itt_certificate_cannot_become_an_exposure_or_mechanism_edge(self):
        itt = self.cert("cert-itt", "door_alarm_assigned", "on_time_dispatch_rate", "total_effect_of_source")
        ident = lambda eid, src: edge(eid, "CAUSALLY_IDENTIFIED", src, "on_time_dispatch_rate", self.scope,
                                      identification_ref="cert-itt")
        r = admit(self.adm, base_request(self.world, "itt", self.cut, certificates=[itt],
                  edges=[ident("exposure", "door_alarm_active"), ident("assignment", "door_alarm_assigned")],
                  effect_claims=[{"claim": self.claim(), "treatment_node": "door_alarm_assigned",
                                  "outcome_node": "on_time_dispatch_rate"}]))
        reasons = {e.edge_id: " ".join(e.reasons) for e in r.edges}
        self.assertIn("RELATION_MISMATCH", reasons["exposure"])            # C01: Z->Y is not D->Y
        self.assertIn("EFFECT_ONLY_CERTIFICATE", reasons["assignment"])     # F05: total effect is not a mechanism
        view = CausalEvidenceView(self.adm.store.pin())
        self.assertEqual(len(view.effect_claims("door_alarm_assigned", "on_time_dispatch_rate")), 1)
        for src in ("door_alarm_assigned", "door_alarm_active"):          # I06: the claim created no edge
            self.assertEqual(view.relationship(src, "on_time_dispatch_rate").status, "UNKNOWN")

    def test_mechanism_certificate_in_matching_scope_is_admitted_other_scope_refused(self):
        mech = self.cert("cert-mech", "door_open", "freezer_temp_c", "mechanism_link")
        r = admit(self.adm, base_request(self.world, "mech", self.cut, certificates=[mech], edges=[
            edge("door-ident", "CAUSALLY_IDENTIFIED", "door_open", "freezer_temp_c", self.scope, identification_ref="cert-mech")]))
        self.assertEqual(r.edges[0].outcome, "ADMITTED")
        prod = {"environment_id": "prod-site7", "environment_kind": "production"}
        r = admit(self.adm, base_request(self.world, "prod", self.cut, scope=prod, edges=[
            edge("prod-ident", "CAUSALLY_IDENTIFIED", "door_open", "freezer_temp_c", prod, identification_ref="cert-mech")]))
        self.assertIn("SCOPE_MISMATCH", " ".join(r.edges[0].reasons))     # sandbox certificate, production edge


class IdentityG09(unittest.TestCase):
    """Phase 2 slice of G09: production identity checker vs the Phase 0 frozen oracle (fixture 01)."""

    def test_identity_checker_agrees_with_phase0_oracle(self):
        from causal_core.api import parse_definition
        fx = json.loads((io.FIXTURE_DIR / "01_ratio_identity.json").read_text())
        d = parse_definition({"schema_version": "2.0", "kind": "metric", "variable_id": "conversion_rate", "unit": "1",
                              "value_schema": {"type": "numeric", "dtype": "proportion"},
                              "scope": {"environment_id": "x", "environment_kind": "staging"},
                              "provenance": {"source_id": "s", "method": "adapter_declared"},
                              "formula_ast": {"node": "binop", "op": "div", "left": {"node": "ref", "variable_id": "orders"},
                                              "right": {"node": "ref", "variable_id": "sessions"}}})
        for key, frozen in (("observations", "main"), ("drifted_variant", "drifted_variant")):
            rows = [(r["bucket"], {"orders": r["numerator"], "sessions": r["denominator"], "conversion_rate": r["reported"]})
                    for r in fx["input"][key]]
            res = verify_identity(d.formula_ast, "conversion_rate", rows, fx["input"]["tolerance"], 1)
            want = fx["expected"][frozen]
            self.assertEqual(res.verdict, want["verdict"])
            self.assertAlmostEqual(res.max_abs_residual, want["max_abs_residual"], places=9)
            self.assertEqual(res.windows_checked, want["rows_checked"])
            self.assertIn(("2026-11-04", "ZERO_DENOMINATOR_OR_PARENT_MISSING"), res.excluded)

    def test_units_population_and_grain(self):
        from causal_core.api import parse_definition
        world = coldchain.emit()
        defs = {k: parse_definition(v) for k, v in world.definitions.items()}
        parents = {k: defs[k] for k in ("orders", "dispatched_on_time")}
        self.assertTrue(check_formula(defs["on_time_dispatch_rate"], parents).ok)
        self.assertEqual(parse_unit("orders/orders"), parse_unit("1"))
        bad_unit = parse_definition({**world.definitions["on_time_dispatch_rate"], "unit": "orders"})
        self.assertIn("UNIT_MISMATCH", check_formula(bad_unit, parents).problems[0])
        weekly = parse_definition({**world.definitions["on_time_dispatch_rate"],
                                   "time_semantics": {"grain": "7d", "window": "tumbling"}})
        self.assertTrue(any("GRAIN_MISMATCH" in p for p in check_formula(weekly, parents).problems))
        self.assertTrue(any("NOT_A_METRIC" in p for p in check_formula(defs["on_time_dispatch_rate"],
                                                                       {**parents, "orders": defs["freezer_temp_c"]}).problems))


class ReadOnlyAndBoundedViews(unittest.TestCase):
    def test_reads_never_write(self):
        _, adm, _, _ = admitted_world()
        version, digest = adm.store.version, adm.store.pin().digest()
        snap = adm.store.pin()
        for view in (StructuralView(snap), TemporalView(snap), CausalEvidenceView(snap)):
            view.neighborhood("freezer_temp_c")
            view.relationship("door_open", "freezer_temp_c")
        CausalEvidenceView(snap).unknowns_relevant_to("freezer_temp_c", "on_time_dispatch_rate")
        TemporalView(snap).lagged_parents("freezer_temp_c")
        self.assertEqual((adm.store.version, adm.store.pin().digest()), (version, digest))
        tree = ast.parse((io.PROJECT_ROOT / "causal_core" / "graph" / "views.py").read_text())
        attrs = {n.attr for n in ast.walk(tree) if isinstance(n, ast.Attribute)}
        self.assertFalse(attrs & {"apply", "commit", "put"}, "views must not reach a mutator")

    def test_traversal_is_bounded_and_says_so(self):
        store = GraphStore()
        scope = {"environment_id": "e", "environment_kind": "staging"}
        nodes = tuple(NodeRecord(N.validate_json(json.dumps({"schema_version": "2.0", "node_id": f"n{i}", "kind": "VARIABLE",
                                                             "variable_id": f"n{i}"})), None, Validity())
                      for i in range(50))
        edges = tuple(E.validate_json(json.dumps(edge(f"e{i}", "OBSERVATIONAL_ASSOCIATION", f"n{i}", f"n{i+1}", scope)))
                      for i in range(49))
        from datetime import datetime, timezone
        store.apply(GraphDelta(1, "r", "f" * 64, "s", "rec", datetime(2026, 1, 1, tzinfo=timezone.utc), nodes, edges))
        view = CausalEvidenceView(store.pin())
        hops = view.neighborhood("n0", TraversalBound(max_hops=3))
        self.assertEqual((hops.truncated, hops.reason, len(hops.nodes)), (True, "MAX_HOPS", 4))
        capped = view.neighborhood("n0", TraversalBound(max_hops=100, max_nodes=10))
        self.assertEqual((capped.truncated, capped.reason), (True, "MAX_NODES"))
        full = view.neighborhood("n0", TraversalBound(max_hops=100, max_nodes=100))
        self.assertEqual((full.truncated, len(full.nodes)), (False, 50))


class SubscriberOnlyNotification(unittest.TestCase):
    def test_no_auto_monitoring_and_only_subscribers_hear(self):
        world = coldchain.emit(days=5)
        adm = coordinator(world)
        cut = START + timedelta(days=5, hours=12)
        heard = []
        adm.subscribe("temp-watch", {"freezer_temp_c"}, lambda v, vars_: heard.append((v, vars_)))
        with self.assertRaises(ValueError):
            adm.subscribe("everything", set())
        verdicts = [admit(adm, raw) for raw in admission_requests(world, cut)]
        self.assertEqual(verdicts[0].notified_subscribers, ())               # orders: nobody subscribed
        self.assertEqual(verdicts[3].notified_subscribers, ("temp-watch",))  # freezer_temp_c definition
        self.assertEqual(verdicts[-1].notified_subscribers, ("temp-watch",)) # structure touches freezer_temp_c
        self.assertEqual([v for v, _ in heard], [4, 7])


class PurityG01Phase2(unittest.TestCase):
    """G01 for the Phase 2 pure layer: semantics/ and graph/ do no I/O and import no I/O libraries."""

    def test_pure_graph_and_semantics_modules(self):
        banned = {"socket", "sqlite3", "subprocess", "requests", "urllib", "http", "os", "pathlib", "shutil"}
        pure = [*(io.PROJECT_ROOT / "causal_core" / "semantics").glob("*.py"), *(io.PROJECT_ROOT / "causal_core" / "graph").glob("*.py")]
        self.assertGreaterEqual(len(pure), 4)
        for path in pure:
            for node in ast.walk(ast.parse(path.read_text())):
                names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                         else [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else [])
                for name in names:
                    self.assertNotIn(name.split(".")[0], banned, f"{path.name} imports {name}")
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) in {"open", "print", "input"}:
                    self.fail(f"{path.name}:{node.lineno} calls {node.func.id}")


if __name__ == "__main__":
    unittest.main()
