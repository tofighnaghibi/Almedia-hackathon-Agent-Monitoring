"""Randomized studies and multiplicity (v3.2 §4.4, §7.3; plan §F.4; C01, C05, F04; CP-019).

randomized_effect_v1
  * SRM tested at the declared *assignment* unit (clusters when clusters were randomized). A source's own
    SRM p-value is never trusted (D6). Two tiers (user decision 2026-11-22, CP-019):
      p < 0.001 (the floor) → refused, SRM_FAIL: assignment integrity is not established;
      p < the warning threshold (default 0.01, learnable from history, never below the floor) → SRM_SUSPECT
      limitation, which the auditor downgrades to partial identification.
    `calibrate_srm_warning` learns the warning threshold from past experiments' SRM p-values.
  * Cluster-randomized → analysis on cluster means, using the cluster-support policy approved at Phase 0
    exit (fixture 05). The naive per-unit SE is never reported.
  * Attrition: complete-case ITT plus an explicit MCAR caveat. Differential attrition is flagged, and the
    auditor downgrades it to partial identification.
  * Noncompliance: the ITT is identified; the exposure effect is not (C01). A CACE is offered only as a
    separately requested IV estimate whose exclusion and monotonicity assumptions are untestable.
multiplicity_v1
  * Holm over a declared family. Missing covariance never becomes zero: joint and surrogate claims are refused (C05).
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Sequence

import numpy as np
from scipy import stats

from ..contracts.observations import AssignmentOutcomes
from ..contracts.studies import RandomizedDesign
from .kernels import pooled_two_sample
from .registry import CapabilitySpec, MethodResult, refused

SRM_BLOCK_ALPHA = 1e-3        # the floor: below this, always a hard block (INVALID_DESIGN)
SRM_WARN_ALPHA = 0.01         # starting (looser) warning threshold when no history has been calibrated
SRM_WARN_CEILING = 0.05       # calibration never warns above this
DIFFERENTIAL_ATTRITION_ALPHA = 0.05

RANDOMIZED = CapabilitySpec(
    id="randomized_effect_v1", version="3", question_kinds=("ESTIMATE_EFFECT", "ASSESS_STUDY"),
    input_shapes=("assignment_outcomes", "aggregate_stats"), output_kind="CAUSAL_EFFECT_CONDITIONAL",
    estimand_families=("ITT", "risk_difference", "mean_difference", "CACE"), required_metadata=("study_design",),
    assumption_checks=("srm_at_assignment_unit", "attrition", "compliance", "clustering", "interference"),
    dependence_handling="assignment-unit analysis (cluster means when clusters are randomized)",
    max_work_units=10_000_000,
    refusal_codes=("ARMS_NOT_IN_DESIGN", "SRM_FAIL", "ASSIGNMENT_INCONSISTENT", "TOO_FEW_UNITS",
                   "UNSUPPORTED_CLUSTER_INFERENCE", "EXPOSURE_NOT_RECORDED", "ESTIMAND_NOT_SUPPORTED",
                   "DEGENERATE_VARIANCE"),
    honest_conclusion='scoped conditional ITT (CACE only under IV assumptions); otherwise a design audit or abstention', source_regimes=('randomized',))

MULTIPLICITY = CapabilitySpec(
    id="multiplicity_v1", version="1", question_kinds=("ESTIMATE_EFFECT", "ASSESS_STUDY"),
    input_shapes=("effect_results",), output_kind="CAUSAL_EFFECT_CONDITIONAL", estimand_families=None,
    required_metadata=("family",), assumption_checks=("family_declared", "covariance_available"),
    dependence_handling="Holm (valid under arbitrary dependence for marginal FWER)", max_work_units=10_000,
    refusal_codes=("FAMILY_UNDECLARED", "COVARIANCE_UNAVAILABLE"),
    honest_conclusion='conservative familywise control of marginal claims; not joint or surrogate evidence', cost_estimator_id='n_log_n_v1')


def cluster_support(clusters_per_arm: dict[str, int], equal_sizes: bool) -> tuple[str, tuple[str, ...]]:
    """Policy approved at Phase 0 exit (A3, fixture 05): <4 per arm unsupported; <15 or unequal → limited.
    One reason per limitation so consumers can act on each separately."""
    smallest = min(clusters_per_arm.values())
    if smallest < 4:
        return "UNSUPPORTED", (f"only {smallest} randomized clusters in the smallest arm",)
    limits = []
    if smallest < 15:
        limits.append(f"FEW_CLUSTERS: {smallest} clusters in the smallest arm: small-sample t on cluster means (df = G - 2)")
    if not equal_sizes:
        limits.append("UNEQUAL_CLUSTER_SIZES: unweighted cluster means, weaker t approximation")
    return ("SUPPORTED_WITH_LIMITATIONS", tuple(limits)) if limits else ("SUPPORTED", ())


def _srm(counts: dict[str, int], allocation: dict[str, float]) -> float:
    arms = sorted(allocation)
    obs = np.array([counts.get(a, 0) for a in arms], dtype=float)
    exp = np.array([allocation[a] for a in arms]) * obs.sum()
    return float(stats.chisquare(obs, exp).pvalue)


def evaluate_randomized(design: RandomizedDesign, data: AssignmentOutcomes, treatment_arm: str, control_arm: str,
                        functional: str = "ITT", level: float = 0.95, srm_warn_alpha: float = SRM_WARN_ALPHA) -> MethodResult:
    spec = RANDOMIZED
    if not SRM_BLOCK_ALPHA <= srm_warn_alpha <= SRM_WARN_CEILING:
        raise ValueError(f"srm_warn_alpha {srm_warn_alpha} outside [{SRM_BLOCK_ALPHA}, {SRM_WARN_CEILING}]: "
                         "the warning tier may not go below the hard-block floor")
    if functional not in spec.estimand_families:
        return refused(spec, "ESTIMAND_NOT_SUPPORTED", f"{functional} is not identified by randomization alone")
    for arm in (treatment_arm, control_arm):
        if arm not in data.arms or arm not in design.expected_allocation:
            return refused(spec, "ARMS_NOT_IN_DESIGN", f"arm {arm!r} missing from payload or design allocation")
    units = [u for u in data.units if u.arm in (treatment_arm, control_arm)]
    clustered = design.randomization == "cluster"
    limitations, checked, untestable = [], [], []

    # 1. assignment integrity at the assignment unit
    if clustered:
        if any(u.cluster_id is None for u in units):
            return refused(spec, "UNSUPPORTED_CLUSTER_INFERENCE", "cluster randomization declared, payload has no cluster ids")
        arm_of: dict[str, set] = {}
        for u in data.units:
            arm_of.setdefault(u.cluster_id, set()).add(u.arm)
        mixed = sorted(c for c, a in arm_of.items() if len(a) > 1)
        if mixed:
            return refused(spec, "ASSIGNMENT_INCONSISTENT", f"clusters assigned to more than one arm: {mixed[:5]}")
        counts = {a: sum(1 for arms in arm_of.values() if a in arms) for a in design.expected_allocation}
    else:
        counts = {a: sum(1 for u in data.units if u.arm == a) for a in design.expected_allocation}
    srm_p = _srm(counts, design.expected_allocation)
    checked.append(f"SRM at {'cluster' if clustered else design.assignment_unit} level: p = {srm_p:.4g} "
                   f"(warn < {srm_warn_alpha:g}, block < {SRM_BLOCK_ALPHA:g})")
    if srm_p < SRM_BLOCK_ALPHA:
        return refused(spec, "SRM_FAIL", f"sample ratio mismatch p = {srm_p:.3g} < {SRM_BLOCK_ALPHA}; assignment integrity not established")
    if srm_p < srm_warn_alpha:
        limitations.append(f"SRM_SUSPECT: sample ratio mismatch p = {srm_p:.3g} < warning threshold {srm_warn_alpha:g}; "
                           "assignment integrity is questionable and the ITT may be biased")

    # 2. attrition (missing outcomes), overall and differential
    miss = {arm: [u.outcome is None for u in units if u.arm == arm] for arm in (treatment_arm, control_arm)}
    rates = {arm: float(np.mean(m)) for arm, m in miss.items()}
    diff_p = None
    if any(rates.values()):
        n_t, n_c = len(miss[treatment_arm]), len(miss[control_arm])
        pooled = (sum(miss[treatment_arm]) + sum(miss[control_arm])) / (n_t + n_c)
        se = math.sqrt(pooled * (1 - pooled) * (1 / n_t + 1 / n_c)) if 0 < pooled < 1 else 0.0
        diff_p = float(2 * stats.norm.sf(abs(rates[treatment_arm] - rates[control_arm]) / se)) if se > 0 else 1.0
        untestable.append("complete-case analysis: outcomes missing completely at random")
        checked.append(f"attrition: treatment {rates[treatment_arm]:.1%}, control {rates[control_arm]:.1%} (p = {diff_p:.3g})")
        if diff_p < DIFFERENTIAL_ATTRITION_ALPHA:
            limitations.append("DIFFERENTIAL_ATTRITION: missingness differs by arm; the complete-case ITT may be biased "
                               "(bounds not computed in P0)")

    # 3. compliance
    exposures = [u for u in units if u.exposure is not None]
    noncompliance = any((u.arm == treatment_arm and u.exposure == 0) or (u.arm == control_arm and u.exposure == 1)
                        for u in exposures)
    if noncompliance:
        limitations.append("NONCOMPLIANCE: the ITT is identified; the effect of actual exposure is NOT (C01)")
    untestable.append("no interference between units (SUTVA): " + (
        "declared by the design, not tested by these data" if design.interference == "none_assumed"
        else "not declared; spillover would bias the ITT and these data cannot detect it"))

    # 4. estimate at the analysis unit
    complete = [u for u in units if u.outcome is not None]
    if clustered:
        by_cluster: dict[str, list[float]] = {}
        arm_c: dict[str, str] = {}
        for u in complete:
            by_cluster.setdefault(u.cluster_id, []).append(u.outcome)
            arm_c[u.cluster_id] = u.arm
        per_arm = {a: sum(1 for c in by_cluster if arm_c[c] == a) for a in (treatment_arm, control_arm)}
        sizes = {len(v) for v in by_cluster.values()}
        support, why = cluster_support(per_arm, len(sizes) == 1)
        if support == "UNSUPPORTED":
            return refused(spec, "UNSUPPORTED_CLUSTER_INFERENCE", why[0])
        limitations.extend(why)
        yt = np.array([np.mean(v) for c, v in by_cluster.items() if arm_c[c] == treatment_arm])
        yc = np.array([np.mean(v) for c, v in by_cluster.items() if arm_c[c] == control_arm])
        k = pooled_two_sample(yt, yc, level)
        if k is None:
            return refused(spec, "DEGENERATE_VARIANCE", "cluster means have zero variance: no sampling uncertainty can be estimated")
        est, se, df, crit, p_value, analysis_unit = k["estimate"], k["se"], k["df"], k["crit"], k["p_value"], "cluster"
    else:
        yt = np.array([u.outcome for u in complete if u.arm == treatment_arm], dtype=float)
        yc = np.array([u.outcome for u in complete if u.arm == control_arm], dtype=float)
        if len(yt) < 2 or len(yc) < 2:
            return refused(spec, "TOO_FEW_UNITS", "need at least 2 complete units per arm")
        est = float(yt.mean() - yc.mean())
        se = float(math.sqrt(yt.var(ddof=1) / len(yt) + yc.var(ddof=1) / len(yc)))
        if not se > 0:
            return refused(spec, "DEGENERATE_VARIANCE", "outcomes are constant in both arms: no sampling uncertainty can be estimated")
        df, crit = None, float(stats.norm.ppf(0.5 + level / 2))
        p_value, analysis_unit = float(2 * stats.norm.sf(abs(est / se))), design.assignment_unit
        per_arm = {treatment_arm: len(yt), control_arm: len(yc)}
    values = {"functional": "ITT", "estimate": est, "se": se, "ci": [est - crit * se, est + crit * se], "p_value": p_value,
              "df": df, "analysis_unit": analysis_unit, "units_per_arm": per_arm, "srm_p_value": srm_p,
              "srm_warn_alpha": srm_warn_alpha, "attrition": rates, "differential_attrition_p": diff_p,
              "noncompliance_detected": noncompliance}

    # 5. CACE only on request, as a separate IV claim
    if functional == "CACE":
        if not exposures or len(exposures) < len(units):
            return refused(spec, "EXPOSURE_NOT_RECORDED", "CACE needs recorded exposure for every unit")
        if clustered:
            return refused(spec, "ESTIMAND_NOT_SUPPORTED", "cluster-level CACE is not implemented in P0")
        d_t = np.array([u.exposure for u in complete if u.arm == treatment_arm], dtype=float)
        d_c = np.array([u.exposure for u in complete if u.arm == control_arm], dtype=float)
        a, b = est, float(d_t.mean() - d_c.mean())
        if abs(b) < 1e-9:
            return refused(spec, "ESTIMAND_NOT_SUPPORTED", "no first stage: assignment does not change exposure")
        cov = float(np.cov(yt, d_t)[0, 1] / len(yt) + np.cov(yc, d_c)[0, 1] / len(yc))
        var_b = float(d_t.var(ddof=1) / len(d_t) + d_c.var(ddof=1) / len(d_c))
        wald_se = math.sqrt(se ** 2 / b ** 2 + a ** 2 * var_b / b ** 4 - 2 * a * cov / b ** 3)
        values.update(functional="CACE", itt=est, first_stage=b, estimate=a / b, se=wald_se,
                      ci=[a / b - crit * wald_se, a / b + crit * wald_se])
        untestable += ["exclusion restriction (assignment affects outcome only through exposure)",
                       "monotonicity (no defiers)"]
        limitations.append("CACE is a complier-only effect under instrumental-variable assumptions")
    status = "OK_WITH_LIMITATIONS" if limitations else "OK"
    return MethodResult(spec.id, spec.version, status, "CAUSAL_EFFECT_CONDITIONAL", values, tuple(limitations),
                        tuple(checked), tuple(untestable))


@dataclass(frozen=True)
class SrmCalibration:
    warn_alpha: float
    n_experiments: int
    healthy_share: float | None          # Storey's π0: estimated share of experiments with no real imbalance
    warnings_in_history: int | None      # history experiments whose p falls in the warning band [floor, warn_alpha)
    estimated_false_warning_share: float | None
    reason: str


def calibrate_srm_warning(historical_p: Sequence[float], *, target_false_warning_share: float = 0.2,
                          min_history: int = 100, min_band: int = 10, floor: float = SRM_BLOCK_ALPHA,
                          ceiling: float = SRM_WARN_CEILING,
                          default: float = SRM_WARN_ALPHA) -> SrmCalibration:
    """Learn the SRM warning threshold from past experiments' SRM p-values (user decision 2026-11-22).

    Healthy experiments have uniform SRM p-values; real imbalances pile up near 0. Storey's estimator gives the
    healthy share π0 = #{p > 0.5} / (0.5 N). For a threshold t, the warning band is [floor, t): experiments below
    the floor are already blocked, so only the band decides whether a warning tier is worth having. The expected
    number of healthy experiments in the band is π0 (t − floor) N, so the estimated share of false warnings is
    π0 (t − floor) N / #{floor ≤ p < t}. The largest t in (floor, ceiling] whose estimate is at most the target wins.

    * fewer than `min_history` p-values → `default` (the looser starting threshold), with the reason;
    * a threshold qualifies only if at least `min_band` past experiments fall in its band: estimates from a
      handful of experiments are noise (measured: 2 in the band gave an estimated 19% vs a true 50%);
    * no t qualifies → `floor`: the warning tier collapses onto the block, because on this history a warning
      would mostly fire on healthy experiments;
    * never below `floor`, never above `ceiling`.
    """
    p = np.asarray([float(x) for x in historical_p], dtype=float)
    if p.size and (np.isnan(p).any() or (p < 0).any() or (p > 1).any()):
        raise ValueError("historical SRM p-values must lie in [0, 1]")
    n = int(p.size)
    if n < min_history:
        return SrmCalibration(default, n, None, None, None,
                              f"insufficient history ({n} < {min_history}): starting threshold {default:g} kept")
    pi0 = min(1.0, float((p > 0.5).sum()) / (0.5 * n))
    candidates = sorted({float(x) for x in p if floor < x <= ceiling} | {ceiling})
    best = None
    for t in candidates:
        t_open = np.nextafter(t, 1.0)                         # warn when p < t' for t' just above an observed p
        in_band = int(((p >= floor) & (p < t_open)).sum())
        if in_band < min_band:
            continue
        share = pi0 * (t_open - floor) * n / in_band
        if share <= target_false_warning_share:
            best = (float(min(t_open, ceiling)), in_band, share)
    if best is None:
        return SrmCalibration(floor, n, pi0, 0, None,
                              f"no warning threshold keeps estimated false warnings ≤ {target_false_warning_share:.0%}: "
                              f"warning tier collapses onto the {floor:g} block")
    t, k, share = best
    return SrmCalibration(t, n, pi0, k, share,
                          f"largest threshold with estimated false-warning share ≤ {target_false_warning_share:.0%}")


@dataclass(frozen=True)
class ArmSummary:
    """Per-arm summary from a source (e.g. an experimentation platform). se is the standard error of the arm mean."""
    n: int
    mean: float
    se: float


def evaluate_randomized_summary(design: RandomizedDesign, arms: dict[str, ArmSummary], treatment_arm: str,
                                control_arm: str, functional: str = "ITT", level: float = 0.95,
                                srm_warn_alpha: float = SRM_WARN_ALPHA, variance_adjusted_by_source: bool = False,
                                assignment_counts: dict[str, int] | None = None,
                                assignment_count_source: str | None = None) -> MethodResult:
    """ITT from per-arm summaries (CP-032). SRM is recomputed from the arm counts (D6: never the source's p-value);
    the effect and SE are recomputed from the arm means and SEs. Unit-level checks are impossible and say so."""
    spec = RANDOMIZED
    if not SRM_BLOCK_ALPHA <= srm_warn_alpha <= SRM_WARN_CEILING:
        raise ValueError(f"srm_warn_alpha {srm_warn_alpha} outside [{SRM_BLOCK_ALPHA}, {SRM_WARN_CEILING}]")
    if functional not in ("ITT", "risk_difference", "mean_difference"):
        return refused(spec, "ESTIMAND_NOT_SUPPORTED", f"{functional} needs unit-level exposure data; summaries carry none")
    for arm in (treatment_arm, control_arm):
        if arm not in arms or arm not in design.expected_allocation:
            return refused(spec, "ARMS_NOT_IN_DESIGN", f"arm {arm!r} missing from the summaries or the design allocation")
    if design.randomization == "cluster":
        return refused(spec, "UNSUPPORTED_CLUSTER_INFERENCE", "cluster-randomized design: arm summaries cannot give a "
                       "cluster-level SE")
    t, c = arms[treatment_arm], arms[control_arm]
    if min(t.n, c.n) < 2:
        return refused(spec, "TOO_FEW_UNITS", "need at least 2 units per arm")
    share = {a: design.expected_allocation[a] for a in (treatment_arm, control_arm)}
    tot = sum(share.values())
    limitations = []
    if assignment_counts and treatment_arm in assignment_counts and control_arm in assignment_counts:
        counts, source = {a: int(assignment_counts[a]) for a in (treatment_arm, control_arm)}, assignment_count_source or "declared"
    else:
        # CP-032 fix: a metric's own units can be a filtered subset (unit counts often differ across metrics), so SRM
        # on them is not an assignment check; said, not hidden.
        counts, source = {treatment_arm: t.n, control_arm: c.n}, "this metric's units"
        limitations.append("SRM_ON_METRIC_UNITS: no assignment counts supplied; SRM used this metric's units, which may "
                           "be a filtered subset of the assigned population")
    srm_p = _srm(counts, {a: v / tot for a, v in share.items()})
    checked = [f"SRM on {source} ({treatment_arm} {counts[treatment_arm]:,}, {control_arm} {counts[control_arm]:,}): "
               f"p = {srm_p:.4g} (warn < {srm_warn_alpha:g}, block < {SRM_BLOCK_ALPHA:g})"]
    if srm_p < SRM_BLOCK_ALPHA:
        return refused(spec, "SRM_FAIL", f"sample ratio mismatch p = {srm_p:.3g} < {SRM_BLOCK_ALPHA}; assignment integrity not established")
    if srm_p < srm_warn_alpha:
        limitations.append(f"SRM_SUSPECT: sample ratio mismatch p = {srm_p:.3g} < warning threshold {srm_warn_alpha:g}; "
                           "assignment integrity is questionable and the ITT may be biased")
    se = math.sqrt(t.se ** 2 + c.se ** 2)
    if not (se > 0 and math.isfinite(se)):
        return refused(spec, "DEGENERATE_VARIANCE", "arm standard errors are zero or not finite")
    est = t.mean - c.mean
    crit = float(stats.norm.ppf(0.5 + level / 2))
    limitations.append("UNIT_LEVEL_CHECKS_UNAVAILABLE: attrition, noncompliance and clustering cannot be assessed "
                       "from arm summaries")
    if variance_adjusted_by_source:
        limitations.append("VARIANCE_ADJUSTED_BY_SOURCE: arm means/SEs are regression-adjusted (CUPED/CURE) by the source")
    untestable = ["the source's arm standard errors are valid (independent units at the assignment unit)",
                  "no differential attrition or exclusion between arms (not checkable from summaries)",
                  "no interference between units (SUTVA): " + ("declared by the design, not tested by these data"
                                                               if design.interference == "none_assumed" else
                                                               "not declared; spillover would bias the ITT and these "
                                                               "data cannot detect it")]
    return MethodResult(spec.id, spec.version, "OK_WITH_LIMITATIONS", "CAUSAL_EFFECT_CONDITIONAL",
                        {"functional": "ITT", "estimate": est, "se": se, "ci": [est - crit * se, est + crit * se],
                         "p_value": float(2 * stats.norm.sf(abs(est / se))), "df": None, "analysis_unit": design.assignment_unit,
                         "units_per_arm": {treatment_arm: t.n, control_arm: c.n}, "srm_p_value": srm_p,
                         "srm_warn_alpha": srm_warn_alpha, "input": "arm_summaries", "level": level},
                        tuple(limitations), tuple(checked), tuple(untestable))


def holm(p_values: dict[str, float], alpha: float, family: str | None, covariance_available: bool,
         requested_claim: str = "marginal") -> MethodResult:
    spec = MULTIPLICITY
    if not family:
        return refused(spec, "FAMILY_UNDECLARED", "a multiplicity family must be declared before testing")
    if requested_claim in {"joint", "surrogate", "mediation"} and not covariance_available:
        return refused(spec, "COVARIANCE_UNAVAILABLE", f"a {requested_claim} claim needs the joint (covariance) distribution; "
                       "missing covariance is never zero (C05). Marginal claims only.")
    order = sorted(p_values, key=p_values.get)
    m, running, adjusted = len(order), 0.0, {}
    for i, h in enumerate(order):
        running = max(running, min(1.0, (m - i) * p_values[h]))
        adjusted[h] = running
    return MethodResult(spec.id, spec.version, "OK", "CAUSAL_EFFECT_CONDITIONAL",
                        {"family": family, "alpha": alpha, "adjusted_p": adjusted,
                         "rejected": sorted(h for h, p in adjusted.items() if p <= alpha), "claim": "marginal"},
                        limitations=() if covariance_available else ("covariance unavailable: marginal FWER control only",),
                        assumptions_checked=(f"family {family!r} declared with {m} hypotheses",))
