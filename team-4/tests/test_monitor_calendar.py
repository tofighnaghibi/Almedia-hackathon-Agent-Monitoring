"""A calendar event explains a change only when the change fits it (risk 3).

A promo predicts more traffic, more orders, a higher add-to-cart rate and a higher discount rate.
A change the other way, or one more than about twice what earlier promos of that place did, is
raised, with the promo kept as partial context instead of a blanket excuse.
"""
import shutil

import pandas as pd
import pytest

from brain.data import Store
from brain.graph import MetricGraph
from brain.monitor import EVENT_EFFECTS, Monitor, leading
from sim import db as simdb
from sim import inject
from sim.run import backfill

T = pd.Timestamp
H = pd.Timedelta(hours=1)
START = "2026-10-25T00:00:00Z"
DAYS = 24
EARLIER = T("2026-11-09T10:00:00Z")   # an earlier FR promo, one week before
PROMO = T("2026-11-16T10:00:00Z")
LENGTH = 10 * H


@pytest.fixture(scope="module")
def clean_db(tmp_path_factory):
    return backfill(str(tmp_path_factory.mktemp("cal") / "clean.db"), DAYS * 24, START, seed=11)


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


def promo(start, **kw):
    return lambda con: inject.promo(con, "FR", start, start + LENGTH, **kw)


def discounts_times(factor):
    def step(con):
        con.execute("UPDATE facts_hourly SET discounts = discounts * ? WHERE country = 'FR' AND ts >= ? AND ts < ?",
                    (factor, PROMO.strftime("%Y-%m-%dT%H:00:00Z"), (PROMO + LENGTH).strftime("%Y-%m-%dT%H:00:00Z")))
        con.commit()
    return step


def run(monitor, hours=4):
    monitor.tick(as_of=PROMO)
    return [monitor.tick(as_of=PROMO + k * H) for k in range(1, hours + 1)]


def in_fr(alerts, metric):
    return [a for a in leading(alerts) if a.metric == metric and a.segment.get("country") in (None, "FR")]


def test_a_promo_predicts_rises():
    assert EVENT_EFFECTS["promo"] == {"sessions": 1, "orders": 1, "add_to_cart_rate": 1, "discount_rate": 1}


def test_a_promo_with_the_rise_earlier_promos_had_stays_explained(scenario):
    ticks = run(scenario(promo(EARLIER), promo(PROMO)))
    raised = [(a.metric, a.segment, round(a.effect, 2)) for t in ticks for a in t.alerts if a.explained_by is None]
    assert raised == []
    moved = {a.metric for t in ticks for a in t.alerts}
    assert {"discount_rate", "sessions"} <= moved


def test_a_fall_in_discount_rate_during_a_promo_is_raised(scenario):
    ticks = run(scenario(promo(PROMO), discounts_times(0.3)))
    found = [a for t in ticks for a in in_fr(t.alerts, "discount_rate")]
    assert found, "the fall was not noticed at all"
    assert all(a.explained_by is None for a in found)
    ctx = found[-1].partial_explanation
    assert ctx["kind"] == "promo" and ctx["country"] == "FR"
    assert "up" in ctx["why"] and "down" in ctx["why"]
    # what the promo does predict is still explained
    assert all(a.explained_by is not None for t in ticks for a in t.alerts if a.metric in ("sessions", "add_to_cart_rate"))


def test_a_rise_five_times_what_promos_do_is_raised(scenario):
    ticks = run(scenario(promo(EARLIER), promo(PROMO, extra_discount=0.15)))
    found = [a for t in ticks for a in in_fr(t.alerts, "discount_rate")]
    assert found and all(a.explained_by is None for a in found)
    assert found[-1].effect > 0
    assert "twice" in found[-1].partial_explanation["why"]
    assert all(a.explained_by is not None for t in ticks for a in t.alerts if a.metric in ("sessions", "add_to_cart_rate"))
