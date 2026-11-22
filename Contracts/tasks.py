"""Reasoning tasks: question-first, no mandatory anomaly or experiment (F02, I02, v3.2 §4.1).

`question_kind` decides which fields the question needs. It never decides the
method. Incident IDs only exist on monitor-triggered tasks; an estimand only
exists on effect questions.
"""
from __future__ import annotations

from typing import Annotated, Literal, Union

from pydantic import AwareDatetime, Field, model_validator

from .common import Contract, EnvironmentScope, Id, Sha256, SchemaVersion, Text, TimeWindow
from .evidence import ClaimKind
from .studies import EstimandSpec

# ── non-causal targets (v3.2 §2.1: EstimandSpec is for effect questions only) ──


class HypothesisTarget(Contract):
    statement: Text
    source: Id
    target: Id
    observable_implications: tuple[Text, ...] = Field(min_length=1)


class PredictionTarget(Contract):
    variable_id: Id
    horizon: Text


# ── the ten question kinds ─────────────────────────────────────────────────


class DetectChange(Contract):
    question_kind: Literal["DETECT_CHANGE"]
    variables: tuple[Id, ...] = Field(min_length=1)
    baseline_policy: Text


class ExplainObservation(Contract):
    question_kind: Literal["EXPLAIN_OBSERVATION"]
    variables: tuple[Id, ...] = Field(min_length=1)
    observed_pattern: Text
    context_window: TimeWindow | None = None


class AssessHypothesis(Contract):
    question_kind: Literal["ASSESS_HYPOTHESIS"]
    hypothesis: HypothesisTarget


class EstimateEffect(Contract):
    question_kind: Literal["ESTIMATE_EFFECT"]
    estimand: EstimandSpec
    study_id: Id | None = None


class AssessStudy(Contract):
    question_kind: Literal["ASSESS_STUDY"]
    study_id: Id
    outcomes: tuple[Id, ...] = Field(min_length=1)


class CompareInterventions(Contract):
    question_kind: Literal["COMPARE_INTERVENTIONS"]
    candidates: tuple[Text, ...] = Field(min_length=2)
    utility: Text
    risk_constraints: tuple[Text, ...] = ()


class DesignEvidence(Contract):
    question_kind: Literal["DESIGN_EVIDENCE"]
    competing_hypotheses: tuple[HypothesisTarget, ...] = Field(min_length=2)


class PredictOutcome(Contract):
    question_kind: Literal["PREDICT_OUTCOME"]
    target: PredictionTarget


class EvaluateOutcome(Contract):
    question_kind: Literal["EVALUATE_OUTCOME"]
    receipt_id: Id
    observed_variable: Id


class CheckModel(Contract):
    question_kind: Literal["CHECK_MODEL"]
    assumption: Text
    holdout_ref: Id


QuestionSpec = Annotated[
    Union[DetectChange, ExplainObservation, AssessHypothesis, EstimateEffect, AssessStudy,
          CompareInterventions, DesignEvidence, PredictOutcome, EvaluateOutcome, CheckModel],
    Field(discriminator="question_kind")]


class ExecutionBudget(Contract):
    max_wall_ms: int = Field(gt=0)
    max_queries: int = Field(default=6, ge=0)
    max_rows: int = Field(default=100_000, gt=0)
    max_buckets: int = Field(default=168, gt=0)
    max_cost_units: float = Field(default=0.0, ge=0)


class DataCut(Contract):
    as_of: AwareDatetime
    evidence_watermark: Id | None = None


class ReasoningTask(Contract):
    schema_version: SchemaVersion
    task_id: Id
    question: QuestionSpec
    trigger: Literal["explicit_question", "scheduled", "monitor", "study_completion"]
    source_scope: EnvironmentScope
    data_cut: DataCut
    priority: int = Field(ge=0, le=9)
    budget: ExecutionBudget
    created_at: AwareDatetime
    incident_id: Id | None = None

    @model_validator(mode="after")
    def _incident_only_from_monitor(self) -> "ReasoningTask":
        if self.incident_id is not None and self.trigger != "monitor":
            raise ValueError("incident_id belongs to monitor-triggered tasks only")
        return self


# ── outputs and durable work (shapes frozen now; behavior in Phases 3 and 5) ──

TaskOutcome = Literal["READY", "WAITING_FOR_EVIDENCE", "PARTIAL_BUDGET", "UNSUPPORTED", "INCONCLUSIVE",
                      "FAILED_VALIDATION", "STALE", "COMPLETED"]


class Pins(Contract):
    graph_version: int = Field(ge=0)
    data_cut: AwareDatetime
    evidence_watermark: Id | None = None
    method_versions: dict[Id, Text] = {}


class Finding(Contract):
    finding_id: Id
    claim_kind: ClaimKind
    statement: Text
    support_refs: tuple[Id, ...] = ()
    limitations: tuple[Text, ...] = ()


class TaskRevision(Contract):
    task_id: Id
    revision: int = Field(ge=1)
    supersedes_revision: int | None = None
    outcome: TaskOutcome
    findings: tuple[Finding, ...] = ()
    evidence_gap_ids: tuple[Id, ...] = ()
    pins: Pins
    idempotency_key: Sha256

    @model_validator(mode="after")
    def _monotone(self) -> "TaskRevision":
        if self.supersedes_revision is not None and self.supersedes_revision >= self.revision:
            raise ValueError("a revision can only supersede an earlier one")
        return self


class WorkItem(Contract):
    job_id: Id
    task_id: Id
    task_revision: int = Field(ge=1)
    stage: Literal["T0", "T1", "T2", "T3", "T4"]
    state: Literal["QUEUED", "LEASED", "RUNNING", "CHECKPOINTED", "COMPLETED", "FAILED", "CANCELLED"]
    priority: int = Field(ge=0, le=9)
    attempt: int = Field(ge=0)
    deadline_at: AwareDatetime
    lease_owner: Id | None = None
    lease_until: AwareDatetime | None = None
    remaining_query_budget: int = Field(ge=0)
    pins: Pins
    idempotency_key: Sha256

    @model_validator(mode="after")
    def _lease(self) -> "WorkItem":
        leased = self.state in {"LEASED", "RUNNING"}
        if leased != (self.lease_owner is not None and self.lease_until is not None):
            raise ValueError("LEASED/RUNNING jobs, and only those, carry a lease owner and expiry")
        return self


class Checkpoint(Contract):
    job_id: Id
    sequence: int = Field(ge=0)
    stage: Literal["FRAME", "AUDIT_INPUTS", "SELECT_CAPABILITIES", "ACQUIRE", "EVALUATE", "CHALLENGE", "REPORT"]
    pins: Pins
    payload_hash: Sha256
    queries_spent: int = Field(ge=0)
