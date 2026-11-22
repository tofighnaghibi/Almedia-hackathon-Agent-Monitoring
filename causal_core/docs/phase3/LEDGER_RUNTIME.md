# Phase 3 — Single-Writer Ledger, Durable Jobs, Budgets, Outbox, E-Process State

**Status:** built on `autonomous/v32-run-20261121`. **Provisional**: roadmap checkpoint B (Phases 2+3)
is reviewed in the internal build log (not included). Decisions: CP-011 … CP-016.

**Run:**
- Demo (real `kill -9` at 7 boundaries): `python3 -m scripts.phase3_demo`
- Tests: `python3 -m unittest tests.test_phase3_runtime` (19 tests)
- Load slice: `python3 -m scripts.phase3_load`
- Crash lab: `python3 -m scripts.phase3_worker --dir D --boundary <b>`, then `--recover`

## Modules

| Module | Role |
|---|---|
| `storage/schema.sql` | evidence, lineage_parents, evidence_quarantine, model_outputs, eprocess_state, method_decisions, task_revisions, outbox, jobs, checkpoints, query_ledger, query_cache (plus the graph tables from `graph_log.SCHEMA`) |
| `storage/ledger.py` | `Ledger`: one connection, one writer lock, `BEGIN IMMEDIATE`. Evidence ingest, e-process updates, method decisions, revision + outbox, named fault points |
| `storage/eprocess_state.py` | Pure consumption rules and wealth-path recomputation; append-only alarm events |
| `storage/graph_log.py` | Graph slice; shares the ledger's connection and lock; fault hook inside the open transaction |
| `orchestration/scheduler.py` | `PriorityScheduler`: leases, heartbeat, reaping, backoff, tiers, deep cap, checkpoint/preempt, cancel, stale pins |
| `orchestration/query_executor.py` | `BudgetedQueryExecutor`: charge before dispatch, replay, cache keys |
| `orchestration/outbox.py` | `OutboxDispatcher`; the delivery guarantee depends on the sink |
| `adapters/outputs/sqlite_sinks.py`, `adapters/sources/recording_port.py` | "Outside world" sinks and provider that survive the core's crash |

## Crash matrix (`KillNineG15`; each crash is a real SIGKILL, exit −9)

| Boundary | Inside a transaction? | After recovery |
|---|---|---|
| `graph:mid_commit` | yes | graph identical to a clean run; 1 version |
| `lease:during_renewal` | yes | lease expires → reaped → resumed (attempt 1) |
| `query:after_charge_before_dispatch` | no | charged 1, dispatched 1 |
| `query:after_dispatch_before_persist` | no | charged 1, **dispatched 2** (at-least-once, recorded) |
| `revision:after_insert_before_outbox` | yes | revision + outbox created once |
| `outbox:after_mark_sent_before_publish` | no | published once |
| `outbox:after_publish_before_ack` | no | idempotent sink: 1 visible. Non-idempotent sink: 2 visible under 1 identity |

At every boundary the job ends COMPLETED, the budget isn't reset (3 → 2), and there is exactly one task revision
and one ACKED outbox row.

## Measured load slice (`docs/phase3/load_profile.json`, M1 Pro)

The run combined 100 queued T2 jobs, 4 deep workers, a 20 ms back-to-back writer hog, a 1.5 s long reader and a
T0 alert every 25 ms:

| Measure | Result |
|---|---|
| T0 enqueue → lease | p50 62.7 ms · p95 151.7 ms · p99 231.2 ms (n = 59) |
| Deep throughput | 17.0 jobs/s |
| WAL | peak 4.19 MiB while the reader held a snapshot; 0 after a TRUNCATE checkpoint |

**Gap (CP-012 follow-up):** fast *lease* capacity is reserved, but the writer lock has no T0 priority. §5.2 asks
for "writer slots". The T0 tail is set by writer contention: fine against the 2 s target, but not designed.

## Gate status (Phase 3 scope)

| Gate | Status | Evidence |
|---|---|---|
| G15 durable scheduler/outbox | PASS (provisional) | `KillNineG15`, `SchedulerG15`, `BudgetAndOutboxG15` |
| G07 evidence independence (ledger part) | PASS (provisional) | `EvidenceLedgerG07`; 50 replicas ignored in `EProcessStateG08` |
| G08 sequential validity (storage part) | PASS (provisional) | `EProcessStateG08` = Phase 0 oracle. The null-stream Type-I study is Phase 4 |
| G05 graph + ledger atomicity | PASS (provisional) | graph mid-commit kill; shared transaction |
| G16 performance and isolation | PARTIAL | ledger/scheduler slice measured. End-to-end T0/T1/T2 tiers need the Phase 5 planner |
| G01 purity | PASS | `PurityG01Phase3` + earlier AST gates |

## Deferred, with owners

- **Phase 4 (`savi_eprocess_v1`):** the e-process *method*, i.e. the betting rule, the 1,000 null streams × 200
  peeks Type-I study, and dependence handling. Storage takes the increment as a parameter.
- **Phase 5:** the T0 writer-priority gate (CP-012 follow-up); the end-to-end tier latencies (G16); and wiring
  stale-pin detection into task revisions.
- **Before any multi-process deployment:** cross-host clock discipline. P0 is single-process.
