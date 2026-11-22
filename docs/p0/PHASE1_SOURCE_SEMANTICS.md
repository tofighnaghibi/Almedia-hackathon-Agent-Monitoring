# Phase 1: Olist source semantics (gates S1, S2)

**Gate S1 (semantic validity): PASS** · **Gate S2 (join & lineage): PASS**

PASS = as expected · EXCEPTION = real data quirk, documented and handled · FAIL = blocks the gate.

## Checks

| Gate | Check | Field(s) | Expectation | Measured | Status | Handling |
|---|---|---|---|---|---|---|
| S2 | count.orders | orders | 99,441 rows (measured 21 Nov) | 99,441 | **PASS** |  |
| S2 | count.order_items | order_items | 112,650 rows (measured 21 Nov) | 112,650 | **PASS** |  |
| S2 | count.order_payments | order_payments | 103,886 rows (measured 21 Nov) | 103,886 | **PASS** |  |
| S2 | count.order_reviews | order_reviews | 99,224 rows (measured 21 Nov) | 99,224 | **PASS** |  |
| S2 | count.customers | customers | 99,441 rows (measured 21 Nov) | 99,441 | **PASS** |  |
| S2 | count.sellers | sellers | 3,095 rows (measured 21 Nov) | 3,095 | **PASS** |  |
| S2 | count.products | products | 32,951 rows (measured 21 Nov) | 32,951 | **PASS** |  |
| S2 | pk.orders | orders.order_id | unique | 0 duplicates | **PASS** |  |
| S2 | pk.customers | customers.customer_id | unique | 0 duplicates | **PASS** |  |
| S2 | pk.sellers | sellers.seller_id | unique | 0 duplicates | **PASS** |  |
| S2 | pk.products | products.product_id | unique | 0 duplicates | **PASS** |  |
| S2 | pk.order_items | order_items.(order_id, order_item_id) | unique | 0 duplicates | **PASS** |  |
| S2 | pk.order_payments | order_payments.(order_id, payment_sequential) | unique | 0 duplicates | **PASS** |  |
| S2 | pk.order_reviews | order_reviews.review_id | review_id unique | 789 review_ids cover >1 order; 547 orders have >1 review; 0 exact duplicate pairs | **EXCEPTION** | key = (review_id, order_id); keep the latest answer per order for 'the' score; flag multi-review orders |
| S2 | fk.order_items->orders | order_items.order_id | every row has a parent | 0 orphans | **PASS** |  |
| S2 | fk.order_payments->orders | order_payments.order_id | every row has a parent | 0 orphans | **PASS** |  |
| S2 | fk.order_reviews->orders | order_reviews.order_id | every row has a parent | 0 orphans | **PASS** |  |
| S2 | fk.orders->customers | orders.customer_id | every row has a parent | 0 orphans | **PASS** |  |
| S2 | fk.order_items->sellers | order_items.seller_id | every row has a parent | 0 orphans | **PASS** |  |
| S2 | fk.order_items->products | order_items.product_id | every row has a parent | 0 orphans | **PASS** |  |
| S2 | mult.orders->items | orders 1..n items | every order has >=1 item | 775 orders without items: [('unavailable', 603), ('canceled', 164), ('created', 5), ('invoiced', 2), ('shipped', 1)] | **EXCEPTION** | legitimate: unavailable/canceled orders carry no items; never impute items |
| S2 | mult.orders->payments | orders 1..n payments | every order has >=1 payment | 1 without payment | **EXCEPTION** | keep; mark payment unknown |
| S2 | mult.customer_id | orders.customer_id | exactly one order per customer_id (doc: per-order key) | max orders per customer_id = 1 | **PASS** |  |
| S2 | geo.not_a_key | geolocation.zip_prefix | many points per prefix (not a join key) | 1,000,163 rows for 19,015 prefixes | **PASS** | join via aggregated prefix centroid only; never row-join (would multiply rows) |
| S2 | items.sequence | order_items.order_item_id | 1..n without gaps per order | 0 orders with gaps | **PASS** |  |
| S1 | grain.estimated_delivery | order_estimated_delivery_date | date only | 100.0% at 00:00:00 | **PASS** |  |
| S1 | grain.review_creation | review_creation_date | date only (survey-sent date) | 99.9% at 00:00:00 | **PASS** |  |
| S1 | grain.purchase | order_purchase_timestamp | full timestamp | 0.00% at exactly midnight | **PASS** |  |
| S1 | order.purchase<=approved | purchase, approved | payment approved after purchase | 0 violations | **PASS** |  |
| S1 | order.approved<=carrier | approved, delivered_carrier | handover after approval | 1,359 handovers before approval | **EXCEPTION** | keep, flag; event order not guaranteed: replay must not assume approval precedes handover |
| S1 | order.carrier<=delivered | delivered_carrier, delivered_customer | delivery after handover | 23 deliveries before handover | **EXCEPTION** | keep, flag as timestamp error |
| S1 | order.promise>=purchase | estimated_delivery | promise not before purchase | 0 violations | **PASS** |  |
| S1 | status.delivered<=>date | order_status, delivered_customer_date | status 'delivered' iff a delivery date | 8 delivered without date; 6 with date but other status (6 canceled after delivery) | **EXCEPTION** | status is a snapshot at the data cut; trust timestamps for events, status for final state |
| S1 | status.cancel_time | order_status=canceled/unavailable | a time for cancellation | no cancellation timestamp exists in any table | **EXCEPTION** | cancellation is a STATE with unknown time; never stamp it with the purchase time (adapter fixed) |
| S1 | items.shipping_limit | shipping_limit_date vs delivered_carrier | seller deadline before most handovers | 9.4% of items handed over after the deadline | **PASS** | seller lateness signal (real) |
| S1 | review.answer>=sent | review_answer_timestamp, review_creation_date | customer answers after the survey is sent | 100.00% of 96,359 | **PASS** |  |
| S1 | review.sent_at_delivery_or_estimate | review_creation_date | survey sent ~ at delivery or at the estimated date (Olist docs) | 93.2% within ±1 day of delivery or estimate; 5.3% sent before delivery | **PASS** | confirms docs: creation = survey sent; review event time = answer time |
| S1 | review.anonymisation | review_comment_message | store names replaced by Game of Thrones houses (docs) | 1950 comments mention a GoT house | **PASS** | treat house names as a placeholder token, not real entities |
| S1 | money.signs | price, freight_value, payment_value | price > 0, freight >= 0, payment >= 0 (BRL) | 0 violations | **PASS** |  |
| S1 | money.reconcile | items vs payments | payments = price + freight per order (BRL) | 99.75% within 1 BRL | **PASS** | residual = instalment interest / vouchers; no settlement data exists, so no 'settled' event |
| S1 | payments.installments | payment_installments | >= 1; > 1 only for credit cards | [('boleto', 1, 1), ('credit_card', 0, 24), ('debit_card', 1, 1), ('not_defined', 1, 1), ('voucher', 1, 1)]; 2 rows with 0 | **EXCEPTION** | 0 instalments = data error; treat as 1 |
| S1 | products.name_is_length | product_name_lenght | a NUMBER of characters, not a name | type BIGINT, max 76 | **PASS** | titles must come from the ESCI bridge (labelled) |
| S1 | zip.prefix5 | customer/seller/geolocation zip prefix | 5-character text with leading zeros kept | type VARCHAR, length 5..5 (geolocation min 5) | **PASS** | always read as text; never cast to integer (would drop leading zeros, e.g. '09790') |
| S1 | category.translation | product_category_name | every category has an English name | 99.96%; missing: ['pc_gamer', 'portateis_cozinha_e_preparadores_de_alimentos'] | **EXCEPTION** | keep Portuguese name for the untranslated ones |

## Field dictionary

| Table | Field | Meaning | Kind | Unit | Actor | Source of meaning | Note |
|---|---|---|---|---|---|---|---|
| orders | `order_id` | order identifier | identifier | - | platform | docs |  |
| orders | `customer_id` | customer key PER ORDER (not a person): each order gets its own customer_id | identifier | - | platform | docs | use customer_unique_id for the person; checked: 1 customer_id per order |
| orders | `order_status` | lifecycle status at the data cut | category | - | platform | docs | values incl. delivered, shipped, canceled, unavailable, invoiced, processing, created, approved; 'unavailable' meaning inferred: no item could be fulfilled |
| orders | `order_purchase_timestamp` | when the customer placed the order | event_time | local time | customer | docs |  |
| orders | `order_approved_at` | when the payment was approved | event_time | local time | payment | docs |  |
| orders | `order_delivered_carrier_date` | when the SELLER handed the order to the logistics partner | event_time | local time | seller | docs | not 'shipped by platform'; can precede approval (data quirk) |
| orders | `order_delivered_customer_date` | when the carrier delivered to the customer | event_time | local time | carrier | docs |  |
| orders | `order_estimated_delivery_date` | delivery date promised to the customer at purchase | date_only | date | platform | docs | date only (00:00:00); the promise, not an event |
| order_items | `order_item_id` | sequence number of the item WITHIN the order (1..n) | identifier | - | platform | docs | there is no quantity column: quantity = number of rows per (order, product) |
| order_items | `product_id` | product identifier | identifier | - | platform | docs |  |
| order_items | `seller_id` | seller fulfilling THIS item (items of one order can have different sellers) | identifier | - | seller | docs |  |
| order_items | `shipping_limit_date` | deadline for the seller to hand the item to the logistics partner | event_time | local time | platform | docs |  |
| order_items | `price` | item price | measure | BRL | seller | docs |  |
| order_items | `freight_value` | freight charged for the item (order freight split over items) | measure | BRL | platform | docs |  |
| order_payments | `payment_sequential` | sequence when one order is paid with several methods | identifier | - | payment | docs |  |
| order_payments | `payment_type` | method: credit_card, boleto, voucher, debit_card, not_defined | category | - | customer | docs |  |
| order_payments | `payment_installments` | number of instalments chosen | measure | count | customer | docs |  |
| order_payments | `payment_value` | amount paid with this method | measure | BRL | payment | docs | a PAYMENT record; Olist has no seller settlement/payout data |
| order_reviews | `review_id` | review (survey) identifier | identifier | - | platform | docs | NOT unique: some review_ids cover several orders |
| order_reviews | `review_score` | 1-5 score the CUSTOMER gave in the satisfaction survey | label | 1-5 | customer | docs |  |
| order_reviews | `review_comment_title` | optional survey comment title | text | pt-BR | customer | docs |  |
| order_reviews | `review_comment_message` | optional survey comment | text | pt-BR | customer | docs | store/partner names replaced by Game of Thrones house names (anonymisation) |
| order_reviews | `review_creation_date` | date Olist SENT the satisfaction survey (at delivery or at the estimated date) | date_only | date | platform | docs | NOT when the customer reviewed |
| order_reviews | `review_answer_timestamp` | when the CUSTOMER answered the survey | event_time | local time | customer | docs | NOT a seller reply (corrected 21 Nov); this is when the score becomes known |
| customers | `customer_unique_id` | the person (stable across orders) | identifier | - | customer | docs |  |
| customers | `customer_zip_code_prefix` | first 5 digits of the customer's ZIP, as quoted text with leading zeros | location | zip5 | customer | measured |  |
| customers | `customer_city` | customer city | location | - | customer | docs |  |
| customers | `customer_state` | customer state (UF) | location | - | customer | docs |  |
| sellers | `seller_id` | seller identifier | identifier | - | seller | docs |  |
| sellers | `seller_zip_code_prefix` | first 5 digits of the seller's ZIP | location | zip5 | seller | docs |  |
| sellers | `seller_city` | seller city | location | - | seller | docs |  |
| sellers | `seller_state` | seller state (UF) | location | - | seller | docs |  |
| products | `product_category_name` | category name in Portuguese | category | pt-BR | seller | docs | English via the translation table |
| products | `product_name_lenght` | NUMBER OF CHARACTERS in the product name (the name itself is not released) | measure | characters | seller | docs | spelling 'lenght' is in the source |
| products | `product_description_lenght` | number of characters in the description | measure | characters | seller | docs |  |
| products | `product_photos_qty` | number of published photos | measure | count | seller | docs |  |
| products | `product_weight_g` | weight | measure | g | seller | docs |  |
| products | `product_length_cm` | length | measure | cm | seller | docs |  |
| products | `product_height_cm` | height | measure | cm | seller | docs |  |
| products | `product_width_cm` | width | measure | cm | seller | docs |  |
| geolocation | `geolocation_zip_code_prefix` | ZIP prefix | location | zip5 | platform | docs | NOT a key: many lat/lng rows per prefix |
| geolocation | `geolocation_lat` | latitude of a point in the prefix | location | degrees | platform | docs |  |
| geolocation | `geolocation_lng` | longitude of a point in the prefix | location | degrees | platform | docs |  |
| * | `(all timestamps)` | local time, naive (no timezone) | event_time | local time | platform | inferred | assumed America/Sao_Paulo; Brazil spans several zones and had DST in 2016-2018: hour-level analyses carry this uncertainty |
