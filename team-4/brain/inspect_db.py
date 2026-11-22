"""Print what is in the simulator's database: tables, row counts and the time range.
Usage: python -m brain.inspect_db [path]   (default: $MARKET_DB or data/market.db)"""
from __future__ import annotations

import os
import sys

from brain.data import Store


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    path = argv[0] if argv else os.environ.get("MARKET_DB", "data/market.db")
    store = Store(path)
    lo, hi = store.time_range()
    print(f"{path}: facts_hourly from {lo} to {hi}")
    for table, n in store.row_counts().items():
        print(f"  {table:<16} {n:>9} rows")
    print(f"  measures: {', '.join(store.measures)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
