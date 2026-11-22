"""M1 emulator API + M2 planes (ASTRONOMY_SHOP_INTEGRATION.md §G)."""
import json
from datetime import date, datetime, timezone

import pytest

from causal_core.contracts.capabilities import GetSeries
from causal_core.contracts.common import TimeWindow
from mkt import pipeline, registry
from mkt.emulator.api import Emulator, EmulatorConfig
from mkt.sim import chain
from mkt.engine_types import InterventionSpec

U = timezone.utc


@pytest.fixture(scope="module")
def params(cfg):
    try:
        registry.load("olist", cfg.artifacts)
    except registry.MissingSource:
        pytest.skip("Olist not registered")
    return chain.fit(pipeline.canonical_olist(cfg))


def emu(params, tmp_path, name, **kw):
    e = Emulator(params, tmp_path / name)
    e.initialize(EmulatorConfig(start=date(2018, 1, 1), horizon_end=date(2018, 4, 1), **kw), seed=91)
    return e


def test_step_in_pieces_equals_one_step(params, tmp_path):
    a, b = emu(params, tmp_path, "a"), emu(params, tmp_path, "b")
    for d in (date(2018, 1, 8), date(2018, 1, 15), date(2018, 1, 29)):
        a.step(d)
    b.step(date(2018, 1, 29))
    assert a.ledger_hash() == b.ledger_hash()
    assert a.step(date(2018, 1, 20))["noop"]                       # until <= clock: no-op


def test_snapshot_restore_equivalence(params, tmp_path):
    e = emu(params, tmp_path, "s")
    e.step(date(2018, 1, 15))
    snap = e.snapshot()
    e.intervene(InterventionSpec(id="d", target="demand.multiplier", scope="", operator="scale", value=0.5,
                                 window_start=datetime(2018, 1, 20), window_end=datetime(2018, 1, 27), requested_by="test"))
    e.step(date(2018, 2, 5))
    first = e.ledger_hash()
    e.restore(snap)
    assert e.clock == date(2018, 1, 15)
    e.intervene(InterventionSpec(id="d", target="demand.multiplier", scope="", operator="scale", value=0.5,
                                 window_start=datetime(2018, 1, 20), window_end=datetime(2018, 1, 27), requested_by="test"))
    e.step(date(2018, 2, 5))
    assert e.ledger_hash() == first


def test_gateway_rejects_backdated_and_invalid_actions_with_audit(params, tmp_path):
    e = emu(params, tmp_path, "g")
    e.step(date(2018, 1, 15))
    back = e.intervene(InterventionSpec(id="late", target="demand.multiplier", scope="", operator="scale", value=0.5,
                                        window_start=datetime(2018, 1, 5), window_end=datetime(2018, 1, 10), requested_by="test"))
    bad = e.intervene(InterventionSpec(id="bad", target="price.cut", scope="", operator="scale", value=0.5,
                                       window_start=datetime(2018, 1, 20), window_end=datetime(2018, 1, 27), requested_by="test"))
    assert back.status == "rejected" and bad.status == "rejected" and bad.reason
    audit = [json.loads(l) for l in open(e.run_dir / "audit.jsonl")]
    assert [a["kind"] for a in audit] == ["initialize", "step", "intervene", "intervene"]
    assert all(a.get("reason") for a in audit if a["kind"] == "intervene")


def test_public_plane_is_cut_at_the_clock_and_hides_until_granted(params, tmp_path):
    e = emu(params, tmp_path, "p", hidden_variables=("payments_count", "payment_approved"))
    e.step(date(2018, 2, 5))
    port = e.observe()
    assert port.scope.data_cut <= datetime(2018, 2, 5, 3, tzinfo=U)
    q = lambda v, port=port: port.query(GetSeries(schema_version="2.0", request_id="x", variables=(v,), scope=port.scope,
                                       data_cut=datetime(2018, 2, 5, 3, tzinfo=U), purpose="t", request="get_series", grain="1w",
                                       window=TimeWindow(start=datetime(2018, 1, 1, 3, tzinfo=U), end=datetime(2018, 2, 5, 3, tzinfo=U))))
    assert q("payments_count").status == "UNSUPPORTED"
    assert e.grant_measurement("payments_count") and not e.grant_measurement("not_hidden")
    port = e.observe()
    assert q("payments_count").status == "UNSUPPORTED"            # old port object unchanged
    r = port.query(GetSeries(schema_version="2.0", request_id="x", variables=("payments_count",), scope=port.scope,
                             data_cut=datetime(2018, 2, 5, 3, tzinfo=U), purpose="t", request="get_series", grain="1w",
                             window=TimeWindow(start=datetime(2018, 1, 1, 3, tzinfo=U), end=datetime(2018, 2, 5, 3, tzinfo=U))))
    assert r.status == "OK"
    blob = r.model_dump_json()
    assert "privileged" not in blob and str(e.priv) not in blob
