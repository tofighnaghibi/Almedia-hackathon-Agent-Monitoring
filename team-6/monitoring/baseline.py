"""Seasonal baseline and prediction range (L4, Person A).

For a metric, a set of segments and a window length L (hours), every hour t
gets:

    actual(t)    the metric over the L hours ending with hour t
    expected(t)  median of the same window 1..K weeks earlier, each scaled by
                 the segment's week-over-week growth, so it captures the time
                 of day, the day of the week, and the trend
    sigma(t)     how wrong exactly this predictor was on past data: the robust
                 spread (1.4826 * MAD) of the backtest error over the training
                 period, per segment and window length. It is never below the
                 noise the counts themselves imply (Poisson for counts,
                 binomial for ratios), so a small segment can't look precise.
    lower/upper  expected -/+ z * sigma, on the metric's scale

Scale: most metrics are compared on a log scale (relative change). Rare-event
rates, whose typical level is below ``rare_rate_level`` (out-of-stock rate,
ad CTR, conversion), are compared as absolute differences. On a log scale
their normal wobble (0.5% -> 2%) would look as large as a real jump (1.5% ->
30%).

Windows longer than an hour are built from hourly values: counts are summed,
ratios are recombined weighted by their denominator (exact for ratio-of-sums),
and anything else is averaged. ``COUNT(*)`` metrics measure completeness:
missing hours count as 0 rows, not as missing.

Only data before ``train_end`` is used for growth, bias and sigma, so a fault
in the evaluation period can't widen its own range.
"""

from __future__ import annotations

import warnings
from dataclasses import dataclass, field, replace
from typing import Any, Optional, Sequence

import numpy as np
import pandas as pd

from graph.bridge import weight_metric_for
from graph.engine import MetricEngine

HOURS_PER_WEEK = 168


@dataclass
class BaselineConfig:
    history_weeks: int = 4  # K: how many previous weeks the expectation uses
    min_history_weeks: int = 2  # fewer comparable weeks -> no expectation
    sigma_lookback_days: int = 21  # training days used to measure the error
    backtest_min_weeks: int = 1  # previous weeks a training hour needs to enter the backtest
    rate_offset: float = 0.05  # log offset for non-count metrics, as a share of the typical level
    rare_rate_level: float = 0.05  # ratios/rates typically below this use the absolute scale
    min_sigma: float = 0.01  # log scale: never claim more than 1% precision
    max_weekly_growth: float = 0.25  # clamp the growth factor to +/-25% a week
    use_trend: bool = True
    use_bias: bool = True  # correct the median backtest error


@dataclass
class SegmentSeries:
    """Hourly values of one metric for many segments (columns), all hours (rows)."""

    metric: str
    unit: str
    by: list[str]
    index: pd.DatetimeIndex
    segments: pd.DataFrame  # one row per column: the dimension values
    values: np.ndarray  # (hours, segments)
    weights: Optional[np.ndarray]  # denominator for ratios, else None
    rows: np.ndarray  # data rows behind each value (0 = missing)
    kind: str  # "sum", "weighted" or "mean"
    scale: str  # "log" or "linear"
    log_offset: float  # added before taking logs (0.5 for counts)
    completeness: bool  # a COUNT(*) metric: measures the data itself

    def segment_dict(self, j: int) -> dict[str, str]:
        return {d: str(self.segments.iloc[j][d]) for d in self.by}

    def to_scale(self, x: np.ndarray) -> np.ndarray:
        if self.scale == "linear":
            return x
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.log(x + self.log_offset)

    def from_scale(self, y: np.ndarray) -> np.ndarray:
        return y if self.scale == "linear" else np.exp(y) - self.log_offset


@dataclass
class Baseline:
    """Actual, expected and range for every hour and segment, for one window length."""

    series: SegmentSeries
    window_hours: int
    actual: np.ndarray
    expected: np.ndarray
    sigma: np.ndarray  # per cell, on the series' scale
    growth: np.ndarray  # weekly growth factor per segment
    bias: np.ndarray  # per segment, on the series' scale
    weeks_used: np.ndarray  # comparable previous weeks per cell
    coverage: np.ndarray  # rows present / rows normally present
    train_end: pd.Timestamp
    config: BaselineConfig = field(default_factory=BaselineConfig)
    fit: Optional[dict] = None  # what was learned from training data (reusable)

    def z(self) -> np.ndarray:
        s = self.series
        with np.errstate(divide="ignore", invalid="ignore"):
            return (s.to_scale(self.actual) - s.to_scale(self.expected)) / self.sigma

    def band(self, z: float) -> tuple[np.ndarray, np.ndarray]:
        s = self.series
        e = s.to_scale(self.expected)
        lo, hi = s.from_scale(e - z * self.sigma), s.from_scale(e + z * self.sigma)
        lo = np.maximum(lo, 0.0)
        if s.unit == "ratio":
            hi = np.minimum(hi, 1.0)
        return lo, hi

    def frame(self, z: float = 5.0, start: Any = None, end: Any = None) -> pd.DataFrame:
        """Long table for plotting: one row per hour and segment."""
        lo, hi = self.band(z)
        idx = self.series.index
        keep = np.ones(len(idx), dtype=bool)
        if start is not None:
            keep &= idx >= _ts(start)
        if end is not None:
            keep &= idx < _ts(end)
        rows = []
        for j in range(self.actual.shape[1]):
            rows.append(pd.DataFrame({
                **self.series.segment_dict(j),
                "period": idx[keep],
                "actual": self.actual[keep, j],
                "expected": self.expected[keep, j],
                "lower": lo[keep, j],
                "upper": hi[keep, j],
                "coverage": self.coverage[keep, j],
            }))
        return pd.concat(rows, ignore_index=True) if rows else pd.DataFrame()


# ---------------------------------------------------------------------------
# data
# ---------------------------------------------------------------------------

def load_series(
    engine: MetricEngine,
    metric: str,
    by: Sequence[str] = (),
    segment: Optional[dict] = None,
    start: Any = None,
    end: Any = None,
    config: Optional[BaselineConfig] = None,
) -> SegmentSeries:
    """Hourly values of ``metric`` per segment, every hour present (NaN = missing)."""
    cfg = config or BaselineConfig()
    reg = engine.registry
    defn = reg.get(metric)
    by = list(by)
    weight = None if defn.is_additive else weight_metric_for(metric, reg)
    if weight is not None and not all(d in reg.get(weight).dimensions for d in by + list(segment or {})):
        weight = None
    names = [metric] + ([weight] if weight else [])
    df = engine.compute_many(names, start, end, segment=segment, by=by, grain="hour")

    key = df[by].astype(str).agg("|".join, axis=1) if by else pd.Series("all", index=df.index)
    df = df.assign(__key=key.to_numpy())
    index = pd.DatetimeIndex(sorted(df["period"].unique()))
    table = {col: df.pivot_table(index="period", columns="__key", values=col, aggfunc="first", dropna=False)
             .reindex(index=index) for col in names + ["n_rows"]}
    cols = table["n_rows"].columns
    seg = (df.drop_duplicates("__key").set_index("__key").reindex(cols)[by].reset_index(drop=True)
           if by else pd.DataFrame(index=[0]))

    values = table[metric].reindex(columns=cols).to_numpy(dtype=float)
    rows = np.nan_to_num(table["n_rows"].to_numpy(dtype=float), nan=0.0)
    completeness = defn.calculation.strip().upper() == "COUNT(*)"
    if completeness:
        values = rows.copy()  # a missing hour is 0 rows, which is the signal
    else:
        values[rows == 0] = np.nan
    weights = table[weight].reindex(columns=cols).to_numpy(dtype=float) if weight else None
    kind = "sum" if defn.is_additive else ("weighted" if weight else "mean")

    pos = values[np.isfinite(values) & (values > 0)]
    typical = float(np.median(pos)) if len(pos) else 1.0
    if defn.is_additive:
        scale, offset = "log", 0.5
    elif defn.unit in ("ratio", "rate") and typical < cfg.rare_rate_level:
        scale, offset = "linear", 0.0
    else:
        # rates can be exactly 0 in an hour; a tiny offset would turn those
        # into huge log changes
        scale, offset = "log", cfg.rate_offset * typical
    return SegmentSeries(metric, defn.unit, by, index, seg, values, weights, rows, kind, scale, offset,
                         completeness)


# ---------------------------------------------------------------------------
# baseline
# ---------------------------------------------------------------------------

def _rolling_sum(a: np.ndarray, hours: int) -> np.ndarray:
    return pd.DataFrame(a).rolling(hours, min_periods=hours).sum().to_numpy()


def window_values(s: SegmentSeries, hours: int) -> np.ndarray:
    """Metric over the ``hours`` hours ending at each hour (NaN if any hour is missing)."""
    if hours == 1:
        return s.values.copy()
    if s.kind == "sum":
        return _rolling_sum(s.values, hours)
    if s.kind == "weighted":
        num, den = _rolling_sum(s.values * s.weights, hours), _rolling_sum(s.weights, hours)
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(den > 0, num / den, np.nan)
    return pd.DataFrame(s.values).rolling(hours, min_periods=hours).mean().to_numpy()


def typical_rows(s: SegmentSeries, train_mask: np.ndarray) -> np.ndarray:
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        typical = np.nanmedian(np.where(train_mask[:, None] & (s.rows > 0), s.rows, np.nan), axis=0)
    return np.where(np.isfinite(typical) & (typical > 0), typical, 1.0)


def window_coverage(s: SegmentSeries, hours: int, train_mask: np.ndarray,
                    typical: Optional[np.ndarray] = None) -> np.ndarray:
    """Data rows in the window divided by the rows normally there."""
    if typical is None:
        typical = typical_rows(s, train_mask)
    have = pd.DataFrame(s.rows).rolling(hours, min_periods=1).sum().to_numpy()
    return have / (typical * hours)


def count_noise(s: SegmentSeries, hours: int, expected: np.ndarray) -> np.ndarray:
    """The smallest sigma the counts allow, on the series' scale."""
    if s.completeness:  # the number of rows is fixed by design, not a random count
        return np.zeros_like(expected)
    with np.errstate(divide="ignore", invalid="ignore"):
        if s.kind == "sum" and s.unit == "count":
            sd = np.sqrt(np.maximum(expected, 0.0))  # Poisson
        elif s.kind == "weighted":
            n = window_values(SegmentSeries(**{**s.__dict__, "values": s.weights, "kind": "sum"}), hours)
            p = np.maximum(expected, 0.0)
            spread = np.maximum(1.0 - p, 0.0) if s.unit == "ratio" else 1.0
            sd = np.sqrt(p * spread / np.maximum(n, 1.0))  # binomial / Poisson numerator
        else:
            return np.zeros_like(expected)
        if s.scale == "log":
            sd = sd / (np.abs(expected) + s.log_offset)
    return np.where(np.isfinite(sd), sd, 0.0)


def _shift(a: np.ndarray, k: int) -> np.ndarray:
    out = np.full_like(a, np.nan)
    if k < len(a):
        out[k:] = a[:-k] if k else a
    return out


def _nanmedian(a: np.ndarray, axis: int) -> np.ndarray:
    with np.errstate(all="ignore"), warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)  # cells with no values at all
        return np.nanmedian(a, axis=axis)


def build_baseline(
    s: SegmentSeries,
    window_hours: int,
    train_end: Any,
    config: Optional[BaselineConfig] = None,
    fit: Optional[dict] = None,
) -> Baseline:
    """Baseline for every hour of ``s``.

    ``fit`` (from a previous ``Baseline.fit`` with the same ``train_end``)
    skips re-learning growth, bias, sigma and typical row counts from the
    training data. The live detector passes it, with a short trailing slice
    of the series, so a tick costs milliseconds.
    """
    cfg = config or BaselineConfig()
    train_end = _ts(train_end)
    train = np.asarray(s.index < train_end)
    A = window_values(s, window_hours)
    y = s.to_scale(A)
    if fit is not None:
        g, bias, sigma_seg = fit["g"], fit["bias"], fit["sigma_seg"]
        stack = np.stack([_shift(y, k * HOURS_PER_WEEK) + k * g for k in range(1, cfg.history_weeks + 1)])
        weeks = np.isfinite(stack).sum(axis=0)
        yE = np.where(weeks >= cfg.min_history_weeks, _nanmedian(stack, axis=0), np.nan)
        expected = s.from_scale(yE + bias)
        sigma = np.maximum(sigma_seg[None, :], count_noise(s, window_hours, expected))
        if s.scale == "linear":
            sigma = np.where(sigma > 0, sigma, np.nan)
        return Baseline(s, window_hours, A, expected, sigma, np.exp(g), bias, weeks,
                        window_coverage(s, window_hours, train, fit["typical"]), train_end, cfg, fit)

    # weekly growth per segment, from 24h windows in the training period
    # (log scale only: absolute-scale rates have no multiplicative trend)
    g = np.zeros(A.shape[1])
    if cfg.use_trend and s.scale == "log":
        d24 = s.to_scale(window_values(s, 24))
        wow = d24 - _shift(d24, HOURS_PER_WEEK)
        wow[~train] = np.nan
        g = _nanmedian(wow, axis=0)
        g = np.where(np.isfinite(g), g, 0.0)
        g = np.clip(g, np.log(1 - cfg.max_weekly_growth), np.log(1 + cfg.max_weekly_growth))

    # expected = median over previous weeks of (value k weeks ago) * growth^k
    stack = np.stack([_shift(y, k * HOURS_PER_WEEK) + k * g for k in range(1, cfg.history_weeks + 1)])
    weeks = np.isfinite(stack).sum(axis=0)
    yE_all = _nanmedian(stack, axis=0)
    yE = np.where(weeks >= cfg.min_history_weeks, yE_all, np.nan)

    # backtest error on the training period (an hour needs >= backtest_min_weeks
    # earlier weeks; fewer weeks predict worse, so this errs on the wide side)
    res = np.where(weeks >= cfg.backtest_min_weeks, y - yE_all, np.nan)
    lookback = train & np.asarray(s.index >= train_end - pd.Timedelta(days=cfg.sigma_lookback_days))
    r = np.where(lookback[:, None], res, np.nan)
    bias = _nanmedian(r, axis=0) if cfg.use_bias else np.zeros(A.shape[1])
    bias = np.where(np.isfinite(bias), bias, 0.0)
    sigma_seg = 1.4826 * _nanmedian(np.abs(r - bias), axis=0)
    enough = np.isfinite(r).sum(axis=0) >= 24
    floor = cfg.min_sigma if s.scale == "log" else 0.0
    sigma_seg = np.where(enough & np.isfinite(sigma_seg), np.maximum(sigma_seg, floor), np.nan)

    expected = s.from_scale(yE + bias)
    sigma = np.maximum(sigma_seg[None, :], count_noise(s, window_hours, expected))
    if s.scale == "linear":  # an absolute-scale sigma of exactly 0 can't be right
        sigma = np.where(sigma > 0, sigma, np.nan)
    return Baseline(
        series=s,
        window_hours=window_hours,
        actual=A,
        expected=expected,
        sigma=sigma,
        growth=np.exp(g),
        bias=bias,
        weeks_used=weeks,
        coverage=window_coverage(s, window_hours, train),
        train_end=train_end,
        config=cfg,
        fit={"g": g, "bias": bias, "sigma_seg": sigma_seg, "typical": typical_rows(s, train)},
    )


def score_positions(s: SegmentSeries, window_hours: int, fit: dict, positions: np.ndarray,
                    config: Optional[BaselineConfig] = None) -> dict[str, np.ndarray]:
    """Actual, expected, sigma, coverage and weeks at a few hour positions only.

    The same formulas as ``build_baseline`` with a ``fit``, evaluated at
    ``positions`` (row indices of ``s``) instead of every hour: the window
    value at t and at t - 1..K weeks. Used by the live detector; a test checks
    it gives the same numbers as ``build_baseline``.
    """
    cfg = config or BaselineConfig()
    w = window_hours
    T = len(s.index)

    def window_at(pos: np.ndarray, vals: np.ndarray, kind: str, wts: Optional[np.ndarray]) -> np.ndarray:
        out = np.full((len(pos), vals.shape[1]), np.nan)
        for i, p in enumerate(pos):
            if p - w + 1 < 0 or p >= T:
                continue
            block = vals[p - w + 1:p + 1]
            if kind == "sum":
                out[i] = block.sum(axis=0) if w > 1 else block[0]
                if w > 1:
                    out[i] = np.where(np.isnan(block).any(axis=0), np.nan, out[i])
            elif kind == "weighted":
                wb = wts[p - w + 1:p + 1]
                num, den = (block * wb).sum(axis=0), wb.sum(axis=0)
                bad = np.isnan(block).any(axis=0) | np.isnan(wb).any(axis=0)
                with np.errstate(divide="ignore", invalid="ignore"):
                    out[i] = np.where(bad | (den <= 0), np.nan, num / den) if w > 1 else block[0]
            else:
                out[i] = block.mean(axis=0) if w > 1 else block[0]
                if w > 1:
                    out[i] = np.where(np.isnan(block).any(axis=0), np.nan, out[i])
        return out

    A = window_at(positions, s.values, s.kind, s.weights)
    shifted = np.stack([s.to_scale(window_at(positions - k * HOURS_PER_WEEK, s.values, s.kind, s.weights))
                        + k * fit["g"] for k in range(1, cfg.history_weeks + 1)])
    weeks = np.isfinite(shifted).sum(axis=0)
    yE = np.where(weeks >= cfg.min_history_weeks, _nanmedian(shifted, axis=0), np.nan)
    expected = s.from_scale(yE + fit["bias"])
    # count noise needs the window's denominator / expected count at these positions
    if s.kind == "weighted":
        n = window_at(positions, s.weights, "sum", None)
        with np.errstate(divide="ignore", invalid="ignore"):
            p_ = np.maximum(expected, 0.0)
            spread = np.maximum(1.0 - p_, 0.0) if s.unit == "ratio" else 1.0
            sd = np.sqrt(p_ * spread / np.maximum(n, 1.0))
    elif s.kind == "sum" and s.unit == "count" and not s.completeness:
        sd = np.sqrt(np.maximum(expected, 0.0))
    else:
        sd = np.zeros_like(expected)
    if s.completeness:
        sd = np.zeros_like(expected)
    with np.errstate(divide="ignore", invalid="ignore"):
        if s.scale == "log":
            sd = sd / (np.abs(expected) + s.log_offset)
    sd = np.where(np.isfinite(sd), sd, 0.0)
    sigma = np.maximum(fit["sigma_seg"][None, :], sd)
    if s.scale == "linear":
        sigma = np.where(sigma > 0, sigma, np.nan)
    have = np.stack([s.rows[max(0, p - w + 1):p + 1].sum(axis=0) for p in positions])
    coverage = have / (fit["typical"] * w)
    return {"actual": A, "expected": expected, "sigma": sigma, "coverage": coverage, "weeks": weeks}


def _ts(t: Any) -> pd.Timestamp:
    ts = pd.Timestamp(t)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")
