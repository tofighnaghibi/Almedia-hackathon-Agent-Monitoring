"""NetworkX MultiDiGraph wrapper for the marketplace metric graph."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import networkx as nx
import yaml

from graph.edges import GraphEdge
from graph.formulas import parse_formula


class MetricGraph:
    """Typed metric graph backed by ``networkx.MultiDiGraph``."""

    def __init__(self) -> None:
        self.graph = nx.MultiDiGraph()

    def add_edge(self, edge: GraphEdge) -> None:
        """Add an edge; formula edges expand to one arrow per input metric."""
        if edge.type == "formula":
            lhs, inputs, _ = parse_formula(edge.formula or "")
            if edge.to_metric != lhs:
                raise ValueError(
                    f"Formula LHS {lhs!r} does not match to_metric {edge.to_metric!r}"
                )
            if edge.from_metric not in inputs:
                raise ValueError(
                    f"from_metric {edge.from_metric!r} is not an input in {edge.formula!r}"
                )
            for inp in inputs:
                arrow_edge = replace(edge, from_metric=inp)
                self.graph.add_edge(inp, edge.to_metric, key=edge.key, edge=arrow_edge)
            return

        self.graph.add_edge(edge.from_metric, edge.to_metric, key=edge.key, edge=edge)

    def edges(self, type: str | None = None) -> list[GraphEdge]:
        """Return all stored ``GraphEdge`` objects, optionally filtered by type."""
        result = [data["edge"] for _, _, _, data in self.graph.edges(keys=True, data=True)]
        if type is not None:
            return [e for e in result if e.type == type]
        return result

    def parents(self, metric: str, type: str | None = None) -> list[str]:
        """Return unique parent metrics pointing into ``metric``."""
        if metric not in self.graph:
            return []
        seen: list[str] = []
        for u, _, data in self.graph.in_edges(metric, data=True):
            edge: GraphEdge = data["edge"]
            if type is not None and edge.type != type:
                continue
            if u not in seen:
                seen.append(u)
        return seen

    def children(self, metric: str, type: str | None = None) -> list[str]:
        """Return unique child metrics pointed to by ``metric``."""
        if metric not in self.graph:
            return []
        seen: list[str] = []
        for _, v, data in self.graph.out_edges(metric, data=True):
            edge: GraphEdge = data["edge"]
            if type is not None and edge.type != type:
                continue
            if v not in seen:
                seen.append(v)
        return seen

    def causal_view(self) -> nx.DiGraph:
        """Plain DiGraph with only formula and possible_cause edges (no self-loops)."""
        view = nx.DiGraph()
        for node in self.graph.nodes:
            view.add_node(node)
        for u, v, data in self.graph.edges(data=True):
            edge: GraphEdge = data["edge"]
            if u == v or edge.type not in {"formula", "possible_cause"}:
                continue
            view.add_edge(u, v, edge=edge)
        return view

    def apply_result(self, edge: GraphEdge, outcome: str, evidence: str = "") -> None:
        """Update status and evidence for every arrow belonging to the same edge."""
        if outcome == "skipped":
            return
        if outcome == "pass":
            new_status = "confirmed"
        elif outcome == "fail":
            new_status = "rejected"
        else:
            raise ValueError(f"Unknown check outcome: {outcome!r}")

        for _, _, _, data in self.graph.edges(keys=True, data=True):
            stored: GraphEdge = data["edge"]
            if stored.to_metric == edge.to_metric and stored.key == edge.key:
                stored.status = new_status
                if evidence:
                    stored.evidence = evidence

    def save(self, yaml_path: str | Path) -> None:
        """Save edges to YAML, deduplicating multi-input formula arrows."""
        seen: set[tuple[str, str, str]] = set()
        serialized: list[dict] = []
        for edge in self.edges():
            dedup_id = (
                ("", edge.to_metric, edge.key)
                if edge.type == "formula"
                else (edge.from_metric, edge.to_metric, edge.key)
            )
            if dedup_id in seen:
                continue
            seen.add(dedup_id)
            serialized.append(edge.to_dict())

        path = Path(yaml_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(yaml.safe_dump(serialized, sort_keys=False), encoding="utf-8")

    @classmethod
    def load(cls, yaml_path: str | Path) -> MetricGraph:
        """Load edges from a YAML file (list of edge dicts or {'edges': [...]})."""
        raw = yaml.safe_load(Path(yaml_path).read_text(encoding="utf-8"))
        if raw is None:
            items: list[dict] = []
        elif isinstance(raw, dict) and "edges" in raw:
            items = raw["edges"]
        elif isinstance(raw, list):
            items = raw
        else:
            raise ValueError(f"Unexpected YAML structure in {yaml_path}")

        mg = cls()
        for item in items:
            mg.add_edge(GraphEdge.from_dict(item))
        return mg
