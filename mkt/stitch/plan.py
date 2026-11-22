"""The stitch plan: one entry per row of the stitching table (ARCHITECTURE.md §7.5), run by scripts/stitch.py.

Each entry says which datasets feed which data type, how (the §7.5 method), which fitter turns the data into
a transport card or bridge table (§7.6), how it is held-out checked, and whether the simulator consumes it yet.
Single source of truth for the pipeline diagram (docs/tools/make_stitch_pipeline_excalidraw.py) and the runner.
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class Entry:
    id: str                       # card id, e.g. "L2-prices"
    layer: str
    data_type: str                # exactly as in the stitching table (docs/tools/make_stitching_excalidraw.py ROWS)
    method: str                   # fit | join on date | transfer | bridge | pool | anchor | replay | assumed (combinations allowed)
    sources: tuple[str, ...]      # dataset folders under ~/data/mkt/raw
    fitter: str                   # function name in mkt/stitch/fitters.py
    heldout: str                  # what the held-out check compares
    consumed_by: str | None       # simulator module that already uses the card, or None (fitted only)


PLAN: list[Entry] = [
    # L0 external environment
    Entry("L0-seasonality", "L0", "Seasonality, holidays, Black Friday", "fit + join on date", ("olist", "holidays"), "seasonality",
          "weekday shape and holiday effect: 2017 vs Jan-Aug 2018", "sim.chain (weekday + hour shape only)"),
    Entry("L0-macro", "L0", "Macro: inflation, FX, interest", "join on date", ("bcb",), "macro", "none: exogenous series, joined by date", None),
    Entry("L0-shock", "L0", "External shocks (2018 strike)", "replay + fit", ("olist", "public_record"), "strike",
          "order drop during the documented window vs the 4 weeks before", None),
    # L1 identity, scope, context
    Entry("L1-buyers", "L1", "Buyers, regions", "fit", ("olist",), "buyers", "region shares: 2017 vs 2018", "sim.chain"),
    Entry("L1-sellers", "L1", "Sellers, regions, size", "fit", ("olist",), "sellers", "seller-size distribution: 2017 vs 2018", "sim.chain"),
    Entry("L1-products", "L1", "Products, categories, titles", "fit + bridge", ("olist", "esci", "reviews2023"), "products",
          "category shares: 2017 vs 2018; bridge coverage per mapped category", None),
    Entry("L1-devices", "L1", "Devices, channels", "prior + transfer", ("statcounter", "ga"), "devices",
          "GA mobile:desktop conversion ratio, chronological halves", "sim.sessions"),
    # L2 catalog, offers, competition
    Entry("L2-prices", "L2", "Prices, promotions", "fit + transfer", ("olist", "m5"), "prices",
          "M5 weekly price-change probability and size: first vs second half of weeks", None),
    Entry("L2-offers", "L2", "Multi-seller offers", "fit + assumed", ("olist", "expedia"), "offers",
          "simultaneous-seller share: 2017 vs 2018", None),
    Entry("L2-stock", "L2", "Inventory, stock-outs", "transfer", ("retailrocket",), "stock",
          "stock-out share and spell length: first vs second half of snapshots", None),
    Entry("L2-search", "L2", "Search queries, relevance", "bridge", ("esci", "reviews2023"), "search",
          "E/S/C/I label mix: random halves of queries", None),
    # L3 seller health
    Entry("L3-handling", "L3", "Handling time, reliability", "fit", ("olist",), "handling",
          "approval-to-carrier hours: 2017 vs 2018", "sim.chain"),
    Entry("L3-entry", "L3", "Seller entry, ramp-up", "exact join", ("olist_funnel", "olist"), "entry",
          "deal-to-first-sale lag: first vs second half of deals", None),
    Entry("L3-exit", "L3", "Seller exit", "fit", ("olist",), "exit_", "inactivity hazard by size: 2017H1 vs 2017H2 cohorts", None),
    Entry("L3-fees", "L3", "Fees, payouts", "assumed (A-10)", (), "fees", "none: assumption", None),
    # L4 demand and discovery
    Entry("L4-traffic", "L4", "Traffic volume, calendar", "anchor (levels)", ("olist",), "traffic",
          "daily order level: 2017 fit predicts 2018 within tolerance", "sim.chain"),
    Entry("L4-sessions", "L4", "Session length, revisits", "transfer", ("taobao", "retailrocket"), "sessions",
          "card TC-TB1 (first-view lag halves)", "sim.sessions"),
    Entry("L4-funnel", "L4", "Funnel view → cart → buy, timing", "transfer", ("retailrocket", "taobao"), "funnel",
          "card TC-RR1 (views and cart share halves)", "sim.sessions"),
    Entry("L4-exposure", "L4", "Exposure, position, clicks", "transfer", ("expedia",), "exposure",
          "card TC-EX1 (odd vs even dates)", "sim.sessions"),
    Entry("L4-recs", "L4", "Recommendation policy, propensities", "transfer", ("open_bandit",), "recs",
          "random-policy CTR: first vs second half of days", None),
    Entry("L4-repeat", "L4", "Repeat purchases", "fit + transfer", ("olist", "instacart"), "repeat",
          "Instacart inter-order gap: random halves of users", None),
    Entry("L4-ads", "L4", "Ads, sponsored lift", "transfer", ("criteo", "hillstrom"), "ads",
          "Criteo visit uplift: random halves of rows", None),
    # L5 transactions and fulfilment
    Entry("L5-orders", "L5", "Orders, items, payments", "replay + fit", ("olist",), "orders",
          "items per order and payment-type mix: 2017 vs 2018", "sim.chain (orders, payments)"),
    Entry("L5-delivery", "L5", "Delivery promise, delays", "fit", ("olist",), "delivery",
          "late share and promise days: 2017 vs 2018 (phase-3 F7-F10)", "sim.chain"),
    Entry("L5-returns", "L5", "Cancellations, returns", "fit + transfer", ("olist", "uci"), "returns",
          "UCI return-line share: 2010 vs 2011", None),
    # L6 trust and feedback
    Entry("L6-score", "L6", "Review score given delivery", "fit", ("olist",), "score",
          "score gap on-time vs late: 2017 vs 2018", "sim.chain"),
    Entry("L6-text", "L6", "Review text", "pool", ("olist",), "text", "text share per score: 2017 vs 2018", None),
    Entry("L6-timing", "L6", "Review timing (survey sent, answered)", "fit", ("olist",), "timing",
          "answer lag median: 2017 vs 2018", "sim.chain"),
    Entry("L6-mnar", "L6", "Who leaves reviews (MNAR)", "fit + transfer", ("olist", "coat"), "mnar",
          "Coat self-selection ratio: random halves of users", None),
    Entry("L6-fraud", "L6", "Fake reviews, fraud rings", "transfer", ("yelpchi", "amazon_fraud"), "fraud",
          "spam-vs-genuine degree ratio: random halves of nodes", None),
    # L7 platform, L9 measurement
    Entry("L7-telemetry", "L7", "Service telemetry, faults", "live + shape", ("astronomy_shop", "nab_loghub"), "telemetry",
          "NAB anomaly-window length: real vs artificial series", "adapters.astronomy (live)"),
    Entry("L7-experiments", "L7", "Experiments, assignments", "transfer", ("open_bandit", "criteo"), "experiments",
          "allocation balance (sample-ratio check) in each source", None),
    Entry("L9-logging", "L9", "Logging gaps, delays, late data", "assumed (A-11) + evidence", ("retailrocket", "olist"), "logging",
          "purchases-without-view share: first vs second half", "sim.observe_chain (delays assumed)"),
]
BY_ID = {e.id: e for e in PLAN}
