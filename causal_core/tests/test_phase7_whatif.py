"""Phase 7 (P1) gates: G07 predictions never evidence, G11 no mechanism invented, G17 out-of-regime abstention and
no action authority, G18 held-out calibration (seeds, family, regime) scored outside the core. Includes the
2026-11-22 fixes: shape sets + multi-step backtest (CP-034), 7-day baseline and 5% margin (CP-035), risk constraints
checked or refused (CP-036).

    python -m unittest tests.test_phase7_whatif -v
"""
from __future__ import annotations

import ast
import json
import unittest
from pathlib import Path

from causal_core.contracts.mechanisms import InterventionSpec, MechanismSet, MechanismSpec
from causal_core.counterfactual.mechanisms import validate_mechanism
from causal_core.counterfactual.rollouts import Caps, rollout
from evaluation import phase7_cases as P7
from emulator import dynamics_world as D

ROOT = Path(__file__).resolve().parents[1]
W = D.build(seed=3)
M1 = P7.register(W, shapes=False)          # single straight-line specs: engine mechanics
M = P7.register(W)                         # shape sets + backtest: the registered default
H = P7.history(W)
IV = (InterventionSpec(variable_id="spend", kind="set", value=130.0),)


def run(mech=M1, iv=IV, **kw):
    kw.setdefault("target_scope_kind", "emulator")
    return rollout(mech, "trials", H, 14, iv, n_samples=kw.pop("n_samples", 800), seed=kw.pop("seed", 3), **kw)


def edited(spec: MechanismSpec, **changes) -> MechanismSpec:
    body = spec.model_dump(mode="json")
    body.update(changes)
    return MechanismSpec.model_validate_json(json.dumps(body))


class MechanismContract(unittest.TestCase):
    def test_validated_needs_a_passing_holdout_and_shapes_must_agree(self):
        with self.assertRaises(Exception):
            edited(M1["visitors"], validation=None, status="VALIDATED")
        with self.assertRaises(Exception):
            edited(M1["visitors"], coefficients=[1.0])
        self.assertEqual({m.status for m in M1.values()}, {"VALIDATED"})
        with self.assertRaises(ValueError):                             # no peeking: holdout after the fit
            validate_mechanism(M1["visitors"], W.series, start=sorted(W.series["spend"])[5], end=sorted(W.series["spend"])[50])

    def test_shape_sets_hold_validated_members_with_weights(self):
        s = M["visitors"]
        self.assertIsInstance(s, MechanismSet)
        self.assertEqual([m.mechanism_id for m in s.members], ["m:visitors:linear", "m:visitors:curved"])
        self.assertAlmostEqual(sum(s.weights), 1.0)
        self.assertTrue(any(p.transform == "square" for p in s.members[1].parents))
        self.assertIsNotNone(M["trials"].multi_step)                    # backtested before registration
        self.assertGreaterEqual(M["trials"].multi_step.inflation, 1.0)
        body = s.model_dump(mode="json")
        body["weights"] = [0.9, 0.9]
        with self.assertRaises(Exception):
            MechanismSet.model_validate_json(json.dumps(body))


class NoInventedMechanismG11(unittest.TestCase):
    def test_every_unsupported_model_is_an_abstention_with_a_reason(self):
        cand = {**M1, "visitors": edited(M1["visitors"], validation=None, status="CANDIDATE")}
        self.assertEqual(run(cand).status, "UNSUPPORTED_MODEL")                         # not validated
        self.assertEqual(run({"trials": M1["trials"]}).status, "UNSUPPORTED_MODEL")       # no path spend → trials
        self.assertIn("unknown, not a zero effect", run({"trials": M1["trials"]}).reasons[0])
        self.assertEqual(run(target_scope_kind="production").status, "UNSUPPORTED_MODEL")   # simulator fit ≠ production
        self.assertEqual(run(caps=Caps(max_samples=100)).status, "CAP_EXCEEDED")
        v = M1["visitors"]
        loop = {**M1, "visitors": edited(v, parents=[{"variable_id": "spend", "lag_steps": 0}, {"variable_id": "trials", "lag_steps": 0}],
                                         coefficients=list(v.coefficients) + [0.1],
                                         covariance=[[1e-6 if i == j else 0.0 for j in range(3)] for i in range(3)],
                                         valid_regime=[r.model_dump(mode="json") for r in v.valid_regime]
                                         + [{"variable_id": "trials", "lag_steps": 0, "minimum": 0, "maximum": 999}])}
        self.assertIn("cycle", run(loop).reasons[0])


class RegimeAndActionsG17(unittest.TestCase):
    def test_out_of_regime_abstains_with_a_five_percent_margin(self):
        top = max(r.maximum for r in M1["visitors"].valid_regime)
        self.assertEqual(run(M, iv=(InterventionSpec(variable_id="spend", kind="set", value=top + 2),)).status, "PREDICTED")
        self.assertEqual(run(M, iv=(InterventionSpec(variable_id="spend", kind="set", value=300.0),)).status, "OUT_OF_REGIME")
        self.assertEqual(run(M, iv=(InterventionSpec(variable_id="spend", kind="set", value=top + 2),),
                             regime_margin=0.0).status, "OUT_OF_REGIME")
        o = P7.out_of_regime_study()
        self.assertTrue(all(x["status"] == "OUT_OF_REGIME" for x in o.values()))
        self.assertFalse(o["saturating"]["unguarded_covers_truth"])                    # extrapolation would mislead

    def test_actions_are_denied_and_predictions_are_labelled(self):
        co, _ = P7.harness(W, M, samples=800)
        r = co.run(P7.task("act", P7.Q_COMPARE), requested_action="set spend to 130")
        self.assertEqual(r.decisions[-1].verdict, "DENY")
        r2 = co.run(P7.task("lbl", P7.Q_COMPARE))
        self.assertIn("not evidence", r2.decisions[-1].reasons[0])


class BaselineAndConstraintsCP035CP036(unittest.TestCase):
    def test_no_change_means_the_seven_day_mean(self):
        p = run(M)
        self.assertTrue(any("7-day mean" in a for a in p.assumptions))
        last = run(M, exogenous_policy="last")
        self.assertNotEqual(p.effect.mean, last.effect.mean)

    def test_risk_constraints_are_checked_or_refused_never_ignored(self):
        co, _ = P7.harness(W, M, samples=800)
        r = co.run(P7.task("rc", {**P7.Q_COMPARE, "risk_constraints": ["visitors <= 1300"]}))
        self.assertTrue(any("fails a risk constraint" in f.statement for f in r.state.findings))
        bad = co.run(P7.task("rc-bad", {**P7.Q_COMPARE, "risk_constraints": ["keep it safe"]}))
        self.assertEqual((bad.state.outcome, bad.state.failures[0].cls), ("FAILED_VALIDATION", "ILL_DEFINED_TARGET"))
        off = co.run(P7.task("rc-off", {**P7.Q_COMPARE, "risk_constraints": ["churn <= 0.1"]}))
        self.assertIn("cannot be checked", off.state.findings[0].statement)


class PredictionsNeverEvidenceG07(unittest.TestCase):
    def test_predictions_go_to_model_outputs_only(self):
        co, ledger = P7.harness(W, M, samples=800)
        r = co.run(P7.task("g07", P7.Q_COMPARE))
        n_pred = sum(1 for m, _ in r.state.results if m.startswith("counterfactual_rollout_v1"))
        self.assertEqual(ledger.read("SELECT COUNT(*) FROM model_outputs")[0][0], n_pred)
        self.assertEqual(ledger.read("SELECT COUNT(*) FROM evidence")[0][0], 0)
        self.assertEqual(ledger.read("SELECT COUNT(*) FROM eprocess_state")[0][0], 0)          # no alarm wealth touched
        self.assertFalse(r.state.claims)                                                # never an EffectClaim
        self.assertIn("MODEL_PREDICTION", {f.claim_kind for f in r.state.findings})


class CommonRandomNumbers(unittest.TestCase):
    def test_keyed_draws_are_reproducible_stable_and_pair_the_worlds(self):
        a, b = run(M), run(M)
        self.assertEqual(a.effect, b.effect)
        extra = {**M, "newsletter": edited(M1["visitors"], mechanism_id="m:newsletter", target="newsletter")}
        self.assertEqual(run(extra).effect, a.effect)                                  # an unrelated node shifts nothing
        self.assertLess(P7.crn_study()["ratio"], 0.5)


class HeldOutCalibrationG18(unittest.TestCase):
    def test_shape_sets_fix_the_held_out_family_under_coverage(self):
        fixed = P7.calibration_study(seeds=range(100, 125), levels=(0.9,), samples=600)
        line = P7.calibration_study(seeds=range(100, 125), levels=(0.9,), samples=600, shapes=False)
        for fam in ("linear", "saturating"):
            self.assertGreaterEqual(fixed[fam]["coverage"]["0.9"], 0.85, fam)
        self.assertGreaterEqual(fixed["saturating"]["coverage"]["0.9"], line["saturating"]["coverage"]["0.9"])


class PlannerWhatIf(unittest.TestCase):
    def test_utility_and_candidates_are_parsed_never_invented(self):
        co, _ = P7.harness(W, M, samples=800)
        bad = {**P7.Q_COMPARE, "utility": "make it better"}
        r = co.run(P7.task("bad", bad))
        self.assertEqual((r.state.outcome, r.state.failures[0].cls), ("FAILED_VALIDATION", "ILL_DEFINED_TARGET"))
        r2 = co.run(P7.task("fc", P7.Q_FORECAST, "scheduled"))
        self.assertEqual(r2.state.outcome, "COMPLETED")


class PurityPhase7(unittest.TestCase):
    def test_counterfactual_package_is_pure(self):
        for f in (ROOT / "causal_core/counterfactual").glob("*.py"):
            for node in ast.walk(ast.parse(f.read_text())):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                    for n in names:
                        self.assertNotIn(n.split(".")[0], {"emulator", "evaluation", "adapters", "scripts"}, f.name)


if __name__ == "__main__":
    unittest.main()
