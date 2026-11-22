# Phase 4: observation process + time integrity

**Gate S3 (no look-ahead): PASS** (`tests/test_phase4_observation.py`, 6 tests; full suite green)

## Built
- `mkt/sim/observe_chain.py`: turns the simulated world into what a monitor could see, in the same event vocabulary as the real adapter.

  | Property | Value | Label |
  |---|---|---|
  | Order capture delay | median 20 s (log-normal) | assumed (A-11), range 5–120 s |
  | Payment record delay | median 60 s | assumed (A-11), range 10–600 s |
  | Carrier events (handover, delivery) | arrive in a daily batch, next day 06:00 | assumed (A-11) |
  | Review | known when the customer answers | measured (phase 1) |
  | Carrier timestamps at minute precision | 29.5% | **measured** on Olist |
  | Missing delivery records | 8 per 96,478 | **measured** on Olist |
  | Delivery corrections | 1% first reported ±1 day off, corrected 2–7 days later | assumed (A-12), range 0–5% |
- Late corrections are **revisions**: same record key, higher revision, `supersedes` the earlier one. An as-of query returns only the latest revision available at that time.
- `EmulatorPort` serves this through **the same query engine and v3 contract as the real-data port** (`EventFramePort`). Evidence is labelled `synthetic` within an `emulator` scope, with `trained_through` in its lineage. It refuses to start if any mechanism was trained on data at or after the simulation start.

## Gate S3 tests
1. Across 25 random as-of times × 4 variables, nothing is ever served before it was available.
2. Append-only: simulating a longer horizon changes nothing knowable at an earlier time (keyed randomness per record).
3. Each correction is served once: the original before it arrives, the corrected one after, never both.
4. Measured properties are reproduced (rounding share; correction rate).
5. Emulator evidence is synthetic and dated; mechanisms trained after the start are refused.

## Not done in P0
Observation *sensitivity suites* (running scenarios across the assumed ranges above) are P1, listed in the morning report.
