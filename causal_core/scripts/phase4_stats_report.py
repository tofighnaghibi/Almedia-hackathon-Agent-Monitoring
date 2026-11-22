"""Independent statistics report and method catalog for Phase 4 (plan §F deliverables). Measured, reproducible, seeded.

    python -m scripts.phase4_stats_report
    # writes docs/phase4/statistics_report.json, STATISTICS_REPORT.md and METHODS.md (generated from the registry)
"""
from __future__ import annotations

import json
import math
import platform
from datetime import datetime, timezone

import numpy as np

from causal_core.analytics.catalog import P0_SPECS
from causal_core.analytics.quasi_experimental import UNTESTABLE as DID_UNTESTABLE
from causal_core.analytics.sequential import log_wealth_path, type_one_error
from evaluation.fixture_io import PROJECT_ROOT
from evaluation.phase4_cases import adversarial_suite, differential, srm_calibration_study

OUT = PROJECT_ROOT / "docs" / "phase4"


OLD_BET = {"bet": "fixed", "sides": "up", "lam": 0.5}     # Phase 0 reference D5, the default until 2026-11-22
NEW_BET = {}                                               # production default: regularized, λ0 0.25, k 100, two-sided


def _paths(z: np.ndarray, bet: dict | None, lam: float) -> np.ndarray:
    return np.cumsum(lam * z - lam * lam / 2.0, axis=1) if bet is None else log_wealth_path(z, **bet)


def power(shift: float, n_streams: int = 1000, n_peeks: int = 200, alpha: float = 0.05, seed: int = 7, lam: float = 0.5,
          bet: dict | None = None) -> dict:
    """Alarm rate and median alarm time when every increment is N(shift, 1). Evaluation side, seeded.
    bet=None is the fixed one-sided λ formula written out here; a dict runs the production bet with those settings."""
    rng = np.random.default_rng(seed)
    paths = _paths(rng.standard_normal((n_streams, n_peeks)) + shift, bet, lam)
    hit = paths >= math.log(1.0 / alpha)
    alarmed = hit.any(axis=1)
    first = np.argmax(hit, axis=1)[alarmed] + 1
    return {"shift_sd": shift, "peeks": n_peeks, "drift_per_step": lam * shift - lam * lam / 2 if bet is None else None,
            "alarm_rate": float(alarmed.mean()), "median_peeks_to_alarm": float(np.median(first)) if alarmed.any() else None}


def revised_and_bursty(n_streams: int = 1000, n_peeks: int = 200, alpha: float = 0.05, seed: int = 7, lam: float = 0.5,
                       bet: dict | None = None) -> dict:
    """G08's 'revised/bursty streams'. Evaluation side, seeded.

    revised: at peek 100 the last 50 increments are revised (fresh null draws) and wealth is recomputed, as the
    ledger does (CP-015). 'final' = alarm on the final data; 'ever raised' also counts alarms later SUPERSEDED.
    bursty: the null mean holds but 10% of peeks (blocks of 10) have SD 2, so the declared N(0, 1) is wrong.
    """
    thr = math.log(1.0 / alpha)
    rng = np.random.default_rng(seed)
    z = rng.standard_normal((n_streams, n_peeks))
    v1 = _paths(z[:, :100], bet, lam)
    z2 = z.copy()
    z2[:, 50:100] = rng.standard_normal((n_streams, 50))
    v2 = _paths(z2, bet, lam)
    raised_v1 = (v1 >= thr).any(axis=1)
    final = (v2 >= thr).any(axis=1)
    sd = np.ones(n_peeks)
    for start in range(0, n_peeks, 100):
        sd[start + 40:start + 50] = 2.0
    zb = rng.standard_normal((n_streams, n_peeks)) * sd
    bursty = (_paths(zb, bet, lam) >= thr).any(axis=1)
    return {"alpha": alpha, "streams": n_streams, "peeks": n_peeks, "seed": seed,
            "revised_final_version_rate": float(final.mean()), "revised_ever_raised_rate": float((raised_v1 | final).mean()),
            "revised_superseded_only_rate": float((raised_v1 & ~final).mean()), "bursty_variance_rate": float(bursty.mean())}


def build() -> dict:
    diff = differential()
    t1 = [{"rho": rho, "streams": 1000, "peeks": 200, "alpha": 0.05, "seed": 7,
           "false_alarm_rate": type_one_error(1000, 200, rho=rho), "false_alarm_rate_old_bet": type_one_error(1000, 200, rho=rho, **OLD_BET)}
          for rho in (0.0, 0.3, 0.5, 0.8)]
    pw = [power(d) for d in (0.1, 0.2, 0.25, 0.3, 0.5, 1.0)]
    pw_new = [{"old": power(d, n_peeks=n, bet=OLD_BET), "new": power(d, n_peeks=n, bet=NEW_BET)}
              for d, n in ((-0.25, 200), (0.1, 200), (0.2, 200), (0.25, 200), (0.5, 200), (1.0, 200), (0.05, 1000), (0.1, 1000))]
    rb = revised_and_bursty()
    rb_new = revised_and_bursty(bet=NEW_BET)
    srm = srm_calibration_study()
    adv = adversarial_suite()
    return {"generated_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "python": platform.python_version(), "differential_vs_phase0_oracle": diff, "type_one_error": t1, "revised_and_bursty": rb, "revised_and_bursty_new_bet": rb_new, "power_fixed_bet": pw,
            "power_old_vs_new_bet": pw_new, "srm_calibration": srm,
            "adversarial_suite": adv,
            "summary": {"differential_quantities": len(diff), "max_abs_diff": max(r["abs_diff"] for r in diff),
                        "adversarial_expectations_met": f"{sum(r['expectation_met'] for r in adv)}/{len(adv)}"},
            "history": ["First adversarial run (same expectations): skewed_clusters NOT MET. The method found both problems "
                        "but packed them into ONE limitation string. Fix: one limitation per reason "
                        "(experiments.cluster_support). The expectation was not changed.",
                        "Tier 1 T1-P4-01: the cluster and DiD SEs were computed as estimate / t-statistic, which is NaN "
                        "when the estimate is exactly 0; constant outcomes crashed the unit-level path with "
                        "ZeroDivisionError; a constant x crashed the association slope. Now one pooled kernel "
                        "(analytics/kernels.py) computes the SE directly, and zero variance refuses with "
                        "DEGENERATE_VARIANCE / NO_VARIATION. The differential rows above were re-run after the fix.",
                        "DiD trend sensitivity added (plan §F.5) after the suite's expectations were fixed. On "
                        "pretrend_ok_but_violated it reports fragile = "
                        f"{next(a for a in adv if a['scenario'] == 'pretrend_ok_but_violated')['observed']['trend_sensitivity_fragile']}: "
                        "the violation begins at the intervention, which no pre-period extrapolation can detect. "
                        "The expectation was not changed.",
                        "User decisions 2026-11-22: the default bet became the regularized two-sided plug-in "
                        "(λ0 0.25, prior strength 100, floor 0.05, cap 1.0); k = 100 was chosen from a sweep over "
                        "k ∈ {1, 5, 10, 20, 50, 100, 200, ∞} as the best balance of power near 0.25 SD in 200 peeks and power "
                        "on 0.1 SD over 1,000 peeks. Measured costs, recorded rather than hidden: autocorrelated streams "
                        "inflate more (ρ 0.8: 0.825 vs 0.458; still refused), variance bursts 0.064 vs 0.058, and the "
                        "revised 'ever raised' rate 0.052 vs 0.045.",
                        "SRM calibration, first version: a threshold qualified with as few as 1 experiment in its warning "
                        "band; on the mixed history it estimated 19% false warnings where truth gave 40%, and on the "
                        "blatant-bugs history 19% vs 50% (2 warnings). Fix: a threshold needs at least 10 past experiments "
                        "in its band (min_band). Truth was used to find the problem; the target (20%) was not tuned to it."]}


def markdown(r: dict) -> str:
    lines = ["# Phase 4 — Independent Statistics Report", "",
             f"Generated {r['generated_at']} (Python {r['python']}) by `python -m scripts.phase4_stats_report`. "
             "Every number below was produced by that run.", "",
             "## A. Production kernels vs the Phase 0 stdlib oracle", "",
             "Two independent implementations: the production kernels use NumPy/SciPy; the oracle is stdlib only (own incomplete beta).", "",
             "| Fixture | Quantity | Production | Oracle | abs diff |", "|---|---|---|---|---|"]
    for d in r["differential_vs_phase0_oracle"]:
        lines.append(f"| {d['fixture']} | {d['quantity']} | {d['production']:.12g} | {d['oracle']:.12g} | {d['abs_diff']:.2e} |")
    lines += ["", "Fixture 04 `itt_se` differs by 2e-7 because the oracle uses p(1−p)/n while Welch uses the n−1 sample variance.", "",
              "## B. Sequential Type-I error (G08)", "",
              "Production default since 2026-11-22: regularized two-sided plug-in bet (λ0 0.25, prior strength 100, floor 0.05, "
              "cap 1.0). Old: fixed one-sided λ = 0.5. Threshold log(1/α), α = 0.05. Each row: 1,000 null streams × 200 peeks, seed 7.", "",
              "| AR(1) ρ | False-alarm rate (new) | Within α? | Old fixed bet |", "|---|---|---|---|"]
    for t in r["type_one_error"]:
        lines.append(f"| {t['rho']} | {t['false_alarm_rate']:.3f} | {'yes' if t['false_alarm_rate'] <= t['alpha'] else '**no**'} | "
                     f"{t['false_alarm_rate_old_bet']:.3f} |")
    lines += ["", "Independent increments respect α, as Ville's inequality guarantees. Autocorrelated increments break the guarantee, "
              "so `savi_eprocess_v1` refuses any stream not declared independent (`DEPENDENCE_UNSUPPORTED`).", "",
              "### Revised and bursty null streams", "",
              "Same 1,000 × 200 design, seed 7. Revised: at peek 100 the last 50 increments are re-issued (fresh null draws) and "
              "wealth is recomputed as the ledger does (CP-015). Bursty: the null mean holds, but 10% of peeks (blocks of 10) "
              "have SD 2, so the declared N(0, 1) null is wrong.", "",
              "| Stream | Old fixed bet | New default | Within α (new)? |", "|---|---|---|---|"]
    for key, label in (("revised_final_version_rate", "revised: alarm open on the final data"),
                       ("revised_ever_raised_rate", "revised: ever raised (incl. later SUPERSEDED)"),
                       ("bursty_variance_rate", "bursty variance (null misspecified)")):
        o, n = r["revised_and_bursty"][key], r["revised_and_bursty_new_bet"][key]
        lines.append(f"| {label} | {o:.3f} | {n:.3f} | {'yes' if n <= 0.05 else '**no**'} |")
    lines += ["",
              "The final-data rate is the guarantee the e-process gives. An alarm raised on data that is later revised away is "
              "marked SUPERSEDED, but if it was already delivered, users saw it: the 'ever raised' row is the operational "
              "false-alarm rate. It has no guarantee: each revision gives the path another chance to cross. Arrival bursts (several increments delivered at once) cannot raise the rate: checking the "
              "same path less often can only lower it. Variance bursts can, because the declared null no longer holds.", "",
              "### Power of the fixed bet (λ = 0.5)", "",
              "Each increment ~ N(shift, 1); 1,000 streams × 200 peeks, seed 7. The expected log-wealth drift per step is "
              "λ·shift − λ²/2, which is ≤ 0 for shift ≤ λ/2 = 0.25 SD. Small shifts still alarm sometimes, but only early and by "
              "noise: with zero or negative drift, more data does not push the alarm rate toward 1.", "",
              "| Shift (SD) | Drift / step | Alarm rate | Median peeks to alarm |", "|---|---|---|---|"]
    for w in r["power_fixed_bet"]:
        lines.append(f"| {w['shift_sd']} | {w['drift_per_step']:+.4f} | {w['alarm_rate']:.3f} | {w['median_peeks_to_alarm']} |")
    lines += ["", "This was a power limit, not a validity problem. It is why the default changed on 2026-11-22:", "",
              "### Old fixed bet vs the new default (1,000 streams, seed 7)", "",
              "| Shift (SD) | Peeks | Old alarm rate | New alarm rate | New median peeks to alarm |", "|---|---|---|---|---|"]
    for w in r["power_old_vs_new_bet"]:
        lines.append(f"| {w['new']['shift_sd']:+} | {w['new']['peeks']} | {w['old']['alarm_rate']:.3f} | "
                     f"{w['new']['alarm_rate']:.3f} | {w['new']['median_peeks_to_alarm']} |")
    lines += ["", "A negative shift is invisible to the old one-sided bet. The new bet adapts toward the observed drift, so power "
              "keeps growing with data for any shift above floor/2 = 0.025 SD; the prior (λ0 = 0.25, worth 100 observations) "
              "keeps early bets stable.", "",
              "## B2. SRM warning threshold learned from simulated history (CP-019)", "",
              "Warn (PARTIAL) from the learned threshold, always block (INVALID_DESIGN) below 0.001. p-values from the stdlib "
              "oracle; threshold from production `calibrate_srm_warning` (target ≤ 20% false warnings, ≥ 10 experiments in "
              "the band); right/wrong read from emulator truth here only.", "",
              "| History | Learned threshold | Warned (false share) | Blocked (truly broken) | Broken missed | False share if 0.01 kept |",
              "|---|---|---|---|---|---|"]
    for label, v in r["srm_calibration"].items():
        tf = "–" if v["true_false_warning_share"] is None else f"{v['true_false_warning_share']:.0%}"
        f01 = "–" if v["false_share_at_0.01"] is None else f"{v['false_share_at_0.01']:.0%}"
        lines.append(f"| {label} | {v['warn_alpha']:.4f} | {v['warned']} ({tf}) | {v['blocked']} ({v['blocked_truly_broken']}) | "
                     f"{v['broken_missed']} | {f01} |")
    lines += ["", "Subtle imbalances (true split 49.25/50.75) are mostly indistinguishable from healthy noise at these sizes: "
              "a loose warning tier would mostly fire on healthy experiments, so the calibration keeps the band narrow. "
              "Small bands are noisy (estimated 19% vs actual 40% on the mixed history).", "",
              "## C. Adversarial suite scored against emulator truth", "",
              "Expectations were written in `evaluation/phase4_cases.py` before results were seen. Truth never enters the core.", "",
              "| Scenario | Expected behaviour | Observed | Truth | CI covers truth | Met |", "|---|---|---|---|---|---|"]
    for a in r["adversarial_suite"]:
        obs = "; ".join(f"{k}={v}" for k, v in a["observed"].items())
        lines.append(f"| {a['scenario']} | {a['expected']} | {obs} | {a['truth']} | {a['ci_covers_truth']} | "
                     f"{'✓' if a['expectation_met'] else '✗'} |")
    lines += ["", "**Reading the table:** a CI that misses the truth is expected wherever an untestable assumption is violated by "
              "construction (pre-trend, attrition, spillover, confounding). The test is whether the system *says* so: it "
              "flags, downgrades, lists the assumption as untestable, or refuses to identify. A wrong estimate presented as "
              "certified would be the failure.", "", "## History", ""] + [f"- {h}" for h in r["history"]]
    return "\n".join(lines) + "\n"


UNTESTABLE_BY_METHOD = {
    "randomized_effect_v1": ["no interference between units (SUTVA): always listed; worded as declared-not-tested when the design assumes none",
                             "complete-case analysis: outcomes missing completely at random (only when attrition > 0)",
                             "CACE only: exclusion restriction; monotonicity (no defiers)"],
    "did_v1": list(DID_UNTESTABLE),
    "savi_eprocess_v1": ["the declared null actually holds before any change", "independence of increments"],
    "rcd_lite_v1": ["no hidden confounding that shifts with the regime", "stable selection"],
    "baseline_shift_v1": ["the calendar model still describes the series", "Gaussian residuals with the training-window variance",
                          "independence of residuals (lag-1 checked on training only)"],
    "association_v1": ["none tested: confounding, selection and reverse causation are not addressed"],
    "identifiability_audit_v1": ["transport: each declared transport assumption (CONDITIONAL kept); "
                                 "otherwise transportability of the effect (PARTIAL)",
                                 "no unmeasured confounding beyond the declared view (observational)"],
}


def methods_markdown() -> str:
    lines = ["# Phase 4 — Method Catalog (assumption registry + rejection catalog)", "",
             "Generated by `python -m scripts.phase4_stats_report` from `causal_core/analytics/catalog.py`. Do not edit by hand.", "",
             "The registry runs the generic prechecks (question kind, input shape, metadata, estimand family, source regime, "
             "forecast work vs cap) before any method code. Each method then runs its own checks and returns a typed refusal "
             "from the list below when a premise fails. `refused()` raises if a method tries an undeclared code.", "",
             "## Descriptors", "",
             "| Method | Questions | Input shapes | Source regimes | Estimands | Required metadata | Cost estimator / cap | Honest conclusion (ceiling) |",
             "|---|---|---|---|---|---|---|---|"]
    for sp in P0_SPECS:
        lines.append(f"| `{sp.id}` v{sp.version} | {', '.join(sp.question_kinds)} | {', '.join(sp.input_shapes)} | "
                     f"{', '.join(sp.source_regimes) if sp.source_regimes else 'any'} | "
                     f"{', '.join(sp.estimand_families) if sp.estimand_families else 'any'} | {', '.join(sp.required_metadata) or '—'} | "
                     f"`{sp.cost_estimator_id}` / {sp.max_work_units:,} | {sp.honest_conclusion} |")
    lines += ["", "## Assumption registry", "",
              "| Method | Checked by the method | Listed as untestable (returned in every result) | Dependence handling |",
              "|---|---|---|---|"]
    for sp in P0_SPECS:
        lines.append(f"| `{sp.id}` | {', '.join(sp.assumption_checks)} | {'; '.join(UNTESTABLE_BY_METHOD.get(sp.id, ['—']))} | "
                     f"{sp.dependence_handling} |")
    lines += ["", "## Rejection catalog", "", "| Method | Typed refusal codes |", "|---|---|"]
    for sp in P0_SPECS:
        lines.append(f"| `{sp.id}` | {', '.join(f'`{c}`' for c in sp.refusal_codes) or '— (returns a status, never refuses)'} |")
    lines += ["", "Registry-level refusal reasons (any method): `QUESTION_KIND`, `INPUT_SHAPE`, `MISSING_METADATA`, "
              "`ESTIMAND_FAMILY`, `SOURCE_REGIME`, `BUDGET`. Each comes with a candidate next query."]
    return "\n".join(lines) + "\n"


def main() -> int:
    r = build()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "METHODS.md").write_text(methods_markdown())
    (OUT / "statistics_report.json").write_text(json.dumps(r, indent=2, default=str) + "\n")
    (OUT / "STATISTICS_REPORT.md").write_text(markdown(r))
    print(json.dumps(r["summary"], indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
