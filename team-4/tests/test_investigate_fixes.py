"""The investigator after the brief audit: the top-down check of the order chain (V1), volume alerts (V2),
innocent explanations (V3), the stock-out and ad-pacing wording (V4) and the recommendations system (V5)."""
import pandas as pd
import pytest

from brain.data import Store
from brain.graph import MetricGraph
from brain.incidents import Book, Incident
from brain.investigate import Investigator
from brain.monitor import Alert, Monitor
from sim import db as simdb
from sim import inject
from tests.test_investigate import DAYS, FAULT, H, START, clean_db, payment, release, run  # noqa: F401  (fixtures)

CHAIN = ("sessions", "views_per_session", "add_to_cart_rate", "checkout_rate", "payment_attempt_rate",
         "payment_success_rate", "order_rate")


def step_of(inc, action, target):
    return next(s for s in inc["trail"] if s["action"] == action and s["target"] == target)


# ------------------------------------------------------------------ V1: the order chain, top-down
def test_the_trail_checks_every_factor_of_orders_in_the_place(run):
    (inc,) = run(payment)
    check = step_of(inc, "check", "factors of orders")
    text = check["result"]
    assert "DE x Android" in text
    for m in CHAIN:
        assert m in text
    parts = {p.split(" ")[0]: p for p in text.split(": ", 1)[1].split("; ")}
    assert "moved -" in parts["payment_success_rate"]
    for m in CHAIN:
        if m != "payment_success_rate":
            assert parts[m].endswith("normal"), parts[m]
    actions = [s["action"] for s in inc["trail"]]
    assert actions.index("drill") < inc["trail"].index(check)


def test_the_report_says_which_factor_moved_and_that_traffic_stayed_normal(run):
    (inc,) = run(payment)
    assert "payment_success_rate moved" in inc["report"]
    assert "traffic (sessions) included, stayed normal" in inc["report"]
    assert not any("Traffic is taken as given" in u for u in inc["uncertain"])


# ------------------------------------------------------------------ V2: volume alerts
def ts(t):
    return t.strftime("%Y-%m-%dT%H:00:00Z")


def investigate_by_hand(db, alerts, as_of):
    """A tick of the monitor, then the investigator on an incident built from the given alerts."""
    monitor = Monitor(MetricGraph.load(), Store(db))
    tick = monitor.tick(as_of=as_of)
    inc = Incident(id="inc-0001", first_seen=max(a.last_bad for a in alerts), last_seen=max(a.last_bad for a in alerts),
                   onset=min(a.onset for a in alerts), segment=dict(alerts[0].segment),
                   metrics={a.metric: a for a in alerts})
    return monitor, tick, Investigator(monitor).report(inc, tick)


@pytest.fixture
def traffic_drop(clean_db, tmp_path):
    """Every count of DE x Android halved from FAULT on: sessions fall, every rate stays the same."""
    from sim.schema import MEASURES
    path = tmp_path / "drop.db"
    import shutil
    shutil.copy(clean_db, path)
    con = simdb.connect(path)
    sets = ", ".join(f'"{c}" = ROUND("{c}" * 0.5)' for c in MEASURES)
    con.execute(f"UPDATE facts_hourly SET {sets} WHERE country = 'DE' AND platform = 'Android' AND ts >= ?", (ts(FAULT),))
    con.commit()
    con.close()
    return path


def volume_alert(db, metric, last_bad):
    """The alert the monitor raises for a fallen count (interface 1): counts per hour over the newest bad hours."""
    monitor = Monitor(MetricGraph.load(), Store(db))
    monitor.tick(as_of=last_bad + H)
    seg = {"country": "DE", "platform": "Android"}
    newest = Investigator(monitor).volume(metric, seg, last_bad - 2 * H, last_bad + H)
    episode = Investigator(monitor).volume(metric, seg, FAULT, last_bad + H)
    return Alert(metric=metric, kind="volume", segment=seg, onset=FAULT, last_bad=last_bad, hours=5,
                 observed=newest["observed"] / newest["hours"], expected=newest["expected"] / newest["hours"],
                 numerator=newest["observed"], denominator=newest["expected"],
                 episode_observed=episode["observed"] / episode["hours"], score=9.0, z=-6.0)


@pytest.mark.parametrize("metric", ["sessions", "orders"])
def test_a_traffic_drop_is_named_as_traffic_with_no_confirmed_cause(traffic_drop, metric):
    alert = volume_alert(traffic_drop, metric, FAULT + 4 * H)
    assert alert.observed < 0.7 * alert.expected     # small counts round up: orders fall a little less than half
    _, _, body = investigate_by_hand(traffic_drop, [alert], FAULT + 5 * H)
    check = step_of(body, "check", "factors of orders")["result"]
    parts = {p.split(" ")[0]: p for p in check.split(": ", 1)[1].split("; ")}
    assert "moved -" in parts["sessions"]
    for m in CHAIN[1:]:
        assert parts[m].endswith("normal"), parts[m]
    assert body["contributions"][0]["node"] == "sessions" and body["contributions"][0]["delta"] < 0
    assert body["impact"]["orders"] < 0
    assert "No cause is confirmed: traffic fell in DE x Android; no release, event or late source fits" in body["report"]
    lead = body["hypotheses"][0]
    assert lead["status"] == "plausible" and lead["cause"].startswith("traffic fell in DE x Android")
    assert any("tracking" in u and "app store" in u and "outage" in u for u in body["uncertain"])
    watch = body["trail"][0]["result"]
    assert "per hour" in watch and "same hours of earlier weeks" in watch


def test_a_volume_alert_gets_the_same_release_check(traffic_drop):
    from tests.test_investigate import release as add_release
    con = simdb.connect(traffic_drop)
    add_release(FAULT, "demand", "DE", "Android", "app store listing removed (DE, Android)")(con)
    con.close()
    alert = volume_alert(traffic_drop, "sessions", FAULT + 4 * H)
    _, _, body = investigate_by_hand(traffic_drop, [alert], FAULT + 5 * H)
    lead = body["hypotheses"][0]
    assert lead["kind"] == "release" and lead["status"] == "supported" and "app store" in lead["cause"]


# ------------------------------------------------------------------ V3: innocent explanations
def records_after(clean_db, tmp_path, step, last=7):
    """Every record the book writes (incidents and explained ones) after `step` changed a copy of the data."""
    import json
    import shutil
    path = tmp_path / "s.db"
    shutil.copy(clean_db, path)
    con = simdb.connect(path)
    try:
        step(con)
    finally:
        con.close()
    monitor = Monitor(MetricGraph.load(), Store(path))
    book = Book(monitor, tmp_path / "incidents", fresh=True)
    for k in range(0, last + 1):
        book.update(monitor.tick(as_of=FAULT + k * H))
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(book.folder.glob("*.json"))]


def shift_to_electronics(con):
    """Traffic moves toward electronics for 8 hours (the simulator's innocent mix shift): the total stays the same and
    every category's own rates stay the same, so only the market-wide rates move."""
    inject.mix_shift(con, "electronics", FAULT, FAULT + 8 * H, share=0.5)


def test_a_mix_shift_that_explains_the_change_is_supported_and_innocent(clean_db, tmp_path):
    records = records_after(clean_db, tmp_path, shift_to_electronics)
    live = [r for r in records if r.get("status") in ("open", "ongoing", "resolved")]
    innocent = [r for r in records if r.get("status") == "explained" and (r.get("innocent") or {}).get("kind") == "mix"]
    assert not live, [(r["id"], r["metric"], r["segment"]) for r in live]      # no alert for an innocent change
    assert innocent
    for rec in innocent:
        assert "%" in rec["innocent"]["reason"] and "mix" in rec["innocent"]["reason"]
        assert rec["metric"] and rec["trail"] and rec["report"]     # every other field is still filled


def test_a_real_fault_is_not_called_innocent(run):
    (inc,) = run(payment)
    assert "innocent" not in inc


def promo_alerts(db, as_of):
    monitor = Monitor(MetricGraph.load(), Store(db))
    tick = monitor.tick(as_of=as_of)
    return [a for a in tick.alerts if a.explained_by is not None and a.segment == {"country": "FR"}]


def promo_db(clean_db, tmp_path, **kw):
    import shutil
    path = tmp_path / "promo.db"
    shutil.copy(clean_db, path)
    con = simdb.connect(path)
    inject.promo(con, "FR", FAULT - 2 * H, FAULT + 10 * H, **kw)
    con.close()
    return path


def test_a_planned_event_that_fits_in_direction_and_size_is_innocent(clean_db, tmp_path):
    db = promo_db(clean_db, tmp_path)
    alerts = promo_alerts(db, FAULT + 4 * H)
    assert alerts and all(a.observed > a.expected for a in alerts)
    _, _, body = investigate_by_hand(db, alerts, FAULT + 4 * H)
    event = next(h for h in body["hypotheses"] if h["kind"] == "calendar")
    assert event["status"] == "supported" and body["hypotheses"][0] is event
    assert any("predicts a rise" in e and "fits" in e for e in event["evidence"])
    assert body["innocent"]["kind"] == "calendar" and "FR promo" in body["innocent"]["reason"]


def test_a_planned_event_that_does_not_fit_stays_a_rival_and_is_not_innocent(clean_db, tmp_path):
    import dataclasses
    db = promo_db(clean_db, tmp_path)
    alerts = promo_alerts(db, FAULT + 4 * H)
    fell = [dataclasses.replace(a, observed=0.7 * a.expected, episode_observed=0.7 * a.expected) for a in alerts]
    _, _, body = investigate_by_hand(db, fell, FAULT + 4 * H)
    event = next(h for h in body["hypotheses"] if h["kind"] == "calendar")
    assert event["status"] == "rejected" and any("predicts a rise" in e and "fell" in e for e in event["evidence"])
    assert "innocent" not in body
    too_big = [dataclasses.replace(a, observed=3.5 * a.expected, episode_observed=3.5 * a.expected) for a in alerts]
    _, _, body = investigate_by_hand(db, too_big, FAULT + 4 * H)
    event = next(h for h in body["hypotheses"] if h["kind"] == "calendar")
    assert event["status"] == "rejected" and any("more than" in e for e in event["evidence"])
    assert "innocent" not in body


# ------------------------------------------------------------------ V4: stock-out and ad-pacing wording
def test_a_stockout_cause_says_no_first_cause_beyond_it_is_in_the_data(run):
    (inc,) = run(lambda con: inject.stockout_surge(con, "electronics", FAULT), last=8)
    lead = inc["hypotheses"][0]
    assert lead["kind"] == "metric" and lead["status"] == "supported"
    assert lead["cause"].startswith("stockouts rose in electronics")
    assert "no first cause beyond them is in the data (the simulator has no supplier deliveries or purchase orders)" in lead["cause"]
    assert "stockout" in lead["cause"].lower()       # what the scorer looks for
    assert any("supplier deliveries" in u and "purchase orders" in u for u in inc["uncertain"])


def test_an_ad_budget_spent_early_is_told_as_spent_early(run):
    (inc,) = run(lambda con: inject.ad_budget_exhausted(con, "PL", FAULT, FAULT + 10 * H, boost_hours=3), last=8)
    report = inc["report"]
    assert "sponsored impressions per session first rose (x" in report and "for 3 h), then fell" in report
    assert "spending ran ahead of schedule and the budget ran out" in report
    assert "spent early, not earned" in report
    check = step_of(inc, "check", "factors of ad_clicks")["result"]
    for m in ("sessions", "ad_load", "ad_ctr", "ad_cpc"):
        assert m in check


def test_an_ad_budget_still_in_its_first_phase_has_no_such_sentence(run):
    (inc,) = run(lambda con: inject.ad_budget_exhausted(con, "PL", FAULT, FAULT + 10 * H, boost_hours=3), last=2)
    assert "then fell" not in inc["report"]


# ------------------------------------------------------------------ V5: the recommendations system
def test_the_recommendations_system_is_linked_to_what_the_feed_moves():
    graph = MetricGraph.load()
    for m in ("feed_ctr", "stockout_rate", "views_per_session", "discount_rate"):
        assert "recommendations" in graph.specs[m].systems, m


def stockouts_in(channel):
    def step(con):   # unavailable products shown in one channel only: stock-outs there x12
        con.execute("UPDATE facts_hourly SET stockouts = MIN(product_views, stockouts * 12 + 3) WHERE channel = ? AND ts >= ?",
                    (channel, ts(FAULT)))
        con.commit()
    return step


def test_a_market_wide_recommendations_release_is_in_scope_for_the_recommendations_channel(run):
    incidents = run(stockouts_in("recommendations"), release(FAULT, "recommendations", None, None, "recommendations model 3.1 rollout"))
    inc = next(i for i in incidents if i["segment"].get("channel") == "recommendations")
    lead = inc["hypotheses"][0]
    assert lead["kind"] == "release" and lead["status"] == "supported" and "recommendations model 3.1" in lead["cause"]
    assert any("recommendations channel" in e for e in lead["evidence"])


def test_a_recommendations_release_does_not_explain_a_change_in_another_channel(run):
    incidents = run(stockouts_in("organic"), release(FAULT, "recommendations", None, None, "recommendations model 3.1 rollout"))
    inc = next(i for i in incidents if i["segment"].get("channel") == "organic")
    rec = next(h for h in inc["hypotheses"] if "recommendations model 3.1" in h["cause"])
    assert rec["status"] == "rejected" and any("organic" in e for e in rec["evidence"])


# ------------------------------------------------------------------ what "supported" requires
def test_each_release_says_what_supported_requires_and_which_parts_held(run):
    (inc,) = run(payment, release(FAULT, "payments-sdk", "DE", None, "payments-sdk rollout (DE)"))
    for h in inc["hypotheses"]:
        if h["kind"] == "release":
            assert h["evidence"][-1].startswith("supported needs the system linked, the scope exactly the place and the timing at the onset")
    lead = inc["hypotheses"][0]
    assert lead["evidence"][-1].endswith("linked yes, scope exact, timing yes")
    wide = next(h for h in inc["hypotheses"] if h["cause"].endswith("rollout (DE)"))
    assert "scope wider" in wide["evidence"][-1]


def test_an_event_the_monitor_did_not_accept_never_makes_the_incident_innocent(clean_db, tmp_path):
    """The monitor raised the alert although a promo covers it (partial_explanation): its verdict stands,
    even when the rise would fit the investigator's own size rule."""
    import dataclasses
    db = promo_db(clean_db, tmp_path)
    alerts = promo_alerts(db, FAULT + 4 * H)
    why = "it rose more than twice what earlier promos gave"
    raised = [dataclasses.replace(a, observed=1.5 * a.expected, episode_observed=1.5 * a.expected, explained_by=None,
                                  partial_explanation=dict(a.explained_by, why=why)) for a in alerts]
    _, _, body = investigate_by_hand(db, raised, FAULT + 4 * H)
    event = next(h for h in body["hypotheses"] if h["kind"] == "calendar")
    assert event["status"] == "rejected" and any(why in e for e in event["evidence"])
    assert "innocent" not in body
