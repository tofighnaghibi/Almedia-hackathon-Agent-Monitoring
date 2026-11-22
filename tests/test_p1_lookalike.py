"""P1 look-alike pair mechanics: a logging failure changes only what is observed; a demand drop changes the world."""
from datetime import date, datetime

import polars as pl
import pytest

from mkt import pipeline, registry
from mkt.sim import chain, twin
from mkt.engine_types import InterventionSpec

W0, W1 = datetime(2018, 2, 12), datetime(2018, 2, 19)


def sp(target, scope, op, value):
    return InterventionSpec(id="x", target=target, scope=scope, operator=op, value=value, window_start=W0, window_end=W1,
                            requested_by="test")


@pytest.fixture(scope="module")
def params(cfg):
    try:
        registry.load("olist", cfg.artifacts)
    except registry.MissingSource:
        pytest.skip("Olist not registered")
    return chain.fit(pipeline.canonical_olist(cfg))


def _count(port, etype):
    ev = port.events.filter((pl.col("event_type") == etype) & (pl.col("event_time") >= W0) & (pl.col("event_time") < W1))
    return ev.unique("record_key").height


def test_logging_drop_changes_only_observation(params):
    _, f, t, port, truth = twin.run(params, date(2018, 1, 1), date(2018, 3, 1), 41,
                                    sp("logging.drop_rate", "event_type == 'order_created'", "set", 0.4))
    assert f.equals(t) and truth.world_changed is False and truth.causes[0]["layer"] == "observation"
    world = f.filter((pl.col("purchased_at") >= W0) & (pl.col("purchased_at") < W1)).height
    assert 0.5 * world < _count(port, "order_created") < 0.7 * world
    _, _, _, clean, _ = twin.run(params, date(2018, 1, 1), date(2018, 3, 1), 41, None)
    assert _count(port, "payment_approved") == _count(clean, "payment_approved")   # the identity partner is untouched


def test_demand_drop_changes_the_world_only_in_its_window(params):
    _, f, t, _, truth = twin.run(params, date(2018, 1, 1), date(2018, 3, 1), 42, sp("demand.multiplier", "", "scale", 0.6))
    assert truth.world_changed and truth.causes[0]["layer"] == "world"
    pre = lambda w: w.filter(pl.col("purchased_at") < W0)
    assert pre(f).equals(pre(t))                                            # global keys: nothing before the window moves
    inw = lambda w: w.filter((pl.col("purchased_at") >= W0) & (pl.col("purchased_at") < W1)).height
    assert 0.5 < inw(f) / inw(t) < 0.7


def test_identity_is_declared_in_the_metric_definitions(params):
    _, _, _, port, _ = twin.run(params, date(2018, 1, 1), date(2018, 1, 15), 43, None)
    defs = {v.id: v for v in port.variables()}
    assert any("payments_count" in i for i in defs["orders_count"].empirical_identities)


@pytest.mark.parametrize("bad", [
    sp("logging.drop_rate", "region == 'RJ'", "set", 0.4),                  # wrong selector key for this lever
    sp("logging.drop_rate", "event_type == 'nonsense'", "set", 0.4),        # unknown event type
    sp("logging.drop_rate", "event_type == 'order_created'", "set", 0.95),  # above safety bound
    sp("demand.multiplier", "", "scale", 0.1),                               # below safety bound
    sp("demand.multiplier", "", "set", 0.6),                                 # operator not allowed
])
def test_new_levers_are_guarded(params, bad):
    ev, eff = twin.gateway(bad, set(params.buyer_regions), (datetime(2018, 1, 1), datetime(2018, 3, 1)))
    assert ev.status == "rejected" and eff is None
