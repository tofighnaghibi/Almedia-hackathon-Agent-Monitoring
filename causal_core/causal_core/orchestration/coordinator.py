"""TaskCoordinator: the imperative shell around the pure planner (v3.2 §4.1, §5.2, §7; plan §G tasks 2, 3, 7; CP-025).

* Accepts any trigger (explicit question, schedule, monitor, study completion); none is required and the monitor
  is just one optional producer.
* Leases a durable job, checkpoints after every stage, and runs ACQUIRE through the budgeted, retry-safe executor
  (charge before dispatch, CP-013) with a per-request deadline: a hung provider becomes a typed TIMEOUT.
* Progressive delivery: a provisional T0 revision as soon as inputs are audited, then the final revision. Both go
  through the policy gate and the transactional outbox (CP-014).
"""
from __future__ import annotations

import concurrent.futures as cf
import hashlib
import json
import time
from dataclasses import dataclass, field, replace

from ..contracts.capabilities import CapabilityResponse
from ..contracts.tasks import Pins, ReasoningTask, TaskRevision
from ..storage.ledger import Ledger
from . import planner as P
from .outbox import OutboxDispatcher
from .policy import PolicyDecision, serialize, vet
from .query_executor import BudgetedQueryExecutor, BudgetExhausted, cache_key
from .scheduler import PriorityScheduler


@dataclass
class RunResult:
    state: P.PlanState
    reports: list[dict] = field(default_factory=list)
    decisions: list[PolicyDecision] = field(default_factory=list)
    timings_ms: dict[str, float] = field(default_factory=dict)
    job_id: str = ""
    delivery: str = ""


class TaskCoordinator:
    def __init__(self, ledger: Ledger, port, sink, *, context: P.TaskContext = P.TaskContext(),
                 clock=time.monotonic, worker_id: str = "coordinator-1"):
        self.ledger, self.port, self.sink, self.ctx = ledger, port, sink, context
        self.scheduler = PriorityScheduler(ledger, clock)
        self.executor = BudgetedQueryExecutor(ledger, port) if port is not None else None
        self.outbox = OutboxDispatcher(ledger, sink) if sink is not None else None
        self.worker_id = worker_id
        self._pool = cf.ThreadPoolExecutor(max_workers=4, thread_name_prefix="evidence")

    # ── public ───────────────────────────────────────────────────────────
    def content_key(self, task: ReasoningTask) -> str:
        """What makes two runs 'the same work' (CP-025 fix): the question, data cut, scope, budget and graph pin.
        The task id alone is not enough: re-asking under the same id with a later data cut must recompute."""
        blob = json.dumps({"question": task.question.model_dump(mode="json"), "data_cut": task.data_cut.model_dump(mode="json"),
                           "scope": task.source_scope.model_dump(mode="json"), "budget": task.budget.model_dump(mode="json"),
                           "graph_version": self.ctx.graph_version}, sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()

    def run(self, task: ReasoningTask, *, requested_action: str | None = None) -> RunResult:
        t_start = time.perf_counter()
        content = self.content_key(task)
        job_id = "job:" + hashlib.sha256(f"{task.task_id}|{content}|T1".encode()).hexdigest()[:16]
        prior = self.ledger.read("SELECT state FROM jobs WHERE job_id=?", (job_id,))
        if prior and prior[0][0] == "COMPLETED":
            return self._replay(task, job_id)                # same work again: recorded result, nothing republished
        latest = self.ledger.read("SELECT COALESCE(MAX(revision), 0) FROM task_revisions WHERE task_id=?", (task.task_id,))[0][0]
        if not prior:
            self.scheduler.enqueue(job_id, task.task_id, latest + 1, "T1", task.priority,
                                   self.scheduler.clock() + task.budget.max_wall_ms / 1000,
                                   task.budget.max_queries, self.ctx.graph_version)
        job = self.scheduler.lease_job(job_id, self.worker_id)
        if job is None or job["job_id"] != job_id:
            raise RuntimeError(f"could not lease {job_id}")
        self.scheduler.start(job_id, self.worker_id)
        out = RunResult(P.PlanState(task), job_id=job_id)
        manifest = self.port.manifest() if self.port is not None else None
        s = out.state
        revision = latest                                    # a re-asked task continues its revision history

        def stage(fn, *args):
            nonlocal s
            name = s.stage
            t0 = time.perf_counter()
            s = fn(s, *args)
            out.timings_ms[name] = round((time.perf_counter() - t0) * 1000, 3)
            self.scheduler.checkpoint(job_id, self.worker_id, name, {"digest": P.payload_digest(s), "stage": name},
                                      queries_spent=len(s.responses))

        stage(P.frame, self.ctx)
        if not s.done:
            stage(P.audit_inputs, self.ctx, manifest)
        if not s.done:                                   # T0: inputs accepted, provisional revision
            revision += 1
            self._publish(out, s, revision, provisional=True, requested_action=None)
            out.timings_ms["T0_published"] = round((time.perf_counter() - t_start) * 1000, 3)
            stage(P.select_capabilities, self.ctx, manifest)
            for m, st, reasons in s.applicability:
                self.ledger.record_method_decision(task.task_id, 1, m, "-", st, list(reasons))
        if not s.done:
            stage(P.plan_requests, self.ctx, manifest)
        if not s.done and s.requests:
            responses = []
            for req in s.requests:
                responses.append(self._query(job_id, task, req))
                if responses[-1].status == "BUDGET_EXCEEDED":
                    break
            s = P.ingest_responses(s, tuple(responses))
            self.scheduler.checkpoint(job_id, self.worker_id, "ACQUIRE", {"digest": P.payload_digest(s), "stage": "ACQUIRE"},
                                      queries_spent=len(responses))
        elif not s.done:
            s = replace(s, stage="EVALUATE")
        if not s.done:
            stage(P.evaluate, self.ctx)
        if not s.done:
            stage(P.challenge, self.ctx)
        s = P.report(s)
        for mid, r in s.results:                 # P1: model predictions go to model_outputs, never to evidence (I11)
            if mid.startswith("counterfactual_rollout_v1"):
                # ids are content hashes of the inputs, so a repeat of the same prediction is recorded once
                if not self.ledger.read("SELECT 1 FROM model_outputs WHERE prediction_id=?", (r.prediction_id,)):
                    self.ledger.append_model_output(r.prediction_id, "counterfactual_rollout_v1", "1", r.model_dump(mode="json"))
        revision += 1
        self._publish(out, s, revision, provisional=False, requested_action=requested_action)
        out.timings_ms["final_published"] = round((time.perf_counter() - t_start) * 1000, 3)
        self.scheduler.checkpoint(job_id, self.worker_id, "REPORT", {"stage": "REPORT", "content_key": content,
                                                                     "revisions": [r["revision"] for r in out.reports]},
                                  queries_spent=len(s.responses))
        self.scheduler.complete(job_id, self.worker_id)
        out.state = s
        out.delivery = self.outbox.delivery_guarantee if self.outbox else "NONE"
        return out

    def _replay(self, task: ReasoningTask, job_id: str) -> RunResult:
        """Return the revisions this exact work produced (from the job's REPORT checkpoint), not merely the latest."""
        cp = self.scheduler.last_checkpoint(job_id)
        revs = cp[2].get("revisions", []) if cp and cp[1] == "REPORT" else []
        if not revs:
            raise RuntimeError(f"{job_id} is COMPLETED but has no REPORT checkpoint to replay")
        row = self.ledger.read("SELECT payload_json FROM task_revisions WHERE task_id=? AND revision=?",
                               (task.task_id, max(revs)))
        last = TaskRevision.model_validate_json(row[0][0])
        state = replace(P.PlanState(task), outcome=last.outcome, findings=last.findings, done=True, stage="REPORT",
                        trace=(("REPORT", "REPLAY", f"same work already completed: revision(s) {revs} returned"),))
        return RunResult(state, job_id=job_id, delivery="REPLAYED")

    @staticmethod
    def _versions(s: P.PlanState) -> dict:
        """Method ids (without per-candidate labels) and, for predictions, every mechanism version used."""
        out = {}
        for m, r in s.results:
            base = m.split(":", 1)[0]
            out[base] = str(getattr(r, "method_version", "1" if base == "counterfactual_rollout_v1" else "-"))
            for mech, v in getattr(r, "mechanism_versions", {}).items():
                out[mech] = f"v{v}"
        return out

    # ── internals ────────────────────────────────────────────────────────
    def _query(self, job_id: str, task: ReasoningTask, req) -> CapabilityResponse:
        key = cache_key(scope_id=task.source_scope.environment_id, data_cut=task.data_cut.as_of.isoformat(),
                        request_fingerprint=BudgetedQueryExecutor.fingerprint(req), graph_version=self.ctx.graph_version,
                        definition_versions={})
        fp = BudgetedQueryExecutor.fingerprint(req)
        fut = self._pool.submit(self.executor.execute, job_id, req, cache=key)
        try:
            return fut.result(timeout=req.deadline_ms / 1000)
        except BudgetExhausted as exc:
            return CapabilityResponse(schema_version="2.0", request_id=req.request_id, request_fingerprint=fp,
                                      status="BUDGET_EXCEEDED", reason=str(exc))
        except cf.TimeoutError:
            return CapabilityResponse(schema_version="2.0", request_id=req.request_id, request_fingerprint=fp,
                                      status="TIMEOUT", reason=f"no response within the {req.deadline_ms} ms deadline")
        except Exception as exc:                                    # provider bug: typed, never swallowed
            return CapabilityResponse(schema_version="2.0", request_id=req.request_id, request_fingerprint=fp,
                                      status="FAILED", reason=f"{type(exc).__name__}: {exc}")

    def _publish(self, out: RunResult, s: P.PlanState, revision: int, *, provisional: bool, requested_action) -> None:
        if provisional:
            note = P._finding(s, "DESCRIPTIVE", f"accepted {s.task.question.question_kind} ({s.task.trigger}); inputs "
                              "audited; investigation running")
            s = replace(s, findings=(note,), outcome="READY")
        decision = vet(s, provisional=provisional, requested_action=requested_action)
        pins = {"graph_version": self.ctx.graph_version, "data_cut": s.task.data_cut.as_of.isoformat(),
                "method_versions": self._versions(s)}
        body = serialize(s, decision, revision=revision, provisional=provisional, pins=pins)
        key = Ledger.revision_key(s.task.task_id, revision, body)
        rev = TaskRevision(schema_version="2.0", task_id=s.task.task_id, revision=revision,
                           supersedes_revision=revision - 1 if revision > 1 else None,
                           outcome=s.outcome or "READY", findings=s.findings,
                           evidence_gap_ids=tuple(g.gap_id for g in s.gaps),
                           pins=Pins(graph_version=self.ctx.graph_version, data_cut=s.task.data_cut.as_of,
                                     method_versions={k: str(v) for k, v in pins["method_versions"].items()}),
                           idempotency_key=key)
        self.ledger.commit_revision(rev, rev.model_dump(mode="json"))
        if self.outbox is not None:
            self.outbox.drain()
        out.reports.append(body)
        out.decisions.append(decision)
