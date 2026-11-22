# Roadmap Checkpoint D — Operational P0 (Phases 5 + 6)

**Verdict: `APPROVED_BY_USER` 2026-11-22** (BEFORE: `PROVISIONAL_PASS_PENDING_HUMAN_SIGNOFF`). Approval covers local/staging deployment of the P0 core as built; it does not cover real-source adapters, production writes or P1.

**Plan §K row D:**
- **Before changing:** activation of monitoring and question entrypoints.
- **Evidence required:** three vertical slices; full p50/p95/p99 load; compatibility difference report; G01–G16.
- **Decision requested:** approve local/staging deployment.

## Evidence

| Required | Where | Result |
|---|---|---|
| Three vertical slices | `scripts/phase6_demo.py`, `tests/test_phase6_slices.py`, `docs/phase6/results.csv` | S1 metric-by-segment, S2 RCT with no monitor, S3 sensors/events with no metric fields; each records a success, an error and an abstention; **9/9 truth-scored acceptance checks met** |
| Architectural generality (plan §H exit) | `test_s2_needs_no_monitor_and_s3_needs_no_metric_fields`, `test_no_slice_branch_inside_the_core`, `test_planner_has_no_domain_vocabulary` | S2 runs with no monitor configured; S3 runs with no metric fields; planner/coordinator/policy contain no slice or domain literals |
| Baseline comparison | `BaselineComparison` | a fixed dashboard rule reports "conversions −38%" on a partly loaded day; the core reports a DQ fault plus a mix-driven drop (truth: segment rates unchanged) |
| p50/p95/p99 load | `docs/phase6/load_profile.json`, `LoadG16` | 100 tasks, 8 threads, one ledger, 4 on a hung provider: T0 p95 ≈ 70–80 ms, final p95 ≈ 190 ms, p99 ≈ 470 ms; 0 errors; 0 duplicate publications |
| G01–G16 | full suite: 158 tests; demos 0–6 | see the gate notes below |

## Gate notes (honest)

- G13 task independence: PASS (Phases 5 and 6).
- G14 evidence acquisition: PASS for ranking, gaps and the planner path; no learned cost statistics yet.
- G15 durability: PASS (Phase 3 kill -9 plus Phase 5 provisional/final revisions and replay).
- **G16 performance: PARTIAL.** Covered: 100 concurrent tasks, one writer, a hung adapter, tier latencies, memory, WAL.
  Not covered in the same run: a 5k-node graph under load (Phase 2 profiled it separately), large or corrupted
  payloads, a repeatedly revised bucket under load, deliberate writer saturation beyond the shared ledger.
- G06 remains partial as recorded at checkpoint B.
- Compatibility difference report vs v2.1: the Phase 0 reference cases (`phase0_demo`) are the comparison;
  no separate report was written in this phase.

## Decisions in this checkpoint (provisional)

CP-023 … CP-026 (Phase 5) and CP-027 … CP-029 (Phase 6) in `DECISION_LOG.md`.

**User review 2026-11-22:** all seven approved; CP-025 (replay key) and CP-026 (proposal labelling) approved with fixes, now implemented and tested. The checkpoint itself still awaits your sign-off.

## Not done

- No real sources: every slice runs on emulator worlds. Real Babbel adapters (Statsig, Databricks) need your approval.
- P1 untouched (Phases 7–8, TabPFN, sandbox `TestExecutionPort`).

## Sign-off — 2026-11-22

- Decisions CP-023…CP-029 approved (CP-025, CP-026 with fixes); CP-030 (learned baseline) added at the user's request.
- Validation at sign-off: 162 tests; demos 0–6; schemas; baseline hashes; truth firewall clean.
- Open, not blocking: G16 items listed above; segment field (CP-027) before real adapters; CP-030 follow-ups.
