"""Phase 5 demo: one question-first coordinator, many triggers, typed failures, a safety gate.

    python -m scripts.phase5_demo
    python -m scripts.phase5_demo --no-color
"""
from __future__ import annotations

import argparse
import json
import sys
import unittest
from io import StringIO

from causal_core.orchestration.policy import ReportEnvelope
from emulator import experiment_world as W
from evaluation.fixture_io import PROJECT_ROOT
from evaluation.phase5_cases import (Q_ATE, Q_DESIGN, Q_ITT, Q_MONITOR, Q_NO_STUDY, Q_PREDICT, Q_STUDY, harness, task,
                                     truth_check)

C = {"b": "\033[1m", "d": "\033[2m", "g": "\033[32m", "y": "\033[33m", "r": "\033[31m", "c": "\033[36m", "x": "\033[0m"}
OUTCOME_COLOR = {"COMPLETED": "g", "INCONCLUSIVE": "y", "WAITING_FOR_EVIDENCE": "y", "PARTIAL_BUDGET": "y",
                 "UNSUPPORTED": "r", "FAILED_VALIDATION": "r"}


def paint(t, *s):
    return "".join(C[x] for x in s) + t + C["x"] if C["x"] else t


def section(n, title):
    print("\n" + paint(f"[{n}] {title}", "b", "c"))


def headline(r) -> str:
    f = r.state.findings[0] if r.state.findings else None
    return (f.statement if f else "-")[:92]


def run() -> int:
    print(paint("CAUSAL CORE · PHASE 5 · question-first planner, typed evidence, safety output", "b"))
    print(paint("One coordinator. The trigger and question decide the route; no anomaly or experiment is required.", "d"))
    co, sink, ledger, world = harness()

    section(1, "Five entry points, one core")
    runs = {}
    for tid, q, trig, extra in (("ask-itt", Q_ITT, "explicit_question", {}), ("ask-ate", Q_ATE, "explicit_question", {}),
                                ("study-done", Q_STUDY, "study_completion", {}),
                                ("monitor-alarm", Q_MONITOR, "monitor", {"incident_id": "inc-0042"}),
                                ("design", Q_DESIGN, "explicit_question", {}), ("forecast", Q_PREDICT, "scheduled", {})):
        r = co.run(task(tid, q, trig, **extra))
        runs[tid] = r
        route = " → ".join(dict.fromkeys(s for s, _, _ in r.state.trace))
        print(f"  {tid:<14} {trig:<18} {paint(r.state.outcome, OUTCOME_COLOR.get(r.state.outcome, 'y')):<22} "
              f"{paint(r.decisions[-1].verdict, 'd')}")
        print(paint(f"      route: {route}", "d"))
        print(f"      {headline(r)}")

    section(2, "Inside one run: the explicit effect question, stage by stage")
    r = runs["ask-itt"]
    for stage, verdict, note in r.state.trace:
        print(f"  {stage:<20} {paint(verdict, 'g' if verdict in ('PASS', 'DONE', 'PLAN', 'AUDIT') else 'y'):<6} {note[:100]}")
    refused = [(m, rs[0]) for m, st, rs in r.state.applicability if st == "UNSUPPORTED"][:3]
    print(paint(f"  methods refused before running ({len(r.state.applicability)} checked): " + "; ".join(f"{m}: {why[:44]}" for m, why in refused), "d"))
    print(paint("  rivals and untestable assumptions carried into the report:", "d"))
    for c in r.state.challenges[:4]:
        print(paint(f"    · {c[:108]}", "d"))

    section(3, "Every failure is typed: nothing hangs, nothing is silently skipped")
    cases = (("provider hangs (1 s, deadline 200 ms)", dict(hang_s=1.0), task("f-hang", Q_ITT, max_wall_ms=200)),
             ("provider crashes", dict(fail="connection reset by peer"), task("f-crash", Q_ITT)),
             ("query budget is 0", {}, task("f-quota", Q_ITT, max_queries=0)),
             ("effect question names no study", {}, task("f-nostudy", Q_NO_STUDY)),
             ("task scoped to another environment", {}, task("f-scope", Q_ITT, scope=W.scope("emu-product-2"))),
             ("randomization broken (56/44 split)", "srm", task("f-srm", Q_ITT)))
    for label, kw, t in cases:
        c2, *_ = harness(W.build(treated_share=0.56)) if kw == "srm" else harness(**kw)
        rr = c2.run(t)
        f0 = rr.state.failures[0]
        ms = rr.timings_ms.get("final_published", 0)
        print(f"  {label:<38} {paint(rr.state.outcome, OUTCOME_COLOR.get(rr.state.outcome, 'y')):<22} "
              f"{f0.cls:<24} {ms:>6.0f} ms")
        print(paint(f"      {f0.detail[:100]}" + (f" · gap: {rr.state.gaps[0].recommended_measurements[0][:60]}"
                                                   if rr.state.gaps else ""), "d"))

    section(4, "Safety gate: reports, proposals and actions are different things")
    act = co.run(task("ship-it", Q_ITT), requested_action="ship variant to 100% of traffic")
    print(f"  'ship variant to 100%' after a positive ITT → {paint(act.decisions[-1].verdict, 'r')}: {act.decisions[-1].reasons[0][:90]}")
    print(f"  'how do we tell price display from a tracking bug?' → {paint(runs['design'].decisions[-1].verdict, 'y')}")
    for f in runs["design"].state.findings:
        print(paint(f"      {f.statement}", "d"))
    for g in runs["design"].state.gaps:
        print(paint(f"      gap: {g.missing[:60]} → {g.recommended_measurements[0][:60]}", "d"))
    out = PROJECT_ROOT / "docs" / "phase5"
    out.mkdir(parents=True, exist_ok=True)
    (out / "result_schema.json").write_text(json.dumps(ReportEnvelope.model_json_schema(), indent=2) + "\n")
    (out / "example_report_itt.json").write_text(json.dumps(runs["ask-itt"].reports[-1], indent=2) + "\n")
    (out / "example_report_abstention.json").write_text(json.dumps(runs["ask-ate"].reports[-1], indent=2) + "\n")
    rep = runs["ask-itt"].reports[-1]
    print(f"  serialized report: {len(rep['findings'])} finding, {len(rep['effect_claims'])} claim, "
          f"{len(rep['method_trace'])} method decisions, {len(rep['evidence_ids'])} evidence id, pins {rep['pins']['data_cut']}")
    print(paint("  written: docs/phase5/result_schema.json, example_report_itt.json, example_report_abstention.json", "d"))

    section(5, "Progressive delivery and replay")
    r = runs["ask-itt"]
    print(f"  revision 1 (provisional) at {r.timings_ms['T0_published']:.1f} ms · revision 2 (final) at "
          f"{r.timings_ms['final_published']:.1f} ms · delivery {r.delivery}")
    before = len(sink.visible())
    again = co.run(task("ask-itt", Q_ITT))
    print(f"  same task again → {again.delivery}; visible publications {before} → {len(sink.visible())} (no duplicates)")

    section(6, "Scored against hidden truth (harness side only)")
    t = truth_check(runs["ask-itt"], runs["monitor-alarm"], world)
    print(f"  ITT truth {t['itt_truth']:.3f} · estimate {t['itt_estimate']:+.4f} · CI covers truth: "
          f"{paint(str(t['itt_ci_covers_truth']), 'g' if t['itt_ci_covers_truth'] else 'r')}")
    print(f"  effect of actually seeing the variant (truth {t['exposure_effect_truth']:.3f}): claimed = "
          f"{paint(str(t['exposure_effect_claimed']), 'g' if not t['exposure_effect_claimed'] else 'r')} (abstained: not identified by assignment)")
    print(f"  conversion drop on day {t['drop_day_truth']} → alarm on day {t['alarm_point']} "
          f"({t['detection_delay_days']} days later; no alarm before the drop: {not t['alarm_before_drop']})")

    section(7, "Gates (run live from tests/test_phase5_orchestration.py)")
    from tests import test_phase5_orchestration as T
    ok_all = True
    for cls, label in (("TaskIndependenceG13", "G13 no monitor needed; any trigger; no domain words in the planner"),
                       ("CapabilityTraceG10", "G10 every method decision persisted"),
                       ("AcquisitionG14", "G14 ranked queries, gaps, nothing executed"),
                       ("DurableRevisionsG15", "G15 provisional → final revisions; replay idempotent"),
                       ("FailureTaxonomyAndDeadlines", "typed failures; hung provider bounded by deadline"),
                       ("PolicyGate", "safety gate: actions denied; effects need claims; schema"),
                       ("TruthScoredOutsideTheCore", "truth: ITT covered, exposure not claimed, drop found"),
                       ("PurityG01Phase5", "G01 orchestration imports no emulator/evaluation/adapters")):
        res = unittest.TextTestRunner(stream=StringIO(), verbosity=0).run(unittest.defaultTestLoader.loadTestsFromTestCase(getattr(T, cls)))
        ok_all &= res.wasSuccessful()
        print(f"  {paint('✓', 'g') if res.wasSuccessful() else paint('✗', 'r')} {label:<70}{res.testsRun} test{'s' if res.testsRun != 1 else ''}")
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
