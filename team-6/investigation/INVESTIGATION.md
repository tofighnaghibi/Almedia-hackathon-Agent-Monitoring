# L5 Following the graph and quantifying (Person A)

```bash
python -m simulator --scenarios all
python -m investigation.investigate --metric orders --segment country=DE      # strongest detected anomaly
python -m investigation.investigate --metric orders --segment country=DE \
    --start 2026-10-22T10:00Z --end 2026-10-23T10:00Z [--json]
```

```python
from investigation.investigate import investigate, investigate_episode
res = investigate(engine, graph, "orders", start, end, segment={"country": "DE"})
res.summary            # one line
res.trail              # contract trail: step, action, query, observation
res.evidence           # contract evidence_list entries (metric_drop / metric_rise)
res.business_impact    # lost_orders, lost_revenue_eur, changes in segment and localized segment
res.to_dict()          # everything, JSON-serializable
```

## Steps

1. **Compare** ([compare.py](compare.py)). The window against the *pooled*
   same window 1–4 weeks earlier: counts averaged, ratios recomputed from the
   pooled numerators and denominators. Because every metric is a ratio of sums,
   the graph's formulas hold exactly on the baseline too, so contributions add
   up to the change. Monitoring uses a median with growth instead: that
   decides *whether* something changed, while investigation explains *how*.
2. **Follow the graph** ([decompose.py](decompose.py)). For a formula
   `y = f(a, b, …)`, the change is split into **Shapley values**: each input's
   average effect when switched from baseline to actual. They add up exactly,
   for products, ratios and sums. Every input carrying at least 10% is
   expanded recursively, and shares multiply down, so each node shows its part
   of the original change.
   - A formula is checked on the data (actual and baseline) before use.
   - If several formulas define a metric, the one that concentrates the
     change most is used, and the others are listed as `alternatives`.
   - **Drivers** are the leaves carrying at least 20% of the change.
3. **Localize** ([drilldown.py](drilldown.py)). Each driver's change is split
   by every free dimension.
   - Counts: each value's own change.
   - Ratios: an exact **rate effect + mix effect** per value. Did its rate
     change, or its share of the denominator?
   - It follows the dimension where one value carries the most, while that's
     at least 50% and moves the same way as the total. It stops with a reason
     when the change is spread out or no dimension is left.
4. **Quantify impact**: orders and revenue against the baseline, in the
   anomaly's segment and in the localized segment.

## Results on the simulator data (the injected faults)

| Anomaly | Formula path | Driver | Localized to |
|---|---|---|---|
| orders DE −25% | orders = attempts × payment_success_rate | payment_success_rate −65% in the segment (97% of the change); attempts normal | **DE / android** (99%; rate effect, no mix effect) |
| orders DE −55% | orders → attempts → checkouts → cart_adds → product_clicks → search_clicks = searches × search_ctr | **search_ctr −50%** (56%); searches −5% (4%) | **DE** (spread evenly over platforms and categories) |
| ad_spend FR −63% | ad_spend = ad_clicks × CPC; ad_clicks = impressions × CTR | **ad_impressions** (100%); CPC, CTR normal | **FR**; French orders didn't fall |
| out_of_stock_rate | (leaf) | itself | **electronics** (87%, spread over countries) |
| sessions (UK promo) | (leaf) | itself | **UK** (93%) |

The search case shows the brief's "customers keep searching, but fewer buy".
It also shows `add_to_cart_rate` carrying 37%. That comes from the scenario
injector, which scales cart adds by 50% although only search clicks (part of
product clicks) were affected. The decomposition reports what's in the data.

## For Person B (hypotheses and the agent loop)

- Use the detected episode's exact window. Scenarios can sit back to back,
  and one extra hour can pull in the next fault.
- `ContributionNode` (the tree) and `DrillResult.steps` (each step's split
  tables, with `share`, `rate_effect`, `mix_effect`, `others_rel_change`) are
  the quantities to rank hypotheses with. A driver with a large share in a
  localized segment is the "first cause" candidate; its ancestors in the tree
  are symptoms.
- `decompose`, `drill_down` and `split` can be called step by step, so the
  agent loop can choose its next query itself instead of using
  `investigate()`'s fixed order.
