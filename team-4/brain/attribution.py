"""How much of an outcome's change each atomic factor explains, in the outcome's own unit.

Through the formulas of the graph an outcome is a sum of signed products of atomic factors:

    contribution = sessions x (six funnel rates) x aov x take_rate
                 - sessions x (six funnel rates) x aov x discount_rate
                 + sessions x ad_load x ad_ctr x ad_cpc

Two states are compared: the factors as observed and the factors as expected. For a product T that
moves from T0 to T1, the part due to factor x is L(T1, T0) * ln(x1 / x0), with L the logarithmic
mean (logarithmic mean Divisia index, Ang 2005). The parts add up to T1 - T0 exactly, leave no
interaction term over, and do not depend on the order in which factors are looked at. A factor in
several products (the payment success rate is in fees and in discounts) collects from each, so a
change is never booked along one path only.

Where a logarithm is not defined (a factor at zero, a sign change) or a formula is neither a sum nor
a product, the Shapley value over the changed factors is used instead: also exact, by evaluating the
formulas for every subset of changed factors.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field
from itertools import combinations

from brain.graph import MetricGraph
from brain.impact import recompute, tree_metrics

MAX_SHAPLEY_FACTORS = 14    # 2^14 evaluations of the formulas; more changed factors than that are not expected


@dataclass
class Attribution:
    outcome: str
    actual: float
    expected: float
    by_factor: dict = field(default_factory=dict)   # atomic factor -> part of (actual - expected) it explains
    method: str = "lmdi"

    @property
    def delta(self) -> float:
        return self.actual - self.expected


def leaves(graph: MetricGraph, outcome: str) -> list[str]:
    """The atomic factors under an outcome: where the formulas the investigator walks end."""
    return [m for m in tree_metrics(graph, outcome) if graph.specs[m].investigative_formula is None]


def expand(graph: MetricGraph, metric: str) -> list[tuple[int, tuple[str, ...]]] | None:
    """The metric as signed products of atomic factors, or None when a formula on the way is
    neither a sum nor a product."""
    f = graph.specs[metric].investigative_formula
    if f is None:
        return [(1, (metric,))]
    if f.kind == "name":
        return expand(graph, f.names[0])
    if f.kind == "sum":
        out = []
        for sign, name in f.terms():
            sub = expand(graph, name)
            if sub is None:
                return None
            out += [(sign * s, factors) for s, factors in sub]
        return out
    if f.kind == "product":
        out = [(1, ())]
        for name in f.factors():
            sub = expand(graph, name)
            if sub is None:
                return None
            out = [(s1 * s2, f1 + f2) for s1, f1 in out for s2, f2 in sub]
        return out
    return None


def _logmean(a: float, b: float) -> float:
    return a if math.isclose(a, b, rel_tol=1e-15, abs_tol=0.0) else (a - b) / (math.log(a) - math.log(b))


def _lmdi(terms, actual: dict, expected: dict, names: list[str]) -> dict:
    out = dict.fromkeys(names, 0.0)
    for sign, factors in terms:
        t1 = math.prod(actual[x] for x in factors)
        t0 = math.prod(expected[x] for x in factors)
        weight = sign * _logmean(t1, t0)
        for x in factors:
            if actual[x] != expected[x]:
                out[x] += weight * math.log(actual[x] / expected[x])
    return out


def _shapley(graph: MetricGraph, outcome: str, actual: dict, expected: dict, names: list[str]) -> dict:
    moved = [x for x in names if actual[x] != expected[x]]
    out = dict.fromkeys(names, 0.0)
    n = len(moved)
    if n == 0:
        return out
    if n > MAX_SHAPLEY_FACTORS:
        raise ValueError(f"{n} changed factors are too many for an exact Shapley split")
    value: dict[frozenset, float] = {}
    for size in range(n + 1):
        for subset in combinations(moved, size):
            state = dict(expected)
            state.update({x: actual[x] for x in subset})
            value[frozenset(subset)] = recompute(graph, outcome, state, {})
    for x in moved:
        others = [m for m in moved if m != x]
        for size in range(n):
            weight = math.factorial(size) * math.factorial(n - size - 1) / math.factorial(n)
            for subset in combinations(others, size):
                s = frozenset(subset)
                out[x] += weight * (value[s | {x}] - value[s])
    return out


def attribute(graph: MetricGraph, outcome: str, actual: dict, expected: dict, method: str | None = None) -> Attribution | None:
    """Split outcome(actual) - outcome(expected) over the atomic factors. `actual` and `expected` hold a
    value for every atomic factor under the outcome. None when a value is missing."""
    names = leaves(graph, outcome)
    if any(not math.isfinite(float(state.get(x, math.nan))) for state in (actual, expected) for x in names):
        return None
    y1, y0 = recompute(graph, outcome, actual, {}), recompute(graph, outcome, expected, {})
    if not (math.isfinite(y1) and math.isfinite(y0)):
        return None
    terms = expand(graph, outcome)
    positive = all(actual[x] > 0 and expected[x] > 0 for x in names)
    if method is None:
        method = "lmdi" if terms is not None and positive else "shapley"
    parts = _lmdi(terms, actual, expected, names) if method == "lmdi" else _shapley(graph, outcome, actual, expected, names)
    return Attribution(outcome=outcome, actual=y1, expected=y0, by_factor=parts, method=method)
