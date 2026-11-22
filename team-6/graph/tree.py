"""Tree view builder and ASCII renderer for browsing the metric graph."""

from __future__ import annotations

from typing import Any

from graph.edges import GraphEdge
from graph.metric_graph import MetricGraph


def _breakdown_dims(graph: MetricGraph, metric: str) -> list[str]:
    dims: list[str] = []
    for _, _, data in graph.graph.out_edges(metric, data=True):
        edge: GraphEdge = data["edge"]
        if edge.type == "breakdown" and edge.dimension and edge.dimension not in dims:
            dims.append(edge.dimension)
    return dims


def _formula_inputs(graph: MetricGraph, metric: str) -> list[tuple[str, str]]:
    """Return sorted ``(input_metric, status)`` pairs for formula edges into ``metric``."""
    items: dict[str, str] = {}
    for u, _, data in graph.graph.in_edges(metric, data=True):
        edge: GraphEdge = data["edge"]
        if edge.type == "formula":
            items[u] = edge.status
    return sorted(items.items())


def _build_node(
    graph: MetricGraph,
    metric: str,
    status: str | None,
    seen: set[str],
) -> dict[str, Any]:
    splits = _breakdown_dims(graph, metric)
    if metric in seen:
        return {
            "metric": metric,
            "status": status,
            "splits": splits,
            "see_above": True,
            "children": [],
        }

    seen.add(metric)
    children = [
        _build_node(graph, inp, inp_status, seen)
        for inp, inp_status in _formula_inputs(graph, metric)
    ]
    return {
        "metric": metric,
        "status": status,
        "splits": splits,
        "see_above": False,
        "children": children,
    }


def build_tree(
    graph: MetricGraph,
    roots: tuple[str, ...] = ("revenue", "orders"),
) -> dict[str, Any]:
    """Build a nested tree dict walking backwards along formula edges from ``roots``."""
    seen: set[str] = set()
    return {
        "roots": [_build_node(graph, root, None, seen) for root in roots],
    }


def _render_node(node: dict[str, Any], prefix: str, is_last: bool, is_root: bool, lines: list[str]) -> None:
    connector = "" if is_root else ("└── " if is_last else "├── ")
    parts = [node["metric"]]
    if node.get("status"):
        parts.append(f"[{node['status']}]")
    if node.get("splits"):
        parts.append(f"(splits: {', '.join(node['splits'])})")
    if node.get("see_above"):
        parts.append("[see above]")
    lines.append(f"{prefix}{connector}{' '.join(parts)}")

    child_prefix = "" if is_root else (prefix + ("    " if is_last else "│   "))
    children = node.get("children", [])
    for idx, child in enumerate(children):
        _render_node(child, child_prefix, idx == len(children) - 1, False, lines)


def render_text(tree: dict[str, Any]) -> str:
    """Render a nested tree from ``build_tree`` as an indented ASCII tree."""
    lines: list[str] = []
    for root in tree.get("roots", []):
        _render_node(root, "", True, True, lines)
    return "\n".join(lines)
