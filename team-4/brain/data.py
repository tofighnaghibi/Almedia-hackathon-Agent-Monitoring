"""Read-only access to the simulator's SQLite database (README §3.1 data contract).

Timestamps are UTC hour starts in the form 2026-11-01T13:00:00Z; windows are [start, end).
Guardrail 2 is enforced inside SQLite: an authorizer lets this connection read only the
contract tables, so no query text - core or contributed - can reach the scoring table.
A missing value is never summed as zero: a group with any NULL comes back as NaN.
A contributed query can also be run on a private in-memory copy of the contract tables in which
every fact row is split in two (`rerun_on_split_rows`); the market database is never written.
"""
from __future__ import annotations

import random
import sqlite3
import time
from pathlib import Path

import pandas as pd

from brain.metric_spec import DIMENSIONS

FACTS = "facts_hourly"
READABLE_TABLES = (FACTS, "ingestion_log", "releases", "calendar")
TS_FORMAT = "%Y-%m-%dT%H:00:00Z"
TS_GLOB = "[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:00:00Z"
_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION,
                    getattr(sqlite3, "SQLITE_RECURSIVE", 33)}
SPLIT_SHARE = (0.2, 0.8)  # a value of a fact row goes to the first of its two halves in a share from this range


class ContractError(ValueError):
    """The database does not follow the data contract."""


def iso_hour(ts) -> str:
    t = pd.Timestamp(ts)
    t = t.tz_localize("UTC") if t.tzinfo is None else t.tz_convert("UTC")
    return t.floor("h").strftime(TS_FORMAT)


def to_ts(values) -> pd.Series | pd.Timestamp:
    return pd.to_datetime(values, utc=True)


def _authorize(action, arg1, arg2, dbname, source):
    if action not in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_READ and (arg1 or "").lower() not in READABLE_TABLES:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _run(conn: sqlite3.Connection, sql: str, params, timeout: float | None, max_rows: int | None) -> pd.DataFrame:
    """Run one query on a connection, with the limits a contributed query gets."""
    deadline = None if timeout is None else time.monotonic() + timeout
    if deadline is not None:
        conn.set_progress_handler(lambda: time.monotonic() > deadline, 20_000)
    try:
        if max_rows is None:
            return pd.read_sql_query(sql, conn, params=params)
        cursor = conn.execute(sql, params)
        try:
            if cursor.description is None:
                raise ValueError("the query returns no columns")
            rows = cursor.fetchmany(max_rows + 1)
            if len(rows) > max_rows:
                raise OverflowError(f"the query returns more than {max_rows} rows")
            return pd.DataFrame.from_records(rows, columns=[d[0] for d in cursor.description],
                                             coerce_float=True)
        finally:
            cursor.close()
    except OverflowError:
        raise
    except Exception as e:  # pandas wraps sqlite3 errors in its own DatabaseError
        if deadline is not None and time.monotonic() > deadline:
            raise TimeoutError(f"the query took longer than {timeout:g} s and was stopped") from e
        if "not authorized" in str(e) or "is prohibited" in str(e):
            raise PermissionError(f"query reads outside the contract tables {READABLE_TABLES}") from e
        raise
    finally:
        if deadline is not None:
            conn.set_progress_handler(None, 0)


class Store:
    def __init__(self, path: str | Path):
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"database not found: {p} (run the simulator, or set MARKET_DB)")
        self.path = p
        # read-only URI (as_uri escapes '#', '?' and spaces); the simulator should use WAL mode
        self.conn = sqlite3.connect(p.resolve().as_uri() + "?mode=ro", uri=True, check_same_thread=False)
        cols = [r[1] for r in self.conn.execute(f"PRAGMA table_info({FACTS})")]
        if not cols:
            raise ContractError(f"{p}: table {FACTS} is missing")
        missing_dims = [d for d in ("ts",) + DIMENSIONS if d not in cols]
        if missing_dims:
            raise ContractError(f"{p}: {FACTS} lacks columns {missing_dims}")
        present = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        self._tables = [t for t in READABLE_TABLES if t in present]
        # the declared columns of the contract tables, read here because the authorizer below refuses PRAGMA
        self._declared = {t: [(r[1], r[2]) for r in self.conn.execute(f'PRAGMA table_info("{t}")')]
                          for t in self._tables}
        bad, example = self.conn.execute(
            f"SELECT COUNT(*), MIN(ts) FROM {FACTS} WHERE ts IS NULL OR ts NOT GLOB ?", (TS_GLOB,)).fetchone()
        if bad:
            raise ContractError(f"{p}: {bad} rows of {FACTS} have ts like {example!r}; "
                                f"the contract is UTC hour starts like 2026-11-01T13:00:00Z")
        stamps = [r[0] for r in self.conn.execute(f"SELECT DISTINCT ts FROM {FACTS}")]
        parsed = pd.to_datetime(pd.Series(stamps, dtype="object"), format=TS_FORMAT, errors="coerce", utc=True)
        if parsed.isna().any():
            raise ContractError(f"{p}: {FACTS} has impossible timestamps such as "
                                f"{stamps[int(parsed.isna().to_numpy().argmax())]!r}")
        self.columns = cols
        self.measures = [c for c in cols if c not in ("ts",) + DIMENSIONS]
        self.conn.set_authorizer(_authorize)

    # --------------------------------------------------------------- tables
    def tables(self) -> list[str]:
        """Readable contract tables present in the database."""
        return list(self._tables)

    def row_counts(self) -> dict[str, int]:
        return {t: self.conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in self._tables}

    def time_range(self) -> tuple[pd.Timestamp, pd.Timestamp]:
        lo, hi = self.conn.execute(f"SELECT MIN(ts), MAX(ts) FROM {FACTS}").fetchone()
        if lo is None:
            raise ContractError(f"{self.path}: {FACTS} is empty")
        return to_ts(lo), to_ts(hi)

    def distinct(self, table: str, column: str) -> list:
        """Distinct values of a column in a readable table; [] when the table or column is absent."""
        if table not in self._tables:
            return []
        try:
            return [r[0] for r in self.conn.execute(f'SELECT DISTINCT "{column}" FROM "{table}"')]
        except sqlite3.DatabaseError:
            return []

    def read_sql(self, sql: str, params=(), timeout: float | None = None,
                 max_rows: int | None = None) -> pd.DataFrame:
        """Run a read-only query; anything outside the contract tables is refused by SQLite.
        A contributed query is run with limits: SQLite is interrupted and TimeoutError raised when it
        takes longer than `timeout` seconds; OverflowError when it returns more than `max_rows` rows."""
        return _run(self.conn, sql, params, timeout, max_rows)

    def rerun_on_split_rows(self, sql: str, hours: int = 24, seed: int = 0, timeout: float | None = None,
                            max_rows: int | None = None) -> tuple[pd.DataFrame, pd.DataFrame, int]:
        """Run a contributed query twice on private in-memory copies of the contract tables: on the last
        `hours` hours of facts as they are, and with every fact row there replaced by two rows that add
        up to it. Each measure is split in a share of its own; a count stays a whole number and a missing
        value stays missing in both halves. A query that is a function of sums returns the same values
        both times; an average, a maximum or a count of rows does not. Returns the two results and the
        number of fact rows that were split. The copies hold the contract tables and nothing else, the
        same authorizer and limits apply, and the split is the same every time (`seed`)."""
        start = iso_hour(self.time_range()[1] - pd.Timedelta(hours=hours - 1))
        names = [name for name, _ in self._declared[FACTS]]
        rows = self.conn.execute(f"SELECT * FROM {FACTS} WHERE ts >= ?", (start,)).fetchall()
        measure_at = [i for i, name in enumerate(names) if name in self.measures]
        rng = random.Random(seed)
        halves = []
        for row in rows:
            a, b = list(row), list(row)
            for i in measure_at:
                share = rng.uniform(*SPLIT_SHARE)   # drawn for every value, so a missing one does not shift the rest
                if isinstance(row[i], int):
                    a[i] = int(row[i] * share)
                elif isinstance(row[i], float):
                    a[i] = row[i] * share
                else:
                    continue                        # missing: missing in both halves
                b[i] = row[i] - a[i]
            halves += [a, b]
        results = []
        for facts in (rows, halves):
            copy = sqlite3.connect(":memory:")
            try:
                for table in self._tables:
                    columns = self._declared[table]
                    copy.execute(f'CREATE TABLE "{table}" (' + ", ".join(f'"{n}" {kind}' for n, kind in columns) + ")")
                    content = facts if table == FACTS else self.conn.execute(f'SELECT * FROM "{table}"').fetchall()
                    copy.executemany(f'INSERT INTO "{table}" VALUES ({", ".join("?" * len(columns))})', content)
                copy.set_authorizer(_authorize)
                results.append(_run(copy, sql, (), timeout, max_rows))
            finally:
                copy.close()
        return results[0], results[1], len(rows)

    # ---------------------------------------------------------------- sums
    def sums(self, columns, start=None, end=None, segment: dict | None = None,
             group_by=(), by_hour: bool = False) -> pd.DataFrame:
        """SUM of each measure column, grouped by the given dimensions (and hour).
        A group containing any NULL for a column returns NaN for that column, never a partial sum."""
        columns = list(dict.fromkeys(columns))
        bad = [c for c in columns if c not in self.measures]
        if bad:
            raise KeyError(f"unknown measure columns {bad}")
        group_by = list(group_by)
        bad = [d for d in group_by if d not in DIMENSIONS]
        if bad:
            raise KeyError(f"unknown dimensions {bad}")
        keys = (["ts"] if by_hour else []) + group_by

        where, params = [], []
        if start is not None:
            where.append("ts >= ?")
            params.append(iso_hour(start))
        if end is not None:
            where.append("ts < ?")
            params.append(iso_hour(end))
        for dim, val in (segment or {}).items():
            if dim not in DIMENSIONS:
                raise KeyError(f"unknown dimension {dim}")
            vals = list(val) if isinstance(val, (list, tuple, set)) else [val]
            where.append(f'"{dim}" IN ({",".join("?" * len(vals))})')
            params.extend(str(v) for v in vals)

        select = [f'"{k}"' for k in keys] + [
            f'CASE WHEN COUNT("{c}") < COUNT(*) THEN NULL ELSE SUM("{c}") END AS "{c}"' for c in columns]
        sql = f"SELECT {', '.join(select)} FROM {FACTS}"
        if where:
            sql += " WHERE " + " AND ".join(where)
        if keys:
            sql += " GROUP BY " + ", ".join(f'"{k}"' for k in keys) + " ORDER BY " + ", ".join(f'"{k}"' for k in keys)
        df = pd.read_sql_query(sql, self.conn, params=params)
        if not keys and not self._any_rows(where, params):
            df = df.iloc[0:0]  # no rows in the window: an empty result, not a row of NULLs
        for c in columns:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
        if by_hour:
            df["ts"] = to_ts(df["ts"])
        return df

    def _any_rows(self, where: list[str], params: list) -> bool:
        sql = f"SELECT 1 FROM {FACTS}" + (" WHERE " + " AND ".join(where) if where else "") + " LIMIT 1"
        return self.conn.execute(sql, params).fetchone() is not None
