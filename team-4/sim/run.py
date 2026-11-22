"""Create a database with a clean history.

    python -m sim.run --backfill 28d
    python -m sim.run --backfill 14d --countries DE,FR --db data/sample.db
"""
from __future__ import annotations

import argparse
import re
import time
from pathlib import Path

import pandas as pd

from sim import db
from sim.world import COUNTRIES

DEFAULT_START = "2026-10-25T00:00:00Z"


def parse_span(text: str) -> int:
    m = re.fullmatch(r"(\d+)\s*([dh])", text.strip().lower())
    if not m:
        raise argparse.ArgumentTypeError("use a span like 28d or 72h")
    return int(m.group(1)) * (24 if m.group(2) == "d" else 1)


def backfill(path: str, hours: int, start: str = DEFAULT_START, countries=COUNTRIES, seed: int = 42,
             overwrite: bool = True, add_context: bool = False) -> Path:
    p = Path(path)
    if p.exists():
        if not overwrite:
            raise FileExistsError(f"{p} exists; pass --force to replace it")
        for suffix in ("", "-wal", "-shm"):
            Path(str(p) + suffix).unlink(missing_ok=True)
    con = db.connect(p)
    try:
        db.init_db(con, seed, pd.Timestamp(start), countries)
        if add_context:
            db.add_release_context(con, hours)
        db.write_hours(con, 0, hours)
    finally:
        con.close()
    return p


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--backfill", type=parse_span, required=True, help="history length, e.g. 28d")
    clock = ap.add_mutually_exclusive_group()
    clock.add_argument("--start", default=None, help="first hour, UTC")
    clock.add_argument("--until-now", action="store_true", help="history ends at the real current UTC hour (demo)")
    ap.add_argument("--countries", default=",".join(COUNTRIES))
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--db", default=db.DEFAULT_DB)
    ap.add_argument("--force", action="store_true", help="replace an existing database")
    a = ap.parse_args(argv)
    t0 = time.time()
    start = a.start or DEFAULT_START
    if a.until_now:
        if a.backfill < 1:
            ap.error("--backfill must be at least one hour")
        start = (pd.Timestamp.now(tz="UTC").floor("h") - pd.Timedelta(hours=a.backfill - 1)).strftime("%Y-%m-%dT%H:00:00Z")
    p = backfill(a.db, a.backfill, start, tuple(a.countries.split(",")), a.seed, overwrite=a.force or not Path(a.db).exists(), add_context=True)
    con = db.connect(p)
    rows = con.execute("SELECT COUNT(*) FROM facts_hourly").fetchone()[0]
    print(f"{p}: {rows} rows, {a.backfill} hours, seed {a.seed}, {time.time() - t0:.1f} s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
