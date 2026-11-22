"""The simulated clock and the completeness gate.

The data is the clock: `now` is one hour after the newest hour in facts_hourly. A source's data
for an hour has arrived when its ingestion_log row says so, with a margin of ten minutes, because
on-time data is logged a few minutes after the hour ends. A missing log row means not arrived.

`as_of` replays history: everything from that hour on is invisible, so the monitor can walk a
finished database hour by hour without looking ahead.
"""
from __future__ import annotations

import pandas as pd

from brain.data import FACTS, Store, iso_hour

HOUR = pd.Timedelta(hours=1)
ARRIVAL_MARGIN = pd.Timedelta(minutes=10)


def fmt_hour(ts) -> str:
    return iso_hour(ts)


class Clock:
    def __init__(self, store: Store, as_of=None):
        self.store = store
        self.as_of = None if as_of is None else pd.Timestamp(iso_hour(as_of))
        self._newest: pd.Timestamp | None = None
        self._read = False

    def newest_hour(self) -> pd.Timestamp | None:
        """Start of the newest visible hour of data; None when there is no data yet."""
        if not self._read:
            sql, params = f"SELECT MAX(ts) AS ts FROM {FACTS}", ()
            if self.as_of is not None:
                sql, params = sql + " WHERE ts < ?", (iso_hour(self.as_of),)
            value = self.store.read_sql(sql, params)["ts"].iloc[0]
            self._newest = None if value is None or pd.isna(value) else pd.Timestamp(value)
            self._read = True
        return self._newest

    @property
    def now(self) -> pd.Timestamp | None:
        if self.as_of is not None:
            return self.as_of
        newest = self.newest_hour()
        return None if newest is None else newest + HOUR

    def late(self, hours, sources) -> dict[pd.Timestamp, set[str]]:
        """For each of the given hours, the sources whose data has not arrived yet.
        Hours with nothing late are left out."""
        hours = sorted(pd.Timestamp(h) for h in hours)
        if not hours or self.now is None:
            return {}
        log = self.store.read_sql(
            "SELECT source, ts, arrived_at FROM ingestion_log WHERE ts >= ? AND ts <= ?",
            (iso_hour(hours[0]), iso_hour(hours[-1])))
        deadline = self.now + ARRIVAL_MARGIN
        arrived = pd.to_datetime(log["arrived_at"], utc=True, errors="coerce")
        here = set(zip(log["source"][arrived <= deadline], log["ts"][arrived <= deadline]))
        out: dict[pd.Timestamp, set[str]] = {}
        for h in hours:
            missing = {s for s in sources if (s, iso_hour(h)) not in here}
            if missing:
                out[h] = missing
        return out
