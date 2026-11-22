"""Phase 3 load profile (G16 ledger/scheduler slice; plan §E "measure WAL contention, disk usage and long
reader effects"). Measured, not promised.

    python -m scripts.phase3_load          # writes docs/phase3/load_profile.json

One process, threads sharing the single-writer ledger:
  4 deep workers draining 100 queued T2 jobs (3 checkpoints each);
  1 writer hog holding 20 ms write transactions back to back;
  1 long reader holding a read snapshot for 1.5 s (blocks WAL checkpointing);
  T0 alerts enqueued every 25 ms, leased by one reserved fast worker. Latency = enqueue → lease.
"""
from __future__ import annotations

import json
import os
import platform
import sqlite3
import statistics
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timezone

from causal_core.orchestration.scheduler import PriorityScheduler
from causal_core.storage.ledger import Ledger
from evaluation.fixture_io import PROJECT_ROOT

OUT = PROJECT_ROOT / "docs" / "phase3" / "load_profile.json"


def _pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))] if xs else None


def profile(duration_s=4.0, deep_jobs=100, deep_workers=4, hog_ms=20.0, t0_every_ms=25.0) -> dict:
    tmp = tempfile.mkdtemp()
    ledger = Ledger(os.path.join(tmp, "ledger.db"))
    clock = time.monotonic
    sched = PriorityScheduler(ledger, clock, lease_ttl_s=30, max_deep_leases=deep_workers)
    for i in range(deep_jobs):
        sched.enqueue(f"deep{i}", f"deep-task-{i}", 1, "T2", 5, clock() + 600, 3, 0)
    stop = threading.Event()
    stats = {"deep_done": 0, "hog_txns": 0, "t0_latency_ms": [], "wal_mib": []}
    enqueued: dict[str, float] = {}
    lock = threading.Lock()

    def deep(wid):
        while not stop.is_set():
            job = sched.lease(f"deep-{wid}", ("T2",))
            if job is None:
                if not any(r[0] == "QUEUED" for r in ledger.read("SELECT state FROM jobs WHERE tier='T2' AND state='QUEUED' LIMIT 1")):
                    return
                time.sleep(0.002)
                continue
            sched.start(job["job_id"], f"deep-{wid}")
            for stage in ("ACQUIRE", "EVALUATE", "REPORT"):
                sched.checkpoint(job["job_id"], f"deep-{wid}", stage, {"stage": stage}, 0)
                time.sleep(0.005)
            sched.complete(job["job_id"], f"deep-{wid}")
            with lock:
                stats["deep_done"] += 1

    def hog():
        n = 0
        while not stop.is_set():
            with ledger.write() as cur:
                cur.execute("INSERT INTO model_outputs VALUES (?,?,?,?,?)", (f"hog-{n}", "hog", "0", "{}", n))
                time.sleep(hog_ms / 1000)
            n += 1
            stats["hog_txns"] = n

    def fast():
        while not stop.is_set():
            job = sched.lease("fast", ("T0", "T1"))
            if job is None:
                time.sleep(0.001)
                continue
            t = clock()
            with lock:
                stats["t0_latency_ms"].append((t - enqueued[job["job_id"]]) * 1000)
            sched.complete(job["job_id"], "fast")

    def producer():
        i = 0
        while not stop.is_set():
            jid = f"alert{i}"
            with lock:
                enqueued[jid] = clock()
            sched.enqueue(jid, f"alert-task-{i}", 1, "T0", 9, clock() + 2, 1, 0)
            i += 1
            time.sleep(t0_every_ms / 1000)

    def long_reader():
        conn = sqlite3.connect(os.path.join(tmp, "ledger.db"))
        conn.execute("BEGIN")
        conn.execute("SELECT COUNT(*) FROM jobs").fetchone()
        time.sleep(1.5)                       # holds a snapshot: WAL cannot be checkpointed past it
        conn.execute("COMMIT")
        conn.close()

    def wal_watch():
        while not stop.is_set():
            wal = os.path.join(tmp, "ledger.db-wal")
            stats["wal_mib"].append(os.path.getsize(wal) / 2**20 if os.path.exists(wal) else 0.0)
            time.sleep(0.05)

    threads = [threading.Thread(target=f, args=a, daemon=True) for f, a in
               [(deep, (w,)) for w in range(deep_workers)] + [(hog, ()), (fast, ()), (producer, ()), (long_reader, ()), (wal_watch, ())]]
    start = clock()
    for t in threads:
        t.start()
    time.sleep(duration_s)
    stop.set()
    for t in threads:
        t.join(timeout=5)
    elapsed = clock() - start
    ledger.read("PRAGMA wal_checkpoint(TRUNCATE)")
    wal_after = os.path.getsize(os.path.join(tmp, "ledger.db-wal")) / 2**20 if os.path.exists(os.path.join(tmp, "ledger.db-wal")) else 0.0
    lat = stats["t0_latency_ms"]
    return {
        "scenario": {"duration_s": duration_s, "deep_jobs_queued": deep_jobs, "deep_workers": deep_workers,
                     "writer_hog_txn_ms": hog_ms, "t0_interval_ms": t0_every_ms, "long_reader_s": 1.5},
        "t0_enqueue_to_lease_ms": {"n": len(lat), "p50": round(_pct(lat, .5), 2), "p95": round(_pct(lat, .95), 2),
                                   "p99": round(_pct(lat, .99), 2), "max": round(max(lat), 2),
                                   "target_context": "T0 end-to-end target is p95 < 2 s (§5.3); this is only the scheduling slice"},
        "deep_jobs_completed": stats["deep_done"], "deep_jobs_per_s": round(stats["deep_done"] / elapsed, 1),
        "writer_hog_txns": stats["hog_txns"],
        "wal_mib": {"max_during_run": round(max(stats["wal_mib"] or [0]), 2), "after_truncate_checkpoint": round(wal_after, 2)},
        "db_mib": round(os.path.getsize(os.path.join(tmp, "ledger.db")) / 2**20, 2),
    }


def main() -> int:
    cpu = subprocess.run(["sysctl", "-n", "machdep.cpu.brand_string"], capture_output=True, text=True).stdout.strip()
    result = {"measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "machine": {"os": platform.platform(), "cpu": cpu, "python": platform.python_version()},
              "caveat": "Threads in one CPython process sharing one SQLite connection behind one writer lock (GIL applies). "
                        "Not an end-to-end tier latency; measures ledger + scheduler contention only.",
              **profile()}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
