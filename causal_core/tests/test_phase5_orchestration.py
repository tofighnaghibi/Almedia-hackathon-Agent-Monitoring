"""Phase 5 gates: G01 purity, G10 capability trace, G13 task independence, G14 acquisition, G15 durable revisions,
G16 (partial: deadlines, progressive delivery); plus the failure taxonomy and the policy gate.

    python -m unittest tests.test_phase5_orchestration -v
"""
from __future__ import annotations

import ast
import time
import unittest
from dataclasses import replace
from pathlib import Path

from causal_core.orchestration import planner as P
from causal_core.orchestration.policy import ReportEnvelope, vet
from emulator import experiment_world as W
from evaluation.phase5_cases import (Q_ATE, Q_DESIGN, Q_ITT, Q_MONITOR, Q_NO_STUDY, Q_PREDICT, Q_STUDY, harness, task,
                                     truth_check)

ROOT = Path(__file__).resolve().parents[1]


class TaskIndependenceG13(unittest.TestCase):
    def test_effect_question_runs_with_no_monitor_and_no_incident(self):
        co, *_ = harness(monitors=False)
        r = co.run(task("g13-itt", Q_ITT))
        self.assertEqual(r.state.outcome, "COMPLETED")
        self.assertIsNone(r.state.task.incident_id)
        self.assertNotIn("savi_eprocess_v1", r.state.selected)
        self.assertEqual(r.state.claims[0].assessment_status, "CONDITIONAL_IDENTIFICATION")

    def test_monitor_trigger_and_study_completion_use_the_same_core(self):
        co, *_ = harness()
        self.assertEqual(co.run(task("g13-mon", Q_MONITOR, "monitor", incident_id="inc-1")).state.outcome, "COMPLETED")
        self.assertEqual(co.run(task("g13-study", Q_STUDY, "study_completion")).state.outcome, "COMPLETED")

    def test_planner_has_no_domain_vocabulary(self):
        tree = ast.parse((ROOT / "causal_core/orchestration/planner.py").read_text())
        docstrings = {id(n.body[0].value) for n in ast.walk(tree)
                      if isinstance(n, (ast.Module, ast.FunctionDef, ast.ClassDef)) and n.body
                      and isinstance(n.body[0], ast.Expr) and isinstance(n.body[0].value, ast.Constant)}
        words = " ".join(n.value.lower() for n in ast.walk(tree)
                         if isinstance(n, ast.Constant) and isinstance(n.value, str) and id(n) not in docstrings)
        for banned in ("conversion", "revenue", "anomaly", "incident", "freezer", "door", "trial", "checkout", "kpi"):
            self.assertNotIn(banned, words, banned)


class CapabilityTraceG10(unittest.TestCase):
    def test_method_applicability_is_persisted_for_every_method(self):
        co, _, ledger, _ = harness()
        co.run(task("g10", Q_ITT))
        rows = ledger.read("SELECT method_id, applicability FROM method_decisions WHERE task_id='g10'")
        self.assertGreaterEqual(len(rows), 12)
        self.assertIn(("randomized_effect_v1", "SUPPORTED"), {(m, a) for m, a in rows})


class AcquisitionG14(unittest.TestCase):
    def test_design_evidence_ranks_queries_and_reports_gaps_without_executing(self):
        co, *_ = harness()
        r = co.run(task("g14", Q_DESIGN))
        self.assertEqual(r.state.findings[0].claim_kind, "ACTION_RECOMMENDATION")
        self.assertTrue(any("rerun_ab_with_holdout" in g.missing for g in r.state.gaps))
        self.assertEqual(r.decisions[-1].verdict, "DESIGN_TEST_ONLY")


class DurableRevisionsG15(unittest.TestCase):
    def test_progressive_revisions_are_monotone_and_rerun_is_idempotent(self):
        co, sink, ledger, _ = harness()
        t = task("g15", Q_ITT)
        r1 = co.run(t)
        self.assertEqual([x["revision"] for x in r1.reports], [1, 2])
        self.assertTrue(r1.reports[0]["provisional"] and not r1.reports[1]["provisional"])
        stages = [s for (s,) in ledger.read("SELECT stage FROM checkpoints ORDER BY seq")]
        self.assertEqual(stages[:3], ["FRAME", "AUDIT_INPUTS", "SELECT_CAPABILITIES"])
        before = len(sink.visible())
        again = co.run(t)                         # same task, same ledger: replayed, nothing new becomes visible
        self.assertEqual(again.delivery, "REPLAYED")
        self.assertEqual(again.state.outcome, r1.state.outcome)
        self.assertEqual(len(sink.visible()), before)


    def test_reasking_with_a_later_data_cut_recomputes_and_continues_the_revision_history(self):
        """CP-025 fix: replay is keyed on the question's content (incl. data cut), not on the task id alone."""
        from evaluation.phase5_cases import CUT
        co, sink, ledger, _ = harness()
        first = co.run(task("cp025", Q_ITT))
        later = task("cp025", Q_ITT).model_copy(update={"data_cut": task("cp025", Q_ITT).data_cut.model_copy(
            update={"as_of": task("cp025", Q_ITT).data_cut.as_of.replace(hour=7)})})
        again = co.run(later)
        self.assertNotEqual(again.delivery, "REPLAYED")
        self.assertEqual([r["revision"] for r in again.reports], [3, 4])         # continues after 1, 2
        same_again = co.run(later)
        self.assertEqual(same_again.delivery, "REPLAYED")
        self.assertIn("[3, 4]", same_again.state.trace[0][2])
        bigger = task("cp025", Q_ITT, max_queries=7)                            # a different budget is different work
        self.assertEqual([r["revision"] for r in co.run(bigger).reports], [5, 6])
        revs = [r for (r,) in ledger.read("SELECT revision FROM task_revisions WHERE task_id='cp025' ORDER BY revision")]
        self.assertEqual(revs, [1, 2, 3, 4, 5, 6])
        self.assertEqual(len(sink.visible()), 6 + 0)                            # no duplicate publication on replay
        self.assertEqual(first.state.outcome, again.state.outcome)


class FailureTaxonomyAndDeadlines(unittest.TestCase):
    def test_every_failure_path_is_typed(self):
        cases = {
            "hung provider": (dict(hang_s=1.0), task("f-hang", Q_ITT, max_wall_ms=200), "SOURCE_TIMEOUT", "INCONCLUSIVE"),
            "provider crash": (dict(fail="connection reset"), task("f-crash", Q_ITT), "SOURCE_FAILURE", "INCONCLUSIVE"),
            "zero budget": ({}, task("f-quota", Q_ITT, max_queries=0), "QUOTA", "PARTIAL_BUDGET"),
            "forecast with no mechanism": ({}, task("f-p1", Q_PREDICT, "scheduled"), "UNSUPPORTED_MODEL", "UNSUPPORTED"),
            "no study named": ({}, task("f-nostudy", Q_NO_STUDY), "MISSING_EVIDENCE", "WAITING_FOR_EVIDENCE"),
            "other scope": ({}, task("f-scope", Q_ITT, scope=W.scope("emu-product-2")), "SCOPE_MISMATCH", "FAILED_VALIDATION"),
        }
        for name, (port_kwargs, t, cls, outcome) in cases.items():
            with self.subTest(case=name):
                co, *_ = harness(**port_kwargs)
                t0 = time.perf_counter()
                r = co.run(t)
                self.assertEqual(r.state.outcome, outcome)
                self.assertEqual(r.state.failures[0].cls, cls)
                if name == "hung provider":
                    self.assertLess(time.perf_counter() - t0, 0.9)          # deadline 200 ms, provider hangs 1 s

    def test_missing_design_or_null_is_a_gap_not_a_default(self):
        co, *_ = harness(studies=False, monitors=False)
        self.assertEqual(co.run(task("f-design", Q_ITT)).state.failures[0].cls, "MISSING_EVIDENCE")
        r = co.run(task("f-null", Q_MONITOR, "monitor"))
        self.assertEqual(r.state.outcome, "WAITING_FOR_EVIDENCE")
        self.assertTrue(r.state.gaps)

    def test_broken_randomization_is_statistical_invalidity(self):
        co, *_ = harness(W.build(treated_share=0.56))
        r = co.run(task("f-srm", Q_ITT))
        self.assertEqual(r.state.failures[0].cls, "STATISTICAL_INVALIDITY")
        self.assertEqual(r.state.assessments[0].status, "INVALID_DESIGN")
        self.assertFalse(r.state.claims)


class PolicyGate(unittest.TestCase):
    def test_actions_are_denied_and_effects_need_a_claim(self):
        co, *_ = harness()
        r = co.run(task("pol-act", Q_ITT), requested_action="ship variant to 100%")
        self.assertEqual(r.decisions[-1].verdict, "DENY")
        self.assertEqual(r.decisions[0].verdict, "PUBLISH_PROVISIONAL")
        forged = replace(r.state, claims=())
        self.assertEqual(vet(forged).verdict, "DENY")

    def test_reports_with_gaps_are_reports_and_only_test_designs_are_proposals(self):
        """CP-026 fix: an unsupported question or a missing study is a read-only report that lists gaps."""
        co, *_ = harness()
        for tid, q, trig in (("lbl-p1", Q_PREDICT, "scheduled"), ("lbl-nostudy", Q_NO_STUDY, "explicit_question")):
            r = co.run(task(tid, q, trig))
            self.assertTrue(r.state.gaps)
            self.assertEqual(r.decisions[-1].verdict, "PUBLISH_REPORT", tid)
            self.assertIn("recommended next measurement", r.decisions[-1].reasons[0])
        self.assertEqual(co.run(task("lbl-design", Q_DESIGN)).decisions[-1].verdict, "DESIGN_TEST_ONLY")

    def test_output_matches_the_result_schema(self):
        co, *_ = harness()
        for q, trig in ((Q_ITT, "explicit_question"), (Q_ATE, "explicit_question"), (Q_PREDICT, "scheduled")):
            for rep in co.run(task(f"schema-{q['question_kind']}-{q.get('estimand', {}).get('functional', '')}", q, trig)).reports:
                ReportEnvelope.model_validate(rep)


class TruthScoredOutsideTheCore(unittest.TestCase):
    def test_itt_covers_truth_exposure_not_claimed_drop_detected_after_it_happens(self):
        co, _, _, world = harness()
        t = truth_check(co.run(task("truth-itt", Q_ITT)), co.run(task("truth-mon", Q_MONITOR, "monitor")), world)
        self.assertTrue(t["itt_ci_covers_truth"])
        self.assertFalse(t["exposure_effect_claimed"])
        self.assertIsNotNone(t["alarm_point"])
        self.assertFalse(t["alarm_before_drop"])


class PurityG01Phase5(unittest.TestCase):
    def test_orchestration_imports_no_emulator_evaluation_adapters_or_scripts(self):
        for f in (ROOT / "causal_core/orchestration").glob("*.py"):
            for node in ast.walk(ast.parse(f.read_text())):
                if isinstance(node, (ast.Import, ast.ImportFrom)):
                    names = [a.name for a in node.names] if isinstance(node, ast.Import) else [node.module or ""]
                    for n in names:
                        self.assertFalse(n.split(".")[0] in {"emulator", "evaluation", "adapters", "scripts"}, f"{f.name}: {n}")
        self.assertEqual(P.STAGES[0], "FRAME")


if __name__ == "__main__":
    unittest.main()
