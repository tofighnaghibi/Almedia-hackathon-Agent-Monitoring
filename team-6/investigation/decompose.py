"""How much each component contributed to a change, along formula edges (L5, Person A).

For a metric ``y = f(a, b, ...)`` (a formula edge in the graph), the change
``f(actual) - f(baseline)`` is split with **Shapley values**. Each input's
contribution is its average effect when switched from baseline to actual,
averaged over every order of switching. The contributions add up exactly to
the total change, for any formula: products, ratios or sums. For ``y = a * b``
they also match the intuitive split (both inputs fall -> both share the drop).

The walk starts at the anomalous metric and recursively expands every input
that carries at least ``min_share`` of the change and has a formula of its own.
Shares multiply down the tree, so every node's ``share`` is its part of the
*original* change. Leaves with a large share are the drivers.

Before a formula is used it is checked on the data (actual and baseline values
must satisfy it); a formula that doesn't hold is skipped and noted.
"""

from __future__ import annotations

import itertools
import math
from dataclasses import dataclass, field
from typing import Any, Optional

import numpy as np

from graph.edges import GraphEdge
from graph.engine import MetricEngine
from graph.formulas import eval_formula, parse_formula
from graph.metric_graph import MetricGraph

from .compare import DEFAULT_WEEKS, Window, compare_total

IDENTITY_TOL = 1e-6


@dataclass
class ContributionNode:
    metric: str
    actual: float
    baseline: float
    share: float  # part of the root change carried by this node (root = 1.0)
    local_share: float = 1.0  # part of the parent's change
    contribution: float = 0.0  # in the parent's units (Shapley value)
    formula: Optional[str] = None  # how this node was decomposed, if it was
    alternatives: list[str] = field(default_factory=list)  # other formulas that also hold
    children: list["ContributionNode"] = field(default_factory=list)
    note: str = ""

    @property
    def change(self) -> float:
        return self.actual - self.baseline

    @property
    def rel_change(self) -> float:
        return self.actual / self.baseline - 1 if self.baseline else float("nan")

    def walk(self):
        yield self
        for c in self.children:
            yield from c.walk()

    def drivers(self, min_share: float = 0.2) -> list["ContributionNode"]:
        """Leaves (not decomposed further) that carry at least ``min_share``."""
        leaves = [n for n in self.walk() if not n.children]
        return sorted([n for n in leaves if abs(n.share) >= min_share], key=lambda n: -abs(n.share))

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric, "actual": _f(self.actual), "baseline": _f(self.baseline),
            "rel_change_pct": _f(100 * self.rel_change), "share_pct": _f(100 * self.share),
            "formula": self.formula, "alternatives": self.alternatives, "note": self.note,
            "children": [c.to_dict() for c in self.children],
        }


def shapley(formula: str, baseline: dict[str, float], actual: dict[str, float]) -> dict[str, float]:
    """Shapley contribution of each input of ``formula`` to f(actual) - f(baseline)."""
    _, inputs, code = parse_formula(formula)
    n = len(inputs)
    cache: dict[frozenset, float] = {}

    def f(switched: frozenset) -> float:
        if switched not in cache:
            env = {k: (actual[k] if k in switched else baseline[k]) for k in inputs}
            with np.errstate(all="ignore"):
                cache[switched] = float(eval_formula(code, env))
        return cache[switched]

    out = {}
    for i in inputs:
        others = [k for k in inputs if k != i]
        total = 0.0
        for r in range(len(others) + 1):
            weight = math.factorial(r) * math.factorial(n - r - 1) / math.factorial(n)
            for subset in itertools.combinations(others, r):
                s = frozenset(subset)
                total += weight * (f(s | {i}) - f(s))
        out[i] = total
    return out


def formulas_for(graph: MetricGraph, metric: str) -> list[GraphEdge]:
    """Formula edges defining ``metric`` (one per formula), seed edges first."""
    seen, out = set(), []
    for e in graph.edges("formula"):
        if e.to_metric == metric and e.status != "rejected" and e.formula not in seen:
            seen.add(e.formula)
            out.append(e)
    return sorted(out, key=lambda e: (e.source != "seed", e.status != "confirmed"))


def decompose(
    engine: MetricEngine,
    graph: MetricGraph,
    metric: str,
    window: Window,
    segment: Optional[dict] = None,
    weeks: int = DEFAULT_WEEKS,
    min_share: float = 0.1,
    max_depth: int = 10,
) -> ContributionNode:
    """Contribution tree of ``metric``'s change in ``segment`` over ``window``."""
    segment = dict(segment or {})
    cache: dict[str, tuple[float, float]] = {}

    def values(m: str) -> tuple[float, float]:
        if m not in cache:
            dims = engine.registry.get(m).dimensions
            if not all(d in dims for d in segment):
                cache[m] = (float("nan"), float("nan"))
            else:
                cache[m] = compare_total(engine, m, window, segment, weeks)
        return cache[m]

    a, b = values(metric)
    root = ContributionNode(metric, a, b, share=1.0)
    _expand(root, engine, graph, values, min_share, max_depth, {metric})
    return root


def _expand(node, engine, graph, values, min_share, depth, visited) -> None:
    if depth <= 0 or not np.isfinite(node.change) or node.change == 0:
        return
    usable, notes = [], []
    for edge in formulas_for(graph, node.metric):
        _, inputs, code = parse_formula(edge.formula)
        if any(i in visited or i not in engine.registry for i in inputs):
            continue
        vals = {i: values(i) for i in inputs}
        if not all(np.isfinite(v).all() for v in vals.values()):
            continue
        act, base = {i: v[0] for i, v in vals.items()}, {i: v[1] for i, v in vals.items()}
        with np.errstate(all="ignore"):
            fa, fb = float(eval_formula(code, act)), float(eval_formula(code, base))
        if not (_close(fa, node.actual) and _close(fb, node.baseline)):
            notes.append(f"{edge.formula} does not hold on this data")
            continue
        usable.append((edge, inputs, act, base))
    if not usable:
        node.note = "; ".join(notes)
        return

    # Prefer the formula whose biggest contributor carries the most: it says the
    # most about where the change comes from. Seed formulas win ties.
    def concentration(item) -> float:
        edge, inputs, act, base = item
        phi = shapley(edge.formula, base, act)
        return max(abs(v) for v in phi.values()) / abs(node.change)

    usable.sort(key=lambda it: -round(concentration(it), 3))
    edge, inputs, act, base = usable[0]
    node.formula = edge.formula
    node.alternatives = [u[0].formula for u in usable[1:]]
    node.note = "; ".join(notes)
    phi = shapley(edge.formula, base, act)
    for i in sorted(inputs, key=lambda k: -abs(phi[k])):
        local = phi[i] / node.change
        child = ContributionNode(i, act[i], base[i], share=node.share * local, local_share=local,
                                 contribution=phi[i])
        node.children.append(child)
        if abs(child.share) >= min_share:
            _expand(child, engine, graph, values, min_share, depth - 1, visited | {i})


def _close(x: float, y: float) -> bool:
    return abs(x - y) <= IDENTITY_TOL * max(1.0, abs(y))


def _f(x: float) -> Optional[float]:
    return None if x is None or not np.isfinite(x) else round(float(x), 6)
