# Return Briefing — `autonomous/v32-run-20261121`

_Overwritten after every checkpoint. Last update: 2026-11-22, Phase 7 (P1, user-authorized) built; Statsig adapter added._

| Branch & HEAD | Phases covered | Gates verified | Tier 1 fixes | Decisions | Hard stops / blockers | Status |
|---|---|---|---|---|---|---|
| `autonomous/v32-run-20261121`; local only, nothing pushed | Phase 1 audit; Phases 2–7 (7 = P1) | G01–G05, G07, G08 (documented limits), G09–G12, G14 (method part), G15; G06 and G16 partial | 7 | 22 recorded: 17 approved as written, 5 decided by the user on 2026-11-22 | none | A, B, C, D `APPROVED_BY_USER` |

Validation after the review: 137 tests, demos 0–4, schemas, baseline hashes and the fixture manifest pass.

## What changed on 2026-11-22

- CP-018: regularized two-sided bet (λ0 0.25, prior strength 100). Power at 0.25 SD 0.62 → 0.83; drops caught.
- CP-019: SRM warns (PARTIAL) from 0.01, blocks below 0.001; warning threshold learnable from history (simulated so far).
- CP-021: declared transport assumptions keep CONDITIONAL; simulator → production stays PARTIAL (frozen §4.5.5).
- CP-022: unknown latency/cost get a geometric prior learned from known options.
- CP-001: continue to Phases 5 and 6, each with a demo. P1 still out of scope.

## Phases 5–6 (2026-11-22)

- `python3 -m scripts.phase5_demo`: one coordinator, any trigger, typed failures, safety gate.
- `python3 -m scripts.phase6_demo`: three slices on one core, 9/9 truth checks, dashboard baseline, load.
- Review: `docs/autonomous_run/CHECKPOINT_D.md`; decisions CP-023…CP-029 (provisional).

## Phase 7 (P1)

- `python3 -m scripts.phase7_demo`: validated mechanisms, what-if questions, abstentions, held-out calibration.
- Decisions CP-034…CP-036 (provisional); CP-031…CP-033 (segment field, summaries, Statsig) partly provisional.

## Read first

1. `docs/autonomous_run/DECISION_LOG.md` → *User review — 2026-11-22*.
2. `docs/autonomous_run/CHECKPOINT_C.md` (claim semantics, with the AFTER section).
3. `docs/phase4/STATISTICS_REPORT.md` §B (old vs new bet) and §B2 (SRM calibration).

## Demos

`python3 -m scripts.phase0_demo` … `phase4_demo`; Phase 4 section [6] shows the user's decisions live.

## Open

- Scale misspecification of the sequential null (variance bursts 0.064 > α).
- Superseded-alarm delivery policy; `status_reasons` field; T0 writer priority; end-to-end G16 (Phase 5).
- SRM calibration on real platform history; transport sensitivity checks.
