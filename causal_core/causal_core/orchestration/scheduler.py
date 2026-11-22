"""PriorityScheduler: durable jobs and leases on the ledger (v3.2 §5.2; plan §E.3; CP-012).

States: QUEUED → LEASED → RUNNING → CHECKPOINTED | COMPLETED | FAILED | CANCELLED.
- Lease acquisition is one conditional UPDATE inside BEGIN IMMEDIATE. Only the lease owner may
  checkpoint, complete or fail. Expired leases are reaped back to QUEUED with backoff, or to FAILED
  once max attempts are used.
- Reserved fast capacity: a worker asks for tiers. T0/T1 never compete with T2/T3, and deep leases
  are capped (`max_deep_leases`), so a poisoned or saturated deep queue cannot stall T0.
- Preemption and cancellation are cooperative, at phase boundaries. Nothing interrupts a NumPy call.
- Times are seconds from an injected clock (one process, one clock; no cross-host drift in P0).
"""
from __future__ import annotations

import hashlib
import json
from typing import Callable

from ..graph.store import Snapshot
from ..storage.ledger import Ledger

FAST, DEEP = ("T0", "T1"), ("T2", "T3")


class LeaseLost(RuntimeError):
    pass


class PriorityScheduler:
    def __init__(self, ledger: Ledger, clock: Callable[[], float], *, lease_ttl_s: float = 30.0,
                 backoff_base_s: float = 2.0, backoff_cap_s: float = 60.0, max_deep_leases: int = 4):
        self.ledger, self.clock = ledger, clock
        self.lease_ttl_s, self.backoff_base_s, self.backoff_cap_s = lease_ttl_s, backoff_base_s, backoff_cap_s
        self.max_deep_leases = max_deep_leases

    @staticmethod
    def job_key(task_id: str, task_revision: int, tier: str) -> str:
        return hashlib.sha256(f"{task_id}|{task_revision}|{tier}".encode()).hexdigest()

    def backoff(self, attempt: int) -> float:
        return min(self.backoff_cap_s, self.backoff_base_s * 2 ** max(0, attempt - 1))

    def enqueue(self, job_id: str, task_id: str, task_revision: int, tier: str, priority: int, deadline_at: float,
                query_budget: int, pinned_graph_version: int, max_attempts: int = 3) -> bool:
        """False if this (task, revision, tier) is already queued: enqueueing is idempotent."""
        key = self.job_key(task_id, task_revision, tier)
        with self.ledger.write() as cur:
            if cur.execute("SELECT 1 FROM jobs WHERE idempotency_key=?", (key,)).fetchone():
                return False
            cur.execute("INSERT INTO jobs (job_id, task_id, task_revision, tier, priority, state, max_attempts, deadline_at, "
                        "remaining_query_budget, pinned_graph_version, idempotency_key) VALUES (?,?,?,?,?, 'QUEUED', ?,?,?,?,?)",
                        (job_id, task_id, task_revision, tier, priority, max_attempts, deadline_at, query_budget,
                         pinned_graph_version, key))
            return True

    def _reap(self, cur, now: float) -> list[tuple[str, str]]:
        out = []
        for job_id, attempt, max_attempts in cur.execute(
                "SELECT job_id, attempt, max_attempts FROM jobs WHERE state IN ('LEASED','RUNNING') AND lease_until < ?",
                (now,)).fetchall():
            nxt = attempt + 1
            if nxt >= max_attempts:
                cur.execute("UPDATE jobs SET state='FAILED', attempt=?, lease_owner=NULL, lease_until=NULL, "
                            "last_error='LEASE_EXPIRED: max attempts exhausted' WHERE job_id=?", (nxt, job_id))
                out.append((job_id, "FAILED"))
            else:
                cur.execute("UPDATE jobs SET state='QUEUED', attempt=?, lease_owner=NULL, lease_until=NULL, retry_after=?, "
                            "last_error='LEASE_EXPIRED: requeued' WHERE job_id=?", (nxt, now + self.backoff(nxt), job_id))
                out.append((job_id, "QUEUED"))
        return out

    def reap(self) -> list[tuple[str, str]]:
        with self.ledger.write() as cur:
            return self._reap(cur, self.clock())

    def lease(self, worker_id: str, tiers: tuple[str, ...]) -> dict | None:
        now = self.clock()
        with self.ledger.write() as cur:
            self._reap(cur, now)
            if set(tiers) & set(DEEP):
                active = cur.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('LEASED','RUNNING') AND tier IN ('T2','T3')").fetchone()[0]
                if active >= self.max_deep_leases:
                    tiers = tuple(t for t in tiers if t in FAST)
                    if not tiers:
                        return None
            marks = ",".join("?" * len(tiers))
            row = cur.execute(f"SELECT job_id FROM jobs WHERE state IN ('QUEUED','CHECKPOINTED') AND cancel_requested=0 "
                              f"AND retry_after <= ? AND tier IN ({marks}) ORDER BY tier, priority DESC, deadline_at LIMIT 1",
                              (now, *tiers)).fetchone()
            if row is None:
                return None
            cur.execute("UPDATE jobs SET state='LEASED', lease_owner=?, lease_until=? WHERE job_id=? "
                        "AND state IN ('QUEUED','CHECKPOINTED')", (worker_id, now + self.lease_ttl_s, row[0]))
            return self._row(cur, row[0])

    def lease_job(self, job_id: str, worker_id: str) -> dict | None:
        """Lease one named job (a coordinator running its own task). Same conditional update as lease()."""
        now = self.clock()
        with self.ledger.write() as cur:
            self._reap(cur, now)
            n = cur.execute("UPDATE jobs SET state='LEASED', lease_owner=?, lease_until=? WHERE job_id=? "
                            "AND state IN ('QUEUED','CHECKPOINTED') AND cancel_requested=0 AND retry_after <= ?",
                            (worker_id, now + self.lease_ttl_s, job_id, now)).rowcount
            return self._row(cur, job_id) if n == 1 else None

    def _row(self, cur, job_id: str) -> dict:
        cols = [d[0] for d in cur.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).description]
        return dict(zip(cols, cur.execute("SELECT * FROM jobs WHERE job_id=?", (job_id,)).fetchone()))

    def job(self, job_id: str) -> dict:
        with self.ledger.write() as cur:
            return self._row(cur, job_id)

    def _own(self, cur, job_id: str, worker_id: str, now: float) -> None:
        r = cur.execute("SELECT lease_owner, lease_until, state FROM jobs WHERE job_id=?", (job_id,)).fetchone()
        if r is None or r[0] != worker_id or r[1] is None or r[1] < now or r[2] not in ("LEASED", "RUNNING"):
            raise LeaseLost(f"{worker_id} does not hold a live lease on {job_id}")

    def start(self, job_id: str, worker_id: str) -> None:
        with self.ledger.write() as cur:
            self._own(cur, job_id, worker_id, self.clock())
            cur.execute("UPDATE jobs SET state='RUNNING' WHERE job_id=?", (job_id,))

    def heartbeat(self, job_id: str, worker_id: str) -> None:
        now = self.clock()
        with self.ledger.write() as cur:
            self._own(cur, job_id, worker_id, now)
            self.ledger.fault("lease:during_renewal")
            cur.execute("UPDATE jobs SET lease_until=? WHERE job_id=?", (now + self.lease_ttl_s, job_id))

    def checkpoint(self, job_id: str, worker_id: str, stage: str, payload: dict, queries_spent: int,
                   release: bool = False) -> int:
        """Persist progress. release=True is cooperative preemption: the job becomes CHECKPOINTED (re-leasable)."""
        body = json.dumps(payload, sort_keys=True)
        with self.ledger.write() as cur:
            self._own(cur, job_id, worker_id, self.clock())
            seq = cur.execute("SELECT COALESCE(MAX(seq), 0) + 1 FROM checkpoints WHERE job_id=?", (job_id,)).fetchone()[0]
            cur.execute("INSERT INTO checkpoints VALUES (?,?,?,?,?,?)",
                        (job_id, seq, stage, hashlib.sha256(body.encode()).hexdigest(), body, queries_spent))
            if release:
                cur.execute("UPDATE jobs SET state='CHECKPOINTED', lease_owner=NULL, lease_until=NULL WHERE job_id=?", (job_id,))
            return seq

    def last_checkpoint(self, job_id: str) -> tuple[int, str, dict] | None:
        r = self.ledger.read("SELECT seq, stage, payload_json FROM checkpoints WHERE job_id=? ORDER BY seq DESC LIMIT 1", (job_id,))
        return (r[0][0], r[0][1], json.loads(r[0][2])) if r else None

    def complete(self, job_id: str, worker_id: str) -> None:
        with self.ledger.write() as cur:
            self._own(cur, job_id, worker_id, self.clock())
            cur.execute("UPDATE jobs SET state='COMPLETED', lease_owner=NULL, lease_until=NULL WHERE job_id=?", (job_id,))

    def fail(self, job_id: str, worker_id: str, error: str, retryable: bool = True) -> str:
        now = self.clock()
        with self.ledger.write() as cur:
            self._own(cur, job_id, worker_id, now)
            attempt, max_attempts = cur.execute("SELECT attempt, max_attempts FROM jobs WHERE job_id=?", (job_id,)).fetchone()
            nxt = attempt + 1
            if retryable and nxt < max_attempts:
                cur.execute("UPDATE jobs SET state='QUEUED', attempt=?, lease_owner=NULL, lease_until=NULL, retry_after=?, "
                            "last_error=? WHERE job_id=?", (nxt, now + self.backoff(nxt), error, job_id))
                return "QUEUED"
            cur.execute("UPDATE jobs SET state='FAILED', attempt=?, lease_owner=NULL, lease_until=NULL, last_error=? "
                        "WHERE job_id=?", (nxt, error, job_id))
            return "FAILED"

    def cancel(self, job_id: str) -> str:
        with self.ledger.write() as cur:
            state = cur.execute("SELECT state FROM jobs WHERE job_id=?", (job_id,)).fetchone()[0]
            if state in ("QUEUED", "CHECKPOINTED"):
                cur.execute("UPDATE jobs SET state='CANCELLED', cancel_requested=1 WHERE job_id=?", (job_id,))
                return "CANCELLED"
            cur.execute("UPDATE jobs SET cancel_requested=1 WHERE job_id=?", (job_id,))
            return "CANCEL_REQUESTED"

    def should_stop(self, job_id: str) -> bool:
        return bool(self.ledger.read("SELECT cancel_requested FROM jobs WHERE job_id=?", (job_id,))[0][0])

    def acknowledge_cancel(self, job_id: str, worker_id: str) -> None:
        with self.ledger.write() as cur:
            self._own(cur, job_id, worker_id, self.clock())
            cur.execute("UPDATE jobs SET state='CANCELLED', lease_owner=NULL, lease_until=NULL WHERE job_id=?", (job_id,))

    @staticmethod
    def is_stale(pinned_version: int, current: Snapshot, relevant_nodes: tuple[str, ...]) -> list[str]:
        """Nodes whose definition or incident edges changed after the pin (§2.2: STALE → new revision, no mixing)."""
        return [n for n in relevant_nodes if current.last_changed(n) > pinned_version]
