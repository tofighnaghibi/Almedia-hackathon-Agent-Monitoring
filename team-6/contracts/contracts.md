# Data & Interface Contracts

This document defines the shared contracts agreed upon before implementation so both tracks (Anila / "numbers" and Ayesha / "logic") can build and test in parallel.

> **Change policy:** Do not modify any contract in `contracts/` without discussing and agreeing with the other person first.

---

## 1. Data Tables (`L1 → Everything`)

All simulated marketplace data uses **UTC timestamps at hourly grain** (`YYYY-MM-DDTHH:MM:SSZ`) so seasonal baselines (e.g. same hour in previous weeks) and lag correlations align cleanly.

### 1.1 `marketplace_hourly` (Fact Table)

Granularity: `(timestamp, country, platform, category)`

| Column | Type | Description |
|---|---|---|
| `timestamp` | `datetime` (UTC) | Start of the hour (e.g. `2026-10-01T14:00:00Z`) |
| `country` | `string` | ISO-2 country code (e.g. `DE`, `UK`, `FR`, `US`) |
| `platform` | `string` | Client platform (`ios`, `android`, `web`) |
| `category` | `string` | Product category (e.g. `electronics`, `fashion`, `home`, `beauty`) |
| `sessions` | `int` | Total user visits / traffic |
| `searches` | `int` | Search queries executed |
| `search_clicks` | `int` | Clicks on search results |
| `recommendation_impressions` | `int` | Recommendation widget views |
| `recommendation_clicks` | `int` | Clicks on recommended products |
| `cart_adds` | `int` | Add-to-cart events |
| `checkouts` | `int` | Checkout flows started |
| `payment_attempts` | `int` | Payment submissions |
| `orders` | `int` | Completed orders (successful payments) |
| `refunds` | `int` | Refunded orders |
| `revenue` | `float` | Gross merchandise value / revenue in EUR |
| `ad_impressions` | `int` | Paid ad impressions |
| `ad_clicks` | `int` | Paid ad clicks |
| `ad_spend` | `float` | Ad spend in EUR |
| `active_listings` | `int` | Available product listings in segment |
| `out_of_stock_views` | `int` | Product views hitting out-of-stock items |

> **Data quality note (L4):** Late or missing data scenarios can omit rows for specific `(timestamp, country, platform, category)` slices or delay their appearance until a later `ingested_at` / batch timestamp.

### 1.2 `events` Table

Stores both **planned** business/system events and **unplanned** operational signals (releases, campaigns, holidays, data delays).

| Column | Type | Description |
|---|---|---|
| `event_id` | `string` | Unique identifier (e.g. `evt_001`) |
| `start_time` | `datetime` (UTC) | Event start time |
| `end_time` | `datetime` (UTC, nullable) | Event end time (`null` for point-in-time events like releases) |
| `event_type` | `string` | One of: `release`, `campaign`, `holiday`, `data_delay` |
| `planned` | `boolean` | `true` if on the planned-events calendar (e.g. promo, holiday); `false` otherwise |
| `country` | `string` (nullable) | Affected country, or `null` / `"*"` for all |
| `platform` | `string` (nullable) | Affected platform, or `null` / `"*"` for all |
| `category` | `string` (nullable) | Affected category, or `null` / `"*"` for all |
| `description` | `string` | Human-readable summary (e.g. `"Android app v4.2 checkout release in DE"`) |
| `expected_effect` | `string` (nullable) | Optional hint for planned events (e.g. `"+25% sessions"`) |

### 1.3 `ground_truth.json` (Scenario Injector Output → L5/L7 Evaluation)

Written by L1 Scenario Injector; **never** read by L4/L5/L6 during monitoring/investigation—only used by tests and L7 scoring.

```json
{
  "scenarios": [
    {
      "scenario_id": "scn_de_android_payment",
      "kind": "fault",
      "root_cause": "payment_gateway_timeout",
      "description": "Payment failure spike on Android in DE after checkout release",
      "start_time": "2026-10-15T10:00:00Z",
      "end_time": "2026-10-16T18:00:00Z",
      "affected_segment": {
        "country": "DE",
        "platform": "android"
      },
      "primary_metric": "payment_success_rate",
      "downstream_metrics": ["orders", "revenue"],
      "related_event_id": "evt_012"
    },
    {
      "scenario_id": "scn_uk_promo",
      "kind": "innocent",
      "root_cause": "planned_campaign",
      "description": "Planned autumn promo in UK",
      "start_time": "2026-10-18T00:00:00Z",
      "end_time": "2026-10-19T23:59:59Z",
      "affected_segment": {
        "country": "UK"
      },
      "primary_metric": "sessions",
      "downstream_metrics": ["orders", "revenue"],
      "related_event_id": "evt_015"
    }
  ]
}
```

---

## 2. Metric Definition (`L2 / L3`)

Stored as YAML files (e.g. in `graph/metrics/` or a single `metrics.yaml`).

### Schema

| Field | Type | Required | Description |
|---|---|---|---|
| `name` | `string` | Yes | Unique snake_case metric identifier |
| `description` | `string` | Yes | Plain-English meaning of the metric |
| `unit` | `string` | Yes | `count`, `ratio`, `currency_eur`, `rate` |
| `calculation` | `string` | Yes | SQL / DuckDB expression over `marketplace_hourly` |
| `dimensions` | `list[string]` | Yes | Allowed slice dimensions (e.g. `["country", "platform", "category"]`) |
| `source_table` | `string` | No | Defaults to `marketplace_hourly` |
| `higher_is_better` | `boolean` | No | `true`, `false`, or `null` if neutral |
| `business_weight` | `float` | No | Importance weight (`0.0–1.0`) used by L4 priority scoring |

### Examples

```yaml
- name: orders
  description: Total completed orders
  unit: count
  calculation: "SUM(orders)"
  dimensions: [country, platform, category]
  higher_is_better: true
  business_weight: 1.0

- name: payment_success_rate
  description: Share of payment attempts that result in a completed order
  unit: ratio
  calculation: "CAST(SUM(orders) AS DOUBLE) / NULLIF(SUM(payment_attempts), 0)"
  dimensions: [country, platform, category]
  higher_is_better: true
  business_weight: 0.9

- name: refund_rate
  description: Share of completed orders that are refunded
  unit: ratio
  calculation: "CAST(SUM(refunds) AS DOUBLE) / NULLIF(SUM(orders), 0)"
  dimensions: [country, platform, category]
  higher_is_better: false
  business_weight: 0.7
```

---

## 3. Graph Edge (`L2 / L3 → L5`)

Represents a directed relationship between two nodes in the metric graph.

### Schema

| Field | Type | Required | Description |
|---|---|---|---|
| `from` | `string` | Yes | Source metric name (or event/driver node) |
| `to` | `string` | Yes | Target metric name |
| `type` | `string` | Yes | One of: `formula`, `breakdown`, `possible_cause` |
| `formula` | `string` | If `type == "formula"` | Mathematical relationship (e.g. `orders = checkouts * checkout_to_order_rate`) |
| `dimension` | `string` | If `type == "breakdown"` | Dimension along which `to` decomposes into segments (e.g. `country`, `platform`, `category`) |
| `confidence` | `float` | Yes | Confidence score in `[0.0, 1.0]` (`1.0` for verified seed/formula edges) |
| `validated` | `boolean` | Yes | `true` if checked and confirmed against data; `false` for unverified hypotheses |
| `lag_hours` | `int` | No | Optional time lag in hours for `possible_cause` edges (default `0`) |
| `source` | `string` | No | `seed`, `auto_discovered`, or `llm_suggested` |

### Examples

```yaml
# Formula edge: orders depends on payment_attempts and payment_success_rate
- from: payment_success_rate
  to: orders
  type: formula
  formula: "orders = payment_attempts * payment_success_rate"
  confidence: 1.0
  validated: true
  source: seed

# Breakdown edge: total orders decomposes by country
- from: orders
  to: orders
  type: breakdown
  dimension: country
  confidence: 1.0
  validated: true
  source: seed

# Possible cause edge: discovered or hypothesized relationship
- from: out_of_stock_rate
  to: cart_adds
  type: possible_cause
  confidence: 0.68
  validated: false
  lag_hours: 1
  source: auto_discovered
```

---

## 4. Finding / Alert (`L4 / L5 → L6 / L7`)

Produced by L4 (initial anomaly detection) and enriched by L5 (root-cause investigation), then deduplicated, formatted, and tracked by L6 and displayed in L7.

### Schema

| Field | Type | Description |
|---|---|---|
| `finding_id` | `string` | Unique ID for this finding/alert instance |
| `fingerprint` | `string` | Stable hash of `(metric, segment, root_cause_key)` used by L6 to deduplicate ongoing issues |
| `metric` | `string` | Affected top-level or monitored metric name |
| `segment` | `dict[string, string]` | Dimension filter (e.g. `{"country": "DE", "platform": "android"}`; `{}` for overall) |
| `time_window` | `object` | `{"start": "<ISO-8601>", "end": "<ISO-8601>"}` |
| `expected` | `object` | `{"value": float, "lower": float, "upper": float}` |
| `actual` | `float` | Observed metric value in `time_window` |
| `change_size` | `object` | `{"absolute": float, "relative_pct": float, "score": float}` |
| `business_impact` | `object` | Estimated impact (e.g. `{"lost_orders": int, "lost_revenue_eur": float}`) |
| `evidence_list` | `list[object]` | Supporting data checks, correlated metric drops, and matching `events` |
| `trail` | `list[object]` | Ordered steps taken by L5 (`step`, `action`, `query`, `observation`) |
| `hypotheses` | `list[object]` | Ranked candidate explanations (`cause`, `confidence`, `status`: `confirmed` / `rejected` / `uncertain`) |
| `uncertainty` | `string` | Summary of what remains unverified or ambiguous |
| `status` | `string` | Lifecycle state managed by L6: `new`, `ongoing`, or `resolved` |
| `created_at` | `datetime` (UTC) | First detection timestamp |
| `updated_at` | `datetime` (UTC) | Most recent update timestamp |

### Example

```json
{
  "finding_id": "fnd_20261015_001",
  "fingerprint": "orders|DE|android|payment_success_rate",
  "metric": "orders",
  "segment": {
    "country": "DE",
    "platform": "android"
  },
  "time_window": {
    "start": "2026-10-15T10:00:00Z",
    "end": "2026-10-15T13:00:00Z"
  },
  "expected": {
    "value": 1240.0,
    "lower": 1110.0,
    "upper": 1370.0
  },
  "actual": 410.0,
  "change_size": {
    "absolute": -830.0,
    "relative_pct": -66.9,
    "score": 8.4
  },
  "business_impact": {
    "lost_orders": 830,
    "lost_revenue_eur": 45650.0
  },
  "evidence_list": [
    {
      "type": "metric_drop",
      "metric": "payment_success_rate",
      "segment": {"country": "DE", "platform": "android"},
      "expected": 0.94,
      "actual": 0.31,
      "contribution_pct": 96.2
    },
    {
      "type": "event_match",
      "event_id": "evt_012",
      "event_type": "release",
      "description": "Android app v4.2 checkout release in DE at 2026-10-15T09:45:00Z"
    }
  ],
  "trail": [
    {
      "step": 1,
      "action": "walk_formula",
      "query": "orders = payment_attempts * payment_success_rate",
      "observation": "payment_attempts normal (+1.2%); payment_success_rate dropped -67.0%"
    },
    {
      "step": 2,
      "action": "drill_dimension",
      "query": "breakdown payment_success_rate by country, platform",
      "observation": "country=DE, platform=android accounts for 96.2% of total drop"
    },
    {
      "step": 3,
      "action": "check_events",
      "query": "events matching DE/android near 2026-10-15T10:00:00Z",
      "observation": "Found release evt_012 15 minutes before drop"
    }
  ],
  "hypotheses": [
    {
      "cause": "Android v4.2 checkout release broke payment processing in DE",
      "confidence": 0.92,
      "status": "confirmed"
    }
  ],
  "uncertainty": "Exact payment gateway error code is not in the simulated telemetry.",
  "status": "new",
  "created_at": "2026-10-15T13:05:00Z",
  "updated_at": "2026-10-15T13:05:00Z"
}
```
