# Suggested additions to `Contracts/` (schema 2.0), for the contracts' owner

**Status:** optional. Everything built so far conforms to the contracts as they are; these two additions would replace workarounds with typed contract fields. Context: `docs/p0/INTEGRATION_M1_M5.md`, `docs/p0/VARIABLE_CATALOG.md`.

## Decisions already taken (22 Nov 2026)
| Gap | Decision | Applied in |
|---|---|---|
| G1 the manifest has no variable catalogue | the variable taxonomy must conform to the contracts | `mkt/variable_catalog.py` (106 variables, all four kinds), `mkt/taxonomy.py` |
| G2 scope for replayed real history | **not** `production` → `offline_replay` (evidence role therefore `SANDBOX_OBSERVED`) | `mkt/core_port.py::olist_port` |
| G3 origin of transported parameters | keep them in namespaced `extensions` (`mkt:transport_card`, `mkt:transport_status`) | `VariableDefinition.extensions` |

## Suggestion 1: let a provider publish its variable catalogue through the contract

**Problem:** `EvidenceAccessPort` has only `manifest()` and `query()`. `ProviderManifest` lists request types and scopes, but not which variables exist. A core that uses only the protocol can't discover what to ask for. Today we expose a read-only `definitions()` method outside the protocol.

**Option A (smallest change):** add the catalogue to the manifest.
```python
class ProviderManifest(Contract):
    ...
    variables: tuple[VariableDefinition, ...] = ()      # new: what this provider serves, in this scope
```

**Option B:** answer `get_source_metadata` with the catalogue as a new payload shape.
```python
class VariableCatalog(Contract):
    shape: Literal["variable_catalog"]
    definitions: tuple[VariableDefinition, ...] = Field(min_length=1)
```
(This adds `variable_catalog` to `ValuePayload` and to an `ALLOWED_SHAPES` entry for a metadata kind.)

**Recommendation:** A. The catalogue is part of what a provider *is*, not an observation, and A keeps `query()` for data only.

## Suggestion 2: typed measurement requests in `EvidenceGap`

**Problem:** `EvidenceGap.recommended_measurements` is `tuple[Text, ...]`. A policy gate that decides whether to grant a measurement has to parse free text. We use the convention `"independent_measurement:<variable_id>"`; a convention is not a contract.

**Proposal:**
```python
class MeasurementRequest(Contract):
    kind: Literal["independent_measurement", "finer_grain", "longer_window", "new_variable", "experiment"]
    target_variable: Id                       # what the measurement should corroborate or explain
    reason: Text
    max_cost_units: float | None = Field(default=None, ge=0)

class EvidenceGap(Contract):
    ...
    recommended_measurements: tuple[Text, ...] = Field(min_length=1)   # unchanged (human-readable)
    measurement_requests: tuple[MeasurementRequest, ...] = ()          # new, machine-readable
```
This is additive: existing producers and consumers keep working.

## Smaller notes (no change requested; for awareness)
- **Calendar grain:** `Grain` allows `m|h|d|w`, so a calendar month can't be expressed (`30d` is not a month). We serve weekly and daily only.
- **Cross-entity ratios:** `completion_rate = orders_count / checkouts_count` divides an *order* count by a *session* count. That's valid only because of a 1:1 entity relationship. The graph contract can express it (`ENTITY_RELATIONSHIP`), but `MetricDefinition` cannot; we document it in the description.
- **Observations have no `extensions`:** transport-card references therefore live on the variable definitions, not on each observation. That's fine for us; mentioned in case per-observation tags are wanted later.
