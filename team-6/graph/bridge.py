"""Connects the metric engine (L2 A) to the graph checkers (L2 B).

The checkers call ``compute(metric, start, end, group_by=None)`` and expect a
DataFrame with ``timestamp``, the ``group_by`` columns and ``value``. The
engine's ``compute`` uses ``by=`` / ``grain=`` and calls the time column
``period``; ``engine_compute_fn`` translates between the two.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import pandas as pd

from .checkers import CheckResult, check_graph
from .engine import MetricEngine
from .metric_graph import MetricGraph
from .registry import MetricRegistry

_DENOMINATOR = re.compile(r"NULLIF\(\s*(SUM\(.+?\))\s*,\s*0\s*\)\s*$", re.IGNORECASE)


def engine_compute_fn(
    engine: MetricEngine, grain: str = "hour", segment: Optional[dict] = None
) -> Callable[..., pd.DataFrame]:
    """A ``compute`` function in the signature the checkers expect."""

    def compute(metric: str, start: Any, end: Any, group_by: Optional[list[str]] = None) -> pd.DataFrame:
        df = engine.compute(metric, start, end, segment=segment, by=list(group_by or []), grain=grain)
        return df.rename(columns={"period": "timestamp"})

    return compute


def weight_metric_for(metric: str, registry: MetricRegistry) -> Optional[str]:
    """For a ratio-of-sums metric, the registered metric that is its denominator.

    ``payment_success_rate = SUM(orders) / NULLIF(SUM(payment_attempts), 0)``
    -> ``payment_attempts``. Slices of a ratio recombine to the total when
    weighted by the denominator.
    """
    m = _DENOMINATOR.search(registry.get(metric).calculation)
    if not m:
        return None
    denominator = re.sub(r"\s+", "", m.group(1)).lower()
    for other in registry:
        if other.name != metric and re.sub(r"\s+", "", other.calculation).lower() == denominator:
            return other.name
    return None


def checker_metric_defs(registry: MetricRegistry) -> dict[str, dict[str, Any]]:
    """Metric definitions as plain dicts for the checkers, plus ``is_additive``
    and, for non-additive metrics, a ``weight_metric`` derived from the calculation."""
    defs = {}
    for m in registry:
        d = m.to_dict()
        d["is_additive"] = m.is_additive
        if not m.is_additive:
            w = weight_metric_for(m.name, registry)
            if w:
                d["weight_metric"] = w
        defs[m.name] = d
    return defs


@dataclass
class GraphCheckReport:
    results: dict[tuple[str, str], CheckResult]
    unknown_metrics: list[str] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        out = {"pass": 0, "fail": 0, "skipped": 0}
        for r in self.results.values():
            out[r.outcome] += 1
        return out

    @property
    def ok(self) -> bool:
        return not self.unknown_metrics and self.counts["fail"] == 0


def graph_metrics(graph: MetricGraph) -> list[str]:
    return sorted(graph.graph.nodes)


def run_checks(
    engine: MetricEngine,
    graph: MetricGraph,
    start: Any = None,
    end: Any = None,
    rel_tol: float = 1e-4,
) -> GraphCheckReport:
    """Check every formula and breakdown edge on the engine's data.

    Edges whose metrics are not in the registry are reported and the check
    stops there, because nothing about them can be computed.
    """
    unknown = [m for m in graph_metrics(graph) if m not in engine.registry]
    if unknown:
        return GraphCheckReport({}, unknown)
    results = check_graph(
        graph,
        checker_metric_defs(engine.registry),
        engine_compute_fn(engine),
        start,
        end,
        rel_tol=rel_tol,
    )
    return GraphCheckReport(results)
