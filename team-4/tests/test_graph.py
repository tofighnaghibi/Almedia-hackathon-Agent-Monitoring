import json

import pytest

from brain.graph import GraphError, MetricGraph, main
from brain.metric_spec import SpecError, spec_from_dict


def specs(*raws):
    return [spec_from_dict(r) for r in raws]


def test_core_graph_has_one_top_outcome(graph):
    assert graph.top_outcomes() == ["contribution"]


def test_core_tree_structure(graph):
    t = graph.tree("contribution")
    assert [c.metric for c in t.children] == ["marketplace_fees", "ad_spend", "discounts"]
    assert [c.role for c in t.children] == ["term+", "term+", "term-"]
    discounts = t.children[2]
    gmv_again = [c for c in discounts.children if c.metric == "gmv"][0]
    assert gmv_again.repeated and gmv_again.children == []
    conv = graph.formula_children("conversion_rate")
    assert [c for c, _ in conv] == ["views_per_session", "add_to_cart_rate", "checkout_rate",
                                    "payment_attempt_rate", "payment_success_rate", "order_rate"]


def test_edge_kinds_and_context(graph):
    kinds = {e.kind for e in graph.edges}
    assert kinds == {"formula", "definition", "breakdown", "may_affect", "context"}
    causes = {e.src for e in graph.possible_causes("add_to_cart_rate")}
    assert {"stockouts", "discount_rate", "feed_ctr"} <= causes   # from the stock level, not stockout_rate
    assert all(graph.status[e.id] == "assumed" for e in graph.edges if e.kind == "may_affect")
    ctx = {n.id for n in graph.context_of("payment_success_rate")}
    assert {"release:payments", "freshness:payments"} <= ctx
    assert "calendar" in {n.id for n in graph.context_of("sessions")}
    assert "orders@country" in graph.nodes and graph.nodes["orders@country"].kind == "breakdown"


def test_definition_edges_for_atomic_rates(graph):
    defs = {e.dst for e in graph.out_edges("payment_success_rate", "definition")}
    assert defs == {"payment_successes", "payment_attempts"}
    assert graph.out_edges("payment_success_rate", "formula") == []


def test_additivity_inference(graph):
    assert graph.additive["contribution"] is True
    assert graph.additive["orders"] is True
    assert graph.additive["conversion_rate"] is False
    assert graph.additive["take_rate"] is False


def test_formula_cycle_is_rejected_with_a_hint():
    with pytest.raises(GraphError, match="atomic"):
        MetricGraph.from_specs(specs(
            {"name": "a", "column": "a", "decomposition": "b * c"},
            {"name": "b", "formula": "a / c"},
            {"name": "c", "column": "c"},
        ))


def test_unknown_reference_and_duplicates():
    with pytest.raises(GraphError, match="unknown"):
        MetricGraph.from_specs(specs({"name": "a", "formula": "b / c"}, {"name": "b", "column": "b"}))
    with pytest.raises(GraphError, match="duplicate"):
        MetricGraph.from_specs(specs({"name": "a", "column": "a"}, {"name": "a", "column": "a"}))
    with pytest.raises(GraphError, match="may_affect"):
        MetricGraph.from_specs(specs({"name": "a", "column": "a", "may_affect": ["zzz"]}))


@pytest.mark.parametrize("raw, msg", [
    ({"name": "Bad Name", "column": "x"}, "bad metric name"),
    ({"name": "a"}, "needs one of"),
    ({"name": "a", "column": "a", "colour": "red"}, "unknown fields"),
    ({"name": "a", "formula": "a + b"}, "itself"),
    ({"name": "a", "column": "a", "dimensions": ["planet"]}, "unknown dimensions"),
    ({"name": "a", "column": "a", "bounds": [0]}, "bounds"),
    ({"name": "a", "column": "a", "direction": "sideways"}, "direction"),
    ({"name": "a", "column": "a", "may_affect": [{"metric": "b", "prior": 2}]}, "prior"),
    ({"name": "a", "formula": "a.b"}, "bad formula"),
])
def test_spec_errors(raw, msg):
    with pytest.raises(SpecError, match=msg):
        spec_from_dict(raw)


def test_export_is_json_and_complete(graph):
    d = json.loads(json.dumps(graph.to_dict()))
    ids = {n["id"] for n in d["nodes"]}
    assert {"contribution", "gmv@country", "release:payments", "calendar"} <= ids
    assert d["top_outcomes"] == ["contribution"]
    assert d["trees"][0]["metric"] == "contribution"
    assert all(e["source"] in ids and e["target"] in ids for e in d["edges"])


def test_render_tree_and_cli(capsys, tmp_path):
    assert main(["--tree", "--export", str(tmp_path / "g.json")]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0].startswith("contribution [eur]")
    assert "Outside the outcome tree" in out
    assert (tmp_path / "g.json").exists()
