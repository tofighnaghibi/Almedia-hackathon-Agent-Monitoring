"""Phase 2 graph profile (plan §D performance tests; F08: measured, never assumed).

    python -m scripts.phase2_profile            # writes docs/phase2/graph_profile.json

Synthetic graph: 5,000 VARIABLE nodes, 4 out-edges each (20,000 edges), then 10 knowledge versions
each revising 5% of edges. It measures pin cost, bounded traversal percentiles and truncation, relationship
lookups, memory by retained version count, and SQLite log commit/rebuild time. These numbers hold for
this machine and shape only. They are not a guarantee.
"""
from __future__ import annotations

import json
import os
import platform
import random
import statistics
import subprocess
import tempfile
import time
import tracemalloc
from datetime import datetime, timezone

from causal_core.contracts.graph import GraphEdge, GraphNode, Validity
from causal_core.graph.store import GraphDelta, GraphStore, NodeRecord
from causal_core.graph.views import CausalEvidenceView, TraversalBound
from causal_core.storage.graph_log import GraphLog, rebuild
from evaluation.fixture_io import PROJECT_ROOT

OUT = PROJECT_ROOT / "docs" / "phase2" / "graph_profile.json"
SCOPE = {"environment_id": "bench", "environment_kind": "offline_replay"}
KINDS = ("OBSERVATIONAL_ASSOCIATION", "CAUSAL_HYPOTHESIS", "UNKNOWN_RELATIONSHIP")
T0 = datetime(2026, 1, 1, tzinfo=timezone.utc)


def _edge(i: int, src: int, dst: int, status: str = "ACTIVE") -> GraphEdge:
    reasons = ["bench revision"] if status != "ACTIVE" else []
    return GraphEdge.model_validate_json(json.dumps({
        "schema_version": "2.0", "edge_id": f"e{i}", "kind": KINDS[i % 3], "source": f"n{src}", "target": f"n{dst}",
        "status": status, "status_reasons": reasons, "scope": SCOPE,
        "lineage": {"source_id": "bench", "origin_kind": "synthetic_generated", "generator_version": "bench/1",
                    "lineage_root_id": f"bench:{i}", "content_hash": "b" * 64}}))


def _pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def profile(n_nodes=5000, out_degree=4, versions=10, revise_share=0.05, seeds=1000, seed=7) -> dict:
    rng = random.Random(seed)
    tracemalloc.start()
    store = GraphStore()
    nodes = tuple(NodeRecord(GraphNode(schema_version="2.0", node_id=f"n{i}", kind="VARIABLE", variable_id=f"n{i}"),
                             None, Validity()) for i in range(n_nodes))
    pairs = [(i, rng.randrange(n_nodes)) for i in range(n_nodes) for _ in range(out_degree)]
    pairs = [(a, b if b != a else (b + 1) % n_nodes) for a, b in pairs]
    edges = [_edge(i, a, b) for i, (a, b) in enumerate(pairs)]
    first = GraphDelta(1, "bench-1", "f" * 64, "bench", "r1", T0, nodes, tuple(edges))
    store.apply(first)
    memory = {"after_v1_mib": tracemalloc.get_traced_memory()[0] / 2**20}
    deltas = [first]
    for v in range(2, versions + 1):
        idx = rng.sample(range(len(edges)), int(revise_share * len(edges)))
        d = GraphDelta(v, f"bench-{v}", "f" * 64, "bench", f"r{v}", T0,
                       edges=tuple(_edge(i, *pairs[i], status=rng.choice(["ACTIVE", "QUARANTINED"])) for i in idx))
        store.apply(d)
        deltas.append(d)
        if v in (5, versions):
            memory[f"after_v{v}_mib"] = tracemalloc.get_traced_memory()[0] / 2**20
    tracemalloc.stop()

    pin_ns = []
    for _ in range(20000):
        t = time.perf_counter_ns()
        store.pin(rng.randrange(1, versions + 1))
        pin_ns.append(time.perf_counter_ns() - t)

    trav_ms, reasons, sizes = [], {}, []
    for _ in range(seeds):
        view = CausalEvidenceView(store.pin(rng.randrange(1, versions + 1)))
        t = time.perf_counter()
        nb = view.neighborhood(f"n{rng.randrange(n_nodes)}", TraversalBound())
        trav_ms.append((time.perf_counter() - t) * 1000)
        reasons[nb.reason or "COMPLETE"] = reasons.get(nb.reason or "COMPLETE", 0) + 1
        sizes.append(len(nb.nodes))

    rel_us = []
    view = CausalEvidenceView(store.pin())
    for _ in range(20000):
        a, b = rng.randrange(n_nodes), rng.randrange(n_nodes)
        t = time.perf_counter()
        view.relationship(f"n{a}", f"n{b}")
        rel_us.append((time.perf_counter() - t) * 1e6)

    with tempfile.TemporaryDirectory() as tmp:
        log = GraphLog(os.path.join(tmp, "bench.db"))
        t = time.perf_counter()
        for d in deltas:
            log.commit(d)
        commit_s = time.perf_counter() - t
        t = time.perf_counter()
        rebuilt = rebuild(log)
        rebuild_s = time.perf_counter() - t
        same = rebuilt.pin().digest() == store.pin().digest()
        db_mib = os.path.getsize(os.path.join(tmp, "bench.db")) / 2**20
        log.close()

    return {
        "shape": {"nodes": n_nodes, "edges": len(edges), "versions": versions, "revised_per_version": int(revise_share * len(edges))},
        "pin_ns": {"p50": statistics.median(pin_ns), "p99": _pct(pin_ns, 0.99), "samples": len(pin_ns)},
        "bounded_traversal_ms": {"bound": "3 hops / 100 nodes / 400 edges / 50 ms", "samples": seeds,
                                 "p50": round(_pct(trav_ms, 0.50), 3), "p95": round(_pct(trav_ms, 0.95), 3),
                                 "p99": round(_pct(trav_ms, 0.99), 3), "max": round(max(trav_ms), 3),
                                 "stop_reasons": reasons, "median_nodes_reached": statistics.median(sizes)},
        "relationship_lookup_us": {"p50": round(_pct(rel_us, 0.5), 2), "p99": round(_pct(rel_us, 0.99), 2), "samples": len(rel_us)},
        "memory_tracemalloc": {k: round(v, 1) for k, v in memory.items()},
        "sqlite_graph_log": {"commit_all_versions_s": round(commit_s, 2), "rebuild_s": round(rebuild_s, 2),
                             "rebuild_digest_equal": same, "db_mib": round(db_mib, 1)},
    }


def main() -> int:
    def sysctl(k):
        try:
            return subprocess.run(["sysctl", "-n", k], capture_output=True, text=True, timeout=5).stdout.strip()
        except OSError:
            return None
    result = {"measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "machine": {"os": platform.platform(), "cpu": sysctl("machdep.cpu.brand_string"),
                          "python": platform.python_version()},
              "caveat": "Single-process CPython on this machine and this synthetic shape; not a latency guarantee (F08).",
              **profile()}
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(result, indent=2) + "\n")
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
