# Phase 2: real Olist data through the v3 contracts (no simulator)

**Gates: S8 PASS · S6 (origin classes) PASS · S2 (lineage) PASS** (`tests/test_phase2_v3.py`, 8 tests)

## Built
- `mkt/v3.py`: Pydantic v2 contracts named as in the causal core v3 design: `VariableDefinition`/`MetricDefinition`, `Observation` (typed payload shapes), `EvidenceRecord` + `ProvenanceLineage`, `EnvironmentScope`, `StudyDesign`/`ExperimentRecord`, `InterventionSpec`/`InterventionEvent`, `CausalQuestion`, `CapabilitySpec`, `EvidenceRequest`, `Refusal`, and the `EvidenceAccessPort` protocol. Frozen; extra fields rejected; transported evidence must cite a transport card. Field details the v3 diagram does not specify are marked **provisional**.
- `mkt/replay_port.py`: a read-only, as-of `EvidenceAccessPort` over real Olist data. 7 event variables and 7 metrics, each metric with an explicit formula, population and quality flags. It never returns anything later than `as_of`, flags incomplete windows instead of reporting zeros, and refuses with a code when it can't answer: `NO_STUDIES_IN_SOURCE`, `UNKNOWN_VARIABLE`, `UNSUPPORTED_SHAPE`, `BUDGET_EXCEEDED`, `NO_INTERVENTIONS_IN_ENVIRONMENT`.
- `mkt/standin_reader.py`: a small question-first reader (FRAME → AUDIT → ACQUIRE → EVALUATE → CHALLENGE → REPORT) that contains no domain words (tested). **It is not the v3 core**; it only proves the port and contracts can serve one.

## Result on real data (2018 truckers' strike)
| Finding | Evidence |
|---|---|
| Weekly orders fell 1,720 → 954 (week of 21 May) | z = −6.3 vs robust baseline |
| Purchase-cohort late share fell 3.8% → 0.3% (week of 28 May) | z = −13.2 |
| Promised delivery days rose 24.1 → 37.4 | z = +6.9 |
| **Challenge:** the late-share drop is a changed yardstick (longer promise), not faster delivery | actual delivery days in those weeks: z = −1.8 |
| **Gap:** the cause is not identified; the source is observational only | the reader never claims more than "observational association" |

## Corrections made during this phase
1. **My stand-in first compared a delivery-week rate with a purchase-week yardstick**, a time-base mismatch. Added `late_share_cohort` (purchase cohort) so rate and yardstick share a time base.
2. **The baseline was contaminated** by the spike in the week before the strike (orders caught in it). It now uses median + MAD (a standard robust choice, decided on method grounds and documented).
3. **Scenario S13's ground-truth note was wrong.** It said actual delivery days were flat; in fact they were flat during the strike weeks but **got genuinely faster from mid-June (~8.3 days)**. The note now says the late-May/early-June drop is the longer promise, and later weeks have both causes.

## Open for the v3 owner
- Field-level details of `Observation`, `EvidenceRecord`, `CapabilitySpec` and `EvidenceRequest` are provisional.
- The port's `available_at = event_time` is an assumption for real replay (Olist has no arrival times); phase 4 replaces it with an observation model.
