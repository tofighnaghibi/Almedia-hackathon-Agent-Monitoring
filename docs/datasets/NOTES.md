## Decisions 21 Nov 2026: 16 datasets + 2 reference files

**Kept (downloaded + audited):** Olist, Olist Funnel, Retailrocket, Taobao, ESCI, Open Bandit (random + BTS), Criteo, Hillstrom, Coat, UCI, M5, Instacart, BCB SGS, YelpChi, Amazon fraud graph, NAB/LogHub (16).
**Reference files (2):** `holidays_br_2016_2018.csv` (39 dates, rule-generated, verified) and `truckers_strike_2018.json` (window + sources).
**Support (not counted):** Amazon Reviews 2023 (category map for the ESCI title bridge only), Astronomy Shop (live storefront, on hold).
**Dropped:** Amazon-M2, Tmall, iPinYou (moved to `~/data/mkt/archive`, reversible); Yahoo! R3 (unavailable); Keepa (paid).
**Rule:** each data type has one primary and at most one secondary, which must fill a measured gap, cover another part, be an independent check, or be a fallback.

## Findings that affect the design

| # | Finding (measured) | Affected data type | Status |
|---|---|---|---|
| 1 | Open Bandit has no position effect: CTR 0.35% / 0.35% / 0.34% across 3 slots (random policy) | Exposure, position, clicks | Kept for propensities/OPE; position curve becomes an explicit assumption (R6, pending) |
| 2 | Amazon-M2 has no event types or timestamps | Funnel, sessions | **Resolved**: M2 dropped; Taobao is the session primary |
| 3 | Retailrocket has no geography | Buyers, regions | **Resolved**: secondary removed; Olist alone |
| 4 | Olist has no titles, returns, promo flags or sessions | Products, returns, prices, traffic | Titles from ESCI, returns from UCI, price changes from M5; traffic level via the funnel anchor (CAVEATS) |
| 5 | YelpChi (graph version) has no timestamps or text | Fake reviews | Amazon fraud graph covers ring structure; burst timing assumed |
| 6 | Olist review presence is ~99% (survey) | Who leaves reviews | Coat is the secondary that supplies the selection shape |
| 7 | Reviews 2023 has no delivery fields; price for 16–45% of items | Review score, products | Used only for the category map and (if R2 = English) review text |
| 8 | iPinYou has prices but no randomised treatment | Ads | **Resolved**: dropped; ad lift from Criteo, checked by Hillstrom |
| 9 | Retailrocket "790" behaves like a price but is undocumented | Prices | Not used (M5 is the price-change secondary) |
| 10 | Astronomy Shop telemetry not auditable without running it | Service telemetry | On hold (A2); NAB/LogHub shapes as fallback |
| 11 | Tmall barely logs cart adds (76,750 vs 3.29M purchases) | Funnel | **Resolved**: Tmall dropped; Taobao has 5.5M cart events |
| 12 | Text languages differ: Olist Portuguese; ESCI/Reviews English | Review text, titles | **Resolved (R2)**: reviews stay Portuguese (Olist text matches its cause: late 1–2★ mention delay 41.2%, on-time 1–2★ mention product problems 21.7%); titles/queries English from ESCI; R5 dropped |
| 13 | ~~Seller reply time~~ **CORRECTED (external review, 21 Nov):** `review_answer_timestamp` is when the *customer* answers the satisfaction survey, which Olist emails at delivery or at the estimated date. There is no seller reply in the data | Review timing (L6), not seller health | Seller-responsiveness row removed; kept as "Review timing (survey sent, answered)" |
| 14 | Multi-seller products are mostly **sequential**: only 123 of 1,225 had two sellers selling in the same week | Multi-seller offers | Marked PARTIAL; simultaneous offers and rankings are synthetic (assumed), with Expedia showing how competing results are displayed |

## Pruning rule applied 21 Nov 2026

A dataset stays in a data-type row only if it works (PASS) or works partly on something real (PARTIAL). Removed pairings:
M5 for stock-outs (zero-sales proxy only), Taobao for traffic (9 days; Olist already has the shape), ESCI for position effects (no positions).
The two FAIL results in the claims below (Retailrocket "Buyers, regions", Amazon-M2 "Funnel") belong to pairings no longer in the design.
The design now uses 37 PASS and 8 PARTIAL dataset/row pairings, with 3 data types assumed and none failing, missing or downloading.

## Assumptions replaced by data, 21 Nov 2026

| Was assumed | Now | Evidence (measured) |
|---|---|---|
| Search position effect (and R6) | **Expedia hotel search** (Kaggle competition): 29.6% of results come from randomly ordered searches | Clicks relative to position 1: 0.76, 0.64, 0.55 … 0.33 at position 10; bookings 1.84% → 0.56% |
| Devices, channels | **Statcounter Brazil** (prior; PARTIAL: page views, not shoppers) + **Google Analytics** sessions | Brazil mobile share 28.7% → 24.0% (2016→2018); GA conversion desktop 1.58% vs mobile 0.41%; channels 0.05% (social) to 5.07% (referral) |
| Seller exit | **Fitted on Olist** (PARTIAL: censored at Aug 2018) | 52.5% of pre-2018 sellers silent after May 2018; small 92%, large 11% |
| Fees, payouts | **Still assumed** | No reliable public Olist fee schedule found; payouts have no public data |
| Logging faults | **Assumed on purpose** | Injected test lever; real rates from Retailrocket/Olist evidence |

Open Bandit keeps its roles for recommendation propensities and experiments; it no longer backs position effects.
