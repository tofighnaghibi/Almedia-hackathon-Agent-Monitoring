# M0: Astronomy Shop feasibility experiments (22 Nov 2026)

**Setup:** release **3.1.0** images (pinned), a separate checkout of the 3.1.0 tag, and the minimal 17-service stack (load generator, flag UI, Kafka, accounting, fraud detection and observability backends off). Upstream files are untouched: everything goes through override files in `deploy/m0/`. Raw outputs are in `~/data/mkt/astronomy/` (3.1 GB, on /home) and the numbers in `m0_results.json`. The stack is **stopped**; images are kept.

| | Question | Result | Verdict |
|---|---|---|---|
| **E1** | Storefront throughput on this laptop | **47.6 checkouts/s** at 50 concurrent shoppers for 10 min (28,555 sessions; 12 errors = 0.04%). p95 latency: checkout 0.85 s, product pages 0.38 s, whole session 1.4 s. Already 41.6/s at 5 shoppers, so ~48/s is the ceiling. Containers used < 1 GB | **Ample.** Our Olist-scale world creates ≈ 230 orders and ≈ 28,000 sessions per day (0.3 sessions/s). The shop can replay a whole simulated day of sessions in ~10 min, or run live windows at 100× real time |
| **E2** | Interface stability | `demo.proto` and the checkout, product and recommendation handlers are **identical** between 3.1.0 and `main` (20 Nov). Only three wrong-method replies changed | **Stable.** Pin 3.1.0; the hash list in `E2_hashes.txt` becomes a contract test |
| **E3** | Does fault ground truth leak into telemetry? | **Yes, heavily, without redaction:** 416k hits in 10 min with payment failure 25% and recommendation cache failure on. (1) Every service that checks a flag writes the flag name and value on its own spans (`feature_flag.*`), not only the flag service. (2) Services call the flag service, so those calls appear as spans. (3) Payment errors carry the fault text ("Invalid token") and the customer's loyalty level. (4) Card type and validity appear on payment spans. **With our redaction layer** (`otelcol-redact.yml`): 21k hits → 46 (the collector's own config log) → **0**, in 3 rounds | **Solved, but needs care.** Faults stay visible as plain errors (290 error spans, 108 declined checkouts in the final 90-s run). Ground truth is gone. The redaction rules must be a regression test on every upstream bump |
| **E4** | Catalog scale | `ListProducts` through the frontend: 10 → 1k → 10k products = 0.006 → 0.025 → **0.20 s in USD**, but 0.02 → 0.62 → **14 s in EUR**: the frontend converts every product's price in a separate call. Search (unindexed `LIKE`): 0.2 → 1.8 → 16 ms | **Keep the synced catalog ≤ ~1,000 offers,** or store prices already in the shop currency. Search is not a bottleneck |
| **E5** (shop half only) | Retries and duplicates | The shop has **no idempotency**. A blind retry of a successful checkout returns a **500 error** (the cart is already empty), so it looks failed but isn't. If the cart is refilled first, a retry creates a **second order** | **Design requirement confirmed:** the session driver owns idempotency (one command ID per checkout, result recorded before any retry, no blind checkout retries; reconcile by cart state). The ledger half of E5 needs M1+ code |

## What changes in the design
1. **Storefront role can be wider than planned.** The design budgeted ~200 sessions per 20-minute window; the laptop can do about 57,000 in that time. The limit is now our own driver and replay design, not the shop.
2. **Redaction is a required component, not an option.** `deploy/m0/otelcol-redact.yml` is the starting point for the M5 telemetry adapter, with the leak scan (`scripts/m0_e3_scan.py`) as its test.
3. **Catalog sync caps at ~1,000 offers**, priced in the shop's currency.
4. **Checkout retries are the driver's job** (E5).

## Stop point (from the integration plan: "stop for review after M0")
M1+ means writing integration code against the shop: ledger, command bus, session driver, catalog sync and telemetry adapter. The plan asks for your review before it starts. Nothing else in M0 needs a decision.
