"""Argus must work with no LLM key: template reports and keyword metric parsing."""
from fastapi.testclient import TestClient

from argus import llm
from argus.api import app


def test_no_key_falls_back_to_templates():
    assert not llm.configured()
    assert llm.chat("system", "user") is None
    assert llm.chat_json("system", "user") is None
    assert llm.load_dotenv() is False          # ARGUS_NO_DOTENV set by conftest


def test_api_without_key_uses_templates_and_keywords():
    client = TestClient(app)
    S = {"X-Session": "no-llm"}
    assert client.get("/api/health").json()["llm"] is None
    inc_id = client.post("/api/demo/bot_farm", headers=S).json()["incident_id"]
    rep = client.get(f"/api/incidents/{inc_id}", headers=S).json()["reports"]
    assert rep["source"] == "template"
    assert "Canada" in rep["ceo"] and rep["manager"] and rep["engineer"]
    m = client.post("/api/metrics", json={"name": "Start rate", "description": "how often users start offers"},
                    headers=S).json()
    assert m["validation"]["ok"] and m["node"]["expr"] == "starts / impressions"
    assert "keyword rules" in m["validation"]["checks"][0]["detail"]


def test_unreachable_endpoint_falls_back(monkeypatch):
    monkeypatch.setenv("LLM_BASE_URL", "http://127.0.0.1:9/v1/")
    monkeypatch.setenv("LLM_API_KEY", "test-key-not-real")
    monkeypatch.setenv("LLM_MODEL", "none")
    assert llm.configured()
    assert llm.chat("s", "u", timeout=1.0) is None


def test_grounding_check_rejects_invented_numbers():
    from argus.agent import ungrounded_numbers
    src = '{"risk_7d_usd": 233743, "explained": 0.988, "impact_usd_h": 85.1, "reaction_h": 28}'
    assert ungrounded_numbers("Risk $233,743; 98.8% explained; about $85/h; reclaims in 28 h.", src) == []
    assert ungrounded_numbers("Risk $250,000 and 42% explained.", src) == [250000.0, 42.0]
