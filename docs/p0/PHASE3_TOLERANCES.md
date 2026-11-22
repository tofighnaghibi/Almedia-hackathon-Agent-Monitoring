# Phase 3: preregistered fidelity tolerances (gate S5)

*Committed BEFORE the chain simulator was built or any simulated statistic was computed (21 Nov 2026).*

**Setup.** Mechanisms are fitted only on purchases in 2017-01-01 … 2017-12-31 (`trained_through` = 2017-12-31). The simulation starts 2018-01-01 (gate S3: nothing fitted later than the start). Comparison set: orders **purchased 2018-01-01 … 2018-06-30** (held out), so deliveries and reviews have matured by the data cut. Both real and simulated events after 2018-10-17 are cut identically.

**Mechanism design decided in advance** (not tuned on 2018):
- Demand level: robust linear trend of weekly median daily orders over 2017, extrapolated into 2018; weekday and hour-of-day profiles from 2017.
- Sellers: pseudonymised 2017 sellers, chosen in proportion to 2017 sales; each keeps its region and a shrunk handling-time level.
- Fulfilment: approval delay, seller handling, and transit by same/different state, all log-normal; promise days by same/different state.
- Survey (phase-1 semantics): sent at the earlier of delivery and the promised date; answered after an empirical lag; answer probability from 2017.
- Score: empirical distribution given the lateness bucket (on time, 1–3, 4–10, >10 days late).
- Randomness: keyed by (seed, mechanism, order id), so every order's draws are stable.

| Id | Statistic (held-out real vs simulated) | Pass if |
|---|---|---|
| F1 | Mean daily orders | within ±20% |
| F2 | Weekday share of orders (7 values) | max absolute difference ≤ 0.02 |
| F3 | Hour-of-day profile | total variation distance ≤ 0.05 |
| F4 | Delivery days (purchase → delivered), q25 / q50 / q75 | each within ±20% |
| F5 | Late share (delivered orders) | within ±0.03 absolute |
| F6 | Mean score per lateness bucket (4 values) — **dependency** | each within ±0.25 stars |
| F7 | Survey answer lag after sending (median days); share of answers sent before delivery — **lag** | median within ±1 day; share within ±0.03 |
| F8 | Spearman correlation days_late ↔ score — **cross-layer** | within ±0.05 |
| F9 | Share of orders from the top 10% of sellers | within ±0.05 |
| F10 | Share of different-state orders; late-share gap (different-state minus same-state) | within ±0.05; within ±0.03 |

**Gate S5 passes only if all of F1–F10 pass.** If it fails: at most one strategy change plus up to 3 fix attempts, each using 2017 data only and logged in `docs/p0/STRATEGY_CHANGES.md`. If it still fails, P0 stops (D7 condition).
