"""Follow the graph, quantify, localize (L5, Person A).

    from investigation.investigate import investigate
    res = investigate(engine, graph, "orders", start, end, segment={"country": "DE"})
    res.trail, res.evidence, res.business_impact

    python -m investigation.investigate --metric orders                 # strongest orders anomaly
    python -m investigation.investigate --metric orders --segment country=DE \
        --start 2026-10-22T10:00Z --end 2026-10-23T10:00Z

Steps (each one becomes an entry of the contract's ``trail``):

1. **walk_formula**: decompose the anomalous metric along formula edges
   (``decompose``) and take the drivers, the leaves carrying >= 20% of the
   change.
2. **drill_dimension**: for each driver (up to ``max_drivers``), find the
   segment that explains its change (``drill_down``).
3. **quantify_impact**: orders and revenue against the baseline, in the
   anomaly's segment and in the localized segment.

The result has everything L5 Person B's hypothesis ranking and the L6 report
need: the contribution tree, the localized segments, ``evidence_list`` and
``trail`` in the contract format, and ``business_impact``.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from graph.engine import MetricEngine
from graph.metric_graph import MetricGraph

from .compare import DEFAULT_WEEKS, Window, compare_total
from .decompose import ContributionNode, decompose
from .drilldown import DIMENSIONS, DrillResult, drill_down

IMPACT_METRICS = ("orders", "revenue")


@dataclass
class Investigation:
    metric: str
    segment: dict[str, str]
    window: Window
    actual: float
    baseline: float
    tree: ContributionNode
    drivers: list[ContributionNode]
    localization: dict[str, DrillResult]  # driver -> drill result
    impact_drill: Optional[DrillResult]  # where the anomalous metric itself changed
    business_impact: dict[str, Any]
    trail: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)

    @property
    def rel_change(self) -> float:
        return self.actual / self.baseline - 1 if self.baseline else float("nan")

    @property
    def summary(self) -> str:
        if not self.drivers:
            return f"{self.metric} {100 * self.rel_change:+.1f}%: no driver found"
        d = self.drivers[0]
        loc = self.localization.get(d.metric)
        where = _seg(loc.segment) if loc else _seg(self.segment)
        return (f"{self.metric} {100 * self.rel_change:+.1f}% in {_seg(self.segment)}: "
                f"{d.metric} {100 * d.rel_change:+.1f}% carries {100 * d.share:.0f}% of the change, "
                f"localized to {where}")

    def to_dict(self) -> dict[str, Any]:
        return {
            "metric": self.metric, "segment": self.segment,
            "time_window": {"start": _iso(self.window.start), "end": _iso(self.window.end)},
            "actual": _f(self.actual), "baseline": _f(self.baseline),
            "relative_pct": _f(100 * self.rel_change),
            "summary": self.summary,
            "drivers": [{"metric": d.metric, "share_pct": _f(100 * d.share), "rel_change_pct": _f(100 * d.rel_change),
                         "localized_segment": self.localization[d.metric].segment if d.metric in self.localization else None}
                        for d in self.drivers],
            "contribution_tree": self.tree.to_dict(),
            "business_impact": self.business_impact,
            "evidence_list": self.evidence,
            "trail": self.trail,
        }


def investigate(
    engine: MetricEngine,
    graph: MetricGraph,
    metric: str,
    start: Any,
    end: Any,
    segment: Optional[dict] = None,
    weeks: int = DEFAULT_WEEKS,
    driver_share: float = 0.2,
    max_drivers: int = 2,
) -> Investigation:
    window = Window.of(start, end)
    segment = dict(segment or {})
    trail: list[dict[str, Any]] = []
    evidence: list[dict[str, Any]] = []

    def step(action: str, query: str, observation: str) -> None:
        trail.append({"step": len(trail) + 1, "action": action, "query": query, "observation": observation})

    # 1. follow formula edges
    tree = decompose(engine, graph, metric, window, segment, weeks)
    drivers = tree.drivers(driver_share)[:max_drivers] if tree.children else []
    path = [n for n in tree.walk() if n.formula]
    if path:
        for n in path:
            parts = ", ".join(f"{c.metric} {100 * c.rel_change:+.1f}% ({100 * c.share:.0f}% of the change)"
                              for c in n.children)
            step("walk_formula", n.formula, parts)
    else:
        step("walk_formula", f"formulas defining {metric}",
             f"{metric} has no formula that holds here; it is investigated directly"
             + (f" ({tree.note})" if tree.note else ""))
        drivers = [tree]

    # 2. localize each driver
    localization: dict[str, DrillResult] = {}
    for d in drivers:
        r = drill_down(engine, d.metric, window, segment, weeks=weeks)
        localization[d.metric] = r
        for s in r.steps:
            dims = [x for x in DIMENSIONS if x not in s.segment]
            step("drill_dimension", f"{d.metric} in {_seg(s.segment)} by {', '.join(dims)}", s.summary())
        loc_a, loc_b = _values(engine, d.metric, window, r.segment, weeks)
        evidence.append({
            "type": "metric_drop" if loc_a < loc_b else "metric_rise",
            "metric": d.metric,
            "segment": r.segment,
            "expected": _f(loc_b), "actual": _f(loc_a),
            "contribution_pct": _f(100 * d.share),
            "localized_share_pct": _f(100 * r.share_of_root),
            "path": [n.metric for n in _path_to(tree, d.metric)],
        })

    # the anomalous metric itself: where did it change?
    impact_drill = None
    if drivers and drivers[0] is not tree:
        impact_drill = drill_down(engine, metric, window, segment, weeks=weeks)

    # 3. business impact
    impact: dict[str, Any] = {}
    for where, seg in [("segment", segment)] + [("localized", localization[drivers[0].metric].segment)] * bool(
            drivers and drivers[0].metric in localization):
        for m in IMPACT_METRICS:
            if m not in engine.registry or not all(k in engine.registry.get(m).dimensions for k in seg):
                continue
            a, b = _values(engine, m, window, seg, weeks)
            if np.isfinite(a) and np.isfinite(b):
                key = {"orders": "orders", "revenue": "revenue_eur"}[m]
                impact[f"{where}_{key}_change"] = _f(a - b)
                impact[f"{where}_{key}_change_pct"] = _f(100 * (a / b - 1)) if b else None
    if impact:
        lost = impact.get("segment_orders_change")
        rev = impact.get("segment_revenue_eur_change")
        if lost is not None:
            impact["lost_orders"] = _f(max(0.0, -lost))
        if rev is not None:
            impact["lost_revenue_eur"] = _f(max(0.0, -rev))
        step("quantify_impact", f"orders and revenue in {_seg(segment)} vs the same window in previous weeks",
             describe_impact(impact))

    a, b = tree.actual, tree.baseline
    return Investigation(metric, segment, window, a, b, tree, drivers, localization, impact_drill, impact,
                         trail, evidence)


def investigate_episode(engine: MetricEngine, graph: MetricGraph, episode: Any, **kw) -> Investigation:
    """Investigate a ``monitoring.detect.Episode`` (or anything with metric/segment/start/end)."""
    return investigate(engine, graph, episode.metric, episode.start, episode.end, episode.segment, **kw)


def describe_impact(impact: dict[str, Any]) -> str:
    """'orders -2,625 (-25.2%), revenue -EUR 203,576 (-24.8%); of which in the localized segment: ...'."""
    def part(prefix: str) -> str:
        bits = []
        o, op = impact.get(f"{prefix}_orders_change"), impact.get(f"{prefix}_orders_change_pct")
        r, rp = impact.get(f"{prefix}_revenue_eur_change"), impact.get(f"{prefix}_revenue_eur_change_pct")
        if o is not None:
            bits.append(f"orders {o:+,.0f}" + (f" ({op:+.1f}%)" if op is not None else ""))
        if r is not None:
            bits.append(f"revenue {'-' if r < 0 else '+'}EUR {abs(r):,.0f}" + (f" ({rp:+.1f}%)" if rp is not None else ""))
        return ", ".join(bits)

    out = part("segment")
    loc = part("localized")
    if loc and loc != out:
        out += f"; of which in the localized segment: {loc}"
    return out


def _values(engine, metric, window, seg, weeks) -> tuple[float, float]:
    if not all(k in engine.registry.get(metric).dimensions for k in seg):
        return float("nan"), float("nan")
    return compare_total(engine, metric, window, seg, weeks)


def _path_to(tree: ContributionNode, metric: str) -> list[ContributionNode]:
    def go(n, acc):
        if n.metric == metric:
            return acc + [n]
        for c in n.children:
            r = go(c, acc + [n])
            if r:
                return r
        return None
    return go(tree, []) or [tree]


def _seg(seg: dict) -> str:
    ordered = [f"{k}={seg[k]}" for k in DIMENSIONS if k in seg] + [f"{k}={v}" for k, v in seg.items() if k not in DIMENSIONS]
    return ", ".join(ordered) or "total"


def _iso(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def _f(x: Any) -> Optional[float]:
    try:
        return None if x is None or not np.isfinite(x) else round(float(x), 6)
    except TypeError:
        return None


# ---------------------------------------------------------------------------
# command line
# ---------------------------------------------------------------------------

def main(argv: list[str] | None = None) -> int:
    from graph.__main__ import DEFAULT_GRAPH

    ap = argparse.ArgumentParser(description="Investigate a metric change: follow the graph, quantify, localize.")
    ap.add_argument("--data", default="data/generated/scenarios")
    ap.add_argument("--graph", default=str(DEFAULT_GRAPH))
    ap.add_argument("--metric", required=True)
    ap.add_argument("--segment", default="", help="e.g. country=DE,platform=android")
    ap.add_argument("--start", default=None, help="UTC start; with --end. Default: strongest detected episode")
    ap.add_argument("--end", default=None)
    ap.add_argument("--json", action="store_true", help="print the full result as JSON")
    args = ap.parse_args(argv)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    data = Path(args.data)
    if not (data / "marketplace_hourly.csv").exists():
        print(f"no data in {data}. Generate it first:  python -m simulator --scenarios all", file=sys.stderr)
        return 2
    engine = MetricEngine.from_dir(data)
    graph = MetricGraph.load(args.graph)
    segment = dict(p.split("=", 1) for p in args.segment.split(",") if p)

    if args.start and args.end:
        start, end = args.start, args.end
    else:
        from monitoring.detect import Detector

        lo, hi = engine.time_range()
        since = (hi + pd.Timedelta(hours=1)).floor("D") - pd.Timedelta(days=7)
        eps = [e for e in Detector(engine).detect([args.metric], by=list(segment), segment=None, start=since)
               if all(e.segment.get(k) == v for k, v in segment.items())]
        if not eps:
            print(f"no anomaly detected for {args.metric} in {_seg(segment)} since {since:%Y-%m-%d}")
            return 1
        ep = eps[0]
        start, end = ep.start, ep.end
        print(f"strongest anomaly: {args.metric} in {_seg(ep.segment)} {ep.start:%m-%d %H:%M} -> "
              f"{ep.end:%m-%d %H:%M} (z={ep.peak_z:.1f})\n")

    res = investigate(engine, graph, args.metric, start, end, segment)
    if args.json:
        print(json.dumps(res.to_dict(), indent=2))
        return 0
    print(res.summary + "\n")
    for t in res.trail:
        print(f"{t['step']:2}. {t['action']:16} {t['query']}")
        print(f"    {'':16} -> {t['observation']}")
    if res.business_impact:
        print("\nbusiness impact: " + describe_impact(res.business_impact))
    return 0


if __name__ == "__main__":
    sys.exit(main())
