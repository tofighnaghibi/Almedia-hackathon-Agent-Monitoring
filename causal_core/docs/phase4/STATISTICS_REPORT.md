# Phase 4 — Independent Statistics Report

Generated 2026-11-22T10:47:05+00:00 (Python 3.11.12) by `python -m scripts.phase4_stats_report`. Every number below was produced by that run.

## A. Production kernels vs the Phase 0 stdlib oracle

Two independent implementations: the production kernels use NumPy/SciPy; the oracle is stdlib only (own incomplete beta).

| Fixture | Quantity | Production | Oracle | abs diff |
|---|---|---|---|---|
| 02 | mix_component | -0.024 | -0.024 | 3.47e-18 |
| 02 | rate_component | 0.005 | 0.005 | 2.60e-18 |
| 04 | itt | 0.0100401606426 | 0.010040160643 | 4.30e-13 |
| 04 | itt_se | 0.0043357220702 | 0.004335505235 | 2.17e-07 |
| 04 | srm_p | 0.571607644953 | 0.571607644953 | 3.31e-13 |
| 05 | cluster_estimate | 0.039282584 | 0.039282584 | 6.94e-18 |
| 05 | cluster_se | 0.104195839926 | 0.104195839926 | 4.23e-13 |
| 05 | cluster_p_value | 0.710576118007 | 0.710576118007 | 2.29e-13 |
| 06 | att | 1.32212083333 | 1.32212083333 | 3.33e-13 |
| 06 | att_se | 0.132995009966 | 0.132995009966 | 1.37e-13 |
| 06 | pretrend_p | 0.170093579813 | 0.170093579813 | 4.71e-13 |
| 07 | slope | 0.989971307391 | 0.989971307391 | 3.41e-13 |
| 07 | se_slope | 0.0253609834911 | 0.025360983491 | 1.22e-13 |
| 08 | log_wealth | 0.85 | 0.85 | 1.11e-16 |
| 11 | itt | 0.590830956842 | 0.590830956842 | 1.05e-13 |
| 11 | cace | 0.957831755973 | 0.957831755973 | 3.04e-13 |
| 11 | cace_se | 0.072572382456 | 0.072572382456 | 3.09e-14 |

Fixture 04 `itt_se` differs by 2e-7 because the oracle uses p(1−p)/n while Welch uses the n−1 sample variance.

## B. Sequential Type-I error (G08)

Production default since 2026-11-22: regularized two-sided plug-in bet (λ0 0.25, prior strength 100, floor 0.05, cap 1.0). Old: fixed one-sided λ = 0.5. Threshold log(1/α), α = 0.05. Each row: 1,000 null streams × 200 peeks, seed 7.

| AR(1) ρ | False-alarm rate (new) | Within α? | Old fixed bet |
|---|---|---|---|
| 0.0 | 0.036 | yes | 0.042 |
| 0.3 | 0.204 | **no** | 0.131 |
| 0.5 | 0.419 | **no** | 0.232 |
| 0.8 | 0.825 | **no** | 0.458 |

Independent increments respect α, as Ville's inequality guarantees. Autocorrelated increments break the guarantee, so `savi_eprocess_v1` refuses any stream not declared independent (`DEPENDENCE_UNSUPPORTED`).

### Revised and bursty null streams

Same 1,000 × 200 design, seed 7. Revised: at peek 100 the last 50 increments are re-issued (fresh null draws) and wealth is recomputed as the ledger does (CP-015). Bursty: the null mean holds, but 10% of peeks (blocks of 10) have SD 2, so the declared N(0, 1) null is wrong.

| Stream | Old fixed bet | New default | Within α (new)? |
|---|---|---|---|
| revised: alarm open on the final data | 0.043 | 0.041 | yes |
| revised: ever raised (incl. later SUPERSEDED) | 0.045 | 0.052 | **no** |
| bursty variance (null misspecified) | 0.058 | 0.064 | **no** |

The final-data rate is the guarantee the e-process gives. An alarm raised on data that is later revised away is marked SUPERSEDED, but if it was already delivered, users saw it: the 'ever raised' row is the operational false-alarm rate. It has no guarantee: each revision gives the path another chance to cross. Arrival bursts (several increments delivered at once) cannot raise the rate: checking the same path less often can only lower it. Variance bursts can, because the declared null no longer holds.

### Power of the fixed bet (λ = 0.5)

Each increment ~ N(shift, 1); 1,000 streams × 200 peeks, seed 7. The expected log-wealth drift per step is λ·shift − λ²/2, which is ≤ 0 for shift ≤ λ/2 = 0.25 SD. Small shifts still alarm sometimes, but only early and by noise: with zero or negative drift, more data does not push the alarm rate toward 1.

| Shift (SD) | Drift / step | Alarm rate | Median peeks to alarm |
|---|---|---|---|
| 0.1 | -0.0750 | 0.142 | 29.0 |
| 0.2 | -0.0250 | 0.423 | 45.0 |
| 0.25 | +0.0000 | 0.620 | 45.0 |
| 0.3 | +0.0250 | 0.819 | 43.0 |
| 0.5 | +0.1250 | 1.000 | 21.0 |
| 1.0 | +0.3750 | 1.000 | 8.0 |

This was a power limit, not a validity problem. It is why the default changed on 2026-11-22:

### Old fixed bet vs the new default (1,000 streams, seed 7)

| Shift (SD) | Peeks | Old alarm rate | New alarm rate | New median peeks to alarm |
|---|---|---|---|---|
| -0.25 | 200 | 0.000 | 0.813 | 85.0 |
| +0.1 | 200 | 0.142 | 0.172 | 106.0 |
| +0.2 | 200 | 0.423 | 0.592 | 98.0 |
| +0.25 | 200 | 0.620 | 0.829 | 93.0 |
| +0.5 | 200 | 1.000 | 1.000 | 36.0 |
| +1.0 | 200 | 1.000 | 1.000 | 15.0 |
| +0.05 | 1000 | 0.084 | 0.150 | 248.0 |
| +0.1 | 1000 | 0.147 | 0.594 | 384.0 |

A negative shift is invisible to the old one-sided bet. The new bet adapts toward the observed drift, so power keeps growing with data for any shift above floor/2 = 0.025 SD; the prior (λ0 = 0.25, worth 100 observations) keeps early bets stable.

## B2. SRM warning threshold learned from simulated history (CP-019)

Warn (PARTIAL) from the learned threshold, always block (INVALID_DESIGN) below 0.001. p-values from the stdlib oracle; threshold from production `calibrate_srm_warning` (target ≤ 20% false warnings, ≥ 10 experiments in the band); right/wrong read from emulator truth here only.

| History | Learned threshold | Warned (false share) | Blocked (truly broken) | Broken missed | False share if 0.01 kept |
|---|---|---|---|---|---|
| mixed history (90% healthy, 5% gross, 5% subtle) | 0.0021 | 10 (40%) | 108 (104) | 86 | 59% |
| only blatant bugs (95% healthy, 5% gross) | 0.0010 | 0 (–) | 110 (106) | 2 | 92% |
| all healthy | 0.0010 | 0 (–) | 5 (0) | 0 | 100% |
| short history (60 experiments) | 0.0100 | 1 (0%) | 2 (2) | 3 | 0% |

Subtle imbalances (true split 49.25/50.75) are mostly indistinguishable from healthy noise at these sizes: a loose warning tier would mostly fire on healthy experiments, so the calibration keeps the band narrow. Small bands are noisy (estimated 19% vs actual 40% on the mixed history).

## C. Adversarial suite scored against emulator truth

Expectations were written in `evaluation/phase4_cases.py` before results were seen. Truth never enters the core.

| Scenario | Expected behaviour | Observed | Truth | CI covers truth | Met |
|---|---|---|---|---|---|
| pretrend_ok_but_violated | pre-trend diagnostic passes, status stays CONDITIONAL (never certified), parallel trends listed untestable | pretrend=NO_DETECTABLE_DIVERGENCE; status=CONDITIONAL_IDENTIFICATION; estimate=1.054; trend_sensitivity_fragile=False | 0.0 | False | ✓ |
| differential_attrition | DIFFERENTIAL_ATTRITION flagged and status PARTIAL_IDENTIFICATION | status=PARTIAL_IDENTIFICATION; limitations=['DIFFERENTIAL_ATTRITION']; estimate=0.432 | 0.2 | False | ✓ |
| spillover | cannot be detected from these data; SUTVA must be listed as untestable | status=CONDITIONAL_IDENTIFICATION; estimate=0.336 | 0.5 | False | ✓ |
| skewed_clusters | 6/arm unequal → limitations (few + unequal); 3/arm → UNSUPPORTED_CLUSTER_INFERENCE | six_per_arm=OK_WITH_LIMITATIONS; limitations=2; three_per_arm=UNSUPPORTED_CLUSTER_INFERENCE | 0.3 | True | ✓ |
| ate_vs_att | INCOMPARABLE: no blanket RCT override across estimands (C02, I09) | comparable=False; differences=["population: 'all sites' vs 'self-selected sites'", "functional: 'ATE' vs 'ATT'"] | None | None | ✓ |
| unmeasured_confounding | association reported; effect UNIDENTIFIED even with an unrelated adjustment set | status=ASSOCIATION_ONLY; status_with_adjustment=UNIDENTIFIED; association=0.99 | 0.5 | False | ✓ |

**Reading the table:** a CI that misses the truth is expected wherever an untestable assumption is violated by construction (pre-trend, attrition, spillover, confounding). The test is whether the system *says* so: it flags, downgrades, lists the assumption as untestable, or refuses to identify. A wrong estimate presented as certified would be the failure.

## History

- First adversarial run (same expectations): skewed_clusters NOT MET. The method found both problems but packed them into ONE limitation string. Fix: one limitation per reason (experiments.cluster_support). The expectation was not changed.
- Tier 1 T1-P4-01: the cluster and DiD SEs were computed as estimate / t-statistic, which is NaN when the estimate is exactly 0; constant outcomes crashed the unit-level path with ZeroDivisionError; a constant x crashed the association slope. Now one pooled kernel (analytics/kernels.py) computes the SE directly, and zero variance refuses with DEGENERATE_VARIANCE / NO_VARIATION. The differential rows above were re-run after the fix.
- DiD trend sensitivity added (plan §F.5) after the suite's expectations were fixed. On pretrend_ok_but_violated it reports fragile = False: the violation begins at the intervention, which no pre-period extrapolation can detect. The expectation was not changed.
- User decisions 2026-11-22: the default bet became the regularized two-sided plug-in (λ0 0.25, prior strength 100, floor 0.05, cap 1.0); k = 100 was chosen from a sweep over k ∈ {1, 5, 10, 20, 50, 100, 200, ∞} as the best balance of power near 0.25 SD in 200 peeks and power on 0.1 SD over 1,000 peeks. Measured costs, recorded rather than hidden: autocorrelated streams inflate more (ρ 0.8: 0.825 vs 0.458; still refused), variance bursts 0.064 vs 0.058, and the revised 'ever raised' rate 0.052 vs 0.045.
- SRM calibration, first version: a threshold qualified with as few as 1 experiment in its warning band; on the mixed history it estimated 19% false warnings where truth gave 40%, and on the blatant-bugs history 19% vs 50% (2 warnings). Fix: a threshold needs at least 10 past experiments in its band (min_band). Truth was used to find the problem; the target (20%) was not tuned to it.
