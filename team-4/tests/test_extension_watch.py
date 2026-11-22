"""X2: the answer to a new metric says plainly whether it is watched. Only ratios of two column metrics are
judged by the hourly scan; any other shape is in the graph and validated, and the message says it is not watched."""
import pytest

from brain.data import Store
from brain.extension import Extension
from brain.graph import MetricGraph
from brain.monitor import Config, Monitor
from tests.test_extension import by_check, definition, drop, look, page_yaml

NOT_WATCHED = "not watched: only ratios of two columns are watched"
CHECKS = ["read", "sql", "formula", "definition", "rows", "split", "alias", "rollup", "coverage", "placement",
          "context", "monitoring"]


@pytest.fixture(scope="module")
def market(tmp_path_factory):
    from sim.run import backfill
    return backfill(str(tmp_path_factory.mktemp("extension_watch") / "market.db"), 240)   # 10 days, seed 42


@pytest.fixture
def ext(market, tmp_path):
    store, graph = Store(market), MetricGraph.load()
    monitor = Monitor(graph, store, Config())
    yield Extension(graph, store, tmp_path / "incoming", monitor=monitor, quiet=True)
    store.conn.close()


@pytest.mark.parametrize("name, fields", [
    ("net_gmv", {"formula": "gmv - discounts", "unit": "eur"}),                       # a difference
    ("platform_revenue", {"formula": "marketplace_fees + ad_spend", "unit": "eur"}),  # a sum
    ("refunds_per_view", {"formula": "refunds / orders * conversion_rate"}),         # a ratio times a rate
    ("gmv_net_of_vat", {"formula": "gmv / 1.19", "unit": "eur"}),                      # a constant
])
def test_a_metric_of_another_shape_is_said_to_be_not_watched(ext, name, fields):
    drop(ext.incoming, name, definition(name, **fields))
    [r] = look(ext)
    assert r["status"] == "validated", r["message"]
    message = r["message"]
    assert message.startswith(f"{name} added to the graph and validated"), message
    assert message.endswith(NOT_WATCHED), message
    assert message.count(NOT_WATCHED) == 1 and "\n" not in message
    assert "watched from the next tick" not in message
    assert [c["check"] for c in r["checks"]] == CHECKS                    # the checks list is kept
    monitoring = by_check(r)["monitoring"]
    assert monitoring["status"] == "skip" and NOT_WATCHED in monitoring["detail"]
    assert name not in {w.metric for w in ext.monitor.watched}


def test_a_ratio_of_two_columns_is_said_to_be_watched(ext):
    drop(ext.incoming, "refund_rate", page_yaml())
    [r] = look(ext)
    assert r["status"] == "validated"
    assert r["message"].startswith("refund_rate added to the graph and validated")
    assert r["message"].endswith("watched from the next tick") and "not watched" not in r["message"]
    assert by_check(r)["monitoring"]["status"] == "pass"
    assert "refund_rate" in {w.metric for w in ext.monitor.watched}


def test_a_tentative_metric_says_so_and_whether_it_is_watched(ext):
    drop(ext.incoming, "avg_net", definition("avg_net", "AVG(gmv) - AVG(discounts)"))
    [r] = look(ext)
    assert r["status"] == "tentative"
    assert r["message"].startswith("avg_net added to the graph as tentative:"), r["message"]
    assert r["message"].endswith(NOT_WATCHED)
