"""Phase 1 demo: canonical schema v2.0 on one emulator world, adversarial corpus, legacy migration.

    python -m scripts.phase1_demo
    python -m scripts.phase1_demo --no-color
"""
from __future__ import annotations

import argparse
import json
import sys
import unittest
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from io import StringIO

from adapters.legacy.evidence_queries_v1 import UnknownLegacyMethod, to_typed_request
from causal_core import api
from causal_core.contracts import METRIC_ONLY_FIELDS
from emulator import coldchain
from evaluation import fixture_io as io
from evaluation.adversarial_contracts import build_cases, run_case

C = {"b": "\033[1m", "d": "\033[2m", "g": "\033[32m", "y": "\033[33m", "r": "\033[31m", "c": "\033[36m", "x": "\033[0m"}
NOW = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)


def paint(text: str, *styles: str) -> str:
    return "".join(C[s] for s in styles) + text + C["x"] if C["x"] else text


def section(n: int, title: str) -> None:
    print("\n" + paint(f"[{n}] {title}", "b", "c"))


OK, NO = (lambda: paint("✓", "g")), (lambda: paint("✗", "r"))


def run() -> int:
    print(paint("CAUSAL CORE · PHASE 1 · canonical schema v2.0", "b"))
    print(paint("One emulated cold-chain site, seen as a metric, a sensor state, events and a categorical mode.\n"
                "The emulator emits plain JSON; the core parses it with frozen, strict, closed contracts.", "d"))
    world = coldchain.emit()
    defs = {k: api.parse_definition(v) for k, v in world.definitions.items()}

    section(1, "One world, four variable kinds — no metric scaffolding on non-metrics")
    shapes, counts = defaultdict(set), defaultdict(int)
    for raw in world.observations:
        obs = api.parse_observation(raw, defs[raw["variable_id"]])
        shapes[obs.variable_id].add(obs.payload.shape)
        counts[obs.variable_id] += 1
    print(f"  {'variable':<24}{'kind':<13}{'payload':<19}{'obs':>4}   metric-only fields set")
    nonmetric_with_fields = 0
    for vid, d in defs.items():
        present = [f for f in METRIC_ONLY_FIELDS if getattr(d, f, None) is not None]
        nonmetric_with_fields += d.kind != "metric" and bool(present)
        print(f"  {vid:<24}{d.kind:<13}{', '.join(sorted(shapes[vid])) or '—':<19}{counts[vid]:>4}   "
              + (", ".join(present) if present else paint("none", "d")))
    print(f"  {OK() if nonmetric_with_fields == 0 else NO()} stop condition: 3 non-metric definitions parse "
          f"with zero numerator / denominator / window / formula / experiment / alert fields")

    section(2, "Conditional fields — each operation checks what it needs, nothing is defaulted")
    ops = ("exact_identity", "mix_rate_decomposition", "time_bucketing", "sequential_monitoring")
    print("  " + f"{'':<24}" + "".join(f"{o:<26}" for o in ops))
    short = {"formula_ast": "formula", "population_coverage": "pop", "time_semantics": "time",
             "aggregation_kind=ratio": "ratio", "unit": "unit", "sampling_cadence_s": "cadence"}
    for vid, d in defs.items():
        cells = []
        for op in ops:
            r = api.assess_contract_compatibility(d, op)
            if r.compatible:
                text, colour = "✓ ready", "g"
            elif r.missing:
                text, colour = "needs " + "+".join(short.get(m, m) for m in r.missing), "y"
            else:
                text, colour = f"n/a for {d.kind}", "d"
            cells.append(paint(f"{text:<26}", colour))
        print(f"  {vid:<24}" + "".join(cells))
    ev = api.assess_contract_compatibility(defs["door_open"], "time_bucketing")
    print(paint(f"  e.g. door_open × time_bucketing: {ev.reasons[0]}", "d"))

    section(3, "Adversarial corpus — one defect per case, positive controls must pass")
    by_cat, passed, cases = defaultdict(list), 0, build_cases(world)
    for case in cases:
        ok, observed = run_case(case)
        passed += ok
        by_cat[case.category].append((case, ok, observed))
    for cat, rows in by_cat.items():
        good = all(ok for _, ok, _ in rows)
        case, _, observed = rows[-1]
        print(f"  {OK() if good else NO()} {cat:<28}{len(rows)} cases   e.g. {case.case_id} → "
              + paint(observed[:78], "d"))
    print(f"  {passed}/{len(cases)} behaved as expected "
          f"({sum(c.expect == 'ACCEPT' for c in cases)} positive controls accepted)")

    section(4, "Time firewall — replay needs a KNOWN available_at (daily metric is published 6 h late)")
    replay = coldchain.START + timedelta(days=1, hours=3)
    rows = [api.parse_observation(r) for r in world.observations if r["record_key"] in ("day0", "day1")]
    for obs in rows:
        state, why = api.replay_visibility(obs, replay)
        naive = obs.clocks.observed_at.value <= replay
        colour = "g" if state == "VISIBLE" else "y"
        print(f"  {obs.observation_id:<28} observed ≤ replay: {str(naive):<5}  → {paint(state, colour)} ({why})")
    print(paint(f"  replay time {replay.isoformat()}: an observed_at-only rule would leak the day-0 dispatch rate", "d"))

    section(5, "Legacy migration — byte receipts, no granted scope or authority")
    fx = json.loads((io.FIXTURE_DIR / "09_migrated_legacy_record.json").read_text())
    frozen = fx["expected"]["receipts"]
    for name, raw in fx["input"]["raw_records"].items():
        r = api.migrate_legacy_record(raw, migrated_at=NOW)
        match = r.receipt.original_sha256 == frozen[name]["original_sha256"]
        rec = r.record
        detail = (f"{rec.payload.shape}, {rec.scope.environment_kind}, iid {rec.payload.iid}"
                  if hasattr(rec, "payload") else f"{rec.kind} {rec.status} {list(rec.status_reasons) or ''}")
        print(f"  {'fixture 09 ' + name:<38}{r.outcome:<26}{detail}  sha {r.receipt.original_sha256[:10]}… "
              + (OK() + " = Phase 0" if match else NO()))
    for raw in world.legacy_records[-3:]:
        r = api.migrate_legacy_record(raw, migrated_at=NOW)
        label = f"emulator v{r.receipt.source_schema_version} {r.receipt.source_kind}"
        print(f"  {label:<38}{r.outcome:<26}"
              + paint((r.reasons[0] if r.reasons else "")[:72], "d"))
    scope = world.definitions["orders"]["scope"]
    req = to_typed_request("fetch_segment_breakdown", request_id="r1", scope=scope, data_cut="2026-11-20T00:00:00Z",
                           metric_id="on_time_dispatch_rate", dimension="door_id", top_k=5)
    print(f"  v1 fetch_segment_breakdown → {type(req).__name__}(include_other={req.include_other}, "
          f"bucket_grain={req.bucket_grain}, max_buckets={req.max_buckets})")
    try:
        to_typed_request("fetch_anything", request_id="r2", scope=scope, data_cut="2026-11-20T00:00:00Z")
    except UnknownLegacyMethod as exc:
        print(f"  v1 fetch_anything → refused: " + paint(str(exc)[:95], "d"))

    section(6, "Gates (run live from tests/test_phase1_contracts.py)")
    from tests import test_phase1_contracts as t
    gates = [("StopCondition", "stop condition"), ("RoundTripG02", "G02 canonical types + round trip"),
             ("MigrationG03", "G03 migration = Phase 0 frozen fixture 09"),
             ("TimeFirewallG06", "G06 replay = Phase 0 oracle on fixture 03"),
             ("EvidenceIndependenceG07", "G07 provenance role vs origin/scope"),
             ("ClaimsI05", "I05 effect claims need identification + uncertainty"),
             ("AdversarialCorpus", "plan §L adversarial corpus"),
             ("CompatibilityAndPorts", "conditional fields, D1 legacy port, no-write P0 ports")]
    all_ok = True
    for cls, label in gates:
        res = unittest.TextTestRunner(stream=StringIO(), verbosity=0).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(getattr(t, cls)))
        good = res.wasSuccessful()
        all_ok &= good
        print(f"  {OK() if good else NO()} {label:<58}{res.testsRun} test{'s' if res.testsRun != 1 else ''}")
    return 0 if all_ok else 1


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-color", action="store_true")
    if ap.parse_args(argv).no_color or not sys.stdout.isatty():
        for k in C:
            C[k] = ""
    return run()


if __name__ == "__main__":
    sys.exit(main())
