"""L4 Monitoring priorities and watchlist selection.

Importance is computed from shortest-path distance to ``root_metrics`` in the
reversed ``causal_view`` graph: ``1.0 / (1.0 + dist)`` (``1.0`` for roots,
``0.5`` at distance 1, ``0.1`` if unreachable). Works on empty or disconnected graphs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

import networkx as nx
import numpy as np

from graph.metric_graph import MetricGraph
from graph.registry import MetricDefinition

HistoryFn = Callable[[str], Sequence[float]]


@dataclass(frozen=True)
class MetricPriority:
    """Priority breakdown for a single monitored metric."""

    metric: str
    score: float
    importance: float
    business_weight: float
    noisiness: float
    cost: float
    reason: str


def _business_weight(mdef: MetricDefinition | Mapping[str, Any]) -> float:
    if isinstance(mdef, MetricDefinition):
        return float(mdef.business_weight)
    raw = mdef.get("business_weight", 0.5)
    return 0.5 if raw is None else float(raw)


def _compute_importance(
    graph: MetricGraph,
    metrics: list[str],
    root_metrics: tuple[str, ...],
) -> dict[str, float]:
    """Shortest-path distance to ``root_metrics`` on reversed ``causal_view``."""
    if not metrics:
        return {}
    causal = graph.causal_view()
    if causal.number_of_edges() == 0:
        return {m: (1.0 if m in root_metrics else 0.2) for m in metrics}

    rev = causal.reverse(copy=True)
    present_roots = [r for r in root_metrics if r in rev]
    if not present_roots:
        return {m: (1.0 if m in root_metrics else 0.2) for m in metrics}

    dists = nx.multi_source_dijkstra_path_length(rev, present_roots)
    return {
        m: (1.0 / (1.0 + dists[m]) if m in dists else (1.0 if m in root_metrics else 0.1))
        for m in metrics
    }


def _compute_noisiness(values: Sequence[float]) -> float:
    """Robust volatility: ``MAD / |median|`` over finite history, 0.0 if zero median."""
    arr = np.asarray(list(values), dtype=float)
    arr = arr[np.isfinite(arr)]
    if arr.size == 0:
        return 0.0
    med = float(np.median(arr))
    if abs(med) < 1e-12:
        return 0.0
    mad = float(np.median(np.abs(arr - med)))
    return mad / abs(med)


def score_metrics(
    graph: MetricGraph,
    metric_defs: Mapping[str, MetricDefinition | Mapping[str, Any]],
    history_fn: HistoryFn,
    root_metrics: tuple[str, ...] = ("revenue", "orders"),
    cost_per_metric: float = 1.0,
    group_by_size: int = 1,
) -> list[MetricPriority]:
    """Score and rank metrics by causal importance, business weight, noisiness, and cost."""
    if cost_per_metric <= 0:
        raise ValueError(f"cost_per_metric must be positive, got {cost_per_metric}")
    if group_by_size < 1:
        raise ValueError(f"group_by_size must be >= 1, got {group_by_size}")

    metrics = sorted(metric_defs.keys())
    importances = _compute_importance(graph, metrics, root_metrics)
    cost = float(cost_per_metric) * int(group_by_size)

    scored: list[MetricPriority] = []
    for name in metrics:
        imp = importances.get(name, 0.0)
        bw = _business_weight(metric_defs[name])
        noisiness = _compute_noisiness(history_fn(name))
        noise_factor = 1.0 / (1.0 + 0.25 * noisiness)
        score = ((0.6 * imp + 0.4 * bw) * noise_factor) / cost

        notes = [f"importance={imp:.2f}", f"weight={bw:.2f}", f"noisiness={noisiness:.2f}"]
        if name in root_metrics:
            notes.append("root KPI")
        if noisiness > 0.2:
            notes.append("high volatility")
        scored.append(
            MetricPriority(
                metric=name,
                score=round(score, 6),
                importance=round(imp, 4),
                business_weight=round(bw, 4),
                noisiness=round(noisiness, 4),
                cost=cost,
                reason="; ".join(notes),
            )
        )

    scored.sort(key=lambda item: (-item.score, item.metric))
    return scored


def select_watchlist(
    priorities: Sequence[MetricPriority],
    budget: float,
    segment_candidates: Sequence[Mapping[str, str]],
    root_metrics: tuple[str, ...] = ("revenue", "orders"),
    cost_per_check: float = 1.0,
) -> list[tuple[str, dict[str, str]]]:
    """Select deterministic ``(metric, segment)`` checks within ``budget``."""
    if budget < 0:
        raise ValueError(f"budget must be non-negative, got {budget}")
    if cost_per_check <= 0:
        raise ValueError(f"cost_per_check must be positive, got {cost_per_check}")

    watchlist: list[tuple[str, dict[str, str]]] = []
    seen: set[tuple[str, tuple[tuple[str, str], ...]]] = set()
    remaining = float(budget)

    def _try_add(metric: str, seg: Mapping[str, str]) -> None:
        nonlocal remaining
        seg_dict = dict(seg)
        key = (metric, tuple(sorted(seg_dict.items())))
        if key in seen or remaining + 1e-12 < cost_per_check:
            return
        seen.add(key)
        watchlist.append((metric, seg_dict))
        remaining -= cost_per_check

    # Always include root KPIs overall first.
    for root in root_metrics:
        _try_add(root, {})

    segments = [dict(s) for s in segment_candidates] or [{}]
    for item in priorities:
        for seg in segments:
            if remaining + 1e-12 < cost_per_check:
                return watchlist
            _try_add(item.metric, seg)

    return watchlist
