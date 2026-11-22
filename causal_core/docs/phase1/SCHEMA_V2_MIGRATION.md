# Phase 1 — Canonical Schema v2.0 and Migration

**Status:** APPROVED 2026-11-21 at commit `38a5081` (checkpoint A, A-1 to A-5 as written). Later provisional changes: §10.
**Demo:** `python3 -m scripts.phase1_demo`. **Tests:** `python3 -m unittest discover -s tests -t .`
(33 tests: Phase 0's 12, unchanged and green, plus 21 Phase 1 tests).

## 1. What v2.0 contains

| Module (`causal_core/contracts/`) | Types |
|---|---|
| `common.py` | `Contract` base, `Clock` (KNOWN / ESTIMATED / UNKNOWN), `TimeWindow`, `EnvironmentScope`, `SourceAttestation`, `ProvenanceLineage`, namespaced `extensions` |
| `variables.py` | `VariableDefinition` = Metric \| State \| Event \| Categorical; value schemas; formula AST (`ref` / `const` / `binop` add·sub·mul·div only) |
| `observations.py` | `Observation`, 8 payload shapes, `ObservationClocks`, `DataQuality`, `ObservationMechanismSpec` |
| `studies.py` | `EstimandSpec`; `StudyDesign` = Randomized \| QuasiExperimental \| Observational \| Descriptive |
| `evidence.py` | `EvidenceRecord`, `IdentificationAssessment`, `EffectClaim`, `Uncertainty`, `EvidenceGap` |
| `graph.py` | `GraphNode`, `GraphEdge` (13 kinds; per-edge rules; status adds `REVIEW_REQUIRED`) |
| `tasks.py` | `ReasoningTask` with 10 question kinds, `ExecutionBudget`, `TaskRevision`, `WorkItem`, `Checkpoint`, `Finding` |
| `capabilities.py` | 9 typed evidence requests, `CapabilityResponse`, `ProviderManifest` |
| `ports.py` | `EvidenceAccessPort`, `OutputSinkPort`, `HypothesisProposerPort`. None has a write/execute method |

**Public API (`causal_core/api.py`):**
- `parse_definition`, `parse_observation(raw, definition=None)`, `parse_study_design`,
  `parse_reasoning_task`, `parse_graph_edge`
- `migrate_legacy_record`, `assess_contract_compatibility`, `replay_visibility`

All are pure and do no I/O.

## 2. Parsing rules

- **Frozen, closed, strict.** Unknown fields are errors. There's no coercion: `"3"`≠3, `3.0`≠int, and a
  naive datetime is rejected.
- **One entry path.** Dicts are serialized and validated as JSON, so a dict and its bytes behave identically.
- **Failures** raise `ContractError(code, details)`:

| Code | Meaning |
|---|---|
| `NOT_JSON` | Input is not JSON text or a JSON-serializable dict |
| `SCHEMA_INVALID` | Fails a contract rule; details carry the field path and Pydantic error type |
| `VARIABLE_MISMATCH` | Observation is for a different variable than the definition |
| `SHAPE_NOT_ALLOWED_FOR_KIND` | Payload shape is not allowed for the variable kind (e.g. events as a rate) |
| `PAYLOAD_VIOLATES_DEFINITION` | Value outside declared range or categories; undeclared or missing event fields |

## 3. Variable kinds and conditional fields

| Kind | Required beyond the universal six* | Allowed payload shapes |
|---|---|---|
| metric | numeric `value_schema` | scalar, aggregate_stats, time_series, panel, assignment_outcomes |
| state | numeric or categorical `value_schema` | scalar, categorical, time_series, state_trajectory, panel |
| event | event payload schema with ≥1 identity field | event_batch only |
| categorical | category list (order and exhaustiveness are *not* assumed) | categorical only |

\*The universal six are: `schema_version`, `variable_id`, `kind`, `value_schema`, `scope`, `provenance`.

Metric-only fields (`aggregation_kind`, `population_coverage`, `time_semantics`, `formula_ast`,
`identity_tolerance`) exist only on `MetricDefinition`, and even there they are optional. The
operation that needs a field asks for it via `assess_contract_compatibility`:

| Operation | Needs |
|---|---|
| `exact_identity` | metric with formula + unit + population + time semantics |
| `mix_rate_decomposition` | ratio metric with formula + population |
| `time_bucketing` | metric with time semantics. Events and states need a *derived* MetricDefinition with a declared operator |
| `sequential_monitoring` | metric with time semantics, or a numeric state with a sampling cadence |
| `leakage_safe_replay` | an observation whose `available_at` is KNOWN |

## 4. Clocks and provenance

- **Clock values.** `UNKNOWN` clocks carry no value; KNOWN and ESTIMATED clocks must have one.
- **Ordering.** `available_at` may not precede `observed_at` when both are KNOWN.
- **Replay.** Replay is `VISIBLE` only when `available_at` is KNOWN and at or before the replay time. ESTIMATED,
  UNKNOWN or absent → `REFUSED`. Timeless observations are allowed but not replayable.
- **Origin rules.** Synthetic or predicted origins need a `generator_version`; replicas must name what they
  replicate.
- **Role vs origin.** `EvidenceRecord.provenance_role` must agree with the lineage origin:

  | Origin | Allowed role |
  |---|---|
  | model_prediction | MODEL_PREDICTION |
  | synthetic_replica | SYNTHETIC_REPLICA |
  | synthetic_generated | SYNTHETIC_GENERATED |
  | original | REAL_OBSERVED (production only) or SANDBOX_OBSERVED |
  | legacy_unknown | LEGACY_UNSCOPED |
- **No covariance number.** `AggregateStats` has no covariance field at all, only an optional
  `covariance_ref`, so absence means UNKNOWN. `iid` defaults to UNKNOWN.

## 5. Migration map (v1.0 / v1.1 / v1.2 → v2.0)

Every input gets a `MigrationReceipt` (sha256 of the exact bytes, size, versions, migrator version, time)
and keeps its original text. **Migration never raises.**

| v1 record | Outcome | Rules |
|---|---|---|
| Observation + SufficientStats | `MIGRATED` | → `AggregateStats` (numerator, denominator, n, se, window, revision kept). Scope `legacy_unscoped`; origin `legacy_unknown`; root `legacy:<sha16>`; `observed_at` from event_time; `available_at` UNKNOWN; iid / covariance UNKNOWN. Missing `n` → `QUARANTINED` |
| GraphEdge `HYPOTHESIZED_CAUSAL` | `MIGRATED` | → `CAUSAL_HYPOTHESIS`, no certification upgrade |
| GraphEdge `CAUSALLY_IDENTIFIED` | `MIGRATED_REVIEW_REQUIRED` | Status `REVIEW_REQUIRED` with the missing items (estimand, assumption audit, scope) |
| MetricDefinition | `MIGRATED` | Formula string parsed by a recursive-descent parser, never eval. Grain kept; window kind `unknown` |
| ExperimentRecord | `QUARANTINED` | Unattested randomization claims and a source-supplied SRM p-value (D6). Must be re-ingested as `StudyDesign` |
| Not UTF-8 / not JSON / wrong version / unknown kind | `QUARANTINED` | With reason; bytes hash kept |

The core's migrator reproduces Phase 0 fixture 09 exactly (test `MigrationG03`).

**Legacy port (`adapters/legacy/evidence_queries_v1.py`, D1):**

| v1 call | Typed request |
|---|---|
| `fetch_metric_history` | `get_series` |
| `fetch_segment_breakdown` | `get_strata` (`include_other=True`; `bucket_grain=None` = v1.0 single window; `max_buckets` 168) |
| `fetch_interventions` | `get_intervention_history` |
| `fetch_experiments` | `get_assignment_outcomes` (requires `study_id`) |
| anything else | refused, citing D1 |

## 6. Adversarial corpus (plan §L)

41 cases in `evaluation/adversarial_contracts.py`, each a valid cold-chain record with exactly one
defect:
- 13 categories (the plan's 10, plus task entry, edge rules and positive controls)
- 5 positive controls that must be **accepted**:
  - a state variable with no metric fields
  - an event variable with no metric fields
  - a metric with no formula/population/time
  - a cluster-randomized design whose assignment unit (site) ≠ outcome unit (order)
  - an effect question with no monitor or incident

## 7. Deliberately not in Phase 1

- **Phase 2:** cross-edge graph rules (cycles, empirical identity checks, admission).
- **Phase 3:** cross-record conflicts (same `(source, record_key, revision)` with different content), which
  need the ledger.
- **Phase 4:** method-level requirements (e.g. Kitagawa partition completeness).

## 8. Checkpoint A — approval items

- **A-1 Contract shapes** as frozen in §1 (schema `2.0`).
- **A-2 Strictness policy:** closed, frozen, no coercion, JSON-only entry, stable error codes (§2).
- **A-3 Kind/shape table:** events only as `event_batch`; bucketing events or states requires a
  derived metric definition (§3).
- **A-4 Migration policy** (§5), including quarantine of v1 `ExperimentRecord`.
- **A-5 Judgment calls I made that you may want to flip:**
  1. A legacy `event_time` becomes `observed_at` = KNOWN. The alternative is ESTIMATED; `available_at` stays UNKNOWN
     either way.
  2. Staging scope cannot carry `REAL_OBSERVED`. Staging counts as sandbox, so it is `SANDBOX_OBSERVED`.
  3. v1 `fetch_experiments` without a `study_id` is refused rather than mapped to a discovery call.

## 9. Adding a new observation shape (extension strategy)

New shapes are **schema changes, not plugins** (v3.2 §1.2: "Add new capability kinds via schema
review, not arbitrary dictionaries"). To add one:
1. Add a `Contract` class with a unique `shape` literal and its own validators in `observations.py`,
   then add it to `ValuePayload`.
2. Decide which variable kinds may carry it in `ALLOWED_SHAPES`. Anything not listed is refused with
   `SHAPE_NOT_ALLOWED_FOR_KIND`.
3. Add a definition-level check in `api._check_against_definition`, if the shape carries values.
4. Add a round-trip case and a substantive negative case to `tests/test_phase1_audit.py`
   (`SHAPES`, `SHAPE_NEGATIVES`).
5. Log the decision. Then re-freeze with `python -m scripts.export_schemas` and record the new hash.
   `SchemaFreezeT1` fails until you do.

Old readers reject the new shape (closed union), which is the intended fail-closed behaviour. A
`schema_version` bump is needed only when an existing field changes meaning.

## 10. Changes after checkpoint A (autonomous run, provisional)

Made on `autonomous/v32-run-20261121` after the user approved checkpoint A on `38a5081`. Each change
is **provisional** until reviewed. Rationale: `docs/autonomous_run/DECISION_LOG.md`; evidence:
`docs/audits/phase1/`.

| Change | Kind | Ref |
|---|---|---|
| `schema_version` required on 9 native outputs + 2 reports; manifest `expected_latency_ms` / `cost_units_per_request` (None = UNKNOWN) | contract | CP-002 |
| Every timestamp must be UTC on the wire (`UtcDatetime`); non-UTC offsets refused, not converted | contract | CP-003 |
| `EstimandSpec.source_scope` and `target_scope` both required; `requires_transport` derived | contract | CP-004 |
| New `EntitySpec`, `DimensionSpec` (+ `parse_entity_spec`, `parse_dimension_spec`) | contract | CP-005 |
| Aggregate payload ratio/mean checked against the definition's range; assignment outcomes too | validation fix | Tier 1 |
| `NaN` / `Infinity` refused on every float | validation fix | Tier 1 |
| Frozen JSON Schemas in `docs/phase1/schemas/` with drift test | deliverable | Tier 1 |

The adversarial corpus now has **43** cases. Added: D4 (non-UTC offset) and L5 (estimand without
source scope).
