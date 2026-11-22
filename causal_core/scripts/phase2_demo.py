"""Phase 2 demo: typed bitemporal graph, eight-step admission, views, effect-vs-edge, crash safety.

    python -m scripts.phase2_demo
    python -m scripts.phase2_demo --no-color

Outcome vocabulary: ADMITTED (success) · REJECTED (validation refusal: structurally invalid) ·
QUARANTINED / UNKNOWN (epistemic abstention: not enough evidence to trust or assert).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import unittest
from datetime import timedelta
from io import StringIO

from pydantic import TypeAdapter

from adapters.sources.emulator_port import EmulatorEvidencePort, admission_requests
from causal_core.contracts.admission import AdmissionRequest
from causal_core.graph.store import GraphStore
from causal_core.graph.views import CausalEvidenceView, StructuralView
from causal_core.orchestration.admission import GraphAdmissionCoordinator
from causal_core.storage.graph_log import GraphLog, InjectedCrash, rebuild
from emulator import coldchain
from evaluation.fixture_io import PROJECT_ROOT

C = {"b": "\033[1m", "d": "\033[2m", "g": "\033[32m", "y": "\033[33m", "r": "\033[31m", "c": "\033[36m", "x": "\033[0m"}
R = TypeAdapter(AdmissionRequest)
START = coldchain.START
CODE = {"PASS": ("P", "g"), "NOT_APPLICABLE": ("·", "d"), "QUARANTINE": ("Q", "y"), "REJECT": ("R", "r"), "SKIPPED": ("-", "d")}
OUTCOME = {"ADMITTED": "g", "QUARANTINED": "y", "REJECTED": "r"}


def paint(text, *styles):
    return "".join(C[s] for s in styles) + text + C["x"] if C["x"] else text


def section(n, title):
    print("\n" + paint(f"[{n}] {title}", "b", "c"))


def coordinator(world):
    return GraphAdmissionCoordinator(GraphStore(), GraphLog(os.path.join(tempfile.mkdtemp(), "graph.db")),
                                     EmulatorEvidencePort(world))


def admit(adm, raw):
    return adm.admit(R.validate_json(json.dumps(raw)))


def edge_line(o):
    reason = f"  {o.reasons[0][:96]}" if o.reasons else ""
    return f"       {paint(f'{o.outcome:<11}', OUTCOME[o.outcome])} {o.edge_id:<58}" + paint(reason, "d")


def request(world, rid, cut, **parts):
    return {"schema_version": "2.0", "request_id": rid, "source_id": "emu-coldchain", "record_id": f"rec:{rid}",
            "attestation": {"source_id": "emu-coldchain", "method": "adapter_declared"},
            "scope": parts.pop("scope", world.definitions["orders"]["scope"]), "data_cut": cut.isoformat(), **parts}


def edge(eid, kind, src, dst, scope, **extra):
    return {"schema_version": "2.0", "edge_id": eid, "kind": kind, "source": src, "target": dst, "status": "ACTIVE",
            "scope": scope, "lineage": {"source_id": "emu-coldchain", "origin_kind": "original",
                                        "lineage_root_id": f"demo:{eid}", "content_hash": "d" * 64}, **extra}


def run() -> int:
    print(paint("CAUSAL CORE · PHASE 2 · typed bitemporal graph and admission", "b"))
    print(paint(__doc__.split("\n\n")[2].strip(), "d"))

    world = coldchain.emit(days=5)
    cut = START + timedelta(days=5, hours=12)
    adm = coordinator(world)
    scope = world.definitions["orders"]["scope"]

    section(1, "Eight-step admission of the cold-chain world  (P pass · Q quarantine · R reject · · n/a)")
    print(paint("       steps 1 2 3 4 5 6 7 8   request", "d"))
    for raw in admission_requests(world, cut):
        v = admit(adm, raw)
        codes = " ".join(paint(*CODE[s.status]) for s in v.steps)
        print(f"  v{v.graph_version:<3}       {codes}   {raw['request_id']}")
        for o in v.edges:
            print(edge_line(o))
        s6 = v.steps[5]
        if s6.status != "NOT_APPLICABLE":
            print(paint(f"       step 6: {s6.details[0]}", "d"))
    print(f"  evidence queries issued: {adm.queries_issued}  (one bounded typed query, only for the exact identity)")

    section(2, "Definition drift: the source silently drops cancelled orders from the rate's denominator")
    drift_world = coldchain.emit(days=6, drift_from_day=3)
    dadm = coordinator(drift_world)
    early, late = START + timedelta(days=3, hours=7), START + timedelta(days=6, hours=12)
    for raw in admission_requests(drift_world, early)[:3]:
        admit(dadm, raw)
    v_before = dadm.store.version
    reattest = {**admission_requests(drift_world, late)[2], "request_id": "reattest",
                "record_id": "def:on_time_dispatch_rate:reattest-1"}
    v = admit(dadm, reattest)
    for o in v.edges:
        print(edge_line(o))
    for ver in (v_before, dadm.store.version):
        rel = StructuralView(dadm.store.pin(ver)).relationship("orders", "on_time_dispatch_rate")
        state = rel.active[0].status if rel.active else (rel.inactive[0].status if rel.inactive else "none")
        print(f"  pin(version={ver}) orders→on_time_dispatch_rate: {paint(rel.status, 'g' if rel.status == 'KNOWN' else 'y')}"
              f" (edge {state})   digest {dadm.store.pin(ver).digest()[:12]}…")
    print(paint("  the earlier belief is reconstructed exactly; the new one is quarantined with its residual, not overwritten", "d"))

    section(3, "Open world and structural refusals")
    view = CausalEvidenceView(adm.store.pin())
    rel = view.relationship("door_open", "on_time_dispatch_rate")
    print(f"  door_open ? on_time_dispatch_rate → {paint(rel.status, 'y')} (no edge recorded ≠ no effect)")
    print(f"  unknowns relevant to freezer_temp_c → compressor_mode: {view.unknowns_relevant_to('freezer_temp_c', 'compressor_mode')}")
    prod = {"environment_id": "prod-site7", "environment_kind": "production"}
    v = admit(adm, request(world, "refusals", cut, edges=[
        edge("back-loop", "CAUSAL_HYPOTHESIS", "on_time_dispatch_rate", "freezer_temp_c", scope),
        edge("lag-as-cause", "CAUSAL_HYPOTHESIS", "dispatch_reporting_lag", "on_time_dispatch_rate", scope),
        edge("forged-scope", "OBSERVATIONAL_ASSOCIATION", "door_open", "compressor_mode", prod)]))
    for o in v.edges:
        print(edge_line(o))

    section(4, "Effect claim ≠ structural edge (C01, F05, I06)")
    cert = lambda cid, s, t, ident: {"schema_version": "2.0", "certificate_id": cid, "source_node": s, "target_node": t,
                                     "basis": "randomized_assignment", "identifies": ident, "assessment_id": f"a-{cid}",
                                     "assessment_status": "CONDITIONAL_IDENTIFICATION", "scope": scope,
                                     "evidence_ids": ["ev-study-1"], "assumptions": ["randomization integrity", "no interference"]}
    claim = {"schema_version": "2.0", "claim_id": "itt-door-alarm", "assessment_id": "a-cert-itt",
             "assessment_status": "CONDITIONAL_IDENTIFICATION", "estimate": 0.012, "uncertainty": {"se": 0.004},
             "dependence_model": "sites independent", "scope": scope, "evidence_ids": ["ev-study-1"],
             "method": {"method_id": "randomized_effect_v1", "method_version": "0"},
             "estimand": {"outcome": "on_time_dispatch_rate", "contrast": {"treatment": "alarm assigned", "control": "not assigned"},
                          "population": "freezer sites", "horizon": "14d", "functional": "ITT",
                          "source_scope": scope, "target_scope": scope}}
    ident = lambda eid, s, t, ref, sc=scope: edge(eid, "CAUSALLY_IDENTIFIED", s, t, sc, identification_ref=ref)
    v = admit(adm, request(world, "effects", cut,
                           certificates=[cert("cert-itt", "door_alarm_assigned", "on_time_dispatch_rate", "total_effect_of_source"),
                                         cert("cert-mech", "door_open", "freezer_temp_c", "mechanism_link")],
                           edges=[ident("exposure-edge", "door_alarm_active", "on_time_dispatch_rate", "cert-itt"),
                                  ident("assignment-edge", "door_alarm_assigned", "on_time_dispatch_rate", "cert-itt"),
                                  ident("mechanism-edge", "door_open", "freezer_temp_c", "cert-mech")],
                           effect_claims=[{"claim": claim, "treatment_node": "door_alarm_assigned",
                                           "outcome_node": "on_time_dispatch_rate"}]))
    for o in v.edges:
        print(edge_line(o))
    v = admit(adm, request(world, "transport", cut, scope=prod,
                           edges=[ident("prod-edge", "door_open", "freezer_temp_c", "cert-mech", prod)]))
    for o in v.edges:
        print(edge_line(o))
    view = CausalEvidenceView(adm.store.pin())
    claims = view.effect_claims("door_alarm_assigned", "on_time_dispatch_rate")
    print(f"  effect claims stored for assigned→rate: {[c.claim_id for c in claims]} (artifact) · "
          f"edge assigned→rate: {view.relationship('door_alarm_assigned', 'on_time_dispatch_rate').status}")

    section(5, "Crash safety and replay")
    head, digest = adm.log.head(), adm.store.pin().digest()
    adm.log.fail_after_rows = 1
    try:
        admit(adm, request(world, "crash", cut, edges=[
            edge("c1", "OBSERVATIONAL_ASSOCIATION", "door_open", "freezer_temp_c", scope),
            edge("c2", "OBSERVATIONAL_ASSOCIATION", "compressor_mode", "on_time_dispatch_rate", scope)]))
    except InjectedCrash as exc:
        print(f"  injected: {exc}")
    adm.log.fail_after_rows = None
    same = adm.log.head() == head and adm.store.version == head and adm.store.pin().digest() == digest
    print(f"  after crash: log head {adm.log.head()}, store version {adm.store.version}, digest unchanged: "
          + paint(str(same), "g" if same else "r"))
    rebuilt = rebuild(adm.log)
    equal = all(rebuilt.pin(v).digest() == adm.store.pin(v).digest() for v in range(adm.store.version + 1))
    print(f"  rebuild from SQLite log: digests equal at all {adm.store.version + 1} versions: " + paint(str(equal), "g" if equal else "r"))

    section(6, "Profile (python -m scripts.phase2_profile; measured, not promised)")
    prof_path = PROJECT_ROOT / "docs" / "phase2" / "graph_profile.json"
    if prof_path.exists():
        p = json.loads(prof_path.read_text())
        t = p["bounded_traversal_ms"]
        print(f"  {p['machine']['cpu']}, Python {p['machine']['python']} · {p['shape']['nodes']} nodes / {p['shape']['edges']} edges / "
              f"{p['shape']['versions']} versions")
        print(f"  pin p50 {p['pin_ns']['p50']:.0f} ns · traversal p50/p95/p99 {t['p50']}/{t['p95']}/{t['p99']} ms "
              f"(stops: {t['stop_reasons']}) · memory v1→v10 {p['memory_tracemalloc']['after_v1_mib']}→"
              f"{p['memory_tracemalloc']['after_v10_mib']} MiB")
    else:
        print("  not measured yet")

    section(7, "Gates (run live from tests/test_phase2_graph.py)")
    from tests import test_phase2_graph as t
    ok_all = True
    for cls, label in (("EdgesG04", "G04 13 edge kinds, UNKNOWN, cycles, firewall, scope"),
                       ("AdmissionG05", "G05 admission, bitemporal replay, drift, crash, rebuild"),
                       ("IdentityG09", "G09 (Phase 2 part) units/pop/grain; identity = Phase 0 oracle"),
                       ("EffectVsEdgeG11", "G11 effect claim vs structural edge"),
                       ("ReadOnlyAndBoundedViews", "read-only views, bounded traversal"),
                       ("SubscriberOnlyNotification", "subscriber-only notification, no auto-monitor")):
        res = unittest.TextTestRunner(stream=StringIO(), verbosity=0).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(getattr(t, cls)))
        ok_all &= res.wasSuccessful()
        mark = paint("✓", "g") if res.wasSuccessful() else paint("✗", "r")
        print(f"  {mark} {label:<62}{res.testsRun} test{'s' if res.testsRun != 1 else ''}")
    return 0 if ok_all else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-color", action="store_true")
    if ap.parse_args(argv).no_color or not sys.stdout.isatty():
        for k in C:
            C[k] = ""
    return run()


if __name__ == "__main__":
    sys.exit(main())
