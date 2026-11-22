"""ActionSafetyPolicyGate and the output serializer (v3.2 §6, §4.7; plan §G task 6; I15, G17; CP-026).

P0 has no actuation surface. The gate decides only how a finished (or provisional) result may leave the
core:
  PUBLISH_REPORT       read-only report; every claim carries evidence IDs, scope, versions and its audit
  PUBLISH_PROVISIONAL  an early revision (T0) that says it is provisional
  DESIGN_TEST_ONLY     the result IS a test or measurement design (DESIGN_EVIDENCE): a proposal, never an action;
                       a report that merely lists evidence gaps stays a read-only report
  DENY                 an action was requested (P0 cannot act), or a claim breaks the output rules
A statistically strong effect does not grant permission to act (§6.2).
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict

from .planner import PlanState

VERDICTS = ("PUBLISH_REPORT", "PUBLISH_PROVISIONAL", "DESIGN_TEST_ONLY", "DENY")


@dataclass(frozen=True)
class PolicyDecision:
    verdict: str
    reasons: tuple[str, ...]


def vet(state: PlanState, *, provisional: bool = False, requested_action: str | None = None) -> PolicyDecision:
    reasons: list[str] = []
    if requested_action:
        return PolicyDecision("DENY", (f"requested action {requested_action!r}: P0 has no actuation or test-execution "
                                       "port (I15, G17); propose it as a test design instead",))
    task_scope = state.task.source_scope.environment_id
    for f in state.findings:
        if f.claim_kind == "CAUSAL_EFFECT_CONDITIONAL":
            claim = next((c for c in state.claims if c.claim_id in f.support_refs), None)
            if claim is None:
                return PolicyDecision("DENY", (f"finding {f.finding_id} states an effect without an EffectClaim",))
            if not claim.evidence_ids or claim.evidence_ids == ("none",):
                return PolicyDecision("DENY", (f"claim {claim.claim_id} has no evidence IDs",))
            if claim.estimand.source_scope.environment_id != task_scope:
                return PolicyDecision("DENY", (f"claim {claim.claim_id} evidence scope differs from the task scope",))
            if claim.assessment_status == "PARTIAL_IDENTIFICATION" and not f.limitations:
                return PolicyDecision("DENY", (f"partial claim {claim.claim_id} must state its limitations",))
    if provisional:
        return PolicyDecision("PUBLISH_PROVISIONAL", ("early revision; superseded by the final revision",))
    # CP-026 fix: a result is a *test proposal* only when its deliverable is a test or measurement design. A report that
    # also lists evidence gaps (an unsupported question, a missing study) is still a read-only report.
    if any(f.claim_kind == "ACTION_RECOMMENDATION" for f in state.findings):
        return PolicyDecision("DESIGN_TEST_ONLY", ("the result is a test/measurement design: a proposal, nothing is executed",))
    if any(f.claim_kind == "MODEL_PREDICTION" for f in state.findings):
        reasons.append("contains model predictions: not evidence, no authority to act")
    if state.gaps:
        reasons.append(f"read-only report; {len(state.gaps)} recommended next measurement(s) listed, none executed")
        return PolicyDecision("PUBLISH_REPORT", tuple(reasons))
    return PolicyDecision("PUBLISH_REPORT", tuple(reasons) or ("read-only report",))


class ReportEnvelope(BaseModel):
    """The JSON result schema (plan §G deliverable). Written to docs/phase5/result_schema.json by the demo."""
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: str
    task_id: str
    revision: int
    provisional: bool
    trigger: str
    question_kind: str
    outcome: str
    policy: dict[str, Any]
    findings: list[dict[str, Any]]
    effect_claims: list[dict[str, Any]]
    identification: list[dict[str, Any]]
    method_trace: list[dict[str, Any]]
    stage_trace: list[dict[str, Any]]
    rivals_and_assumptions: list[str]
    evidence_gaps: list[dict[str, Any]]
    failures: list[dict[str, Any]]
    evidence_ids: list[str]
    pins: dict[str, Any]
    abstained: bool


def serialize(state: PlanState, decision: PolicyDecision, *, revision: int, provisional: bool, pins: dict) -> dict:
    claims = [{"claim_id": c.claim_id, "estimand": c.estimand.functional, "treatment": c.estimand.contrast.treatment,
               "control": c.estimand.contrast.control, "outcome": c.estimand.outcome, "estimate": c.estimate,
               "interval": list(c.uncertainty.interval), "status": c.assessment_status,
               "scope": c.scope.environment_id, "evidence_ids": len(c.evidence_ids),
               "method": f"{c.method.method_id} v{c.method.method_version}"} for c in state.claims]
    env = ReportEnvelope(
        schema_version="2.0", task_id=state.task.task_id, revision=revision, provisional=provisional,
        trigger=state.task.trigger, question_kind=state.task.question.question_kind, outcome=state.outcome or "READY",
        policy={"verdict": decision.verdict, "reasons": list(decision.reasons)},
        findings=[f.model_dump(mode="json") for f in state.findings], effect_claims=claims,
        identification=[{"assessment_id": a.assessment_id, "status": a.status,
                         "untestable": list(a.assumptions_untestable)} for a in state.assessments],
        method_trace=[{"method": m, "applicability": st, "reasons": list(r)} for m, st, r in state.applicability],
        stage_trace=[{"stage": s, "verdict": v, "note": n} for s, v, n in state.trace],
        rivals_and_assumptions=list(state.challenges),
        evidence_gaps=[g.model_dump(mode="json") for g in state.gaps],
        failures=[{"class": f.cls, "stage": f.stage, "detail": f.detail} for f in state.failures],
        evidence_ids=list(state.evidence_ids), pins=pins,
        abstained=not claims and not any(f.claim_kind in ("DESCRIPTIVE", "STRUCTURAL_CAUSAL_HYPOTHESIS", "ACTION_RECOMMENDATION")
                                         for f in state.findings))
    return env.model_dump(mode="json")
