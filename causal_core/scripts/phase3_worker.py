"""Phase 3 crash worker: run one scripted job, optionally SIGKILL itself at a named state boundary.

    python -m scripts.phase3_worker --dir D --boundary query:after_dispatch_before_persist
    python -m scripts.phase3_worker --dir D --recover

Every step is deterministic (same request fingerprint, same revision idempotency key, same graph
record id), so a recovery run that re-executes the job proves idempotency rather than assuming it.
The job is: admit a tiny graph → heartbeat → one budgeted query → checkpoint → commit revision +
outbox → drain to the sink → complete.
"""
from __future__ import annotations

import argparse
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from pydantic import TypeAdapter

from adapters.outputs.sqlite_sinks import AtLeastOnceSink, IdempotentSink
from adapters.sources.recording_port import RecordingPort
from causal_core.contracts.admission import AdmissionRequest
from causal_core.contracts.capabilities import CapabilityRequest
from causal_core.contracts.tasks import Pins, TaskRevision
from causal_core.orchestration.admission import GraphAdmissionCoordinator
from causal_core.orchestration.outbox import OutboxDispatcher
from causal_core.orchestration.query_executor import BudgetedQueryExecutor
from causal_core.orchestration.scheduler import PriorityScheduler
from causal_core.storage.graph_log import rebuild
from causal_core.storage.ledger import Ledger

BOUNDARIES = ("graph:mid_commit", "lease:during_renewal", "query:after_charge_before_dispatch",
              "query:after_dispatch_before_persist", "revision:after_insert_before_outbox",
              "outbox:after_mark_sent_before_publish", "outbox:after_publish_before_ack")
SCOPE = {"environment_id": "crash-lab", "environment_kind": "offline_replay"}
CUT = "2026-11-20T00:00:00Z"


def _sigkill(point: str) -> None:
    sys.stdout.write(f"SIGKILL at {point}\n")
    sys.stdout.flush()
    os.kill(os.getpid(), signal.SIGKILL)


def _admission() -> AdmissionRequest:
    node = lambda n: {"schema_version": "2.0", "node_id": n, "kind": "VARIABLE", "variable_id": n}
    edge = lambda e, a, b: {"schema_version": "2.0", "edge_id": e, "kind": "OBSERVATIONAL_ASSOCIATION", "source": a,
                            "target": b, "status": "ACTIVE", "scope": SCOPE,
                            "lineage": {"source_id": "lab", "origin_kind": "original", "lineage_root_id": f"lab:{e}",
                                        "content_hash": "a" * 64}}
    return TypeAdapter(AdmissionRequest).validate_json(json.dumps({
        "schema_version": "2.0", "request_id": "lab-structure", "source_id": "lab", "record_id": "structure:v1",
        "attestation": {"source_id": "lab", "method": "adapter_declared"}, "scope": SCOPE, "data_cut": CUT,
        "nodes": [node("a"), node("b"), node("c")], "edges": [edge("ab", "a", "b"), edge("bc", "b", "c")]}))


def _request() -> object:
    return TypeAdapter(CapabilityRequest).validate_json(json.dumps({
        "schema_version": "2.0", "request": "get_aggregate", "request_id": "job-1:q1", "variables": ["a"],
        "scope": SCOPE, "data_cut": CUT, "purpose": "phase 3 crash lab"}))


def run(directory: Path, boundary: str | None, sink_kind: str, recover: bool) -> dict:
    ledger = Ledger(directory / "ledger.db", fault_points=frozenset({boundary} if boundary else ()), on_fault=_sigkill)
    offset = [0.0]
    clock = lambda: time.time() + offset[0]
    sched = PriorityScheduler(ledger, clock, lease_ttl_s=30.0)
    if recover:
        offset[0] = 120.0
        sched.reap()                         # the dead worker's lease has expired by now
        offset[0] = 120.0 + sched.backoff_cap_s
    store = rebuild(ledger.graph)
    sched.enqueue("job-1", "task-1", 1, "T2", 5, clock() + 3600, query_budget=3, pinned_graph_version=store.version)
    worker = f"w-{os.getpid()}"
    job = sched.lease(worker, ("T2",))
    if job is None:
        return {"status": "NO_LEASE", "job": sched.job("job-1")}
    sched.start("job-1", worker)
    GraphAdmissionCoordinator(store, ledger.graph, None, clock=lambda: datetime(2026, 11, 20, tzinfo=timezone.utc)).admit(_admission())
    sched.heartbeat("job-1", worker)
    port = RecordingPort(directory / "provider.db")
    BudgetedQueryExecutor(ledger, port).execute("job-1", _request())
    sched.checkpoint("job-1", worker, "EVALUATE", {"queries": 1}, 1)
    payload = {"finding": "a~b~c association admitted; query answered"}
    rev = TaskRevision(schema_version="2.0", task_id="task-1", revision=1, outcome="COMPLETED",
                       pins=Pins(graph_version=store.version, data_cut=datetime(2026, 11, 20, tzinfo=timezone.utc)),
                       idempotency_key=Ledger.revision_key("task-1", 1, payload))
    ledger.commit_revision(rev, json.loads(rev.model_dump_json()))
    sink = (IdempotentSink if sink_kind == "idempotent" else AtLeastOnceSink)(directory / f"sink_{sink_kind}.db")
    OutboxDispatcher(ledger, sink).drain()
    sched.complete("job-1", worker)
    return {"status": "COMPLETED", "graph_version": store.version}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dir", required=True, type=Path)
    ap.add_argument("--boundary", choices=BOUNDARIES)
    ap.add_argument("--sink", choices=("idempotent", "at_least_once"), default="idempotent")
    ap.add_argument("--recover", action="store_true")
    a = ap.parse_args(argv)
    print(json.dumps(run(a.dir, a.boundary, a.sink, a.recover)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
