"""Live detection: score each new hour without reloading the history (L4, Person A).

    det = LiveDetector(engine, train_end=live_start)
    det.watch(["orders", "payment_success_rate"])
    det.refresh(hours_from, until)   # after new rows (or late rows) arrived
    cells = det.score_hour(hour)     # every watched metric, grouping and window

Every watched (metric, grouping) keeps its hourly ``SegmentSeries`` in memory.
Per tick:

- ``refresh`` re-reads only the hours that changed (new hour, plus earlier
  hours that received late rows), with one query per grouping.
- what the baseline learned from the training data (growth, bias, sigma,
  typical row counts) is learned once and reused;
- scoring runs on a trailing slice of the series: the previous weeks the
  expectation needs plus a few hours of context.

Scoring uses exactly the same rules as ``Detector.score``.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable, Optional, Sequence

import numpy as np
import pandas as pd

from graph.bridge import weight_metric_for
from graph.engine import MetricEngine

from .baseline import HOURS_PER_WEEK, SegmentSeries, load_series, score_positions
from .detect import DEFAULT_GROUPINGS, Detector, DetectorConfig

HOUR = pd.Timedelta(hours=1)


class LiveDetector:
    def __init__(
        self,
        engine: MetricEngine,
        train_end: pd.Timestamp,
        config: Optional[DetectorConfig] = None,
        groupings: Sequence[Sequence[str]] = DEFAULT_GROUPINGS,
    ):
        self.engine = engine
        self.train_end = pd.Timestamp(train_end)
        self.config = config or DetectorConfig(windows=(1, 6))
        self.detector = Detector(engine, self.config)
        self.groupings = [list(g) for g in groupings]
        self._series: dict[tuple[str, tuple[str, ...]], SegmentSeries] = {}
        self._fits: dict[tuple[str, tuple[str, ...]], dict] = {}
        self._labels: dict[tuple[str, tuple[str, ...]], dict] = {}

    # --- what is watched -------------------------------------------------------
    @property
    def metrics(self) -> list[str]:
        return sorted({k[0] for k in self._series})

    def keys(self) -> list[tuple[str, tuple[str, ...]]]:
        return list(self._series)

    def watch(self, metrics: Iterable[str]) -> list[str]:
        """Start watching metrics (in every grouping they allow). Returns the new ones."""
        added = []
        for m in metrics:
            if m not in self.engine.registry:
                continue
            dims = self.engine.registry.get(m).dimensions
            for by in self.groupings:
                if not all(d in dims for d in by):
                    continue
                key = (m, tuple(by))
                if key not in self._series:
                    self._series[key] = load_series(self.engine, m, by, None, None, None, config=self.config.baseline)
                    self._fits[key] = {}
                    if m not in added:
                        added.append(m)
        return added

    def unwatch(self, metrics: Iterable[str]) -> None:
        drop = set(metrics)
        self._series = {k: v for k, v in self._series.items() if k[0] not in drop}
        self._fits = {k: v for k, v in self._fits.items() if k[0] not in drop}

    def series(self, metric: str, by: Sequence[str]) -> Optional[SegmentSeries]:
        return self._series.get((metric, tuple(by)))

    # --- new data ---------------------------------------------------------------
    def refresh(self, hours_from: pd.Timestamp, until: pd.Timestamp) -> None:
        """Re-read [hours_from, until) for every watched series (new or late rows)."""
        by_grouping: dict[tuple[str, ...], list[str]] = {}
        for m, by in self._series:
            by_grouping.setdefault(by, []).append(m)
        reg = self.engine.registry
        for by, metrics in by_grouping.items():
            weights = {}
            for m in metrics:
                s = self._series[(m, by)]
                if s.kind == "weighted":
                    weights[m] = weight_metric_for(m, reg)
            names = list(dict.fromkeys(metrics + [w for w in weights.values() if w]))
            wide = self.engine.compute_many(names, hours_from, until, by=list(by), grain="hour")
            table = _unstack(wide, list(by))
            for m in metrics:
                key = (m, by)
                part = _from_wide(self._series[key], wide, m, weights.get(m), table)
                self._series[key] = _merge(self._series[key], part)

    # --- scoring ----------------------------------------------------------------
    def score_hour(self, hour: pd.Timestamp, context_hours: int = 2) -> pd.DataFrame:
        """Scores for ``hour`` (with a little context for the persistence rule)."""
        cfg = self.config
        frames = []
        for key, s in self._series.items():
            fits = self._fits[key]
            if len(fits) < len(cfg.windows):  # first time: learn from the training data once
                self.detector.score_series(s, hour, hour + HOUR, self.train_end, fits=fits)
            pos_hour = s.index.get_indexer([hour])[0]
            if pos_hour < 0:
                continue
            positions = np.arange(pos_hour - context_hours, pos_hour + 1)
            hours = s.index[positions]
            n_seg = s.values.shape[1]
            labels = self._labels.get(key)
            if labels is None:
                labels = self._labels[key] = {d: s.segments[d].astype(str).to_numpy() for d in s.by}
            seg_cols = {d: np.repeat(labels[d], len(positions)) for d in s.by}
            for w in cfg.windows:
                r = score_positions(s, w, fits[w], positions, cfg.baseline)
                e = r["expected"]
                with np.errstate(divide="ignore", invalid="ignore"):
                    z = (s.to_scale(r["actual"]) - s.to_scale(e)) / r["sigma"]
                    lo = s.from_scale(s.to_scale(e) - cfg.z_threshold * r["sigma"])
                    hi = s.from_scale(s.to_scale(e) + cfg.z_threshold * r["sigma"])
                lo = np.maximum(lo, 0.0)
                if s.unit == "ratio":
                    hi = np.minimum(hi, 1.0)
                col = lambda a: np.asarray(a).T.reshape(-1)  # noqa: E731  segment-major
                a = col(r["actual"])
                with np.errstate(divide="ignore", invalid="ignore"):
                    rel = a / col(e) - 1
                frames.append((s, pd.DataFrame({
                    "metric": key[0], "window_hours": w,
                    "window_end": np.tile(hours + HOUR, n_seg),
                    "actual": a, "expected": col(e), "lower": col(lo), "upper": col(hi),
                    "abs_change": a - col(e), "rel_change": rel, "z": col(z), "sigma": col(r["sigma"]),
                    "coverage": col(r["coverage"]), "weeks_used": col(r["weeks"]), **seg_cols,
                })))
        if not frames:
            return pd.DataFrame()
        # decide all series at once: one group key per series and segment
        parts = []
        for s, df in frames:
            seg = df[list(s.by)].astype(str).agg("|".join, axis=1) if s.by else "all"
            defn = self.engine.registry.get(s.metric)
            parts.append(df.assign(by="|".join(s.by), __g=s.metric + "/" + "|".join(s.by) + "/" + seg,
                                   __complete=s.completeness, __hib=defn.higher_is_better))
        allc = pd.concat(parts, ignore_index=True)
        allc["window_end"] = pd.to_datetime(allc["window_end"], utc=True)
        res = self.detector.decide(allc, ["__g"], allc["__complete"].to_numpy(), allc["__hib"].to_numpy())
        res = res[res["window_end"] == hour + HOUR].drop(columns=["__g", "__complete", "__hib"])
        return res.reset_index(drop=True)

    def baseline_frame(self, metric: str, segment: dict, hours: int, until: pd.Timestamp, window: int = 1):
        """Actual, expected and range for the last ``hours`` hours of one segment (for charts)."""
        by = tuple(d for d in ("country", "platform", "category") if d in segment)
        key = (metric, by)
        if key not in self._series:
            return pd.DataFrame()
        from .baseline import build_baseline

        s = self._series[key]
        fit = self._fits[key].get(window)
        span = self.config.baseline.history_weeks * HOURS_PER_WEEK + window + hours + 2
        b = build_baseline(_tail(s, span) if fit else s, window, self.train_end, self.config.baseline, fit=fit)
        df = b.frame(self.config.z_threshold, start=until - HOUR * hours, end=until)
        for d, v in segment.items():
            if d in df.columns:
                df = df[df[d] == v]
        return df.reset_index(drop=True)


def _tail(s: SegmentSeries, n: int) -> SegmentSeries:
    return replace(s, index=s.index[-n:], values=s.values[-n:], rows=s.rows[-n:],
                   weights=None if s.weights is None else s.weights[-n:])


def _from_wide(old: SegmentSeries, wide: pd.DataFrame, metric: str, weight: Optional[str],
               table: Optional[pd.DataFrame] = None) -> SegmentSeries:
    """A partial series (same segments as ``old``) from a ``compute_many`` result.

    ``table`` is ``wide`` already reshaped to (period x (column, segment)); pass
    it to avoid reshaping once per metric.
    """
    if table is None:
        table = _unstack(wide, old.by)
    keys = _keys(old)
    index = pd.DatetimeIndex(table.index)

    def grab(col: str) -> np.ndarray:
        return table[col].reindex(columns=keys).to_numpy(dtype=float)

    rows = np.nan_to_num(grab("n_rows"), nan=0.0)
    if old.completeness:
        values = rows.copy()
    else:
        values = grab(metric)
        values[rows == 0] = np.nan
    weights = grab(weight) if (weight and old.weights is not None) else None
    return replace(old, index=index, values=values, rows=rows, weights=weights)


def _unstack(wide: pd.DataFrame, by: Sequence[str]) -> pd.DataFrame:
    key = wide[list(by)].astype(str).agg("|".join, axis=1) if by else pd.Series("all", index=wide.index)
    cols = [c for c in wide.columns if c not in (*by, "period")]
    return wide.assign(__key=key.to_numpy()).set_index(["period", "__key"])[cols].unstack("__key").sort_index()


def _merge(old: SegmentSeries, new: SegmentSeries) -> SegmentSeries:
    """``old`` with the hours of ``new`` overwritten or appended (same segment columns)."""
    if not len(new.index):
        return old
    index = old.index.union(new.index)
    pos_old = index.get_indexer(old.index)
    pos_new = index.get_indexer(new.index)

    def grow(a_old, a_new, fill):
        if a_old is None:
            return None
        out = np.full((len(index), a_old.shape[1]), fill)
        out[pos_old] = a_old
        if a_new is not None:
            out[pos_new] = a_new
        return out

    return replace(old, index=pd.DatetimeIndex(index), values=grow(old.values, new.values, np.nan),
                   rows=grow(old.rows, new.rows, 0.0),
                   weights=grow(old.weights, new.weights, np.nan) if old.weights is not None else None)


def _keys(s: SegmentSeries) -> list[str]:
    if not s.by:
        return ["all"]
    return ["|".join(str(s.segments.iloc[j][d]) for d in s.by) for j in range(len(s.segments))]
