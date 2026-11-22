"""Check the metric graph against simulator data and print the tree.

    python -m simulator                     # once: writes data/generated/baseline
    python -m graph                         # check seed_graph.yaml on that data
    python -m graph --data data/generated/scenarios --save data/generated/graph_checked.yaml

Exit code 0 when every formula and breakdown edge passes, 1 otherwise.
"""

from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

from .bridge import run_checks
from .engine import MetricEngine
from .metric_graph import MetricGraph
from .registry import DEFAULT_METRICS_FILE, MetricRegistry
from .tree import build_tree, render_text

DEFAULT_GRAPH = Path(__file__).with_name("seed_graph.yaml")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Validate the metric graph on simulator data.")
    ap.add_argument("--data", default="data/generated/baseline", help="simulator output folder")
    ap.add_argument("--metrics", default=str(DEFAULT_METRICS_FILE), help="metric definitions YAML")
    ap.add_argument("--graph", default=str(DEFAULT_GRAPH), help="graph edges YAML")
    ap.add_argument("--start", default=None, help="UTC start (default: first hour in the data)")
    ap.add_argument("--end", default=None, help="UTC end, exclusive (default: after the last hour)")
    ap.add_argument("--save", default=None, help="write the graph with updated edge statuses here")
    args = ap.parse_args(argv)
    # the tree uses box-drawing characters; Windows consoles default to cp1252
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    data = Path(args.data)
    if not (data / "marketplace_hourly.csv").exists():
        print(f"no data in {data}. Generate it first:  python -m simulator --out {data}", file=sys.stderr)
        return 2

    t0 = time.perf_counter()
    registry = MetricRegistry.load(args.metrics)
    engine = MetricEngine.from_dir(data, registry)
    graph = MetricGraph.load(args.graph)
    lo, hi = engine.time_range()
    print(f"data: {data}  ({lo:%Y-%m-%d %H:%M} .. {hi:%Y-%m-%d %H:%M} UTC)")
    print(f"metrics: {len(registry)}  graph: {graph.graph.number_of_nodes()} nodes, "
          f"{len(graph.edges())} edge arrows")

    invalid = {m.name: r.errors for m in registry for r in [engine.validate(m)] if not r.ok}
    if invalid:
        print("\nmetric definitions that fail on this data:")
        for name, errs in invalid.items():
            print(f"  {name}: {'; '.join(errs)}")

    report = run_checks(engine, graph, args.start, args.end)
    if report.unknown_metrics:
        print(f"\ngraph uses metrics that are not in the registry: {report.unknown_metrics}")
        print("add them to the metric definitions or rename them in the graph.")
        return 1

    print(f"\n{'result':8} {'edge':60} detail")
    for (metric, key), res in sorted(report.results.items(), key=lambda kv: (kv[1].outcome != "fail", kv[0])):
        print(f"{res.outcome:8} {metric + '  ' + key:60} {res.detail}  ({res.checked} rows)")
    c = report.counts
    print(f"\n{c['pass']} passed, {c['fail']} failed, {c['skipped']} skipped "
          f"in {time.perf_counter() - t0:.1f}s")

    print("\nmetric tree (formula inputs; splits = breakdown dimensions):\n")
    print(render_text(build_tree(graph)))

    if args.save:
        graph.save(args.save)
        print(f"\nsaved checked graph to {args.save}")
    return 0 if report.ok else 1


if __name__ == "__main__":
    sys.exit(main())
