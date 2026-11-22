"""Phase 0 demo: frozen baseline, reference cases, v2.1 vs v3.2, harness truth.

    python -m scripts.phase0_demo              # full walkthrough
    python -m scripts.phase0_demo --case 08    # one case, full expectations
    python -m scripts.phase0_demo --no-color
"""
from __future__ import annotations

import argparse
import csv
import json
import shutil
import sys
import tempfile
from collections import Counter
from pathlib import Path

from evaluation import fixture_io as io
from evaluation.reference_cases import CASES

C = {"b": "\033[1m", "d": "\033[2m", "g": "\033[32m", "y": "\033[33m", "r": "\033[31m",
     "c": "\033[36m", "m": "\033[35m", "x": "\033[0m"}


def paint(text: str, *styles: str) -> str:
    return "".join(C[s] for s in styles) + text + C["x"] if C["x"] else text


def pp(x: float) -> str:
    return f"{x * 100:+.2f}pp"


def ci(v: list[float], fmt: str = "{:.2f}") -> str:
    return "[" + ", ".join(fmt.format(a) for a in v) + "]"


# ── one-line summaries per case (read only from the fixture) ───────────────


def _s01(f):
    m, d = f["expected"]["main"], f["expected"]["drifted_variant"]
    return [f"identity {m['verdict']} (max |resid| {m['max_abs_residual']:g}; 0/0 bucket → ZERO_DENOMINATOR, excluded)",
            f"drifted source → {d['verdict']}, edge {d['edge_status']} (resid {d['max_abs_residual']:.4f} > tol 1e-4)"]


def _s02(f):
    m, v = f["expected"]["main"], f["expected"]["variant_missing_segment"]
    return [f"total {pp(m['total_change'])} = mix {pp(m['mix_component'])} + rate {pp(m['rate_component'])}"
            f"   (every segment improved: {m['every_segment_rate_rose']})",
            f"unreported segment → {v['partition']}, {v['uncovered_share_max']:.1%} of population unexplained"]


def _s03(f):
    e, v = f["expected"], f["v21_reference"]
    b = e["dq"]["immature_buckets"][0]
    return [f"{b['bucket']} completeness {b['completeness']} → DQ_FAILURE; naive {b['naive_change_vs_mature_mean']:+.1%} 'drop' not attributed",
            f"replay @12:00 visible {e['replay_visible']} · v2.1 rule would leak {v['look_ahead_leak']}, unverifiable {v['unverifiable']}"]


def _s04(f):
    e = f["expected"]
    i = e["itt"]
    return [f"SRM χ²={e['srm_at_assignment_unit']['chi2']:.2f} p={e['srm_at_assignment_unit']['p_value']:.2f} · "
            f"ITT {pp(i['estimate'])} {ci([x * 100 for x in i['ci']])}pp, p={i['p_value']:.3f}",
            f"→ {e['graph_artifact']}; structural edge admitted: {e['structural_edge_admitted']}; no monitor involved"]


def _s05(f):
    e, n = f["expected"], f["failure_mode_if_unguarded"]
    c = e["cluster_analysis"]
    first = f"naive individual SE {n['se']:.3f} (p={n['p_value']:.3f}) → REFUSED"
    if c["support"] == "UNSUPPORTED":
        return [first, f"cluster inference {c['support']}: {c['support_reason']}"]
    return [first, f"cluster-level ({c['support']}) est {c['estimate']:.3f} SE {c['se']:.3f} df {c['df']} "
                   f"p={c['p_value']:.3f} · variance inflation ×{e['empirical_variance_inflation']:.1f}"]


def _s06(f):
    e = f["expected"]
    a, p = e["att"], e["pretrend_slope_difference"]
    return [f"ATT {a['estimate']:.2f} {ci(a['ci'])} (unit-collapsed, df {a['df']})",
            f"pre-trend {e['pretrend_diagnostic']} (p={p['p_value']:.2f}) — diagnostic, not proof → {e['claim_kind']}"]


def _s07(f):
    e = f["expected"]
    return [f"association slope {e['association']['slope']:.3f} ± {e['association']['se_slope']:.3f} → "
            f"{e['identification']['status']}, claim {e['claim_kind']}",
            f"evidence gaps: {'; '.join(e['evidence_gaps'])}"]


def _s08(f):
    e, v, u = f["expected"], f["v21_reference"]["first_seen_root_implementation"], f["failure_mode_if_unguarded"]
    return [f"log-wealth {e['log_wealth']:.3f} vs threshold {e['threshold']:.3f} → alarm {e['alarm']}  "
            f"({len(e['ignored'])} records ignored: 50 replicas + superseded b3r1)",
            f"first-seen-root rule keeps stale b3r1 → {v['log_wealth']:.2f} (alarm {v['alarm']}) · "
            f"unguarded → {u['log_wealth']:.2f} (alarm {u['alarm']})"]


def _s09(f):
    e = f["expected"]
    return [f"{len(e['receipts'])} byte receipts kept · observation → {e['observation']['payload_kind']}, "
            f"{e['observation']['scope']}, covariance {e['observation']['not_inferred']['covariance']}",
            f"old certified edge → {e['certified_edge']['status']} ({', '.join(e['certified_edge']['reasons'])})"]


def _s10(f):
    e, v = f["expected"], f["v21_reference"]
    return [f"{pid}: v2.1 {v[pid]['decision']:<5} → v3.2 {e[pid]['decision']} {e[pid]['reasons'] or ''}"
            for pid in e]


def _s11(f):
    e = f["expected"]
    w = e["wald_iv_cace"]
    return [f"ITT {e['itt']['estimate']:.2f} {ci(e['itt']['ci'])} · take-up {e['first_stage_takeup']['estimate']:.2f} · "
            f"as-treated {e['as_treated_comparison']['estimate']:.2f} REFUSED",
            f"CACE (separate IV claim) {w['estimate']:.2f} {ci(w['ci'])} · D→Y edge from ITT: "
            f"{e['claims']['edge_d_to_y_from_itt']['reason']}"]


SUMMARY = {"01": _s01, "02": _s02, "03": _s03, "04": _s04, "05": _s05, "06": _s06,
           "07": _s07, "08": _s08, "09": _s09, "10": _s10, "11": _s11}
BADGE = {"PRESERVED": ("PRESERVED", "g"), "CORRECTED": ("CORRECTED", "y"), "CLARIFIED": ("CLARIFIED", "c")}


# ── harness view: the only place truth meets estimates ─────────────────────


def _covers(interval, value) -> str:
    return paint("covers truth", "g") if interval[0] <= value <= interval[1] else paint("misses truth", "r")


def harness_lines(case_id: str, f: dict, truth: dict) -> list[str]:
    t, e = truth["true_values"], f["expected"]
    if case_id.startswith("05"):
        c, n = e["cluster_analysis"], f["failure_mode_if_unguarded"]
        out = [f"true effect {t['average_treatment_effect']}, true design effect {t['design_effect']:.1f}",
               f"naive CI {ci(n['ci'])} {_covers(n['ci'], t['average_treatment_effect'])}"]
        if "ci" in c:
            out.append(f"cluster CI {ci(c['ci'])} {_covers(c['ci'], t['average_treatment_effect'])}")
        return out
    if case_id.startswith("06"):
        return [f"true ATT {t['att']} · estimate {e['att']['estimate']:.2f} {ci(e['att']['ci'])} "
                f"{_covers(e['att']['ci'], t['att'])}"]
    if case_id.startswith("07"):
        gap = e["association"]["slope"] - t["causal_effect_x_on_y"]
        return [f"true effect {t['causal_effect_x_on_y']} · association {e['association']['slope']:.2f} "
                f"→ reading it causally overstates by {gap:+.2f}",
                f"engine verdict {e['identification']['status']}: " + paint("correct abstention", "g")]
    if case_id.startswith("11"):
        w, a = e["wald_iv_cace"], e["as_treated_comparison"]["estimate"]
        return [f"true ITT {t['itt_effect_of_assignment']} · estimate CI {ci(e['itt']['ci'])} "
                f"{_covers(e['itt']['ci'], t['itt_effect_of_assignment'])}",
                f"true CACE {t['complier_average_causal_effect']} · IV CI {ci(w['ci'])} "
                f"{_covers(w['ci'], t['complier_average_causal_effect'])} · as-treated {a:.2f} "
                f"would overstate by {a - t['complier_average_causal_effect']:+.2f}"]
    return []


# ── sections ───────────────────────────────────────────────────────────────


def section(n: int, title: str) -> None:
    print("\n" + paint(f"[{n}] {title}", "b", "c"))


def tamper_drill() -> str:
    with tempfile.TemporaryDirectory() as tmp:
        copy = Path(tmp) / "v21"
        shutil.copytree(io.FIXTURE_DIR, copy)
        target = copy / "02_simpson_kitagawa.json"
        target.write_text(target.read_text().replace('"mix_component": -0.024', '"mix_component": -0.023', 1))
        res = io.verify_manifest(copy)
    return (paint("✓ detected", "g") if res["changed"] == ["02_simpson_kitagawa.json"]
            else paint("✗ NOT detected", "r")) + f" (changed: {res['changed']})"


def run(case_filter: str | None) -> int:
    print(paint("CAUSAL CORE · PHASE 0 · frozen baseline", "b"))
    print(paint("Specification comparison only: no v2.1 implementation exists, so every expectation\n"
                "comes from evaluation/oracle.py (stdlib, independent of any future kernel).", "d"))

    section(1, "Integrity")
    base, man = io.verify_baseline_corpus(), io.verify_manifest()
    ok = lambda r: paint("✓", "g") if r["ok"] else paint("✗", "r")
    print(f"  {ok(base)} design corpus   {base['checked']} docs match docs/baseline/SHA256SUMS")
    print(f"  {ok(man)} fixture corpus  {man['checked']} files match tests/fixtures/v21/MANIFEST.sha256"
          + ("" if man["ok"] else f"  {man}"))
    print(f"  tamper drill: change one digit in a scratch copy of fixture 02 → {tamper_drill()}")

    section(2, "Reference cases — what v3.2 must conclude, and how it differs from v2.1")
    pending = 0
    for case in CASES:
        num = case.case_id[:2]
        if case_filter and num != case_filter:
            continue
        path = io.FIXTURE_DIR / f"{case.case_id}.json"
        if not path.exists():
            pending += 1
            print(f"  {num} {paint('PENDING  ', 'm')} {case.title}")
            print(paint("       awaiting cluster_inference_support() policy in evaluation/oracle.py → "
                        "then: python -m scripts.freeze_fixtures", "m"))
            continue
        fx = json.loads(path.read_text())
        label, colour = BADGE[fx["behavior"]["status"]]
        print(f"  {num} {paint(f'{label:<9}', colour)} {paint(case.title, 'b')}  "
              + paint(f"[{', '.join(case.gates)}]", "d"))
        for line in SUMMARY[num](fx):
            print(f"       {line}")
        if fx["behavior"]["status"] != "PRESERVED":
            print(paint(f"       v2.1: {fx['behavior']['v21']}  →  v3.2: {fx['behavior']['v32']}", "d"))
        if case_filter:
            print(json.dumps({k: fx[k] for k in fx if k in ("expected", "v21_reference",
                                                           "failure_mode_if_unguarded", "note")}, indent=2))

    section(3, "Evaluation harness view — truth lives in evaluation/truth/, never in fixtures or core")
    for case in CASES:
        tpath = io.TRUTH_DIR / f"{case.case_id}.json"
        fpath = io.FIXTURE_DIR / f"{case.case_id}.json"
        if (case_filter and case.case_id[:2] != case_filter) or not (tpath.exists() and fpath.exists()):
            continue
        print(f"  {case.case_id[:2]} {case.title}")
        for line in harness_lines(case.case_id, json.loads(fpath.read_text()), json.loads(tpath.read_text())):
            print(f"       {line}")

    if case_filter:
        return 0

    section(4, "Inventory")
    doc = io.PROJECT_ROOT / "docs" / "phase0"
    read = lambda name: csv.DictReader((doc / name).read_text().splitlines())
    classes = Counter(r["class"] for r in read("rule_classification.csv"))
    methods = Counter(r["phase"] for r in read("methods_preconditions.csv"))
    bench = json.loads((doc / "baseline_benchmarks.json").read_text())
    missing = [k for k, v in bench["measurements"].items() if v["status"] == "MISSING"]
    inv = (doc / "migration_inventory.md").read_text().splitlines()
    statuses = Counter(json.loads(p.read_text())["behavior"]["status"] for p in io.FIXTURE_DIR.glob("*.json"))
    print(f"  48 v2.1 rules     " + " · ".join(f"{k} {classes[k]}" for k in ("KEEP", "FIX", "SPECIALIZE", "DEFER")))
    print(f"  methods           P0 {methods['P0']} · P1 {methods['P1']} (shapes, checks, refusal codes)")
    print(f"  fixtures          " + " · ".join(f"{k} {v}" for k, v in sorted(statuses.items()))
          + (f" · PENDING {pending}" if pending else ""))
    print(f"  discrepancies     {sum(l.startswith('| **D') for l in inv)} found (D1–D8), incl. gate-ID collision v2.1↔v3.2")
    print(f"  benchmarks        {bench['machine']['cpu']}, {bench['machine']['ram_gib']} GiB, "
          f"Python {bench['python']['version']} · MISSING: {', '.join(missing)}")

    section(5, "Exit gate (docs/phase0/migration_inventory.md §7)")
    status = next((l for l in inv if l.startswith("**Status:**")), "**Status:** unknown")
    print("  " + paint(status.replace("**", ""), "g" if "APPROVED" in status else "y"))
    items: list[str] = []
    for line in inv:
        if line.startswith("- **A"):
            items.append(line[2:])
        elif items and line.startswith("  ") and line.strip():
            items[-1] += " " + line.strip()  # markdown continuation line
        elif items and not line.strip():
            break
    for item in items:
        print("  " + item.replace("**", "").replace("`", ""))
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--case", help="two-digit case number, e.g. 08")
    ap.add_argument("--no-color", action="store_true")
    args = ap.parse_args(argv)
    if args.no_color or not sys.stdout.isatty():
        for k in C:
            C[k] = ""
    return run(args.case)


if __name__ == "__main__":
    sys.exit(main())
