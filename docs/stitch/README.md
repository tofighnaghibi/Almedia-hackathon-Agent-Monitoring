# Stitched dataset: where it is and how it is built

## Locations
| What | Where | In git? |
|---|---|---|
| Raw datasets (19 + 2 reference files) | `~/data/mkt/raw/<dataset>/` | no (large; licences) |
| **Transport cards** (one per stitching row, 33) | `~/marketplace-recon/docs/stitch/cards/<row>.json` | yes |
| Session-layer cards used by the simulator (5) | `~/marketplace-recon/docs/p0/cards/TC-*.json` | yes |
| **Bridge tables** (large) | `~/data/mkt/stitch/bridges/` (`L0-macro.parquet`, `L2-search.parquet`) | no |
| Cached conversions | `~/data/mkt/stitch/cache/` (`esci_us_categories.parquet`, `uci.parquet`) | no |
| **Contract samples** (106 variables, Observation + EvidenceRecord) | `~/data/mkt/stitch/samples/<variable>.json` | no (regenerate in ~35 s) |
| Run manifest (inputs, cache keys, status) | `docs/stitch/manifest.json` | yes |
| Reports | `docs/stitch/STITCH_REPORT.md`, `docs/stitch/SAMPLES_REPORT.md`, `docs/stitch/checks.json` | yes |
| Pipeline diagram | `docs/stitch-pipeline.excalidraw` | yes |
| Variable catalogue (what each sample means) | `docs/p0/VARIABLE_CATALOG.md` | yes |

## Architecture
```
~/data/mkt/raw ──► scripts/stitch.py ──────────────► cards (docs/stitch/cards) + bridges (~/data/mkt/stitch/bridges)
  (19 datasets)    plan: mkt/stitch/plan.py (33 rows)      │  each: estimate, held-out check, status,
                   fitters: mkt/stitch/fitters.py           │  trained_through, assumptions, provenance
                   parallel · cached by content             │
                                                            ├──► simulator layers (mkt/sim: chain, sessions, observation)
                                                            │       └─ gated by scripts/stitch_check.py (S5, F1–F6, S7/S3, S10)
                                                            │
                                                            └──► scripts/stitch_samples.py ──► ~/data/mkt/stitch/samples
                                                                   one Contracts Observation + EvidenceRecord per catalogue
                                                                   variable (mkt/variable_catalog.py), checked by
                                                                   mkt/stitch/sample_checks.py and tests/test_stitch_samples.py
```
- **Cards** hold *shapes and ratios* learned from each dataset; **levels** always come from Olist.
- **Status per card:** supported · partially supported · unsupported transfer (kept, with a sensitivity range) · joined · evidence only · assumed.
- **Samples** carry honest provenance: `SANDBOX_OBSERVED` (Olist replay, shop telemetry), `SYNTHETIC_GENERATED` (simulator, card synthesis, named assumptions), `EXTERNAL_STUDY` (rows of another dataset shown as-is).
- The causal core reads samples or live data only through the contract port (`mkt/core_port.py`). The Astronomy Shop receives catalog rows, shopper sessions and flags, never samples.

## Rebuild everything
```bash
cd ~/marketplace-recon
.venv/bin/python -I scripts/stitch.py run          # cards + bridges   (~80 s first time, seconds when cached)
.venv/bin/python -I scripts/stitch_check.py        # gates             (~1–2 min)
.venv/bin/python -I scripts/stitch_samples.py      # contract samples  (~35 s)
```
