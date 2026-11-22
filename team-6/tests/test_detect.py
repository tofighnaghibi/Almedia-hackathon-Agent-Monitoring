"""L4 Person A: seasonal baseline, prediction range and change detection."""

import numpy as np
import pandas as pd
import pytest

from graph import MetricEngine, MetricRegistry
from monitoring.baseline import BaselineConfig, build_baseline, load_series
from monitoring.detect import Detector, DetectorConfig, main
from simulator import SimConfig
from simulator.pipeline import build_dataset

CFG = SimConfig(start="2026-10-01", days=28, seed=42)  # scenarios start 2026-10-22 10:00
MONITOR_FROM = "2026-10-22"
CORE = ["orders", "sessions", "payment_success_rate", "search_ctr", "out_of_stock_rate", "ad_spend",
        "conversion_rate", "row_count"]


@pytest.fixture(scope="module")
def scenario():
    result, truth = build_dataset(CFG, "all")
    return MetricEngine(tables={"marketplace_hourly": result.marketplace_hourly}), truth["scenarios"]


@pytest.fixture(scope="module")
def clean():
    result, _ = build_dataset(CFG, "none")
    return MetricEngine(tables={"marketplace_hourly": result.marketplace_hourly})


def _found(det, s, metric):
    seg = s["affected_segment"]
    t0, t1 = pd.Timestamp(s["start_time"]), pd.Timestamp(s["end_time"])
    eps = [e for e in det.detect([metric], by=list(seg), start=MONITOR_FROM)
           if e.segment == seg and e.start <= t1 and e.end > t0]
    return max(eps, key=lambda e: abs(e.peak_z)) if eps else None


# --- calibration and false alarms on clean data ---------------------------------

def test_scores_are_calibrated_on_clean_data(clean):
    """Never overconfident; close to 1 unless the 1% precision floor applies."""
    det = Detector(clean)
    min_sigma = DetectorConfig().baseline.min_sigma
    for metric in ["orders", "sessions", "payment_success_rate", "revenue_per_session"]:
        for by in [[], ["country"], ["country", "platform"]]:
            sc = det.score(metric, by=by, start=MONITOR_FROM).dropna(subset=["z"])
            assert sc["z"].std() < 1.5, (metric, by, sc["z"].std())
            above_floor = sc[sc["sigma"] > 1.5 * min_sigma]
            if len(above_floor) > 100:
                assert above_floor["z"].std() > 0.6, (metric, by, above_floor["z"].std())


def test_few_alarms_on_clean_data(clean):
    det = Detector(clean)
    eps = []
    for by in [[], ["country"], ["country", "platform"]]:
        eps += det.detect([m for m in CORE if m != "out_of_stock_rate"], by=by, start=MONITOR_FROM)
    # 7 metrics x 17 segments x 3 windows x 168 hours scored; almost nothing flagged
    assert len(eps) <= 5, [(e.metric, e.segment, e.peak_z) for e in eps]


# --- every injected scenario is found where and when it happened ---------------

@pytest.mark.parametrize("scenario_id,metric,max_delay_h", [
    ("scn_de_android_payment", "payment_success_rate", 1),
    ("scn_de_bad_search", "search_ctr", 1),
    ("scn_fr_ad_exhaustion", "ad_spend", 1),
    ("scn_uk_planned_promo", "sessions", 1),
    ("scn_us_holiday", "sessions", 2),
    ("scn_de_ios_late_data", "row_count", 1),
    ("scn_electronics_stockout", "out_of_stock_rate", 24),
])
def test_scenario_is_detected(scenario, scenario_id, metric, max_delay_h):
    engine, truth = scenario
    s = next(x for x in truth if x["scenario_id"] == scenario_id)
    ep = _found(Detector(engine), s, metric)
    assert ep is not None, f"{scenario_id} not flagged on {metric}"
    delay = (ep.start - pd.Timestamp(s["start_time"])) / pd.Timedelta(hours=1)
    assert -24 <= delay <= max_delay_h
    assert abs(ep.peak_z) >= 5


def test_impact_follows_higher_is_better(scenario):
    engine, truth = scenario
    det = Detector(engine)
    pay = _found(det, next(x for x in truth if x["scenario_id"] == "scn_de_android_payment"), "payment_success_rate")
    promo = _found(det, next(x for x in truth if x["scenario_id"] == "scn_uk_planned_promo"), "sessions")
    assert (pay.direction, pay.impact) == ("down", "bad")
    assert (promo.direction, promo.impact) == ("up", "good")
    assert abs(pay.rel_change + 0.65) < 0.05  # injected: -65%


def test_late_data_is_held_back_not_reported(scenario):
    engine, truth = scenario
    s = next(x for x in truth if x["scenario_id"] == "scn_de_ios_late_data")
    t0 = pd.Timestamp(s["start_time"])
    sc = Detector(engine).score("sessions", by=["country"], start=t0 - pd.Timedelta(hours=1),
                                end=t0 + pd.Timedelta(hours=5), windows=[1])
    gap = sc[(sc["country"] == "DE") & (sc["window_start"] >= t0) & (sc["window_start"] < t0 + pd.Timedelta(hours=4))]
    assert len(gap) == 4 and (gap["z"] < -5).all()  # sessions look much lower ...
    assert not gap["is_anomaly"].any() and (gap["suppressed"] == "incomplete data").all()  # ... but it's the data


def test_episode_and_finding(scenario):
    engine, truth = scenario
    s = next(x for x in truth if x["scenario_id"] == "scn_de_android_payment")
    ep = _found(Detector(engine), s, "payment_success_rate")
    assert ep.windows == [1, 6, 24] and not ep.ongoing
    f = ep.to_finding()
    for key in ["finding_id", "fingerprint", "metric", "segment", "time_window", "expected", "actual", "change_size"]:
        assert key in f
    assert f["segment"] == {"country": "DE", "platform": "android"}
    assert set(f["expected"]) == {"value", "lower", "upper"}
    assert f["expected"]["lower"] <= f["expected"]["value"] <= f["expected"]["upper"]
    assert f["change_size"]["relative_pct"] < -50 and f["fingerprint"].startswith("payment_success_rate|")


# --- the baseline on small synthetic data ----------------------------------------

def _synthetic(weeks=5, spike_at=None, spike=1.0, drop_rows=None):
    ts = pd.date_range("2026-09-07", periods=24 * 7 * weeks, freq="h", tz="UTC")  # a Monday
    rng = np.random.default_rng(1)
    season = 1000 * (1 + 0.6 * np.sin(2 * np.pi * ts.hour / 24)) * (1 + 0.2 * (ts.dayofweek >= 5))
    a = rng.poisson(season).astype(float)
    if spike_at is not None:
        a[spike_at] *= spike
    df = pd.DataFrame({"timestamp": ts, "country": "DE", "a": a,
                       "hits": rng.binomial(a.astype(int), 0.02), "views": a})
    if drop_rows is not None:
        df = df.drop(index=drop_rows)
    reg = MetricRegistry()
    reg.add({"name": "a_total", "description": "a", "unit": "count", "calculation": "SUM(a)", "dimensions": ["country"]})
    reg.add({"name": "views", "description": "v", "unit": "count", "calculation": "SUM(views)", "dimensions": ["country"]})
    reg.add({"name": "hit_rate", "description": "r", "unit": "ratio",
             "calculation": "CAST(SUM(hits) AS DOUBLE) / NULLIF(SUM(views), 0)", "dimensions": ["country"]})
    reg.add({"name": "rows", "description": "n", "unit": "count", "calculation": "COUNT(*)", "dimensions": ["country"]})
    return MetricEngine(reg, {"marketplace_hourly": df}), ts


def test_expected_follows_the_weekly_pattern():
    engine, ts = _synthetic()
    s = load_series(engine, "a_total")
    b = build_baseline(s, 1, ts[24 * 7 * 4])
    last_week = slice(24 * 7 * 4, None)
    rel = b.actual[last_week, 0] / b.expected[last_week, 0] - 1
    assert np.abs(np.nanmedian(rel)) < 0.01 and np.nanpercentile(np.abs(rel), 95) < 0.1
    assert s.scale == "log" and b.sigma[-1, 0] >= np.sqrt(b.expected[-1, 0]) / (b.expected[-1, 0] + 0.5) - 1e-12


def test_rare_rate_uses_the_absolute_scale_and_ratio_windows_are_weighted():
    engine, ts = _synthetic()
    s = load_series(engine, "hit_rate")
    assert s.scale == "linear" and s.kind == "weighted"
    b = build_baseline(s, 24, ts[24 * 7 * 4])
    t = len(ts) - 1
    window = engine.value("hit_rate", ts[t - 23], ts[t] + pd.Timedelta(hours=1))
    assert b.actual[t, 0] == pytest.approx(window)  # exact ratio-of-sums over the window
    lo, hi = b.band(5)
    assert 0 <= lo[t, 0] < b.expected[t, 0] < hi[t, 0] <= 1


def test_single_hour_blip_is_not_reported_but_a_big_one_is():
    spike_hour = 24 * 7 * 4 + 50
    for factor, reported in [(1.25, False), (3.0, True)]:
        engine, ts = _synthetic(spike_at=spike_hour, spike=factor)
        sc = Detector(engine).score("a_total", start=ts[24 * 7 * 4], windows=[1])
        row = sc[sc["window_start"] == ts[spike_hour]].iloc[0]
        assert row["z"] > 5
        assert bool(row["is_anomaly"]) is reported
        if not reported:
            assert row["suppressed"] == "single hour"


def test_missing_rows_count_as_zero_for_completeness_metrics():
    gap = list(range(24 * 7 * 4 + 10, 24 * 7 * 4 + 13))
    engine, ts = _synthetic(drop_rows=gap)
    det = Detector(engine)
    rows = det.score("rows", start=ts[24 * 7 * 4], windows=[1])
    hit = rows[rows["window_start"].isin(ts[gap])]
    assert (hit["actual"] == 0).all() and hit["is_anomaly"].all()
    a = det.score("a_total", start=ts[24 * 7 * 4], windows=[1])
    assert a[a["window_start"].isin(ts[gap])]["suppressed"].eq("missing data").all()


def test_cli(tmp_path, capsys):
    build_dataset(CFG, "faults", out_dir=tmp_path)
    assert main(["--data", str(tmp_path), "--metrics", "payment_success_rate,ad_spend",
                 "--by", "country,platform", "--start", MONITOR_FROM]) == 0
    out = capsys.readouterr().out
    assert "payment_success_rate" in out and "country=DE, platform=android" in out
    assert main(["--data", str(tmp_path / "missing")]) == 2
