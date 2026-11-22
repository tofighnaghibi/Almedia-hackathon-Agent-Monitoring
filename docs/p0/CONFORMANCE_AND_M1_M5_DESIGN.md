# Design: conform to `Contracts/` (schema 2.0) and build the Astronomy Shop integration M1–M5

Written before code (22 Nov 2026), at your request: "conform to the contracts and run the astronomy shop integration build too".
`Contracts/` is the authority. `mkt/v3.py` was my provisional guess and is **superseded** for everything the core sees.

## Part 1: conformance

```
simulator / Olist frames ──► EventFramePort (internal engine, unchanged)
                                   │
                                   ▼
                    mkt/core_port.py  ContractPort  ── implements Contracts.ports.EvidenceAccessPort
                      manifest() → ProviderManifest
                      query(CapabilityRequest) → CapabilityResponse     (read-only: no act/write method)
                      definitions() → tuple[VariableDefinition]         (read-only catalogue; see gap G1)
```

| Contract rule | How it is met |
|---|---|
| Closed request union | `get_series`, `get_aggregate`, `get_event_batch`, `get_source_metadata` served. `get_strata` / `get_panel` / `get_assignment_outcomes` / `get_intervention_history` / `get_measurement` → `UNSUPPORTED` + reason |
| Strict, frozen, aware datetimes | Every request and response is built and validated as a `Contracts` object. Olist and simulated clock times are local Brazil time, converted from `America/Sao_Paulo` to UTC at the boundary; `source_timezone` recorded |
| Clock quality | `observed_at` KNOWN (event time). `available_at`: **ESTIMATED** for Olist (no arrival times exist; was an assumption), **KNOWN** for the emulator (simulated delays) |
| Typed payloads | Series → `TimeSeries`; aggregate → `AggregateStats` (count: numerator/denominator or total; IID `UNKNOWN`, no covariance); events → `EventBatch` |
| Budgets | `max_rows` / `max_buckets` enforced → `BUDGET_EXCEEDED` or `PARTIAL` + `truncated` + reason |
| Revisions | `record_key` per observation; `revision` = internal revision + 1 (≥ 1) |
| Provenance | `ProvenanceLineage.origin_kind`: Olist = `original`; emulator = `synthetic_generated` with `generator_version` = chain params hash + session-card hashes; storefront telemetry = `original` in the emulator scope. `content_hash` = sha256 of the canonical payload JSON |
| Scopes | Olist: `production` with `data_cut` 2018-10-17 and `dataset_version` (it is real data from a production system; **owner check G2**). Emulator: `emulator` with `generator_version` |
| Roles (`EvidenceRecord`) | Olist `REAL_OBSERVED`; emulator `SYNTHETIC_GENERATED`; storefront telemetry `SANDBOX_OBSERVED` (real services, simulated marketplace) |
| Metric identities | `MetricDefinition.formula_ast` (closed AST) + `identity_tolerance`; e.g. `order_payment_ratio = payments_count / orders_count`, tolerance 0.002. Funnel = ratio metrics `cart_rate`, `checkout_rate`, `completion_rate`, each a `div` of two counts. The reader walks `formula_ast` refs instead of my free-text strings |
| Transport cards | `VariableDefinition.extensions` `{"mkt:transport_card": "TC-RR1", "mkt:transport_status": …}` (namespaced, carried, not interpreted) |
| No actuation on P0 ports (I15) | `propose_intervention` removed from every port. Interventions are scenario-side only (the evaluator's gateway). A core-originated action is a `Finding` of kind `ACTION_RECOMMENDATION` or an `EvidenceGap` published through `OutputSinkPort`; a separate policy gate in the harness decides whether the emulator executes it |
| Reader | The stand-in core takes a `ReasoningTask` (`DETECT_CHANGE` / `EXPLAIN_OBSERVATION`) and returns a `TaskRevision` with `Finding`s (`DESCRIPTIVE`, `ASSOCIATION`, `DQ_FAILURE`, `INCONCLUSIVE`) and `EvidenceGap`s. It imports only `Contracts` (import-boundary test) |

**Gates re-run on the new port:**
- **S8:** every response from real Olist and from the emulator validates as a `Contracts` object, and unsupported requests are explained.
- **S6:** roles and scopes are consistent, `REAL_OBSERVED` never comes from an emulator, and no oracle field is reachable.
- **S3:** `available_at` never exceeds the data cut.

**Gaps for the contracts' owner (recorded, not invented around):**
- **G1:** `ProviderManifest` has no variable catalogue. I add a read-only `definitions()`.
- **G2:** should replayed historical real data be scope `production` or `offline_replay`? `offline_replay` forbids `REAL_OBSERVED`.
- **G3:** no origin kind for *transported* parameters, so I use `extensions`.

## Part 2: Astronomy Shop integration (milestones from `ASTRONOMY_SHOP_INTEGRATION.md` §G, on the gated chain)

| M | Build | Acceptance (test) |
|---|---|---|
| **M1** Emulator API | `mkt/emulator/api.py`: `initialize(config, seed)`, `step(until)`, `observe() → ContractPort`, `intervene(spec)` (scenario-side, via the gateway), `snapshot()`, `restore()`, `capabilities()`. The ledger is append-only, one segment per step (Parquet in the run directory) | Stepping in pieces equals one step; snapshot → step → restore → step gives identical ledgers; `step` with `until ≤ now` is a no-op |
| **M2** Planes | public store (released observations) vs **privileged** directory (world frames, twins, correlation table, scenario answers); import-boundary test (`Contracts`, `mkt/core_port.py` and the stand-in core never import `mkt.sim`, `mkt.emulator`, `mkt.adapters.astronomy` or privileged modules) | leak tests; gateway rejections; audit log of every accepted or rejected action |
| **M3** MVS headless loop | scenario #8 (logging drop vs demand drop, hidden coin flip). The payments stream is **not exposed** until requested. The stand-in core sees the order dip and publishes an `EvidenceGap` asking for `payments_count`. The harness policy accepts, the emulator exposes the stream, steps one more day, the core re-queries and diagnoses, and the evaluator scores against the sealed answer | `scripts/mvs_headless.py` runs end to end; twice with the same seed gives identical ledgers |
| **M4** Storefront projection | `mkt/adapters/astronomy/`: **catalog sync** (top-N ≤ 1,000 products by 2017 Olist demand → `catalog.products` UPSERT, price BRL → USD at a fixed recorded rate, public 10-character IDs); **session driver** (plays a storefront window's buying sessions: product → cart → checkout, baggage `session.id`, `synthetic_request=true`, emulator-generated `command_id`); **commit/release** (the kernel commits an order only on `ok`; failures become `operational_failure` events and lost orders) | driven sessions produce committed ledger orders with matching correlation rows; a duplicate-injection test (replayed `command_id`) gives zero duplicate orders |
| **M5** Telemetry adapter + redaction | collector with `deploy/m0/otelcol-redact.yml` → file export → `telemetry_adapter` aggregates per minute and service (request count, error count, p95 latency) → `Observation`s, role `SANDBOX_OBSERVED`. **Flag adapter** (gateway lever `storefront.payment_failure`) writes a flag file atomically and reads it back through the flag service | scenario #5 (payment failure 25% for a window): error metrics rise and ledger orders drop in the window; leak scan clean (E3 as a test) |

**Deferred, with reasons:**
- **sim-recommendation gRPC service:** needs a new container and gRPC toolchain. Checkout doesn't depend on it; the upstream recommendation service stays.
- **M6** interactive human mode: optional (A5).
- **M7** full scenario suite.

**Products:** the chain has no product IDs, so each storefront buying session draws its product from the top-N catalog weights. The draw is keyed by `order_idx`, so it's deterministic and leaves the chain unchanged.

**Order of work:** Part 1 first, because the integration emits through the conformed port. Commit after each milestone.

---
## Part 1 result (22 Nov 2026)
- `mkt/core_port.py` (`ContractPort`, `olist_port`, `emulator_port`) and `mkt/standin_core.py` (reads `ReasoningTask`, returns `TaskRevision` + `EvidenceGap`s).
- `propose_intervention` removed from every port; `mkt/v3.py` is now internal to the engine only.
- **Re-run gates on the contract port: S8 pass, S6 pass, S3 pass** (`tests/test_contracts_conformance.py`, 10 tests):
  - every response survives strict JSON re-validation;
  - unsupported requests and budget overruns are explained;
  - roles and origins are consistent, and the contract itself rejects emulator output relabelled as real;
  - hidden variables are absent until exposed;
  - the import boundary holds (the core side imports only `Contracts`);
  - no actuation methods exist.
- **The look-alike pair through the conformed path** (supplementary; same seeds as P1-1): 8a 5/5, 8b 3/5, control 4/5, i.e. 12/15 against 13/15 for the old reader. The two 8b misses are near-threshold: the payments partner fell 2.8 standard units, against the 3.0 needed. Thresholds were not changed after seeing this.
