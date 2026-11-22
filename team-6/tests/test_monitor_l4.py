"""Tests for L4 monitoring: data quality, calendar, priorities, and triage."""

from __future__ import annotations

import pandas as pd
import pytest

from graph import GraphEdge, MetricGraph
from monitoring.event_calendar import EventCalendar
from monitoring.data_quality import check_dq
from monitoring.priorities import score_metrics, select_watchlist
from monitoring.triage import AdaptedAnomaly, triage

SLICES = {
    "country": ["DE", "UK"],
    "platform": ["android", "ios"],
    "category": ["electronics"],
}


def _make_dq_df(hours: int = 4) -> pd.DataFrame:
    ts = pd.date_range("2026-10-15T00:00:00Z", periods=hours, freq="1h", tz="UTC")
    rows = [
        {"timestamp": t, "country": c, "platform": p, "category": cat, "orders": 10}
        for t in ts
        for c in SLICES["country"]
        for p in SLICES["platform"]
        for cat in SLICES["category"]
    ]
    return pd.DataFrame(rows)


def test_dq_complete_missing_late_and_empty() -> None:
    df = _make_dq_df(hours=4)
    start = "2026-10-15T00:00:00Z"
    end = "2026-10-15T04:00:00Z"

    ok = check_dq(df, start, end, now="2026-10-15T04:00:00Z", expected_slices=SLICES)
    assert ok.fresh is True
    assert ok.complete is True
    assert ok.completeness == 1.0
    assert ok.missing == []

    # Remove one DE/android slice -> completeness drops and missing lists it
    dropped = df[~((df["country"] == "DE") & (df["platform"] == "android") & (df["timestamp"] == pd.Timestamp("2026-10-15T02:00:00Z")))]
    inc = check_dq(dropped, start, end, now="2026-10-15T04:00:00Z", expected_slices=SLICES)
    assert inc.complete is False
    assert len(inc.missing) == 1
    assert inc.missing[0]["country"] == "DE"

    # Late data: now is 3 hours after latest hour -> fails freshness
    late = check_dq(df, start, end, now="2026-10-15T06:00:00Z", expected_slices=SLICES, freshness_minutes=90)
    assert late.fresh is False

    # Empty window does not crash
    empty_res = check_dq(df.iloc[0:0], start, end, now="2026-10-15T04:00:00Z", expected_slices=SLICES)
    assert empty_res.fresh is False
    assert empty_res.complete is False
    assert empty_res.latest_hour is None


def test_dq_rejects_invalid_window() -> None:
    df = _make_dq_df(hours=2)
    with pytest.raises(ValueError):
        check_dq(df, "2026-10-15T02:00:00Z", "2026-10-15T01:00:00Z", now="2026-10-15T02:00:00Z", expected_slices=SLICES)


def test_calendar_matching_and_explanations() -> None:
    events_df = pd.DataFrame(
        [
            {
                "event_id": "evt_1",
                "start_time": "2026-10-15T09:45:00Z",
                "end_time": None,
                "event_type": "release",
                "planned": True,  # scheduled release is STILL never innocent
                "country": "DE",
                "platform": "android",
                "category": None,
                "description": "Android release",
                "expected_effect": None,
            },
            {
                "event_id": "evt_2",
                "start_time": "2026-10-15T00:00:00Z",
                "end_time": "2026-10-16T00:00:00Z",
                "event_type": "campaign",
                "planned": True,
                "country": "*",
                "platform": "",
                "category": None,
                "description": "Autumn sale",
                "expected_effect": "+25% sessions",
            },
            {
                "event_id": "evt_3",
                "start_time": "2026-10-15T10:00:00Z",
                "end_time": "2026-10-15T12:00:00Z",
                "event_type": "data_delay",
                "planned": False,
                "country": "DE",
                "platform": "ios",
                "category": None,
                "description": "Ingestion lag",
                "expected_effect": "big delay",  # unparsable -> ignored without error
            },
        ]
    )
    cal = EventCalendar.from_dataframe(events_df)

    # Window [09:00, 10:00) includes point-in-time evt_1 at 09:45 and wildcard evt_2
    exp = cal.explains("2026-10-15T09:00:00Z", "2026-10-15T10:00:00Z", {"country": "DE", "platform": "android"})
    assert [e.event_id for e in exp.planned_events] == ["evt_2"]
    assert [e.event_id for e in exp.unplanned_events] == ["evt_1"]
    assert exp.data_delay_events == []

    # End boundary exclusive: [10:00, 11:00) does NOT include evt_1 (09:45), DOES include evt_3
    exp2 = cal.explains("2026-10-15T10:00:00Z", "2026-10-15T11:00:00Z", {"country": "DE", "platform": "ios"})
    assert [e.event_id for e in exp2.data_delay_events] == ["evt_3"]
    assert exp2.unplanned_events == []

    # expected_effect "+25% sessions" -> 1.25; unparsable ignored
    assert cal.sensitivity_multiplier("2026-10-15T09:00:00Z", "2026-10-15T10:00:00Z", {"country": "UK"}) == 1.25


def test_priorities_and_watchlist() -> None:
    mg = MetricGraph()
    mg.add_edge(GraphEdge("orders", "revenue", "formula", formula="revenue = orders * aov"))
    mg.add_edge(GraphEdge("sessions", "orders", "possible_cause"))

    mdefs = {
        "revenue": {"business_weight": 1.0},
        "orders": {"business_weight": 0.9},
        "sessions": {"business_weight": 0.5},
        "zero_metric": {"business_weight": 0.2},
    }
    histories = {
        "revenue": [100.0, 102.0, 99.0, 101.0],
        "orders": [50.0, 51.0, 49.0, 50.0],
        "sessions": [1000.0, 1100.0, 900.0, 1050.0],
        "zero_metric": [0.0, 0.0, 0.0, 0.0],  # zero median must not divide by zero
    }
    ranked = score_metrics(mg, mdefs, lambda m: histories[m])
    names = [p.metric for p in ranked]
    assert names[0] == "revenue"
    assert names.index("orders") < names.index("zero_metric")

    # Empty graph works cleanly
    empty_ranked = score_metrics(MetricGraph(), mdefs, lambda m: histories[m])
    assert len(empty_ranked) == 4

    # Budget is respected and root KPIs overall are always included
    wl = select_watchlist(ranked, budget=3.0, segment_candidates=[{}, {"country": "DE"}])
    assert len(wl) == 3
    assert ("revenue", {}) in wl
    assert ("orders", {}) in wl


def test_triage_decisions() -> None:
    df = _make_dq_df(hours=4)
    start = pd.Timestamp("2026-10-15T00:00:00Z")
    end = pd.Timestamp("2026-10-15T04:00:00Z")

    cal = EventCalendar.from_dataframe(
        pd.DataFrame(
            [
                {
                    "event_id": "evt_rel",
                    "start_time": "2026-10-15T01:00:00Z",
                    "end_time": None,
                    "event_type": "release",
                    "planned": False,
                    "country": "DE",
                    "platform": "android",
                    "category": None,
                    "description": "Android checkout v4.2",
                    "expected_effect": None,
                },
                {
                    "event_id": "evt_promo",
                    "start_time": "2026-10-15T00:00:00Z",
                    "end_time": "2026-10-16T00:00:00Z",
                    "event_type": "campaign",
                    "planned": True,
                    "country": "UK",
                    "platform": None,
                    "category": None,
                    "description": "UK promo",
                    "expected_effect": None,
                },
            ]
        )
    )

    dq_ok = lambda s, e, seg: check_dq(df, s, e, now=end, expected_slices=SLICES, segment=seg)

    # 1. DE fault with complete data and unplanned release -> report
    de_fault = AdaptedAnomaly("orders", {"country": "DE", "platform": "android"}, start, end, 100.0, 90.0, 110.0, 40.0, 8.5)
    d1 = triage(de_fault, cal, dq_ok)
    assert d1.action == "report"
    assert any("evt_rel" in r for r in d1.reasons)

    # 2. UK promo within widened range (100 +/- 1.5*10 = [85, 115]) -> suppress_planned
    uk_mild = AdaptedAnomaly("orders", {"country": "UK"}, start, end, 100.0, 90.0, 110.0, 113.0, 5.2)
    d2 = triage(uk_mild, cal, dq_ok)
    assert d2.action == "suppress_planned"

    # 3. UK promo far beyond widened range -> report
    uk_extreme = AdaptedAnomaly("orders", {"country": "UK"}, start, end, 100.0, 90.0, 110.0, 150.0, 9.0)
    d3 = triage(uk_extreme, cal, dq_ok)
    assert d3.action == "report"

    # 4. Incomplete data -> insufficient_data
    incomplete_df = df[~((df["country"] == "UK") & (df["timestamp"] == start))]
    dq_bad = lambda s, e, seg: check_dq(incomplete_df, s, e, now=end, expected_slices=SLICES, segment=seg)
    d4 = triage(uk_mild, cal, dq_bad)
    assert d4.action == "insufficient_data"
