"""L5 Person A: following the graph, quantifying contributions, localizing segments."""

import json

import numpy as np
import pandas as pd
import pytest

from graph import GraphEdge, MetricEngine, MetricGraph
from graph.__main__ import DEFAULT_GRAPH
from investigation.compare import Window, compare, compare_total
from investigation.decompose import decompose, shapley
from investigation.drilldown import drill_down, split
from investigation.investigate import investigate, main
from simulator import SimConfig
from simulator.pipeline import build_dataset

CFG = SimConfig(start="2026-10-01", days=28, seed=42)  # scenarios from 2026-10-22 10:00


@pytest.fixture(scope="module")
def world():
    result, truth = build_dataset(CFG, "all")
    engine = MetricEngine(tables={"marketplace_hourly": result.marketplace_hourly})
    return engine, MetricGraph.load(DEFAULT_GRAPH), {s["scenario_id"]: s for s in truth["scenarios"]}


def _window(s):
    # exactly as recorded; the next scenario can start right at end_time
    return Window.of(s["start_time"], s["end_time"])


# --- Shapley contributions ------------------------------------------------------------

def test_shapley_adds_up_for_any_formula():
    base, act = {"a": 100.0, "b": 0.9, "c": 5.0}, {"a": 80.0, "b": 0.5, "c": 7.0}
    for formula in ["y = a * b", "y = a / b", "y = a + b + c", "y = a * b - c", "y = 1 - b"]:
        from graph.formulas import eval_formula, parse_formula
        _, inputs, code = parse_formula(formula)
        phi = shapley(formula, {k: base[k] for k in inputs}, {k: act[k] for k in inputs})
        total = eval_formula(code, act) - eval_formula(code, base)
        assert sum(phi.values()) == pytest.approx(total)
    phi = shapley("y = a + b + c", base, act)
    assert phi == pytest.approx({"a": -20.0, "b": -0.4, "c": 2.0})  # sums: each its own change


def test_baseline_keeps_the_graph_identities(world):
    engine, _, sc = world
    w = _window(sc["scn_de_android_payment"])
    seg = {"country": "DE"}
    o, a, p = (compare_total(engine, m, w, seg) for m in ["orders", "payment_attempts", "payment_success_rate"])
    assert o[0] == pytest.approx(a[0] * p[0]) and o[1] == pytest.approx(a[1] * p[1])  # actual and baseline


# --- following the graph ------------------------------------------------------------------

@pytest.mark.parametrize("scenario_id,metric,segment,driver,min_share", [
    ("scn_de_android_payment", "orders", {"country": "DE"}, "payment_success_rate", 0.9),
    ("scn_de_bad_search", "orders", {"country": "DE"}, "search_ctr", 0.4),
    ("scn_fr_ad_exhaustion", "ad_spend", {"country": "FR"}, "ad_impressions", 0.9),
])
def test_walk_finds_the_driver(world, scenario_id, metric, segment, driver, min_share):
    engine, graph, sc = world
    tree = decompose(engine, graph, metric, _window(sc[scenario_id]), segment)
    top = tree.drivers()[0]
    assert top.metric == driver and top.share >= min_share
    # contributions of every decomposed node add up to its change
    for n in tree.walk():
        if n.children:
            assert sum(c.contribution for c in n.children) == pytest.approx(n.change, rel=1e-6)


def test_search_fault_keeps_searches_normal(world):
    """'Customers keep searching, but fewer buy': searches carry almost none of it."""
    engine, graph, sc = world
    tree = decompose(engine, graph, "orders", _window(sc["scn_de_bad_search"]), {"country": "DE"})
    nodes = {n.metric: n for n in tree.walk()}
    assert abs(nodes["searches"].share) < 0.1 and nodes["search_ctr"].share > 0.4
    assert nodes["product_clicks"].formula.startswith("product_clicks = search_clicks")


def test_formula_that_does_not_hold_is_skipped(world):
    engine, _, sc = world
    g = MetricGraph()
    g.add_edge(GraphEdge("sessions", "orders", "formula", formula="orders = sessions * payment_success_rate"))
    tree = decompose(engine, g, "orders", _window(sc["scn_de_android_payment"]), {"country": "DE"})
    assert tree.children == [] and "does not hold" in tree.note


# --- localizing -----------------------------------------------------------------------------

@pytest.mark.parametrize("scenario_id,metric,expected_segment", [
    ("scn_de_android_payment", "payment_success_rate", {"country": "DE", "platform": "android"}),
    ("scn_de_bad_search", "search_ctr", {"country": "DE"}),
    ("scn_fr_ad_exhaustion", "ad_impressions", {"country": "FR"}),
    ("scn_electronics_stockout", "out_of_stock_rate", {"category": "electronics"}),
    ("scn_uk_planned_promo", "sessions", {"country": "UK"}),
])
def test_drill_down_finds_the_injected_segment(world, scenario_id, metric, expected_segment):
    engine, _, sc = world
    r = drill_down(engine, metric, _window(sc[scenario_id]))
    assert r.segment == expected_segment
    assert r.share_of_root > 0.8
    assert "spread" in r.steps[-1].stop_reason


def test_ratio_split_is_exact_and_separates_rate_from_mix(world):
    engine, _, sc = world
    w = _window(sc["scn_de_android_payment"])
    t = split(engine, "payment_success_rate", w, {"country": "DE"}, "platform")
    a, b = compare_total(engine, "payment_success_rate", w, {"country": "DE"})
    assert t["contribution"].sum() == pytest.approx(a - b, rel=1e-9)
    android = t.set_index("value").loc["android"]
    assert android["share"] > 0.9 and abs(android["mix_effect"]) < 0.05 * abs(android["rate_effect"])


def test_additive_split_is_each_slice_change(world):
    engine, _, sc = world
    w = _window(sc["scn_uk_planned_promo"])
    t = split(engine, "sessions", w, {}, "country")
    total = compare(engine, "sessions", w)
    assert t["contribution"].sum() == pytest.approx(total["actual"][0] - total["baseline"][0])


# --- the whole investigation -------------------------------------------------------------------

def test_investigation_output(world):
    engine, graph, sc = world
    w = _window(sc["scn_de_android_payment"])
    res = investigate(engine, graph, "orders", w.start, w.end, segment={"country": "DE"})
    assert res.drivers[0].metric == "payment_success_rate"
    assert res.localization["payment_success_rate"].segment == {"country": "DE", "platform": "android"}
    actions = [t["action"] for t in res.trail]
    assert actions[0] == "walk_formula" and "drill_dimension" in actions and actions[-1] == "quantify_impact"
    assert [t["step"] for t in res.trail] == list(range(1, len(actions) + 1))
    assert all({"step", "action", "query", "observation"} <= set(t) for t in res.trail)
    ev = res.evidence[0]
    assert ev["type"] == "metric_drop" and ev["segment"] == {"country": "DE", "platform": "android"}
    assert ev["contribution_pct"] > 90 and ev["actual"] < 0.5 < 0.9 < ev["expected"]
    assert res.business_impact["lost_orders"] > 1000 and res.business_impact["lost_revenue_eur"] > 0
    json.dumps(res.to_dict())  # serializable for L6
    assert "localized to country=DE, platform=android" in res.summary


def test_leaf_metric_is_investigated_directly(world):
    engine, graph, sc = world
    w = _window(sc["scn_de_bad_search"])
    res = investigate(engine, graph, "search_ctr", w.start, w.end)
    assert res.drivers[0].metric == "search_ctr"
    assert res.localization["search_ctr"].segment == {"country": "DE"}


def test_cli(tmp_path, capsys):
    build_dataset(CFG, "faults", out_dir=tmp_path)
    assert main(["--data", str(tmp_path), "--metric", "payment_success_rate",
                 "--segment", "country=DE"]) == 0
    out = capsys.readouterr().out
    assert "strongest anomaly" in out and "platform=android" in out
    assert main(["--data", str(tmp_path / "missing"), "--metric", "orders"]) == 2
