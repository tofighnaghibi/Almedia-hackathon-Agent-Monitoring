"""Public contract API for schema v2.0 (plan §C public API snapshot).

    parse_definition, parse_observation, parse_study_design, parse_reasoning_task,
    migrate_legacy_record, assess_contract_compatibility

Pure functions, no I/O. Everything enters through the JSON wire format, so a
dict and its serialized bytes are validated identically. Failures raise
ContractError with a stable code and every underlying reason.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Literal

from pydantic import TypeAdapter, ValidationError

from .contracts.common import Contract, Id, SchemaVersion, Text
from .contracts.graph import GraphEdge
from .contracts.observations import ALLOWED_SHAPES, Observation
from .contracts.studies import StudyDesign
from .contracts.tasks import ReasoningTask
from .contracts.variables import (CategoricalValueSchema, DimensionSpec, EntitySpec, EventVariableDefinition,
                                  MetricDefinition, NumericValueSchema, StateVariableDefinition, VariableDefinition)


class ContractError(ValueError):
    """code ∈ NOT_JSON | SCHEMA_INVALID | VARIABLE_MISMATCH | SHAPE_NOT_ALLOWED_FOR_KIND | PAYLOAD_VIOLATES_DEFINITION"""

    def __init__(self, code: str, details: list[str] | tuple[str, ...]):
        self.code = code
        self.details = tuple(details)
        super().__init__(f"{code}: " + "; ".join(self.details))


_DEFINITION = TypeAdapter(VariableDefinition)
_OBSERVATION = TypeAdapter(Observation)
_STUDY = TypeAdapter(StudyDesign)
_TASK = TypeAdapter(ReasoningTask)
_EDGE = TypeAdapter(GraphEdge)
_ENTITY = TypeAdapter(EntitySpec)
_DIMENSION = TypeAdapter(DimensionSpec)


def _fmt(err: dict) -> str:
    loc = ".".join(str(p) for p in err["loc"]) or "<root>"
    return f"{loc}: {err['msg']} [{err['type']}]"


def _validate(adapter: TypeAdapter, raw):
    if isinstance(raw, (str, bytes, bytearray)):
        text = raw
    elif isinstance(raw, dict):
        try:
            text = json.dumps(raw, allow_nan=False)
        except (TypeError, ValueError) as exc:
            raise ContractError("NOT_JSON", [str(exc)]) from None
    else:
        raise ContractError("NOT_JSON", [f"expected JSON text or a dict, got {type(raw).__name__}"])
    try:
        return adapter.validate_json(text)
    except ValidationError as exc:
        raise ContractError("SCHEMA_INVALID", [_fmt(e) for e in exc.errors()]) from None


def parse_definition(raw):
    return _validate(_DEFINITION, raw)


def parse_study_design(raw):
    return _validate(_STUDY, raw)


def parse_reasoning_task(raw) -> ReasoningTask:
    return _validate(_TASK, raw)


def parse_entity_spec(raw) -> EntitySpec:
    return _validate(_ENTITY, raw)


def parse_dimension_spec(raw) -> DimensionSpec:
    return _validate(_DIMENSION, raw)


def parse_graph_edge(raw) -> GraphEdge:
    """Per-edge rules only; cross-edge admission (cycles, identity checks) is Phase 2."""
    return _validate(_EDGE, raw)


def parse_observation(raw, definition=None) -> Observation:
    """Parse an observation; with a definition, also check it means what the definition says."""
    obs = _validate(_OBSERVATION, raw)
    if definition is not None:
        _check_against_definition(obs, definition)
    return obs


# ── observation vs definition ──────────────────────────────────────────────


def _numeric_problems(values, schema: NumericValueSchema) -> list[str]:
    out = []
    for v in values:
        if v is None:
            continue
        if schema.minimum is not None and v < schema.minimum:
            out.append(f"value {v} below declared minimum {schema.minimum}")
        if schema.maximum is not None and v > schema.maximum:
            out.append(f"value {v} above declared maximum {schema.maximum}")
        if schema.dtype in {"integer", "count"} and float(v) != int(v):
            out.append(f"value {v} is not integral for dtype {schema.dtype}")
        if schema.dtype == "count" and v < 0:
            out.append(f"negative count {v}")
    return out


def _category_problems(values, schema: CategoricalValueSchema) -> list[str]:
    return [f"category {v!r} not declared {list(schema.categories)}" for v in values
            if v not in schema.categories]


_EVENT_TYPES = {"real": (int, float), "integer": (int,), "boolean": (bool,), "text": (str,), "category": (str,)}


def _event_problems(obs: Observation, d: EventVariableDefinition) -> list[str]:
    out, fields = [], d.value_schema.fields
    for e in obs.payload.events:
        extra = set(e.attributes) - set(fields)
        if extra:
            out.append(f"event {e.event_id}: undeclared attributes {sorted(extra)}")
        for f in d.value_schema.identity_fields:
            if e.attributes.get(f) is None:
                out.append(f"event {e.event_id}: identity field {f!r} missing")
        for name, value in e.attributes.items():
            want = fields.get(name)
            if want is None or value is None:
                continue
            is_bool = isinstance(value, bool)
            if (want != "boolean" and is_bool) or not isinstance(value, _EVENT_TYPES[want]):
                out.append(f"event {e.event_id}: {name}={value!r} is not {want}")
    return out


def _check_against_definition(obs: Observation, d) -> None:
    if obs.variable_id != d.variable_id:
        raise ContractError("VARIABLE_MISMATCH", [f"observation is for {obs.variable_id!r}, definition is {d.variable_id!r}"])
    shape = obs.payload.shape
    if shape not in ALLOWED_SHAPES[d.kind]:
        hint = (" — aggregating events needs a derived MetricDefinition with a declared operator"
                if d.kind == "event" else "")
        raise ContractError("SHAPE_NOT_ALLOWED_FOR_KIND",
                            [f"{d.kind} variable cannot carry a {shape} payload{hint}"])
    p, problems = obs.payload, []
    schema = d.value_schema
    if isinstance(d, EventVariableDefinition):
        problems = _event_problems(obs, d)
    elif isinstance(schema, NumericValueSchema):
        if shape in {"categorical"} or (shape == "state_trajectory" and any(isinstance(s.state, str) for s in p.samples)):
            problems = [f"numeric {d.kind} received categorical values"]
        elif shape == "scalar":
            problems = _numeric_problems([p.value], schema)
        elif shape == "time_series":
            problems = _numeric_problems([pt.value for pt in p.points], schema)
        elif shape == "state_trajectory":
            problems = _numeric_problems([s.state for s in p.samples], schema)
        elif shape == "panel":
            problems = _numeric_problems([c.value for c in p.cells], schema)
        elif shape == "assignment_outcomes":
            problems = _numeric_problems([u.outcome for u in p.units], schema)
        elif shape == "aggregate_stats":
            # Audit fix P1-049 (§C.6 "arbitrary outcome denominators"): the implied ratio or mean must
            # respect the declared range. Bounds only: a mean of counts may be fractional. 0/0 is undefined, not a violation.
            implied = [p.mean]
            if p.numerator is not None and p.denominator:
                implied.append(p.numerator / p.denominator)
            bounds_only = schema.model_copy(update={"dtype": "real"})
            problems = [f"aggregate implies {m}" for m in _numeric_problems(implied, bounds_only)]
    elif isinstance(schema, CategoricalValueSchema):
        if shape == "categorical":
            problems = _category_problems([p.category], schema)
        elif shape == "state_trajectory":
            problems = _category_problems([s.state for s in p.samples], schema)
        else:
            problems = [f"categorical {d.kind} received a {shape} payload"]
    if problems:
        raise ContractError("PAYLOAD_VIOLATES_DEFINITION", problems)


# ── leakage-safe replay (v3.2 §2.2, C09) ───────────────────────────────────


def replay_visibility(obs: Observation, replay_time: datetime) -> tuple[str, str]:
    """VISIBLE only with a KNOWN available_at at or before replay_time. observed_at alone is never enough."""
    clocks = obs.clocks
    if clocks is None or clocks.available_at is None:
        return "REFUSED", "NO_AVAILABILITY_CLOCK"
    if clocks.available_at.quality != "KNOWN":
        return "REFUSED", f"{clocks.available_at.quality}_AVAILABILITY_CLOCK"
    if clocks.available_at.value <= replay_time:
        return "VISIBLE", "KNOWN_AVAILABLE_BEFORE_REPLAY"
    return "NOT_YET_AVAILABLE", "AVAILABLE_AFTER_REPLAY_TIME"


# ── conditional requirements: what an operation needs, checked at use time ──

Operation = Literal["exact_identity", "mix_rate_decomposition", "time_bucketing", "sequential_monitoring",
                    "leakage_safe_replay"]


class CompatibilityReport(Contract):
    schema_version: SchemaVersion
    operation: Text
    subject: Id
    compatible: bool
    missing: tuple[Text, ...] = ()
    reasons: tuple[Text, ...] = ()


def assess_contract_compatibility(record, operation: Operation) -> CompatibilityReport:
    """Does this record carry what `operation` needs? Missing fields are reported, never defaulted."""
    if operation == "leakage_safe_replay":
        if not isinstance(record, Observation):
            return CompatibilityReport(schema_version="2.0", operation=operation, subject="non-observation", compatible=False,
                                       reasons=("replay applies to observations",))
        c = record.clocks
        ok = bool(c and c.available_at and c.available_at.quality == "KNOWN")
        return CompatibilityReport(schema_version="2.0", operation=operation, subject=record.observation_id, compatible=ok,
                                   missing=() if ok else ("clocks.available_at (KNOWN)",))

    subject = record.variable_id
    if isinstance(record, MetricDefinition):
        need = {"exact_identity": ("formula_ast", "unit", "population_coverage", "time_semantics"),
                "mix_rate_decomposition": ("formula_ast", "aggregation_kind=ratio", "population_coverage"),
                "time_bucketing": ("time_semantics",),
                "sequential_monitoring": ("time_semantics",)}[operation]
        missing = []
        for field in need:
            if field == "aggregation_kind=ratio":
                if record.aggregation_kind != "ratio":
                    missing.append(field)
            elif getattr(record, field) is None:
                missing.append(field)
        return CompatibilityReport(schema_version="2.0", operation=operation, subject=subject, compatible=not missing,
                                   missing=tuple(missing))

    if isinstance(record, StateVariableDefinition):
        numeric = isinstance(record.value_schema, NumericValueSchema)
        if operation == "sequential_monitoring" and numeric:
            ok = record.sampling_cadence_s is not None
            return CompatibilityReport(schema_version="2.0", operation=operation, subject=subject, compatible=ok,
                                       missing=() if ok else ("sampling_cadence_s",))
        reason = {"exact_identity": "formula identities apply to metrics only",
                  "mix_rate_decomposition": "mix/rate decomposition applies to ratio metrics only",
                  "time_bucketing": "bucketing a state needs a derived MetricDefinition with a declared operator",
                  "sequential_monitoring": "categorical states need a categorical change method (not in P0)"}[operation]
        return CompatibilityReport(schema_version="2.0", operation=operation, subject=subject, compatible=False, reasons=(reason,))

    reason = ("bucketing events needs a derived MetricDefinition with a declared operator and lost-information flag"
              if operation == "time_bucketing" and record.kind == "event"
              else f"{operation} is not defined for {record.kind} variables")
    return CompatibilityReport(schema_version="2.0", operation=operation, subject=subject, compatible=False, reasons=(reason,))


from .migration.legacy import migrate_legacy_record  # noqa: E402  (public re-export)

__all__ = ["ContractError", "parse_definition", "parse_observation", "parse_study_design",
           "parse_reasoning_task", "parse_graph_edge", "parse_entity_spec", "parse_dimension_spec",
           "migrate_legacy_record",
           "assess_contract_compatibility", "replay_visibility", "CompatibilityReport"]
