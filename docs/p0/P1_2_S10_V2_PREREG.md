# S10 v2: preregistered rerun of the session layer's value check

Written and committed **before** the run (22 Nov 2026). You accepted the reader recommendation from `P1_2_SESSION_LAYER.md` §5.

## What changes (reader only; the session layer and the scenarios are unchanged)
Reader = `mkt/standin_core.py` (Contracts-only), via the contract port.

1. **Counting-noise floor** for every ratio (already in the core): scale ≥ r·√(1/num + 1/den).
2. **Local baseline** instead of a single median over the whole window. For week *t*, the baseline is the median of weeks t−3 … t+3, excluding *t* itself (at least 3 neighbours, otherwise the window median). The scale is 1.4826 × MAD of all weeks' residuals from their local baselines. For **count** series it is floored by counting noise √(baseline). This follows trend and slow seasonality.
3. **No holiday calendar** (kept as a fallback, per my recommendation).

## Scenarios (unchanged from the preregistration in `P1_2_SESSION_LAYER_PREREG.md` §3)
- **9a:** `checkout.failure_rate = 0.4` for 12–19 Feb 2018.
- **9b:** `demand.multiplier = 0.6` for the same week.
- **control:** no intervention.

Simulated 1 Jan – 2 Apr 2018, read as of 1 May 2018. **Fresh seeds 81–85.** The task is an `EXPLAIN_OBSERVATION` on `orders_count`, with the window 1 Jan – 2 Apr.

## Scoring (unchanged rule)
- **9a correct:** the finding for the week of 12 Feb locates the change at `stage ratio completion_rate` (checkouts → orders).
- **9b correct:** it locates the change `upstream of sessions_count`.
- **Control correct:** no period is flagged as changed for `orders_count`.
- **Pass:** at least 4/5 correct for 9a, at least 4/5 for 9b, and at most 1/5 false alarms in the control.
- **Comparison without the session layer** (same seeds): the reader should be unable to localise, i.e. no stage finding.

If v2 fails, the result is recorded and the layer stays "no measurable gain yet". The rule is not loosened.
