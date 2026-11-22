"""Unit tests for L1 scenario injector and generator integration."""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from simulator import (
    ScenarioSpec,
    SimConfig,
    build_inventory_hooks,
    default_fault_scenarios,
    default_innocent_scenarios,
    generate,
    inject_scenarios,
)
from simulator.schema import EVENTS_COLUMNS, MARKETPLACE_HOURLY_COLUMN_NAMES


def _make_baseline_df(hours: int = 120) -> pd.DataFrame:
    """Create a deterministic baseline marketplace_hourly DataFrame."""
    timestamps = pd.date_range("2026-10-15T00:00:00Z", periods=hours, freq="1h", tz="UTC")
    countries = ["DE", "UK", "FR", "US"]
    platforms = ["android", "ios"]
    categories = ["electronics", "fashion"]

    rows = []
    for ts in timestamps:
        for country in countries:
            for platform in platforms:
                for category in categories:
                    rows.append(
                        {
                            "timestamp": ts,
                            "country": country,
                            "platform": platform,
                            "category": category,
                            "sessions": 1000,
                            "searches": 600,
                            "search_clicks": 300,
                            "recommendation_impressions": 500,
                            "recommendation_clicks": 80,
                            "cart_adds": 180,
                            "checkouts": 120,
                            "payment_attempts": 100,
                            "orders": 94,
                            "refunds": 5,
                            "revenue": 4700.0,
                            "ad_impressions": 4000,
                            "ad_clicks": 200,
                            "ad_spend": 150.0,
                            "active_listings": 500,
                            "out_of_stock_views": 10,
                        }
                    )
    return pd.DataFrame(rows, columns=list(MARKETPLACE_HOURLY_COLUMN_NAMES))


class TestScenarioInjector(unittest.TestCase):
    def test_payment_failure_targets_only_de_android(self) -> None:
        baseline = _make_baseline_df(hours=48)
        fault = default_fault_scenarios("2026-10-15T10:00:00Z")[0]

        modified, events, gt = inject_scenarios(baseline, [fault])

        ts = pd.to_datetime(modified["timestamp"], utc=True)
        in_window = (ts >= fault.start_time) & (ts <= fault.end_time)
        de_android = (modified["country"] == "DE") & (modified["platform"] == "android")

        affected = modified.loc[in_window & de_android]
        self.assertTrue((affected["payment_attempts"] == 100).all())
        self.assertTrue((affected["orders"] < 50).all())
        self.assertTrue((affected["revenue"] < 2500.0).all())

        untouched = modified.loc[
            in_window & (modified["country"] == "DE") & (modified["platform"] == "ios")
        ]
        self.assertTrue((untouched["orders"] == 94).all())

        self.assertEqual(list(events.columns), EVENTS_COLUMNS)
        self.assertEqual(len(events), 1)
        self.assertEqual(events.iloc[0]["event_type"], "release")
        self.assertFalse(bool(events.iloc[0]["planned"]))
        self.assertEqual(gt["scenarios"][0]["kind"], "fault")
        self.assertEqual(gt["scenarios"][0]["related_event_id"], "evt_001")

    def test_all_fault_and_innocent_scenarios_preserve_funnel_and_contracts(self) -> None:
        baseline = _make_baseline_df(hours=120)
        scenarios = default_fault_scenarios("2026-10-15T00:00:00Z") + default_innocent_scenarios(
            "2026-10-15T00:00:00Z"
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            gt_path = Path(tmpdir) / "ground_truth.json"
            modified, events, gt = inject_scenarios(baseline, scenarios, ground_truth_path=gt_path)

            self.assertTrue(gt_path.exists())
            loaded_gt = json.loads(gt_path.read_text(encoding="utf-8"))
            self.assertEqual(len(loaded_gt["scenarios"]), len(scenarios))
            self.assertEqual(loaded_gt, gt)

        self.assertTrue((modified["orders"] <= modified["payment_attempts"]).all())
        self.assertTrue((modified["payment_attempts"] <= modified["checkouts"]).all())
        self.assertTrue((modified["search_clicks"] <= modified["searches"]).all())
        self.assertTrue((modified["refunds"] <= modified["orders"]).all())

        # Late/missing data dropped 4 hours * 2 categories = 8 rows for DE/ios.
        self.assertEqual(len(modified), len(baseline) - 8)

        planned_types = set(events.loc[events["planned"], "event_type"])
        self.assertEqual(planned_types, {"campaign", "holiday"})
        self.assertIn("data_delay", set(events["event_type"]))

        # Event IDs are strictly sequential without gaps.
        self.assertEqual(
            list(events["event_id"]),
            [f"evt_{i:03d}" for i in range(1, len(events) + 1)],
        )

        # Ground-truth and event timestamps use 'Z' suffix per contract.
        for entry in gt["scenarios"]:
            self.assertTrue(entry["start_time"].endswith("Z"))
            self.assertTrue(entry["end_time"].endswith("Z"))
        self.assertTrue(events["start_time"].str.endswith("Z").all())

    def test_ad_budget_exhaustion_zeros_late_hours_and_explains_session_drop(self) -> None:
        baseline = _make_baseline_df(hours=24)
        ad_fault = ScenarioSpec(
            scenario_id="scn_fr_ad",
            kind="fault",
            scenario_type="ad_budget_exhaustion",
            root_cause="ad_budget_spent_too_fast",
            description="Ad budget exhausted at 10:00 local time (FR, UTC+1)",
            start_time=pd.Timestamp("2026-10-15T00:00:00Z"),
            end_time=pd.Timestamp("2026-10-15T23:00:00Z"),
            affected_segment={"country": "FR"},
            primary_metric="ad_spend",
            downstream_metrics=["sessions", "orders"],
            params={"exhaust_hour": 10, "utc_offset_hours": 1},
        )

        modified, events, _ = inject_scenarios(baseline, [ad_fault])
        fr = modified[modified["country"] == "FR"]
        fr_base = baseline[baseline["country"] == "FR"]
        local_hours = (pd.to_datetime(fr["timestamp"], utc=True) + pd.Timedelta(hours=1)).dt.hour

        self.assertTrue((fr.loc[local_hours < 10, "ad_spend"] > 150.0).all())
        self.assertTrue((fr.loc[local_hours >= 10, "ad_spend"] == 0.0).all())
        self.assertTrue((fr.loc[local_hours >= 10, "ad_clicks"] == 0).all())

        # Session drop in late hours equals the lost ad_clicks (organic sessions untouched).
        late_session_drop = (
            fr_base.loc[local_hours >= 10, "sessions"] - fr.loc[local_hours >= 10, "sessions"]
        )
        self.assertTrue((late_session_drop == 200).all())
        self.assertTrue(events.empty)

    def test_promo_and_holiday_scale_ads_and_recommendations_uniformly(self) -> None:
        baseline = _make_baseline_df(hours=72)
        innocents = default_innocent_scenarios("2026-10-15T00:00:00Z")[:2]
        modified, _, _ = inject_scenarios(baseline, innocents)

        uk = modified[
            (modified["country"] == "UK")
            & (pd.to_datetime(modified["timestamp"], utc=True) < pd.Timestamp("2026-10-17T00:00:00Z"))
        ]
        self.assertTrue(((uk["ad_impressions"] / uk["sessions"]).round(2) == 4.0).all())
        self.assertTrue(((uk["recommendation_impressions"] / uk["sessions"]).round(2) == 0.5).all())

        us = modified[
            (modified["country"] == "US")
            & (pd.to_datetime(modified["timestamp"], utc=True) >= pd.Timestamp("2026-10-17T00:00:00Z"))
            & (pd.to_datetime(modified["timestamp"], utc=True) <= pd.Timestamp("2026-10-17T23:00:00Z"))
        ]
        self.assertTrue(((us["ad_impressions"] / us["sessions"]).round(2) == 4.0).all())
        self.assertTrue(((us["recommendation_impressions"] / us["sessions"]).round(2) == 0.5).all())

    def test_stock_out_inventory_hooks_leave_supply_chain_evidence(self) -> None:
        cfg = SimConfig(start="2026-10-01", days=14, products_per_category=30)
        stock_fault = ScenarioSpec(
            scenario_id="scn_elec_stockout",
            kind="fault",
            scenario_type="stock_out",
            root_cause="inventory_stock_out",
            description="Late supplier deliveries in electronics",
            start_time=pd.Timestamp("2026-10-06T00:00:00Z"),
            end_time=pd.Timestamp("2026-10-11T23:00:00Z"),
            affected_segment={"category": "electronics"},
            primary_metric="out_of_stock_rate",
            downstream_metrics=["cart_adds", "orders", "revenue"],
            params={"delivery_delay_days": 6, "po_lookback_days": 5},
        )

        baseline_res = generate(cfg)
        hooks = build_inventory_hooks([stock_fault])
        fault_res = generate(cfg, inventory_hooks=hooks)

        # Purchase orders for electronics have delayed arrivals in the fault run.
        elec_pos = fault_res.world.purchase_orders
        elec_pos = elec_pos[elec_pos["category"] == "electronics"]
        base_pos = baseline_res.world.purchase_orders
        base_pos = base_pos[base_pos["category"] == "electronics"]
        self.assertGreater(
            (elec_pos["expected_arrival"] != elec_pos["actual_arrival"]).sum(),
            (base_pos["expected_arrival"] != base_pos["actual_arrival"]).sum(),
        )

        # inject_scenarios with apply_stock_out_to_df=False records ground truth without double-applying.
        final_df, _, gt = inject_scenarios(
            fault_res.marketplace_hourly, [stock_fault], apply_stock_out_to_df=False
        )
        self.assertEqual(len(final_df), len(fault_res.marketplace_hourly))
        self.assertEqual(gt["scenarios"][0]["scenario_id"], "scn_elec_stockout")

    def test_missing_columns_raises(self) -> None:
        bad_df = pd.DataFrame({"timestamp": ["2026-10-15T00:00:00Z"]})
        fault = default_fault_scenarios("2026-10-15T00:00:00Z")[0]
        with self.assertRaises(ValueError):
            inject_scenarios(bad_df, [fault])


if __name__ == "__main__":
    unittest.main()
