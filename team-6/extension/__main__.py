"""Propose graph connections for new metric definitions.

    python -m extension --metric extension/examples/new_metrics.yaml
    python -m extension --metric my_metric.yaml --data data/generated/scenarios --save data/generated/candidates.yaml

Prints the candidates for each metric. ``--save`` writes all candidate edges
in the graph YAML format (status "uncertain", source "auto_discovered") for
the validation step.

With ``--apply`` the full L3 step runs: candidates are validated on the data
(graph.extend), the metric is registered and the graph is extended.

    python -m extension --metric extension/examples/new_metrics.yaml --apply
        --save-graph data/generated/graph_extended.yaml
        --save-metrics data/generated/metrics_extended.yaml
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

from graph.__main__ import DEFAULT_GRAPH
from graph.engine import MetricEngine
from graph.metric_graph import MetricGraph
from graph.registry import DEFAULT_METRICS_FILE, MetricDefinition, MetricDefinitionError, MetricRegistry

from .discovery import discover_connections


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Find candidate graph connections for new metrics.")
    ap.add_argument("--metric", required=True, help="YAML file with one or more new metric definitions")
    ap.add_argument("--data", default="data/generated/scenarios", help="simulator output folder")
    ap.add_argument("--metrics", default=str(DEFAULT_METRICS_FILE), help="existing metric definitions")
    ap.add_argument("--graph", default=str(DEFAULT_GRAPH), help="existing graph edges")
    ap.add_argument("--max-lag", type=int, default=6, help="hours of lead/lag to test in correlations")
    ap.add_argument("--save", default=None, help="write candidate edges (graph YAML format) here")
    ap.add_argument("--apply", action="store_true",
                    help="validate the candidates, register the metric and extend the graph")
    ap.add_argument("--save-graph", default=None, help="with --apply: write the extended graph here")
    ap.add_argument("--save-metrics", default=None, help="with --apply: write the extended metric list here")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    data = Path(args.data)
    if not (data / "marketplace_hourly.csv").exists():
        print(f"no data in {data}. Generate it first:  python -m simulator --scenarios all --out {data}",
              file=sys.stderr)
        return 2
    try:
        raw = yaml.safe_load(Path(args.metric).read_text(encoding="utf-8"))
        items = raw if isinstance(raw, list) else [raw]
        new = [MetricDefinition.from_dict(i) for i in items]
    except (OSError, yaml.YAMLError, MetricDefinitionError) as exc:
        print(f"cannot read {args.metric}: {exc}", file=sys.stderr)
        return 2

    engine = MetricEngine.from_dir(data, MetricRegistry.load(args.metrics))
    graph = MetricGraph.load(args.graph)
    if args.apply:
        return _apply(new, engine, graph, args)
    all_edges = []
    for defn in new:
        rep = discover_connections(defn, engine, graph, max_lag=args.max_lag)
        print(f"\n=== {defn.name}: {defn.description}")
        if not rep.validation.ok:
            print("  definition does not work on the data: " + "; ".join(rep.validation.errors))
            continue
        for w in rep.validation.warnings:
            print(f"  warning: {w}")
        for n in rep.notes:
            print(f"  note: {n}")
        if rep.related_metrics:
            print("  related by columns: " + ", ".join(f"{n} ({s:.2f})" for n, s in rep.related_metrics[:5]))
        for method, title in [("formula", "formulas"), ("correlation", "possible causes"), ("breakdown", "breakdowns")]:
            cands = rep.by_method(method)
            if not cands:
                continue
            print(f"  {title}:")
            for c in cands:
                extra = f"  [needs {c.details['not_in_graph']} in graph]" if c.details.get("not_in_graph") else ""
                print(f"    {c.summary}{extra}")
                if method != "breakdown":
                    print(f"        {c.evidence}")
        all_edges += rep.edges()

    if args.save:
        out = Path(args.save)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(yaml.safe_dump([e.to_dict() for e in all_edges], sort_keys=False), encoding="utf-8")
        print(f"\nsaved {len(all_edges)} candidate edges to {out}")
    return 0


def _apply(new, engine, graph, args) -> int:
    import warnings

    from .pipeline import add_new_metric

    failed = 0
    for defn in new:
        print(f"\n=== {defn.name}: {defn.description}")
        try:
            with warnings.catch_warnings():
                warnings.simplefilter("ignore", RuntimeWarning)  # 0/0 hours in the checkers
                res = add_new_metric(defn, engine, graph, max_lag=args.max_lag)
        except MetricDefinitionError as exc:
            print(f"  not added: {exc}")
            failed += 1
            continue
        for e in res.extension.added_edges:
            what = e.get("formula") or (f"by {e['dimension']}" if e["type"] == "breakdown" else f"{e['from']} -> {e['to']}")
            print(f"  {e['status']:10} {e['type']:14} {what}")
            print(f"  {'':10} {'':14} {e.get('evidence', '')}")
        for r in res.extension.rejected_edges:
            c = r["candidate"]
            what = c.get("formula") or (f"by {c['dimension']}" if c["type"] == "breakdown" else f"{c['from_metric']} -> {c['to_metric']}")
            print(f"  {'rejected':10} {c['type']:14} {what}  ({r['evidence']})")
        print(f"  -> registered; {len(res.confirmed)} confirmed, {len(res.uncertain)} uncertain, "
              f"{len(res.extension.rejected_edges)} rejected")
    if args.save_graph:
        graph.save(args.save_graph)
        print(f"\nsaved extended graph to {args.save_graph}")
    if args.save_metrics:
        engine.registry.to_yaml(args.save_metrics)
        print(f"saved extended metric list to {args.save_metrics}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
