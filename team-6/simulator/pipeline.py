"""One call that builds a full L1 dataset: baseline + scenarios + ground truth.

Used by the CLI (``python -m simulator --scenarios all``) and meant to be reused
by the L7 demo script.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

import pandas as pd

from .catalog import category_availability
from .config import SimConfig
from .generator import SimulationResult, World, build_world, generate
from .scenarios import (
    ScenarioSpec,
    build_inventory_hooks,
    default_fault_scenarios,
    default_innocent_scenarios,
    inject_scenarios,
    write_ground_truth,
)
from .schema import TIMESTAMP_FORMAT

ScenarioSet = Literal["none", "faults", "innocent", "all"]

# The default scenario suites run up to ~4.6 days after the anchor.
_SUITE_SPAN = pd.Timedelta(days=5)


def default_anchor(cfg: SimConfig) -> pd.Timestamp:
    """10:00 UTC on the first day of the last week, so there is history before it."""
    return pd.Timestamp(cfg.start, tz="UTC") + pd.Timedelta(days=cfg.days - 7, hours=10)


def select_scenarios(which: ScenarioSet, anchor: pd.Timestamp) -> list[ScenarioSpec]:
    if which == "none":
        return []
    out: list[ScenarioSpec] = []
    if which in ("faults", "all"):
        out += default_fault_scenarios(anchor)
    if which in ("innocent", "all"):
        out += default_innocent_scenarios(anchor)
    return out


def build_dataset(
    cfg: Optional[SimConfig] = None,
    scenarios: ScenarioSet | list[ScenarioSpec] = "all",
    anchor: Optional[pd.Timestamp | str] = None,
    out_dir: Optional[str | Path] = None,
) -> tuple[SimulationResult, dict]:
    """Generate the baseline, apply the scenarios and (optionally) write everything.

    Stock-out scenarios are produced through ``InventoryHooks`` (late supplier
    deliveries), so ``inventory_daily`` and ``purchase_orders`` hold the real
    evidence; all other scenarios are applied to the hourly table.

    Returns ``(result, ground_truth)``. With ``out_dir`` the contract tables,
    the supporting tables and ``ground_truth.json`` are written there.
    """
    cfg = cfg or SimConfig()
    anchor_ts = pd.Timestamp(anchor) if anchor is not None else default_anchor(cfg)
    if anchor_ts.tzinfo is None:
        anchor_ts = anchor_ts.tz_localize("UTC")

    specs = select_scenarios(scenarios, anchor_ts) if isinstance(scenarios, str) else list(scenarios)
    if specs:
        start = pd.Timestamp(cfg.start, tz="UTC")
        end = start + pd.Timedelta(days=cfg.days)
        first = min(s.start_time for s in specs)
        last = max(s.end_time for s in specs)
        if pd.Timestamp(first) < start + pd.Timedelta(days=7):
            raise ValueError("scenarios need at least 7 days of normal history before them")
        if pd.Timestamp(last) >= end:
            raise ValueError(
                f"scenarios end at {last}, after the simulated period ({end}); "
                f"use more --days or an earlier --anchor (default suites span ~{_SUITE_SPAN.days} days)"
            )

    result = generate(cfg, inventory_hooks=build_inventory_hooks(specs))
    hourly, events, truth = inject_scenarios(
        result.marketplace_hourly, specs, ground_truth_path=None, apply_stock_out_to_df=False
    )
    stock_outs = [s for s in specs if s.scenario_type == "stock_out"]
    if stock_outs:
        set_observed_stock_out_windows(truth, stock_outs, result.world, build_world(cfg).inventory_daily)
    if out_dir is not None:
        write_ground_truth(truth, Path(out_dir) / "ground_truth.json")
    result.marketplace_hourly = hourly
    result.events = events
    if out_dir is not None:
        result.write(out_dir)
    return result, truth


# A day counts as affected when the popularity-weighted share of views landing
# on out-of-stock products is this much above the same day without the fault.
STOCK_OUT_EXCESS_THRESHOLD = 0.02


def observed_stock_out_days(
    fault_world: World, base_inventory: pd.DataFrame, category: Optional[str]
) -> pd.DataFrame:
    """Per day: out-of-stock view share with and without the fault, and the excess."""
    products = fault_world.products
    fault = category_availability(fault_world.inventory_daily, products)
    base = category_availability(base_inventory, products)
    if category:
        fault, base = fault[fault["category"] == category], base[base["category"] == category]
    f = fault.groupby("date")["oos_view_share"].mean()
    b = base.groupby("date")["oos_view_share"].mean()
    out = pd.DataFrame({"base": b, "fault": f})
    out["excess"] = out["fault"] - out["base"]
    return out


def set_observed_stock_out_windows(
    truth: dict, specs: list[ScenarioSpec], fault_world: World, base_inventory: pd.DataFrame
) -> None:
    """Replace each stock-out scenario's window with the days it is visible in the data.

    A supply-chain fault does not end when the delays stop: shelves stay empty
    until the late deliveries arrive. The configured end time is therefore not
    the real end, and the ground truth uses the first to the last affected day
    instead (00:00 of the first day to 23:00 of the last, both UTC). If the
    stock-out is still going when the data ends, the window ends at the last hour.
    """
    by_id = {s["scenario_id"]: s for s in truth["scenarios"]}
    for spec in specs:
        entry = by_id.get(spec.scenario_id)
        if entry is None:
            continue
        days = observed_stock_out_days(fault_world, base_inventory, spec.affected_segment.get("category"))
        hit = days.index[days["excess"] > STOCK_OUT_EXCESS_THRESHOLD]
        if not len(hit):
            continue  # no visible effect: keep the configured window
        first, last = hit.min(), hit.max()
        entry["start_time"] = first.strftime(TIMESTAMP_FORMAT)
        entry["end_time"] = (last + pd.Timedelta(hours=23)).strftime(TIMESTAMP_FORMAT)
