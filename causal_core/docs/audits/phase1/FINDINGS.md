# Phase 1 Audit — Findings (prioritized)

Classes: **A** clear defect, fixed locally · **B** material deviation needing approval · **C** valid
alternate kept · **D** future-phase obligation · **E** document ambiguity.

Under the audit prompt alone, B items would stop and wait. The run protocol (Tier 2) overrides that:
B items are implemented **provisionally** behind an isolated change, then logged as `CP-NNN`.

| # | Risk | Class | Finding | Evidence (before → after) | Action / status |
|---|---|---|---|---|---|
| F-1 | HIGH | A | An aggregate's implied ratio was never checked against the definition. A proportion metric accepted 600/400 (plan §C.6 "arbitrary outcome denominators") | probe: ACCEPTED → `AggregateBoundsT1` refuses it | **Fixed** (Tier 1, `ca8bf70`) |
| F-12 | HIGH | A | `NaN` / `Infinity` / `-Infinity` accepted as measurements | probe: 3× ACCEPTED → `StrictnessT1.test_non_finite_numbers_refused` | **Fixed** (Tier 1, `ca8bf70`) |
| F-2 | HIGH | B | 9 native output contracts (+2 reports) had no `schema_version` (plan §C "mandatory for native outputs") | probe list → `NativeOutputsCP002` | **Provisional** CP-002 |
| F-3 | HIGH | B | `EntitySpec` / `DimensionSpec` absent (v3.2 §1.2 tree, §2.1). Phase 2 admission step 3 depends on them | probe False/False → `EntityDimensionCP005` | **Provisional** CP-005 |
| F-7 | HIGH | A | Rules existed in code with no executable evidence: 2/8 shape round-trips, 4/8 shape constraints, coercion refusal, extensions, mechanisms, capability responses, attestation, migration determinism, I/O purity | 14 matrix rows PARTIAL → PASS | **Fixed** (tests only) |
| F-10 | HIGH | A | All non-emulator evidence came from emulator-generated data (self-validation). The audit's probe E requires hand fixtures | none → `HandFixturesT1` | **Fixed** (2 hand fixtures) |
| F-4 | MEDIUM | B | Non-UTC offsets accepted (v3.2 §2.2 "UTC values on the wire") | `+02:00` ACCEPTED → refused (`UtcOnTheWireCP003`, corpus D4) | **Provisional** CP-003 |
| F-5 | MEDIUM | B | `EstimandSpec` lacked the required source/target scope (v3.2 §2.1; I05) | optional target only → both required (`EstimandScopesCP004`, corpus L5) | **Provisional** CP-004 |
| F-6 | MEDIUM | A | "Frozen schemas" deliverable missing (plan §C) | none → 20 schemas + `SchemaFreezeT1` | **Fixed** (Tier 1) |
| F-11 | MEDIUM | A | Compatibility fixtures were only fixture 09 + generated records | → 8 hand-written v1 cases (`CompatibilityFixturesT1`) | **Fixed** |
| F-13 | MEDIUM | D | "Reject false sample independence" can only be half-enforced by a contract: IID defaults to UNKNOWN, but a source's `ASSERTED_BY_SOURCE` can't be verified here | P1-049 PARTIAL | **Deferred**: Phase 4 `randomized_effect_v1` must refuse naive SE under clusters (fixture 05) |
| F-8 | LOW | B | `ProviderManifest` couldn't express latency/cost (audit probe D) | fields absent → optional, None = UNKNOWN | **Provisional** CP-002 |
| F-9 | LOW | A | No documented strategy for adding an observation shape (audit probe A) | → `SCHEMA_V2_MIGRATION.md` §9 | **Fixed** (doc) |
| F-14 | LOW | E | `ActionProposal` appears in v3.2 §2.1's core-type table but not in plan §C.4's Phase 1 list | future-007 | **Assigned to Phase 5** (policy gate); see `APPROVAL_REQUEST.md` |
| F-15 | INFO | — | `docs/ROADMAP.md` was moved out of the working tree by the user | byte-identical copy in `~/Downloads/hackathon/` | Preserved; read from git |
| F-16 | INFO | — | Checkpoint A was approved on `38a5081`. Everything above postdates that approval | — | **Re-approval required** (`APPROVAL_REQUEST.md`) |

**No BLOCKER-risk finding remains open.** The highest open items are provisional decisions awaiting
review (F-2, F-3, F-4, F-5, F-8) and two deferred method-level obligations (F-13, plus G06/G07 behaviour).

## Top three consequential findings

1. **F-1 + F-12 (fixed):** the parser accepted impossible and non-finite measurements, so a
   proportion of 1.5 or NaN could have reached Phase 4 estimators as data.
2. **F-3 (provisional):** without `DimensionSpec`, Phase 2 admission and Phase 4 Kitagawa would each
   have invented their own idea of "complete partition". That's the exact top-K/OTHER hazard Phase 0
   fixture 02 freezes.
3. **F-10 (fixed):** before the audit, the only non-metric evidence was emulator-generated, which the
   audit prompt explicitly rejects as insufficient for release-critical claims.
