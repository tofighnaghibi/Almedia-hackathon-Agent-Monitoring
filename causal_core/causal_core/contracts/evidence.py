"""Evidence records, claims and identification (v3.2 §2.3, §4.5, I05, I06, I11).

An EffectClaim qualifies a declared estimand. It is not a graph edge.
Provenance role must agree with lineage origin and environment, so a model
prediction can never be relabelled as observed production evidence.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .common import Contract, EnvironmentScope, Id, ProvenanceLineage, SchemaVersion, Sha256, Text
from .studies import EstimandSpec

ProvenanceRole = Literal["REAL_OBSERVED", "EXTERNAL_STUDY", "SANDBOX_OBSERVED", "SYNTHETIC_GENERATED",
                         "SYNTHETIC_REPLICA", "MODEL_PREDICTION", "LEGACY_UNSCOPED"]
LegacyEvidenceClass = Literal["OBSERVED", "EXPERIMENTAL", "EMULATOR_OBSERVATION", "MODEL_PREDICTION"]

_ROLE_FOR_ORIGIN = {
    "synthetic_generated": {"SYNTHETIC_GENERATED"},
    "synthetic_replica": {"SYNTHETIC_REPLICA"},
    "model_prediction": {"MODEL_PREDICTION"},
    "external_study": {"EXTERNAL_STUDY"},
    "legacy_unknown": {"LEGACY_UNSCOPED"},
    "original": {"REAL_OBSERVED", "SANDBOX_OBSERVED"},
}

ClaimKind = Literal["MATHEMATICAL_IDENTITY", "DESCRIPTIVE", "ASSOCIATION", "CAUSAL_EFFECT_CONDITIONAL",
                    "STRUCTURAL_CAUSAL_HYPOTHESIS", "MODEL_PREDICTION", "INCONCLUSIVE", "DQ_FAILURE",
                    "ACTION_RECOMMENDATION"]
IdentificationStatus = Literal["CONDITIONAL_IDENTIFICATION", "PARTIAL_IDENTIFICATION", "ASSOCIATION_ONLY",
                               "UNIDENTIFIED", "INVALID_DESIGN", "INSUFFICIENT_EVIDENCE"]


class MethodProvenance(Contract):
    method_id: Id
    method_version: Text
    seed: int | None = None
    parameters_hash: Sha256 | None = None


class EvidenceRecord(Contract):
    schema_version: SchemaVersion
    evidence_id: Id
    lineage: ProvenanceLineage
    scope: EnvironmentScope
    provenance_role: ProvenanceRole
    evidence_class: LegacyEvidenceClass | None = None   # compatibility only; confers no authority
    content_hash: Sha256
    method: MethodProvenance | None = None

    @model_validator(mode="after")
    def _role_matches_origin(self) -> "EvidenceRecord":
        allowed = _ROLE_FOR_ORIGIN[self.lineage.origin_kind]
        if self.provenance_role not in allowed:
            raise ValueError(f"role {self.provenance_role} contradicts origin {self.lineage.origin_kind}")
        sandbox = self.scope.environment_kind in {"emulator", "offline_replay", "staging"}
        if self.provenance_role == "REAL_OBSERVED" and sandbox:
            raise ValueError(f"REAL_OBSERVED cannot come from a {self.scope.environment_kind} scope")
        if self.scope.environment_kind == "legacy_unscoped" and self.provenance_role != "LEGACY_UNSCOPED":
            raise ValueError("legacy_unscoped evidence must keep role LEGACY_UNSCOPED")
        return self


class Uncertainty(Contract):
    se: float | None = Field(default=None, ge=0)
    interval: tuple[float, float] | None = None
    level: float = Field(default=0.95, gt=0, lt=1)

    @model_validator(mode="after")
    def _present(self) -> "Uncertainty":
        if self.se is None and self.interval is None:
            raise ValueError("uncertainty needs an SE or an interval")
        if self.interval and self.interval[0] > self.interval[1]:
            raise ValueError("interval lower bound exceeds upper bound")
        return self


class IdentificationAssessment(Contract):
    schema_version: SchemaVersion
    assessment_id: Id
    estimand: EstimandSpec
    strategy: Text
    status: IdentificationStatus
    assumptions_checked: tuple[Text, ...] = ()
    assumptions_untestable: tuple[Text, ...] = ()
    assumptions_refuted: tuple[Text, ...] = ()
    scope: EnvironmentScope
    evidence_ids: tuple[Id, ...] = Field(min_length=1)
    method: MethodProvenance


class EffectClaim(Contract):
    """A scoped, conditional effect on a declared estimand. Never a structural edge by itself (I06)."""

    schema_version: SchemaVersion
    claim_id: Id
    estimand: EstimandSpec
    assessment_id: Id
    assessment_status: IdentificationStatus
    estimate: float
    uncertainty: Uncertainty
    dependence_model: Text
    scope: EnvironmentScope
    evidence_ids: tuple[Id, ...] = Field(min_length=1)
    method: MethodProvenance

    @model_validator(mode="after")
    def _identified(self) -> "EffectClaim":
        if self.assessment_status not in {"CONDITIONAL_IDENTIFICATION", "PARTIAL_IDENTIFICATION"}:
            raise ValueError(f"an EffectClaim needs identification, not {self.assessment_status}")
        return self


class EvidenceGap(Contract):
    schema_version: SchemaVersion
    gap_id: Id
    task_id: Id
    missing: Text
    recommended_measurements: tuple[Text, ...] = Field(min_length=1)
    blocks_claim: ClaimKind | None = None
    executable_now: bool = False
