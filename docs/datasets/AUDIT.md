# Dataset retrieval and audit

*Generated 2026-11-21 21:10 by `scripts/datasets.py report`. Data under `~/data/mkt/raw`.*

**Claims:** FAIL 1, PARTIAL 16, PASS 46

## Datasets

| Dataset | Fetch | Size | Origin / reason | Licence |
|---|---|---|---|---|
| Olist Brazilian E-Commerce | ok | 0.13 GB | kaggle olistbr/brazilian-ecommerce (+ marketing-funnel-olist) | CC BY-NC-SA 4.0 |
| Olist Marketing Funnel | ok | 0.00 GB | kaggle olistbr/marketing-funnel-olist | CC BY-NC-SA 4.0 |
| Retailrocket e-commerce | ok | 0.99 GB | kaggle retailrocket/ecommerce-dataset | CC BY-NC-SA 4.0 |
| Amazon-M2 (KDD Cup 2023) | **dropped** | 0.85 GB | redundant: Taobao covers session length with real timestamps; M2 has no event types/timestamps | AIcrowd challenge terms (not reviewed) |
| Amazon Shopping Queries (ESCI) | ok | 1.16 GB | github amazon-science/esci-data (official) | Apache-2.0 |
| Amazon Reviews 2023 (sample categories) | ok | 1.25 GB | HF McAuley-Lab/Amazon-Reviews-2023: category map only (asin2category.json); category files archived 21 Nov | research use; none declared on HF card |
| Open Bandit Dataset (ZOZOTOWN) | ok | 7.02 GB | HF zozonext/open-bandit (random + bts, all campaigns) | CC BY 4.0 |
| Criteo Uplift v2.1 | ok | 0.31 GB | HF criteo/criteo-uplift v2.1 | CC BY-NC-SA 4.0 |
| Banco Central do Brasil SGS | ok | 0.00 GB | api.bcb.gov.br SGS series 433, 432, 1 (2016-2018) | public data (terms of use of BCB) |
| Brazil holidays (reference file) | ok | 0.00 GB | reference file generated from rules (scripts/stitch.py _easter + fixed dates); checked against python-holidays | n/a |
| 2018 strike window (reference file) | ok | 0.00 GB | reference file: documented strike window with source links | n/a |
| M5 Forecasting (Walmart) | ok | 0.45 GB | kaggle competition m5-forecasting-accuracy | competition rules |
| Instacart Market Basket | ok | 0.71 GB | Kaggle copy (manual download by user, ~/Downloads/archive.zip, 207,073,669 bytes = psparks/instacart-market-basket-analysis size); original competition page is gone (404) | Instacart non-commercial terms (original); mirror |
| UCI Online Retail II | ok | 0.09 GB | UCI ML repository #502 | CC BY 4.0 |
| YelpChi (CARE-GNN graph) | ok | 0.23 GB | github YingtongDou/CARE-GNN data/YelpChi.zip (graph version of Rayana & Akoglu 2015) | research use |
| Amazon fraud users (CARE-GNN graph) | ok | 0.25 GB | github YingtongDou/CARE-GNN data/Amazon.zip (McAuley & Leskovec 2013 users, fraud labels) | research use |
| Coat (MNAR ratings) | ok | 0.00 GB | Cornell (Schnabel et al. 2016) coat.zip | research use |
| Yahoo! R3 | **dropped** |  | unavailable (Webscope unreachable); Coat covers review self-selection | Webscope agreement |
| iPinYou RTB (mirror) | **dropped** | 6.30 GB | not needed: auction prices only, no auction mechanism in the build | iPinYou release terms (mirror) |
| Hillstrom email campaign | ok | 0.00 GB | minethatdata.com (Kevin Hillstrom, 2008) | free for research |
| NAB + LogHub samples | ok | 0.01 GB | github numenta/NAB (data, labels) + logpai/loghub (2k-line samples) | AGPL-3.0 (NAB) / research (LogHub) |
| OpenTelemetry Astronomy Shop | ok | 0.00 GB | github open-telemetry/opentelemetry-demo (pinned clone) | Apache-2.0 |
| Tmall IJCAI-15 repeat buyers | **dropped** | 1.92 GB | redundant: repeat purchases covered by Olist + Instacart; carts barely logged | Tianchi research terms |
| Taobao User Behavior | ok | 3.67 GB | Tianchi dataset 649 (Taobao User Behavior), manual download, md5 verified | Tianchi research terms |
| Expedia hotel search (randomised order) | ok | 2.36 GB | kaggle competition expedia-personalized-sort (data.zip: train.csv) | competition rules |
| Google Analytics sessions (Merchandise Store) | ok | 1.50 GB | kaggle competition ga-customer-revenue-prediction (train.csv v1 only; 25 GB v2 not needed) | competition rules |
| Statcounter Brazil device share | ok | 0.00 GB | gs.statcounter.com: Brazil desktop/mobile/tablet share, monthly 2016-2018 (CSV export) | free with attribution (CC BY-SA, per site) [verify] |
| Keepa price history | **dropped** |  | paid source (rule: drop anything paid) | commercial |

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

## Claims by data type (stitching row)

### Ads, sponsored lift

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Criteo Uplift v2.1 | primary | randomised ad treatment with exposure, visit, conversion | **PASS** | 13,979,592 rows; treated 85.0%; exposure control 0.0000 vs treated 0.0360; visit/conv by arm [(0, 0.0382, 0.00194), (1, 0.0485, 0.00309)]; columns ['f0', 'f1', 'f2', 'f3', 'f4', 'f5', 'f6', 'f7', 'f8', 'f9', 'f10', 'f11', 'treatment', 'conversion', 'visit', 'exposure'] |
| Hillstrom email campaign | secondary | randomised campaign arms with visit/conversion/spend | **PASS** | columns ['recency', 'history_segment', 'history', 'mens', 'womens', 'zip_code', 'newbie', 'channel', 'segment', 'visit', 'conversion', 'spend']; arms [('Mens E-Mail', 21307, 0.1828, 0.0125), ('No E-Mail', 21306, 0.1062, 0.0057), ('Womens E-Mail', 21387, 0.1514, 0.0088)] |

### Buyers, regions

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Olist Brazilian E-Commerce | primary | buyer region | **PASS** | 27 Brazilian states |
| Retailrocket e-commerce | secondary | buyer region | **FAIL** | no geography field at all (only visitorid) -> design must drop this secondary |

### Cancellations, returns

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Olist Brazilian E-Commerce | primary | cancellation and return records | **PARTIAL** | canceled 625, unavailable 609; NO return records |
| UCI Online Retail II | secondary | cancelled / returned lines | **PASS** | 1,067,371 lines; 0.0% cancellation lines ('C' invoices) |

### Delivery promise, delays

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Olist Brazilian E-Commerce | primary | purchase, approval, carrier, delivered and estimated timestamps | **PASS** | non-null: approved 99.8%, carrier 98.2%, delivered 97.0%, estimate 100.0% |

### Devices, channels

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Statcounter Brazil device share | primary | Brazil device share per month (Olist years) | **PARTIAL** | 36 months; mobile 28.66% (2016-01) -> 23.99% (2018-12); desktop 69.14% -> 75.18%. Brazil-specific (differs from worldwide 55.9%->47.2% mobile and US), but it counts page views on sites using Statcounter, not shoppers -> a prior for device mix, not a measured shopper share |
| Google Analytics sessions (Merchandise Store) | secondary | sessions with device + marketing channel + purchase outcome | **PASS** | 903,653 sessions; conversion by device [('desktop', 664479, 0.0158), ('mobile', 208725, 0.0041), ('tablet', 30449, 0.0055)]; by channel [('Organic Search', 0.009), ('Social', 0.0005), ('Direct', 0.0143), ('Referral', 0.0507), ('Paid Search', 0.0185), ('Affiliates', 0.0005), ('Display', 0.0227), ('(Other)', 0.0083)] |
| OpenTelemetry Astronomy Shop | secondary | device/channel attributes | **PARTIAL** | web + react-native app exist; device mix not generated by the shop (Locust/driver decides) |

### Experiments, assignments

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Open Bandit Dataset (ZOZOTOWN) | primary | assignment probabilities known per impression | **PASS** | time span 2019-11-24 01:00:00.007365+01:00 .. 2019-12-01 00:59:59.408889+01:00 |
| Criteo Uplift v2.1 | secondary | known assignment + non-compliance | **PASS** | treatment is randomised; exposure records non-compliance |
| OpenTelemetry Astronomy Shop | secondary | feature-flag controlled conditions | **PASS** | flagd file-based flags: ['adFailure', 'adHighCpu', 'adManualGc', 'aiRunawayAgent', 'aiSlowResponse', 'cartFailure']… |

### Exposure, position, clicks

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Expedia hotel search (randomised order) | primary | position + click/booking under randomly ordered results | **PASS** | 9,917,530 result rows, 399,344 searches, 136,886 hotels; 29.6% of rows from randomly ordered searches; click rate relative to position 1 (random order): [(1, 1.0), (2, 0.76), (3, 0.64), (4, 0.55), (5, 0.39), (6, 0.49), (7, 0.43), (8, 0.39), (9, 0.36), (10, 0.33)]; booking@1 0.0184 vs @10 0.0056 |
| Open Bandit Dataset (ZOZOTOWN) | primary | position + click under a uniformly random policy; position effect present | **PARTIAL** | random: 1,374,327 rows, 3 positions, 80 items, propensity 0.01250..0.01250 (uniform, as required); CTR by position [(1, 0.0035), (2, 0.0035), (3, 0.0034)] -> no meaningful position effect (likely because ZOZOTOWN shows the 3 slots together; unverified), so it cannot supply a search-ranking decay curve |
| Amazon Shopping Queries (ESCI) | secondary | rank positions / exposure | **PARTIAL** | relevance labels only: no positions, impressions or clicks |

### External shocks (2018 strike)

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Olist Brazilian E-Commerce | primary | visible shock in strike week | **PASS** | orders 1837 (week of 14 May) -> 954 (week of 21 May) |
| 2018 strike window (reference file) | secondary | documented strike window | **PASS** | 21 May 2018 start; effectively over 31 May-1 Jun, formal end 2 Jun (Wikipedia, Reuters via Stabroek News 1 Jun, Garda alert 1 Jun) |

### Fake reviews, fraud rings

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| YelpChi (CARE-GNN graph) | primary | fraud labels + reviewer relations + timing for bursts | **PARTIAL** | 45,954 reviews, 14.5% labelled spam; arrays {'homo': (45954, 45954), 'net_rur': (45954, 45954), 'net_rtr': (45954, 45954), 'net_rsr': (45954, 45954), 'features': (45954, 32), 'label': (1, 45954)}; relations R-U-R / R-T-R / R-S-R present, but NO raw timestamps or text -> burst sizes not measurable from this version |
| Amazon fraud users (CARE-GNN graph) | secondary | fraud-labelled users + user relations | **PASS** | 11,944 users, 6.9% fraud; arrays {'homo': (11944, 11944), 'net_upu': (11944, 11944), 'net_usu': (11944, 11944), 'net_uvu': (11944, 11944), 'features': (11944, 25), 'label': (1, 11944)} |

### Funnel view -> cart -> buy, timing

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Retailrocket e-commerce | primary | typed events with ms timestamps | **PASS** | columns ['timestamp', 'visitorid', 'event', 'itemid', 'transactionid']; {'view': 2664312, 'addtocart': 69332, 'transaction': 22457}; 2015-05-03 .. 2015-09-18 |
| Taobao User Behavior | secondary | typed events with second-level timestamps | **PASS** | 100,150,807 events from 987,994 users; {'pv': 89716264, 'cart': 5530446, 'fav': 2888258, 'buy': 2015839}; 99.8% within 2017-11-24..2017-12-03; 0.1% outside the documented 9 days |

### Handling time, reliability

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Olist Brazilian E-Commerce | primary | approval -> carrier handover per seller | **PASS** | carrier date present 98.2% |

### Inventory, stock-outs

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Retailrocket e-commerce | primary | time-varying availability flag | **PASS** | 'available' on 417,053 items, values 2 (snapshot-based, weekly-ish) |
| M5 Forecasting (Walmart) | secondary | stock-outs | **PARTIAL** | 30,490 item-store series; only zero-sales runs, no stock field (proxy) |

### Logging gaps, delays, late data

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Olist Brazilian E-Commerce | secondary | timestamp anomalies as evidence of measurement issues | **PASS** | 1359 orders handed to carrier before approval |
| Retailrocket e-commerce | secondary | purchases without a logged prior view | **PASS** | 11.9% of purchased (visitor,item) pairs have no earlier view |

### Macro: inflation, FX, interest

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Banco Central do Brasil SGS | primary | daily/monthly macro series 2016-2018 | **PASS** | ipca_monthly_pct: 36 obs 01/01/2016..01/12/2018 range -0.23..1.27; selic_target_pct: 1096 obs 01/01/2016..31/12/2018 range 6.50..14.25; usd_brl: 750 obs 04/01/2016..31/12/2018 range 3.05..4.19 / expected: Selic should fall ~14 -> ~6.5; IPCA monthly within -0.5..1.5; USD/BRL ~3-4.2 |

### Multi-seller offers

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Olist Brazilian E-Commerce | primary | same product sold by several sellers AT THE SAME TIME | **PARTIAL** | 3.7% of products had >1 seller over the whole period, but only 123 of 1225 had two sellers selling in the same week -> mostly sequential, not simultaneous competition; displayed offers and rankings are synthetic |
| Expedia hotel search (randomised order) | secondary | (bonus) many sellers competing in one result list | **PASS** | columns include ['prop_starrating', 'prop_review_score', 'price_usd', 'promotion_flag', 'comp1_rate'] |

### Orders, items, payments

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Olist Brazilian E-Commerce | primary | order/item/payment tables joined by order_id | **PASS** | 99,441 orders; 0 orphan rows |
| UCI Online Retail II | secondary | invoice lines with quantity and price | **PASS** | columns ['Invoice', 'StockCode', 'Description', 'Quantity', 'InvoiceDate', 'Price', 'Customer ID', 'Country']; dates 2009-12-01 07:45:00..2011-12-09 12:50:00 |

### Prices, promotions

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Olist Brazilian E-Commerce | primary | price per sold item, variation over time | **PARTIAL** | price on every item; 17.9% of products sold at >1 price; no list prices or promotion flags |
| M5 Forecasting (Walmart) | secondary | weekly prices with changes | **PASS** | weekly change probability 1.2%; no explicit promo flag (drops inferred) |
| Retailrocket e-commerce | secondary | price history (property 790 assumed = price) | **PARTIAL** | 1,790,516 snapshots, 100.0% numeric 'n…' format, 20.4% of items change value; meaning 'price' is inferred, not documented (hashed properties) |

### Products, categories, titles

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Olist Brazilian E-Commerce | primary | product categories (no titles) | **PARTIAL** | category coverage 98.6%; NO product titles or brands (names removed) -> titles must come from ESCI/Reviews bridge |
| Amazon Shopping Queries (ESCI) | secondary | product titles and brands | **PASS** | US products 1,215,854; title 100.0%, brand 94.1%; columns ['product_id', 'product_title', 'product_description', 'product_bullet_point', 'product_brand', 'product_color', 'product_locale'] |

### Recommendation policy, propensities

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Open Bandit Dataset (ZOZOTOWN) | primary | logged propensities for two policies | **PASS** | bts: 12,357,200 rows, propensity 1e-06..0.9542; columns ['column00', 'timestamp', 'item_id', 'position', 'click', 'propensity_score', 'user_feature_0', 'user_feature_1']… |

### Repeat purchases

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Olist Brazilian E-Commerce | primary | stable buyer id across orders | **PASS** | customer_unique_id; repeat-buyer share 3.1% |
| Instacart Market Basket | secondary | inter-purchase intervals | **PASS** | 3,421,083 orders, 206,209 users, median gap 7.0 days; columns ['order_id', 'user_id', 'eval_set', 'order_number', 'order_dow', 'order_hour_of_day', 'days_since_prior_order']; no absolute dates |
| UCI Online Retail II | secondary | customers with several invoices | **PASS** | 75.3% of 5,942 customers buy more than once |

### Review score given delivery

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Olist Brazilian E-Commerce | primary | 1-5 score linked to delivery outcome | **PASS** | mean score on time 4.29 vs late 2.27 |

### Review text

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Olist Brazilian E-Commerce | primary | free-text review comments | **PASS** | 41.3% of reviews have text (Portuguese) |

### Review timing (survey sent, answered)

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Olist Brazilian E-Commerce | primary | when the satisfaction survey is sent and when the CUSTOMER answers it | **PASS** | survey sent at delivery or at the estimated date (Olist description); answer timestamp present 100.0%; customer answers after median 40.2 h, p90 116.5 h. NOT a seller reply (corrected 21 Nov after external review) |

### Search queries, relevance

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Amazon Shopping Queries (ESCI) | primary | query text + human E/S/C/I labels per product | **PASS** | 2,621,288 judgements, 130,652 queries, 1,802,772 products; US label mix {'C': 0.015, 'I': 0.062, 'E': 0.476, 'S': 0.141}; columns ['example_id', 'query', 'query_id', 'product_id', 'product_locale', 'esci_label', 'small_version', 'large_version', 'split', '__index_level_0__'] |
| Amazon Reviews 2023 (sample categories) | secondary | category for each Amazon item (to place ESCI items in mapped categories) | **PASS** | asin2category.json maps 35,393,189 parent ASINs to 34 categories (measured earlier); 36.8% of ESCI-US items covered. Review/meta category files archived 21 Nov (audited earlier: ratings + English text present, no delivery fields) |

### Seasonality, holidays, Black Friday

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Olist Brazilian E-Commerce | primary | daily order series with seasonal peaks | **PASS** | Black Friday peak 6.2x Oct-Dec median |
| Brazil holidays (reference file) | secondary | national holiday calendar | **PASS** | vs python-holidays Brazil: missing [], extra [] (carnival/corpus christi are optional points in the reference) |
| M5 Forecasting (Walmart) | secondary | calendar events + SNAP days | **PASS** | 162 event days of 1969; calendar columns ['date', 'wm_yr_wk', 'weekday', 'wday', 'month', 'year', 'd', 'event_name_1', 'event_type_1', 'event_name_2', 'event_type_2', 'snap_CA', 'snap_TX', 'snap_WI'] |

### Seller entry, ramp-up

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Olist Marketing Funnel | primary | acquisition date + channel, joinable to sales | **PASS** | 842 closed deals, 380 join to Olist sales on seller_id; MQL columns ['mql_id', 'first_contact_date', 'landing_page_id', 'origin'] |
| Olist Brazilian E-Commerce | secondary | first sale per seller | **PASS** | first order date per seller_id derivable |

### Seller exit

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Olist Brazilian E-Commerce | primary | sellers that stop selling, by size | **PARTIAL** | 52.5% of sellers active before 2018 had no sale after May 2018 (small <=5 sales 92.3%, large >50 10.6%); data ends Aug 2018, so quiet occasional sellers look like exits (censoring) |

### Sellers, regions, size

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Olist Brazilian E-Commerce | primary | seller ids with region and sales volume | **PASS** | 3,095 sellers; state present 100.0% |
| Olist Marketing Funnel | secondary | seller segment / type | **PASS** | business_segment, lead_type, business_type columns |

### Service telemetry, faults

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| OpenTelemetry Astronomy Shop | primary | real services emitting OTel telemetry with injectable faults | **PARTIAL** | source @ 7a88536: 21 fault flags; telemetry not observed yet (running the stack = decision A2) |
| NAB + LogHub samples | secondary | labelled anomaly windows + system logs | **PASS** | NAB 58 series, 116 labelled windows; LogHub 4 2k-line samples ['HDFS_2k.log', 'Apache_2k.log', 'OpenStack_2k.log', 'Linux_2k.log'] |

### Session length, revisits

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Retailrocket e-commerce | secondary | events per visitor-day | **PASS** | visitor id + timestamps give visitor-day sessions (no explicit session id) |
| Taobao User Behavior | secondary | event gaps to cut sessions | **PASS** | no session id, but second-level timestamps: median gap between a user's events 76s, p90 12167s -> sessions by inactivity gap |

### Traffic volume, calendar

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Olist Brazilian E-Commerce | primary | volume level and calendar profile | **PARTIAL** | orders only: no sessions/visits, so traffic level is inferred via the funnel (anchor step) |
| Retailrocket e-commerce | secondary | hourly traffic pattern | **PASS** | event timestamps (UTC ms) |
| Taobao User Behavior | secondary | hourly traffic pattern | **PARTIAL** | only 9 days: hour-of-day yes, weekly/seasonal no |

### Who leaves reviews (MNAR)

| Dataset | Role | Claim | Status | Evidence |
|---|---|---|---|---|
| Olist Brazilian E-Commerce | primary | review presence observable per order | **PARTIAL** | presence observable, but ~99% of orders have a review (survey-driven), so self-selection is weak; Coat/Yahoo needed for MNAR shape |
| Coat (MNAR ratings) | secondary | self-selected ratings + randomly assigned ratings | **PASS** | train (self-selected) (290, 300), 6,960 ratings; test (random) 4,640 ratings; mean rating train 2.61 vs random 2.23 |
