# Variable catalogue for the monitoring agent, by layer (generated from `mkt/variable_catalog.py`)

Each variable is something the marketplace itself would log; datasets are the sources behind it. All four `Contracts` kinds are used (metric, state, event, categorical), and every entry builds into a valid `Contracts` definition (`tests/test_variable_catalog.py`).

**Status:** *served* = the ports serve it today · *planned* = a verified dataset backs it, but the simulator layer is not built yet · *assumed* = no data, assumption-labelled lever.

| | served | planned | assumed |
|---|---|---|---|
| metric | 41 | 27 | 0 |
| state | 0 | 11 | 2 |
| event | 11 | 8 | 0 |
| categorical | 2 | 4 | 0 |

## L0 External environment

| Variable | Kind | Entity | Meaning | Why the agent needs it | Source | Status |
|---|---|---|---|---|---|---|
| `public_holiday` | state | calendar_day | national holiday on this day (name or 'none') | separates 'the world changed' from 'we broke something' on holiday dips | Holidays reference file (verified vs python-holidays) | planned |
| `commercial_event` | state | calendar_day | public commercial date (Black Friday, Christmas run-up, none) | explains the 6.2x Black Friday peak without blaming ranking or ads | Olist order series (fitted peak) + public calendar | planned |
| `usd_brl` | state | economy | USD/BRL exchange rate | price-driven demand shifts for imported goods | BCB SGS series 1 (daily) | planned |
| `selic_rate` | state | economy | Selic policy rate | credit-dependent purchase behaviour | BCB SGS (daily) | planned |
| `ipca_inflation` | state | economy | monthly consumer inflation | real vs nominal GMV change | BCB SGS (monthly) | planned |
| `logistics_disruption` | state | economy | known national logistics disruption (e.g. the May 2018 truckers' strike) | a delivery slowdown during a public strike is not a carrier or seller fault | Public record reference file; visible in Olist | planned |

## L1 Identity, scope, context

| Variable | Kind | Entity | Meaning | Why the agent needs it | Source | Status |
|---|---|---|---|---|---|---|
| `buyer_region` | categorical | buyer | buyer's state (UF) | regional slowdowns and Simpson-type mix shifts | Olist customers | served |
| `seller_region` | categorical | seller | seller's state (UF) | seller-side regional issues; same-region vs cross-region shipping | Olist sellers | planned |
| `product_category` | categorical | product | product category (top categories + other) | category-level demand and quality changes | Olist products (English names) | planned |
| `device` | categorical | session | device of a visit | device-specific breakage (mobile checkout bug) | Statcounter Brazil share + Google Analytics conversion ratio (transport cards TC-DV1, TC-DV2) | served |
| `traffic_channel` | categorical | session | marketing channel of a visit | a traffic-mix shift can look like a conversion drop | Google Analytics channelGrouping (transferred mix) | planned |
| `new_buyers_count` | metric | buyer | buyers placing their first order | acquisition vs retention changes | Olist customer_unique_id | planned |

## L2 Catalog, offers, competition

| Variable | Kind | Entity | Meaning | Why the agent needs it | Source | Status |
|---|---|---|---|---|---|---|
| `offer_price` | state | offer | current price of an offer | price changes are the first suspect for demand shifts | Olist item prices (levels) + M5 price-change frequency | planned |
| `price_changed` | event | offer | an offer's price changed | time-stamps the cause candidate for a demand change | M5 weekly change probability 1.2% (transferred) | planned |
| `offer_available` | state | offer | offer can be bought (in stock and listed) | stock-outs move sales to competitors | Retailrocket availability flag over time (transferred) | planned |
| `stockout_started` | event | offer | an offer went out of stock | explains a seller's sales drop without blaming ranking | Retailrocket availability transitions | planned |
| `active_offers_count` | metric | offer | offers listed and available | supply-side capacity | derived from offer_available | planned |
| `multi_seller_products_count` | metric | product | products offered by 2+ sellers at once | competition intensity (Buy-Box-like allocation) | Olist (123 simultaneous cases; rest synthetic, labelled) | planned |
| `search_submitted` | event | session | a search was run | search breakage and zero-result spikes | ESCI queries (bridged to the synthetic catalog) | planned |
| `zero_result_search_share` | metric | session | share of searches with no results (= zero_result_searches_count / searches_count) | search index or relevance regressions | ESCI relevance labels (bridged) | planned |
| `searches_count` | metric | session | searches run | denominator for search quality | ESCI (bridged) | planned |
| `zero_result_searches_count` | metric | session | searches that returned nothing | numerator for search quality | ESCI (bridged) | planned |

## L3 Seller health

| Variable | Kind | Entity | Meaning | Why the agent needs it | Source | Status |
|---|---|---|---|---|---|---|
| `seller_status` | state | seller | seller lifecycle state | seller churn shrinks supply before it shows in orders | Olist Funnel (entry) + Olist sales (exit, censored) | planned |
| `seller_onboarded` | event | seller | a seller closed a deal and joined | supply growth attribution | Olist Marketing Funnel (842 deals, 380 join to sales) | planned |
| `active_sellers_count` | metric | seller | sellers with a sale in the last 30 days | supply health | Olist sales | planned |
| `mean_handling_hours` | metric | seller | approval to carrier handover | seller-side fulfilment degradation vs carrier problems | Olist (approval and carrier timestamps) | planned |
| `platform_fee_rate` | state | platform | commission charged to sellers | fee changes change seller behaviour | none public (assumption A-10) | assumed |

## L4 Demand and discovery

| Variable | Kind | Entity | Meaning | Why the agent needs it | Source | Status |
|---|---|---|---|---|---|---|
| `sessions_count` | metric | session | shopping visits started | top of the funnel | anchored to Olist orders x Retailrocket visits per buyer (TC-RR1) | served |
| `views_count` | metric | session | items viewed | browsing depth | Retailrocket (TC-RR1) | served |
| `cart_adds_count` | metric | session | visits with a cart add | funnel stage | Retailrocket (TC-RR1) | served |
| `checkouts_count` | metric | session | visits that started checkout | funnel stage | Retailrocket + Taobao timing (TC-RR1, TC-TB1) | served |
| `cart_rate` | metric | session | cart adds per visit (= cart_adds_count / sessions_count) | funnel localisation | derived | served |
| `checkout_rate` | metric | session | checkouts per cart add (= checkouts_count / cart_adds_count) | funnel localisation | derived | served |
| `completion_rate` | metric | session | orders per checkout (= orders_count / checkouts_count) | checkout and payment breakage | derived | served |
| `views_per_session` | metric | session | views per visit (= views_count / sessions_count) | engagement shifts | derived | served |
| `item_viewed` | event | session | an item was opened from a results page | exposure denominators | Expedia random-order position curve (TC-EX1) | served |
| `session_started` | event | session | a visit started | traffic level and device mix | Retailrocket + Statcounter + GA (TC-RR1, TC-DV1, TC-DV2) | served |
| `cart_added` | event | session | an item went to the cart | funnel stage | Retailrocket (TC-RR1) | served |
| `checkout_started` | event | session | checkout started | payment and checkout faults | Taobao timing (TC-TB1) | served |
| `impressions_count` | metric | session | result slots shown | exposure denominator | Expedia result lists (TC-EX1) | planned |
| `ctr_position_1` | metric | session | click rate at result position 1 (= clicks_position_1_count / impressions_position_1_count) | ranking regressions show as a position-curve change | Expedia random order (TC-EX1) | planned |
| `clicks_position_1_count` | metric | session | clicks on position 1 | numerator | Expedia | planned |
| `impressions_position_1_count` | metric | session | position-1 slots shown | denominator | Expedia | planned |
| `recommendation_shown` | event | session | a recommendation was shown, with the logging policy's propensity | off-policy evaluation of recommender changes | Open Bandit (uniform + BTS propensities) | planned |
| `repeat_buyer_share` | metric | buyer | share of orders from returning buyers (= repeat_orders_count / orders_count) | retention changes | Olist 3.1% + Instacart interval shape | planned |
| `repeat_orders_count` | metric | buyer | orders by returning buyers | numerator | Olist | planned |
| `ad_exposure` | event | session | a sponsored placement was shown | paid vs organic attribution | Criteo uplift (randomised; transferred lift) | planned |

## L5 Transactions and fulfilment

| Variable | Kind | Entity | Meaning | Why the agent needs it | Source | Status |
|---|---|---|---|---|---|---|
| `orders_count` | metric | order | orders created in the window | core business outcome or its declared components | Olist (replay and chain) | served |
| `payments_count` | metric | order | payment approvals in the window (by approval time) | core business outcome or its declared components | Olist (replay and chain) | served |
| `gmv` | metric | order | sum of order amounts in the window | core business outcome or its declared components | Olist (replay and chain) | served |
| `deliveries_count` | metric | order | deliveries in the window (by delivery time) | core business outcome or its declared components | Olist (replay and chain) | served |
| `late_deliveries_count` | metric | order | deliveries in the window that arrived after the promised date | core business outcome or its declared components | Olist (replay and chain) | served |
| `late_share` | metric | order | share of deliveries that were late (= late_deliveries_count / deliveries_count) | core business outcome or its declared components | Olist (replay and chain) | served |
| `cohort_delivered_count` | metric | order | orders created in the window that were delivered by the data cut | core business outcome or its declared components | Olist (replay and chain) | served |
| `cohort_late_count` | metric | order | orders created in the window, delivered late by the data cut | core business outcome or its declared components | Olist (replay and chain) | served |
| `late_share_cohort` | metric | order | share of an order cohort delivered late (recent cohorts immature) (= cohort_late_count / cohort_delivered_count) | core business outcome or its declared components | Olist (replay and chain) | served |
| `mean_promised_days` | metric | order | mean promised delivery time | core business outcome or its declared components | Olist (replay and chain) | served |
| `mean_delivery_days` | metric | order | mean purchase-to-delivery time | core business outcome or its declared components | Olist (replay and chain) | served |
| `order_payment_ratio` | metric | order | accounting identity: every order is paid once (lags across window edges) (= payments_count / orders_count) | core business outcome or its declared components | Olist (replay and chain) | served |
| `order_placed` | event | order | an order is created (purchase time) | order lifecycle step (time-stamped) | Olist (replay and chain) | served |
| `item_sold` | event | order_item | one line item of an order at purchase time | order lifecycle step (time-stamped) | Olist (replay and chain) | served |
| `payment_approved` | event | order | the order's payment is approved | order lifecycle step (time-stamped) | Olist (replay and chain) | served |
| `handed_to_carrier` | event | order | the seller hands the order to the carrier | order lifecycle step (time-stamped) | Olist (replay and chain) | served |
| `delivered` | event | order | the order is delivered to the customer | order lifecycle step (time-stamped) | Olist (replay and chain) | served |
| `payment_type` | categorical | order | how the order was paid | payment-method-specific failures (boleto delays) | Olist payments | planned |
| `order_status` | state | order | order lifecycle status | stuck orders (approved, never shipped) | Olist order status | planned |
| `cancellations_count` | metric | order | orders canceled | cancellation spikes (stock or payment problems) | Olist (625 canceled + 609 unavailable) | planned |
| `returns_count` | metric | order | returned orders | quality problems after delivery | UCI 'C' invoices (transferred; Olist has no returns) | planned |

## L6 Trust and feedback

| Variable | Kind | Entity | Meaning | Why the agent needs it | Source | Status |
|---|---|---|---|---|---|---|
| `survey_sent` | event | order | a satisfaction survey is sent | satisfaction signal and its timing | Olist reviews | served |
| `review_score` | event | order | the customer answers the survey | satisfaction signal and its timing | Olist reviews | served |
| `mean_review_score` | metric | order | mean survey score | satisfaction follows lateness (4.29 on time vs 1.71 late) | Olist reviews | served |
| `survey_response_share` | metric | order | surveys answered / sent (= surveys_answered_count / surveys_sent_count) | who leaves reviews (MNAR); falling response hides dissatisfaction | Olist (~99%) + Coat selection shape | planned |
| `surveys_sent_count` | metric | order | surveys sent | denominator | Olist | planned |
| `surveys_answered_count` | metric | order | surveys answered | numerator | Olist | planned |
| `review_answer_lag_p50_hours` | metric | order | median survey answer delay | outcome maturity: recent weeks are incomplete | Olist (median 40 h) | planned |
| `review_text_submitted` | event | order | free-text review (pt-BR) | complaint themes behind a score drop | Olist review comments (41% have text; pooled by score x delivery) | planned |
| `flagged_reviews_count` | metric | review | reviews flagged by the platform's spam filter | review manipulation campaigns | YelpChi / Amazon fraud graph (detector shape; true labels stay privileged) | planned |

## L7 Platform and infrastructure

| Variable | Kind | Entity | Meaning | Why the agent needs it | Source | Status |
|---|---|---|---|---|---|---|
| `frontend.requests` | metric | service | requests of frontend | infrastructure-caused symptoms | Astronomy Shop telemetry (redacted) | served |
| `frontend.errors` | metric | service | errors of frontend | infrastructure-caused symptoms | Astronomy Shop telemetry (redacted) | served |
| `frontend.error_rate` | metric | service | error_rate of frontend | infrastructure-caused symptoms | Astronomy Shop telemetry (redacted) | served |
| `frontend.latency_p95_ms` | metric | service | latency_p95_ms of frontend | infrastructure-caused symptoms | Astronomy Shop telemetry (redacted) | served |
| `checkout.requests` | metric | service | requests of checkout | infrastructure-caused symptoms | Astronomy Shop telemetry (redacted) | served |
| `checkout.errors` | metric | service | errors of checkout | infrastructure-caused symptoms | Astronomy Shop telemetry (redacted) | served |
| `checkout.error_rate` | metric | service | error_rate of checkout | infrastructure-caused symptoms | Astronomy Shop telemetry (redacted) | served |
| `checkout.latency_p95_ms` | metric | service | latency_p95_ms of checkout | infrastructure-caused symptoms | Astronomy Shop telemetry (redacted) | served |
| `payment.requests` | metric | service | requests of payment | infrastructure-caused symptoms | Astronomy Shop telemetry (redacted) | served |
| `payment.errors` | metric | service | errors of payment | infrastructure-caused symptoms | Astronomy Shop telemetry (redacted) | served |
| `payment.error_rate` | metric | service | error_rate of payment | infrastructure-caused symptoms | Astronomy Shop telemetry (redacted) | served |
| `payment.latency_p95_ms` | metric | service | latency_p95_ms of payment | infrastructure-caused symptoms | Astronomy Shop telemetry (redacted) | served |
| `cart.requests` | metric | service | requests of cart | infrastructure-caused symptoms | Astronomy Shop telemetry (redacted) | served |
| `cart.errors` | metric | service | errors of cart | infrastructure-caused symptoms | Astronomy Shop telemetry (redacted) | served |
| `cart.error_rate` | metric | service | error_rate of cart | infrastructure-caused symptoms | Astronomy Shop telemetry (redacted) | served |
| `cart.latency_p95_ms` | metric | service | latency_p95_ms of cart | infrastructure-caused symptoms | Astronomy Shop telemetry (redacted) | served |
| `product-catalog.requests` | metric | service | requests of product-catalog | infrastructure-caused symptoms | Astronomy Shop telemetry (redacted) | served |
| `product-catalog.errors` | metric | service | errors of product-catalog | infrastructure-caused symptoms | Astronomy Shop telemetry (redacted) | served |
| `product-catalog.error_rate` | metric | service | error_rate of product-catalog | infrastructure-caused symptoms | Astronomy Shop telemetry (redacted) | served |
| `product-catalog.latency_p95_ms` | metric | service | latency_p95_ms of product-catalog | infrastructure-caused symptoms | Astronomy Shop telemetry (redacted) | served |
| `ranking_policy_version` | state | platform | ranking model version in production | a ranking release explains exposure shifts | platform release log (emulator lever) | planned |
| `deployment` | event | service | a service was deployed | change-point candidates for incidents | Astronomy Shop image versions (emulator release log) | planned |
| `experiment_conversion` | metric | buyer | conversion per arm of a randomised test | the agent's own experiments (served as assignment_outcomes) | Open Bandit / Criteo assignment shapes | planned |

## L9 Observation and measurement

| Variable | Kind | Entity | Meaning | Why the agent needs it | Source | Status |
|---|---|---|---|---|---|---|
| `ingestion_lag_p95_s` | metric | pipeline | 95th percentile of available_at - event_time | late data looks like a drop until it arrives | observation model (assumed A-11) + Olist batch evidence | planned |
| `late_corrections_count` | metric | pipeline | records replaced by a later revision | revisions change history after the fact | observation model (assumed A-12) | planned |
| `out_of_order_records_count` | metric | pipeline | lifecycle steps logged in an impossible order (e.g. handed to carrier before approval) | measurement anomalies vs real process changes | Olist (1,359 handovers before approval) | planned |
| `purchases_without_view_share` | metric | pipeline | purchases with no logged prior view (= purchases_without_view_count / orders_count) | logging gaps in the web stream | Retailrocket (11.9%) | planned |
| `purchases_without_view_count` | metric | pipeline | purchases with no logged view | numerator | Retailrocket | planned |
| `event_schema_version` | state | pipeline | schema version of the event stream | a schema change explains a field going null | emulator lever (assumed) | assumed |

## Not variables (considered and excluded)

| Layer | Field | Why not |
|---|---|---|
| L8 | fraud labels (YelpChi, Amazon fraud graph) | ground truth: privileged; the agent only sees the platform's own flags |
| L8 | fault flag states (Astronomy Shop flagd) | hidden-fault ground truth; redacted from telemetry by design (E3) |
| L8 | true lateness, latent quality, twin outcomes, intervention log | evaluator-only (privileged plane) |
| L10 | per-field provenance, coverage, freshness | already carried by every Observation (lineage, scope, clocks, dq); not variables |
| — | Criteo f0-f11, Open Bandit user features | anonymised model features with no marketplace meaning |
| — | Expedia hotel star rating, length of stay, competitor rates | lodging-specific; only the position curve transfers |
| — | Hillstrom spend, Instacart day-of-week, Taobao 'fav' | mechanism shapes for other variables, not marketplace signals here |
| — | M5 SNAP days | US food-stamp calendar: no Brazilian equivalent |
| — | NAB / LogHub series | anomaly shapes for synthetic telemetry only (fallback), not variables of this marketplace |
| — | ESCI product titles, brand, colour | catalog enrichment (bridged attributes), not time-varying variables |
