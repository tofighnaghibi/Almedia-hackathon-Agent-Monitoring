"""L4 joint test: detection (A) + data quality, events calendar and triage (B).

Work plan check: the faults are detected; the planned promo, the holiday and
the late data are NOT reported as incidents.
"""

from collections import Counter

import pandas as pd
import pytest

from graph import MetricEngine
from monitoring.data_quality import check_dq
from monitoring.detect import DEFAULT_GROUPINGS, Detector
from monitoring.event_calendar import EventCalendar
from monitoring.triage import AdaptedAnomaly, triage
from simulator import SimConfig
from simulator.pipeline import build_dataset

CFG = SimConfig(start="2026-10-01", days=28, seed=42)


def _event(**kw):
    base = {"event_id": "e", "start_time": "2026-10-15T00:00:00Z", "end_time": "2026-10-16T00:00:00Z",
            "event_type": "campaign", "planned": True, "country": None, "platform": None, "category": None,
            "description": "", "expected_effect": None}
    return {**base, **kw}


def _complete_dq(start, end, segment):
    hours = pd.date_range(start, end, freq="h", inclusive="left")
    df = pd.DataFrame({"timestamp": hours, "country": segment.get("country", "UK")})
    return check_dq(df, start, end, now=end, expected_slices={"country": [segment.get("country", "UK")]})


# --- calendar rules ---------------------------------------------------------------

def test_country_event_covers_only_that_country():
    cal = EventCalendar.from_dataframe(pd.DataFrame([_event(event_id="promo", country="UK")]))
    w = ("2026-10-15T10:00:00Z", "2026-10-15T12:00:00Z")
    assert [e.event_id for e in cal.explains(*w, {"country": "UK", "platform": "ios"}).planned_events] == ["promo"]
    total = cal.explains(*w, {})
    assert total.planned_events == [] and [e.event_id for e in total.partial_planned_events] == ["promo"]
    assert cal.explains(*w, {"country": "DE"}).partial_planned_events == []


def test_release_shortly_before_the_anomaly_is_a_candidate_cause():
    cal = EventCalendar.from_dataframe(pd.DataFrame([
        _event(event_id="rel", event_type="release", planned=False, start_time="2026-10-15T09:45:00Z",
               end_time=None, country="DE", platform="android"),
    ]))
    seg = {"country": "DE", "platform": "android"}
    assert [e.event_id for e in cal.explains("2026-10-15T10:00:00Z", "2026-10-15T14:00:00Z", seg).unplanned_events] == ["rel"]
    assert cal.explains("2026-10-15T16:00:00Z", "2026-10-15T18:00:00Z", seg).unplanned_events == []  # > 6h later
    assert cal.explains("2026-10-15T08:00:00Z", "2026-10-15T09:00:00Z", seg).unplanned_events == []  # before it


def test_stated_effect_shifts_the_expectation():
    cal = EventCalendar.from_dataframe(pd.DataFrame([
        _event(event_id="promo", country="UK", expected_effect="+25% sessions and orders"),
    ]))
    start, end = pd.Timestamp("2026-10-15T00:00:00Z"), pd.Timestamp("2026-10-15T04:00:00Z")
    assert cal.expected_effect_factor(start, end, {"country": "UK"}) == pytest.approx(1.25)
    # +27% against a +25% promo: explained
    lifted = AdaptedAnomaly("sessions", {"country": "UK"}, start, end, 100.0, 92.0, 108.0, 127.0, 9.0)
    assert triage(lifted, cal, _complete_dq).action == "suppress_planned"
    # -40% during a +25% promo: not explained by it
    crashed = AdaptedAnomaly("sessions", {"country": "UK"}, start, end, 100.0, 92.0, 108.0, 60.0, -12.0)
    assert triage(crashed, cal, _complete_dq).action == "report"
    # the effect applies to volumes, not to rates
    rate = AdaptedAnomaly("payment_success_rate", {"country": "UK"}, start, end, 0.95, 0.93, 0.97, 1.0, 6.0)
    d = triage(rate, cal, _complete_dq, volume_metric=lambda m: m == "sessions")
    assert d.action == "report" and "range widened" in d.reasons[0]


def test_partial_event_never_suppresses():
    cal = EventCalendar.from_dataframe(pd.DataFrame([
        _event(event_id="promo", country="UK", expected_effect="+25%"),
    ]))
    start, end = pd.Timestamp("2026-10-15T00:00:00Z"), pd.Timestamp("2026-10-15T04:00:00Z")
    total = AdaptedAnomaly("orders", {}, start, end, 100.0, 95.0, 105.0, 107.0, 5.5)
    d = triage(total, cal, _complete_dq)
    assert d.action == "report" and any("cover only part" in r for r in d.reasons)


# --- the joint check on simulator data ------------------------------------------------

@pytest.fixture(scope="module")
def triaged():
    result, truth = build_dataset(CFG, "all")
    table = result.marketplace_hourly
    engine = MetricEngine(tables={"marketplace_hourly": table})
    cal = EventCalendar.from_dataframe(result.events)
    slices = {d: sorted(table[d].unique()) for d in ["country", "platform", "category"]}

    def dq(start, end, segment):
        return check_dq(table, start, end, now=end, expected_slices=slices, segment=segment)

    det = Detector(engine)
    metrics = ["sessions", "orders", "revenue", "payment_success_rate", "search_ctr", "ad_spend",
               "out_of_stock_rate", "row_count"]
    eps = [e for by in DEFAULT_GROUPINGS for e in det.detect(metrics, by=by, start="2026-10-22")]
    vol = lambda m: engine.registry.get(m).is_additive  # noqa: E731
    return [(e, triage(e, cal, dq, volume_metric=vol)) for e in eps], truth["scenarios"]


def _for(triaged, scenario_id, metric=None):
    decided, truth = triaged
    s = next(x for x in truth if x["scenario_id"] == scenario_id)
    t0, t1 = pd.Timestamp(s["start_time"]), pd.Timestamp(s["end_time"])
    return [(e, d) for e, d in decided
            if all(e.segment.get(k) == v for k, v in s["affected_segment"].items())
            and e.start <= t1 and e.end > t0 and (metric is None or e.metric == metric)]


@pytest.mark.parametrize("scenario_id,metric", [
    ("scn_de_android_payment", "payment_success_rate"),
    ("scn_de_bad_search", "search_ctr"),
    ("scn_fr_ad_exhaustion", "ad_spend"),
    ("scn_electronics_stockout", "out_of_stock_rate"),
])
def test_faults_are_reported(triaged, scenario_id, metric):
    found = _for(triaged, scenario_id, metric)
    assert found and all(d.action == "report" for _, d in found)
    # no symptom of a fault is hidden as "planned"
    assert not any(d.action == "suppress_planned" for _, d in _for(triaged, scenario_id))


def test_payment_failure_points_at_the_android_release(triaged):
    (e, d), *_ = sorted(_for(triaged, "scn_de_android_payment", "payment_success_rate"),
                        key=lambda x: -abs(x[0].peak_z))
    assert "evt_001" in {ev.event_id for ev in d.events.unplanned_events}


@pytest.mark.parametrize("scenario_id", ["scn_uk_planned_promo", "scn_us_holiday"])
def test_planned_events_are_not_incidents(triaged, scenario_id):
    """Whatever the planned event moves in its country is explained, not reported."""
    flagged = 0
    for metric in ["sessions", "orders", "revenue"]:
        found = [(e, d) for e, d in _for(triaged, scenario_id, metric) if set(e.segment) == {"country"}]
        flagged += len(found)
        assert all(d.action == "suppress_planned" for _, d in found), (metric, Counter(d.action for _, d in found))
    assert _for(triaged, scenario_id, "sessions") and flagged >= 1  # the change itself was detected


def test_late_data_is_not_an_incident(triaged):
    found = _for(triaged, "scn_de_ios_late_data")
    assert found and all(d.action == "insufficient_data" for _, d in found)
    assert any(ev.event_id for _, d in found for ev in d.events.data_delay_events)
