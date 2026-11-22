"""Volumes: sessions and orders are judged as counts against the same hour of the week.

A traffic drop leaves every rate where it was: only the counts show it. The history here is 24 days,
so the newest days have three earlier weeks to compare with. The drop is written with plain SQL on a
copy of a clean history: every measure of one country x platform halved for six hours.
"""
import shutil
import sqlite3

import pandas as pd
import pytest

from brain.data import Store
from brain.graph import MetricGraph
from brain.monitor import Monitor, leading
from sim import db as simdb
from sim import inject
from sim.run import backfill
from sim.schema import MEASURES

T = pd.Timestamp
H = pd.Timedelta(hours=1)
START = "2026-10-25T00:00:00Z"
DAYS = 24
END = T(START) + pd.Timedelta(days=DAYS)
DROP = T("2026-11-16T12:00:00Z")   # day 23, a Monday, midday
PLACE = {"country": "DE", "platform": "Android"}


@pytest.fixture(scope="module")
def clean_db(tmp_path_factory):
    return backfill(str(tmp_path_factory.mktemp("vol") / "clean.db"), DAYS * 24, START, seed=11)


@pytest.fixture
def scenario(clean_db, tmp_path):
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


def scale(country, platform, start, end, factor):
    """Every measure of one place multiplied by `factor` over [start, end): rates stay the same."""
    def step(con: sqlite3.Connection):
        sets = ", ".join(f"{m} = {m} * {factor}" for m in MEASURES)
        con.execute(f"UPDATE facts_hourly SET {sets} WHERE country = ? AND platform = ? AND ts >= ? AND ts < ?",
                    (country, platform, start.strftime("%Y-%m-%dT%H:00:00Z"), end.strftime("%Y-%m-%dT%H:00:00Z")))
        con.commit()
    return step


def volumes(tick, explained=False):
    return [a for a in tick.alerts if a.kind == "volume" and (explained or a.explained_by is None)]


def test_clean_counts_raise_no_volume_alert(scenario):
    monitor = scenario()
    raised = []
    for k in range(3 * 24, -1, -1):
        tick = monitor.tick(as_of=END - k * H)
        raised += [(tick.now, a.metric, a.segment, round(a.effect, 2), round(a.score, 1)) for a in volumes(tick, True)]
    assert raised == []


def test_a_traffic_drop_with_unchanged_rates_is_a_volume_alert(scenario):
    monitor = scenario(scale("DE", "Android", DROP, DROP + 6 * H, 0.5))
    assert volumes(monitor.tick(as_of=DROP)) == []
    found, rates = None, []
    for k in (1, 2, 3):
        tick = monitor.tick(as_of=DROP + k * H)
        rates += [(a.metric, a.segment) for a in tick.alerts if a.kind != "volume" and a.explained_by is None]
        lead = {a.metric: a for a in leading(volumes(tick))}
        if "sessions" in lead and found is None:
            found = lead["sessions"]
    assert found is not None, "the traffic drop raised no volume alert within three hours"
    assert found.segment == PLACE
    assert found.onset == DROP
    assert -0.6 < found.effect < -0.4
    newest = (found.last_bad - max(found.onset, found.last_bad - 2 * H)) / H + 1   # the newest bad hours, at most 3
    assert found.observed == pytest.approx(found.numerator / newest)       # counts per hour
    assert found.expected == pytest.approx(found.denominator / newest)
    assert found.denominator > found.numerator    # counts: expected summed over the same hours
    assert found.z < -5 and found.score >= 7
    assert rates == []                             # no rate moved


def test_a_traffic_drop_is_one_alert_not_two(scenario):
    # orders fell exactly as much as sessions: the traffic explains them, so they are not a second problem
    monitor = scenario(scale("DE", "Android", DROP, DROP + 6 * H, 0.5))
    monitor.tick(as_of=DROP)
    for k in (1, 2, 3, 4):
        tick = monitor.tick(as_of=DROP + k * H)
        assert "orders" not in {a.metric for a in volumes(tick)}


def test_orders_that_fall_with_a_payment_fault_are_not_a_second_alert(scenario):
    monitor = scenario(lambda con: inject.payment_failure(con, "DE", "Android", DROP, 0.6, end=DROP + 6 * H))
    monitor.tick(as_of=DROP)
    seen = set()
    for k in range(1, 7):
        tick = monitor.tick(as_of=DROP + k * H)
        seen |= {a.metric for a in tick.alerts if a.explained_by is None}
        assert "orders" not in {a.metric for a in volumes(tick)}
    assert "payment_success_rate" in seen


def test_a_promo_explains_the_traffic_it_brings(scenario):
    monitor = scenario(lambda con: inject.promo(con, "FR", DROP, DROP + 10 * H))
    monitor.tick(as_of=DROP)
    seen = []
    for k in (1, 2, 3, 4):
        tick = monitor.tick(as_of=DROP + k * H)
        seen += volumes(tick, explained=True)
        assert volumes(tick) == [], [(a.metric, a.segment, a.effect) for a in volumes(tick)]
    assert any(a.metric == "sessions" and a.effect > 0.2 and a.explained_by["kind"] == "promo" for a in seen)


def test_a_late_traffic_source_holds_the_sessions_count(scenario):
    # the delay first: an injection rewrites the facts from its start, which would undo the drop
    monitor = scenario(lambda con: inject.data_delay(con, "traffic", DROP, DROP + 2 * H, 3),
                       scale("DE", "Android", DROP, DROP + 6 * H, 0.5))
    first = monitor.tick(as_of=DROP + H)
    assert "traffic" in first.late.get(DROP, set())
    assert "sessions" in first.held.get(DROP, set())
    assert "sessions" not in {a.metric for a in volumes(first)}
    later = [monitor.tick(as_of=DROP + k * H) for k in range(2, 7)]
    assert any(a.metric == "sessions" and a.segment == PLACE and a.onset == DROP
               for t in later for a in volumes(t))


def test_a_volume_series_answers_a_targeted_look(scenario):
    # the investigator and the incident book ask the monitor about a series; volumes answer too
    monitor = scenario(scale("DE", "Android", DROP, DROP + 6 * H, 0.5))
    for k in range(0, 4):
        monitor.tick(as_of=DROP + k * H)
    assert monitor.has_series("sessions", PLACE)
    w = monitor.window("sessions", PLACE, DROP, DROP + 3 * H)
    assert w is not None and w["observed"] < 0.6 * w["expected"] and w["score"] >= 7
    hours = monitor.hourly("sessions", PLACE, DROP - 2 * H, DROP + 3 * H)
    assert [h["hour"] for h in hours] == [DROP + k * H for k in range(-2, 3)]
    assert hours[0]["z"] > -3 > hours[-1]["z"]


def test_a_volume_incident_is_not_healthy_while_the_drop_runs_on_into_the_night(scenario, tmp_path):
    """The health check compares like with like: the episode's daytime average is not the faulty level at night."""
    from brain.incidents import Book
    monitor = scenario(scale("DE", "Android", DROP, DROP + 20 * H, 0.7))
    book = Book(monitor, tmp_path / "inc", fresh=True)
    t = DROP - H
    while t <= T("2026-11-17T05:00:00Z"):
        book.update(monitor.tick(as_of=t))
        t += H
    inc = next(i for i in book.incidents if "sessions" in i.metrics)
    assert inc.status != "resolved"
    assert not book._healthy(inc, monitor.tick(as_of=T("2026-11-17T08:00:00Z")))     # the drop is still on at 08:00
