import re

import pandas as pd
import pytest

from simulator import InventoryHooks, SimConfig, build_world, generate, load_marketplace_hourly, realize
from simulator.catalog import category_availability
from simulator.schema import MARKETPLACE_HOURLY_COLUMNS, MARKETPLACE_HOURLY_COLUMN_NAMES

DAYS = 14


@pytest.fixture(scope="module")
def cfg():
    return SimConfig(start="2026-10-05", days=DAYS, seed=7)  # starts on a Monday


@pytest.fixture(scope="module")
def result(cfg):
    return generate(cfg)


@pytest.fixture(scope="module")
def hourly(result):
    return result.marketplace_hourly


# --- contract ----------------------------------------------------------------

def test_columns_match_contract(hourly):
    assert list(hourly.columns) == MARKETPLACE_HOURLY_COLUMN_NAMES
    assert not hourly.isna().any().any()
    for name, kind in MARKETPLACE_HOURLY_COLUMNS:
        s = hourly[name]
        if kind == "datetime":
            assert isinstance(s.dtype, pd.DatetimeTZDtype) and str(s.dt.tz) == "UTC"
        elif kind == "int":
            assert pd.api.types.is_integer_dtype(s), name
        elif kind == "float":
            assert pd.api.types.is_float_dtype(s), name
        else:
            assert s.map(type).eq(str).all(), name


def test_full_hourly_grid(hourly, cfg):
    n_cells = len(cfg.countries) * len(cfg.platforms) * len(cfg.categories)
    assert len(hourly) == DAYS * 24 * n_cells
    assert not hourly.duplicated(["timestamp", "country", "platform", "category"]).any()
    ts = hourly["timestamp"].drop_duplicates().sort_values()
    assert (ts.diff().dropna() == pd.Timedelta(hours=1)).all()
    assert (ts.dt.minute == 0).all()


# --- funnel consistency --------------------------------------------------------

@pytest.mark.parametrize(
    "child,parent",
    [
        ("search_clicks", "searches"),
        ("recommendation_clicks", "recommendation_impressions"),
        ("ad_clicks", "ad_impressions"),
        ("checkouts", "cart_adds"),
        ("payment_attempts", "checkouts"),
        ("orders", "payment_attempts"),
        ("refunds", "orders"),
    ],
)
def test_funnel_never_inverts(hourly, child, parent):
    assert (hourly[child] <= hourly[parent]).all()


def test_values_non_negative_and_money_consistent(hourly):
    numeric = hourly.select_dtypes("number")
    assert (numeric >= 0).all().all()
    assert (hourly.loc[hourly["orders"] == 0, "revenue"] == 0).all()
    assert (hourly.loc[hourly["orders"] > 0, "revenue"] > 0).all()
    assert (hourly.loc[hourly["ad_clicks"] == 0, "ad_spend"] == 0).all()


def test_rates_close_to_config(hourly, cfg):
    by_p = hourly.groupby("platform")[["orders", "payment_attempts"]].sum()
    psr = by_p["orders"] / by_p["payment_attempts"]
    for p, expected in cfg.payment_success_rate.items():
        assert psr[p] == pytest.approx(expected, abs=0.005)

    by_c = hourly.groupby("category")[["revenue", "orders", "refunds"]].sum()
    aov = by_c["revenue"] / by_c["orders"]
    for c, expected in cfg.aov_eur.items():
        assert aov[c] == pytest.approx(expected, rel=0.08)  # country price factors add a few %
    refund = by_c["refunds"] / by_c["orders"]
    for c, expected in cfg.refund_rate.items():
        assert refund[c] == pytest.approx(expected, abs=0.006)


# --- seasonality ---------------------------------------------------------------

def test_daily_cycle_in_local_time(hourly):
    de = hourly[(hourly["country"] == "DE") & (hourly["platform"] == "android")]
    local_hour = (de["timestamp"].dt.hour + 1) % 24  # DE = UTC+1 in the simulator
    by_hour = de.groupby(local_hour)["sessions"].mean()
    assert by_hour.loc[20:21].mean() > 3 * by_hour.loc[3:4].mean()

    us = hourly[(hourly["country"] == "US") & (hourly["platform"] == "android")]
    us_peak_utc = us.groupby(us["timestamp"].dt.hour)["sessions"].mean().idxmax()
    assert (us_peak_utc - 5) % 24 in range(19, 23)  # evening peak in US local time


def test_weekend_lift(hourly):
    fashion = hourly[hourly["category"] == "fashion"]
    daily = fashion.groupby(fashion["timestamp"].dt.floor("D"))["sessions"].sum()
    weekend = daily[daily.index.dayofweek >= 5].mean()
    weekday = daily[daily.index.dayofweek < 5].mean()
    assert weekend > 1.08 * weekday


# --- reproducibility and the injector interface ---------------------------------

def test_same_seed_same_data(cfg, hourly):
    again = generate(cfg).marketplace_hourly
    pd.testing.assert_frame_equal(hourly, again)
    other = generate(SimConfig(start=cfg.start, days=DAYS, seed=cfg.seed + 1)).marketplace_hourly
    assert not other["orders"].equals(hourly["orders"])


def test_changing_a_driver_only_moves_its_rows_and_downstream(result):
    """What the scenario injector relies on: edit drivers, re-sample, and only
    the touched rows and the metrics downstream of the edited rate change."""
    world = result.world
    d = world.drivers.copy()
    start = d["timestamp"].min() + pd.Timedelta(days=7, hours=10)
    hit = (
        (d["country"] == "DE")
        & (d["platform"] == "android")
        & (d["timestamp"] >= start)
        & (d["timestamp"] < start + pd.Timedelta(hours=24))
    )
    d.loc[hit, "payment_success_rate"] *= 0.35
    faulty = realize(world, d)
    base = result.marketplace_hourly

    pd.testing.assert_frame_equal(faulty[~hit.to_numpy()], base[~hit.to_numpy()])
    upstream = ["sessions", "searches", "cart_adds", "checkouts", "payment_attempts", "ad_spend"]
    pd.testing.assert_frame_equal(faulty[upstream], base[upstream])
    ratio = faulty.loc[hit, "orders"].sum() / base.loc[hit, "orders"].sum()
    assert 0.30 < ratio < 0.40
    assert faulty.loc[hit, "revenue"].sum() < 0.45 * base.loc[hit, "revenue"].sum()


def test_realize_rejects_a_different_grid(result):
    with pytest.raises(ValueError):
        realize(result.world, result.world.drivers.iloc[:-1])


# --- inventory -----------------------------------------------------------------

def test_inventory_books_balance(result):
    inv = result.world.inventory_daily.sort_values(["product_id", "date"])
    assert (inv["stock_end"] == inv["stock_start"] - inv["units_sold"]).all()
    assert (inv["units_sold"] <= inv["demand_units"]).all()
    assert (inv["lost_units"] == inv["demand_units"] - inv["units_sold"]).all()
    nxt = inv.groupby("product_id").shift(-1)
    m = nxt["stock_start"].notna()
    assert (nxt.loc[m, "stock_start"] == inv.loc[m, "stock_end"] + nxt.loc[m, "received"]).all()


def test_baseline_stock_outs_are_rare(result, cfg):
    avail = category_availability(result.world.inventory_daily, result.world.products)
    assert avail["oos_view_share"].mean() < 0.03
    assert (avail["active_listings"] <= cfg.products_per_category).all()


def test_delayed_deliveries_cause_stock_outs():
    # 21 days: best-sellers carry ~14 days of stock, so a delay needs time to bite.
    cfg = SimConfig(start="2026-10-05", days=21, seed=7)
    base = build_world(cfg)
    top = set(base.products.query("category == 'electronics' and popularity_rank <= 5")["product_id"])
    hooks = InventoryHooks(delivery_delay=lambda prod, day: 10 if prod["product_id"] in top else 0)
    delayed = build_world(cfg, hooks)

    def peak_oos(world):
        a = category_availability(world.inventory_daily, world.products)
        return a.loc[a["category"] == "electronics", "oos_view_share"].max()

    def lost_top(world):
        inv = world.inventory_daily
        return inv.loc[inv["product_id"].isin(top), "lost_units"].sum()

    assert peak_oos(delayed) > peak_oos(base) + 0.10
    assert lost_top(delayed) > lost_top(base) + 500
    late = delayed.purchase_orders[delayed.purchase_orders["product_id"].isin(top)]
    assert len(late) and (late["actual_arrival"].isna() | (late["actual_arrival"] > late["expected_arrival"])).all()
    # other categories are untouched
    pd.testing.assert_frame_equal(
        delayed.inventory_daily.query("category != 'electronics'").reset_index(drop=True),
        base.inventory_daily.query("category != 'electronics'").reset_index(drop=True),
    )


# --- files ---------------------------------------------------------------------

def test_write_and_load_round_trip(result, tmp_path):
    out = result.write(tmp_path)
    for name in ["marketplace_hourly.csv", "events.csv", "products.csv", "sellers.csv",
                 "inventory_daily.csv", "purchase_orders.csv", "sim_config.json"]:
        assert (out / name).exists(), name

    raw = pd.read_csv(out / "marketplace_hourly.csv", nrows=5)
    assert all(re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:00:00Z", t) for t in raw["timestamp"])

    loaded = load_marketplace_hourly(out / "marketplace_hourly.csv")
    pd.testing.assert_frame_equal(loaded, result.marketplace_hourly, check_exact=False, atol=0.01)

    events = pd.read_csv(out / "events.csv")
    assert len(events) == 0 and "event_id" in events.columns
