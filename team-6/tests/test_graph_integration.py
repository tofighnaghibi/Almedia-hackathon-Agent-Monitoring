"""L2 joint test: the seed graph checked with the real engine on simulator data."""

import pandas as pd
import pytest

from graph import GraphEdge, MetricEngine, MetricGraph, MetricRegistry, run_checks
from graph.__main__ import DEFAULT_GRAPH, main
from graph.bridge import checker_metric_defs, engine_compute_fn, weight_metric_for
from simulator import SimConfig
from simulator.pipeline import build_dataset

CFG = SimConfig(start="2026-10-05", days=8, seed=5)


@pytest.fixture(scope="module")
def baseline_dir(tmp_path_factory):
    out = tmp_path_factory.mktemp("baseline")
    build_dataset(CFG, "none", out_dir=out)
    return out


@pytest.fixture(scope="module")
def engine(baseline_dir):
    return MetricEngine.from_dir(baseline_dir)


def test_every_seed_edge_passes_on_clean_data(engine):
    graph = MetricGraph.load(DEFAULT_GRAPH)
    expected = len({(e.to_metric, e.key) for e in graph.edges() if e.type in ("formula", "breakdown")})
    assert expected >= 23
    report = run_checks(engine, graph)
    assert report.unknown_metrics == []
    assert report.counts == {"pass": expected, "fail": 0, "skipped": 0}
    assert all(e.status == "confirmed" for e in graph.edges() if e.type in ("formula", "breakdown"))
    assert all(e.status == "uncertain" for e in graph.edges("possible_cause"))


def test_wrong_formula_is_rejected(engine):
    graph = MetricGraph()
    graph.add_edge(GraphEdge("sessions", "orders", "formula",
                             formula="orders = sessions * payment_success_rate"))
    report = run_checks(engine, graph)
    (res,) = report.results.values()
    assert res.outcome == "fail" and not report.ok
    assert graph.edges()[0].status == "rejected"


def test_unknown_metrics_are_reported(engine):
    graph = MetricGraph()
    graph.add_edge(GraphEdge("orders", "revenue", "formula", formula="revenue = orders * aov"))
    report = run_checks(engine, graph)
    assert report.unknown_metrics == ["aov"] and not report.ok


def test_weight_metric_is_derived_from_the_denominator():
    reg = MetricRegistry.default()
    assert weight_metric_for("payment_success_rate", reg) == "payment_attempts"
    assert weight_metric_for("conversion_rate", reg) == "sessions"
    assert weight_metric_for("out_of_stock_rate", reg) == "product_clicks"
    assert weight_metric_for("avg_active_listings", reg) is None
    defs = checker_metric_defs(reg)
    assert "weight_metric" not in defs["orders"]
    assert defs["orders"]["is_additive"] and defs["revenue"]["is_additive"]
    # currency / count units that must not be summed across slices
    assert not defs["average_order_value"]["is_additive"]
    assert not defs["avg_active_listings"]["is_additive"]


def test_non_additive_currency_metric_breakdown_is_weighted(engine):
    """average_order_value has unit currency_eur but must be weighted by orders, not summed."""
    graph = MetricGraph()
    graph.add_edge(GraphEdge("average_order_value", "average_order_value", "breakdown", dimension="country"))
    (res,) = run_checks(engine, graph).results.values()
    assert res.outcome == "pass", res.detail


def test_compute_fn_has_the_checker_signature(engine):
    compute = engine_compute_fn(engine)
    df = compute("orders", "2026-10-06", "2026-10-07", group_by=["country"])
    assert list(df.columns) == ["country", "timestamp", "value", "n_rows"]
    assert len(df) == 4 * 24
    assert df["value"].sum() == engine.value("orders", "2026-10-06", "2026-10-07")
    assert str(pd.to_datetime(df["timestamp"]).dt.tz) == "UTC"


def test_cli(baseline_dir, tmp_path, capsys):
    saved = tmp_path / "checked.yaml"
    assert main(["--data", str(baseline_dir), "--save", str(saved)]) == 0
    out = capsys.readouterr().out
    assert " passed, 0 failed, 0 skipped" in out and "payment_success_rate [confirmed]" in out
    assert all(e.status == "confirmed" for e in MetricGraph.load(saved).edges("formula"))
    assert main(["--data", str(tmp_path / "missing")]) == 2
