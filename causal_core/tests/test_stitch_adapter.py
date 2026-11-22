"""Stitch adapter (CP-038). Offline on an invented fixture zip; an optional check runs on the real sample zip if it
is present next to the repo (it is not committed).

    python -m unittest tests.test_stitch_adapter -v
"""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from adapters.sources.stitch_port import StitchEvidencePort, load_stitch
from evaluation import stitch_cases as SC

SCOPE = {"environment_id": "fixture-chain", "environment_kind": "emulator", "generator_version": "fx-1"}
LINEAGE = {"source_id": "fx", "origin_kind": "synthetic_generated", "lineage_root_id": "fx:root", "content_hash": "a" * 64,
           "generator_version": "fx-1"}


def definition(vid="orders_count", **extra):
    return {"schema_version": "2.0", "variable_id": vid, "scope": SCOPE, "provenance": {"source_id": "fx", "method": "adapter_declared"},
            "kind": "metric", "unit": "orders", "value_schema": {"type": "numeric", "dtype": "count", "minimum": 0},
            "aggregation_kind": "sum", "population_coverage": {"population_id": "orders", "coverage": "full"},
            "time_semantics": {"grain": "1w", "window": "tumbling", "timezone": "America/Sao_Paulo"},
            "extensions": {"mkt:layer": "L5", "mkt:status": "served"}, **extra}


def sample(vid, points, oid):
    obs = {"schema_version": "2.0", "observation_id": oid, "variable_id": vid, "source_id": "fx", "record_key": oid,
           "revision": 1, "scope": SCOPE, "lineage": LINEAGE, "payload": {"shape": "time_series", "points": points}}
    ev = {"evidence_id": f"ev:{oid}", "lineage": LINEAGE, "scope": SCOPE, "provenance_role": "SYNTHETIC_GENERATED",
          "content_hash": "b" * 64}                                    # no schema_version: Phase-1 style
    return {"observation": obs, "evidence": ev}


def fixture_zip() -> Path:
    d = Path(tempfile.mkdtemp(prefix="stitch-fx-"))
    files = {
        "orders_count.json": {"variable": "orders_count", "definition": definition(),
                              "samples": [sample("orders_count", [{"t": "2018-01-01T02:00:00Z", "value": 10.0},
                                                                  {"t": "2018-01-08T02:00:00Z", "value": 12.0}], "obs:a")]},
        "offset_metric.json": {"variable": "late_count", "definition": definition("late_count"),
                               "samples": [sample("late_count", [{"t": "2018-01-01T00:00:00+02:00", "value": 1.0}], "obs:b")]},
        "bad_evidence.json": {"variable": "returns_count", "definition": definition("returns_count"),
                              "samples": [sample("returns_count", [{"t": "2018-01-01T02:00:00Z", "value": 1.0}], "obs:c")]},
        "bad_definition.json": {"variable": "broken", "definition": {**definition("broken"), "kind": "metric", "unit": " "},
                                "samples": []},
        "bad_observation.json": {"variable": "neg_count", "definition": definition("neg_count"),
                                 "samples": [sample("neg_count", [{"t": "2018-01-01T02:00:00Z", "value": -5.0}], "obs:d")]},
    }
    bad = files["bad_evidence.json"]["samples"][0]["evidence"]
    bad["provenance_role"] = "REAL_OBSERVED"                         # contradicts the emulator scope: must not be "fixed"
    z = d / "stitch_samples.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for name, body in files.items():
            zf.writestr(f"samples/{name}", json.dumps(body))
        zf.writestr("samples/SAMPLES_REPORT.md", "# report")
    return z


class StitchAdapterCP038(unittest.TestCase):
    def test_version_injected_only_when_everything_else_complies(self):
        L = load_stitch(fixture_zip())
        self.assertEqual(L.counts["evidence_schema_version_injected"], 3)          # obs:a, obs:b, obs:d evidence
        q = {(f, item) for f, item, _ in L.quarantine}
        self.assertIn(("bad_evidence.json", "evidence[0]"), q)                     # wrong role stays rejected
        self.assertTrue(all(e.schema_version == "2.0" for e in L.evidence))

    def test_offsets_are_converted_and_recorded_other_errors_quarantined(self):
        L = load_stitch(fixture_zip())
        conv = next(o for o in L.observations if o["variable_id"] == "late_count")
        self.assertEqual(conv["payload"]["points"][0]["t"], "2017-12-31T22:00:00Z")
        self.assertEqual(conv["clocks"]["source_timezone"], "UTC+02:00")
        self.assertEqual(L.counts["observations_converted_to_utc"], 1)
        reasons = {f: r for f, _, r in L.quarantine}
        self.assertIn("bad_definition.json", reasons)                             # never repaired
        self.assertIn("bad_observation.json", reasons)                            # negative count violates its schema
        self.assertNotIn("broken", L.definitions)

    def test_port_serves_accepted_observations_and_definitions(self):
        L = load_stitch(fixture_zip())
        p = StitchEvidencePort(L)
        self.assertEqual(p.manifest().scopes[0].environment_id, "fixture-chain")
        self.assertIn("orders_count", p.definitions())


REAL = SC.DEFAULT_ZIP


@unittest.skipUnless(REAL.exists(), "real stitch_samples.zip not present")
class RealStitchSamples(unittest.TestCase):
    def test_real_samples_load_and_identities_verify(self):
        L = load_stitch(REAL)
        self.assertEqual(L.counts["definitions"], 106)
        self.assertEqual(L.counts["observations_accepted"] + L.counts["observations_converted_to_utc"], 340)
        self.assertEqual(L.quarantine, [])
        ids = SC.identities(L)
        self.assertGreaterEqual(sum(r["verdict"] == "VERIFIED_IDENTITY" for r in ids), 10)
        self.assertTrue(all(r["verdict"] in ("VERIFIED_IDENTITY", "UNVERIFIED") for r in ids))   # no mismatches


if __name__ == "__main__":
    unittest.main()
