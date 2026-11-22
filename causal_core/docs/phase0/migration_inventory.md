# Phase 0 — Migration Inventory, Correction List and Approval Items

**Status:** APPROVED 2026-11-21 (A1–A5, plan §B exit gate). Phase 1 may start.
**Mode:** *specification comparison only*. No v2.1 implementation, port adapter, SQL schema or
test result exists anywhere, so nothing below is a "golden capture". Expected values come from
the independent oracle `evaluation/oracle.py` (stdlib only), checked against hand arithmetic in
`tests/test_phase0.py`.

| Artifact | Path | What it is |
|---|---|---|
| Frozen spec corpus | `docs/baseline/` + `SHA256SUMS` | The 6 design documents, byte copies, write-protected |
| Reference fixtures | `tests/fixtures/v21/` + `MANIFEST.sha256` | 11 cases (JSON input + expectations, CSV data where emulated) |
| Hidden truth | `evaluation/truth/` | Emulator mechanism parameters; never inside the fixture dir |
| Rule classification | `docs/phase0/rule_classification.csv` | 48 v2.1 rules → KEEP 21 · FIX 18 · SPECIALIZE 5 · DEFER 4 |
| Method preconditions | `docs/phase0/methods_preconditions.csv` | 12 P0 + 5 P1 methods: shapes, checks, v2.1 vs v3.2 refusal behavior |
| Benchmark baseline | `docs/phase0/baseline_benchmarks.json` | Machine facts captured; every tier measurement `MISSING` with reason |

---

## 1. Contract inventory (v1.0 / v1.1 / v1.2 → v2.0)

Sources: v2.1 §3.1–3.3. The target is v3.2 §2.1 and §2.4.

| v2.1 contract | Ver. | Fields as specified in v2.1 | v2.0 target | Action |
|---|---|---|---|---|
| `MetricDefinition` + `FormulaNode` | 1.0 | id, unit, aggregation kind, `TimeGrain`, `PopulationCoverage`, optional AST | `MetricDefinition` subtype of `VariableDefinition`; aggregation/population/formula/time **conditional** | Lossless; keep original bytes |
| *(none)* | — | — | `StateVariableDefinition`, `EventVariableDefinition`, `CategoricalVariableDefinition` | New |
| `EntitySpec` / `DimensionSpec` | 1.0 | opaque keys, cardinality, hierarchy, top-K + OTHER | Same, plus match confidence and partition policy | Keep; truncation explicit |
| `Observation` + `SufficientStats` | 1.0 | numerator, denominator, n, SS/SE, `event_time`, `as_of`, `revision_id` | `Observation` with typed `ValuePayload` (`AggregateStats`, …) and conditional clocks with KNOWN/ESTIMATED/UNKNOWN | Map; never infer IID or covariance |
| `InterventionEvent` | 1.0 | scoped change, `[started_at, ended_at]` | `InterventionSpec` inside `StudyDesign` or a graph INTERVENTION node | Map |
| `ExperimentRecord` | 1.0 | randomization unit, exposure unit, **`srm_p_value`**, propensities, effects, optional covariances | `StudyDesign` (randomized subtype) + `AssignmentOutcomes` payload | Map when attested, else quarantine (see D6) |
| `ObservationMechanismSpec` + `DataQualityStatus` | 1.0 | SAMPLING, SELECTION, CENSORING, MEASUREMENT, REPORTING_LAG; completeness watermarks | Same, observation layer | Keep |
| `GraphNode` | 1.1 | kind ∈ {METRIC, ENTITY, INTERVENTION, EXPERIMENT, OBSERVATION_MECHANISM, LATENT}, layer, population, versions | kind ∈ {VARIABLE, ENTITY, INTERVENTION, STUDY, OBSERVATION_MECHANISM, LATENT, EFFECT_CLAIM} | METRIC→VARIABLE(metric), EXPERIMENT→STUDY |
| `GraphEdge` | 1.1 | 13 kinds, layer, status {ACTIVE, QUARANTINED, SUPERSEDED, REFUTED}, bitemporal, lineage, lag bounds, certificate id | Same 13 kinds; + `REVIEW_REQUIRED` for historical certificates; `EffectClaim` split | Map (fixture 09) |
| `ProvenanceLineage` | 1.1 | evidence ids, source id, origin_kind {original, synthetic_replica, synthetic_generated}, root, parents, generator version | origin_kind adds {model_prediction, external_study}; content + transformation hashes | Extend |
| `EProcessState` | 1.1 | log_wealth, peak_log_wealth, independent_lineage_count, seen roots, last_bucket_end, alpha | Same + declared null, betting rule, revision-recompute policy | Extend (fixture 08) |
| `InvestigationResult` | 1.1 | identifiability audit, diagnostics, lineage roots used, partial_cutoff | `Finding` / `TaskRevision` / `EvidenceGap` | Specialize |
| `EvidenceRecord.evidence_class` | 1.2 | OBSERVED, EXPERIMENTAL, EMULATOR_OBSERVATION, MODEL_PREDICTION | Kept for compatibility; authority from provenance role + claim kind | Specialize |
| `EnvironmentScope` | 1.2 | id, kind {production, staging, emulator, offline_replay}, data_cut, generator version | Same + source attestation; missing → `LEGACY_UNSCOPED` | Keep |
| `WorkItem` / `Checkpoint` / `IncidentRevision` | 1.2 | stage, priority, lease, retry, cursors, pins, idempotency key, supersedes | `WorkItem`, `Checkpoint`, `TaskRevision` (incident is a specialization) | Map |
| `MechanismSpec` / `CounterfactualPrediction` | 1.2 (P1) | target, parent lags, equation family, parameters, noise, regime, lineage | Same; CRN keyed per entity/event/time/version | P1 |
| `EmulatorExperimentRequest` / `Receipt` | 1.2 (P1) | allowlist, action schema, approval token, idempotency key | `TestExecutionPort` request/receipt with trusted attestation | P1, fixture 10 |

## 2. Ports and enums as specified in v2.1

- **`EvidenceQueryPort`** ("exactly 6 capabilities", §3.2). Methods named anywhere in the corpus:
  `fetch_metric_history`, `fetch_segment_breakdown(bucket_grain)`, `fetch_interventions`,
  `fetch_experiments(include_covariance)`, plus the capability flag `EXPERIMENT_COVARIANCE`.
  `QueryBudget.max_buckets = 168`. **Only four methods are named: see D1.**
- **Graph-internal reads** (metric-semantics sequence diagram): `fetch_nodes`, `find_grain_siblings`.
- **Other ports:** `EmulatorExperimentPort` (P1, write-capable), `HypothesisProposerPort` (optional
  LLM, 3 s cap), `OutputSinkPort`.
- **EdgeKind (13):** EXACT_ARITHMETIC, AGGREGATION_COMPOSITION, DIMENSIONAL_DECOMPOSITION,
  ENTITY_RELATIONSHIP, TEMPORAL_WINDOW, TEMPORAL_LAG, TEMPORAL_FEEDBACK, OBSERVATION_EFFECT,
  OBSERVATIONAL_ASSOCIATION, CAUSAL_HYPOTHESIS, CAUSALLY_IDENTIFIED, LATENT_CONFOUNDING,
  UNKNOWN_RELATIONSHIP.
- **v2.1 result labels → v3.2 claim kinds:**

  | v2.1 label | v3.2 claim kind |
  |---|---|
  | MIX_COMPOSITION_EXPLAINED | MATHEMATICAL_IDENTITY |
  | INTERVENTION_SUPPORTED | CAUSAL_EFFECT_CONDITIONAL |
  | DATA_QUALITY_FAILURE / OBSERVATION_ARTIFACT | DQ_FAILURE |
  | INCONCLUSIVE_ABSTAINED | INCONCLUSIVE, or ASSOCIATION + EvidenceGap |
  | OBSERVATIONAL_HYPOTHESIS | STRUCTURAL_CAUSAL_HYPOTHESIS |
  | CAUSALLY_IDENTIFIED (from a certificate) | CAUSAL_EFFECT_CONDITIONAL; a structural edge only with separate evidence |

## 3. Rule classification

See `rule_classification.csv` (48 rows). Headline: the statistics v2.1 chose are mostly **kept**.
What changes is **what a result is allowed to claim**: effect vs edge, conditional vs certified,
scope, and time availability.

## 4. Correction list (behavior that will intentionally differ from v2.1)

Each correction is pinned by a frozen fixture whose `behavior.status` is `CORRECTED`.

| Fixture | v2.1 conclusion | v3.2 conclusion | Why (ID) | Concrete number from the fixture |
|---|---|---|---|---|
| 03 | Replay shows anything with `observed_at ≤ t` | Needs KNOWN `available_at ≤ t` | C09 | o2 would leak; o3/o4 unverifiable |
| 04 | Certificate → `CAUSALLY_IDENTIFIED` edge | Scoped `EffectClaim`; SRM computed at the assignment unit | F05, D6 | ITT +1.00pp, 95% CI [0.15, 1.85]pp, SRM p = 0.57 |
| 05 | Unit mismatch → study invalid | Cluster-level inference or `UNSUPPORTED_CLUSTER_INFERENCE` | F04 | Naive SE 0.061 refused. Cluster t (df 18, `SUPPORTED_WITH_LIMITATIONS`): 0.04, SE 0.104, p = 0.71. Variance inflation ×2.9. Policy: < 4 clusters/arm → unsupported; < 15/arm or unequal sizes → with limitations |
| 06 | Pre-trend pass → `INTERVENTION_SUPPORTED` | `CAUSAL_EFFECT_CONDITIONAL` + untestable assumptions | C04 | ATT 1.32, CI [1.04, 1.61] |
| 08 | Root dedup; revision rule unspecified | Recompute the path on revision; one contribution per bucket | C03 | log-wealth 0.85 (no alarm) vs 3.15 first-seen-root (false alarm) vs 24.5 unguarded |
| 09 | Old certificates implicitly authoritative | `REVIEW_REQUIRED` until re-audited | §2.4 | 3 receipts with original sha256 |
| 10 | Scope read from `environment_kind` string | Trusted attestation + allowlist + bound approval | F12, C10 | Spoofed request: v2.1 ALLOW → v3.2 DENY |
| 11 | Unit alignment pass → certified exposure edge | `EffectClaim` for Z→Y only; CACE as a separate IV claim | C01, F05 | ITT 0.59; as-treated 1.27 refused; Wald 0.96 ± 0.07 |

Preserved (same conclusion): 01 ratio identity, 02 Simpson/Kitagawa, 07 unidentifiable confounder.

## 5. Discrepancies found while freezing

| ID | Finding | Proposed resolution |
|---|---|---|
| **D1** | v2.1 says `EvidenceQueryPort` keeps "exactly 6 capabilities" but only four methods are ever named (`fetch_metric_history`, `fetch_segment_breakdown`, `fetch_interventions`, `fetch_experiments`) plus the `EXPERIMENT_COVARIANCE` flag | Legacy adapter implements the four named methods + covariance flag. Confirm whether two more existed in an earlier draft |
| **D2** | v2.1 §3.4 describes the SQLite tables in prose; no DDL exists | Phase 3 schema is new, not a migration. Nothing to freeze |
| **D3** | No v2.1 code, adapters or test results exist | "Specification comparison only" (plan §B). All expectations are oracle-derived |
| **D4** | **Gate IDs collide.** v2.1 G3 = synthetic non-inflation, but v3.2 G03 = migration. v2.1 G7 = replay, but v3.2 G07 = evidence independence | Use v3.2 IDs everywhere. Mapping below |
| **D5** | v2.1 never specifies the e-process null or betting rule, so its sequential *behavior* cannot be frozen | Fixture 08 declares a valid fixed-bet Gaussian martingale as the reference |
| **D6** | `ExperimentRecord.srm_p_value` is an *input*: v2.1 trusted the source's SRM | v3.2 computes SRM at the declared assignment unit; a source value is only cross-checked |
| **D7** | v2.1 §7 names specific TabPFN model variants and footprints; vendor facts go stale | DEFER; re-verify at P1 before use |
| **D8** | TRACE pins `scipy ~1.15`, which doesn't import on macOS 27. This machine's Python 3.11.12 already has SciPy 1.17.1 / Pydantic 2.14.1 | Phase 1 gets its own `pyproject.toml` with SciPy ≥ 1.16. Phase 0 stays stdlib-only |

**Gate ID mapping (D4):**

| v2.1 gate | v3.2 gate |
|---|---|
| G1 purity | G01 |
| G2 contracts / provenance | G02 + G03 + G07 |
| G3 synthetic non-inflation | **G07** |
| G4 DQ firewall | G06 |
| G5 open-world | G11 (+ G04) |
| G6 sequential validity | G08 |
| G7 bitemporal replay | **G05** |
| G8 tier SLA | G16 |
| G9 checkpoint/restart | G15 |
| G10 prediction firewall | G17 |
| G11 experiment safety | G17 |
| G12 generalization | G18 |

## 6. Preservation policy (proposed)

Because v2.1 is a specification, "valid legacy behavior" means **its stated mathematics and
thresholds**, frozen as fixtures:
- Kitagawa formula
- identity tolerance 1e-4
- DQ completeness threshold 0.95
- `max_buckets = 168`
- traversal caps 3 hops / 100 nodes
- T0/T1/T2 targets 2 s / 10 s / 60 s
- `LEGACY_UNSCOPED`
- `HYPOTHESIZED_CAUSAL` → `CAUSAL_HYPOTHESIS`

Any Phase 1+ result that differs from a `PRESERVED` fixture is a regression. Any intentional
change requires a new `CORRECTED` fixture and your sign-off, never an edited expectation.

## 7. Exit gate — items for your approval

- **A1 Schema direction:** `VariableDefinition` canonical, `MetricDefinition` a subtype; one-way
  v1.x → v2.0 migrations with byte receipts (§1).
- **A2 Preservation policy** as in §6.
- **A3 Correction list** as in §4 (8 corrected fixtures), including the cluster-support thresholds
  used by fixture 05.
- **A4 Discrepancy resolutions** D1, D5, D6.
- **A5 Location:** the project lives at `experimentation.cop/causal_core/`, separate from TRACE.
  TRACE is a candidate read-only data source for slice S2 (Phase 6), not a dependency.

**Decision 2026-11-21:** A1–A5 approved by the project owner as written, including the
cluster-support thresholds behind fixture 05 and the D1/D5/D6 resolutions.
