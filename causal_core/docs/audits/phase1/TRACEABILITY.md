# Phase 1 Audit — Traceability Summary

Row-level matrix: `TRACEABILITY.csv`. It has 91 rows, one falsifiable requirement each, with
`status_before` and `status` (after) kept separate. Recompute every number below with:
`python3 -m scripts.audit_coverage docs/audits/phase1/TRACEABILITY.csv`.

## Coverage, with denominators

| Measure | Before (`38a5081`) | After |
|---|---|---|
| (a) due-now requirements implemented (PASS or PARTIAL) | 61 / 70 | **70 / 70** |
| (a′) due-now requirements PASS | 42 / 70 | **67 / 70** |
| (b) due-now PASS with an executable test **and** a cited negative case | 29 / 70 | **44 / 70** |
| (c) requirements with a named owner (incl. 21 future obligations) | 91 / 91 | 91 / 91 |

- "Due now" = phase 0 or 1, including split rows like `1/4`.
- 21 rows are `DEFERRED_NOT_DUE`. They are never counted as passed.
- The 26 due-now PASS rows without a cited negative are mostly existence or deliverable rows
  (e.g. P1-009, P1-058). There a negative case is not meaningful, and the matrix says so with `—`.

**Still PARTIAL after the audit:**

| Row | Why |
|---|---|
| P1-049 | Source IID assertions can't be verified at contract level → Phase 4 |
| G06 | Contract part passes; DQ behaviour is method-level → Phase 4 |
| G07 | Contract part passes; 1+50 clone counting needs the ledger → Phase 3 |

## BLOCKER-risk requirements (all PASS after)

These pass with tests:
- P1-001: kinds union
- P1-002 / P1-003 / P1-056: stop condition, proven on a hand fixture
- P1-012: shape-vs-kind refusal
- P1-019: replay vs Phase 0 oracle
- P1-021: no default scope
- P1-026 / P1-052: no covariance number
- P1-028: prediction relabelling refused
- P1-033: explicit tasks need no incident
- P1-034: EffectClaim is not an edge
- P1-035: no RCT-only shortcut
- P1-047: missing scope
- P1-054: no guessed availability
- G01, G02, G17-surface

## Reverse coverage (code and tests → requirements)

| Symbol / file | Requirement | Verdict |
|---|---|---|
| `api.parse_graph_edge` | P1-035, future-001 | Justified extension: the migration validates migrated edges through it; Phase 2 consumes it |
| `api.parse_entity_spec`, `parse_dimension_spec` | P1-060 | Justified (CP-005) |
| `api.replay_visibility` | P1-019 | Justified |
| `api.CompatibilityReport`, `assess_contract_compatibility` | P1-004, P1-008 | Justified |
| `contracts.graph.LagBounds`, `Validity`, `GraphNode` | future-001 prerequisites | Justified early freeze of shapes; no behaviour |
| `contracts.tasks.Pins`, `Finding` | I12, v3.2 §2.1 Finding | Justified (shape only) |
| `contracts.ports.HypothesisProposerPort` | v3.2 §1.1 | Justified (optional producer) |
| `migration.legacy.parse_v1_formula` | P1-040 | Justified (v1 formula strings) |
| `contracts.common.SCHEMA_VERSION` constant | — | **Unused constant.** Harmless; kept for documentation |
| `DataQuality.missingness` / `selection` | G06 (Phase 4 consumer) | Fields without a consumer yet; owned by `dq_observation_v1` |
| `emulator/coldchain.py`, `evaluation/adversarial_contracts.py` | P1-062, plan §L | Test infrastructure; not evidence of independence |
| `scripts/export_schemas.py`, `scripts/audit_coverage.py` | P1-057, audit Step 2 | Justified |
| `tests/…::RoundTripG02.test_dict_and_bytes_parse_identically` | DD-01 | Justified (single wire path) |
| `tests/…::RoundTripG02.test_randomized_payload_round_trips` | P1-010 | Property-style test (200 seeded cases) |

No dead test files or empty tests were found. No README claim is used as proof.

## Deferred obligations (owner → gate)

| Owner phase | Obligations |
|---|---|
| Phase 2 | future-001 (13 edges/views, G04), future-002 (certificate-to-estimand, G11), future-004 (bitemporal admission, G05), I06, I08 |
| Phase 3 | P1-023 (lineage DAG), P1-027 (dependence blocks), future-005 (scheduler/outbox, G15), future-010 (WorkItem fields), future-011 (revision conflicts), I12, I14, G07 counting |
| Phase 4 | P1-049 (IID refusal), future-003 (registry, G10), future-008 (design detail), future-009 (sampling weights), I09, I17, G06 behaviour |
| Phase 5 | future-007 (ActionProposal, Class E) |
| Phase 6 | I18 (profile removal end to end) |
| Phases 7–8 (P1, outside this run) | future-006, future-012 |
