# Phase 2 — Typed Bitemporal Graph, Views and Admission

**Status:** built on `autonomous/v32-run-20261121`. Roadmap checkpoint B covers Phases 2+3, so Phase 2
is **provisional** and has no checkpoint of its own. Decisions: CP-006 … CP-010 in
the internal build log (not included).

**Run:**
- Demo: `python3 -m scripts.phase2_demo`
- Tests: `python3 -m unittest tests.test_phase2_graph` (25 tests)
- Profile: `python3 -m scripts.phase2_profile`

## Modules

| Module | Role | Pure? |
|---|---|---|
| `contracts/graph.py` | 13 `EdgeKind`s, statuses, `GraphNode`, `GraphEdge`; **`StructuralCertificate`** (new) | yes |
| `contracts/admission.py` | `AdmissionRequest`, `AdmissionVerdict` (per-step results, per-edge outcomes) | yes |
| `semantics/metrics.py` | Unit algebra, population/grain checks, structural-edge derivation, numerical identity | yes |
| `graph/validation.py` | Endpoint/layer rules for all 13 kinds, observation firewall, instantaneous-cycle search | yes |
| `graph/store.py` | MVCC store: append-only versions, O(1) `pin()`, valid-time segments for definitions, `digest()` | yes (`apply` is the only mutator) |
| `graph/views.py` | `StructuralView`, `TemporalView`, `CausalEvidenceView`; `TraversalBound` | yes, read-only |
| `storage/graph_log.py` | SQLite WAL delta log; one transaction per admission; `rebuild()` | shell |
| `orchestration/admission.py` | `GraphAdmissionCoordinator`, the eight steps | shell |
| `adapters/sources/emulator_port.py` | Read-only `EvidenceAccessPort` over the emulator; request builder | adapter |

## The eight steps (v3.2 §3.3)

| Step | What happens | Possible outcomes |
|---|---|---|
| 1 | Dedupe by (source_id, record_id) | identical → `IDEMPOTENT_NOOP`; same id with different content → `REJECTED` |
| 2 | Subtype (contract), attestation source, definition scope, provenance | scope or attestation mismatch → `REJECTED`; `method: none` or `legacy_unscoped` → everything quarantined |
| 3 | Entity and dimension mapping | uncertain entity links kept out of exact closure; `DIMENSIONAL_DECOMPOSITION` without a complete `DimensionSpec` → QUARANTINED |
| 4 | Metric formula: parents admitted, units, population, grain → derive structural edges | non-metric → `NOT_APPLICABLE`; missing parent → standalone (no edges); semantic violation → QUARANTINED |
| 5 | Declared edges: endpoints/layers, validity order, edge scope = request scope, certificate binding, instantaneous cycles | each failure → REJECTED with reason |
| 6 | **One** bounded `GetAggregate` query; leak-safe recheck; ≥2 windows; tolerance | `VERIFIED_IDENTITY` → ACTIVE; mismatch or unverified → QUARANTINED |
| 7 | Assign statuses | ACTIVE / QUARANTINED, with reasons |
| 8 | SQLite commit → `store.apply` → notify subscribers only | commit failure leaves memory untouched |

**Reject vs quarantine (CP-008):** structurally invalid → **REJECTED**, not stored.
Evidentially questionable → **QUARANTINED**, stored with reasons and excluded from default traversal.

## Views

- **`relationship(a, b)`:** `KNOWN` only with an ACTIVE edge of that view's kinds. Otherwise
  `UNKNOWN`, with any quarantined/refuted edges listed separately. There is no `KNOWN_ABSENT` (I08).
- **`neighborhood(seed, bound)`:** BFS bounded by hops / nodes / edges / wall-clock. Always reports
  `truncated` and `reason` (`MAX_HOPS`, `MAX_NODES`, `MAX_EDGES`, `MAX_WALL_MS`).
- **`CausalEvidenceView.unknowns_relevant_to(t, y)`:** only (t, y) and each recorded cause of t
  against y (C07). It doesn't enumerate every unknown pair.
- **`effect_claims(t, y)`:** effect claims are artifacts, never edges (I06).

## Gate evidence (`tests/test_phase2_graph.py`)

| Gate | Tests | What it proves |
|---|---|---|
| G04 | `EdgesG04` (7) | All 13 kinds have a valid and an invalid endpoint pair. Metric-only kinds refuse non-metrics. Observation firewall holds. Missing adjacency = UNKNOWN. Zero-lag cycle rejected; positive-lag feedback kept. Uncertain entity links excluded from exact closure. Decompositions need a complete partition. Scope forgery and bad validity rejected |
| G05 | `AdmissionG05` (10) | One identity query. Idempotent and conflicting records. Drift → QUARANTINED with history reconstructable at the earlier pin. Unverifiable identity quarantined. Leaky provider can't verify. Population change and missing parent handled. Domain-time redefinition. Crash mid-commit leaves nothing. Rebuild equal at every version. Pinned snapshot stable |
| G09 (Phase 2 part) | `IdentityG09` (2) | Production identity checker = Phase 0 oracle on fixture 01. Units, population and grain rules |
| G11 | `EffectVsEdgeG11` (2) | ITT certificate refused as an exposure edge (`RELATION_MISMATCH`) and as a mechanism edge (`EFFECT_ONLY_CERTIFICATE`). The claim is stored as an artifact and creates no edge. A mechanism certificate is admitted in its own scope and refused in another (`SCOPE_MISMATCH`) |
| G01 | `PurityG01Phase2` + `test_phase0.CoreImportBoundary` | `semantics/` and `graph/` do no I/O. The core imports no emulator, evaluation or scripts code |
| Plan §D stop conditions | `ReadOnlyAndBoundedViews`, `SubscriberOnlyNotification` | No read-path write (digest unchanged after all view calls). No unbounded traversal. No auto-monitoring |

## Measured profile (`docs/phase2/graph_profile.json`)

Apple M1 Pro, Python 3.11.12, 5,000 nodes / 20,000 edges / 10 versions (1,000 edges revised per version):

| Measure | Result |
|---|---|
| `pin()` | 750 ns p50 / 875 ns p99 |
| Bounded traversal (3/100/400/50 ms) | p50 0.127 ms, p95 0.156 ms, p99 0.192 ms. **997/1000 stopped at `MAX_NODES`** |
| `relationship()` | 5.3 µs p50 |
| Memory (tracemalloc) | 73.8 MiB at v1 → 100.4 MiB at v10 (+2.7 MiB per 1,000 revised edges) |
| SQLite log | commit of all 10 versions 0.38 s; rebuild 0.56 s; digests equal; 23.3 MiB on disk |

Two observations for Phase 4:
1. With an average degree of 8, the default 100-node bound truncates almost every 3-hop
   neighbourhood. Methods must treat `truncated=True` as incomplete context, never as absence.
2. About 3.7 KB per edge comes from Pydantic objects; that's fine against the 512 MB per-worker
   starting cap (§5.3).

## Deferred, with owners

- **Re-verification scheduling (Phase 3 scheduler).** When a parent's definition changes, the verdict lists
  `downstream_reverify` children, but nothing re-runs them yet.
- **Ledger merge (Phase 3).** The graph-log tables become the graph slice of the single-writer ledger.
- **Domain-time segments for edges.** Edges have knowledge-time revisions plus their own validity interval.
  Valid-time segmentation like node definitions is not implemented, and nothing needs it yet.
- **`KNOWN_ABSENT`.** Intentionally not representable (§3.1). A future evidence-backed negative relation
  would need its own contract.
