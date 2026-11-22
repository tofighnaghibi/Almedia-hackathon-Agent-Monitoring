# P1-2 Session layer: design and preregistered checks

Written and committed **before** any source estimate is computed on held-out halves and before any simulated output exists.
Changing a tolerance after measuring means logging it in `STRATEGY_CHANGES.md` and marking the check as failed.

## 1. Design: sessions wrap the order chain

The P0 chain stays the source of orders (anchored to Olist). The session layer adds what happens **around** each order, plus the sessions that buy nothing:

```
shoppers arrive ─┬─ buying session  (one per chain order) ── views ─ results page ─ click ─ cart ─ checkout ─ order_created (chain)
                 └─ non-buying session (Poisson, same hourly shape as orders × k) ── views ─ maybe cart ─ leave
```

- **Chain unchanged.** With the session layer on or off, every chain column is bit-identical (gate S7). Sessions are keyed by `order_idx` (buying) or by the global hour key (non-buying), never by run position.
- **Levels from Olist, shapes from elsewhere.** The number of buying sessions equals Olist-anchored orders. Every transferred quantity is a **ratio or shape**, and each has a transport card.
- **New world levers** (via the gateway): `checkout.failure_rate`, which makes a share of checkouts fail; the order is lost and the session ends as `checkout_failed`. `demand.multiplier` now also scales non-buying sessions.
- **New observed events**, in the same vocabulary style: `session_started`, `item_viewed`, `results_shown` (exposure, positions 1–10), `cart_added`, `checkout_started`, plus `order_created` (from the chain). Each carries `device`.
- **New metrics** with declared identities: `sessions_count`, `cart_adds_count`, `checkouts_count`; identity `orders_count ~ checkouts_count` (each order comes from one checkout) and the ordered funnel `sessions → cart_adds → checkouts → orders`.

## 2. One source per quantity (transport cards)

| Card | Quantity transferred | Source (primary) | Source population | Assumption carried over |
|---|---|---|---|---|
| TC-RR1 | views per session (buying, non-buying); share of non-buying sessions with a cart add; non-buying sessions per buying session (k) | Retailrocket events, sessions cut at 30 min idle | visitors of one e-commerce site, May–Sep 2015 | ratios, not levels; k is the **least** transferable (site-specific conversion), labelled *partially supported* at best |
| TC-TB1 | time from first view of the bought item to purchase; cart → purchase lag | Taobao user behaviour | Taobao users, 25 Nov–3 Dec 2017 (9 days, includes the run-up to Double 12) | lag shape, truncated at 9 days |
| TC-EX1 | click probability by results position 1–10, relative to position 1 | Expedia hotel search, **randomly ordered** searches only | Expedia searches 2012–2013, lodging | relative decay carries over; absolute click level is not used |
| TC-DV1 | device mix of all sessions, by month | Statcounter Brazil desktop/mobile/tablet | page views on sites using Statcounter, Brazil 2016–2018 | page-view share approximates session share |
| TC-DV2 | conversion ratio mobile : desktop | Google Analytics sample (merchandise store) | one US store, 2016–2017 | the *ratio* carries over; buying-session device mix = share × relative conversion (Bayes) |

Every card records `trained_through`, an estimate with a 95% bootstrap CI, its held-out result and a status: **supported**, **partially supported** or **unsupported transfer**. A failed held-out check does not block the build. It downgrades the status, and the sensitivity range becomes the span of both halves.

## 3. Preregistered checks

**Held-out stability inside each source (chronological halves; the status is downgraded if a check fails):**

| ID | Check | Tolerance |
|---|---|---|
| H1 | Retailrocket mean views per session, first vs second half | relative difference ≤ 10% |
| H2 | Retailrocket non-buying cart-add share | absolute difference ≤ 0.02 |
| H3 | Taobao median first-view → buy lag, first 4.5 days vs last 4.5 days | relative difference ≤ 25% |
| H4 | Expedia relative CTR at positions 2–10, odd vs even search dates | every position's halves overlap within their 95% CIs |
| H5 | GA mobile:desktop conversion ratio, first vs second half of the period | relative difference ≤ 30% |

**Implementation fidelity (simulated vs the card's estimate; the build fails if these fail):**

| ID | Check | Tolerance |
|---|---|---|
| F1 | mean views per session (buying and non-buying separately) | within 5% of the card estimate |
| F2 | non-buying cart-add share | within 0.01 absolute |
| F3 | first-view → buy lag median and 90th percentile | within 10% / 15% |
| F4 | relative CTR at positions 2–10 | ≥ 9 of 9 inside the card's 95% CI |
| F5 | mobile share of all sessions, per month | within 0.01 of Statcounter |
| F6 | buying sessions = chain orders; every order has exactly one session that starts before it | exact |

**Invariance (gate S7) and time (gate S3):**

| ID | Check |
|---|---|
| V1 | Chain columns are bit-identical with the session layer on or off |
| V2 | Chunked run (Jan + Feb) equals one run (Jan–Feb) for all session events |
| V3 | A checkout failure changes only sessions inside its window; the twin pairs every session |
| V4 | No session event has `available_at` earlier than its event time; cards record `trained_through`; sources from other periods are labelled transport, not leakage |

**Incremental value (gate S10, decided before running):**
Scenario pair **9a vs 9b**, 5 seeds each, plus a control:
- **9a checkout failure:** 40% of checkouts fail for one week.
- **9b demand drop:** 40% fewer shoppers for one week.

On the order chart both look the same, and both are real losses. The question is **where in the funnel** the change starts. The stand-in reader walks the declared funnel in order and reports the first stage whose ratio to the previous stage breaks (robust z ≥ 3, other ratios quiet).

| Variant | Expected result |
|---|---|
| **With the session layer** | 9a localised to `checkouts → orders`; 9b localised to `sessions` (ratios quiet, levels down). Pass if ≥ 4/5 correct for each, and ≤ 1/5 false alarms in the control |
| **Without it** (orders and payments only) | the reader cannot localise either: abstains, or calls both "real change" |

S10 passes if the session layer turns *cannot tell* into *correct* on this pair. If it doesn't, the layer is parked as no measurable gain, per §13.0 phase 7.

## 4. Order of work
1. Fit cards TC-RR1, TC-TB1, TC-EX1, TC-DV1, TC-DV2 (source data only) and run H1–H5.
2. Build the layer in `mkt/sim/sessions.py`, one card at a time, with tests for F1–F6 and V1–V4.
3. Run the 9a/9b scenario and score it (S10).
4. Report in `P1_2_SESSION_LAYER.md`.
