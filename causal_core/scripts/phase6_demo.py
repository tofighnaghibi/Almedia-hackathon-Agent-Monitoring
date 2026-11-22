"""Phase 6 demo: three different sources, one core. Truth-scored, compared with a dashboard rule, under load.

    python -m scripts.phase6_demo
    python -m scripts.phase6_demo --no-color
"""
from __future__ import annotations

import argparse
import csv
import json
import sys
import unittest
from io import StringIO

from evaluation import phase6_cases as P6
from evaluation.fixture_io import PROJECT_ROOT

C = {"b": "\033[1m", "d": "\033[2m", "g": "\033[32m", "y": "\033[33m", "r": "\033[31m", "c": "\033[36m", "x": "\033[0m"}
COL = {"COMPLETED": "g", "INCONCLUSIVE": "y", "WAITING_FOR_EVIDENCE": "y", "FAILED_VALIDATION": "r", "UNSUPPORTED": "r"}


def paint(t, *s):
    return "".join(C[x] for x in s) + t + C["x"] if C["x"] else t


def section(n, title):
    print("\n" + paint(f"[{n}] {title}", "b", "c"))


def show_case(label, r):
    f0 = r.state.findings[0].statement if r.state.findings else "-"
    fail = f" · {r.state.failures[0].cls}" if r.state.failures else ""
    print(f"  {label:<11} {paint(r.state.outcome, COL.get(r.state.outcome, 'y')):<22}{paint(fail, 'd')}")
    for f in r.state.findings[:2]:
        print(paint(f"      {f.claim_kind}: {f.statement[:112]}", "d"))


def run() -> int:
    print(paint("CAUSAL CORE · PHASE 6 · three end-to-end slices on one core", "b"))
    print(paint("Different sources and adapters; the same contracts, planner, methods, ledger and policy gate.", "d"))
    sl = P6.run_slices()

    section(1, "Three sources, one core")
    rows = (("S1", "web conversion by country (warehouse-style)", "market-warehouse-adapter", "aggregate_stats strata",
             "EXPLAIN_OBSERVATION, DETECT_CHANGE"),
            ("S2", "A/B test, no monitor configured", "emu-product-port", "assignment_outcomes",
             "ESTIMATE_EFFECT"),
            ("S3", "freezer sensors + door events, no metrics", "sensor-gateway-adapter", "state_trajectory, graph",
             "ASSESS_HYPOTHESIS, DETECT_CHANGE"))
    for s, what, adapter, shapes, qs in rows:
        print(f"  {paint(s, 'b')}  {what:<44} {paint(adapter, 'd'):<26}  {shapes:<24} {qs}")

    section(2, "S1 · 'Why did web conversion fall this week?'")
    r = sl["S1"]["success"]
    for stage, verdict, note in r.state.trace:
        if stage in ("AUDIT_INPUTS", "ACQUIRE", "EVALUATE", "REPORT"):
            print(paint(f"  {stage:<14} {verdict:<5} {note[:104]}", "d"))
    for f in r.state.findings:
        print(f"  {paint(f.claim_kind, 'g' if f.claim_kind != 'DQ_FAILURE' else 'y')}: {f.statement[:150]}")
    b = P6.s1_baseline(sl["_worlds"]["S1"])
    print(f"  {paint('dashboard rule', 'r')} ({b['rule']}): {b['what_it_says']} → alert = {b['alert']}")
    print(paint(f"  truth: every country's rate is unchanged; Brazil's traffic share went from "
                f"{sl['_worlds']['S1'].truth['base_share']['BR']:.0%} to {sl['_worlds']['S1'].truth['current_share']['BR']:.0%}, "
                f"and {sl['_worlds']['S1'].truth['immature_day']} is only 62% loaded", "d"))
    show_case("error", sl["S1"]["error"])
    show_case("abstention", sl["S1"]["abstention"])

    section(3, "S2 · an A/B test question with no monitor anywhere")
    for k in ("success", "error", "abstention"):
        show_case(k, sl["S2"][k])

    section(4, "S3 · sensors and events, no metric fields at all")
    for k in ("success", "error", "abstention"):
        show_case(k, sl["S3"][k])
    print(paint("  the sensor adapter declares its 5-minute samples autocorrelated, so the change monitor refuses instead of\n"
                "  raising false alarms; a zero-lag feedback loop was already rejected at graph admission (Phase 2)", "d"))

    section(5, "Independent acceptance against hidden truth (harness side only)")
    score = P6.score(sl)
    for row in score:
        print(f"  {paint('✓', 'g') if row['met'] else paint('✗', 'r')} {row['slice']}  {row['check']}")

    section("5b", "Learned normal level (CURE-style): learn on an older window, freeze, monitor what comes after")
    bs = P6.baseline_study()
    b = bs["baseline"]
    print(f"  learned on days 0–{bs['train_days'] - 1}: intercept + weekday effects, residual SD {b.residual_sd:.4f}, "
          f"lag-1 autocorrelation {b.lag1_autocorrelation:+.2f} (independence check passed: {not b.problems})")
    print(f"  conversion drops on day {bs['drop_day']} (truth). Weekly swings are larger than the drop.")
    print(f"    learned baseline        → alarm on day {bs['learned']['alarm_day']} "
          f"({bs['learned']['alarm_day'] - bs['drop_day']} days after the drop; none before it)")
    print(f"    naive frozen mean / SD  → {'alarm on day ' + str(bs['naive']['alarm_day']) if bs['naive']['alarm_day'] else 'no alarm at all'}"
          " (the weekly pattern inflates its SD and hides the drop)")
    t1 = P6.baseline_type_one(train_days=56, horizon=120, sims=300)
    print(f"  false alarms, 300 seasonal null series, 8-week training, 120 days monitored: learned {t1['learned_exact']:.3f} "
          f"(≤ 0.05) · plugging in the frozen fit {t1['plug_in']:.3f} (its error is a permanent offset)")
    print(paint("  refuses training shorter than 8 weeks, autocorrelated training residuals, or training that overlaps monitoring", "d"))

    section(6, "Load: 100 mixed tasks, 8 threads, one ledger, 4 tasks on a provider that hangs past its deadline")
    load = P6.load_profile(n_tasks=100, workers=8)
    print(f"  provisional (T0) revision  p50 {load['T0_ms']['p50']:>6} ms   p95 {load['T0_ms']['p95']:>6} ms   "
          f"p99 {load['T0_ms']['p99']:>6} ms   target p95 < 2,000 ms")
    print(f"  final revision             p50 {load['final_ms']['p50']:>6} ms   p95 {load['final_ms']['p95']:>6} ms   "
          f"p99 {load['final_ms']['p99']:>6} ms   target p95 < 10,000 ms")
    print(f"  outcomes {load['outcomes']} · errors {load['error_count']} · visible publications "
          f"{load['visible_publications']} (= 2 per task, no duplicates) · {load['throughput_tasks_per_s']} tasks/s · "
          f"peak RSS {load['peak_rss_mb']} MB")
    print(paint(f"  machine: {load['machine']['platform']}, {load['machine']['cpus']} CPUs, Python {load['machine']['python']}. "
                "Not covered here: 5k-node graph under the same load (Phase 2 profile), corrupted payloads, revisions under load.", "d"))

    out = PROJECT_ROOT / "docs" / "phase6"
    out.mkdir(parents=True, exist_ok=True)
    with (out / "results.csv").open("w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["slice", "case", "task_id", "outcome", "failure_class", "first_finding"])
        for s in ("S1", "S2", "S3"):
            for k, r in sl[s].items():
                w.writerow([s, k, r.state.task.task_id, r.state.outcome, r.state.failures[0].cls if r.state.failures else "",
                            r.state.findings[0].statement if r.state.findings else ""])
        for row in score:
            w.writerow([row["slice"], "acceptance", "", "MET" if row["met"] else "NOT_MET", "", row["check"]])
    (out / "load_profile.json").write_text(json.dumps(load, indent=2) + "\n")
    print(paint("  written: docs/phase6/results.csv, docs/phase6/load_profile.json", "d"))

    section(7, "Gates (run live from tests/test_phase6_slices.py)")
    from tests import test_phase6_slices as T
    ok_all = all(row["met"] for row in score)
    for cls, label in (("ThreeSlicesOneCore", "3 slices × success/error/abstention; no slice branch in the core"),
                       ("BaselineComparison", "dashboard rule raises a false crash; the core separates DQ from mix"),
                       ("LearnedBaselineCP030", "CP-030 learned frozen baseline: exact null, refusals, beats naive"),
                       ("LoadG16", "G16 100 concurrent tasks: tier targets, 0 errors, 0 duplicates")):
        res = unittest.TextTestRunner(stream=StringIO(), verbosity=0).run(unittest.defaultTestLoader.loadTestsFromTestCase(getattr(T, cls)))
        ok_all &= res.wasSuccessful()
        print(f"  {paint('✓', 'g') if res.wasSuccessful() else paint('✗', 'r')} {label:<72}{res.testsRun} test{'s' if res.testsRun != 1 else ''}")
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
