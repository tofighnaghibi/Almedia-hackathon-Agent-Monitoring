"""Phase 3 gates: G15 durable scheduler/outbox under real kill -9, G07 evidence independence (ledger),
G08 storage side (revision-aware e-process state), G05 graph+ledger atomicity, plus budgets and leases.

    python -m unittest discover -s tests -t . -v
"""
from __future__ import annotations

import ast
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from adapters.outputs.sqlite_sinks import AtLeastOnceSink, IdempotentSink
from adapters.sources.recording_port import RecordingPort
from causal_core import api
from causal_core.contracts.tasks import Pins, TaskRevision
from causal_core.orchestration.outbox import OutboxDispatcher
from causal_core.orchestration.query_executor import BudgetExhausted, BudgetedQueryExecutor, cache_key
from causal_core.orchestration.scheduler import LeaseLost, PriorityScheduler
from causal_core.storage.graph_log import rebuild
from causal_core.storage.ledger import Ledger
from evaluation import fixture_io as io
from scripts.phase3_worker import BOUNDARIES, _request

ROOT = io.PROJECT_ROOT


def worker(directory: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "scripts.phase3_worker", "--dir", str(directory), *args],
                          cwd=ROOT, capture_output=True, text=True, timeout=60)


def q(path: Path, sql: str) -> list:
    with sqlite3.connect(path) as c:
        return c.execute(sql).fetchall()


class KillNineG15(unittest.TestCase):
    """plan §E fault injection: kill at each boundary; restart must not duplicate visible output or reset budgets."""

    @classmethod
    def setUpClass(cls):
        clean = Path(tempfile.mkdtemp())
        assert worker(clean).returncode == 0
        ledger = Ledger(clean / "ledger.db")
        cls.reference_digest = rebuild(ledger.graph).pin().digest()      # what an uncrashed run produces
        ledger.close()

    def _crash_and_recover(self, boundary: str, sink: str = "idempotent") -> Path:
        d = Path(tempfile.mkdtemp())
        crashed = worker(d, "--boundary", boundary, "--sink", sink)
        self.assertEqual(crashed.returncode, -9, f"{boundary}: {crashed.stdout} {crashed.stderr}")
        recovered = worker(d, "--recover", "--sink", sink)
        self.assertEqual(recovered.returncode, 0, recovered.stderr)
        self.assertIn('"COMPLETED"', recovered.stdout)
        return d

    def test_every_boundary_recovers_without_duplicates_or_budget_reset(self):
        for boundary in BOUNDARIES:
            with self.subTest(boundary=boundary):
                d = self._crash_and_recover(boundary)
                led = d / "ledger.db"
                self.assertEqual(q(led, "SELECT state, remaining_query_budget FROM jobs"), [("COMPLETED", 2)])
                self.assertEqual(q(led, "SELECT SUM(charged_units) FROM query_ledger"), [(1,)])     # charged once
                self.assertEqual(q(led, "SELECT COUNT(*) FROM task_revisions"), [(1,)])
                self.assertEqual(q(led, "SELECT status FROM outbox"), [("ACKED",)])
                self.assertEqual(q(led, "SELECT COUNT(*) FROM graph_versions"), [(1,)])
                self.assertEqual(q(d / "sink_idempotent.db", "SELECT COUNT(*) FROM visible"), [(1,)])  # exactly-once visible
                ledger = Ledger(led)
                recovered = rebuild(ledger.graph)
                self.assertEqual((recovered.version, recovered.pin().digest()), (1, self.reference_digest))
                ledger.close()

    def test_dispatch_may_repeat_but_charge_never_does(self):
        d = self._crash_and_recover("query:after_dispatch_before_persist")
        self.assertEqual(q(d / "provider.db", "SELECT COUNT(*) FROM dispatches"), [(2,)])           # at-least-once execution
        self.assertEqual(q(d / "ledger.db", "SELECT dispatch_count, charged_units FROM query_ledger"), [(2, 1)])

    def test_non_idempotent_sink_shows_the_documented_duplicate(self):
        d = self._crash_and_recover("outbox:after_publish_before_ack", sink="at_least_once")
        visible = q(d / "sink_at_least_once.db", "SELECT idempotency_key FROM visible")
        self.assertEqual(len(visible), 2)                     # at-least-once: duplicate is visible, not hidden
        self.assertEqual(len({k for (k,) in visible}), 1)     # ... but under the same identity, never a new one


class LedgerFixtureMixin:
    def setUp(self):
        self.dir = Path(tempfile.mkdtemp())
        self.ledger = Ledger(self.dir / "ledger.db")
        self.now = [1000.0]
        self.sched = PriorityScheduler(self.ledger, lambda: self.now[0], lease_ttl_s=30, max_deep_leases=2)

    def tearDown(self):
        self.ledger.close()


def _obs(record_key="k1", revision=1, value=0.5, available="2026-11-20T06:00:00Z", origin="original", parents=()):
    lineage = {"source_id": "src", "origin_kind": origin, "lineage_root_id": f"root:{record_key}", "content_hash": "e" * 64}
    if origin != "original":
        lineage |= {"generator_version": "g1", "parent_evidence_ids": list(parents) or ["seed"]}
    return api.parse_observation({
        "schema_version": "2.0", "observation_id": f"{record_key}:r{revision}:{value}", "variable_id": "v",
        "payload": {"shape": "scalar", "value": value}, "source_id": "src", "record_key": record_key,
        "revision": revision, "lineage": lineage, "scope": {"environment_id": "env", "environment_kind": "production"},
        "clocks": {"observed_at": {"quality": "KNOWN", "value": "2026-11-20T00:00:00Z"},
                   "available_at": {"quality": "KNOWN", "value": available}}})


class EvidenceLedgerG07(LedgerFixtureMixin, unittest.TestCase):
    def test_revision_identity_idempotency_and_conflicts(self):
        L = self.ledger
        self.assertEqual(L.append_observation(_obs(), "REAL_OBSERVED"), "INSERTED")
        self.assertEqual(L.append_observation(_obs(), "REAL_OBSERVED"), "IDEMPOTENT")
        self.assertEqual(L.append_observation(_obs(value=0.6), "REAL_OBSERVED"), "QUARANTINED_CONFLICT")
        self.assertEqual(len(L.read("SELECT * FROM evidence_quarantine")), 1)
        self.assertEqual(L.append_observation(_obs(revision=2, value=0.7), "REAL_OBSERVED"), "INSERTED")
        self.assertEqual([o.payload.value for o in L.visible_observations("v", "2026-11-21T00:00:00+00:00")], [0.7])

    def test_late_arrivals_invisible_before_their_availability(self):
        L = self.ledger
        L.append_observation(_obs("early", available="2026-11-20T06:00:00Z"), "REAL_OBSERVED")
        L.append_observation(_obs("late", available="2026-11-22T06:00:00Z"), "REAL_OBSERVED")
        self.assertEqual([o.record_key for o in L.visible_observations("v", "2026-11-21T00:00:00+00:00")], ["early"])

    def test_model_outputs_never_enter_evidence_and_parents_must_exist(self):
        with self.assertRaises(ValueError):
            self.ledger.append_observation(_obs(), "MODEL_PREDICTION")
        self.ledger.append_model_output("p1", "rollout", "0", {"forecast": 1.0})
        self.assertEqual(len(self.ledger.read("SELECT * FROM evidence")), 0)
        with self.assertRaises(ValueError):
            self.ledger.append_observation(_obs("child"), "REAL_OBSERVED", parents=("missing-parent",))


class EProcessStateG08(LedgerFixtureMixin, unittest.TestCase):
    """Storage side of G08: revision-aware recomputation, against the Phase 0 frozen oracle (fixture 08)."""

    def setUp(self):
        super().setUp()
        fx = json.loads((io.FIXTURE_DIR / "08_revised_bucket_eprocess.json").read_text())
        self.inp, self.want = fx["input"], fx["expected"]
        lam = self.inp["lambda"]
        self.inc = lambda z: lam * z - lam * lam / 2
        self.spec = {"null": self.inp["null"], "bet": self.inp["bet"], "lambda": lam, "alpha": self.inp["alpha"]}

    def test_matches_oracle_and_keeps_alarm_history(self):
        r = self.ledger.eprocess_update("s", self.spec, self.inp["records_in_arrival_order"], self.inc)
        self.assertAlmostEqual(r.log_wealth, self.want["log_wealth"], places=9)
        self.assertEqual(r.peak_log_wealth >= self.want["threshold"], self.want["alarm"])
        self.assertEqual([e["event"] for e in r.alarm_events], ["RAISED", "SUPERSEDED"])   # fixture 08 history_rule
        self.assertEqual(sum(ev == "IGNORED_SYNTHETIC" for _, ev in r.events), 50)

    def test_replay_never_double_consumes(self):
        first = self.ledger.eprocess_update("s", self.spec, self.inp["records_in_arrival_order"], self.inc)
        again = self.ledger.eprocess_update("s", self.spec, self.inp["records_in_arrival_order"], self.inc)
        self.assertAlmostEqual(again.log_wealth, first.log_wealth, places=12)

    def test_conflicting_equal_revision_quarantined_and_spec_change_refused(self):
        rec = {"id": "x", "bucket": "b9", "revision": 1, "lineage_root": "r", "origin_kind": "original", "z": 1.0}
        self.ledger.eprocess_update("s", self.spec, [rec], self.inc)
        r = self.ledger.eprocess_update("s", self.spec, [{**rec, "z": 2.0}], self.inc)
        self.assertEqual(r.events[-1][1], "CONFLICT_EQUAL_REVISION")
        with self.assertRaises(ValueError):
            self.ledger.eprocess_update("s", {**self.spec, "lambda": 0.9}, [rec], self.inc)


class SchedulerG15(LedgerFixtureMixin, unittest.TestCase):
    def enqueue(self, job, tier="T2", priority=5, attempts=3):
        return self.sched.enqueue(job, f"task-{job}", 1, tier, priority, self.now[0] + 600, 3, 0, max_attempts=attempts)

    def test_lease_is_exclusive_and_enqueue_idempotent(self):
        self.assertTrue(self.enqueue("j1"))
        self.assertFalse(self.sched.enqueue("j1-dup", "task-j1", 1, "T2", 5, 2000, 3, 0))   # same (task, revision, tier)
        self.assertIsNotNone(self.sched.lease("w1", ("T2",)))
        self.assertIsNone(self.sched.lease("w2", ("T2",)))
        with self.assertRaises(LeaseLost):
            self.sched.checkpoint("j1", "w2", "ACQUIRE", {}, 0)

    def test_poisoned_deep_queue_cannot_stall_t0(self):
        for i in range(100):
            self.enqueue(f"deep{i}")
        self.enqueue("poison", attempts=3)
        self.assertIsNotNone(self.sched.lease("d1", ("T2",)))
        self.assertIsNotNone(self.sched.lease("d2", ("T2",)))
        self.assertIsNone(self.sched.lease("d3", ("T2",)))              # deep slots capped at 2
        self.enqueue("alert", tier="T0", priority=9)
        fast = self.sched.lease("fast", ("T0", "T1"))
        self.assertEqual(fast["job_id"], "alert")                       # reserved fast capacity

    def test_failure_backoff_and_max_attempts(self):
        self.enqueue("poison", attempts=3)
        delays = []
        for _ in range(3):
            job = self.sched.lease("w", ("T2",))
            self.assertIsNotNone(job)
            state = self.sched.fail("poison", "w", "boom")
            if state == "QUEUED":
                delays.append(self.sched.job("poison")["retry_after"] - self.now[0])
                self.assertIsNone(self.sched.lease("w", ("T2",)))      # not before backoff
                self.now[0] += 120
        self.assertEqual(state, "FAILED")
        self.assertEqual(delays, [2.0, 4.0])                            # exponential backoff

    def test_expired_lease_is_reaped_and_resumes_from_checkpoint(self):
        self.enqueue("j")
        self.sched.lease("w1", ("T2",))
        self.sched.start("j", "w1")
        self.sched.checkpoint("j", "w1", "ACQUIRE", {"cursor": 7}, 1)
        self.now[0] += 31                                               # w1 dies; lease expires
        self.sched.reap()
        self.now[0] += 10
        job = self.sched.lease("w2", ("T2",))
        self.assertEqual((job["job_id"], job["attempt"]), ("j", 1))
        self.assertEqual(self.sched.last_checkpoint("j")[1:], ("ACQUIRE", {"cursor": 7}))

    def test_cooperative_preemption_and_cancellation(self):
        self.enqueue("j")
        self.sched.lease("w", ("T2",))
        self.sched.checkpoint("j", "w", "EVALUATE", {"k": 1}, 1, release=True)
        self.assertEqual(self.sched.job("j")["state"], "CHECKPOINTED")
        self.sched.lease("w", ("T2",))
        self.assertEqual(self.sched.cancel("j"), "CANCEL_REQUESTED")
        self.assertTrue(self.sched.should_stop("j"))
        self.sched.acknowledge_cancel("j", "w")
        self.assertEqual(self.sched.job("j")["state"], "CANCELLED")

    def test_stale_pin_detected_after_relevant_graph_change(self):
        from datetime import timedelta
        from adapters.sources.emulator_port import EmulatorEvidencePort, admission_requests
        from causal_core.contracts.admission import AdmissionRequest
        from causal_core.graph.store import GraphStore
        from causal_core.orchestration.admission import GraphAdmissionCoordinator
        from emulator import coldchain
        from pydantic import TypeAdapter
        world = coldchain.emit(days=5)
        adm = GraphAdmissionCoordinator(GraphStore(), self.ledger.graph, EmulatorEvidencePort(world))
        reqs = admission_requests(world, coldchain.START + timedelta(days=5, hours=12))
        for raw in reqs:
            adm.admit(TypeAdapter(AdmissionRequest).validate_json(json.dumps(raw)))
        pin = adm.store.version
        self.assertEqual(PriorityScheduler.is_stale(pin, adm.store.pin(), ("freezer_temp_c",)), [])
        redef = {**reqs[3], "request_id": "redef", "record_id": "def:freezer_temp_c:v2",
                 "definition": {**reqs[3]["definition"], "sampling_cadence_s": 60}}
        adm.admit(TypeAdapter(AdmissionRequest).validate_json(json.dumps(redef)))
        self.assertEqual(PriorityScheduler.is_stale(pin, adm.store.pin(), ("freezer_temp_c", "door_open")), ["freezer_temp_c"])


class BudgetAndOutboxG15(LedgerFixtureMixin, unittest.TestCase):
    def test_budget_is_checked_before_dispatch(self):
        self.sched.enqueue("j", "t", 1, "T2", 5, 2000, 1, 0)
        port = RecordingPort(self.dir / "provider.db")
        ex = BudgetedQueryExecutor(self.ledger, port)
        ex.execute("j", _request())
        second = _request().model_copy(update={"request_id": "job-1:q2"})
        with self.assertRaises(BudgetExhausted):
            ex.execute("j", second)
        self.assertEqual(port.dispatch_count(), 1)                      # refused before anything was sent
        ex.execute("j", _request())                                     # replay: no charge, no dispatch
        self.assertEqual(port.dispatch_count(), 1)

    def test_cache_key_separates_cuts_and_versions(self):
        base = dict(scope_id="s", data_cut="2026-11-20", request_fingerprint="f", graph_version=3, definition_versions={"v": 1})
        self.assertNotEqual(cache_key(**base), cache_key(**{**base, "data_cut": "2026-11-21"}))
        self.assertNotEqual(cache_key(**base), cache_key(**{**base, "graph_version": 4}))
        self.assertNotEqual(cache_key(**base), cache_key(**{**base, "definition_versions": {"v": 2}}))

    def test_outbox_is_idempotent_and_monotone(self):
        pins = Pins(graph_version=0, data_cut=datetime(2026, 11, 20, tzinfo=timezone.utc))
        rev = TaskRevision(schema_version="2.0", task_id="t", revision=2, outcome="COMPLETED", pins=pins,
                           idempotency_key=Ledger.revision_key("t", 2, {"x": 1}))
        first = self.ledger.commit_revision(rev, json.loads(rev.model_dump_json()))
        self.assertEqual(self.ledger.commit_revision(rev, json.loads(rev.model_dump_json())), (first[0], False))
        older = rev.model_copy(update={"revision": 1, "idempotency_key": Ledger.revision_key("t", 1, {"x": 1})})
        with self.assertRaises(ValueError):
            self.ledger.commit_revision(older, json.loads(older.model_dump_json()))
        self.assertEqual(OutboxDispatcher(self.ledger, IdempotentSink(self.dir / "a.db")).delivery_guarantee, "EXACTLY_ONCE_VISIBLE")
        self.assertEqual(OutboxDispatcher(self.ledger, AtLeastOnceSink(self.dir / "b.db")).delivery_guarantee, "AT_LEAST_ONCE")


class PurityG01Phase3(unittest.TestCase):
    def test_eprocess_state_is_pure(self):
        tree = ast.parse((ROOT / "causal_core" / "storage" / "eprocess_state.py").read_text())
        imported = {(n.module or "").split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom)} | \
                   {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        self.assertFalse(imported & {"sqlite3", "os", "socket", "subprocess", "pathlib"})


if __name__ == "__main__":
    unittest.main()
