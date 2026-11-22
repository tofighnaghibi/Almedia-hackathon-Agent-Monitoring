# L2 Metric graph

## Running it

```bash
pip install -r requirements.txt
python -m simulator                 # writes clean data to data/generated/baseline
python -m graph                     # checks every formula and breakdown edge, prints the tree
python -m graph --data data/generated/scenarios --save data/generated/graph_checked.yaml
python -m pytest
```

`python -m graph` loads [metrics.yaml](metrics.yaml) and
[seed_graph.yaml](seed_graph.yaml), validates every metric on the data, checks
every formula and breakdown edge with the real engine, and prints the results
and the metric tree. It exits with code 0 only if every edge passes. `--save`
writes the graph with the updated edge statuses.

How the two halves fit:

- **Person A:** `registry.py`, `engine.py`, `metrics.yaml`
- **Person B:** `edges.py`, `metric_graph.py`, `formulas.py`, `checkers.py`, `tree.py`, `seed_graph.yaml`
- **Bridge:** `bridge.py` adapts the engine to the checkers' `compute(metric, start, end, group_by)`
  signature. It also derives the `weight_metric` for a ratio breakdown from the
  calculation's denominator (for example `payment_success_rate` gets `payment_attempts`).

## Registry + calculation engine

The registry holds metric definitions in the contract format
(`contracts/contracts.md` section 2; seed list in [metrics.yaml](metrics.yaml)).
The engine computes any metric for any time window, segment, breakdown and
time grain, using DuckDB over the simulator tables.

```python
from graph import MetricEngine

engine = MetricEngine.from_dir("data/generated/scenarios")   # loads metrics.yaml by default

engine.value("orders", "2026-10-22T10:00Z", "2026-10-23T10:00Z", {"country": "DE", "platform": "android"})
engine.compute("payment_success_rate", start, end, segment={"country": "DE"}, by=["platform"])
engine.compute("sessions", start, end, by=["country"], grain="hour")       # time series per country
engine.compute_many(["orders", "payment_attempts", "payment_success_rate"], start, end, grain="day")
```

`compute` returns a DataFrame `[*by, period (if grain), value, n_rows]`.
`compute_many` returns one column per metric instead of `value`.

## Rules

- **Time windows are half-open**, `start <= timestamp < end`, in UTC. Strings
  without a timezone are read as UTC.
- **Ratios are ratio-of-sums** for the whole group (`SUM(orders) / SUM(payment_attempts)`),
  never an average of hourly ratios, so a ratio is right for any slice.
- **Segments** filter on equality, or on a list (`{"country": ["DE", "FR"]}`).
  Only a metric's own `dimensions` are allowed. `avg_active_listings` can't
  be split by platform because it's a stock level, not a flow.
- **Missing data stays visible.** With a `grain`, every period in the window
  appears for every group seen in the table. A period with no rows has
  `value = NaN` and `n_rows = 0`. The `row_count` metric (`COUNT(*)`) is there
  for completeness checks.
- **Grains** are `hour`, `day` and `week` (weeks start on Monday).

## Adding a metric (what L3 uses)

```python
engine.registry.add(
    {"name": "android_order_share", "description": "Share of orders placed on Android",
     "unit": "ratio",
     "calculation": "CAST(SUM(orders) FILTER (WHERE platform = 'android') AS DOUBLE) / NULLIF(SUM(orders), 0)",
     "dimensions": ["country", "category"]},
    engine=engine,                      # also validates against the data
)
```

There are two levels of checks:

1. **Structure** (`MetricDefinition`, no data needed): snake_case name; unit in
   `count / ratio / currency_eur / rate`; weight between 0 and 1; no unknown
   fields. The calculation must be one aggregate expression: no `SELECT`, `FROM`,
   `;` or comments.
2. **Data** (`engine.validate`, returns a `ValidationReport`): the table and
   dimensions exist; the calculation references real columns, runs as an
   aggregate, and can be broken down by all of its dimensions.
   - Warnings for a `ratio` outside 0..1, negative counts or money, and
     constant values.
   - `columns_used` lists the table columns the calculation reads. It is a
     starting point for L3's connection finding.

## For the relationship checkers (L2 B)

- **Formula check** (`orders = payment_attempts * payment_success_rate`):
  `compute_many([...], by=..., grain="hour")` returns every side of the formula
  aligned row by row.
- **Breakdown check:** `compute(m, by=[dim])["value"].sum()` against
  `value(m)`. Only metrics with `MetricDefinition.is_additive` (a plain
  `SUM(...)` or `COUNT(*)`) should add up. Ratios and `avg_active_listings`
  don't.
