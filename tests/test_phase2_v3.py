"""Phase 2 gates on REAL data through the v3 contracts: S8 (core compatibility), S6 (origin classes),
S2 (lineage), plus as-of safety of the port."""
import inspect
from datetime import datetime

import pytest
from pydantic import ValidationError

from mkt import registry
from mkt.engine_types import (EnvironmentKind, EngineEvidence, EvidenceRequest, EvidenceResult, FailureCode, InterventionSpec, LineageRoot,
                    OriginClass, EngineLineage, Refusal)

AS_OF = datetime(2018, 8, 31)


@pytest.fixture(scope="module")
def port(cfg):
    try:
        registry.load("olist", cfg.artifacts)
    except registry.MissingSource:
        pytest.skip("Olist not registered")
    from mkt.replay_port import OlistReplayPort
    return OlistReplayPort(cfg)


def test_contracts_are_frozen_and_strict():
    lin = EngineLineage(roots=[LineageRoot(source="x", source_version="1", files={})])
    rec = EngineEvidence(id="e", origin=OriginClass.REAL, environment_id="env", lineage=lin)
    with pytest.raises(ValidationError):
        rec.origin = OriginClass.SYNTHETIC                       # frozen
    with pytest.raises(ValidationError):
        EngineEvidence(id="e", origin=OriginClass.REAL, environment_id="env", lineage=lin, oracle="leak")   # extra=forbid
    with pytest.raises(ValidationError):
        EngineEvidence(id="e", origin=OriginClass.TRANSPORTED, environment_id="env", lineage=lin)          # needs transport card


def test_port_never_serves_the_future(port):
    as_of = datetime(2018, 5, 25)
    r = port.query(EvidenceRequest(variable_id="delivered", shape="event_sequence", as_of=as_of,
                                   window_start=datetime(2018, 5, 1), window_end=datetime(2018, 6, 30)))
    assert isinstance(r, EvidenceResult)
    o = r.observations[0]
    assert all(e["event_time"] <= as_of for e in o.payload)
    assert o.available_at <= as_of
    assert any("past as_of" in f for f in o.quality_flags)    # incomplete window is flagged, not silently zero


def test_metric_as_of_is_reproducible(port):
    req = EvidenceRequest(variable_id="orders_count", shape="series", grain="week", as_of=datetime(2018, 6, 1),
                          window_start=datetime(2018, 4, 1), window_end=datetime(2018, 6, 1))
    a, b = port.query(req), port.query(req)
    assert a.observations[0].payload == b.observations[0].payload


def test_refusals_are_explicit(port):
    r = port.query(EvidenceRequest(variable_id="orders_count", shape="assignment", as_of=AS_OF))
    assert isinstance(r, Refusal) and r.code == FailureCode.NO_STUDIES_IN_SOURCE
    r = port.query(EvidenceRequest(variable_id="no_such_variable", shape="series", as_of=AS_OF))
    assert isinstance(r, Refusal) and r.code == FailureCode.UNKNOWN_VARIABLE
    r = port.query(EvidenceRequest(variable_id="delivered", shape="series", as_of=AS_OF))
    assert isinstance(r, Refusal) and r.code == FailureCode.UNSUPPORTED_SHAPE
    small = type(port).__new__(type(port))
    small.__dict__.update(port.__dict__)
    small.event_rows_max = 1000                                   # same data, tiny budget
    r = small.query(EvidenceRequest(variable_id="item_sold", shape="event_sequence", as_of=AS_OF))
    assert isinstance(r, Refusal) and r.code == FailureCode.BUDGET_EXCEEDED
    # read-only by construction (Contracts I15): no actuation method exists on the port at all
    assert not any(hasattr(port, m) for m in ("propose_intervention", "act", "intervene", "write"))


def test_origin_classes_and_lineage(port, cfg):
    man = registry.load("olist", cfg.artifacts)
    e = port.query(EvidenceRequest(variable_id="review_score", shape="event_sequence", as_of=AS_OF,
                                   window_start=datetime(2018, 1, 1), window_end=datetime(2018, 1, 15)))
    m = port.query(EvidenceRequest(variable_id="gmv", shape="aggregate", as_of=AS_OF,
                                   window_start=datetime(2018, 1, 1), window_end=datetime(2018, 2, 1)))
    assert e.evidence[0].origin == OriginClass.REAL and m.evidence[0].origin == OriginClass.DERIVED
    for res in (e, m):
        assert res.evidence[0].environment_id == port.env.id and port.env.kind == EnvironmentKind.REAL_REPLAY
        assert res.evidence[0].lineage.roots[0].files == man.files                     # back to the exact source files
        assert res.observations[0].evidence_id == res.evidence[0].id


def test_empirical_identity_gmv_equals_items(port):
    w = dict(as_of=AS_OF, window_start=datetime(2018, 1, 1), window_end=datetime(2018, 1, 8))
    gmv = port.query(EvidenceRequest(variable_id="gmv", shape="aggregate", **w)).observations[0].payload["value"]
    items = port.query(EvidenceRequest(variable_id="item_sold", shape="event_sequence", **w)).observations[0].payload
    assert abs(gmv - sum(i["amount"] + i["value"] for i in items)) < 0.01
