# Autonomous Run — Decision Log

Run branch `autonomous/v32-run-20261121`, forked from `38a5081` (Phase 1, approved at checkpoint A by
the user before this run). Every entry below was decided **without the user present** and is
`PROVISIONAL_PENDING_USER_REVIEW` until the user approves, switches option, or reverts.

Tie-breakers, applied in order (run protocol §3):
(a) zero invariant violation (I01–I18, F01–F12, C01–C10);
(b) fail-closed epistemic safety;
(c) domain/profile neutrality;
(d) reversibility / bounded blast radius.

---

## CP-001 — Target ceiling of the autonomous run

| Field | Value |
|---|---|
| Phase | run scope |
| Risk | MEDIUM |
| Status | USER_DECIDED 2026-11-22: ceiling raised to Phase 6 / Checkpoint D — was `PROVISIONAL_PENDING_USER_REVIEW`; see *User review — 2026-11-22* |
| Git | tag `checkpoint/CP-001-target-ceiling` |

**Trigger and anchors:** the protocol's scope reads "execute sequentially through
**[SET TARGET CEILING: e.g., Phase 1–4 / Phase 1–6 (P0, up to Checkpoint D)]**". The placeholder
was never filled in. Hard limit: never cross into P1 (Phases 7–8, TabPFN, sandbox `TestExecutionPort`).
Roadmap checkpoints: B covers Phases 2+3, C Phase 4, D Phase 6 (impl. plan §K).

**Research:**
- Impl. plan §K. Checkpoint C (claim semantics: identification/certificate output policy) is the
  last checkpoint before operational wiring. Phases 5–6 consume claim semantics directly (planner
  `REPORT`, policy gate, slices).
- Plan §H exit: "If S2 cannot work with monitor fully absent or S3 without metric fields,
  architectural generality has failed". That judgement needs human review of C first.

**Options:**

| # | Option | Conformance | Safety | Reversibility | Momentum |
|---|---|---|---|---|---|
| 1 | Phase 1 audit only (literal reading of an unset ceiling) | ✓ | highest | trivial | lowest: ignores "never stall" |
| 2 | **Phases 1–4, stop at Checkpoint C** (first example given) | ✓ | high | Phases 5–6 untouched | high |
| 3 | Phases 1–6, stop at Checkpoint D | ✓ | lower: stacks 2 more phases on unreviewed claim semantics | costly to unwind | highest |

**Provisional decision: Option 2.**
- (a) All three options are conformant.
- (b) Fewer layers sit on unreviewed provisional decisions.
- (d) If C's claim semantics are overridden, nothing in Phases 5–6 needs rework.

Option 1 contradicts "never stall".

**Cost / reversibility:** If you raise the ceiling, continue from the run branch's HEAD; nothing to undo.

**Falsifiable test:** No file under `causal_core/orchestration/{planner,fsm,coordinator,policy}.py`
or a `scripts/phase5_demo.py` exists on the run branch:
`git diff --name-only 38a5081..HEAD | grep -E "orchestration/(planner|fsm|coordinator|policy)|phase[5-8]_demo"`
must print nothing.

---

## CP-002 — `schema_version` on native outputs; latency/cost on provider manifests

| Field | Value |
|---|---|
| Phase | 1 (audit finding F-2, F-8) |
| Risk | HIGH |
| Status | APPROVED_BY_USER 2026-11-22 (batch) — was `PROVISIONAL_PENDING_USER_REVIEW`; see *User review — 2026-11-22* |
| Git | tag `checkpoint/CP-002-native-output-versioning` |

**Trigger and anchors:**
- Impl. plan §C public API: "`schema_version='2.0'` mandatory for native outputs".
- Audit prompt probe D: "Native output is schema 2.0"; future evidence-access capabilities "can express
  supported typed shapes, scope, latency/cost and refusal reasons".

**Research (BEFORE probe, `audit_probe_before.txt`):**
- 9 output contracts had no `schema_version`: `EffectClaim`, `IdentificationAssessment`,
  `EvidenceRecord`, `EvidenceGap`, `TaskRevision`, `WorkItem`, `Checkpoint`, `CapabilityResponse`,
  `ProviderManifest`.
- Two internal reports (`MigrationResult`, `CompatibilityReport`) also leave the core.
- `ProviderManifest` had no latency or cost field.

**Options:**

| # | Option | Conformance | Safety | Blast radius |
|---|---|---|---|---|
| 1 | **Required `schema_version: Literal["2.0"]` on each output** | literal | a wrong or absent version is rejected | constructors must pass it (6 call sites) |
| 2 | Same field with default `"2.0"` | literal on the wire | silently stamps objects that never chose a version | none |
| 3 | Generic `OutputEnvelope{schema_version, payload}` wrapper | indirect | ok | new abstraction, every consumer unwraps |

Latency/cost: optional `expected_latency_ms` / `cost_units_per_request`, where `None` = UNKNOWN. The
alternative, making them required, would force providers to invent numbers they don't have, which
breaks the fail-closed rule.

**Provisional decision: Option 1.**
- (b) Fail-closed: an explicit version can't be silently wrong.
- (d) Additive: no field was removed, and existing wire data had no outputs yet.

The unknown-cost rule is stated in code: the Phase 5 planner must treat `None` as a penalty or veto.

**Cost / reversibility:** 11 model classes, 8 constructor call sites, 2 test payloads. Reverting is
mechanical: delete the field lines and the `schema_version="2.0"` arguments.

**Falsifiable test:** `tests/test_phase1_audit.py::NativeOutputsCP002`. It fails if any output
contract lacks a *required* `schema_version`, or accepts `"1.2"`.

---

## CP-003 — UTC on the wire: reject non-UTC offsets (no silent conversion)

| Field | Value |
|---|---|
| Phase | 1 (audit finding F-4) |
| Risk | MEDIUM |
| Status | APPROVED_BY_USER 2026-11-22 (batch) — was `PROVISIONAL_PENDING_USER_REVIEW`; see *User review — 2026-11-22* |
| Git | tag `checkpoint/CP-003-utc-on-the-wire` |

**Trigger and anchors:**
- v3.2 §2.2: "When present, use timezone-aware UTC values on the wire with original source
  timezone/conversion policy retained."
- Plan §C.6: "reject … unknown coercions".
- Comparison C09: time leakage.

**Research:**
- BEFORE probe: `2026-11-19T02:00:00+02:00` was accepted and kept as `+02:00`.
- All emulator data and Phase 0 fixtures already use `Z` or `+00:00`.
- `ObservationClocks.source_timezone` already exists to carry the original zone.
- Comparing two aware datetimes with different offsets is correct in Python, so this is about
  wire discipline and provenance, not arithmetic.

**Options:**

| # | Option | Conformance | Safety | Blast radius |
|---|---|---|---|---|
| 1 | **Reject any non-zero offset; adapters convert and record `source_timezone`** | literal | fail-closed: no silent rewrite | adapters must convert (none exist yet besides the emulator, already UTC) |
| 2 | Accept and normalize to UTC on parse | half: the instant is preserved, but the source offset is lost silently | silent coercion (§C.6) | none |
| 3 | Accept any aware offset (status quo) | contradicts "UTC on the wire" | mixed-zone records reach the ledger | none |

**Provisional decision: Option 1.**
- (b) Rejecting beats silently converting: the adapter is the one place that knows the source zone.
- (a) It keeps §C.6 "no unknown coercions" intact.

Implemented as one type, `UtcDatetime`, in `contracts/common.py`, used by every contract timestamp.

**Cost / reversibility:** All timestamp fields now go through one type alias. Switching to Option 2
means replacing `_require_utc` with a converting validator: one function, no field changes.

**Falsifiable test:**
- `tests/test_phase1_audit.py::UtcOnTheWireCP003`.
- Adversarial corpus case `D4`. Both fail if a `+02:00` value parses or is silently converted.

---

## CP-004 — `EstimandSpec` requires both source and target scope

| Field | Value |
|---|---|
| Phase | 1 (audit finding F-5) |
| Risk | MEDIUM |
| Status | APPROVED_BY_USER 2026-11-22 (batch) — was `PROVISIONAL_PENDING_USER_REVIEW`; see *User review — 2026-11-22* |
| Git | tag `checkpoint/CP-004-estimand-scopes` |

**Trigger and anchors:**
- v3.2 §2.1 table, `EstimandSpec` **Required** column: "outcome, intervention/contrast, target
  population, horizon and effect functional, source/target scope".
- I05: every causal effect claim has a *scoped* estimand.
- §4.5 item 5: sandbox identification stays sandbox-scoped, and transport needs explicit assumptions.
- Comparison C02: compare estimands before preferring a source.

**Research:**
- BEFORE probe: fields were `outcome, contrast, population, horizon, functional, target_scope`, with
  `target_scope` optional and no source scope.
- Checkpoint A was approved on that shape, but the blueprint row is unambiguous, so this corrects
  a contract contradiction rather than redesigning one.
- Blast radius: 2 test/corpus constructors. No stored v2.0 data exists outside tests.

**Options:**

| # | Option | Conformance | Safety | Blast radius |
|---|---|---|---|---|
| 1 | **Both `source_scope` and `target_scope` required; `requires_transport` derived** | literal | transport can't be implicit | 2 constructors |
| 2 | Add optional `source_scope`, keep `target_scope` optional | partial | an unscoped estimand is still possible, which violates I05 | none |
| 3 | Drop scopes from the estimand; inherit `ReasoningTask.source_scope` | partial: no target scope at all | transport unrepresentable | `EffectClaim` loses its target |

**Provisional decision: Option 1.**
- (a) I05 and the §2.1 Required column are satisfied literally.
- (b) A claim whose evidence and target differ is flagged by construction (`requires_transport`), which
  is what Phase 4's auditor needs for C02 and §4.5.5.

**Cost / reversibility:** One class in `contracts/studies.py` changes. Option 2 needs only `= None` on two fields.

**Falsifiable test:**
- `tests/test_phase1_audit.py::EstimandScopesCP004`.
- Corpus case `L5`. Both fail if an estimand parses without either scope.

---

## CP-005 — Add `EntitySpec` and `DimensionSpec` contracts

| Field | Value |
|---|---|
| Phase | 1 (audit finding F-3) |
| Risk | HIGH |
| Status | APPROVED_BY_USER 2026-11-22 (batch) — was `PROVISIONAL_PENDING_USER_REVIEW`; see *User review — 2026-11-22* |
| Git | tag `checkpoint/CP-005-entity-dimension-specs` |

**Trigger and anchors:**
- v3.2 §1.2 package tree: `variables.py` holds "…, EntitySpec, DimensionSpec".
- §2.1 table: "opaque typed IDs, source/scope and key semantics | hierarchy and match confidence,
  partition policy | Confidence<1.0 treated as exact equality [invalid]".
- §3.1 `DIMENSIONAL_DECOMPOSITION`: "Full partition incl. explicit missing/OTHER or bound on truncation;
  top_K alone not complete coverage".
- §3.3 step 3: "missing categories are not auto-coded as OTHER unless declared".
- The Phase 2 admission step 3 consumes these contracts.

**Research:**
- BEFORE probe: neither type existed.
- The v2.1 `EntitySpec` ("opaque grouping keys, cardinality hints, hierarchies") and Phase 0 fixture 02
  (an incomplete partition with 9.1% uncovered) show what partition policy must express.

**Options:**

| # | Option | Conformance | Safety | Blast radius |
|---|---|---|---|---|
| 1 | **Dedicated `EntitySpec` + `DimensionSpec` with explicit `partition_policy` ∈ {exhaustive_declared, top_k_plus_other, open_unknown}** | literal | OTHER only when declared; open dimensions are never "complete" | additive (2 types, 2 parsers) |
| 2 | Reuse `CategoricalValueSchema` for dimensions | partial: no partition policy, no entity hierarchy | completeness implicit | none |
| 3 | Defer to Phase 2 | delays a Phase 1 contract | Phase 2 admission would invent its own shape | none now, rework later |

**Provisional decision: Option 1.**
- (a) It matches §2.1 and §3.1 literally.
- (b) `open_unknown` refuses an OTHER code: the remainder is unknown, not OTHER.
- (d) The change is additive, with new parsers `parse_entity_spec` and `parse_dimension_spec`, so it can
  be extended without breaking anything.

**Cost / reversibility:** 2 classes in `contracts/variables.py` and 2 parser functions in `api.py`.
Phase 2 and 4 consume `complete_partition` and `parent_is_exact`. Reverting removes only additive code.

**Falsifiable test:** `tests/test_phase1_audit.py::EntityDimensionCP005`. It fails if:
- a top-K partition without a declared OTHER parses;
- an open dimension claims completeness;
- a confidence-0.8 parent counts as exact.

---

## CP-006 — Graph store: MVCC version lists (not copy-on-write, not NetworkX copies)

| Field | Value |
|---|---|
| Phase | 2 |
| Risk | HIGH (storage strategy) |
| Status | APPROVED_BY_USER 2026-11-22 (batch) — was `PROVISIONAL_PENDING_USER_REVIEW`; see *User review — 2026-11-22* |
| Git | tag `checkpoint/CP-006-mvcc-store` |

**Trigger and anchors:**
- Plan §D.3: "First benchmark implementation options: copy-on-write adjacency+version tables vs persistent
  indexes. O(1) pin handle only".
- v3.2 §3.2: "Avoid deep-copying NetworkX on each mutation".
- F08: no fabricated latency guarantee.

**Research:** A micro-benchmark (scratch `cp006_bench.py`) used 5k nodes and 20k edges, then 200 commits of 25 revisions each:

| Option | ms/commit | Memory after 201 versions | Out-edge read |
|---|---|---|---|
| MVCC | 0.154 | 7.8 MiB | 0.88 µs |
| Copy-on-write | 0.290 | 25.2 MiB (3.2×, O(nodes) per commit) | 0.72 µs |

The full profile (`scripts/phase2_profile.py`, `docs/phase2/graph_profile.json`) shows:
- pin: 750 ns p50
- bounded traversal: p95 0.156 ms
- memory: 73.8 → 100.4 MiB across 10 versions, i.e. +2.7 MiB per 1,000 revised edges

**Options:**

| # | Option | Pin | Memory growth | Reads | Notes |
|---|---|---|---|---|---|
| 1 | **Append-only version list per node/edge + bisect** | O(1) | O(delta) | O(degree · log versions) | Rebuildable from the delta log |
| 2 | Copy-on-write adjacency dict per commit | O(1) | O(nodes) per commit | ~18% faster | Unbounded memory with versions |
| 3 | NetworkX deep copy per version | O(1) after an O(graph) copy | O(graph) per version | fast | Explicitly discouraged by §3.2 |

**Provisional decision: Option 1.**
- (a) An O(1) pin handle, as in F08 and §3.2.
- (d) Memory is bounded by the number of changes, not graph size × versions.

Sub-decision: node definitions are stored as valid-time **segments** per knowledge version. A
redefinition with `effective_from` keeps the old definition for domain times before it
(`test_redefinition_keeps_domain_time_history`). Edges carry their own validity interval plus
knowledge-time revisions.

**Cost / reversibility:**
- Everything is behind `GraphStore.apply` / `Snapshot` in `graph/store.py`; views only use the
  `Snapshot` read API.
- Swapping in copy-on-write means changing one module.
- Known cost: about 3.7 KB per edge, because edges are Pydantic objects.

**Falsifiable tests:**
- `AdmissionG05.test_rebuild_matches_live_store_at_every_version`
- `AdmissionG05.test_pinned_snapshot_is_stable_across_later_commits`
- `scripts/phase2_profile.py`. The decision is disproved if memory grows by more than O(delta) per
  version, or if pin cost grows with graph size.

---

## CP-007 — Effect claims vs structural edges: certificates bound to exact relations

| Field | Value |
|---|---|
| Phase | 2 |
| Risk | BLOCKER-class subject (I06) |
| Status | APPROVED_BY_USER 2026-11-22 (batch) — was `PROVISIONAL_PENDING_USER_REVIEW`; see *User review — 2026-11-22* |
| Git | tag `checkpoint/CP-007-certificate-binding` |

**Trigger and anchors:**
- Plan §D.2: "Separate EffectClaim artifact/node from structural identified edge … explicit
  certificate-to-estimand identity validator".
- v3.2 §3.1 (`CAUSALLY_IDENTIFIED` only for a specifically identified relationship) and §4.5 items 2 and 5.
- I06, C01, F05.

**Research:**
- Phase 1's `GraphEdge` only had a free `identification_ref`, and `EffectClaim` carries an estimand
  whose contrast is text, so nothing tied a certificate to two *graph nodes*.
- Phase 0 fixture 11 (ITT 0.59 vs exposure, as-treated 1.27) is the failure this must prevent.

**Options:**

| # | Option | I06 / C01 | Complexity | Reversibility |
|---|---|---|---|---|
| 1 | **New `StructuralCertificate{source_node, target_node, identifies, basis, scope}`.** Edge admitted only if (source, target) match exactly, `identifies` ∈ {direct_effect, mechanism_link} and scopes match. `EffectClaim`s stored as artifacts keyed by (treatment, outcome) | enforced | +1 contract, 1 validator | additive |
| 2 | `EFFECT_CLAIM` graph nodes linked to treatment/outcome by edges | enforced | more node and edge kinds in traversal | heavier |
| 3 | Accept any CONDITIONAL certificate whose outcome matches the edge target (v2.1-like) | **violates C01/I06** | low | — |

**Provisional decision: Option 1.**
- (a) It is the only option that refuses both the exposure leap (Z→Y certificate on D→Y) and the
  mechanism leap (total effect → edge).
- (d) It is additive: the `EFFECT_CLAIM` node kind stays unused, so Option 2 remains open.

Companion rule: a declared edge's scope must equal the request's attested scope
(`EDGE_SCOPE_MISMATCH`). This prevents an adapter writing into another environment (§4.5.5).

**Cost / reversibility:** `contracts/graph.py` (`StructuralCertificate`), `_certificate_problems` in
`orchestration/admission.py`, and the claim index in `graph/store.py`.

**Falsifiable tests:**
- `EffectVsEdgeG11.test_itt_certificate_cannot_become_an_exposure_or_mechanism_edge`
- `EffectVsEdgeG11.test_mechanism_certificate_in_matching_scope_is_admitted_other_scope_refused`
- `EdgesG04.test_scope_forgery_and_bad_validity_rejected`

---

## CP-008 — What counts as an instantaneous cycle; reject vs quarantine

| Field | Value |
|---|---|
| Phase | 2 |
| Risk | MEDIUM |
| Status | APPROVED_BY_USER 2026-11-22 (batch) — was `PROVISIONAL_PENDING_USER_REVIEW`; see *User review — 2026-11-22* |
| Git | tag `checkpoint/CP-008-instantaneous-dag` |

**Trigger and anchors:**
- v3.2 §3.1: `TEMPORAL_FEEDBACK` "only with strictly positive time-expanded total lag; zero-time
  equilibrium unsupported in P0".
- §3.3 step 5: "instantaneous DAG".
- §3.3 step 7: "retain questionable edges QUARANTINED".

**Research:**
- The spec doesn't list which edge kinds form the instantaneous DAG.
- Identities (`EXACT_ARITHMETIC`, `AGGREGATION_COMPOSITION`, `DIMENSIONAL_DECOMPOSITION`) and causal
  claims (`CAUSAL_HYPOTHESIS`, `CAUSALLY_IDENTIFIED`) assert same-instant dependence.
- Associations, latents and unknowns assert no direction. Observation effects live in the other layer.
- The cold-chain loop temp→mode (lag 0–300 s) plus mode→temp (feedback, 60–900 s) must stay legal.

**Options:**

| # | Instantaneous set | Effect on the cold-chain loop | Risk |
|---|---|---|---|
| 1 | **identities + causal claims + `TEMPORAL_LAG` with min lag 0** | admitted (the feedback edge has positive lag) | none observed |
| 2 | Causal claims only | admitted | permits algebraic cycles (a = b + c, b = a − c) |
| 3 | All directed edges | **rejected**, a false refusal of valid positive-lag feedback | over-refusal |

Second axis:
- **Structurally invalid edges** (endpoint/layer, cycle, scope forgery, bad validity) are
  **REJECTED**: not stored, with reasons in the verdict.
- **Evidentially questionable edges** (identity mismatch or unverified, incomplete partition,
  missing provenance) are **QUARANTINED**: stored with reasons and excluded from traversal by default.

**Provisional decision: Option 1, plus the reject/quarantine split.**
- (b) Fail-closed on true zero-lag loops.
- (a) Positive-lag feedback stays legal, as §3.1 requires.

**Cost / reversibility:** One constant (`INSTANTANEOUS_KINDS`) and `is_instantaneous` in
`graph/validation.py`.

**Falsifiable test:** `EdgesG04.test_instantaneous_cycle_rejected_positive_lag_feedback_kept`. It
fails if the positive-lag loop is refused or the zero-lag loop admitted.

---

## CP-009 — Durable graph-delta log on SQLite (ahead of the Phase 3 ledger)

| Field | Value |
|---|---|
| Phase | 2 (feeds Phase 3) |
| Risk | HIGH (persistence schema) |
| Status | APPROVED_BY_USER 2026-11-22 (batch) — was `PROVISIONAL_PENDING_USER_REVIEW`; see *User review — 2026-11-22* |
| Git | tag `checkpoint/CP-009-graph-delta-log` |

**Trigger and anchors:**
- Plan §D.6: "Implement SQLite graph-delta transaction + index rebuild after crash, and snapshot
  consistency across task resume".
- v3.2 §3.2: "Every graph write is a single SQLite transaction … never leave memory ahead of disk".
- §5.1 single writer.

**Research:**
- Phase 3 owns the full ledger (evidence, jobs, outbox).
- Phase 2 must still prove atomicity and rebuild.
- Measured: 10 versions / 29k rows commit in 0.38 s and rebuild in 0.56 s with identical digests.
- A fault injected after 1 row leaves no partial version (`InjectedCrash` → ROLLBACK).

**Options:**

| # | Option | Atomicity | Rebuild | Phase 3 fit |
|---|---|---|---|---|
| 1 | **SQLite WAL, `BEGIN IMMEDIATE`; tables `graph_versions`, `graph_deltas`, `source_records`** | single transaction | replay | becomes the ledger's graph slice |
| 2 | In-memory only until Phase 3 | none | none | defers G05 crash evidence |
| 3 | JSON-lines append file | partial-write risk | replay | second persistence mechanism |

**Provisional decision: Option 1.**
- (b) Durable before visible.
- (d) The table shapes are isolated in `storage/graph_log.py`. Phase 3 can absorb or rename them.

**Cost / reversibility:** One module, plus a schema DDL string. Nothing else touches SQLite.

**Falsifiable tests:**
- `AdmissionG05.test_crash_mid_commit_leaves_no_partial_version`
- `AdmissionG05.test_rebuild_matches_live_store_at_every_version`

---

## CP-010 — Exact-identity verification policy at admission

| Field | Value |
|---|---|
| Phase | 2 |
| Risk | MEDIUM |
| Status | APPROVED_BY_USER 2026-11-22 (batch) — was `PROVISIONAL_PENDING_USER_REVIEW`; see *User review — 2026-11-22* |
| Git | tag `checkpoint/CP-010-identity-policy` |

**Trigger and anchors:**
- v3.2 §3.3 step 6: "issue one bounded, typed, pinned source query; check tolerance/rounding, zero
  denominators, missingness and completeness. If unavailable return UNVERIFIED/quarantine".
- v2.1 tolerance 1e-4 (Phase 0 R04: KEEP).
- C09: no leakage.

**Research:**
- Phase 0 fixture 01: the production `verify_identity` agrees exactly with the stdlib oracle
  (VERIFIED, then MISMATCH 0.0025, 0/0 excluded), shown by `IdentityG09`.
- Drift demo: max residual 0.0375. Hand derivation for a 4% cancel share: 0.9 × (1/0.96 − 1) ≈ 0.0375.

**Policy (each item is a choice):**
- Exactly **one** `GetAggregate` query per admission.
- Lookback **7 days** before the data cut.
- At least **2** comparable windows; fewer → `UNVERIFIED` → `QUARANTINED`.
- Tolerance = the definition's `identity_tolerance`, else 1e-4.
- Every returned observation is re-checked with `replay_visibility` against the data cut, even if the
  provider misbehaves.
- Units must match by exact symbols (`orders/orders` = `1`; no plural normalization, no `pint`).
- Population id and grain must be identical for an *exact* identity.

**Options for the window minimum:**

| # | Minimum windows | Effect |
|---|---|---|
| 1 | 1 | A single window can match by coincidence (e.g. a stale constant) |
| 2 | **2** | Cheap. A one-window coincidence can't verify |
| 3 | ≥ 3 | Delays verification of new sources by a further reporting cycle |

**Provisional decision: Option 2 + the policy above.**
- (b) Unverified exactness is quarantined, never activated.
- The defence-in-depth leak check stops a provider from "verifying" with future data
  (`test_leaky_provider_cannot_verify_with_future_data`).

**Cost / reversibility:** These are `GraphAdmissionCoordinator(identity_min_windows=2,
identity_lookback=7d, default_tolerance=1e-4)` constructor arguments. Unit strictness lives in
`semantics/metrics.py:parse_unit`.

**Falsifiable tests:**
- `IdentityG09` (two tests)
- `AdmissionG05.test_drift_quarantines_and_history_is_kept`
- `test_unverifiable_identity_is_quarantined_not_trusted`
- `test_leaky_provider_cannot_verify_with_future_data`

---

## CP-011 — One ledger: one SQLite file, one connection, one writer lock (graph log absorbed)

| Field | Value |
|---|---|
| Phase | 3 |
| Risk | HIGH (persistence) |
| Status | APPROVED_BY_USER 2026-11-22 (batch) — was `PROVISIONAL_PENDING_USER_REVIEW`; see *User review — 2026-11-22* |
| Git | tag `checkpoint/CP-011-single-writer-ledger` |

**Trigger and anchors:**
- v3.2 §5.1: "SQLite owns durable truth … explicit `BEGIN IMMEDIATE` for one writer or a serialized
  in-process writer queue … transactions that atomically update ledger, outbox and graph version".
- Plan §E.1–E.2. ADR A02.

**Research:**
- Phase 2's `GraphLog` had its own connection.
- Two connections would mean two writers and no cross-table atomicity.
- The Phase 3 load run measured WAL growth to 4.19 MiB under a 1.5 s reader, truncated to 0 afterwards.

**Options:**

| # | Option | Atomicity | Simplicity | Throughput |
|---|---|---|---|---|
| 1 | **One connection + in-process `RLock` + `BEGIN IMMEDIATE`; `GraphLog` shares both** | all tables in one transaction | simple | bounded by one writer (A02) |
| 2 | Dedicated writer thread + bounded queue | same | more moving parts, async errors | similar |
| 3 | Separate DB files (graph.db, ledger.db) | **no cross-file atomicity** | simple | higher |

**Provisional decision: Option 1.**
- (a) It is the literal §5.1 rule.
- (d) The schema lives in `storage/schema.sql`. Moving to Option 2 changes only `Ledger.write()`.

**Cost:** The lock isn't FIFO-fair. See the CP-012 follow-up on writer priority.

**Falsifiable tests:**
- `KillNineG15.test_every_boundary_recovers_without_duplicates_or_budget_reset` (graph mid-commit kill)
- `AdmissionG05.test_crash_mid_commit_leaves_no_partial_version`

---

## CP-012 — Scheduler and lease policy

| Field | Value |
|---|---|
| Phase | 3 |
| Risk | HIGH |
| Status | APPROVED_BY_USER 2026-11-22 (batch) — was `PROVISIONAL_PENDING_USER_REVIEW (has a known follow-up)`; see *User review — 2026-11-22* |
| Git | tag `checkpoint/CP-012-scheduler-leases` |

**Trigger and anchors:**
- v3.2 §5.2 (atomic leases, owner-only commits, bounded retries, cooperative preemption, T0/T1 not starved,
  "reserved short path/writer slots", DB clock).
- I13; plan §E.3.

**Policy:**
- Leasing:
  - lease TTL 30 s, with heartbeat renewal;
  - one conditional `UPDATE` inside `BEGIN IMMEDIATE`;
  - expired leases are reaped to QUEUED, or to FAILED at `max_attempts` (default 3).
- Backoff:
  - exponential, `2·2^(attempt−1)` s, capped at 60 s;
  - measured as 2 s then 4 s in `test_failure_backoff_and_max_attempts`.
- Reserved fast capacity:
  - workers request tiers, and T0/T1 never compete with T2/T3;
  - deep leases are capped (`max_deep_leases`).
- Cooperative behaviour:
  - preemption = checkpoint + release → CHECKPOINTED;
  - cancellation = request + acknowledge at a boundary.
- Stale pins: `is_stale` uses `Snapshot.last_changed`.
- Clock: one injected clock per process (seconds). P0 is single-process, so there's no cross-host drift.

**Options for starvation protection:**

| # | Option | T0 isolation | Cost |
|---|---|---|---|
| 1 | **Tier-scoped leasing + deep-lease cap** | proven (`test_poisoned_deep_queue_cannot_stall_t0`) | simple |
| 2 | Single priority queue only | T0 waits behind leased deep work | none |
| 3 | Separate OS processes per tier | strongest | process management in P0 |

**Provisional decision: Option 1.**

**Known gap (honest, measured):** §5.2 also says "**writer** slots". Under a 20 ms writer hog with 4
deep workers and a long reader, T0 enqueue→lease measured **p50 63 ms / p95 152 ms / p99 231 ms**. That's
far inside the 2 s T0 target, but the tail is set by writer-lock contention, not by T0 priority.

Recommended follow-up, when Phase 5 creates the end-to-end T0 path:
- a priority-aware writer gate (T0 writes jump the queue);
- or a separate writer budget for T0.

**Falsifiable tests:**
- `SchedulerG15` (6 tests)
- `docs/phase3/load_profile.json`. The design is disproved if T0 is ever un-leasable while deep work is queued.

---

## CP-013 — Query budget accounting and response cache

| Field | Value |
|---|---|
| Phase | 3 |
| Risk | MEDIUM |
| Status | APPROVED_BY_USER 2026-11-22 (batch) — was `PROVISIONAL_PENDING_USER_REVIEW`; see *User review — 2026-11-22* |
| Git | tag `checkpoint/CP-013-query-budget` |

**Trigger and anchors:**
- v3.2 §5.2: "A query budget is consumed when external operation is issued … persisted **before** dispatch …
  A retry either reuses recorded response or queries with same idempotency key and does not reset the budget".
- §5.4 cache key.
- Plan §E.4.

**Policy:**
- The charge and ISSUED row commit before dispatch.
- A RESPONDED fingerprint is replayed with no charge and no dispatch.
- An ISSUED-without-response (crash after dispatch) re-dispatches under the same fingerprint without charging.
- `BudgetExhausted` is raised before anything is sent.
- Cache key = scope + data cut + request fingerprint + graph pin + definition versions + method + seed.

**Options:**

| # | Option | Double charge | Double dispatch |
|---|---|---|---|
| 1 | **Charge before dispatch; re-dispatch on ISSUED** | never | possible (at-least-once, recorded) |
| 2 | Charge after response | never | possible, and the budget overspends during a crash loop |
| 3 | Refuse to retry an ISSUED request | never | never, but the task can't finish without manual repair |

**Provisional decision: Option 1.**
- (b) Budget truth stays durable.
- Double dispatch is made visible (`dispatch_count`) rather than prevented by giving up.
- Providers should honour `request_id` for idempotency.

**Falsifiable tests:**
- `KillNineG15.test_dispatch_may_repeat_but_charge_never_does`
- `BudgetAndOutboxG15.test_budget_is_checked_before_dispatch`
- `test_cache_key_separates_cuts_and_versions`

---

## CP-014 — Transactional outbox and delivery guarantee

| Field | Value |
|---|---|
| Phase | 3 |
| Risk | HIGH |
| Status | APPROVED_BY_USER 2026-11-22 (batch) — was `PROVISIONAL_PENDING_USER_REVIEW`; see *User review — 2026-11-22* |
| Git | tag `checkpoint/CP-014-outbox` |

**Trigger and anchors:**
- v3.2 §5.1: "exactly-once-visible via unique (task_id, revision) and idempotency outbox … Never promise external
  exactly-once delivery without sink support".
- I14; plan §E.5.

**Policy:**
- The revision row and outbox row commit in one transaction.
- The idempotency key = sha256(task, revision, payload), so re-running the same work can't mint a new identity.
- Revisions must increase.
- Dispatch is SENT (attempts += 1) → publish → ACKED.
- After a crash, every non-ACKED row is re-published with the same key.
- The guarantee is reported per sink: `EXACTLY_ONCE_VISIBLE` if the sink honours keys, otherwise `AT_LEAST_ONCE`.

**Options:**

| # | Option | Visible duplicates | Honesty |
|---|---|---|---|
| 1 | **Outbox + key; guarantee depends on sink capability** | none with an idempotent sink; possible otherwise (shown) | explicit |
| 2 | Publish inside the DB transaction | partial-failure ambiguity | claims more than it can |
| 3 | Claim exactly-once regardless of sink | hidden duplicates | **violates §5.1** |

**Provisional decision: Option 1.**

**Falsifiable tests:**
- `KillNineG15`: an idempotent sink shows exactly 1 publication at every boundary.
- `test_non_idempotent_sink_shows_the_documented_duplicate`: 2 publications, 1 identity.
- `test_outbox_is_idempotent_and_monotone`.

---

## CP-015 — E-process state: revision-aware recomputation and alarm history

| Field | Value |
|---|---|
| Phase | 3 (the method itself is Phase 4) |
| Risk | HIGH (sequential validity) |
| Status | APPROVED_BY_USER 2026-11-22 (batch) — was `PROVISIONAL_PENDING_USER_REVIEW`; see *User review — 2026-11-22* |
| Git | tag `checkpoint/CP-015-eprocess-state` |

**Trigger and anchors:**
- v3.2 §2.2: "sequential wealth cannot simply be counted twice".
- §5.4; C03; plan §E.6.
- Phase 0 fixture 08 + approved D5 (fixed-bet reference) + the frozen `history_rule`.

**Policy:**
- Per stream, keep a consumed map bucket → (record, revision, root, z).
- Only `original` records count, so replicas are ignored.
- A higher revision replaces its bucket's entry and the wealth path is recomputed from scratch.
- An equal revision with different content is a conflict: quarantined.
- A change of spec (betting rule) is refused, so a new stream key is required.
- Alarm events are append-only: RAISED, and later SUPERSEDED if a revision removes the evidence.
- The betting increment is injected by the method (Phase 4), not owned by storage.

**Research:** It matches the Phase 0 oracle exactly:
- log wealth 0.85;
- 50 replicas ignored, 1 revision;
- alarm RAISED at peak 3.15, then SUPERSEDED.

That equals the frozen rule: "an alarm already published from b3r1 stays in history".

**Options:**

| # | Option | Double count | Stale value kept | History |
|---|---|---|---|---|
| 1 | **Recompute the path from the consumed map on every update** | never | never | append-only events |
| 2 | Incremental update with root dedup (v2.1-literal) | never | **yes** (fixture 08: 3.15, false alarm) | — |
| 3 | Incremental update, add revision deltas | possible under replays | no | — |

**Provisional decision: Option 1.** Cost is O(buckets) per update, bounded by the stream window.

**Falsifiable tests:** `EProcessStateG08` (3 tests: oracle match, replay idempotence, conflict and spec change).

---

## CP-016 — Evidence ingest: revision identity, quarantine, model outputs separate

| Field | Value |
|---|---|
| Phase | 3 |
| Risk | HIGH (G07/I11) |
| Status | APPROVED_BY_USER 2026-11-22 (batch) — was `PROVISIONAL_PENDING_USER_REVIEW`; see *User review — 2026-11-22* |
| Git | tag `checkpoint/CP-016-evidence-ingest` |

**Trigger and anchors:**
- v3.2 §2.2 revisions: "higher source-declared revision … adds a new observation; a duplicate content hash
  with same lineage is idempotent. Conflicting equal revision quarantined".
- I11; §5.1 evidence row; plan §E.7 ("Persist model outputs separately from evidence").

**Policy:**
- `UNIQUE(source, record_key, revision)`.
- Same content hash → IDEMPOTENT. Different hash → `evidence_quarantine` (kept, not dropped).
- `visible_observations` returns the latest revision per record with a KNOWN `available_at` ≤ the cut.
- Model predictions are refused by code **and** by a SQL `CHECK`. They go to `model_outputs`.
- Lineage parents must pre-exist (acyclic by construction).

**Options:**

| # | Option | Conflicting revision | Model outputs |
|---|---|---|---|
| 1 | **Quarantine the conflict; separate table + CHECK** | kept for review | cannot enter evidence |
| 2 | Last write wins | silently overwritten | — |
| 3 | Reject the conflict and drop it | lost | — |

**Provisional decision: Option 1.**

**Falsifiable tests:** `EvidenceLedgerG07` (3 tests).

---

## CP-017 — Capability registry, the 12-method P0 catalog, and NumPy/SciPy as core dependencies

| Field | Value |
|---|---|
| Phase | 4 |
| Risk | HIGH (G10) |
| Status | APPROVED_BY_USER 2026-11-22 (batch) — was `PROVISIONAL_PENDING_USER_REVIEW`; see *User review — 2026-11-22* |
| Git | tag `checkpoint/CP-017-capability-registry` |

**Trigger and anchors:**
- Blueprint §4.3: the descriptor fields, and "Do not register a method as 'general' by declaring every kind".
  Conformance test: semantically inapplicable inputs must be refused, naming the missing premise.
- §4.4: the 12 required P0 methods.
- Plan §F.1: matching considers "task question, input payload kind, metadata, target estimand, source
  regime and method budget, **not** defaulted task profile".
- §F.2: each method has a pure `evaluate` and explicit refusal codes. A12: no universal solver.

**Policy:**
- **One explicit table** of 12 specs (`analytics/catalog.py`), with no plugin discovery.
- **Six generic prechecks before any method code:** question kind, input shape, metadata, estimand
  family, source regime, and forecast work ≤ cap.
  - Every `Applicability` records all six in `conditions`.
  - Each failure gives a reason prefix and a candidate next query.
- **Source-regime vocabulary** = the existing `StudyDesign` discriminator: randomized /
  quasi_experimental / observational / descriptive. Only `randomized_effect_v1` and `did_v1` restrict it.
- **Cost:** `cost_estimator_id` ∈ {`constant_v1`, `linear_v1`, `n_log_n_v1`} maps input size to forecast
  work units.
  - Unknown size gives `SUPPORTED_WITH_LIMITATIONS` ("cut off at the cap"). This is the only
    registry-level use of that status.
  - Method-level limitations come back at run time as `OK_WITH_LIMITATIONS`.
- **Honest conclusion:** every spec carries its §4.4 ceiling (for example "association … never causal by
  itself"). It is surfaced before anything runs.
- **Complete refusal catalog:** `refused()` raises on an undeclared code, so the catalog is complete by
  construction. `docs/phase4/METHODS.md` is generated from the specs.
- **Dependencies:** numpy ≥ 1.26 and scipy ≥ 1.16 move from the optional `stats` extra to core
  dependencies. The Phase 0 oracle stays stdlib-only, so the differential test remains independent.

**Options:**

| # | Option | Auditable refusal trace | §F.1 conformance | Cost |
|---|---|---|---|---|
| 1 | **Static spec table + generic prechecks + typed per-method refusals** | yes: every method, every condition | yes | small |
| 2 | Each method self-selects (`can_handle()`) | no: the reasons stay inside the method | partial | small |
| 3 | Task-profile-driven selection | no | **violates** "not defaulted task profile" | small |

**Provisional decision: Option 1.**

Also in this checkpoint (Tier 1, because the spec states the fields): `cost_estimator_id`, source regime,
condition results, next queries, forecast/cap, scope limitations, and registration of
`identifiability_audit_v1`. Before this, 11 of 12 methods were registered (T1-P4-02).

**Falsifiable tests:** `CapabilityNeutralityG10` (4 tests: shape, metadata, estimand, regime, budget, trace
persisted, catalog complete, descriptor conformance), `PurityG01Phase4`.

---

## CP-018 — Sequential monitoring: fixed bet, dependence refusal, alpha split

| Field | Value |
|---|---|
| Phase | 4 |
| Risk | HIGH (G08) |
| Status | USER_DECIDED 2026-11-22: changed to a regularized two-sided bet — was `PROVISIONAL_PENDING_USER_REVIEW (has a measured power limit)`; see *User review — 2026-11-22* |
| Git | tag `checkpoint/CP-018-sequential-eprocess` |

**Trigger and anchors:**
- §4.4 `savi_eprocess_v1`: "specific null, adapted predictable bets, dependence and revision handling,
  lineage and alpha budget … else UNSUPPORTED".
- Plan §F.3: "document null and predictable betting rule; empirical Type I tests across null streams and
  realistic dependence; handle revisions and multiple-testing budget. Disable guarantees when
  assumptions fail."
- Phase 0 fixture 08 and CP-015, which already store the fixed-bet path.

**Policy:**
- **Null:** must be declared (`null_spec`). Increments are standardized, z ~ N(0, 1) under the null.
  A missing null refuses with `NULL_UNDECLARED`.
- **Bet:** fixed λ = 0.5. The log-wealth increment is λz − λ²/2 (predictable, so wealth is a test
  martingale). Alarm when log-wealth ≥ log(1/α_stream).
- **Dependence:** only `independent` is accepted. Anything else refuses with `DEPENDENCE_UNSUPPORTED`:
  the guarantee is switched off, never silently kept.
- **Alpha budget:** α / family_size per stream. This is a union bound, valid under any cross-stream
  dependence.
- **Revisions:** wealth is recomputed from the revision-aware consumed set (CP-015). The alarm history is
  append-only.

**Measured** (`docs/phase4/STATISTICS_REPORT.md` §B; 1,000 streams × 200 peeks, seed 7):

| AR(1) ρ | False-alarm rate |
|---|---|
| 0 | **0.042** ≤ 0.05 |
| 0.3 | 0.131 |
| 0.5 | 0.232 |
| 0.8 | 0.458 |

This is why dependence is refused.

**Power limit.** The drift per step is λ·shift − λ²/2, which is ≤ 0 for shifts ≤ 0.25 SD.

| Shift (SD) | Alarm rate |
|---|---|
| 0.1 | 0.142 |
| 0.2 | 0.423 |
| 0.25 | 0.620 |
| 0.3 | 0.819 |
| 0.5 | 1.000 |

Below 0.25 SD, alarms come only early and by noise; more data does not help.

**Revised and bursty streams** (G08 asks for both; report §B):

| Stream | False-alarm rate |
|---|---|
| revised (last 50 increments re-issued at peek 100), alarm on the final data | **0.043** |
| revised, ever raised (counting alarms later `SUPERSEDED`) | 0.045 |
| variance bursts (10% of peeks at SD 2, so the declared null is wrong) | **0.058 > α** |

- The ever-raised rate has no guarantee: each revision is another chance to cross.
- The method cannot detect a misspecified scale. It lists "the declared null actually holds" as untestable.

**Options:**

| # | Bet | Validity | Power on small shifts | Fits stored state (CP-015) |
|---|---|---|---|---|
| 1 | **Fixed λ = 0.5** | ✓ (measured 0.042) | weak below 0.25 SD | ✓ (fixture 08) |
| 2 | Normal-mixture martingale (mSPRT) | ✓ | grows with time for any shift > 0 | needs sufficient statistics, not one wealth path |
| 3 | Adaptive predictable plug-in (aGRAPA / ONS) | ✓ | best | more state; harder independent oracle |

| # | Dependence | Choice |
|---|---|---|
| a | **Refuse anything not declared independent** | chosen for P0 |
| b | Inflate the threshold by an effective-sample-size factor | approximate; no finite-sample guarantee |
| c | Batch-means blocks, then bet on block means | the natural upgrade |

**Provisional decision: 1 + a + union-bound split.**

**Recommended follow-ups, before any production use:**
- for small effects: option 2, or option 3 with option c;
- for scale misspecification: a robust bet (for example a sub-Gaussian or empirical-Bernstein bet);
- for revisions: a delivery policy that holds alarms until the data matures.

**Falsifiable tests:** `SequentialG08` (4 tests), report §B.

---

## CP-019 — Randomized-effect policy: SRM, attrition, clusters, compliance, interference

| Field | Value |
|---|---|
| Phase | 4 |
| Risk | HIGH (G12, C01) |
| Status | USER_DECIDED 2026-11-22: SRM warning tier added, threshold learned from history — was `PROVISIONAL_PENDING_USER_REVIEW`; see *User review — 2026-11-22* |
| Git | tag `checkpoint/CP-019-randomized-policy` |

**Trigger and anchors:**
- §4.4 `randomized_effect_v1`: "assignment integrity, SRM at assignment unit, ITT vs exposure contrast,
  attrition, cluster dependence, spillover, proper SE".
- Plan §F.4: "if cluster support unavailable, refuse rather than report naive SE".
- C01: ITT ≠ the effect of exposure.
- Phase 0 exit item A3: the cluster policy, approved by the user.

**Policy, in evaluation order:**
1. **SRM:** χ² at the assignment unit (clusters when cluster-randomized). p < **0.001** →
   `SRM_FAIL` → `INVALID_DESIGN`.
2. **Assignment integrity:** a cluster appearing in two arms → `ASSIGNMENT_INCONSISTENT`. Missing
   cluster ids → `UNSUPPORTED_CLUSTER_INFERENCE`.
3. **Clusters (A3, approved):**
   - fewer than 4 clusters in the smallest arm → refuse;
   - fewer than 15, or unequal cluster sizes → one limitation per reason;
   - inference is a pooled t on cluster means with df = G − 2.
4. **Attrition:**
   - per-arm missing-outcome rates; a two-proportion test at 0.05 →
     `DIFFERENTIAL_ATTRITION` → `PARTIAL_IDENTIFICATION`;
   - MCAR is listed as untestable whenever any outcome is missing;
   - no bounds in P0.
5. **Compliance:**
   - exposure that contradicts assignment → `NONCOMPLIANCE`, and the ATE/ATT of exposure is
     `UNIDENTIFIED`;
   - CACE only on request: a Wald IV estimate with a delta-method SE, with exclusion and
     monotonicity listed as untestable.
6. **Interference:** SUTVA is **always** listed as untestable. The wording differs when the design
   declares none (T1-P4-03; before this fix it was dropped when the design declared none).
7. **Zero variance:** refuses with `DEGENERATE_VARIANCE` (T1-P4-01; before this fix it crashed or
   returned a NaN SE).

**Options for the SRM threshold** (this is a refusal, not a flag, so false refusals block valid studies):

| # | Threshold | False refusals of healthy studies | Sensitivity |
|---|---|---|---|
| 1 | 0.05 | 1 in 20 | high |
| 2 | 0.01 | 1 in 100 | medium |
| 3 | **0.001** | 1 in 1,000 | detects only material imbalance at small n |

**Options for attrition:**
- **flag + PARTIAL** (chosen);
- Lee bounds (needs monotone attrition; P1);
- refuse (loses valid ITTs with mild attrition).

**Provisional decision:** SRM 0.001; attrition flagged at 0.05 with a PARTIAL status. A flag is cheap,
so its threshold is the sensitive one.

**Falsifiable tests:**
- `StudyValidityG12` (3), `IdentificationG11` (noncompliance), `DegenerateInputsTier1` (2);
- differential rows 04/05/11 (max diff 2.2e-7);
- adversarial cases `differential_attrition`, `spillover`, `skewed_clusters`.

---

## CP-020 — Difference-in-differences policy and trend sensitivity

| Field | Value |
|---|---|
| Phase | 4 |
| Risk | HIGH (G11, C04) |
| Status | APPROVED_BY_USER 2026-11-22 (batch) — was `PROVISIONAL_PENDING_USER_REVIEW`; see *User review — 2026-11-22* |
| Git | tag `checkpoint/CP-020-did-policy` |

**Trigger and anchors:**
- §4.4 `did_v1`: "target estimand, pre-intervention comparability, trends diagnostics, anticipation,
  timing, spillover, serial correlation".
- Plan §F.5: "parallel trends sensitivity … pretrend test is diagnostic, not proof. Return *conditional*
  identification".
- C04.

**Policy:**
- **Timing:** a single intervention time only. Staggered timing refuses with
  `STAGGERED_TIMING_UNSUPPORTED`, because two-way fixed effects are biased under heterogeneous timing
  effects.
- **Estimator:** collapse each unit to (post mean − pre mean), then a pooled two-sample t on the
  unit changes (Bertrand, Duflo & Mullainathan 2004). This removes serial correlation from the SE.
  It matches the fixture 06 oracle to 3e-13.
- **Anticipation:** the last w pre-periods are dropped.
- **Pre-trend:** a slope-difference test, reported as a **diagnostic**. Divergence →
  `PRETREND_DIVERGENCE` → `PARTIAL`.
- **Sensitivity:** the breakdown differential linear trend (the trend per period that would explain the
  whole estimate), compared with the CI on the pre-period slope difference. This is linear extrapolation,
  i.e. Rambachan & Roth's smoothness restriction with M = 0.
  - If the CI contains the breakdown trend → a `FRAGILE_TO_TRENDS` limitation.
  - **This does not change the status.**
- **Estimand:** ATT only. Asking for the ATE → `UNIDENTIFIED`.
- **Always listed as untestable:** parallel trends in the post period, no anticipation beyond the
  window, no spillover to controls, stable group composition.

**Options:**

| # | Estimator | SE validity at small G | P0 cost |
|---|---|---|---|
| 1 | **Unit-collapsed pre/post** | good | trivial |
| 2 | TWFE with cluster-robust SE | poor below ~30 clusters | small |
| 3 | Heterogeneity-robust staggered estimators | good | P1 |

| # | Sensitivity | Effect on status |
|---|---|---|
| a | **Linear-extrapolation breakdown; informs only** | none; recorded as a limitation |
| b | Full Rambachan–Roth with M > 0 | P1 |
| c | Fragility downgrades to PARTIAL | penalises noisy but valid designs |

**Provisional decision: 1 + a.**

**Measured honestly:** on the adversarial `pretrend_ok_but_violated` case the sensitivity reports
**fragile = False**. The violation starts at the intervention, and no pre-period method can see that.
This is why parallel trends stays untestable and the status is never "certified".

**Falsifiable tests:**
- `IdentificationG11` (the DiD and trend-sensitivity tests);
- differential rows for fixture 06;
- the adversarial `pretrend_ok_but_violated` case.

---

## CP-021 — Identification auditor: status rules, estimand comparability, transport

| Field | Value |
|---|---|
| Phase | 4 |
| Risk | **BLOCKER-class subject** (G11, C01, C02, I06, I09) |
| Status | USER_DECIDED 2026-11-22: declared transport assumptions keep CONDITIONAL — was `PROVISIONAL_PENDING_USER_REVIEW`; see *User review — 2026-11-22* |
| Git | tag `checkpoint/CP-021-identification-auditor` |

**Trigger and anchors:**
- §4.5: the auditor's inputs and the six statuses; "does not require RCT or blindly reject all
  unknowns" (§4.4).
- Plan §F.7; C02 (no blanket RCT override across estimands); C07 (audit only the unknowns relevant
  to the estimand).

**Policy** (first matching rule wins; the full list is in `analytics/identification.py`):
- **Method refused:** SRM or assignment → `INVALID_DESIGN`; any other refusal → `INSUFFICIENT_EVIDENCE`.
- **Association:** a descriptive design or an association method → `ASSOCIATION_ONLY`.
- **Randomized:**
  - ITT-like → `CONDITIONAL`, or `PARTIAL` with differential attrition;
  - CACE → `CONDITIONAL` (IV assumptions listed);
  - the ATE/ATT of exposure → `UNIDENTIFIED`.
- **Quasi-experimental:** ATT → `CONDITIONAL`, or `PARTIAL` on pre-trend divergence; any other functional
  → `UNIDENTIFIED`.
- **Observational** → `UNIDENTIFIED` if any of these hold:
  - an active `LATENT_CONFOUNDING` edge between treatment and outcome;
  - unknown links from unadjusted treatment causes to the outcome;
  - no adjustment set.

  Otherwise → `CONDITIONAL`, with no-unmeasured-confounding listed as untestable.
- **Transport:** source scope ≠ target scope caps the status at `PARTIAL`; transportability is listed
  as untestable.
- **`EffectClaim`:** exists only for CONDITIONAL/PARTIAL. It is an artifact; CP-007 admission refuses it
  as an edge.
- **`compare_estimands`:** any difference in population, functional, outcome, contrast, horizon or scope
  → incomparable.

**Options:**

| # | Option | Generality | Handles UNKNOWN adjacency | Auditable |
|---|---|---|---|---|
| 1 | **Design × functional rule table + bounded view checks** | P0 designs | yes (treats unknown as blocking only where relevant) | yes |
| 2 | Complete ID algorithm on an ADMG (Shpitser–Pearl) | full | no: needs a complete graph | yes, but brittle under unknowns |
| 3 | Require an RCT for any effect | — | — | **violates** §4.4 |

**Provisional decision: Option 1.**

**Known wart (open item, not fixed: it would need a schema change).** `IdentificationAssessment` has no
`blockers` field. The reason for a status (for example "DiD identifies the ATT, not the ATE") is stored as
the last entry of `assumptions_checked`.

Suggested fix: an additive `status_reasons` field, frozen through `export_schemas` (Phase 5, when the
planner consumes it).

**Falsifiable tests:**
- `IdentificationG11` (5), `EstimandComparisonC02`;
- adversarial cases `ate_vs_att` and `unmeasured_confounding`;
- the demo's abstention case.

---

## CP-022 — Value-of-information heuristic

| Field | Value |
|---|---|
| Phase | 4 |
| Risk | MEDIUM (G14) |
| Status | USER_DECIDED 2026-11-22: geometric prior on unknown latency/cost — was `PROVISIONAL_PENDING_USER_REVIEW`; see *User review — 2026-11-22* |
| Git | tag `checkpoint/CP-022-voi-heuristic` |

**Trigger and anchors:**
- §4.4 `voi_heuristic_v1`: "provider support, costs, discrimination proxies, risk, lineage novelty,
  resource limits … **no** claim of calibrated expected information gain".
- G14.

**Policy:**
- **Score** = (share of competing-hypothesis pairs the option separates) × relevance × novelty
  ÷ (1 + latency_s + cost_units) × penalty.
- **Unknown latency or cost:** × **0.5**, with a note. Never treated as free.
- **Novelty:** 0 when every lineage root has already been read.
- **Privacy:** above the allowed level → vetoed. Vetoed options are listed, not ranked.
- **Non-executable but discriminating options** → evidence gaps, recommended as a measurement or test.
- **Output label:** `PROXY_SCORE (heuristic; not a calibrated expected value of information)`.

**Options:**

| # | Option | Needs | P0 |
|---|---|---|---|
| 1 | **Proxy score** | declared separations and costs | ✓ |
| 2 | Bayesian expected information gain | calibrated likelihoods per hypothesis | not available |
| 3 | Cheapest query first | costs only | ignores discrimination |

**Provisional decision: Option 1.** The 0.5 unknown-cost penalty is an arbitrary constant and is flagged
for the user to calibrate.

**Falsifiable tests:** `EvidenceAcquisitionG14`.

---

## User review — 2026-11-22

The user reviewed the run interactively. Original entries above are kept unchanged as the BEFORE record;
each status line now names its AFTER state.

**Batch approval.** Every decision not listed below was approved as written: CP-002…CP-017 and CP-020
(including "fragile to trends" informs only and never downgrades a DiD result). Roadmap checkpoints A, B
and C: `APPROVED_BY_USER`.

### CP-001 — ceiling raised

The user asked to continue with Phases 5 and 6, each with a demo. New ceiling: Phase 6 / Checkpoint D.
The P1 hard limit stands (no Phases 7–8, no TabPFN, no sandbox `TestExecutionPort`).

### CP-018 — regularized two-sided bet (the user's words: "a regularizable parameter but can start at .25")

- BEFORE: fixed λ = 0.5, one-sided.
- AFTER: λ⁺_t = clip((k·λ0 + Σ_{s<t} z_s)/(k + t − 1), floor, cap), and the same on −z; two-sided wealth
  = average of the two sides. λ0 = 0.25; k = 100 (regularization: the prior counts as 100 observations);
  floor 0.05; cap 1.0. Each bet uses only past data, so validity under the declared null is exact (Ville).
- k = 100 was chosen from a sweep (k ∈ {1, 5, 10, 20, 50, 100, 200, ∞}); every setting is a parameter.
- Measured (report §B): iid false alarms 0.036; power at 0.25 SD in 200 peeks 0.62 → 0.83; drop of 0.25 SD
  0.00 → 0.81; 0.1 SD over 1,000 peeks 0.15 → 0.59.
- Costs, measured: AR(1) ρ 0.8 inflates more (0.458 → 0.825; still refused); variance bursts 0.058 → 0.064;
  revised "ever raised" 0.045 → 0.052 (no guarantee, as before).
- The fixed bet stays available (`bet="fixed"`) for Phase 0 fixture 08 and the Phase 3 stored streams.
- Tests: `UserDecisions20261122.test_regularized_two_sided_bet_is_valid_and_catches_drops`,
  `test_regularized_bet_matches_an_independent_stdlib_reference_and_ledger_recompute`.

### CP-019 — SRM: "start with a looser threshold then enforce a sensible lower bound", learned from data

- BEFORE: one threshold, p < 0.001 → block.
- AFTER: p < 0.001 (the floor) → block (`SRM_FAIL`, INVALID_DESIGN); p < the warning threshold → `SRM_SUSPECT`
  limitation → PARTIAL. The warning threshold starts at 0.01 and can be learned with
  `calibrate_srm_warning(past SRM p-values)`: Storey π0, the largest threshold whose estimated share of false
  warnings in the band [0.001, t) is ≤ 20%, needing ≥ 10 past experiments in the band; never below 0.001,
  never above 0.05; fewer than 100 past experiments → stay at 0.01.
- Learned from **simulated** history only (user choice); real Statsig history is a later, separate step.
- Result (report §B2): on a mixed history, keeping 0.01 would make 59% of warnings false; the calibration
  narrows the band to 0.0021. All-healthy or blatant-only histories collapse to the floor.
- Implementation fix found while scoring: the first version accepted bands with 1–2 experiments (estimated
  19% false vs truth 40–50%). `min_band = 10` added; the 20% target was not tuned to truth.
- Tests: `test_srm_has_a_warning_tier_above_a_hard_floor`, `test_srm_threshold_is_learned_from_history_and_never_below_the_floor`.

### CP-021 — "you can use conditional with assumptions"

- BEFORE: source ≠ target scope → at most PARTIAL.
- AFTER: declared transport assumptions (`assess(..., transport_assumptions=…)`) keep CONDITIONAL, each listed as
  untestable ("transport: …"); none declared → PARTIAL as before.
- Kept by the frozen spec (user confirmed): a sandbox (emulator) or unscoped source moving to another kind of
  environment stays PARTIAL even with assumptions (§4.5.5, I11).
- Gap: the blueprint also asks for transport sensitivity checks; none in P0 (noted in every such assessment).
- Tests: `test_transport_with_declared_assumptions_is_conditional`, `IdentificationG11.test_transport_caps_at_partial`.

### CP-022 — "geometric distribution on the penalty"

- BEFORE: unknown latency or cost → score × 0.5.
- AFTER: each unknown value gets a geometric prior on {1, 2, …} (seconds or cost units) whose mean is learned
  from the options in the same ranking with a known value (≥ 1; 2 when none is known); the score is the
  expectation over the prior. An unknown option scores at most half of an otherwise identical free one, and
  two unknowns cost more than one.
- Known property: by averaging, an unknown cost can score slightly above a known option at exactly the prior
  mean (mean 3: 0.324 vs 0.250).
- Test: `test_unknown_cost_gets_a_learned_geometric_prior`.

### Tier 1 found while implementing

- T1-P4-04: a CACE with differential attrition was CONDITIONAL (only the ITT branch checked it). Now the
  randomized weakeners (SRM_SUSPECT, DIFFERENTIAL_ATTRITION) downgrade both ITT and CACE to PARTIAL.

Commits: `1032aa6` (decisions), `0714e9b` (demo bar width), and the docs commit that follows.

---

## Phase 5 decisions (2026-11-22, user present; recorded as PROVISIONAL_PENDING_USER_REVIEW)

| ID | Decision | Options weighed | Chosen, and why | Test |
|---|---|---|---|---|
| CP-023 | Where study designs and monitor nulls come from | infer from data / defaults / **registered, attested `TaskContext`** | Registered. A design is never inferred from outcomes and a null is never estimated from the series it monitors; absent → `MISSING_EVIDENCE` + `EvidenceGap` | `test_missing_design_or_null_is_a_gap_not_a_default` |
| CP-024 | How the planner routes | per-domain pipelines / **question kind → payload shape table + registry prechecks** | Shape table; no domain strings in the planner (AST-checked). Only shapes with a runner are routed in Phase 5; `EXPLAIN_OBSERVATION` is wired in Phase 6 | `test_planner_has_no_domain_vocabulary` |
| CP-025 | Progressive delivery and replay | final only / **provisional T0 revision after input audit + final revision** | Two revisions through the outbox; re-running a completed task replays the recorded result (no republish). Hung providers are bounded by a per-request deadline ≤ the task's wall budget | `DurableRevisionsG15`, `FailureTaxonomyAndDeadlines` |
| CP-026 | Policy gate verdicts | allow/deny / **PUBLISH_REPORT, PUBLISH_PROVISIONAL, DESIGN_TEST_ONLY, DENY** | Any requested action → DENY in P0; an effect finding without an EffectClaim, evidence IDs or matching scope → DENY; recommendations → DESIGN_TEST_ONLY | `PolicyGate` |

Known limits: the T0 tier is a provisional revision in the same worker, not a separate fast lane (CP-012
writer-priority follow-up still open); only one provider per coordinator; `dq_observation_v1` is reported
applicable for effect questions but not run.

## Phase 6 decisions (2026-11-22, user present; PROVISIONAL_PENDING_USER_REVIEW)

| ID | Decision | Options weighed | Chosen, and why | Test |
|---|---|---|---|---|
| CP-027 | How a strata observation names its segment | add a segment field to `Observation` (schema change) / **`record_key = "<dimension>:<value>:<period>"` adapter convention** | Convention, to avoid a schema change mid-run; the adapter and planner share it. A dedicated field is an open item for a schema revision | `ThreeSlicesOneCore` |
| CP-028 | S1 order of operations | decompose everything / **DQ first, exclude immature buckets, then the exact mix/rate split** | An immature bucket is a data fault, not a domain explanation (G06/G09) | `test_every_independent_acceptance_check_is_met` |
| CP-029 | Hypothesis audit scope | causal-evidence view only / **causal-evidence + temporal views** | Lagged mechanisms live in the temporal view; a hypothesis question must see them. Still graph-only: "not tested by data in this task" is stated | S3 acceptance |

Also changed: `savi_eprocess_v1` accepts `state_trajectory` input (same validity conditions; S3's adapter declares
its 5-minute samples `ar1`, so the method refuses); the scheduler gained `lease_job` so a coordinator leases its own
job under concurrency; the SQLite sinks allow cross-thread use (one autocommit connection).

## User review of Phases 5–6 decisions — 2026-11-22

The Phase 5 and Phase 6 tables above are kept as the BEFORE record (`PROVISIONAL_PENDING_USER_REVIEW`). AFTER:

| ID | Status | Note |
|---|---|---|
| CP-023 | APPROVED_BY_USER | Follow-up: learn a monitored variable's null from a separate, older window, then freeze it |
| CP-024 | APPROVED_BY_USER | Follow-up: wire DiD, seasonal screening and association into the loop |
| CP-025 | APPROVED_BY_USER **with fix** | BEFORE: replay keyed on task id alone, so a re-ask under the same id with a later data cut silently returned the old answer. AFTER: keyed on question + data cut + scope + budget + graph pin; a re-ask continues the task's revision history (3, 4, … superseding 2); replay returns the exact revisions that work produced (REPORT checkpoint). Test: `test_reasking_with_a_later_data_cut_recomputes_and_continues_the_revision_history` |
| CP-026 | APPROVED_BY_USER **with fix** | BEFORE: any open evidence gap labelled the output DESIGN_TEST_ONLY (an unsupported forecast read as a test proposal). AFTER: DESIGN_TEST_ONLY only when the deliverable is a test/measurement design; reports that list gaps stay PUBLISH_REPORT, with the count of recommended measurements. Test: `test_reports_with_gaps_are_reports_and_only_test_designs_are_proposals` |
| CP-027 | APPROVED_BY_USER | For the demos. Before any real Statsig/Databricks adapter: add a dedicated segment field (schema revision) |
| CP-028 | APPROVED_BY_USER | — |
| CP-029 | APPROVED_BY_USER | Follow-up: test lagged hypotheses against data, not only the graph |

Validation after the fixes: 158 tests; demos 0–6; schemas; baseline hashes; truth firewall clean.

## CP-030 — Learned, frozen baseline for change monitoring (CURE-style) — USER_DECIDED 2026-11-22

**Trigger:** the user approved CP-023 with the follow-up "learn a metric's normal level from an older, separate
window, then freeze it", and asked whether a version of CURE (Statsig's CUPED + extra covariates, Lasso-selected)
could do it.

**Answer:** yes, as a regression baseline: fit "normal" on an older closed window, freeze it, monitor the residual.

**Options (measured, scratch simulations then `evaluation/phase6_cases.py`):**

| # | Option | False alarms (8-week training, α 5%) | Drop of 0.5 SD found within 30 days |
|---|---|---|---|
| 1 | naive frozen mean/SD from the training window | 8% (and weekly swings hide drops) | ~6% |
| 2 | frozen regression plugged in as if exact | **18–20%** (the fit's error is a permanent offset; still 6% with a year of training) | ~34% |
| 3 | **frozen regression, its estimation error kept in the null; normal-mixture test (mSPRT)** | **1–4%** | 23–31% |

**Decision: option 3**, as registered method `baseline_shift_v1` (catalog now 13 methods).
- Model v1: intercept + weekday indicators from UTC timestamps; residual SD from training (n − p df).
- Exact under the model: the monitored residuals' joint null covariance includes the shared fit error; the mixture
  likelihood ratio is a test martingale (Woodbury, O(p²) per point). A dense-matrix reference agrees to 1e-9.
- Lower bounds (the user's "sensible lower bound"): ≥ 8 weeks of training; training residuals must pass a lag-1
  independence check; monitored data must start after training ends. Otherwise typed refusals.
- Mixture scale τ = 0.5 residual SD (measured: 0.25 valid but weaker, power 0.22 vs 0.31). A parameter.
- The planner uses it when a monitored variable's `MonitorSpec` carries a learned baseline (registered, CP-023).

**Result on the seasonal emulator series:** the drop on day 112 is found on day 136; no alarm before it; the naive
baseline never alarms. 300 null series: 0.043 learned vs 0.177 plug-in.

**Not yet (follow-ups):** CURE's extra covariates (declared exogenous series, CUPAC-style predictions) and Lasso
selection; Student-t treatment of the estimated σ; re-learning policy (when to refresh a frozen baseline).

**Tests:** `LearnedBaselineCP030` (4). **Demo:** `phase6_demo` section [5b].

## CP-031 — Segment field on Observation (schema revision) — USER_DECIDED 2026-11-22

**Trigger:** the user asked to replace the CP-027 record_key convention with a real field before any Statsig adapter.

- AFTER: `Observation.segment: tuple[SegmentKey(dimension_id: Id, value: Text), ...] = ()`; each dimension at most
  once; free-text values so source labels survive ("Test Group 1"). Multi-dimension rows (country × platform) fit.
- Additive and optional: every existing record still parses. `schema_version` stays "2.0"; a reader built before
  this change would reject records that carry a segment (closed contracts), so producers must upgrade first.
- Re-frozen schemas (BEFORE → AFTER, sha256 prefix): Observation `2d4b79e44a63ec9d` → `e1a1289a884e3648`;
  CapabilityResponse `1d3a701ce7ac13ed` → `e56e42f9e127ee5a`; MigrationResult `107c44cbf1e3f287` → `65dfcb5ba93a918e` (both embed Observation).
  `docs/baseline/` is untouched.
- CP-027 (record_key convention) is retired: the planner and the canonical port read `segment`.
- Test: `SegmentFieldCP031`.

## CP-032 — Experiment results from per-arm summaries — PROVISIONAL_PENDING_USER_REVIEW

**Why:** Statsig Pulse returns per-group n, mean and standard error, not unit rows.

- `randomized_effect_v1` v3 accepts per-arm summaries (`evaluate_randomized_summary`). It recomputes SRM from the arm
  counts (never the source's p-value, D6) and the ITT and SE from the arm means and SEs (normal CI).
- Says what it cannot check: `UNIT_LEVEL_CHECKS_UNAVAILABLE` (attrition, noncompliance, clustering); untestable
  "no differential attrition or exclusion between arms" and "the source's arm SEs are valid"; CUPED/CURE-adjusted
  inputs are flagged. CACE and cluster designs are refused from summaries.
- Same identification rules as before (an ITT in its own scope is CONDITIONAL).

## CP-033 — Read-only Statsig adapter — PROVISIONAL_PENDING_USER_REVIEW

- `adapters/sources/statsig_port.py`: experiment details → `RandomizedDesign` (allocation from group sizes, unit from
  idType, compliance and interference "unknown"); Pulse → per-arm `AggregateStats` with `segment ((arm, group),)`,
  se = Pulse "Std" (the standard error of the mean), CUPED flagged as an observation mechanism.
- Never writes: the client only issues GET (other verbs raise before any network access).
- Never guesses the contrast: live, the control is the group Statsig marks `isControl`; with 3+ groups the caller must
  declare the pair (54 of 417 cached experiments have 3–4 groups).
- Cached Pulse files do not record which groups they compare. Found when the SRM check refused a cached crm1009
  result: the cache had been filled with "control = name contains 'control'", while Statsig marks the 20% Holdout as
  control, so the arms had been swapped. The adapter now applies the cache's own rule and refuses other contrasts.
- Availability: `available_at` = the moment of retrieval (a KNOWN upper bound); an earlier data cut sees nothing.
- Statsig's own deltas/CIs/p-values are kept aside for a cross-check only. Live check (read-only, 2026-11-22) on
  crm1009 "revenue day 0-9": core delta and CI equal Statsig's to 7e-18 at Statsig's level (1 − adjustedAlpha = 0.975);
  SRM p = 0.51. `scripts/statsig_smoke.py` prints to the terminal and writes nothing to the repository.
- Tests (offline, invented fixture; no real data committed): `StatsigAdapterCP033` (8).

Validation: 171 tests; demos 0–6; schemas re-frozen and checked; baseline hashes; fixture manifest; firewall clean.

## P1 authorization — 2026-11-22

The user wrote "Start Phase 7" after the readiness review. CP-001's ceiling moves to Phase 7 (P1, optional, plan §I).
Phase 8 (sandbox `TestExecutionPort`) is NOT authorized. TabPFN (§6.4) is not installed: it needs a large download
(PyTorch) and the user's approval first.

## Phase 7 decisions (2026-11-22; PROVISIONAL_PENDING_USER_REVIEW)

| ID | Decision | Options weighed | Chosen, and why | Test |
|---|---|---|---|---|
| CP-034 | Mechanism contracts and validation | free-form equations / **typed `MechanismSpec` (linear-Gaussian only in v1) + `CounterfactualPrediction`; VALIDATED only after a held-out pass** | §6.3 fields: parents/lags, family, parameter covariance, noise, valid regime, fitted-on lineage, feature availability, diagnostics, scope. Held-out one-step coverage must fall in the 99% binomial band (a fixed ±0.07 rejected a correct mechanism: 0.975 on 80 points). Schemas added (two new files; nothing else changed) | `MechanismContract` |
| CP-035 | Rollout engine | per-draw regime check / **average-path regime check**; shared generator / **keyed common random numbers** | Bounded subgraph through mechanisms; exogenous inputs held at last value (declared); parameters drawn from their covariance; paired worlds share keyed draws (interval 23% of independent); caps → CAP_EXCEEDED; reach sensitivity 75%/50%; emulator-fitted → production refused (I11). A per-draw 1% rule flagged in-range rollouts (≈2% of draws exceed ~100-point extremes by chance) and was replaced | `NoInventedMechanismG11`, `RegimeAndActionsG17`, `CommonRandomNumbers` |
| CP-036 | What-if questions in the planner | **utility grammar "maximize/minimize <var> over <N>d"; candidates "<var>=<v>" / "<var>+=<d>"** | Nothing is invented: an unreadable utility or candidate is ILL_DEFINED_TARGET. Predictions are written to `model_outputs` only (idempotent: ids are content hashes); findings are MODEL_PREDICTION; the policy says "not evidence, no authority to act"; all-abstain → INCONCLUSIVE | `PlannerWhatIf`, `PredictionsNeverEvidenceG07` |

**Measured (docs/phase7/calibration.json):** linear worlds (same family as the model, 37 predicted of 40): coverage
73/89/92/95% at nominal 50/80/90/95%. Saturating worlds (held-out family, 38 predicted): 24/47/74/84% — the intervals
are too narrow when the true equation has a different shape, even inside the fitted range, and one-step validation
does not catch it. Out of regime (spend 300): always abstains; an unguarded extrapolation would have covered the truth
in the linear world but missed it badly in the saturating one (1,210 vs 789).

**Open items:** multi-step / effect-level backtests or a model-discrepancy term (held-out family under-coverage);
the regime boundary is strict (spend 150 abstains when the fitted maximum is 147.3); CHECK_MODEL question route;
exogenous inputs only "hold last"; only the linear-Gaussian family; TabPFN not installed; EVALUATE_OUTCOME needs Phase 8.

Validation: 180 tests; demos 0–7; schemas; baseline hashes; fixture manifest; firewall clean (counterfactual package pure).

## User review of CP-032 … CP-036 and TabPFN — 2026-11-22

The user: "take all recommended decisions and fix issues", and confirmed TabPFN is for internal use only.

| ID | Status | BEFORE → AFTER |
|---|---|---|
| CP-032 | APPROVED_BY_USER **with fix** | BEFORE: SRM on each metric's own units. Found: in 348 of 407 cached experiments unit counts differ across metrics (e.g. 237 vs 222,195), so a filtered metric would trigger a false SRM block. The Console API `exposure_count` endpoint has only experiment totals (pastDay, past7Days), no per-group split. AFTER: SRM on assignment counts: a warehouse exposures hook when supplied (authoritative), otherwise the largest per-group unit count across the experiment's Pulse metrics, labelled as such; without either, `SRM_ON_METRIC_UNITS` is stated. Tests: `test_srm_uses_assignment_counts_not_a_filtered_metrics_units`, `test_without_assignment_counts_the_fallback_is_labelled` |
| CP-033 | APPROVED_BY_USER | unchanged |
| CP-034 | APPROVED_BY_USER **with fix, deviating from the recommended option** | Recommended: widen intervals by a 14-day backtest error. Measured first: on curved worlds that left effect coverage at 30% (straight line alone 29%), because observational backtests cannot see a wrong shape. Implemented instead: `MechanismSet` = straight + curved (squared-term) shapes, each validated, weighted by held-out log score; each sample draws one shape, the same in both worlds. The 14-day backtest is kept as a registration gate and widens intervals only if needed (it catches lag/dynamics errors). Result (40 held-out worlds per family): curved worlds 90% → 98% (was 80%), 50% → 70% (was 32%); straight-line worlds 90% → 95%; the price is caution (a stated 50% covers ~77%). Schemas re-frozen: MechanismSpec, CounterfactualPrediction, new MechanismSet |
| CP-035 | APPROVED_BY_USER **with fix** | "No change" = exogenous inputs at their 7-day mean (was: last value); regime = fitted range ± 5% (was strict): spend 150 is now predicted when the fitted maximum is 147.3; 300 still abstains |
| CP-036 | APPROVED_BY_USER **with fix** | BEFORE: risk constraints accepted and silently ignored. AFTER: "<variable> <=/>=/</> <number>" is checked on simulated paths (P(violated at some step)); a candidate above 5% is excluded from the ranking; an unreadable constraint is ILL_DEFINED_TARGET; a constraint on an unsimulated variable makes the engine abstain |

## CP-037 — TabPFN internal evaluation — USER_DECIDED 2026-11-22 (internal use only); BLOCKED on licence acceptance

- Installed in an isolated environment outside the repo (`~/.venvs/causal-core-tabpfn`, 746 MB): tabpfn 10.2.0 (code
  Apache-2.0), torch 2.15.0, both from public PyPI. Never a core dependency; `causal_core/` imports neither.
- First use requires a one-time licence acceptance with Prior Labs (account, accept, API key → `TABPFN_TOKEN`).
  That is the user's step; nothing was accepted or registered on their behalf. Weights download after that.
- `scripts/tabpfn_eval.py` (run with the venv's python) compares TabPFN with the NumPy methods on (A) covariate
  baseline prediction (allowed use, §6.4) and (B) equation shape (exploratory, outside §6.4's allowed uses). NumPy
  columns today: (A) calendar-only residual variance 10.0, calendar + driver 4.8, noise floor 2.25; (B) a single
  straight line covers the true effect 13% of the time in curved worlds. TabPFN columns: not run (licence).

Validation: 185 tests; demos 0–7; schemas re-frozen and checked; baseline hashes; fixture manifest; firewall clean.

### CP-037 update — TabPFN evaluated (2026-11-22, internal use)

- The user set `TABPFN_TOKEN`. The package defaults to TabPFN 3.5, whose licence was not yet accepted on the account;
  v2.5, v2.6 and v3 likewise. TabPFN v2 runs without a further acceptance: weights 42 MB in ~/Library/Caches/tabpfn.
  `scripts/tabpfn_eval.py` now takes `TABPFN_MODEL_VERSION` (default v2).
- Results (docs/tabpfn/eval.json; emulator worlds; CPU; 12.8 s for 25 fits):
  - A. covariate baseline prediction (allowed use, §6.4): unexplained variance on held-out days — calendar only 10.0,
    calendar + linear driver 4.8, **TabPFN 2.31** (noise floor 2.25). Residual SD 1.52 vs 2.20: roughly 1.45× the
    signal-to-noise for a learned monitoring baseline.
  - B. equation shape (exploratory, outside §6.4): effect of spend 100 → 130 in curved worlds — straight line error 12.0
    (interval covers truth 13%), **core shape set 5.8 (covers 80%)**, TabPFN 7.6 (no effect interval).
- Reading: TabPFN does not earn a place in the what-if engine (the shape set is more accurate and has intervals).
  It is a strong candidate for the learned monitoring baseline's extra covariates (CURE-style, CP-030 follow-up). Its
  use there would NOT inherit CP-030's exact false-alarm guarantee (that covers the linear fit's error only): false
  alarms would have to be measured on change-free periods, and the blueprint's §6.4 rules apply (lazy optional import,
  caps, logged model/weights version and seed, replay of recorded outputs, NumPy fallback). Not integrated: needs the
  user's decision.

## CP-038 — Stitch marketplace adapter — USER_DECIDED 2026-11-22

**Trigger:** the user asked to look at `stitch_samples.zip` (marketplace-recon output: 106 variables, layers L0–L9,
real public datasets stitched to an emulator chain) and decided: "inject missing schema version if the data types
otherwise comply, no manifest for now. build the stitch adapter."

- marketplace-recon builds against a Phase 1 copy of the contracts (`Contracts/`), so its evidence records lack
  `schema_version` (required since CP-002) and two external adapters emit non-UTC timestamps (rejected since CP-003).
- `adapters/sources/stitch_port.py` reads the zip in memory and: injects `schema_version` "2.0" only into evidence
  that then passes every other check (340/340 on the real samples); converts non-UTC timestamps to UTC as CP-003
  assigns to adapters, keeping the offset in `clocks.source_timezone` (3 observations: +02:00 ×2, +01:00 ×1);
  quarantines everything else with the failing field (0 on the real samples; tested on an invented fixture: a
  contradictory evidence role, a negative count and an invalid definition stay quarantined).
- Definitions are offered through `definitions()` outside the port protocol (no manifest change, per the user).
- Through the core: 10 of 12 ratio identities verify exactly; 2 are UNVERIFIED because the ratio and its parts live in
  different environments. 7 of 8 telemetry series monitored without alarm; 1 refused (lag-1 autocorrelation 0.27).
  Hillstrom sample: no design → waiting; with the published design, one contrast refused (no variance), one estimated.
- Not adopted for now: marketplace-recon's two contract suggestions (manifest variable catalogue; typed
  measurement requests). Real data is not committed; tests use an invented fixture.
- Demo: `python3 -m scripts.stitch_demo`. Tests: `tests/test_stitch_adapter.py` (4).
