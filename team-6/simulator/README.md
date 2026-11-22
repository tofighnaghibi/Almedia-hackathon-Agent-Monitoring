# L1 Simulator: baseline generator

Generates normal marketplace data (no faults, no events) in the
`marketplace_hourly` format from `contracts/contracts.md`.

```bash
pip install -r requirements.txt
python -m simulator                       # clean baseline -> data/generated/baseline
python -m simulator --scenarios all       # + faults and innocent changes -> data/generated/scenarios
python -m simulator --scenarios faults --days 35 --anchor 2026-10-29T10:00:00Z --out data/generated/demo
python -m pytest
```

`--scenarios` takes `none`, `faults`, `innocent` or `all`. By default the
suites start at 10:00 UTC on the first day of the last week, so there is at
least a week of normal history before them. Stock-outs are produced through
late supplier deliveries (`InventoryHooks`), so `purchase_orders.csv` and
`inventory_daily.csv` hold the evidence. A stock-out's ground-truth window is
set from the data: it runs from the first to the last day on which the
out-of-stock view share is more than 2 percentage points above the same day
without the fault. That includes the days the shelves stay empty after the
supplier delays stop. `ground_truth.json` is written next
to the data. Only tests and L7 scoring may read it; L4 to L6 must not.

From Python (for example the L7 demo script):

```python
from simulator import SimConfig
from simulator.pipeline import build_dataset

result, truth = build_dataset(SimConfig(days=35), scenarios="all", out_dir="data/generated/demo")
```

28 days take about 5 seconds and give 32,256 rows
(4 countries × 3 platforms × 4 categories × 672 hours). Per day that is about
1.18M sessions, 40k orders and €3.3M revenue, with 3.4% conversion.

## What is in the data

- **Funnel per row:** sessions → searches → search clicks; recommendation
  impressions → clicks; ad impressions → clicks → spend. Product views
  (search + recommendation + ad clicks + browsing) → out-of-stock views → cart adds
  → checkouts → payment attempts → orders → refunds, and revenue. Each step is
  sampled from the one before it, so the funnel never inverts.
- **Seasonality:** a daily curve in *local* time (mobile peaks in the evening,
  web during working hours), a weekday pattern with a weekend lift for fashion
  and home, and +0.1% growth per day. Fixed UTC offsets, no daylight saving.
- **Noise:** a day-level shock per country, a day-level shock per segment, an
  hour-level shock, a small daily wobble on every rate, and Poisson or binomial
  sampling noise.
- **Inventory:** 150 products per category with Zipf popularity, 40
  third-party sellers plus the marketplace itself buying wholesale (`S000`,
  "1p"). Daily stock follows a reorder-point policy, with purchase orders
  arriving after a lead time plus jitter. `active_listings` and
  `out_of_stock_views` come from this stock. Baseline stock-outs are rare
  (about 1% of views) and sit mostly on tail products.

## How the scenario injector plugs in (for L1 B)

Generation happens in two stages, so a fault changes a *cause* and its
downstream effects follow:

```python
from simulator import SimConfig, build_world, realize, InventoryHooks

world = build_world(SimConfig(days=35))        # stage 1: drivers + inventory
d = world.drivers.copy()                       # one row per hour × segment
hit = (d.country == "DE") & (d.platform == "android") & (d.timestamp >= "2026-10-15T10:00Z")
d.loc[hit, "payment_success_rate"] *= 0.35     # break payments
faulty = realize(world, d)                     # stage 2: sample counts
```

- Driver columns: `exp_sessions`, the per-session rates (`searches_per_session`,
  `rec_impressions_per_session`, `ad_impressions_per_session`,
  `browse_views_per_session`), the probabilities (`search_ctr`, `rec_ctr`,
  `ad_ctr`, `oos_view_share`, `add_to_cart_rate`, `checkout_rate`,
  `payment_attempt_rate`, `payment_success_rate`, `refund_rate`), and `aov_eur`,
  `cpc_eur`, `active_listings`.
- **Rows you don't touch come out identical** to the baseline run, because
  sampling uses inverse CDFs over pre-drawn random numbers. `baseline - faulty`
  is exactly the injected effect, which makes the ground truth easy to check.
  Keep the rows and their order; change values only.
- **Supply-chain faults:** pass `InventoryHooks` to `build_world`.
  `delivery_delay(product, order_date) -> extra days` models a late supplier;
  `order_qty_factor(product, order_date) -> multiplier` models the purchasing
  system ordering too little. These cover both explanations in the wholesale
  scenario.
- **Late or missing data:** drop rows from the realized table, or hold them
  back, after `realize`.

## Output files

| File | In contract? | Content |
|---|---|---|
| `marketplace_hourly.csv` | yes | the fact table, timestamps `YYYY-MM-DDTHH:00:00Z` |
| `events.csv` | yes | empty in the baseline (header only); the injector fills it |
| `products.csv`, `sellers.csv` | no | catalog |
| `inventory_daily.csv` | no | stock, demand, sales and lost units per product per day |
| `purchase_orders.csv` | no | wholesale and seller restock orders, with expected and actual arrival |
| `sim_config.json` | no | the exact config used |

The last four are proposed additions to the contract. The wholesale scenario
needs them, so they should be agreed before L2 relies on them.

## Notes for the contract discussion

- `active_listings` is a stock level, not a flow. It repeats on every platform
  row for the same (hour, country, category), so it **must not be summed** across
  platforms or countries. The breakdown checker in L2 should treat it as
  non-additive.
- The contract mentions `ingested_at` for late data, but the table has no such
  column yet.
- Advertiser-level pacing (one advertiser's budget spent by 10 a.m.) and the
  contribution margin (fees, platform-funded discounts) aren't covered by the
  current columns.
