"""Phase 3 demo: single-writer ledger, real kill -9 at every state boundary, budgets, outbox, e-process state.

    python -m scripts.phase3_demo
    python -m scripts.phase3_demo --no-color
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from io import StringIO
from pathlib import Path

from causal_core.storage.graph_log import rebuild
from causal_core.storage.ledger import Ledger
from evaluation.fixture_io import FIXTURE_DIR, PROJECT_ROOT
from scripts.phase3_worker import BOUNDARIES

C = {"b": "\033[1m", "d": "\033[2m", "g": "\033[32m", "y": "\033[33m", "r": "\033[31m", "c": "\033[36m", "x": "\033[0m"}


def paint(t, *s):
    return "".join(C[x] for x in s) + t + C["x"] if C["x"] else t


def section(n, title):
    print("\n" + paint(f"[{n}] {title}", "b", "c"))


def worker(d: Path, *args) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "scripts.phase3_worker", "--dir", str(d), *args],
                          cwd=PROJECT_ROOT, capture_output=True, text=True, timeout=60)


def q(path: Path, sql: str):
    with sqlite3.connect(path) as c:
        return c.execute(sql).fetchall()


def graph_digest(d: Path) -> str:
    ledger = Ledger(d / "ledger.db")
    digest = rebuild(ledger.graph).pin().digest()
    ledger.close()
    return digest


def run() -> int:
    print(paint("CAUSAL CORE · PHASE 3 · single-writer ledger, durable jobs, outbox", "b"))
    print(paint("Each crash below is a real SIGKILL of a separate worker process at a named state boundary.\n"
                "A second process then recovers: reaps the dead lease, resumes and finishes idempotently.", "d"))

    section(1, "kill -9 at every boundary → recover → check invariants")
    clean = Path(tempfile.mkdtemp())
    worker(clean)
    reference = graph_digest(clean)
    ok_all = True
    print(paint(f"  {'boundary':<40}{'exit':>5}  job        charged dispatched revisions outbox visible graph", "d"))
    for b in BOUNDARIES:
        d = Path(tempfile.mkdtemp())
        crash = worker(d, "--boundary", b)
        rec = worker(d, "--recover")
        job = q(d / "ledger.db", "SELECT state, remaining_query_budget FROM jobs")[0]
        charged = q(d / "ledger.db", "SELECT COALESCE(SUM(charged_units),0) FROM query_ledger")[0][0]
        dispatched = q(d / "provider.db", "SELECT COUNT(*) FROM dispatches")[0][0]
        revs = q(d / "ledger.db", "SELECT COUNT(*) FROM task_revisions")[0][0]
        outbox = ",".join(s for (s,) in q(d / "ledger.db", "SELECT status FROM outbox"))
        visible = q(d / "sink_idempotent.db", "SELECT COUNT(*) FROM visible")[0][0]
        same_graph = graph_digest(d) == reference
        good = (crash.returncode == -9 and rec.returncode == 0 and job == ("COMPLETED", 2) and charged == 1
                and revs == 1 and outbox == "ACKED" and visible == 1 and same_graph)
        ok_all &= good
        mark = paint("✓", "g") if good else paint("✗", "r")
        print(f"  {mark} {b:<38}{crash.returncode:>5}  {job[0]:<10} {charged:^7} {dispatched:^10} {revs:^9} {outbox:<6} "
              f"{visible:^7} {'= clean' if same_graph else 'DIFFERS'}")
    print(paint("  charged is always 1 and the budget is never reset; dispatched can be 2 when the crash falls between\n"
                "  dispatch and persistence (at-least-once execution, recorded in query_ledger.dispatch_count)", "d"))

    section(2, "The idempotency caveat, shown rather than hidden")
    d = Path(tempfile.mkdtemp())
    worker(d, "--boundary", "outbox:after_publish_before_ack", "--sink", "at_least_once")
    worker(d, "--recover", "--sink", "at_least_once")
    vis = q(d / "sink_at_least_once.db", "SELECT idempotency_key FROM visible")
    print(f"  sink WITHOUT idempotency, crash after publish before ack → {len(vis)} visible publications, "
          f"{len({k for (k,) in vis})} distinct identity")
    print(paint("  exactly-once visibility requires a sink that honours the idempotency key; otherwise at-least-once", "d"))

    section(3, "Evidence: revision identity, conflicts, late arrival, model outputs (I11)")
    from tests.test_phase3_runtime import _obs
    L = Ledger(Path(tempfile.mkdtemp()) / "ledger.db")
    for label, obs in (("first", _obs()), ("same again", _obs()), ("same revision, other value", _obs(value=0.6)),
                       ("revision 2", _obs(revision=2, value=0.7)), ("late arrival", _obs("late", available="2026-11-22T06:00:00Z"))):
        print(f"  {label:<28} → {L.append_observation(obs, 'REAL_OBSERVED')}")
    vis = [(o.record_key, o.revision, o.payload.value) for o in L.visible_observations("v", "2026-11-21T00:00:00+00:00")]
    print(f"  visible as of 2026-11-21: {vis}  (late arrival hidden; revision 2 supersedes)")
    try:
        L.append_observation(_obs("pred"), "MODEL_PREDICTION")
    except ValueError as exc:
        print(f"  model prediction into evidence → refused: {str(exc)[:70]}")

    section(4, "E-process state on Phase 0 fixture 08 (revised bucket + 50 replicas)")
    fx = json.loads((FIXTURE_DIR / "08_revised_bucket_eprocess.json").read_text())
    lam = fx["input"]["lambda"]
    r = L.eprocess_update("fx08", {"null": fx["input"]["null"], "bet": fx["input"]["bet"], "lambda": lam,
                                   "alpha": fx["input"]["alpha"]}, fx["input"]["records_in_arrival_order"],
                          lambda z: lam * z - lam * lam / 2)
    from collections import Counter
    print(f"  log-wealth {r.log_wealth:.3f} (oracle {fx['expected']['log_wealth']}) · events {dict(Counter(e for _, e in r.events))}")
    for ev in r.alarm_events:
        print(f"  alarm history: {ev['event']:<10} at peak {ev['peak_log_wealth']:.3f}")
    again = L.eprocess_update("fx08", {"null": fx["input"]["null"], "bet": fx["input"]["bet"], "lambda": lam,
                                       "alpha": fx["input"]["alpha"]}, fx["input"]["records_in_arrival_order"],
                              lambda z: lam * z - lam * lam / 2)
    print(f"  replaying the same records → log-wealth {again.log_wealth:.3f} (never double-consumed)")

    section(5, "Load slice (python -m scripts.phase3_load; measured, not promised)")
    path = PROJECT_ROOT / "docs" / "phase3" / "load_profile.json"
    if path.exists():
        p = json.loads(path.read_text())
        t = p["t0_enqueue_to_lease_ms"]
        print(f"  {p['machine']['cpu']} · 100 deep jobs, 4 workers, 20 ms writer hog, 1.5 s long reader")
        print(f"  T0 enqueue→lease p50/p95/p99 {t['p50']}/{t['p95']}/{t['p99']} ms (n={t['n']}) · deep {p['deep_jobs_per_s']} jobs/s · "
              f"WAL max {p['wal_mib']['max_during_run']} MiB → {p['wal_mib']['after_truncate_checkpoint']} after checkpoint")
        print(paint("  gap: leases reserve fast capacity but the writer lock has no T0 priority (§5.2 'writer slots') — CP-012 follow-up", "y"))

    section(6, "Gates (run live from tests/test_phase3_runtime.py)")
    from tests import test_phase3_runtime as t
    for cls, label in (("KillNineG15", "G15 kill -9 at 7 boundaries; no duplicates, no budget reset"),
                       ("SchedulerG15", "G15 leases, backoff, reaping, preemption, cancel, stale pins"),
                       ("BudgetAndOutboxG15", "G15 budget before dispatch, cache keys, monotone outbox"),
                       ("EvidenceLedgerG07", "G07 revision identity, conflicts, late arrival, I11"),
                       ("EProcessStateG08", "G08 (storage) revision-aware e-process = Phase 0 oracle"),
                       ("PurityG01Phase3", "G01 pure e-process state")):
        res = unittest.TextTestRunner(stream=StringIO(), verbosity=0).run(
            unittest.defaultTestLoader.loadTestsFromTestCase(getattr(t, cls)))
        ok_all &= res.wasSuccessful()
        print(f"  {paint('✓', 'g') if res.wasSuccessful() else paint('✗', 'r')} {label:<64}{res.testsRun} test{'s' if res.testsRun != 1 else ''}")
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
