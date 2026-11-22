# Argus API contract

Base path: `/api`. All bodies are JSON. All endpoints accept an optional `X-Session` header; each session gets its own simulated world. Without the header the shared `default` session is used. Sessions are capped by memory (default 13, from a 350 MB budget: ~100 MB base + ~16–18 MB per session; override with `ARGUS_MAX_SESSIONS` or `ARGUS_MEM_BUDGET_MB`), the least recently used session is evicted first, and sessions idle for 30 min expire (`ARGUS_SESSION_TTL_S`).

### `GET /api/health`
`{ "ok": true, "llm": "model id" | null, "sessions": 3, "max_sessions": 12, "session_ttl_s": 1800, "evicted": 0, "expired": 0, "rss_mb": 146.2, "mem_budget_mb": 350 }`

The built frontend (`frontend/dist`) is served at `/` when it exists.

**Time labels.** Hour `t` covers simulated UTC `t:00–(t+1):00` (sim day 0 is a Monday). Every label is the time the
hour's data are complete ("data through 04:00"): the clock shows the end of the last processed hour, series points and
timeline events are labelled by the end of their hour, and planned calendar events by their start time.

## Read endpoints

### `GET /api/state`
Globe state.
```jsonc
{
  "clock": { "t": 336, "label": "Day 15 · Mon 01:00 UTC", "utc_hour": 1, "day_of_week": "Mon", "day": 15 },  // end of last processed hour; for day/night
  "countries": [
    {
      "iso": "CA", "name": "Canada", "lat": 56.1, "lng": -106.3,
      "status": "ok",              // "ok" | "watch" (|z|≥4 for 1 h) | "incident" (persists ≥2 h and impact > $150/h or |z|≥8) | "explained" | "waiting"
      "health_z": 0.4,             // max |z| over the country's KPIs (signed value of that KPI)
      "kpis": {                    // last-hour observed values
        "new_accounts": 0, "completions": 0, "revenue": 0,
        "rewards": 0, "margin": 0, "quality_per_completion": 0
      },
      "potential_usd_day": 1234.0,  // twin-estimated margin uplift if the feed matched local taste
      "potential_pct": 2.4,         // same, as % of the country's current daily margin
      "attention": 0.924,           // 0..1: how much this market needs a human now = $ at stake × confidence / $250k (fixed cap)
      "attention_usd": 230938       // the raw $ figure (7-day). incident: Σ open issues' risk_7d_usd × explained share of
                                    // the top cause; watch: margin-side deviation (USD/h) × 168 h × P(becomes an issue),
                                    // P = (|z| − 3) / 5 clipped to 0..1; 0 for ok / explained / waiting, and 0 while an
                                    // approved fix is being verified. Demo: CA bot farm 0.92, GB tracking break 0.64, DE promo 0
    }
  ],
  "incidents": [
    { "id": "inc-1", "title": "...", "country": "CA", "status": "open", "severity": "high",
      "impact_usd_h": 134.0,      // current deviation in USD/h
      "risk_7d_usd": 114042,      // 7-day margin lost by doing nothing vs the recommended fix (p50, twin)
      "reaction_h": 27,           // hours until advertisers react (chargebacks), from the do-nothing route's events; 0 = losses accrue now; null = n/a
      "short_title": "Fake accounts on Android in Canada",   // card title, ≤ ~6 words (tracking break:
                                       // "Advertisers stopped confirming offers in the UK")
      "title_detail": null,            // rest of the full title as a detail line, e.g. "Casual-game and strategy-game
                                       // offers on iOS"; null when short_title says it all. `title` stays the full sentence.
                                       // Both also in /api/incidents and the incident detail.
      "found_line": "Found by Argus at 06:00, nobody asked · advertisers would react in ~28 h",
                                       // detection hour (end of hour, HH:00) + reaction_h; reaction_h 0 → "· losses are
                                       // happening now"; reaction_h null → no second part. Also in /api/incidents and detail.
      "status_label": "Still going",   // "Open" | "Still going" | "Fix applied" | "Fix verified" | "Resolved"
      "first_seen": "Mon 04:00",       // first hour out of the normal zone (or detection time)
      "last_seen": "Mon 09:00",        // last hour the alert condition held
      "resolved_at": null,             // time label once resolved
      "resolved_t": null,              // same moment as a numeric hour t (like detected_at)
      "alerts_merged": 3,              // later alerts for the same problem folded into this issue (never reported twice)
      "recurrence_of": null,           // id of an earlier resolved issue with the same country/platform/root cause
      "downstream": [                  // predicted consequences attached to this issue (not reported separately)
        { "effect": "chargeback", "platform": "iOS", "category": "Fintech apps", "t": 365, "label": "Tue 11:00",
          "after_h": 29, "predicted_after_h": 28,
          "text": "Advertisers reclaiming money: fintech apps on iOS, caused by the farm, as predicted (~28 h, seen after 29 h)" } ],
      "card": { ... } }           // plain-language issue card, see below
  ],
  "added_metrics": [               // metrics added via POST /api/metrics, now monitored
    { "id": "margin_per_signup", "label": "margin_per_signup", "expr": "margin / new_accounts",
      "unit": "USD per new account", "added_at": 335, "status": "ok",          // "ok" | "watch" (|z| ≥ 4 somewhere)
      "max_z": { "country": "US", "z": 1.3 }, "note": "Added because you asked for 'margin_per_signup'. ..." }
  ],
  "explained": [
    { "t": 350, "country": "DE", "event": "Weekend Boost (Casual games, Strategy games): rewards +36% vs a normal week, as expected for this event",
      "naive_z": 5.1, "twin_z": 0.7,
      "change_pct": 36,                // signed % move of the KPI vs the normal (naive) baseline that the event explains
      "kpi": "rewards",                // which KPI moved (KPI label)
      "line": "Germany +36% · planned promo, no alarm" }   // noun: "planned promo" (calendar promo), "late data", else the event name
  ],
  "attention": { "watched_series": 120, "investigations": 1, "l3_runs": 1,
                 "segments_total": 240,        // countries × platforms × offer categories = 20 × 2 × 6
                 "segments_opened": 1,         // distinct segments Argus walked the causal chain in (L2), this session
                 "users_total": 8816000,       // daily users across the 20 watched countries (simulated, calibrated)
                 "users_checked": 600 }        // user records pulled for account-level checks (L3), this session
// Demo: start 0/240 · 0 users; bot_farm 1/240 · 600 of 8,816,000; tracking_break 2/240 · 600 (no user check needed)
}
```
Incident `status`: `open` | `persisting` | `fix_applied` | `verified` | `resolved` (show `status_label`).
Issue tracking: an alert is merged into an open issue only if it has the same country and the same cause (platform ×
cause family: users / revenue / rewards). A strong new root of another family, holding 2 h, opens its own issue. An
advertiser reaction the issue predicted (chargebacks: revenue per completion, budget cut: impressions, in the issue's
offer categories, near the predicted hour) is attached to it as `downstream` instead of a new issue. With a fix: resolved after verification plus 6 healthy hours. Without a fix: resolved after 6 healthy
hours ("Recovered without action" in the trail). A returning problem opens a new issue with `recurrence_of` set. `severity`: `low` | `medium` | `high`,
from risk rather than hourly impact: high if `risk_7d_usd` ≥ $25k, or ≥ $10k with advertisers reacting within 24 h;
medium if ≥ $5k, or ≥ $1k with a reaction within 24 h; low otherwise.

### Issue card (`incident.card`, in `/api/state` incidents and `/api/incidents/{id}`)
Plain short sentences for the issue card; no z-scores or model terms (those stay in `reports.engineer`).
Recomputed on every read, so `status_line` follows the incident.
```jsonc
{
  "title": "Fake accounts on Android in Canada",
  "looks_like": "+$85/h of 'growth'",              // how it looks on a naive dashboard
  "at_stake_7d_usd": 156194,                        // = risk_7d_usd
  "what_happened": "About 103 fake accounts appeared on Android in Canada and are completing high-paying strategy-game and fintech offers.",
  "why": "Their stated interests don't match what they do and they share emulator devices (proven, 94% explained).",
  "what_to_do": "Verify new accounts: require phone and device checks for new Android sign-ups. Saves about $156,194 over the next 7 days.",
  "status_line": "Needs a decision"                 // "Needs a decision" | "Fix applied · verifying" | "Prediction verified" |
                                                    // "Fix applied · prediction missed, model recalibrated" | "Resolved[ · prediction verified]"
}
```
After approval `what_to_do` becomes "Done: <fix> applied."

### `GET /api/incidents/{id}`
```jsonc
{
  "id": "inc-1", "title": "...", "status": "open", "severity": "high", "impact_usd_h": 134.0, "risk_7d_usd": 114042, "reaction_h": 21,
  "found_line": "Found by Argus at 06:00, nobody asked · advertisers would react in ~21 h",
  "short_title": "Fake accounts on Android in Canada", "title_detail": null,
  "country": "CA", "platform": "android", "category": "fintech", "detected_at": 352,
  "trail": [ { "t": 352, "level": "L1", "action": "...", "finding": "..." } ],   // level: "L1" | "L2" | "L3"
  "graph": {
    "nodes": [ { "id": "new_accounts", "label": "New accounts", "z": 9.3, "state": "root" } ],  // state: "root" | "broken" | "symptom" | "normal"
    "edges": [ { "source": "new_accounts", "target": "active_users", "type": "mechanism" } ]
  },
  "contributions": [ { "mechanism": "new_accounts", "value_usd": -420.0 } ],          // exact Shapley over mechanisms, sums to Δmargin
  "hypotheses": [ { "name": "Fraud cluster", "explained": 0.94, "verdict": "supported" } ],  // verdict: "supported" | "partial" | "rejected"
  "l3": {                                                                               // null when no L3 run
    "summary": { "n_users": 600, "n_flagged": 28, "estimated_total": 103,   // estimated_total: from excess sign-ups (same number as the card)
                  "coherence_flagged": 0.28, "coherence_others": 0.73, "top_devices": ["emulator-x86"] },
    "points": [ { "coherence": 0.71, "payout_focus": 0.2, "flagged": false, "device": "Phone 12", "account_age_days": 140 } ],
    "links": [ { "a": 22, "b": 34 } ],               // flagged accounts sharing a device (indices into points; ≤120)
    "groups": [ { "device": "Emu-A1", "count": 15 } ] // flagged accounts per device, largest first
  },
  "fixes": [
    {
      "id": "block_cluster", "name": "Block the fake accounts", "description": "...",
      "days": [1,2,3,4,5,6,7], "p10": [], "p50": [], "p90": [],                           // cumulative margin USD
      "delta_p50_usd": 15000.0,                                                            // day-7 p50 vs do nothing
      "delta_daily": { "p10": [], "p50": [], "p90": [] },   // cumulative margin vs do nothing, days 1..7 (paired per simulated future)
      "events": [                                           // advertiser reactions in the twin's simulated future (≥50% of paths)
        { "after_h": 27, "label": "Advertisers start reclaiming money", "probability": 1.0 },
        { "after_h": 50, "label": "Advertiser cuts the budget", "probability": 1.0 }
      ],                                                    // labels also: "Advertiser fixes tracking"; [] when none apply
      "advertiser_risk": "..."
    }
  ],
  "recommended": "block_cluster",
  "applied": null,                                                                         // fix id once approved
  "verification": { "status": "pending", "predicted": [10, 50, 90], "actual": null },       // status: "pending" | "verified" | "missed"
  "reports": { "ceo": "...", "manager": "...", "engineer": "...", "source": "template" },  // source: "template" | "llm"
  "card": { ... },                                                                          // issue card (above)
  "series_meta": { "kpi": "new_accounts", "label": "New accounts, Canada · Android", "unit": "per hour" },
  "series": [                                     // alerting KPI for the root segment, hourly, last 48 h up to now
    { "t": 344, "label": "Mon 08:00", "actual": 77.8, "expected": 47.5, "lo": 41.7, "hi": 54.2 }   // lo/hi = twin ±2σ
  ],
  "left_zone_at": { "t": 344, "label": "Mon 08:00" },  // first hour of the excursion that led to detection; null if none
  "counterfactual": {                             // twin replay with the supported cause removed; null if no supported cause
    "label": "Without the fake accounts (28 flagged in a sample of 600, about 103 in total)",  // sample count + estimated total
    "series": [ { "t": 344, "value": 47.5 } ],          // same hours as `series`; equals `actual` before left_zone_at
    "explained_share": 0.942
  }
}
```

### `replay` (in `GET /api/incidents/{id}`): "Watch Argus think"
How Argus found the issue, step by step. Every sentence is built from the incident's own data, in plain words.
```jsonc
"replay": {
  "l1": { "sentence": "New sign-ups in Canada left their usual range at 04:00.",
          "metric": "New sign-ups per hour, Canada", "kpi": "new_accounts",
          "series": [ { "t": 318, "label": "Sun 07:00", "actual": 176.2, "expected": 170.9, "lo": 150.1, "hi": 194.6 }, ... ],
          "left_at": { "t": 339, "label": "Mon 04:00" } },
  "l2": { "sentence": "It's Canada, Android.", "measure": "completions",
          "cells": [ { "platform": "Android", "category": "Fintech apps", "z": 2.5, "driving": true }, ... ] },   // 12 cells
  "walk": { "sentence": "Margin, revenue and rewards moved → completed offers fine → offer starts moved → offers shown and active users fine → new sign-ups: the cause.",
            "steps": [ { "node": "margin", "label": "margin", "verdict": "affected" }, ...,
                       { "node": "new_accounts", "label": "new sign-ups", "verdict": "cause" } ] },
  "l3": { "sentence": "15 of 600 users checked look fake: most share 2 devices, and their interests don't fit what they do.",
          "ran": true },
  "proof": { "sentence": "Without the fake accounts, 99% of the change disappears.", "explained": 0.988 }
}
```
How each step is built:
- **`l1`:** the country number that triggered the alert, i.e. the one with the largest move at detection. For the farm
  that's new sign-ups, because country margin never left its range.
  - The series covers the 24 h up to detection, with lo/hi = the usual ±2σ for a single hour.
  - `left_at` is the start of the last run of hours outside the band. If no single hour was outside, `left_at` is null
    and the sentence says the number stayed off its usual level for 3 hours.
- **`l2`:** z per platform × category at detection time. `measure` names which number the z is for: completions for
  user-side issues, revenue for tracking, rewards for pricing. `driving` marks the issue's own cells.
- **`walk`:** the causal chain is checked from margin backwards: margin → revenue → rewards → completions → offer starts →
  offers shown → active users → new sign-ups. It stops at the cause.
  - verdict "cause" = the root, "affected" = that number moved (it broke or was knock-on), "fine" otherwise.
- **`l3`:** `ran: false` when Argus didn't need to check single users. The sentence then reads "Users behave as usual,
  so no need to check single accounts."
- **Tracking break, same block:** "Margin in the United Kingdom left its usual range at 09:00." → "It's the United
  Kingdom, iOS, casual-game and strategy-game offers." → "Margin moved → revenue: the cause." → no user check → "If
  advertisers confirmed offers as usual, 94% of the change disappears."

### `GET /api/incidents`
List of the same summaries as `state.incidents`.

### `GET /api/graph`
The metric graph with typed edges.
```jsonc
{
  "nodes": [ { "id": "margin", "label": "Contribution margin", "unit": "USD/h", "kind": "money", "level": "L2",
               "parents": ["revenue", "rewards"], "edge": "formula", "expr": "revenue - rewards", "aggregation": "sum",
               "origin": "core",            // "core" | "extension" (added via POST /api/metrics)
               "breakdown": ["country", "platform", "offer category"],   // users: ["country", "platform"]
               "note": "" } ],              // extension nodes: the "Added because ..." note
  "edges": [                                // drawable between nodes (+ the "calendar" source)
    { "source": "new_accounts", "target": "active_users", "type": "mechanism", "label": "Possible cause" },
    { "source": "revenue", "target": "margin", "type": "formula", "label": "Formula" },
    { "source": "calendar", "target": "starts", "type": "exogenous", "label": "Known event" } ],
  "breakdown_edges": [                      // metric -> first dimension, then the dimension chain
    { "source": "margin", "target": "dim:country", "type": "breakdown", "label": "Breakdown" },
    { "source": "dim:country", "target": "dim:platform", "type": "breakdown", "label": "Breakdown" },
    { "source": "dim:platform", "target": "dim:offer_category", "type": "breakdown", "label": "Breakdown" } ],
  "dimensions": [ { "id": "dim:country", "label": "Country (20)" }, { "id": "dim:platform", "label": "Platform (iOS, Android)" },
                  { "id": "dim:offer_category", "label": "Offer category (6)" } ],
  "edge_types": [                           // legend: one entry per edge type
    { "type": "formula",   "label": "Formula",        "meaning": "the metric is computed from its inputs (e.g. margin = revenue − rewards)", "reasoning": "exact decomposition: how much each input contributes to a change" },
    { "type": "breakdown", "label": "Breakdown",      "meaning": "the same metric split by country → platform → offer category", "reasoning": "localise: find the segment that carries the change" },
    { "type": "mechanism", "label": "Possible cause", "meaning": "the upstream metric drives this one (e.g. starts → completions)", "reasoning": "root cause: test each link given its parents, counterfactual proof" },
    { "type": "exogenous", "label": "Known event",    "meaning": "planned marketing events change these metrics on purpose", "reasoning": "explain innocent changes instead of alerting" } ],
  "exogenous": [ { "id": "calendar", "label": "Planned events (marketing calendar)" } ],
  "levels": { "L1": "...", "L2": "...", "L3": "..." }
}
```

### `GET /api/graph/tree`
The same graph as a browsable tree: business outcomes (margin, quality per completion) and added metrics at the top,
then formula inputs, then possible causes. A metric reached a second time has `repeat: true` (show "see above").
```jsonc
{ "roots": ["margin", "quality_per_completion", "margin_per_signup"],
  "rows": [   // depth-first, render as an indented list
    { "depth": 0, "id": "margin", "label": "Contribution margin", "relation": null, "relation_label": null,
      "expr": "revenue - rewards", "unit": "USD/h", "origin": "core",
      "breakdown": "global → country → platform → offer category", "repeat": false },
    { "depth": 1, "id": "revenue", "label": "Advertiser revenue", "relation": "formula", "relation_label": "Formula", ... },
    { "depth": 2, "id": "completions", "label": "Completions", "relation": "mechanism", "relation_label": "Possible cause", ... }
  ],
  "edge_types": [ ... ] }   // same legend as /api/graph
```

### `GET /api/country/{iso}`
L2 heatmap.
```jsonc
{ "iso": "CA", "name": "Canada",
  "segments": [ { "platform": "Android", "category": "Fintech apps",
                  "kpi_z": { "new_accounts": 12.4,      // platform-level (sign-ups have no category); same for all categories of the platform
                             "completions": 2.6, "revenue": 2.7, "rewards": 2.6, "quality_per_completion": -1.9 },
                  "deviation_usd_h": 55.0 } ],          // margin deviation vs the twin, USD/h
  "platforms": [ { "platform": "Android", "new_accounts_z": 12.4, "active_users_z": -0.3,
                   "new_accounts_h": 95.5, "new_accounts_expected_h": 64.8 } ] }
```

`segments[]` also carries `kpi_ratio`: observed / usual for the same KPIs as `kpi_z` (3 h window). Sign-ups are
platform-level, like their z.
```jsonc
{ "platform": "iOS", "category": "Casual games", "kpi_z": { ... },
  "kpi_ratio": { "new_accounts": 0.982, "completions": 0.863, "revenue": 0.139, "rewards": 0.899, "quality_per_completion": 0.944 },
  "deviation_usd_h": -251.3 }
```

`summary` (added for the simplified country view; same response, extra key):
```jsonc
"summary": {
  "status": "ok" | "watch" | "incident" | "explained" | "waiting",   // same as the globe
  "verdict": "Fake accounts on Android",           // ONE sentence: incident title without the country; explained:
                                                   // "+22% · planned promo, no alarm"; waiting: "Data arriving late: ...";
                                                   // watch: "Something unusual: watching ..."; ok: "All fine"
  "numbers": [                                     // always 3, averages of the last 3 h vs the twin's usual
    { "label": "Margin", "value": 2964.2, "usual": 2810.5, "unit": "USD/h", "line": "$3.0k an hour · usual $2.8k" },
    { "label": "New sign-ups", "value": 214, "usual": 176, "unit": "per hour", "line": "214 an hour · usual 176" },
    { "label": "Completed offers", "value": 1475, "usual": 1435, "unit": "per hour", "line": "1,475 an hour · usual 1,435" } ],
  "series": { "metric": "Margin per hour", "kpi": "margin", "unit": "USD/h",
              "change_label": null,                // explained only, e.g. "Completed offers +10% vs a normal Monday"
              "points": [ { "t": 341, "label": "Mon 06:00", "value": 2426.3, "usual": 2360.0, "lo": 1984.7, "hi": 2735.3,
                            "normal": 2360.0 } ],
              "events": [ { "t": 336, "label": "Planned promo", "kind": "start", "time_label": "Mon 02:00" } ] },
                                                   // calendar events of this country starting/ending inside the 24 h
                                                   // (kind "start" | "end", end label "Planned promo ends"); [] if none
                                                   // last 24 h; lo/hi = usual ±2σ (single hour, twin backtest error)
  "segments_total": 12,                            // platforms × categories in this country: "All 12 segments as expected"
  "unusual": [                                     // ALL unusual segments, most money at stake first; [] for "ok" countries
    { "line": "iOS · casual & strategy: revenue −86% vs usual", "platform": "iOS",
      "categories": ["Casual games", "Strategy games"],      // null = all offers (sign-ups are platform-level)
      "kpi": "revenue", "ratio": 0.14, "z": -11.4,
      "view": "why",                               // "who" = user-side cause (sign-ups, or the platform of an open
                                                   // fake-account / new-user issue), "why" otherwise
      "money_usd_h": 503.2 } ],                    // |margin deviation| of those segments, USD/h (rank key)
  "moving": [                                      // ≤ 2, empty when status is "ok" or nothing has |z| ≥ 4
    { "line": "Android: sign-ups 1.6× usual", "platform": "Android", "category": null, "kpi": "new_accounts", "ratio": 1.58 },
    { "line": "iOS · casual games: revenue -86% vs usual", "platform": "iOS", "category": "Casual games", "kpi": "revenue", "ratio": 0.14 } ]
}
```
`series` details:
- **Which number:** margin for every status except "explained". For an explained country it is the number that moved,
  the same one as the verdict's percentage. Example: the DE promo gives metric "Completed offers per hour", kpi
  "completions", unit "per hour". kpi is one of new_accounts, completions, revenue, rewards, margin,
  quality_per_completion (unit "share").
- **`usual`:** the twin's expectation, which knows the planned event. It rises with the promo, so `value` stays inside
  lo/hi.
- **`normal`:** a normal day, meaning the same weekday and hour with no planned event. It is the naive baseline the
  event is compared against. Outside an event, `normal` equals `usual`. Draw value against normal to show the jump.
- **`change_label`:** the verdict's percentage, worded against the weekday of the current clock. It is null unless
  the status is "explained".

How `unusual` is built:
- **Evidence:** the same as `moving` (|z| ≥ 4, calendar-aware), but nothing is capped.
- **Merging:** categories on one platform whose same number moves the same way become one line. The ratio is their
  geometric mean and z is the strongest one.
- **Example lines:** the bot farm gives "Android · all offers: sign-ups 1.6× usual" with view "who".

How `moving` is built:
- **Evidence:** the same as the heatmap, i.e. the twin's calendar-aware z per segment. A planned promo therefore doesn't
  show up here.
- **Sign-ups (`kpi: "new_accounts"`):** they are platform-level. `category` is filled only when one category's
  completions also have |z| ≥ 4.
- **Ratio format:** a ratio ≥ 1.5 reads "N× usual", otherwise "±N% vs usual".

### `GET /api/potential`
```jsonc
{ "countries": [ { "iso": "DE", "potential_usd_day": 1432.0, "potential_pct": 3.0, "current_margin_usd_day": 48441.0,
                   "top_categories": ["Streaming apps", "Shopping cashback"],      // under-served AND locally distinctive
                   "explain": ["watches series weekly", "night owl", "parent"],    // life signals behind the top category
                   "demand_index": { "Casual games": 1.02, "Strategy games": 0.92, "...": 1.0 },  // local / global demand
                   "mix": [ { "category": "Fintech apps", "liked": 0.164, "seen": 0.554 }, ... ],   // all 6 categories;
                                       // liked = share users would choose (taste-model demand, local), seen = share of
                                       // impressions shown, last 24 h; each column sums to 1 (± rounding)
                   "mostly_seen": ["Fintech apps"],                     // 1–2 categories with the most offers shown today
                                                                        // (2nd only if ≥ 75% of the 1st's share)
                   "mostly_seen_shares": { "Fintech apps": 0.489 },     // share of impressions, last 24 h
                   "line": "Germany: users like streaming apps and shopping cashback, but mostly see fintech apps. Showing more of what they like: about +$3,601 a day." } ],
  "summary_line": "Showing each country more of what its users like: about +$35,572 a day in total · biggest in the United States" }
```
### `POST /api/what_if/offer_mix` — `{ "country": "US" }`
Read-only 7-day rollout in the twin: today's offer mix against the better-fit mix behind the Growth number. Nothing in
the session world changes. An unknown country returns `400`.
```jsonc
{ "country": "US", "days": [1, 2, 3, 4, 5, 6, 7],
  "current": { "p10": [...7], "p50": [...7], "p90": [...7] },      // cumulative margin USD, end of each day
  "better":  { "p10": [...7], "p50": [...7], "p90": [...7] },
  "gain_week_usd": { "p10": 81602, "p50": 138835, "p90": 216423 },  // better − current after 7 days (same paths)
  "gain_day_usd": 19834,                                            // p50 / 7, close to potential_usd_day
  "line": "+$138,835 over the week", "applied": false }
```
How the rollout works:
- **Both options** use the twin's hourly forecast for the country and the same 40 random paths: a level shift from the
  twin's backtest error, plus hourly noise.
- **The better mix** scales each category's margin by the Growth model's per-category gain.
- **Uncertainty:** the size of that gain is uncertain (lognormal, σ 0.35, an assumption), which widens the p10–p90.

`line` variants:
- **Normal:** "users like X, but mostly see Y. Showing more of what they like: about +$N a day."
- **What they like is already among what they mostly see:** "users like X and already see some, but more would add
  about +$N a day."
- **Nothing to gain:** "users already mostly see what they like."

Here "like" = `top_categories`. Screen wording avoids "match", "taste", "feed" and "potential".

### `GET /api/timeline`
What Argus knows, in time order. Built only from the public calendar, observed data and Argus's own decisions; never
from simulator ground truth (e.g. the true start of a bot farm).
```jsonc
{ "events": [
  { "t": 342, "time_label": "Mon 06:00", "kind": "planned",   "country": "DE", "label": "Planned: Weekend Boost (Casual games, Strategy games)" },
  { "t": 348, "time_label": "Mon 12:00", "kind": "anomaly",   "country": "CA", "label": "First unusual hour: new accounts on Android left the normal zone" },
  { "t": 350, "time_label": "Mon 14:00", "kind": "detected",  "country": "CA", "label": "Detected: Fake accounts on Android in Canada" },
  { "t": 352, "time_label": "Mon 16:00", "kind": "approved",  "country": "CA", "label": "Approved: Verify new accounts" },
  { "t": 376, "time_label": "Tue 16:00", "kind": "verified",  "country": "CA", "label": "Prediction verified" }
] }
```
`kind`: `planned` | `anomaly` (= the incident's `left_zone_at`) | `detected` | `waiting` (data arrived incomplete) |
`approved` | `verified` (also when the prediction missed) | `explained`. Same-hour events are ordered as listed.

### `GET /api/meta`
Public data behind the simulation, for a "Data" panel.
```jsonc
{ "data_sources": [ { "label": "Annual revenue run rate", "value": "$1B",
                      "source": "Almedia release via PPC Land, 15 Oct 2026", "url": "https://ppc.land/..." } ],
  "calibration_note": "Calibrated to public data: ...",
  "calibration": { "revenue_usd_day": { "sim": 2740947, "published": 2739726 },
                   "rewards_share": [ { "iso": "US", "name": "United States", "sim": 0.5788, "published": 0.579 } ] } }
```
`published` is `null` for countries without a published payout share.

## Write endpoints

| Method & path | Body | Effect / response |
|---|---|---|
| `POST /api/step` | `{ "hours": 1 }` | Advance world and agent; returns `/api/state` |
| `POST /api/reset` | `{}` | New world (14-day history, twin learned); returns `/api/state` |
| `POST /api/inject` | `{ "type": "promo" \| "bot_farm" \| "tracking_break" \| "data_delay", "country": "CA", "platform": "android" }` (`platform` optional) | Schedules the event at the current hour; returns `{ ok, event }` |
| `POST /api/incidents/{id}/approve` | `{ "fix_id": "block_cluster" }` | Applies fix to reality; returns the incident detail |
| `POST /api/metrics` | `{ "name": "margin_per_signup = margin / signups" }` (see below) | Automatic extension: `{ status, message, node, validation, placement?, monitoring?, note?, ambiguity? }` |
| `POST /api/what_if/launch` | `{ "country": "AR", "users": 250000 }` (both optional; default `AR`, users assumed) | Read-only forecast for entering a new country (see below) |
| `POST /api/demo/{scene}` | — (optional query `?frames=1`) | Deterministic (fixed seed) jump to a video scene; returns `{ scene, state, incident_id? }`, plus `frames` with `?frames=1` (see below) |

Demo scenes, in order: `start`, `promo`, `bot_farm`, `investigate`, `approve`, `verify`, `new_metric`, `tracking_break`. Each scene advances the session world from wherever the previous scene left it (calling `start` resets).
Story clock: `start` Mon 01:00 · promo from 01:00, explained by 03:00 · bot farm from 03:00, leaves the normal zone
with the 04:00 data, detected at 06:00 · approve at 08:00 · verified Tue 08:00 · tracking break detected Tue 13:00.

Time-lapse frames for "Play the demo" (`POST /api/demo/{scene}?frames=1`):
- **Order:** oldest first. The first frame is the hour the scene starts from, then there is one frame per simulated
  hour it advanced, and the last frame is the returned `state`.
- **Earlier scenes:** if the call had to run earlier scenes first, their hours are included.
- **`start`:** it's a copy, so it gives one frame.
- **Without `?frames`** the response is unchanged.
```jsonc
"frames": [
  { "t": 338, "clock": { ...same as state.clock },
    "countries": [ { "iso": "CA", "status": "ok", "health_z": 1.2, "attention": 0.0, "attention_usd": 0 }, ... ],  // all 20
    "incidents": [ { "id": "inc-1", "country": "CA", "status": "open", "status_label": "Open", "severity": "high",
                     "short_title": "Fake accounts on Android in Canada" } ] }, ... ]
```
Canada in the frames:
- **`bot_farm`:** 4 frames, Mon 03:00 → 06:00, with Canada `ok`, `ok`, `watch` (attention 0.08), `incident` (0.92).
- **`verify`:** ~23 frames covering the day after approval.

Errors: `404 {detail}` for unknown incident / country / scene, `400 {detail}` for invalid input.

## Automatic extension: `POST /api/metrics`

Request (any of these forms):
```jsonc
{ "name": "margin_per_signup = margin / signups" }                       // name and formula in one line
{ "name": "margin_per_signup", "formula": "margin / signups" }           // separate fields
{ "name": "Start rate", "description": "how often users start an offer" } // words only: LLM or keyword rules propose the formula
```
Formulas use `+ - * / ( )`, numbers, metric ids (`new_accounts`, `active_users`, `impressions`, `starts`, `completions`,
`revenue`, `rewards`, `quality`, `margin`, `quality_per_completion`) or business words that map to exactly one metric
(`signups` → new_accounts, `profit` → margin, `views` → impressions, `payouts` → rewards, `dau` → active_users, …).

Response, always HTTP 200; branch on `status`:

| `status` | Meaning | UI |
|---|---|---|
| `added` | Placed, validated, monitored | show `note`, highlight the new node (`node.parents` are its formula edges) |
| `ambiguous` | A word maps to several metrics | show `message`, offer `ambiguity.suggested_formulas` as buttons that re-submit |
| `invalid` | Unknown metric (with "did you mean"), bad syntax, units that don't match, or undefined on the data | show `message` |
| `duplicate` | Same formula, or the same values on the data as an existing metric | show `message`, point at `node` |

```jsonc
// added
{ "status": "added", "message": "Added 'margin_per_signup' and started monitoring it.",
  "node": { "id": "margin_per_signup", "label": "margin_per_signup", "unit": "USD per new account", "kind": "ratio",
            "level": "L2", "parents": ["margin", "new_accounts"], "edge": "formula", "expr": "margin / new_accounts",
            "aggregation": "ratio", "origin": "extension", "confidence": 1.0, "note": "Added because ..." },
  "placement": { "parents": ["margin", "new_accounts"], "edge_type": "formula", "level": "L2",
                 "breakdown": ["global", "country", "platform"] },                  // dimensions shared by all inputs
  "validation": { "ok": true, "checks": [        // always in this order; the first failing check ends the flow
    { "name": "formula",       "ok": true, "detail": "margin_per_signup = margin / new_accounts (from your formula); read 'signups' as new_accounts" },
    { "name": "names",         "ok": true, "detail": "All inputs are known metrics: margin, new_accounts" },
    { "name": "dimensions",    "ok": true, "detail": "Units are consistent: result in USD per new account" },
    { "name": "duplicate",     "ok": true, "detail": "New metric: no existing metric has this formula or the same values on the data" },
    { "name": "holds_on_data", "ok": true, "detail": "Computed on 336 h of observed data at country × platform level: 100% of segment-hours defined; Canada last 24 h = 13.12 USD per new account" },
    { "name": "aggregation",   "ok": true, "detail": "Ratio: not additive. Country and global values are Σnumerator / Σdenominator (6.087), not the average of segments (6.159)" },
    { "name": "monitoring",    "ok": true, "detail": "Watched in 20 countries against its seasonal baseline (backtest error 5.8% per 3 h)" } ] },
  "monitoring": { "watched": true, "countries": 20, "baseline_error_pct": 5.8, "now": { "country": "US", "z": 1.3 },
                  "watched_series": 140 },
  "note": "Added because you asked for 'margin_per_signup'. Argus read it as margin / new_accounts ('signups' = new_accounts), placed it under contribution margin and new accounts as a formula edge, checked the units (USD per new account) and that it computes on 14 days of data, and now watches it in 20 countries as a ratio of sums." }

// ambiguous
{ "status": "ambiguous", "node": null,
  "message": "'users' is ambiguous: it could mean active users or new accounts. Pick one and add the metric again.",
  "ambiguity": { "term": "users", "candidates": ["active_users", "new_accounts"],
                 "suggested_formulas": ["margin / active_users", "margin / new_accounts"] },
  "validation": { "ok": false, "checks": [ ... ] } }

// invalid (examples of message)
"The units don't match: cannot add or subtract USD/h and completion per hour."
"Unknown metric 'revnue'. Did you mean revenue? Known metrics: active_users, completions, ..."

// duplicate
{ "status": "duplicate", "message": "On the data this is identical to the existing metric 'rewards': not added twice.",
  "node": { "id": "rewards", ... } }
```
After `added`, the metric appears in `GET /api/graph` (node + formula edges), `GET /api/graph/tree` (as a root),
`GET /api/state` → `added_metrics`, and `attention.watched_series` grows by 20.
The `new_metric` demo scene posts `margin_per_signup = margin / signups` and returns this response as `metric`.

## Enter a new country: `POST /api/what_if/launch`

Read-only: nothing in the session world changes.
- **Request:** `{ "country": "AR", "users": 250000 }`.
  - `country`: the only candidate is `AR` (Argentina). A watched country or an unknown code returns `400 {detail}`.
  - `users` (optional): expected daily users after 4 weeks, between 1,000 and 50,000,000. If omitted, it is
    **assumed** from the similar countries (their daily users per capita × Argentina's population).
- **Timing:** the first call takes ~1.5 s; after that ~5 ms, cached per seed.
- **What depends on `users`:** only the totals (`users`, `margin_per_day_usd`, weekly, 4-week, `total_line`) and
  `fraud_risk`. Everything per user is independent of it.

**What the UI shows:**
- **Headline** is per user: `headline`, then `range_line`.
- **Total** sits under it, marked as an assumption: `total_line` (says "(assumed)", or "(planned)" when `users` was given).
- **Credibility:** `learned_line` and `backtest_line`.
- **Globe pin:** `country.lat/lng`.

```jsonc
{
  "country": { "iso": "AR", "name": "Argentina", "region": "Latin America", "lat": -38.4, "lng": -63.6,
               "ios_share": 0.1422, "gdp_per_capita_usd": 13970, "population": 45696159 },
  "horizon_days": 28,
  "margin_per_1000_users_day_usd": { "p10": 17.72, "p50": 24.74, "p90": 36.05 },   // what Argus predicts
  "users": { "daily_users": 176000, "assumed": true,
             "source": "assumed: daily users per capita of Mexico, Brazil, Türkiye × population of Argentina (45,696,159)",
             "note": "An assumption, not a prediction: ..." },                       // source "input" when given
  "margin_per_day_usd": { "p10": 3062, "p50": 4275, "p90": 6229 },        // day 28 at that user count (98% adopted)
  "margin_per_day_weekly_usd": [ { "p10": 1285, "p50": 1794, "p90": 2614 }, ... ],   // 4 entries, users build up
  "margin_4w_total_usd": { "p10": 67406, "p50": 94098, "p90": 137101 },
  "best_offer_mix": [ { "category": "Casual games", "margin_share": 0.242, "taste": 0.189 }, ... ],   // top 3
  "best_reward": { "reward_share": 0.65, "current_share": 0.55, "uplift_per_1000_users_day_usd": 1.33,
                   "grid": [ { "reward_share": 0.45, "margin_per_1000_users_day_usd": 20.38 }, ... ],  // 0.45 … 0.65
                   "elasticity": 2.13, "note": "..." },
  "fraud_risk": { "level": "low" | "medium" | "high", "farm_chance_4w": 0.45, "expected_loss_usd_4w": 3817,
                  "farms_per_quarter_neighbours": { "MX": 1.8, "BR": 2.4, "TR": 1.6 }, "text": "...", "source": "..." },
  "learned_from": [ { "iso": "MX", "name": "Mexico", "weight": 0.66, "distance": 0.78 }, ... ],    // 3 entries
  "assumptions": { "payout_level_vs_us": 0.103, "active_hours_per_user": 1.71,
                   "adoption": "users build up as 1 - exp(-day/7)", "taste_start_elasticity": 0.141, "paths": 400 },
  "backtest": {
    "n": 20,
    "offers": { "true_top_in_top3": 20, "true_top_is_first": 15,
                "naive_baseline": { "true_top_in_top3": 18, "true_top_is_first": 11, "rule": "..." },
                "text": "best offers right in 20 of 20" },
    "margin_per_user": { "median_abs_error": 0.242, "max_abs_error": 0.638, "inside_p10_p90": 17,
                         "text": "margin per user typically within ±24%, 17 of 20 inside the range" },
    "reward": { "within_one_step": 19, "exact": 10, "step": 0.05,
                "text": "best reward level within one step in 19 of 20" },
    "countries": [ {                                                       // 20 rows, one per watched country
      "country": "US", "name": "United States", "learned_from": ["CA", "GB", "AU"],
      "offers": { "predicted_top3": [...], "true_top": "Fintech apps", "hit": true, "top1_hit": true,
                  "naive_top3": [...], "naive_hit": true, "naive_top1_hit": true },
      "per_1000_users": { "predicted": 416.5, "range": [290.1, 590.98], "true": 357.45, "error": 0.165,
                          "inside_p10_p90": true },
      "reward": { "predicted": 0.55, "true_best": 0.5, "steps_off": 1, "within_one_step": true } }, ... ],
    "method": "Each watched country is hidden in turn and predicted from the other 19 only, ..."
  },
  "headline": "Launching in Argentina: about $25 margin per 1,000 users a day",
  "range_line": "Likely $18–$36 per 1,000 users a day (p10–p90)",
  "total_line": "At 176,000 daily users (assumed): about +$4,275 a day after 4 weeks",
  "learned_line": "Learned from Mexico, Brazil and Türkiye",
  "backtest_line": "Tested on 20 countries we know: picked the #1 offer in 15 of 20 (the global average: 11 of 20)"
}
```

How the backtest works:
- **Ground truth:** the simulator's true next 4 weeks for each watched country.
- **Best reward truth:** the simulator is rerun at each reward share. The reward response is hidden in the simulator
  and only switched on by a `set_reward` change. Argus sees only noisy results of past reward tests.
- **Which line the UI gets:** `backtest_line` quotes how often Argus picked the #1 offer. The global-average baseline is
  added in brackets only when it is more than 2 below.
- **Why not the top-3 count:** the global average already gets 18 of 20 in the top 3, so "20 of 20 in the top 3" is
  in the README table and in `backtest.offers`, not on screen.
