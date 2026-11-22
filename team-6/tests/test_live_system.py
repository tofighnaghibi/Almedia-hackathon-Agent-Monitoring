"""Live system: streaming simulator, live detection, issue tracking, reports, runner, dashboard API."""

import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import pandas as pd
import pytest

from graph import MetricEngine
from monitoring.detect import Detector
from monitoring.live import LiveDetector
from reporting.issues import IssueTracker, TrackedEpisode
from reporting.report import LLMNarrator, build_report, severity
from runtime.runner import AutonomousMonitor
from simulator.stream import LiveMarketplace

HOUR = pd.Timedelta(hours=1)


# --- streaming simulator -------------------------------------------------------------------

def test_stream_reveals_hours_and_applies_injections_from_then_on():
    live = LiveMarketplace(history_days=28, live_days=1)
    assert live.history()["timestamp"].max() < live.live_start
    first = live.step()
    assert first.hour == live.live_start and (first.rows["timestamp"] == first.hour).all()
    live.inject("payment_failure", duration_hours=3)
    rows = live.step().rows
    de = rows[(rows.country == "DE") & (rows.platform == "android")]
    other = rows[(rows.country == "DE") & (rows.platform == "ios")]
    assert de.orders.sum() / de.payment_attempts.sum() < 0.5 < 0.9 < other.orders.sum() / other.payment_attempts.sum()
    assert live.events["event_type"].tolist() == ["release"]
    assert live.truth[0]["preset"] == "payment_failure"


def test_late_data_is_held_back_then_backfilled():
    live = LiveMarketplace(history_days=28, live_days=1, release_delay_hours=2)
    live.inject("late_missing_data", duration_hours=1)  # hours 0 and 1
    ticks = [live.step() for _ in range(6)]
    assert [t.held_back for t in ticks[:2]] == [4, 4] and ticks[2].held_back == 0
    late = [t for t in ticks if t.late_rows]
    assert len(late) == 1 and late[0].late_rows == 8
    assert (late[0].rows["ingested_at"] > late[0].rows["timestamp"] + HOUR).sum() == 8


# --- live detection ----------------------------------------------------------------------------

def test_live_detector_scores_like_the_batch_detector():
    live = LiveMarketplace(history_days=28, live_days=1)
    engine = MetricEngine(tables={"marketplace_hourly": live.history()})
    det = LiveDetector(engine, live.live_start)
    det.watch(["orders", "payment_success_rate", "out_of_stock_rate", "row_count"])
    live.inject("payment_failure", duration_hours=6)
    cells = []
    for _ in range(6):
        tk = live.step()
        engine.append_rows("marketplace_hourly", tk.rows)
        det.refresh(tk.rows["timestamp"].min(), tk.hour + HOUR)
        cells.append(det.score_hour(tk.hour))
    cells = pd.concat(cells)
    batch = Detector(engine, det.config)
    for m in ["orders", "payment_success_rate", "out_of_stock_rate"]:
        b = batch.score(m, by=["country", "platform"], start=live.live_start, train_end=live.live_start, windows=[1, 6])
        j = cells[(cells.metric == m) & (cells.by == "country|platform")].merge(
            b, on=["country", "platform", "window_hours", "window_end"], suffixes=("_l", "_b"))
        ok = j.z_l.notna() & j.z_b.notna()
        assert ok.sum() > 100 and (j.z_l[ok] - j.z_b[ok]).abs().max() < 1e-3
    hit = cells[(cells.metric == "payment_success_rate") & cells.is_anomaly & (cells.country == "DE")
                & (cells.platform == "android")]
    assert pd.Timestamp(hit.window_end.min()) - HOUR <= live.live_start + HOUR  # flagged in its first hours


# --- issue tracking ------------------------------------------------------------------------------

def _ep(metric, seg, hour, direction="down", key=None):
    return TrackedEpisode(key=key or f"{metric}|{seg}|{direction}|{hour:%H}", metric=metric, segment=seg,
                          direction=direction, impact="bad", first=hour, last=hour, peak_z=-9.0,
                          actual=50.0, expected=100.0, lower=80.0, upper=120.0)


def _finding(node, seg, status="confirmed", nodes=()):
    return {"hypotheses": [{"node": node, "segment": seg, "status": status, "cause": f"{node} change",
                            "source": "formula_component", "is_symptom": False, "missing_conditions": [],
                            "explained_share": 0.95, "evidence": []}],
            "evidence_list": [{"type": "formula_decomposition", "nodes": [{"metric": n, "share": 0.5} for n in nodes]}],
            "business_impact": {"lost_orders": 100, "lost_revenue_eur": 8000.0},
            "time_window": {"start": "2026-10-29T00:00:00Z", "end": "2026-10-29T02:00:00Z"}}


def test_symptoms_join_one_issue_and_it_alerts_once():
    tr, t0 = IssueTracker(), pd.Timestamp("2026-10-29T00:00Z")
    iss, created = tr.route(_ep("payment_success_rate", {"country": "DE", "platform": "android"}, t0), t0)
    assert created and iss.status == "investigating"
    assert tr.set_finding(iss, _finding("payment_success_rate", {"country": "DE", "platform": "android"}),
                          t0, downstream={"orders", "revenue"}) == "opened"
    for m, seg in [("orders", {"country": "DE", "platform": "android"}), ("revenue", {"country": "DE"}),
                   ("orders", {})]:
        other, created = tr.route(_ep(m, seg, t0 + HOUR), t0 + HOUR)
        assert other is iss and not created
    # the same finding again is not news
    assert tr.set_finding(iss, _finding("payment_success_rate", {"country": "DE", "platform": "android"}), t0 + HOUR) is None
    # a different country is a different problem
    fr, created = tr.route(_ep("orders", {"country": "FR"}, t0 + HOUR), t0 + HOUR)
    assert created and fr is not iss


def test_same_root_cause_merges_and_quiet_episodes_resolve():
    tr, t0 = IssueTracker(), pd.Timestamp("2026-10-29T00:00Z")
    a, _ = tr.route(_ep("search_ctr", {"country": "DE"}, t0), t0)
    tr.set_finding(a, _finding("search_ctr", {"country": "DE"}), t0)
    b, created = tr.route(_ep("conversion_rate", {"category": "fashion"}, t0 + HOUR), t0 + HOUR)
    assert created
    assert tr.set_finding(b, _finding("search_ctr", {"country": "DE", "category": "fashion"}), t0 + HOUR) is None
    assert b.status == "merged" and b.merged_into == a.id
    alerts = tr.tick(t0 + 6 * HOUR)
    assert (a, "resolved") in alerts and a.status == "resolved"


def test_data_gap_resolves_with_the_backfill():
    tr, t0 = IssueTracker(), pd.Timestamp("2026-10-29T00:00Z")
    missing = [{"country": "DE", "platform": "ios", "category": c} for c in ["beauty", "fashion"]]
    iss, created = tr.data_gap(t0, missing, ["evt_9"], t0 + HOUR)
    assert created and iss.kind == "data_quality" and iss.segment == {"country": "DE", "platform": "ios"}
    again, created = tr.data_gap(t0 + HOUR, missing, ["evt_9"], t0 + 2 * HOUR)
    assert again is iss and not created
    assert tr.data_complete([t0], t0 + 3 * HOUR) == []  # one hour still pending
    assert tr.data_complete([t0 + HOUR], t0 + 4 * HOUR) == [iss] and iss.status == "resolved"


# --- reports ---------------------------------------------------------------------------------------------

def test_report_sections_and_severity():
    tr, t0 = IssueTracker(), pd.Timestamp("2026-10-29T00:00Z")
    iss, _ = tr.route(_ep("payment_success_rate", {"country": "DE", "platform": "android"}, t0), t0)
    tr.set_finding(iss, _finding("payment_success_rate", {"country": "DE", "platform": "android"}), t0)
    r = build_report(iss, tr)
    assert severity(iss) == "medium"  # EUR 8,000 in 2 hours = EUR 96,000 a day
    for section in ["What happened", "Business impact", "Recommended actions"]:
        assert section in r["markdown"]
    assert any("payment provider" in a for a in r["recommended_actions"])
    assert r["headline"].startswith("Confirmed")


def test_llm_narrative_with_an_unknown_number_is_rejected():
    class Fake:
        def __init__(self, text):
            self.text = text

        def complete_json(self, system, user):
            return {"narrative": self.text}

    tr, t0 = IssueTracker(), pd.Timestamp("2026-10-29T00:00Z")
    iss, _ = tr.route(_ep("payment_success_rate", {"country": "DE", "platform": "android"}, t0), t0)
    tr.set_finding(iss, _finding("payment_success_rate", {"country": "DE", "platform": "android"}), t0)
    good = build_report(iss, tr, narrator=LLMNarrator(Fake("Payment success fell in DE on android; 100 orders were lost.")))
    bad = build_report(iss, tr, narrator=LLMNarrator(Fake("About 4,321 orders were lost.")))
    assert good["narrative"] and bad["narrative"] is None


# --- the runner, end to end -----------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def monitor():
    live = LiveMarketplace(history_days=28, live_days=2)
    mon = AutonomousMonitor(live)
    mon.run(hours=40, script={2: [("inject", "payment_failure")], 4: [("inject", "planned_promo")]})
    return mon


def test_runner_finds_confirms_and_resolves_the_payment_failure(monitor):
    hits = [i for i in monitor.tracker.visible() if i.root and i.root["node"] == "payment_success_rate"
            and i.root["segment"] == {"country": "DE", "platform": "android"}]
    assert len(hits) == 1
    iss = hits[0]
    assert iss.root["status"] == "confirmed" and iss.status == "resolved"
    assert [a["type"] for a in iss.alerts] == ["opened", "resolved"]
    assert "evt_" in iss.report["headline"]  # the release is named as the likely trigger
    assert len(iss.episodes) > 3  # symptoms grouped, not alerted separately


def test_runner_does_not_alert_on_the_planned_promo(monitor):
    uk = [i for i in monitor.tracker.visible() if i.alerts and i.kind == "incident"
          and i.segment.get("country") == "UK"]
    assert uk == []
    assert any("explained by planned event" in f.text for f in monitor.feed)


def test_runner_never_reads_the_ground_truth(monitor):
    import inspect

    import runtime.runner as runner

    assert "truth" not in inspect.getsource(runner).replace("ground truth", "")


def test_dashboard_api(monitor):
    from demo.server import DemoController, _handler

    ctl = DemoController.__new__(DemoController)
    ctl.mon, ctl.script, ctl.delay, ctl.out_dir = monitor, {}, 0.0, "unused"
    ctl.lock, ctl.paused, ctl._step, ctl.saved = threading.RLock(), True, False, False
    ctl.examples = {}
    srv = ThreadingHTTPServer(("127.0.0.1", 0), _handler(ctl))
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{srv.server_address[1]}"
    try:
        get = lambda p: json.loads(urllib.request.urlopen(base + p, timeout=30).read())  # noqa: E731
        assert b"Eye of the System" in urllib.request.urlopen(base + "/", timeout=10).read()
        s = get("/api/state")
        assert s["issues"] and s["feed"] and s["presets"]
        d = get(f"/api/issue?id={s['issues'][0]['id']}")
        assert d["report"]["title"] and d["charts"]
        assert get("/api/tree")["nodes"]
        assert get("/api/score")["summary"]["faults_found"] == "1/1"
    finally:
        srv.shutdown()
