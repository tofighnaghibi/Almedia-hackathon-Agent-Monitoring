"""Numeric signatures of metric definitions.

Every metric that is computed from columns is evaluated on a few random positive values of
those columns. Two definitions with the same signature are the same metric whatever the data
(polynomial identity testing: random evaluation separates different rational functions with
overwhelming probability). No database is needed.

Uses:
  - tell an identity that holds by construction (orders = sessions * (orders / sessions)) from
    one that the data has to confirm;
  - later (extension agent): recognise an alias of an existing metric and search for the place
    of a new metric among the existing ones.
"""
from __future__ import annotations

import numpy as np

from brain.graph import MetricGraph

N_POINTS = 8


def signatures(graph: MetricGraph, n: int = N_POINTS, seed: int = 0) -> dict[str, np.ndarray]:
    """metric -> vector of its values on n random assignments of the base columns.
    Metrics that cannot be computed from columns (SQL only) are left out."""
    rng = np.random.default_rng(seed)
    columns = sorted({s.column for s in graph.specs.values() if s.column})
    base = {c: rng.uniform(1.0, 2.0, n) for c in columns}
    out: dict[str, np.ndarray] = {}
    visiting: set[str] = set()

    def value(name: str):
        if name in out:
            return out[name]
        s = graph.specs[name]
        if s.column:
            out[name] = base[s.column]
        elif s.formula is not None and name not in visiting:
            visiting.add(name)
            try:
                out[name] = np.asarray(s.formula.evaluate(_Env(value)), dtype=float)
            finally:
                visiting.discard(name)
        else:
            raise KeyError(name)
        return out[name]

    for m in graph.specs:
        try:
            value(m)
        except KeyError:
            continue
    return out


class _Env:
    def __init__(self, fn):
        self._fn = fn

    def __getitem__(self, name):
        return self._fn(name)


def same(a: np.ndarray, b: np.ndarray, rtol: float = 1e-9) -> bool:
    return bool(np.all(np.isfinite(a)) and np.all(np.isfinite(b)) and np.allclose(a, b, rtol=rtol, atol=0.0))


def holds_by_construction(graph: MetricGraph, metric: str, sig: dict[str, np.ndarray] | None = None) -> bool:
    """True when the metric's investigative formula equals the metric for any data, because the
    components are themselves defined from the same columns."""
    spec = graph.specs[metric]
    inv = spec.investigative_formula
    if inv is None:
        return False
    sig = signatures(graph) if sig is None else sig
    if metric not in sig or any(n not in sig for n in inv.names):
        return False
    return same(np.asarray(inv.evaluate({n: sig[n] for n in inv.names}), dtype=float), sig[metric])
