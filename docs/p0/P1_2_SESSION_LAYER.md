# P1-2 Session layer: results

Design and checks were preregistered first (`P1_2_SESSION_LAYER_PREREG.md`, commit df48c68).

**Result:** the layer is built and implemented faithfully (8/8 fidelity checks, all invariance tests pass). Two of the five borrowed sources are labelled *unsupported transfer*. **Gate S10 fails as preregistered:** 9a passes, 9b does not. The cause is the stand-in reader, not the layer; see §4 and the post-hoc check.

## 1. Transport cards (fitted on source data only)

| Card | Source | Held-out check | Status |
|---|---|---|---|
| TC-RR1 views, cart share, non-buying sessions per buyer | Retailrocket 2015 | H1 views per session 1.52 vs 1.43 (6%) **pass**; H2 cart share 0.0185 vs 0.0181 **pass** | partially supported |
| TC-TB1 first view → purchase lag, cart lag | Taobao Nov–Dec 2017 | H3 median lag 34 vs 60 min (79%) **fail**: likely left truncation of the 9-day window | **unsupported transfer**; sensitivity = both halves |
| TC-EX1 click by results position | Expedia random-order searches 2012–13 | H4 CIs overlap at positions 2–10 **pass** | partially supported |
| TC-DV1 device mix per month | Statcounter Brazil | joined by month, not fitted | partially supported (page views, not shoppers) |
| TC-DV2 mobile : desktop conversion | Google Analytics store 2016–17 | H5 ratio 0.33 vs 0.21 (38%) **fail** | **unsupported transfer**; sensitivity 0.21–0.33 |

Key numbers: 6.25 views per buying session and 1.47 per non-buying one; 1.8% of non-buying sessions add to cart; **122 non-buying sessions per buying one**, which is site-specific and the least transferable number; median lag from first view to purchase 45 min, with 55% over 30 min; relative clicks 1.00, 0.76, 0.64, 0.55 … 0.33 at position 10.

## 2. Implementation fidelity (preregistered tolerances): 8/8 pass
Views per session: buying 6.50 vs 6.25 (4.0%), non-buying 1.475 vs 1.474. Cart share 0.0184 vs 0.0183. Lag median and 90th percentile within 6% and 4%. Click curve: 9/9 positions inside the card's CI. Mobile share per month within 0.01 of Statcounter. Exactly one buying session per order, always starting before it.

## 3. Invariance and time (gates S7, S3): all pass
- The chain is bit-identical with the layer on or off.
- Chunked runs equal one run.
- A checkout failure changes only the outcome of sessions inside its window: 40% of them, each paired with its twin.
- Session events never become visible before they happen.
- Card ids and statuses are in the environment scope.
- A card trained at or after the simulation start is refused.

Implementation fixes found while building, before scoring:
1. The view-count distribution was cut at 50, which lowered the mean (F1 would have failed).
2. A slow string match took 80% of the build time.

## 4. Gate S10 (incremental value): **FAIL as preregistered**

| | 9a checkout failure | 9b demand drop | Control |
|---|---|---|---|
| **With the session layer** | **4/5** localised to *checkouts → orders* (1 abstention) | **2/5** localised upstream (2 abstentions, 1 wrong: *checkouts → orders*) | **5/5** no change |
| **Without it** | 0/5 (cannot localise) | 0/5 (cannot localise) | 5/5 |
| Preregistered pass rule | ≥ 4/5 | ≥ 4/5 | ≤ 1/5 false alarms |

**Why 9b failed (diagnosed after scoring):** every order comes from one checkout, so the weekly *checkouts → orders* ratio sits at almost exactly 1. Its median-absolute-deviation scale is then almost zero, and the stand-in reader treats tiny week-boundary noise as a "broken" stage. That produced the abstentions and the one wrong answer. The layer itself carried the right information: in every 9b run, all funnel ratios stayed constant.

**What I did not do:** retune the reader and re-score the same seeds. The formal result stays **fail**. Under the preregistered rule, the session layer counts as having **no measurable gain** until a reader shows one.

**Post-hoc check (labelled; not the gate):** the reader with a counting-uncertainty floor on each ratio, run on new seeds 71–75. Result: see §5.

## 5. Post-hoc result (count-uncertainty floor; new seeds 71–75; not the gate)

| | 9a checkout failure | 9b demand drop | Control |
|---|---|---|---|
| Post-hoc reader, with the layer | **4/5** (1 abstention) | **4/5** (1 abstention) | 3/5 (2 abstentions, both on the New Year weeks) |

- **No wrong localisations at all,** compared with one before. The diagnosis holds: the collapsed scale caused the 9b failure.
- **A new weakness:** in 2 of 5 controls the order count dips in the first weeks of January, and the reader flags it. It then correctly abstains instead of blaming a funnel stage. The stand-in's flat baseline doesn't model that seasonal dip. Under the preregistered rule these count as false alarms, so the control check would also fail.

**Decision for you** (recorded in `STRATEGY_CHANGES.md` #9–#10):
1. **Accept the post-hoc result as indicative and move on.** The session layer stays in the build, labelled "S10 not yet passed", and scenarios that rely on it stay supplementary.
2. **Preregister S10 v2:** count floor + a seasonal baseline, fixed in a committed file before running, then score on fresh seeds. Recommended. It's cheap (about 30 min of runs) and is the honest way to un-park the layer.
3. **Wait for the real causal core v3** and test S10 with it instead of the stand-in.

## 6. S10 v2: **PASS** (preregistered in `P1_2_S10_V2_PREREG.md`; fresh seeds 81–85; contract port + `standin_core` with a local baseline)

| | 9a checkout failure | 9b demand drop | Control |
|---|---|---|---|
| **With the session layer** | **5/5**, located at `completion_rate` (checkouts → orders) | **4/5**, located upstream of `sessions_count` (1 abstention: mixed stage evidence) | **5/5**, no false alarms |
| Without it | 0/5 (the change is found, but can't be located) | 0/5 | 5/5 |
| Pass rule (unchanged) | ≥ 4/5 | ≥ 4/5 | ≤ 1/5 false alarms |

**Verdict:** the session layer turns "can't tell" into "correct" on this look-alike pair, so it is **un-parked**. The local baseline also removed the New Year false alarms seen in the post-hoc run. No wrong localisations in any run.
