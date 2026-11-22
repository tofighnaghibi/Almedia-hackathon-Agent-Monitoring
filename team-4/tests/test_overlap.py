"""Several faults at once, lasting faults and a failing tick: cases from the review of the monitor."""
import json
import shutil

import pandas as pd
import pytest

from brain.data import Store
from brain.graph import MetricGraph
from brain.impact import estimate, recompute, tree_metrics
from brain.incidents import Book, clean, write_json
from brain.monitor import Monitor
from brain.run import main as run_main
from sim import db as simdb
from sim import inject
from sim import score as simscore
from sim.run import backfill

T = pd.Timestamp
H = pd.Timedelta(hours=1)
START = "2026-10-25T00:00:00Z"
DAYS = 10
FAULT = T("2026-11-03T12:00:00Z")


@pytest.fixture(scope="module")
def clean_db(tmp_path_factory):
    return backfill(str(tmp_path_factory.mktemp("inc") / "clean.db"), DAYS * 24, START, seed=11)


@pytest.fixture
def run(clean_db, tmp_path):
    """Inject, then tick the monitor and the book from `first` to `last` hours after the fault."""
    def go(*steps, first=0, last=4):
        path = tmp_path / "s.db"
        shutil.copy(clean_db, path)
        con = simdb.connect(path)
        try:
            for step in steps:
                step(con)
        finally:
            con.close()
        monitor = Monitor(MetricGraph.load(), Store(path))
        book = Book(monitor, tmp_path / "incidents", fresh=True)
        for k in range(first, last + 1):
            book.update(monitor.tick(as_of=FAULT + k * H))
        return book, path
    return go


def records(book):
    """Everything the readers see in the folder, alerts or not."""
    def strict(text):
        return json.loads(text, parse_constant=lambda c: pytest.fail(f"non-finite number {c} in an incident file"))
    return [strict(p.read_text(encoding="utf-8")) for p in sorted(book.folder.glob("*.json"))]


def files(book):
    """The alerts: records that are not `explained` or `pending`."""
    return [r for r in records(book) if r["status"] in ("open", "ongoing", "resolved")]


def assert_readable(record):
    """What the console and the scorer need from any record, alert or not."""
    for key in ("id", "status", "metric", "first_seen", "last_seen", "report"):
        assert isinstance(record[key], str) and record[key], key
    assert isinstance(record["segment"], dict)
    assert all(isinstance(h["cause"], str) and h["kind"] and h["status"] and
               all(isinstance(e, str) for e in h["evidence"]) for h in record["hypotheses"])
    assert all(isinstance(s["target"], str) and s["target"] and isinstance(s["result"], str) for s in record["trail"])


def payment(con):
    inject.payment_failure(con, "DE", "Android", FAULT, 0.4)


# ------------------------------------------------------- several faults at once (review of the monitor)
def by_place(book):
    return {tuple(i["segment"].items()): i for i in files(book)}


def test_unrelated_faults_at_the_same_time_stay_separate_incidents(run):
    # the demo order: a payment fault, then stockouts in a category, then a search regression elsewhere
    book, _ = run(payment,
                  lambda con: inject.stockout_surge(con, "electronics", FAULT + 2 * H),
                  lambda con: inject.search_regression(con, "FR", FAULT + 3 * H, 0.5), last=9)
    got = by_place(book)
    assert set(got) == {(("country", "DE"), ("platform", "Android")), (("category", "electronics"),),
                        (("country", "FR"),)}
    assert got[(("country", "DE"), ("platform", "Android"))]["drivers"][0]["metric"] == "payment_success_rate"
    assert got[(("category", "electronics"),)]["drivers"][0]["metric"] == "stockout_rate"
    assert got[(("country", "FR"),)]["drivers"][0]["metric"] == "search_ctr"
    assert all(i["status"] != "resolved" for i in got.values())   # all three faults are still running
    assert got[(("country", "DE"), ("platform", "Android"))]["hypotheses"][0]["cause"].startswith("release payments-sdk")
    assert got[(("country", "FR"),)]["hypotheses"][0]["cause"].startswith("release search")


def test_a_fault_inside_the_country_of_another_fault_is_its_own_incident(run):
    book, _ = run(payment, lambda con: inject.search_regression(con, "DE", FAULT + 2 * H, 0.5), last=8)
    got = by_place(book)
    assert set(got) == {(("country", "DE"), ("platform", "Android")), (("country", "DE"),)}
    assert got[(("country", "DE"),)]["drivers"][0]["metric"] == "search_ctr"


def test_the_same_kind_of_fault_in_two_places_gives_two_incidents(run):
    book, _ = run(lambda con: inject.payment_failure(con, "FR", "iOS", FAULT, 0.5),
                  lambda con: inject.payment_failure(con, "ES", "iOS", FAULT + 4 * H, 0.5), last=10)
    got = by_place(book)
    assert set(got) == {(("country", "FR"), ("platform", "iOS")), (("country", "ES"), ("platform", "iOS"))}
    assert all(i["hypotheses"][0]["status"] == "supported" for i in got.values())


def test_the_cause_stays_supported_when_the_predicted_effect_grows_strong(run):
    # the stock level explains the fall of the add-to-cart rate: more evidence must not weaken the label
    book, _ = run(lambda con: inject.stockout_surge(con, "electronics", FAULT - 14 * H), first=-14, last=10)
    (inc,) = files(book)
    lead = inc["hypotheses"][0]
    assert lead["kind"] == "metric" and lead["status"] == "supported"
    assert any("add_to_cart_rate" in e for e in lead["evidence"])


def test_a_running_fault_in_a_small_cell_is_not_closed_overnight(run):
    start = T("2026-11-02T10:00:00Z")
    book, _ = run(lambda con: inject.payment_failure(con, "PL", "Web", start, 0.35), first=-26, last=12)
    got = files(book)
    assert len(got) == 1, [(i["id"], i["status"], i["first_seen"]) for i in got]
    assert got[0]["status"] != "resolved"
    assert got[0]["hypotheses"][0]["cause"].startswith("release payments-sdk")


def test_the_live_loop_survives_a_failing_tick(clean_db, tmp_path, capsys):
    from brain import run as brain_run
    path = tmp_path / "live.db"
    shutil.copy(clean_db, path)
    monitor = Monitor(MetricGraph.load(), Store(path))
    book = Book(monitor, tmp_path / "out", fresh=True)
    calls, real = {"n": 0}, monitor.tick

    def flaky(*a, **k):
        calls["n"] += 1
        if calls["n"] == 1:
            raise OSError("disk hiccup")
        return real(*a, **k)

    monitor.tick = flaky
    brain_run.live(monitor, book, poll=0.01, max_ticks=1, quiet=True)
    assert calls["n"] >= 2
    assert "disk hiccup" in capsys.readouterr().err


def test_the_watch_after_a_release_does_not_move_the_onset_back():
    # a harmless release hours before a fault: the watch asks its question from the release on, but that
    # is when the question was asked, not when the change began
    from types import SimpleNamespace
    from brain.incidents import earliest_onset
    onset = T("2026-11-03T12:00:00Z")
    scan = SimpleNamespace(onset=onset - 1 * H, trigger="scan")
    watch = SimpleNamespace(onset=onset - 5 * H, trigger="release")
    assert earliest_onset(onset, [watch]) == onset
    assert earliest_onset(onset, [watch, scan]) == onset - 1 * H


def test_a_fault_whose_two_phases_cancel_still_resolves(run):
    # an ad budget spent three times as fast for 3 hours, then gone for 5: over the whole episode the rate
    # is about what it always was, which must not be taken for "the fault would not show, keep it open"
    start = FAULT - 4 * H     # 08:00 UTC: with this day's traffic the two phases cancel to within a few percent
    book, _ = run(lambda con: inject.ad_budget_exhausted(con, "FR", start, start + 8 * H, boost_hours=3), first=-4, last=11)
    (inc,) = files(book)
    driver = inc["drivers"][0]
    assert driver["metric"] == "ad_load"
    assert inc["status"] == "resolved", (inc["status"], inc["last_seen"])
