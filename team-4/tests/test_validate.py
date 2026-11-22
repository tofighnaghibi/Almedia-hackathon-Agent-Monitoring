from pathlib import Path

import yaml

from brain.graph import MetricGraph, main
from brain.metric_spec import spec_from_dict
from brain.validate import validate_graph

METRICS = Path(__file__).resolve().parents[1] / "brain" / "metrics"


def raw_specs():
    out = []
    for f in sorted(METRICS.glob("*.yaml")):
        out.extend(yaml.safe_load(f.read_text(encoding="utf-8"))["metrics"])
    return out


def graph_with(change):
    raws = raw_specs()
    for r in raws:
        change(r)
    return MetricGraph.from_specs([spec_from_dict(r) for r in raws])


def failures(checks):
    return [(c.metric, c.check) for c in checks if c.status == "fail"]


def test_core_graph_validates_on_contract_data(graph, store):
    checks = validate_graph(graph, store, days=7)
    assert failures(checks) == []
    passed = {(c.metric, c.check) for c in checks if c.status == "pass"}
    assert {("orders", "identity"), ("gmv", "identity"), ("conversion_rate", "identity"),
            ("contribution", "additivity"), ("payment_success_rate", "bounds")} <= passed
    assert graph.status["orders"] == "validated"
    assert all(graph.status[e.id] == "validated" for e in graph.out_edges("gmv", "formula"))


def test_wrong_decomposition_fails(store):
    def change(r):
        if r["name"] == "gmv":
            r["decomposition"] = "orders * aov * take_rate"
    g = graph_with(change)
    checks = validate_graph(g, store, days=7)
    assert ("gmv", "identity") in failures(checks)
    assert g.status["gmv"] == "failed"


def test_false_additivity_fails(store):
    def change(r):
        if r["name"] == "take_rate":
            r["additive"] = True
    assert ("take_rate", "additivity") in failures(validate_graph(graph_with(change), store, days=7))


def test_bounds_violation_fails(store):
    def change(r):
        if r["name"] == "payment_success_rate":
            r["bounds"] = [0, 0.5]
    assert ("payment_success_rate", "bounds") in failures(validate_graph(graph_with(change), store, days=7))


def test_column_and_formula_must_agree(store):
    def change(r):
        if r["name"] == "orders":
            r["formula"] = "payment_successes"  # orders != payment_successes in the data
    g = graph_with(change)
    assert ("orders", "definition") in failures(validate_graph(g, store, days=7))


def test_cli_validate(capsys, db_path):
    assert main(["--validate", "--db", str(db_path), "--tree"]) == 0
    out = capsys.readouterr().out
    assert "Validation:" in out and "0 failed" in out and "✓" in out
