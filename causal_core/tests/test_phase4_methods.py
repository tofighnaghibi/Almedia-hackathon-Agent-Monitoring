"""Phase 4 gates: G08 sequential validity, G09 decomposition, G10 capability neutrality, G11 identification,
G12 study validity, G14 evidence acquisition; plus the differential check vs the Phase 0 oracle and the
adversarial suite scored against emulator truth (evaluation side).

    python -m unittest discover -s tests -t . -v
"""
from __future__ import annotations

import ast
import math
import tempfile
import unittest
from pathlib import Path

from causal_core.analytics import identification as ia
from causal_core.analytics.catalog import P0_SPECS, REGISTRY, refusal_catalog
from causal_core.analytics.decomposition import Stratum, evaluate_kitagawa, evaluate_rcd_lite
from causal_core.analytics.descriptive import BucketQuality, evaluate_association, evaluate_dq, evaluate_seasonal
from causal_core.analytics.experiments import evaluate_randomized, holm
from causal_core.analytics.quasi_experimental import evaluate_did
from causal_core.analytics.registry import MethodInput
from causal_core.analytics.sequential import evaluate_eprocess, type_one_error
from causal_core.analytics.voi import QueryOption, rank
from causal_core.storage.ledger import Ledger
from evaluation import fixture_io as io
from evaluation.phase4_cases import (adversarial_suite, assignment, did_design, differential, estimand, panel,
                                     rct_design, _confounded_view)

import numpy as np


class DifferentialVsPhase0Oracle(unittest.TestCase):
    """Production kernels (NumPy/SciPy) vs the stdlib oracle frozen in Phase 0: two independent implementations."""

    def test_all_quantities_agree(self):
        rows = differential()
        self.assertGreaterEqual(len(rows), 17)
        for r in rows:
            with self.subTest(fixture=r["fixture"], quantity=r["quantity"]):
                tol = 1e-6 if r["quantity"] == "itt_se" else 1e-9     # oracle uses p(1-p)/n, Welch uses n-1
                self.assertLessEqual(r["abs_diff"], tol)


class CapabilityNeutralityG10(unittest.TestCase):
    def test_inapplicable_inputs_are_refused_with_reasons(self):
        cases = {
            "event payload to Kitagawa": (MethodInput("EXPLAIN_OBSERVATION", ("event_batch",), frozenset({"strata", "ratio_metric"})), "kitagawa_v1", "INPUT_SHAPE"),
            "assignment data to DiD": (MethodInput("ESTIMATE_EFFECT", ("assignment_outcomes",), frozenset({"study_design", "group_map"}), "ATT"), "did_v1", "INPUT_SHAPE"),
            "ATE from an RCT method": (MethodInput("ESTIMATE_EFFECT", ("assignment_outcomes",), frozenset({"study_design"}), "ATE"), "randomized_effect_v1", "ESTIMAND_FAMILY"),
            "monitoring without a declared null": (MethodInput("DETECT_CHANGE", ("time_series",), frozenset({"alpha"})), "savi_eprocess_v1", "MISSING_METADATA"),
            "budget exceeded": (MethodInput("EXPLAIN_OBSERVATION", ("aggregate_stats",), frozenset({"strata", "ratio_metric"}), work_units=10**9), "kitagawa_v1", "BUDGET"),
        }
        for name, (inp, method, code) in cases.items():
            with self.subTest(case=name):
                a = REGISTRY.assess(REGISTRY.specs[method], inp)
                self.assertEqual(a.status, "UNSUPPORTED")
                self.assertTrue(any(r.startswith(code) for r in a.reasons), a.reasons)

    def test_rejection_trace_is_persisted(self):
        ledger = Ledger(Path(tempfile.mkdtemp()) / "l.db")
        _, trace = REGISTRY.select(MethodInput("EXPLAIN_OBSERVATION", ("event_batch",), frozenset()))
        for a in trace:
            ledger.record_method_decision("t", 1, a.method_id, a.method_version, a.status, list(a.reasons))
        rows = ledger.read("SELECT method_id, applicability FROM method_decisions")
        self.assertEqual(len(rows), len(P0_SPECS))
        self.assertIn(("kitagawa_v1", "UNSUPPORTED"), rows)

    def test_every_refusal_code_is_declared(self):
        catalog = refusal_catalog()
        self.assertEqual(len(catalog), 14)                                   # 12 P0 methods + baseline_shift_v1 + P1 rollout
        with self.assertRaises(ValueError):
            from causal_core.analytics.registry import refused
            refused(P0_SPECS[0], "UNDECLARED_CODE", "x")


    def test_descriptor_conformance_regime_forecast_next_queries(self):
        rct = REGISTRY.specs["randomized_effect_v1"]
        inp = MethodInput("ESTIMATE_EFFECT", ("assignment_outcomes",), frozenset({"study_design"}), "ITT")
        a = REGISTRY.assess(rct, inp)                                         # no source regime declared
        self.assertEqual(a.status, "UNSUPPORTED")
        self.assertTrue(any(r.startswith("SOURCE_REGIME") for r in a.reasons))
        self.assertIn(("source_regime", False), a.conditions)
        self.assertTrue(any("randomized" in q for q in a.next_queries))
        ok = REGISTRY.assess(rct, MethodInput("ESTIMATE_EFFECT", ("assignment_outcomes",), frozenset({"study_design"}),
                                              "ITT", work_units=5000, source_regime="randomized"))
        self.assertEqual((ok.status, ok.forecast_work_units, ok.work_unit_cap), ("SUPPORTED", 5000, rct.max_work_units))
        self.assertIn(rct.honest_conclusion, ok.scope_limitations)
        obs = REGISTRY.assess(rct, MethodInput("ESTIMATE_EFFECT", ("assignment_outcomes",), frozenset({"study_design"}),
                                               "ITT", source_regime="observational"))
        self.assertEqual(obs.status, "UNSUPPORTED")                            # an RCT method on observational data
        unknown = REGISTRY.assess(rct, MethodInput("ESTIMATE_EFFECT", ("assignment_outcomes",), frozenset({"study_design"}),
                                                   "ITT", work_units=None, source_regime="randomized"))
        self.assertEqual(unknown.status, "SUPPORTED_WITH_LIMITATIONS")
        missing = REGISTRY.assess(REGISTRY.specs["savi_eprocess_v1"], MethodInput("DETECT_CHANGE", ("time_series",), frozenset({"alpha"})))
        self.assertTrue(any("null_spec" in q for q in missing.next_queries))
        self.assertTrue(all(s.honest_conclusion for s in P0_SPECS))


class SequentialG08(unittest.TestCase):
    def test_type_one_error_controlled_under_declared_null(self):
        self.assertLessEqual(type_one_error(1000, 200, rho=0.0), 0.05)          # 1,000 null streams x 200 peeks

    def test_dependence_breaks_the_guarantee_so_the_method_refuses(self):
        self.assertGreater(type_one_error(1000, 200, rho=0.5), 0.05)           # documented inflation
        r = evaluate_eprocess([0.1] * 10, null_spec="N(0,1)", dependence="ar1", alpha=0.05)
        self.assertEqual(r.refusal_code, "DEPENDENCE_UNSUPPORTED")
        self.assertEqual(evaluate_eprocess([0.1], null_spec=None, dependence="independent", alpha=0.05).refusal_code,
                         "NULL_UNDECLARED")

    def test_family_splits_alpha(self):
        r = evaluate_eprocess([3.0] * 8, null_spec="N(0,1)", dependence="independent", alpha=0.05, family_size=10)
        self.assertAlmostEqual(r.values["alpha_per_stream"], 0.005)


    def test_revised_and_bursty_streams_measured(self):
        from scripts.phase4_stats_report import revised_and_bursty
        r = revised_and_bursty()                                              # 1,000 x 200, seed 7
        self.assertLessEqual(r["revised_final_version_rate"], 0.05)          # recompute keeps the guarantee
        self.assertGreaterEqual(r["revised_ever_raised_rate"], r["revised_final_version_rate"])
        self.assertGreater(r["bursty_variance_rate"], 0.05)                   # documented: misspecified scale inflates


class DecompositionG09(unittest.TestCase):
    def test_kitagawa_partition_and_zero_denominators(self):
        s = [Stratum("a", 40, 2000, 200, 8000), Stratum("b", 480, 8000, 130, 2000)]
        self.assertEqual(evaluate_kitagawa(s).values["partition"], "UNDECLARED")
        self.assertEqual(evaluate_kitagawa(s, declared_totals=(11000, 11000)).values["partition"], "INCOMPLETE_PARTITION")
        self.assertEqual(evaluate_kitagawa([Stratum("a", 1, 0, 1, 10)]).refusal_code, "ZERO_DENOMINATOR_SEGMENT")
        self.assertIn("not causal", evaluate_kitagawa(s).limitations[0])

    def test_rcd_lite_is_a_candidate_only(self):
        rng = np.random.default_rng(1)
        x = rng.normal(size=200); regime = np.repeat([0, 1], 100)
        y = np.where(regime == 0, 1.0 * x, 3.0 * x) + rng.normal(scale=0.5, size=200)
        r = evaluate_rcd_lite(y, x.reshape(-1, 1), regime)
        self.assertEqual(r.values["verdict"], "SHIFTED_MECHANISM_CANDIDATE")
        self.assertEqual(r.claim_kind, "STRUCTURAL_CAUSAL_HYPOTHESIS")
        self.assertEqual(evaluate_rcd_lite(y, np.empty((200, 0)), regime).refusal_code, "PARENTS_UNMEASURED")


class IdentificationG11(unittest.TestCase):
    def _units(self, n=400, noncompliance=False):
        rng = np.random.default_rng(3)
        out = []
        for i in range(n):
            arm = "treatment" if i % 2 else "control"
            exposed = 1 if arm == "treatment" and (not noncompliance or i % 4 != 1) else 0
            out.append({"unit_id": f"u{i}", "arm": arm, "outcome": float(rng.normal(0.3 * exposed)), "exposure": exposed})
        return out

    def test_randomized_itt_conditional_and_claim_is_not_an_edge(self):
        r = evaluate_randomized(rct_design(), assignment(self._units()), "treatment", "control")
        a = ia.assess(estimand("ITT"), "randomized", r, evidence_ids=("ev",))
        self.assertEqual(a.status, "CONDITIONAL_IDENTIFICATION")
        claim = ia.effect_claim(a, r, "units independent")
        self.assertEqual((claim.assessment_status, type(claim).__name__), ("CONDITIONAL_IDENTIFICATION", "EffectClaim"))
        self.assertTrue(any(x.startswith("no interference") and "declared" in x for x in a.assumptions_untestable))  # T1-P4-03

    def test_noncompliance_blocks_the_exposure_leap(self):
        r = evaluate_randomized(rct_design(), assignment(self._units(noncompliance=True)), "treatment", "control")
        self.assertIn("NONCOMPLIANCE", r.limitations[0])
        self.assertEqual(ia.assess(estimand("ATE"), "randomized", r, evidence_ids=("ev",)).status, "UNIDENTIFIED")
        cace = evaluate_randomized(rct_design(), assignment(self._units(noncompliance=True)), "treatment", "control", "CACE")
        a = ia.assess(estimand("CACE"), "randomized", cace, evidence_ids=("ev",))
        self.assertEqual(a.status, "CONDITIONAL_IDENTIFICATION")
        self.assertTrue(any("exclusion" in x for x in a.assumptions_untestable))
        self.assertIsNone(ia.effect_claim(ia.assess(estimand("ATE"), "randomized", r, evidence_ids=("ev",)), r, "x"))

    def test_did_identifies_att_not_ate_and_confounding_is_unidentified(self):
        fx, rows = io.load_fixture("06_conditional_did")
        r = evaluate_did(did_design(), panel([{"unit": x["unit"], "period": x["period"], "value": x["y"]} for x in rows]),
                         {x["unit"]: x["group"] for x in rows}, "treated", "control")
        self.assertEqual(ia.assess(estimand("ATT"), "quasi_experimental", r, evidence_ids=("e",)).status, "CONDITIONAL_IDENTIFICATION")
        self.assertEqual(ia.assess(estimand("ATE"), "quasi_experimental", r, evidence_ids=("e",)).status, "UNIDENTIFIED")
        fake = r.__class__(**{**r.__dict__, "claim_kind": "CAUSAL_EFFECT_CONDITIONAL"})
        a = ia.assess(estimand("ATE"), "observational", fake, evidence_ids=("e",), view=_confounded_view(),
                      treatment_node="x", outcome_node="y", adjustment_set=("z",))
        self.assertEqual(a.status, "UNIDENTIFIED")

    def test_transport_caps_at_partial(self):
        r = evaluate_randomized(rct_design(), assignment(self._units()), "treatment", "control")
        a = ia.assess(estimand("ITT", source="emu", target="prod"), "randomized", r, evidence_ids=("ev",))
        self.assertEqual(a.status, "PARTIAL_IDENTIFICATION")
        self.assertTrue(any("transport" in x for x in a.assumptions_untestable))


    def test_did_trend_sensitivity_flags_fragile_estimates_without_changing_status(self):
        rng = np.random.default_rng(11)
        cells, groups = [], {}
        for i in range(16):
            g = "treated" if i < 8 else "control"
            groups[f"u{i}"] = g
            for t in range(-4, 2):
                cells.append({"unit": f"u{i}", "period": t, "value": float(rng.normal(0.15 * (t >= 0) * (g == "treated"), 1.0))})
        r = evaluate_did(did_design(), panel(cells), groups, "treated", "control")
        sens = r.values["sensitivity"]
        self.assertTrue(sens["assessable"] and sens["fragile"], sens)
        self.assertTrue(any(x.startswith("FRAGILE_TO_TRENDS") for x in r.limitations))
        self.assertEqual(ia.assess(estimand("ATT"), "quasi_experimental", r, evidence_ids=("e",)).status,
                         "CONDITIONAL_IDENTIFICATION")
        fx, rows = io.load_fixture("06_conditional_did")
        r6 = evaluate_did(did_design(), panel([{"unit": x["unit"], "period": x["period"], "value": x["y"]} for x in rows]),
                          {x["unit"]: x["group"] for x in rows}, "treated", "control")
        self.assertFalse(r6.values["sensitivity"]["fragile"])                 # fixture 06: robust to linear trends


class StudyValidityG12(unittest.TestCase):
    def test_srm_failure_is_an_invalid_design(self):
        units = [{"unit_id": f"t{i}", "arm": "treatment", "outcome": 0.0} for i in range(600)] + \
                [{"unit_id": f"c{i}", "arm": "control", "outcome": 0.0} for i in range(400)]
        r = evaluate_randomized(rct_design(), assignment(units), "treatment", "control")
        self.assertEqual(r.refusal_code, "SRM_FAIL")
        self.assertEqual(ia.assess(estimand("ITT"), "randomized", r, evidence_ids=("e",)).status, "INVALID_DESIGN")

    def test_cluster_assignment_must_be_consistent_and_large_enough(self):
        mixed = [{"unit_id": f"u{i}", "arm": "treatment" if i % 2 else "control", "outcome": 0.0, "cluster_id": "c1"} for i in range(20)]
        self.assertEqual(evaluate_randomized(rct_design("cluster"), assignment(mixed), "treatment", "control").refusal_code,
                         "ASSIGNMENT_INCONSISTENT")

    def test_missing_covariance_blocks_joint_claims(self):
        self.assertEqual(holm({"a": 0.01, "b": 0.2}, 0.05, "fam", False, "surrogate").refusal_code, "COVARIANCE_UNAVAILABLE")
        self.assertEqual(holm({"a": 0.01}, 0.05, None, True).refusal_code, "FAMILY_UNDECLARED")
        self.assertEqual(holm({"a": 0.01, "b": 0.04, "c": 0.03}, 0.05, "fam", False).values["rejected"], ["a"])


class DescriptiveChecks(unittest.TestCase):
    def test_dq_unassessed_is_never_pass(self):
        r = evaluate_dq([BucketQuality("d1", 1.0, True), BucketQuality("d2", None, True), BucketQuality("d3", 0.6, True)])
        self.assertEqual(r.values["verdicts"], {"d1": "DQ_OK", "d2": "DQ_UNASSESSED", "d3": "DQ_IMMATURE"})
        self.assertEqual(r.values["domain_attribution_blocked"], ["d2", "d3"])

    def test_seasonal_and_association_refusals(self):
        self.assertEqual(evaluate_seasonal([1.0] * 10, period=7).refusal_code, "INSUFFICIENT_HISTORY")
        self.assertTrue(evaluate_seasonal([1.0, 2.0] * 14 + [9.0], period=2).values["anomaly_candidate"])
        self.assertEqual(evaluate_association([1, 2, 3, 4], [1, 2, 3, 4], "autocorrelated").refusal_code, "DEPENDENCE_UNDECLARED")


class EvidenceAcquisitionG14(unittest.TestCase):
    def test_cheap_discriminating_query_ranks_first_and_gaps_are_reported(self):
        h = ["mechanism", "measurement_artifact", "confounder"]
        pair = lambda a, b: frozenset({a, b})
        opts = [
            QueryOption("dq_audit", frozenset({pair("mechanism", "measurement_artifact")}), 0.9, frozenset({"r9"}), True,
                        latency_ms=200, cost_units=0.1),
            QueryOption("replica_reread", frozenset({pair("mechanism", "confounder")}), 0.9, frozenset({"r1"}), True,
                        latency_ms=50, cost_units=0.0),
            QueryOption("unknown_cost", frozenset({pair("mechanism", "measurement_artifact")}), 0.9, frozenset({"r8"}), True),
            QueryOption("pii_join", frozenset({pair("mechanism", "confounder")}), 1.0, frozenset({"r7"}), True, privacy="restricted",
                        latency_ms=10, cost_units=0.0),
            QueryOption("randomized_test", frozenset({pair("mechanism", "confounder")}), 1.0, frozenset(), False,
                        recommendation="randomize door alarms across sites"),
        ]
        r = rank(opts, h, seen_roots=frozenset({"r1"}))
        self.assertEqual(r.ranked[0][0], "dq_audit")
        self.assertEqual(dict((i, s) for i, s, _ in r.ranked)["replica_reread"], 0.0)
        self.assertLess(dict((i, s) for i, s, _ in r.ranked)["unknown_cost"], dict((i, s) for i, s, _ in r.ranked)["dq_audit"])
        self.assertEqual(r.vetoed[0][0], "pii_join")
        self.assertEqual(r.evidence_gaps[0][0], "randomized_test")
        self.assertIn("PROXY", r.label)


class EstimandComparisonC02(unittest.TestCase):
    def test_no_blanket_rct_override(self):
        self.assertTrue(ia.compare_estimands(estimand("ITT"), estimand("ITT")).comparable)
        cmp = ia.compare_estimands(estimand("ATE", population="all"), estimand("ATT", population="self-selected"))
        self.assertFalse(cmp.comparable)
        self.assertEqual(len(cmp.differences), 2)


class AdversarialSuiteScoredWithTruth(unittest.TestCase):
    """Pre-registered expectations in evaluation/phase4_cases.py; truth stays on the evaluation side."""

    def test_every_expectation_met(self):
        results = adversarial_suite()
        self.assertEqual(len(results), 6)
        for r in results:
            with self.subTest(scenario=r["scenario"]):
                self.assertTrue(r["expectation_met"], r)


class DegenerateInputsTier1(unittest.TestCase):
    """T1-P4-01: zero-variance inputs refuse with a typed code; a zero estimate keeps a finite SE."""

    def test_constant_outcomes_refuse_instead_of_crashing(self):
        u = [{"unit_id": f"u{i}", "arm": "treatment" if i % 2 else "control", "outcome": 1.0} for i in range(40)]
        self.assertEqual(evaluate_randomized(rct_design(), assignment(u), "treatment", "control").refusal_code,
                         "DEGENERATE_VARIANCE")
        u = [{"unit_id": f"c{c}u{j}", "arm": "treatment" if c % 2 else "control", "cluster_id": f"k{c}", "outcome": 2.0}
             for c in range(12) for j in range(3)]
        self.assertEqual(evaluate_randomized(rct_design("cluster"), assignment(u), "treatment", "control").refusal_code,
                         "DEGENERATE_VARIANCE")
        self.assertEqual(evaluate_association([1.0] * 5, [1.0, 2, 3, 4, 5], "independent").refusal_code, "NO_VARIATION")

    def test_exact_zero_cluster_estimate_has_finite_se_and_a_claim(self):
        u = [{"unit_id": f"c{c}u{j}", "arm": "treatment" if c % 2 else "control", "cluster_id": f"k{c}",
              "outcome": float((c // 2) % 3 + j)} for c in range(12) for j in range(5)]
        r = evaluate_randomized(rct_design("cluster"), assignment(u), "treatment", "control")
        self.assertEqual(r.values["estimate"], 0.0)
        self.assertTrue(math.isfinite(r.values["se"]) and r.values["se"] > 0)
        claim = ia.effect_claim(ia.assess(estimand("ITT"), "randomized", r, evidence_ids=("ev",)), r, "clusters independent")
        self.assertIsNotNone(claim)


class UserDecisions20261122(unittest.TestCase):
    """Decisions taken by the user on 2026-11-22 (CP-018, CP-019, CP-021, CP-022)."""

    @staticmethod
    def _rct(n_t, n_c):
        return [{"unit_id": f"t{i}", "arm": "treatment", "outcome": float(i % 3 == 0)} for i in range(n_t)] + \
               [{"unit_id": f"c{i}", "arm": "control", "outcome": float(i % 4 == 0)} for i in range(n_c)]

    def test_srm_has_a_warning_tier_above_a_hard_floor(self):
        r = evaluate_randomized(rct_design(), assignment(self._rct(1060, 940)), "treatment", "control")   # p ≈ 0.007
        self.assertTrue(0.001 <= r.values["srm_p_value"] < 0.01, r.values["srm_p_value"])
        self.assertTrue(any(l.startswith("SRM_SUSPECT") for l in r.limitations))
        self.assertEqual(ia.assess(estimand("ITT"), "randomized", r, evidence_ids=("e",)).status, "PARTIAL_IDENTIFICATION")
        self.assertEqual(evaluate_randomized(rct_design(), assignment(self._rct(1100, 900)), "treatment", "control").refusal_code,
                         "SRM_FAIL")                                                                    # p < 0.001: block
        with self.assertRaises(ValueError):                                                             # the floor is enforced
            evaluate_randomized(rct_design(), assignment(self._rct(1000, 1000)), "treatment", "control", srm_warn_alpha=1e-4)

    def test_srm_threshold_is_learned_from_history_and_never_below_the_floor(self):
        from causal_core.analytics.experiments import calibrate_srm_warning
        self.assertEqual(calibrate_srm_warning([0.5] * 20).warn_alpha, 0.01)                        # short history: start loose
        healthy = list(np.random.default_rng(3).uniform(size=2000))
        self.assertEqual(calibrate_srm_warning(healthy).warn_alpha, 0.001)                          # nothing to catch: collapse
        broken = healthy + list(np.random.default_rng(4).uniform(0.001, 0.008, size=150))
        self.assertGreater(calibrate_srm_warning(broken).warn_alpha, 0.005)                         # real problems in the band
        study = __import__("evaluation.phase4_cases", fromlist=["x"]).srm_calibration_study()
        self.assertTrue(all(v["warn_alpha"] >= 0.001 for v in study.values()))

    def test_transport_with_declared_assumptions_is_conditional(self):
        r = evaluate_randomized(rct_design(), assignment(self._rct(1000, 1000)), "treatment", "control")
        e = estimand("ITT", source="de_web", target="us_web", source_kind="production", target_kind="production")
        self.assertEqual(ia.assess(e, "randomized", r, evidence_ids=("e",)).status, "PARTIAL_IDENTIFICATION")
        a = ia.assess(e, "randomized", r, evidence_ids=("e",), transport_assumptions=("same effect modifiers in the target",))
        self.assertEqual(a.status, "CONDITIONAL_IDENTIFICATION")
        self.assertIn("transport: same effect modifiers in the target", a.assumptions_untestable)
        emu = estimand("ITT", source="sim", target="prod", source_kind="emulator", target_kind="production")
        self.assertEqual(ia.assess(emu, "randomized", r, evidence_ids=("e",),
                                   transport_assumptions=("simulator is faithful",)).status, "PARTIAL_IDENTIFICATION")  # §4.5.5

    def test_regularized_two_sided_bet_is_valid_and_catches_drops(self):
        self.assertLessEqual(type_one_error(1000, 200), 0.05)
        self.assertLessEqual(type_one_error(1000, 200, prior_strength=1.0), 0.05)
        self.assertLessEqual(type_one_error(1000, 200, prior_strength=math.inf), 0.05)
        z = list(np.random.default_rng(3).normal(-0.6, 1, 80))
        r = evaluate_eprocess(z, null_spec="N(0,1)", dependence="independent", alpha=0.05)
        self.assertTrue(r.values["alarm"])
        self.assertEqual(r.values["direction"], "down")
        self.assertEqual(r.values["bet_config"]["lambda0"], 0.25)
        self.assertEqual(evaluate_eprocess(z, null_spec="N", dependence="independent", alpha=0.05, lam_floor=0.0).refusal_code,
                         "BAD_BET")

    def test_regularized_bet_matches_an_independent_stdlib_reference_and_ledger_recompute(self):
        from causal_core.analytics.sequential import log_wealth_path, path_fn
        from causal_core.storage.eprocess_state import wealth_path_series
        z = [float(x) for x in np.random.default_rng(9).normal(0.2, 1, 60)]

        def reference(zs, lam0=0.25, k=100.0, lo=0.05, hi=1.0):                     # written from the formula, stdlib only
            def side(sign):
                total, s, out = 0.0, 0.0, []
                for t, x in enumerate(zs):
                    lam = min(hi, max(lo, (k * lam0 + s) / (k + t)))
                    total += lam * sign * x - lam * lam / 2
                    s += sign * x
                    out.append(total)
                return out
            return [math.log((math.exp(u) + math.exp(d)) / 2) for u, d in zip(side(1), side(-1))]
        ref = reference(z)
        self.assertLess(max(abs(a - b) for a, b in zip(log_wealth_path(z), ref)), 1e-12)
        consumed = {i: {"z": v} for i, v in enumerate(z)}
        self.assertAlmostEqual(wealth_path_series(consumed, path_fn())[0], ref[-1], places=12)

    def test_unknown_cost_gets_a_learned_geometric_prior(self):
        from causal_core.analytics.voi import expected_inverse_cost
        self.assertAlmostEqual(expected_inverse_cost(0.0, [1.0]), 0.5)                 # at most half of a free option
        self.assertLess(expected_inverse_cost(0.0, [3.0]), 0.5)
        self.assertLess(expected_inverse_cost(0.0, [2.0, 2.0]), expected_inverse_cost(0.0, [2.0]))   # two unknowns cost more
        h = ["a", "b"]
        pair = frozenset({frozenset({"a", "b"})})
        opts = [QueryOption("known_cheap", pair, 1.0, frozenset({"r1"}), True, latency_ms=0, cost_units=1.0),
                QueryOption("known_pricey", pair, 1.0, frozenset({"r2"}), True, latency_ms=0, cost_units=9.0),
                QueryOption("unknown", pair, 1.0, frozenset({"r3"}), True, latency_ms=0)]
        r = rank(opts, h, frozenset())
        notes = dict((i, n) for i, _, n in r.ranked)["unknown"]
        self.assertTrue(any("geometric prior, mean 5" in n for n in notes), notes)        # learned: mean of 1 and 9


class PurityG01Phase4(unittest.TestCase):
    def test_analytics_is_pure(self):
        banned = {"sqlite3", "socket", "subprocess", "requests", "urllib", "http", "os", "pathlib", "shutil",
                  "emulator", "evaluation", "scripts"}
        for path in (io.PROJECT_ROOT / "causal_core" / "analytics").glob("*.py"):
            tree = ast.parse(path.read_text())
            for node in ast.walk(tree):
                names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                         else [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else [])
                for name in names:
                    self.assertNotIn(name.split(".")[0], banned, f"{path.name} imports {name}")


if __name__ == "__main__":
    unittest.main()
