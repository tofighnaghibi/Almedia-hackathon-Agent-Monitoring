"""Baseline marketplace data generator (L1, Person A).

The generator works in two stages:

1. ``build_world(cfg)`` computes the **drivers**: one row per
   (timestamp, country, platform, category) with the *expected* traffic and
   every funnel rate (search CTR, add-to-cart rate, payment success rate, ...),
   plus the product catalog and the inventory simulation.
2. ``realize(world)`` samples the observed counts of ``marketplace_hourly``
   from those drivers.

The scenario injector changes drivers between the two stages (for example
``payment_success_rate`` for Android in DE after a release), so every
downstream count (orders, revenue, refunds) moves consistently.

Sampling uses inverse CDFs over uniforms drawn up front. Rows whose drivers
were not touched therefore come out *identical* in the baseline run and in the
fault run, and the difference between the two runs is exactly the injected
effect.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from scipy import stats

from .catalog import InventoryHooks, category_availability, make_catalog, simulate_inventory
from .config import SimConfig
from .schema import EVENTS_COLUMNS, MARKETPLACE_HOURLY_COLUMN_NAMES, MARKETPLACE_HOURLY_COLUMNS, TIMESTAMP_FORMAT
from .seasonality import hourly_profile, weekday_profile

# Driver columns that are rates or per-session multipliers. The injector may
# scale any of them; realize() clips probabilities to [0, 1].
RATE_DRIVERS = [
    "searches_per_session",
    "search_ctr",
    "rec_impressions_per_session",
    "rec_ctr",
    "ad_impressions_per_session",
    "ad_ctr",
    "browse_views_per_session",
    "oos_view_share",
    "add_to_cart_rate",
    "checkout_rate",
    "payment_attempt_rate",
    "payment_success_rate",
    "refund_rate",
]
PROBABILITY_DRIVERS = {
    "search_ctr", "rec_ctr", "ad_ctr", "oos_view_share", "add_to_cart_rate",
    "checkout_rate", "payment_attempt_rate", "payment_success_rate", "refund_rate",
}
DRIVER_COLUMNS = (
    ["timestamp", "country", "platform", "category", "exp_sessions"]
    + RATE_DRIVERS
    + ["aov_eur", "cpc_eur", "active_listings"]
)

# Order of the uniform columns used by realize(); never reorder, append only.
_UNIFORM_COLUMNS = [
    "sessions", "searches", "search_clicks", "rec_impr", "rec_clicks",
    "ad_impr", "ad_clicks", "browse_views", "oos_views", "cart_adds",
    "checkouts", "payment_attempts", "orders", "refunds", "revenue", "ad_spend",
]
_AD_SPEND_SHAPE = 4.0
_SEED_STREAMS = {"traffic": 0, "catalog": 1, "inventory": 2, "realize": 3}


@dataclass
class World:
    """Everything the baseline knows before counts are sampled."""

    config: SimConfig
    drivers: pd.DataFrame
    sellers: pd.DataFrame
    products: pd.DataFrame
    inventory_daily: pd.DataFrame
    purchase_orders: pd.DataFrame


@dataclass
class SimulationResult:
    world: World
    marketplace_hourly: pd.DataFrame
    events: pd.DataFrame = field(default_factory=lambda: pd.DataFrame(columns=EVENTS_COLUMNS))

    def write(self, out_dir: str | Path) -> Path:
        return write_outputs(self, out_dir)


# ---------------------------------------------------------------------------
# stage 1: drivers
# ---------------------------------------------------------------------------

def _rng(cfg: SimConfig, stream: str) -> np.random.Generator:
    seq = np.random.SeedSequence(cfg.seed).spawn(len(_SEED_STREAMS))[_SEED_STREAMS[stream]]
    return np.random.default_rng(seq)


def _grid(cfg: SimConfig) -> pd.DataFrame:
    ts = pd.date_range(pd.Timestamp(cfg.start, tz="UTC"), periods=cfg.days * 24, freq="h")
    idx = pd.MultiIndex.from_product(
        [ts, cfg.countries, cfg.platforms, cfg.categories],
        names=["timestamp", "country", "platform", "category"],
    )
    return idx.to_frame(index=False)


def _lookup(series: pd.Series, mapping: dict) -> np.ndarray:
    return series.map(mapping).to_numpy(dtype=float)


def _day_shock(keys: pd.DataFrame, sigma: float, rng: np.random.Generator) -> np.ndarray:
    """Mean-one lognormal shock shared by all rows with the same key."""
    codes, _ = pd.MultiIndex.from_frame(keys).factorize()
    shocks = rng.lognormal(-0.5 * sigma**2, sigma, size=codes.max() + 1)
    return shocks[codes]


def base_drivers(cfg: SimConfig) -> pd.DataFrame:
    """Expected traffic and funnel rates, before inventory is applied."""
    rng = _rng(cfg, "traffic")
    g = _grid(cfg)
    n = len(g)
    ts = g["timestamp"]
    offset = g["country"].map(cfg.utc_offset_hours).astype(int)
    local = ts + pd.to_timedelta(offset, unit="h")
    local_hour = local.dt.hour.to_numpy()
    local_wd = local.dt.dayofweek.to_numpy()
    date = ts.dt.floor("D")
    day_index = ((ts - ts.min()).dt.total_seconds() / 86400.0).to_numpy()

    # --- expected sessions --------------------------------------------------
    hourly = np.vstack([hourly_profile(p) for p in cfg.platforms])  # (platforms, 24)
    p_idx = g["platform"].map({p: i for i, p in enumerate(cfg.platforms)}).to_numpy()
    hour_f = hourly[p_idx, local_hour]
    wd = weekday_profile(cfg.weekday_factor)[local_wd]
    weekend = local_wd >= 5
    wd = wd * np.where(weekend, _lookup(g["category"], cfg.weekend_category_lift), 1.0)
    share_platform = np.array([cfg.platform_share[c][p] for c, p in zip(g["country"], g["platform"])])

    exp_sessions = (
        cfg.base_country_sessions_per_hour
        * _lookup(g["country"], cfg.country_scale)
        * share_platform
        * _lookup(g["category"], cfg.category_share)
        * hour_f
        * wd
        * (1.0 + cfg.daily_growth) ** day_index
        * _day_shock(pd.DataFrame({"d": date, "c": g["country"]}), cfg.noise_day_country, rng)
        * _day_shock(pd.DataFrame({"d": date, "c": g["country"], "p": g["platform"], "k": g["category"]}),
                     cfg.noise_day_segment, rng)
        * rng.lognormal(-0.5 * cfg.noise_hour**2, cfg.noise_hour, size=n)
    )

    d = g.copy()
    d["exp_sessions"] = exp_sessions

    # --- funnel rates (with a small day-level wobble per segment) ----------
    seg_day = pd.DataFrame({"d": date, "c": g["country"], "p": g["platform"], "k": g["category"]})

    def wobble() -> np.ndarray:
        return _day_shock(seg_day, cfg.noise_rate_day, rng)

    by_platform = lambda m: _lookup(g["platform"], m)  # noqa: E731
    by_category = lambda m: _lookup(g["category"], m)  # noqa: E731

    d["searches_per_session"] = by_platform(cfg.searches_per_session) * wobble()
    d["search_ctr"] = by_category(cfg.search_ctr) * wobble()
    d["rec_impressions_per_session"] = by_platform(cfg.rec_impressions_per_session) * wobble()
    d["rec_ctr"] = by_category(cfg.rec_ctr) * wobble()
    d["ad_impressions_per_session"] = cfg.ad_impressions_per_session * wobble()
    d["ad_ctr"] = by_category(cfg.ad_ctr) * wobble()
    d["browse_views_per_session"] = cfg.browse_views_per_session * wobble()
    d["oos_view_share"] = 0.0  # filled in from inventory
    d["add_to_cart_rate"] = by_category(cfg.add_to_cart_rate) * wobble()
    d["checkout_rate"] = by_platform(cfg.checkout_rate) * wobble()
    d["payment_attempt_rate"] = by_platform(cfg.payment_attempt_rate) * wobble()
    d["payment_success_rate"] = np.minimum(by_platform(cfg.payment_success_rate) * wobble(), 0.995)
    d["refund_rate"] = by_category(cfg.refund_rate) * wobble()
    d["aov_eur"] = by_category(cfg.aov_eur) * _lookup(g["country"], cfg.country_price_factor) * wobble()
    d["cpc_eur"] = by_category(cfg.cpc_eur) * _lookup(g["country"], cfg.country_cpc_factor) * wobble()
    d["active_listings"] = 0
    return d[DRIVER_COLUMNS]


def expected_daily_units(drivers: pd.DataFrame, cfg: SimConfig) -> pd.DataFrame:
    """Expected units demanded per (date, category), from the funnel drivers."""
    views = drivers["exp_sessions"] * (
        drivers["searches_per_session"] * drivers["search_ctr"]
        + drivers["rec_impressions_per_session"] * drivers["rec_ctr"]
        + drivers["ad_impressions_per_session"] * drivers["ad_ctr"]
        + drivers["browse_views_per_session"]
    )
    orders = (
        views
        * drivers["add_to_cart_rate"]
        * drivers["checkout_rate"]
        * drivers["payment_attempt_rate"]
        * drivers["payment_success_rate"]
    )
    units = (orders * cfg.units_per_order).rename("units")
    frame = pd.DataFrame({"date": drivers["timestamp"].dt.floor("D"), "category": drivers["category"], "units": units})
    return frame.pivot_table(index="date", columns="category", values="units", aggfunc="sum")


def apply_inventory(drivers: pd.DataFrame, availability: pd.DataFrame) -> pd.DataFrame:
    """Copy daily per-category stock levels into the hourly drivers."""
    d = drivers.drop(columns=["active_listings", "oos_view_share"])
    d["date"] = d["timestamp"].dt.floor("D")
    d = d.merge(availability, on=["date", "category"], how="left", validate="many_to_one")
    d["active_listings"] = d["active_listings"].astype(int)
    return d.drop(columns="date")[DRIVER_COLUMNS]


def build_world(cfg: Optional[SimConfig] = None, inventory_hooks: Optional[InventoryHooks] = None) -> World:
    cfg = cfg or SimConfig()
    drivers = base_drivers(cfg)
    sellers, products = make_catalog(cfg, _rng(cfg, "catalog"))
    inventory, pos = simulate_inventory(
        products, expected_daily_units(drivers, cfg), cfg, _rng(cfg, "inventory"), inventory_hooks
    )
    drivers = apply_inventory(drivers, category_availability(inventory, products))
    return World(cfg, drivers, sellers, products, inventory, pos)


# ---------------------------------------------------------------------------
# stage 2: sample observed counts
# ---------------------------------------------------------------------------

# Above this expected count the exact inverse CDF is replaced by a normal
# approximation (same uniform in, so runs stay coupled); exact below it.
_EXACT_LIMIT = 40.0


def poisson_ppf(u: np.ndarray, mu: np.ndarray) -> np.ndarray:
    mu = np.maximum(np.asarray(mu, dtype=float), 0.0)
    out = np.empty(len(mu), dtype=np.int64)
    small = mu < _EXACT_LIMIT
    out[small] = stats.poisson.ppf(u[small], mu[small])
    z = stats.norm.ppf(u[~small])
    out[~small] = np.maximum(0, np.round(mu[~small] + z * np.sqrt(mu[~small])))
    return out


def binom_ppf(u: np.ndarray, n: np.ndarray, p: np.ndarray) -> np.ndarray:
    n = np.asarray(n, dtype=np.int64)
    p = np.broadcast_to(np.asarray(p, dtype=float), n.shape)
    mean, var = n * p, n * p * (1 - p)
    out = np.empty(len(n), dtype=np.int64)
    small = np.minimum(mean, n - mean) < _EXACT_LIMIT
    out[small] = stats.binom.ppf(u[small], n[small], p[small])
    z = stats.norm.ppf(u[~small])
    out[~small] = np.clip(np.round(mean[~small] + z * np.sqrt(var[~small])), 0, n[~small])
    return out


def _uniforms(cfg: SimConfig, n_rows: int) -> dict[str, np.ndarray]:
    u = _rng(cfg, "realize").random((n_rows, len(_UNIFORM_COLUMNS)))
    u = np.clip(u, 1e-12, 1 - 1e-12)
    return {name: u[:, i] for i, name in enumerate(_UNIFORM_COLUMNS)}


def realize(world: World, drivers: Optional[pd.DataFrame] = None) -> pd.DataFrame:
    """Sample ``marketplace_hourly`` from the drivers.

    ``drivers`` defaults to ``world.drivers``. A modified copy can be passed;
    it must keep the same rows in the same order (only values may change), so
    untouched rows reuse the same random numbers.
    """
    cfg = world.config
    d = world.drivers if drivers is None else drivers
    if len(d) != len(world.drivers):
        raise ValueError("drivers must keep the baseline grid (same rows, same order)")
    u = _uniforms(cfg, len(d))

    def prob(col: str) -> np.ndarray:
        p = d[col].to_numpy(dtype=float)
        return np.clip(p, 0.0, 1.0) if col in PROBABILITY_DRIVERS else np.maximum(p, 0.0)

    def poisson(key: str, mu: np.ndarray) -> np.ndarray:
        return poisson_ppf(u[key], mu)

    def binom(key: str, n: np.ndarray, p: np.ndarray) -> np.ndarray:
        return binom_ppf(u[key], n, p)

    def gamma_total(key: str, count: np.ndarray, mean_each: np.ndarray, shape: float) -> np.ndarray:
        # sum of `count` Gamma(shape, mean_each/shape) draws = Gamma(count*shape, ...)
        out = np.zeros(len(count))
        m = count > 0
        out[m] = stats.gamma.ppf(u[key][m], a=count[m] * shape, scale=mean_each[m] / shape)
        return np.round(out, 2)

    sessions = poisson("sessions", d["exp_sessions"].to_numpy(dtype=float))
    searches = poisson("searches", sessions * prob("searches_per_session"))
    search_clicks = binom("search_clicks", searches, prob("search_ctr"))
    rec_impr = poisson("rec_impr", sessions * prob("rec_impressions_per_session"))
    rec_clicks = binom("rec_clicks", rec_impr, prob("rec_ctr"))
    ad_impr = poisson("ad_impr", sessions * prob("ad_impressions_per_session"))
    ad_clicks = binom("ad_clicks", ad_impr, prob("ad_ctr"))
    browse_views = poisson("browse_views", sessions * prob("browse_views_per_session"))
    product_views = search_clicks + rec_clicks + ad_clicks + browse_views
    oos_views = binom("oos_views", product_views, prob("oos_view_share"))
    cart_adds = binom("cart_adds", product_views - oos_views, prob("add_to_cart_rate"))
    checkouts = binom("checkouts", cart_adds, prob("checkout_rate"))
    payment_attempts = binom("payment_attempts", checkouts, prob("payment_attempt_rate"))
    orders = binom("orders", payment_attempts, prob("payment_success_rate"))
    refunds = binom("refunds", orders, prob("refund_rate"))
    revenue = gamma_total("revenue", orders, d["aov_eur"].to_numpy(dtype=float), cfg.order_value_shape)
    ad_spend = gamma_total("ad_spend", ad_clicks, d["cpc_eur"].to_numpy(dtype=float), _AD_SPEND_SHAPE)

    out = pd.DataFrame(
        {
            "timestamp": d["timestamp"].to_numpy(),
            "country": d["country"].to_numpy(),
            "platform": d["platform"].to_numpy(),
            "category": d["category"].to_numpy(),
            "sessions": sessions,
            "searches": searches,
            "search_clicks": search_clicks,
            "recommendation_impressions": rec_impr,
            "recommendation_clicks": rec_clicks,
            "cart_adds": cart_adds,
            "checkouts": checkouts,
            "payment_attempts": payment_attempts,
            "orders": orders,
            "refunds": refunds,
            "revenue": revenue,
            "ad_impressions": ad_impr,
            "ad_clicks": ad_clicks,
            "ad_spend": ad_spend,
            "active_listings": d["active_listings"].to_numpy(dtype=np.int64),
            "out_of_stock_views": oos_views,
        }
    )
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)
    return out[MARKETPLACE_HOURLY_COLUMN_NAMES]


def generate(cfg: Optional[SimConfig] = None, inventory_hooks: Optional[InventoryHooks] = None) -> SimulationResult:
    """Build the world and sample the baseline (no faults, no events)."""
    world = build_world(cfg, inventory_hooks)
    return SimulationResult(world=world, marketplace_hourly=realize(world))


# ---------------------------------------------------------------------------
# files
# ---------------------------------------------------------------------------

def _fmt_ts(s: pd.Series) -> pd.Series:
    return pd.to_datetime(s, utc=True).dt.strftime(TIMESTAMP_FORMAT)


def write_outputs(result: SimulationResult, out_dir: str | Path) -> Path:
    """Write the contract tables plus the supporting product-level tables.

    Contract tables: marketplace_hourly.csv, events.csv.
    Extra (not in the contract yet): sellers.csv, products.csv,
    inventory_daily.csv, purchase_orders.csv, sim_config.json.
    """
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    hourly = result.marketplace_hourly.copy()
    hourly["timestamp"] = _fmt_ts(hourly["timestamp"])
    hourly.to_csv(out / "marketplace_hourly.csv", index=False)

    events = result.events.copy()
    for col in ("start_time", "end_time"):
        if len(events):
            events[col] = pd.to_datetime(events[col], utc=True).dt.strftime(TIMESTAMP_FORMAT)
    events.reindex(columns=EVENTS_COLUMNS).to_csv(out / "events.csv", index=False)

    w = result.world
    w.sellers.to_csv(out / "sellers.csv", index=False)
    w.products.to_csv(out / "products.csv", index=False)
    inv = w.inventory_daily.copy()
    inv["date"] = inv["date"].dt.strftime("%Y-%m-%d")
    inv.to_csv(out / "inventory_daily.csv", index=False)
    pos = w.purchase_orders.copy()
    for col in ("order_date", "expected_arrival", "actual_arrival"):
        pos[col] = pd.to_datetime(pos[col]).dt.strftime("%Y-%m-%d")
    pos.to_csv(out / "purchase_orders.csv", index=False)
    (out / "sim_config.json").write_text(json.dumps(asdict(w.config), indent=2))
    return out


def load_marketplace_hourly(path: str | Path) -> pd.DataFrame:
    """Read marketplace_hourly.csv back with the contract dtypes."""
    df = pd.read_csv(path)
    for name, kind in MARKETPLACE_HOURLY_COLUMNS:
        if kind == "datetime":
            df[name] = pd.to_datetime(df[name], utc=True, format=TIMESTAMP_FORMAT)
        elif kind == "int":
            df[name] = df[name].astype(np.int64)
        elif kind == "float":
            df[name] = df[name].astype(float)
        else:
            df[name] = df[name].astype(str)
    return df
