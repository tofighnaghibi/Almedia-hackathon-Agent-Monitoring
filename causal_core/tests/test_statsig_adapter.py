"""Statsig adapter (CP-033), the per-arm summary path (CP-032) and the segment field (CP-031). Offline: an invented
experiment in the Statsig cache layout; no network, no real data.

    python -m unittest tests.test_statsig_adapter -v
"""
from __future__ import annotations

import unittest

from adapters.sources.statsig_port import ReadOnlyStatsigClient, design_from_statsig, slug
from causal_core import api
from causal_core.api import ContractError
from evaluation import statsig_cases as S

M = slug(S.METRIC_ID)
CUT = "2026-11-22T06:00:00Z"


class SegmentFieldCP031(unittest.TestCase):
    def test_segment_is_typed_and_each_dimension_appears_once(self):
        base = {"schema_version": "2.0", "observation_id": "o1", "variable_id": "y", "source_id": "s", "record_key": "k",
                "revision": 1, "scope": {"environment_id": "e", "environment_kind": "staging"},
                "lineage": {"source_id": "s", "origin_kind": "original", "lineage_root_id": "r", "content_hash": "a" * 64},
                "payload": {"shape": "aggregate_stats", "n": 10, "mean": 1.0}}
        ok = api.parse_observation({**base, "segment": [{"dimension_id": "country", "value": "DE"},
                                                        {"dimension_id": "platform", "value": "iOS app"}]})
        self.assertEqual([(x.dimension_id, x.value) for x in ok.segment], [("country", "DE"), ("platform", "iOS app")])
        self.assertEqual(api.parse_observation(base).segment, ())              # old records still parse
        with self.assertRaises(ContractError):
            api.parse_observation({**base, "segment": [{"dimension_id": "arm", "value": "a"}, {"dimension_id": "arm", "value": "b"}]})


class StatsigAdapterCP033(unittest.TestCase):
    def test_design_states_only_what_statsig_states(self):
        details, _ = ReadOnlyStatsigClient(cache_dir=S.fixture_cache()).experiment(S.EXP)
        d = api.parse_study_design(design_from_statsig(details))
        self.assertEqual(d.expected_allocation, {"Control": 0.5, "Variant_A": 0.5})
        self.assertEqual((d.assignment_unit, d.compliance, d.interference), ("userID", "unknown", "unknown"))
        self.assertEqual((d.scope.environment_kind, d.provenance.source_id), ("production", "statsig-console"))

    def test_effect_question_end_to_end_and_recomputation_matches_statsig(self):
        co, port = S.harness(S.fixture_cache())
        r = co.run(S.effect_task("st-itt", S.EXP, M, "Variant_A", "Control", CUT))
        self.assertEqual(r.state.outcome, "COMPLETED")
        claim = r.state.claims[0]
        self.assertEqual(claim.assessment_status, "CONDITIONAL_IDENTIFICATION")   # same scope, SRM fine
        lims = " ".join(r.state.findings[0].limitations)
        self.assertIn("SUTVA", lims)
        self.assertIn("differential attrition", lims)                              # what summaries cannot check
        res = next(x for m, x in r.state.results if m == "randomized_effect_v1")
        self.assertTrue(any(l.startswith("UNIT_LEVEL_CHECKS_UNAVAILABLE") for l in res.limitations))
        obs = [o for resp in r.state.responses for o in resp.observations if o.variable_id == M]
        self.assertEqual({o.segment[0].value for o in obs}, {"Control", "Variant_A"})
        self.assertTrue(all("variance_adjusted_by_source:cuped" in o.observation_mechanisms for o in obs))
        x = S.cross_check(port, co.ctx.studies[slug(S.EXP)], S.EXP, M, "Variant_A", "Control", obs)
        self.assertLess(x["max_abs_diff"], 1e-9)

    def test_contrast_is_never_guessed_with_three_groups(self):
        cache = S.fixture_cache(groups=(("Control", 34, True), ("A", 33, False), ("B", 33, False)), units=(34_000, 33_000))
        co, _ = S.harness(cache)
        r = co.run(S.effect_task("st-3", S.EXP, M, "A", "Control", CUT))
        self.assertEqual(r.state.failures[0].cls, "MISSING_EVIDENCE")
        self.assertIn("declare which two", r.state.failures[0].detail)
        co2, _ = S.harness(cache, contrasts={S.EXP: ("Control", "A")})
        self.assertEqual(co2.run(S.effect_task("st-3b", S.EXP, M, "A", "Control", CUT)).state.outcome, "COMPLETED")

    def test_cached_pulse_uses_the_cache_rule_and_refuses_other_contrasts(self):
        cache = S.fixture_cache(groups=(("Control", 34, True), ("A", 33, False), ("B", 33, False)), units=(34_000, 33_000))
        co, _ = S.harness(cache, live_semantics=False)
        self.assertEqual(co.run(S.effect_task("st-c1", S.EXP, M, "A", "Control", CUT)).state.outcome, "COMPLETED")
        co2, _ = S.harness(cache, contrasts={S.EXP: ("Control", "B")}, live_semantics=False)
        r = co2.run(S.effect_task("st-c2", S.EXP, M, "B", "Control", CUT))
        self.assertIn("needs a live call", r.state.failures[0].detail)

    def test_srm_is_recomputed_from_units_not_taken_from_statsig(self):
        co, _ = S.harness(S.fixture_cache(units=(46_000, 54_000)))
        r = co.run(S.effect_task("st-srm", S.EXP, M, "Variant_A", "Control", CUT))
        self.assertEqual(r.state.failures[0].cls, "STATISTICAL_INVALIDITY")
        self.assertFalse(r.state.claims)

    def test_srm_uses_assignment_counts_not_a_filtered_metrics_units(self):
        """CP-032 fix: a filtered metric (1,500 vs 2,000 purchasers) must not trigger a false SRM block."""
        cache = S.fixture_cache(filtered_metric_units=(2_000, 1_500))
        co, _ = S.harness(cache)
        r = co.run(S.effect_task("st-filt", S.EXP, slug(S.FILTERED_ID), "Variant_A", "Control", CUT))
        self.assertEqual(r.state.outcome, "COMPLETED")
        res = next(x for m, x in r.state.results if m == "randomized_effect_v1")
        self.assertIn("max_units_across_pulse_metrics", res.assumptions_checked[0])
        self.assertIn("49,900", res.assumptions_checked[0])
        skewed = lambda exp, groups: {"Control": 46_000, "Variant A": 54_000}        # warehouse exposures win
        co2, _ = S.harness(cache, exposure_counts=skewed)
        r2 = co2.run(S.effect_task("st-wh", S.EXP, M, "Variant_A", "Control", CUT))
        self.assertEqual(r2.state.failures[0].cls, "STATISTICAL_INVALIDITY")

    def test_without_assignment_counts_the_fallback_is_labelled(self):
        from causal_core.analytics.experiments import ArmSummary, evaluate_randomized_summary
        details, _ = ReadOnlyStatsigClient(cache_dir=S.fixture_cache()).experiment(S.EXP)
        d = api.parse_study_design(design_from_statsig(details))
        r = evaluate_randomized_summary(d, {"Control": ArmSummary(5000, 0.1, 0.004), "Variant_A": ArmSummary(5010, 0.11, 0.004)},
                                        "Variant_A", "Control")
        self.assertTrue(any(l.startswith("SRM_ON_METRIC_UNITS") for l in r.limitations))

    def test_data_cut_before_retrieval_sees_nothing(self):
        co, _ = S.harness(S.fixture_cache(cached_at="2026-11-22T05:00:00"))
        r = co.run(S.effect_task("st-cut", S.EXP, M, "Variant_A", "Control", "2026-11-21T00:00:00Z"))
        self.assertEqual(r.state.outcome, "WAITING_FOR_EVIDENCE")

    def test_exposure_effects_are_refused_from_summaries(self):
        co, _ = S.harness(S.fixture_cache())
        r = co.run(S.effect_task("st-cace", S.EXP, M, "Variant_A", "Control", CUT, functional="CACE"))
        self.assertFalse(r.state.claims)

    def test_client_cannot_write(self):
        c = ReadOnlyStatsigClient(cache_dir=S.fixture_cache())
        for verb in ("POST", "PATCH", "DELETE", "PUT"):
            with self.assertRaises(PermissionError):
                c.request(verb, "experiments/x")


if __name__ == "__main__":
    unittest.main()
