"""The monitor on the simulator's data: what it raises, where, when, and what it holds back.

The scenarios are built with the simulator's own injection functions, the same ones the demo
buttons call. The monitor itself only ever sees the four contract tables.
"""
import shutil

import pandas as pd
import pytest

from brain.data import Store
from brain.graph import MetricGraph
from brain.monitor import Monitor, leading
from sim import db as simdb
from sim import inject
from sim.run import backfill

T = pd.Timestamp
H = pd.Timedelta(hours=1)
START = "2026-10-25T00:00:00Z"
DAYS = 10
FAULT = T("2026-11-03T12:00:00Z")  # day 9, midday
END_OF_DATA = T(START) + pd.Timedelta(days=DAYS)


@pytest.fixture(scope="module")
def clean_db(tmp_path_factory):
    return backfill(str(tmp_path_factory.mktemp("mon") / "clean.db"), DAYS * 24, START, seed=11)


@pytest.fixture
def scenario(clean_db, tmp_path):
    """Copy of the clean history with one or more injections applied; returns a Monitor on it."""
    def make(*steps):
        path = tmp_path / "s.db"
        shutil.copy(clean_db, path)
        con = simdb.connect(path)
        try:
            for step in steps:
                step(con)
        finally:
            con.close()
        return Monitor(MetricGraph.load(), Store(path))
    return make


def by_metric(tick):
    return {a.metric: a for a in leading(tick.alerts) if a.explained_by is None}


def test_clean_history_raises_nothing(scenario):
    monitor = scenario()
    raised = []
    for k in range(4 * 24, DAYS * 24 + 1):
        tick = monitor.tick(as_of=T(START) + k * H)
        raised += [(tick.now, a.metric, a.segment, round(a.score, 1)) for a in tick.alerts]
    assert raised == []


def test_payment_fault_is_found_in_its_cell(scenario):
    monitor = scenario(lambda con: inject.payment_failure(con, "DE", "Android", FAULT, 0.4))
    assert monitor.tick(as_of=FAULT).alerts == []  # nothing is known before the first faulty hour ends
    found = None
    for k in (1, 2, 3):
        found = by_metric(monitor.tick(as_of=FAULT + k * H)).get("payment_success_rate")
        if found:
            break
    assert found is not None, "not raised within three hours"
    assert found.segment == {"country": "DE", "platform": "Android"}
    assert found.observed < 0.75 < found.expected
    assert found.onset == FAULT
    assert found.z < -5


def test_no_look_ahead(scenario):
    monitor = scenario(lambda con: inject.payment_failure(con, "DE", "Android", FAULT, 0.4))
    for k in range(-6, 1):
        assert monitor.tick(as_of=FAULT + k * H).alerts == []


@pytest.mark.parametrize("step, metric, segment", [
    (lambda con: inject.search_regression(con, "FR", FAULT, 0.4), "search_ctr", {"country": "FR"}),
    (lambda con: inject.stockout_surge(con, "electronics", FAULT), "stockout_rate", {"category": "electronics"}),
    (lambda con: inject.stockout_surge(con, "fashion", FAULT, country="DE"), "stockout_rate",
     {"country": "DE", "category": "fashion"}),
    (lambda con: inject.ad_budget_exhausted(con, "PL", FAULT, FAULT + 8 * H, boost_hours=3), "ad_load",
     {"country": "PL"}),
])
def test_other_faults_are_found_on_their_driver_and_scope(scenario, step, metric, segment):
    monitor = scenario(step)
    monitor.tick(as_of=FAULT)
    found = by_metric(monitor.tick(as_of=FAULT + 2 * H)).get(metric)
    assert found is not None
    assert found.segment == segment


def test_ad_load_is_watched_in_both_directions(scenario):
    monitor = scenario(lambda con: inject.ad_budget_exhausted(con, "PL", FAULT, FAULT + 8 * H, boost_hours=3))
    up = by_metric(monitor.tick(as_of=FAULT + 2 * H))["ad_load"]
    down = by_metric(monitor.tick(as_of=FAULT + 6 * H))["ad_load"]
    assert up.z > 0 and down.observed < down.expected
    assert down.onset == FAULT  # one episode, not two


def test_a_long_fault_does_not_become_the_new_normal(scenario):
    monitor = scenario(lambda con: inject.payment_failure(con, "DE", "Android", FAULT - 30 * H, 0.4))
    for k in range(-30, 1):
        monitor.tick(as_of=FAULT + k * H)
    found = by_metric(monitor.tick(as_of=FAULT + H))["payment_success_rate"]
    assert found.expected > 0.9  # the baseline is still the rate from before the fault
    assert found.onset == FAULT - 30 * H


def test_an_ended_fault_stops_alerting(scenario):
    monitor = scenario(lambda con: inject.payment_failure(con, "DE", "Android", FAULT, 0.4, end=FAULT + 3 * H))
    ticks = [monitor.tick(as_of=FAULT + k * H) for k in range(1, 12)]
    assert "payment_success_rate" in by_metric(ticks[2])
    assert ticks[-1].alerts == []


def test_a_late_source_holds_its_metrics_until_the_data_is_in(scenario):
    monitor = scenario(
        lambda con: inject.data_delay(con, "payments", FAULT, FAULT + 3 * H, 2),
        lambda con: inject.payment_failure(con, "DE", "Android", FAULT, 0.4))
    first = monitor.tick(as_of=FAULT + H)
    assert first.late == {FAULT: {"payments"}}
    assert "payment_success_rate" not in {a.metric for a in first.alerts}
    held = monitor.tick(as_of=FAULT + 2 * H)
    assert "payment_success_rate" not in {a.metric for a in held.alerts}
    arrived = monitor.tick(as_of=FAULT + 3 * H)   # the first hour is in now, two ticks late
    assert FAULT not in arrived.late
    assert by_metric(arrived)["payment_success_rate"].segment == {"country": "DE", "platform": "Android"}


def test_a_promo_is_noticed_and_explained_by_the_calendar(scenario):
    monitor = scenario(lambda con: inject.promo(con, "FR", FAULT, FAULT + 12 * H))
    monitor.tick(as_of=FAULT)
    tick = monitor.tick(as_of=FAULT + 2 * H)
    moved = {a.metric: a for a in leading(tick.alerts)}
    assert "discount_rate" in moved
    assert all(a.explained_by is not None for a in tick.alerts), [(a.metric, a.segment) for a in tick.alerts
                                                                   if a.explained_by is None]
    assert moved["discount_rate"].explained_by["kind"] == "promo"
    assert moved["discount_rate"].explained_by["country"] == "FR"


def test_a_fault_during_a_promo_elsewhere_is_still_a_fault(scenario):
    monitor = scenario(
        lambda con: inject.promo(con, "FR", FAULT, FAULT + 12 * H),
        lambda con: inject.payment_failure(con, "DE", "Android", FAULT, 0.4))
    monitor.tick(as_of=FAULT)
    found = by_metric(monitor.tick(as_of=FAULT + 3 * H))
    assert found["payment_success_rate"].segment == {"country": "DE", "platform": "Android"}
    assert "discount_rate" not in found


def test_promo_hours_are_kept_out_of_later_baselines(scenario):
    # the same hours one week later must not read as a drop against the promo week
    monitor = scenario(lambda con: inject.promo(con, "FR", T("2026-10-27T08:00:00Z"), T("2026-10-27T20:00:00Z")))
    raised = []
    for k in range(4 * 24, DAYS * 24 + 1):
        tick = monitor.tick(as_of=T(START) + k * H)
        raised += [(tick.now, a.metric, a.segment) for a in tick.alerts if a.explained_by is None]
    assert raised == []


def test_a_fault_written_into_the_past_is_still_seen(scenario, tmp_path):
    # `python -m sim.inject ...` without --now rewrites the last hours of an already published history
    monitor = scenario()
    for k in range(-8, 1):
        assert monitor.tick(as_of=END_OF_DATA + k * H).alerts == []
    con = simdb.connect(monitor.store.path)
    try:
        inject.payment_failure(con, "DE", "Android", END_OF_DATA - 5 * H, 0.4)
    finally:
        con.close()
    found = by_metric(monitor.tick(as_of=END_OF_DATA))
    assert found["payment_success_rate"].onset == END_OF_DATA - 5 * H


# ------------------------------------------------------------- a release is a reason to look closer
NIGHT = T("2026-11-02T02:00:00Z")   # 03:00 in Warsaw: the smallest cell at its quietest hour


def _night_fault(con):
    inject.payment_failure(con, "PL", "iOS", NIGHT, 0.4, end=NIGHT + 6 * H)


def test_a_small_night_fault_is_too_weak_for_the_hourly_scan_alone(scenario):
    # the reason for the release watch: about 25 payment attempts in six hours
    from brain.monitor import Config
    monitor = scenario(_night_fault)
    monitor.config = Config(release_watch_hours=0)
    seen = set()
    for k in range(0, 13):
        seen |= {a.metric for a in monitor.tick(as_of=NIGHT + k * H).alerts}
    assert "payment_success_rate" not in seen


def test_after_a_release_its_metrics_are_tested_in_its_scope_since_the_release(scenario):
    monitor = scenario(_night_fault)
    found, when = None, None
    for k in range(0, 13):
        tick = monitor.tick(as_of=NIGHT + k * H)
        hit = by_metric(tick).get("payment_success_rate")
        if hit and found is None:
            found, when = hit, k
    assert found is not None, "not raised within the fault and six hours after it"
    assert found.segment == {"country": "PL", "platform": "iOS"}
    assert found.onset == NIGHT and found.trigger == "release"
    assert found.observed < 0.8 < found.expected
    assert when <= 12


def test_the_release_watch_goes_quiet_when_the_evidence_stops_growing(scenario):
    monitor = scenario(_night_fault)
    ticks = [monitor.tick(as_of=NIGHT + k * H) for k in range(0, 20)]
    assert any("payment_success_rate" in by_metric(t) for t in ticks)
    assert ticks[-1].alerts == []


def test_harmless_releases_raise_nothing(scenario):
    def releases(con):
        rows = [("2026-10-31T10:00:00Z", "payments-sdk", "PL", "iOS", "payments-sdk rollout (PL, iOS)"),
                ("2026-11-01T03:00:00Z", "search", "FR", None, "search rollout (FR)"),
                ("2026-11-01T19:00:00Z", "pacing", "DE", None, "pacing rollout (DE)"),
                ("2026-11-02T08:00:00Z", "payments-sdk", "ES", "Web", "payments-sdk rollout (ES, Web)"),
                ("2026-11-02T22:00:00Z", "search", "PL", "Android", "search rollout (PL, Android)"),
                ("2026-11-03T05:00:00Z", "payments-sdk", "DE", "Android", "payments-sdk rollout (DE, Android)"),
                ("2026-11-03T15:00:00Z", "pacing", "FR", "iOS", "pacing rollout (FR, iOS)")]
        con.executemany("INSERT INTO releases VALUES (?, ?, ?, ?, ?)", rows)
        con.commit()
    monitor = scenario(releases)
    raised = []
    for k in range(4 * 24, DAYS * 24 + 1):
        tick = monitor.tick(as_of=T(START) + k * H)
        raised += [(tick.now, a.metric, a.segment, a.trigger) for a in tick.alerts]
    assert raised == []
