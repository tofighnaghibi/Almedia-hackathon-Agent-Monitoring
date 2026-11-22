# Phase 3: minimal simulated chain (demand → orders → fulfilment → survey/review)

**Gate S5 (realism), FINAL after the phase-5 key fix: 9/10 on the preregistered seed. F2 (weekday share) misses by 0.0005 on every seed; accepted as a documented exception (decision #6 in `STRATEGY_CHANGES.md`), **confirmed by you on 22 Nov 2026**.** Before the key fix it was 10/10 on seed 1, but that was partly luck from run-relative random keys.
**Gate S3 (no look-ahead in fitting): PASS.** Everything is fitted on 2017 (`trained_through` 2017-12-31), and the simulator refuses to start earlier (tested).

Tolerances were preregistered and committed **before** building or measuring (`PHASE3_TOLERANCES.md`, commit `2b759c4`).

## Results on held-out Jan–Jun 2018 (seed 1, attempt 2)

| Check | Real | Simulated | |
|---|---|---|---|
| F1 daily orders | 226.8 | 237.8 | ✔ |
| F2 weekday shares | max diff 0.020 | | ✔ (at the edge) |
| F3 hour profile | TV 0.025 | | ✔ |
| F4 delivery days q25/q50/q75 | 6.8 / 10.8 / 17.1 | 7.2 / 11.5 / 17.4 | ✔ |
| F5 late share | 8.6% | 8.9% | ✔ |
| F6 score by lateness | 4.27 / 3.19 / 1.86 / 1.63 | 4.28 / 3.25 / 2.03 / 1.82 | ✔ (late buckets +0.17–0.19) |
| F7 answer lag; surveys sent before delivery | 1.71 d; 6.7% | 1.65 d; 7.3% | ✔ |
| F8 Spearman lateness ↔ score | −0.226 | −0.200 | ✔ |
| F9 top-10% seller share | 62.6% | 63.1% | ✔ |
| F10 cross-state share; late gap | 62.8%; +7.2 pts | 66.2%; +6.2 pts | ✔ |

## Fix log (all fitted on 2017 data only)

| Attempt | Failing | Change | Result |
|---|---|---|---|
| 0 | F8, F10 | (first build as preregistered) | 8/10 |
| 1 | F8, F10 | Seller choice depends on the buyer's state (locality); transit and promise by destination state; score by 7 lateness bins (early arrival matters) | 9/10: F8 fixed, F10 gap still +0.6 vs +7.2 |
| 2 | F10 | Diagnosed: the held-out gap comes from a **Feb–Mar 2018 delivery disruption** absent from 2017. The one 2017-supported fix: 2017 shows cross-state transit slowing and same-state speeding up over the year, so fit that drift and carry it forward | **10/10** |
| 3 | — | **Not used.** The remaining biases (F2 Friday share, F6 late-bucket scores) are known only from the held-out data; fixing them would be tuning on 2018 | — |

## Seed robustness (supplementary; not part of the preregistered gate)

| Seed | Passes | Failing |
|---|---|---|
| 1 (preregistered) | 10/10 | — |
| 2 | 9/10 | F2 |
| 3 | 9/10 | F6 |
| 4 | 8/10 | F2, F6 |
| 5 | 9/10 | F2 |

**Reading:** two systematic 2017→2018 differences sit at the tolerance edge: Friday share is about +2 points, and scores of late orders are about +0.18 stars (2018 customers rated late orders more harshly). The chain is realistic on totals, timing, geography, concentration and the cross-layer lateness → score dependency, **within these two known biases.**
**Lesson for later gates:** preregister a multi-seed pass rule (e.g. ≥ 4 of 5 seeds).

## Mechanisms (all class "fitted on Olist", no transferred data)
Demand trend (Theil–Sen over 2017 weekly medians) × weekday × hour · buyer state · seller depending on buyer state · seller handling time (partial pooling) · approval delay · transit by destination with a fitted 2017 drift · promise by destination · delivery probability · survey sent at min(delivery, promise) · answer lag · score depending on 7 lateness bins.
Randomness is keyed by (seed, mechanism, order index); tests show earlier orders are unchanged when the horizon grows.

## Re-measurement after global randomness keys (phase 5, decision #5/#6)

| Seed | Passes | Failing |
|---|---|---|
| 1 (preregistered) | 9/10 | F2 (max diff 0.0205 vs 0.02) |
| 2 | 9/10 | F2 |
| 3 | 9/10 | F2 |
| 4 | 9/10 | F2 |
| 5 | 8/10 | F2, F6 |

F2 is now a **consistent** miss: Fridays are 15.4% of simulated orders vs 13.4% real. 2017's weekday pattern differs slightly from 2018's. All other checks pass on every seed except one F6 miss (seed 5). Results: `phase3_fidelity_attempt2-global-key.json`.
