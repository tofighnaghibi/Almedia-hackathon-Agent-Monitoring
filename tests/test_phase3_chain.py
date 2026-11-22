"""Phase 3: the minimal chain respects time cutoffs (S3) and keyed randomness (S7 precondition)."""
from datetime import date

import numpy as np
import polars as pl
import pytest

from mkt import pipeline, registry
from mkt.sim import chain


@pytest.fixture(scope="module")
def params(cfg):
    try:
        registry.load("olist", cfg.artifacts)
    except registry.MissingSource:
        pytest.skip("Olist not registered")
    return chain.fit(pipeline.canonical_olist(cfg))


def test_trained_through_is_enforced(params):
    assert params.trained_through == date(2017, 12, 31)
    with pytest.raises(ValueError, match="S3"):
        chain.simulate(params, date(2017, 12, 1), date(2018, 1, 1))


def test_deterministic(params):
    a = chain.simulate(params, date(2018, 1, 1), date(2018, 1, 15), seed=3)
    b = chain.simulate(params, date(2018, 1, 1), date(2018, 1, 15), seed=3)
    assert a.equals(b)


def test_order_draws_do_not_depend_on_horizon(params):
    """Keyed randomness: simulating longer must not change any earlier order."""
    short = chain.simulate(params, date(2018, 1, 1), date(2018, 1, 10), seed=4)
    long = chain.simulate(params, date(2018, 1, 1), date(2018, 1, 20), seed=4).head(short.height)
    assert short.drop("sent_at", "answered_at", "score").equals(long.drop("sent_at", "answered_at", "score"))


def test_uniform_is_keyed_and_uniform():
    idx = np.arange(200_000)
    u = chain.uniform(1, "x", idx)
    assert 0 < u.min() and u.max() < 1 and abs(u.mean() - 0.5) < 0.005
    assert np.array_equal(u[:10], chain.uniform(1, "x", idx[:10]))
    assert not np.array_equal(u[:10], chain.uniform(1, "y", idx[:10]))
    assert not np.array_equal(u[:10], chain.uniform(2, "x", idx[:10]))
