"""Exercise the real simulator -> monitor -> incident lifecycle without an LLM.
Uses a NEW run directory and synthetic hourly data. Never touches the interactive wall-clock database.
"""
from pathlib import Path
import argparse
import json
import time
import pandas as pd
from sim import db, inject
from sim.run import backfill
from brain.graph import MetricGraph
from brain.data import Store
from brain.monitor import Monitor, Config
from brain.incidents import Book


def main(argv=None):
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--run',type=Path,required=True)
    args=ap.parse_args(argv)
    if args.run.exists():ap.error('run directory already exists; choose a new one')
    args.run.mkdir(parents=True)
    started=time.monotonic();path=args.run/'market.db'
    backfill(path,28*24,start='2026-10-24T16:00:00Z')
    con=db.connect(path);store=Store(path)
    monitor=Monitor(MetricGraph.load(),store,Config());book=Book(monitor,args.run/'incidents')
    stages=[]
    def tick(label):
        written=book.update(monitor.tick())
        all_records=[json.loads(p.read_text()) for p in (args.run/'incidents').glob('*.json')]
        records=[i for i in all_records if i.get('status') in ('open', 'ongoing', 'resolved')]
        stages.append(dict(stage=label,last_hour=str(db.last_hour(con)),non_alert_statuses=[i['status'] for i in all_records if i.get('status') in ('explained', 'pending')],incidents=[dict(id=i['id'],status=i['status'],segment=i['segment']) for i in records],seconds=round(time.monotonic()-started,2)))
        return records
    try:
        assert not tick('clean'), 'clean baseline raised an alert'
        at=db.last_hour(con);inject.payment_failure(con,'DE','Android',at,.4,end=at+pd.Timedelta(hours=12))
        for _ in range(4):
            records=tick('payment injected')
            if records:break
            db.write_hours(con,db.hour_index(con,db.last_hour(con))+1,1)
        assert records, 'monitor did not detect payment fault within four data hours'
        assert any(i['segment'].get('country')=='DE' for i in records),'wrong fault location'
        rid=con.execute("SELECT rowid FROM ground_truth WHERE kind='payment_failure'").fetchone()[0]
        inject.end_injection(con,rid,db.last_hour(con))
        for _ in range(8):
            db.write_hours(con,db.hour_index(con,db.last_hour(con))+1,1)
            records=tick('rollback and normal hours')
            if records and all(i['status']=='resolved' for i in records):break
        assert records and all(i['status']=='resolved' for i in records),'incident did not resolve'
        count=len(records);at=db.last_hour(con)
        inject.promo(con,'FR',at,at+pd.Timedelta(hours=12))
        tick('planned promo')
        for _ in range(3):
            db.write_hours(con,db.hour_index(con,db.last_hour(con))+1,1);records=tick('promo hours')
        assert len(records)==count,'promo created a false alert'
        at=db.last_hour(con);inject.data_delay(con,'payments',at,at+pd.Timedelta(hours=3),2)
        tick('payments delayed')
        pending = [json.loads(f.read_text()) for f in (args.run/'incidents').glob('pending*.json')]
        pending += [json.loads(f.read_text()) for f in (args.run/'incidents/notes').glob('pending*.json')]
        assert any(i.get('status') == 'pending' for i in pending),'data delay not reported pending'
        result=dict(passed=True,duration_seconds=round(time.monotonic()-started,2),stages=stages,
                    limits=['Real hourly data was accelerated; this is a technical rehearsal, not a presenter timed rehearsal.','New-metric watcher remains a separate B dependency.'])
        (args.run/'rehearsal.json').write_text(json.dumps(result,indent=2)+'\n')
        print(json.dumps(result,indent=2))
    finally:
        con.close();store.conn.close()
    return 0

if __name__=='__main__':raise SystemExit(main())
