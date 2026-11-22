"""Phase 1 gates: G02 types, G03 migration, G06 time firewall, G07 evidence independence,
plus the stop condition, round trips and the P0 no-write port surface.

    python -m unittest discover -s tests -t . -v
"""
from __future__ import annotations

import json
import random
import unittest
from datetime import datetime, timedelta, timezone

from adapters.legacy.evidence_queries_v1 import UnknownLegacyMethod, to_typed_request
from causal_core import api
from causal_core.contracts import (METRIC_ONLY_FIELDS, CategoricalVariableDefinition, EffectClaim,
                                   EventVariableDefinition, EvidenceRecord, StateVariableDefinition)
from causal_core.contracts.observations import Observation
from causal_core.contracts.ports import P0_PORTS
from emulator import coldchain
from evaluation import fixture_io as io
from evaluation.adversarial_contracts import build_cases, run_case

NOW = datetime(2026, 11, 21, 12, tzinfo=timezone.utc)
WORLD = coldchain.emit()


class StopCondition(unittest.TestCase):
    """Plan §C stop condition: no nonmetric kind may require (or even accept) metric fields."""

    def test_nonmetric_models_have_no_metric_fields(self):
        forbidden = set(METRIC_ONLY_FIELDS) | {"numerator", "denominator", "time_bucket", "experiment_id", "alert"}
        for model in (StateVariableDefinition, EventVariableDefinition, CategoricalVariableDefinition):
            self.assertFalse(forbidden & set(model.model_fields), model.__name__)

    def test_nonmetric_required_fields_are_universal_only(self):
        for model in (StateVariableDefinition, EventVariableDefinition, CategoricalVariableDefinition):
            required = {n for n, f in model.model_fields.items() if f.is_required()}
            self.assertEqual(required, {"schema_version", "variable_id", "scope", "provenance", "kind",
                                        "value_schema"}, model.__name__)


class RoundTripG02(unittest.TestCase):
    def test_world_round_trips(self):
        defs = {k: api.parse_definition(v) for k, v in WORLD.definitions.items()}
        for d in defs.values():
            self.assertEqual(api.parse_definition(d.model_dump_json()), d)
        for raw in WORLD.observations:
            obs = api.parse_observation(raw, defs[raw["variable_id"]])
            self.assertEqual(api.parse_observation(obs.model_dump_json()), obs)

    def test_dict_and_bytes_parse_identically(self):
        raw = WORLD.observations[0]
        self.assertEqual(api.parse_observation(raw), api.parse_observation(json.dumps(raw).encode()))

    def test_randomized_payload_round_trips(self):
        rng = random.Random(7)
        base = next(o for o in WORLD.observations if o["variable_id"] == "on_time_dispatch_rate")
        for i in range(200):
            n = rng.randint(1, 10_000)
            num = rng.randint(0, n)
            payload = rng.choice([
                {"shape": "aggregate_stats", "n": n, "numerator": num, "denominator": n, "se": rng.random()},
                {"shape": "time_series", "points": [
                    {"t": (NOW + timedelta(hours=k)).isoformat(), "value": rng.uniform(0, 1)} for k in range(rng.randint(1, 30))]},
                {"shape": "scalar", "value": rng.uniform(0, 1)}])
            raw = {**base, "observation_id": f"rt-{i}", "payload": payload}
            obs = api.parse_observation(raw)
            self.assertEqual(api.parse_observation(obs.model_dump_json()), obs)

    def test_absent_covariance_is_unknown_not_zero(self):
        obs = api.parse_observation(next(o for o in WORLD.observations if o["payload"]["shape"] == "aggregate_stats"))
        self.assertIsNone(obs.payload.covariance_ref)
        self.assertEqual(obs.payload.iid, "UNKNOWN")


class AdversarialCorpus(unittest.TestCase):
    def test_every_case_behaves(self):
        cases = build_cases(WORLD)
        self.assertGreaterEqual(len(cases), 30)
        for case in cases:
            with self.subTest(case=case.case_id, category=case.category):
                ok, observed = run_case(case)
                self.assertTrue(ok, f"expected {case.expect} ({case.match!r}), got {observed}")


class MigrationG03(unittest.TestCase):
    """Differential check: the core migrator must reproduce the Phase 0 frozen expectations."""

    def setUp(self):
        self.fx = json.loads((io.FIXTURE_DIR / "09_migrated_legacy_record.json").read_text())
        self.raw = self.fx["input"]["raw_records"]
        self.want = self.fx["expected"]

    def test_receipts_match_frozen_hashes(self):
        for name, raw in self.raw.items():
            r = api.migrate_legacy_record(raw, migrated_at=NOW)
            self.assertEqual(r.receipt.original_sha256, self.want["receipts"][name]["original_sha256"])
            self.assertEqual(r.original_text, raw)

    def test_observation_matches_frozen(self):
        r = api.migrate_legacy_record(self.raw["observation"], migrated_at=NOW)
        w, obs = self.want["observation"], r.record
        self.assertEqual(r.outcome, "MIGRATED")
        self.assertEqual(obs.payload.shape, "aggregate_stats")
        for key in ("numerator", "denominator", "n", "se"):
            self.assertEqual(getattr(obs.payload, key), w["preserved"][key])
        self.assertEqual(obs.revision, w["preserved"]["revision"])
        self.assertEqual(obs.scope.environment_kind, "legacy_unscoped")
        self.assertEqual(obs.lineage.lineage_root_id, w["lineage_root"])
        self.assertEqual((obs.payload.iid, obs.payload.covariance_ref), ("UNKNOWN", None))
        self.assertEqual(obs.clocks.available_at.quality, "UNKNOWN")

    def test_edges_match_frozen(self):
        hyp = api.migrate_legacy_record(self.raw["hypothesis_edge"], migrated_at=NOW).record
        self.assertEqual(hyp.kind, self.want["hypothesis_edge"]["edge_kind"])
        cert = api.migrate_legacy_record(self.raw["certified_edge"], migrated_at=NOW)
        self.assertEqual(cert.outcome, "MIGRATED_REVIEW_REQUIRED")
        self.assertEqual(cert.record.status, self.want["certified_edge"]["status"])
        self.assertEqual(list(cert.record.status_reasons), self.want["certified_edge"]["reasons"])

    def test_migration_never_raises(self):
        for raw in (b"\xff\xfe", "not json", "[1]", '{"schema_version":"9"}', '{"schema_version":"1.1","kind":"Nope"}',
                    '{"schema_version":"1.1","kind":"Observation"}'):
            r = api.migrate_legacy_record(raw, migrated_at=NOW)
            self.assertEqual(r.outcome, "QUARANTINED")
            self.assertTrue(r.reasons)

    def test_world_legacy_stream(self):
        outcomes = [api.migrate_legacy_record(raw, migrated_at=NOW).outcome for raw in WORLD.legacy_records]
        self.assertEqual(outcomes[-1], "QUARANTINED")           # ExperimentRecord: unattested design
        self.assertTrue(all(o == "MIGRATED" for o in outcomes[:-1]))


class TimeFirewallG06(unittest.TestCase):
    """Replay must agree with the Phase 0 oracle on fixture 03 (differential check)."""

    def test_replay_matches_phase0_oracle(self):
        fx = json.loads((io.FIXTURE_DIR / "03_dq_reporting_lag.json").read_text())
        replay_time = datetime.fromisoformat(fx["input"]["replay_time"])
        base = WORLD.observations[0]
        got = {}
        for o in fx["input"]["observations"]:
            avail = {"quality": o["available_at_quality"]}
            if o["available_at"]:
                avail["value"] = o["available_at"]
            raw = {**base, "observation_id": o["id"],
                   "clocks": {"observed_at": {"quality": "KNOWN", "value": o["observed_at"]}, "available_at": avail}}
            got[o["id"]] = api.replay_visibility(api.parse_observation(raw), replay_time)[0]
        want = fx["expected"]
        self.assertEqual([k for k, v in got.items() if v == "VISIBLE"], want["replay_visible"])
        self.assertEqual([k for k, v in got.items() if v == "NOT_YET_AVAILABLE"], [h["id"] for h in want["replay_hidden"]])
        self.assertEqual([k for k, v in got.items() if v == "REFUSED"], [r["id"] for r in want["replay_refused"]])

    def test_timeless_observation_needs_no_clock_but_is_not_replayable(self):
        raw = {k: v for k, v in WORLD.observations[0].items() if k != "clocks"}
        obs = api.parse_observation(raw)
        self.assertEqual(api.replay_visibility(obs, NOW), ("REFUSED", "NO_AVAILABILITY_CLOCK"))


class EvidenceIndependenceG07(unittest.TestCase):
    def _record(self, origin: str, role: str, env: str, gen: str | None = "g1"):
        lineage = {"source_id": "s", "origin_kind": origin, "lineage_root_id": "r", "content_hash": "a" * 64}
        if gen:
            lineage["generator_version"] = gen
        if origin == "synthetic_replica":
            lineage["parent_evidence_ids"] = ["e0"]
        scope = {"environment_id": "x", "environment_kind": env}
        if env == "emulator":
            scope["generator_version"] = "g1"
        return EvidenceRecord.model_validate_json(json.dumps({
            "schema_version": "2.0", "evidence_id": "e1", "lineage": lineage, "scope": scope, "provenance_role": role, "content_hash": "a" * 64}))

    def test_prediction_cannot_be_relabelled_observed(self):
        with self.assertRaises(Exception):
            self._record("model_prediction", "REAL_OBSERVED", "production")

    def test_emulator_data_cannot_be_real_observed(self):
        with self.assertRaises(Exception):
            self._record("original", "REAL_OBSERVED", "emulator")
        self.assertEqual(self._record("original", "SANDBOX_OBSERVED", "emulator").provenance_role, "SANDBOX_OBSERVED")

    def test_replica_role_is_kept(self):
        self.assertEqual(self._record("synthetic_replica", "SYNTHETIC_REPLICA", "emulator").provenance_role,
                         "SYNTHETIC_REPLICA")


class ClaimsI05(unittest.TestCase):
    def _claim(self, status: str, uncertainty: dict) -> EffectClaim:
        scope = {"environment_id": "x", "environment_kind": "staging"}
        return EffectClaim.model_validate_json(json.dumps({
            "schema_version": "2.0", "claim_id": "c1", "assessment_id": "a1", "assessment_status": status, "estimate": 0.01,
            "uncertainty": uncertainty, "dependence_model": "users independent", "scope": scope,
            "evidence_ids": ["e1"], "method": {"method_id": "randomized_effect_v1", "method_version": "1"},
            "estimand": {"outcome": "y", "contrast": {"treatment": "t", "control": "c"}, "population": "p",
                         "horizon": "7d", "functional": "ITT", "source_scope": scope, "target_scope": scope}}))

    def test_effect_claim_needs_identification_and_uncertainty(self):
        self.assertEqual(self._claim("CONDITIONAL_IDENTIFICATION", {"se": 0.004}).estimate, 0.01)
        for status, unc in (("ASSOCIATION_ONLY", {"se": 0.004}), ("CONDITIONAL_IDENTIFICATION", {})):
            with self.assertRaises(Exception):
                self._claim(status, unc)


class CompatibilityAndPorts(unittest.TestCase):
    def test_conditional_requirements(self):
        defs = {k: api.parse_definition(v) for k, v in WORLD.definitions.items()}
        rate = api.assess_contract_compatibility(defs["on_time_dispatch_rate"], "exact_identity")
        orders = api.assess_contract_compatibility(defs["orders"], "exact_identity")
        event = api.assess_contract_compatibility(defs["door_open"], "time_bucketing")
        self.assertTrue(rate.compatible)
        self.assertEqual(set(orders.missing), {"formula_ast"})
        bare = {k: v for k, v in WORLD.definitions["orders"].items() if k not in {"population_coverage", "time_semantics"}}
        self.assertEqual(set(api.assess_contract_compatibility(api.parse_definition(bare), "exact_identity").missing),
                         {"formula_ast", "population_coverage", "time_semantics"})
        self.assertFalse(event.compatible)
        self.assertIn("derived MetricDefinition", event.reasons[0])

    def test_p0_ports_have_no_write_surface(self):
        banned = ("write", "execute", "actuate", "apply", "submit", "delete", "update")
        for port in P0_PORTS:
            methods = [m for m in vars(port) if not m.startswith("_")]
            self.assertFalse([m for m in methods if any(b in m for b in banned)], port.__name__)

    def test_legacy_port_d1(self):
        scope = WORLD.definitions["orders"]["scope"]
        req = to_typed_request("fetch_segment_breakdown", request_id="r", scope=scope,
                               data_cut="2026-11-20T00:00:00Z", metric_id="orders", dimension="door_id")
        self.assertEqual((req.request, req.bucket_grain, req.max_buckets), ("get_strata", None, 168))
        with self.assertRaises(UnknownLegacyMethod):
            to_typed_request("fetch_anything", request_id="r", scope=scope, data_cut="2026-11-20T00:00:00Z")


if __name__ == "__main__":
    unittest.main()
