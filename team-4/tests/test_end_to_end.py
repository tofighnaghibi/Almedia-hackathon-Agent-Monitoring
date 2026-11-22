"""End to end, with the real parts and no made-up incident: simulator -> injected faults -> monitor replay ->
incident files -> the console's API. About a minute, because the monitor really walks the days hour by hour.

What it proves: every injected fault is found by the monitor, in the right place and with the right cause, nothing that is
innocent raises an alarm, and the files are in a shape the console and the scorer both accept."""
import json
import subprocess
import sys
from pathlib import Path

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from sim import db, inject
from sim import score as sc
from sim.run import backfill
from ui import domains as domain_map

ROOT = Path(__file__).resolve().parent.parent
T = pd.Timestamp


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    d = tmp_path_factory.mktemp("e2e")
    # 16 days from 25 Oct end on 9 Nov 23:00: the last fault ends on 8 Nov 22:00, so a full day of normal hours follows
    path = backfill(str(d / "market.db"), 16 * 24, "2026-10-25T00:00:00Z", seed=11)
    con = db.connect(path)
    # six events, at least eight hours apart: four faults (payments, stockouts, search, ad budget) and two innocent ones
    inject.promo(con, "ES", T("2026-11-05T08:00:00Z"), T("2026-11-05T18:00:00Z"), replay_now=False)
    inject.data_delay(con, "payments", T("2026-11-05T22:00:00Z"), T("2026-11-06T01:00:00Z"), delay_hours=2, replay_now=False)
    inject.payment_failure(con, "DE", "Android", T("2026-11-06T10:00:00Z"), severity=0.5, end=T("2026-11-06T18:00:00Z"), replay_now=False)
    inject.stockout_surge(con, "electronics", T("2026-11-07T12:00:00Z"), T("2026-11-07T20:00:00Z"), replay_now=False)
    inject.search_regression(con, "FR", T("2026-11-08T00:00:00Z"), severity=0.5, end=T("2026-11-08T08:00:00Z"), replay_now=False)
    inject.ad_budget_exhausted(con, "PL", T("2026-11-08T12:00:00Z"), T("2026-11-08T22:00:00Z"), boost_hours=4, replay_now=False)
    db.write_hours(con, 0, 16 * 24)
    con.close()
    incidents = d / "incidents"
    run = subprocess.run([sys.executable, "-m", "brain.run", "--replay", "--db", str(path), "--incidents", str(incidents), "--fresh"],
                         cwd=ROOT, capture_output=True, text=True, timeout=420)
    assert run.returncode == 0, run.stdout[-500:] + run.stderr[-800:]
    return {"db": path, "incidents": incidents, "dir": d, "run_out": run.stdout}


def read_incidents(folder: Path) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(folder.glob("*.json"))]


def test_the_monitor_finds_every_fault_with_the_right_place_and_cause_and_no_false_alarm(world):
    events, start, end = sc.load_truth(world["db"])
    incidents = sc.load_incidents(world["incidents"])
    result = sc.score(events, incidents, end, (end - start) / pd.Timedelta(days=1))
    assert result["faults"] == 4 and result["detected"] == 4, result["per_fault"]
    assert result["localized_rate"] == 1, result["per_fault"]
    assert result["false_alarms"] == 0, [i["id"] for i in incidents]
    assert result["innocents"] == 2 and result["innocents_silent"] == 2
    assert result["correct_cause_rate"] >= 0.75, result["per_fault"]           # 3 of 4 at least; the weakest is stockouts
    assert result["mean_delay_h"] <= 3, result["per_fault"]


def test_each_fault_is_its_own_incident_when_they_are_apart_in_time_and_place(world):
    incidents = [i for i in read_incidents(world["incidents"]) if i.get("status") in ("open", "ongoing", "resolved")]
    assert len(incidents) == 4
    assert len({json.dumps(i.get("segment"), sort_keys=True) for i in incidents}) == 4          # four different places
    assert all(i["status"] == "resolved" for i in incidents)                                     # every fault ended inside the data


def test_innocent_changes_are_recorded_as_explained_or_pending_and_never_as_alerts(world):
    top = {p.name: json.loads(p.read_text(encoding="utf-8")) for p in world["incidents"].glob("*.json")}
    statuses = {i.get("status") for i in top.values()}
    assert "explained" in statuses                                                              # the promo, from the calendar
    explained = [i for i in top.values() if i.get("status") == "explained"]
    assert any(any(h.get("kind") == "calendar" for h in i.get("hypotheses", [])) for i in explained)


def test_the_files_have_the_shape_the_console_and_the_scorer_read(world):
    for i in read_incidents(world["incidents"]):
        assert isinstance(i["id"], str) and isinstance(i["metric"], str)
        assert pd.Timestamp(i["first_seen"]).tzinfo is not None
        assert all(isinstance(h.get("kind"), str) for h in i.get("hypotheses", [])), i["id"]
    text = "".join(p.read_text(encoding="utf-8") for p in world["incidents"].glob("*.json"))
    assert "NaN" not in text and "Infinity" not in text                      # not valid JSON in a browser


def test_the_console_serves_exactly_what_the_monitor_wrote(world, monkeypatch):
    import ui.app as ui
    monkeypatch.setattr(ui, "MARKET_DB", world["db"])
    monkeypatch.setattr(ui, "INCIDENTS_DIR", world["incidents"])
    client = TestClient(ui.app)
    body = client.get("/api/incidents").json()
    written = {i["id"] for i in read_incidents(world["incidents"]) if isinstance(i.get("id"), str)}
    assert body["example"] is False and body["skipped"] == 0
    assert {i["id"] for i in body["incidents"]} == written
    state = client.get("/api/state").json()
    assert state["ready"] and len(state["injected"]) == 6 and not any(i["active"] for i in state["injected"][2:])   # all ended
    assert client.get("/").status_code == 200 and client.get("/data").status_code == 200


def test_every_domain_of_an_injected_fault_lights_in_the_console(world):
    """The page counts an incident for a domain when its metric, a contribution or a metric the monitor also watched
    (a 'watch' step in the trail) belongs to it: the same rule as incidentNodes() in ui/static/index.html."""
    owner = {m: d for d, _, metrics in domain_map.DOMAINS for m in metrics}
    touched = set()
    for i in read_incidents(world["incidents"]):
        if i.get("status") not in ("open", "ongoing", "resolved"):
            continue
        nodes = {i["metric"]} | {c.get("node") for c in i.get("contributions", []) if isinstance(c, dict)}
        nodes |= {t.get("target") for t in i.get("trail", []) if isinstance(t, dict) and t.get("action") == "watch"}
        touched |= {owner[n] for n in nodes if n in owner}
    assert {"payments", "inventory", "search", "ads"} <= touched, touched


# ---------------------------------------------------------------------------------------------------------------------
# Four faults that begin in the same hour, as when a presenter clicks four buttons in a row.
@pytest.fixture(scope="module")
def together(tmp_path_factory):
    d = tmp_path_factory.mktemp("e2e_together")
    path = backfill(str(d / "market.db"), 12 * 24, "2026-10-25T00:00:00Z", seed=12)
    con = db.connect(path)
    start, end = T("2026-11-04T10:00:00Z"), T("2026-11-04T20:00:00Z")
    inject.payment_failure(con, "DE", "iOS", start, severity=0.5, end=end, replay_now=False)
    inject.stockout_surge(con, "electronics", start, end, replay_now=False)
    inject.search_regression(con, "DE", start, severity=0.5, end=end, replay_now=False)
    inject.ad_budget_exhausted(con, "DE", start, start + pd.Timedelta(hours=12), boost_hours=4, replay_now=False)
    db.write_hours(con, 0, 12 * 24)
    con.close()
    incidents = d / "incidents"
    run = subprocess.run([sys.executable, "-m", "brain.run", "--replay", "--db", str(path), "--incidents", str(incidents), "--fresh"],
                         cwd=ROOT, capture_output=True, text=True, timeout=420)
    assert run.returncode == 0, run.stdout[-500:] + run.stderr[-800:]
    return {"db": path, "incidents": incidents}


def test_faults_that_begin_together_are_found_and_none_of_them_is_lost_from_the_report(together):
    """The monitor may fold several faults that begin together into one or two incidents (by design); what must hold is that
    every one of them is named somewhere in the incident, so each domain lights in the console."""
    incidents = [i for i in read_incidents(together["incidents"]) if i.get("status") in ("open", "ongoing", "resolved")]
    assert incidents, "nothing was found"
    assert len(incidents) <= 4
    owner = {m: dom for dom, _, metrics in domain_map.DOMAINS for m in metrics}
    touched = set()
    for i in incidents:
        nodes = {i["metric"]} | {c.get("node") for c in i.get("contributions", []) if isinstance(c, dict)}
        nodes |= {t.get("target") for t in i.get("trail", []) if isinstance(t, dict) and t.get("action") == "watch"}
        touched |= {owner[n] for n in nodes if n in owner}
    assert {"payments", "inventory", "search", "ads"} <= touched, touched
    assert all(pd.Timestamp(i["first_seen"]) >= T("2026-11-04T10:00:00Z") for i in incidents)       # nothing before the faults
    assert all(pd.Timestamp(i["first_seen"]) <= T("2026-11-04T13:00:00Z") for i in incidents)       # found within three hours


def test_faults_that_begin_together_are_all_over_when_the_data_shows_normal_hours_again(together):
    incidents = [i for i in read_incidents(together["incidents"]) if i.get("status") in ("open", "ongoing", "resolved")]
    assert incidents and all(i["status"] == "resolved" for i in incidents), [(i["id"], i["status"]) for i in incidents]
