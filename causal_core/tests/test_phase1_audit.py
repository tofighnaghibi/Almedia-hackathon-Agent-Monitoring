"""Phase 1 retrospective audit: tests for every finding (docs/audits/phase1/FINDINGS.md).

Each class names the finding or checkpoint it proves. These tests deliberately avoid the
emulator where the audit asks for independent, hand-constructed evidence.

    python -m unittest discover -s tests -t . -v
"""
from __future__ import annotations

import json
import unittest

from causal_core import api
from causal_core.contracts import capabilities as cap
from causal_core.contracts import evidence as ev
from causal_core.contracts import tasks as tk
from causal_core.migration import legacy

NATIVE_OUTPUTS = (ev.EvidenceRecord, ev.IdentificationAssessment, ev.EffectClaim, ev.EvidenceGap,
                  tk.TaskRevision, tk.WorkItem, tk.Checkpoint, cap.CapabilityResponse, cap.ProviderManifest,
                  legacy.MigrationResult, api.CompatibilityReport)


class NativeOutputsCP002(unittest.TestCase):
    """CP-002: plan §C 'schema_version=2.0 mandatory for native outputs'."""

    def test_every_native_output_requires_schema_version(self):
        for model in NATIVE_OUTPUTS:
            field = model.model_fields.get("schema_version")
            self.assertIsNotNone(field, model.__name__)
            self.assertTrue(field.is_required(), f"{model.__name__}.schema_version must be explicit, not defaulted")

    def test_wrong_version_is_rejected(self):
        gap = {"schema_version": "1.2", "gap_id": "g", "task_id": "t", "missing": "x",
               "recommended_measurements": ["y"]}
        with self.assertRaises(Exception):
            ev.EvidenceGap.model_validate_json(json.dumps(gap))
        gap["schema_version"] = "2.0"
        self.assertEqual(ev.EvidenceGap.model_validate_json(json.dumps(gap)).gap_id, "g")

    def test_manifest_latency_and_cost_unknown_by_default(self):
        m = cap.ProviderManifest.model_validate_json(json.dumps({
            "schema_version": "2.0", "provider_id": "p", "manifest_version": "1", "supported_requests": ["get_series"],
            "max_rows": 10, "scopes": [{"environment_id": "x", "environment_kind": "staging"}]}))
        self.assertIsNone(m.expected_latency_ms)
        self.assertIsNone(m.cost_units_per_request)


class UtcOnTheWireCP003(unittest.TestCase):
    """CP-003: v3.2 §2.2 'timezone-aware UTC values on the wire, original source timezone retained'."""

    def _clock(self, value: str):
        from causal_core.contracts.common import Clock
        return Clock.model_validate_json(json.dumps({"quality": "KNOWN", "value": value}))

    def test_utc_spellings_accepted(self):
        self.assertEqual(self._clock("2026-11-19T00:00:00Z"), self._clock("2026-11-19T00:00:00+00:00"))

    def test_non_utc_offsets_rejected_not_converted(self):
        for value in ("2026-11-19T02:00:00+02:00", "2026-11-18T19:00:00-05:00"):
            with self.assertRaises(Exception) as ctx:
                self._clock(value)
            self.assertIn("UTC on the wire", str(ctx.exception))

    def test_source_zone_is_carried_separately(self):
        from causal_core.contracts.observations import ObservationClocks
        c = ObservationClocks.model_validate_json(json.dumps(
            {"observed_at": {"quality": "KNOWN", "value": "2026-11-19T00:00:00Z"}, "source_timezone": "Europe/Berlin"}))
        self.assertEqual(c.source_timezone, "Europe/Berlin")


class EstimandScopesCP004(unittest.TestCase):
    """CP-004: v3.2 §2.1 EstimandSpec requires 'source/target scope'; I05 scoped estimands."""

    PROD = {"environment_id": "prod-eu", "environment_kind": "production"}
    EMU = {"environment_id": "emu-1", "environment_kind": "emulator", "generator_version": "g1"}

    def _estimand(self, **scopes):
        from causal_core.contracts.studies import EstimandSpec
        body = {"outcome": "y", "contrast": {"treatment": "t", "control": "c"}, "population": "p",
                "horizon": "7d", "functional": "ATE", **scopes}
        return EstimandSpec.model_validate_json(json.dumps(body))

    def test_both_scopes_required(self):
        for scopes in ({}, {"source_scope": self.PROD}, {"target_scope": self.PROD}):
            with self.assertRaises(Exception):
                self._estimand(**scopes)

    def test_transport_is_explicit(self):
        self.assertFalse(self._estimand(source_scope=self.PROD, target_scope=self.PROD).requires_transport)
        self.assertTrue(self._estimand(source_scope=self.EMU, target_scope=self.PROD).requires_transport)


class EntityDimensionCP005(unittest.TestCase):
    """CP-005: v3.2 §2.1 EntitySpec/DimensionSpec; §3.1 'top_K alone not complete'; §3.3 step 3."""

    BASE = {"schema_version": "2.0",
            "scope": {"environment_id": "plant-3", "environment_kind": "production"},
            "provenance": {"source_id": "historian", "method": "adapter_declared"}}

    def test_uncertain_parent_is_not_exact(self):
        e = api.parse_entity_spec({**self.BASE, "entity_type_id": "pump", "key_fields": ["pump_serial"],
                                   "parent_entity_type_id": "line", "parent_match_confidence": 0.8})
        self.assertFalse(e.parent_is_exact)
        with self.assertRaises(api.ContractError):   # parent without confidence
            api.parse_entity_spec({**self.BASE, "entity_type_id": "pump", "key_fields": ["s"],
                                   "parent_entity_type_id": "line"})

    def test_partition_policies(self):
        d = lambda **kw: api.parse_dimension_spec({**self.BASE, "dimension_id": "shift", **kw})
        self.assertTrue(d(partition_policy="exhaustive_declared", values=["early", "late", "night"]).complete_partition)
        self.assertTrue(d(partition_policy="top_k_plus_other", top_k=2, values=["early", "late", "OTHER"],
                          other_code="OTHER").complete_partition)
        self.assertFalse(d(partition_policy="open_unknown").complete_partition)
        for bad in ({"partition_policy": "top_k_plus_other", "top_k": 2},                 # no declared OTHER
                    {"partition_policy": "open_unknown", "other_code": "OTHER"},          # remainder is not OTHER
                    {"partition_policy": "exhaustive_declared"},                          # no values
                    {"partition_policy": "exhaustive_declared", "values": ["a"], "missing_code": "NA"}):
            with self.subTest(bad=bad), self.assertRaises(api.ContractError):
                d(**bad)


# ── Tier 1 fixes and missing evidence (FINDINGS F-1, F-6, F-7, F-13) ───────


def _fixture(*parts):
    from evaluation.fixture_io import PROJECT_ROOT
    return json.loads(PROJECT_ROOT.joinpath("tests", "fixtures", *parts).read_text())


def _envelope(payload: dict, variable_id: str = "v1") -> dict:
    """A hand-written observation envelope (no emulator)."""
    return {"schema_version": "2.0", "observation_id": "o1", "variable_id": variable_id, "payload": payload,
            "source_id": "src", "record_key": "k1", "revision": 1,
            "lineage": {"source_id": "src", "origin_kind": "original", "lineage_root_id": "r1", "content_hash": "e" * 64},
            "scope": {"environment_id": "env", "environment_kind": "production"}}


SHAPES = {
    "scalar": {"shape": "scalar", "value": 0.25},
    "categorical": {"shape": "categorical", "category": "boost"},
    "aggregate_stats": {"shape": "aggregate_stats", "n": 400, "numerator": 380, "denominator": 400, "se": 0.011},
    "time_series": {"shape": "time_series", "points": [{"t": "2026-11-20T00:00:00Z", "value": 1.5},
                                                      {"t": "2026-11-20T01:00:00Z", "value": None}]},
    "panel": {"shape": "panel", "cells": [{"unit": "a", "period": 0, "value": 1.0}, {"unit": "a", "period": 1, "value": 2.0}]},
    "event_batch": {"shape": "event_batch", "events": [{"event_id": "e1", "attributes": {"k": "x"}}]},
    "assignment_outcomes": {"shape": "assignment_outcomes", "study_id": "s", "arms": ["t", "c"],
                            "units": [{"unit_id": "u1", "arm": "t", "outcome": 1.0, "exposure": 1},
                                      {"unit_id": "u2", "arm": "c", "outcome": None}]},
    "state_trajectory": {"shape": "state_trajectory", "samples": [{"t": "2026-11-20T00:00:00Z", "state": "eco"},
                                                                 {"t": "2026-11-20T00:05:00Z", "state": "boost"}]},
}

SHAPE_NEGATIVES = {   # one substantive constraint per shape, not just parsing
    "scalar": {"shape": "scalar", "value": "0.25"},
    "categorical": {"shape": "categorical", "category": " "},
    "aggregate_stats": {"shape": "aggregate_stats", "n": 0, "mean": 2.0},
    "time_series": {"shape": "time_series", "points": []},
    "panel": {"shape": "panel", "cells": [{"unit": "a", "period": 0, "value": 1.0}, {"unit": "a", "period": 0, "value": 2.0}]},
    "event_batch": {"shape": "event_batch", "events": []},
    "assignment_outcomes": {"shape": "assignment_outcomes", "study_id": "s", "arms": ["t"],
                            "units": [{"unit_id": "u1", "arm": "t"}]},
    "state_trajectory": {"shape": "state_trajectory", "samples": [{"t": "2026-11-20T00:05:00Z", "state": 1.0},
                                                                 {"t": "2026-11-20T00:00:00Z", "state": 2.0}]},
}


class ShapesT1(unittest.TestCase):
    """F-7 / P1-010, P1-011: every one of the 8 shapes round-trips, and each enforces a real constraint."""

    def test_all_eight_shapes_round_trip(self):
        self.assertEqual(len(SHAPES), 8)
        for shape, payload in SHAPES.items():
            with self.subTest(shape=shape):
                obs = api.parse_observation(_envelope(payload))
                self.assertEqual(obs.payload.shape, shape)
                self.assertEqual(api.parse_observation(obs.model_dump_json()), obs)

    def test_each_shape_rejects_its_defect(self):
        for shape, payload in SHAPE_NEGATIVES.items():
            with self.subTest(shape=shape), self.assertRaises(api.ContractError):
                api.parse_observation(_envelope(payload))


class StrictnessT1(unittest.TestCase):
    """F-7 / P1-051 + F-13: no coercion, closed models, non-finite numbers refused."""

    def test_no_coercion_and_closed_models(self):
        base = _envelope(SHAPES["scalar"])
        cases = {"str->int": {**base, "revision": "3"}, "float->int": {**base, "revision": 3.0},
                 "str->bool": {**_envelope({"shape": "categorical", "category": "x"}), "dq": {"completeness": "1"}},
                 "unknown tag": {**base, "payload": {"shape": "histogram"}},
                 "extra field": {**base, "confidence": 0.9}, "null required": {**base, "source_id": None}}
        for name, raw in cases.items():
            with self.subTest(case=name), self.assertRaises(api.ContractError):
                api.parse_observation(raw)

    def test_non_finite_numbers_refused(self):
        for token in ("NaN", "Infinity", "-Infinity"):
            raw = json.dumps(_envelope({"shape": "scalar", "value": 0.5})).replace("0.5}", token + "}", 1)
            with self.subTest(token=token), self.assertRaises(api.ContractError):
                api.parse_observation(raw)


class AggregateBoundsT1(unittest.TestCase):
    """F-1 / P1-049: an aggregate's implied ratio must respect the definition's declared range."""

    DEF = {"schema_version": "2.0", "kind": "metric", "variable_id": "v1",
           "value_schema": {"type": "numeric", "dtype": "proportion", "minimum": 0, "maximum": 1},
           "scope": {"environment_id": "env", "environment_kind": "production"},
           "provenance": {"source_id": "src", "method": "adapter_declared"}}

    def test_impossible_ratio_refused(self):
        d = api.parse_definition(self.DEF)
        bad = _envelope({"shape": "aggregate_stats", "n": 400, "numerator": 600, "denominator": 400})
        with self.assertRaises(api.ContractError) as ctx:
            api.parse_observation(bad, d)
        self.assertEqual(ctx.exception.code, "PAYLOAD_VIOLATES_DEFINITION")
        ok = _envelope({"shape": "aggregate_stats", "n": 400, "numerator": 300, "denominator": 400})
        self.assertEqual(api.parse_observation(ok, d).payload.numerator, 300)
        empty = _envelope({"shape": "aggregate_stats", "n": 0, "numerator": 0, "denominator": 0})
        self.assertIsNone(api.parse_observation(empty, d).payload.mean)   # 0/0 is undefined, not a violation


class OtherContractRulesT1(unittest.TestCase):
    """F-7: rules that existed in code but had no executable evidence."""

    def test_capability_response_must_explain(self):
        base = {"schema_version": "2.0", "request_id": "r", "request_fingerprint": "a" * 64}
        for bad in ({**base, "status": "UNSUPPORTED"}, {**base, "status": "OK", "truncated": True},
                    {**base, "status": "FAILED", "reason": "x", "observations": [_envelope(SHAPES["scalar"])]}):
            with self.subTest(bad=bad["status"]), self.assertRaises(Exception):
                cap.CapabilityResponse.model_validate_json(json.dumps(bad))

    def test_categorical_does_not_assume_order_or_exhaustiveness(self):
        from causal_core.contracts.variables import CategoricalValueSchema
        s = CategoricalValueSchema.model_validate_json('{"type": "categorical", "categories": ["a", "b"]}')
        self.assertEqual((s.ordered, s.exhaustive), (False, False))

    def test_extensions_are_namespaced_scalars(self):
        d = dict(AggregateBoundsT1.DEF)
        self.assertEqual(api.parse_definition({**d, "extensions": {"acme:owner": "team-x"}}).extensions["acme:owner"], "team-x")
        for bad in ({"owner": "x"}, {"acme:nested": {"a": 1}}):
            with self.subTest(bad=bad), self.assertRaises(api.ContractError):
                api.parse_definition({**d, "extensions": bad})

    def test_observation_mechanism_kinds_are_closed(self):
        from causal_core.contracts.observations import ObservationMechanismSpec
        ok = {"mechanism_id": "m", "kind": "REPORTING_LAG", "affects": ["v1"], "description": "6h delay"}
        self.assertEqual(ObservationMechanismSpec.model_validate_json(json.dumps(ok)).kind, "REPORTING_LAG")
        for bad in ({**ok, "kind": "BIAS"}, {**ok, "affects": []}):
            with self.subTest(bad=bad), self.assertRaises(Exception):
                ObservationMechanismSpec.model_validate_json(json.dumps(bad))


class HandFixturesT1(unittest.TestCase):
    """Audit probe E: hand-constructed sensor and study fixtures that share no code with the emulator."""

    def test_sensor_state_and_events_need_no_metric_fields(self):
        fx = _fixture("contracts", "hand_sensor.json")
        for d_key, o_key in (("vibration_definition", "vibration_observation"), ("valve_definition", "valve_observation")):
            d = api.parse_definition(fx[d_key])
            obs = api.parse_observation(fx[o_key], d)
            dumped = json.loads(obs.model_dump_json()) | json.loads(d.model_dump_json())
            for forbidden in ("numerator", "denominator", "formula_ast", "time_semantics", "aggregation_kind",
                              "population_coverage", "experiment_id", "incident_id"):
                self.assertNotIn(forbidden, json.dumps(dumped), f"{o_key} carries {forbidden}")
        vib = api.parse_observation(fx["vibration_observation"])
        self.assertEqual(api.replay_visibility(vib, vib.clocks.observed_at.value)[0], "REFUSED")   # ESTIMATED clock

    def test_cluster_study_with_different_units_is_valid(self):
        fx = _fixture("contracts", "hand_cluster_study.json")
        design = api.parse_study_design(fx["design"])
        self.assertNotEqual(design.assignment_unit, design.outcome_unit)
        outcomes = api.parse_observation(fx["outcomes"])
        self.assertEqual({u.cluster_id for u in outcomes.payload.units}, {"cls-a", "cls-b"})
        self.assertIsNone(outcomes.payload.units[1].outcome)   # attrition kept as missing, not zero


class CompatibilityFixturesT1(unittest.TestCase):
    """Plan §C deliverable 'compatibility fixtures': hand-written v1.x records with hand-written expectations."""

    def test_v1_records(self):
        from datetime import datetime, timezone
        when = datetime(2026, 11, 21, tzinfo=timezone.utc)
        for case in _fixture("compat", "v1_records.json")["cases"]:
            with self.subTest(case=case["name"]):
                r = api.migrate_legacy_record(case["raw"], migrated_at=when)
                self.assertEqual(r.outcome, case["outcome"], r.reasons)
                if "reason_contains" in case:
                    self.assertTrue(any(case["reason_contains"] in x for x in r.reasons), r.reasons)
                exp, rec = case.get("expect", {}), r.record
                if "formula_root_op" in exp:
                    self.assertEqual((rec.formula_ast.op, rec.time_semantics.window, rec.population_coverage.coverage),
                                     (exp["formula_root_op"], exp["time_window"], exp["population_coverage"]))
                if "available_at_quality" in exp:
                    self.assertIsNone(rec.clocks.observed_at)
                    self.assertEqual((rec.clocks.available_at.quality, rec.revision, rec.scope.environment_kind),
                                     (exp["available_at_quality"], exp["revision"], exp["scope"]))
                if "lag" in exp:
                    self.assertEqual([rec.lag.min_s, rec.lag.max_s], exp["lag"])
                if "reasons" in exp:
                    self.assertEqual(list(rec.status_reasons), exp["reasons"])

    def test_migration_is_deterministic(self):
        from datetime import datetime, timezone
        raw = _fixture("compat", "v1_records.json")["cases"][0]["raw"]
        t1, t2 = datetime(2026, 1, 1, tzinfo=timezone.utc), datetime(2026, 6, 1, tzinfo=timezone.utc)
        self.assertEqual(api.migrate_legacy_record(raw, migrated_at=t1), api.migrate_legacy_record(raw, migrated_at=t1))
        a, b = api.migrate_legacy_record(raw, migrated_at=t1), api.migrate_legacy_record(raw, migrated_at=t2)
        self.assertEqual((a.receipt.original_sha256, a.record), (b.receipt.original_sha256, b.record))


class PurityT1(unittest.TestCase):
    """G01 'pure-method I/O monkeypatch trap' for the Phase 1 pure layer (contracts, api, migration)."""

    def test_parsing_performs_no_io(self):
        from unittest import mock
        fx = _fixture("contracts", "hand_sensor.json")
        raw_v1 = _fixture("compat", "v1_records.json")["cases"][0]["raw"]
        from datetime import datetime, timezone
        trap = mock.Mock(side_effect=AssertionError("I/O attempted in the pure layer"))
        with mock.patch("builtins.open", trap), mock.patch("socket.socket", trap), \
             mock.patch("subprocess.Popen", trap):
            d = api.parse_definition(fx["vibration_definition"])
            api.parse_observation(fx["vibration_observation"], d)
            api.assess_contract_compatibility(d, "sequential_monitoring")
            api.migrate_legacy_record(raw_v1, migrated_at=datetime(2026, 1, 1, tzinfo=timezone.utc))
        trap.assert_not_called()

    def test_pure_modules_import_no_io_libraries(self):
        import ast
        from evaluation.fixture_io import PROJECT_ROOT
        banned = {"socket", "sqlite3", "subprocess", "requests", "urllib", "http", "os", "pathlib", "shutil"}
        pure = [*PROJECT_ROOT.joinpath("causal_core", "contracts").glob("*.py"),
                *PROJECT_ROOT.joinpath("causal_core", "migration").glob("*.py"), PROJECT_ROOT / "causal_core" / "api.py"]
        for path in pure:
            for node in ast.walk(ast.parse(path.read_text())):
                names = ([a.name for a in node.names] if isinstance(node, ast.Import)
                         else [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else [])
                for name in names:
                    self.assertNotIn(name.split(".")[0], banned, f"{path.name} imports {name}")
                if isinstance(node, ast.Call) and getattr(node.func, "id", None) in {"open", "print", "input"}:
                    self.fail(f"{path.name}:{node.lineno} calls {node.func.id}")


class AttestationT1(unittest.TestCase):
    """P1-024: a 'signed' attestation without a signature is a forged claim of trust."""

    def test_signed_requires_signature(self):
        from causal_core.contracts.common import SourceAttestation
        ok = {"source_id": "s", "method": "signed", "signature": "sig:abc"}
        self.assertEqual(SourceAttestation.model_validate_json(json.dumps(ok)).method, "signed")
        with self.assertRaises(Exception):
            SourceAttestation.model_validate_json(json.dumps({"source_id": "s", "method": "signed"}))


class SchemaFreezeT1(unittest.TestCase):
    """F-6 / plan §C deliverable 'frozen schemas': the models must match docs/phase1/schemas/."""

    def test_no_schema_drift(self):
        from scripts.export_schemas import check
        self.assertEqual(check(), [], "contract changed: log a decision, then re-run python -m scripts.export_schemas")


if __name__ == "__main__":
    unittest.main()
