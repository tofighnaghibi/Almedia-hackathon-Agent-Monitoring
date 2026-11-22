# Section-by-Section Architecture Audit: Original v2.1 → v3.0 → Reviewed v3.2

**Purpose:** This is the independent-reading audit trail for `causal_core_v3_2_master_blueprint.md` and `causal_core_v3_2_implementation_plan.md`. It checks every original master blueprint section against the generalized v3.0 direction, identifies lost precision and hidden assumptions, and specifies the correction and a measurable test. **It is not evidence that tests have been run.**

**Compared inputs:** `master_implementation_blueprint.md` (689 lines), `causal_core_v3_domain_agnostic_blueprint.md` (283 lines), `causal_core_v3_implementation_plan.md` (68 lines), and the incomplete draft `causal_core_v3_1_master_implementation_blueprint.md` (180 lines). Original diagrams were also used to identify scope and workflow coupling. All cited section numbers below refer to the source document's Markdown headings.

## 1. Executive coverage matrix

| Original section | Original strengths | Failure / hidden assumption to correct | What v3.0 lost or generalized too vaguely | v3.2 resolution | Verification |
|---|---|---|---|---|---|
| **§1 Executive summary & invariants** | Two-part core, functional purity, open-world, observation firewall, evidence scope, staged delivery, safe actions | "Domain agnostic" actually meant metric/anomaly/experiment shaped; causal evidence too dependent on RCT | High-level invariants no longer operationally traced to failures | Blueprint §0 explicit I01–I18, F01–F12 and exclusions; responsibility boundaries §1 | G01/G02/G11/G13/G17 |
| **§2.1 Dependency DAG** | Acyclic modular monolith, no god orchestrator, clean pure/statistical separation | `MetricSemantics` and detector structured as universal entrypoint | Some package relationships underspecified | §1 explicit DAG and table with both allowed and forbidden calls, P0/P1 tree | G01 + import graph snapshot |
| **§2.2 Component topology** | Ports and adapters, read-only graph views, scheduler/ledger/feedback isolation | Inputs assume metric, study and segment panels; experiment port specialized by name | V3 had abstractions but no exact runtime data paths | §1 and §7 distinct source/task paths; three-view Excalidraw adds top-level, interactions and runtime entrypoints | G13/G14, diagram-vs-contract review |
| **§2.3 Repository layout** | Concrete testable Python package and optional P1 dependencies | Source-specific module name under core; location of admission and pure validation unclear | V3 had skeleton but underdefined shell storage ownership | §1.2 explicit packages/files, one owner per mutation, optional modules marked | G01 and compile/import tests |
| **§3.1 Canonical abstractions** | Metric formulas, entities, observations, intervention/study, observation mechanisms, lineage, forecast | `MetricDefinition`, per-bucket stats, `ExperimentRecord` effectively universal; RCT-only edge certification | V3 introduced variable but field semantics/migration lacked specificity | §2.1 exact shape table and pseudocode; §§2.3/4.5 effect vs structure, study design as distinct data | G02/G03/G10/G11/G12 |
| **§3.2 v1.0→v1.1 delta** | Edge statuses, provenance ancestry, E-process state, query budget, partial investigation results | Legacy defaults can assert false precision/authority | V3 merely promised compatibility | §2.4 named mapping including `bucket_grain/max_buckets`, `LEGACY_UNSCOPED`, historical certificates and negative paths | G03/G07/G08 |
| **§3.3 v1.2 evidence contracts** | `EnvironmentScope`, work/checkpoint/revision, mechanisms and experiment receipt | Evidence class is too coarse for causal authority; experiment privileged | V3 lacked complete source/claim/time distinctions | §§2.1–2.3 split source/provenance, payload shape, estimand, claim kind, clock semantics; method-specific audit | G06/G07/G11 |
| **§3.4 Persistent state ownership** | Detailed SQLite ledger, graph, E-state, stats cache, jobs, forecasts | Claimed exactly-once-visible without explicit sink idempotency; ambiguous graph+ledger atomicity | V3 summary compressed operational rules | §5.1 ownership table, atomic graph commit, outbox and sink caveat; §5.2 leases/budget persistence, §5.4 cache keys | G05/G15/G16 |
| **§4.1 13 typed edges / views** | Crucial semantic distinctions incl. UNKNOWN, OBSERVATION_EFFECT, temporal feedback | `CAUSALLY_IDENTIFIED` used for total effect as if it established structural link | V3 generalized views but risked losing edge-specific semantics | §3.1 full 13-edge table; §3.4 three views; effect artifact vs identified structural edge split | G04/G11 |
| **§4.2 Bitemporal store** | Pinning, non-destructive revision, quarantine, in-flight consistency | O(1) / <2ms phrased like unqualified guarantee; missing edge blanket abstention | V3 lacked explicit asymptotic/storage caveats | §3.2 O(1) handle vs bounded traversal, cache and write recovery; snapshot limits and cutoffs | G04/G05/G16 |
| **§5.1 Responsibility mapping** | Clear controllers, pure kernel, optional prediction, policy, feedback, scheduling | Planner task implicitly investigation/anomaly; `StatsCausalEngine` methods bundled | Generic v3 roles were shorter, state ownership fuzzy | §1.1 exhaustive responsibility/forbidden column; §§4.2–4.4 method interfaces and FSM | G01/G10/G13 |
| **§5.2 Five-stage pipeline** | DQ, temporal dedup, decomposition, experiment, identifiability, safe result | Forced universal flow, RCT dominance, SRM/assignment exposure alignment, pretrend "mandatory" as proof | V3 changed to question-first but lost per-method failure rules | §§4.1–4.5 question taxonomy, pure FSM, P0 method matrix, conditional effect assessment and method-specific challenge | G08–G13 |
| **§5.3 VoI** | Cost-bounded active evidence, lineage novelty, next diagnostics | Numerator/proxy marketed as true expected value; source capabilities fixed | V3 generic VoI without executable request contract | §§4.6–4.7 typed manifests, request budgets, explicit heuristic ranking and unsupported | G14 |
| **§5.4 Tier scheduling** | Detailed T0 <2s, T1 <10s, T2 <60s, T3 minutes+, T4 event-driven, checkpoints | Any claim of hard cutoffs without isolation/preemption model | V3 lacked full worker/lease/saturation details | §§5.2–5.4 exact job fields, transactional outbox, query-cost accounting, p50/p95/p99 test suite | G15/G16 |
| **§5.5 Sparse model** | Scope of versioned local predictions, CRN, uncertainty, abstention | Prediction might be mistaken for identified effect; random-state drift when interventions change counts | V3 shortened validation details | §6.3 seeded per entity/event/mechanism, explicit regime/validation/no-invention and no causal promotion | G17 |
| **§5.6 Intervention permissions** | Emulator-only external write, no production, scoping, approvals and receipts | Relying on `environment_kind` string is not adequate; test types overly experiment-centered | V3 generalized `TestExecutionPort` but did not specify trusted attestation | §6.2 separate proposal vs execution, attestations, credentials, approval binding, idempotency | G17 |
| **§6.1 Monitoring + investigation sequence** | Clear T0/T1/T2 stages, DQ, methods, revisions | Monitoring assumed entrance for every task | V3 had only high-level prose | §7.2 monitored or explicit change; §7.1 no-anomaly effect; runtime diagram separately distinguishes flows | G13/G16 |
| **§6.2 Experiment reconciliation** | Covariance fallback, SRM and randomized effects | RCT always "wins" even across estimands; marginal significance confused with surrogacy | V3 lacked detailed covariance negative path | §§4.4–4.5, §7.3: analysis units, ITT, clustering, marginal-vs-joint distinction | G11/G12 |
| **§6.3 Automatic onboarding** | Eight steps and quarantine | All new nodes pass metric formula identity and start monitoring | V3 lost exact steps | §3.3 full eight steps with nonmetric N/A path and subscriber rule | G05/G02 |
| **§6.4 Feedback revision** | Synthetic dedup, noncausal post hoc, bitemporal updates | Distinct generator seeds mistaken for production independence | V3 short feedback descriptions | §6.1 origin/dependence, §7.6 feedback + scope, versioning and causal audit | G07/G15/G17 |
| **§6.5 Scheduling sequence** | Monotone incident revisions, T2 checkpointing | Exactly-once visible not possible without sink cooperation | V3 omitted outbox implementation | §§5.1–5.2 transactional outbox and idempotent sink caveat; §7 sequences | G15 |
| **§6.6 What-if/test/learning** | Emulator oracle outside, prediction not evidence | Everything called “experiment”; hidden test target assumptions | V3 renamed port but could still be an A/B-only action schema | §§6.2–6.3, §7.5 typed sandbox test recipes, no production path | G17/G18 |
| **§7 TabPFN optional** | Lazy imports, hardware and latency caps, pure fallback | Specific model/version/footprint availability can change; ML must not certify causality | V3 only said optional | §6.4 explicit use, unavailable model fallback, measured caps, stored outputs | G16/G17 (P1) |
| **§8 Implementation plan** | Six phases, phase-specific exit gates, P0/P1 scope | No approval checkpoint before production-critical changes; v2.1 core not genuinely general | v3 plan only 68 lines, insufficient build tasks/owners | Companion implementation plan: 9 phases with artifacts, tests, rollback and approvals | all gates |
| **§9 Twelve merge gates** | Strong statistical, safety, replay and workload tests | Did not check nonmetric entrypoints, effect-vs-structure, clustered studies, typed evidence | v3 had 14 gates but omitted some original details | §8 eighteen gates retain original G1–G12 content while adding semantic neutrality, design-aware identification and provenance timing | G01–G18 |
| **§10 Demo portfolio** | Differentiated causal decisions vs dashboard baseline | Too marketplace-specific, some success narratives not falsifiable | v3 expanded but lacked benchmark denominator/protocol | §9 twelve scenarios across marketplace, study and sensor; independent oracle and method-matched baseline | G18 |
| **§11 Non-goals/ADRs** | Locality, no second emulator, no production, no mandatory LLM | Risk of calling universal solver with undefined identification scope | V3 less implementation tradeoff detail | §§10–11 twelve ADRs with explicit costs and non-goals | architecture review |

## 2. Specific statistical and causal corrections requiring independent signoff

**C01 — Treatment-effect ≠ identified causal mechanism.** `Z` randomized, `Y` outcome, `D` observed exposure. A randomized ITT `Z → Y` estimate is not automatically an identified exposure effect `D → Y`, nor an identified mediation pathway. Test with randomized noncompliance and latent mediation. Add a typed `EffectClaim` when effect but not structural mechanism is identified.

**C02 — RCT and observational effect comparisons require the same estimand.** Differences in target population, treatment version, adherence, analysis time, or outcome measurement can explain disagreement. Never implement `if study.is_randomized: override(observational)`. Tests use matched and deliberately mismatched estimands.

**C03 — Sequential e-process validity is not implied by repeated observations.** Need specific null, adapted/predictable betting strategy, dependence specification, multiple testing budget, non-duplicated information and careful treatment of late revised buckets. Validate both ideal null and correlated/revision-aware null simulations; reject or disable method when formal requirements fail.

**C04 — DiD parallel trends cannot be proven by a pretrend p-value.** Record the assumption as untestable in general, diagnose plausibility and conduct sensitivity/placebo checks. Cluster/serial correlation and staggered intervention timing may require a different estimator. Return conditional effects, not unconditional proof.

**C05 — Missing covariance is not zero.** Holm gives conservative marginal familywise-error control when its conditions hold; it does not reconstruct covariance or certify mediation/surrogate behavior. Negative tests must distinguish the marginal conclusion from unavailable joint inference.

**C06 — Mechanism invariance/RCD-lite is a pruning heuristic.** Conditional distribution stability may fail under selection, latent causes or changing observation regimes; treat as candidate, not identified cause. Hidden-confounder and missing-parent cases mandatory.

**C07 — Open-world graph is neither “no missing edges” nor “all effects unknowable.”** For every identification strategy, audit only relevant unobserved arrows and assumptions; preserve uncertainty, sensitivity and abstention. Never infer absence from unrecorded adjacency.

**C08 — Simulator correctness vs external causal validity.** A sandbox oracle can test whether the engine recovers **programmed** effects. It does not establish that mechanisms transferred from other marketplaces/sensors are real-world valid. Record simulator fidelity and causal inference performance separately; benchmark oracle never enters core.

**C09 — Source time leakage.** `observed_at` and `available_at` differ. A delayed real-time observation must not appear in earlier replay. Use source data cutoff and graph belief version together; warn if historical source availability is inferred rather than attested.

**C10 — Scope-aware authorization.** Action gate checks trusted environment/credential target rather than accepting user-supplied `environment_kind=emulator`. P1 never has production write credentials.

## 3. No-regression checklist for implementers

- [ ] Full old v2.1 metric formula corpus still parses and runs under `MetricDefinition` subtype.
- [ ] All 13 graph edge kinds and three read-only views retain semantics; no observations directly mutate the graph.
- [ ] Historic v1.0/v1.1/v1.2 migrations record source bytes and do not fabricate sample independence, covariance or causal authority.
- [ ] E-process `log_wealth`, `peak_log_wealth`, seen roots, alpha budget, revision handling and monitoring false-positive tests retained.
- [ ] Kitagawa, seasonal screening, RCD-lite, DiD, randomized study and Holm algorithms have identical validated behavior on golden fixtures; changed invalid behavior has signoff.
- [ ] Graph admission has eight steps, nonmetric shortcut, atomicity/quarantine and historical reconstruction.
- [ ] SQLite single writer, WAL/backpressure, outbox, durable leases and retry accounting tested under kill/restart.
- [ ] SLA measurements use complete-record boundary, queue contention and p50/p95/p99; T0/T1 isolation verified.
- [ ] Mechanism P1 rollouts do not become causal evidence; missing/out-of-regime mechanisms abstain.
- [ ] There is **no** production write path and external sandbox receipts remain scoped.
- [ ] A monitor-free effect question and experiment-free sensor problem execute end-to-end.
- [ ] Independent evaluator, rather than the system under test, controls hidden mechanism truth and causal outcome scoring.

## 4. Review disposition

**Architecture:** approved as a *proposed implementation target*, subject to explicit user checkpoint approvals and successful independent verification.  
**Not approved:** production changes, unconditional causal certification, causal truth inferred from synthetic-only validation, claimed performance without benchmarks, or unsupported source/transport assumptions.  
**Next decision:** adopt canonical schema diff and three vertical-slice fixtures before coding generalized planner. Keep the original blueprint and v3 files as immutable baseline artifacts to support differential tests.

## 5. Final hidden-assumption check made during v3.2 review

A second pass found that even the new `MetricDefinition` and `StudyDesign` contracts could accidentally retain universal assumptions if their subtype fields were indiscriminately mandatory. The final v3.2 schema therefore makes metric aggregation/population/formula/time fields **conditional on the method requiring them**, and uses design-specific subtypes for randomized/quasi/observational/descriptive studies. Likewise, static or timeless observations may have unknown event/availability time: leakage-sensitive replay requires **known** `available_at`, but other tasks must not invent a clock. `EstimandSpec` describes causal effect targets only; other question types use separate target contracts. These are architecture decisions, not cosmetic optional fields. Add to G02/G06/G10 and corresponding migration/port conformance fixtures.
