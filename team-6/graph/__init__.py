"""L2 metric graph.

Person A: metric registry and calculation engine (registry.py, engine.py).
Person B: typed edges, metric graph, formula/breakdown checkers, tree view
(edges.py, metric_graph.py, formulas.py, checkers.py, tree.py).
bridge.py connects the two.
"""

from .bridge import checker_metric_defs, engine_compute_fn, run_checks
from .checkers import CheckResult, check_breakdown, check_formula, check_graph
from .edges import GraphEdge
from .engine import MetricEngine, ValidationReport
from .formulas import eval_formula, parse_formula
from .metric_graph import MetricGraph
from .registry import (
    ALLOWED_UNITS,
    DEFAULT_METRICS_FILE,
    MetricDefinition,
    MetricDefinitionError,
    MetricRegistry,
)
from .tree import build_tree, render_text

__all__ = [
    "ALLOWED_UNITS",
    "CheckResult",
    "DEFAULT_METRICS_FILE",
    "GraphEdge",
    "MetricDefinition",
    "MetricDefinitionError",
    "MetricEngine",
    "MetricGraph",
    "MetricRegistry",
    "ValidationReport",
    "build_tree",
    "check_breakdown",
    "check_formula",
    "check_graph",
    "checker_metric_defs",
    "engine_compute_fn",
    "eval_formula",
    "parse_formula",
    "render_text",
    "run_checks",
]
