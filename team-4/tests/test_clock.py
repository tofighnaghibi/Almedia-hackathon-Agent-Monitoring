"""The simulated clock and the completeness gate (README 3.4, issue #9)."""
import sqlite3

import pandas as pd

from brain.clock import ARRIVAL_MARGIN, Clock
from brain.data import Store
from tests.fixture_db import SOURCES, make_fixture_db

T = pd.Timestamp


def _db(tmp_path, arrivals=None, days=2):
    """A fixture database whose ingestion log follows the simulator: arrived = ts + 1 h + 5 min,
    except the (source, ts) pairs given in `arrivals`."""
    path = make_fixture_db(tmp_path / "m.db", days=days)
    con = sqlite3.connect(path)
    for (source, ts), arrived in (arrivals or {}).items():
        if arrived is None:
            con.execute("DELETE FROM ingestion_log WHERE source = ? AND ts = ?", (source, ts))
        else:
            con.execute("UPDATE ingestion_log SET arrived_at = ? WHERE source = ? AND ts = ?", (arrived, source, ts))
    con.commit()
    con.close()
    return Store(path)


def test_now_is_one_hour_after_the_newest_data(tmp_path):
    clock = Clock(_db(tmp_path))
    assert clock.newest_hour() == T("2026-11-02T23:00:00Z")
    assert clock.now == T("2026-11-03T00:00:00Z")


def test_as_of_hides_everything_from_that_hour_on(tmp_path):
    clock = Clock(_db(tmp_path), as_of="2026-11-02T10:00:00Z")
    assert clock.now == T("2026-11-02T10:00:00Z")
    assert clock.newest_hour() == T("2026-11-02T09:00:00Z")


def test_on_time_data_of_the_newest_hour_counts_as_arrived(tmp_path):
    # the simulator logs the newest hour a few minutes after it ends, so later than `now`
    clock = Clock(_db(tmp_path))
    newest = clock.newest_hour()
    assert clock.late([newest], SOURCES) == {}
    assert ARRIVAL_MARGIN == pd.Timedelta(minutes=10)


def test_a_delayed_source_is_late_until_the_clock_passes_its_arrival(tmp_path):
    store = _db(tmp_path, {("payments", "2026-11-02T08:00:00Z"): "2026-11-02T11:05:00Z"})
    hour = T("2026-11-02T08:00:00Z")
    assert Clock(store, as_of="2026-11-02T09:00:00Z").late([hour], SOURCES) == {hour: {"payments"}}
    assert Clock(store, as_of="2026-11-02T10:00:00Z").late([hour], SOURCES) == {hour: {"payments"}}
    assert Clock(store, as_of="2026-11-02T11:00:00Z").late([hour], SOURCES) == {}


def test_a_missing_log_row_means_not_arrived(tmp_path):
    store = _db(tmp_path, {("ads", "2026-11-02T08:00:00Z"): None})
    hour = T("2026-11-02T08:00:00Z")
    assert Clock(store, as_of="2026-11-02T20:00:00Z").late([hour], SOURCES) == {hour: {"ads"}}


def test_an_empty_database_has_no_clock(tmp_path):
    store = _db(tmp_path)
    assert Clock(store, as_of="2026-10-01T00:00:00Z").newest_hour() is None
