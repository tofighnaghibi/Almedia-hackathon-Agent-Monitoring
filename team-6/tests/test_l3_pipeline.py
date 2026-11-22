"""L3 joint test: discovery (A) + validation and graph extension (B) on real data."""

import warnings

import pytest

import extension.pipeline as pipeline
from extension import add_new_metric
from extension.__main__ import main as extension_main
from graph import MetricEngine, MetricGraph, MetricRegistry, run_checks
from graph.__main__ import DEFAULT_GRAPH
from graph.__main__ import main as graph_main
from graph.registry import MetricDefinitionError
from simulator import SimConfig
from simulator.pipeline import build_dataset

CFG = SimConfig(start="2026-10-05", days=14, seed=21)

ORDERS_PER_SEARCH = {
    "name": "orders_per_search", "description": "Orders per search", "unit": "rate",
    "calculation": "CAST(SUM(orders) AS DOUBLE) / NULLIF(SUM(searches), 0)",
    "dimensions": ["country", "platform", "category"],
}


@pytest.fixture(scope="module")
def data_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("scenarios")
    build_dataset(CFG, "all", out_dir=out)
    return out


@pytest.fixture(autouse=True)
def _quiet():
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", RuntimeWarning)
        yield


def _added(res, kind):
    return {(e.get("formula") or e.get("dimension") or f"{e['from']} -> {e['to']}"): e["status"]
            for e in res.extension.added_edges if e["type"] == kind}


def test_new_metric_is_validated_registered_and_added(data_dir):
    engine = MetricEngine.from_dir(data_dir)
    graph = MetricGraph.load(DEFAULT_GRAPH)
    res = add_new_metric(ORDERS_PER_SEARCH, engine, graph)

    assert _added(res, "formula")["orders = searches * orders_per_search"] == "confirmed"
    # ratio breakdowns are checked (weighted by searches), not skipped
    assert _added(res, "breakdown") == {"country": "confirmed", "platform": "confirmed", "category": "confirmed"}
    assert "orders_per_search" in engine.registry and "orders_per_search" in graph.graph
    assert engine.value("orders_per_search") > 0

    report = run_checks(engine, graph)  # the extended graph still checks out
    assert report.ok, report.counts
    assert report.counts["pass"] >= 26 + 4


def test_candidates_using_registered_metrics_are_not_rejected_as_unknown(data_dir):
    reg = MetricRegistry.default()
    defn = reg.get("out_of_stock_rate").to_dict()
    reg.remove("out_of_stock_rate")
    engine = MetricEngine.from_dir(data_dir, reg)
    graph = MetricGraph.load(DEFAULT_GRAPH)
    for u, v, k in [(u, v, k) for u, v, k in graph.graph.edges(keys=True) if "out_of_stock_rate" in (u, v)]:
        graph.graph.remove_edge(u, v, k)
    graph.graph.remove_node("out_of_stock_rate")

    res = add_new_metric(defn, engine, graph)
    assert _added(res, "formula")["out_of_stock_views = product_clicks * out_of_stock_rate"] == "confirmed"
    causes = _added(res, "possible_cause")
    assert causes.get("avg_active_listings -> out_of_stock_rate") == "uncertain"  # a hypothesis, kept
    assert not any("unknown metric" in r["evidence"] for r in res.extension.rejected_edges)


def test_bad_or_duplicate_metrics_leave_the_registry_unchanged(data_dir):
    engine = MetricEngine.from_dir(data_dir)
    graph = MetricGraph.load(DEFAULT_GRAPH)
    before = engine.registry.names()
    with pytest.raises(MetricDefinitionError, match="already registered"):
        add_new_metric(engine.registry.get("orders"), engine, graph)
    with pytest.raises(MetricDefinitionError, match="failed validation"):
        add_new_metric({"name": "broken", "description": "d", "unit": "count",
                        "calculation": "SUM(nope)", "dimensions": ["country"]}, engine, graph)
    assert engine.registry.names() == before


def test_registry_is_rolled_back_if_extension_fails(data_dir, monkeypatch):
    engine = MetricEngine.from_dir(data_dir)

    def boom(*args, **kwargs):
        raise RuntimeError("extension failed")

    monkeypatch.setattr(pipeline, "extend_graph", boom)
    with pytest.raises(RuntimeError):
        add_new_metric(ORDERS_PER_SEARCH, engine, MetricGraph.load(DEFAULT_GRAPH))
    assert "orders_per_search" not in engine.registry


def test_cli_apply_then_check_the_extended_graph(data_dir, tmp_path, capsys):
    g, m = tmp_path / "graph.yaml", tmp_path / "metrics.yaml"
    code = extension_main(["--metric", "extension/examples/new_metrics.yaml", "--data", str(data_dir),
                           "--apply", "--save-graph", str(g), "--save-metrics", str(m)])
    assert code == 0
    out = capsys.readouterr().out
    assert out.count("-> registered") == 4
    assert {"orders_per_search", "cart_abandonment_rate", "ad_click_share", "organic_clicks"} <= set(
        MetricRegistry.load(m).names())
    assert graph_main(["--data", str(data_dir), "--graph", str(g), "--metrics", str(m)]) == 0
    assert " passed, 0 failed, 0 skipped" in capsys.readouterr().out
