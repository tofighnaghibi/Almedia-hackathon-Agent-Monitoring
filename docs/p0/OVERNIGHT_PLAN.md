# Overnight plan: phase P0, phase by phase, and where it stops

*Written 21 Nov 2026, before starting. Approved structure: ARCHITECTURE.md §13.0 (phases), §12.5 (gates S1–S10). D7 condition: a phase must pass its gates before the next one starts.*

Work happens on git branches; each phase that passes gets one commit. Nothing is merged to `main`.
In the morning: `docs/p0/MORNING_REPORT.md`, plus the session handoff in your Open Items note.

---

## Phase 1: Olist field meanings + the factual backbone  *(in progress)*

|                     |                                                                                                                                                                                                                                                                                                                                                     |
| ------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Builds**          | `mkt/semantics.py`: a field dictionary for every Olist field (meaning, unit, granularity, who produces it, source of the meaning) plus checks. Fixes to the Olist adapter's 5 semantic errors (review time, invented settlement, cancellation time, multi-seller orders, carrier handover). One shared event vocabulary for real and simulated data |
| **Gates**           | **S1** meaning & units · **S2** keys, joins, lineage                                                                                                                                                                                                                                                                                                |
| **Status**          | Audit done: **S1 PASS, S2 PASS** (10 data quirks documented as exceptions)                                                                                                                                                                                                                                                                          |
| **I decide myself** | How each documented quirk is handled (keep and flag, never silently drop). Renaming events consistently. Updating tests for the new names                                                                                                                                                                                                           |
| **STOPS if**        | A check FAILs and the cause is in the data rather than our code, or a field's meaning can't be settled from Olist's docs plus a measurement                                                                                                                                                                                                         |
| **Ends with**       | Commit "phase 1"; report `docs/p0/PHASE1_SOURCE_SEMANTICS.md`                                                                                                                                                                                                                                                                                       |

## Phase 2: real data through the causal core v3 contracts (no simulator)

| | |
|---|---|
| **Builds** | `mkt/v3.py`: Pydantic v2 models named as in the v3 design (`VariableDefinition`, `MetricDefinition`, `Observation`, `StudyDesign`/`ExperimentRecord`, `InterventionSpec`/`InterventionEvent`, `EvidenceRecord` + `ProvenanceLineage`, `EnvironmentScope`, `CapabilitySpec`). An Olist → v3 adapter. A read-only, as-of `EvidenceAccessPort`. One investigation on **real** data only (e.g. the 2018 strike) run through a **stand-in reader**, because the real v3 core isn't in this repo |
| **Gates** | **S8** core compatibility (no mandatory experiment fields; unsupported requests fail with a code) · **S6** (part) every record carries its origin class · **S2** lineage survives |
| **I decide myself** | Field details the v3 diagram doesn't specify (marked "provisional, align with the v3 owner") |
| **STOPS if** | The v3 diagram contradicts itself on a contract so that I'd have to guess its meaning, not just its field names |
| **Ends with** | Commit "phase 2"; contract tests |
| **Caveat** | Proves the contracts and the port, **not** the real core. Plugging in the real core is a later step with whoever owns it |

## Phase 3: one minimal simulated chain (demand → orders → delivery → reviews)

|                      |                                                                                                                                                                                                          |
| -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Builds**           | A trimmed simulator mode with only Olist-fitted mechanisms (no transferred datasets in P0, so no transport cards needed yet). Every fitted mechanism records `trained_through` = 2017-12-31              |
| **Before measuring** | Tolerances written to `docs/p0/PHASE3_TOLERANCES.md` and **committed before any comparison runs** (preregistration)                                                                                      |
| **Gates**            | **S5** realism on held-out Jan–Aug 2018: totals **and** dependencies (score given lateness, delivery → review lag, weekly pattern) · **S3** no mechanism trained on data later than the simulation start |
| **I decide myself**  | Up to **3 fix attempts** if S5 fails, each documented, using 2017 (fit) data only, never tuned on the 2018 held-out data                                                                                 |
| **STOPS if**         | S5 still fails after 3 attempts. **P0 halts here** (D7 condition); I write up what fails and why                                                                                                         |
| **Ends with**        | Commit "phase 3"; fidelity report                                                                                                                                                                        |
| **Known risk**       | Earlier measurement: simulated delivery ~2 days slower than 2018 (Olist got faster in 2018). This may fail S5 honestly; that's a valid outcome, not something to hide                                    |

## Phase 4: observation process + time integrity

| | |
|---|---|
| **Builds** | `available_at` delays, sampling, missing events, late corrections (revisions), as-of queries ("what was knowable at time T"), and enforcement of `trained_through` in replay |
| **Gates** | **S3** no look-ahead: as-of queries are reproducible, and nothing later than the clock is served |
| **I decide myself** | Delay and missingness settings: calibrated where data allows (Retailrocket's purchases without a view, Olist's timestamp quirks), otherwise labelled assumptions with a sensitivity range |
| **STOPS if** | A leak I can't remove |
| **Ends with** | Commit "phase 4"; leakage test suite |

## Phase 5: one intervention + counterfactual twin

|                     |                                                                                                                                                                                                              |
| ------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| **Builds**          | Randomness keyed by (seed, mechanism, entity, event slot, tick); one intervention (carrier slowdown in one region); an evaluator-only oracle                                                                 |
| **Gates**           | **S7** the known simulated effect is recovered, and results are unchanged under reordering, parallelism and changes to unrelated mechanisms · **S6** the oracle never appears in anything the reader can see |
| **I decide myself** | Up to 3 fix attempts, as in phase 3                                                                                                                                                                          |
| **STOPS if**        | Still failing after 3 attempts                                                                                                                                                                               |
| **Ends with**       | Commit "phase 5"; the first independent score of the stand-in reader on the scenario                                                                                                                         |

## After phase 5: P0 is done, and I STOP

No P1 work (more datasets, Astronomy Shop, feedback loops) without you.

---

## Update ~22:30: "don't wait on me; make decisions where necessary"

I make every decision needed to keep going, including ones I'd otherwise have asked about (e.g. A1, A5, and after P0 the next step), and log each in `docs/p0/STRATEGY_CHANGES.md` with its reasoning, for your review. Still excluded, because nothing requires them and they can't be cleanly undone: **deleting data, merging to `main`, pushing or publishing, and paid sources.**

## Never without you (any phase)

- deleting any data;
- merging to `main`, pushing, or publishing anything;
- downloading new or paid data;
- running the Astronomy Shop or Docker (A2);
- starting P1.

## Strategy changes I may make (your permission, 21 Nov ~21:19)

If a gate shows the current strategy can't work, I may change the **backbone, dataset roles or mechanism strategy** instead of stopping, within these limits:

1. **Evidence first:** a change is allowed only when a gate result (a measurement) shows the current strategy fails. Taste is not a reason.
2. **Only data already on disk:** swapping primaries and secondaries, using the archived datasets (Amazon-M2, Tmall, iPinYou, Reviews-2023 categories), or dropping a role. No new downloads.
3. **Logged:** each change gets an entry in the decision log (`docs/p0/STRATEGY_CHANGES.md`) with the failing measurement, the alternatives considered, and why this one.
4. **Re-gated:** every earlier gate the change touches is re-run before moving on.
5. **Bounded:** at most one strategy change per gate, then up to 3 fix attempts. If it still fails, P0 stops there.
6. **Reversible:** each change is a separate commit on the phase branch, so you can undo it in the morning.
7. **A backbone change (replacing Olist) is the last resort** and needs a measured reason why Olist cannot support the P0 chain. It is listed first in the morning report.

## Gates S4, S9, S10

These are not part of P0: S4 needs transferred mechanisms (P1), and S9/S10 need a real reasoning core. They're listed in the morning report as not yet run.
