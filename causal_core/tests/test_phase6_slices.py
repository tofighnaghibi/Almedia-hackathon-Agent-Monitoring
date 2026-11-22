"""Phase 6 gates: three slices on one core (G13 generality), truth-scored acceptance, baseline comparison, load (G16).

    python -m unittest tests.test_phase6_slices -v
"""
from __future__ import annotations

import ast
import unittest
from pathlib import Path

from evaluation import phase6_cases as P6

ROOT = Path(__file__).resolve().parents[1]
_SLICES = None


def slices():
    global _SLICES
    if _SLICES is None:
        _SLICES = P6.run_slices()
    return _SLICES


class ThreeSlicesOneCore(unittest.TestCase):
    def test_each_slice_records_a_success_an_error_and_an_abstention(self):
        expect = {"S1": ("COMPLETED", "FAILED_VALIDATION", "WAITING_FOR_EVIDENCE"),
                  "S2": ("COMPLETED", "INCONCLUSIVE", "COMPLETED"),
                  "S3": ("COMPLETED", "INCONCLUSIVE", "COMPLETED")}
        for sl, (ok, err, abst) in expect.items():
            with self.subTest(slice=sl):
                r = slices()[sl]
                self.assertEqual(r["success"].state.outcome, ok)
                self.assertEqual(r["error"].state.outcome, err)
                self.assertTrue(r["error"].state.failures)
                self.assertEqual(r["abstention"].state.outcome, abst)

    def test_every_independent_acceptance_check_is_met(self):
        for row in P6.score(slices()):
            with self.subTest(slice=row["slice"], check=row["check"]):
                self.assertTrue(row["met"])

    def test_s2_needs_no_monitor_and_s3_needs_no_metric_fields(self):
        s2 = slices()["S2"]["success"]
        self.assertFalse(any(m == "savi_eprocess_v1" for m, _ in s2.state.results))
        s3 = slices()["S3"]
        for r in s3.values():
            for _, res in r.state.results:
                for f in ("numerator", "denominator", "formula_ast", "experiment"):
                    self.assertNotIn(f, str(getattr(res, "values", res)))

    def test_no_slice_branch_inside_the_core(self):
        """Plan §H task 1: adapters differ; planner/coordinator/policy have no slice or domain names."""
        for name in ("planner.py", "coordinator.py", "policy.py"):
            tree = ast.parse((ROOT / "causal_core/orchestration" / name).read_text())
            docs = {id(n.body[0].value) for n in ast.walk(tree)
                    if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body
                    and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
            literals = {n.value.lower() for n in ast.walk(tree)
                        if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docs}
            text = " ".join(literals)
            for banned in ("marketplace", "coldchain", "freezer", "checkout", "country", "sensor", "conversion"):
                self.assertNotIn(banned, text, f"{name}: {banned}")
            self.assertFalse(literals & {"s1", "s2", "s3"}, name)


class BaselineComparison(unittest.TestCase):
    def test_fixed_dashboard_rule_raises_a_false_crash_the_core_does_not(self):
        b = P6.s1_baseline(slices()["_worlds"]["S1"])
        self.assertTrue(b["alert"])                                   # the dashboard says "conversion crashed"
        s1 = slices()["S1"]["success"].state
        kinds = {f.claim_kind for f in s1.findings}
        self.assertIn("DQ_FAILURE", kinds)                            # the core says: that day is not loaded yet
        self.assertIn("MATHEMATICAL_IDENTITY", kinds)                 # and the week-over-week drop is mix


class LearnedBaselineCP030(unittest.TestCase):
    """CURE-style learned, frozen baseline (user decision 2026-11-22)."""

    def test_incremental_formula_matches_a_dense_independent_reference(self):
        import math
        from datetime import datetime, timedelta, timezone
        import numpy as np
        from causal_core.analytics.baseline import learn_baseline, mixture_log_wealth
        rng = np.random.default_rng(1)
        t0 = datetime(2026, 1, 5, tzinfo=timezone.utc)
        ts = [t0 + timedelta(days=i) for i in range(90)]
        y = [float(P6.WEEKLY[t.weekday()] + rng.standard_normal()) for t in ts]
        b = learn_baseline(list(zip(ts[:60], y[:60])))
        prod = mixture_log_wealth(b, ts[60:], y[60:], 0.5)

        def X(tt):                                                     # written out independently
            return np.array([[1.0] + [1.0 if t.weekday() == k else 0.0 for k in range(1, 7)] for t in tt])
        Xt, Xm = X(ts[:60]), X(ts[60:])
        beta = np.linalg.lstsq(Xt, np.array(y[:60]), rcond=None)[0]
        res = np.array(y[:60]) - Xt @ beta
        s2 = res @ res / (60 - 7)
        r = np.array(y[60:]) - Xm @ beta
        for t in (1, 10, 30):
            Sig = s2 * (np.eye(t) + Xm[:t] @ np.linalg.inv(Xt.T @ Xt) @ Xm[:t].T)
            one = np.ones(t)
            u, v = one @ np.linalg.solve(Sig, r[:t]), one @ np.linalg.solve(Sig, one)
            t2 = 0.25 * s2
            ref = -0.5 * math.log(1 + t2 * v) + t2 * u * u / (2 * (1 + t2 * v))
            self.assertAlmostEqual(prod[t - 1], ref, places=9)

    def test_too_short_or_autocorrelated_training_is_refused(self):
        from datetime import datetime, timedelta, timezone
        import numpy as np
        from causal_core.analytics.baseline import evaluate_baseline_shift, learn_baseline
        t0 = datetime(2026, 1, 5, tzinfo=timezone.utc)
        ts = [t0 + timedelta(days=i) for i in range(160)]
        rng = np.random.default_rng(2)
        short = learn_baseline([(t, float(rng.standard_normal())) for t in ts[:28]])
        self.assertEqual(evaluate_baseline_shift(short, [(ts[40], 0.0)], alpha=0.05).refusal_code, "BASELINE_TOO_SHORT")
        e, ar = 0.0, []
        for t in ts[:84]:
            e = 0.8 * e + float(rng.standard_normal())
            ar.append((t, e))
        self.assertEqual(evaluate_baseline_shift(learn_baseline(ar), [(ts[100], 0.0)], alpha=0.05).refusal_code,
                         "BASELINE_AUTOCORRELATED")
        ok = learn_baseline([(t, float(rng.standard_normal())) for t in ts[:84]])
        self.assertEqual(evaluate_baseline_shift(ok, [(ts[10], 0.0)], alpha=0.05).refusal_code, "BASELINE_OVERLAPS_MONITORING")

    def test_false_alarms_within_alpha_where_the_plug_in_fails(self):
        r = P6.baseline_type_one(train_days=56, horizon=120, sims=300)
        self.assertLessEqual(r["learned_exact"], 0.05)
        self.assertGreater(r["plug_in"], 0.05)                          # documented: plugging in the frozen fit is invalid

    def test_learned_baseline_finds_the_drop_the_naive_baseline_misses(self):
        s = P6.baseline_study()
        self.assertFalse(s["learned_alarm_before_drop"])
        self.assertIsNotNone(s["learned"]["alarm_day"])
        self.assertTrue(s["naive"]["alarm_day"] is None or s["naive"]["alarm_day"] > s["learned"]["alarm_day"])


class LoadG16(unittest.TestCase):
    def test_hundred_concurrent_tasks_meet_tier_targets_without_errors_or_duplicates(self):
        r = P6.load_profile(n_tasks=100, workers=8)
        self.assertEqual(r["error_count"], 0, r["errors"])
        self.assertLess(r["T0_ms"]["p95"], 2_000)
        self.assertLess(r["final_ms"]["p95"], 10_000)
        self.assertEqual(r["visible_publications"], 2 * r["tasks"])
        self.assertEqual(r["outcomes"].get("INCONCLUSIVE", 0), r["hung_provider_tasks"])


if __name__ == "__main__":
    unittest.main()
