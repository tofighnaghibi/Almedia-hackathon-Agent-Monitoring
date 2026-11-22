CREATE TABLE IF NOT EXISTS facts_hourly (
  ts TEXT NOT NULL,
  country TEXT NOT NULL,
  platform TEXT NOT NULL,
  category TEXT NOT NULL,
  channel TEXT NOT NULL,
  sessions INTEGER NOT NULL,
  searches INTEGER NOT NULL,
  search_clicks INTEGER NOT NULL,
  feed_impressions INTEGER NOT NULL,
  feed_clicks INTEGER NOT NULL,
  product_views INTEGER NOT NULL,
  add_to_cart INTEGER NOT NULL,
  checkouts INTEGER NOT NULL,
  payment_attempts INTEGER NOT NULL,
  payment_successes INTEGER NOT NULL,
  orders INTEGER NOT NULL,
  gmv REAL NOT NULL,
  discounts REAL NOT NULL,
  marketplace_fees REAL NOT NULL,
  refunds INTEGER NOT NULL,
  ad_impressions INTEGER NOT NULL,
  ad_clicks INTEGER NOT NULL,
  ad_spend REAL NOT NULL,
  stockouts INTEGER NOT NULL,
  PRIMARY KEY (ts, country, platform, category, channel)
);

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

