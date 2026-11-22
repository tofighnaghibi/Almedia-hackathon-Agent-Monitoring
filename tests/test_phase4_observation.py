"""Phase 4, gate S3: no look-ahead, reproducible as-of queries, revisions handled, observation properties as specified."""
import random
from datetime import date, datetime, timedelta

import polars as pl
import pytest

from mkt import pipeline, registry
from mkt.sim import chain
from mkt.sim.observe_chain import EmulatorPort, ObservationModel, observe
from mkt.engine_types import EvidenceRequest, OriginClass

START = datetime(2018, 1, 1)


@pytest.fixture(scope="module")
def params(cfg):
    try:
        registry.load("olist", cfg.artifacts)
    except registry.MissingSource:
        pytest.skip("Olist not registered")
    return chain.fit(pipeline.canonical_olist(cfg))


@pytest.fixture(scope="module")
def obs(params):
    return observe(chain.simulate(params, date(2018, 1, 1), date(2018, 3, 1), seed=2), ObservationModel(), seed=2)


@pytest.fixture(scope="module")
def port(obs, params):
    return EmulatorPort(obs, params, ObservationModel(), START, seed=2)


def test_never_serves_the_future(port):
    rnd = random.Random(0)
    for _ in range(25):
        as_of = START + timedelta(hours=rnd.randint(24, 24 * 80))
        for var in ("order_placed", "delivered", "handed_to_carrier", "review_score"):
            r = port.query(EvidenceRequest(variable_id=var, shape="event_sequence", as_of=as_of,
                                           window_start=as_of - timedelta(days=10), window_end=as_of + timedelta(days=5)))
            o = r.observations[0]
            assert o.available_at <= as_of
            ids = {e["event_id"] for e in o.payload}
            arrived = port.events.filter(pl.col("event_id").is_in(list(ids)))
            assert (arrived["available_at"] <= as_of).all()


def test_as_of_is_reproducible_and_append_only(params):
    """A longer simulation must not change anything knowable at an earlier time."""
    om = ObservationModel()
    short = observe(chain.simulate(params, date(2018, 1, 1), date(2018, 2, 1), seed=3), om, seed=3)
    long = observe(chain.simulate(params, date(2018, 1, 1), date(2018, 3, 1), seed=3), om, seed=3)
    ps, pl_ = EmulatorPort(short, params, om, START, 3), EmulatorPort(long, params, om, START, 3)
    req = EvidenceRequest(variable_id="orders_count", shape="series", grain="day", as_of=datetime(2018, 1, 20),
                          window_start=START, window_end=datetime(2018, 1, 20))
    assert ps.query(req).observations[0].payload == pl_.query(req).observations[0].payload
    assert ps.query(req).observations[0].payload == ps.query(req).observations[0].payload


def test_revisions_supersede_and_never_double_count(port, obs):
    rev = obs.filter(pl.col("revision") == 1).head(5)
    assert rev.height > 0
    for row in rev.iter_rows(named=True):
        first = obs.filter((pl.col("record_key") == row["record_key"]) & (pl.col("revision") == 0)).row(0, named=True)
        between = first["available_at"] + (row["available_at"] - first["available_at"]) / 2
        for as_of, expect in ((between, first["event_time"]), (row["available_at"], row["event_time"])):
            r = port.query(EvidenceRequest(variable_id="delivered", shape="event_sequence", as_of=as_of,
                                           window_start=min(first["event_time"], row["event_time"]) - timedelta(days=2),
                                           window_end=max(first["event_time"], row["event_time"]) + timedelta(days=2)))
            hits = [e for e in r.observations[0].payload if e["order_id"] == row["order_id"]]
            assert len(hits) == 1 and hits[0]["event_time"] == expect


def test_measured_observation_properties(obs):
    h = obs.filter(pl.col("event_type") == "handed_to_carrier")
    assert abs((h["event_time"].dt.second() == 0).mean() - (0.295 + 0.705 / 60)) < 0.02     # MEASURED rounding share
    d = obs.filter(pl.col("event_type") == "delivered")
    assert d["revision"].max() == 1 and 0.005 < (d["revision"] == 1).sum() / (d["revision"] == 0).sum() < 0.02


def test_emulator_evidence_is_synthetic_and_dated(port):
    r = port.query(EvidenceRequest(variable_id="late_share_cohort", shape="aggregate", as_of=datetime(2018, 4, 1),
                                   window_start=START, window_end=datetime(2018, 2, 1)))
    ev = r.evidence[0]
    assert ev.origin == OriginClass.SYNTHETIC and port.env.kind.value == "emulator"
    assert ev.lineage.trained_through < START                           # gate S3 on fitted mechanisms
    assert any("assumed" in a or "correction_rate" in a for a in port.env.assumptions)


def test_emulator_refuses_mechanisms_trained_after_start(params, obs):
    with pytest.raises(ValueError, match="S3"):
        EmulatorPort(obs, params, ObservationModel(), datetime(2017, 6, 1), seed=2)
