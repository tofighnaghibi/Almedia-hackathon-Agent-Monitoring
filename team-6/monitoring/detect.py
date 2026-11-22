"""Change scores and anomaly detection (L4, Person A).

    from monitoring.detect import Detector
    det = Detector(engine)
    scores = det.score("payment_success_rate", by=["country", "platform"], start=..., end=...)
    episodes = det.detect(["orders", "payment_success_rate"], by=["country", "platform"], start=..., end=...)

    python -m monitoring.detect --data data/generated/scenarios

A cell (metric, segment, window, hour) is an anomaly when all of these hold:

- |z| >= z_threshold, where z = (actual - expected) / sigma on the metric's
  scale: log for most metrics, absolute for rare-event rates (see baseline.py)
- |relative change| >= min_rel_change, so tiny but precise moves are ignored
- in 1-hour windows the change lasts at least 2 consecutive hours, unless
  |z| >= 2 * z_threshold (single noisy hours are not reported)
- coverage >= min_coverage: incomplete data is held back (``suppressed``),
  not reported as a business change. ``COUNT(*)`` metrics such as
  ``row_count`` are exempt: missing rows are exactly what they measure.
- at least ``min_history_weeks`` comparable previous weeks exist

Consecutive anomalous hours for the same metric, segment, window and
direction become one ``Episode``. ``Episode.to_finding()`` gives the fields
of the contract's Finding / Alert (section 4) that detection can fill.
"""

from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence

import numpy as np
import pandas as pd

from graph.engine import MetricEngine

from .baseline import BaselineConfig, build_baseline, load_series, _ts

TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"


@dataclass
class DetectorConfig:
    windows: tuple[int, ...] = (1, 6, 24)  # window lengths in hours
    z_threshold: float = 5.0
    min_rel_change: float = 0.05
    min_coverage: float = 0.98
    baseline: BaselineConfig = field(default_factory=BaselineConfig)


@dataclass
class Episode:
    """Consecutive anomalous hours for one metric, segment, window and direction."""

    metric: str
    segment: dict[str, str]
    window_hours: int
    direction: str  # "up" or "down"
    impact: str  # "bad", "good" or "neutral" (from higher_is_better)
    start: pd.Timestamp  # first hour flagged (start of the last hour of the first anomalous window)
    end: pd.Timestamp  # end of the last flagged hour (exclusive)
    hours: int  # anomalous hours in the episode
    peak_z: float
    peak_time: pd.Timestamp  # end of the peak window (exclusive)
    actual: float  # at the peak
    expected: float
    lower: float
    upper: float
    rel_change: float
    abs_change: float
    ongoing: bool  # still anomalous at the last evaluated hour
    windows: list[int] = field(default_factory=list)  # window lengths that flagged it

    @property
    def key(self) -> str:
        seg = "|".join(f"{k}={v}" for k, v in sorted(self.segment.items())) or "all"
        return f"{self.metric}|{seg}|{self.direction}"

    def to_finding(self) -> dict[str, Any]:
        """Contract Finding fields that detection fills (L5/L6 add the rest)."""
        start = self.peak_time - pd.Timedelta(hours=self.window_hours)
        return {
            "finding_id": "fnd_" + hashlib.sha1(f"{self.key}|{self.start}".encode()).hexdigest()[:12],
            "fingerprint": self.key,
            "metric": self.metric,
            "segment": dict(self.segment),
            "time_window": {"start": start.strftime(TIME_FORMAT), "end": self.peak_time.strftime(TIME_FORMAT)},
            "expected": {"value": _f(self.expected), "lower": _f(self.lower), "upper": _f(self.upper)},
            "actual": _f(self.actual),
            "change_size": {"absolute": _f(self.abs_change), "relative_pct": _f(100 * self.rel_change),
                            "score": _f(self.peak_z)},
            "detection": {"window_hours": self.window_hours, "direction": self.direction,
                          "impact": self.impact, "anomalous_hours": self.hours,
                          "episode_start": self.start.strftime(TIME_FORMAT),
                          "episode_end": self.end.strftime(TIME_FORMAT), "ongoing": self.ongoing,
                          "windows": list(self.windows or [self.window_hours])},
        }


class Detector:
    def __init__(self, engine: MetricEngine, config: Optional[DetectorConfig] = None):
        self.engine = engine
        self.config = config or DetectorConfig()

    # --- scores for every hour ----------------------------------------------
    def score(
        self,
        metric: str,
        by: Sequence[str] = (),
        segment: Optional[dict] = None,
        start: Any = None,
        end: Any = None,
        windows: Optional[Iterable[int]] = None,
        train_end: Any = None,
    ) -> pd.DataFrame:
        """Scores for every hour in [start, end), every segment and window length.

        History before ``start`` is used for the baseline. ``train_end``
        (default ``start``) is where growth and error are measured up to.
        """
        cfg = self.config
        lo, hi = self.engine.time_range()
        start_ts = _ts(start) if start is not None else lo + pd.Timedelta(weeks=cfg.baseline.history_weeks)
        end_ts = _ts(end) if end is not None else hi + pd.Timedelta(hours=1)
        train_end = _ts(train_end) if train_end is not None else start_ts
        series = load_series(self.engine, metric, by, segment, None, end_ts, config=cfg.baseline)
        return self.score_series(series, start_ts, end_ts, train_end, windows)

    def score_series(self, series, start_ts, end_ts, train_end, windows=None, fits=None) -> pd.DataFrame:
        """Score an already loaded ``SegmentSeries`` (used by the live detector).

        ``fits`` (window -> ``Baseline.fit``) is filled on first use and reused
        afterwards, so the training data is only learned from once.
        """
        cfg = self.config
        metric = series.metric
        defn = self.engine.registry.get(metric)
        idx = series.index
        keep = np.asarray((idx >= start_ts) & (idx < end_ts))
        n_keep, n_seg = int(keep.sum()), series.values.shape[1]
        seg_cols = {d: np.repeat([str(series.segments.iloc[j][d]) for j in range(n_seg)], n_keep)
                    for d in series.by}

        def col(a):  # segment-major: all kept hours of segment 0, then segment 1, ...
            return np.asarray(a)[keep].T.reshape(-1)

        frames = []
        for w in windows or cfg.windows:
            fit = fits.get(w) if fits is not None else None
            b = build_baseline(series, w, train_end, cfg.baseline, fit=fit)
            if fits is not None and fit is None:
                fits[w] = b.fit
            z = b.z()
            lower, upper = b.band(cfg.z_threshold)
            a, e = col(b.actual), col(b.expected)
            with np.errstate(divide="ignore", invalid="ignore"):
                rel = a / e - 1
            frames.append(pd.DataFrame({
                "metric": metric,
                "window_hours": w,
                "window_end": np.tile(idx[keep] + pd.Timedelta(hours=1), n_seg),
                "actual": a,
                "expected": e,
                "lower": col(lower),
                "upper": col(upper),
                "abs_change": a - e,
                "rel_change": rel,
                "z": col(z),
                "sigma": col(b.sigma),
                "coverage": col(b.coverage),
                "weeks_used": col(b.weeks_used),
                **seg_cols,
            }))
        out = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if not out.empty:
            out["window_end"] = pd.to_datetime(out["window_end"], utc=True)
        if out.empty:
            return out
        return self.decide(out, series.by, series.completeness, defn.higher_is_better)

    def decide(self, out: pd.DataFrame, by: Sequence[str], completeness, higher_is_better) -> pd.DataFrame:
        """Apply the anomaly rules to scored cells (rows ordered by segment, then hour).

        ``completeness`` and ``higher_is_better`` are one value for the whole
        frame, or one per row (the live detector decides all series at once,
        grouping by a combined key in ``by``). Extra columns are kept.
        """
        cfg = self.config
        out["window_start"] = out["window_end"] - pd.to_timedelta(out["window_hours"], unit="h")
        out["direction"] = np.where(out["z"] >= 0, "up", "down")
        out["impact"] = _impact_vec(higher_is_better, out["direction"].to_numpy())
        scored = np.isfinite(out["z"]) & (out["weeks_used"] >= cfg.baseline.min_history_weeks)
        big = scored & (out["z"].abs() >= cfg.z_threshold) & (out["rel_change"].abs() >= cfg.min_rel_change)
        # 1h windows: a single hour must be extreme; otherwise its neighbour must agree
        seg_keys = ["window_hours", *by]
        same_dir = out["direction"]
        prev_big = big.groupby([out[k] for k in seg_keys]).shift(1, fill_value=False) &             (same_dir.groupby([out[k] for k in seg_keys]).shift(1) == same_dir)
        next_big = big.groupby([out[k] for k in seg_keys]).shift(-1, fill_value=False) &             (same_dir.groupby([out[k] for k in seg_keys]).shift(-1) == same_dir)
        strong = out["z"].abs() >= 2 * cfg.z_threshold
        persistent = (out["window_hours"] > 1) | prev_big | next_big | strong
        incomplete = (out["coverage"] < cfg.min_coverage) & ~np.asarray(completeness, dtype=bool)
        out["is_anomaly"] = big & persistent & ~incomplete
        out["suppressed"] = np.where(big & incomplete, "incomplete data",
                                     np.where(big & ~persistent, "single hour", ""))
        out.loc[~np.isfinite(out["actual"]), "suppressed"] = "missing data"
        cols = ["metric", *by, "window_hours", "window_start", "window_end", "actual", "expected",
                "lower", "upper", "abs_change", "rel_change", "z", "sigma", "direction", "impact",
                "coverage", "weeks_used", "is_anomaly", "suppressed"]
        return out[cols + [c for c in out.columns if c not in cols]]

    # --- anomalies grouped into episodes -------------------------------------
    def detect(
        self,
        metrics: Iterable[str],
        by: Sequence[str] = (),
        segment: Optional[dict] = None,
        start: Any = None,
        end: Any = None,
        windows: Optional[Iterable[int]] = None,
        merge: bool = True,
    ) -> list[Episode]:
        """Anomalous episodes; with ``merge`` one per metric/segment/direction/time span."""
        episodes: list[Episode] = []
        for m in metrics:
            if not all(d in self.engine.registry.get(m).dimensions for d in list(by) + list(segment or {})):
                continue
            s = self.score(m, by, segment, start, end, windows)
            if not s.empty:
                episodes += episodes_from_scores(s, list(by), self.engine.registry.get(m).higher_is_better)
        if merge:
            return merge_windows(episodes)
        return sorted(episodes, key=lambda e: -abs(e.peak_z))


def episodes_from_scores(scores: pd.DataFrame, by: list[str], higher_is_better=None) -> list[Episode]:
    """Merge consecutive anomalous hours (same metric/segment/window/direction)."""
    if scores.empty:
        return []
    last_hour = scores["window_end"].max()
    out = []
    keys = ["metric", "window_hours", "direction", *by]
    for key, g in scores[scores["is_anomaly"]].groupby(keys, sort=False):
        g = g.sort_values("window_end")
        run = (g["window_end"].diff() != pd.Timedelta(hours=1)).cumsum()
        for _, r in g.groupby(run):
            peak = r.loc[r["z"].abs().idxmax()]
            seg = {d: str(peak[d]) for d in by}
            out.append(Episode(
                metric=peak["metric"], segment=seg, window_hours=int(peak["window_hours"]),
                direction=peak["direction"], impact=peak["impact"],
                start=r["window_end"].min() - pd.Timedelta(hours=1), end=r["window_end"].max(), hours=len(r),
                peak_z=float(peak["z"]), peak_time=peak["window_end"],
                actual=float(peak["actual"]), expected=float(peak["expected"]),
                lower=float(peak["lower"]), upper=float(peak["upper"]),
                rel_change=float(peak["rel_change"]), abs_change=float(peak["abs_change"]),
                ongoing=bool(r["window_end"].max() == last_hour),
                windows=[int(peak["window_hours"])],
            ))
    return out


def merge_windows(episodes: list[Episode], gap_hours: int = 1) -> list[Episode]:
    """One episode per metric, segment and direction for overlapping time spans.

    The same change is usually flagged by several window lengths; the merged
    episode covers the union of their spans, keeps the strongest score, and
    lists the windows that flagged it.
    """
    groups: dict[str, list[Episode]] = {}
    for e in episodes:
        groups.setdefault(e.key, []).append(e)
    out = []
    gap = pd.Timedelta(hours=gap_hours)
    for eps in groups.values():
        eps.sort(key=lambda e: e.start)
        cluster = [eps[0]]
        for e in eps[1:]:
            if e.start <= max(c.end for c in cluster) + gap:
                cluster.append(e)
            else:
                out.append(_combine(cluster))
                cluster = [e]
        out.append(_combine(cluster))
    return sorted(out, key=lambda e: -abs(e.peak_z))


def _combine(cluster: list[Episode]) -> Episode:
    """Merge one change seen through several windows.

    Start: the earliest flag. End and ``ongoing``: from the shortest window
    that flagged it, because a 24h window keeps "seeing" a change for up to a
    day after it has stopped.
    """
    best = max(cluster, key=lambda e: abs(e.peak_z))
    shortest = min(e.window_hours for e in cluster)
    short = [e for e in cluster if e.window_hours == shortest]
    start, end = min(e.start for e in cluster), max(e.end for e in short)
    return Episode(**{
        **best.__dict__,
        "start": start, "end": end,
        "hours": int((end - start) / pd.Timedelta(hours=1)),
        "ongoing": any(e.ongoing for e in short),
        "windows": sorted({w for e in cluster for w in (e.windows or [e.window_hours])}),
    })


def _impact_vec(higher_is_better, direction: np.ndarray) -> np.ndarray:
    """``_impact`` for many rows; ``higher_is_better`` is one value or one per row."""
    hib = np.broadcast_to(np.asarray(higher_is_better, dtype=object), direction.shape)
    up = direction == "up"
    good = np.array([h is not None and (u == bool(h)) for h, u in zip(hib, up)])
    return np.where(np.array([h is None for h in hib]), "neutral", np.where(good, "good", "bad"))


def _impact(higher_is_better, direction: str) -> str:
    if higher_is_better is None:
        return "neutral"
    good = (direction == "up") == bool(higher_is_better)
    return "good" if good else "bad"


def _f(x: float) -> Optional[float]:
    return None if x is None or not np.isfinite(x) else round(float(x), 6)


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------

DEFAULT_GROUPINGS = [[], ["country"], ["country", "platform"], ["category"]]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Detect unusual changes in metrics.")
    ap.add_argument("--data", default="data/generated/scenarios")
    ap.add_argument("--metrics", default=None, help="comma-separated (default: all registered)")
    ap.add_argument("--by", action="append", default=None,
                    help="grouping, e.g. --by country,platform (repeatable; default: total, country, "
                         "country+platform, category)")
    ap.add_argument("--start", default=None, help="UTC start of monitoring (default: last 7 days)")
    ap.add_argument("--end", default=None, help="UTC end, exclusive (default: end of data)")
    ap.add_argument("--windows", default="1,6,24", help="window lengths in hours")
    ap.add_argument("--z", type=float, default=DetectorConfig.z_threshold, help="z threshold")
    ap.add_argument("--top", type=int, default=40, help="episodes to print")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    data = Path(args.data)
    if not (data / "marketplace_hourly.csv").exists():
        print(f"no data in {data}. Generate it first:  python -m simulator --scenarios all", file=sys.stderr)
        return 2
    engine = MetricEngine.from_dir(data)
    lo, hi = engine.time_range()
    start = _ts(args.start) if args.start else (hi + pd.Timedelta(hours=1)).floor("D") - pd.Timedelta(days=7)
    metrics = args.metrics.split(",") if args.metrics else engine.registry.names()
    groupings = [[d for d in g.split(",") if d] for g in args.by] if args.by else DEFAULT_GROUPINGS
    cfg = DetectorConfig(windows=tuple(int(w) for w in args.windows.split(",")), z_threshold=args.z)
    det = Detector(engine, cfg)

    episodes = []
    for by in groupings:
        episodes += det.detect(metrics, by=by, start=start, end=args.end)
    episodes.sort(key=lambda e: -abs(e.peak_z))
    print(f"monitoring {len(metrics)} metrics x {len(groupings)} groupings, windows {cfg.windows}h, "
          f"from {start:%Y-%m-%d %H:%M} UTC; {len(episodes)} episodes\n")
    print(f"{'z':>7} {'impact':7} {'metric':26} {'segment':34} {'windows':9} {'change':>8}  flagged")
    for e in episodes[: args.top]:
        seg = ", ".join(f"{k}={v}" for k, v in e.segment.items()) or "total"
        wins = ",".join(str(w) for w in e.windows) + "h"
        print(f"{e.peak_z:7.1f} {e.impact:7} {e.metric:26} {seg:34} {wins:9} {100 * e.rel_change:+7.1f}%  "
              f"{e.start:%m-%d %H:%M} -> {e.end:%m-%d %H:%M} ({e.hours}h){' ongoing' if e.ongoing else ''}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
