import json
import pandas as pd
import pytest
from fastapi.testclient import TestClient
from sim.run import backfill
import ui.app as ui

@pytest.fixture
def demo(tmp_path, monkeypatch):
    path=backfill(tmp_path/'market.db',48,'2026-10-25T00:00:00Z',countries=('DE','FR'))
    monkeypatch.setattr(ui,'MARKET_DB',path)
    monkeypatch.setattr(ui,'INCIDENTS_DIR',tmp_path/'incidents')
    return TestClient(ui.app),tmp_path

@pytest.mark.parametrize('kind,extra',[('payment_failure',{'country':'DE','platform':'Android'}),('stockout_surge',{'category':'home'}),('search_regression',{'country':'DE'})])
def test_ui_faults_end_after_requested_duration(demo,kind,extra):
    c,_=demo
    r=c.post('/api/inject',json=dict(kind=kind,hours=3,**extra))
    assert r.status_code==200
    scope=c.get('/api/state').json()['injected'][0]['scope']
    assert pd.Timestamp(scope['end'])-pd.Timestamp(scope['start'])==pd.Timedelta(hours=3)


def test_monitor_notes_are_visible_but_not_fabricated_alerts(demo):
    c,p=demo
    notes=p/'incidents'/'notes';notes.mkdir(parents=True)
    (notes/'pending.json').write_text(json.dumps({'id':'pending','status':'pending','last_seen':'2026-10-26T23:00:00Z','held':[{'hour':'2026-10-26T23:00:00Z','sources':['payments'],'metrics':['payment_success_rate']}],'report':'Waiting for late data'}))
    r=c.get('/api/incidents').json()
    assert not r['example'] and len(r['incidents'])==1
    assert r['incidents'][0]['status']=='pending' and r['incidents'][0]['metric']=='data_arrival'
