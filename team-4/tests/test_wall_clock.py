import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sim import db
from sim.run import backfill
from sim.clock import sync_current_hour
import ui.app as ui


def test_sync_advances_only_to_current_hour_and_is_idempotent(tmp_path):
    p = backfill(tmp_path / 'market.db', 2, '2026-11-21T11:00:00Z', countries=('DE',))
    c = db.connect(p)
    try:
        assert sync_current_hour(c, pd.Timestamp('2026-11-21T14:37:00Z')) == pd.Timestamp('2026-11-21T14:00:00Z')
        rows = c.execute('SELECT COUNT(*) FROM facts_hourly').fetchone()[0]
        sync_current_hour(c, pd.Timestamp('2026-11-21T14:59:00Z'))
        assert c.execute('SELECT COUNT(*) FROM facts_hourly').fetchone()[0] == rows
    finally:
        c.close()


def test_future_database_is_refused_without_changes(tmp_path):
    p = backfill(tmp_path / 'future.db', 2, '2026-11-21T22:00:00Z', countries=('DE',))
    c = db.connect(p)
    try:
        with pytest.raises(ValueError, match='future'):
            sync_current_hour(c, pd.Timestamp('2026-11-21T14:37:00Z'))
        assert db.last_hour(c) == pd.Timestamp('2026-11-21T23:00:00Z')
    finally:
        c.close()


def test_wall_injection_affects_current_hour_without_waiting(tmp_path, monkeypatch):
    p = backfill(tmp_path / 'market.db', 2, '2026-11-21T11:00:00Z', countries=('DE',))
    monkeypatch.setattr(ui, 'MARKET_DB', p)
    monkeypatch.setattr(ui, 'CLOCK_MODE', 'wall')
    monkeypatch.setattr(ui, 'wall_now', lambda: pd.Timestamp('2026-11-21T14:37:00Z'))
    client = TestClient(ui.app)
    state = client.get('/api/state').json()
    assert state['last_hour'] == '2026-11-21T14:00:00Z'
    assert state['clock_mode'] == 'wall' and state['display_timezone'] == 'Europe/Berlin'
    c = db.connect(p)
    before = c.execute("SELECT SUM(payment_successes) FROM facts_hourly WHERE ts='2026-11-21T14:00:00Z' AND platform='Android'").fetchone()[0]
    c.close()
    r = client.post('/api/inject', json={'kind':'payment_failure','country':'DE','platform':'Android','severity':0.5})
    assert r.status_code == 200 and r.json()['start'] == '2026-11-21T14:00:00Z'
    c = db.connect(p)
    after = c.execute("SELECT SUM(payment_successes) FROM facts_hourly WHERE ts='2026-11-21T14:00:00Z' AND platform='Android'").fetchone()[0]
    c.close()
    assert after < before
    assert client.post('/api/inject', json={'kind':'payment_failure','country':'DE','platform':'Android'}).status_code == 409


def test_until_now_cli_ends_at_current_hour(tmp_path):
    from sim.run import main
    p = tmp_path / 'now.db'
    before = pd.Timestamp.now(tz='UTC').floor('h')
    assert main(['--backfill', '2h', '--until-now', '--countries', 'DE', '--db', str(p)]) == 0
    after = pd.Timestamp.now(tz='UTC').floor('h')
    c = db.connect(p)
    try:
        assert before <= db.last_hour(c) <= after
        assert c.execute('SELECT COUNT(DISTINCT ts) FROM facts_hourly').fetchone()[0] == 2
    finally:
        c.close()
