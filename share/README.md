# Shared data for collaborators

- `../artifacts/`: processed Olist tables the emulator runs on (built by `mkt ingest` / `mkt fit`). Derived from the
  Olist Brazilian E-Commerce dataset (Kaggle, **CC BY-NC-SA 4.0**: attribution, non-commercial, share-alike).
  Kept in this private repo so collaborators can run the emulator without downloading anything.
- `stitch_samples.zip`: one contract-valid sample (Observation + EvidenceRecord) per catalogue variable (106),
  with SAMPLES_REPORT.md and VARIABLE_CATALOG.md. Contains 20 public Olist review texts (pt-BR).

Not shared (re-created by scripts): `~/data/mkt/runs/` (past runs; their `privileged/` folders hold ground truth)
and `~/data/mkt/raw/` (~20 GB; `scripts/datasets.py` downloads it; Kaggle/AIcrowd need your own accounts).
