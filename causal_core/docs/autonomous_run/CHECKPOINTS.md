# Autonomous Run — Checkpoint Ledger

Chronological. Verdicts are `PROVISIONAL_*`; no human approval is implied by any entry after CP-000.

## CP-000 — Run baseline (2026-11-21)

- **Branch:** `autonomous/v32-run-20261121` created from `38a5081dc2801cdd6e2de42f3e87cc65e76f2da4`.
  `causal-core-phase-0` = `a22353b`; `causal-core-phase-1` = `causal-core-phase-2` = `38a5081`;
  `main` = `d03ccfd` (untouched).
- **Approvals given by the user before the run:**
  - Phase 0 exit items A1–A5: "approval all".
  - Checkpoint A: "Approve A", against commit `38a5081`.
- **Environment:** macOS-27.2.1 arm64 (Apple M1 Pro, 16 GiB), Python 3.11.12, pydantic 2.14.1 /
  pydantic_core 2.50.1, numpy 2.4.6, scipy 1.17.1, networkx 3.6.1.
- **Commands and exit codes:**
  - `python3 -m unittest discover -s tests -t .` → 33 tests, OK (exit 0)
  - `python3 -m scripts.phase0_demo --no-color` → exit 0
  - `python3 -m scripts.phase1_demo --no-color` → exit 0
  - `shasum -a 256 -c docs/baseline/SHA256SUMS` → OK
- **Hashes:** fixture `MANIFEST.sha256` sha256 = `06b762f82e51074302e4de450a924451adbb70a82e8326bedda4ecb28dede011`.
- **Uncommitted user change preserved:** `causal_core/docs/ROADMAP.md` was deleted in the working
  tree. A byte-identical copy (diff-checked) exists at `~/Downloads/hackathon/ROADMAP.md`, so it was
  moved by the user. It is not restored and never staged: every run commit uses an explicit
  pathspec that excludes it. The roadmap is read from `git show HEAD:causal_core/docs/ROADMAP.md`.
- **Inputs found outside the repo:** `~/Downloads/hackathon/untitled folder/phase1_traceability_conformance_audit_prompt.md`
  (168 lines). It is followed as the Phase 1 audit specification.

## CP-001 — Target ceiling: Phases 1–4, stop at Checkpoint C

`PROVISIONAL_PENDING_USER_REVIEW`. See `DECISION_LOG.md#cp-001`.

## CP-002 … CP-005 — Phase 1 contract completions (audit Class B)

| CP | Commit | Tag | Verdict |
|---|---|---|---|
| CP-002 schema_version on outputs; manifest latency/cost | `7502f12` | `checkpoint/CP-002-native-output-versioning` | PROVISIONAL_PENDING_USER_REVIEW |
| CP-003 UTC on the wire | `72add25` | `checkpoint/CP-003-utc-on-the-wire` | PROVISIONAL_PENDING_USER_REVIEW |
| CP-004 estimand source/target scope | `265e730` | `checkpoint/CP-004-estimand-scopes` | PROVISIONAL_PENDING_USER_REVIEW |
| CP-005 EntitySpec / DimensionSpec | `39515bf` | `checkpoint/CP-005-entity-dimension-specs` | PROVISIONAL_PENDING_USER_REVIEW |

Each commit left `python3 -m unittest discover -s tests -t .` green (exit 0) before the next began.

## Tier 1 — Phase 1 audit fixes (`ca8bf70`)

- Fixed: aggregate bounds (F-1) and NaN/Inf refusal (F-12).
- Added: frozen schemas (F-6), 27 audit tests, hand fixtures (F-10), compat fixtures (F-11).

## Roadmap Checkpoint A (re-verdict after audit) — `PROVISIONAL_PASS_PENDING_HUMAN_SIGNOFF`

- **Audit verdict:** `PASS_WITH_NONBLOCKING_FOLLOWUPS` (provisional). Docs: `docs/audits/phase1/`.
- **Commands (2026-11-21T21:14:44Z), all exit 0:**
  - `python3 -m unittest discover -s tests -t .` → 60 tests (12 + 21 + 27)
  - `phase0_demo`, `phase1_demo`
  - `export_schemas --check` → 20 schemas, no drift
  - baseline `SHA256SUMS` OK
  - fixture manifest OK (15 files, unchanged)
  - adversarial corpus 43/43
- **Coverage:**
  - due-now PASS 42/70 → 67/70
  - positive + negative evidence 29/70 → 44/70
  - ownership 91/91
- **Open:** 3 PARTIAL rows (P1-049, G06, G07), each owned by Phase 3 or 4. 21 obligations are deferred
  with owners. 6 items in `docs/audits/phase1/APPROVAL_REQUEST.md`.
- **Tag:** `checkpoint/A-phase1-audit`.

## CP-006 … CP-010 — Phase 2: typed bitemporal graph, views, admission (`3f658ff`)

| CP | Tag | Verdict |
|---|---|---|
| CP-006 MVCC store | `checkpoint/CP-006-mvcc-store` | PROVISIONAL_PENDING_USER_REVIEW |
| CP-007 certificate binding / effect-vs-edge | `checkpoint/CP-007-certificate-binding` | PROVISIONAL_PENDING_USER_REVIEW |
| CP-008 instantaneous DAG; reject vs quarantine | `checkpoint/CP-008-instantaneous-dag` | PROVISIONAL_PENDING_USER_REVIEW |
| CP-009 SQLite graph-delta log | `checkpoint/CP-009-graph-delta-log` | PROVISIONAL_PENDING_USER_REVIEW |
| CP-010 identity verification policy | `checkpoint/CP-010-identity-policy` | PROVISIONAL_PENDING_USER_REVIEW |

- **Commands, all exit 0:**
  - `python3 -m unittest discover -s tests -t .` → 85 tests (Phase 2: 25)
  - `phase0_demo`, `phase1_demo`, `phase2_demo`
  - `export_schemas --check` (3 additive schemas frozen; none of the 20 Phase 1 schemas changed)
  - Phase 0 fixture manifest unchanged (`06b762f8…`)
- **Measured profile** (`docs/phase2/graph_profile.json`, M1 Pro):
  - pin: 750 ns p50
  - traversal: p95 0.156 ms (997/1000 stopped at MAX_NODES)
  - memory: 73.8 → 100.4 MiB over 10 versions
  - SQLite: commit 0.38 s, rebuild 0.56 s, digests equal
- **Gates (Phase 2 scope):**
  - **PASS (provisional):** G04, G05 (graph part), G11 (edge-vs-effect), G01
  - **PASS for the identity/unit/pop/grain part:** G09. Kitagawa is Phase 4.

## CP-011 … CP-016 — Phase 3: single-writer ledger, jobs, budgets, outbox (`1a6c659`)

| CP | Tag | Verdict |
|---|---|---|
| CP-011 one ledger / one writer | `checkpoint/CP-011-single-writer-ledger` | PROVISIONAL_PENDING_USER_REVIEW |
| CP-012 scheduler and leases (known gap: no T0 writer priority) | `checkpoint/CP-012-scheduler-leases` | PROVISIONAL_PENDING_USER_REVIEW |
| CP-013 budget before dispatch | `checkpoint/CP-013-query-budget` | PROVISIONAL_PENDING_USER_REVIEW |
| CP-014 outbox guarantee per sink | `checkpoint/CP-014-outbox` | PROVISIONAL_PENDING_USER_REVIEW |
| CP-015 e-process revision recompute | `checkpoint/CP-015-eprocess-state` | PROVISIONAL_PENDING_USER_REVIEW |
| CP-016 evidence ingest | `checkpoint/CP-016-evidence-ingest` | PROVISIONAL_PENDING_USER_REVIEW |

- **Commands, all exit 0:**
  - `python3 -m unittest discover -s tests -t .` → **104 tests** (12 + 21 + 27 + 25 + 19)
  - `phase0_demo` … `phase3_demo`
  - `export_schemas --check`
  - baseline `SHA256SUMS`
  - Phase 0 manifest `06b762f8…` unchanged
- **Real `kill -9` at 7 boundaries:** all recover with job COMPLETED, charged 1, budget 3 → 2, 1 revision, outbox
  ACKED, 1 visible publication, and graph digest = clean run.
- **Load slice** (`docs/phase3/load_profile.json`): T0 enqueue→lease p50 62.7 / p95 151.7 / p99 231.2 ms; 17 deep
  jobs/s; WAL peak 4.19 MiB.

## Roadmap Checkpoint B — Graph & Ledger — `PROVISIONAL_PASS_PENDING_HUMAN_SIGNOFF`

- Review: `docs/autonomous_run/CHECKPOINT_B.md`. Tag `checkpoint/B-graph-ledger`.
- All §K evidence items PASS. No §D/§E stop condition was observed.
- Open: the T0 writer priority (CP-012), end-to-end G16 (Phase 5), the G08 Type-I study (Phase 4), and
  re-verification scheduling (Phase 5).

## CP-017 … CP-022 — Phase 4: capability registry, methods, identification (`5419c11`)

| CP | Tag | Verdict |
|---|---|---|
| CP-017 registry, 12-method catalog, numpy/scipy core | `checkpoint/CP-017-capability-registry` | PROVISIONAL_PENDING_USER_REVIEW |
| CP-018 sequential (measured power and scale limits) | `checkpoint/CP-018-sequential-eprocess` | PROVISIONAL_PENDING_USER_REVIEW |
| CP-019 randomized policy | `checkpoint/CP-019-randomized-policy` | PROVISIONAL_PENDING_USER_REVIEW |
| CP-020 DiD policy and trend sensitivity | `checkpoint/CP-020-did-policy` | PROVISIONAL_PENDING_USER_REVIEW |
| CP-021 identification auditor (BLOCKER-class subject) | `checkpoint/CP-021-identification-auditor` | PROVISIONAL_PENDING_USER_REVIEW |
| CP-022 VoI heuristic | `checkpoint/CP-022-voi-heuristic` | PROVISIONAL_PENDING_USER_REVIEW |

- **Commands (2026-11-21T22:07:14Z), all exit 0:**
  - `python3 -m unittest discover -s tests -t .` → **131 tests** (12 + 21 + 27 + 25 + 19 + 27)
  - `phase0_demo` … `phase4_demo`
  - `phase4_stats_report`
  - `export_schemas --check` (no drift)
  - baseline `SHA256SUMS` OK
  - fixture `MANIFEST.sha256` check OK; manifest sha `06b762f8…` unchanged
  - no core imports of `emulator`/`evaluation`/`scripts`
- **Independent statistics** (`docs/phase4/STATISTICS_REPORT.md`, seed 7):
  - 17 quantities vs the stdlib oracle: max diff 2.2e-7 (one explained row); all others ≤ 4.7e-13.
  - Type-I (1,000 × 200): iid 0.042; AR(1) ρ 0.3 / 0.5 / 0.8 → 0.131 / 0.232 / 0.458 (refused).
  - Revised streams: final data 0.043, ever raised 0.045. Variance bursts: 0.058.
  - Power at 0.1 / 0.2 / 0.5 SD: 0.142 / 0.423 / 1.000.
  - Adversarial suite: 6/6 pre-registered expectations met.

## Tier 1 — Phase 4 fixes (in `5419c11`)

| ID | Defect (BEFORE) | Fix (AFTER) | Falsifying test |
|---|---|---|---|
| T1-P4-01 | Cluster and DiD SEs were computed as `estimate / t`, giving NaN when the estimate was exactly 0. Constant outcomes crashed with `ZeroDivisionError`. A constant x crashed the association slope | `analytics/kernels.pooled_two_sample` computes the SE directly; zero variance → `DEGENERATE_VARIANCE` / `NO_VARIATION` | `DegenerateInputsTier1` (2) |
| T1-P4-02 | Registry lacked §4.3 fields (`cost_estimator_id`, next queries, forecast/cap, scope limitations) and §F.1 source-regime matching; `identifiability_audit_v1` unregistered (11 of 12) | Fields added; regime = `StudyDesign` discriminator; 12 methods | `test_descriptor_conformance_regime_forecast_next_queries`, `test_every_refusal_code_is_declared` |
| T1-P4-03 | SUTVA left out of the untestable assumptions when the design declared `interference = none_assumed` | Always listed; worded "declared by the design, not tested" | `test_randomized_itt_conditional_and_claim_is_not_an_edge` |

## Roadmap Checkpoint C — Claim semantics — `PROVISIONAL_PASS_PENDING_HUMAN_SIGNOFF`

- Review: `docs/autonomous_run/CHECKPOINT_C.md`. Tag `checkpoint/C-claim-semantics` (`5419c11`).
- Every §K evidence item PASSES. No plan §F stop condition was observed.
- G08 passes with documented limits; G09–G12 pass; G14 passes for the method part.
- Open:
  - fixed-bet power and scale misspecification (CP-018);
  - delivery policy for superseded alarms;
  - `status_reasons` field (CP-021);
  - P1 methods deferred;
  - items carried from B.

## Run stop — ceiling reached (CP-001)

The run stops here, at the end of Phase 4 / Checkpoint C. Phase 5 has **not** started. No hard stop
(Tier 3) was hit during the run. Nothing was pushed; `main` is untouched.

## User review — 2026-11-22

- Approved in batch: CP-002…CP-017, CP-020; roadmap checkpoints **A, B, C → `APPROVED_BY_USER`**
  (BEFORE: `PROVISIONAL_PASS_PENDING_HUMAN_SIGNOFF`).
- Changed by the user: CP-018, CP-019, CP-021, CP-022 (commit `1032aa6`); CP-001 ceiling raised to Phase 6.
- Tier 1: T1-P4-04 (CACE ignored differential attrition), fixed in `1032aa6`.
- Validation after the changes: 137 tests; demos 0–4 (Phase 4 demo section [6] shows the decisions live);
  `export_schemas --check`; baseline SHA256SUMS; fixture manifest; truth firewall clean.
- Statistics report regenerated (`docs/phase4/STATISTICS_REPORT.md` §B, §B2).

## Phase 5 — question-first planner, typed evidence, safety output (2026-11-22)

- New: `orchestration/planner.py` (pure FSM), `coordinator.py` (shell), `policy.py` (gate + `ReportEnvelope`),
  `adapters/sources/canonical_port.py`, `emulator/experiment_world.py`, `evaluation/phase5_cases.py`.
- Gates: G01, G10, G13, G14, G15 pass (`tests/test_phase5_orchestration.py`, 13 tests); G16 partial (deadline
  and progressive delivery only; load profile is Phase 6).
- Demo: `python3 -m scripts.phase5_demo`. Decisions CP-023…CP-026 (provisional).

## Phase 6 — three slices, evaluation, load (2026-11-22)

- New: `emulator/marketplace_world.py`, `evaluation/phase6_cases.py`, `tests/test_phase6_slices.py` (6 tests),
  `scripts/phase6_demo.py`, `docs/phase6/{results.csv, load_profile.json}`.
- Roadmap **Checkpoint D — `PROVISIONAL_PASS_PENDING_HUMAN_SIGNOFF`**: `docs/autonomous_run/CHECKPOINT_D.md`.
- Validation: 156 tests; demos 0–6; schemas; baseline hashes; fixture manifest; truth firewall clean.

## User review of Phases 5–6 decisions (2026-11-22)

- CP-023, CP-024, CP-027, CP-028, CP-029 approved; CP-025 and CP-026 approved with fixes (implemented, tested).
- Validation: 158 tests; demos 0–6; schemas; baseline; firewall. Checkpoint D still `PROVISIONAL_PASS_PENDING_HUMAN_SIGNOFF`.

## CP-030 and Checkpoint D sign-off (2026-11-22)

- CP-030 learned, frozen baseline (`baseline_shift_v1`): 4 tests; phase6_demo [5b].
- Roadmap **Checkpoint D → `APPROVED_BY_USER`** (BEFORE: `PROVISIONAL_PASS_PENDING_HUMAN_SIGNOFF`).
- Validation: 162 tests; demos 0–6; schemas; baseline hashes; truth firewall clean.

## Segment field, summary path, Statsig adapter (2026-11-22)

- CP-031 schema revision (Observation.segment), CP-032 per-arm summaries, CP-033 read-only Statsig adapter.
- Live read-only smoke on one experiment: recomputation equals Statsig to 7e-18.
- Validation: 171 tests; demos 0–6; schemas re-frozen; baseline; fixtures; firewall.

## Phase 7 (P1) — sparse what-if prediction (2026-11-22, user-authorized)

- New: `contracts/mechanisms.py`, `counterfactual/{mechanisms,rollouts}.py`, `emulator/dynamics_world.py`,
  `evaluation/phase7_cases.py`, `tests/test_phase7_whatif.py` (9), `scripts/phase7_demo.py`, `docs/phase7/calibration.json`.
- Decisions CP-034…CP-036 provisional. No roadmap checkpoint covers Phase 7 (E precedes Phase 8, not authorized).
- Validation: 180 tests; demos 0–7; schemas; baseline; fixtures; firewall.

## Fixes from the CP-032…CP-036 review; TabPFN evaluation set up (2026-11-22)

- CP-032 SRM on assignment counts; CP-034 shape sets + backtest gate; CP-035 7-day baseline, 5% margin; CP-036 risk
  constraints checked or refused. CP-033 approved. CP-037 TabPFN: isolated env + harness; blocked on licence acceptance.
- Validation: 185 tests; demos 0–7; schemas; baseline; fixtures; firewall.
