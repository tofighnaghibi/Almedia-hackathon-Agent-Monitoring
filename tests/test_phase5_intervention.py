"""Phase 5 gates: S7 (intervention correctness, shared randomness, chunk invariance) and S6 (oracle firewall)."""
import dataclasses
from datetime import date, datetime

import numpy as np
import polars as pl
import pytest

from mkt import pipeline, registry
from mkt.sim import chain, twin
from mkt.engine_types import EvidenceRequest, FailureCode, InterventionSpec

W0, W1 = datetime(2018, 2, 5), datetime(2018, 2, 12)


def spec(**kw):
    base = dict(id="iv", target="carrier.transit_multiplier", scope="region == 'RJ'", operator="scale", value=2.5,
                window_start=W0, window_end=W1, requested_by="test")
    return InterventionSpec(**(base | kw))


@pytest.fixture(scope="module")
def params(cfg):
    try:
        registry.load("olist", cfg.artifacts)
    except registry.MissingSource:
        pytest.skip("Olist not registered")
    return chain.fit(pipeline.canonical_olist(cfg))


@pytest.fixture(scope="module")
def result(params):
    return twin.run(params, date(2018, 1, 1), date(2018, 3, 15), 11, spec())


def test_unaffected_orders_are_identical(result):
    _, f, t, _, truth = result
    aff = truth.per_order["affected"]
    assert 50 < aff.sum() < f.height
    assert f.filter(~aff).equals(t.filter(~aff))                       # paired: nothing else moved
    assert f.select("order_idx", "purchased_at", "shipped_at", "promised_at").equals(t.select("order_idx", "purchased_at", "shipped_at", "promised_at"))


def test_known_effect_is_applied_exactly(result):
    _, f, t, _, truth = result
    aff = truth.per_order["affected"]
    ff, tt = f.filter(aff), t.filter(aff)
    tr_f = (ff["delivered_at"] - ff["shipped_at"]).dt.total_seconds().to_numpy()
    tr_t = (tt["delivered_at"] - tt["shipped_at"]).dt.total_seconds().to_numpy()
    assert np.allclose(tr_f, 2.5 * tr_t, atol=2)                         # transit scaled by exactly the requested factor
    assert ff["buyer_region"].unique().to_list() == ["RJ"]
    assert ((ff["shipped_at"] >= W0) & (ff["shipped_at"] < W1)).all()
    assert truth.effect_late_share_affected > 0.2                        # the effect is real and large in the oracle


def test_chunked_runs_equal_one_run(params):
    full = chain.simulate(params, date(2018, 1, 1), date(2018, 3, 1), seed=12)
    parts = pl.concat([chain.simulate(params, date(2018, 1, 1), date(2018, 2, 1), seed=12),
                       chain.simulate(params, date(2018, 2, 1), date(2018, 3, 1), seed=12)])
    assert full.equals(parts)


def test_unrelated_mechanism_change_leaves_fulfilment_untouched(params):
    other = dataclasses.replace(params, lag_q=[x * 3 for x in params.lag_q])     # change only the survey-answer lag
    a = chain.simulate(params, date(2018, 1, 1), date(2018, 2, 1), seed=13)
    b = chain.simulate(other, date(2018, 1, 1), date(2018, 2, 1), seed=13)
    cols = ["order_idx", "purchased_at", "approved_at", "shipped_at", "delivered_at", "promised_at"]
    assert a.select(cols).equals(b.select(cols))
    assert not a["answered_at"].equals(b["answered_at"])
    # scores are drawn identically; they only differ in WHETHER they are observed (a longer lag can pass the data cut)
    both = a["score"].is_not_null() & b["score"].is_not_null()
    assert (a.filter(both)["score"] == b.filter(both)["score"]).all()


def test_twin_without_intervention_equals_baseline(params):
    _, f, t, _, truth = twin.run(params, date(2018, 1, 1), date(2018, 2, 1), 14, None)
    assert truth is None and f.equals(t)


@pytest.mark.parametrize("bad, code", [
    (dict(target="price.multiplier"), FailureCode.UNSUPPORTED_REQUEST),
    (dict(value=50.0), FailureCode.UNSUPPORTED_REQUEST),
    (dict(scope="region == 'ATLANTIS'"), FailureCode.UNSUPPORTED_REQUEST),
    (dict(scope="seller == 's1'"), FailureCode.UNSUPPORTED_REQUEST),
    (dict(window_start=datetime(2017, 6, 1)), FailureCode.UNSUPPORTED_REQUEST),
])
def test_gateway_rejects_invalid_actions(params, bad, code):
    ev, mult = twin.gateway(spec(**bad), set(params.buyer_regions), (datetime(2018, 1, 1), datetime(2018, 3, 15)))
    assert ev.status == "rejected" and ev.failure_code == code.value and mult is None


ORACLE_WORDS = ("affected", "late_fact", "late_twin", "score_twin", "transit_multiplier", "order_idx", "truth", "oracle", "twin")


def test_oracle_never_reaches_the_reader(result):
    _, _, _, port, _ = result
    assert not any(w in c for c in port.events.columns for w in ORACLE_WORDS)
    assert not any(isinstance(v, twin.Truth) for v in vars(port).values())
    r = port.query(EvidenceRequest(variable_id="delivered", shape="event_sequence", as_of=datetime(2018, 4, 1),
                                   window_start=datetime(2018, 2, 1), window_end=datetime(2018, 2, 20)))
    keys = set().union(*(e.keys() for e in r.observations[0].payload))
    assert not any(w in k for k in keys for w in ORACLE_WORDS)
    assert r.evidence[0].origin.value == "synthetic"
