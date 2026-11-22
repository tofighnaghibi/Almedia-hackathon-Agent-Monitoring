"""EvidenceLedger: the single-writer SQLite ledger (v3.2 §5.1; plan §E; CP-011, CP-014, CP-015, CP-016).

One connection and one in-process writer lock. Every multi-row change is a single
`BEGIN IMMEDIATE` transaction: graph deltas (via the shared GraphLog), evidence and lineage, e-process
state, method decisions, task revisions plus outbox rows, jobs/checkpoints and the query ledger.

Fault injection: `fault_points` names state boundaries at which `on_fault(point)` runs. The default
raises InjectedCrash (rollback). The demo's worker uses SIGKILL instead to prove durability against
a real `kill -9`.
"""
from __future__ import annotations

import hashlib
import json
import threading
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Iterator

from ..contracts.observations import Observation
from ..contracts.tasks import TaskRevision
from .eprocess_state import alarm_events, consume, wealth_path
from .graph_log import GraphLog, InjectedCrash, connect

SCHEMA_SQL = (Path(__file__).with_name("schema.sql")).read_text()


def _raise(point: str) -> None:
    raise InjectedCrash(f"injected crash at {point}")


@dataclass(frozen=True)
class EProcessResult:
    stream_key: str
    log_wealth: float
    peak_log_wealth: float
    alarm_events: tuple
    events: tuple            # per-record consumption events in arrival order


class Ledger:
    def __init__(self, path: str | Path, *, fault_points: frozenset[str] = frozenset(),
                 on_fault: Callable[[str], None] = _raise):
        self.path = str(path)
        self._conn = connect(self.path)
        self._lock = threading.RLock()
        self.graph = GraphLog(self._conn, writer_lock=self._lock)
        self._conn.executescript(SCHEMA_SQL)
        self.fault_points, self.on_fault = set(fault_points), on_fault
        self.graph.fault = self.fault

    def close(self) -> None:
        self._conn.close()

    def fault(self, point: str) -> None:
        if point in self.fault_points:
            self.on_fault(point)

    @contextmanager
    def write(self) -> Iterator:
        """The only write path: serialized by the lock, atomic by BEGIN IMMEDIATE."""
        with self._lock:
            cur = self._conn
            cur.execute("BEGIN IMMEDIATE")
            try:
                yield cur
                cur.execute("COMMIT")
            except BaseException:
                cur.execute("ROLLBACK")
                raise

    def read(self, sql: str, params: tuple = ()) -> list:
        return self._conn.execute(sql, params).fetchall()

    def _next_seq(self, cur, table: str, col: str) -> int:
        return cur.execute(f"SELECT COALESCE(MAX({col}), 0) + 1 FROM {table}").fetchone()[0]

    # ── evidence: revision identity, idempotency, conflicts (CP-016) ─────────
    def append_observation(self, obs: Observation, provenance_role: str, parents: tuple[str, ...] = ()) -> str:
        """INSERTED | IDEMPOTENT | QUARANTINED_CONFLICT. Model predictions are refused here (I11)."""
        if provenance_role == "MODEL_PREDICTION" or obs.lineage.origin_kind == "model_prediction":
            raise ValueError("model outputs go to append_model_output, never to evidence (I11)")
        body = obs.model_dump_json()
        h = hashlib.sha256(body.encode()).hexdigest()
        avail = obs.clocks.available_at if obs.clocks else None
        with self.write() as cur:
            prior = cur.execute("SELECT content_hash FROM evidence WHERE source_id=? AND record_key=? AND revision=?",
                                (obs.source_id, obs.record_key, obs.revision)).fetchone()
            if prior is not None:
                if prior[0] == h:
                    return "IDEMPOTENT"
                cur.execute("INSERT OR IGNORE INTO evidence_quarantine VALUES (?,?,?,?,?,?)",
                            (obs.source_id, obs.record_key, obs.revision, h,
                             "CONFLICTING_EQUAL_REVISION: same (source, record_key, revision), different content", body))
                return "QUARANTINED_CONFLICT"
            missing = [p for p in parents if cur.execute("SELECT 1 FROM evidence WHERE evidence_id=?", (p,)).fetchone() is None]
            if missing:
                raise ValueError(f"lineage parents must already exist (acyclic by construction): {missing}")
            cur.execute("INSERT INTO evidence VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (obs.observation_id, obs.source_id, obs.record_key, obs.revision, obs.variable_id, h,
                         obs.lineage.lineage_root_id, obs.lineage.origin_kind, provenance_role, obs.scope.environment_id,
                         avail.value.isoformat() if avail and avail.quality == "KNOWN" else None, body,
                         self._next_seq(cur, "evidence", "recorded_seq")))
            cur.executemany("INSERT INTO lineage_parents VALUES (?,?)", [(obs.observation_id, p) for p in parents])
            return "INSERTED"

    def visible_observations(self, variable_id: str, data_cut_iso: str) -> list[Observation]:
        """Latest revision per record whose KNOWN availability is at or before the data cut (leakage-safe)."""
        rows = self.read(
            "SELECT e.observation_json FROM evidence e WHERE e.variable_id=? AND e.available_at IS NOT NULL "
            "AND e.available_at <= ? AND e.revision = (SELECT MAX(revision) FROM evidence x WHERE x.source_id=e.source_id "
            "AND x.record_key=e.record_key AND x.available_at IS NOT NULL AND x.available_at <= ?) ORDER BY e.record_key",
            (variable_id, data_cut_iso, data_cut_iso))
        return [Observation.model_validate_json(r[0]) for r in rows]

    def append_model_output(self, prediction_id: str, model_id: str, model_version: str, payload: dict) -> None:
        with self.write() as cur:
            cur.execute("INSERT INTO model_outputs VALUES (?,?,?,?,?)",
                        (prediction_id, model_id, model_version, json.dumps(payload, sort_keys=True),
                         self._next_seq(cur, "model_outputs", "recorded_seq")))

    # ── e-process state (CP-015) ────────────────────────────────────────────
    def eprocess_update(self, stream_key: str, spec: dict, records: list[dict],
                        increment: Callable[[float], float]) -> EProcessResult:
        """Apply records in arrival order; recompute the wealth path; append alarm events."""
        with self.write() as cur:
            row = cur.execute("SELECT spec_json, consumed_json, alarm_events_json FROM eprocess_state WHERE stream_key=?",
                              (stream_key,)).fetchone()
            if row is not None and json.loads(row[0]) != spec:
                raise ValueError("e-process spec changed: start a new stream key instead of mixing betting rules")
            consumed = json.loads(row[1]) if row else {}
            alarms = json.loads(row[2]) if row else []
            seq = self._next_seq(cur, "eprocess_state", "updated_seq")
            events = []
            for rec in records:
                consumed, ev = consume(consumed, rec)
                events.append((rec["id"], ev))
                if ev == "CONFLICT_EQUAL_REVISION":
                    cur.execute("INSERT OR IGNORE INTO evidence_quarantine VALUES (?,?,?,?,?,?)",
                                (stream_key, rec["bucket"], rec["revision"], hashlib.sha256(json.dumps(rec, sort_keys=True).encode()).hexdigest(),
                                 "CONFLICTING_EQUAL_REVISION in e-process stream", json.dumps(rec, sort_keys=True)))
                total, peak = wealth_path(consumed, increment)
                alarms = alarm_events(alarms, peak, spec["alpha"], seq)
            total, peak = wealth_path(consumed, increment)
            cur.execute("INSERT INTO eprocess_state VALUES (?,?,?,?,?,?,?) ON CONFLICT(stream_key) DO UPDATE SET "
                        "consumed_json=excluded.consumed_json, log_wealth=excluded.log_wealth, "
                        "peak_log_wealth=excluded.peak_log_wealth, alarm_events_json=excluded.alarm_events_json, "
                        "updated_seq=excluded.updated_seq",
                        (stream_key, json.dumps(spec, sort_keys=True), json.dumps(consumed, sort_keys=True), total, peak,
                         json.dumps(alarms), seq))
        return EProcessResult(stream_key, total, peak, tuple(alarms), tuple(events))

    # ── method decisions (plan §E.7) ────────────────────────────────────────
    def record_method_decision(self, task_id: str, revision: int, method_id: str, method_version: str,
                               applicability: str, reasons: list[str], seed: int | None = None) -> None:
        with self.write() as cur:
            seq = cur.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM method_decisions WHERE task_id=? AND task_revision=?",
                              (task_id, revision)).fetchone()[0]
            cur.execute("INSERT INTO method_decisions VALUES (?,?,?,?,?,?,?,?)",
                        (task_id, revision, seq, method_id, method_version, applicability, json.dumps(reasons), seed))

    # ── task revisions + transactional outbox (CP-014) ──────────────────────
    @staticmethod
    def revision_key(task_id: str, revision: int, payload: dict) -> str:
        """Deterministic: re-running the same work yields the same key, so outputs cannot reappear under a new identity."""
        blob = json.dumps({"task": task_id, "revision": revision, "payload": payload}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def commit_revision(self, rev: TaskRevision, payload: dict) -> tuple[int, bool]:
        """(outbox_id, created). Revision row and outbox row commit together; a duplicate is a no-op."""
        with self.write() as cur:
            hit = cur.execute("SELECT outbox_id FROM outbox WHERE idempotency_key=?", (rev.idempotency_key,)).fetchone()
            if hit is not None:
                return hit[0], False
            latest = cur.execute("SELECT MAX(revision) FROM task_revisions WHERE task_id=?", (rev.task_id,)).fetchone()[0]
            if latest is not None and rev.revision <= latest:
                raise ValueError(f"revision {rev.revision} is not after {latest}: revisions are monotone")
            cur.execute("INSERT INTO task_revisions VALUES (?,?,?,?,?,?)",
                        (rev.task_id, rev.revision, rev.supersedes_revision, rev.outcome, rev.idempotency_key,
                         rev.model_dump_json()))
            self.fault("revision:after_insert_before_outbox")
            cur.execute("INSERT INTO outbox (task_id, revision, idempotency_key, payload_json, status) VALUES (?,?,?,?, 'PENDING')",
                        (rev.task_id, rev.revision, rev.idempotency_key, json.dumps(payload, sort_keys=True)))
            return cur.execute("SELECT last_insert_rowid()").fetchone()[0], True

    def unacked_outbox(self) -> list[tuple]:
        return self.read("SELECT outbox_id, task_id, revision, idempotency_key, payload_json, status, attempts "
                         "FROM outbox WHERE status <> 'ACKED' ORDER BY outbox_id")

    def mark_outbox(self, outbox_id: int, status: str, error: str | None = None, bump: bool = False) -> None:
        with self.write() as cur:
            cur.execute("UPDATE outbox SET status=?, last_error=?, attempts=attempts+? WHERE outbox_id=?",
                        (status, error, 1 if bump else 0, outbox_id))
