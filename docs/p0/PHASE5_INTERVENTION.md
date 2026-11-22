# Phase 5: one intervention + counterfactual twin

**Gate S7 (intervention correctness): PASS · Gate S6 (oracle firewall): PASS** (`tests/test_phase5_intervention.py`, 11 tests; full suite green)

## Built
- `mkt/sim/twin.py`:
  - an **action gateway** (allowlist, safety bounds, allowed selector keys, horizon check, explicit rejection codes);
  - a factual run and a **twin with identical randomness**;
  - an **evaluator-only `Truth`** (per-order pairing and true effects) that is never given to the port.
- Intervention: `carrier.transit_multiplier` × 2.5 for shipments to RJ handed over 5–12 Feb 2018.

## Gate tests
| Test | Result |
|---|---|
| Unaffected orders are bit-identical between factual and twin | pass |
| Affected orders: transit is exactly 2.5× the twin's; only RJ; only in the window | pass |
| Chunked runs (Jan + Feb) equal one run (Jan–Feb), every column | pass (after fixing 2 real flaws: run-relative order keys; floating-point purchase times) |
| Changing an unrelated mechanism (survey-answer lag) leaves fulfilment unchanged; score draws unchanged (only *whether* a score is observed can change via the data cut) | pass |
| No intervention → factual equals twin | pass |
| Gateway rejects: off-allowlist target, out-of-bounds value, unknown region, disallowed selector key, window outside the horizon | pass (5 cases) |
| No oracle field or object reachable from the port or its payloads; evidence labelled synthetic | pass |

## First independent score (not a gate; supplementary)
The stand-in reader sees only the port: weekly purchase-cohort late share for RJ, as of 15 May 2018.

| Seed | True affected weeks | Reader flagged | Precision | Recall | Mean abs effect error | False alarms in no-intervention control |
|---|---|---|---|---|---|---|
| 21 | 29 Jan, 5 Feb | same | 1.0 | 1.0 | 0.004 | 0 |
| 22 | same | same | 1.0 | 1.0 | 0.021 | 0 |
| 23 | same | same | 1.0 | 1.0 | 0.038 | 0 |
| 24 | same | same | 1.0 | 1.0 | 0.016 | 0 |
| 25 | same | same | 1.0 | 1.0 | 0.027 | 0 |

**Reading:** this is an easy sanity scenario (one strong, localised cause; the reader is told the metric and the region). It shows the loop works end to end: world → observation → port → reader → oracle score. It is **not** evidence of reasoning ability (gate S9), which needs harder scenarios (look-alike causes, confounding) and the real v3 core.
