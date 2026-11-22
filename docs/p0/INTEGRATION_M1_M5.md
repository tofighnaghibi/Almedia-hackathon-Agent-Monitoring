# Contracts conformance + Astronomy Shop integration M1–M5: results (22 Nov 2026)

Design written first: `CONFORMANCE_AND_M1_M5_DESIGN.md` (commit fd0acbc).

## Part 1: conformance to `Contracts/` (schema 2.0)
- The core now sees only `Contracts` types through `mkt/core_port.py`: `manifest()` and `query(CapabilityRequest)`, returning `CapabilityResponse` with typed payloads, clocks with timezones and quality labels, provenance origins and roles, and `formula_ast` identities.
- **Ports are read-only** (I15): `propose_intervention` is gone.
- **Gates S8, S6 and S3 re-run on the contract port: pass** (`tests/test_contracts_conformance.py`).
- The stand-in core (`mkt/standin_core.py`) takes a `ReasoningTask` and returns a `TaskRevision`, `Finding`s and `EvidenceGap`s, importing only `Contracts`.
- **Gaps for the contracts' owner:**
  - **G1:** the manifest has no variable catalogue, so I added a read-only `definitions()`.
  - **G2:** should replayed real history be scope `production` (my choice) or `offline_replay`? The latter forbids `REAL_OBSERVED`.
  - **G3:** there is no origin kind for transported parameters, so they go in `extensions`.

## Part 2: integration milestones

| M | Built | Result |
|---|---|---|
| **M1** Emulator API | `mkt/emulator/api.py`: initialize / step / observe / intervene / snapshot / restore / capabilities over the gated chain (+ session layer) | stepping in pieces equals one step; snapshot → restore gives identical ledgers; no-op for `until ≤ clock` |
| **M2** Planes + gateway | privileged directory (ledger, interventions, correlation table, sealed answers) vs the public contract port cut at the clock; `audit.jsonl` | backdated or invalid actions are rejected with reasons; hidden measurements stay absent until granted; no privileged paths in public output |
| **M3** Headless closed loop | `mkt/harness/` (output sink with idempotency, measurement policy outside the core, sealed answers), `scripts/mvs_headless.py` | **6/6 seeds correct** (hidden coin flip: 4× logging drop, 2× demand drop). The core asks for an independent measurement, the policy grants payments, and revision 2 is right. Identical ledgers and outputs per seed |
| **M4** Storefront projection | `mkt/adapters/astronomy/storefront.py`: catalog sync (200 products), keyed product choice, idempotent session driver, commit-only-on-ok, correlation rows; `deploy/emulator/compose.emulator.yaml` | 495 sessions through the real shop: every successful command is a committed order, no failed one is, order IDs are unique. **Duplicate injection: zero new checkouts** |
| **M5** Telemetry + redaction | `mkt/adapters/astronomy/telemetry.py` (spans → `Contracts` observations, `SANDBOX_OBSERVED`); flag adapter; payment-failure lever through the gateway | **Scenario #5:** day A 247/247 ok; day B 51/248 payment failures, exactly those missing from the ledger; payment error rate 0 → 21%. **Leak scan 0** after fixing a new leak: the flag adapter's own read-back appeared in proxy spans and logs |

**Not deterministic in storefront mode (by design):** the shop draws payment failures at random per request (29% in the first run, 21% in the second). Headless runs are bit-reproducible; storefront runs are reproducible only in their recorded command results.

**Deferred:**
- the sim-recommendation gRPC service (needs a gRPC toolchain and its own container; checkout doesn't depend on it);
- M6, interactive human sessions (optional, A5);
- M7, the full scenario suite.

**How to run:**
```bash
cd ~/marketplace-recon
.venv/bin/python -I scripts/mvs_headless.py 7 8 9      # M3, no shop needed
# storefront (M4/M5): start the shop in storefront mode, then
cd ~/.cache/otel-demo-3.1.0 && DEMO_VERSION=3.1.0 OTEL_COLLECTOR_CONFIG_EXTRAS=~/marketplace-recon/deploy/m0/otelcol-redact.yml \
  docker compose -p m0 -f compose.yaml -f ~/marketplace-recon/deploy/emulator/compose.emulator.yaml up -d --no-deps \
  flagd otel-collector valkey-cart astronomy-db product-catalog cart currency payment shipping quote email ad recommendation checkout image-provider frontend frontend-proxy
cd ~/marketplace-recon && .venv/bin/python -I scripts/storefront_s5.py
```
