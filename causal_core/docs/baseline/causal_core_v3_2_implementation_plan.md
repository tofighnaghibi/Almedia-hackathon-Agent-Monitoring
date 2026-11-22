# Causal Core v3.2 — Detailed Engineering Implementation & Verification Plan

**Companion / normative architecture:** `causal_core_v3_2_master_blueprint.md` and its 18 merge gates.  
**Comparison evidence:** `causal_core_v3_2_section_comparison.md`.  
**Baseline:** v2.1 689-line master blueprint, v3.0 variable-first rewrite, unfinished v3.1 draft.  
**Purpose:** actionable implementation order, owners, interface changes, verification, performance targets, tradeoffs, rollback and required approval. **This document describes future work; no tests are claimed to have run.**

## A. Release strategy and work rules

**P0** preserves valid v2.1 metric functionality and delivers three end-to-end vertical slices: metric/change investigation; an explicit randomized/quasi-estimand request without a monitor; and a nonmetric event/state hypothesis task. P0 is a read-only core with durable output. **P1** adds bounded structural what-if models and authorized nonproduction tests with delayed feedback; neural modules remain optional.

Execution order matters: characterize baseline → typed contracts → graph admission → persistence/scheduler → method registry + identification → task-first planner and ports → vertical slices → benchmarks and independent negative tests → P1. Do not rewrite algorithms that already pass reference fixtures just because the interface has been generalized. No large DI framework, generic source DSL, Kafka or mandatory LLM.

**Change discipline:** At every checkpoint produce (1) exact proposed diff and responsibility owner; (2) falsifiable expected behavior and prior baseline; (3) independent tests including adversarial negative; (4) measured latency, memory and output comparison; (5) explicit unresolved assumptions; (6) reviewer approval before touching production-critical behavior. Automatic implementation may proceed locally; code touching production deployments or actuation requires explicit user approval.

### A.1 Workstream and ownership model

| Workstream | Primary owner role | Artifacts to review |
|---|---|---|
| Contracts/migration | Core engineer + independent schema reviewer | Versioned Pydantic unions, migration fixtures, negative coercion tests |
| Graph/semantics | Graph engineer + causal reviewer | 13-edge rules, bitemporal store, admission, snapshot tests |
| Statistical methods | Stats/causal engineer + independent statistician/test author | Capability specs, method math, identification audits, simulation validation |
| Storage/runtime | Platform engineer + fault-injection reviewer | SQLite schema, outbox, scheduler, budgets, load profiles |
| Input contract integration | Adapter engineer + data provenance reviewer | EvidenceAccessPort and compatible legacy adapter, source clock attestations |
| Security/test policy P1 | Security reviewer + sandbox maintainer | Environment authorization, credentials boundary, negative authorization tests |
| Benchmark/evaluation | Independent evaluation owner | Harness separated from oracle/truth data, held-out scenarios, report |

Ownership defines responsibilities, **not** a requirement for seven separate people. For a small team, the same implementer may wear multiple roles but **independent checks must not reuse the production inference implementation** as its only oracle.

## B. Phase 0 — Freeze original behavior and adversarial requirements [P0]

**Prerequisites:** original v2.1 blueprint/source repository or reference implementation, v3.0 artifacts and known contract fixtures. If code is not present, record “specification comparison only”; do not pretend golden output captures exist.

**Tasks**
1. Collect exact original contract schema (v1.0/1.1/1.2), port signatures, graph-edge enums, evidence classes, SQL schema, source adapters and test results. Hash snapshots and store read-only fixture corpus.
2. Build matrix of every method's actual required shapes: SAVI, seasonal, Kitagawa, RCD-lite, RCT+SRM, Holm/covariance, DiD, VoI, optional SDID/Shapley/TabPFN. Record current behavior on missing/degenerate inputs.
3. Freeze deterministic reference cases: ratio identity; Simpson decomposition; DQ lag; a valid randomized assignment; cluster-randomized invalid IID SE; a conditional DiD; known unidentifiable confounder; revised bucket; one migrated old record; unauthorized action. Mark expected *corrected* differences separately.
4. Capture baseline machine, Python version, dependencies, memory, T0/T1/T2 p50/p95/p99 under defined conditions **if runnable**. Note missing benchmarks instead of inventing results.
5. Classify each historical rule as KEEP / FIX / SPECIALIZE / DEFER. Use the separate section audit as initial inventory, but verify against actual implementation.

**Deliverables:** `tests/fixtures/v21/` + hashes; `migration_inventory.md`, `methods_preconditions.csv`, `baseline_benchmarks.json` (with missing flags), and architecture decision review.

**Exit gate:** User approves schema diff, valid-legacy behavior preservation policy and correction list. Dependencies for next phases: G01 baseline, G03 fixtures. **Stop if** observed code deviates materially from given v2.1 blueprint; resolve discrepancy first.

## C. Phase 1 — Canonical schema v2.0 + compatibility [P0]

**Tasks**
1. Implement `contracts/variables.py`: tagged union of `MetricDefinition`, `StateVariableDefinition`, `EventVariableDefinition`, `CategoricalVariableDefinition`. Field requirements per subtype, finite value schemas, units only if meaningful; aggregation and population semantics are conditional for metrics and required only by relevant calculations.
2. Implement typed observation payloads `Scalar`, `Categorical`, `AggregateStats`, `TimeSeries`, `Panel`, `EventBatch`, `AssignmentOutcomes`, `StateTrajectory`; shape-specific validators and exhaustive serialization tests.
3. Add `EnvironmentScope`, `ProvenanceLineage` ancestry DAG, `SourceAttestation`, observation mechanisms and precise **optional** `observed_at/available_at/as_of/ingested_at` clocks with known/estimated/unknown qualifiers; require a known availability clock for leakage-safe historical replay, not for timeless observations. Make legacy scope uncertain (`LEGACY_UNSCOPED`) rather than default production.
4. Add `StudyDesign`, `EstimandSpec`, `ReasoningTask`, `EffectClaim`, `IdentificationAssessment`, `EvidenceGap`, `CapabilityRequest/Response`, `TaskRevision`, `WorkItem`, `Checkpoint`. Do not require assignment fields except for methods/designs that need them.
5. Implement one-way explicit schema migrations for v1.0/v1.1/v1.2; preserve original serialized bytes/hash and migration receipt. Backward adapter supports the six original `EvidenceQueryPort` methods with `bucket_grain` and max-bucket budget semantics.
6. Reject malformed event payloads, missing scope, arbitrary outcome denominators, false sample independence or unknown coercions; never guess covariance, randomization units or historical availability.

**Public API snapshot:** `parse_definition`, `parse_observation`, `parse_study_design`, `parse_reasoning_task`, `migrate_legacy_record`, `assess_contract_compatibility`. No external I/O in these functions. `schema_version='2.0'` mandatory for native outputs.

**Deliverables:** frozen schemas; exhaustive round-trip/property tests; `SCHEMA_V2_MIGRATION.md`; compatibility fixtures. **Gate:** G02, G03, G06, G07. **Stop condition:** a nonmetric state/event fixture requires a numerator, denominator, window, metric formula, experiment ID or alert.

## D. Phase 2 — Typed graph, bitemporal store, and admission [P0]

**Tasks**
1. Retain all 13 edge kinds with explicit allowed node/layer/target combinations. Implement `StructuralView`, `TemporalView`, `CausalEvidenceView` against pinned version/valid-time; `UNKNOWN` on absent adjacency.
2. Separate `EffectClaim` artifact/node from structural identified edge. A certificate for total ITT effect cannot auto-admit mediation/actual-exposure edge. Add explicit certificate-to-estimand identity validator.
3. Implement immutable bitemporal node/edge deltas and version-indexed adjacency. First benchmark implementation options: copy-on-write adjacency+version tables vs persistent indexes. **O(1) pin handle only**, traversal bounded by caps; no fabricated latency guarantee.
4. Implement eight-step `GraphAdmissionCoordinator`: identity and layer checks, metric-only AST and empirical checks, conditional source query, quarantine, atomic version commit, and subscriber-only monitoring notification.
5. Handle historical source definition drift, changed population scope, late observation, invalid cycle, positive-lag feedback and uncertain entity matches with explicit outcomes.
6. Implement SQLite graph-delta transaction + index rebuild after crash, and snapshot consistency across task resume.

**Deliverables:** graph and views API, edge status validators, migration shim `TypedMetricGraph` if needed, replay + quarantine test corpus, snapshot performance profile.

**Gates:** G04, G05, G09 and G11 edge-vs-effect. **Performance tests:** 5k-node graph at specified density; cost of pin vs bounded hop traversal; memory by retained version count, under sustained updates. **Stop:** any read-path write, unbounded traversal, or actor that silently converts effect estimates into structural links.

## E. Phase 3 — Single-writer ledger, task engine, and state ownership [P0]

**Tasks**
1. Create `schema.sql` for evidence/lineage, variable/graph versions, E-process states, results, method decision traces, task/incident revisions, jobs/checkpoints, request ledger and outbox. Include unique constraints and foreign keys.
2. Implement a serialized SQLite WAL writer with `BEGIN IMMEDIATE` and bounded write queue. Atomically commit graph version + delta + corresponding evidence/audit. Measure WAL contention, disk usage and long reader effects.
3. Build durable `PriorityScheduler`: atomic lease owner/expiry, retries, backoff, max attempts, cancellation, cooperative preemption, stale snapshot detection, reserved T0/T1 capacity.
4. Implement `QueryBudget` and `ExecutionBudget`; record source request issue **before** dispatch; retry safely without double spend; cache by scope+cut+versions+content fingerprints.
5. Build task/incident revision outbox; exactly-once-visible **when sink supports idempotency**, at-least-once otherwise. Persist payload before publishing; reconcile after crash.
6. Add EProcessState storage (`log_wealth`, peak, alpha, seen roots/buckets) plus revision-aware recomputation; prohibit double-consumption of late-revised buckets.
7. Record method applicability decisions and their failure reasons, not just selected successful outputs. Persist model outputs separately from evidence.

**Deliverables:** transactional schema, migration and test DB scripts, scheduler, ledger/outbox, crash replay tests, logging/metrics definitions.

**Gates:** G05, G07, G08, G15, G16. **Fault injection:** kill after query dispatch, after result persistence, before outbound acknowledgement, mid-graph-commit, during lease renewal; restart must not duplicate visible action or reset budgets. **Stop:** graph memory and DB disagree after restart, poisoned queue stalls T0, or outputs silently reappear with new identity.

## F. Phase 4 — Pure capability registry and conditional causal identification [P0]

**Tasks**
1. Implement static `CapabilitySpec`/`ApplicabilityAssessment` and simple registry. Matching considers task question, input payload kind, metadata, target estimand, source regime and method budget, **not** defaulted task profile.
2. Wrap existing exact metric algebra, DQ/observation maturity, seasonal screening, SAVI/e-process, Kitagawa, association, RCD-lite, randomized effects, Holm, DiD, identifiability, and VoI. Each method exports a pure `evaluate(input: typed) -> typed result` and explicit refusal codes.
3. Sequential method: document null and predictable betting rule; empirical Type I tests across null streams and realistic dependence; handle revisions and multiple-testing budget. Disable guarantees when assumptions fail.
4. RCT method: assignment integrity and SRM at correct unit, ITT vs actual treatment, attrition, contamination, spillovers and cluster-robust/appropriate variance; if cluster support unavailable, refuse rather than report naive SE. Record familywise hypothesis context.
5. DiD method: estimand/treatment/control/panel time/anticipation/parallel trends sensitivity, serial correlation, treatment timing; pretrend test is diagnostic, not proof. Return *conditional* identification, never automatic structural mechanism edge.
6. Kitagawa and RCD-lite: complete or bounded partitions, residual/truncation/OTHER, noncausal nature of mixture attribution and invariance pruning.
7. `IdentificationAuditor`: validate requested estimand, design, transport and method-specific assumptions; states conditional/partial/associational/unidentified/invalid. Distinct `EffectClaim` vs `GraphEdge` admission.
8. Run independent reference calculations outside production kernel (e.g., hand-derived small examples or a separately implemented numeric check), including anti-pattern cases missing covariance, skewed clusters and unmeasured confounding.

**Deliverables:** typed method module and specs, assumption registry, method rejection catalog, independent statistics report, reproducible seeds.

**Gates:** G08–G12, G14 method selection prerequisites. **Stop:** method silently reshapes data, claims causality from significance alone, assumes experiment evidence dominates different estimands, or accepts simulated truth as validation.

## G. Phase 5 — Question-first FSM, typed evidence acquisition, and safety output [P0]

**Tasks**
1. Implement immutable planner FSM transitions `FRAME/AUDIT_INPUTS/SELECT_CAPABILITIES/ACQUIRE/EVALUATE/CHALLENGE/REPORT`; optional route/skips but no domain strings or fixed “anomaly first” assumption.
2. `TaskCoordinator` accepts an explicit question, scheduled task, monitor-produced trigger, or study completion. `MonitorDetector` stays an optional producer. Default source and evidence scope preserved.
3. Implement `EvidenceAccessPort` finite typed union and provider manifest. Requests carry budget, as-of/data-cut, source scope, shape, rows/bytes/grain limits, request ID, privacy class and deadline; responses include lineage, completeness, truncation, unsupported reason and available_at.
4. Build `EvidenceAcquisitionPlanner` with bounded cost/novelty/discrimination proxy score. Distinguish estimated proxy value from calibrated VoI; unavailable high-value tests become `EvidenceGap` rather than hanging dispatch.
5. `CHALLENGE` stage: alternative hypotheses, relevant unknown/latent paths, observation artifacts, negative controls, external validity and conflicting data; do not rely on LLM generated hypotheses as truth.
6. `ActionSafetyPolicyGate` makes read-only report vs test proposal distinct; output serializer includes evidence IDs, scope/versions, method audit and explicit abstention.
7. Enable P0 T0/T1/T2 progressive delivery with checkpointed findings. Every failure path emits a typed reason: missing evidence, method refusal, DQ fault, source timeout, quota, statistical invalidity, stale snapshot or computation error.

**Deliverables:** running end-to-end CLI/library flow for `ReasoningTask`, typed port conformance tests, optional monitor producer, JSON result schema, example traces.

**Gates:** G01, G10, G13, G14, G15, G16. **Stop:** any explicit effect question has to create a fake incident or any sensor task needs fake metric fields.

## H. Phase 6 — Three end-to-end P0 slices + evaluation + performance [P0]

| Slice | Canonical input | Expected method candidates | Independent acceptance |
|---|---|---|---|
| **S1 marketplace monitoring** | Ratio metric, entities/population, time buckets, reporting delay and segment breakdown | DQ, seasonal or SAVI if valid, Kitagawa, lag/association, optional DiD | Matches valid v2.1 golden math; novel entity labels, correct DQ vs domain separation, budgeted useful partial result |
| **S2 explicit study/effect** | Exact estimand, randomized assignment and outcome, with optional clusters; **no anomaly** | Design audit, RCT/cluster inference, Holm/cov checks, identification | ITT identified only when design supports; exposure/mediator edges not fabricated; monitor disabled |
| **S3 nonmetric dynamics** | Event/state sequence with lagged mechanisms and observation quality | Association/time-lag/invariance if applicable, graph-based hypothesis/audit | Runs without metrics or experiment profile; instantaneous equilibrium unsupported stated honestly |

**Tasks**
1. Build three distinct adapters that translate sources to *the same core contracts*; do not add domain branches inside planner.
2. Implement CLI outputs and trace view showing method applicability and assumptions. Record one success, one error and one abstention per slice.
3. Build independent evaluation harness: ground truth strictly outside core/ports/proposer, hidden mechanism families, opaque IDs, negative controls and target-estimand checks.
4. Compare to v2.1 where valid and to two baselines: fixed dashboard/SQL monitoring under equal budgets and method-matched analytics for direct questions.
5. Benchmark designated laptop across 100 concurrent tasks, 5k-node graph, deliberate writer saturation, hung port, large/corrupted payload and source revisions. Target p95 T0 <2 s, T1 <10 s, T2 <60 s per attempt (not end-to-end guarantee); record p50/p95/p99, memory and degradation/failure rates.
6. Run 18-gate matrix (P0-applicable gates) and independently validate every scoped effect certificate, including a negative where test would falsely certify a causal mechanism.

**Deliverables:** executable example set, independent benchmark harness, reproducible test report (`results.csv`, machine details, assumptions, failures), signed-off architecture/compatibility notes.

**Exit:** user approves P0 candidate after independent gates; no unexplained regressions. If S2 cannot work with monitor fully absent or S3 without metric fields, architectural generality has **failed** regardless of good dashboard demos.

## I. Phase 7 — Sparse what-if and model calibration [P1, optional]

**Tasks:** versioned `MechanismSpec` validation, ancestor/descendant bounded subgraph, common-random-number keying stable under event count changes, memory/CPU/sample/horizon caps, uncertainty intervals, regime and spillover sensitivity, `MODEL_PREDICTION` ledger separation, held-out mechanism-family and regime calibration. Out-of-regime predictions abstain. Never use forecasts as independent evidence or permit production action.

**Deliverables:** pure local rollout module, structural/equation tests, held-out calibration chart, versioned mechanism audit, model-vs-observed scoring. **Gates:** G07/G11/G17/G18 (P1 parts). **Stop:** missing mechanism silently invented, simulator-trained model reported real-world validated, or predictions increase empirical confidence.

## J. Phase 8 — Sandboxed test execution and T4 learning [P1, optional]

**Tasks:** `TestExecutionPort` restricted to authenticated allowlisted simulator/sandbox contexts; policy gate checks signed approval token bound to action/scope/expiry/risk/cost; adapter credential has no production write capability. Transactional test-intent/outbox and idempotent receipts; asynchronous outcome correlation; independent effect audit before graph revision; mechanisms recalibrated with held-out scenario splits; fail closed if target, permissions, replay, or source lineage uncertain.

**Deliverables:** sandbox test adapter; authorization receipt schema; credential/allowlist negative tests; T3/T4 feedback and bitemporal revision demo; independent action regret report. **Gates:** G15/G17/G18. **Stop:** can reach production endpoint, duplicate test dispatch during retry, expired authorization succeeds, or emulator effect silently becomes production claim.

## K. Approval checkpoints (must be explicit)

| Checkpoint | Before changing | Evidence required | Approval decision |
|---|---|---|---|
| **A — Contracts** | Canonical v2.0 + migration | Source contract diff, three fixtures, no fabricated metadata, migration fallback | Approve schema + adapter strategy |
| **B — Graph & ledger** | Graph/admission and persistent state migration | 13-edge tests, graph and DB atomicity, replay, data-cut semantics, failure recovery | Approve storage and graph implementation |
| **C — Causal methods** | Identification/certificate output policy | Independent RCT/DiD/e-process tests, effect-vs-edge, negative confounding and cluster examples | Approve claim semantics |
| **D — Operational P0** | Activation of monitoring and question entrypoints | three vertical slices, full p50/p95/p99 load, compatibility difference report, G01–G16 | Approve local/staging deployment |
| **E — P1 action safety** | Sandbox test execution | real allowlist/credential review, negative policy paths, idempotent receipts, oracle isolation | Approve nonproduction tests only |
| **F — Any production release** | Changes to production critical integration | Independent deployment verification, rollback and observability plan, signed risk acceptance | Explicit user approval; no automatic production writes |

## L. Test matrix and failure injection details

**Contract adversarial:** 3 cases each: absent metric-only fields, ambiguous unit, source timezone drift, duplicate revision, wrong `available_at`, unscoped evidence, mismatched graph definition, invalid entity confidence, event silently coerced to rate, experiment covariance absent.

**Causal adversarial:** clustered assignment + individual outcomes; randomized encouragement with noncompliance; ATE vs ATT mismatch; selection into exposure; differential attrition; spillovers; DiD pretrend looks OK despite real violation; confounded invariant mechanism; missing covariate; mediator claim without identification; Simpson reversal with missing OTHER; no-effect placebo.

**Runtime adversarial:** kill/writer lock at each state boundary; lease takeover; adapter timeout while T0 arrives; 100 simultaneous T2 jobs; duplicate source response; 50 synthetic replicas of one root; multiple seeds of same simulator; revised sequential bucket; old graph snapshot after definition drift; single stale query response; output sink idempotency refusal; invalid permissions to production target.

**Benchmark reporting requirements:** test machine/CPU/cores/RAM/OS/Python/dependencies, graph nodes/edges, task count, event rates, source delay distribution, cache warm/cold, numerator and denominator of each latency quantile, p50/p95/p99, deadline miss count, timeouts, queue backlog, memory peak, WAL size, cost/query count, source bytes and error budgets. No silent performance target weakening.

## M. Traceability: phase → blueprint → gates

| Phase | Blueprint sections | Main gates |
|---|---|---|
| 0 | §§0,2.4,10 | G01/G03 |
| 1 | §2 | G02/G03/G06/G07 |
| 2 | §3 | G04/G05/G09/G11 |
| 3 | §5 | G05/G07/G08/G15/G16 |
| 4 | §4 | G08/G09/G10/G11/G12/G14 |
| 5 | §§4.1–4.7,5,6 | G01/G10/G13/G14/G15/G16 |
| 6 | §§7–9 | G01–G16, G18 P0 subset |
| 7 | §§6.3–6.4 | G07/G17/G18 P1 |
| 8 | §§6.2,7.5–7.6 | G15/G17/G18 P1 |

## N. Definition of done and explicit remaining tradeoffs

- **P0 complete:** 3 slices with **zero hidden core branches**, all P0 gates pass independently, valid v2.1 behavior preserved, every method failure explicit, output replay/versions traceable and measured latency under declared load.
- **P1 complete:** sandbox-only constrained tests and local rollouts pass safety/oracle/held-out gates; no scope promotion or production writes.
- **Known tradeoffs:** strong contracts produce more code than a permissive query DSL; one-writer SQLite bounds throughput; avoiding a full causal identification solver means honest unsupported scenarios; conservative method gating can lower answer rate; persistence/provenance increases write volume; a narrow method registry slows support for entirely new domains but prevents hidden assumptions.
- **No claims yet:** no code was compiled or benchmarked for this document. Targets, diagrams, and tests define **what an implementation must demonstrate**, not what has already been demonstrated.
