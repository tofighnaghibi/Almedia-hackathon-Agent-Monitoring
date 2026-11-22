"""P1 contracts for sparse what-if prediction (v3.2 §6.3, §2.1 row "MechanismSpec, CounterfactualPrediction"; plan §I).

A MechanismSpec is a fitted structural equation for ONE target with its uncertainty, valid regime, fitting lineage and
validation diagnostics. Only VALIDATED specs may be rolled out. A CounterfactualPrediction is a MODEL_PREDICTION:
it is never evidence, never updates e-process wealth or graph certification, and never authorizes an action.
"""
from __future__ import annotations

import math
from typing import Literal

from pydantic import Field, model_validator

from .common import Contract, EnvironmentScope, Id, SchemaVersion, Text, TimeWindow


class MechanismParent(Contract):
    variable_id: Id
    lag_steps: int = Field(ge=0)          # 0 = same step (contemporaneous), k = k steps earlier
    transform: Literal["identity", "square"] = "identity"   # "square" = a curved term (CP-034 fix); still linear in β


class RegimeBound(Contract):
    """The range of one parent seen while fitting. Outside it the mechanism is not trusted (OUT_OF_REGIME)."""
    variable_id: Id
    lag_steps: int = Field(ge=0)
    minimum: float
    maximum: float

    @model_validator(mode="after")
    def _ordered(self) -> "RegimeBound":
        if not self.minimum <= self.maximum:
            raise ValueError("regime minimum exceeds maximum")
        return self


class FittedOn(Contract):
    scope: EnvironmentScope
    window: TimeWindow
    n: int = Field(gt=0)
    lineage_roots: tuple[Id, ...] = Field(min_length=1)


class ValidationDiagnostics(Contract):
    holdout_window: TimeWindow
    n: int = Field(gt=0)
    rmse: float = Field(ge=0)
    interval_level: float = Field(gt=0, lt=1)
    coverage: float = Field(ge=0, le=1)
    out_of_regime_points: int = Field(ge=0)
    verdict: Literal["PASS", "FAIL"]
    log_score: float | None = None                     # held-out log predictive density (for shape weights)


class MechanismSpec(Contract):
    schema_version: SchemaVersion
    mechanism_id: Id
    version: int = Field(ge=1)
    target: Id
    family: Literal["linear_gaussian"]                  # P1 v1: the only family the engine runs
    parents: tuple[MechanismParent, ...]
    coefficients: tuple[float, ...]                     # intercept first, then one per parent, in order
    covariance: tuple[tuple[float, ...], ...]           # of the coefficients
    noise_sd: float = Field(gt=0)
    valid_regime: tuple[RegimeBound, ...]
    feature_availability: Literal["parents_observed_by_target_time"]
    fitted_on: FittedOn
    validation: ValidationDiagnostics | None = None
    status: Literal["CANDIDATE", "VALIDATED", "REJECTED"]

    @model_validator(mode="after")
    def _consistent(self) -> "MechanismSpec":
        p = 1 + len(self.parents)
        if len(self.coefficients) != p or len(self.covariance) != p or any(len(r) != p for r in self.covariance):
            raise ValueError(f"coefficients and covariance must have size {p} (intercept + parents)")
        if not all(math.isfinite(x) for x in self.coefficients) or any(not math.isfinite(x) for r in self.covariance for x in r):
            raise ValueError("coefficients and covariance must be finite")
        terms = [(q.variable_id, q.lag_steps, q.transform) for q in self.parents]
        if len(set(terms)) != len(terms):
            raise ValueError("each (parent, lag, transform) appears once")
        keys = {(q.variable_id, q.lag_steps) for q in self.parents}
        if (self.target, 0) in keys:
            raise ValueError("a target cannot be its own same-step parent")
        bounds = [(r.variable_id, r.lag_steps) for r in self.valid_regime]
        if len(set(bounds)) != len(bounds) or set(bounds) != keys:
            raise ValueError("valid_regime needs exactly one bound per (parent, lag), on the untransformed value")
        if self.status == "VALIDATED" and (self.validation is None or self.validation.verdict != "PASS"):
            raise ValueError("VALIDATED requires passing held-out validation diagnostics")
        return self


class MultiStepDiagnostics(Contract):
    """Held-out multi-step backtest of the whole set of mechanisms feeding a target (CP-034 fix)."""
    horizon_steps: int = Field(ge=1)
    origins: int = Field(ge=1)
    interval_level: float = Field(gt=0, lt=1)
    coverage: float = Field(ge=0, le=1)
    inflation: float = Field(ge=1)                     # applied to every interval for this target
    verdict: Literal["PASS", "FAIL"]


class MechanismSet(Contract):
    """Alternative equation shapes for one target, weighted by held-out fit (CP-034 fix). A rollout draws one member
    per sample (the same member in both worlds), so the prediction carries the uncertainty about the shape."""
    schema_version: SchemaVersion
    target: Id
    members: tuple[MechanismSpec, ...] = Field(min_length=1)
    weights: tuple[float, ...]
    weighting: Literal["holdout_log_score"]
    multi_step: MultiStepDiagnostics | None = None

    @model_validator(mode="after")
    def _members(self) -> "MechanismSet":
        if len(self.weights) != len(self.members) or any(w < 0 for w in self.weights) or abs(sum(self.weights) - 1) > 1e-9:
            raise ValueError("one non-negative weight per member, summing to 1")
        if any(m.target != self.target or m.status != "VALIDATED" for m in self.members):
            raise ValueError("members must be VALIDATED mechanisms for this target")
        if self.multi_step is not None and self.multi_step.verdict == "FAIL":
            raise ValueError("a set whose multi-step backtest failed cannot be registered")
        return self


class InterventionSpec(Contract):
    variable_id: Id
    kind: Literal["set", "shift"]
    value: float


class PredictiveSummary(Contract):
    mean: float
    lower: float
    upper: float


class CounterfactualPrediction(Contract):
    schema_version: SchemaVersion
    prediction_id: Id
    provenance_role: Literal["MODEL_PREDICTION"] = "MODEL_PREDICTION"
    target: Id
    horizon_steps: int = Field(ge=1)
    interventions: tuple[InterventionSpec, ...] = ()
    status: Literal["PREDICTED", "OUT_OF_REGIME", "UNSUPPORTED_MODEL", "CAP_EXCEEDED"]
    reasons: tuple[Text, ...] = ()
    level: float = Field(gt=0, lt=1)
    baseline: PredictiveSummary | None = None           # target, summed over the horizon, without intervention
    intervened: PredictiveSummary | None = None
    effect: PredictiveSummary | None = None             # paired (common random numbers)
    reach_sensitivity: dict[Text, PredictiveSummary] = {}
    constraint_risk: dict[Text, float] = {}             # P(constraint violated at some step), intervened world
    mechanism_versions: dict[Id, int] = {}
    calibration_scope_kinds: tuple[Text, ...] = ()
    seed: int
    n_samples: int = Field(ge=1)
    assumptions: tuple[Text, ...] = ()

    @model_validator(mode="after")
    def _shape(self) -> "CounterfactualPrediction":
        if self.status == "PREDICTED" and self.baseline is None:
            raise ValueError("a PREDICTED forecast carries its distribution")
        if self.status != "PREDICTED" and (self.effect is not None or self.intervened is not None):
            raise ValueError("an abstention carries no numbers")
        return self
