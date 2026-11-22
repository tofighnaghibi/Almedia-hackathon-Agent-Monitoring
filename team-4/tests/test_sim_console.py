"""The console's inject endpoint for the four newer simulator kinds (traffic_drop, feed_unavailable, mix_shift,
discount_push): accepted with their documented settings, refused with a 422 when a setting is wrong, 409 on stacking."""
import sqlite3

import pytest
from fastapi.testclient import TestClient

import ui.app as ui
from sim.run import backfill

START = "2026-10-25T00:00:00Z"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = backfill(str(tmp_path / "market.db"), 48, START, seed=1)
    monkeypatch.setattr(ui, "MARKET_DB", db_path)
    monkeypatch.setattr(ui, "INCIDENTS_DIR", tmp_path / "incidents")
    monkeypatch.setattr(ui, "INCOMING_DIR", tmp_path / "incoming")
    return TestClient(ui.app)


def _rows(client, sql):
    con = sqlite3.connect(ui.MARKET_DB)
    try:
        return con.execute(sql).fetchall()
    finally:
        con.close()


def test_the_four_new_kinds_can_be_injected_from_the_console(client):
    ok = [{"kind": "traffic_drop", "country": "DE", "platform": "Android"},
          {"kind": "feed_unavailable"},
          {"kind": "mix_shift", "category": "electronics"},
          {"kind": "discount_push"}]
    for body in ok:
        r = client.post("/api/inject", json=body)
        assert r.status_code == 200, (body, r.text)
        assert r.json()["start"] == "2026-10-26T23:00:00Z"
    assert [i["kind"] for i in client.get("/api/state").json()["injected"]] == [b["kind"] for b in ok]
    scopes = dict(_rows(client, "SELECT kind, scope FROM ground_truth"))
    assert '"severity": 0.5' in scopes["traffic_drop"]                  # the documented default, not the payment one
    assert '"share": 0.5' in scopes["mix_shift"] and '"lift": 0.6' in scopes["discount_push"]
    assert '"channel": "recommendations"' in scopes["feed_unavailable"]
    releases = _rows(client, "SELECT system, description FROM releases ORDER BY rowid")
    assert releases == [("recommendations", "recommendations model 3.1 rollout"),
                        ("recommendations", "recommendations ranking 3.2 rollout")]


def test_the_payment_default_severity_is_unchanged(client):
    assert client.post("/api/inject", json={"kind": "payment_failure", "country": "DE", "platform": "Web"}).status_code == 200
    (scope,) = _rows(client, "SELECT scope FROM ground_truth")[0]
    assert '"severity": 0.35' in scope


@pytest.mark.parametrize("body", [
    {"kind": "traffic_drop", "country": "DE"},
    {"kind": "traffic_drop", "country": "XX", "platform": "Android"},
    {"kind": "traffic_drop", "country": "DE", "platform": "Android", "severity": 1.2},
    {"kind": "mix_shift"},
    {"kind": "mix_shift", "category": "cars"},
    {"kind": "mix_shift", "category": "home", "share": 1.0},
    {"kind": "discount_push", "lift": 0},
])
def test_bad_settings_for_the_new_kinds_are_a_422_and_change_nothing(client, body):
    assert client.post("/api/inject", json=body).status_code == 422
    assert client.get("/api/state").json()["injected"] == []


@pytest.mark.parametrize("body", [
    {"kind": "traffic_drop", "country": "FR", "platform": "Web", "severity": 0.4},
    {"kind": "feed_unavailable"},
    {"kind": "mix_shift", "category": "home"},
    {"kind": "discount_push"},
])
def test_the_same_click_twice_is_a_409(client, body):
    assert client.post("/api/inject", json=body).status_code == 200
    assert client.post("/api/inject", json=body).status_code == 409
    assert len(client.get("/api/state").json()["injected"]) == 1
