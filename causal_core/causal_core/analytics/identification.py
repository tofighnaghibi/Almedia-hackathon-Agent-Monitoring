"""IdentificationAuditor (v3.2 §4.5; plan §F.7; C01, C02, C04, C07, I05, I09; CP-021).

Turns (estimand, design, method result, optional causal view) into an IdentificationAssessment
with one of six statuses. It never certifies from significance, never lets an experiment override a
*different* estimand, and audits only the unknowns relevant to the estimand (C07).

Status rules (first match wins):
  method refused: SRM / assignment → INVALID_DESIGN; any other refusal → INSUFFICIENT_EVIDENCE
  descriptive design or association-only method → ASSOCIATION_ONLY
  randomized + ITT-type functional → CONDITIONAL (PARTIAL if SRM_SUSPECT or differential attrition)
  randomized + CACE → CONDITIONAL (IV assumptions untestable; PARTIAL on the same weakeners)
  randomized + ATE/ATT of exposure → UNIDENTIFIED (C01)
  quasi DiD + ATT → CONDITIONAL (PARTIAL if the pre-trend diagnostic diverged); DiD + ATE → UNIDENTIFIED
  observational: latent confounding or relevant unknown back-door pairs → UNIDENTIFIED; else CONDITIONAL
  transport (source ≠ target scope; user decision 2026-11-22):
    declared transport assumptions → CONDITIONAL is kept, each assumption listed as untestable;
    none declared → at most PARTIAL;
    a sandbox (emulator) or unscoped source moving to another kind of environment → at most PARTIAL even with
    assumptions: §4.5.5 "sandbox causal identification is sandbox-scoped" and I11 are frozen.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass

from ..contracts.evidence import EffectClaim, IdentificationAssessment, MethodProvenance, Uncertainty
from ..contracts.studies import EstimandSpec
from ..graph.views import CausalEvidenceView
from .registry import CapabilitySpec, MethodResult

IDENTIFIABILITY = CapabilitySpec(
    id="identifiability_audit_v1", version="2", question_kinds=("ESTIMATE_EFFECT", "ASSESS_STUDY"),
    input_shapes=("method_result",), output_kind="CAUSAL_EFFECT_CONDITIONAL", estimand_families=None,
    required_metadata=("estimand", "study_design"),
    assumption_checks=("estimand_matches_design", "latent_paths_in_view", "adjustment_set", "transport"),
    dependence_handling="inherits the method result's dependence model", max_work_units=100_000, refusal_codes=(),
    honest_conclusion="scoped IdentificationAssessment; requires no RCT and does not blindly reject every unknown")

ITT_LIKE = {"ITT", "risk_difference", "mean_difference"}
RANDOMIZED_WEAKENERS = {"SRM_SUSPECT": "sample ratio mismatch in the warning band: assignment integrity questionable",
                        "DIFFERENTIAL_ATTRITION": "differential attrition: complete-case ITT may be biased"}
NON_PROMOTABLE_SOURCES = {"emulator", "legacy_unscoped"}     # §4.5.5 / I11: cannot be promoted by assumption


@dataclass(frozen=True)
class EstimandComparison:
    comparable: bool
    differences: tuple[str, ...]


def compare_estimands(a: EstimandSpec, b: EstimandSpec) -> EstimandComparison:
    """C02 / I09: two results disagree meaningfully only if they target the same estimand."""
    diffs = []
    for field in ("outcome", "population", "horizon", "functional"):
        if getattr(a, field) != getattr(b, field):
            diffs.append(f"{field}: {getattr(a, field)!r} vs {getattr(b, field)!r}")
    if (a.contrast.treatment, a.contrast.control) != (b.contrast.treatment, b.contrast.control):
        diffs.append("contrast differs")
    if a.target_scope.environment_id != b.target_scope.environment_id:
        diffs.append(f"target scope: {a.target_scope.environment_id} vs {b.target_scope.environment_id}")
    return EstimandComparison(not diffs, tuple(diffs))


def _status(estimand: EstimandSpec, design: str, result: MethodResult, view: CausalEvidenceView | None,
            treatment_node: str | None, outcome_node: str | None, adjustment_set: tuple[str, ...]) -> tuple[str, list, list]:
    f, notes, untestable = estimand.functional, [], list(result.assumptions_untestable)
    if result.status == "REFUSED":
        if result.refusal_code in {"SRM_FAIL", "ASSIGNMENT_INCONSISTENT"}:
            return "INVALID_DESIGN", [f"{result.refusal_code}: {result.refusal_reason}"], untestable
        return "INSUFFICIENT_EVIDENCE", [f"{result.refusal_code}: {result.refusal_reason}"], untestable
    if design == "descriptive" or result.claim_kind == "ASSOCIATION":
        return "ASSOCIATION_ONLY", ["design or method yields association only"], untestable
    if design == "randomized":
        weak = [why for code, why in RANDOMIZED_WEAKENERS.items() if any(l.startswith(code) for l in result.limitations)]
        if f in ITT_LIKE:
            if weak:
                return "PARTIAL_IDENTIFICATION", weak, untestable
            return "CONDITIONAL_IDENTIFICATION", ["randomization identifies the effect of assignment"], untestable
        if f == "CACE" and result.values.get("functional") == "CACE":
            if weak:
                return "PARTIAL_IDENTIFICATION", ["complier effect under IV assumptions, weakened: " + "; ".join(weak)], untestable
            return "CONDITIONAL_IDENTIFICATION", ["complier effect under IV assumptions"], untestable
        return "UNIDENTIFIED", [f"{f} of actual exposure is not identified by random assignment alone (C01)"], untestable
    if design == "quasi_experimental":
        if f != "ATT":
            return "UNIDENTIFIED", [f"difference-in-differences identifies the ATT, not the {f}"], untestable
        if any(l.startswith("PRETREND_DIVERGENCE") for l in result.limitations):
            return "PARTIAL_IDENTIFICATION", ["pre-period trends diverge: parallel trends implausible"], untestable
        return "CONDITIONAL_IDENTIFICATION", ["ATT under untestable parallel trends (pre-trend pass is not proof, C04)"], untestable
    if design == "observational":
        blockers = []
        if view is not None and treatment_node and outcome_node:
            rel = view.relationship(treatment_node, outcome_node)
            if any(e.kind == "LATENT_CONFOUNDING" for e in rel.active):
                blockers.append(f"LATENT_CONFOUNDING({treatment_node}, {outcome_node}) is unblocked")
            relevant = [p for p in view.unknowns_relevant_to(treatment_node, outcome_node) if p != (treatment_node, outcome_node)]
            open_paths = [p for p in relevant if p[0] not in adjustment_set]
            if open_paths:
                blockers.append(f"unadjusted causes of the treatment with unknown links to the outcome: {open_paths}")
        if blockers or not adjustment_set:
            return "UNIDENTIFIED", blockers or ["no adjustment set declared: no-unmeasured-confounding unsupported"], untestable
        untestable.append(f"no unmeasured confounding given {list(adjustment_set)}")
        return "CONDITIONAL_IDENTIFICATION", ["back-door adjustment under declared assumptions"], untestable
    return "INSUFFICIENT_EVIDENCE", [f"unknown design {design!r}"], untestable


def assess(estimand: EstimandSpec, design: str, result: MethodResult, *, evidence_ids: tuple[str, ...],
           view: CausalEvidenceView | None = None, treatment_node: str | None = None, outcome_node: str | None = None,
           adjustment_set: tuple[str, ...] = (), transport_assumptions: tuple[str, ...] = ()) -> IdentificationAssessment:
    status, notes, untestable = _status(estimand, design, result, view, treatment_node, outcome_node, adjustment_set)
    declared = tuple(dict.fromkeys(a.strip() for a in transport_assumptions if a and a.strip()))
    if estimand.requires_transport and status in {"CONDITIONAL_IDENTIFICATION", "PARTIAL_IDENTIFICATION"}:
        src, tgt = estimand.source_scope, estimand.target_scope
        route = f"{src.environment_id} ({src.environment_kind}) to {tgt.environment_id} ({tgt.environment_kind})"
        if src.environment_kind in NON_PROMOTABLE_SOURCES and tgt.environment_kind != src.environment_kind:
            status = "PARTIAL_IDENTIFICATION"
            notes.append(f"transport from {route}: a {src.environment_kind} result is not promoted by declared assumptions "
                         "(§4.5.5 sandbox-scoped; I11)")
            untestable.append("transportability of the effect to the target scope")
        elif not declared:
            status = "PARTIAL_IDENTIFICATION"
            notes.append(f"transport from {route} without declared transport assumptions")
            untestable.append("transportability of the effect to the target scope")
        else:
            notes.append(f"transport from {route} under {len(declared)} declared assumption(s); no sensitivity analysis in P0")
            untestable.extend(f"transport: {a}" for a in declared)
    key = [estimand.model_dump(mode="json"), design, result.method_id, result.values.get("estimate")]
    if declared:                       # different declared assumptions are a different assessment (ids unchanged otherwise)
        key.append(list(declared))
    aid = "ia:" + hashlib.sha256(json.dumps(key, sort_keys=True, default=str).encode()).hexdigest()[:16]
    return IdentificationAssessment(
        schema_version="2.0", assessment_id=aid, estimand=estimand, strategy=f"{design}:{result.method_id}", status=status,
        assumptions_checked=tuple(result.assumptions_checked) + tuple(notes),
        assumptions_untestable=tuple(dict.fromkeys(untestable)), scope=estimand.target_scope,
        evidence_ids=evidence_ids, method=MethodProvenance(method_id=result.method_id, method_version=result.method_version))


def effect_claim(assessment: IdentificationAssessment, result: MethodResult, dependence_model: str) -> EffectClaim | None:
    """An EffectClaim exists only for CONDITIONAL/PARTIAL identification with an uncertainty. Never a graph edge."""
    if assessment.status not in {"CONDITIONAL_IDENTIFICATION", "PARTIAL_IDENTIFICATION"}:
        return None
    v = result.values
    return EffectClaim(schema_version="2.0", claim_id="claim:" + assessment.assessment_id[3:], estimand=assessment.estimand,
                       assessment_id=assessment.assessment_id, assessment_status=assessment.status, estimate=v["estimate"],
                       uncertainty=Uncertainty(se=v["se"], interval=tuple(v["ci"])), dependence_model=dependence_model,
                       scope=assessment.scope, evidence_ids=assessment.evidence_ids, method=assessment.method)
