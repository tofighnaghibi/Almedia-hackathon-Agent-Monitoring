# Subsystem 2 — Causal Core v3.2: Reviewed Master Implementation Blueprint

**Status:** Proposed, buildable architecture; **not** a statement that an implementation has passed the gates.  
**Date:** 2026-11-21.  
**Authority:** This standalone document supersedes the *architecture* in `master_implementation_blueprint.md` (v2.1), v3.0, and the unfinished v3.1 draft after the section-by-section audit recorded in `causal_core_v3_2_section_comparison.md`. Existing code and data remain unchanged until separately approved.  
**Companions:** `causal_core_v3_2_implementation_plan.md` and `causal_core_v3_2_three_views.excalidraw`.

## 0. Decisions, exact scope, and corrections

**System:** a small, local-first **hexagonal modular monolith**, with (A) a typed, bitemporal causal/semantic graph and (B) question-driven, capability-checked analysis. A deliberately thin imperative shell holds I/O, task scheduling, evidence persistence, graph admission, policy, and feedback. **`VariableDefinition` is canonical; `MetricDefinition` is a rich subtype**. Keep metric algebra and experimentation as supported specializations; neither owns the universal planner.

**Inside Subsystem 2:** schema and semantic validation; versioned typed graph; bounded graph views; pure statistical/causal/decision kernels; method-eligibility and identification audits; evidence acquisition planning; resumable reasoning tasks; provenance/ledger; incident revisions; safe sandbox-test requests; optional local sparse forecasts; feedback/revision proposals.

**Outside:** raw ingestion, source-specific SQL and SDKs, dataset stitching, marketplace and sensor semantics, emulator internals/ground-truth oracle, UI rendering, real production actuation, identities/credentials, and external benchmark labeling. The input layer supplies declared meanings, scopes, quality and provenance; the core must never invent them.

### 0.1 What v2.1 got right (must not regress)

- Functional pure computation / imperative shell; ports/adapters; no vendor imports; no LLM statistical authority.
- Algebraic exact identities, population/grain checks, typed graph edge taxonomy, explicit observation-mechanism separation, open-world unknowns and latent confounding.
- Non-destructive MVCC/bitemporal graph, immutable pinned views, quarantine and traceable belief revision.
- Provenance/lineage, source environments, no synthetic evidence inflation; predictions kept distinct from evidence.
- Exact-enough reproducibility from logged inputs, deterministic analytical kernels, single-writer SQLite, restart-safe work and idempotent output.
- SAVI / sequential monitoring **when assumptions hold**, seasonal anomaly screening, Kitagawa, RCD-lite, RCT/DiD and optional SDID, method uncertainty, experimental validation, VoI, and ability to abstain.
- T0/T1/T2 progressive outputs, deferred T3, event-driven T4, action policy, optional sparse rollout and optional TabPFN behind hard gates.

### 0.2 Non-negotiable fixes to v2.1 and v3.0

| ID | Historical issue | Enforced correction |
|---|---|---|
| F01 | `MetricDefinition` effectively universal | Metric is a variable subtype; non-metric categorical, event and state data need no formula/denominator/time bucket. |
| F02 | Detect → decompose → experiment → act was the implicit universal pipeline | **Explicit `ReasoningTask`** with target question/estimand, and use-case-neutral FSM; monitoring only a producer. |
| F03 | RCT/`ExperimentRecord` was sole route to `CAUSALLY_IDENTIFIED` | **Method-specific `IdentificationAssessment`** permits a conditional, scoped claim when a valid strategy supports it; no experiment monopoly. |
| F04 | Randomization and exposure unit equality as validity criterion | Study-aware assignment, outcome, analysis unit, dependence, noncompliance, interference and cluster inference. No forced equality. |
| F05 | `CAUSALLY_IDENTIFIED` represented as if a tested effect proved a structural mechanism | **Distinguish identified intervention effect from graph mechanism edge.** Effect certificate does not itself identify mediator, parent or structural equation. |
| F06 | Generic query API still encoded fixed analytics methods | Versioned typed evidence capability descriptors and method-specific requests; legacy six methods stay behind compatibility adapter. |
| F07 | Fixed five-stage statistics pipeline and blanket causal evidence ordering | Method selection by target estimand, data shape, design, scope, applicability, assumption audits, cost and budget; no blanket rank. |
| F08 | Reported O(1) graph and <2ms lookup as if guaranteed | O(1) **pin handle** only; traversal and cost measured on specified graph shape/hardware; explicit SLO targets, not claims. |
| F09 | Potential overblocking from universal open-world UNKNOWN edges | Unknown means unverified; audit **relevant** assumptions/sensitivity, not every unrelated graph pair. |
| F10 | v3.0 reduced operational rigor and build specificity | Restore exact schemas, owners, persistence rules, failure states, budgets, observability, 18 release gates and 12 scenarios. |
| F11 | Source provenance and “ground truth” could be confused | Source, dependence, lineage, synthetic/replay scope, and oracle firewall; no silent cross-environment transport. |
| F12 | “No production writes” might rely on a string label | Hard nonproduction test adapter, externally verified allowlist, deny-by-default policy, independent authorization and idempotency. |

### 0.3 Hard invariants (machine-tested where possible)

| ID | Requirement and negative case |
|---|---|
| I01 | No core/vendor/source-specific imports; no arbitrary SQL/SDK invocation in pure code. |
| I02 | Universal contracts require only fields valid for all supported variable/task kinds; no mandatory time grain, metric, randomized assignment or anomaly. |
| I03 | Graph stores declarations, hypotheses, evidence-linked claims and uncertainty; graph views do no estimation or actuation. |
| I04 | Every method has typed input and output, target estimand, prerequisites, assumptions, resource bound, version and explicit `UNSUPPORTED`/`INVALID` cases. |
| I05 | Every causal effect claim has a *scoped* estimand, design, dependence model, assumption audit, uncertainty, method version and evidence lineage. |
| I06 | Effect identification never automatically promotes an arbitrary edge to a structural causal mechanism. |
| I07 | Observed values, missingness, selection, sampling, reporting delay and sensor error cannot be silently interpreted as domain mechanisms. |
| I08 | Graph edge absence is `UNKNOWN`; bounded identifiability audit tests relevant assumptions and may abstain. |
| I09 | Experimental evidence has no automatic precedence across mismatched populations, outcomes, treatments, time horizons or estimands. |
| I10 | Inference respects randomization/assignment/observation/analysis units and clustering; missing covariance is not zero; multiple testing is explicit. |
| I11 | Synthetic replicas, model forecasts and benchmark oracle cannot create independent production evidence. |
| I12 | Every graph/evidence/task revision uses pinned versions and recorded seeds/method settings; stale data fork or mark a new revision. |
| I13 | High-priority short tasks are isolated from deep work; all tiers have bounded time, memory, query count and persistence. |
| I14 | Execution is at least once; **external publication and test submission** are idempotent per request/revision key, not magically exactly-once execution. |
| I15 | Production writes absent from ports/credentials; sandbox tests require trusted environment attestation and explicit permission. |
| I16 | The independent evaluation harness has exclusive access to true simulator mechanisms / benchmark labels. |
| I17 | Analytics capability descriptors are checked *before* costly queries; missing shapes do not trigger silent transformation. |
| I18 | Generality is proved by removing monitoring and experimentation profiles in separate end-to-end tests, not just an AST import linter. |

**Limits:** Not a universal causal discovery/identification solver. P0 deliberately excludes instantaneous equilibrium causal cycles, arbitrary full-graph identifiability, unrestricted optimal experiment design, distributed processing, and production execution. Return precise unsupported conditions. No hypothetical certainty from a plausible narrative.

## 1. Topology, package ownership, and dependency discipline

```text
                    INPUT ADAPTERS (external)               OUTPUT/TEST ADAPTERS (external)
  data / questions / studies / events / scopes                         |
                    |                                                   |
                    v                                                   v
     ┌──────────────────── SUBSYSTEM 2 ──────────────────────────────────────────┐
     │  Typed immutable Contracts: Variable, Observation, Study, Task, Evidence │
     │       |                                  |                                │
     │  PART A (no estimators)             PART B (pure reasoning)               │
     │  Variable/MetricSemantics           Task FSM + capability applicability   │
     │  TypedCausalGraph / 3 views <-----> pure estimator kernels and auditor   │
     │       ^ read-only snapshot            hypotheses, evidence VoI, challenge │
     │       |                                  |                                │
     │  SHELL: TaskCoordinator / PriorityScheduler / Admission / Ledger / Policy │
     │        FeedbackReconciler / optional sandbox TestCoordinator             │
     └─────────────────────────┬─────────────────────────────────────────────────┘
                               v
                       OutputSinkPort
```

**Dependency DAG:** `contracts <- semantics, analytics_kernels, graph_validation`; `graph <- contracts, pure_structural_validation`; `storage <- contracts`; `orchestration -> graph + analytics + storage + abstract_port_protocols`. `app/wiring.py` alone imports concrete adapters. No circular imports and no introspection-based import magic. A simple explicit registry is preferable to a generic dependency injection container.

### 1.1 Component responsibilities and *negative* responsibilities

| Component | Owner of | May call | Must never do |
|---|---|---|---|
| `VariableSemantics` (pure) | Definition validation: value kinds, units if meaningful, state and event schemas | Typed contracts only | Query source, infer causal links, require metric fields for non-metrics |
| `MetricSemantics` (pure) | Formula AST, algebra, grain/population compatibility, numerical identity checker | Metric subtype contracts | Expand every variable to a metric or treat arithmetic as causality |
| `TypedCausalGraph` | Versioned graph index and immutable snapshot views | Pure validation, ledger transaction interface | Estimation, decision, graph-write-from-read |
| `GraphAdmissionCoordinator` | Atomic admission, quarantine, graph deltas | Structural semantics and bounded typed evidence checks | Issue causal certificates, auto-monitor all new variables |
| `ReasoningPlanner` | Pure, resumable FSM transitions and task-specific next step | Registry, graph views, pure analysis | External I/O in transition function; hard-coded experiment/monitor branch |
| `AnalyticsCapabilityRegistry` | Descriptor/implementation registration, admissibility and selection trace | Pure method kernels | Swallow missing prerequisites; choose method from name alone |
| `IdentificationAuditor` | Estimand/method-design assumptions, effect claim eligibility | Audit specs + method results | Treat method label as proof; infer structure from effect estimate alone |
| `EvidenceAcquisitionPlanner` | Bounded options, attainable utility and costs, information-gain proxies | Provider manifests, saved outcomes | Invent unavailable queries or present heuristic VoI as calibrated expected value |
| `MonitorDetector` | Optional monitoring task trigger; DQ/SAVI on suitable data | Pure monitoring methods, stored sequence state | Be mandatory prerequisite for explicit questions |
| `TaskCoordinator` | Task ingress, pinning and stage dispatch | Planner, scheduler, ledger | Make statistical decisions or hide transformations |
| `PriorityScheduler` | Leases, checkpoint/retry/backpressure, priority isolation | Single-writer ledger | Execute statistical methods itself; duplicate visible outputs |
| `EvidenceLedger` | Immutable evidence, method decisions, audit, revisions, E-state, jobs, predictions | SQLite WAL transaction writer | Treat synthetic as independent production observations |
| `FeedbackReconciler` | Scoped outcome correlation, revisions and calibration requests | Auditor, graph admission, ledger | Rewrite historical evidence or auto-certify post-hoc correlation |
| `ActionSafetyPolicyGate` | Permissions, risk/resource policy and publish/actuation decision | Typed results, trusted authorization | Confuse statistical support with authorization; allow production writes |
| `TestCoordinator` (P1) | Authorized sandbox test intent and idempotent receipt | `TestExecutionPort` | Send to production; see emulator oracle labels |
| `CounterfactualRolloutEngine` (P1 pure) | Bounded local SCM forecasts | Validated pinned graph+mechanism definitions | Certify causes or replace external simulator |
| `HypothesisProposerPort` (optional) | External candidate hypotheses only | Scoped public task context | Supply truth, calculate effects or override safety |
| `OutputSinkPort` | External report/alert/UI transport | Policy-vetted immutable output | Modify core decision or results |

### 1.2 Target package tree (P0; P1 marked)

```text
causal_core/
  contracts/
    variables.py            # VariableDefinition union, MetricDefinition subtype, EntitySpec, DimensionSpec
    observations.py         # typed value payloads, DQ, ObservationMechanismSpec, time fields
    studies.py              # CausalQuestion, EstimandSpec, StudyDesign, InterventionSpec
    graph.py                # GraphNode/Edge, EdgeKind, statuses, BitemporalValidity, TraversalBound
    evidence.py             # EvidenceRecord, ProvenanceLineage, EnvironmentScope, EvidenceGap
    tasks.py                # ReasoningTask, Finding, TaskRevision, IncidentRevision, checkpoint, action
    capabilities.py         # CapabilitySpec, ApplicabilityAssessment, typed request/response union
    ports.py                # read-only evidence, hypothesis proposal, sink, optional sandbox test
  semantics/{variables.py,metrics.py,studies.py}
  graph/{store.py,views.py,validation.py}
  analytics/
    {registry.py,descriptive.py,sequential.py,decomposition.py,discovery.py,
     experiments.py,quasi_experimental.py,identification.py,voi.py}
    tabpfn_estimators.py     # P1 optional, lazy import only
  counterfactual/{mechanisms.py,rollouts.py}   # P1 only
  storage/{schema.sql,ledger.py,query_cache.py}
  orchestration/
    {coordinator.py,fsm.py,planner.py,scheduler.py,admission.py,monitor.py,
     feedback.py,policy.py,test_coordinator.py} # last P1 only
adapters/
  legacy/{metric_v1.py,evidence_queries_v1.py}
  sources/{marketplace.py,experimentation.py,telemetry.py,sensors.py,simulator.py}
  tests/{evidence_port.py,sandbox.py}
  outputs/{json.py,cli.py}
app/wiring.py
scripts/{fixture_freeze.py,run_benchmarks.py,audit_assumptions.py}
tests/{unit,contract,integration,adversarial,performance,independent,architecture}
```

**Do not implement a plugin marketplace.** Register an explicit small method table compiled with the package; source adapters can implement subsets of a finite, versioned request union. Add new capability kinds via schema review, not arbitrary dictionaries. Optional third-party methods must pass capability conformance.

## 2. Canonical schema v2.0 and exact contract behavior

**Frozen, versioned Pydantic v2 discriminated models.** Avoid `dict[str, Any]` as a universal escape hatch. Common envelopes are small, typed payloads preserve distinctions; flexible domain metadata only in explicitly namespaced, ignored-by-core extension fields that **cannot** influence inference unless a registered typed capability understands them.

### 2.1 Core types — minimum fields / refinements

| Type | Required | Specialized or conditional | Invalid / unsupported examples |
|---|---|---|---|
| `VariableDefinition` | `schema_version`, `variable_id`, `kind`, `value_schema`, `scope`, `provenance` | optional `unit`, `entity_ref`, `observation_binding`; subtype discriminant | Every variable requiring grain or numerator |
| `MetricDefinition` (`kind=metric`) | Base fields; numeric measure semantics (aggregation, population, time window and formula are **conditional**, not universally required) | `aggregation_kind`, `population_coverage`, `time_semantics`, `formula_ast`, dimension map, units, numerator/denominator, numeric tolerance/rounding policy — each required only by the operation that needs it | Exact sum on overlapping populations, formula units mismatch |
| `StateVariableDefinition` | Base fields, state value type | sampling cadence, clocks, physical units, valid range | Silently forcing raw state into a rate |
| `EventVariableDefinition` | Base fields, event payload schema and event identity | occurrence time semantics and event entity binding | Aggregating to buckets without declared operator and lost-information flag |
| `CategoricalVariableDefinition` | Base fields, category schema | missing/OTHER/ordinal semantics | Assuming category order or exhaustiveness |
| `EntitySpec` / `DimensionSpec` | opaque typed IDs, source/scope and key semantics | hierarchy and match confidence, partition policy | Confidence<1.0 treated as exact equality |
| `Observation` | variable ID, **typed payload**, source ID, lineage, environment, revision, temporal metadata with `KNOWN/ESTIMATED/UNKNOWN` qualifiers when relevant | `observed_at`, `available_at`, `as_of`, `ingested_at` where supported; source event-time vs valid-time distinction, DQ, sampling/selection/missingness | Universal fields `numerator`, `denominator`, `time_bucket` |
| `ValuePayload` | discriminated union: `Scalar`, `Categorical`, `AggregateStats`, `TimeSeries`, `Panel`, `EventBatch`, `AssignmentOutcomes`, `StateTrajectory` | shape-specific sample counts, covariance, cluster ID, sampling weights, completeness | `AggregateStats` treated as independent IID observations by default |
| `StudyDesign` | study ID, design subtype, source scope and provenance; randomized/quasi/observational/descriptive designs have distinct mandatory fields | population, contrast, outcome, analysis unit, clocks, randomization unit, exposure unit, cluster map, propensity, noncompliance, missingness, interference map, multiple outcomes as appropriate to the design | Forced assignment = exposure = outcome unit, false RCT attestation |
| `EstimandSpec` (**causal-effect tasks only**) | outcome, intervention/contrast, target population, horizon and effect functional, source/target scope | e.g. ATE, ATT, ITT, dose response, policy value; identification strategy optional. Noncausal tasks use distinct `DescriptiveTarget`, `PredictionTarget` or `HypothesisTarget` contracts | “Lift” with undefined treatment, time or population; forcing intervention field onto descriptive work |
| `ReasoningTask` | ID, `question_kind`, `target`, declared `scope`, priority, budgets, creation/time cut | estimand only for causal effect question; incident IDs only for monitoring; candidate actions for comparison | Mandatory anomaly/experiment ID on explicit question |
| `EvidenceRecord` | source/provenance graph, method provenance when computed, evidence class, source/environment scope, content hash; as-of and observation time conditional by evidence kind | lineage parent IDs, generator/model version, dependence blocks, retention/privacy metadata | Predictions relabeled as observed production evidence |
| `GraphNode`/`GraphEdge` | kind/layer/validity/provenance/status, endpoints, permitted edge type | lag bounds, match confidence, identification reference | Missing edge interpreted as `KNOWN_ABSENT` |
| `IdentificationAssessment` / `EffectClaim` | estimand, strategy, assumptions/diagnostics, effect/uncertainty, scope, evidence IDs, method version | may indicate *conditional* support or abstention; separate structural claim | Statistical significance alone creating arbitrary graph edge |
| `Finding`, `EvidenceGap`, `ActionProposal` | task ID/revision, claim type, support refs, uncertainty, scope, known limitations | recommended measurement/test, authorization requirements | Mix decomposition reported as proof of harmlessness |
| `WorkItem`, `Checkpoint` | task and stage, attempts, lease, pins, request budgets spent, stage timestamps, trace hashes | cancellation/preemption reason, evidence cursors, method model versions | Retry repeats expensive source query without accounting |
| `MechanismSpec`, `CounterfactualPrediction` | P1: family/parameters/noise/valid regime/version/input lineage and predicted horizon/interval | sensitivity/uncertainty/calibration | Unsupported structural equation silently synthesized |

**Contract pseudocode (illustrative shapes, not an implementation of all validators):**

```python
class VariableDefinition(BaseModel, frozen=True):
    schema_version: Literal['2.0']
    variable_id: str
    kind: Literal['metric','state','event','categorical']
    value_schema: ValueSchema
    scope: DataScope
    provenance: SourceAttestation

class MetricDefinition(VariableDefinition):
    kind: Literal['metric'] = 'metric'
    aggregation_kind: AggregationKind | None = None
    population_coverage: PopulationCoverage | None = None
    formula_ast: FormulaNode | None = None
    time_semantics: MetricTimeSemantics | None = None

class ReasoningTask(BaseModel, frozen=True):
    task_id: str
    question: QuestionSpec              # discriminated by task kind
    source_scope: EnvironmentScope
    data_cut: DataCut
    priority: int
    budget: ExecutionBudget

# The actual serialized VariableDefinition field uses a discriminated union
# over MetricDefinition, StateVariableDefinition, EventVariableDefinition,
# CategoricalVariableDefinition. Avoid recursive base-class parsing as subtype.
```

**`EnvironmentScope`:** (`environment_id`, `environment_kind` = production/staging/emulator/offline_replay, data_cut, dataset/generator version if applicable, source attestation). **`ProvenanceLineage`:** source ID; `origin_kind` original/synthetic_generated/synthetic_replica/model_prediction/external_study; lineage root; parent evidence IDs; generator version; content and transformation hashes. **Independence is not inferred from distinct roots:** account for shared units/cluster/time and mechanism ancestry separately.

### 2.2 Clock semantics and revision policy

- `observed_at` = when fact occurred or was measured; `available_at` = earliest time an analyst could have received it; `as_of` = query snapshot cut; `ingested_at` = when core saw it; `valid_from/to` = domain truth interval of graph declaration; `recorded_at_version` = historical belief version. **These time fields are conditional, not mandatory for timeless/static evidence.** Mark each as `KNOWN`, `ESTIMATED` or `UNKNOWN` rather than manufacturing timestamps. When present, use timezone-aware UTC values on the wire with original source timezone/conversion policy retained.
- **Time-dependent replay** requires a known `available_at <= replay_time` **and** data cut consistent with source snapshot. `observed_at <= replay_time` alone is insufficient. With missing/estimated availability, refuse leakage-sensitive replay or explicitly run an uncertainty scenario; static/cross-sectional inference need not invent a time axis.
- Revisions are immutable. For a given `(source_id, record_key)`, higher source-declared revision or explicit supersession adds a new observation; a duplicate content hash with same lineage is idempotent. Conflicting equal revision quarantined. New revisions invalidate method-specific caches/monitor state consistently; **sequential wealth cannot simply be counted twice**.
- A pinned task has `(graph_version, valid_at, data_cut, evidence_watermark, capability_versions, method_versions, mechanism_version?)`. On material drift: `STALE`, new revision / new task attempt with explicit changed pins. No mixing.

### 2.3 Evidence classes, claims and epistemic ordering

Distinguish **source kind** from **claim kind**: observed data; external study result; sandbox observation; synthetic replica; locally modeled forecast; benchmark truth. For claim outputs distinguish `MATHEMATICAL_IDENTITY`, `DESCRIPTIVE`, `ASSOCIATION`, `CAUSAL_EFFECT_CONDITIONAL`, `STRUCTURAL_CAUSAL_HYPOTHESIS`, `MODEL_PREDICTION`, `INCONCLUSIVE`, `DQ_FAILURE`, and `ACTION_RECOMMENDATION`.

A causal *effect* certificate attaches to an identified **estimand** and declared assumptions. A **structural edge** requires separate mechanism-identification evidence; an RCT of treatment T on Y does not automatically validate every `T -> mediator -> Y` edge. A valid nonrandomized identification strategy may support a conditional effect; failing an untestable assumption does not become “certified true.” Evidence from different environments/populations is not interchangeable without explicit transport assumptions and sensitivity checks.

### 2.4 Migration map (v1.0/v1.1/v1.2 → v2.0)

| Old contract | Compatibility action | Negative rule |
|---|---|---|
| `MetricDefinition`, `FormulaNode` | Lossless metric subtype with old AST, grain and population fields; record original bytes and migration version | No mandatory metric fields for other kinds |
| `GraphNode` METRIC default and `GraphEdge` 13 kinds | Map metric nodes and statuses; carry bitemporal/version/provenance | Legacy defaults cannot silently assert verified identity |
| `HYPOTHESIZED_CAUSAL` | Deterministically map `CAUSAL_HYPOTHESIS` | No causal certification upgrade |
| `Observation` + `SufficientStats` | Map to `AggregateStats` preserving window, SE/sums, source lineage and revision | Do not infer IID or covariance |
| `ExperimentRecord` | `StudyDesign` + typed assignment/outcome payload where sufficiently attested; else quarantine | No forced unit alignment; missing covariance never zero |
| `EvidenceQueryPort` six methods + `bucket_grain` / `max_buckets=168` | `LegacyEvidencePortAdapter` with explicit typed request conversion and original behavior | New core never requires all six methods |
| E-process `log_wealth`, `peak_log_wealth`, `alpha` | Preserve monitor-specific state; document old null/betting assumptions and seen lineage roots | Do not reuse wealth after invalidated/revised buckets without valid recomputation |
| `IncidentRevision` / scheduler jobs | Convert to task specialization; retain old fingerprint, idempotency key, stage pins | No duplicate outward revision |
| Old `CAUSALLY_IDENTIFIED` edges/certificates | Historical status + `REVIEW_REQUIRED` if estimand/method/assumption fields inadequate | No grandfathered unconditional authority |
| Missing source origin/scope | `LEGACY_UNSCOPED` quarantine with deterministic stable root | No production claims or action permissions |

**Regression principle:** Preserve valid legacy conclusions; intentionally corrected statistical mistakes are documented as behavior changes with independent tests. Never silently modify the original input records.

## 3. Part A — typed bitemporal causal graph

### 3.1 Preserve all thirteen typed edge distinctions

| `EdgeKind` | View | Conditions and interpretation |
|---|---|---|
| `EXACT_ARITHMETIC` | `StructuralView` | Formula AST identity over compatible metrics/units/grain/population; check residuals/tolerances and explicit zero-denominator behavior. **Not causal.** |
| `AGGREGATION_COMPOSITION` | Structural | Verified numerator/denominator or additive decomposition; do not sum overlapping populations. |
| `DIMENSIONAL_DECOMPOSITION` | Structural | Full partition incl. explicit missing/OTHER or bound on truncation; `top_K` alone not complete coverage. |
| `ENTITY_RELATIONSHIP` | Structural | Hierarchy or identity; match confidence below one excludes *exact* closure. |
| `TEMPORAL_WINDOW` | `TemporalView` | Overlap between windows only where variable has declared window semantics; dedup echo without erasing distinct causal events. |
| `TEMPORAL_LAG` | Temporal | Direction and nonnegative/positive bounds, time clock, relevant validity. Lag association alone never proves causal direction. |
| `TEMPORAL_FEEDBACK` | Temporal | Feedback only with strictly positive time-expanded total lag; zero-time equilibrium unsupported in P0. |
| `OBSERVATION_EFFECT` | `CausalEvidenceView` (observation layer) | Data production/measurement/selection link; cannot become a domain cause by promotion. |
| `OBSERVATIONAL_ASSOCIATION` | CausalEvidence | Co-movement/dependence evidence; not identified causal link. |
| `CAUSAL_HYPOTHESIS` | CausalEvidence | Candidate structural explanation with provenance/assumptions/rivals; quarantinable. |
| `CAUSALLY_IDENTIFIED` | CausalEvidence | **Only** for specifically identified relationship/estimand and immutable certificate; if certification is effect-only, record `EffectClaim` linked to relevant nodes instead of claiming the full mechanism. |
| `LATENT_CONFOUNDING` | CausalEvidence | Known/plausible hidden common cause (candidate/verified status); may invalidate certain strategies. |
| `UNKNOWN_RELATIONSHIP` | CausalEvidence | No tested support/absence; graph API returns `UNKNOWN` for missing edge. |

**Graph node kinds:** VARIABLE, ENTITY, INTERVENTION, STUDY, OBSERVATION_MECHANISM, LATENT, EFFECT_CLAIM (if effect node representation chosen). `METRIC` remains a `VariableDefinition` specialization, not a separate universal graph infrastructure. Keep `DOMAIN` and `OBSERVATION` layers; a latent may be a declared domain or observation mechanism. `KNOWN_ABSENT` is not implicitly represented by missing adjacency; if a future evidence-backed negative relationship type is desired, add separately with a scoped falsification contract.

**Important correction:** A randomized assignment `Z` identifies an ITT effect of assignment on outcome under relevant assumptions; it need not identify the effect of actual exposure `D` when noncompliance exists. Attaching that ITT certificate to a `D → Y` edge without a separate valid strategy is forbidden.

### 3.2 Snapshot and mutation model

- Immutable node/edge versions with knowledge time `[recorded_version, superseded_version)` and domain-valid time `[valid_from, valid_to)`; the query chooses both knowledge version and domain time.
- `pin_snapshot(graph_version, valid_at)` returns an **O(1) immutable handle** to persistent index roots. Actual filtering / traversal is O(visited nodes+edges) plus indexing overhead, **not O(1)**. Avoid deep-copying NetworkX on each mutation; validate candidate materialization strategy against memory and replay tests before selecting a persistent-map or copy-on-write index.
- A read-only traversal bound is required: starting config `max_hops=3`, `max_nodes=100`, `max_edges=400`, `include_quarantined=false`, `include_superseded=false`, `max_wall_ms=50` (last is a cancellation threshold, not a hard Python interruption guarantee). Return `TRUNCATED` and the boundary reason; no implicit exhaustive inference from a truncated neighborhood.
- Every graph write is a single SQLite transaction containing validated edge/node delta, graph version bump, admission result and relevant evidence reference; update in-memory indexes **after commit** or roll back coherently. Crash recovery rebuilds index from committed deltas; never leave memory ahead of disk.
- Graph admission must not import analytics policy. Identity residual verification is invoked through a pure checker and must not infer causal direction. Causal effects originate from auditor results and are admitted only after certificate schema and scope validation.
- Graph reads are side-effect-free. A resumed task carries its pin; if new evidence invalidates applicability, mark old revision STALE and create a new pinned revision, not retroactively rewrite it.

### 3.3 Eight-step admission algorithm (including non-metric path)

1. Receive signed/attested source envelope and schema version; deduplicate by source and immutable record ID.
2. Validate subtype and observation binding; reject malformed or unsupported values, quarantine ambiguous environment or missing provenance.
3. Validate entity identity and dimension mapping; uncertain entity links remain uncertain; missing categories are not auto-coded as OTHER unless declared.
4. If a metric has a formula, parse allowed AST operations (no dynamic evaluation), type-check units/pops/grains, derive structural edges. For nonmetric types skip this step with `NOT_APPLICABLE`, not fake formula.
5. Validate temporal and observation edges against permissible layers, lag clock, instantaneous DAG, existing validity periods, and request-bound graph limits.
6. If an asserted exact numeric identity needs evidence, issue one bounded, typed, pinned source query; check tolerance/rounding, zero denominators, missingness and completeness. If unavailable return `UNVERIFIED`/quarantine rather than activating unsupported exactness.
7. Commit only verified structural facts to ACTIVE; retain questionable edges QUARANTINED with reason, evidence ID and next diagnostic; nonmetric variables without structural claims may be admitted normally.
8. Persist atomic graph delta, admission verdict, versions and fingerprint; notify **subscribed** profiles only, never auto-onboard a monitor for every variable.

### 3.4 Read-only semantic views

- `StructuralView`: identity constraints, entity relationships, metric AST sensitivity, conservation/partition validation. No causal proof from exact arithmetic.
- `TemporalView`: windows, intervals, rolling-grain echo dedup, lagged links and time-indexed feedback; distinguish *available_at* from domain event time.
- `CausalEvidenceView`: associations, hypotheses, documented causal effect claims, latent/unknown/observation mechanisms, certificates and counterevidence with scoped status. Queries return known facts and `unknowns_relevant_to(estimand, strategy)`; **not** a fictitious complete DAG.

## 4. Part B — reasoning workflow and method capabilities

### 4.1 `ReasoningTask`: task taxonomy (entrypoints are not domain-specific)

| Question kind | Minimum question content | Example trigger | No required prerequisite |
|---|---|---|---|
| `DETECT_CHANGE` | target variable(s), comparison window or baseline policy | monitored metric or sensor | Experiment |
| `EXPLAIN_OBSERVATION` | observed contrast/pattern, time/context | user asks why outcome shifted | Anomaly threshold, RCT |
| `ASSESS_HYPOTHESIS` | candidate relation/mechanism, observable implications | domain scientist or automated proposal | Metric formula |
| `ESTIMATE_EFFECT` | precise intervention, outcome, target population, horizon, estimand | analyst asks ATE/ITT/ATT | Alert or anomalous KPI |
| `ASSESS_STUDY` | source study/design/outcomes | study completed | A/B vendor SDK |
| `COMPARE_INTERVENTIONS` | candidate interventions and utility/risk constraints | decision point | Simulator with valid mechanisms (must abstain if absent) |
| `DESIGN_EVIDENCE` | competing hypotheses, proposed observable contrasts, budget | cannot distinguish causes | Action authority |
| `PREDICT_OUTCOME` | target horizon and supported predictive mechanism | forecast request | Identified causal effect (but do not claim causality) |
| `EVALUATE_OUTCOME` | past action/forecast receipt and observed feedback | outcome arrives | A monitor alarm |
| `CHECK_MODEL` | structural/predictive assumption and held-out data | regime drift or state oscillation | Conversion metric |

**Question semantics:** `question_kind` selects required question fields but does not hardcode a statistical method; an `ESTIMATE_EFFECT` task can return association-only, insufficient data or an audited causal estimate. A user-facing “why” request must preserve rival explanations. `DETECT_CHANGE` and `ASSESS_STUDY` have different first checks, but both use the same typed task/registry/ledger infrastructure.

### 4.2 Pure planner transitions and failure-handling

```
FRAME -> AUDIT_INPUTS -> SELECT_CAPABILITIES -> ACQUIRE
     -> EVALUATE -> CHALLENGE -> REPORT
                    ^                  |
                    +---- selective retry / acquire with remaining budget
```

`FRAME`: pin target, estimand, population, time horizon and task version; refuse ill-defined targets (do not invent an estimand). `AUDIT_INPUTS`: DQ, provenance, availability clock, observation bias, scope, graph snapshot. `SELECT_CAPABILITIES`: candidate analytic methods must pass input-shape and design prechecks. `ACQUIRE`: choose **executable** typed queries with version and budget; non-executable recommendations can be reported without waiting. `EVALUATE`: run pure computations with deterministic seeds/version. `CHALLENGE`: check counterevidence, negative controls, selection/latent assumptions, sensitivity, and relevant alternative explanations. `REPORT`: claim kind, scope, uncertainty, limitations, evidence gaps, policy, revision. **No mandatory fixed five-stage path**: a valid study might skip anomaly screening; a simple algebraic question may stop after structural audit; a sensor question may never call metric decomposition.

State is immutable between transitions and checkpointed by shell. Explicit transition outcomes: `READY`, `WAITING_FOR_EVIDENCE`, `PARTIAL_BUDGET`, `UNSUPPORTED`, `INCONCLUSIVE`, `FAILED_VALIDATION`, `STALE`, `COMPLETED`. A method's runtime error cannot silently turn into a causal abstention; preserve both failure class and cause.

### 4.3 Capability descriptor and pure registry contract

```python
CapabilitySpec(
  id: str, version: str,
  question_kinds: tuple[QuestionKind, ...],
  input_shapes: tuple[ValuePayloadKind, ...],
  output_kind: ClaimKind,
  estimand_families: tuple[EstimandFamily, ...] | None,
  required_metadata: tuple[FieldPath, ...],
  assumption_checks: tuple[AssumptionCheckID, ...],
  dependence_handling: DependenceMode,
  max_work_units: int,
  cost_estimator_id: str,
)
# ApplicabilityAssessment = SUPPORTED | SUPPORTED_WITH_LIMITATIONS | UNSUPPORTED
# Fields: condition results, why, missing evidence, candidate next queries,
#        forecast cost and runtime bound, scope limitations, method version.
```

Do not register a method as “general” by declaring every kind and silently delegating to a hidden metric workflow. **Conformance test:** construct syntactically valid but semantically inapplicable inputs; the method must refuse and state the missing premise. Profile selection may narrow registered capabilities but never bypass per-method checks.

### 4.4 Required P0 analytical capability matrix

| Method / ID | Applicable to | Mandatory checks | Honest conclusion / failure |
|---|---|---|---|
| `dq_observation_v1` | Supported shape with quality metadata | completeness, reporting maturity, censoring, selection/measurement coverage, source availability | Data quality diagnosis; unassessed bias prevents unjustified domain attribution |
| `metric_identity_v1` | Typed metric formulas | units, population, time grain, formula AST, empirical identity | Mathematical identity/structural inconsistency; no cause |
| `seasonal_screen_v1` | Adequate time series | seasonality support, baseline window, missingness, multiple comparisons | Anomaly *candidate*, not causal proof |
| `savi_eprocess_v1` | Valid sequential evidence stream | specific null, adapted predictable bets, dependence and revision handling, lineage and alpha budget | Sequential alert only under documented martingale/e-process conditions; else UNSUPPORTED |
| `kitagawa_v1` | Comparable stratified rates | exhaustive or bounded partition, matching populations/window/denominators, uncertainty | Mix-vs-rate arithmetic; residual/truncation shown; never “harmless” without impact policy |
| `rcd_lite_v1` | Time-aligned graph parent neighborhood | enough history/regimes, measured parents, stability/selection assumptions, valid time alignment | Mechanism-invariance **candidate pruning** only; hidden confounding may mimic shift |
| `association_v1` | Paired/panel/series data with declared design | sample alignment, dependence, selection, missingness | Association with uncertainty; never causal by itself |
| `randomized_effect_v1` | Randomized design + valid outcomes | assignment integrity, SRM at assignment unit, ITT vs exposure contrast, attrition, cluster dependence, spillover, proper SE | Scoped conditional ITT/ATE when identified; otherwise design audit / abstain |
| `multiplicity_v1` | Multiple tested hypotheses | family definition, alpha budget, dependence assumptions, available covariance | Holm valid conservative familywise control where appropriate; **not** a substitute for joint surrogate evidence |
| `did_v1` | Panel treated/controls + intervention timing | target estimand, pre-intervention comparability, trends diagnostics, anticipation, timing, spillover, serial correlation, treatment assignment | Conditional DiD/ATT estimate under untestable parallel trends; pretrend pass never proves it |
| `identifiability_audit_v1` | Target estimand + relevant local graph/design | latent/selective paths relevant to strategy, transport, target scope, method validity, observed variables | Scoped `IdentificationAssessment`; does not require RCT or blindly reject all unknowns |
| `voi_heuristic_v1` | Competing hypotheses + executable query options | provider support, costs, discrimination proxies, risk, lineage novelty, resource limits | Ranked **heuristic** diagnostic value; no claim of calibrated expected information gain |

**Optional P1:** synthetic DiD with documented donor/regularization design and falsification; causal Shapley **only** given defensible structural value function and identified/assumed mechanism, not as default explanation; TabPFN residual/invariance modules behind hardware and measured budget gate; IPW/DR if observational propensity/overlap/target assumptions are explicit; sparse structural rollouts. Each method's descriptor and independent test suite must precede activation. No generic LLM certification.

### 4.5 Identification and graph admission: two different questions

1. **Effect identification:** `IdentificationAuditor` receives `(EstimandSpec, StudyDesign?, EvidenceSet, local_graph_assumptions, method_result)`. It produces `IdentificationAssessment` with strategy, checked/untestable/refuted assumptions, violated conditions, target population/horizon, uncertainty, environmental/transport boundary, and evidence IDs. Status can be `CONDITIONAL_IDENTIFICATION`, `PARTIAL_IDENTIFICATION`, `ASSOCIATION_ONLY`, `UNIDENTIFIED`, `INVALID_DESIGN`, `INSUFFICIENT_EVIDENCE`.
2. **Structural mechanism admission:** `GraphAdmissionCoordinator` receives a **separate** structural claim and lineage; to create `CAUSALLY_IDENTIFIED` edge it must verify a certificate that identifies exactly that relation/definition, not merely treatment's total effect. Otherwise store effect claim as attached node/artifact and keep candidate mechanisms as hypotheses.
3. **No blanket hierarchy:** If RCT estimand A differs from observational estimand B, report both or transport analysis. Within the **same estimand** prefer credible identification and design validity, not source label.
4. **Positive results are not all-or-nothing:** RCT may support ITT but not exposure-on-outcome; missing shared-unit covariance may support marginal outcomes but block surrogate validity. Conditional DiD can support its declared ATT under assumptions without claiming an experimental proof.
5. **Source/environment limitations:** Sandbox causal identification is sandbox-scoped. Transport to production requires independent evidence or explicit, separately assessed transport assumptions; cannot rename a scope to promote it.

### 4.6 Evidence acquisition and VoI

`EvidenceAccessPort` exposes **versioned capability manifests** plus a finite typed request union: `GetSeries`, `GetAggregate`, `GetStrata`, `GetPanel`, `GetEventBatch`, `GetAssignmentOutcomes`, `GetInterventionHistory`, `GetMeasurement`, `GetSourceMetadata`. Legacy six queries map through a separate adapter; providers may support any subset. Each request includes exact variables/units, scope, `data_cut/as_of`, time and population filters, max bytes/rows/buckets, deadline, source query ID, evidence-purpose and privacy level. Responses carry completeness, truncation, sampling design, unit IDs as permitted, confidence/missingness, provenance lineage, request fingerprint, and unsupported reason. **Requests must not be accepted just because their Python type matches:** capability-specific semantic validators run on both ends.

`EvidenceAcquisitionPlanner` scores feasible actions by a transparent, bounded heuristic: estimated hypothesis-discrimination benefit × novelty × relevance, divided by expected latency + money/resource + opportunity/risk cost, with explicit privacy and authorization vetoes. Distinguish **proxy scores** from actual expected utility; learn cost/discrimination statistics only from evaluated, scoped previous outcomes. Missing feasible queries -> `EvidenceGap` + specific recommendation. No fake `fetch_anything(**kwargs)`.

### 4.7 Hypothesis challenge and abstention

- Enumerate candidate mechanisms, observation artifacts, confounding alternatives, and 'none of the proposed explanations' where appropriate. Seed with graph plus optional bounded proposer, **not** benchmark oracle or untrusted metadata labels.
- Use negative controls, sensitivity analyses, relevant missing-edge implications, competing interventions, placebo/pre-period tests (method-specific), and data quality audit where applicable.
- Distinguish: failure to reject, equivalence, absence of effect, no usable data, model incompatibility, insufficient causal identification. Avoid translating them into the same `NO_CHANGE` outcome.
- On budget exhaustion emit useful partial result with assumptions, top rivals, diagnostic next action, pinned versions and `PARTIAL_BUDGET`. Safe urgent alerts can publish before full investigation.

## 5. Durable state, concurrent execution and performance

### 5.1 Persistent state ownership — one SQLite WAL writer

| Persisted artifact | Logical owner / write command | Invariant and replay behavior | Key/index concept |
|---|---|---|---|
| Definitions + bitemporal graph deltas | GraphAdmissionCoordinator → `EvidenceLedger.commit_graph_delta` | All-or-nothing graph version and deltas, append-only; rebuild in-memory views from committed log | `(graph_version, kind, endpoint)`, `(valid_from,valid_to)` |
| Evidence + lineage / ancestry DAG | EvidenceLedger | Immutable content-addressed observation, source scope, origin; dedup by lineage semantics, not just rows | `(evidence_id)`, `(source_id,record_key,revision)` and root index |
| Query results and failures | EvidenceLedger/query cache | Persist request envelope, version, budget spent, source response/failure, truncation, expiry; cannot use cache beyond pinned data cut | `(request_fingerprint,scope,data_cut)` |
| Method selection / rejection / audit | ReasoningPlanner → ledger | Record candidate methods, applicability matrix, assumptions, method version and RNG seed; replay uses recorded source responses | `(task_id,revision,transition_seq)` |
| `TaskRevision` + `IncidentRevision` | TaskCoordinator + policy | Monotonic revision, supersession/stale marker, exactly-once-visible via unique `(task_id, revision)` and idempotency outbox | `(task_id,revision)`, `(incident_fingerprint)` |
| WorkItems, leases, checkpoints | PriorityScheduler | Transactional lease/heartbeat/retry, pinned evidence/graph versions, consumed budgets, cancellation; restart from last committed transition | `(state,priority,next_eligible_at)` |
| SAVI `EProcessState` | MonitorDetector via ledger | Log wealth, alpha/betting policy, lineage/bucket consumption, revision recomputation policy | `(variable_id,segment_key,hypothesis_key,method_version)` |
| Mechanisms and predictions | FeedbackReconciler / P1 Rollout result | New version for recalibration; prediction cannot be reclassified as observed evidence; training/eval lineage separate | `(mechanism_id,version)`, `(prediction_id)` |
| Sandbox test intents/receipts | TestCoordinator P1 | Authorized request (trusted environment) and outbox receipt share deterministic idempotency key; no duplicate executed test if adapter honors key | `(environment_id,idempotency_key)` |
| Feedback and model calibration | FeedbackReconciler | Correlate receipt to later observation; revisions append, never rewrite; no “post hoc proves cause” | `(action_id,observation_id)` |

**Physical transaction rule:** SQLite owns durable truth. Use WAL mode, `busy_timeout`, explicit `BEGIN IMMEDIATE` for one writer or a serialized in-process writer queue. Batch small commits; use transactions that atomically update ledger, outbox and graph version. Commit before emitting external output. **Crash window:** drain unsent outbox idempotently on restart; output sink must accept idempotency key or may be at-least-once (document provider limitation). Never promise external exactly-once delivery without sink support.

**SQLite constraints:** appropriate for local/single-process P0, not infinite ingest. Measure write saturation with realistic event volumes; cap queues, checkpoint WAL, protect against long-running read transactions; if target workload fails, gather profiles before introducing a distributed log/queue. No premature Kafka/Celery. Backpressure must not silently drop loss-critical tasks: emit `DEGRADED` or `DROPPED_WITH_REASON` accounting and policy-approved shedding of optional work.

### 5.2 Scheduler and task stage protocol

`QUEUED → LEASED → RUNNING → CHECKPOINTED | COMPLETED | FAILED | CANCELLED` with `STALE` a separate result/revision condition. Required job fields: `job_id`, `task_id`, `task_revision`, `stage`, `priority`, `deadline_at`, `lease_owner`, `lease_until`, `attempt`, `retry_after`, `cancel_requested`, `remaining_query_budget`, `remaining_compute_budget`, `remaining_external_cost`, pinned graph/evidence/capability/method version fingerprints, checkpoint payload hash, and idempotency key.

- Lease acquisition is atomic conditional update; only current lease owner may commit checkpoint; expired lease requeues with retry/backoff and bounded attempt count. Use the DB clock consistently for lease logic and record process clock drift.
- A query budget is consumed when external operation is issued or when charge is incurred, and persisted **before** dispatch with retry-safe request fingerprint. A retry either reuses recorded response or queries with same idempotency key and **does not reset** the budget.
- Preempt deep work cooperatively at phase boundaries, persist checkpoint, release resource; never assume arbitrary NumPy/scipy call can be preempted safely mid-call. Bound input sizes and use process isolation for methods that might exceed a hard wall-time if needed.
- T0 alert delivery has reserved short path/writer slots; T1 bounded quick work cannot be starved by T2/P1. Source port calls have timeouts and circuit breakers with explicit failure output. A slow external query does not hold SQLite write lock.
- An incident can be published provisionally, then updated with monotonic revisions and `supersedes_revision_id`; never retract or rewrite history silently.

### 5.3 Tier objectives and resource defaults (targets, not unsupported guarantees)

| Tier | When used | Measured target / cutoff | Default bound / failure behavior |
|---|---|---|---|
| **T0** | Immediate validated signal / DQ + safety | p95 < 2 s from complete canonical record at core boundary | zero blocking remote queries; cheap local validation/monitor only; publish provisional/dq or `DEGRADED` |
| **T1** | First useful answer for any task type | p95 < 10 s | graph bound + cached/pinned read + cheap estimator; no long joins, models or external experiments |
| **T2** | Bounded deep investigation | < 60 s **per attempt** p95 objective | e.g. 3 hops/100 nodes/400 edges, max 6 source requests, max 168 time buckets per source request by default, 512 MB per worker ceiling; checkpoint on budget exhaustion |
| **T3** | Approved external sandbox tests P1 | minutes+/asynchronous, independently budgeted | outbox receipt, expiry, max actions and cost, never blocks T0–T2 |
| **T4** | Feedback/revision on arrival | event-driven | idempotent reconciliation, scoped update, new task/graph version; no SLA for outcome arrival itself |

These initial caps are **configuration starting points**, not validated universal optimum. Source requests carry provider-specific rate limits and rows/bytes; budget includes queue wait, serialization and registry-check overhead. Define clock start/stop explicitly (receipt of complete canonical envelope -> durable published revision). Report p50/p95/p99 both under isolated and contended load; distinguish ingest-network latency from core runtime. P0 load suite: reference laptop specs recorded, 5,000 nodes / declared edge density, 100 simultaneous tasks, deliberate SQLite writer saturation, 1 slow/unreachable adapter, 1 repeatedly revised time bucket, and large unknown/latent neighborhoods. Report throughput, queue age, memory peak, error/degrade fraction, graph traversal percentiles and retries; no fake claim of <2 ms graph traversal.

**Production-critical 20 s note:** Historical user requirement for some other systems does **not** transfer into this blueprint: original v2.1 explicitly chose independent T0/T1/T2 targets, with T2 60 s and T3 deferred. If the deploying application actually needs a global 20 s deadline, that is a separate acceptance decision requiring a different T2 strategy, not a silent reinterpretation of this specification.

### 5.4 Deterministic replay and cache safety

- Exact reproduction means **same decisions from recorded inputs, snapshot/method/model versions, config and RNG seeds**, not byte-identical rerunning of network queries or GPU kernels. Persist canonical request/response bodies or immutable content addresses and hashes (subject to privacy policy); evidence retrieval in replay uses recorder, never fresh source.
- Cache key includes `(scope, source_data_cut, method_id/version, input content hash, graph pin, variable definition versions, parameters, seed)`. Invalidation respects revised source buckets and bitemporal definition changes; do not reuse a model/metric cache across sources or populations by ID collision.
- E-process append-only statistics must be recomputed or correctly revised when a bucket changes; provenance-root dedup is insufficient for dependence; record null/betting strategy and test time-order validity. Sequential alert guarantees only exist under the actual mathematical assumptions.

## 6. Safety, permissions, and optional prediction

### 6.1 Evidence firewall and privacy/security

- Independent evaluation-only oracle stores causal mechanisms and counterfactual truth **outside all adapters visible to Subsystem 2**, including LLM prompts, code-visible examples, IDs, and test receipt metadata. Separate access principals in evaluation execution where feasible.
- Distinguish `REAL_OBSERVED`, `EXTERNAL_STUDY`, `SANDBOX_OBSERVED`, `SYNTHETIC_GENERATED`, `SYNTHETIC_REPLICA` and `MODEL_PREDICTION` as provenance roles. Evidence can be supplied under `EvidenceClass=OBSERVED/EXPERIMENTAL/EMULATOR_OBSERVATION/MODEL_PREDICTION` for backward compatibility, but inference uses richer provenance, design, and dependence; class alone never confers authority.
- Retain lineage DAG and parent transformations; 50 replicas of one original create no extra independent support. Different simulation seeds from one generator are independent **conditional on that simulator** only if simulator design justifies that assumption, not independent real-world confirmation.
- Minimum necessary identifiers and redaction in logs; secrets never in immutable evidence. Verify adapter authorization, response integrity and deterministic retention policy before full event payload ingestion. If raw data is sensitive, store content hashes + secure references with replayability tradeoffs declared.

### 6.2 Policy and sandbox test execution (P1)

Two distinct activities: **(a)** `ActionProposal` / `DESIGN_EVIDENCE`, which does not execute; **(b)** `RUN_ALLOWED_TEST` through `TestCoordinator` and separate `TestExecutionPort`, which can only call a nonproduction sandbox/emulator. The policy gate checks trusted environment attestation (not mutable request string), allowed operations and target objects, approval token binding/expiry, maximum risk/cost, reversibility when relevant, scope/horizon, experiment integrity, rate limits and idempotency. Adapter credentials are incapable of production writes; authorization must be tested with adversarial negatives. For initial P1, permit simulator or sandbox only; production actuation is **not implemented**.

A statistically strong effect does not grant action permission. Predictions, untransported sandbox findings, unsupported assumptions, or missing approval must yield `DENY`, `HUMAN_REVIEW`, or `DESIGN_TEST_ONLY` with explicit reason. Test output is `Receipt` with environment, action/query hash, execution time, correlation ID, budgets used; feedback arrives later with scoped observations. A revoked/expired approval prevents new tests, but does not erase old receipts.

### 6.3 Sparse bounded counterfactual prediction (optional P1)

`MechanismSpec` has target variable, parents/lags, equation family, parameter point/covariance/interval, noise process, valid regimes, fitted-on lineage, feature availability, version, validation diagnostics and scope. `CounterfactualRolloutEngine` receives a pin and **only validated** mechanisms; computes bounded paired intervention/no-intervention rollouts with common random numbers keyed by source seed + node/entity/event/time + mechanism version (avoid random-state drift on changed event counts), with explicit horizon, spillover/unknown sensitivity and sample/memory/time caps. Returns forecast distributions, risk and predictive intervals plus `UNSUPPORTED_MODEL`/`OUT_OF_REGIME` abstention when justified. Never invent missing structural equations. Model-vs-emulator error evaluated on held-out scenarios/seeds/regimes; successful fit to simulator is not production causal validation. Forecasts cannot update e-process wealth, graph mechanism certification, or authorize production actions.

### 6.4 Optional P1 TabPFN / neural extension — controlled

Lazy import only via `.[tabpfn]`, no base `torch` dependency; weights pre-cached for offline use and licensed/available deployment path checked separately. Candidate uses: nonlinear residual/invariance **ranking** and covariate baseline prediction. Ineligible: causal identification, graph validity, SAVI guarantee or policy authority. Gate by device (`cuda`, `mps`, or explicit CPU fallback), dataset shape, measured memory, cached model size, remaining wall time; v2.1's `rows <= 2000`, `>=5000ms` and at most 3 calls are **initial heuristic caps**, not proof of latency or validity. Log model/weights version, seed, inputs, output summary, numerical platform; replay the *recorded* output. Keep pure NumPy/SciPy fallback.

## 7. Runtime operation sequences — diverse entrypoints, not one mandatory pipeline

### 7.1 Explicit effect question, no anomaly

1. `TaskCoordinator` accepts `ReasoningTask(ESTIMATE_EFFECT, EstimandSpec, data_cut, budget)` with no monitor/incident.
2. Planner pins graph and evidence versions, audits target/assignment/outcome scopes and provider capability manifest.
3. Registry tests randomized, quasi-experimental and/or observational method prerequisites; unsupported methods recorded with exact failures.
4. Evidence planner retrieves **only** declared typed data; source adapter reports design, unit IDs, missingness, uncertainties and provenance; never fabricates covariance/propensity.
5. Pure method computes effect/SE/diagnostics; identification audit issues *conditional effect assessment* tied to requested estimand and source environment; challenger considers attrition, interference and transport.
6. Result committed as `TaskRevision` and published through policy-vetted sink. No `MonitorDetector` invocation and no forced structural-edge promotion.

### 7.2 Change/observation diagnosis

1. Monitoring producer or explicit user query supplies event/metric/state observation and DQ/availability metadata.
2. Fast path evaluates supported DQ and cheap detection (only for suitable shapes), emitting provisional alert when warranted.
3. T1 checks exact structural identities, time echo and simple local decomposition if applicable; stores `PARTIAL` and rival explanations.
4. T2 traverses pinned graph, runs applicable invariance/DiD/association/measurement checks, selectively acquires evidence by attainable benefit; unsupported methods are logged, not faked.
5. Challenger examines observation artifacts, latent confounding, alternative explanations, time/context and data-cut limitations; result may remain inconclusive.
6. New feedback yields later revision without overwriting earlier claim.

### 7.3 Study audit (general experimentation)

1. Study design records assignment mechanism, assignment unit, observation unit, analysis unit, exposure/compliance, interference assumptions, population, outcomes, metric definitions when used and missingness.
2. SRM test operates at *assignment unit* with expected allocation and declared exclusions; an SRM pass is only one diagnostic. Cluster randomized designs use cluster-appropriate inference or `UNSUPPORTED_CLUSTER_INFERENCE`.
3. ITT analysis is allowed when assignment and outcome data support it; exposure effect requires a different valid identification strategy. Multiplicity family must be declared; missing covariance invokes allowed marginal conservative procedures, not made-up joint causal mediation claims.
4. If observational and randomized results conflict, first compare estimands/time/populations; no blanket RCT dominance. Publish what is identified, what disagrees and what measurement would distinguish hypotheses.

### 7.4 Graph onboarding and empirical identity drift

1. Receive new variable and potential edge(s) with provenance; pure type checks and graph structural checks.
2. Metric-only formula AST validation; nonmetric variable skips without false failure.
3. If asserted exact identity requires evidence, bounded source query, tolerance and population coverage check; quarantine on mismatch/unavailable proof.
4. Commit version atomically, append audit and notify subscribed tasks; old pinned investigations keep their original version.

### 7.5 Controlled test and delayed feedback (P1)

1. If two hypotheses remain observationally equivalent, planner emits `EvidenceGap` + test/measurement design; may also submit **proposal** to policy.
2. Only authenticated, explicitly authorized sandbox test intent can reach `TestCoordinator`; records idempotency outbox and spends budget before remote attempt.
3. External sandbox performs action, returns receipt; later observed feedback enters as scoped source evidence, not evaluation oracle.
4. `FeedbackReconciler` compares predictions with actual scoped outcomes and contemporaneous changes; recalibrates mechanism version or proposes graph revision **only with admissible evidence**.
5. External harness independently scores chosen action and discovered effects against oracle, without contaminating core.

### 7.6 Reconciliation and belief revision

1. Store post-intervention observation with `available_at`, provenance/dependence, action receipt and exact scope.
2. Find old pinned graph/evidence context and known concurrent interventions; do not infer causal proof from after/before alone.
3. Audit valid randomized or quasi design where possible; otherwise association/hypothesis and diagnostic plan only.
4. Commit proposed edge revision or new mechanism parameter version via controlled admission; mark affected tasks stale and create new revisions. Old evidence remains immutable.

## 8. Verification matrix — eighteen independent merge gates

Each gate has a named fixture, negative case, deterministic expected output and an independently implemented oracle/assertion wherever feasible. **Passing the implementation's own unit tests is not sufficient** for a production-critical causal certificate. Test harness must be a separate module/process unable to import internal truth flags from emulator fixtures.

| Gate | Target | Required positive and adversarial negative cases |
|---|---|---|
| **G01** | Boundary/purity | AST/import DAG, no source/vendor/LLM/oracle import; pure-method I/O monkeypatch trap; no network in pure layer. |
| **G02** | Canonical type semantics | Metric/state/event/categorical round-trips; nonmetric no time bucket/denominator; wrong subtype or metric coercion refused. |
| **G03** | Migration/backward behavior | Golden v2.1 metric and evidence fixtures match valid outputs; invalid old RCT-only certificate flagged; source bytes/hash archived. |
| **G04** | Graph edge correctness | All 13 kinds, layer/status constraints, instantaneous cycles rejected, positive-lag feedback accepted, missing adjacency UNKNOWN. |
| **G05** | Bitemporal/replay + admission | Reconstruct before/after drift; exact identity quarantine; no partial graph commit; crash-index rebuild correct. |
| **G06** | Observation and time firewall | Reporting lag, missing/selected/censored/sampled inputs, late arrival vs event time, source `available_at` leakage negative. |
| **G07** | Evidence independence | One original + 50 clones no increased support; 50 emulator seeds no production proof; dependence/cluster not inferred from roots. |
| **G08** | Statistical sequential validity | 1,000 null streams × 200 peeks with defined betting/null policy; correlated/revised/bursty streams; report empirical false-positive bounds. |
| **G09** | Metric decomposition and math | Unit/grain/pop conservation, Simpson's paradox, top-K/OTHER incompleteness, zero denominators, numerical tolerance. |
| **G10** | Capability semantic neutrality | Registry refuses event→Kitagawa, missing-panel→DiD, naive cluster RCT, arbitrary source query; rejection trace persisted. |
| **G11** | Identification and effect-vs-edge | Scoped randomized ITT and conditional DiD assessed; noncompliance prevents exposure-effect leap; RCT effect does not auto-certify mediation/structure; confounding negative. |
| **G12** | Study-specific validity | Cluster assignment/outcome mismatch handled; assignment SRM vs exposure, attrition, spillover, multiplicity and missing covariance; no surrogacy leap. |
| **G13** | Task/workflow independence | Explicit effect request without monitor; sensor event task without metrics; no monitor profile; no experimentation profile; task does not require alarm. |
| **G14** | Evidence acquisition and VoI | Provider supports only some typed shapes; unsupported returns reason; budget/latency/privacy veto; sensible low-cost discriminating query in held-out case. |
| **G15** | Durable scheduler/outbox | Kill after lease/query/persist/publish/test-intent; restart no extra query budget or duplicate visible output/test; new data yields STALE revision. |
| **G16** | Performance and isolation | Reference laptop and declared load; T0 <2s/T1 <10s/T2 attempt <60s p95 targets; p50/p95/p99, 100 tasks, writer pressure, hung adapter, graph traversal and memory. |
| **G17** | Safety and prediction | Nonproduction allowlist, expired/untrusted permission, malicious scope switching, denied production; predictions/rollouts cannot promote evidence; out-of-regime abstain. |
| **G18** | Generalization and independent scoring | At least 12 diverse scenarios, held-out mechanisms/seeds, oracle outside core; equivalent evidence/action budgets vs fixed analytics baseline; report false identification, abstention, quality, regret and cost. |

**Release rule:** P0 release requires G01–G16 and non-P1 portions of G18; G17's no-production surface is mandatory even though execution is P1. P1 release adds remaining G17/G18. Any failed evidence-firewall, causal certification, execution safety or replay gate is a **blocker**, not a known-warning waiver. Performance target misses prompt workload and architecture diagnosis rather than silent removal of bounds.

## 9. Demonstration portfolio and eval design

| # | Scenario | Capability at issue | Correct behavior / failure to avoid |
|---|---|---|---|
| 01 | Delayed reporting masks a real metric drop | DQ vs domain | Separate reporting defect from possible domain impact; no future availability leak |
| 02 | Conversion Simpson reversal | Structural/Kitagawa | Explain mix vs within-group rate, quantify uncertainty and truncation; not “no harm” |
| 03 | Stable total, deteriorating subgroup | Distribution/coverage | Investigate hidden minority decline without mandatory aggregate anomaly |
| 04 | Lagged fulfillment/quality/demand cycle | Temporal / RCD-lite | Hypothesis + delayed feedback, explicit confounders, no causal proof from lag alone |
| 05 | Promotion policy incrementality | Effect estimation | Distinguish net effect from observational sales lift; own population and horizon |
| 06 | Valid randomized study, no anomaly | Study / effect | Conditional ITT for declared estimand; cannot require monitoring |
| 07 | Cluster randomized trial with outcome-level data | Design audit | Appropriate cluster SE or unsupported, not naive IID SE |
| 08 | RCT vs observational estimates differ by estimand | Identification | Flag incomparable targets before preferring a source |
| 09 | Missing covariance suggests tempting mediator claim | Multiplicity/claims | Marginal effect can survive while surrogate/mediation claim blocked |
| 10 | Two observationally equivalent causes | Active evidence design | Abstain; select feasible discriminating measurement/test, cost-aware |
| 11 | Non-metric industrial state/event oscillation | Nonmetric inference | Graph + lag/invariance without KPI or time buckets; unsupported equilibrium explicit |
| 12 | Post-action forecast drift in sandbox | T3/T4 / rollout | Capture versioned error, recalibration and new decision; no production evidence promotion |

**Evaluation protocol:** Independent harness holds hidden mechanisms, generator seeds, counterfactual truth and benchmark answer keys. Scenario IDs/metric names must not leak answers. Include negative controls, no-effect, selection/censoring bias, hidden confounding, invalid pretrends and out-of-regime cases. Split by **mechanism family and regimes**, not only random seeds; seeds alone may leak a generator's fixed assumptions. Baselines get identical source observations, query limits, actions and time budgets (a standard monitor + fixed SQL checks; plus a method-matched statistics-only baseline for direct questions). Report exact target and denominator for root cause ranking, causal false-certification rate, calibration/coverage, DQ false alarm, valid abstention, action regret, query/runtime cost and latency. **No acceptance based solely on plausible narrative or cosmetic graph.** P0 must prove subset 01/02/03/06/07/08/10/11; P1 adds 04/05/09/12 where supported.

## 10. Engineering tradeoffs and architectural decisions (ADRs)

| ADR | Decision | Benefit | Cost / explicit failure trigger |
|---|---|---|---|
| A01 | Python modular monolith + frozen Pydantic contracts | Local reproducibility, clarity, cheap integration | Runtime overhead; measure before vectorizing/rewriting |
| A02 | SQLite WAL with one writer and outbox | Transactions, restart safety, low ops burden | Writer throughput ceiling; move only on benchmark evidence |
| A03 | Immutable graph deltas + pinned views | Reproducible historical claims; no deep-copy on every task | Index and version management; benchmark time/memory |
| A04 | Finite typed capability registry | Explicit assumptions, testable refusals | More schema/code for each new data shape; intentional safety cost |
| A05 | Question-first planner with optional monitoring producer | Reuse for experiments, sensors, marketplace; avoids hidden anomaly-first | FSM complexity; constrain states and keep pure |
| A06 | Method-specific identification vs evidence label | Valid conditional non-RCT methods, no authority confusion | Harder audit; conservative unsupported paths |
| A07 | Preserve metric subtype/legacy adapters | No regression in validated decomposition + existing feeds | Temporary compatibility adapter maintenance; sunset only with dual-run evidence |
| A08 | Strict origin/scope and oracle isolation | Prevents simulator-induced apparent “causal skill” | Extra data lineage/policy work; essential not optional |
| A09 | Stage-based budgets and partial results | Fast useful output with deeper later reasoning | Versioned revisions/complex checkpointing |
| A10 | Optional sparse SCM, not second emulator | Fast local what-if with uncertainty | Misspecification; never used as empirical validation |
| A11 | Nonproduction test port as only write surface | Controlled closed-loop learning without production risk | Cannot autonomously operate live marketplace; intentional P1 limit |
| A12 | Avoid universal solver and generic query DSL | Small auditable build with truthful gaps | Explicit capabilities needed for new causal regimes |

## 11. Non-goals, assumptions, and definitions of done

**Non-goals:** a monolithic causal discovery oracle, automatic identification from graphs with unknown edges, a generic SQL/LLM tool executor, obligatory sequential monitoring for every task, arbitrary physical equilibrium models, mandatory neural inference, a second full emulator, distributed task queues in P0, automatic live production writes, or guaranteed performance independent of hardware and provider latency.

**External assumptions:** every input adapter can attest its schema, transformation lineage, and environment; source clocks and study-design claims may be incomplete and must be tagged so; analytics methods are valid only on supported shapes/regimes; invalid provider manifests are observable failures, not silent feature disablement.

**Done means:** (a) exactly the three diagrammed views are consistent with code contracts; (b) all P0 gates pass with independently authored negative tests; (c) v2.1 valid behavior reproduced on pinned fixtures and corrected behavior explicitly reported; (d) a direct experiment effect question and a nonmetric sensor question both run without monitoring, while a marketplace metric diagnosis still works; (e) every output is traceable to source data cut, evidence IDs, graph/method versions, assumptions and limits; (f) known uncertainties and unsupported cases are reported rather than hidden; (g) output publication survives retry without duplicates where sinks honor idempotency; (h) performance metrics are actual measurements with machine/load context; and (i) **user approval is obtained at the architecture/contract, identification, safety, and deployment checkpoints before any production-critical change**.
