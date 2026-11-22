# Roadmap Checkpoint B — Graph & Ledger (Phases 2 + 3)

**Verdict: `APPROVED_BY_USER` 2026-11-22** (was `PROVISIONAL_PASS_PENDING_HUMAN_SIGNOFF`)

Made unattended. No human approval has occurred.

**Plan §K row B:**
- **Before changing:** graph/admission and persistent state migration.
- **Evidence required:** 13-edge tests, graph and DB atomicity, replay, data-cut semantics, failure recovery.
- **Decision requested:** approve the storage and graph implementation.

## Evidence required by §K, mapped

| Required evidence | Where | Result |
|---|---|---|
| 13-edge tests | `EdgesG04.test_all_13_kinds_have_a_valid_and_an_invalid_endpoint_pair` + 6 more `EdgesG04` tests | PASS: every kind has an accepted and a refused endpoint pair |
| Graph and DB atomicity | `AdmissionG05.test_crash_mid_commit_leaves_no_partial_version`; `KillNineG15` at `graph:mid_commit` (real SIGKILL inside the transaction) | PASS: no partial version; recovered graph equals a clean run |
| Replay | `AdmissionG05.test_rebuild_matches_live_store_at_every_version`; `test_pinned_snapshot_is_stable_across_later_commits`; `test_drift_quarantines_and_history_is_kept` | PASS: digests equal at every version; the old belief is reconstructed after drift |
| Data-cut semantics | `test_leaky_provider_cannot_verify_with_future_data`; `EvidenceLedgerG07.test_late_arrivals_invisible_before_their_availability`; Phase 1 `TimeFirewallG06` | PASS: nothing after the cut is used, even from a misbehaving provider |
| Failure recovery | `KillNineG15` (7 boundaries × real SIGKILL + recovery); `SchedulerG15` (reaping, backoff, max attempts, preemption, cancel) | PASS: no duplicate visible output, no budget reset, at-least-once dispatch recorded |

## Stop conditions (plan §D, §E)

| Stop condition | Observed |
|---|---|
| any read-path write | none: `ReadOnlyAndBoundedViews.test_reads_never_write` (digest unchanged) and an AST check that views reach no mutator |
| unbounded traversal | none: every traversal is bounded and reports `truncated` / `reason` |
| an actor silently converting effect estimates into structural links | none: `EffectVsEdgeG11` (ITT certificate refused as an exposure edge and as a mechanism edge; claim stored as an artifact) |
| graph memory and DB disagree after restart | none: recovered digest = clean-run digest at all 7 boundaries |
| poisoned queue stalls T0 | none: `test_poisoned_deep_queue_cannot_stall_t0` |
| outputs silently reappear with a new identity | none: deterministic revision keys; the non-idempotent sink duplicates under **one** identity, and that is reported |

## Decisions to review

| Decision | Topic | Risk |
|---|---|---|
| CP-006 | MVCC store (benchmarked against CoW) | HIGH |
| CP-007 | Certificate binding; effect ≠ edge | BLOCKER-class subject |
| CP-008 | Instantaneous DAG; reject vs quarantine | MEDIUM |
| CP-009 | SQLite graph-delta log | HIGH |
| CP-010 | Identity verification policy | MEDIUM |
| CP-011 | One ledger, one writer | HIGH |
| CP-012 | Scheduler and leases (**known gap**) | HIGH |
| CP-013 | Budget before dispatch | MEDIUM |
| CP-014 | Outbox guarantee depends on the sink | HIGH |
| CP-015 | E-process revision recompute | HIGH |
| CP-016 | Evidence ingest rules | HIGH |

## Open items (don't block B; recorded honestly)

1. **Writer priority for T0 (CP-012).** Measured T0 enqueue→lease p95 152 ms under writer contention.
   That's inside target, but §5.2's "writer slots" aren't implemented. Owner: Phase 5.
2. **G16 end-to-end:** only the ledger/scheduler slice and the graph profile are measured. Tier latencies need the
   Phase 5 planner.
3. **G08 statistical validity:** the storage side matches the oracle. The Type-I study (1,000 × 200) is Phase 4.
4. **Re-verification scheduling.** `downstream_reverify` is reported but not yet scheduled. Owner: Phase 5.
