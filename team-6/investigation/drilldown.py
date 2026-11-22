"""Which segment explains a change (L5, Person A).

For a metric's change in a segment, every dimension that isn't fixed yet
(country, platform, category) is split into its values. Each value's
contribution to the total change is:

- for counts: its own change, ``actual_i - baseline_i``;
- for ratios: a **rate effect** (its own rate changed) plus a **mix effect**
  (its share of the denominator changed), using the exact midpoint
  decomposition

      dR = sum_i  w_bar_i * dr_i            (rate effect)
         + sum_i  (r_bar_i - R_bar) * dw_i  (mix effect)

  where w_i is the value's share of the denominator, r_i its rate, and bars
  are averages of baseline and actual. The two parts add up exactly to dR.

The dimension where one value carries the largest share of the change is
chosen. If that share is at least ``min_share``, and the rest of the segment
moved by less than ``max_rest_ratio`` times as much as that value, the drill
continues inside that value. Otherwise it stops, because the change is spread
across the dimension. It also stops when no dimension is left.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import numpy as np
import pandas as pd

from graph.engine import MetricEngine

from .compare import DEFAULT_WEEKS, Window, compare

DIMENSIONS = ("country", "platform", "category")


@dataclass
class DrillStep:
    segment: dict[str, str]  # where this step looked
    total_actual: float
    total_baseline: float
    dimension: Optional[str]  # dimension chosen (None if the drill stopped)
    value: Optional[str]  # value chosen in that dimension
    share: float  # chosen value's share of this step's change
    share_of_root: float  # ... of the change the drill started from
    tables: dict[str, pd.DataFrame] = field(default_factory=dict)  # per dimension: the split
    stop_reason: str = ""

    def summary(self) -> str:
        seg = _seg(self.segment)
        if self.dimension is None:
            return f"{seg}: stop ({self.stop_reason})"
        t = self.tables[self.dimension].set_index("value").loc[self.value]
        return (f"{seg}: by {self.dimension}, {self.dimension}={self.value} carries {100 * self.share:.0f}% "
                f"of the change ({100 * t['rel_change']:+.1f}% vs {100 * t['others_rel_change']:+.1f}% elsewhere)")


@dataclass
class DrillResult:
    metric: str
    steps: list[DrillStep]

    @property
    def segment(self) -> dict[str, str]:
        """The narrowest segment the change was localized to."""
        last = self.steps[-1]
        seg = dict(last.segment)
        if last.dimension is not None:
            seg[last.dimension] = last.value
        return seg

    @property
    def share_of_root(self) -> float:
        return self.steps[-1].share_of_root if self.steps[-1].dimension else (
            self.steps[-2].share_of_root if len(self.steps) > 1 else 1.0)


def split(
    engine: MetricEngine,
    metric: str,
    window: Window,
    segment: dict[str, str],
    dimension: str,
    weeks: int = DEFAULT_WEEKS,
) -> pd.DataFrame:
    """Each value of ``dimension``: actual, baseline, contribution and its share.

    Columns: value, actual, baseline, rel_change, contribution, share,
    rate_effect, mix_effect, others_rel_change.
    """
    df = compare(engine, metric, window, segment, [dimension], weeks).rename(columns={dimension: "value"})
    df = df.dropna(subset=["actual", "baseline"]).reset_index(drop=True)
    additive = engine.registry.get(metric).is_additive
    if additive:
        df["contribution"] = df["actual"] - df["baseline"]
        df["rate_effect"], df["mix_effect"] = df["contribution"], 0.0
        total_a, total_b = df["actual"].sum(), df["baseline"].sum()
        others = lambda i: (total_a - df["actual"][i]) / (total_b - df["baseline"][i]) - 1  # noqa: E731
    else:
        wa = df["w_actual"] / df["w_actual"].sum()
        wb = df["w_baseline"] / df["w_baseline"].sum()
        Ra, Rb = float((wa * df["actual"]).sum()), float((wb * df["baseline"]).sum())
        w_bar, r_bar, R_bar = (wa + wb) / 2, (df["actual"] + df["baseline"]) / 2, (Ra + Rb) / 2
        df["rate_effect"] = w_bar * (df["actual"] - df["baseline"])
        df["mix_effect"] = (r_bar - R_bar) * (wa - wb)
        df["contribution"] = df["rate_effect"] + df["mix_effect"]

        def others(i):
            m = df.index != i
            ra = (df["actual"][m] * df["w_actual"][m]).sum() / df["w_actual"][m].sum()
            rb = (df["baseline"][m] * df["w_baseline"][m]).sum() / df["w_baseline"][m].sum()
            return ra / rb - 1
    total = df["contribution"].sum()
    with np.errstate(divide="ignore", invalid="ignore"):
        df["rel_change"] = df["actual"] / df["baseline"] - 1
        df["share"] = df["contribution"] / total if total else np.nan
    df["others_rel_change"] = [others(i) if len(df) > 1 else np.nan for i in df.index]
    df = df.sort_values("share", ascending=False, key=lambda s: s.abs()).reset_index(drop=True)
    return df[["value", "actual", "baseline", "rel_change", "contribution", "share",
               "rate_effect", "mix_effect", "others_rel_change"]]


def drill_down(
    engine: MetricEngine,
    metric: str,
    window: Window,
    segment: Optional[dict] = None,
    dimensions: Sequence[str] = DIMENSIONS,
    weeks: int = DEFAULT_WEEKS,
    min_share: float = 0.5,
    min_lead: float = 0.0,
    max_rest_ratio: float = 0.5,
) -> DrillResult:
    """Narrow the change down to the segment that explains most of it."""
    defn = engine.registry.get(metric)
    seg = dict(segment or {})
    steps: list[DrillStep] = []
    root_share = 1.0
    while True:
        a, b = _total(engine, metric, window, seg, weeks)
        free = [d for d in dimensions if d not in seg and d in defn.dimensions]
        if not free:
            steps.append(DrillStep(seg, a, b, None, None, 1.0, root_share, stop_reason="no dimension left to split"))
            break
        tables = {d: split(engine, metric, window, seg, d, weeks) for d in free}
        best_dim, best = None, None
        for d, t in tables.items():
            if t.empty or not np.isfinite(t["share"]).any():
                continue
            top = t.iloc[0]
            # a single value only "explains" if it moves the same way as the total ...
            if top["share"] <= 0:
                continue
            # ... and the rest did not move nearly as much: android -100% while web and
            # ios are also -100% is a change across platforms, not an android problem
            rel, rest = abs(float(top["rel_change"])), float(top["others_rel_change"])
            if np.isfinite(rest) and np.isfinite(rel) and abs(rest) > max_rest_ratio * rel:
                continue
            if best is None or top["share"] > best["share"]:
                best_dim, best = d, top
        if best is None or best["share"] < min_share:
            spread = ", ".join(f"{d}: top {t.iloc[0]['value']} {100 * t.iloc[0]['share']:.0f}%"
                               for d, t in tables.items() if not t.empty)
            steps.append(DrillStep(seg, a, b, None, None, 1.0, root_share, tables,
                                   stop_reason=f"change is spread across segments ({spread})"))
            break
        root_share *= float(best["share"])
        steps.append(DrillStep(seg, a, b, best_dim, str(best["value"]), float(best["share"]), root_share, tables))
        seg = {**seg, best_dim: str(best["value"])}
    return DrillResult(metric, steps)


def _total(engine, metric, window, seg, weeks) -> tuple[float, float]:
    t = compare(engine, metric, window, seg, (), weeks).iloc[0]
    return float(t["actual"]), float(t["baseline"])


def _seg(seg: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in seg.items()) or "total"
