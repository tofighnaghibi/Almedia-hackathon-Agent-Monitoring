"""Question-first planner FSM (v3.2 §4.2; plan §G tasks 1, 4, 5, 7; CP-023, CP-024).

FRAME → AUDIT_INPUTS → SELECT_CAPABILITIES → ACQUIRE → EVALUATE → CHALLENGE → REPORT

Pure: every stage takes an immutable `PlanState` and returns a new one. The shell (coordinator) only
executes the typed requests ACQUIRE asks for and feeds the responses back. Routing is by question kind and
payload *shape*, never by domain: the planner has no metric, experiment, sensor or anomaly vocabulary, and
skips stages that a question does not need (a study never passes through change detection; a hypothesis
question may stop after the graph audit).

Every non-success path carries a typed failure class (plan §G.7). A method refusal is never turned into a
causal abstention silently: both the class and the cause are kept.
"""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field, replace
from typing import Mapping

from ..analytics import identification as ia
from ..analytics.baseline import LearnedBaseline, evaluate_baseline_shift
from ..analytics.catalog import REGISTRY
from ..analytics.decomposition import Stratum, evaluate_kitagawa
from ..analytics.descriptive import BucketQuality, evaluate_dq
from ..analytics.experiments import ArmSummary, evaluate_randomized, evaluate_randomized_summary
from ..analytics.registry import MethodInput, MethodResult
from ..analytics.sequential import evaluate_eprocess
from ..analytics.voi import QueryOption, rank
from ..contracts.mechanisms import CounterfactualPrediction, InterventionSpec
from ..counterfactual.rollouts import members, rollout
from ..contracts.capabilities import CapabilityRequest, CapabilityResponse, ProviderManifest
from ..contracts.evidence import EvidenceGap
from ..contracts.studies import RandomizedDesign
from ..contracts.tasks import Finding, ReasoningTask

STAGES = ("FRAME", "AUDIT_INPUTS", "SELECT_CAPABILITIES", "ACQUIRE", "EVALUATE", "CHALLENGE", "REPORT")

FAILURE_CLASSES = ("UNSUPPORTED_QUESTION", "ILL_DEFINED_TARGET", "SCOPE_MISMATCH", "MISSING_EVIDENCE",
                   "METHOD_REFUSAL", "DQ_FAULT", "SOURCE_TIMEOUT", "SOURCE_FAILURE", "QUOTA",
                   "STATISTICAL_INVALIDITY", "STALE_SNAPSHOT", "COMPUTATION_ERROR",
                   "UNSUPPORTED_MODEL", "OUT_OF_REGIME", "CAP_EXCEEDED")

# Question kind → payload shapes that can answer it (preference order). Shapes, not domains.
QUESTION_SHAPES: dict[str, tuple[str, ...]] = {
    "ESTIMATE_EFFECT": ("assignment_outcomes", "aggregate_stats"),     # unit rows, or per-arm summaries (CP-032)
    "ASSESS_STUDY": ("assignment_outcomes", "aggregate_stats"),
    "DETECT_CHANGE": ("time_series", "state_trajectory"),
    "EXPLAIN_OBSERVATION": ("aggregate_stats",),
    "DESIGN_EVIDENCE": (),
    "ASSESS_HYPOTHESIS": (),
    "PREDICT_OUTCOME": ("time_series",),             # P1 (Phase 7, user-authorized 2026-11-22)
    "COMPARE_INTERVENTIONS": ("time_series",),
}
WHAT_IF = ("PREDICT_OUTCOME", "COMPARE_INTERVENTIONS")
SHAPE_TO_REQUEST = {"assignment_outcomes": "get_assignment_outcomes", "time_series": "get_series",
                    "state_trajectory": "get_series", "aggregate_stats": "get_strata"}
P1_QUESTIONS = {"EVALUATE_OUTCOME": "needs action receipts from sandbox tests (P1, Phase 8: not authorized)",
                "CHECK_MODEL": "held-out model checks run when a mechanism is registered (validate_mechanism); "
                               "the CHECK_MODEL question route is not wired"}
# Shape-level runners exist in this phase for these questions; the slices (Phase 6) add more.
NOT_YET_WIRED: dict[str, str] = {}
# Strata responses identify their segment with Observation.segment (CP-031; replaced the record_key convention of CP-027).


@dataclass(frozen=True)
class MonitorSpec:
    """The null for one monitored variable: declared (mean, sd), or a baseline learned on an OLDER closed window and
    frozen (CP-030). Never estimated from the data being monitored."""
    null_mean: float = 0.0
    null_sd: float = 1.0
    dependence: str = "independent"
    alpha: float = 0.05
    family_size: int = 1
    baseline: LearnedBaseline | None = None

    @property
    def null_spec(self) -> str:
        return f"N(mean={self.null_mean:g}, sd={self.null_sd:g}) per point"


@dataclass(frozen=True)
class TaskContext:
    """Attested, registered metadata the planner may use (CP-023). Absent metadata is a gap, never a default."""
    studies: Mapping[str, object] = field(default_factory=dict)          # study_id → StudyDesign
    monitors: Mapping[str, MonitorSpec] = field(default_factory=dict)     # variable_id → declared null
    view: object | None = None                                           # pinned CausalEvidenceView
    temporal_view: object | None = None                                  # pinned TemporalView (lagged mechanisms)
    graph_version: int = 0
    hypotheses: tuple[str, ...] = ()
    query_options: tuple[QueryOption, ...] = ()
    transport_assumptions: tuple[str, ...] = ()
    dimensions: Mapping[str, object] = field(default_factory=dict)       # variable_id → DimensionSpec to split by
    mechanisms: Mapping[str, object] = field(default_factory=dict)       # target → MechanismSpec (P1; only VALIDATED run)
    rollout_samples: int = 2_000
    rollout_seed: int = 0
    exogenous_policy: str = "mean_7"          # user decision 2026-11-22: "no change" = 7-day mean of exogenous inputs
    regime_margin: float = 0.05               # user decision 2026-11-22: fitted range ± 5%
    risk_tolerance: float = 0.05              # a candidate whose constraint is violated in > 5% of paths fails it


@dataclass(frozen=True)
class Failure:
    cls: str
    stage: str
    detail: str

    def __post_init__(self):
        if self.cls not in FAILURE_CLASSES:
            raise ValueError(f"undeclared failure class {self.cls}")


@dataclass(frozen=True)
class PlanState:
    task: ReasoningTask
    stage: str = "FRAME"
    done: bool = False
    outcome: str | None = None
    frame: Mapping = field(default_factory=dict)
    trace: tuple[tuple[str, str, str], ...] = ()            # (stage, verdict, note)
    applicability: tuple[tuple[str, str, tuple[str, ...]], ...] = ()   # (method, status, reasons)
    selected: tuple[str, ...] = ()
    requests: tuple[CapabilityRequest, ...] = ()
    responses: tuple[CapabilityResponse, ...] = ()
    results: tuple[tuple[str, MethodResult], ...] = ()
    assessments: tuple = ()
    claims: tuple = ()
    findings: tuple[Finding, ...] = ()
    challenges: tuple[str, ...] = ()
    gaps: tuple[EvidenceGap, ...] = ()
    failures: tuple[Failure, ...] = ()
    evidence_ids: tuple[str, ...] = ()

    def log(self, verdict: str, note: str) -> "PlanState":
        return replace(self, trace=self.trace + ((self.stage, verdict, note),))

    def fail(self, cls: str, detail: str, outcome: str) -> "PlanState":
        s = self.log("STOP", f"{cls}: {detail}")
        return replace(s, failures=s.failures + (Failure(cls, self.stage, detail),), outcome=outcome, done=True,
                       stage="REPORT")

    def gap(self, missing: str, *recommend: str, blocks: str | None = None, executable: bool = False) -> "PlanState":
        gid = "gap:" + hashlib.sha256(f"{self.task.task_id}|{missing}".encode()).hexdigest()[:12]
        g = EvidenceGap(schema_version="2.0", gap_id=gid, task_id=self.task.task_id, missing=missing,
                        recommended_measurements=tuple(recommend), blocks_claim=blocks, executable_now=executable)
        return replace(self, gaps=self.gaps + (g,))


def _finding(state: PlanState, claim_kind: str, statement: str, refs=(), limitations=()) -> Finding:
    fid = "f:" + hashlib.sha256(f"{state.task.task_id}|{len(state.findings)}|{statement}".encode()).hexdigest()[:12]
    return Finding(finding_id=fid, claim_kind=claim_kind, statement=statement, support_refs=tuple(refs),
                   limitations=tuple(limitations))


def _request_for(kind: str, shape: str) -> str | None:
    """Study questions always ask for the study's assignment/outcome data; the provider answers with unit rows or
    per-arm summaries (segment dimension "arm")."""
    return "get_assignment_outcomes" if kind in ("ESTIMATE_EFFECT", "ASSESS_STUDY") else SHAPE_TO_REQUEST.get(shape)


# ── FRAME ────────────────────────────────────────────────────────────────


def frame(state: PlanState, ctx: TaskContext) -> PlanState:
    q = state.task.question
    kind = q.question_kind
    if kind in P1_QUESTIONS:
        return state.gap(f"{kind} is not supported in P0", P1_QUESTIONS[kind]).fail(
            "UNSUPPORTED_QUESTION", P1_QUESTIONS[kind], "UNSUPPORTED")
    if kind in NOT_YET_WIRED:
        return state.fail("UNSUPPORTED_QUESTION", NOT_YET_WIRED[kind], "UNSUPPORTED")
    f: dict = {"question_kind": kind, "data_cut": state.task.data_cut.as_of.isoformat(),
               "scope": state.task.source_scope.environment_id, "graph_version": ctx.graph_version}
    if kind == "ESTIMATE_EFFECT":
        e = q.estimand
        f.update(variables=(e.outcome,), estimand=e.functional, treatment=e.contrast.treatment,
                 control=e.contrast.control, population=e.population, horizon=e.horizon, study_id=q.study_id)
        if q.study_id is None:
            s = state.gap(f"no identified data source for {e.functional} of {e.contrast.treatment} on {e.outcome}",
                          f"randomize {e.contrast.treatment} vs {e.contrast.control} and register the study",
                          "register an existing study or quasi-experiment that targets this estimand",
                          blocks="CAUSAL_EFFECT_CONDITIONAL")
            return replace(s, frame=f).fail("MISSING_EVIDENCE", "the effect question names no study; the planner does "
                                            "not invent one", "WAITING_FOR_EVIDENCE")
    elif kind == "ASSESS_STUDY":
        f.update(variables=tuple(q.outcomes), study_id=q.study_id, estimand="ITT")
    elif kind == "DETECT_CHANGE":
        f.update(variables=tuple(q.variables), baseline_policy=q.baseline_policy)
    elif kind == "EXPLAIN_OBSERVATION":
        if q.context_window is None:
            return state.gap("the observed pattern has no time window", "state the window in which the pattern was seen").fail(
                "ILL_DEFINED_TARGET", "an observation to explain needs its time window; none is invented", "FAILED_VALIDATION")
        w = q.context_window
        f.update(variables=tuple(q.variables), pattern=q.observed_pattern, current=(w.start.isoformat(), w.end.isoformat()),
                 baseline=((w.start - (w.end - w.start)).isoformat(), w.start.isoformat()))
    elif kind == "DESIGN_EVIDENCE":
        f.update(hypotheses=tuple(h.statement for h in q.competing_hypotheses))
    elif kind == "ASSESS_HYPOTHESIS":
        f.update(source=q.hypothesis.source, target=q.hypothesis.target, statement=q.hypothesis.statement)
    elif kind in WHAT_IF:
        import re
        if kind == "PREDICT_OUTCOME":
            target, horizon_text, candidates = q.target.variable_id, q.target.horizon, ()
        else:
            m = re.fullmatch(r"\s*(maximize|minimize)\s+([A-Za-z0-9_.:\-/]+)\s+over\s+(\d+)d\s*", q.utility)
            if not m:
                return state.gap("utility must read 'maximize|minimize <variable> over <N>d'",
                                 "restate the utility with its target and horizon").fail(
                    "ILL_DEFINED_TARGET", f"cannot read the utility {q.utility!r}; no target or horizon is invented",
                    "FAILED_VALIDATION")
            target, horizon_text, candidates = m.group(2), f"{m.group(3)}d", q.candidates
            f.update(direction=m.group(1))
        hm = re.fullmatch(r"\s*(\d+)d\s*", horizon_text)
        if not hm:
            return state.fail("ILL_DEFINED_TARGET", f"horizon {horizon_text!r} is not '<N>d'", "FAILED_VALIDATION")
        parsed = []
        for c in candidates:
            cm = re.fullmatch(r"\s*([A-Za-z0-9_.:\-/]+)\s*(\+=|=)\s*(-?[0-9.]+)\s*", c)
            if not cm:
                return state.fail("ILL_DEFINED_TARGET", f"candidate {c!r} is not '<variable>=<value>' or "
                                  "'<variable>+=<delta>'", "FAILED_VALIDATION")
            parsed.append((c, cm.group(1), "shift" if cm.group(2) == "+=" else "set", float(cm.group(3))))
        constraints = []
        for c in (getattr(q, "risk_constraints", ()) or ()):
            rm = re.fullmatch(r"\s*([A-Za-z0-9_.:\-/]+)\s*(<=|>=|<|>)\s*(-?[0-9.]+)\s*", c)
            if not rm:
                return state.gap(f"risk constraint {c!r} is not '<variable> <=|>=|<|> <number>'",
                                 "restate the constraint in that form").fail(
                    "ILL_DEFINED_TARGET", f"cannot check the risk constraint {c!r}; refusing rather than ignoring it",
                    "FAILED_VALIDATION")
            constraints.append((rm.group(1), rm.group(2), float(rm.group(3))))
        f.update(target=target, horizon=int(hm.group(1)), candidates=tuple(parsed), constraints=tuple(constraints),
                 risk_tolerance=ctx.risk_tolerance)
    s = replace(state, frame=f).log("PASS", f"{kind}: target pinned (data cut {f['data_cut']}, graph v{ctx.graph_version})")
    return replace(s, stage="AUDIT_INPUTS")


# ── AUDIT_INPUTS ─────────────────────────────────────────────────────────


def audit_inputs(state: PlanState, ctx: TaskContext, manifest: ProviderManifest | None) -> PlanState:
    f, kind = state.frame, state.frame["question_kind"]
    notes = []
    if kind in ("DESIGN_EVIDENCE", "ASSESS_HYPOTHESIS"):
        return replace(state.log("PASS", "no source data needed for this question"), stage="SELECT_CAPABILITIES")
    if manifest is None:
        return state.fail("MISSING_EVIDENCE", "no evidence provider configured", "WAITING_FOR_EVIDENCE")
    scopes = {s.environment_id for s in manifest.scopes}
    if f["scope"] not in scopes:
        return state.fail("SCOPE_MISMATCH", f"provider {manifest.provider_id} serves {sorted(scopes)}, task is scoped to "
                          f"{f['scope']}: no silent transport", "FAILED_VALIDATION")
    if manifest.expected_latency_ms is None or manifest.cost_units_per_request is None:
        notes.append("provider latency/cost UNKNOWN: penalised in acquisition, not treated as free")
    if kind in ("ESTIMATE_EFFECT", "ASSESS_STUDY"):
        design = ctx.studies.get(f["study_id"])
        if design is None:
            s = state.gap(f"study {f['study_id']} has no registered, attested design",
                          "register the study design (assignment unit, allocation, interference, compliance)")
            return s.fail("MISSING_EVIDENCE", f"no attested design for study {f['study_id']}; a design is never "
                          "inferred from outcome data", "WAITING_FOR_EVIDENCE")
        notes.append(f"design {design.design} ({getattr(design, 'randomization', '?')}), attested by "
                     f"{design.provenance.source_id}")
    if kind in WHAT_IF:
        if f["target"] not in ctx.mechanisms:
            s = state.gap(f"no registered mechanism for {f['target']}",
                          "fit and validate mechanisms for the target and its ancestors on held-out data")
            return s.fail("UNSUPPORTED_MODEL", f"no mechanism for {f['target']}: an equation is never invented",
                          "UNSUPPORTED")
        nodes, stack = set(), [f["target"]]
        while stack:
            v = stack.pop()
            if v in nodes:
                continue
            nodes.add(v)
            if v in ctx.mechanisms:
                stack.extend(p.variable_id for m, _ in members(ctx.mechanisms[v]) for p in m.parents)
        f = {**f, "variables": tuple(sorted(nodes))}
        notes.append(f"bounded subgraph for {f['target']}: {sorted(nodes)}; mechanisms "
                     + ", ".join(f"{m.mechanism_id} v{m.version} {m.status} (w {wt:.2f})"
                                 for v in sorted(nodes) if v in ctx.mechanisms for m, wt in members(ctx.mechanisms[v])))
        state = replace(state, frame=f)
    if kind == "EXPLAIN_OBSERVATION":
        missing = [v for v in f["variables"] if v not in ctx.dimensions]
        if missing:
            s = state.gap(f"no registered dimension to split {missing}", "register a DimensionSpec with its partition policy")
            return s.fail("MISSING_EVIDENCE", f"no registered partition for {missing}", "WAITING_FOR_EVIDENCE")
        notes.append("partition " + ", ".join(f"{v}: {ctx.dimensions[v].dimension_id} ({ctx.dimensions[v].partition_policy})"
                                              for v in f["variables"]))
    if kind == "DETECT_CHANGE":
        missing = [v for v in f["variables"] if v not in ctx.monitors]
        if missing:
            s = state.gap(f"no declared null for {missing}", "declare the null (mean, sd, dependence) for each monitored variable")
            return s.fail("MISSING_EVIDENCE", f"no declared null for {missing}: a change test needs one", "WAITING_FOR_EVIDENCE")
    return replace(state.log("PASS", "; ".join(notes) or "inputs audited"), stage="SELECT_CAPABILITIES")


# ── SELECT_CAPABILITIES ──────────────────────────────────────────────────


def select_capabilities(state: PlanState, ctx: TaskContext, manifest: ProviderManifest | None) -> PlanState:
    f, kind = state.frame, state.frame["question_kind"]
    if kind in ("DESIGN_EVIDENCE", "ASSESS_HYPOTHESIS"):
        shapes = ("query_options",) if kind == "DESIGN_EVIDENCE" else ("method_result",)
        meta = frozenset({"hypotheses"}) if kind == "DESIGN_EVIDENCE" else frozenset({"estimand", "study_design"})
    else:
        offered = set(manifest.supported_requests) if manifest else set()
        shapes = tuple(sh for sh in QUESTION_SHAPES.get(kind, ()) if _request_for(kind, sh) in offered)
        if not shapes:
            s = state.gap(f"provider offers none of {sorted({_request_for(kind, x) for x in QUESTION_SHAPES.get(kind, ())})}",
                          "connect a provider that can serve the needed data shape")
            return s.fail("MISSING_EVIDENCE", "no provider request can produce a usable data shape", "WAITING_FOR_EVIDENCE")
        meta = {"alpha", "family_size"}
        if kind in ("ESTIMATE_EFFECT", "ASSESS_STUDY"):
            meta |= {"study_design"}
        if kind == "DETECT_CHANGE":
            learned = all(ctx.monitors[v].baseline is not None for v in f["variables"])
            meta |= {"learned_baseline"} if learned else {"null_spec", "dependence"}
        if kind == "EXPLAIN_OBSERVATION":
            meta |= {"strata", "ratio_metric"}
        if kind in WHAT_IF:
            meta = frozenset({"validated_mechanisms"})
        meta = frozenset(meta)
    design = ctx.studies.get(f.get("study_id")) if f.get("study_id") else None
    regime = getattr(design, "design", None)
    inp = MethodInput(kind if kind != "ASSESS_HYPOTHESIS" else "ESTIMATE_EFFECT", shapes, meta, f.get("estimand"),
                      source_regime=regime)
    ok, trace = REGISTRY.select(inp)
    app = tuple((a.method_id, a.status, tuple(a.reasons)) for a in trace)
    chosen = tuple(sp.id for sp in ok if sp.id != "identifiability_audit_v1" or kind == "ASSESS_HYPOTHESIS")
    if kind == "ASSESS_HYPOTHESIS":
        chosen = ("graph_audit",)
    s = replace(state, applicability=app, selected=chosen)
    if not chosen:
        return s.fail("METHOD_REFUSAL", "no registered method accepts this question, shape, metadata and estimand "
                      "(trace kept)", "UNSUPPORTED")
    return replace(s.log("PASS", f"applicable: {', '.join(chosen)}; refused {sum(a[1] == 'UNSUPPORTED' for a in app)} "
                         "other methods with reasons"),
                   stage="ACQUIRE")


# ── ACQUIRE (pure planning; the shell executes) ──────────────────────────


def plan_requests(state: PlanState, ctx: TaskContext, manifest: ProviderManifest | None) -> PlanState:
    from pydantic import TypeAdapter
    f, kind = state.frame, state.frame["question_kind"]
    if kind in ("DESIGN_EVIDENCE", "ASSESS_HYPOTHESIS"):
        return replace(state.log("SKIP", "no source query needed"), stage="EVALUATE")
    task = state.task
    base = {"schema_version": "2.0", "variables": list(f["variables"]), "scope": task.source_scope.model_dump(mode="json"),
            "data_cut": task.data_cut.as_of.isoformat(), "max_rows": task.budget.max_rows,
            "purpose": f"{kind} for task {task.task_id}", "deadline_ms": max(1, min(task.budget.max_wall_ms, 5_000))}
    reqs = []
    if kind in ("ESTIMATE_EFFECT", "ASSESS_STUDY"):
        reqs.append({**base, "request": "get_assignment_outcomes", "request_id": f"{task.task_id}:ao",
                     "study_id": f["study_id"]})
    elif kind == "DETECT_CHANGE" or kind in WHAT_IF:
        reqs.append({**base, "request": "get_series", "request_id": f"{task.task_id}:series", "grain": "1d",
                     "max_buckets": task.budget.max_buckets})
    elif kind == "EXPLAIN_OBSERVATION":
        for v in f["variables"]:
            dim = ctx.dimensions[v]
            reqs.append({**base, "variables": [v], "request": "get_strata", "request_id": f"{task.task_id}:strata:{v}",
                         "dimension": dim.dimension_id, "top_k": dim.top_k, "include_other": True, "bucket_grain": "1d",
                         "window": {"start": f["baseline"][0], "end": f["current"][1]},
                         "max_buckets": task.budget.max_buckets})
    adapter = TypeAdapter(CapabilityRequest)
    parsed = tuple(adapter.validate_json(json.dumps(r)) for r in reqs)
    if len(parsed) > task.budget.max_queries:
        s = state.gap(f"{len(parsed)} source quer{'y' if len(parsed) == 1 else 'ies'} needed, budget allows "
                      f"{task.budget.max_queries}", "raise the task's query budget", executable=True)
        return s.fail("QUOTA", f"query budget {task.budget.max_queries} < {len(parsed)} needed; nothing was sent",
                      "PARTIAL_BUDGET")
    return replace(state.log("PLAN", f"{len(parsed)} typed request(s): {', '.join(r.request for r in parsed)}"),
                   requests=parsed)


def ingest_responses(state: PlanState, responses: tuple[CapabilityResponse, ...]) -> PlanState:
    s = replace(state, responses=responses)
    bad = [r for r in responses if r.status in ("TIMEOUT", "FAILED", "UNSUPPORTED", "BUDGET_EXCEEDED")]
    if bad:
        r = bad[0]
        cls = {"TIMEOUT": "SOURCE_TIMEOUT", "FAILED": "SOURCE_FAILURE", "UNSUPPORTED": "MISSING_EVIDENCE",
               "BUDGET_EXCEEDED": "QUOTA"}[r.status]
        s = s.gap(f"request {r.request_id} returned {r.status}: {r.reason}", "retry later or use another provider",
                  executable=True)
        return s.fail(cls, f"{r.request_id}: {r.reason}", "INCONCLUSIVE" if cls != "QUOTA" else "PARTIAL_BUDGET")
    notes = []
    for r in responses:
        if r.status == "PARTIAL":
            notes.append(f"{r.request_id} PARTIAL ({r.reason})")
        if not r.observations:
            return s.gap(f"{r.request_id} returned no visible observations at the data cut",
                         "wait for the data to become available, or move the data cut").fail(
                "MISSING_EVIDENCE", f"{r.request_id}: nothing visible at the data cut", "WAITING_FOR_EVIDENCE")
    ids = tuple(o.observation_id for r in responses for o in r.observations)
    return replace(s.log("PASS", f"{len(ids)} observation(s) received" + (f"; {'; '.join(notes)}" if notes else "")),
                   evidence_ids=ids, stage="EVALUATE")


# ── EVALUATE ─────────────────────────────────────────────────────────────


def evaluate(state: PlanState, ctx: TaskContext) -> PlanState:
    f, kind = state.frame, state.frame["question_kind"]
    try:
        if kind in ("ESTIMATE_EFFECT", "ASSESS_STUDY"):
            return _evaluate_study(state, ctx)
        if kind == "DETECT_CHANGE":
            return _evaluate_change(state, ctx)
        if kind == "EXPLAIN_OBSERVATION":
            return _evaluate_explain(state, ctx)
        if kind in WHAT_IF:
            return _evaluate_what_if(state, ctx)
        if kind == "DESIGN_EVIDENCE":
            return _evaluate_design(state, ctx)
        if kind == "ASSESS_HYPOTHESIS":
            return _evaluate_hypothesis(state, ctx)
    except (ValueError, ArithmeticError, KeyError) as exc:          # never silently becomes a causal abstention
        return state.fail("COMPUTATION_ERROR", f"{type(exc).__name__}: {exc}", "INCONCLUSIVE")
    return state.fail("UNSUPPORTED_QUESTION", f"no evaluator for {kind}", "UNSUPPORTED")


def _evaluate_study(state: PlanState, ctx: TaskContext) -> PlanState:
    f, q = state.frame, state.task.question
    design = ctx.studies[f["study_id"]]
    payloads = [o.payload for r in state.responses for o in r.observations if o.payload.shape == "assignment_outcomes"]
    summaries = {next(x.value for x in o.segment if x.dimension_id == "arm"): o
                 for r in state.responses for o in r.observations
                 if o.payload.shape == "aggregate_stats" and any(x.dimension_id == "arm" for x in o.segment)
                 and o.variable_id in f["variables"]}
    if not payloads and not summaries:
        return state.fail("MISSING_EVIDENCE", "no assignment/outcome rows or per-arm summaries returned", "WAITING_FOR_EVIDENCE")
    data = payloads[0] if payloads else None
    if not isinstance(design, RandomizedDesign):
        return state.fail("METHOD_REFUSAL", f"design {design.design} has no P0 runner for assignment data", "UNSUPPORTED")
    if q.question_kind == "ESTIMATE_EFFECT":
        estimand, treat, ctrl = q.estimand, q.estimand.contrast.treatment, q.estimand.contrast.control
    else:
        arms = sorted(design.expected_allocation)
        ctrl = "control" if "control" in arms else arms[0]
        treat = next(a for a in arms if a != ctrl)
        estimand = None
    functional = estimand.functional if estimand is not None else "ITT"
    run_functional = functional if functional in ("ITT", "CACE", "risk_difference", "mean_difference") else "ITT"
    if data is not None:
        r = evaluate_randomized(design, data, treat, ctrl, functional=run_functional)
    else:
        arms = {}
        for arm, o in summaries.items():
            p = o.payload
            if p.mean is None or p.se is None:
                return state.fail("MISSING_EVIDENCE", f"arm {arm}: summary lacks a mean or a standard error", "WAITING_FOR_EVIDENCE")
            arms[arm] = ArmSummary(p.n, p.mean, p.se)
        adjusted = any("variance_adjusted" in m for o in summaries.values() for m in o.observation_mechanisms)
        assigned = [o for r_ in state.responses for o in r_.observations
                    if o.variable_id == "assignment_units" and o.payload.shape == "aggregate_stats"
                    and any(x.dimension_id == "arm" for x in o.segment)]
        counts = {next(x.value for x in o.segment if x.dimension_id == "arm"): o.payload.n for o in assigned} or None
        source = next((m.split(":", 1)[1] for o in assigned for m in o.observation_mechanisms
                       if m.startswith("assignment_count:")), None)
        r = evaluate_randomized_summary(design, arms, treat, ctrl, functional=run_functional,
                                        variance_adjusted_by_source=adjusted, assignment_counts=counts,
                                        assignment_count_source=source)
    s = replace(state, results=state.results + (("randomized_effect_v1", r),))
    if r.status == "REFUSED":
        cls = "STATISTICAL_INVALIDITY" if r.refusal_code in ("SRM_FAIL", "ASSIGNMENT_INCONSISTENT") else "METHOD_REFUSAL"
        s = s.log("REFUSED", f"randomized_effect_v1: {r.refusal_code}")
        s = replace(s, failures=s.failures + (Failure(cls, "EVALUATE", f"{r.refusal_code}: {r.refusal_reason}"),))
    else:
        s = s.log("PASS", f"randomized_effect_v1: {run_functional} {r.values['estimate']:+.4f} (SE {r.values['se']:.4f})")
    if estimand is not None:
        a = ia.assess(estimand, design.design, r, evidence_ids=state.evidence_ids or ("none",),
                      transport_assumptions=ctx.transport_assumptions)
        claim = ia.effect_claim(a, r, "units independent at the assignment unit") if r.status != "REFUSED" else None
        s = replace(s, assessments=s.assessments + (a,), claims=s.claims + ((claim,) if claim else ()))
        s = s.log("AUDIT", f"identification: {a.status}")
    return replace(s, stage="CHALLENGE")


def _evaluate_change(state: PlanState, ctx: TaskContext) -> PlanState:
    s = state
    for var in state.frame["variables"]:
        spec = ctx.monitors[var]
        pts = [(p.t, p.value) for r in state.responses for o in r.observations
               if o.variable_id == var and o.payload.shape == "time_series" for p in o.payload.points]
        pts += [(x.t, x.state if isinstance(x.state, (int, float)) else None) for r in state.responses
                for o in r.observations if o.variable_id == var and o.payload.shape == "state_trajectory" for x in o.payload.samples]
        if spec.baseline is not None:
            monitored = sorted((p for p in pts if p[0] > spec.baseline.train_end), key=lambda p: p[0])
            s = s.log("PLAN", f"{var}: {len(monitored)} point(s) after the frozen training window "
                      f"(ends {spec.baseline.train_end:%Y-%m-%d}); {len(pts) - len(monitored)} older point(s) not re-used")
            r = evaluate_baseline_shift(spec.baseline, monitored, alpha=spec.alpha, family_size=spec.family_size)
            s = replace(s, results=s.results + (("baseline_shift_v1", r),))
            if r.status == "REFUSED":
                s = s.log("REFUSED", f"baseline_shift_v1 on {var}: {r.refusal_code}")
                s = replace(s, failures=s.failures + (Failure("METHOD_REFUSAL", "EVALUATE", f"{var}: {r.refusal_code}"),))
            else:
                v = r.values
                s = s.log("PASS", f"baseline_shift_v1 on {var} (learned on {v['baseline']['n_train']} points): alarm "
                          f"{v['alarm']}" + (f" ({v['direction']}, at point {v['first_crossing_index']})" if v["alarm"] else ""))
            continue
        values = [v for _, v in sorted(pts, key=lambda p: p[0])]
        if any(v is None for v in values):
            s = s.gap(f"{var}: {sum(v is None for v in values)} missing point(s)", "repair or mark the missing buckets")
            s = replace(s, failures=s.failures + (Failure("DQ_FAULT", "EVALUATE", f"{var}: missing points"),))
            values = [v for v in values if v is not None]
        z = [(v - spec.null_mean) / spec.null_sd for v in values]
        r = evaluate_eprocess(z, null_spec=spec.null_spec, dependence=spec.dependence, alpha=spec.alpha,
                              family_size=spec.family_size)
        s = replace(s, results=s.results + (("savi_eprocess_v1", r),))
        if r.status == "REFUSED":
            s = s.log("REFUSED", f"savi_eprocess_v1 on {var}: {r.refusal_code}")
            s = replace(s, failures=s.failures + (Failure("METHOD_REFUSAL", "EVALUATE", f"{var}: {r.refusal_code}"),))
        else:
            v = r.values
            s = s.log("PASS", f"savi_eprocess_v1 on {var}: alarm {v['alarm']}"
                      + (f" ({v['direction']}, at point {v['first_crossing_index']})" if v["alarm"] else ""))
    return replace(s, stage="CHALLENGE")


def _evaluate_explain(state: PlanState, ctx: TaskContext) -> PlanState:
    """DQ first (an immature bucket is a data fault, not a domain explanation), then the exact mix/rate split."""
    from datetime import datetime
    f, s = state.frame, state
    cur0, cur1 = (datetime.fromisoformat(x) for x in f["current"])
    for var in f["variables"]:
        obs = [o for r in state.responses for o in r.observations if o.variable_id == var and o.payload.shape == "aggregate_stats"]
        dq = evaluate_dq([BucketQuality(o.record_key, o.dq.completeness, bool(o.clocks and o.clocks.available_at
                                                                              and o.clocks.available_at.quality == "KNOWN"))
                          for o in obs])
        s = replace(s, results=s.results + (("dq_observation_v1", dq),))
        bad = set(dq.values.get("domain_attribution_blocked", [])) if dq.status != "REFUSED" else set()
        if bad:
            s = replace(s, failures=s.failures + (Failure("DQ_FAULT", "EVALUATE", f"{var}: {len(bad)} bucket(s) not mature: "
                                                          f"{sorted(bad)[:3]}"),))
            s = s.log("DQ", f"{var}: {len(bad)} immature bucket(s) excluded from attribution")
        seg: dict[str, list[float]] = {}
        for o in obs:
            if o.record_key in bad or o.payload.numerator is None or o.payload.window is None:
                continue
            dim_id = ctx.dimensions[var].dimension_id
            name = next((x.value for x in o.segment if x.dimension_id == dim_id), None)
            if name is None:
                s = replace(s, failures=s.failures + (Failure("DQ_FAULT", "EVALUATE", f"{o.observation_id}: no "
                                                              f"{dim_id} segment on a strata row; excluded"),))
                continue
            period = 1 if o.payload.window.start >= cur0 else 0
            acc = seg.setdefault(name, [0.0, 0.0, 0.0, 0.0])
            acc[2 * period] += o.payload.numerator
            acc[2 * period + 1] += o.payload.denominator
        strata = [Stratum(k, v[0], v[1], v[2], v[3]) for k, v in sorted(seg.items())]
        k = evaluate_kitagawa(strata, ctx.dimensions.get(var))
        s = replace(s, results=s.results + (("kitagawa_v1", k),))
        if k.status == "REFUSED":
            s = s.log("REFUSED", f"kitagawa_v1 on {var}: {k.refusal_code}")
            s = replace(s, failures=s.failures + (Failure("METHOD_REFUSAL", "EVALUATE", f"{var}: {k.refusal_code}"),))
        else:
            v = k.values
            s = s.log("PASS", f"kitagawa_v1 on {var}: total {v['total_change']:+.4f} = mix {v['mix_component']:+.4f} + "
                      f"rate {v['rate_component']:+.4f} ({v['partition']})")
    return replace(s, stage="CHALLENGE")


def _evaluate_what_if(state: PlanState, ctx: TaskContext) -> PlanState:
    """Model predictions (P1). Recorded as results; never evidence (the shell writes them to model_outputs)."""
    f, s = state.frame, state
    history = {}
    for r in state.responses:
        for o in r.observations:
            if o.payload.shape == "time_series":
                history[o.variable_id] = [p.value for p in sorted(o.payload.points, key=lambda p: p.t) if p.value is not None]
    kind_scope = state.task.source_scope.environment_kind
    runs = [("forecast", ())] if not f["candidates"] else \
        [(label, (InterventionSpec(variable_id=v, kind=k, value=x),)) for label, v, k, x in f["candidates"]]
    for label, ivs in runs:
        p = rollout(ctx.mechanisms, f["target"], history, f["horizon"], ivs, n_samples=ctx.rollout_samples,
                    seed=ctx.rollout_seed, target_scope_kind=kind_scope, exogenous_policy=ctx.exogenous_policy,
                    regime_margin=ctx.regime_margin, constraints=f.get("constraints", ()))
        s = replace(s, results=s.results + ((f"counterfactual_rollout_v1:{label}", p),))
        if p.status != "PREDICTED":
            s = replace(s, failures=s.failures + (Failure(p.status if p.status in FAILURE_CLASSES else "UNSUPPORTED_MODEL",
                                                          "EVALUATE", f"{label}: {p.reasons[0]}"),))
            s = s.log("ABSTAIN", f"{label}: {p.status}: {p.reasons[0][:90]}")
        else:
            x = p.effect or p.baseline
            s = s.log("PASS", f"{label}: {'effect' if p.effect else 'forecast'} {x.mean:+.1f} [{x.lower:+.1f}, {x.upper:+.1f}]")
    return replace(s, stage="CHALLENGE")


def _evaluate_design(state: PlanState, ctx: TaskContext) -> PlanState:
    hyps = ctx.hypotheses or state.frame["hypotheses"]
    r = rank(list(ctx.query_options), list(hyps), frozenset())
    s = state
    for gid, why in r.evidence_gaps:
        s = s.gap(f"{gid} would discriminate but no provider can run it", why)
    s = replace(s, results=s.results + (("voi_heuristic_v1", r),))
    top = r.ranked[0] if r.ranked else None
    return replace(s.log("PASS", f"{r.label}: top {top[0]} ({top[1]:.3f})" if top else "no executable option"),
                   stage="CHALLENGE")


def _evaluate_hypothesis(state: PlanState, ctx: TaskContext) -> PlanState:
    f = state.frame
    if ctx.view is None:
        return state.fail("MISSING_EVIDENCE", "no pinned graph view supplied", "WAITING_FOR_EVIDENCE")
    rels = [v.relationship(f["source"], f["target"]) for v in (ctx.view, ctx.temporal_view) if v is not None]
    active = [f"{e.kind}" + (f" (lag {e.lag.min_s:g}–{e.lag.max_s:g} s)" if getattr(e, "lag", None) else "")
              for r in rels for e in r.active]
    status = "KNOWN" if active else "UNKNOWN"
    unknown = ctx.view.unknowns_relevant_to(f["source"], f["target"])
    s = replace(state, results=state.results + (("graph_audit", {"active": active, "status": status,
                                                                  "relevant_unknowns": unknown}),))
    for imp in state.task.question.hypothesis.observable_implications:
        s = s.gap(f"observable implication not yet checked: {imp}", f"measure: {imp}")
    return replace(s.log("PASS", f"graph v{ctx.graph_version}: {f['source']}→{f['target']} {status}"
                         + (f" via {active}" if active else "") + f"; {len(unknown)} relevant unknown pair(s)"),
                   stage="CHALLENGE")


# ── CHALLENGE ────────────────────────────────────────────────────────────


def challenge(state: PlanState, ctx: TaskContext) -> PlanState:
    """Rivals, untestable assumptions, observation artifacts, external validity. Seeded from method audits and the
    graph, never from benchmark truth or untrusted labels."""
    kind = state.frame["question_kind"]
    rivals: list[str] = []
    for mid, r in state.results:
        if isinstance(r, MethodResult):
            rivals += [f"untestable ({mid}): {u}" for u in r.assumptions_untestable]
            rivals += [f"limitation ({mid}): {l}" for l in r.limitations]
    for a in state.assessments:
        rivals += [f"identification: {x}" for x in a.assumptions_untestable if x not in " ".join(rivals)]
    if kind == "EXPLAIN_OBSERVATION":
        rivals += ["a mix shift is arithmetic, not a cause: why the mix moved is a separate question",
                   "observation artifact: a definition or logging change in one segment can look like a rate change"]
    if kind == "DETECT_CHANGE":
        rivals += ["observation artifact: a definition, logging or pipeline change can move the series without a real change",
                   "an alarm is evidence against the declared null, not a cause: explaining it is a separate question"]
    if kind in ("ESTIMATE_EFFECT", "ASSESS_STUDY"):
        rivals.append("external validity: the effect holds for the study's population, window and scope only")
    if kind in WHAT_IF:
        rivals += ["a model prediction is not evidence: it cannot update alarms, graph certification or claims",
                   "validated one step ahead on held-out data; multi-step error can be larger",
                   "mechanisms assume their parents are not confounded with their noise"]
    if kind == "ASSESS_HYPOTHESIS":
        rivals.append("none of the proposed explanations: absence of an edge in the graph is UNKNOWN, not 'no effect'")
    rivals = list(dict.fromkeys(rivals))
    return replace(state.log("PASS", f"{len(rivals)} rival explanation(s)/assumption(s) recorded"),
                   challenges=tuple(rivals), stage="REPORT")


# ── REPORT ───────────────────────────────────────────────────────────────


def report(state: PlanState) -> PlanState:
    """Turn results into findings and a TaskOutcome. Statuses are distinct: an alarm, no alarm, an abstention, a
    refusal and a missing source never collapse into one 'no change'."""
    s, kind = state, state.frame.get("question_kind")
    findings = list(s.findings)
    if s.outcome is None:
        for mid, r in s.results:
            if isinstance(r, MethodResult) and r.status == "REFUSED":
                findings.append(_finding(s, "INCONCLUSIVE", f"{mid} refused: {r.refusal_code}: {r.refusal_reason}"))
        for a in s.assessments:
            if a.status in ("CONDITIONAL_IDENTIFICATION", "PARTIAL_IDENTIFICATION"):
                c = next((c for c in s.claims if c.assessment_id == a.assessment_id), None)
                if c is not None:
                    lo, hi = c.uncertainty.interval
                    findings.append(_finding(s, "CAUSAL_EFFECT_CONDITIONAL",
                                             f"{c.estimand.functional} of {c.estimand.contrast.treatment} vs "
                                             f"{c.estimand.contrast.control} on {c.estimand.outcome}: {c.estimate:+.4f} "
                                             f"[{lo:+.4f}, {hi:+.4f}] ({a.status})", refs=(c.claim_id, a.assessment_id),
                                             limitations=a.assumptions_untestable))
            elif a.status not in ("INVALID_DESIGN", "INSUFFICIENT_EVIDENCE"):
                why = a.assumptions_checked[-1] if a.assumptions_checked else a.status
                findings.append(_finding(s, "INCONCLUSIVE", f"abstain: {a.status}: {why}", refs=(a.assessment_id,)))
        if kind == "ASSESS_STUDY":
            for mid, r in s.results:
                if isinstance(r, MethodResult) and r.status != "REFUSED":
                    v = r.values
                    findings.append(_finding(s, "DESCRIPTIVE", f"study audit: SRM p = {v['srm_p_value']:.3g}; ITT "
                                             f"{v['estimate']:+.4f} [{v['ci'][0]:+.4f}, {v['ci'][1]:+.4f}]; "
                                             f"{len(r.limitations)} limitation(s)", limitations=r.limitations))
        if kind == "DETECT_CHANGE":
            for mid, r in s.results:
                if isinstance(r, MethodResult) and r.status != "REFUSED":
                    v = r.values
                    text = (f"alarm: evidence against the null ({v['direction']}), first at point "
                            f"{v['first_crossing_index']}; peak log-wealth {v['peak_log_wealth']:.2f} ≥ {v['threshold']:.2f}"
                            if v["alarm"] else
                            f"no alarm: peak log-wealth {v['peak_log_wealth']:.2f} < {v['threshold']:.2f} (failure to reject, "
                            "not evidence of no change)")
                    if mid == "baseline_shift_v1":
                        text += f" (against a baseline learned on {v['baseline']['n_train']} older points, frozen)"
                    findings.append(_finding(s, "DESCRIPTIVE", text, limitations=r.assumptions_untestable))
        if kind == "EXPLAIN_OBSERVATION":
            for mid, r in s.results:
                if mid == "dq_observation_v1" and r.status != "REFUSED" and r.values.get("domain_attribution_blocked"):
                    blocked = r.values["domain_attribution_blocked"]
                    findings.append(_finding(s, "DQ_FAILURE", f"{len(blocked)} bucket(s) not mature or not assessed "
                                             f"({', '.join(sorted(blocked)[:3])}…): excluded; a change there is not attributed to "
                                             "the domain"))
                if mid == "kitagawa_v1" and r.status != "REFUSED":
                    v = r.values
                    top = sorted(v["segments"].items(), key=lambda kv: kv[1]["mix"] + kv[1]["rate"])[:2]
                    findings.append(_finding(s, "MATHEMATICAL_IDENTITY",
                                             f"total {v['total_change']:+.4f} = mix {v['mix_component']:+.4f} + rate "
                                             f"{v['rate_component']:+.4f}; largest contributors: "
                                             + ", ".join(f"{k} (mix {x['mix']:+.4f}, rate {x['rate']:+.4f})" for k, x in top)
                                             + ("; every segment's rate rose" if v["every_segment_rate_rose"] else ""),
                                             limitations=r.limitations))
        if kind in WHAT_IF:
            preds = [(mid.split(":", 1)[1], r) for mid, r in s.results if isinstance(r, CounterfactualPrediction)]
            for label, p in preds:
                if p.status != "PREDICTED":
                    findings.append(_finding(s, "INCONCLUSIVE", f"{label}: abstain {p.status}: {'; '.join(p.reasons)[:220]}"))
                elif p.effect is not None:
                    sens = "; ".join(f"{k} {v.mean:+.1f}" for k, v in p.reach_sensitivity.items())
                    if p.constraint_risk:
                        sens += "; risk: " + ", ".join(f"P(not {c}) = {r:.0%}" for c, r in p.constraint_risk.items())
                    findings.append(_finding(s, "MODEL_PREDICTION",
                                             f"{label}: predicted change in {p.target} over {p.horizon_steps} step(s) vs holding "
                                             f"inputs: {p.effect.mean:+.1f} [{p.effect.lower:+.1f}, {p.effect.upper:+.1f}] "
                                             f"({p.level:.0%} predictive); reach sensitivity: {sens}",
                                             refs=(p.prediction_id,), limitations=p.assumptions))
                else:
                    findings.append(_finding(s, "MODEL_PREDICTION",
                                             f"forecast of {p.target} summed over {p.horizon_steps} step(s): {p.baseline.mean:.1f} "
                                             f"[{p.baseline.lower:.1f}, {p.baseline.upper:.1f}] ({p.level:.0%} predictive)",
                                             refs=(p.prediction_id,), limitations=p.assumptions))
            tol = s.frame.get("risk_tolerance", 0.05)
            ok, failed = [], []
            for label, p in preds:
                if p.status == "PREDICTED" and p.effect is not None:
                    (failed if any(r > tol for r in p.constraint_risk.values()) else ok).append((label, p))
            if failed:
                findings.append(_finding(s, "MODEL_PREDICTION", "fails a risk constraint (violated in more than "
                                         f"{tol:.0%} of simulated paths), not ranked: " + ", ".join(l for l, _ in failed)))
            if len(ok) >= 2:
                sign = 1 if f_dir(s) == "maximize" else -1
                ok.sort(key=lambda lp: -sign * lp[1].effect.mean)
                (a, pa), (b, pb) = ok[0], ok[1]
                overlap = pa.effect.lower <= pb.effect.upper and pb.effect.lower <= pa.effect.upper
                findings.append(_finding(s, "MODEL_PREDICTION",
                                         f"ranking by predicted mean ({f_dir(s)}): {', '.join(l for l, _ in ok)}"
                                         + ("; the top two intervals overlap: not distinguishable by this model" if overlap else "")))
        if kind == "DESIGN_EVIDENCE":
            r = s.results[0][1]
            for oid, score, notes in r.ranked[:3]:
                findings.append(_finding(s, "ACTION_RECOMMENDATION", f"query {oid}: proxy score {score:.3f}", limitations=notes))
        if kind == "ASSESS_HYPOTHESIS":
            g = s.results[0][1]
            findings.append(_finding(s, "STRUCTURAL_CAUSAL_HYPOTHESIS",
                                     f"{s.frame['statement']}: graph says {g['status']}"
                                     + (f" ({', '.join(g['active'])})" if g["active"] else "")
                                     + "; not tested by data in this task"))
        refused = any(isinstance(r, MethodResult) and r.status == "REFUSED" for _, r in s.results)
        abstained = any(isinstance(r, CounterfactualPrediction) and r.status != "PREDICTED" for _, r in s.results)
        only_inconclusive = not any(f_.claim_kind != "INCONCLUSIVE" for f_ in findings)
        outcome = "INCONCLUSIVE" if (refused or abstained) and only_inconclusive else "COMPLETED"
        s = replace(s, outcome=outcome)
    if s.failures and not findings:
        f0 = s.failures[0]
        findings.append(_finding(s, "INCONCLUSIVE", f"{f0.cls} at {f0.stage}: {f0.detail}"))
    return replace(s.log("DONE", f"outcome {s.outcome}; {len(findings)} finding(s); {len(s.gaps)} gap(s)"),
                   findings=tuple(findings), stage="REPORT", done=True)


def f_dir(state: PlanState) -> str:
    return state.frame.get("direction", "maximize")


def payload_digest(state: PlanState) -> str:
    blob = json.dumps({"stage": state.stage, "trace": state.trace, "outcome": state.outcome,
                       "findings": [x.model_dump(mode="json") for x in state.findings]}, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()


def is_finite(x) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)
