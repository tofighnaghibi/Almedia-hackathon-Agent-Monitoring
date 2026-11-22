"""L3: extending the metric graph with new metrics.

Person A (discovery.py): find candidate connections for a new metric.
Person B (graph/extend.py): validate candidates, score them, and add them to the graph.
pipeline.py connects the two: add_new_metric() runs the whole step.
"""

from .discovery import Candidate, DiscoveryReport, discover_connections
from .pipeline import AddMetricResult, add_new_metric, to_extend_candidate

__all__ = [
    "AddMetricResult",
    "Candidate",
    "DiscoveryReport",
    "add_new_metric",
    "discover_connections",
    "to_extend_candidate",
]
