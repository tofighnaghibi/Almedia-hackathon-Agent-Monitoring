# Phase 1 Audit — Changes

All changes are on `autonomous/v32-run-20261121`. Nothing is pushed or merged.

| Commit | Tag | Change | Class |
|---|---|---|---|
| `7502f12` | `checkpoint/CP-002-native-output-versioning` | Required `schema_version` on 11 output models; manifest latency/cost fields | B, provisional |
| `72add25` | `checkpoint/CP-003-utc-on-the-wire` | `UtcDatetime` for every contract timestamp; corpus D4 | B, provisional |
| `265e730` | `checkpoint/CP-004-estimand-scopes` | `EstimandSpec.source_scope` + `target_scope` required; corpus L5 | B, provisional |
| `39515bf` | `checkpoint/CP-005-entity-dimension-specs` | `EntitySpec`, `DimensionSpec`, two parsers | B, provisional |
| `ca8bf70` | — | Aggregate bounds; NaN/Inf refusal; frozen schemas; 27 audit tests; hand fixtures; compat fixtures | A (Tier 1) |
| (this commit) | `checkpoint/A-phase1-audit` | Audit documents, `SCHEMA_V2_MIGRATION.md` §9–§10, `scripts/audit_coverage.py` | docs |

## Before / after behaviour

| Input | Before (`38a5081`) | After |
|---|---|---|
| Proportion metric, aggregate 600/400 | ACCEPTED | `PAYLOAD_VIOLATES_DEFINITION: aggregate implies value 1.5 above declared maximum 1.0` |
| Scalar value `NaN` / `Infinity` | ACCEPTED (`nan`, `inf`) | `SCHEMA_INVALID` |
| `observed_at = 2026-11-19T02:00:00+02:00` | ACCEPTED, kept as +02:00 | `SCHEMA_INVALID: timestamps must be UTC on the wire …` |
| `EvidenceGap` without `schema_version` | ACCEPTED | `SCHEMA_INVALID` (missing); `"1.2"` refused |
| `EstimandSpec` without scopes | ACCEPTED | `SCHEMA_INVALID` (both required) |
| `DimensionSpec` top-K without OTHER | type did not exist | `SCHEMA_INVALID` |
| Aggregate 0/0 for a proportion | ACCEPTED | ACCEPTED (undefined, not a violation; unchanged) |

## Blast radius

| Surface | Impact |
|---|---|
| Public symbols added | `parse_entity_spec`, `parse_dimension_spec`, `EntitySpec`, `DimensionSpec`, `UtcDatetime`, `EstimandSpec.requires_transport` |
| Serialized schema changes | 11 output models gain a required field. `EstimandSpec` gains 1 required field and makes 1 required. All timestamps tighten to UTC. `ProviderManifest` gains 2 optional fields |
| Old fixtures | Phase 0 fixtures untouched (manifest hash unchanged). Phase 1 tests: 2 payload constructors updated (CP-002), 2 estimand constructors updated (CP-004) |
| Field meaning | None changed. Absence of latency/cost explicitly means UNKNOWN |
| Source adapters | The emulator is already UTC. The legacy adapter is unaffected. Future adapters must convert to UTC and stamp `schema_version` on outputs |
| Downstream consumers | Phase 2 admission uses `DimensionSpec.complete_partition` / `EntitySpec.parent_is_exact`. Phase 4 auditor uses `requires_transport`. Phase 5 planner must treat unknown cost as a penalty |

## Unmodified proposals

None. Every Class B item was implemented provisionally under the run protocol. Each can be reverted
on its own via its tag.
