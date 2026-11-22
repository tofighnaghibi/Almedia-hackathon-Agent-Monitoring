"""Database schema of the simulator (README section 3.1 data contract).

Column names and units follow brain's fixture (tests/fixture_db.py), so the brain can read the
simulator's database unchanged. Money is in EUR, timestamps are UTC hour starts like
2026-11-01T13:00:00Z. Run `python -m sim.schema` to print the DDL (docs/contracts/schema.sql).
"""
from __future__ import annotations

TS_FORMAT = "%Y-%m-%dT%H:00:00Z"
TS_SECONDS_FORMAT = "%Y-%m-%dT%H:%M:%SZ"

DIMENSIONS = ("country", "platform", "category", "channel")

# order matters: it is the column order of facts_hourly
COUNTS = (
    "sessions", "searches", "search_clicks", "feed_impressions", "feed_clicks",
    "product_views", "add_to_cart", "checkouts", "payment_attempts", "payment_successes",
    "orders",
)
MONEY = ("gmv", "discounts", "marketplace_fees")
OTHER_COUNTS = ("refunds", "ad_impressions", "ad_clicks")
AD_SPEND = ("ad_spend",)
STOCK = ("stockouts",)
MEASURES = COUNTS + MONEY + OTHER_COUNTS + AD_SPEND + STOCK
REAL_COLUMNS = set(MONEY + AD_SPEND)

SOURCES = ("traffic", "search", "recommendations", "orders", "payments", "ads", "inventory")


def facts_ddl() -> str:
    cols = ["ts TEXT NOT NULL"] + [f"{d} TEXT NOT NULL" for d in DIMENSIONS]
    cols += [f"{m} {'REAL' if m in REAL_COLUMNS else 'INTEGER'} NOT NULL" for m in MEASURES]
    cols.append(f"PRIMARY KEY (ts, {', '.join(DIMENSIONS)})")
    return "CREATE TABLE IF NOT EXISTS facts_hourly (\n  " + ",\n  ".join(cols) + "\n);"


OTHER_DDL = """
-- how fresh each source is, per hour (completeness gate for the monitor).
-- The clock rule (agreed with B, issue 14): clock = MAX(facts_hourly.ts) + 1 hour; a source's hour has arrived when
-- arrived_at <= clock + 10 minutes. On time, arrived_at is ts + 1 h + 2..9 minutes, so the jitter must stay under 10
-- minutes (tests/test_sim.py checks it); a data delay adds delay_hours.
CREATE TABLE IF NOT EXISTS ingestion_log (source TEXT NOT NULL, ts TEXT NOT NULL, arrived_at TEXT NOT NULL,
  PRIMARY KEY (source, ts));
-- deployments the brain may use as context
CREATE TABLE IF NOT EXISTS releases (ts TEXT NOT NULL, system TEXT NOT NULL, country TEXT, platform TEXT,
  description TEXT);
-- planned events (promos, holidays) the brain may use as context
CREATE TABLE IF NOT EXISTS calendar (start_ts TEXT NOT NULL, end_ts TEXT NOT NULL, country TEXT, kind TEXT NOT NULL,
  description TEXT);
-- the answer. Hidden from brain/ (it never reads this table); used by the simulator and the scoring harness only.
-- scope is JSON with the full injection parameters, so the simulator can replay it.
CREATE TABLE IF NOT EXISTS ground_truth (injected_at TEXT NOT NULL, kind TEXT NOT NULL, scope TEXT NOT NULL,
  description TEXT);
-- simulator bookkeeping (seed, start, countries); not part of the contract
CREATE TABLE IF NOT EXISTS sim_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
"""


def full_ddl() -> str:
    return facts_ddl() + "\n" + OTHER_DDL


if __name__ == "__main__":
    print(full_ddl())
