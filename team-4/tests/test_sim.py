"""Tests for the simulator: contract, internal consistency, seasonality, and the injected scenarios."""
import re
import sqlite3
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from sim import db, inject
from sim.run import backfill
from sim.schema import DIMENSIONS, MEASURES
from tests.fixture_db import make_frame

START = "2026-10-25T00:00:00Z"
FAULT_START = pd.Timestamp("2026-10-30T08:00:00Z")


def facts(path) -> pd.DataFrame:
    con = sqlite3.connect(path)
    try:
        return pd.read_sql_query("SELECT * FROM facts_hourly", con)
    finally:
        con.close()


@pytest.fixture(scope="module")
def clean_db(tmp_path_factory):
    return backfill(str(tmp_path_factory.mktemp("sim") / "clean.db"), 7 * 24, START, seed=7)


def fresh_copy(tmp_path, hours=7 * 24, seed=7):
    return backfill(str(tmp_path / "x.db"), hours, START, seed=seed)


def key(df):
    return df.sort_values(["ts", *DIMENSIONS]).reset_index(drop=True)


# ----------------------------------------------------------------- contract
def test_columns_match_the_brain_fixture(clean_db):
    assert list(facts(clean_db).columns) == list(make_frame(days=1).columns)


def test_timestamps_are_utc_hour_starts(clean_db):
    ts = facts(clean_db)["ts"].unique()
    assert all(re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:00:00Z", t) for t in ts)
    assert len(ts) == 7 * 24


def test_brain_can_read_it(clean_db):
    from brain.data import Store
    counts = Store(clean_db).row_counts()
    assert counts["facts_hourly"] > 0 and counts["ingestion_log"] == 7 * 24 * 7
    assert "ground_truth" not in counts  # the brain never reads the answer


# ------------------------------------------------------------- consistency
def test_funnel_is_consistent_and_non_negative(clean_db):
    f = facts(clean_db)
    assert (f[list(MEASURES)] >= 0).all().all()
    assert (f.orders <= f.payment_successes).all()
    assert (f.payment_successes <= f.payment_attempts).all()
    assert (f.checkouts <= f.payment_attempts).all()
    assert (f.add_to_cart <= f.product_views).all()
    assert (f.checkouts <= f.add_to_cart).all()
    assert (f.refunds <= f.orders).all()
    assert (f.stockouts <= f.product_views).all()
    assert 0.01 < f.stockouts.sum() / f.product_views.sum() < 0.02      # about 1.5% of views meet an unavailable listing
    assert (f.product_views >= f.search_clicks + f.feed_clicks).all()
    assert not f.isna().any().any()


def test_money_follows_orders(clean_db):
    f = facts(clean_db)
    assert (f.loc[f.orders == 0, "gmv"] == 0).all()
    take = f.marketplace_fees.sum() / f.gmv.sum()
    assert 0.08 < take < 0.16
    assert (f.discounts <= f.gmv).all()


def test_same_seed_gives_identical_data(tmp_path):
    a = facts(backfill(str(tmp_path / "a.db"), 48, START, seed=3))
    b = facts(backfill(str(tmp_path / "b.db"), 48, START, seed=3))
    c = facts(backfill(str(tmp_path / "c.db"), 48, START, seed=4))
    pd.testing.assert_frame_equal(key(a), key(b))
    assert not key(a).equals(key(c))


def test_full_backfill_is_fast(tmp_path):
    t0 = time.time()
    backfill(str(tmp_path / "big.db"), 28 * 24, START)
    assert time.time() - t0 < 30


# ------------------------------------------------------------- seasonality
def test_evening_peak_and_night_dip(clean_db):
    f = facts(clean_db)
    f["hour"] = f.ts.str[11:13].astype(int)
    by_hour = f.groupby("hour").sessions.sum()
    assert by_hour.loc[19] > 3 * by_hour.loc[3]      # 20:00 local peak vs 04:00 local
    assert by_hour.loc[11] > by_hour.loc[5]          # lunch bump vs early morning


def test_weekend_is_busier_than_midweek(clean_db):
    f = facts(clean_db)
    day = pd.to_datetime(f.ts).dt.day_of_week
    per_day = f.groupby(day).sessions.sum()
    assert per_day.loc[6] > per_day.loc[1]            # Sunday vs Tuesday


# ------------------------------------------------------------------ S1
def test_payment_failure_hits_only_its_segment(clean_db, tmp_path):
    clean = key(facts(clean_db))
    path = fresh_copy(tmp_path)
    con = db.connect(path)
    inject.payment_failure(con, "DE", "Android", FAULT_START, severity=0.35)
    con.close()
    faulty = key(facts(path))

    target = (faulty.country == "DE") & (faulty.platform == "Android")
    after = pd.to_datetime(faulty.ts) >= FAULT_START
    # every other row is exactly as before, and so is the target before the fault
    pd.testing.assert_frame_equal(faulty[~(target & after)], clean[~(target & after)])

    def psr(df):
        sub = df[target & after]
        return sub.payment_successes.sum() / sub.payment_attempts.sum()
    assert psr(faulty) < 0.75 * psr(clean)
    assert faulty[target & after].orders.sum() < 0.8 * clean[target & after].orders.sum()


def test_payment_failure_leaves_a_release_and_the_answer(tmp_path):
    path = fresh_copy(tmp_path, hours=3 * 24)
    con = db.connect(path)
    inject.payment_failure(con, "FR", "iOS", pd.Timestamp("2026-10-26T10:00:00Z"))
    release = con.execute("SELECT ts, system, country, platform FROM releases").fetchall()
    truth = con.execute("SELECT injected_at, kind, scope FROM ground_truth").fetchall()
    con.close()
    assert release == [("2026-10-26T10:00:00Z", "payments-sdk", "FR", "iOS")]
    assert truth[0][1] == "payment_failure" and '"country": "FR"' in truth[0][2]


def test_replay_equals_a_run_that_knew_the_fault_from_the_start(tmp_path):
    a = fresh_copy(tmp_path / "a" if (tmp_path / "a").mkdir() is None else tmp_path, hours=4 * 24)
    con = db.connect(a)
    inject.payment_failure(con, "DE", "Web", pd.Timestamp("2026-10-27T06:00:00Z"), severity=0.5)
    con.close()
    # second database: record the fault first, then generate all hours once
    (tmp_path / "b").mkdir()
    b = str(tmp_path / "b" / "x.db")
    con = db.connect(b)
    db.init_db(con, 7, pd.Timestamp(START), ("DE", "FR", "ES", "PL"))
    from sim.world import Injection
    inj = Injection("payment_failure", pd.Timestamp("2026-10-27T06:00:00Z"), None,
                    {"country": "DE", "platform": "Web", "severity": 0.5})
    con.execute("INSERT INTO ground_truth VALUES (?, ?, ?, ?)", ("2026-10-27T06:00:00Z", inj.kind, inj.to_scope_json(), ""))
    con.commit()
    db.write_hours(con, 0, 4 * 24)
    con.close()
    pd.testing.assert_frame_equal(key(facts(a)), key(facts(b)))


# ------------------------------------------------------------------ S2
def test_promo_lifts_orders_in_its_country_and_is_in_the_calendar(clean_db, tmp_path):
    clean = key(facts(clean_db))
    path = fresh_copy(tmp_path)
    s, e = pd.Timestamp("2026-10-28T08:00:00Z"), pd.Timestamp("2026-10-28T20:00:00Z")
    con = db.connect(path)
    inject.promo(con, "FR", s, e)
    cal = con.execute("SELECT start_ts, end_ts, country, kind FROM calendar").fetchall()
    con.close()
    promo = key(facts(path))
    ts = pd.to_datetime(promo.ts)
    window = (ts >= s) & (ts < e)
    fr = promo.country == "FR"
    assert promo[fr & window].orders.sum() > 1.3 * clean[fr & window].orders.sum()
    pd.testing.assert_frame_equal(promo[~(fr & window)], clean[~(fr & window)])
    assert cal == [("2026-10-28T08:00:00Z", "2026-10-28T20:00:00Z", "FR", "promo")]


# ------------------------------------------------------------------ S3
def test_data_delay_changes_only_the_ingestion_log(clean_db, tmp_path):
    path = fresh_copy(tmp_path)
    s, e = pd.Timestamp("2026-10-29T06:00:00Z"), pd.Timestamp("2026-10-29T09:00:00Z")
    con = db.connect(path)
    inject.data_delay(con, "payments", s, e, delay_hours=2)
    rows = con.execute("SELECT ts, arrived_at FROM ingestion_log WHERE source='payments' AND ts >= ? AND ts < ?",
                       (s.strftime("%Y-%m-%dT%H:00:00Z"), e.strftime("%Y-%m-%dT%H:00:00Z"))).fetchall()
    other = con.execute("SELECT ts, arrived_at FROM ingestion_log WHERE source='orders' AND ts = ?",
                        (s.strftime("%Y-%m-%dT%H:00:00Z"),)).fetchone()
    con.close()
    pd.testing.assert_frame_equal(key(facts(path)), key(facts(clean_db)))
    for ts, arrived in rows:
        lag = pd.Timestamp(arrived) - pd.Timestamp(ts)
        assert pd.Timedelta(hours=3) <= lag <= pd.Timedelta(hours=3, minutes=10)
    assert pd.Timestamp(other[1]) - pd.Timestamp(other[0]) < pd.Timedelta(hours=1, minutes=10)


# -------------------------------------------------------------------- live
def test_live_mode_appends_hours_and_picks_up_a_fault_scheduled_for_the_next_hour(tmp_path):
    from sim.live import run_live
    (tmp_path / "a").mkdir()
    (tmp_path / "b").mkdir()
    base = backfill(str(tmp_path / "a" / "x.db"), 48, START, seed=5)
    with_fault = backfill(str(tmp_path / "b" / "x.db"), 48, START, seed=5)

    con = db.connect(with_fault)
    next_hour = db.last_hour(con) + pd.Timedelta(hours=1)
    inject.payment_failure(con, "ES", "Android", next_hour, severity=0.5)   # nothing to replay yet
    con.close()
    assert run_live(base, speed=0, hours=6) == 6
    assert run_live(with_fault, speed=0, hours=6) == 6

    a, b = key(facts(base)), key(facts(with_fault))
    assert len(a) == 54 * 180 == len(b)
    new = pd.to_datetime(a.ts) >= next_hour
    target = (a.country == "ES") & (a.platform == "Android")
    pd.testing.assert_frame_equal(a[~(new & target)], b[~(new & target)])       # nothing else moved
    ok = lambda df: df[new & target].payment_successes.sum() / df[new & target].payment_attempts.sum()
    assert ok(b) < 0.7 * ok(a)


# --------------------------------------------------------------- no stacking
def test_the_same_injection_cannot_be_recorded_twice(tmp_path):
    path = fresh_copy(tmp_path, hours=2 * 24)
    con = db.connect(path)
    start = pd.Timestamp("2026-10-26T10:00:00Z")
    inject.payment_failure(con, "DE", "Web", start, severity=0.4)
    before = con.execute("SELECT COUNT(*) FROM ground_truth").fetchone()[0], con.execute("SELECT COUNT(*) FROM releases").fetchone()[0]
    with pytest.raises(inject.AlreadyInjected):
        inject.payment_failure(con, "DE", "Web", start, severity=0.4)
    after = con.execute("SELECT COUNT(*) FROM ground_truth").fetchone()[0], con.execute("SELECT COUNT(*) FROM releases").fetchone()[0]
    with pytest.raises(inject.AlreadyInjected):                            # other settings, same segment, still in effect
        inject.payment_failure(con, "DE", "Web", start + pd.Timedelta(hours=3), severity=0.5)
    inject.payment_failure(con, "FR", "Web", start, severity=0.4)         # different segment: allowed
    con.close()
    assert before == after == (1, 1)


# ------------------------------------------------- S5 stockouts, search, S6 ad pacing
def _compare(clean_path, faulty_path, mask_fn, window):
    c, f = key(facts(clean_path)), key(facts(faulty_path))
    ts = pd.to_datetime(c.ts)
    inside = (ts >= window[0]) & (ts < window[1]) & mask_fn(c)
    pd.testing.assert_frame_equal(c[~inside], f[~inside])           # nothing outside the fault moved
    return c[inside], f[inside]


def test_stockout_surge_hits_one_category_and_lowers_add_to_cart_rate(clean_db, tmp_path):
    path = fresh_copy(tmp_path)
    s, e = pd.Timestamp("2026-10-28T08:00:00Z"), pd.Timestamp("2026-10-29T08:00:00Z")
    con = db.connect(path)
    inject.stockout_surge(con, "electronics", s, e, multiplier=12)
    con.close()
    c, f = _compare(clean_db, path, lambda d: d.category == "electronics", (s, e))
    rate = lambda d: d.stockouts.sum() / d.product_views.sum()
    assert rate(f) > 8 * rate(c)
    atc = lambda d: d.add_to_cart.sum() / d.product_views.sum()
    assert atc(f) < 0.9 * atc(c)
    assert (f.feed_clicks.sum() / f.feed_impressions.sum()) > 1.15 * (c.feed_clicks.sum() / c.feed_impressions.sum())
    assert f.orders.sum() < c.orders.sum()


def test_search_regression_hits_one_country_and_writes_a_search_release(clean_db, tmp_path):
    path = fresh_copy(tmp_path)
    s = pd.Timestamp("2026-10-28T08:00:00Z")
    con = db.connect(path)
    inject.search_regression(con, "DE", s, severity=0.5)
    rel = con.execute("SELECT system, country, platform FROM releases").fetchall()
    con.close()
    c, f = _compare(clean_db, path, lambda d: d.country == "DE", (s, pd.Timestamp("2027-01-01T00:00:00Z")))
    ctr = lambda d: d.search_clicks.sum() / d.searches.sum()
    assert ctr(f) < 0.65 * ctr(c)
    assert f.product_views.sum() < 0.9 * c.product_views.sum()
    assert rel == [("search", "DE", None)]


def test_ad_budget_exhausted_spikes_early_then_stops_and_leaves_traffic_alone(clean_db, tmp_path):
    path = fresh_copy(tmp_path)
    s, e = pd.Timestamp("2026-10-27T23:00:00Z"), pd.Timestamp("2026-10-28T23:00:00Z")   # one local day, 00:00 to 24:00 CET
    con = db.connect(path)
    inject.ad_budget_exhausted(con, "FR", s, e)
    rel = con.execute("SELECT system, country FROM releases").fetchall()
    con.close()
    c, f = _compare(clean_db, path, lambda d: d.country == "FR", (s, e))
    local = (pd.to_datetime(f.ts) + pd.Timedelta(hours=1)).dt.hour
    early, late = local < 10, local >= 10
    assert f[early].ad_impressions.sum() > 2.5 * c[early].ad_impressions.sum()
    assert f[late].ad_impressions.sum() < 0.1 * c[late].ad_impressions.sum()
    assert f.sessions.sum() == c.sessions.sum()                    # traffic did not change
    assert rel == [("pacing", "FR")]


def test_new_faults_cannot_be_recorded_twice_and_are_replayable(tmp_path):
    path = fresh_copy(tmp_path, hours=3 * 24)
    con = db.connect(path)
    s = pd.Timestamp("2026-10-26T06:00:00Z")
    inject.stockout_surge(con, "home", s)
    with pytest.raises(inject.AlreadyInjected):
        inject.stockout_surge(con, "home", s)
    inject.search_regression(con, "ES", s)
    inject.ad_budget_exhausted(con, "PL", s, s + pd.Timedelta(hours=24))
    kinds = [r[0] for r in con.execute("SELECT kind FROM ground_truth ORDER BY rowid")]
    con.close()
    assert kinds == ["stockout_surge", "search_regression", "ad_budget_exhausted"]


# ------------------------------------------------------------------ rollback
def test_ending_a_fault_restores_normal_data_from_that_hour_and_writes_a_rollback(clean_db, tmp_path):
    path = fresh_copy(tmp_path)
    s, e = pd.Timestamp("2026-10-28T08:00:00Z"), pd.Timestamp("2026-10-28T14:00:00Z")
    con = db.connect(path)
    inject.payment_failure(con, "DE", "Android", s, severity=0.5)
    (rowid,) = con.execute("SELECT rowid FROM ground_truth").fetchone()
    inject.end_injection(con, rowid, e)
    releases = con.execute("SELECT ts, system, country, platform, description FROM releases ORDER BY ts").fetchall()
    con.close()
    clean, now = key(facts(clean_db)), key(facts(path))
    ts = pd.to_datetime(clean.ts)
    target = (clean.country == "DE") & (clean.platform == "Android")
    during, after = (ts >= s) & (ts < e) & target, (ts >= e)
    pd.testing.assert_frame_equal(clean[~during], now[~during])               # only the fault window differs
    assert now[during].payment_successes.sum() < 0.7 * clean[during].payment_successes.sum()
    assert [r[1] for r in releases] == ["payments-sdk", "payments-sdk"] and "rollback" in releases[1][4]
    assert releases[1][0] == "2026-10-28T14:00:00Z" and releases[1][2:4] == ("DE", "Android")


def test_ending_twice_or_ending_nothing_is_refused(tmp_path):
    path = fresh_copy(tmp_path, hours=3 * 24)
    con = db.connect(path)
    inject.stockout_surge(con, "home", pd.Timestamp("2026-10-26T06:00:00Z"))
    (rowid,) = con.execute("SELECT rowid FROM ground_truth").fetchone()
    inject.end_injection(con, rowid, pd.Timestamp("2026-10-26T12:00:00Z"))
    with pytest.raises(inject.AlreadyEnded):
        inject.end_injection(con, rowid, pd.Timestamp("2026-10-26T18:00:00Z"))
    with pytest.raises(inject.NoSuchInjection):
        inject.end_injection(con, 999, pd.Timestamp("2026-10-26T18:00:00Z"))
    rel = con.execute("SELECT COUNT(*) FROM releases").fetchone()[0]
    ended = inject.list_injections(con, pd.Timestamp("2026-10-26T12:00:00Z"))
    con.close()
    assert rel == 0                                                           # stockouts have no release to roll back
    assert ended[0]["active"] is False


def test_a_fault_ended_before_it_starts_is_cancelled_and_the_same_fault_can_be_injected_again(clean_db, tmp_path):
    path = fresh_copy(tmp_path)
    s = pd.Timestamp("2026-11-02T00:00:00Z")                                  # after the last hour of the data (10-31): nothing generated yet
    con = db.connect(path)
    inject.search_regression(con, "ES", s)
    (rowid,) = con.execute("SELECT rowid FROM ground_truth").fetchone()
    inject.end_injection(con, rowid, s)                                       # ends the moment it starts
    inject.search_regression(con, "ES", s)                                    # the same settings again: allowed
    con.close()
    pd.testing.assert_frame_equal(key(facts(clean_db)), key(facts(path)))


# ------------------------------------------------------------ published totals
def test_default_database_has_the_published_totals(tmp_path):
    """The numbers in docs/contracts/README.md. They differ if numpy or scipy behave differently, or if the generator
    changed on purpose (then update this test and that file together)."""
    path = backfill(str(tmp_path / "default.db"), 28 * 24)                    # default start and seed 42
    con = sqlite3.connect(path)
    row = con.execute("SELECT COUNT(*), SUM(sessions), SUM(orders), ROUND(SUM(gmv), 2), ROUND(SUM(ad_spend), 2) FROM facts_hourly").fetchone()
    con.close()
    assert row == (120960, 3081260, 181185, 11631523.55, 64859.08)


# ------------------------------------------------------------- effect at once, no stacking
def test_a_fault_started_at_the_latest_hour_shows_at_once(clean_db, tmp_path):
    path = fresh_copy(tmp_path)
    con = db.connect(path)
    last = db.last_hour(con)
    inject.payment_failure(con, "DE", "Android", last, severity=0.5)       # "now" = the latest hour
    con.close()
    clean, now = key(facts(clean_db)), key(facts(path))
    ts = pd.to_datetime(clean.ts)
    target = (clean.country == "DE") & (clean.platform == "Android") & (ts == last)
    assert now[target].payment_successes.sum() < 0.8 * clean[target].payment_successes.sum()
    pd.testing.assert_frame_equal(clean[~target], now[~target])             # only the latest hour of that segment moved


def test_a_later_click_on_the_same_target_does_not_stack_but_a_finished_one_can_be_repeated(tmp_path):
    path = fresh_copy(tmp_path, hours=3 * 24)
    con = db.connect(path)
    s = pd.Timestamp("2026-10-26T06:00:00Z")
    inject.search_regression(con, "ES", s, severity=0.4)
    with pytest.raises(inject.AlreadyInjected):                            # three hours later, same target, still on
        inject.search_regression(con, "ES", s + pd.Timedelta(hours=3), severity=0.4)
    (rowid,) = con.execute("SELECT rowid FROM ground_truth").fetchone()
    inject.end_injection(con, rowid, s + pd.Timedelta(hours=5))
    inject.search_regression(con, "ES", s + pd.Timedelta(hours=8), severity=0.4)    # after it ended: allowed
    inject.promo(con, "FR", s, s + pd.Timedelta(hours=4))
    inject.promo(con, "FR", s + pd.Timedelta(hours=4), s + pd.Timedelta(hours=8))   # back to back, no overlap: allowed
    con.close()


# ------------------------------------------------------------------- the clock rule
def test_on_time_data_arrives_between_two_and_nine_minutes_after_the_hour_ends(clean_db):
    """The agreed clock rule only works while the on-time jitter stays under 10 minutes."""
    con = sqlite3.connect(clean_db)
    rows = con.execute("SELECT ts, arrived_at FROM ingestion_log").fetchall()
    con.close()
    lags = [(pd.Timestamp(a) - pd.Timestamp(t) - pd.Timedelta(hours=1)) / pd.Timedelta(minutes=1) for t, a in rows]
    assert min(lags) >= 2 and max(lags) < 10


def test_the_clock_rule_gives_the_pending_lists_the_demo_expects(tmp_path):
    """A 2 hour payments delay over 3 hours: on successive ticks the held hours are [10], [10, 11], [11, 12], [12], []."""
    path = fresh_copy(tmp_path, hours=3 * 24)
    s, e = pd.Timestamp("2026-10-27T10:00:00Z"), pd.Timestamp("2026-10-27T13:00:00Z")
    con = db.connect(path)
    inject.data_delay(con, "payments", s, e, delay_hours=2)
    rows = con.execute("SELECT ts, arrived_at FROM ingestion_log WHERE source = 'payments' AND ts BETWEEN ? AND ?",
                       ("2026-10-27T08:00:00Z", "2026-10-27T15:00:00Z")).fetchall()
    con.close()
    by_ts = {pd.Timestamp(t): pd.Timestamp(a) for t, a in rows}

    def pending(newest):                                   # the tick whose newest data hour is `newest`
        clock = newest + pd.Timedelta(hours=1)
        return [t.hour for t in sorted(by_ts) if t <= newest and by_ts[t] > clock + pd.Timedelta(minutes=10)]

    ticks = [pending(pd.Timestamp(f"2026-10-27T{h}:00:00Z")) for h in (10, 11, 12, 13, 14)]
    assert ticks == [[10], [10, 11], [11, 12], [12], []]


def test_schema_sql_is_what_the_code_prints():
    from sim.schema import full_ddl
    committed = (Path(__file__).resolve().parent.parent / "docs" / "contracts" / "schema.sql").read_text(encoding="utf-8")
    assert committed.strip() == full_ddl().strip()
# ------------------------------------------------------------ command line slips (issue 11)
def test_unknown_values_are_refused_before_anything_is_written(tmp_path):
    path = fresh_copy(tmp_path, hours=2 * 24)
    con = db.connect(path)
    s = pd.Timestamp("2026-10-26T06:00:00Z")
    for call in (lambda: inject.payment_failure(con, "de", "Android", s),
                 lambda: inject.payment_failure(con, "DE", "android", s),
                 lambda: inject.stockout_surge(con, "Electronics", s),
                 lambda: inject.search_regression(con, "XX", s),
                 lambda: inject.data_delay(con, "payment", s, s + pd.Timedelta(hours=2))):
        with pytest.raises(ValueError, match="unknown"):
            call()
    counts = [con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("ground_truth", "releases", "calendar")]
    con.close()
    assert counts == [0, 0, 0]


def test_a_time_without_a_zone_is_taken_as_utc(tmp_path):
    assert inject.utc("2026-10-26T06:00:00") == inject.utc("2026-10-26T06:00:00Z") == pd.Timestamp("2026-10-26T06:00:00Z")
    path = fresh_copy(tmp_path, hours=2 * 24)
    con = db.connect(path)
    inject.payment_failure(con, "DE", "Android", pd.Timestamp("2026-10-26T06:00:00"), severity=0.5)    # naive: used to crash in replay
    n = con.execute("SELECT COUNT(*) FROM ground_truth").fetchone()[0]
    con.close()
    assert n == 1


def _consistent_facts(f):
    """The funnel order and the money rules hold in every row."""
    assert (f[list(MEASURES)] >= 0).all().all()
    assert (f.orders <= f.payment_successes).all() and (f.payment_successes <= f.payment_attempts).all()
    assert (f.checkouts <= f.add_to_cart).all() and (f.add_to_cart <= f.product_views).all()
    assert (f.stockouts <= f.product_views).all() and (f.product_views >= f.search_clicks + f.feed_clicks).all()
    assert (f.refunds <= f.orders).all() and (f.discounts <= f.gmv).all()
    assert (f.loc[f.orders == 0, "gmv"] == 0).all()


def _ratio(d, num, den):
    return d[num].sum() / d[den].sum()


# ------------------------------------------------- new kinds: traffic, feed, mix, discount push
def test_traffic_drop_lowers_sessions_in_its_place_and_keeps_every_rate(clean_db, tmp_path):
    path = fresh_copy(tmp_path)
    s, e = pd.Timestamp("2026-10-28T08:00:00Z"), pd.Timestamp("2026-10-28T20:00:00Z")
    con = db.connect(path)
    inject.traffic_drop(con, "DE", "Android", s, e, severity=0.5)
    rel = con.execute("SELECT COUNT(*) FROM releases").fetchone()[0]
    cal = con.execute("SELECT COUNT(*) FROM calendar").fetchone()[0]
    truth = con.execute("SELECT kind, scope FROM ground_truth").fetchall()
    con.close()
    c, f = _compare(clean_db, path, lambda d: (d.country == "DE") & (d.platform == "Android"), (s, e))
    assert 0.4 < f.sessions.sum() / c.sessions.sum() < 0.6
    assert f.orders.sum() < 0.7 * c.orders.sum()
    for num, den in (("payment_successes", "payment_attempts"), ("add_to_cart", "product_views"),
                     ("search_clicks", "searches"), ("feed_clicks", "feed_impressions"), ("orders", "sessions"),
                     ("gmv", "orders"), ("discounts", "gmv"), ("stockouts", "product_views")):
        assert abs(_ratio(f, num, den) / _ratio(c, num, den) - 1) < 0.08, num        # rates stay where they were
    assert rel == 0 and cal == 0                                                    # nothing announces it
    assert truth[0][0] == "traffic_drop" and '"country": "DE"' in truth[0][1] and '"platform": "Android"' in truth[0][1]
    _consistent_facts(facts(path))


def test_feed_unavailable_moves_only_the_recommendations_channel_and_writes_its_release(clean_db, tmp_path):
    path = fresh_copy(tmp_path)
    s, e = pd.Timestamp("2026-10-28T08:00:00Z"), pd.Timestamp("2026-10-29T08:00:00Z")
    con = db.connect(path)
    inject.feed_unavailable(con, s, e, multiplier=12.0)
    rel = con.execute("SELECT ts, system, country, platform, description FROM releases").fetchall()
    con.close()
    c, f = _compare(clean_db, path, lambda d: d.channel == "recommendations", (s, e))
    assert _ratio(f, "stockouts", "product_views") > 8 * _ratio(c, "stockouts", "product_views")
    assert _ratio(f, "feed_clicks", "feed_impressions") > 1.15 * _ratio(c, "feed_clicks", "feed_impressions")
    assert _ratio(f, "add_to_cart", "product_views") < 0.9 * _ratio(c, "add_to_cart", "product_views")
    assert f.orders.sum() < c.orders.sum() and f.sessions.sum() == c.sessions.sum()
    assert rel == [("2026-10-28T08:00:00Z", "recommendations", None, None, "recommendations model 3.1 rollout")]
    _consistent_facts(facts(path))


def test_mix_shift_moves_traffic_to_one_category_with_every_per_category_rate_unchanged(clean_db, tmp_path):
    path = fresh_copy(tmp_path)
    s, e = pd.Timestamp("2026-10-28T08:00:00Z"), pd.Timestamp("2026-10-28T20:00:00Z")
    con = db.connect(path)
    inject.mix_shift(con, "electronics", s, e, share=0.5)
    counts = [con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0] for t in ("releases", "calendar")]
    con.close()
    c, f = _compare(clean_db, path, lambda d: d.country.notna(), (s, e))        # the whole window may move
    assert counts == [0, 0]                                                          # innocent and not announced
    assert 0.45 < f[f.category == "electronics"].sessions.sum() / f.sessions.sum() < 0.55
    assert abs(f.sessions.sum() / c.sessions.sum() - 1) < 0.03                      # the total stays the same
    for cat in ("electronics", "fashion", "home"):
        fc, cc = f[f.category == cat], c[c.category == cat]
        for num, den in (("add_to_cart", "product_views"), ("payment_successes", "payment_attempts"), ("gmv", "orders")):
            assert abs(_ratio(fc, num, den) / _ratio(cc, num, den) - 1) < 0.08, (cat, num)
    # the overall add-to-cart rate and order value move because the mix moved
    assert _ratio(f, "gmv", "orders") > 1.05 * _ratio(c, "gmv", "orders")
    _consistent_facts(facts(path))


def test_discount_push_raises_discounts_and_orders_in_recommendations_while_contribution_falls(clean_db, tmp_path):
    path = fresh_copy(tmp_path)
    s, e = pd.Timestamp("2026-10-28T08:00:00Z"), pd.Timestamp("2026-10-29T08:00:00Z")
    con = db.connect(path)
    inject.discount_push(con, s, e, lift=0.6)
    rel = con.execute("SELECT ts, system, country, platform, description FROM releases").fetchall()
    con.close()
    c, f = _compare(clean_db, path, lambda d: d.channel == "recommendations", (s, e))
    assert _ratio(f, "discounts", "gmv") > 1.4 * _ratio(c, "discounts", "gmv")
    assert f.orders.sum() > 1.05 * c.orders.sum()
    contribution = lambda d: d.marketplace_fees.sum() + d.ad_spend.sum() - d.discounts.sum()
    assert contribution(f) < contribution(c)
    assert f.sessions.sum() == c.sessions.sum()
    assert rel == [("2026-10-28T08:00:00Z", "recommendations", None, None, "recommendations ranking 3.2 rollout")]
    _consistent_facts(facts(path))


def test_new_kinds_refuse_stacking_and_ending_one_writes_a_recommendations_rollback(tmp_path):
    path = fresh_copy(tmp_path, hours=3 * 24)
    con = db.connect(path)
    s = pd.Timestamp("2026-10-26T06:00:00Z")
    inject.traffic_drop(con, "FR", "Web", s, None)
    with pytest.raises(inject.AlreadyInjected):
        inject.traffic_drop(con, "FR", "Web", s + pd.Timedelta(hours=2), None, severity=0.3)
    inject.feed_unavailable(con, s, None)
    with pytest.raises(inject.AlreadyInjected):
        inject.feed_unavailable(con, s + pd.Timedelta(hours=1), None)
    inject.mix_shift(con, "home", s, s + pd.Timedelta(hours=6))
    with pytest.raises(inject.AlreadyInjected):                       # a second mix shift at the same time would stack
        inject.mix_shift(con, "beauty", s + pd.Timedelta(hours=1), s + pd.Timedelta(hours=4))
    inject.discount_push(con, s, None)
    rowid = con.execute("SELECT rowid FROM ground_truth WHERE kind = 'feed_unavailable'").fetchone()[0]
    inject.end_injection(con, rowid, s + pd.Timedelta(hours=10))
    rel = con.execute("SELECT system, country, platform, description FROM releases ORDER BY rowid").fetchall()
    con.close()
    assert rel[-1] == ("recommendations", None, None, "recommendations rollback")
    for call in (lambda: inject.traffic_drop(con, "XX", "Web", s, None), lambda: inject.mix_shift(con, "toys", s, None),
                 lambda: inject.traffic_drop(con, "DE", "Web", s, None, severity=1.5),
                 lambda: inject.mix_shift(con, "home", s, None, share=1.0), lambda: inject.discount_push(con, s, None, lift=0)):
        with pytest.raises(ValueError):
            call()


def test_the_command_line_knows_the_new_kinds(tmp_path):
    path = fresh_copy(tmp_path, hours=2 * 24)
    for argv in (["traffic_drop", "--country", "DE", "--platform", "Android", "--severity", "0.5"],
                 ["feed_unavailable"], ["mix_shift", "--category", "home", "--share", "0.5", "--end", "2026-10-26T20:00:00Z"],
                 ["discount_push", "--lift", "0.6"]):
        assert inject.main(argv + ["--db", str(path)]) == 0
    with pytest.raises(SystemExit) as exc:
        inject.main(["traffic_drop", "--country", "DE", "--db", str(path)])          # needs a platform
    assert exc.value.code == 2
    con = db.connect(path)
    kinds = [r[0] for r in con.execute("SELECT kind FROM ground_truth ORDER BY rowid")]
    scope = con.execute("SELECT scope FROM ground_truth WHERE kind = 'traffic_drop'").fetchone()[0]
    con.close()
    assert kinds == ["traffic_drop", "feed_unavailable", "mix_shift", "discount_push"]
    assert '"severity": 0.5' in scope


def test_the_command_line_rejects_a_wrong_value_with_a_usage_error(tmp_path):
    path = fresh_copy(tmp_path, hours=2 * 24)
    for argv in (["payment_failure", "--country", "de", "--platform", "Android"],
                 ["payment_failure", "--country", "DE", "--platform", "android"],
                 ["data_delay", "--source", "payment", "--end", "2026-10-26T10:00:00Z"]):
        with pytest.raises(SystemExit) as exc:
            inject.main(argv + ["--db", str(path)])
        assert exc.value.code == 2
    con = db.connect(path)
    assert con.execute("SELECT COUNT(*) FROM ground_truth").fetchone()[0] == 0
    con.close()
