"""The console's settings: GET/PUT /api/settings, the reset, the API key in the repository's .env (localhost only,
never echoed) and the Settings panel of the main console (ui/static/v3/). No network; the key is a fake one."""
import json
import os
import re
from datetime import datetime, timedelta, timezone

import pytest
from fastapi.testclient import TestClient

import ui.app as ui

FAKE_KEY = "sk-ant-test-000"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """A data folder with no database (the settings never need one) and a repository .env with two other lines."""
    data = tmp_path / "data"
    data.mkdir()
    repo = tmp_path / "repo"
    repo.mkdir()
    env_file = repo / ".env"
    env_file.write_text("# local settings\nOTHER_TOKEN=abc\n", encoding="utf-8", newline="\n")
    monkeypatch.setattr(ui, "MARKET_DB", data / "market.db")
    monkeypatch.setattr(ui, "INCIDENTS_DIR", data / "incidents")
    monkeypatch.setattr(ui, "INCOMING_DIR", data / "incoming")
    monkeypatch.setattr(ui, "ENV_FILE", env_file)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    return {"data": data, "env": env_file, "settings": data / "settings.json", "status": data / "brain_status.json"}


@pytest.fixture()
def client(env):
    return TestClient(ui.app)


def iso(t: datetime) -> str:
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


# ----------------------------------------------------------------------------------- GET
def test_get_returns_the_defaults_without_a_file_and_no_status(client, env):
    r = client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    defaults = {"narration": {"enabled": False}, "clock": {"seconds_per_hour": 2.0},
                "detection": {"min_surprise": 7.0, "min_effect": 0.10, "volume_min_effect": 0.20,
                              "release_min_surprise": 4.0}}
    assert body["defaults"] == defaults
    assert {k: body["settings"][k] for k in defaults} == defaults
    assert body["ranges"]["clock"]["seconds_per_hour"] == {"min": 0.25, "max": 60}
    assert body["ranges"]["detection"]["min_surprise"] == {"min": 5, "max": 10}
    assert body["ranges"]["detection"]["min_effect"] == {"min": 0.03, "max": 0.5}
    assert body["ranges"]["detection"]["volume_min_effect"] == {"min": 0.05, "max": 0.6}
    assert body["ranges"]["detection"]["release_min_surprise"] == {"min": 3, "max": 7}
    assert body["status"] is None and body["monitor_alive"] is False
    e = body["environment"]
    for k in ("market_db", "incidents_dir", "incoming_metrics_dir", "graph_json", "repository", "clock_mode",
              "display_timezone", "newest_hour", "key_set"):
        assert k in e, k
    assert e["market_db"] == str(env["data"] / "market.db")
    assert e["key_set"] is False and e["display_timezone"] == "Europe/Berlin"
    assert e["settings_file"] == str(env["settings"])
    assert not env["settings"].exists()            # reading never writes


def test_get_reads_the_file_and_clamps_what_is_out_of_range(client, env):
    env["settings"].write_text(json.dumps({"detection": {"min_surprise": 9.0, "min_effect": 2.0},
                                           "clock": {"seconds_per_hour": 5}}), encoding="utf-8")
    body = client.get("/api/settings").json()
    assert body["settings"]["detection"]["min_surprise"] == 9.0
    assert body["settings"]["detection"]["min_effect"] == 0.5            # clamped to the range
    assert body["settings"]["detection"]["volume_min_effect"] == 0.2     # missing: the default
    assert body["settings"]["clock"]["seconds_per_hour"] == 5
    assert any("min_effect" in w for w in body["warnings"])


def test_get_survives_a_broken_file(client, env):
    env["settings"].write_text("{not json", encoding="utf-8")
    body = client.get("/api/settings").json()
    assert body["settings"]["detection"]["min_surprise"] == 7.0 and body["warnings"]


def test_status_is_passed_on_and_alive_only_when_fresh(client, env):
    now = datetime.now(timezone.utc)
    status = {"pid": 4321, "updated_at": iso(now), "newest_hour": "2026-11-22T10:00:00Z", "watched_series": 760,
              "incidents_folder": "x", "narration": {"status": "not asked", "model": "claude-opus-5-5", "key_set": False},
              "applied": {"narration_enabled": False, "min_surprise": 7.0, "min_effect": 0.1, "volume_min_effect": 0.2,
                          "release_min_surprise": 4.0}, "applied_at": iso(now), "settings_error": None}
    env["status"].write_text(json.dumps(status), encoding="utf-8")
    body = client.get("/api/settings").json()
    assert body["status"]["pid"] == 4321 and body["monitor_alive"] is True
    assert body["environment"]["newest_hour"] == "2026-11-22T10:00:00Z"
    status["updated_at"] = iso(now - timedelta(seconds=30))
    env["status"].write_text(json.dumps(status), encoding="utf-8")
    assert client.get("/api/settings").json()["monitor_alive"] is False


# ----------------------------------------------------------------------------------- PUT and reset
def test_put_in_range_writes_the_file_atomically_with_updated_at(client, env):
    r = client.put("/api/settings", json={"narration": {"enabled": True}, "detection": {"min_surprise": 8.5}})
    assert r.status_code == 200, r.text
    raw = env["settings"].read_bytes()
    assert b"\r\n" not in raw
    saved = json.loads(raw.decode("utf-8"))
    assert saved["narration"] == {"enabled": True} and saved["detection"]["min_surprise"] == 8.5
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", saved["updated_at"])
    assert r.json()["updated_at"] == saved["updated_at"]
    # a second save merges into the file: what was saved before stays
    client.put("/api/settings", json={"clock": {"seconds_per_hour": 0.5}})
    saved = json.loads(env["settings"].read_text(encoding="utf-8"))
    assert saved["clock"]["seconds_per_hour"] == 0.5 and saved["detection"]["min_surprise"] == 8.5
    assert saved["narration"]["enabled"] is True
    assert not [p for p in env["data"].iterdir() if p.name not in ("settings.json",)]     # no temp file left


@pytest.mark.parametrize("body, word", [
    ({"clock": {"seconds_per_hour": 0.1}}, "seconds_per_hour"),
    ({"clock": {"seconds_per_hour": 61}}, "seconds_per_hour"),
    ({"detection": {"min_surprise": 4.9}}, "min_surprise"),
    ({"detection": {"min_effect": 0.6}}, "min_effect"),
    ({"detection": {"volume_min_effect": 0.01}}, "volume_min_effect"),
    ({"detection": {"release_min_surprise": 8}}, "release_min_surprise"),
    ({"narration": {"enabled": "yes"}}, "enabled"),
    ({"detection": {"min_surprise": "7"}}, "min_surprise"),
    ({"detection": {"min_surprise": True}}, "min_surprise"),
    ({"detection": {"nope": 1}}, "nope"),
    ({"other": {}}, "other"),
])
def test_put_out_of_range_or_wrong_is_422_with_the_reason_and_writes_nothing(client, env, body, word):
    r = client.put("/api/settings", json=body)
    assert r.status_code == 422
    assert word in r.json()["detail"]
    assert not env["settings"].exists()


def test_put_nan_is_refused(client, env):
    r = client.put("/api/settings", content='{"detection": {"min_surprise": NaN}}',
                   headers={"Content-Type": "application/json"})
    assert r.status_code == 422 and not env["settings"].exists()


def test_reset_writes_the_defaults(client, env):
    client.put("/api/settings", json={"detection": {"min_surprise": 9.5}, "narration": {"enabled": True}})
    r = client.post("/api/settings/reset")
    assert r.status_code == 200
    saved = json.loads(env["settings"].read_text(encoding="utf-8"))
    assert saved["detection"] == {"min_surprise": 7.0, "min_effect": 0.1, "volume_min_effect": 0.2,
                                  "release_min_surprise": 4.0}
    assert saved["narration"] == {"enabled": False} and saved["clock"] == {"seconds_per_hour": 2.0}
    assert "updated_at" in saved


# ----------------------------------------------------------------------------------- the API key
def test_saving_the_key_writes_the_line_and_never_echoes_it(client, env):
    r = client.post("/api/settings/key", json={"key": FAKE_KEY})
    assert r.status_code == 200, r.text
    assert FAKE_KEY not in r.text and r.json()["key_set"] is True
    lines = env["env"].read_text(encoding="utf-8").split("\n")
    assert f"ANTHROPIC_API_KEY={FAKE_KEY}" in lines
    assert "# local settings" in lines and "OTHER_TOKEN=abc" in lines        # the other lines are kept
    g = client.get("/api/settings")
    assert FAKE_KEY not in g.text and g.json()["environment"]["key_set"] is True
    assert g.json()["environment"]["key_source"] == ".env"
    # a new key replaces the line, it is not added twice
    client.post("/api/settings/key", json={"key": "sk-ant-test-111"})
    text = env["env"].read_text(encoding="utf-8")
    assert text.count("ANTHROPIC_API_KEY=") == 1 and "sk-ant-test-111" in text and FAKE_KEY not in text


def test_removing_the_key_keeps_the_other_lines(client, env):
    client.post("/api/settings/key", json={"key": FAKE_KEY})
    r = client.delete("/api/settings/key")
    assert r.status_code == 200 and r.json()["key_set"] is False
    text = env["env"].read_text(encoding="utf-8")
    assert "ANTHROPIC_API_KEY" not in text and text == "# local settings\nOTHER_TOKEN=abc\n"
    assert client.get("/api/settings").json()["environment"]["key_set"] is False


def test_the_key_creates_a_missing_env_file_and_keeps_crlf_lines(client, env):
    env["env"].unlink()
    assert client.post("/api/settings/key", json={"key": FAKE_KEY}).status_code == 200
    assert env["env"].read_text(encoding="utf-8") == f"ANTHROPIC_API_KEY={FAKE_KEY}\n"
    env["env"].write_bytes(b"A=1\r\nANTHROPIC_API_KEY=old\r\nB=2\r\n")
    client.post("/api/settings/key", json={"key": FAKE_KEY})
    assert env["env"].read_bytes() == f"A=1\r\nANTHROPIC_API_KEY={FAKE_KEY}\r\nB=2\r\n".encode()


@pytest.mark.parametrize("value", ["", "   ", "sk-ant test", "sk-ant-test\n000", "x" * 301, 123, None, ["sk-ant-test-000"]])
def test_a_key_that_does_not_look_like_a_key_is_refused_without_echo(client, env, value):
    r = client.post("/api/settings/key", json={"key": value})
    assert r.status_code == 422
    if isinstance(value, str) and value.strip():
        assert value not in r.text
    assert "sk-ant-test-000" not in r.text
    assert "ANTHROPIC_API_KEY" not in env["env"].read_text(encoding="utf-8")


def test_the_key_in_the_environment_counts_as_set(client, env, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    body = client.get("/api/settings")
    assert FAKE_KEY not in body.text
    assert body.json()["environment"]["key_set"] is True and body.json()["environment"]["key_source"] == "environment"


def test_the_key_routes_refuse_a_client_that_is_not_local(env):
    far = TestClient(ui.app, client=("10.1.2.3", 50000))
    assert far.post("/api/settings/key", json={"key": FAKE_KEY}).status_code == 403
    assert far.delete("/api/settings/key").status_code == 403
    assert "ANTHROPIC_API_KEY" not in env["env"].read_text(encoding="utf-8")
    for host in ("127.0.0.1", "::1"):
        near = TestClient(ui.app, client=(host, 50000))
        assert near.post("/api/settings/key", json={"key": FAKE_KEY}).status_code == 200, host
        assert near.delete("/api/settings/key").status_code == 200, host


# ----------------------------------------------------------------------------------- the panel
def test_the_main_console_has_the_settings_panel_and_its_requests(client):
    html = client.get("/").text
    src = re.search(r'<script src="(/static/v3/app\.js\?v=\d+)"></script>', html).group(1)
    js = client.get(src).text
    assert 'id="settings-btn"' in html and 'id="settings"' in html and 'role="dialog"' in html
    for label in ("Write In brief with the language model", "Save key", "Remove key", "Reset to defaults",
                  "Seconds per simulated hour", "Environment", "Detection", "Language model", "Monitor"):
        assert label in html, label
    key = re.search(r'<input[^>]*id="set-key"[^>]*>', html).group(0)
    for attr in ('type="password"', 'autocomplete="off"', 'spellcheck="false"'):
        assert attr in key, attr
    for request in ("get('/api/settings')", "send('/api/settings', 'PUT', c)", "send('/api/settings/reset', 'POST')",
                    "send('/api/settings/key', 'POST', { key: v })", "send('/api/settings/key', 'DELETE')"):
        assert request in js, request
    assert "setInterval(function () { loadSettings(); }, 2000)" in js      # polled every 2 s while open
    assert "settings') === '1'" in js            # ?settings=1 opens the panel
    assert "e.key === ','" in js                 # the key "," opens it
    for word in ('id="bench-btn"', 'id="theme-btn"', "Data browser", "Test bench"):     # the labels of v3 stay
        assert word in html, word


def test_every_control_in_the_panel_has_a_label(client):
    html = client.get("/").text
    panel = html[html.index('id="settings"'):html.index("<!-- /settings -->")]
    ids = set(re.findall(r'<label[^>]*for="([^"]+)"', panel))
    for tag in re.findall(r"<(?:input|select|textarea)\b[^>]*>", panel):
        i = re.search(r'id="([^"]+)"', tag)
        assert ("aria-label=" in tag) or (i and i.group(1) in ids), tag
