"""Incident files: what the page and the scoring harness read (issues #4, #7, #8)."""
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


def test_one_fault_gives_one_file_with_the_fields_the_page_needs(run):
    book, _ = run(payment)
    (inc,) = files(book)
    assert inc["id"] == "inc-0001"
    assert inc["status"] in ("open", "ongoing")
    assert inc["segment"] == {"country": "DE", "platform": "Android"}
    for key in ("id", "status", "metric", "first_seen", "last_seen", "report"):
        assert isinstance(inc[key], str) and inc[key]
    assert all(isinstance(e, str) for h in inc["hypotheses"] for e in h["evidence"])
    assert all(isinstance(u, str) for u in inc["uncertain"])
    assert all(isinstance(s["target"], str) and s["target"] and isinstance(s["result"], str) for s in inc["trail"])
    assert inc["drivers"][0]["metric"] == "payment_success_rate"


def test_the_outcome_is_the_metric_and_the_moved_rate_is_the_contribution(run):
    # so the page can light the route from the outcome down to the rate
    book, _ = run(payment)
    (inc,) = files(book)
    assert inc["metric"] == "contribution"
    assert [c["node"] for c in inc["contributions"]] == ["payment_success_rate"]
    assert inc["contributions"][0]["share_of_delta"] == pytest.approx(1.0)   # the only moved rate: all of the effect
    # the incident's share of everything that is off in that cell and window, the other rates' ordinary
    # movement against their own baselines included
    assert 0.5 < inc["explained_share"] <= 1.0
    assert inc["change"]["observed"] < inc["change"]["expected"]
    assert inc["impact"]["contribution_eur"] < 0 and inc["impact"]["orders"] < 0
    assert -60 < inc["change"]["delta_pct"] < -25   # a 40 % payment failure in that cell


def test_first_seen_is_the_data_hour_at_which_it_was_raised_and_never_moves(run):
    book, _ = run(payment, last=1)
    (early,) = files(book)
    assert early["first_seen"] == "2026-11-03T12:00:00Z"   # raised on the tick after the first bad hour
    assert early["status"] == "open"
    assert early["change"]["window"]["start"] == "2026-11-03T12:00:00Z"
    book, _ = run(payment, last=6)
    (later,) = files(book)
    assert later["first_seen"] == early["first_seen"]
    assert later["last_seen"] == "2026-11-03T17:00:00Z"
    assert later["status"] == "ongoing"


def test_the_release_is_named_as_the_scorer_needs_it(run):
    book, path = run(payment)
    (inc,) = files(book)
    lead = inc["hypotheses"][0]
    assert lead["kind"] == "release" and lead["status"] == "supported"
    assert lead["cause"] == "release payments-sdk: payments-sdk 4.2 rollout (DE, Android)"
    events, _, data_end = simscore.load_truth(path)
    result = simscore.score(events, files(book), data_end, days=DAYS)
    assert (result["detected"], result["localized_rate"], result["correct_cause_rate"], result["false_alarms"]) == (1, 1.0, 1.0, 0)


def test_an_ended_fault_is_resolved_in_the_same_file(run):
    book, _ = run(lambda con: inject.payment_failure(con, "DE", "Android", FAULT, 0.4, end=FAULT + 3 * H), last=12)
    (inc,) = files(book)
    assert inc["status"] == "resolved"
    assert inc["last_seen"] <= "2026-11-03T16:00:00Z"
    assert "Back to normal" in inc["report"]


def test_a_second_fault_elsewhere_is_a_second_incident(run):
    book, _ = run(payment, lambda con: inject.search_regression(con, "FR", FAULT + 2 * H, 0.5), last=6)
    got = {tuple(i["segment"].items()): i["drivers"][0]["metric"] for i in files(book)}
    assert got == {(("country", "DE"), ("platform", "Android")): "payment_success_rate",
                   (("country", "FR"),): "search_ctr"}


def test_a_promo_is_written_as_explained_and_is_no_alert(run):
    book, path = run(lambda con: inject.promo(con, "FR", FAULT, FAULT + 12 * H), last=6)
    assert files(book) == []
    (note,) = records(book)
    assert_readable(note)
    assert note["status"] == "explained" and note["event"]["kind"] == "promo"
    assert note["segment"] == {"country": "FR"}
    assert note["hypotheses"][0]["kind"] == "calendar" and note["hypotheses"][0]["status"] == "supported"
    assert "discount_rate" in {m["metric"] for m in note["drivers"]}
    assert note["first_seen"] <= note["last_seen"]
    events, _, data_end = simscore.load_truth(path)
    result = simscore.score(events, records(book), data_end, days=DAYS)   # the scorer reads the whole folder
    assert result["false_alarms"] == 0 and result["innocents_silent"] == result["innocents"]


def test_the_explained_record_keeps_its_first_seen(run):
    early, _ = run(lambda con: inject.promo(con, "FR", FAULT, FAULT + 12 * H), last=2)
    (a,) = records(early)
    late, _ = run(lambda con: inject.promo(con, "FR", FAULT, FAULT + 12 * H), last=8)
    (b,) = records(late)
    assert a["id"] == b["id"] and a["first_seen"] == b["first_seen"] and a["last_seen"] < b["last_seen"]


def test_held_hours_are_written_as_pending_and_removed_when_the_data_is_in(run):
    delay = lambda con: inject.data_delay(con, "payments", FAULT, FAULT + 2 * H, 2)
    book, path = run(delay, first=1, last=2)
    (note,) = records(book)
    assert_readable(note)
    assert note["status"] == "pending" and note["sources"] == ["payments"]
    assert note["hypotheses"][0]["kind"] == "freshness"
    assert {h["hour"] for h in note["held"]} == {"2026-11-03T12:00:00Z", "2026-11-03T13:00:00Z"}
    events, _, data_end = simscore.load_truth(path)
    result = simscore.score(events, records(book), data_end, days=DAYS)
    assert result["false_alarms"] == 0 and result["innocents_silent"] == result["innocents"]
    book, _ = run(delay, first=1, last=8)
    assert records(book) == []


def test_the_console_lists_explained_and_pending_without_skipping_them(run, monkeypatch):
    book, _ = run(lambda con: inject.promo(con, "FR", FAULT, FAULT + 12 * H),
                  lambda con: inject.data_delay(con, "payments", FAULT + 2 * H, FAULT + 5 * H, 2), last=4)
    monkeypatch.setenv("INCIDENTS_DIR", str(book.folder))
    import importlib
    import ui.app
    app = importlib.reload(ui.app)
    try:
        got = app.incidents()
    finally:
        monkeypatch.delenv("INCIDENTS_DIR")
        importlib.reload(ui.app)
    assert got["skipped"] == 0
    assert {i["status"] for i in got["incidents"]} == {"explained", "pending"}


def test_stockouts_light_the_route_through_the_rates_on_the_tree(run):
    book, _ = run(lambda con: inject.stockout_surge(con, "electronics", FAULT), last=5)
    (inc,) = files(book)
    assert inc["segment"] == {"category": "electronics"}
    assert inc["drivers"][0]["metric"] == "stockout_rate"
    assert "add_to_cart_rate" in {c["node"] for c in inc.get("contributions", [])}


def test_clean_removes_what_json_cannot_carry():
    out = clean({"a": float("nan"), "b": [float("inf"), 1.5], "t": T("2026-11-01T13:00:00Z"), "n": pd.NA is None})
    assert out == {"a": None, "b": [None, 1.5], "t": "2026-11-01T13:00:00Z", "n": False}


def test_write_json_leaves_no_temporary_file(tmp_path):
    write_json(tmp_path / "x" / "inc-0001.json", {"id": "inc-0001", "z": float("inf")})
    assert [p.name for p in (tmp_path / "x").iterdir()] == ["inc-0001.json"]
    assert json.loads((tmp_path / "x" / "inc-0001.json").read_text(encoding="utf-8")) == {"id": "inc-0001", "z": None}


def test_recomputing_the_outcome_from_its_factors_gives_the_observed_value(clean_db):
    from brain.evaluator import Evaluator
    graph, store = MetricGraph.load(), Store(clean_db)
    ev = Evaluator(graph, store)
    tree = tree_metrics(graph, "contribution")
    frame = ev.frame(tree, FAULT - 24 * H, FAULT, {"country": "DE"})
    observed = {m: float(frame[m].iloc[0]) for m in tree}
    assert recompute(graph, "contribution", observed, {}) == pytest.approx(observed["contribution"], rel=1e-9)
    half = estimate(graph, ev, "contribution", {"country": "DE"}, FAULT - 24 * H, FAULT,
                    {"payment_success_rate": observed["payment_success_rate"] * 2})
    # twice the success rate would mean twice the orders: fees and discounts double, ad spend does not
    twice = 2 * (observed["marketplace_fees"] - observed["discounts"]) + observed["ad_spend"]
    assert half.expected == pytest.approx(twice, rel=1e-9)
    assert estimate(graph, ev, "contribution", {"country": "DE"}, FAULT - 24 * H, FAULT, {"stockout_rate": 0.1}) is None


def test_fresh_empties_the_folder_and_numbering_restarts(run, tmp_path):
    book, path = run(payment)
    again = Book(Monitor(MetricGraph.load(), Store(path)), book.folder, fresh=True)
    assert list(book.folder.glob("*.json")) == [] and again._next == 1


def test_replay_from_the_command_line_writes_scoreable_incidents(clean_db, tmp_path, capsys):
    path = tmp_path / "r.db"
    shutil.copy(clean_db, path)
    con = simdb.connect(path)
    inject.payment_failure(con, "PL", "iOS", FAULT, 0.5, end=FAULT + 6 * H)
    con.close()
    out = tmp_path / "out"
    assert run_main(["--replay", "--db", str(path), "--incidents", str(out), "--fresh", "--quiet"]) == 0
    assert "1 incident(s)" in capsys.readouterr().out
    events, _, data_end = simscore.load_truth(path)
    result = simscore.score(events, simscore.load_incidents(out), data_end, days=DAYS)
    assert (result["detected"], result["localized_rate"], result["correct_cause_rate"], result["false_alarms"]) == (1, 1.0, 1.0, 0)
