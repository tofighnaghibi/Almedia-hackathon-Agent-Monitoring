"""Adversarial contract corpus (plan §L "contract adversarial").

Each case is a valid record from the cold-chain world with exactly one
defect. Some cases are positive controls that *must* be accepted: absent
metric-only fields, monitor-free effect questions, cluster designs whose
units differ. Rejecting those would itself be a failure (stop condition, F04).
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field

from emulator import coldchain

_DROP = object()


@dataclass(frozen=True)
class Case:
    case_id: str
    category: str
    target: str            # definition | observation | study | task | edge
    raw: dict
    expect: str            # "ACCEPT" or a ContractError code
    match: str = ""        # substring that must appear in the error details
    definition: dict | None = field(default=None)   # observation cross-check


def mutate(base: dict, *changes: tuple[str, object]) -> dict:
    out = copy.deepcopy(base)
    for path, value in changes:
        *parents, last = path.split(".")
        node = out
        for p in parents:
            node = node[int(p)] if isinstance(node, list) else node[p]
        key = int(last) if isinstance(node, list) else last
        if value is _DROP:
            del node[key]
        else:
            node[key] = value
    return out


def _first(world, var):
    return next(o for o in world.observations if o["variable_id"] == var)


def base_study(scope: dict) -> dict:
    return {"schema_version": "2.0", "design": "randomized", "study_id": "door_alarm_trial",
            "scope": scope, "provenance": {"source_id": "emu-coldchain", "method": "adapter_declared"},
            "population": "freezer sites in region eu", "outcomes": ["on_time_dispatch_rate"],
            "randomization": "cluster", "cluster_field": "site_id",
            "assignment_unit": "site", "outcome_unit": "order",
            "expected_allocation": {"alarm_on": 0.5, "alarm_off": 0.5}, "compliance": "full"}


def base_task(scope: dict) -> dict:
    return {"schema_version": "2.0", "task_id": "t-door-alarm-ate", "trigger": "explicit_question",
            "question": {"question_kind": "ESTIMATE_EFFECT", "study_id": "door_alarm_trial",
                         "estimand": {"outcome": "on_time_dispatch_rate",
                                      "contrast": {"treatment": "alarm_on", "control": "alarm_off"},
                                      "population": "freezer sites in region eu", "horizon": "14d",
                                      "functional": "ITT", "source_scope": scope, "target_scope": scope}},
            "source_scope": scope, "data_cut": {"as_of": "2026-11-20T12:00:00Z"}, "priority": 5,
            "budget": {"max_wall_ms": 60000}, "created_at": "2026-11-20T12:00:00Z"}


def base_edge(scope: dict) -> dict:
    return {"schema_version": "2.0", "edge_id": "e-door-temp", "kind": "TEMPORAL_LAG", "source": "door_open",
            "target": "freezer_temp_c", "status": "ACTIVE", "lag": {"min_s": 300, "max_s": 1200},
            "lineage": {"source_id": "emu-coldchain", "origin_kind": "original", "lineage_root_id": "decl:door-temp",
                        "content_hash": "0" * 64}, "scope": scope}


def build_cases(world=None) -> list[Case]:
    w = world or coldchain.emit()
    d = w.definitions
    metric, state, event, cat = (d["on_time_dispatch_rate"], d["freezer_temp_c"], d["door_open"],
                                 d["compressor_mode"])
    o_metric, o_state, o_event, o_cat = (_first(w, "on_time_dispatch_rate"), _first(w, "freezer_temp_c"),
                                         _first(w, "door_open"), _first(w, "compressor_mode"))
    scope = metric["scope"]
    study, task, edge = base_study(scope), base_task(scope), base_edge(scope)
    panel = mutate(o_metric, ("payload", {"shape": "panel", "cells": [
        {"unit": "site_7", "period": 0, "value": 0.95}, {"unit": "site_7", "period": 0, "value": 0.94}]}))
    t0 = o_state["payload"]["samples"][0]["t"]

    C = Case
    return [
        # stop condition, positive side: nonmetric kinds need no metric fields; metric fields are conditional
        C("A1", "absent_metric_fields", "definition", state, "ACCEPT"),
        C("A2", "absent_metric_fields", "definition", event, "ACCEPT"),
        C("A3", "absent_metric_fields", "definition",
          mutate(metric, ("formula_ast", _DROP), ("population_coverage", _DROP), ("time_semantics", _DROP),
                 ("aggregation_kind", _DROP), ("identity_tolerance", _DROP)), "ACCEPT"),
        # stop condition, negative side: metric-only fields on nonmetric kinds are unknown fields
        C("B1", "metric_fields_on_nonmetric", "definition",
          mutate(state, ("time_semantics", {"grain": "1d", "window": "tumbling"})), "SCHEMA_INVALID", "extra_forbidden"),
        C("B2", "metric_fields_on_nonmetric", "definition", mutate(event, ("aggregation_kind", "sum")),
          "SCHEMA_INVALID", "extra_forbidden"),
        C("B3", "metric_fields_on_nonmetric", "definition",
          mutate(cat, ("formula_ast", {"node": "ref", "variable_id": "orders"})), "SCHEMA_INVALID", "extra_forbidden"),
        # ambiguous units
        C("C1", "ambiguous_unit", "definition", mutate(metric, ("value_schema.maximum", 100)),
          "SCHEMA_INVALID", "proportion lives in [0, 1]"),
        C("C2", "ambiguous_unit", "definition", mutate(cat, ("unit", "mode")), "SCHEMA_INVALID", "extra_forbidden"),
        C("C3", "ambiguous_unit", "definition", mutate(metric, ("unit", " ")), "SCHEMA_INVALID", "unit"),
        # timezone drift
        C("D1", "timezone_drift", "observation",
          mutate(o_metric, ("clocks.observed_at.value", "2026-11-19T00:00:00")), "SCHEMA_INVALID", "timezone_aware"),
        C("D2", "timezone_drift", "observation", mutate(o_metric, ("scope.data_cut", "2026-11-20T12:00:00")),
          "SCHEMA_INVALID", "timezone_aware"),
        C("D3", "timezone_drift", "observation", mutate(o_state, ("payload.samples.1.t", t0)),
          "SCHEMA_INVALID", "strictly increasing"),
        C("D4", "timezone_drift", "observation",
          mutate(o_metric, ("clocks.observed_at.value", "2026-11-19T02:00:00+02:00")), "SCHEMA_INVALID",
          "UTC on the wire"),   # added by audit CP-003
        # duplicate revisions / identities
        C("E1", "duplicate_revision", "observation", mutate(o_metric, ("revision", 0)), "SCHEMA_INVALID", "revision"),
        C("E2", "duplicate_revision", "observation",
          mutate(o_event, ("payload.events.1.event_id", o_event["payload"]["events"][0]["event_id"])),
          "SCHEMA_INVALID", "duplicate event_id"),
        C("E3", "duplicate_revision", "observation", panel, "SCHEMA_INVALID", "duplicate (unit, period)"),
        # wrong availability clocks
        C("F1", "wrong_available_at", "observation", mutate(o_metric, ("clocks.available_at.value", _DROP)),
          "SCHEMA_INVALID", "needs a value"),
        C("F2", "wrong_available_at", "observation",
          mutate(o_metric, ("clocks.available_at.quality", "UNKNOWN")), "SCHEMA_INVALID", "must not carry a value"),
        C("F3", "wrong_available_at", "observation",
          mutate(o_metric, ("clocks.available_at.value", "2026-11-18T00:00:00Z")), "SCHEMA_INVALID", "precedes"),
        # unscoped evidence
        C("G1", "unscoped_evidence", "observation", mutate(o_metric, ("scope", _DROP)), "SCHEMA_INVALID", "scope"),
        C("G2", "unscoped_evidence", "observation", mutate(o_metric, ("scope.environment_kind", "prod")),
          "SCHEMA_INVALID", "environment_kind"),
        C("G3", "unscoped_evidence", "observation", mutate(o_metric, ("scope.generator_version", _DROP)),
          "SCHEMA_INVALID", "generator_version"),
        # observation does not mean what its definition says
        C("H1", "definition_mismatch", "observation", mutate(o_cat, ("payload.category", "turbo")),
          "PAYLOAD_VIOLATES_DEFINITION", "not declared", definition=cat),
        C("H2", "definition_mismatch", "observation", mutate(o_state, ("payload.samples.3.state", 99.0)),
          "PAYLOAD_VIOLATES_DEFINITION", "above declared maximum", definition=state),
        C("H3", "definition_mismatch", "observation", o_metric, "VARIABLE_MISMATCH", "freezer_temp_c", definition=state),
        # lineage that would inflate evidence
        C("I1", "invalid_lineage", "observation",
          mutate(o_metric, ("lineage.origin_kind", "synthetic_replica"), ("lineage.generator_version", "g1")),
          "SCHEMA_INVALID", "name the evidence"),
        C("I2", "invalid_lineage", "observation", mutate(o_metric, ("lineage.origin_kind", "model_prediction")),
          "SCHEMA_INVALID", "requires generator_version"),
        C("I3", "invalid_lineage", "observation", mutate(o_metric, ("lineage.content_hash", "abc")),
          "SCHEMA_INVALID", "content_hash"),
        # events silently turned into rates
        C("J1", "event_coerced_to_rate", "observation",
          mutate(o_event, ("payload", {"shape": "aggregate_stats", "n": 14, "total": 14.0})),
          "SHAPE_NOT_ALLOWED_FOR_KIND", "derived MetricDefinition", definition=event),
        C("J2", "event_coerced_to_rate", "observation", mutate(o_event, ("payload.events.0.attributes.rate", 0.3)),
          "PAYLOAD_VIOLATES_DEFINITION", "undeclared attributes", definition=event),
        C("J3", "event_coerced_to_rate", "observation", mutate(o_event, ("payload.events.0.attributes.door_id", _DROP)),
          "PAYLOAD_VIOLATES_DEFINITION", "identity field", definition=event),
        # covariance and design honesty
        C("K1", "covariance_and_design", "observation", mutate(o_metric, ("payload.covariance", 0.0)),
          "SCHEMA_INVALID", "extra_forbidden"),
        C("K2", "covariance_and_design", "study", mutate(study, ("expected_allocation.alarm_off", 0.4)),
          "SCHEMA_INVALID", "sum to 1"),
        C("K3", "covariance_and_design", "study", mutate(study, ("cluster_field", _DROP)),
          "SCHEMA_INVALID", "cluster_field"),
        C("K4", "covariance_and_design", "study", study, "ACCEPT"),
        # task entry: explicit questions need no monitor, and incidents need a monitor
        C("L1", "task_entry", "task", task, "ACCEPT"),
        C("L2", "task_entry", "task", mutate(task, ("question.estimand", _DROP)), "SCHEMA_INVALID", "estimand"),
        C("L3", "task_entry", "task", mutate(task, ("question.estimand.population", "")), "SCHEMA_INVALID", "population"),
        C("L4", "task_entry", "task", mutate(task, ("incident_id", "inc-1")), "SCHEMA_INVALID", "monitor-triggered"),
        C("L5", "task_entry", "task", mutate(task, ("question.estimand.source_scope", _DROP)),
          "SCHEMA_INVALID", "source_scope"),   # added by audit CP-004
        # per-edge graph rules
        C("M1", "edge_rules", "edge", mutate(edge, ("kind", "TEMPORAL_FEEDBACK"), ("lag.min_s", 0)),
          "SCHEMA_INVALID", "strictly positive"),
        C("M2", "edge_rules", "edge", mutate(edge, ("kind", "OBSERVATION_EFFECT")), "SCHEMA_INVALID", "OBSERVATION layer"),
        C("M3", "edge_rules", "edge", mutate(edge, ("kind", "CAUSALLY_IDENTIFIED")), "SCHEMA_INVALID", "identification_ref"),
    ]


def run_case(case: Case) -> tuple[bool, str]:
    """Returns (behaved_as_expected, observed_outcome_text)."""
    from causal_core import api

    parse = {"definition": api.parse_definition, "study": api.parse_study_design,
             "task": api.parse_reasoning_task, "edge": api.parse_graph_edge}
    try:
        if case.target == "observation":
            definition = api.parse_definition(case.definition) if case.definition else None
            api.parse_observation(case.raw, definition)
        else:
            parse[case.target](case.raw)
    except api.ContractError as exc:
        text = "; ".join(exc.details)
        ok = case.expect == exc.code and case.match in text
        return ok, f"{exc.code}: {exc.details[0]}"
    return case.expect == "ACCEPT", "ACCEPTED"
