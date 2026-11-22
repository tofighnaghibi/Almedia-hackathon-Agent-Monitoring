# Roadmap Checkpoint C — Causal Methods / Claim Semantics (Phase 4)

**Verdict: `APPROVED_BY_USER` 2026-11-22** (was `PROVISIONAL_PASS_PENDING_HUMAN_SIGNOFF`)

Made unattended; approved by the user on 2026-11-22 with four changes (below). The ceiling was then raised to Phase 6 (CP-001).

**Plan §K row C:**
- **Before changing:** identification/certificate output policy.
- **Evidence required:** independent RCT/DiD/e-process tests, effect-vs-edge, negative confounding and
  cluster examples.
- **Decision requested:** approve the claim semantics.

## What "claim semantics" means here, in one table

| What the system can say | When | Never |
|---|---|---|
| `CONDITIONAL_IDENTIFICATION` + `EffectClaim` | design identifies the requested estimand; untestable assumptions listed (including declared transport assumptions, 2026-11-22) | an edge, a mechanism, or "certified" |
| `PARTIAL_IDENTIFICATION` + `EffectClaim` | identified but weakened: SRM in the warning band, differential attrition, pre-trend divergence, transport with no declared assumptions, any simulator → production transport | presented as conditional |
| `ASSOCIATION_ONLY` | association method or descriptive design | causal language |
| `UNIDENTIFIED` (no claim) | the estimand isn't identified by the design: exposure effect from ITT, ATE from DiD, open confounding | a number labelled as the effect |
| `INVALID_DESIGN` (no claim) | SRM, clusters in two arms | an estimate |
| `INSUFFICIENT_EVIDENCE` (no claim) | any other typed refusal (too few clusters, zero variance, staggered timing …) | a silent fallback |

The `EffectClaim` is an artifact attached to two nodes. Graph admission (CP-007) refuses it as a structural edge.

## Evidence required by §K, mapped

| Required evidence | Where | Result |
|---|---|---|
| **Independent RCT tests** | `DifferentialVsPhase0Oracle`: fixture 04 (ITT, SE, SRM p), 05 (cluster estimate/SE/p), 11 (ITT, CACE, CACE SE) against the stdlib oracle; `StudyValidityG12`; `IdentificationG11` noncompliance; `DegenerateInputsTier1` | PASS: max abs diff 2.2e-7 (fixture 04 SE, n vs n−1 variance; explained in the report); every other row ≤ 5e-13 |
| **Independent DiD tests** | fixture 06 (ATT, SE, pre-trend p) against the oracle; `test_did_identifies_att_not_ate…`; `test_did_trend_sensitivity…` | PASS: ≤ 5e-13; ATE request → UNIDENTIFIED; fragile estimates flagged without a status change |
| **Independent e-process tests** | fixture 08 log-wealth against the oracle; `SequentialG08` (4); report §B (1,000 × 200 null streams; correlated, revised, bursty; power) | PASS for the declared iid null (0.042; revised final data 0.043). Documented inflation: AR(1) → refused; variance bursts 0.058 (open item 2) |
| **Effect-vs-edge** | Phase 2 `EffectVsEdgeG11` (CP-007); `test_randomized_itt_conditional_and_claim_is_not_an_edge`; `effect_claim()` returns `None` unless CONDITIONAL/PARTIAL | PASS |
| **Negative confounding example** | adversarial `unmeasured_confounding` (association 0.99, truth 0.5 → ASSOCIATION_ONLY; with an unrelated adjustment set → UNIDENTIFIED); fixture 07 slope against the oracle; `_confounded_view()` latent edge | PASS |
| **Cluster examples** | fixture 05 against the oracle; adversarial `skewed_clusters` (6/arm unequal → two limitations; 3/arm → refused); `test_cluster_assignment_must_be_consistent_and_large_enough` | PASS |

Pre-registered adversarial suite (expectations written before results): **6/6 met**. A missed truth is acceptable
only where the system flagged, downgraded, listed the untestable assumption, or refused. Each of the four
CIs that miss the truth is in that category.

## Stop conditions (plan §F)

| Stop condition | Observed |
|---|---|
| a method silently reshapes data | none. Every failed premise returns a declared code (`refused()` raises otherwise). DiD reports dropped units in its limitations. Zero denominators and zero variance refuse |
| a method claims causality from significance alone | none. Status comes from design × estimand, never from a p-value: an association with r = 0.99 stays ASSOCIATION_ONLY / UNIDENTIFIED |
| experiment evidence is assumed to dominate a different estimand | none. `compare_estimands` returns incomparable for ATE (all sites) vs ATT (self-selected sites) (`EstimandComparisonC02`, adversarial `ate_vs_att`) |
| simulated truth is accepted as validation | none. Truth is read only in `evaluation/`. `PurityG01Phase4` bans `emulator`/`evaluation` imports in analytics. An emulator-to-production estimand is capped at PARTIAL (`test_transport_caps_at_partial`) |

## Gates in Phase 4 scope (plan §M: G08–G12, G14)

| Gate | Verdict (provisional) | Note |
|---|---|---|
| G08 | **PASS with documented limits** | iid null within α, revised final data within α; correlated streams refused; variance bursts inflate (0.058); fixed-bet power weak below 0.25 SD |
| G09 | PASS | Kitagawa: Simpson, zero denominators, incomplete/undeclared partitions; identity residual ~1e-18. The unit/grain/pop part was done in Phase 2 |
| G10 | PASS | event→Kitagawa, assignment→DiD, ATE→RCT method, observational→RCT method, missing null, budget; trace persisted |
| G11 | PASS | ITT conditional; noncompliance blocks the exposure leap; DiD conditional; confounding negative; no auto-certified structure |
| G12 | PASS | SRM at the assignment unit, cluster mismatch, attrition, spillover (SUTVA always listed), Holm and missing covariance (no joint/surrogate claim) |
| G14 | PASS for the method part | proxy ranking, novelty, unknown cost penalised, privacy veto, gaps. The planner loop and held-out case are Phase 5/6 |

## Decisions to review

| Decision | Topic | Risk |
|---|---|---|
| CP-017 | Registry, 12-method catalog, NumPy/SciPy as core dependencies | HIGH |
| CP-018 | Sequential: fixed bet, dependence refusal, alpha split (**measured power and scale limits**) | HIGH |
| CP-019 | Randomized: SRM 0.001, attrition flag, cluster policy, CACE as IV, SUTVA | HIGH |
| CP-020 | DiD: unit-collapsed estimator; trend sensitivity informs only | HIGH |
| CP-021 | Identification auditor rules, C02 comparability, transport cap | **BLOCKER-class subject** |
| CP-022 | VoI proxy (0.5 unknown-cost penalty is arbitrary) | MEDIUM |

Tier 1 fixes in this phase: T1-P4-01 (zero-variance crash / NaN SE), T1-P4-02 (registry descriptor
conformance; 12th method registered), T1-P4-03 (SUTVA always listed). See `CHECKPOINTS.md`.

## Open items (don't block C; recorded honestly)

1. **Fixed-bet power (CP-018).** Alarm rate 0.142 at a 0.1 SD shift and 0.423 at 0.2 SD, and it does not
   grow with data. Upgrade: a mixture or adaptive bet.
2. **Scale misspecification (CP-018).** Variance bursts give 0.058 > α. Upgrade: a robust bet; or the
   null must state its scale and the adapter must attest it.
3. **Superseded alarms (CP-015/018).** The ever-raised rate has no guarantee. A delivery policy (hold
   until maturity, or a retraction notice) belongs to Phase 5's output gate.
4. **Status reasons stored in `assumptions_checked` (CP-021).** Needs an additive `status_reasons` field
   and a schema freeze. Owner: Phase 5.
5. **P1 methods deferred by design:** Lee bounds for attrition, staggered DiD, full Rambachan–Roth
   (M > 0), cluster-level CACE, complete ID algorithm.
6. **Carried from B:** T0 writer priority (CP-012), end-to-end G16, re-verification scheduling. All Phase 5.

## User review — 2026-11-22 (AFTER)

Approved, with four changes. Full record: `DECISION_LOG.md` → *User review — 2026-11-22*.

| Decision | Change | Measured |
|---|---|---|
| CP-018 | regularized two-sided bet, λ0 0.25, prior strength 100 | power 0.25 SD: 0.62 → 0.83; drops now caught (0.81); iid false alarms 0.036 |
| CP-019 | SRM warning tier from 0.01 (learnable), hard floor 0.001 | simulated history: 0.01 would be 59% false warnings; learned 0.0021 |
| CP-021 | declared transport assumptions keep CONDITIONAL; simulator → production stays PARTIAL | tests |
| CP-022 | geometric prior on unknown latency/cost, mean learned from known options | tests |

Open items 1 and 2 above, AFTER: item 1 (power) is addressed by the new bet. Item 2 (scale misspecification)
remains open and is slightly worse (variance bursts 0.064). New: SRM calibration on real history; transport
sensitivity checks.
