"""L5 Investigation menu builder from real graph edges and available tools."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Literal

from graph.edges import GraphEdge
from graph.formulas import parse_formula
from graph.metric_graph import MetricGraph
from investigation.decompose import formulas_for

from agent.state import InvestigationState

MenuKind = Literal[
    "decompose_formula",
    "drill_dimension",
    "check_cause_edge",
    "check_events",
    "check_data_quality",
    "counterfactual",
]


@dataclass(frozen=True)
class MenuItem:
    """A single valid next investigation action."""

    number: int
    id: str
    kind: MenuKind
    args: dict[str, Any]
    description: str
    estimated_cost: float
    expected_gain: float


def action_signature(kind: str, args: dict[str, Any]) -> str:
    """Canonical deduplication key for an action + arguments."""
    return f"{kind}:{json.dumps(args, sort_keys=True)}"


def _unexplained(state: InvestigationState) -> float:
    return max(0.05, 1.0 - max(0.0, min(1.0, float(state.explained_share))))


def _uncaused(state: InvestigationState) -> float:
    """Share of the change no driver accounts for yet.

    Localizing the anomaly metric itself (``__self__``) says where the change
    is, not why, so it does not make decomposing the metric worthless. Without
    this, drilling down before decomposing (an order the model may choose)
    would push every gain under ``min_gain`` and end the investigation early.
    """
    parts = sum(v for k, v in state.explained_parts.items() if k != "__self__")
    return max(0.05, 1.0 - max(0.0, min(1.0, float(parts))))


def _below(tree: dict[str, dict[str, Any]], metric: str) -> list[str]:
    """Metrics under ``metric`` in the contribution tree (breadth first)."""
    out, frontier = [], [metric]
    while frontier:
        kids = [m for m, n in tree.items() if n.get("parent") in frontier and m not in out]
        out += kids
        frontier = kids
    return out


def build_menu(
    state: InvestigationState,
    graph: MetricGraph,
    available_tools: set[str] | None = None,
) -> list[MenuItem]:
    """Build deterministic menu items from real graph edges and available tools.

    ``expected_gain = unexplained_share * step_potential`` (never set by LLM).
    Never offers an already-completed ``(kind, args)``, never mixes ingredients
    of two different formulas for the same metric, and falls back to dimension
    drill-down only if the metric is not in ``graph``.
    """
    tools = available_tools or {
        "decompose_formula",
        "drill_dimension",
        "check_cause_edge",
        "check_events",
        "check_data_quality",
        "counterfactual",
    }
    done = set(state.completed_actions)
    anomaly = state.anomaly
    metric = str(anomaly["metric"])
    segment = dict(anomaly.get("segment") or {})
    unexplained = _unexplained(state)
    in_graph = metric in graph.graph

    raw_items: list[tuple[MenuKind, dict[str, Any], str, float, float]] = []

    # 1. Formula decomposition (only if metric is in graph and has a formula)
    if in_graph and "decompose_formula" in tools:
        f_edges = formulas_for(graph, metric)
        active_formula = state.active_formula_by_metric.get(metric)
        if active_formula:
            f_edges = [e for e in f_edges if e.formula == active_formula]
        elif f_edges:
            # Never mix ingredients of two formulas: pick top-ranked formula first.
            f_edges = [f_edges[0]]

        if f_edges:
            f_str = f_edges[0].formula or ""
            args = {"metric": metric, "segment": segment, "formula": f_str}
            if action_signature("decompose_formula", args) not in done:
                raw_items.append(
                    (
                        "decompose_formula",
                        args,
                        f"Decompose {metric} via {f_str}",
                        1.0,
                        max(unexplained, _uncaused(state)) * 0.70,
                    )
                )

    # 2. Dimension drill-down (works even when metric is not in graph)
    if "drill_dimension" in tools:
        target_metrics = [metric]
        for h in state.hypotheses:
            if (h.get("source") == "formula_component" and h.get("explained_share", 0.0) >= 0.2
                    and not h.get("is_symptom")):
                node = str(h["node"])
                if node not in target_metrics:
                    target_metrics.append(node)

        for tm in target_metrics:
            args = {"metric": tm, "segment": segment}
            if action_signature("drill_dimension", args) not in done:
                base_gain = unexplained * 0.65 if tm == metric else max(unexplained, _uncaused(state)) * 0.75
                gain = max(0.20, base_gain) if not any(h.get("localized") for h in state.hypotheses) else base_gain
                raw_items.append(
                    (
                        "drill_dimension",
                        args,
                        f"Drill down {tm} by dimensions to localize segment",
                        1.2,
                        gain,
                    )
                )

    # 3. Check cause edges pointing to metric (only if in graph; respect active formula)
    if in_graph and "check_cause_edge" in tools:
        active_formula = state.active_formula_by_metric.get(metric)
        allowed_formula_inputs: set[str] | None = None
        if active_formula:
            _, inputs, _ = parse_formula(active_formula)
            allowed_formula_inputs = set(inputs)

        seen_causes: set[tuple[str, str]] = set()
        targets = [(metric, 1.0)] + [
            (m, float(n.get("share", 0.0))) for m, n in state.tree.items() if float(n.get("share", 0.0)) >= 0.2
        ]
        # a leaf rate (add_to_cart_rate) also inherits the possible causes of what it feeds
        # into (out_of_stock_rate -> cart_adds, where cart_adds = product_clicks * add_to_cart_rate)
        for e in graph.edges("formula"):
            if e.from_metric == metric and e.to_metric != metric and (e.to_metric, 1.0) not in targets:
                targets.append((e.to_metric, 1.0))
        seg_of = {str(h["node"]): dict(h.get("segment") or segment) for h in state.hypotheses
                  if h.get("source") == "formula_component" and h.get("localized")}
        for target, weight in targets:
            if target not in graph.graph:
                continue
            for u, _, data in graph.graph.in_edges(target, data=True):
                edge: GraphEdge = data["edge"]
                if edge.status == "rejected" or edge.type != "possible_cause":
                    continue
                if target == metric and allowed_formula_inputs is not None and u in allowed_formula_inputs:
                    continue
                if (u, target) in seen_causes:
                    continue
                seen_causes.add((u, target))
                # check the cause where the change was localized, if it was
                where = next((seg_of[m] for m in [target, *_below(state.tree, target)] if m in seg_of), segment)
                args = {"from_metric": u, "to_metric": target, "segment": where, "lag_hours": edge.lag_hours}
                if action_signature("check_cause_edge", args) not in done:
                    raw_items.append(
                        (
                            "check_cause_edge",
                            args,
                            f"Check upstream cause {u} -> {target} in {where or 'all'} (lag={edge.lag_hours}h)",
                            1.0,
                            unexplained * 0.50 * float(edge.confidence) * max(weight, 0.2) + 0.06,
                        )
                    )

    # 4. Check events (including 2h lookback before window start)
    if "check_events" in tools:
        args = {"metric": metric, "segment": segment, "lookback_hours": 2}
        if action_signature("check_events", args) not in done:
            # while a leading hypothesis still lacks timing evidence, checking events stays worth
            # doing, however much of the change is already localized (where is not why)
            timing_open = any("timing_aligned" in (h.get("missing_conditions") or [])
                              for h in state.hypotheses[:3] if h.get("status") != "rejected")
            no_timing = not any(h.get("timing_aligned") for h in state.hypotheses)
            ev_gain = max(0.15, unexplained * 0.45) if (no_timing or timing_open) else unexplained * 0.45
            raw_items.append(
                (
                    "check_events",
                    args,
                    f"Check unplanned events/releases for {metric} (2h lookback)",
                    0.5,
                    ev_gain,
                )
            )

    # 5. Counterfactual check on localized suspect slice / top hypothesis
    if "counterfactual" in tools:
        for h in state.hypotheses:
            if h.get("status") == "rejected" or not h.get("localized"):
                continue
            h_seg = dict(h.get("segment") or segment)
            if not h_seg:
                continue
            cf_metric = metric
            if h.get("source") == "formula_component" and h.get("node") and h.get("explained_share", 0.0) < 0.95:
                cf_metric = str(h["node"])  # another cause may also move the anomaly metric
            args = {"metric": cf_metric, "suspect_segment": h_seg}
            if action_signature("counterfactual", args) not in done:
                cf_gain = max(0.25, float(state.explained_share) * 0.50, unexplained * 0.60)
                raw_items.append(
                    (
                        "counterfactual",
                        args,
                        f"Run counterfactual removing {h_seg} for {metric}",
                        1.0,
                        cf_gain,
                    )
                )
                break

    items: list[MenuItem] = []
    for idx, (kind, args, desc, cost, gain) in enumerate(raw_items, start=1):
        items.append(
            MenuItem(
                number=idx,
                id=f"{kind}_{idx}",
                kind=kind,
                args=args,
                description=desc,
                estimated_cost=round(float(cost), 4),
                expected_gain=round(float(gain), 6),
            )
        )
    return items
