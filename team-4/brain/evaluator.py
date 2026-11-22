"""Compute any metric for any segment, window and grouping.

Column metrics are summed in SQL; derived metrics are evaluated on those sums, so a ratio is
always a ratio of sums (never an average of ratios) at whatever level it is asked for.
Missing data stays visible: an hour with no rows comes back as NaN, not as a gap or a zero.
"""
from __future__ import annotations

import pandas as pd

from brain.data import Store, iso_hour
from brain.formula import Formula
from brain.graph import MetricGraph


class NotComputable(ValueError):
    """The metric cannot be computed from columns yet (SQL-only: awaits the extension agent)."""


class DataMissing(NotComputable):
    """A column the metric depends on is absent from the data: the contract is broken."""


class _LazyEnv:
    def __init__(self, compute):
        self._compute = compute

    def __getitem__(self, name):
        return self._compute(name)


class Evaluator:
    def __init__(self, graph: MetricGraph, store: Store):
        self.graph = graph
        self.store = store

    def base_columns(self, metric: str, _stack: tuple[str, ...] = ()) -> set[str]:
        if metric in _stack:
            raise NotComputable(f"definition cycle: {' -> '.join(_stack + (metric,))}")
        s = self.graph.specs[metric]
        if s.column:
            if s.column not in self.store.measures:
                raise DataMissing(f"{metric}: column {s.column} is missing from facts_hourly")
            return {s.column}
        if s.formula is not None:
            cols: set[str] = set()
            for n in s.formula.names:
                try:
                    cols |= self.base_columns(n, _stack + (metric,))
                except DataMissing as e:
                    raise DataMissing(f"{metric}: depends on {n}; {e}") from None
            return cols
        raise NotComputable(f"{metric}: defined only by SQL; needs the extension agent to infer a formula")

    def computable(self, metric: str) -> bool:
        try:
            self.base_columns(metric)
            return True
        except NotComputable:
            return False

    def sums(self, metrics, start=None, end=None, segment: dict | None = None,
             group_by=(), by_hour: bool = False) -> pd.DataFrame:
        """Sums of the base columns the given metrics are built from: one row per (hour?, group).
        With by_hour, a start and an end, and no group_by, every hour of the window is present
        (NaN where data is missing). This is the table an expected state is built from."""
        cols: set[str] = set()
        for m in dict.fromkeys(metrics):
            cols |= self.base_columns(m)
        raw = self.store.sums(sorted(cols), start, end, segment, group_by, by_hour)
        if by_hour and not group_by and start is not None and end is not None:
            hours = pd.date_range(pd.Timestamp(iso_hour(start)), pd.Timestamp(iso_hour(end)),
                                  freq="h", inclusive="left")
            raw = raw.set_index("ts").reindex(hours).rename_axis("ts").reset_index()
        return raw

    def derive(self, sums: pd.DataFrame, metrics) -> pd.DataFrame:
        """Turn any table of base-column sums into metrics: the actual state, an expected state
        (for example averages of the same hours in earlier weeks) or a mix of the two. Because
        every metric is derived from the same sums, all formulas hold in the result, which the
        attribution in the investigator relies on. Non-measure columns are kept as keys."""
        metrics = list(dict.fromkeys(metrics))
        needed: set[str] = set()
        for m in metrics:
            needed |= self.base_columns(m)
        missing = sorted(needed - set(sums.columns))
        if missing:
            raise KeyError(f"the sums table lacks base columns {missing}")
        keys = [c for c in sums.columns if c not in self.store.measures]
        out = sums[keys].copy()
        cache: dict[str, pd.Series] = {}

        def compute(name: str):
            if name not in cache:
                s = self.graph.specs[name]
                if s.column:
                    cache[name] = sums[s.column]
                else:
                    cache[name] = s.formula.evaluate(_LazyEnv(compute))
            return cache[name]

        for m in metrics:
            out[m] = compute(m)
        return out

    def frame(self, metrics, start=None, end=None, segment: dict | None = None,
              group_by=(), by_hour: bool = False) -> pd.DataFrame:
        """One row per (hour?, group) with a column per requested metric (the actual state)."""
        metrics = list(dict.fromkeys(metrics))
        return self.derive(self.sums(metrics, start, end, segment, group_by, by_hour), metrics)

    def expression(self, formula: Formula, start=None, end=None, segment=None,
                   group_by=(), by_hour: bool = False) -> pd.DataFrame:
        """Evaluate an arbitrary formula over metrics; the result is in column `value`."""
        f = self.frame(formula.names, start, end, segment, group_by, by_hour)
        f["value"] = formula.evaluate({n: f[n] for n in formula.names})
        return f

    # ------------------------------------------------------------ shortcuts
    def value(self, metric: str, start=None, end=None, segment=None) -> float:
        f = self.frame([metric], start, end, segment)
        return float(f[metric].iloc[0]) if len(f) else float("nan")

    def series(self, metric: str, start=None, end=None, segment=None) -> pd.Series:
        f = self.frame([metric], start, end, segment, by_hour=True)
        return f.set_index("ts")[metric]

    def breakdown(self, metric: str, dim: str, start=None, end=None, segment=None) -> pd.Series:
        f = self.frame([metric], start, end, segment, group_by=(dim,))
        return f.set_index(dim)[metric]
