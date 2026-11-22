"""SQLite access for the simulator. WAL mode, so the brain can read while the simulator writes."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from sim.schema import TS_FORMAT, full_ddl
from sim.world import Injection, Segments, generate_hour, ingestion_rows

DEFAULT_DB = "data/market.db"


def connect(path: str | Path = DEFAULT_DB) -> sqlite3.Connection:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p, timeout=30)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("PRAGMA synchronous=NORMAL")
    return con


def init_db(con: sqlite3.Connection, seed: int, start: pd.Timestamp, countries) -> None:
    con.executescript(full_ddl())
    meta = {"seed": str(seed), "start": start.strftime(TS_FORMAT), "countries": ",".join(countries)}
    con.executemany("INSERT OR REPLACE INTO sim_meta VALUES (?, ?)", meta.items())
    con.commit()


def read_meta(con: sqlite3.Connection) -> dict:
    meta = dict(con.execute("SELECT key, value FROM sim_meta"))
    return {"seed": int(meta["seed"]), "start": pd.Timestamp(meta["start"]), "countries": tuple(meta["countries"].split(","))}


def load_injections(con: sqlite3.Connection) -> list[Injection]:
    return [Injection.from_row(kind, scope) for kind, scope in con.execute("SELECT kind, scope FROM ground_truth")]


def last_hour(con: sqlite3.Connection) -> pd.Timestamp | None:
    (ts,) = con.execute("SELECT MAX(ts) FROM facts_hourly").fetchone()
    return None if ts is None else pd.Timestamp(ts)


def write_hours(con: sqlite3.Connection, first_idx: int, count: int) -> None:
    """(Re)generate `count` hours from hour number `first_idx`, using every injection on record."""
    meta = read_meta(con)
    seg = Segments(meta["countries"])
    injections = load_injections(con)
    cols = None
    for idx in range(first_idx, first_idx + count):
        ts = meta["start"] + pd.Timedelta(hours=idx)
        frame = generate_hour(seg, ts, idx, meta["seed"], injections)
        cols = list(frame.columns)
        con.executemany(
            f"INSERT OR REPLACE INTO facts_hourly ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})",
            frame.astype(object).values.tolist(),
        )
        con.executemany("INSERT OR REPLACE INTO ingestion_log VALUES (?, ?, ?)", ingestion_rows(ts, idx, meta["seed"], injections))
    con.commit()


def hour_index(con: sqlite3.Connection, ts: pd.Timestamp) -> int:
    start = read_meta(con)["start"]
    return int((ts - start) / pd.Timedelta(hours=1))


def add_release_context(con: sqlite3.Connection, hours: int) -> None:
    """Routine deployments are context, not proof of a fault. They do not change facts."""
    import numpy as np
    from sim.world import PLATFORMS

    meta = read_meta(con)
    rng = np.random.default_rng(meta["seed"] + 701)
    rows = []
    for day in range((hours + 23) // 24):
        idx = day * 24 + int(rng.integers(0, min(24, hours - day * 24)))
        ts = (meta["start"] + pd.Timedelta(hours=idx)).strftime(TS_FORMAT)
        system = str(rng.choice(("payments-sdk", "search", "pacing")))
        country, platform = str(rng.choice(meta["countries"])), str(rng.choice(PLATFORMS))
        rows.append((ts, system, country, platform, f"{system} rollout ({country}, {platform})"))
    con.executemany("INSERT INTO releases VALUES (?, ?, ?, ?, ?)", rows)
    # Past promotions predate the data window: useful calendar context without contaminating the clean baseline.
    for days_ago in (7, 14):
        start = meta["start"] - pd.Timedelta(days=days_ago)
        end = start + pd.Timedelta(hours=12)
        country = str(rng.choice(meta["countries"]))
        con.execute("INSERT INTO calendar VALUES (?, ?, ?, ?, ?)",
                    (start.strftime(TS_FORMAT), end.strftime(TS_FORMAT), country, "promo", f"{country} promo"))
    con.commit()
