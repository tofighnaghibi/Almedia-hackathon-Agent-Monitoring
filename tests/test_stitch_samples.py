"""Re-check the samples written by scripts/stitch_samples.py (skips if they have not been generated)."""
import json
import pathlib

import pytest

from causal_core.contracts.evidence import EvidenceRecord
from causal_core.contracts.observations import Observation
from mkt.stitch.sample_checks import check_sample
from mkt.variable_catalog import CATALOG, build

OUT = pathlib.Path.home() / "data/mkt/stitch/samples"
pytestmark = pytest.mark.skipif(not OUT.exists(), reason="run scripts/stitch_samples.py first")


@pytest.mark.parametrize("var", CATALOG, ids=lambda v: v.variable_id)
def test_sample_conforms(var):
    f = OUT / f"{var.variable_id}.json"
    if not f.exists():
        assert var.entity == "service" and var.kind == "metric", f"no sample for {var.variable_id}"      # only live telemetry may be absent
        pytest.skip("telemetry needs a saved storefront run")
    data = json.loads(f.read_text())
    for s in data["samples"]:
        o = Observation.model_validate_json(json.dumps(s["observation"]))
        e = EvidenceRecord.model_validate_json(json.dumps(s["evidence"]))
        d = build(var, o.scope)
        assert check_sample(var, d, o, e) == []
