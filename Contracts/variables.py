"""Variable definitions: one canonical union, metric is one subtype (F01, I02).

Metric-only fields (aggregation, population, time semantics, formula) exist
*only* on MetricDefinition, and even there they are conditional. A state,
event or categorical definition that carries one is rejected as an unknown
field. That is the Phase 1 stop condition, enforced structurally.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from .common import (Contract, EnvironmentScope, Extensions, Grain, Id, SchemaVersion,
                     SourceAttestation, Text)

# ── value schemas ──────────────────────────────────────────────────────────


class NumericValueSchema(Contract):
    type: Literal["numeric"]
    dtype: Literal["real", "integer", "count", "proportion"]
    minimum: float | None = None
    maximum: float | None = None

    @model_validator(mode="after")
    def _bounds(self) -> "NumericValueSchema":
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError("minimum exceeds maximum")
        if self.dtype == "proportion" and ((self.minimum or 0.0) < 0.0 or (self.maximum or 1.0) > 1.0):
            raise ValueError("a proportion lives in [0, 1]; declare a percentage as dtype 'real' with a unit")
        return self


class CategoricalValueSchema(Contract):
    type: Literal["categorical"]
    categories: tuple[Text, ...] = Field(min_length=1)
    ordered: bool = False      # never assume order
    exhaustive: bool = False   # never assume exhaustiveness
    missing_code: Text | None = None
    other_code: Text | None = None

    @model_validator(mode="after")
    def _codes(self) -> "CategoricalValueSchema":
        if len(set(self.categories)) != len(self.categories):
            raise ValueError("categories must be unique")
        for name, code in (("missing_code", self.missing_code), ("other_code", self.other_code)):
            if code is not None and code not in self.categories:
                raise ValueError(f"{name} {code!r} is not a declared category")
        return self


EventFieldType = Literal["real", "integer", "boolean", "text", "category"]


class EventPayloadSchema(Contract):
    type: Literal["event"]
    fields: dict[Id, EventFieldType] = Field(min_length=1)
    identity_fields: tuple[Id, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def _identity_declared(self) -> "EventPayloadSchema":
        missing = [f for f in self.identity_fields if f not in self.fields]
        if missing:
            raise ValueError(f"identity_fields not declared in fields: {missing}")
        return self


# ── metric formula AST: closed set of operations, no dynamic evaluation ────


class FormulaRef(Contract):
    node: Literal["ref"]
    variable_id: Id


class FormulaConst(Contract):
    node: Literal["const"]
    value: float


class FormulaBinOp(Contract):
    node: Literal["binop"]
    op: Literal["add", "sub", "mul", "div"]
    left: "FormulaNode"
    right: "FormulaNode"


FormulaNode = Annotated[Union[FormulaRef, FormulaConst, FormulaBinOp], Field(discriminator="node")]
FormulaBinOp.model_rebuild()


def formula_refs(node: FormulaRef | FormulaConst | FormulaBinOp) -> frozenset[str]:
    if isinstance(node, FormulaRef):
        return frozenset({node.variable_id})
    if isinstance(node, FormulaBinOp):
        return formula_refs(node.left) | formula_refs(node.right)
    return frozenset()


class PopulationCoverage(Contract):
    population_id: Id
    coverage: Literal["full", "subset", "unknown"] = "unknown"
    parent_population_id: Id | None = None


class MetricTimeSemantics(Contract):
    grain: Grain
    window: Literal["tumbling", "rolling", "cumulative", "unknown"] = "unknown"
    timezone: Text = "UTC"


# ── the four variable kinds ────────────────────────────────────────────────


class _VariableBase(Contract):
    schema_version: SchemaVersion
    variable_id: Id
    scope: EnvironmentScope
    provenance: SourceAttestation
    entity_ref: Id | None = None
    description: Text | None = None
    extensions: Extensions = {}


class MetricDefinition(_VariableBase):
    kind: Literal["metric"]
    value_schema: NumericValueSchema
    unit: Text | None = None
    aggregation_kind: Literal["sum", "count", "mean", "ratio", "quantile", "min", "max"] | None = None
    population_coverage: PopulationCoverage | None = None
    time_semantics: MetricTimeSemantics | None = None
    formula_ast: FormulaNode | None = None
    identity_tolerance: float | None = Field(default=None, gt=0)

    @model_validator(mode="after")
    def _formula_rules(self) -> "MetricDefinition":
        if self.formula_ast is None:
            return self
        if self.variable_id in formula_refs(self.formula_ast):
            raise ValueError("formula references its own variable_id")
        if self.aggregation_kind == "ratio" and not (
                isinstance(self.formula_ast, FormulaBinOp) and self.formula_ast.op == "div"):
            raise ValueError("aggregation_kind 'ratio' requires a formula whose root is div")
        return self


class StateVariableDefinition(_VariableBase):
    kind: Literal["state"]
    value_schema: Annotated[Union[NumericValueSchema, CategoricalValueSchema], Field(discriminator="type")]
    unit: Text | None = None
    sampling_cadence_s: float | None = Field(default=None, gt=0)
    irregular_sampling: bool = False


class EventVariableDefinition(_VariableBase):
    kind: Literal["event"]
    value_schema: EventPayloadSchema
    occurrence_time: Literal["event_time", "ingest_time_only", "unknown"] = "unknown"


class CategoricalVariableDefinition(_VariableBase):
    kind: Literal["categorical"]
    value_schema: CategoricalValueSchema


VariableDefinition = Annotated[
    Union[MetricDefinition, StateVariableDefinition, EventVariableDefinition, CategoricalVariableDefinition],
    Field(discriminator="kind")]

METRIC_ONLY_FIELDS = ("aggregation_kind", "population_coverage", "time_semantics", "formula_ast",
                      "identity_tolerance")
