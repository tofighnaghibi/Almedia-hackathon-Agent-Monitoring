"""The investigator: where, how much and why, and which explanations it rejects (README 3.5)."""
import json
import shutil
import sqlite3

import numpy as np
import pandas as pd
import pytest

from brain.data import Store
from brain.graph import MetricGraph
from brain.incidents import Book
from brain.investigate import Investigator
from brain.monitor import Monitor
from sim import db as simdb
from sim import inject
from sim.run import backfill

T = pd.Timestamp
H = pd.Timedelta(hours=1)
START = "2026-10-25T00:00:00Z"
DAYS = 10
FAULT = T("2026-11-03T12:00:00Z")


@pytest.fixture(scope="module")
def clean_db(tmp_path_factory):
    return backfill(str(tmp_path_factory.mktemp("inv") / "clean.db"), DAYS * 24, START, seed=11)


@pytest.fixture
def run(clean_db, tmp_path):
    def go(*steps, first=0, last=5):
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
        return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(book.folder.glob("inc-*.json"))]
    return go


def payment(con):
    inject.payment_failure(con, "DE", "Android", FAULT, 0.4)


def release(ts, system, country, platform, description):
    def step(con):
        con.execute("INSERT INTO releases VALUES (?, ?, ?, ?, ?)",
                    (ts.strftime("%Y-%m-%dT%H:00:00Z"), system, country, platform, description))
        con.commit()
    return step


def by_cause(inc, text):
    return next(h for h in inc["hypotheses"] if text in h["cause"])


def test_the_report_names_the_place_the_cost_and_the_cause(run):
    (inc,) = run(payment)
    top = inc["localization"][0]
    assert top["chosen"] and top["segment"] == "DE x Android" and top["dimension"] == "country x platform"
    assert top["share_of_delta"] > 0.7
    assert inc["segment"] == {"country": "DE", "platform": "Android"}
    lead = inc["contributions"][0]
    assert lead["node"] == "payment_success_rate" and lead["delta"] < 0
    assert inc["impact"]["contribution_eur"] == pytest.approx(lead["delta"])
    assert inc["impact"]["orders"] < 0
    assert inc["hypotheses"][0]["status"] == "supported" and inc["hypotheses"][0]["kind"] == "release"
    assert inc["hypotheses"][0]["score"] > 0.9
    actions = [s["action"] for s in inc["trail"]]
    order = [actions.index(a) for a in ("watch", "drill", "decompose", "check", "stop")]
    assert order == sorted(order) and actions[-2:] == ["compare", "stop"]
    assert inc["trail"][-2]["target"] == "explanations"
    assert "supported" in inc["trail"][-1]["result"]


def test_explanations_that_fail_stay_in_the_report_with_the_reason(run):
    (inc,) = run(payment)
    late = by_cause(inc, "late or missing data")
    assert late["status"] == "rejected" and late["kind"] == "freshness"
    mix = by_cause(inc, "shift in the mix")
    assert mix["status"] == "rejected" and "of the change" in mix["evidence"][0]
    assert [h["status"] for h in inc["hypotheses"]] == sorted((h["status"] for h in inc["hypotheses"]),
                                                              key=("supported", "plausible", "rejected").index)
    assert "rejected" in inc["report"]


def test_a_release_in_another_place_is_rejected_for_its_scope(run):
    (inc,) = run(payment, release(FAULT, "payments-sdk", "DE", "Web", "payments-sdk rollout (DE, Web)"))
    assert inc["hypotheses"][0]["cause"] == "release payments-sdk: payments-sdk 4.2 rollout (DE, Android)"
    other = by_cause(inc, "rollout (DE, Web)")
    assert other["status"] == "rejected"
    assert any("does not include DE x Android" in e and "normal" in e for e in other["evidence"])
    assert sum(h["status"] == "supported" for h in inc["hypotheses"]) == 1


def test_an_earlier_release_in_the_same_place_is_rejected_for_its_timing(run):
    (inc,) = run(payment, release(FAULT - 7 * H, "payments-sdk", "DE", "Android", "payments-sdk rollout (DE, Android)"))
    early = by_cause(inc, "payments-sdk rollout (DE, Android)")
    assert early["status"] == "rejected"
    assert any("7 h before" in e and "normal" in e for e in early["evidence"])
    assert inc["hypotheses"][0]["cause"] == "release payments-sdk: payments-sdk 4.2 rollout (DE, Android)"


def test_a_release_of_a_system_the_graph_does_not_link_is_rejected(run):
    (inc,) = run(payment, release(FAULT, "search", "DE", None, "search rollout (DE)"))
    other = by_cause(inc, "search rollout (DE)")
    assert other["status"] == "rejected"
    assert any("not linked" in e for e in other["evidence"])


def test_a_release_after_the_change_began_is_rejected(run):
    (inc,) = run(payment, release(FAULT + 4 * H, "payments-sdk", "DE", "Android", "payments-sdk hotfix (DE, Android)"), last=7)
    late = by_cause(inc, "hotfix")
    assert late["status"] == "rejected"
    assert any("after the change began" in e for e in late["evidence"])
    assert inc["hypotheses"][0]["status"] == "supported"


def test_a_wider_release_is_only_plausible(run):
    (inc,) = run(lambda con: inject.payment_failure(con, "DE", "Android", FAULT, 0.4),
                 lambda con: con.execute("DELETE FROM releases") and con.commit(),
                 release(FAULT, "payments-sdk", "DE", None, "payments-sdk rollout (DE)"))
    wide = by_cause(inc, "payments-sdk rollout (DE)")
    assert wide["status"] == "plausible"
    assert any("wider than the change" in e for e in wide["evidence"])


def test_the_symptom_is_told_from_the_cause(run):
    (inc,) = run(lambda con: inject.search_regression(con, "FR", FAULT, 0.5), last=6)
    roles = {d["metric"]: d["role"] for d in inc["drivers"]}
    assert roles["search_ctr"] == "cause candidate"
    assert inc["hypotheses"][0]["cause"].startswith("release search")
    if "views_per_session" in roles:
        assert roles["views_per_session"] == "symptom of search_ctr"
    else:
        assert any(s["action"] == "follow" and s["target"] == "views_per_session" for s in inc["trail"])
    assert inc["contributions"][0]["node"] == "views_per_session"     # the damage goes through the views


def test_a_fault_in_a_combination_nobody_watches_is_narrowed_to_it(run):
    def step(con):   # successful payments and orders fall on iOS in the ads channel only: no watched series is that cut
        con.execute("UPDATE facts_hourly SET payment_successes = CAST(payment_successes * 0.3 AS INTEGER), "
                    "orders = CAST(orders * 0.3 AS INTEGER) WHERE platform = 'iOS' AND channel = 'ads' AND ts >= ?",
                    (FAULT.strftime("%Y-%m-%dT%H:00:00Z"),))
        con.commit()
    incidents = run(step, last=6)
    inc = next(i for i in incidents if i["drivers"][0]["metric"] == "payment_success_rate")
    assert inc["segment"] == {"platform": "iOS", "channel": "ads"}
    assert inc["localization"][0]["chosen"] and inc["localization"][0]["segment"] == "iOS x ads"
    assert any(s["action"] == "drill" and "narrowed from the monitor's series" in s["result"] for s in inc["trail"])


def test_a_stockout_is_its_own_cause_once_the_link_is_confirmed(run):
    (inc,) = run(lambda con: inject.stockout_surge(con, "electronics", FAULT), last=8)
    lead = inc["hypotheses"][0]
    assert lead["kind"] == "metric" and lead["status"] == "supported" and "stockout_rate" in lead["cause"]
    assert inc["segment"] == {"category": "electronics"}
    assert "add_to_cart_rate" in {c["node"] for c in inc["contributions"]}


def test_mix_and_rate_are_told_apart():
    dims = ("country", "platform", "category", "channel")
    keys = [("DE", "iOS", "home", "ads"), ("DE", "Web", "home", "ads")]
    base = {"dims": dims, "keys": keys, "rate": np.array([0.9, 0.5]), "shift": 0.0,
            "history_den": np.array([1000.0, 1000.0])}
    mixed = Investigator.mix(dict(base, den=np.array([20.0, 180.0]), num=np.array([18.0, 90.0])), {"country": "DE"})
    assert mixed["share"] > 0.95 and mixed["mix"] < 0          # more traffic where the rate was always lower
    moved = Investigator.mix(dict(base, den=np.array([100.0, 100.0]), num=np.array([60.0, 50.0])), {"country": "DE"})
    assert moved["share"] < 0.05 and moved["rate"] < 0          # same weights, one cell's rate fell


@pytest.mark.parametrize("last", [2, 5, 8])
def test_a_rate_that_changed_direction_is_still_placed_and_explained(run, last):
    # the ad budget is spent three times as fast for 3 hours, then there is almost none: over the whole
    # incident the two phases cancel, so the place is read from the phase the rate is in now
    (inc,) = run(lambda con: inject.ad_budget_exhausted(con, "PL", FAULT, FAULT + 10 * H, boost_hours=3), last=last)
    assert inc["segment"] == {"country": "PL"}
    assert inc["hypotheses"][0]["cause"] == "release pacing: pacing controller 2.3 rollout (PL)"
    assert inc["hypotheses"][0]["status"] == "supported"
    if last > 4:
        assert any(s["action"] == "drill" and "changed direction" in s["result"] for s in inc["trail"])


# ----------------------------------------------------------------- from the review of the investigator
def test_the_cost_is_the_loss_against_the_same_hours_without_the_fault(clean_db, tmp_path):
    # a promo runs in the same country: the fault's cost must not swallow what the promo does to the other rates
    from brain.evaluator import Evaluator
    promo = lambda con: inject.promo(con, "FR", FAULT - 2 * H, FAULT + 10 * H)
    fault = lambda con: inject.payment_failure(con, "FR", "Android", FAULT, 0.4)
    stores = {}
    for name, steps in (("with", (promo, fault)), ("without", (promo,))):
        path = tmp_path / f"{name}.db"
        shutil.copy(clean_db, path)
        con = simdb.connect(path)
        for step in steps:
            step(con)
        con.close()
        stores[name] = Store(path)
    graph = MetricGraph.load()
    monitor = Monitor(graph, stores["with"])
    book = Book(monitor, tmp_path / "incidents", fresh=True)
    for k in range(0, 9):
        book.update(monitor.tick(as_of=FAULT + k * H))
    inc = next(json.loads(p.read_text(encoding="utf-8")) for p in sorted(book.folder.glob("inc-*.json")))
    assert inc["drivers"][0]["metric"] == "payment_success_rate" and inc["segment"] == {"country": "FR", "platform": "Android"}
    window, place = inc["change"]["window"], {"country": "FR", "platform": "Android"}
    truth = Evaluator(graph, stores["with"]).value("contribution", window["start"], window["end"], place)         - Evaluator(graph, stores["without"]).value("contribution", window["start"], window["end"], place)
    assert truth < 0
    assert inc["impact"]["contribution_eur"] == pytest.approx(truth, rel=0.15)


def test_a_total_outage_has_a_cost(run):
    # no payment succeeds, so there are no orders: order value, take rate and discount rate are 0 / 0
    (inc,) = run(lambda con: inject.payment_failure(con, "DE", "Android", FAULT, 0.999), last=4)
    assert inc["impact"]["contribution_eur"] < 0 and inc["impact"]["orders"] < 0
    assert not any("could not be computed" in u for u in inc["uncertain"])


def test_the_decompose_line_states_what_was_computed(run):
    (inc,) = run(payment)
    line = next(s["result"] for s in inc["trail"] if s["action"] == "decompose")
    assert "usual range" not in " ".join(s["result"] for s in inc["trail"])
    assert "the other rates account for" in line and "payment_success_rate" in line
    assert "z" not in inc["change"]     # the outcome has no z of its own; the rate's z is in drivers


def test_a_release_without_a_description_does_not_stop_the_monitor(run):
    (inc,) = run(payment, release(FAULT, "payments-sdk", "DE", "Web", None))
    assert inc["hypotheses"][0]["status"] == "supported"
    assert any(h["cause"] == "release payments-sdk: " and h["status"] == "rejected" for h in inc["hypotheses"])
