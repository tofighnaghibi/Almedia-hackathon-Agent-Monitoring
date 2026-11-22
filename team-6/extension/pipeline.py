"""The full L3 step: a new metric definition in, an extended graph out.

Connects candidate discovery (L3 A, ``extension.discovery``) to validation and
graph extension (L3 B, ``graph.extend``):

1. check the definition on the data (``MetricEngine.validate``)
2. discover candidate connections
3. register the metric, so the engine can compute it by name
4. make every registered metric known to the graph, so candidates that point
   at real metrics that are not graph nodes yet are not rejected as "unknown"
5. convert candidates to ``graph.extend.Candidate`` and run ``extend_graph``
   with the real engine, passing the metric's ``is_additive`` and
   ``weight_metric`` so ratio breakdowns can be checked

If validation or extension fails, the registry is left as it was.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional

from graph.bridge import checker_metric_defs, engine_compute_fn
from graph.engine import MetricEngine
from graph.extend import Candidate as ExtendCandidate
from graph.extend import ExtensionReport, MonitorRegistry, extend_graph
from graph.metric_graph import MetricGraph
from graph.registry import MetricDefinition, MetricDefinitionError

from .discovery import Candidate, DiscoveryReport, discover_connections


@dataclass
class AddMetricResult:
    metric: MetricDefinition
    discovery: DiscoveryReport
    extension: ExtensionReport

    @property
    def confirmed(self) -> list[dict[str, Any]]:
        return [e for e in self.extension.added_edges if e["status"] == "confirmed"]

    @property
    def uncertain(self) -> list[dict[str, Any]]:
        return [e for e in self.extension.added_edges if e["status"] == "uncertain"]


def to_extend_candidate(c: Candidate, new_metric: str) -> ExtendCandidate:
    """Discovery candidate -> the Candidate type ``graph.extend`` validates."""
    e = c.edge
    return ExtendCandidate(
        new_metric=new_metric,
        type=e.type,
        from_metric=e.from_metric,
        to_metric=e.to_metric,
        formula=e.formula,
        dimension=e.dimension,
        lag_hours=e.lag_hours,
        source=e.source,
        rationale=f"[{c.method}, score {c.score:.2f}] {c.evidence}",
    )


def add_new_metric(
    metric: MetricDefinition | dict[str, Any],
    engine: MetricEngine,
    graph: MetricGraph,
    start: Any = None,
    end: Any = None,
    monitor_registry: Optional[MonitorRegistry] = None,
    max_lag: int = 6,
) -> AddMetricResult:
    """Discover, validate and add a new metric to the registry and the graph."""
    defn = metric if isinstance(metric, MetricDefinition) else MetricDefinition.from_dict(metric)
    if defn.name in engine.registry:
        raise MetricDefinitionError(f"metric {defn.name!r} is already registered")

    discovery = discover_connections(defn, engine, graph, start, end, max_lag=max_lag)
    if not discovery.validation.ok:
        raise MetricDefinitionError(
            f"metric {defn.name!r} failed validation: " + "; ".join(discovery.validation.errors)
        )

    engine.registry.add(defn, engine=engine)
    try:
        for name in engine.registry.names():
            graph.graph.add_node(name)  # registered metrics are part of the business model
        metric_def = checker_metric_defs(engine.registry)[defn.name]
        report = extend_graph(
            graph,
            metric_def,
            [to_extend_candidate(c, defn.name) for c in discovery.candidates],
            engine_compute_fn(engine),
            start,
            end,
            monitor_registry=monitor_registry,
        )
    except Exception:
        engine.registry.remove(defn.name)
        raise
    return AddMetricResult(defn, discovery, report)
