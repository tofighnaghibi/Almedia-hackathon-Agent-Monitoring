"""Compare a window with the same window in previous weeks (L5, Person A).

The baseline is the *pooled* same window 1..K weeks earlier: counts are
averaged, and ratios are recomputed from the pooled numerators and
denominators. Every metric is a ratio of sums, so the graph's formulas hold
exactly on the baseline as well as on the actual values. That is what lets
contributions add up to the total change.

(Monitoring uses a median with a growth adjustment instead, to decide *whether*
something changed. Investigation needs the identities, to explain *how*.)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Optional, Sequence

import numpy as np
import pandas as pd

from graph.bridge import weight_metric_for
from graph.engine import MetricEngine

DEFAULT_WEEKS = 4


@dataclass(frozen=True)
class Window:
    start: pd.Timestamp
    end: pd.Timestamp

    @classmethod
    def of(cls, start: Any, end: Any) -> "Window":
        return cls(_ts(start), _ts(end))

    def shifted(self, weeks: int) -> "Window":
        d = pd.Timedelta(weeks=weeks)
        return Window(self.start - d, self.end - d)

    @property
    def hours(self) -> float:
        return (self.end - self.start) / pd.Timedelta(hours=1)


def compare(
    engine: MetricEngine,
    metric: str,
    window: Window,
    segment: Optional[dict] = None,
    by: Sequence[str] = (),
    weeks: int = DEFAULT_WEEKS,
) -> pd.DataFrame:
    """Actual and baseline of ``metric`` per group of ``by``.

    Columns: ``*by, actual, baseline, w_actual, w_baseline, weeks``. ``w_*`` is
    the weight used to recombine groups: the denominator for a ratio, the row
    count for an average, and NaN for an additive metric.
    """
    reg = engine.registry
    defn = reg.get(metric)
    by = list(by)
    segment = dict(segment or {})
    weight = None if defn.is_additive else weight_metric_for(metric, reg)
    if weight is not None and not all(d in reg.get(weight).dimensions for d in by + list(segment)):
        weight = None
    kind = "sum" if defn.is_additive else ("weighted" if weight else "rows")
    names = [metric] + ([weight] if weight else [])

    def one(w: Window) -> pd.DataFrame:
        df = engine.compute_many(names, w.start, w.end, segment=segment, by=by)
        df = df.rename(columns={metric: "v"})
        df["w"] = df[weight] if weight else (df["n_rows"].astype(float) if kind == "rows" else np.nan)
        return df[by + ["v", "w", "n_rows"]]

    cur = one(window)
    past = []
    for k in range(1, weeks + 1):
        p = one(window.shifted(k))
        if (p["n_rows"] > 0).any():
            past.append(p.assign(k=k))
    key = by or None
    out = cur.rename(columns={"v": "actual", "w": "w_actual"}).drop(columns="n_rows")
    if not past:
        out["baseline"], out["w_baseline"], out["weeks"] = np.nan, np.nan, 0
        return out[by + ["actual", "baseline", "w_actual", "w_baseline", "weeks"]]

    hist = pd.concat(past, ignore_index=True)
    hist = hist[hist["n_rows"] > 0]
    n_weeks = hist["k"].nunique()
    if kind == "sum":
        agg = (hist.groupby(key)["v"].sum() / n_weeks) if key else pd.Series([hist["v"].sum() / n_weeks])
        base = agg.rename("baseline").to_frame()
        base["w_baseline"] = np.nan
    else:
        hist = hist.assign(vw=hist["v"] * hist["w"])
        g = hist.groupby(key)[["vw", "w"]].sum() if key else hist[["vw", "w"]].sum().to_frame().T
        base = pd.DataFrame({"baseline": g["vw"] / g["w"].replace(0, np.nan), "w_baseline": g["w"] / n_weeks})
    if key:
        out = out.merge(base.reset_index(), on=by, how="outer")
    else:
        out = pd.concat([out.reset_index(drop=True), base.reset_index(drop=True)], axis=1)
    out["weeks"] = n_weeks
    return out[by + ["actual", "baseline", "w_actual", "w_baseline", "weeks"]]


def compare_total(engine: MetricEngine, metric: str, window: Window, segment: Optional[dict] = None,
                  weeks: int = DEFAULT_WEEKS) -> tuple[float, float]:
    """(actual, baseline) of ``metric`` for one segment."""
    row = compare(engine, metric, window, segment, (), weeks).iloc[0]
    return float(row["actual"]), float(row["baseline"])


def _ts(t: Any) -> pd.Timestamp:
    ts = pd.Timestamp(t)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
