"""Kitagawa mix-vs-rate and RCD-lite invariance screening (v3.2 §4.4; plan §F.6; C06).

kitagawa_v1: exact arithmetic identity ΔR = Σ w̄Δr (rate) + Σ r̄Δw (mix). It is MATHEMATICAL_IDENTITY,
  never causal, and never "proof of no harm". Partition completeness comes from the DimensionSpec or
  declared totals. Without either it is reported as undeclared, and an incomplete partition reports
  its uncovered share.
rcd_lite_v1: compares the child | parents regression across regimes (Chow F test). A stable mechanism
  is only a *candidate* for pruning. Hidden confounding or selection can mimic either answer (C06).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy import stats

from ..contracts.variables import DimensionSpec
from .registry import CapabilitySpec, MethodResult, refused

KITAGAWA = CapabilitySpec(
    id="kitagawa_v1", version="1", question_kinds=("EXPLAIN_OBSERVATION", "DETECT_CHANGE"), input_shapes=("aggregate_stats",),
    output_kind="MATHEMATICAL_IDENTITY", estimand_families=None, required_metadata=("strata", "ratio_metric"),
    assumption_checks=("partition_completeness", "matching_population_and_window"), dependence_handling="not inferential",
    max_work_units=100_000, refusal_codes=("ZERO_DENOMINATOR_SEGMENT", "NO_STRATA"),
    honest_conclusion='mix-vs-rate arithmetic with the residual shown; not a cause, never "harmless" by itself')
RCD_LITE = CapabilitySpec(
    id="rcd_lite_v1", version="1", question_kinds=("EXPLAIN_OBSERVATION", "ASSESS_HYPOTHESIS"), input_shapes=("panel", "time_series"),
    output_kind="STRUCTURAL_CAUSAL_HYPOTHESIS", estimand_families=None, required_metadata=("parents", "regimes"),
    assumption_checks=("enough_observations_per_regime", "parents_measured"), dependence_handling="OLS residuals; iid errors assumed",
    max_work_units=1_000_000, refusal_codes=("INSUFFICIENT_REGIMES", "PARENTS_UNMEASURED"),
    honest_conclusion='mechanism-invariance candidate pruning only; hidden confounding can mimic a shift')


@dataclass(frozen=True)
class Stratum:
    segment: str
    num0: float
    den0: float
    num1: float
    den1: float


def evaluate_kitagawa(strata: list[Stratum], partition: DimensionSpec | None = None,
                      declared_totals: tuple[float, float] | None = None) -> MethodResult:
    spec = KITAGAWA
    if not strata:
        return refused(spec, "NO_STRATA", "no strata supplied", "MATHEMATICAL_IDENTITY")
    if any(s.den0 <= 0 or s.den1 <= 0 for s in strata):
        return refused(spec, "ZERO_DENOMINATOR_SEGMENT", "a stratum has no denominator in one period", "MATHEMATICAL_IDENTITY")
    d0 = np.array([s.den0 for s in strata]); d1 = np.array([s.den1 for s in strata])
    n0 = np.array([s.num0 for s in strata]); n1 = np.array([s.num1 for s in strata])
    w0, w1, r0, r1 = d0 / d0.sum(), d1 / d1.sum(), n0 / d0, n1 / d1
    rate_g, mix_g = (w0 + w1) / 2 * (r1 - r0), (r0 + r1) / 2 * (w1 - w0)
    total = n1.sum() / d1.sum() - n0.sum() / d0.sum()
    limitations = ["arithmetic attribution, not causal; a mix explanation is not evidence the change is harmless"]
    values = {"total_change": float(total), "rate_component": float(rate_g.sum()), "mix_component": float(mix_g.sum()),
              "identity_residual": float(total - rate_g.sum() - mix_g.sum()),
              "segments": {s.segment: {"rate": float(r), "mix": float(m)} for s, r, m in zip(strata, rate_g, mix_g)},
              "every_segment_rate_rose": bool(np.all(r1 > r0))}
    if declared_totals is not None:
        cov = min(d0.sum() / declared_totals[0], d1.sum() / declared_totals[1])
        values["coverage"] = float(cov)
        if cov < 1.0:
            values["partition"] = "INCOMPLETE_PARTITION"
            limitations.append(f"INCOMPLETE_PARTITION: {1 - cov:.1%} of the population is outside the listed strata")
    if "partition" not in values:
        if partition is not None and not partition.complete_partition:
            values["partition"] = "INCOMPLETE_PARTITION"
            limitations.append("INCOMPLETE_PARTITION: dimension declared open_unknown")
        elif partition is None and declared_totals is None:
            values["partition"] = "UNDECLARED"
            limitations.append("partition completeness undeclared: the decomposition explains only the listed strata")
        else:
            values["partition"] = "COMPLETE"
    return MethodResult(spec.id, spec.version, "OK_WITH_LIMITATIONS", "MATHEMATICAL_IDENTITY", values, tuple(limitations),
                        ("strata denominators positive",), ())


def evaluate_rcd_lite(child: np.ndarray, parents: np.ndarray, regime: np.ndarray, level: float = 0.95) -> MethodResult:
    """Chow test: does child ~ parents keep the same coefficients across two regimes?"""
    spec = RCD_LITE
    if parents.ndim != 2 or parents.shape[1] == 0:
        return refused(spec, "PARENTS_UNMEASURED", "no measured parents: invariance cannot be assessed")
    labels = np.unique(regime)
    k = parents.shape[1] + 1
    if len(labels) != 2 or min((regime == g).sum() for g in labels) < k + 2:
        return refused(spec, "INSUFFICIENT_REGIMES", f"need exactly 2 regimes with at least {k + 2} observations each")

    def rss(mask):
        X = np.column_stack([np.ones(mask.sum()), parents[mask]])
        beta, *_ = np.linalg.lstsq(X, child[mask], rcond=None)
        return float(((child[mask] - X @ beta) ** 2).sum()), beta

    pooled, _ = rss(np.ones_like(regime, dtype=bool))
    (r_a, b_a), (r_b, b_b) = rss(regime == labels[0]), rss(regime == labels[1])
    n = len(child)
    f = ((pooled - (r_a + r_b)) / k) / ((r_a + r_b) / (n - 2 * k))
    p = float(stats.f.sf(f, k, n - 2 * k))
    verdict = "SHIFTED_MECHANISM_CANDIDATE" if p < 1 - level else "INVARIANT_MECHANISM_CANDIDATE"
    return MethodResult(spec.id, spec.version, "OK_WITH_LIMITATIONS", "STRUCTURAL_CAUSAL_HYPOTHESIS",
                        {"verdict": verdict, "chow_f": float(f), "p_value": p, "coefficients": {str(labels[0]): b_a.tolist(),
                                                                                              str(labels[1]): b_b.tolist()}},
                        ("pruning heuristic only: hidden confounding, selection or an unmeasured parent can mimic "
                         "invariance or shift (C06)",), ("two regimes", "parents measured"),
                        ("all relevant parents measured", "no selection into regimes"))
