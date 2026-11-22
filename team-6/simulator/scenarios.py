"""Scenario injector for L1 marketplace simulator.

Applies fault scenarios and innocent changes to a baseline ``marketplace_hourly``
DataFrame, produces the corresponding ``events`` table and ``ground_truth.json``
artifact defined in ``contracts/contracts.md``, and provides ``InventoryHooks``
builders so stock-out faults leave real supply-chain evidence in
``inventory_daily`` and ``purchase_orders``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Literal

import pandas as pd

from simulator.catalog import InventoryHooks
from simulator.config import SimConfig
from simulator.schema import (
    EVENTS_COLUMNS,
    MARKETPLACE_HOURLY_COLUMN_NAMES,
    TIMESTAMP_FORMAT,
)

# Re-export as tuples for backwards compatibility with callers/tests.
MARKETPLACE_COLUMNS: tuple[str, ...] = tuple(MARKETPLACE_HOURLY_COLUMN_NAMES)

_DEFAULT_UTC_OFFSETS: dict[str, int] = SimConfig().utc_offset_hours

_ALL_TRAFFIC_INT_COLS: list[str] = [
    "sessions",
    "searches",
    "search_clicks",
    "recommendation_impressions",
    "recommendation_clicks",
    "cart_adds",
    "checkouts",
    "payment_attempts",
    "orders",
    "refunds",
    "ad_impressions",
    "ad_clicks",
]
_ALL_TRAFFIC_FLOAT_COLS: list[str] = ["revenue", "ad_spend"]

_FUNNEL_INT_COLS: list[str] = [
    "sessions",
    "searches",
    "search_clicks",
    "recommendation_impressions",
    "recommendation_clicks",
    "cart_adds",
    "checkouts",
    "payment_attempts",
    "orders",
    "refunds",
]


@dataclass(frozen=True)
class ScenarioSpec:
    """Configuration for a single fault or innocent change scenario."""

    scenario_id: str
    kind: Literal["fault", "innocent"]
    scenario_type: str
    root_cause: str
    description: str
    start_time: pd.Timestamp
    end_time: pd.Timestamp
    affected_segment: dict[str, str]
    primary_metric: str
    downstream_metrics: list[str] = field(default_factory=list)
    event_type: str | None = None
    planned: bool = False
    event_lead_minutes: int = 0
    expected_effect: str | None = None
    params: dict[str, Any] = field(default_factory=dict)


def _ensure_utc(ts: pd.Timestamp | str) -> pd.Timestamp:
    parsed = pd.Timestamp(ts)
    if parsed.tzinfo is None:
        return parsed.tz_localize("UTC")
    return parsed.tz_convert("UTC")


def _format_utc_z(ts: pd.Timestamp) -> str:
    return _ensure_utc(ts).strftime(TIMESTAMP_FORMAT)


def _segment_mask(
    df: pd.DataFrame,
    start_time: pd.Timestamp,
    end_time: pd.Timestamp,
    segment: dict[str, str],
) -> pd.Series:
    timestamps = pd.to_datetime(df["timestamp"], utc=True)
    mask = (timestamps >= start_time) & (timestamps <= end_time)
    for dim, val in segment.items():
        if dim not in df.columns:
            raise ValueError(f"Unknown segment dimension: {dim}")
        mask &= df[dim] == val
    return mask


def _scale_int_columns(
    df: pd.DataFrame, mask: pd.Series, columns: list[str], factor: float
) -> None:
    for col in columns:
        df.loc[mask, col] = (df.loc[mask, col] * factor).round().clip(lower=0).astype(int)


def _scale_float_columns(
    df: pd.DataFrame, mask: pd.Series, columns: list[str], factor: float
) -> None:
    for col in columns:
        df.loc[mask, col] = (df.loc[mask, col] * factor).clip(lower=0.0).round(2)


def _scale_funnel_by_series(
    df: pd.DataFrame, mask: pd.Series, ratio: pd.Series
) -> None:
    """Scale session-driven funnel columns row-wise by ``ratio``."""
    for col in _FUNNEL_INT_COLS:
        df.loc[mask, col] = (
            (df.loc[mask, col].astype(float) * ratio).round().clip(lower=0).astype(int)
        )
    df.loc[mask, "revenue"] = (
        (df.loc[mask, "revenue"].astype(float) * ratio).clip(lower=0.0).round(2)
    )


def _enforce_funnel_bounds(df: pd.DataFrame, mask: pd.Series) -> None:
    """Keep basic funnel invariants valid after scaling."""
    df.loc[mask, "search_clicks"] = df.loc[mask, ["search_clicks", "searches"]].min(axis=1)
    df.loc[mask, "payment_attempts"] = df.loc[mask, ["payment_attempts", "checkouts"]].min(axis=1)
    df.loc[mask, "orders"] = df.loc[mask, ["orders", "payment_attempts"]].min(axis=1)
    df.loc[mask, "refunds"] = df.loc[mask, ["refunds", "orders"]].min(axis=1)


def _apply_payment_failure(
    df: pd.DataFrame, mask: pd.Series, spec: ScenarioSpec
) -> pd.DataFrame:
    """Drop payment success rate without changing upstream payment_attempts."""
    drop_rate = float(spec.params.get("drop_rate", 0.65))
    factor = max(0.0, 1.0 - drop_rate)
    _scale_int_columns(df, mask, ["orders", "refunds"], factor)
    _scale_float_columns(df, mask, ["revenue"], factor)
    _enforce_funnel_bounds(df, mask)
    return df


def _apply_bad_search_release(
    df: pd.DataFrame, mask: pd.Series, spec: ScenarioSpec
) -> pd.DataFrame:
    """Drop search clicks and propagate proportionally down the funnel."""
    drop_rate = float(spec.params.get("drop_rate", 0.50))
    factor = max(0.0, 1.0 - drop_rate)
    _scale_int_columns(
        df,
        mask,
        ["search_clicks", "cart_adds", "checkouts", "payment_attempts", "orders", "refunds"],
        factor,
    )
    _scale_float_columns(df, mask, ["revenue"], factor)
    _enforce_funnel_bounds(df, mask)
    return df


def _apply_stock_out(
    df: pd.DataFrame, mask: pd.Series, spec: ScenarioSpec
) -> pd.DataFrame:
    """Fallback DataFrame edit when stock-out was not generated via InventoryHooks.

    When running with ``generator.generate(..., inventory_hooks=build_inventory_hooks(...))``,
    pass ``apply_stock_out_to_df=False`` to ``inject_scenarios`` so the supply-chain
    simulation itself drives the hourly counts.
    """
    listing_drop = float(spec.params.get("listing_drop_rate", 0.40))
    oos_multiplier = float(spec.params.get("oos_multiplier", 4.0))
    conversion_drop = float(spec.params.get("conversion_drop_rate", 0.45))

    _scale_int_columns(df, mask, ["active_listings"], max(0.0, 1.0 - listing_drop))
    _scale_int_columns(df, mask, ["out_of_stock_views"], oos_multiplier)
    zero_oos = mask & (df["out_of_stock_views"] == 0)
    if zero_oos.any():
        df.loc[zero_oos, "out_of_stock_views"] = (
            (df.loc[zero_oos, "sessions"] * 0.15).round().clip(lower=1).astype(int)
        )

    factor = max(0.0, 1.0 - conversion_drop)
    _scale_int_columns(
        df, mask, ["cart_adds", "checkouts", "payment_attempts", "orders", "refunds"], factor
    )
    _scale_float_columns(df, mask, ["revenue"], factor)
    _enforce_funnel_bounds(df, mask)
    return df


def _apply_ad_budget_exhaustion(
    df: pd.DataFrame, mask: pd.Series, spec: ScenarioSpec
) -> pd.DataFrame:
    """Front-load ad spend early in local time and zero it out after exhaust_hour.

    Only paid-traffic-attributed sessions (equal to the delta in ``ad_clicks``)
    are added or removed, leaving organic sessions untouched so the session drop
    is fully explained by the drop in ``ad_clicks``.
    """
    exhaust_hour = int(spec.params.get("exhaust_hour", 10))
    early_multiplier = float(spec.params.get("early_multiplier", 2.2))
    country = spec.affected_segment.get("country", "")
    utc_offset = int(
        spec.params.get("utc_offset_hours", _DEFAULT_UTC_OFFSETS.get(country, 0))
    )

    local_hours = (
        pd.to_datetime(df["timestamp"], utc=True) + pd.Timedelta(hours=utc_offset)
    ).dt.hour
    early_mask = mask & (local_hours < exhaust_hour)
    late_mask = mask & (local_hours >= exhaust_hour)

    # Early hours: ad clicks spike and add their delta to sessions + downstream funnel.
    old_early_sessions = df.loc[early_mask, "sessions"].astype(float)
    old_early_clicks = df.loc[early_mask, "ad_clicks"].astype(float)
    _scale_int_columns(df, early_mask, ["ad_impressions", "ad_clicks"], early_multiplier)
    _scale_float_columns(df, early_mask, ["ad_spend"], early_multiplier)
    click_gain = df.loc[early_mask, "ad_clicks"].astype(float) - old_early_clicks
    early_ratio = ((old_early_sessions + click_gain) / old_early_sessions.replace(0, 1)).clip(
        lower=1.0
    )
    _scale_funnel_by_series(df, early_mask, early_ratio)

    # Late hours: ad spend/clicks drop to 0, removing only the paid-click sessions.
    old_late_sessions = df.loc[late_mask, "sessions"].astype(float)
    lost_clicks = df.loc[late_mask, "ad_clicks"].astype(float)
    df.loc[late_mask, ["ad_impressions", "ad_clicks"]] = 0
    df.loc[late_mask, "ad_spend"] = 0.0
    new_late_sessions = (old_late_sessions - lost_clicks).clip(lower=0.0)
    late_ratio = (new_late_sessions / old_late_sessions.replace(0, 1)).clip(lower=0.0)
    _scale_funnel_by_series(df, late_mask, late_ratio)

    _enforce_funnel_bounds(df, mask)
    return df


def _apply_planned_promo(
    df: pd.DataFrame, mask: pd.Series, spec: ScenarioSpec
) -> pd.DataFrame:
    """Lift traffic, ads, recommendations, and funnel uniformly for a planned promo."""
    lift_factor = float(spec.params.get("lift_factor", 1.25))
    _scale_int_columns(df, mask, _ALL_TRAFFIC_INT_COLS, lift_factor)
    _scale_float_columns(df, mask, _ALL_TRAFFIC_FLOAT_COLS, lift_factor)
    _enforce_funnel_bounds(df, mask)
    return df


def _apply_holiday(
    df: pd.DataFrame, mask: pd.Series, spec: ScenarioSpec
) -> pd.DataFrame:
    """Apply a country-wide holiday shift uniformly across traffic, ads, and funnel."""
    traffic_factor = float(spec.params.get("traffic_factor", 0.80))
    _scale_int_columns(df, mask, _ALL_TRAFFIC_INT_COLS, traffic_factor)
    _scale_float_columns(df, mask, _ALL_TRAFFIC_FLOAT_COLS, traffic_factor)
    _enforce_funnel_bounds(df, mask)
    return df


def _apply_late_missing_data(
    df: pd.DataFrame, mask: pd.Series, spec: ScenarioSpec
) -> pd.DataFrame:
    """Drop rows in the affected slice to simulate late/missing ingestion."""
    return df.loc[~mask].reset_index(drop=True)


_SCENARIO_HANDLERS: dict[str, Callable[[pd.DataFrame, pd.Series, ScenarioSpec], pd.DataFrame]] = {
    "payment_failure": _apply_payment_failure,
    "bad_search_release": _apply_bad_search_release,
    "stock_out": _apply_stock_out,
    "ad_budget_exhaustion": _apply_ad_budget_exhaustion,
    "planned_promo": _apply_planned_promo,
    "holiday": _apply_holiday,
    "late_missing_data": _apply_late_missing_data,
}


def make_stock_out_hooks(
    category: str,
    start_date: pd.Timestamp | str,
    end_date: pd.Timestamp | str,
    delivery_delay_days: int = 5,
    order_qty_factor: float = 1.0,
) -> InventoryHooks:
    """Create ``InventoryHooks`` that inject late deliveries or under-ordering.

    Pass the returned hooks to ``simulator.generate(cfg, inventory_hooks=...)`` so
    ``inventory_daily`` and ``purchase_orders`` contain genuine supply-chain evidence.
    """
    start_d = _ensure_utc(start_date).normalize()
    end_d = _ensure_utc(end_date).normalize()

    def _in_scope(product_row: pd.Series, order_date: pd.Timestamp) -> bool:
        od = _ensure_utc(order_date).normalize()
        return product_row["category"] == category and start_d <= od <= end_d

    def _delay(product_row: pd.Series, order_date: pd.Timestamp) -> int:
        return delivery_delay_days if _in_scope(product_row, order_date) else 0

    def _qty(product_row: pd.Series, order_date: pd.Timestamp) -> float:
        return order_qty_factor if _in_scope(product_row, order_date) else 1.0

    return InventoryHooks(
        delivery_delay=_delay if delivery_delay_days else None,
        order_qty_factor=_qty if order_qty_factor != 1.0 else None,
    )


def build_inventory_hooks(scenarios: list[ScenarioSpec]) -> InventoryHooks | None:
    """Combine ``stock_out`` scenarios into a single ``InventoryHooks`` object."""
    stock_specs = [s for s in scenarios if s.scenario_type == "stock_out"]
    if not stock_specs:
        return None

    def _delay(product_row: pd.Series, order_date: pd.Timestamp) -> int:
        od = _ensure_utc(order_date).normalize()
        delay = 0
        for s in stock_specs:
            cat = s.affected_segment.get("category")
            if cat and product_row["category"] != cat:
                continue
            # Start delaying POs slightly before the stock-out window so shelves empty on time.
            lookback_days = int(s.params.get("po_lookback_days", 5))
            start_d = _ensure_utc(s.start_time).normalize() - pd.Timedelta(days=lookback_days)
            end_d = _ensure_utc(s.end_time).normalize()
            if start_d <= od <= end_d:
                delay = max(delay, int(s.params.get("delivery_delay_days", 5)))
        return delay

    def _qty(product_row: pd.Series, order_date: pd.Timestamp) -> float:
        od = _ensure_utc(order_date).normalize()
        factor = 1.0
        for s in stock_specs:
            cat = s.affected_segment.get("category")
            if cat and product_row["category"] != cat:
                continue
            lookback_days = int(s.params.get("po_lookback_days", 5))
            start_d = _ensure_utc(s.start_time).normalize() - pd.Timedelta(days=lookback_days)
            end_d = _ensure_utc(s.end_time).normalize()
            if start_d <= od <= end_d:
                factor = min(factor, float(s.params.get("order_qty_factor", 1.0)))
        return factor

    return InventoryHooks(delivery_delay=_delay, order_qty_factor=_qty)


def _build_event_row(event_id: str, spec: ScenarioSpec) -> dict[str, Any]:
    event_start = spec.start_time - pd.Timedelta(minutes=spec.event_lead_minutes)
    event_end = None if spec.event_type == "release" else _format_utc_z(spec.end_time)
    return {
        "event_id": event_id,
        "start_time": _format_utc_z(event_start),
        "end_time": event_end,
        "event_type": spec.event_type,
        "planned": bool(spec.planned),
        "country": spec.affected_segment.get("country"),
        "platform": spec.affected_segment.get("platform"),
        "category": spec.affected_segment.get("category"),
        "description": spec.description,
        "expected_effect": spec.expected_effect,
    }


def _build_ground_truth_entry(
    spec: ScenarioSpec, event_id: str | None
) -> dict[str, Any]:
    return {
        "scenario_id": spec.scenario_id,
        "kind": spec.kind,
        "root_cause": spec.root_cause,
        "description": spec.description,
        "start_time": _format_utc_z(spec.start_time),
        "end_time": _format_utc_z(spec.end_time),
        "affected_segment": dict(spec.affected_segment),
        "primary_metric": spec.primary_metric,
        "downstream_metrics": list(spec.downstream_metrics),
        "related_event_id": event_id,
    }


def write_ground_truth(ground_truth: dict[str, Any], path: str | Path) -> Path:
    """Write ``ground_truth.json`` to disk, creating parent directories if needed."""
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(ground_truth, indent=2) + "\n", encoding="utf-8")
    return output_path


def inject_scenarios(
    df: pd.DataFrame,
    scenarios: list[ScenarioSpec],
    ground_truth_path: str | Path | None = None,
    apply_stock_out_to_df: bool = True,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, Any]]:
    """Apply fault and innocent scenarios to baseline marketplace data.

    Set ``apply_stock_out_to_df=False`` when ``df`` was already generated with
    ``inventory_hooks=build_inventory_hooks(scenarios)`` so stock-outs are not
    double-applied.

    Returns ``(modified_df, events_df, ground_truth_dict)`` conforming to
    ``contracts/contracts.md``.
    """
    missing_cols = [col for col in MARKETPLACE_HOURLY_COLUMN_NAMES if col not in df.columns]
    if missing_cols:
        raise ValueError(f"Missing required marketplace columns: {missing_cols}")

    modified_df = df.copy()
    modified_df["timestamp"] = pd.to_datetime(modified_df["timestamp"], utc=True)

    event_rows: list[dict[str, Any]] = []
    gt_entries: list[dict[str, Any]] = []

    for spec in scenarios:
        handler = _SCENARIO_HANDLERS.get(spec.scenario_type)
        if handler is None:
            raise ValueError(f"Unsupported scenario_type: {spec.scenario_type}")

        start_utc = _ensure_utc(spec.start_time)
        end_utc = _ensure_utc(spec.end_time)
        normalized_spec = ScenarioSpec(
            scenario_id=spec.scenario_id,
            kind=spec.kind,
            scenario_type=spec.scenario_type,
            root_cause=spec.root_cause,
            description=spec.description,
            start_time=start_utc,
            end_time=end_utc,
            affected_segment=spec.affected_segment,
            primary_metric=spec.primary_metric,
            downstream_metrics=spec.downstream_metrics,
            event_type=spec.event_type,
            planned=spec.planned,
            event_lead_minutes=spec.event_lead_minutes,
            expected_effect=spec.expected_effect,
            params=spec.params,
        )

        if not (normalized_spec.scenario_type == "stock_out" and not apply_stock_out_to_df):
            mask = _segment_mask(
                modified_df, start_utc, end_utc, normalized_spec.affected_segment
            )
            modified_df = handler(modified_df, mask, normalized_spec)

        event_id: str | None = None
        if normalized_spec.event_type is not None:
            event_id = f"evt_{len(event_rows) + 1:03d}"
            event_rows.append(_build_event_row(event_id, normalized_spec))

        gt_entries.append(_build_ground_truth_entry(normalized_spec, event_id))

    events_df = pd.DataFrame(event_rows, columns=list(EVENTS_COLUMNS))
    ground_truth = {"scenarios": gt_entries}

    if ground_truth_path is not None:
        write_ground_truth(ground_truth, ground_truth_path)

    return modified_df, events_df, ground_truth


def default_fault_scenarios(anchor_time: pd.Timestamp | str) -> list[ScenarioSpec]:
    """Return the standard suite of 4 fault scenarios anchored at ``anchor_time``."""
    t0 = _ensure_utc(anchor_time)
    return [
        ScenarioSpec(
            scenario_id="scn_de_android_payment",
            kind="fault",
            scenario_type="payment_failure",
            root_cause="payment_gateway_timeout",
            description="Payment failure spike on Android in DE after checkout release",
            start_time=t0,
            end_time=t0 + pd.Timedelta(hours=24),
            affected_segment={"country": "DE", "platform": "android"},
            primary_metric="payment_success_rate",
            downstream_metrics=["orders", "revenue"],
            event_type="release",
            planned=False,
            event_lead_minutes=15,
            params={"drop_rate": 0.65},
        ),
        ScenarioSpec(
            scenario_id="scn_de_bad_search",
            kind="fault",
            scenario_type="bad_search_release",
            root_cause="search_ranking_regression",
            description="Search ranking release in DE degraded search click-through",
            start_time=t0 + pd.Timedelta(days=1),
            end_time=t0 + pd.Timedelta(days=1, hours=18),
            affected_segment={"country": "DE"},
            primary_metric="search_ctr",
            downstream_metrics=["cart_adds", "checkouts", "orders", "revenue"],
            event_type="release",
            planned=False,
            event_lead_minutes=10,
            params={"drop_rate": 0.50},
        ),
        ScenarioSpec(
            scenario_id="scn_electronics_stockout",
            kind="fault",
            scenario_type="stock_out",
            root_cause="inventory_stock_out",
            description="Supplier delivery delay caused stock-outs in electronics",
            start_time=t0 + pd.Timedelta(days=2),
            end_time=t0 + pd.Timedelta(days=6, hours=13),
            affected_segment={"category": "electronics"},
            primary_metric="out_of_stock_rate",
            downstream_metrics=["cart_adds", "orders", "revenue"],
            event_type=None,
            planned=False,
            params={
                "delivery_delay_days": 5,
                "po_lookback_days": 5,
                "listing_drop_rate": 0.40,
                "oos_multiplier": 4.0,
                "conversion_drop_rate": 0.45,
            },
        ),
        ScenarioSpec(
            scenario_id="scn_fr_ad_exhaustion",
            kind="fault",
            scenario_type="ad_budget_exhaustion",
            root_cause="ad_budget_spent_too_fast",
            description="Daily ad budget exhausted before 10:00 local time in FR",
            start_time=t0 + pd.Timedelta(days=3),
            end_time=t0 + pd.Timedelta(days=3, hours=23),
            affected_segment={"country": "FR"},
            primary_metric="ad_spend",
            downstream_metrics=["ad_clicks", "sessions", "orders", "revenue"],
            event_type=None,
            planned=False,
            params={"exhaust_hour": 10, "early_multiplier": 2.2, "utc_offset_hours": 1},
        ),
    ]


def default_innocent_scenarios(anchor_time: pd.Timestamp | str) -> list[ScenarioSpec]:
    """Return the standard suite of 3 innocent-change scenarios anchored at ``anchor_time``."""
    t0 = _ensure_utc(anchor_time)
    return [
        ScenarioSpec(
            scenario_id="scn_uk_planned_promo",
            kind="innocent",
            scenario_type="planned_promo",
            root_cause="planned_campaign",
            description="Planned autumn promo in UK",
            start_time=t0,
            end_time=t0 + pd.Timedelta(days=1, hours=23),
            affected_segment={"country": "UK"},
            primary_metric="sessions",
            downstream_metrics=["orders", "revenue"],
            event_type="campaign",
            planned=True,
            expected_effect="+25% sessions and orders",
            params={"lift_factor": 1.25},
        ),
        ScenarioSpec(
            scenario_id="scn_us_holiday",
            kind="innocent",
            scenario_type="holiday",
            root_cause="national_holiday",
            description="Public holiday traffic shift in US",
            start_time=t0 + pd.Timedelta(days=2),
            end_time=t0 + pd.Timedelta(days=2, hours=23),
            affected_segment={"country": "US"},
            primary_metric="sessions",
            downstream_metrics=["orders", "revenue"],
            event_type="holiday",
            planned=True,
            expected_effect="-20% sessions during holiday",
            params={"traffic_factor": 0.80},
        ),
        ScenarioSpec(
            scenario_id="scn_de_ios_late_data",
            kind="innocent",
            scenario_type="late_missing_data",
            root_cause="ingestion_pipeline_delay",
            description="Late telemetry batch for iOS in DE",
            start_time=t0 + pd.Timedelta(days=4, hours=12),
            end_time=t0 + pd.Timedelta(days=4, hours=15),
            affected_segment={"country": "DE", "platform": "ios"},
            primary_metric="row_completeness",
            downstream_metrics=["sessions", "orders"],
            event_type="data_delay",
            planned=False,
            expected_effect="Missing rows for 4 hours until backfill",
        ),
    ]
