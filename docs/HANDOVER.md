# Marketplace emulator: handover (22 Nov 2026)

**Repo:** `github.com/msverma101/marketplace-recon` (private), branch `main`. **Data:** `~/data/mkt/` (not in git; the datasets are on the owner's laptop). **Setup:** Python venv at `.venv` (DuckDB, Polars, Pydantic 2, NumPy, SciPy); the causal core is importable through a `.pth` file pointing at `causal_core/`.

## What this is
A **marketplace emulator** that the **causal core** (`causal_core/`, Subsystem 2 in `docs/master_implementation_blueprint.md`) can monitor and investigate.
- The emulator is anchored on real Brazilian marketplace data (Olist) and stitched with 18 other public datasets.
- The core sees only **contract objects** (`causal_core.contracts`) through read-only ports. Ground truth stays in a privileged plane and is used only for scoring.
- The **OpenTelemetry Astronomy Shop** is an optional real storefront that the emulator drives, adding real service behaviour and telemetry. It is not the emulator.

```
19 datasets ─► stitch cards ─► emulator (order chain + sessions + observation layer) ─► Astronomy Shop (optional)
                                    │ contract port (read-only)                │ redacted telemetry
                                    ▼                                          ▼
                          causal_core (TaskCoordinator) ◄──────── telemetry port
```

## Done (tested; 189 tests pass, plus the core's own suite)
| Area | What exists | Where |
|---|---|---|
| Datasets | 19 datasets + 2 reference files downloaded and audited; paid ones dropped | `~/data/mkt/raw`, `docs/datasets/AUDIT.md` |
| Order chain (P0) | demand → orders → payment → shipping → delivery → reviews, fitted on Olist 2017 and checked against held-out 2018. Gates S1, S2, S3, S5 (9/10; accepted weekday exception), S6, S7, S8 | `mkt/sim/chain.py`, `docs/p0/PHASE*.md` |
| Observation layer | delays, timestamp rounding, missing records, late corrections (revisions) | `mkt/sim/observe_chain.py` |
| Interventions + twin | action gateway (allowlist, bounds), counterfactual twin with keyed randomness | `mkt/sim/twin.py` |
| Session layer | visits, views (search position), carts, checkouts, devices; 5 transport cards; fidelity 8/8; value check S10 v2 PASS | `mkt/sim/sessions.py`, `docs/p0/P1_2_SESSION_LAYER.md` |
| Stitching | all 33 data types fitted into transport cards with held-out checks and status (82 s) | `scripts/stitch.py`, `mkt/stitch/`, `docs/stitch/` |
| Variable catalogue | 106 variables (metric, state, event, categorical) across layers L0–L9, with sources and reasons | `mkt/variable_catalog.py`, `docs/p0/VARIABLE_CATALOG.md` |
| Contract samples | one valid sample per variable; the core's parser accepts all 340 observations | `scripts/stitch_samples.py`, `~/data/mkt/stitch/samples/` |
| Contracts | everything core-facing uses `causal_core.contracts`; one command re-checks it | `mkt/core_port.py`, `scripts/conform.py` |
| Emulator API | step / intervene / snapshot / restore; public vs privileged planes; audit log | `mkt/emulator/api.py` |
| Closed loop | core asks for evidence → policy grants a hidden measurement → core re-answers; 6/6 seeds | `mkt/harness/`, `scripts/mvs_headless.py` |
| Astronomy Shop | catalog sync, idempotent session driver (buying + browsing), flag adapter, redacted telemetry; payment-failure scenario passes; live run 400/400 sessions OK | `mkt/adapters/astronomy/`, `deploy/`, `docs/experiments/` |
| Real core on the emulator | `TaskCoordinator` runs against our port; the payments-per-order ratio separates logging drop from demand drop (2/2 completed seeds) | `scripts/core_on_emulator.py` |
| Core change | opt-in linear trend in `learn_baseline(points, trend=True)` | `causal_core/causal_core/analytics/baseline.py` |

## Not done (what to pick up)
| # | Task | Why it matters | Starting point | Estimate |
|---|---|---|---|---|
| 1 | **Core monitoring window:** order-count results are identical across scenarios per seed, so the default `DETECT_CHANGE` window likely misses the intervention week | the core may be monitoring the wrong period | `causal_core/causal_core/orchestration/planner.py` (`frame`, `plan_requests`); `scripts/core_on_emulator.py` | 1–3 h |
| 2 | **Trend in the core's baseline is only a partial fix:** one seed is still refused (autocorrelation 0.27), and one raises a false alarm on orders in the control | growing marketplaces can't be monitored reliably yet | `baseline.py`; try weekly grain or longer training | 2–4 h |
| 3 | **18 stitched cards aren't used by the simulator yet:** holidays/Black Friday, macro, strike, prices, stock, offers, search, recommendations, repeat buying, ads, returns, review text, who reviews, fraud, seller entry/exit, experiments, fees | most catalogue variables are "planned", not simulated | `docs/stitch/STITCH_REPORT.md` ("used by simulator: not yet"); pattern = `mkt/sim/sessions.py` + `docs/p0/P1_2_SESSION_LAYER_PREREG.md` | 40–60 h in 8 bundles |
| 4 | **Bundle order** (by value): L0 context → L2 supply (prices, stock, offers; re-check realism S5) → search + recommendations → returns/reviews → sellers → ads/experiments → fraud → L9 measurement | — | each bundle: preregister tolerances → layer → fidelity, invariance and value gates → commit | — |
| 5 | Shop: simulator-driven recommendation service (gRPC), human sessions (M6), full scenario suite (M7) | live recommendation and UX scenarios | `docs/ASTRONOMY_SHOP_INTEGRATION.md` §G | 5–8 h + optional |
| 6 | Pre-existing core failure: `tests.test_phase6_slices.LoadG16` (SQLite concurrency) fails with or without our change | core reliability | `causal_core/tests/test_phase6_slices.py` | core team |
| 7 | Contract suggestions for the core team (catalogue in the manifest, typed measurement requests); add the trend-term note | removes two workarounds | GitHub issue #1, `docs/CONTRACT_SUGGESTIONS.md` | — |
| 8 | Gate S9 (reasoning ability) with the real core across the scenario set | the core's scored performance | `mkt/harness/`, sealed answers | after items 1–2 |

## Rules the project follows (please keep them)
- **Design before build:** a diagram or note first, then a script; never do the process by hand.
- **Preregister** tolerances before measuring. A failed check is recorded, never loosened.
- **Levels come from Olist; other datasets give shapes and ratios.** Every borrowed pattern has a transport card and a status.
- **The core never sees ground truth**, and evidence is labelled honestly (`SYNTHETIC_GENERATED`, `SANDBOX_OBSERVED`, `EXTERNAL_STUDY`).
- **No paid sources, no deleting data, no pushing without the owner's go-ahead.** Decisions are logged in `docs/p0/STRATEGY_CHANGES.md`.

## Setup for a new machine
```bash
git clone https://github.com/msverma101/marketplace-recon && cd marketplace-recon     # artifacts/ and share/ come with it
python3 -m venv .venv && .venv/bin/python -m pip install duckdb polars "pydantic>=2.7" numpy scipy pyarrow fastexcel
echo "$PWD/causal_core" > $(.venv/bin/python -c "import site;print(site.getsitepackages()[0])")/causal_core_local.pth
mkdir -p ~/data/mkt/stitch && unzip share/stitch_samples.zip -d ~/data/mkt/stitch/      # samples where the scripts expect them
# only for the Astronomy Shop part:
git clone --branch 3.1.0 --depth 1 https://github.com/open-telemetry/opentelemetry-demo.git ~/.cache/otel-demo-3.1.0
mkdir -p ~/data/mkt/astronomy/flags ~/data/mkt/astronomy/live && chmod 777 ~/data/mkt/astronomy/live
cp ~/.cache/otel-demo-3.1.0/src/flagd/demo.flagd.json ~/data/mkt/astronomy/flags/
```

## Commands
```bash
.venv/bin/python -m pytest -q                         # all tests (~75 s)
.venv/bin/python -I scripts/stitch.py run             # stitch cards (~80 s; cached re-runs take seconds)
.venv/bin/python -I scripts/stitch_check.py           # gates for rows the simulator uses
.venv/bin/python -I scripts/stitch_samples.py         # 106 contract samples (~35 s)
.venv/bin/python -I scripts/conform.py                # contracts check (~70 s)
.venv/bin/python -I scripts/core_on_emulator.py       # real core on the emulator (~2 min)
.venv/bin/python -I scripts/storefront_sessions.py    # live shop run (start the shop first: docs/p0/INTEGRATION_M1_M5.md)
```
