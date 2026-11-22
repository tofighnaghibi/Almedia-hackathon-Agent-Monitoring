# Causal Core

A general dynamic causal graph + reasoning engine, built from the v3.2 blueprint in
`docs/baseline/`, with a truth-holding scenario emulator kept outside the core.
Roadmap with one demo per phase: [`docs/ROADMAP.md`](docs/ROADMAP.md).

| Phase | Status | Branch | Demo |
|---|---|---|---|
| 0 Frozen baseline | approved 2026-11-21 ([inventory](docs/phase0/migration_inventory.md) §7) | `causal-core-phase-0` | `python3 -m scripts.phase0_demo` |
| 1 Canonical schema v2.0 | approved 2026-11-21, checkpoint A ([migration doc](docs/phase1/SCHEMA_V2_MIGRATION.md) §8) | `causal-core-phase-1` | `python3 -m scripts.phase1_demo` |
| 1 audit + 2 Typed graph & admission | **provisional** (autonomous run; see `docs/autonomous_run/RETURN_BRIEFING.md`) | `autonomous/v32-run-20261121` | `python3 -m scripts.phase2_demo` |
| 3 Ledger, jobs, outbox | **provisional** (checkpoint B pending) | `autonomous/v32-run-20261121` | `python3 -m scripts.phase3_demo` |
| 4 Capability registry, methods, identification | **provisional** (checkpoint C pending; run ceiling) | `autonomous/v32-run-20261121` | `python3 -m scripts.phase4_demo` |

## Run it (Python ≥ 3.10; Pydantic ≥ 2.7, NumPy ≥ 1.26, SciPy ≥ 1.16, e.g. `pip install -e .`)

```bash
cd causal_core
python3 -m scripts.phase0_demo                     # Phase 0: frozen reference cases
python3 -m scripts.phase1_demo                     # Phase 1: schema v2.0 on the cold-chain world
python3 -m scripts.phase4_demo                     # Phase 4: refusals, oracle agreement, Type-I, adversarial suite
python3 -m scripts.phase4_stats_report             # regenerates docs/phase4/{STATISTICS_REPORT,METHODS}.md
python3 -m unittest discover -s tests -t . -v      # all gates so far
python3 -m scripts.freeze_fixtures                 # (re)freeze; refuses to change frozen cases
python3 -m scripts.capture_baseline                # machine + MISSING benchmark record
```

## Layout

| Path | Role | May import |
|---|---|---|
| `causal_core/` | The core: `contracts/`, `api.py`, `migration/`, `semantics/`, `graph/`, `analytics/` (pure); `storage/`, `orchestration/` (shell) | pydantic, numpy/scipy (`analytics/`), sqlite3 (shell only) |
| `adapters/` | Legacy v1 query port; emulator-backed evidence port | causal_core |
| `emulator/` | Seeded scenario generators → visible data + separate truth | stdlib |
| `evaluation/` | Independent oracle, reference cases, truth store, fixture I/O | emulator |
| `scripts/` | Freeze, capture, demos | everything |
| `tests/fixtures/v21/` | Frozen reference corpus + `MANIFEST.sha256` | — |
| `docs/baseline/` | The six design documents, hashed and write-protected | — |
