# Progress and next steps (resume point, kept current)

Approved by you on 22 Nov 2026: sync the docs, continue the stitch plan (D7 is approved), finish the plans, Astronomy Shop A1 + A2 (A5 human sessions optional).

| # | Step | Status |
|---|---|---|
| 1 | Docs synced to the plan (§7.5 table generated from the diagram rows, status banners, overview diagram, legacy note on `engine.py`) | **done** (cfbbeff, 0803c01) |
| 2 | Preregister the P1-2 session layer | **done** (df48c68, `P1_2_SESSION_LAYER_PREREG.md`) |
| 3 | Session layer: 5 transport cards (2 unsupported transfers), fidelity 8/8, invariance pass; S10 v1 FAIL → **S10 v2 PASS** (9a 5/5, 9b 4/5, control 5/5) | **done** |
| 4 | Astronomy Shop M0 experiments: E1 47.6 checkouts/s; E2 stable; E3 leaks fixed by redaction (0 hits); E4 catalog ≤ ~1k offers; E5 no checkout idempotency | **done** (035525e, `docs/experiments/M0_REPORT.md`) |
| 5 | Astronomy Shop integration M1–M5 (after conforming to `Contracts/`) | **done** (22 Nov; `INTEGRATION_M1_M5.md`): M3 6/6 seeds, scenario #5 all checks pass, leak scan 0 |

Rules unchanged: no deleting data, no paid sources, no push (you push). Commit after each step.

## Progress so far (22 Nov 2026)

| Area | Status | Where |
|---|---|---|
| Architecture design (incl. stitching, transport cards, v3 contracts, gates S1–S10) | **done, approved** (D7) | `docs/ARCHITECTURE.md`, diagrams in `docs/*.excalidraw` |
| Datasets: 19 + 2 reference files downloaded and audited; paid and unneeded ones dropped | **done** | `~/data/mkt/raw`, `docs/datasets/AUDIT.md`, `NOTES.md` |
| Astronomy Shop integration design (contracts aligned to v3) | **designed, approved** (A1, A2) | `docs/ASTRONOMY_SHOP_INTEGRATION.md` |
| P0 phase 1: Olist field meanings (S1, S2) | **done** | `PHASE1_SOURCE_SEMANTICS.md` |
| P0 phase 2: real data through the v3 contracts (S8, S6, S2) | **done** | `PHASE2_REAL_INTEGRATION.md` |
| P0 phase 3: simulated order chain (S5 9/10, weekday exception accepted; S3) | **done** | `PHASE3_MINIMAL_CHAIN.md` |
| P0 phase 4: what a monitor would see (S3) | **done** | `PHASE4_OBSERVATION.md` |
| P0 phase 5: intervention + counterfactual twin (S7, S6) | **done** | `PHASE5_INTERVENTION.md` |
| P1 item 1: logging failure vs real demand drop (13/15 correct) | **done** | `MORNING_REPORT.md` |
| Docs synced to the plan; outdated `stitch.py` guarded | **done** | this commit history |
| Merged into `main` (local only; no remote yet) | **done** | 61 tests green |

## Work that still remains

| # | Work | Depends on | Gate |
|---|---|---|---|
| R1 | Preregister the session → exposure → cart → checkout layer (transport cards, tolerances) | — | — |
| R2 | Build it one source at a time: Retailrocket, Taobao, Expedia, Google Analytics/Statcounter | R1 | S4 per source |
| R3 | Astronomy Shop feasibility experiments (images already pulled) | — | — |
| R4 | Astronomy Shop integration: session driver, catalog sync, flag and telemetry adapters | R2, R3 | S6, S7 |
| R5 | Remaining P1 sources (ads/Criteo, experiments, reviews text, fraud/infrastructure shapes), each with its card | R2 | S4 |
| R6 | Observation sensitivity suite (rerun the look-alike pair across the assumed ranges) | — | — |
| R7 | Replace the stand-in reader with the real causal core v3 (needs its owner) | contact with the v3 owner | S9, S10 |
| R8 | Optional: human click-through sessions in the shop (A5) | R4 | — |
| R9 | ~~Create a remote and push~~ **done 22 Nov**: github.com/msverma101/marketplace-recon (private) | — | — |

Auto-resume is scheduled for 00:13 on 22 Nov (backup at 01:13) and works through steps 2–5 of the table above. It only runs while this Claude Code session stays open.

## Waiting for you (22 Nov, after the overnight run)
1. ~~S10 for the session layer~~ **v2 passed** (22 Nov).
2. ~~Astronomy Shop M1+~~ done after your go-ahead. Open: the contracts' owner gaps G1–G3 (`INTEGRATION_M1_M5.md`); deferred sim-recommendation, M6, M7.
3. Push the new commits when you want: `cd ~/marketplace-recon && git push`.
