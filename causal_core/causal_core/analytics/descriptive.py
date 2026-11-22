"""Data quality, seasonal screening, association and metric identity (v3.2 §4.4).

dq_observation_v1   unassessed quality is UNASSESSED, never PASS; immature windows block domain attribution.
seasonal_screen_v1  same-phase median/MAD robust z. An anomaly *candidate*, not a cause. Refuses short history.
association_v1      OLS slope + Pearson r with SEs. ASSOCIATION only, never causal (§4.4, I07).
metric_identity_v1  wraps semantics.verify_identity (Phase 2). MATHEMATICAL_IDENTITY, no cause.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats

from ..semantics.metrics import verify_identity
from .registry import CapabilitySpec, MethodResult, refused

DQ = CapabilitySpec(
    id="dq_observation_v1", version="1", question_kinds=("DETECT_CHANGE", "EXPLAIN_OBSERVATION", "ESTIMATE_EFFECT", "ASSESS_STUDY",
                                                         "ASSESS_HYPOTHESIS"),
    input_shapes=("scalar", "aggregate_stats", "time_series", "panel", "event_batch", "assignment_outcomes", "state_trajectory",
                  "categorical"),
    output_kind="DQ_FAILURE", estimand_families=None, required_metadata=(), assumption_checks=("completeness", "maturity", "availability"),
    dependence_handling="not inferential", max_work_units=10_000_000, refusal_codes=("NO_OBSERVATIONS",),
    honest_conclusion='data-quality diagnosis; unassessed bias blocks any domain attribution')
SEASONAL = CapabilitySpec(
    id="seasonal_screen_v1", version="1", question_kinds=("DETECT_CHANGE",), input_shapes=("time_series",), output_kind="DESCRIPTIVE",
    estimand_families=None, required_metadata=("season_period",), assumption_checks=("history_cycles", "missingness"),
    dependence_handling="robust per-phase baseline", max_work_units=1_000_000, refusal_codes=("INSUFFICIENT_HISTORY",),
    honest_conclusion='anomaly candidate, not causal proof')
ASSOCIATION = CapabilitySpec(
    id="association_v1", version="1", question_kinds=("EXPLAIN_OBSERVATION", "ASSESS_HYPOTHESIS", "ESTIMATE_EFFECT"),
    input_shapes=("panel", "time_series", "scalar"), output_kind="ASSOCIATION", estimand_families=None,
    required_metadata=("pairing", "dependence"), assumption_checks=("alignment", "dependence_declared"),
    dependence_handling="refuses undeclared dependence", max_work_units=10_000_000,
    refusal_codes=("MISALIGNED", "DEPENDENCE_UNDECLARED", "TOO_FEW_PAIRS", "NO_VARIATION"),
    honest_conclusion='association with uncertainty; never causal by itself')
IDENTITY = CapabilitySpec(
    id="metric_identity_v1", version="1", question_kinds=("EXPLAIN_OBSERVATION", "DETECT_CHANGE"),
    input_shapes=("aggregate_stats", "scalar", "time_series"), output_kind="MATHEMATICAL_IDENTITY", estimand_families=None,
    required_metadata=("formula",), assumption_checks=("units", "population", "grain", "zero_denominators"),
    dependence_handling="not inferential", max_work_units=1_000_000, refusal_codes=("NO_FORMULA",),
    honest_conclusion='mathematical identity or structural inconsistency; never a cause')


@dataclass(frozen=True)
class BucketQuality:
    key: str
    completeness: float | None
    available_known: bool


def evaluate_dq(buckets: list[BucketQuality], threshold: float = 0.95) -> MethodResult:
    if not buckets:
        return refused(DQ, "NO_OBSERVATIONS", "nothing to assess", "DQ_FAILURE")
    verdicts = {}
    for b in buckets:
        if b.completeness is None:
            verdicts[b.key] = "DQ_UNASSESSED"
        elif b.completeness < threshold:
            verdicts[b.key] = "DQ_IMMATURE"
        elif not b.available_known:
            verdicts[b.key] = "AVAILABILITY_UNKNOWN"
        else:
            verdicts[b.key] = "DQ_OK"
    blocked = sorted(k for k, v in verdicts.items() if v != "DQ_OK")
    return MethodResult(DQ.id, DQ.version, "OK_WITH_LIMITATIONS" if blocked else "OK",
                        "DQ_FAILURE" if blocked else "DESCRIPTIVE",
                        {"verdicts": verdicts, "domain_attribution_blocked": blocked, "threshold": threshold},
                        ("an immature or unassessed bucket is a data-quality finding, not a domain change",) if blocked else (),
                        ("completeness", "availability clock"), ())


def evaluate_seasonal(values: list[float], period: int, min_cycles: int = 3, k: float = 4.0) -> MethodResult:
    x = np.array(values, dtype=float)
    if len(x) < period * min_cycles + 1:
        return refused(SEASONAL, "INSUFFICIENT_HISTORY", f"need >= {min_cycles} full cycles of period {period} plus the point under test",
                       "DESCRIPTIVE")
    last = len(x) - 1
    same_phase = x[last - period::-period][:min_cycles * 2]
    med = float(np.median(same_phase))
    mad = float(np.median(np.abs(same_phase - med))) * 1.4826
    z = (x[last] - med) / mad if mad > 0 else float("inf") if x[last] != med else 0.0
    return MethodResult(SEASONAL.id, SEASONAL.version, "OK", "DESCRIPTIVE",
                        {"robust_z": float(z), "baseline_median": med, "anomaly_candidate": abs(z) >= k, "k": k},
                        ("an anomaly candidate, not a cause; multiplicity across many series is not adjusted here",),
                        (f"{len(same_phase)} same-phase history points",), ())


def evaluate_association(x: list[float], y: list[float], dependence: str, level: float = 0.95) -> MethodResult:
    if len(x) != len(y):
        return refused(ASSOCIATION, "MISALIGNED", "x and y differ in length", "ASSOCIATION")
    if dependence not in {"independent", "cluster_robust_not_needed"}:
        return refused(ASSOCIATION, "DEPENDENCE_UNDECLARED", f"dependence {dependence!r} not supported by the iid SE", "ASSOCIATION")
    if len(x) < 4:
        return refused(ASSOCIATION, "TOO_FEW_PAIRS", "need at least 4 pairs", "ASSOCIATION")
    if len(set(x)) < 2:
        return refused(ASSOCIATION, "NO_VARIATION", "x is constant: no slope can be estimated", "ASSOCIATION")
    res = stats.linregress(np.asarray(x, float), np.asarray(y, float))
    crit = float(stats.t.ppf(0.5 + level / 2, len(x) - 2))
    return MethodResult(ASSOCIATION.id, ASSOCIATION.version, "OK_WITH_LIMITATIONS", "ASSOCIATION",
                        {"slope": float(res.slope), "se_slope": float(res.stderr), "r": float(res.rvalue), "p_value": float(res.pvalue),
                         "ci": [float(res.slope - crit * res.stderr), float(res.slope + crit * res.stderr)], "n": len(x)},
                        ("association only: confounding, selection and reverse causation are not addressed",),
                        ("pairs aligned", "independence declared"), ())


def evaluate_identity(formula, derived_id: str, rows, tolerance: float = 1e-4, min_windows: int = 1) -> MethodResult:
    if formula is None:
        return refused(IDENTITY, "NO_FORMULA", "the metric declares no formula", "MATHEMATICAL_IDENTITY")
    r = verify_identity(formula, derived_id, rows, tolerance, min_windows)
    return MethodResult(IDENTITY.id, IDENTITY.version, "OK", "MATHEMATICAL_IDENTITY",
                        {"verdict": r.verdict, "max_abs_residual": r.max_abs_residual, "windows_checked": r.windows_checked,
                         "excluded": list(r.excluded)}, ("arithmetic identity is not causality",), ("tolerance", "zero denominators"), ())
