"""Pure structural rules for graph edges (v3.2 §3.1, §3.3 step 5; CP-008).

Per-kind endpoint/layer rules for all 13 edge kinds, the observation firewall (I07) and the
instantaneous-cycle check. Nothing here touches storage. Callers pass node lookups in.
"""
from __future__ import annotations

from typing import Callable, Iterable

from ..contracts.graph import GraphEdge, GraphNode

V, E, I, S, M, L, C = "VARIABLE", "ENTITY", "INTERVENTION", "STUDY", "OBSERVATION_MECHANISM", "LATENT", "EFFECT_CLAIM"

# (allowed source node kinds, allowed target node kinds, allowed layers)
ENDPOINTS: dict[str, tuple[frozenset, frozenset, frozenset]] = {
    "EXACT_ARITHMETIC": (frozenset({V}), frozenset({V}), frozenset({"DOMAIN"})),
    "AGGREGATION_COMPOSITION": (frozenset({V}), frozenset({V}), frozenset({"DOMAIN"})),
    "DIMENSIONAL_DECOMPOSITION": (frozenset({V}), frozenset({V}), frozenset({"DOMAIN"})),
    "ENTITY_RELATIONSHIP": (frozenset({E}), frozenset({E}), frozenset({"DOMAIN"})),
    "TEMPORAL_WINDOW": (frozenset({V}), frozenset({V}), frozenset({"DOMAIN"})),
    "TEMPORAL_LAG": (frozenset({V, I, L, M}), frozenset({V}), frozenset({"DOMAIN", "OBSERVATION"})),
    "TEMPORAL_FEEDBACK": (frozenset({V}), frozenset({V}), frozenset({"DOMAIN"})),
    "OBSERVATION_EFFECT": (frozenset({M}), frozenset({V}), frozenset({"OBSERVATION"})),
    "OBSERVATIONAL_ASSOCIATION": (frozenset({V}), frozenset({V}), frozenset({"DOMAIN"})),
    "CAUSAL_HYPOTHESIS": (frozenset({V, I, L}), frozenset({V}), frozenset({"DOMAIN"})),
    "CAUSALLY_IDENTIFIED": (frozenset({V, I}), frozenset({V}), frozenset({"DOMAIN"})),
    "LATENT_CONFOUNDING": (frozenset({V, L}), frozenset({V}), frozenset({"DOMAIN", "OBSERVATION"})),
    "UNKNOWN_RELATIONSHIP": (frozenset({V, I, L}), frozenset({V}), frozenset({"DOMAIN", "OBSERVATION"})),
}
METRIC_ONLY_KINDS = frozenset({"EXACT_ARITHMETIC", "AGGREGATION_COMPOSITION", "DIMENSIONAL_DECOMPOSITION",
                               "TEMPORAL_WINDOW"})
# Edges that assert same-instant dependence: together they must form a DAG (CP-008).
INSTANTANEOUS_KINDS = frozenset({"EXACT_ARITHMETIC", "AGGREGATION_COMPOSITION", "DIMENSIONAL_DECOMPOSITION",
                                 "CAUSAL_HYPOTHESIS", "CAUSALLY_IDENTIFIED"})


def is_instantaneous(edge: GraphEdge) -> bool:
    if edge.kind in INSTANTANEOUS_KINDS:
        return True
    return edge.kind == "TEMPORAL_LAG" and edge.lag is not None and edge.lag.min_s == 0


def endpoint_problems(edge: GraphEdge, source: GraphNode | None, target: GraphNode | None,
                      source_kind_of_variable: str | None = None, target_kind_of_variable: str | None = None
                      ) -> list[str]:
    """Problems with an edge given its endpoint nodes. *_kind_of_variable is the VariableDefinition.kind, if any."""
    if source is None or target is None:
        missing = [n for n, node in ((edge.source, source), (edge.target, target)) if node is None]
        return [f"UNKNOWN_ENDPOINT: {missing}"]
    src_ok, tgt_ok, layers = ENDPOINTS[edge.kind]
    out = []
    if source.kind not in src_ok:
        out.append(f"INVALID_SOURCE: {edge.kind} cannot start at a {source.kind} node")
    if target.kind not in tgt_ok:
        out.append(f"INVALID_TARGET: {edge.kind} cannot end at a {target.kind} node")
    if edge.layer not in layers:
        out.append(f"INVALID_LAYER: {edge.kind} cannot live in the {edge.layer} layer")
    if source.layer == "OBSERVATION" and edge.kind not in {"OBSERVATION_EFFECT", "TEMPORAL_LAG",
                                                            "LATENT_CONFOUNDING", "UNKNOWN_RELATIONSHIP"}:
        out.append(f"OBSERVATION_FIREWALL: an observation-layer node cannot be the source of {edge.kind} (I07)")
    if edge.kind in METRIC_ONLY_KINDS:
        for side, kind in (("source", source_kind_of_variable), ("target", target_kind_of_variable)):
            if kind != "metric":
                out.append(f"NOT_A_METRIC: {edge.kind} {side} is a {kind or 'non-variable'} (metric-only edge kind)")
    return out


def find_instantaneous_cycle(new_edges: Iterable[GraphEdge],
                             existing_out: Callable[[str], Iterable[str]]) -> list[str] | None:
    """Return a cycle path if adding new instantaneous edges closes a zero-lag loop, else None.

    `existing_out(node)` yields targets of the already-admitted instantaneous edges from node.
    """
    pending: dict[str, set[str]] = {}
    for e in new_edges:
        if is_instantaneous(e):
            pending.setdefault(e.source, set()).add(e.target)

    def succ(n: str) -> Iterable[str]:
        yield from existing_out(n)
        yield from pending.get(n, ())

    for src, targets in pending.items():
        for t in targets:
            stack, seen = [(t, [src, t])], {t}
            while stack:
                node, path = stack.pop()
                if node == src:
                    return path
                for nxt in succ(node):
                    if nxt not in seen:
                        seen.add(nxt)
                        stack.append((nxt, path + [nxt]))
    return None
