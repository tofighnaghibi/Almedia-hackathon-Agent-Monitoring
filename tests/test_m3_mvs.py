"""M3 headless closed loop: gap -> policy grant -> re-run -> sealed-answer scoring; deterministic per seed."""
import pytest

from mkt import pipeline, registry
from mkt.harness import mvs
from mkt.sim import chain


@pytest.fixture(scope="module")
def params(cfg):
    try:
        registry.load("olist", cfg.artifacts)
    except registry.MissingSource:
        pytest.skip("Olist not registered")
    return chain.fit(pipeline.canonical_olist(cfg))


@pytest.mark.parametrize("scenario", ["8a", "8b"])
def test_closed_loop(params, tmp_path, scenario):
    r1 = mvs.run(params, tmp_path / "a", seed=7, force=scenario)
    r2 = mvs.run(params, tmp_path / "b", seed=7, force=scenario)
    assert r1["rev1_outcome"] == "WAITING_FOR_EVIDENCE" and r1["granted"] == ["payments_count"]
    assert r1["correct"], r1["rev2_findings"]
    assert r1["ledger_hash"] == r2["ledger_hash"] and r1["rev2_findings"] == r2["rev2_findings"]
    sink = (tmp_path / "a" / "public" / "sink.jsonl").read_text()
    for leak in ('"scenario"', '"expected"', "logging.drop", "demand.multiplier", '"layer"'):   # sealed answer never public
        assert leak not in sink
