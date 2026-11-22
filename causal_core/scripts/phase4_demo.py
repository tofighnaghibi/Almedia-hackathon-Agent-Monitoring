"""Phase 4 demo: capability registry, pure methods, identification auditor, independent statistics.

    python -m scripts.phase4_demo
    python -m scripts.phase4_demo --no-color
"""
from __future__ import annotations

import argparse
import sys
import unittest
from io import StringIO

import json

import numpy as np

from causal_core.analytics import identification as ia
from causal_core.analytics.catalog import REGISTRY
from causal_core.analytics.experiments import evaluate_randomized
from causal_core.analytics.quasi_experimental import evaluate_did
from causal_core.analytics.registry import MethodInput
from causal_core.analytics.sequential import evaluate_eprocess, type_one_error
from evaluation import fixture_io as io
from evaluation.phase4_cases import adversarial_suite, assignment, did_design, differential, estimand, panel, rct_design

C = {"b": "\033[1m", "d": "\033[2m", "g": "\033[32m", "y": "\033[33m", "r": "\033[31m", "c": "\033[36m", "x": "\033[0m"}


def paint(t, *s):
    return "".join(C[x] for x in s) + t + C["x"] if C["x"] else t


def section(n, title):
    print("\n" + paint(f"[{n}] {title}", "b", "c"))


def run() -> int:
    print(paint("CAUSAL CORE · PHASE 4 · capability registry, methods, identification", "b"))
    print(paint("Methods refuse when a premise fails; the auditor scopes every effect; truth is scored only by the harness.", "d"))

    section(0, "Success · refusal · abstention")
    fx = json.loads((io.FIXTURE_DIR / "04_valid_rct.json").read_text())
    a_, c_ = fx["input"]["assigned"], fx["input"]["converted"]
    units = [{"unit_id": f"{arm}{i}", "arm": arm, "outcome": 1.0 if i < c_[arm] else 0.0}
             for arm in ("treatment", "control") for i in range(a_[arm])]
    r = evaluate_randomized(rct_design(), assignment(units), "treatment", "control")
    a = ia.assess(estimand("ITT"), "randomized", r, evidence_ids=("ev:04",))
    claim = ia.effect_claim(a, r, "units independent")
    print(f"  {paint('SUCCESS', 'g')}    RCT fixture 04, ITT: {claim.estimate:+.4f} "
          f"[{claim.uncertainty.interval[0]:+.4f}, {claim.uncertainty.interval[1]:+.4f}] → {type(claim).__name__} "
          f"status {claim.assessment_status} (an artifact, not an edge)")
    rng = np.random.default_rng(1)
    z = [0.0]
    for _ in range(199):
        z.append(0.8 * z[-1] + float(rng.normal()))
    e = evaluate_eprocess(z, null_spec="N(0,1)", dependence="ar1", alpha=0.05)
    print(f"  {paint('REFUSAL', 'r')}    e-process on an AR(1) stream → {e.refusal_code}: {e.refusal_reason[:80]}")
    fx6, rows = io.load_fixture("06_conditional_did")
    d = evaluate_did(did_design(), panel([{"unit": x["unit"], "period": x["period"], "value": x["y"]} for x in rows]),
                     {x["unit"]: x["group"] for x in rows}, "treated", "control")
    ad = ia.assess(estimand("ATE"), "quasi_experimental", d, evidence_ids=("ev:06",))
    print(f"  {paint('ABSTENTION', 'y')} DiD estimate {d.values['estimate']:.3f} exists, but the question asked for the ATE → "
          f"{ad.status}; effect_claim = {ia.effect_claim(ad, d, 'x')}")
    print(paint(f"             blocker: {ad.assumptions_checked[-1]}", "d"))

    section(1, "Refusal trace: the registry checks shape, metadata, estimand and budget before any method runs")
    for label, inp in (("event batch, 'explain this shift'", MethodInput("EXPLAIN_OBSERVATION", ("event_batch",), frozenset({"strata", "ratio_metric"}))),
                       ("RCT data, asks for ATE of exposure", MethodInput("ESTIMATE_EFFECT", ("assignment_outcomes",), frozenset({"study_design"}), "ATE", source_regime="randomized")),
                       ("observational data, RCT method, ITT", MethodInput("ESTIMATE_EFFECT", ("assignment_outcomes",), frozenset({"study_design"}), "ITT", source_regime="observational")),
                       ("RCT data, asks for ITT", MethodInput("ESTIMATE_EFFECT", ("assignment_outcomes",), frozenset({"study_design"}), "ITT", work_units=40_000, source_regime="randomized")),
                       ("time series, no declared null", MethodInput("DETECT_CHANGE", ("time_series",), frozenset({"alpha", "season_period"})))):
        ok, trace = REGISTRY.select(inp)
        print(f"  {label:<38} → applicable: {paint(', '.join(s.id for s in ok) or 'none', 'g')}")
        order = ("randomized_effect_v1", "savi_eprocess_v1", "kitagawa_v1")
        shown = sorted((a for a in trace if a.method_id in order), key=lambda a: order.index(a.method_id))
        shown = [a for a in shown if a.method_id == "randomized_effect_v1"
                 or not any(r.startswith("INPUT_SHAPE") for r in a.reasons)][:2] or shown[-1:]
        for a in shown:
            if a.status == "UNSUPPORTED":
                why = next((r for r in a.reasons if not r.startswith("QUESTION_KIND")), a.reasons[0])
                print(paint(f"       refused {a.method_id}: {why[:96]}", "d"))
                if a.next_queries:
                    print(paint(f"         next query: {a.next_queries[0]}", "d"))
            else:
                print(paint(f"       {a.method_id}: {a.status}, forecast {a.forecast_work_units:,} of "
                            f"{a.work_unit_cap:,} work units", "d"))
                print(paint(f"         ceiling: {a.scope_limitations[0]}", "d"))

    section(2, "Production kernels (NumPy/SciPy) vs the Phase 0 stdlib oracle")
    rows = differential()
    for r in rows:
        mark = paint("✓", "g") if r["abs_diff"] <= (1e-6 if r["quantity"] == "itt_se" else 1e-9) else paint("✗", "r")
        print(f"  {mark} fixture {r['fixture']} {r['quantity']:<16} prod {r['production']:>14.9f}  oracle {r['oracle']:>14.9f}  Δ {r['abs_diff']:.1e}")

    section(3, "Sequential Type-I error: 1,000 null streams × 200 peeks, α = 0.05")
    for rho in (0.0, 0.3, 0.5, 0.8):
        rate = type_one_error(1000, 200, rho=rho)
        bar = "█" * min(46, int(rate * 100)) + ("…" if rate * 100 > 46 else "")
        verdict = paint("within α", "g") if rate <= 0.05 else paint("INFLATED → method refuses dependent streams", "r")
        print(f"  AR(1) ρ={rho:<4} {rate:5.3f} {bar:<46} {verdict}")
    print(paint("  α line at 0.050 = █████", "d"))

    section(4, "Adversarial suite: what the system says vs what is true (truth read by the harness only)")
    suite = adversarial_suite()
    for a in suite:
        mark = paint("✓", "g") if a["expectation_met"] else paint("✗", "r")
        obs = ", ".join(f"{k}={v}" for k, v in a["observed"].items() if k != "limitations")
        cover = "" if a["ci_covers_truth"] is None else (f" · CI covers truth: {a['ci_covers_truth']}")
        print(f"  {mark} {a['scenario']:<26} truth {a['truth']!s:<6} {obs}{cover}")
        print(paint(f"       expected: {a['expected']}", "d"))
    fragile = next(a for a in suite if a["scenario"] == "pretrend_ok_but_violated")["observed"]["trend_sensitivity_fragile"]
    print(paint(f"  trend sensitivity on the pre-trend case: fragile = {fragile}. The violation starts at the intervention,\n"
                "  so extrapolating the pre-trend cannot see it: parallel trends stays listed as untestable.", "d"))
    print(paint("  a missed truth is acceptable only where the system flags, downgrades, lists the untestable\n"
                "  assumption or refuses: the failure mode would be a confident certified claim", "d"))

    section(5, "Gates (run live from tests/test_phase4_methods.py)")
    from tests import test_phase4_methods as t
    ok_all = True
    for cls, label in (("DifferentialVsPhase0Oracle", "independent oracle: 17 quantities agree"),
                       ("CapabilityNeutralityG10", "G10 refusals by shape/metadata/estimand/regime/budget; trace kept"),
                       ("SequentialG08", "G08 Type-I ≤ α (iid); dependence refused"),
                       ("DecompositionG09", "G09 Kitagawa partition, zero denominators; RCD-lite candidate only"),
                       ("IdentificationG11", "G11 ITT≠exposure, DiD ATT≠ATE, confounding, transport, trends"),
                       ("StudyValidityG12", "G12 SRM, cluster integrity, missing covariance"),
                       ("EvidenceAcquisitionG14", "G14 proxy ranking, novelty, unknown cost, privacy, gaps"),
                       ("EstimandComparisonC02", "C02 no blanket RCT override across estimands"),
                       ("AdversarialSuiteScoredWithTruth", "adversarial suite: 6/6 pre-registered expectations"),
                       ("DegenerateInputsTier1", "T1-P4-01 zero variance refuses; zero estimate keeps finite SE"),
                       ("UserDecisions20261122", "your 2026-11-22 decisions: SRM tiers, bet, transport, VoI"),
                       ("PurityG01Phase4", "G01 analytics pure")):
        res = unittest.TextTestRunner(stream=StringIO(), verbosity=0).run(unittest.defaultTestLoader.loadTestsFromTestCase(getattr(t, cls)))
        ok_all &= res.wasSuccessful()
        print(f"  {paint('✓', 'g') if res.wasSuccessful() else paint('✗', 'r')} {label:<68}{res.testsRun} test{'s' if res.testsRun != 1 else ''}")
    decisions()
    return 0 if ok_all else 1


def decisions() -> None:
    import math
    from causal_core.analytics.sequential import log_wealth_path
    from causal_core.analytics.voi import QueryOption, rank
    from evaluation.phase4_cases import srm_calibration_study

    section(6, "Your decisions (2026-11-22), live")
    print(paint("  SRM: warn from p < 0.01, always block below 0.001, warning threshold learned from history", "b"))

    def rct(n_t, n_c):
        return assignment([{"unit_id": f"t{i}", "arm": "treatment", "outcome": float(i % 3 == 0)} for i in range(n_t)] +
                          [{"unit_id": f"c{i}", "arm": "control", "outcome": float(i % 4 == 0)} for i in range(n_c)])
    for n_t, n_c in ((1000, 1000), (1060, 940), (1100, 900)):
        r = evaluate_randomized(rct_design(), rct(n_t, n_c), "treatment", "control")
        st = ia.assess(estimand("ITT"), "randomized", r, evidence_ids=("e",)).status
        p = r.values.get("srm_p_value") if r.status != "REFUSED" else float(r.refusal_reason.split("p = ")[1].split()[0])
        print(f"    split {n_t}/{n_c}  SRM p = {p:<9.3g} → {st}")
    print(paint("    learning the warning threshold on simulated platform history (truth scored by the harness):", "d"))
    study = srm_calibration_study()
    for label, v in study.items():
        tf = "–" if v["true_false_warning_share"] is None else f"{v['true_false_warning_share']:.0%}"
        print(f"    {label:<50} learned {v['warn_alpha']:.4f}  warned {v['warned']:>3} (false {tf:>4})  blocked {v['blocked']:>3}")
    mixed = next(v for k, v in study.items() if k.startswith("mixed"))
    print(paint(f"    on the mixed history, keeping 0.01 would make {mixed['false_share_at_0.01']:.0%} of warnings false alarms; "
                f"learning narrows\n    the band to {mixed['warn_alpha']:.4f} (target ≤ 20% false; small bands are noisy, "
                f"actual {mixed['true_false_warning_share']:.0%}).", "d"))

    print(paint("\n  Sequential monitor: regularized bet, starts at 0.25, prior worth 100 observations, two-sided", "b"))
    rng = np.random.default_rng(7)
    base = rng.standard_normal((1000, 1000))
    thr = math.log(20)

    def rate(shift, peeks, **bet):
        return float((log_wealth_path(base[:, :peeks] + shift, **bet).max(axis=1) >= thr).mean())
    old = dict(bet="fixed", sides="up", lam=0.5)
    print(f"    {'scenario':<34}{'old (fixed 0.5, up only)':>26}{'new':>8}")
    for label, shift, peeks in (("no change (false alarms)", 0.0, 200), ("drop of 0.25 SD", -0.25, 200),
                                ("rise of 0.10 SD, 200 peeks", 0.1, 200), ("rise of 0.25 SD, 200 peeks", 0.25, 200),
                                ("rise of 0.10 SD, 1,000 peeks", 0.1, 1000)):
        print(f"    {label:<34}{rate(shift, peeks, **old):>26.3f}{rate(shift, peeks):>8.3f}")

    print(paint("\n  Transport: CONDITIONAL when transport assumptions are declared", "b"))
    r = evaluate_randomized(rct_design(), rct(1000, 1000), "treatment", "control")
    for label, e, ta in (("DE web → US web, no assumptions", estimand("ITT", source="de_web", target="us_web",
                                                                        source_kind="production", target_kind="production"), ()),
                         ("DE web → US web, 1 assumption declared", estimand("ITT", source="de_web", target="us_web",
                                                                              source_kind="production", target_kind="production"),
                          ("learner mix in the US matches DE on the effect modifiers",)),
                         ("simulator → production, assumption declared", estimand("ITT", source="sim", target="prod",
                                                                                   source_kind="emulator", target_kind="production"),
                          ("the simulator is faithful",))):
        a = ia.assess(e, "randomized", r, evidence_ids=("e",), transport_assumptions=ta)
        print(f"    {label:<46} → {a.status}")
    print(paint("    simulator results are never promoted by assumption alone (frozen spec §4.5.5, I11)", "d"))

    print(paint("\n  Evidence ranking: unknown cost gets a geometric prior learned from known costs", "b"))
    pair = frozenset({frozenset({"mechanism", "artifact"})})
    opts = [QueryOption("dashboard_read", pair, 1.0, frozenset({"r1"}), True, latency_ms=500, cost_units=1.0),
            QueryOption("warehouse_query", pair, 1.0, frozenset({"r2"}), True, latency_ms=4000, cost_units=6.0),
            QueryOption("vendor_api (cost unknown)", pair, 1.0, frozenset({"r3"}), True, latency_ms=800)]
    for oid, score, notes in rank(opts, ["mechanism", "artifact"], frozenset()).ranked:
        print(f"    {oid:<28} score {score:.3f}  " + paint("; ".join(n for n in notes if "UNKNOWN" in n or "downweight" in n), "d"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--no-color", action="store_true")
    if ap.parse_args(argv).no_color or not sys.stdout.isatty():
        for k in C:
            C[k] = ""
    return run()


if __name__ == "__main__":
    sys.exit(main())
