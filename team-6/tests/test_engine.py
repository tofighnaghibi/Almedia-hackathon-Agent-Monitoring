import numpy as np
import pandas as pd
import pytest

from graph import MetricDefinition, MetricDefinitionError, MetricEngine, MetricRegistry
from simulator import SimConfig
from simulator.pipeline import build_dataset

CFG = SimConfig(start="2026-10-05", days=14, seed=11)


@pytest.fixture(scope="module")
def data():
    result, truth = build_dataset(CFG, "all")
    return result.marketplace_hourly, truth


@pytest.fixture(scope="module")
def engine(data):
    hourly, _ = data
    return MetricEngine(tables={"marketplace_hourly": hourly})


def _window(df, start, end, **seg):
    m = (df["timestamp"] >= pd.Timestamp(start, tz="UTC")) & (df["timestamp"] < pd.Timestamp(end, tz="UTC"))
    for k, v in seg.items():
        m &= df[k] == v
    return df[m]


# --- registry ------------------------------------------------------------------

def test_seed_metrics_load_and_all_validate(engine):
    reg = MetricRegistry.default()
    assert len(reg) >= 30
    for name in ["orders", "payment_success_rate", "search_ctr", "out_of_stock_rate", "row_count", "refund_rate"]:
        assert name in reg
    failures = {m.name: r.errors for m in reg for r in [engine.validate(m)] if not r.ok}
    assert failures == {}


def test_registry_yaml_round_trip(tmp_path):
    reg = MetricRegistry.default()
    again = MetricRegistry.load(reg.to_yaml(tmp_path / "m.yaml"))
    assert [m.to_dict() for m in again] == [m.to_dict() for m in reg]


@pytest.mark.parametrize(
    "change,message",
    [
        ({"name": "Orders Total"}, "snake_case"),
        ({"unit": "percent"}, "unit"),
        ({"calculation": "SUM(orders); DROP TABLE marketplace_hourly"}, "single aggregate expression"),
        ({"calculation": "(SELECT SUM(orders) FROM marketplace_hourly)"}, "single aggregate expression"),
        ({"business_weight": 3}, "business_weight"),
        ({"owner": "me"}, "unknown fields"),
    ],
)
def test_structural_validation(change, message):
    base = {"name": "x_metric", "description": "d", "unit": "count", "calculation": "SUM(orders)",
            "dimensions": ["country"]}
    with pytest.raises(MetricDefinitionError, match=message):
        MetricDefinition.from_dict({**base, **change})


def test_add_with_engine_rejects_broken_metrics(engine):
    reg = MetricRegistry.default()
    with pytest.raises(MetricDefinitionError, match="already exists"):
        reg.add(reg.get("orders"))
    bad_column = {"name": "bad_col", "description": "d", "unit": "count",
                  "calculation": "SUM(no_such_column)", "dimensions": ["country"]}
    with pytest.raises(MetricDefinitionError, match="failed validation"):
        reg.add(bad_column, engine=engine)
    bad_dim = {"name": "bad_dim", "description": "d", "unit": "count",
               "calculation": "SUM(orders)", "dimensions": ["seller"]}
    with pytest.raises(MetricDefinitionError, match="not columns"):
        reg.add(bad_dim, engine=engine)
    not_aggregate = {"name": "not_agg", "description": "d", "unit": "count",
                     "calculation": "orders", "dimensions": ["country"]}
    with pytest.raises(MetricDefinitionError, match="failed validation"):
        reg.add(not_aggregate, engine=engine)
    assert "bad_col" not in reg and "not_agg" not in reg


def test_new_metric_can_be_added_and_used(engine):
    """The L3 entry point: a new metric with only a definition and a calculation."""
    engine.registry.add(
        {"name": "android_order_share", "description": "Share of orders placed on Android",
         "unit": "ratio",
         "calculation": "CAST(SUM(orders) FILTER (WHERE platform = 'android') AS DOUBLE) / NULLIF(SUM(orders), 0)",
         "dimensions": ["country", "category"], "higher_is_better": None, "business_weight": 0.3},
        engine=engine,
    )
    v = engine.value("android_order_share", segment={"country": "DE"})
    assert 0.2 < v < 0.6
    engine.registry.remove("android_order_share")


def test_validation_warns_about_suspicious_values(engine):
    rep = engine.validate({"name": "orders_per_session_wrong_unit", "description": "d", "unit": "ratio",
                           "calculation": "CAST(SUM(searches) AS DOUBLE) / NULLIF(SUM(sessions), 0)",
                           "dimensions": ["country"]})
    assert rep.ok and any("outside 0..1" in w for w in rep.warnings)
    assert rep.columns_used == ["searches", "sessions"]


def test_additivity_flag():
    reg = MetricRegistry.default()
    assert reg.get("orders").is_additive and reg.get("product_clicks").is_additive
    assert not reg.get("payment_success_rate").is_additive
    assert not reg.get("avg_active_listings").is_additive


# --- computing -------------------------------------------------------------------

def test_totals_match_pandas(engine, data):
    hourly, _ = data
    w = _window(hourly, "2026-10-10", "2026-10-12", country="DE", platform="android")
    v = engine.value("orders", "2026-10-10", "2026-10-12", {"country": "DE", "platform": "android"})
    assert v == w["orders"].sum()
    assert engine.value("revenue", "2026-10-10", "2026-10-12", {"country": "DE", "platform": "android"}) == pytest.approx(w["revenue"].sum())


def test_ratios_are_ratio_of_sums(engine, data):
    hourly, _ = data
    w = _window(hourly, "2026-10-10", "2026-10-11")
    expected = w["orders"].sum() / w["payment_attempts"].sum()
    assert engine.value("payment_success_rate", "2026-10-10", "2026-10-11") == pytest.approx(expected)
    mean_of_ratios = (w["orders"] / w["payment_attempts"]).mean()
    assert expected != pytest.approx(mean_of_ratios, rel=1e-6)


def test_window_is_half_open(engine, data):
    hourly, _ = data
    one_hour = engine.value("sessions", "2026-10-10T05:00Z", "2026-10-10T06:00Z")
    assert one_hour == _window(hourly, "2026-10-10T05:00", "2026-10-10T06:00")["sessions"].sum()
    assert engine.compute("sessions", "2026-10-10T05:00Z", "2026-10-10T06:00Z")["n_rows"].iloc[0] == 48


def test_breakdown_adds_up_and_respects_segment(engine):
    total = engine.value("orders", "2026-10-10", "2026-10-11")
    by = engine.compute("orders", "2026-10-10", "2026-10-11", by=["country", "platform"])
    assert len(by) == 12 and by["value"].sum() == total
    de = engine.compute("orders", "2026-10-10", "2026-10-11", segment={"country": ["DE", "FR"]}, by=["country"])
    assert list(de["country"]) == ["DE", "FR"]


def test_time_grains(engine):
    daily = engine.compute("orders", "2026-10-06", "2026-10-13", grain="day")
    assert len(daily) == 7 and (daily["period"].dt.hour == 0).all()
    assert str(daily["period"].dt.tz) == "UTC"
    hourly = engine.compute("orders", "2026-10-06", "2026-10-07", by=["platform"], grain="hour")
    assert len(hourly) == 3 * 24
    assert daily["value"].iloc[0] == hourly["value"].sum()
    weekly = engine.compute("orders", "2026-10-05", "2026-10-19", grain="week")
    assert len(weekly) == 2 and weekly["value"].sum() == engine.value("orders", "2026-10-05", "2026-10-19")


def test_missing_data_is_visible(engine, data):
    _, truth = data
    late = next(s for s in truth["scenarios"] if s["scenario_id"] == "scn_de_ios_late_data")
    start = pd.Timestamp(late["start_time"])
    res = engine.compute("sessions", start - pd.Timedelta(hours=2), start + pd.Timedelta(hours=6),
                         segment={"country": "DE", "platform": "ios"}, grain="hour")
    gap = res[res["n_rows"] == 0]
    assert len(gap) == 4 and gap["value"].isna().all()
    assert (res.loc[res["n_rows"] > 0, "value"] > 0).all()


def test_injected_fault_is_visible(engine, data):
    _, truth = data
    s = next(s for s in truth["scenarios"] if s["scenario_id"] == "scn_de_android_payment")
    start, end = pd.Timestamp(s["start_time"]), pd.Timestamp(s["end_time"])
    by = engine.compute("payment_success_rate", start, end, segment={"country": "DE"}, by=["platform"])
    rates = dict(zip(by["platform"], by["value"]))
    assert rates["android"] < 0.5 < 0.9 < rates["ios"]


def test_compute_many_matches_compute(engine):
    many = engine.compute_many(["orders", "sessions", "conversion_rate"], "2026-10-06", "2026-10-09",
                               by=["country"], grain="day")
    one = engine.compute("conversion_rate", "2026-10-06", "2026-10-09", by=["country"], grain="day")
    np.testing.assert_allclose(many["conversion_rate"], one["value"])
    np.testing.assert_allclose(many["orders"] / many["sessions"], many["conversion_rate"])


def test_disallowed_breakdowns_and_bad_input(engine):
    with pytest.raises(ValueError, match="cannot be sliced"):
        engine.compute("avg_active_listings", by=["platform"])
    with pytest.raises(ValueError, match="grain"):
        engine.compute("orders", grain="minute")
    with pytest.raises(ValueError, match="end must be after start"):
        engine.compute("orders", "2026-10-10", "2026-10-09")
    with pytest.raises(KeyError):
        engine.compute("no_such_metric")


def test_empty_window_gives_nan(engine):
    assert np.isnan(engine.value("orders", "2027-01-01", "2027-01-02"))


def test_from_dir(tmp_path):
    result, _ = build_dataset(SimConfig(start="2026-10-05", days=8, seed=2), "none", out_dir=tmp_path)
    e = MetricEngine.from_dir(tmp_path)
    assert e.value("orders") == result.marketplace_hourly["orders"].sum()
    lo, hi = e.time_range()
    assert lo == pd.Timestamp("2026-10-05", tz="UTC") and hi == pd.Timestamp("2026-10-12T23:00", tz="UTC")
    assert e.dimension_values("platform") == ["android", "ios", "web"]
