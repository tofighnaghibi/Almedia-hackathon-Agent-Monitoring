"""Stand-in causal core speaking only Contracts/ (schema 2.0). Imports nothing from the emulator,
the datasets or the marketplace: only Contracts and the standard library (import-boundary test).

ReasoningTask (DETECT_CHANGE / EXPLAIN_OBSERVATION) + an EvidenceAccessPort -> TaskRevision
with Findings and EvidenceGaps. Domain-agnostic: it reasons over declared ratio metrics
(formula_ast div of two variables): with identity_tolerance -> an accounting identity; without -> a
stage-to-stage ratio. It knows no marketplace words.
"""
from __future__ import annotations

import hashlib
import json
import math
from statistics import median

from causal_core.contracts.capabilities import GetSeries
from causal_core.contracts.common import SCHEMA_VERSION
from causal_core.contracts.evidence import EvidenceGap
from causal_core.contracts.tasks import Finding, Pins, ReasoningTask, TaskRevision
from causal_core.contracts.variables import FormulaBinOp, FormulaRef, MetricDefinition

Z_FLAG, Z_QUIET = 3.0, 2.0


def _sha(o) -> str:
    return hashlib.sha256(json.dumps(o, sort_keys=True, default=str).encode()).hexdigest()


class StandinCore:
    method_version = "standin-core-1"

    def __init__(self, port):
        self.port = port
        self.queries = 0

    # ---------------------------------------------------------------- evidence access
    def _series(self, task: ReasoningTask, vid: str):
        if self.queries >= task.budget.max_queries:
            return None
        self.queries += 1
        q = task.question
        win = getattr(q, "context_window", None)
        r = self.port.query(GetSeries(schema_version=SCHEMA_VERSION, request_id=f"{task.task_id}:{self.queries}", variables=(vid,),
                                      scope=task.source_scope, data_cut=task.data_cut.as_of, window=win, purpose=f"task {task.task_id}",
                                      request="get_series", grain="1w", max_buckets=task.budget.max_buckets))
        if r.status != "OK":
            return None
        o = r.observations[0]
        return o.observation_id, {p.t: p.value for p in o.payload.points if p.value is not None}

    @staticmethod
    def _z(series: dict, floor=None, count: bool = False, k: int = 3) -> dict:
        """Robust z of each period against a LOCAL baseline (median of up to k periods either side, excluding
        the period itself), so trends and slow seasonality are not mistaken for changes (S10 v2, preregistered).
        Scale = 1.4826 x median |residual|, floored by counting noise: sqrt(baseline) for counts, `floor` for ratios."""
        ts = sorted(series)
        vals = [series[t] for t in ts]
        base = {}
        for i, t in enumerate(ts):
            near = vals[max(0, i - k):i] + vals[i + 1:i + 1 + k]
            base[t] = median(near) if len(near) >= 3 else median(vals)
        resid = {t: series[t] - base[t] for t in ts}
        mad = 1.4826 * median([abs(r) for r in resid.values()])
        return {t: resid[t] / max(mad, floor(t, series[t]) if floor else 0.0, math.sqrt(max(base[t], 0.0)) if count else 0.0, 1e-9)
                for t in ts}

    def _ratios_over(self, vid: str, defs) -> list[MetricDefinition]:
        """Declared ratio metrics that use `vid` as numerator or denominator."""
        out = []
        for d in defs:
            f = getattr(d, "formula_ast", None)
            if isinstance(f, FormulaBinOp) and f.op == "div" and isinstance(f.left, FormulaRef) and isinstance(f.right, FormulaRef) \
                    and vid in (f.left.variable_id, f.right.variable_id):
                out.append(d)
        return out

    # ---------------------------------------------------------------- task
    def run(self, task: ReasoningTask, revision: int = 1) -> tuple[TaskRevision, tuple[EvidenceGap, ...]]:
        self.queries = 0
        defs = self.port.definitions()
        findings, gaps, cited = [], [], []
        outcome = "COMPLETED"
        for vid in task.question.variables:
            s = self._series(task, vid)
            if s is None:
                findings.append(Finding(finding_id=f"{task.task_id}:{vid}:nodata", claim_kind="INCONCLUSIVE",
                                        statement=f"{vid}: no series available within budget", limitations=("not served or budget spent",)))
                outcome = "INCONCLUSIVE"
                continue
            oid, series = s
            cited.append(oid)
            is_count = {d.variable_id: getattr(d, "aggregation_kind", None) == "count" for d in defs}
            z = self._z(series, count=is_count.get(vid, False))
            flagged = sorted(t for t, v in z.items() if abs(v) >= Z_FLAG)
            if not flagged:
                findings.append(Finding(finding_id=f"{task.task_id}:{vid}:nochange", claim_kind="DESCRIPTIVE",
                                        statement=f"{vid}: no period beyond the robust threshold", support_refs=(oid,)))
                continue
            periods = ", ".join(t.date().isoformat() for t in flagged)
            findings.append(Finding(finding_id=f"{task.task_id}:{vid}:change", claim_kind="DESCRIPTIVE",
                                    statement=f"{vid} changed in period(s) {periods} (robust z {', '.join(f'{z[t]:+.1f}' for t in flagged)})",
                                    support_refs=(oid,), limitations=("baseline = median of the window; no seasonality model",)))
            if task.question.question_kind != "EXPLAIN_OBSERVATION":
                continue
            ratios = self._ratios_over(vid, defs)
            identities = [d for d in ratios if d.identity_tolerance is not None]
            stages = [d for d in ratios if d.identity_tolerance is None]
            if not identities and not stages:
                gaps.append(EvidenceGap(schema_version="2.0", gap_id=f"{task.task_id}:{vid}:gap", task_id=task.task_id,
                                        missing=f"no independent measurement or declared ratio involving {vid}",
                                        recommended_measurements=(f"independent_measurement:{vid}",), blocks_claim="DQ_FAILURE",
                                        executable_now=False))
                findings.append(Finding(finding_id=f"{task.task_id}:{vid}:undetermined", claim_kind="INCONCLUSIVE",
                                        statement=f"cannot tell whether the change in {vid} is real or a measurement problem",
                                        support_refs=(oid,), limitations=("no identity partner served",)))
                outcome = "WAITING_FOR_EVIDENCE"
                continue
            for d in identities:
                # the identity's other side: compare its LEVEL (lag-robust), not the weekly ratio
                a_, b_ = d.formula_ast.left.variable_id, d.formula_ast.right.variable_id
                partner = b_ if a_ == vid else a_
                rs = self._series(task, partner)
                if rs is None:
                    continue
                rid, rser = rs
                cited.append(rid)
                pz = self._z(rser, count=is_count.get(partner, False))
                for t in flagged:                                   # one verdict per flagged period
                    day = t.date().isoformat()
                    zt = pz.get(t)
                    if zt is None:
                        continue
                    if abs(zt) < Z_QUIET:
                        findings.append(Finding(finding_id=f"{task.task_id}:{vid}:{d.variable_id}:{day}:dq", claim_kind="DQ_FAILURE",
                                                statement=f"{day}: {vid} changed but {partner}, its partner in identity {d.variable_id}, did not: measurement suspected",
                                                support_refs=(oid, rid), limitations=("identity checked weekly",)))
                    elif abs(zt) >= Z_FLAG and (zt > 0) == (z[t] > 0):
                        findings.append(Finding(finding_id=f"{task.task_id}:{vid}:{d.variable_id}:{day}:real", claim_kind="ASSOCIATION",
                                                statement=f"{day}: {vid} and {partner} (identity {d.variable_id}) moved together: the change is in the measured quantity, not its measurement",
                                                support_refs=(oid, rid)))
                    else:
                        findings.append(Finding(finding_id=f"{task.task_id}:{vid}:{d.variable_id}:{day}:mixed", claim_kind="INCONCLUSIVE",
                                                statement=f"{day}: identity {d.variable_id} neither clearly held nor broke", support_refs=(oid, rid)))
            if stages:
                findings.append(self._localise(task, vid, flagged, stages, defs, cited))
        rev = TaskRevision(schema_version="2.0", task_id=task.task_id, revision=revision, supersedes_revision=revision - 1 if revision > 1 else None,
                           outcome=outcome, findings=tuple(findings), evidence_gap_ids=tuple(g.gap_id for g in gaps),
                           pins=Pins(graph_version=0, data_cut=task.data_cut.as_of, method_versions={"standin": self.method_version}),
                           idempotency_key=_sha([task.model_dump(mode="json"), revision, [f.statement for f in findings]]))
        return rev, tuple(gaps)

    @staticmethod
    def _is_count(vid, defs) -> bool:
        return any(d.variable_id == vid and getattr(d, "aggregation_kind", None) == "count" for d in defs)

    def _localise(self, task, vid, flagged, stages, defs, cited) -> Finding:
        """Walk the chain of stage ratios back from vid; report the one stage whose ratio broke (others quiet)."""
        by_num = {d.formula_ast.left.variable_id: d for d in defs
                  if isinstance(getattr(d, "formula_ast", None), FormulaBinOp) and d.identity_tolerance is None}
        chain, cur = [], vid
        while cur in by_num and len(chain) < 10:
            d = by_num[cur]
            chain.append(d)
            cur = d.formula_ast.right.variable_id
        root = cur
        levels = {}
        for x in [vid] + [d.formula_ast.right.variable_id for d in chain]:
            s = self._series(task, x)
            if s is None:
                return Finding(finding_id=f"{task.task_id}:{vid}:stage:budget", claim_kind="INCONCLUSIVE",
                               statement="stage walk stopped: budget or data", limitations=("max_queries",))
            cited.append(s[0])
            levels[x] = s[1]
        verdicts = []
        for t in flagged:
            broken, quiet = [], []
            for d in chain:
                a, b = d.formula_ast.left.variable_id, d.formula_ast.right.variable_id
                common = [w for w in levels[a] if w in levels[b] and levels[b][w]]
                r = {w: levels[a][w] / levels[b][w] for w in common}
                floor = lambda w, v, a=a, b=b: v * (1 / max(levels[a][w], 1) + 1 / max(levels[b][w], 1)) ** 0.5   # counting noise
                zz = self._z(r, floor).get(t, 0.0)
                (broken if abs(zz) >= Z_FLAG else quiet if abs(zz) < Z_QUIET else []).append(d.variable_id)
            root_z = self._z(levels[root], count=self._is_count(root, defs)).get(t, 0.0)
            if len(broken) == 1 and len(quiet) == len(chain) - 1:
                verdicts.append(f"stage ratio {broken[0]}")
            elif not broken and len(quiet) == len(chain) and abs(root_z) >= Z_FLAG:
                verdicts.append(f"upstream of {root}")
            else:
                verdicts.append(None)
        if verdicts and verdicts[0] and all(v == verdicts[0] for v in verdicts):
            return Finding(finding_id=f"{task.task_id}:{vid}:stage", claim_kind="ASSOCIATION",
                           statement=f"the change in {vid} is located at: {verdicts[0]}",
                           support_refs=tuple(cited[-(len(chain) + 1):]), limitations=("location, not cause",))
        return Finding(finding_id=f"{task.task_id}:{vid}:stage:mixed", claim_kind="INCONCLUSIVE",
                       statement=f"stage walk for {vid}: mixed evidence ({verdicts})", support_refs=tuple(cited[-1:]))
