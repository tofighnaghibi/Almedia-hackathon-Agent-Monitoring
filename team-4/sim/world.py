"""The simulated marketplace: one call per hour produces one row per segment.

The funnel is built from counts and rates, so the graph formulas hold exactly in the data:
    sessions -> product_views -> add_to_cart -> checkouts -> payment_attempts
             -> payment_successes -> orders -> gmv
Every hour is drawn from its own random stream, seeded by (seed, hour index). A backfill, a live
run and a replay of an injection therefore produce identical rows for everything that was not
changed by the injection.

Local time is CET (UTC+1) all through November.
"""
from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats

from sim.schema import DIMENSIONS, MEASURES, SOURCES, TS_FORMAT, TS_SECONDS_FORMAT

COUNTRIES = ("DE", "FR", "ES", "PL")
PLATFORMS = ("iOS", "Android", "Web")
CATEGORIES = ("electronics", "fashion", "home", "beauty", "sports")
CHANNELS = ("organic", "ads", "recommendations")

SCALE = 4000.0  # sessions per hour at the evening peak for the biggest country, all segments together
TREND_PER_DAY = 0.001
HOUR_SHOCK_SIGMA = 0.04  # shared by all segments of a country in an hour
N_DRAWS = 18             # uniform numbers per segment and hour, one per generated value

COUNTRY_W = {"DE": 1.0, "FR": 0.7, "ES": 0.45, "PL": 0.35}
PLATFORM_W = {"iOS": 0.30, "Android": 0.45, "Web": 0.25}
CATEGORY_W = {"electronics": 0.25, "fashion": 0.30, "home": 0.20, "beauty": 0.15, "sports": 0.10}
CHANNEL_W = {"organic": 0.50, "ads": 0.25, "recommendations": 0.25}

PAYMENT_SUCCESS = {"iOS": 0.945, "Android": 0.935, "Web": 0.955}
PAYMENT_COUNTRY_OFFSET = {"DE": 0.005, "FR": 0.0, "ES": -0.010, "PL": -0.005}
ADD_TO_CART = {"electronics": 0.060, "fashion": 0.100, "home": 0.070, "beauty": 0.090, "sports": 0.080}
ADD_TO_CART_CHANNEL = {"organic": 1.0, "ads": 0.9, "recommendations": 1.1}
AOV = {"electronics": 120.0, "fashion": 55.0, "home": 80.0, "beauty": 35.0, "sports": 60.0}
AOV_COUNTRY = {"DE": 1.0, "FR": 0.95, "ES": 0.85, "PL": 0.75}
TAKE_RATE = {"electronics": 0.08, "fashion": 0.15, "home": 0.12, "beauty": 0.15, "sports": 0.13}
STOCKOUT_RATE = 0.015  # share of product views that land on an out-of-stock listing
DISCOUNT_RATE = {"electronics": 0.03, "fashion": 0.06, "home": 0.04, "beauty": 0.05, "sports": 0.05}


def _diurnal() -> np.ndarray:
    h = np.arange(24)
    def bump(centre, width):
        d = np.minimum(np.abs(h - centre), 24 - np.abs(h - centre))
        return np.exp(-((d / width) ** 2))
    curve = 0.18 + 0.50 * bump(12.5, 2.6) + 0.95 * bump(20.5, 2.9)
    return curve / curve.max()


DIURNAL = _diurnal()  # indexed by local hour


@dataclass(frozen=True)
class Injection:
    """A fault or an innocent event, replayable from the ground_truth table."""
    kind: str                      # payment_failure | stockout_surge | search_regression | ad_budget_exhausted | promo |
                                   # data_delay | traffic_drop | feed_unavailable | mix_shift | discount_push
    start: pd.Timestamp
    end: pd.Timestamp | None       # None = until the end of the simulation
    params: dict = field(default_factory=dict)

    def active(self, ts: pd.Timestamp) -> bool:
        return ts >= self.start and (self.end is None or ts < self.end)

    def to_scope_json(self) -> str:
        d = dict(self.params)
        d["start"] = self.start.strftime(TS_FORMAT)
        d["end"] = None if self.end is None else self.end.strftime(TS_FORMAT)
        return json.dumps(d, sort_keys=True)

    @staticmethod
    def from_row(kind: str, scope_json: str) -> "Injection":
        d = json.loads(scope_json)
        start = pd.Timestamp(d.pop("start"))
        end = d.pop("end", None)
        return Injection(kind, start, None if end is None else pd.Timestamp(end), d)


class Segments:
    """The constant per-segment parameters, built once for a set of countries."""

    def __init__(self, countries=COUNTRIES):
        rows = list(itertools.product(countries, PLATFORMS, CATEGORIES, CHANNELS))
        self.countries = tuple(countries)
        self.n = len(rows)
        self.country = np.array([r[0] for r in rows])
        self.platform = np.array([r[1] for r in rows])
        self.category = np.array([r[2] for r in rows])
        self.channel = np.array([r[3] for r in rows])
        self.share = np.array([COUNTRY_W[c] * PLATFORM_W[p] * CATEGORY_W[k] * CHANNEL_W[ch] for c, p, k, ch in rows])
        self.psr = np.array([PAYMENT_SUCCESS[p] + PAYMENT_COUNTRY_OFFSET[c] for c, p, k, ch in rows])
        self.a2c = np.array([ADD_TO_CART[k] * ADD_TO_CART_CHANNEL[ch] for c, p, k, ch in rows])
        self.aov = np.array([AOV[k] * AOV_COUNTRY[c] for c, p, k, ch in rows])
        self.take = np.array([TAKE_RATE[k] for c, p, k, ch in rows])
        self.disc = np.array([DISCOUNT_RATE[k] for c, p, k, ch in rows])
        self.country_index = np.array([self.countries.index(c) for c in self.country])


def _mask(seg: Segments, params: dict) -> np.ndarray:
    m = np.ones(seg.n, dtype=bool)
    for dim in DIMENSIONS:
        if params.get(dim):
            m &= getattr(seg, dim) == params[dim]
    return m


def generate_hour(seg: Segments, ts: pd.Timestamp, idx: int, seed: int, injections=()) -> pd.DataFrame:
    """One hour of facts for every segment. `idx` is the hour number since the simulation start."""
    n = seg.n
    # Every value gets its own uniform number, drawn up front, and is turned into a count with
    # the inverse CDF. A change in one segment's parameters therefore changes only that
    # segment's values: the random numbers of all other segments stay exactly where they were.
    u = np.clip(np.random.default_rng([seed, idx]).random((N_DRAWS, n)), 1e-12, 1 - 1e-12)
    poisson = lambda i, mean: stats.poisson.ppf(u[i], mean).astype(np.int64)
    binomial = lambda i, trials, p: stats.binom.ppf(u[i], trials, np.clip(p, 0.0, 1.0)).astype(np.int64)

    local = ts + pd.Timedelta(hours=1)
    weekend = {4: 1.03, 5: 1.10, 6: 1.15}.get(local.day_of_week, 1.0)
    trend = 1.0 + TREND_PER_DAY * idx / 24.0
    country_shock = np.exp(np.random.default_rng([seed, idx, 1]).normal(0.0, HOUR_SHOCK_SIGMA, len(seg.countries)))
    shock = country_shock[seg.country_index]

    traffic_mult = np.ones(n)
    conv_mult = np.ones(n)
    psr = seg.psr.copy()
    disc_extra = np.zeros(n)
    stock_mult = np.ones(n)
    feed_boost = np.ones(n)
    search_mult = np.ones(n)
    ad_mult = np.ones(n)
    for inj in injections:
        if not inj.active(ts):
            continue
        m = _mask(seg, inj.params)
        if inj.kind == "payment_failure":
            psr[m] *= 1.0 - float(inj.params.get("severity", 0.3))
        elif inj.kind == "stockout_surge":
            # popular listings run out: more views land on unavailable products, the feed still gets clicked
            stock_mult[m] *= float(inj.params.get("multiplier", 12.0))
            feed_boost[m] *= 1.0 + float(inj.params.get("feed_ctr_lift", 0.25))
        elif inj.kind == "search_regression":
            search_mult[m] *= 1.0 - float(inj.params.get("severity", 0.4))
        elif inj.kind == "ad_budget_exhausted":
            # the pacing controller ignores its schedule: sponsored impressions per session jump for the first
            # boost_hours, then the budget is gone and they fall to almost nothing
            hours_in = (ts - inj.start) / pd.Timedelta(hours=1)
            ad_mult[m] *= float(inj.params.get("boost", 3.0)) if hours_in < float(inj.params.get("boost_hours", 10)) \
                else float(inj.params.get("after", 0.05))
        elif inj.kind == "promo":
            traffic_mult[m] *= 1.0 + float(inj.params.get("traffic_lift", 0.4))
            conv_mult[m] *= 1.0 + float(inj.params.get("conversion_lift", 0.2))
            disc_extra[m] += float(inj.params.get("extra_discount", 0.03))
        elif inj.kind == "traffic_drop":
            # an outage, a tracking loss or an app-store problem: fewer sessions arrive, every step rate stays the same
            traffic_mult[m] *= 1.0 - float(inj.params.get("severity", 0.5))
        elif inj.kind == "feed_unavailable":
            # the recommendations model shows attractive but unavailable products (its params carry the channel):
            # more feed clicks land on out-of-stock listings
            stock_mult[m] *= float(inj.params.get("multiplier", 12.0))
            feed_boost[m] *= 1.0 + float(inj.params.get("feed_ctr_lift", 0.25))
        elif inj.kind == "mix_shift":
            # traffic moves toward one category (its share of sessions becomes `share`), the total stays the same,
            # and every per-category rate is unchanged: an innocent change of the mix
            share, own = float(inj.params.get("share", 0.5)), CATEGORY_W[inj.params["category"]]
            target = seg.category == inj.params["category"]
            traffic_mult[target] *= share / own
            traffic_mult[~target] *= (1.0 - share) / (1.0 - own)
        elif inj.kind == "discount_push":
            # the recommendations ranking pushes discounted products (its params carry the channel): the
            # platform-funded discount per euro rises by `lift`, more carts convert, contribution falls
            disc_extra[m] += float(inj.params.get("lift", 0.6)) * seg.disc[m]
            conv_mult[m] *= 1.0 + float(inj.params.get("conversion_lift", 0.15))

    mean_sessions = SCALE * seg.share * DIURNAL[local.hour] * weekend * trend * shock * traffic_mult
    sessions = poisson(0, mean_sessions)
    searches = poisson(1, sessions * 0.9)
    search_clicks = binomial(2, searches, 0.40 * search_mult)
    feed_impressions = poisson(3, sessions * 5.0)
    feed_clicks = binomial(4, feed_impressions, 0.05 * feed_boost)
    direct_views = poisson(5, sessions * 0.9)
    product_views = search_clicks + feed_clicks + direct_views
    stockouts = binomial(17, product_views, STOCKOUT_RATE * stock_mult)
    add_to_cart = binomial(6, product_views - stockouts, seg.a2c * conv_mult)
    checkouts = binomial(7, add_to_cart, 0.5)
    payment_attempts = checkouts + binomial(8, checkouts, 0.05)
    payment_successes = binomial(9, payment_attempts, psr)
    orders = binomial(10, payment_successes, 0.995)

    order_value = seg.aov * np.exp(stats.norm.ppf(u[11]) * 0.25 / np.sqrt(np.maximum(orders, 1)))
    gmv = np.round(orders * order_value, 2)
    discounts = np.round(gmv * (seg.disc + disc_extra) * (0.85 + 0.30 * u[12]), 2)
    marketplace_fees = np.round(gmv * seg.take, 2)
    refunds = binomial(13, orders, 0.03)
    ad_impressions = poisson(14, sessions * 3.0 * ad_mult)
    ad_clicks = binomial(15, ad_impressions, 0.02)
    ad_spend = np.round(ad_clicks * (0.25 + 0.20 * u[16]), 2)

    frame = {
        "ts": np.full(n, ts.strftime(TS_FORMAT)),
        "country": seg.country, "platform": seg.platform, "category": seg.category, "channel": seg.channel,
        "sessions": sessions, "searches": searches, "search_clicks": search_clicks,
        "feed_impressions": feed_impressions, "feed_clicks": feed_clicks, "product_views": product_views,
        "add_to_cart": add_to_cart, "checkouts": checkouts, "payment_attempts": payment_attempts,
        "payment_successes": payment_successes, "orders": orders, "gmv": gmv, "discounts": discounts,
        "marketplace_fees": marketplace_fees, "refunds": refunds, "ad_impressions": ad_impressions,
        "ad_clicks": ad_clicks, "ad_spend": ad_spend, "stockouts": stockouts,
    }
    return pd.DataFrame(frame)[list(("ts",) + DIMENSIONS + MEASURES)]


def ingestion_rows(ts: pd.Timestamp, idx: int, seed: int, injections=()) -> list[tuple]:
    """When each source's data for hour `ts` arrived. Normally a few minutes after the hour ends."""
    rng = np.random.default_rng([seed, idx, 7])
    rows = []
    for source in SOURCES:
        delay = pd.Timedelta(minutes=int(rng.integers(2, 10)))
        for inj in injections:
            if inj.kind == "data_delay" and inj.params.get("source") == source and inj.active(ts):
                delay += pd.Timedelta(hours=float(inj.params.get("delay_hours", 2)))
        rows.append((source, ts.strftime(TS_FORMAT), (ts + pd.Timedelta(hours=1) + delay).strftime(TS_SECONDS_FORMAT)))
    return rows
