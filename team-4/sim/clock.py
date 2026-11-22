"""Wall-clock demo mode. Database timestamps stay UTC hour starts."""
import pandas as pd
from sim import db


def wall_now():
    return pd.Timestamp.now(tz='UTC')


def sync_current_hour(con, now=None):
    target = (wall_now() if now is None else pd.Timestamp(now)).tz_convert('UTC').floor('h')
    con.execute('BEGIN IMMEDIATE')
    try:
        last = db.last_hour(con)
        if last is None:
            raise ValueError('empty database; initialize with sim.run --until-now')
        if last > target:
            raise ValueError('database is in the future; use a separate database initialized with sim.run --until-now')
        hours = int((target - last) / pd.Timedelta(hours=1))
        if hours > 48:
            raise ValueError('database is over 48 hours behind; initialize a fresh demo database with sim.run --until-now')
        if hours:
            db.write_hours(con, db.hour_index(con, last) + 1, hours)
        con.commit()
        return target
    except Exception:
        con.rollback()
        raise
