"""Study designs and estimands (v3.2 §2.1, §4.5, §7.3).

Designs are separate subtypes with their own mandatory fields; assignment
fields exist only on randomized designs. Units are declared, never forced
equal (F04). EstimandSpec is for causal-effect questions only; other questions
use their own target contracts in tasks.py.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import Field, model_validator

from .common import Contract, EnvironmentScope, Id, SchemaVersion, SourceAttestation, Text

EffectFunctional = Literal["ATE", "ATT", "ITT", "CACE", "risk_difference", "mean_difference",
                           "dose_response", "policy_value"]


class InterventionContrast(Contract):
    treatment: Text
    control: Text

    @model_validator(mode="after")
    def _distinct(self) -> "InterventionContrast":
        if self.treatment == self.control:
            raise ValueError("treatment and control must differ")
        return self


class EstimandSpec(Contract):
    """Exactly what effect is asked for. No 'lift' with an undefined treatment, time or population."""

    outcome: Id
    contrast: InterventionContrast
    population: Text
    horizon: Text
    functional: EffectFunctional
    target_scope: EnvironmentScope | None = None


class _StudyBase(Contract):
    schema_version: SchemaVersion
    study_id: Id
    scope: EnvironmentScope
    provenance: SourceAttestation
    population: Text
    outcomes: tuple[Id, ...] = Field(min_length=1)


class RandomizedDesign(_StudyBase):
    design: Literal["randomized"]
    randomization: Literal["simple", "blocked", "stratified", "cluster"]
    assignment_unit: Id
    outcome_unit: Id
    analysis_unit: Id | None = None            # chosen per method; not forced to equal anything
    expected_allocation: dict[Id, float] = Field(min_length=2)
    cluster_field: Id | None = None
    compliance: Literal["full", "one_sided", "two_sided", "unknown"] = "unknown"
    interference: Literal["none_assumed", "declared", "unknown"] = "unknown"
    multiplicity_family: Id | None = None

    @model_validator(mode="after")
    def _design_rules(self) -> "RandomizedDesign":
        total = sum(self.expected_allocation.values())
        if abs(total - 1.0) > 1e-9 or any(v <= 0 for v in self.expected_allocation.values()):
            raise ValueError(f"expected_allocation must be positive and sum to 1 (got {total})")
        if self.randomization == "cluster" and self.cluster_field is None:
            raise ValueError("cluster randomization requires cluster_field")
        return self


class QuasiExperimentalDesign(_StudyBase):
    design: Literal["quasi_experimental"]
    strategy: Literal["difference_in_differences", "interrupted_time_series", "regression_discontinuity",
                      "instrumental_variable", "synthetic_control"]
    unit: Id
    treated_group: Text
    intervention_period: int
    timing: Literal["single", "staggered"] = "single"
    anticipation_window: int = Field(default=0, ge=0)


class ObservationalDesign(_StudyBase):
    design: Literal["observational"]
    exposure: Id
    adjustment_set: tuple[Id, ...] = ()
    instruments: tuple[Id, ...] = ()


class DescriptiveDesign(_StudyBase):
    design: Literal["descriptive"]
    sampling: Text


StudyDesign = Annotated[
    Union[RandomizedDesign, QuasiExperimentalDesign, ObservationalDesign, DescriptiveDesign],
    Field(discriminator="design")]
