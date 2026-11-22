"""L3 Person A: candidate connections for a new metric."""

import numpy as np
import pandas as pd
import pytest

from extension import discover_connections
from extension.__main__ import main
from graph import MetricEngine, MetricGraph, MetricRegistry
from graph.__main__ import DEFAULT_GRAPH
from simulator import SimConfig
from simulator.pipeline import build_dataset

CFG = SimConfig(start="2026-10-05", days=14, seed=21)


@pytest.fixture(scope="module")
def hourly():
    result, _ = build_dataset(CFG, "all")
    return result.marketplace_hourly


def _without(name, hourly):
    """Engine and graph in which `name` is unknown, plus its definition."""
    reg = MetricRegistry.default()
    defn = reg.get(name)
    reg.remove(name)
    graph = MetricGraph.load(DEFAULT_GRAPH)
    for u, v, k in [(u, v, k) for u, v, k in graph.graph.edges(keys=True) if name in (u, v)]:
        graph.graph.remove_edge(u, v, k)
    if name in graph.graph:
        graph.graph.remove_node(name)
    return defn, MetricEngine(reg, {"marketplace_hourly": hourly}), graph


def test_rediscovers_refund_rate(hourly):
    """The work plan's L3 check: a metric with only a definition gets its place."""
    defn, engine, graph = _without("refund_rate", hourly)
    rep = discover_connections(defn.to_dict(), engine, graph)
    best = rep.best_formula()
    assert best.edge.formula == "refunds = orders * refund_rate"
    assert best.edge.from_metric == "refund_rate" and best.edge.to_metric == "refunds"
    assert best.edge.status == "uncertain" and best.edge.source == "auto_discovered"
    assert best.details["segment_check"]["status"] == "pass" and best.score >= 0.9
    assert [c.edge.dimension for c in rep.by_method("breakdown")] == ["country", "platform", "category"]
    assert rep.related_metrics[0][0] in ("orders", "refunds")


@pytest.mark.parametrize(
    "name,formula",
    [
        ("conversion_rate", "orders = sessions * conversion_rate"),
        ("payment_success_rate", "orders = payment_attempts * payment_success_rate"),
        ("product_clicks", "product_clicks = search_clicks + recommendation_clicks + ad_clicks"),
        ("return_on_ad_spend", "revenue = ad_spend * return_on_ad_spend"),
    ],
)
def test_best_formula_is_the_definition(hourly, name, formula):
    defn, engine, graph = _without(name, hourly)
    rep = discover_connections(defn, engine, graph)
    assert rep.best_formula().edge.formula == formula
    scores = [c.score for c in rep.by_method("formula")]
    assert scores == sorted(scores, reverse=True)


def test_complement_and_alternatives(hourly):
    engine = MetricEngine(MetricRegistry.default(), {"marketplace_hourly": hourly})
    rep = discover_connections(
        {"name": "cart_abandonment_rate", "description": "d", "unit": "ratio",
         "calculation": "1 - CAST(SUM(checkouts) AS DOUBLE) / NULLIF(SUM(cart_adds), 0)",
         "dimensions": ["country", "platform", "category"]},
        engine, MetricGraph.load(DEFAULT_GRAPH))
    assert rep.best_formula().edge.formula == "cart_abandonment_rate = 1 - checkout_rate"

    rep = discover_connections(
        {"name": "organic_clicks", "description": "d", "unit": "count",
         "calculation": "SUM(search_clicks + recommendation_clicks)", "dimensions": ["country", "platform"]},
        engine)
    formulas = [c.edge.formula for c in rep.by_method("formula")]
    assert formulas[0] == "organic_clicks = search_clicks + recommendation_clicks"
    assert "organic_clicks = product_clicks - ad_clicks" in formulas
    assert rep.by_method("formula")[1].details["alternative_to"] == formulas[0]


def test_duplicates_and_broken_definitions(hourly):
    engine = MetricEngine(MetricRegistry.default(), {"marketplace_hourly": hourly})
    dup = discover_connections({"name": "completed_orders", "description": "d", "unit": "count",
                                "calculation": "SUM( orders )", "dimensions": ["country"]}, engine)
    assert dup.duplicates == ["orders"]
    assert all("completed_orders = orders" != c.edge.formula for c in dup.candidates)

    bad = discover_connections({"name": "broken", "description": "d", "unit": "count",
                                "calculation": "SUM(no_such_column)", "dimensions": ["country"]}, engine)
    assert not bad.validation.ok and bad.candidates == []


def test_no_formula_for_a_non_arithmetic_metric(hourly):
    defn, engine, graph = _without("avg_active_listings", hourly)
    rep = discover_connections(defn, engine, graph)
    assert rep.by_method("formula") == []
    assert any("no arithmetic formula" in n for n in rep.notes)


def test_stock_out_correlation_is_found(hourly):
    """In data with the supply-chain fault, fewer listings go with more out-of-stock views."""
    defn, engine, graph = _without("out_of_stock_rate", hourly)
    rep = discover_connections(defn, engine, graph)
    corr = {c.edge.from_metric: c for c in rep.by_method("correlation")}
    assert "avg_active_listings" in corr
    assert corr["avg_active_listings"].details["r"] < -0.3
    assert corr["avg_active_listings"].edge.type == "possible_cause"


def test_formula_linked_metrics_are_not_reported_as_causes(hourly):
    defn, engine, graph = _without("conversion_rate", hourly)
    rep = discover_connections(defn, engine, graph)
    linked = {"orders", "sessions", "payment_attempts", "checkouts", "cart_adds", "revenue"}
    assert not linked & {c.edge.from_metric for c in rep.by_method("correlation")}


def test_lead_is_detected_on_synthetic_data():
    """b follows a three hours later; discovery must find a -> b with lag 3."""
    rng = np.random.default_rng(0)
    ts = pd.date_range("2026-10-05", periods=24 * 21, freq="h", tz="UTC")
    rows = []
    for seg in ["DE", "FR", "UK"]:
        season = 1 + 0.5 * np.sin(2 * np.pi * ts.hour / 24)
        shock = np.exp(rng.normal(0, 0.2, len(ts)))
        a = 1000 * season * shock
        b = 500 * season * np.roll(shock, 3) * np.exp(rng.normal(0, 0.05, len(ts)))
        rows.append(pd.DataFrame({"timestamp": ts, "country": seg, "a": a, "b": b}))
    df = pd.concat(rows, ignore_index=True)
    reg = MetricRegistry()
    reg.add({"name": "metric_a", "description": "a", "unit": "count", "calculation": "SUM(a)",
             "dimensions": ["country"]})
    engine = MetricEngine(reg, {"marketplace_hourly": df})
    rep = discover_connections({"name": "metric_b", "description": "b", "unit": "count",
                                "calculation": "SUM(b)", "dimensions": ["country"]}, engine)
    (c,) = rep.by_method("correlation")
    assert (c.edge.from_metric, c.edge.to_metric, c.edge.lag_hours) == ("metric_a", "metric_b", 3)
    assert c.details["r"] > 0.8 and c.details["direction"] == "lead"


def test_candidates_fit_into_the_graph(hourly):
    engine = MetricEngine(MetricRegistry.default(), {"marketplace_hourly": hourly})
    graph = MetricGraph.load(DEFAULT_GRAPH)
    rep = discover_connections({"name": "orders_per_search", "description": "d", "unit": "rate",
                                "calculation": "CAST(SUM(orders) AS DOUBLE) / NULLIF(SUM(searches), 0)",
                                "dimensions": ["country", "platform", "category"]}, engine, graph)
    assert rep.best_formula().edge.formula == "orders = searches * orders_per_search"
    for edge in rep.edges():
        graph.add_edge(edge)  # Person B's graph accepts every candidate
    assert "orders_per_search" in graph.graph


def test_cli(tmp_path, capsys):
    build_dataset(SimConfig(start="2026-10-05", days=8, seed=3), "none", out_dir=tmp_path / "data")
    saved = tmp_path / "candidates.yaml"
    code = main(["--metric", "extension/examples/new_metrics.yaml", "--data", str(tmp_path / "data"),
                 "--save", str(saved)])
    assert code == 0
    out = capsys.readouterr().out
    assert "cart_abandonment_rate = 1 - checkout_rate" in out
    assert "ad_clicks = product_clicks * ad_click_share" in out
    assert len(MetricGraph.load(saved).edges()) > 0
    assert main(["--metric", "extension/examples/new_metrics.yaml", "--data", str(tmp_path / "nope")]) == 2
