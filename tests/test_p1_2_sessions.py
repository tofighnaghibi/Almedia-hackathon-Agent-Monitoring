"""P1-2 session layer: invariance (V1-V3, gate S7), time integrity (V4, gate S3), gateway guard for the new lever."""
from dataclasses import replace
from datetime import date, datetime

import polars as pl
import pytest

from mkt import pipeline, registry
from mkt.sim import chain, sessions, twin
from mkt.engine_types import InterventionSpec

W0, W1 = datetime(2018, 1, 8), datetime(2018, 1, 15)


@pytest.fixture(scope="module")
def params(cfg):
    try:
        registry.load("olist", cfg.artifacts)
    except registry.MissingSource:
        pytest.skip("Olist not registered")
    return chain.fit(pipeline.canonical_olist(cfg))


@pytest.fixture(scope="module")
def sp():
    try:
        return sessions.SessionParams.from_cards()
    except FileNotFoundError:
        pytest.skip("transport cards not fitted (scripts/p1_2_fit_cards.py)")


def _key(df):
    return df.sort("session_id")


def test_v1_chain_unchanged_by_session_layer(params, sp):
    _, f0, _, _, _ = twin.run(params, date(2018, 1, 1), date(2018, 1, 11), 51, None)
    _, f1, _, port, truth = twin.run(params, date(2018, 1, 1), date(2018, 1, 11), 51, None, session_params=sp)
    assert f0.equals(f1)
    assert truth.sessions_factual[0].filter(pl.col("kind") == "buying").height == f1.height


def test_v2_chunked_sessions_equal_one_run(params, sp):
    def world(a, b):
        o = chain.simulate(params, a, b, seed=52)
        return sessions.build(o, sp, params, a, b, 52)
    s1, v1 = world(date(2018, 1, 1), date(2018, 1, 6))
    s2, v2 = world(date(2018, 1, 6), date(2018, 1, 11))
    s, v = world(date(2018, 1, 1), date(2018, 1, 11))
    assert _key(pl.concat([s1, s2])).equals(_key(s))
    assert pl.concat([v1, v2]).sort("vkey", "session_id").equals(v.sort("vkey", "session_id"))


def test_v3_checkout_failure_changes_only_sessions_in_window(params, sp):
    spec = InterventionSpec(id="9a", target="checkout.failure_rate", scope="", operator="set", value=0.4, window_start=W0,
                            window_end=W1, requested_by="test")
    ev, f, t, port, truth = twin.run(params, date(2018, 1, 1), date(2018, 1, 22), 53, spec, session_params=sp)
    assert ev.status == "executed"
    (sf, vf), (st, vt) = truth.sessions_factual, truth.sessions_twin
    sf, st = _key(sf), _key(st)
    assert sf["session_id"].equals(st["session_id"])                          # the twin pairs every session
    diff = sf.filter(pl.col("outcome") != st["outcome"])
    assert diff.height > 0 and set(diff["outcome"]) == {"checkout_failed"}
    t_end = diff["end_us"].cast(pl.Datetime("us"))
    assert (t_end >= W0).all() and (t_end < W1).all()
    assert sf.drop("outcome").equals(st.drop("outcome")) and vf.sort("vkey").equals(vt.sort("vkey"))
    share = diff.height / sf.filter((pl.col("kind") == "buying") & pl.col("end_us").cast(pl.Datetime("us")).is_between(W0, W1, "left")).height
    assert 0.3 < share < 0.5
    assert f.height == t.height - diff.height                                   # failed checkouts are lost orders


def test_v4_time_integrity_and_lineage(params, sp):
    _, _, _, port, _ = twin.run(params, date(2018, 1, 1), date(2018, 1, 6), 54, None, session_params=sp)
    se = port.events.filter(pl.col("event_type").is_in(["session_started", "cart_added", "checkout_started", "item_viewed"]))
    assert se.height > 0 and (se["available_at"] >= se["event_time"]).all()
    assert any(k.startswith("card:TC-") for k in port.env.mechanism_versions)
    ids = {v.id for v in port.variables()}
    assert {"sessions_count", "cart_adds_count", "checkouts_count"} <= ids
    with pytest.raises(ValueError, match="gate S3"):
        twin.run(params, date(2018, 1, 1), date(2018, 1, 3), 54, None, session_params=replace(sp, trained_through="2018-01-01"))


def test_real_port_does_not_advertise_session_metrics(cfg):
    from mkt.replay_port import EventFramePort
    assert "sessions_count" not in {m.id for m in EventFramePort.metrics}


@pytest.mark.parametrize("value,op", [(0.95, "set"), (0.4, "scale")])
def test_checkout_lever_is_guarded(params, value, op):
    bad = InterventionSpec(id="x", target="checkout.failure_rate", scope="", operator=op, value=value, window_start=W0, window_end=W1,
                           requested_by="test")
    ev, eff = twin.gateway(bad, set(params.buyer_regions), (datetime(2018, 1, 1), datetime(2018, 2, 1)))
    assert ev.status == "rejected" and eff is None
