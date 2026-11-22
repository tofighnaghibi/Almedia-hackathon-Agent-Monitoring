"""Difference-in-differences (v3.2 §4.4 did_v1; plan §F.5; C04; CP-020).

Estimator: collapse each unit to (mean post − mean pre), then a pooled two-sample t on the per-unit
changes (Bertrand, Duflo & Mullainathan 2004). Collapsing removes serial correlation from the SE.
* Single intervention timing only. Staggered timing is refused in P0 (a two-way FE would be biased).
* `anticipation_window` drops the last w pre-periods.
* The pre-trend slope comparison is a DIAGNOSTIC, reported as such and never as proof of parallel trends.
* Sensitivity (plan §F.5): the differential linear trend per period that would explain the whole
  estimate (the "breakdown trend"), compared with what the pre-period data can rule out. This is linear
  extrapolation of the pre-trend, i.e. Rambachan & Roth's smoothness restriction with M = 0. It informs;
  it does not change the identification status.
* Identifies the ATT for the declared treated group, conditional on untestable assumptions. Not the ATE.
"""
from __future__ import annotations

import numpy as np

from ..contracts.observations import Panel
from ..contracts.studies import QuasiExperimentalDesign
from .kernels import pooled_two_sample
from .registry import CapabilitySpec, MethodResult, refused

DID = CapabilitySpec(
    id="did_v1", version="1", question_kinds=("ESTIMATE_EFFECT", "EXPLAIN_OBSERVATION"), input_shapes=("panel",),
    output_kind="CAUSAL_EFFECT_CONDITIONAL", estimand_families=("ATT",), required_metadata=("study_design", "group_map"),
    assumption_checks=("pretrend_diagnostic", "trend_sensitivity", "timing", "anticipation", "panel_balance"),
    dependence_handling="unit-collapsed pre/post changes (serial correlation removed from SE)", max_work_units=1_000_000,
    refusal_codes=("STAGGERED_TIMING_UNSUPPORTED", "PANEL_INSUFFICIENT", "GROUPS_MISSING", "DEGENERATE_VARIANCE"),
    honest_conclusion='conditional ATT under untestable parallel trends; a pre-trend pass never proves it', source_regimes=('quasi_experimental',))

UNTESTABLE = ("parallel trends in the post period", "no anticipation beyond the declared window",
              "no spillover to control units", "stable group composition")


def _slope(periods, values) -> float:
    return float(np.polyfit(periods, values, 1)[0]) if len(set(periods)) >= 2 else float("nan")


def evaluate_did(design: QuasiExperimentalDesign, panel: Panel, group_of: dict[str, str], treated_label: str,
                 control_label: str, level: float = 0.95) -> MethodResult:
    spec = DID
    if design.timing != "single":
        return refused(spec, "STAGGERED_TIMING_UNSUPPORTED", "staggered adoption needs a heterogeneity-robust estimator (not in P0)")
    t0, w = design.intervention_period, design.anticipation_window
    units: dict[str, dict] = {}
    for c in panel.cells:
        if c.value is None or c.unit not in group_of:
            continue
        u = units.setdefault(c.unit, {"pre": [], "pre_t": [], "post": [], "post_t": []})
        if c.period >= t0:
            u["post"].append(c.value)
            u["post_t"].append(c.period)
        elif c.period < t0 - w:
            u["pre"].append(c.value)
            u["pre_t"].append(c.period)
    usable = {k: v for k, v in units.items() if v["pre"] and v["post"]}
    dropped = sorted(set(units) - set(usable))
    change = {g: np.array([np.mean(v["post"]) - np.mean(v["pre"]) for k, v in usable.items() if group_of[k] == g])
              for g in (treated_label, control_label)}
    if any(len(x) == 0 for x in change.values()):
        return refused(spec, "GROUPS_MISSING", "both treated and control units need pre and post observations")
    if any(len(x) < 2 for x in change.values()):
        return refused(spec, "PANEL_INSUFFICIENT", "need at least 2 units per group")
    k = pooled_two_sample(change[treated_label], change[control_label], level)
    if k is None:
        return refused(spec, "DEGENERATE_VARIANCE", "unit-level changes have zero variance: no sampling uncertainty can be estimated")
    est, se, df, crit = k["estimate"], k["se"], k["df"], k["crit"]
    slopes = {g: np.array([_slope(v["pre_t"], v["pre"]) for uid, v in usable.items() if group_of[uid] == g])
              for g in (treated_label, control_label)}
    limitations = []
    pt = None if any(np.isnan(x).any() for x in slopes.values()) else pooled_two_sample(slopes[treated_label],
                                                                                      slopes[control_label], level)
    if pt is None:
        pre = {"diagnostic": "NOT_ASSESSABLE", "reason": "fewer than 2 pre-periods per unit, or zero slope variance"}
        sensitivity = {"assessable": False}
        limitations.append("pre-trend diagnostic not assessable")
    else:
        pre = {"diagnostic": "NO_DETECTABLE_DIVERGENCE" if pt["p_value"] >= 1 - level else "DIVERGENCE_DETECTED",
               "slope_difference": pt["estimate"], "p_value": pt["p_value"],
               "role": "diagnostic only; a pass is not proof of parallel trends (C04)"}
        if pre["diagnostic"] == "DIVERGENCE_DETECTED":
            limitations.append("PRETREND_DIVERGENCE: pre-period trends differ; parallel trends implausible")
        gap = float(np.mean([np.mean(v["post_t"]) - np.mean(v["pre_t"]) for uid, v in usable.items()
                             if group_of[uid] == treated_label]))
        lo, hi = pt["estimate"] - pt["crit"] * pt["se"], pt["estimate"] + pt["crit"] * pt["se"]
        breakdown = est / gap
        sensitivity = {"assessable": True, "method": "linear pre-trend extrapolation (smoothness M = 0)",
                       "periods_between_pre_and_post_centroids": gap, "breakdown_trend_per_period": breakdown,
                       "pretrend_slope_difference_ci": [lo, hi],
                       "trend_adjusted_estimate_range": [est - hi * gap, est - lo * gap],
                       "fragile": bool(lo <= breakdown <= hi),
                       "role": "informs only: does not change the identification status"}
        if sensitivity["fragile"]:
            limitations.append(f"FRAGILE_TO_TRENDS: a differential trend of {breakdown:.3g} per period, which the "
                               f"pre-period data cannot rule out (CI {lo:.3g} to {hi:.3g}), would explain the whole estimate")
    if dropped:
        limitations.append(f"{len(dropped)} unit(s) dropped for lacking pre or post data")
    values = {"functional": "ATT", "estimate": est, "se": se, "df": df, "ci": [est - crit * se, est + crit * se],
              "p_value": k["p_value"], "units_per_group": {g: int(len(x)) for g, x in change.items()},
              "pretrend": pre, "sensitivity": sensitivity, "dropped_units": dropped}
    return MethodResult(spec.id, spec.version, "OK_WITH_LIMITATIONS" if limitations else "OK", "CAUSAL_EFFECT_CONDITIONAL",
                        values, tuple(limitations), ("single timing", f"anticipation window {w}"), UNTESTABLE)
