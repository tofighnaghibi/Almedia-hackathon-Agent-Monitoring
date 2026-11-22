# L3 Extending the graph: candidate connections (Person A)

A new metric arrives with only a definition (name, description, unit,
calculation, dimensions). `discover_connections` proposes where it belongs in
the graph. The contributor doesn't say.

```bash
python -m simulator --scenarios all                               # data, if not there yet
python -m extension --metric extension/examples/new_metrics.yaml  # print candidates
python -m extension --metric my_metric.yaml --save data/generated/candidates.yaml
```

```python
from extension import discover_connections
report = discover_connections(new_definition_dict, engine, graph)
report.best_formula()           # top Candidate or None
report.edges()                  # GraphEdge list: status "uncertain", source "auto_discovered"
```

## The full step: discover, validate, add

`add_new_metric` ([pipeline.py](pipeline.py)) joins discovery (Person A) to
Person B's validation and extension (`graph/extend.py`). It runs these steps:

1. check the definition on the data
2. discover candidates
3. register the metric
4. make every registered metric known to the graph
5. convert the candidates to `graph.extend.Candidate`
6. run `extend_graph` with the real engine, passing `is_additive` and
   `weight_metric` so breakdowns of rate metrics get checked

If anything fails, the registry is rolled back.

```bash
python -m extension --metric extension/examples/new_metrics.yaml --apply \
    --save-graph data/generated/graph_extended.yaml --save-metrics data/generated/metrics_extended.yaml
python -m graph --data data/generated/scenarios \
    --graph data/generated/graph_extended.yaml --metrics data/generated/metrics_extended.yaml
```

With the four example metrics, the extended graph passes all 44 formula and
breakdown checks (26 seed + 18 new).

## How it finds connections

| Step | What it does | Output |
|---|---|---|
| Shared columns | Ranks existing metrics by overlap of the table columns they read (Jaccard), among those sharing a dimension. | `related_metrics` |
| Formula search | Tries `N = A*B`, `N = A/B`, `N = A+B(+C)`, `N = A-B` and `N = 1-A` against every existing metric on all hours of data (max relative error 1e-6, ≥80% of hours comparable). Every fit is re-checked per segment and day, and a formula that breaks on any segment is dropped. | `formula` candidates |
| Lagged correlation | Hourly series per segment, turned into residuals (log value minus its mean for the same segment, weekday and hour). Pearson correlation at lags −6..+6 h. | `possible_cause` candidates |
| Breakdowns | One per declared dimension, noting whether segments should add up. | `breakdown` candidates |

Rules that keep the candidates useful:

- **The definition comes first.** Formulas are ranked by how well their inputs
  cover the metric's own columns, then by simplicity. So
  `product_clicks = search_clicks + recommendation_clicks + ad_clicks` beats the
  circular `product_clicks = cart_adds / add_to_cart_rate`. Other formulas that
  fit are kept and marked `alternative_to`.
- **The graph's convention is kept.** A count ÷ count ratio is written as a
  driver of the count, as in the seed graph: `refunds = orders * refund_rate`.
- **No fake causes.** Metrics already connected by formulas to the metric or
  its formula inputs are left out of the correlation search, because arithmetic
  already explains why they move together.
- **Careful with direction.** A lead is reported only if it beats lag 0 by
  0.05 in |r| and isn't at the edge of the search window. Otherwise the
  direction is `ambiguous`. Correlation scores are capped at 0.85: they are
  hypotheses.
- **Duplicates** (same calculation as an existing metric) are reported, not
  linked.

## Results on the simulator data

| New metric | Best candidate found |
|---|---|
| `refund_rate` (removed, then rediscovered) | `refunds = orders * refund_rate` |
| `conversion_rate` | `orders = sessions * conversion_rate` |
| `orders_per_search` | `orders = searches * orders_per_search` |
| `cart_abandonment_rate` | `cart_abandonment_rate = 1 - checkout_rate` |
| `ad_click_share` | `ad_clicks = product_clicks * ad_click_share` |
| `organic_clicks` | `organic_clicks = search_clicks + recommendation_clicks` |
| `out_of_stock_rate` | possible cause `avg_active_listings` (r ≈ −0.6: fewer listings, more out-of-stock views) |

## For Person B (validation and adding)

- Candidate edges are Person B's `GraphEdge` objects. `MetricGraph.add_edge`
  accepts all of them, and `--save` writes them in the graph YAML format, so
  `MetricGraph.load` reads the file directly.
- `Candidate.score` and `Candidate.details` carry the evidence:
  `segment_check`, `column_coverage` and `alternative_to` for formulas;
  `r`, `r_lag0`, `lag` and `direction` for correlations.
- `details["not_in_graph"]` lists metrics a candidate needs that aren't nodes
  of the graph yet.
- The new metric isn't registered by discovery. Registering it
  (`registry.add(defn, engine=engine)`) is part of the adding step.
