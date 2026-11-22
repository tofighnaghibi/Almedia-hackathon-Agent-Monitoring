# L4 Expected behaviour and change detection (Person A)

```bash
python -m simulator --scenarios all
python -m monitoring.detect                                   # all metrics, last 7 days
python -m monitoring.detect --metrics orders,payment_success_rate --by country,platform --start 2026-10-22
```

```python
from monitoring.detect import Detector
det = Detector(engine)
scores = det.score("payment_success_rate", by=["country", "platform"], start="2026-10-22")  # every hour
episodes = det.detect(["orders", "search_ctr"], by=["country"], start="2026-10-22")         # anomalies
episodes[0].to_finding()  # contract Finding fields: metric, segment, time_window, expected, actual, change_size
```

## Expected behaviour ([baseline.py](baseline.py))

For each metric, segment and window length (1h, 6h, 24h by default):

| | How |
|---|---|
| **Expected** | Median of the same window 1–4 weeks earlier, so time of day and day of week both count. Each week is scaled by the segment's week-over-week growth, measured on training data. |
| **Error (sigma)** | Backtest: run the same predictor on the training weeks and take the robust spread (1.4826 × MAD) of its errors, per segment and window. It's never below the noise implied by the counts themselves (Poisson for counts, binomial for rates), so small segments don't look precise, with a 1% floor on the log scale. |
| **Range** | expected ± z × sigma (default z = 5). |
| **Scale** | Log (relative change) for most metrics. Absolute differences for rare-event rates whose typical level is below 5% (out-of-stock rate, ad CTR, conversion), because on a log scale their normal wobble looks as big as a real jump. |
| **Windows** | Built from hourly values: counts summed, rates recombined weighted by their denominator (exact ratio-of-sums), other metrics averaged. |

Only data before the monitoring start is used to measure growth and error, so
a fault can't widen its own range.

## When a change is reported ([detect.py](detect.py))

A cell is an anomaly when **all** of these hold:

- |z| ≥ 5 and |relative change| ≥ 5%
- in 1h windows it lasts 2 hours, unless |z| ≥ 10 (`suppressed = "single hour"`)
- the data is complete: rows ≥ 98% of normal. Otherwise it's
  `suppressed = "incomplete data"`, not a business change. `row_count`
  (`COUNT(*)`) is exempt, because missing rows are what it measures.
- at least 2 previous weeks exist to compare with

Consecutive anomalous hours become an `Episode`. Episodes for the same metric,
segment and direction that overlap across window lengths are merged: start =
first flag, end and `ongoing` from the shortest window (a 24h window keeps
seeing a change for a day after it stops), score = strongest, `windows` =
which lengths saw it. `impact` is `bad` / `good` / `neutral` from the metric's
`higher_is_better`.

## Results (28 days, monitoring the last 7)

**Clean data:** scores are calibrated, with a spread of 0.8–1.3 across
metrics, segments and windows (close to the ideal 1). For the 7 core metrics ×
17 segments × 3 windows × 168 hours, at most a handful of episodes are
flagged. The remaining flags on all 33 metrics are mostly genuine natural
stock-outs of top products, which the simulator produces on purpose.

**Scenario data:** every injected scenario is caught in its own segment:

| Scenario | Metric | Ground truth | Flagged |
|---|---|---|---|
| payment failure DE/android | payment_success_rate | 10-22 10:00 → 10-23 10:00 | 10-22 10:00 → 10-23 11:00, z = −71 |
| bad search release DE | search_ctr | 10-23 10:00 → 10-24 04:00 | 10-23 10:00 → 10-24 05:00, z = −65 |
| ad budget exhausted FR | ad_spend | 10-25 10:00 → 10-26 09:00 | 10-25 10:00 → 10-26 10:00, z = −119 |
| stock-out electronics | out_of_stock_rate | 10-24 → still going at the end of data | 10-23 → still going, z = +34 (a natural stock-out on 10-23 runs straight into the fault) |
| planned promo UK (innocent) | sessions | 10-22 10:00 → 10-24 09:00 | 10-22 10:00 → 10-24 10:00, z = +15, impact good |
| holiday US (innocent) | sessions | 10-24 10:00 → 10-25 09:00 | 10-24 10:00 → 10-25 00:00, z = −8 |
| late data DE/ios (innocent) | row_count | 10-26 22:00 → 10-27 01:00 | 10-26 22:00 → 10-27 02:00; sessions etc. held back as incomplete |

The ground truth's `end_time` is the start of the last hour, so a flagged end
one hour later is a match.

## Triage: what gets reported (with Person B's modules)

`monitoring.triage.triage(episode, calendar, dq_checker, volume_metric=...)`
decides for each episode:

1. **Data quality first.** Missing or stale data gives `insufficient_data`,
   with any matching `data_delay` event attached.
2. **Planned events** (campaign, holiday) that cover the whole segment. A
   stated effect ("+25% sessions") moves the expected value for volume
   metrics, and the range is widened ×1.5 for uncertainty. If the actual
   value is inside that range, the result is `suppress_planned`. A UK promo
   covers `UK` and `UK/android`, but not the all-country total: there it's
   only context.
3. **Otherwise `report`**, with unplanned events (releases up to 6 hours
   before the anomaly) attached as candidate causes.

The joint check on the simulator data
([tests/test_l4_joint.py](../tests/test_l4_joint.py)):

- All 4 faults are reported, and none of their symptoms is suppressed.
- The payment failure points at the Android release `evt_001`.
- The UK promo and the US holiday are `suppress_planned`.
- The late data is `insufficient_data`.

## For Person B (what to watch, data quality, planned events)

- `Detector.score(...)` returns every hour, with `actual`, `expected`,
  `lower`/`upper`, `z`, `sigma`, `coverage`, `is_anomaly`, `suppressed`.
  `coverage` is ready for the freshness and completeness checks.
- Innocent changes are detected on purpose (promo up, holiday down). The
  planned-events calendar should explain them, not detection.
- `Episode.key` (`metric|segment|direction`) is a natural starting point for
  the L6 fingerprint.
- The folder's `__init__.py` has no imports, so modules can be added without
  touching it. `python -m monitoring` is free for the monitoring loop.
