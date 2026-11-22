# Variable taxonomy (generated from `mkt/taxonomy.py`; do not edit by hand)

Conforms to `Contracts/` schema 2.0: each variable is one of the contract kinds, names its entity, and derived metrics are ratios with a closed `formula_ast`. Checked by `tests/test_contracts_conformance.py`.

## Metrics

| Variable | Entity | Aggregation | Type | Unit | Population | Formula | Identity tolerance | Meaning |
|---|---|---|---|---|---|---|---|---|
| `orders_count` | order | count | count | orders | orders_created (full) | — | — | orders created in the window |
| `payments_count` | order | count | count | orders | payments_approved (full) | — | — | payment approvals in the window (by approval time) |
| `gmv` | order | sum | real | BRL | orders_created (full) | — | — | sum of order amounts in the window |
| `deliveries_count` | order | count | count | orders | deliveries (full) | — | — | deliveries in the window (by delivery time) |
| `late_deliveries_count` | order | count | count | orders | deliveries (full) | — | — | deliveries in the window that arrived after the promised date |
| `late_share` | order | ratio | proportion | — | deliveries (full) | late_deliveries_count / deliveries_count | — | share of deliveries that were late |
| `cohort_delivered_count` | order | count | count | orders | orders_created_cohort (full) | — | — | orders created in the window that were delivered by the data cut |
| `cohort_late_count` | order | count | count | orders | orders_created_cohort (full) | — | — | orders created in the window, delivered late by the data cut |
| `late_share_cohort` | order | ratio | proportion | — | orders_created_cohort (subset) | cohort_late_count / cohort_delivered_count | — | share of an order cohort delivered late (recent cohorts immature) |
| `mean_promised_days` | order | mean | real | days | orders_created (full) | — | — | mean promised delivery time |
| `mean_delivery_days` | order | mean | real | days | orders_created_cohort (subset) | — | — | mean purchase-to-delivery time |
| `mean_review_score` | order | mean | real | score 1-5 | surveys_answered (full) | — | — | mean survey score |
| `order_payment_ratio` | order | ratio | real | — | orders_created (full) | payments_count / orders_count | 0.02 | accounting identity: every order is paid once (lags across window edges) |
| `sessions_count` | session | count | count | sessions | sessions (full) | — | — | shopping visits started |
| `views_count` | session | count | count | views | sessions (full) | — | — | items viewed from results pages |
| `cart_adds_count` | session | count | count | sessions | sessions (full) | — | — | visits with a cart add |
| `checkouts_count` | session | count | count | sessions | sessions (full) | — | — | visits that started checkout |
| `cart_rate` | session | ratio | proportion | — | sessions (full) | cart_adds_count / sessions_count | — | funnel: cart adds per visit |
| `checkout_rate` | session | ratio | real | — | sessions (full) | checkouts_count / cart_adds_count | — | funnel: checkouts per cart add (buy-now skips the cart) |
| `completion_rate` | session | ratio | real | — | sessions (full) | orders_count / checkouts_count | — | funnel: orders per checkout (about 1; can exceed 1 when a checkout and its order fall either side of a window edge) |
| `views_per_session` | session | ratio | real | views per session | sessions (full) | views_count / sessions_count | — | views per visit |

## Events

| Variable | Entity | Engine event | Fields (identity first) | Meaning |
|---|---|---|---|---|
| `order_placed` | order | `order_created` | order_id: text, region: category, amount: real, promised_days: real | an order is created (purchase time) |
| `item_sold` | order_item | `order_item` | order_id: text, seller_id: text, product_id: text, region: category, price: real, freight: real | one line item of an order at purchase time |
| `payment_approved` | order | `payment_approved` | order_id: text, region: category, amount: real | the order's payment is approved |
| `handed_to_carrier` | order | `handed_to_carrier` | order_id: text, region: category | the seller hands the order to the carrier |
| `delivered` | order | `delivered` | order_id: text, region: category, days_late: real | the order is delivered to the customer |
| `survey_sent` | order | `survey_sent` | order_id: text, region: category | a satisfaction survey is sent |
| `review_score` | order | `review_submitted` | order_id: text, region: category, score: integer | the customer answers the survey |
| `session_started` | session | `session_started` | session_id: text, region: category, device: category, n_views: integer | a shopping visit starts |
| `item_viewed` | session | `item_viewed` | session_id: text, region: category, device: category, position: integer | an item is viewed from a results page |
| `cart_added` | session | `cart_added` | session_id: text, region: category, device: category | an item is added to the cart |
| `checkout_started` | session | `checkout_started` | session_id: text, region: category, device: category, completed: boolean | checkout is started |

## Owner decisions applied (22 Nov 2026)
- **G1:** variable catalogue = this taxonomy, served read-only through `definitions()`.
- **G2:** replayed real history is `offline_replay`, not `production`; its evidence role is therefore `SANDBOX_OBSERVED`.
- **G3:** transported parameters are carried in namespaced `extensions` (`mkt:transport_card`, `mkt:transport_status`): ok.
