"""What a moved rate cost: the outcome as observed against the outcome with that rate back at its
expected value and everything else as observed.

The outcome is recomputed through the formulas of the graph (decompositions down to the atomic
factors), so the estimate follows every path: a lower payment success rate lowers orders, GMV,
fees and discounts together, and the contribution changes by fees minus discounts. With several
moved rates each one is restored alone for its share and all together for the total; the exact
split between interacting factors belongs to the investigator (README 3.5).
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from brain.evaluator import Evaluator, NotComputable
from brain.graph import MetricGraph


def tree_metrics(graph: MetricGraph, outcome: str) -> list[str]:
    """Every metric reached from the outcome through the formulas the investigator walks."""
    seen: list[str] = []

    def visit(m: str) -> None:
        if m in seen:
            return
        seen.append(m)
        f = graph.specs[m].investigative_formula
        for n in (f.names if f is not None else []):
            visit(n)

    visit(outcome)
    return seen


def recompute(graph: MetricGraph, metric: str, observed: dict, overrides: dict) -> float:
    """The metric from the atomic factors below it, some of them replaced by other values."""
    def value(m: str) -> float:
        if m in overrides:
            return overrides[m]
        f = graph.specs[m].investigative_formula
        if f is None:
            return observed[m]
        return float(f.evaluate({n: value(n) for n in f.names}))
    return value(metric)


@dataclass
class Impact:
    outcome: str
    observed: float
    expected: float                      # with every moved rate restored
    values: dict = field(default_factory=dict)       # observed value of every metric of the tree
    restored: dict = field(default_factory=dict)     # outcome of other metrics with the rates restored
    by_factor: dict = field(default_factory=dict)    # factor -> outcome change it explains alone

    @property
    def delta(self) -> float:
        return self.observed - self.expected


def estimate(graph: MetricGraph, evaluator: Evaluator, outcome: str, segment: dict, start, end,
             expected_rates: dict[str, float], also: tuple[str, ...] = ()) -> Impact | None:
    """Impact on `outcome` in a segment and window of the given rates being off their expected
    values. Rates that are not on the outcome's formula tree are ignored; None if none is on it
    or the outcome cannot be computed there."""
    tree = tree_metrics(graph, outcome)
    on_tree = {m: r for m, r in expected_rates.items() if m in tree and r is not None and math.isfinite(r)}
    if not on_tree:
        return None
    try:
        frame = evaluator.frame(tree, start, end, segment)
    except NotComputable:
        return None
    if frame.empty:
        return None
    observed = {m: float(frame[m].iloc[0]) for m in tree}
    actual = recompute(graph, outcome, observed, {})
    if not math.isfinite(actual):
        return None
    out = Impact(outcome=outcome, observed=actual, expected=recompute(graph, outcome, observed, on_tree),
                 values=observed)
    for m in also:
        if m in tree:
            out.restored[m] = recompute(graph, m, observed, on_tree)
    for m, r in on_tree.items():
        out.by_factor[m] = actual - recompute(graph, outcome, observed, {m: r})
    return out
