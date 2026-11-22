"""The incident book (brain/incidents.py): one incident per fault when the fault spans sibling series,
innocent explanations, recurrences and severity."""
import json
import shutil
import sqlite3

import pandas as pd
import pytest

from brain.data import Store, iso_hour
from brain.graph import MetricGraph
from brain.incidents import Book
from brain.monitor import Alert, Monitor, Tick
from sim.run import backfill

T = pd.Timestamp
H = pd.Timedelta(hours=1)
START = "2026-10-25T00:00:00Z"
DAYS = 10
FAULT = T("2026-11-03T12:00:00Z")


@pytest.fixture(scope="module")
def clean_db(tmp_path_factory):
    return backfill(str(tmp_path_factory.mktemp("book") / "clean.db"), DAYS * 24, START, seed=11)


def records(folder):
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]


def alerts_in(folder):
    return [r for r in records(folder) if r["status"] in ("open", "ongoing", "resolved")]


# ------------------------------------------------------------------ a book with a stand-in investigator
def key(segment: dict) -> tuple:
    return tuple(sorted(segment.items()))


def alert(metric, segment, score, hour=FAULT, observed=0.2, expected=0.1):
    return Alert(metric=metric, kind="ratio", segment=dict(segment), onset=hour, last_bad=hour, hours=1,
                 observed=observed, expected=expected, numerator=observed * 1000, denominator=1000.0,
                 episode_observed=observed, score=score, z=score / 10)


class StubInvestigator:
    """What the investigation would conclude, per monitor series: the localized place, the lead cause and any
    extra fields (an innocent explanation, an impact)."""

    def __init__(self, places=None, causes=None, extra=None):
        self.places, self.causes, self.extra = places or {}, causes or {}, extra or {}

    def report(self, inc, tick):
        lead = max(inc.metrics.values(), key=lambda a: a.score)
        k = key(inc.segment)
        cause = self.causes.get(k, f"{lead.metric} moved in {k} without a matching release")
        body = {"id": inc.id, "status": inc.status, "first_seen": iso_hour(inc.first_seen),
                "last_seen": iso_hour(inc.last_seen), "metric": lead.metric,
                "segment": dict(self.places.get(k, inc.segment)),
                "fingerprint": f"{lead.metric}|{k}",
                "change": {"observed": lead.observed, "expected": lead.expected, "delta_pct": 100 * lead.effect,
                           "z": lead.z, "window": {"start": iso_hour(inc.onset), "end": iso_hour(inc.last_seen + H)}},
                "drivers": [{"metric": a.metric, "segment": a.segment, "observed": a.observed, "expected": a.expected,
                             "delta_pct": 100 * a.effect, "z": a.z} for a in inc.metrics.values()],
                "hypotheses": [{"cause": cause, "kind": "release", "status": "supported", "score": 0.9,
                                "evidence": ["stub"]}],
                "trail": [{"action": "watch", "target": lead.metric, "result": "stub"}],
                "uncertain": [], "report": f"{lead.metric} moved."}
        body.update(self.extra.get(k, {}))
        return body


@pytest.fixture
def stub_book(clean_db, tmp_path):
    def make(**kw):
        book = Book(Monitor(MetricGraph.load(), Store(clean_db)), tmp_path / "incidents", fresh=True)
        book.investigator = StubInvestigator(**kw)
        return book
    return make


def feed(book, *alerts, hour=FAULT):
    return book.update(Tick(now=hour + H, newest=hour, alerts=list(alerts)))


DE_EL = {"country": "DE", "category": "electronics"}
FR_EL = {"country": "FR", "category": "electronics"}
EL = {"category": "electronics"}


# ------------------------------------------------------------------ I1: one incident per fault across sibling series
def test_sibling_series_of_one_fault_join_through_the_localized_place(stub_book):
    book = stub_book(places={key(DE_EL): EL, key(FR_EL): EL})
    feed(book, alert("discount_rate", DE_EL, 50), alert("discount_rate", FR_EL, 40))
    feed(book, alert("discount_rate", DE_EL, 60), alert("discount_rate", FR_EL, 70), hour=FAULT + H)
    (inc,) = alerts_in(book.folder)
    assert inc["id"] == "inc-0001" and inc["segment"] == EL
    assert book._next == 2     # no number was spent on the duplicate


def test_sibling_series_join_when_the_newer_one_localizes_into_the_older(stub_book):
    # the first series' investigation keeps its own cell; the second one's points at the whole category
    book = stub_book(places={key(FR_EL): EL})
    feed(book, alert("discount_rate", DE_EL, 50))
    feed(book, alert("discount_rate", DE_EL, 55), alert("discount_rate", FR_EL, 40), hour=FAULT + H)
    assert [i["id"] for i in alerts_in(book.folder)] == ["inc-0001"]


def test_sibling_series_with_the_same_supported_cause_are_one_incident(stub_book):
    cause = "release pricing: pricing 7.1 rollout (electronics)"
    book = stub_book(causes={key(DE_EL): cause, key(FR_EL): cause})
    feed(book, alert("discount_rate", DE_EL, 50), alert("discount_rate", FR_EL, 40))
    feed(book, alert("discount_rate", DE_EL, 60), alert("discount_rate", FR_EL, 45), hour=FAULT + H)
    (inc,) = alerts_in(book.folder)
    assert inc["hypotheses"][0]["cause"] == cause


def test_sibling_series_with_their_own_places_and_causes_stay_apart(stub_book):
    book = stub_book(causes={key(DE_EL): "release a: x (DE)", key(FR_EL): "release b: y (FR)"})
    feed(book, alert("discount_rate", DE_EL, 50), alert("discount_rate", FR_EL, 40))
    assert len(alerts_in(book.folder)) == 2


def test_an_unrelated_metric_in_the_localized_place_is_its_own_incident(stub_book):
    book = stub_book(places={key(DE_EL): EL})
    feed(book, alert("discount_rate", DE_EL, 50))
    feed(book, alert("search_ctr", FR_EL, 40, observed=0.05), hour=FAULT + H)
    assert len(alerts_in(book.folder)) == 2


def test_a_discount_bug_across_a_country_is_one_incident(clean_db, tmp_path):
    # the strongest series are the country's category cells (discount_rate is judged on its spread, and the
    # cells are steadier than the country): before the fix this gave one incident per category
    path = tmp_path / "d.db"
    shutil.copy(clean_db, path)
    con = sqlite3.connect(path)
    con.execute("UPDATE facts_hourly SET discounts = ROUND(discounts * 2.5, 2) WHERE country = 'FR' AND ts >= ? AND ts < ?",
                (iso_hour(FAULT), iso_hour(FAULT + 8 * H)))
    con.commit()
    con.close()
    monitor = Monitor(MetricGraph.load(), Store(path))
    book = Book(monitor, tmp_path / "incidents", fresh=True)
    for k in range(0, 6):
        book.update(monitor.tick(as_of=FAULT + k * H))
    got = alerts_in(book.folder)
    assert len(got) == 1, [(i["id"], i["fingerprint"]) for i in got]
    (inc,) = got
    assert inc["segment"] == {"country": "FR"}
    assert {d["metric"] for d in inc["drivers"]} >= {"discount_rate"}


# ------------------------------------------------------------------ I2: innocent explanations found by the investigation
MIX = {"innocent": {"kind": "mix", "reason": "Traffic moved toward electronics: the mix explains 96 % of the change "
                                              "in take_rate, the cells' own rates did not move."}}
DE = {"country": "DE"}


def assert_readable(record):
    """What the console and the scorer need from any record, alert or not."""
    for k in ("id", "status", "metric", "first_seen", "last_seen", "report"):
        assert isinstance(record[k], str) and record[k], k
    assert isinstance(record["segment"], dict)
    assert all(isinstance(h["cause"], str) and h["kind"] and h["status"] and
               all(isinstance(e, str) for e in h["evidence"]) for h in record["hypotheses"])
    assert all(isinstance(s["target"], str) and s["target"] and isinstance(s["result"], str) for s in record["trail"])


def test_an_innocent_change_is_written_as_explained_and_not_as_an_incident(stub_book):
    book = stub_book(extra={key(DE): MIX})
    feed(book, alert("take_rate", DE, 30))
    feed(book, alert("take_rate", DE, 35, hour=FAULT + H), hour=FAULT + H)
    assert alerts_in(book.folder) == []
    (note,) = records(book.folder)
    assert_readable(note)
    assert note["id"].startswith("explained-mix-") and note["status"] == "explained"
    assert note["innocent"] == MIX["innocent"]
    assert MIX["innocent"]["reason"] in note["report"]
    assert note["hypotheses"][0]["status"] == "supported" and MIX["innocent"]["reason"] in note["hypotheses"][0]["evidence"]
    assert note["first_seen"] == iso_hour(FAULT) and note["last_seen"] == iso_hour(FAULT + H)
    assert not [i for i in book.incidents if i.status in ("open", "ongoing")]   # nothing live
    assert book._next == 1                                                        # no incident number spent
    assert book.monitor.open_episodes == []


def test_an_open_incident_found_innocent_later_turns_explained_in_place(stub_book):
    book = stub_book()
    feed(book, alert("take_rate", DE, 30))
    (inc,) = alerts_in(book.folder)
    book.investigator.extra = {key(DE): MIX}
    feed(book, alert("take_rate", DE, 35, hour=FAULT + H), hour=FAULT + H)
    feed(book, alert("take_rate", DE, 36, hour=FAULT + 2 * H), hour=FAULT + 2 * H)
    (note,) = records(book.folder)       # no duplicate beside it
    assert_readable(note)
    assert note["id"] == inc["id"] and note["status"] == "explained"
    assert note["first_seen"] == inc["first_seen"] and note["innocent"]["kind"] == "mix"
    assert not [i for i in book.incidents if i.status in ("open", "ongoing")]


def test_a_calendar_innocence_names_its_kind(stub_book):
    cal = {"innocent": {"kind": "calendar", "reason": "Black Friday in DE (2026-11-03 10:00 to 22:00 UTC) fits: "
                                                       "traffic +38 % against +40 % planned."}}
    book = stub_book(extra={key(DE): cal})
    feed(book, alert("take_rate", DE, 30))
    (note,) = records(book.folder)
    assert note["id"].startswith("explained-calendar-") and note["hypotheses"][0]["kind"] == "calendar"


def test_an_explained_group_that_stops_looking_innocent_becomes_an_incident(stub_book):
    book = stub_book(extra={key(DE): MIX})
    feed(book, alert("take_rate", DE, 30))
    book.investigator.extra = {}
    feed(book, alert("take_rate", DE, 40, hour=FAULT + H), hour=FAULT + H)
    (inc,) = records(book.folder)        # the incident's file replaces the explained record
    assert inc["id"] == "inc-0001" and inc["status"] == "open" and inc["first_seen"] == iso_hour(FAULT)


# ------------------------------------------------------------------ I3: recurrence
def resolve_quietly(book, start, hours=4):
    """Hours without alerts; the book is told the data looks healthy."""
    book._healthy = lambda inc, tick: True
    for k in range(hours):
        feed(book, hour=start + k * H)


def test_a_fault_that_comes_back_names_the_resolved_incident(stub_book):
    book = stub_book()
    feed(book, alert("take_rate", DE_EL, 30))
    resolve_quietly(book, FAULT + H)
    (old,) = alerts_in(book.folder)
    assert old["status"] == "resolved"
    back = FAULT + 8 * H
    feed(book, alert("take_rate", DE, 40, hour=back), hour=back)       # a nested place: the whole country
    new = next(i for i in alerts_in(book.folder) if i["id"] == "inc-0002")
    assert new["recurrence_of"] == "inc-0001"
    assert f"It is back: same metric and place as inc-0001, resolved {old['resolved_at'][:10]}" in new["report"]
    feed(book, alert("take_rate", DE, 41, hour=back + H), hour=back + H)  # still said on the next tick
    new = next(i for i in alerts_in(book.folder) if i["id"] == "inc-0002")
    assert new["recurrence_of"] == "inc-0001" and new["report"].count("It is back") == 1


def test_another_metric_or_place_is_no_recurrence(stub_book):
    book = stub_book()
    feed(book, alert("take_rate", DE_EL, 30))
    resolve_quietly(book, FAULT + H)
    back = FAULT + 8 * H
    feed(book, alert("search_ctr", DE_EL, 40, hour=back, observed=0.05), alert("take_rate", FR_EL, 40, hour=back), hour=back)
    assert all("recurrence_of" not in i and "It is back" not in i["report"] for i in alerts_in(book.folder))


def test_a_real_payment_fault_that_returns_is_linked_to_the_first(clean_db, tmp_path):
    from sim import db as simdb
    from sim import inject
    path = tmp_path / "r.db"
    shutil.copy(clean_db, path)
    con = simdb.connect(path)
    inject.payment_failure(con, "DE", "Android", FAULT, 0.4, end=FAULT + 3 * H)
    inject.payment_failure(con, "DE", "Android", FAULT + 8 * H, 0.4)   # the data ends at FAULT + 12 h
    con.close()
    monitor = Monitor(MetricGraph.load(), Store(path))
    book = Book(monitor, tmp_path / "incidents", fresh=True)
    for k in range(0, 11):
        book.update(monitor.tick(as_of=FAULT + k * H))
    first, second = alerts_in(book.folder)
    assert first["status"] == "resolved" and "recurrence_of" not in first
    assert second["recurrence_of"] == first["id"] and "It is back" in second["report"]


# ------------------------------------------------------------------ I4: severity and the feed's order
from brain.incidents import HIGH_EUR, LONG_HOURS, MEDIUM_EUR, feed_rank  # noqa: E402


def severity_of(stub_book, eur, hours=1, impact_key="contribution_eur"):
    book = stub_book(extra={key(DE): {"impact": {} if eur is None else {impact_key: eur, "orders": -50}}})
    feed(book, Alert(**{**alert("take_rate", DE, 30).__dict__, "onset": FAULT - (hours - 1) * H}))
    (inc,) = alerts_in(book.folder)
    return inc["severity"]


@pytest.mark.parametrize("eur, hours, expected", [
    (-HIGH_EUR, 1, "high"), (HIGH_EUR + 1, 1, "high"),       # a gain counts by its size too (an ad overspend)
    (-MEDIUM_EUR, 1, "medium"), (-(MEDIUM_EUR - 1), 1, "low"), (None, 1, "low"),
    (-MEDIUM_EUR, LONG_HOURS, "high"), (-10, LONG_HOURS, "medium"), (None, LONG_HOURS, "medium"),
])
def test_severity_comes_from_the_euro_impact_and_the_hours(stub_book, eur, hours, expected):
    assert severity_of(stub_book, eur, hours) == expected


def test_only_euro_keys_count_for_severity(stub_book):
    assert severity_of(stub_book, -5000, impact_key="orders_lost") == "low"


def test_the_feed_shows_live_incidents_first_and_the_severe_ones_on_top():
    rows = [{"id": "a", "status": "resolved", "severity": "high", "last_seen": "2026-11-03T15:00:00Z"},
            {"id": "b", "status": "open", "severity": "low", "last_seen": "2026-11-03T16:00:00Z"},
            {"id": "c", "status": "ongoing", "severity": "high", "last_seen": "2026-11-03T12:00:00Z"},
            {"id": "d", "status": "open", "severity": "medium", "last_seen": "2026-11-03T13:00:00Z"},
            {"id": "e", "status": "explained", "last_seen": "2026-11-03T17:00:00Z"},
            {"id": "f", "status": "ongoing", "severity": "high", "last_seen": "2026-11-03T14:00:00Z"}]
    assert [r["id"] for r in sorted(rows, key=feed_rank)] == ["f", "c", "d", "b", "a", "e"]


def test_the_book_keeps_its_feed_sorted(stub_book):
    fr, es = {"country": "FR"}, {"country": "ES"}
    book = stub_book(extra={key(DE): {"impact": {"contribution_eur": -20}},
                            key(fr): {"impact": {"contribution_eur": -5 * HIGH_EUR}},
                            key(es): {"impact": {"contribution_eur": -MEDIUM_EUR}}})
    feed(book, alert("take_rate", DE, 30), alert("take_rate", fr, 20), alert("take_rate", es, 10))
    assert [b["severity"] for b in book.feed()] == ["high", "medium", "low"]


# ------------------------------------------------------------------ a crossing series that fires first
FASHION = {"category": "fashion"}
DE_AND = {"country": "DE", "platform": "Android"}


def test_a_crossing_series_that_fires_first_is_refined_into_the_fault_s_place_not_a_second_incident(stub_book):
    # a payment fault in DE x Android shows, diluted, in the fashion series too; that one fired an hour earlier
    book = stub_book(causes={key(FASHION): "payment_success_rate fell in fashion without a matching release"})
    feed(book, alert("payment_success_rate", FASHION, 9))
    feed(book, alert("payment_success_rate", DE_AND, 30, observed=0.6, expected=0.94), hour=FAULT + H)
    (inc,) = alerts_in(book.folder)
    assert inc["id"] == "inc-0001" and inc["segment"] == DE_AND
