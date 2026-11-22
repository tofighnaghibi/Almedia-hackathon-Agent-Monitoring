"""X1: a metric the extension agent accepts gets the context of the metrics its validated formula is built
from: their release systems (and with them the calendar link) and their data sources (freshness). Without it
a new rate turns a promo into a false alarm and a fault into a second incident with no cause."""
from dataclasses import replace

import pytest

from brain.data import Store
from brain.extension import Extension
from brain.graph import CALENDAR_SYSTEMS, MetricGraph
from brain.monitor import watch_list
from tests.test_extension import by_check, definition, drop, look, page_yaml


@pytest.fixture(scope="module")
def market(tmp_path_factory):
    from sim.run import backfill
    return backfill(str(tmp_path_factory.mktemp("extension_context") / "market.db"), 240)   # 10 days, seed 42


def core_with(**systems) -> MetricGraph:
    """The core graph with release systems given to some of its metrics."""
    specs = [replace(s, systems=tuple(systems[s.name])) if s.name in systems else s
             for s in MetricGraph.load().specs.values()]
    return MetricGraph.from_specs(specs)


def context_ids(graph: MetricGraph, name: str) -> set[str]:
    return {n.id for n in graph.context_of(name)}


@pytest.fixture
def agent(market, tmp_path):
    stores = []

    def make(graph: MetricGraph) -> Extension:
        store = Store(market)
        stores.append(store)
        return Extension(graph, store, tmp_path / "incoming", graph_out=tmp_path / "graph.json", quiet=True)

    yield make
    for s in stores:
        s.conn.close()


def test_refund_rate_inherits_the_systems_calendar_and_sources_of_refunds_and_orders(agent):
    assert "promotions" in CALENDAR_SYSTEMS
    ext = agent(core_with(refunds=["returns"], orders=["orders", "promotions"]))
    drop(ext.incoming, "refund_rate", page_yaml())
    [r] = look(ext)
    assert r["status"] == "validated", r["message"]
    g = ext.graph
    assert set(g.specs["refund_rate"].systems) == {"returns", "orders", "promotions"}
    ids = context_ids(g, "refund_rate")
    assert {"release:returns", "release:orders", "release:promotions", "calendar", "freshness:orders"} <= ids
    assert g.sources("refund_rate") == ["orders"]
    [w] = [w for w in watch_list(g) if w.metric == "refund_rate"]
    assert w.calendar is True and w.sources == ("orders",)
    # the answer says what was inherited and from which metric
    context = by_check(r)["context"]
    assert context["status"] == "pass"
    for word in ("returns", "promotions", "calendar", "freshness of orders", "refunds", "orders"):
        assert word in context["detail"], context["detail"]
    exported = __import__("json").loads(ext.graph_out.read_text(encoding="utf-8"))
    [node] = [n for n in exported["nodes"] if n["id"] == "refund_rate"]
    assert set(node["systems"]) == {"returns", "orders", "promotions"}


def test_a_release_of_an_inherited_system_is_a_release_candidate_for_the_new_metric(agent):
    """What the investigator and the monitor's release watch ask the graph: is the release's system linked
    to the moved metric? Through the graph API, for a release in the new metric's scope."""
    ext = agent(core_with(refunds=["returns"]))
    drop(ext.incoming, "refund_rate", page_yaml())
    [r] = look(ext)
    assert r["status"] == "validated"
    g = ext.graph
    release = {"system": "returns", "country": "PL", "platform": None}
    linked = [m for m in ("refund_rate", "payment_success_rate") if release["system"] in g.specs[m].systems]
    assert linked == ["refund_rate"]
    edges = [e for e in g.in_edges("refund_rate", "context") if e.src == "release:returns"]
    assert len(edges) == 1 and edges[0].attrs["via"] == "releases.system"
    assert g.nodes["release:returns"].attrs == {"table": "releases", "system": "returns"}
    # nothing else got the system: refunds keeps its own, the rest of the graph is as it was
    assert [m for m, s in g.specs.items() if "returns" in s.systems] == ["refunds", "refund_rate"]


def test_declared_systems_are_kept_and_the_inherited_ones_added(agent):
    ext = agent(core_with(refunds=["returns"]))
    drop(ext.incoming, "refund_rate", page_yaml() + "systems: [refund-service]\n")
    [r] = look(ext)
    assert r["status"] == "validated"
    assert ext.graph.specs["refund_rate"].systems == ("refund-service", "returns")


def test_with_the_core_graph_refund_rate_inherits_freshness_and_says_no_system_is_there(agent):
    """refunds and orders carry no release system in the core graph: nothing is invented."""
    ext = agent(MetricGraph.load())
    drop(ext.incoming, "refund_rate", page_yaml())
    [r] = look(ext)
    g = ext.graph
    assert g.specs["refund_rate"].systems == ()
    assert context_ids(g, "refund_rate") == {"freshness:orders"}
    context = by_check(r)["context"]
    assert context["status"] == "pass" and "no release system" in context["detail"]
    assert "freshness of orders" in context["detail"]


def test_a_rate_that_is_a_product_of_watched_rates_takes_their_systems(agent):
    """The audit's second case (E3b): orders_per_checkout = payment_attempt_rate * payment_success_rate * order_rate
    for any data, but orders and checkouts carry no system, so the payments-sdk fault became a second incident
    with no cause. The rates it is the product of (found by signature) lend it their release systems."""
    ext = agent(MetricGraph.load())
    drop(ext.incoming, "orders_per_checkout", definition("orders_per_checkout", "SUM(orders) * 1.0 / NULLIF(SUM(checkouts), 0)"))
    [r] = look(ext)
    assert r["status"] == "validated" and r["formula"] == "orders / checkouts", r["message"]
    g = ext.graph
    assert set(g.specs["orders_per_checkout"].systems) == {"checkout", "payments", "payments-sdk", "orders"}
    assert "release:payments-sdk" in context_ids(g, "orders_per_checkout")
    detail = by_check(r)["context"]["detail"]
    assert "payment_attempt_rate * payment_success_rate * order_rate" in detail, detail
    assert "payments-sdk" in detail
    # the core rates are untouched
    assert g.specs["payment_success_rate"].systems == ("payments", "payments-sdk")


def test_a_rate_over_sessions_gets_the_calendar_link_so_a_promo_is_not_a_false_alarm(agent):
    """The audit's case (E3b): cart_per_session had no calendar link and the FR promo became an incident."""
    ext = agent(MetricGraph.load())
    drop(ext.incoming, "cart_per_session", definition("cart_per_session", "SUM(add_to_cart) * 1.0 / NULLIF(SUM(sessions), 0)"))
    [r] = look(ext)
    assert r["status"] == "validated" and r["formula"] == "add_to_cart / sessions", r["message"]
    g = ext.graph
    assert "demand" in g.specs["cart_per_session"].systems
    assert "calendar" in context_ids(g, "cart_per_session")
    [w] = [w for w in watch_list(g) if w.metric == "cart_per_session"]
    assert w.calendar is True and w.sources == ("traffic",)
