"""Command line.

    python -m simulator                                   # baseline only
    python -m simulator --scenarios all                   # baseline + faults + innocent changes
    python -m simulator --scenarios faults --days 35 --anchor 2026-10-29T10:00:00Z
"""

from __future__ import annotations

import argparse
import time

from .config import SimConfig
from .pipeline import build_dataset, default_anchor


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description="Generate marketplace data, optionally with injected scenarios.")
    ap.add_argument("--start", default=SimConfig.start, help="first day, YYYY-MM-DD (UTC)")
    ap.add_argument("--days", type=int, default=SimConfig.days)
    ap.add_argument("--seed", type=int, default=SimConfig.seed)
    ap.add_argument(
        "--scenarios", choices=["none", "faults", "innocent", "all"], default="none",
        help="which default scenario suites to inject (default: none = clean baseline)",
    )
    ap.add_argument(
        "--anchor", default=None,
        help="UTC time the scenario suites start from (default: 10:00 on the first day of the last week)",
    )
    ap.add_argument("--out", default=None, help="output folder (default: data/generated/<baseline|scenarios>)")
    args = ap.parse_args(argv)

    cfg = SimConfig(start=args.start, days=args.days, seed=args.seed)
    out_dir = args.out or ("data/generated/baseline" if args.scenarios == "none" else "data/generated/scenarios")

    t0 = time.perf_counter()
    try:
        result, truth = build_dataset(cfg, args.scenarios, args.anchor, out_dir)
    except ValueError as exc:
        ap.error(str(exc))
    h = result.marketplace_hourly
    daily = h.groupby(h["timestamp"].dt.floor("D"))[["sessions", "orders", "revenue"]].sum()

    print(f"wrote {len(h):,} rows to {out_dir} in {time.perf_counter() - t0:.1f}s")
    print(f"per day: {daily['sessions'].mean():,.0f} sessions, {daily['orders'].mean():,.0f} orders, "
          f"EUR {daily['revenue'].mean():,.0f} revenue")
    print(f"conversion {daily['orders'].sum() / daily['sessions'].sum():.2%}; "
          f"purchase orders: {len(result.world.purchase_orders):,}; "
          f"out-of-stock product views: {h['out_of_stock_views'].sum():,}")
    if truth["scenarios"]:
        anchor = args.anchor or default_anchor(cfg).strftime("%Y-%m-%dT%H:%M:%SZ")
        print(f"\ninjected {len(truth['scenarios'])} scenarios from {anchor} "
              f"({len(result.events)} events; ground truth in {out_dir}/ground_truth.json):")
        for s in truth["scenarios"]:
            seg = ", ".join(f"{k}={v}" for k, v in s["affected_segment"].items()) or "all"
            print(f"  {s['kind']:8} {s['scenario_id']:28} {seg:30} {s['start_time']} -> {s['end_time']}")


if __name__ == "__main__":
    main()
