"""Phase 7 demo (P1, user-authorized 2026-11-22): sparse what-if prediction with validated mechanisms.

    python -m scripts.phase7_demo
    python -m scripts.phase7_demo --no-color
"""
from __future__ import annotations

import argparse
import json
import sys
import unittest
from io import StringIO

from causal_core.contracts.mechanisms import InterventionSpec
from causal_core.counterfactual.rollouts import Caps, rollout
from emulator import dynamics_world as D
from evaluation import phase7_cases as P7
from evaluation.fixture_io import PROJECT_ROOT

C = {"b": "\033[1m", "d": "\033[2m", "g": "\033[32m", "y": "\033[33m", "r": "\033[31m", "c": "\033[36m", "x": "\033[0m"}


def paint(t, *s):
    return "".join(C[x] for x in s) + t + C["x"] if C["x"] else t


def section(n, title):
    print("\n" + paint(f"[{n}] {title}", "b", "c"))


def bar(x, width=30):
    return "█" * int(round(x * width))


def run() -> int:
    print(paint("CAUSAL CORE · PHASE 7 (P1) · sparse what-if prediction", "b"))
    print(paint("Predictions from validated mechanisms, with intervals and abstentions. Never evidence; never permission to act.", "d"))
    w = D.build(seed=3, family="linear")
    mech = P7.register(w)

    section(1, "Mechanisms: fitted on days 1–119, validated on days 120–199 (held out); straight and curved shapes")
    for t, ms in mech.items():
        for m, wt in zip(ms.members, ms.weights):
            v = m.validation
            terms = " + ".join(f"{c:.3g}·{p.variable_id}" + ("²" if p.transform == "square" else "")
                               + (f"[t-{p.lag_steps}]" if p.lag_steps else "") for c, p in zip(m.coefficients[1:], m.parents))
            print(f"  {m.mechanism_id} (weight {wt:.2f}): {t} = {m.coefficients[0]:.3g} + {terms}")
            print(paint(f"      {m.status}: one-step holdout coverage {v.coverage:.2f} at {v.interval_level:.0%} (n {v.n})", "d"))
        if ms.multi_step:
            d = ms.multi_step
            print(paint(f"  14-day backtest of everything feeding {t}: {d.origins} origins, coverage {d.coverage:.2f} at "
                        f"{d.interval_level:.0%}, widening ×{d.inflation:.2f} ({d.verdict})", "d"))
    print(paint("  truth (harness only): visitors = 200 + 8·spend + N(0, 40²); trials = 0.05·visitors + 0.3·trials[t-1] + N(0, 5²)", "d"))

    section(2, "Ask the core: 'which spend level maximizes trials over 14 days, keeping visitors ≤ 1,400?'")
    co, ledger = P7.harness(w, mech)
    q = {**P7.Q_COMPARE, "candidates": ["spend=130", "spend=150", "spend=300"], "risk_constraints": ["visitors <= 1400"]}
    r = co.run(P7.task("whatif-1", q))
    for stage, verdict, note in r.state.trace:
        if stage == "EVALUATE":
            print(paint(f"  {stage:<12} {verdict:<7} {note[:118]}", "d"))
    for f in r.state.findings:
        col = "g" if f.claim_kind == "MODEL_PREDICTION" else "y"
        print(f"  {paint(f.claim_kind, col)}: {f.statement[:190]}")
    for label, v in (("spend=130", 130.0), ("spend=150", 150.0)):
        print(paint(f"    truth for {label} (vs spend held at its 7-day mean): {D.true_effect(w, v, 14)['effect']:+.1f}", "d"))
    print(f"  policy: {r.decisions[-1].verdict} ({r.decisions[-1].reasons[0]})")
    bad = co.run(P7.task("whatif-bad", {**q, "risk_constraints": ["keep it safe"]}))
    print(f"  a constraint it cannot read ('keep it safe') → {paint(bad.state.outcome, 'r')}: {bad.state.failures[0].detail}")
    fc = co.run(P7.task("whatif-forecast", P7.Q_FORECAST, "scheduled"))
    print(f"  forecast (PREDICT_OUTCOME): {fc.state.findings[0].statement}")

    section(3, "Why it abstains instead of extrapolating: spend = 300, twice the highest spend it was fitted on")
    o = P7.out_of_regime_study()
    for fam, x in o.items():
        print(f"  {fam:<11} engine: {paint(x['status'], 'y')}   truth {x['truth']:+7.0f}   straight-line extrapolation "
              f"{x['unguarded_prediction']:+6.0f} [{x['unguarded_interval'][0]:+.0f}, {x['unguarded_interval'][1]:+.0f}] "
              + (paint("covers", "g") if x["unguarded_covers_truth"] else paint("MISSES", "r"))
              + f" · with shape averaging [{x['unguarded_shapes_interval'][0]:+.0f}, {x['unguarded_shapes_interval'][1]:+.0f}]")
    print(paint("  outside its data the curved shape runs wild: averaging admits the doubt with a huge interval; abstaining is honest", "d"))
    m1 = P7.register(w, shapes=False)
    cases = (("mechanism not validated", {**m1, "visitors": m1["visitors"].model_copy(update={"status": "CANDIDATE"})}, {}),
             ("no mechanism path from spend", {"trials": m1["trials"]}, {}),
             ("simulator-fitted, asked for production", mech, {"target_scope_kind": "production"}),
             ("sample cap", mech, {"caps": Caps(max_samples=500)}))
    for label, mm, kw in cases:
        kw.setdefault("target_scope_kind", "emulator")
        p = rollout(mm, "trials", P7.history(w), 14, (InterventionSpec(variable_id="spend", kind="set", value=130.0),),
                    n_samples=1000, seed=3, **kw)
        print(f"  {label:<40} → {paint(p.status, 'y')}: {p.reasons[0][:96]}")

    section(4, "Common random numbers: both worlds share keyed draws")
    crn = P7.crn_study()
    print(f"  paired effect interval width {crn['paired_width']:.1f} vs ~{crn['independent_width_approx']:.1f} with independent "
          f"draws ({crn['ratio']:.0%}); draws are keyed by seed + node + version + step, so adding an unrelated node changes nothing")

    section(5, "Held-out calibration: does a stated X% interval contain the true effect X% of the time?")
    cal = P7.calibration_study()
    before = P7.calibration_study(shapes=False)
    for fam, c in cal.items():
        tag = "same family as the straight line" if fam == "linear" else "curved truth (held-out family)"
        print(f"  {fam} worlds ({tag}): {c['predicted']} predicted, {c['abstained']} abstained")
        for lv, cov in c["coverage"].items():
            old = before[fam]["coverage"][lv]
            print(f"    nominal {float(lv):>4.0%}  now {cov:>5.0%} {bar(cov):<30}  straight line only {old:>5.0%}")
    print(paint("  shape averaging removes the under-coverage on curved worlds; the price is caution: intervals are wider than\n"
                "  needed on straight-line worlds (a stated 50% covers ~80%)", "d"))
    out = PROJECT_ROOT / "docs" / "phase7"
    out.mkdir(parents=True, exist_ok=True)
    (out / "calibration.json").write_text(json.dumps({"calibration": cal, "straight_line_only": before, "out_of_regime": o,
                                                      "crn": crn}, indent=2) + "\n")
    print(paint("  written: docs/phase7/calibration.json", "d"))

    section(6, "Firewall: predictions are model outputs, not evidence")
    print(f"  model_outputs rows {ledger.read('SELECT COUNT(*) FROM model_outputs')[0][0]} · evidence rows "
          f"{ledger.read('SELECT COUNT(*) FROM evidence')[0][0]} · alarm state rows {ledger.read('SELECT COUNT(*) FROM eprocess_state')[0][0]} · "
          f"effect claims {len(r.state.claims)}")
    act = co.run(P7.task("whatif-act", q), requested_action="set spend to 140 tomorrow")
    print(f"  'set spend to 140 tomorrow' → {paint(act.decisions[-1].verdict, 'r')}")

    section(7, "Gates (run live from tests/test_phase7_whatif.py)")
    from tests import test_phase7_whatif as T
    ok_all = True
    for cls, label in (("MechanismContract", "mechanism contract: validated needs a passing holdout"),
                       ("NoInventedMechanismG11", "G11 no invented equations; unvalidated, no path, prod, caps, cycles"),
                       ("RegimeAndActionsG17", "G17 out-of-regime abstains (5% margin); actions denied"),
                       ("BaselineAndConstraintsCP035CP036", "7-day baseline; risk constraints checked or refused"),
                       ("PredictionsNeverEvidenceG07", "G07 predictions only in model_outputs"),
                       ("CommonRandomNumbers", "common random numbers: reproducible, stable, paired"),
                       ("HeldOutCalibrationG18", "G18 shape sets fix held-out family under-coverage"),
                       ("PlannerWhatIf", "planner: utility/candidates parsed, never invented"),
                       ("PurityPhase7", "counterfactual package is pure")):
        res = unittest.TextTestRunner(stream=StringIO(), verbosity=0).run(unittest.defaultTestLoader.loadTestsFromTestCase(getattr(T, cls)))
        ok_all &= res.wasSuccessful()
        print(f"  {paint('✓', 'g') if res.wasSuccessful() else paint('✗', 'r')} {label:<68}{res.testsRun} test{'s' if res.testsRun != 1 else ''}")
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
