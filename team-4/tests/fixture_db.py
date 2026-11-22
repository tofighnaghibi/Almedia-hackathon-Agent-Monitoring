"""A small database that follows the data contract (README §3.1), for tests and for working
before the simulator is ready. It is a stub of the simulator, not the simulator."""
from __future__ import annotations

import itertools
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

COUNTRIES = ("DE", "FR")
PLATFORMS = ("iOS", "Android", "Web")
CATEGORIES = ("electronics", "fashion", "home")
CHANNELS = ("organic", "ads", "recommendations")
SOURCES = ("traffic", "search", "recommendations", "orders", "payments", "ads", "inventory")
TS_FORMAT = "%Y-%m-%dT%H:00:00Z"


def make_frame(days: int = 7, start: str = "2026-11-01T00:00:00Z", seed: int = 0, scale: float = 1.0,
               payment_success: float = 0.93) -> pd.DataFrame:
    """scale multiplies the traffic (and so every count); payment_success is the success rate of an attempt."""
    rng = np.random.default_rng(seed)
    hours = pd.date_range(start, periods=days * 24, freq="h", tz="UTC")
    grid = pd.DataFrame(list(itertools.product(hours, COUNTRIES, PLATFORMS, CATEGORIES, CHANNELS)),
                        columns=["ts", "country", "platform", "category", "channel"])
    n = len(grid)
    hour = grid["ts"].dt.hour.to_numpy()
    weekend = grid["ts"].dt.day_of_week.to_numpy() >= 5
    diurnal = 0.35 + 0.65 * np.clip(np.sin((hour - 6) / 24 * 2 * np.pi), 0, None)
    level = 40 * scale * diurnal * np.where(weekend, 1.15, 1.0)

    sessions = rng.poisson(level)
    product_views = rng.poisson(sessions * 1.8)
    add_to_cart = rng.binomial(product_views, 0.08)
    checkouts = rng.binomial(add_to_cart, 0.5)
    payment_attempts = checkouts + rng.binomial(checkouts, 0.05)
    payment_successes = rng.binomial(payment_attempts, payment_success)
    orders = rng.binomial(payment_successes, 0.995)
    gmv = np.round(orders * rng.lognormal(np.log(45), 0.3, n), 2)
    searches = rng.poisson(sessions * 0.9)
    feed_impressions = rng.poisson(sessions * 5)
    ad_impressions = rng.poisson(sessions * 3)
    ad_clicks = rng.binomial(ad_impressions, 0.02)

    grid["ts"] = grid["ts"].dt.strftime(TS_FORMAT)
    grid = grid.assign(
        sessions=sessions, searches=searches, search_clicks=rng.binomial(searches, 0.4),
        feed_impressions=feed_impressions, feed_clicks=rng.binomial(feed_impressions, 0.05),
        product_views=product_views, add_to_cart=add_to_cart, checkouts=checkouts,
        payment_attempts=payment_attempts, payment_successes=payment_successes, orders=orders,
        gmv=gmv, discounts=np.round(gmv * rng.uniform(0.02, 0.06, n), 2),
        marketplace_fees=np.round(gmv * 0.12, 2), refunds=rng.binomial(orders, 0.03),
        ad_impressions=ad_impressions, ad_clicks=ad_clicks,
        ad_spend=np.round(ad_clicks * rng.uniform(0.25, 0.45, n), 2),
        stockouts=rng.poisson(2, n),
    )
    return grid


def make_fixture_db(path: str | Path, days: int = 7, seed: int = 0, mutate=None,
                    releases: list[tuple] = (), **frame) -> Path:
    """mutate(df) -> df changes the facts before they are written (for contract-breaking tests);
    releases are (ts, system, country, platform, description) rows; frame goes to make_frame."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    facts = make_frame(days=days, seed=seed, **frame)
    hours = sorted(facts["ts"].unique())
    if mutate is not None:
        facts = mutate(facts)
    con = sqlite3.connect(path)
    try:
        facts.to_sql("facts_hourly", con, index=False)
        arrived = [(s, h, (pd.Timestamp(h) + pd.Timedelta(hours=1, minutes=5)).strftime("%Y-%m-%dT%H:%M:%SZ"))
                   for h in hours for s in SOURCES]
        pd.DataFrame(arrived, columns=["source", "ts", "arrived_at"]).to_sql("ingestion_log", con, index=False)
        con.execute("CREATE TABLE releases (ts TEXT, system TEXT, country TEXT, platform TEXT, description TEXT)")
        con.executemany("INSERT INTO releases VALUES (?, ?, ?, ?, ?)", list(releases))
        con.execute("CREATE TABLE calendar (start_ts TEXT, end_ts TEXT, country TEXT, kind TEXT, description TEXT)")
        con.execute("CREATE TABLE ground_truth (injected_at TEXT, kind TEXT, scope TEXT, description TEXT)")
        con.commit()
    finally:
        con.close()
    return path


if __name__ == "__main__":
    import sys
    out = sys.argv[1] if len(sys.argv) > 1 else "data/market.db"
    print(make_fixture_db(out, days=int(sys.argv[2]) if len(sys.argv) > 2 else 28))
