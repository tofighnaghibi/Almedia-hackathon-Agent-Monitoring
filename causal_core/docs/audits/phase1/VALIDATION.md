# Phase 1 Audit — Validation Record

## Audit inputs (frozen so another agent can reproduce the assessment)

| Item | Value |
|---|---|
| Audited commit (input) | `38a5081dc2801cdd6e2de42f3e87cc65e76f2da4` (Phase 1 as approved at checkpoint A) |
| Branch | `autonomous/v32-run-20261121` |
| Commit after fixes | `ca8bf70` (code) + this documentation commit |
| Audit specification | `~/Downloads/hackathon/untitled folder/phase1_traceability_conformance_audit_prompt.md` (168 lines) |
| Normative docs | `docs/baseline/` (verified against `SHA256SUMS`) |
| Phase 0 fixture manifest | sha256 `06b762f82e51074302e4de450a924451adbb70a82e8326bedda4ecb28dede011`; 15 files verify (unchanged by the audit) |
| Environment | macOS-27.2.1 arm64, Apple M1 Pro, 16 GiB; Python 3.11.12; pydantic 2.14.1 / pydantic_core 2.50.1; numpy 2.4.6; scipy 1.17.1 |
| Working tree | `docs/ROADMAP.md` deleted by the user (moved to `~/Downloads/hackathon/`, byte-identical); not restored, not staged |

## BEFORE (at `38a5081`)

- `python3 -m unittest discover -s tests -t .` → 33 tests, OK (exit 0)
- `python3 -m scripts.phase0_demo --no-color` → exit 0
- `python3 -m scripts.phase1_demo --no-color` → exit 0
- Probe `audit_probe_before.py` (scratch; output reproduced in `CHANGES.md` "Before"). It observed:
  - accepted: proportion aggregate 600/400; timestamp `+02:00`; `NaN` / `Infinity` / `-Infinity`
  - missing: `schema_version` on 9 output contracts; `EstimandSpec` source scope (target scope optional);
    `EntitySpec` / `DimensionSpec`; manifest latency/cost
  - correct but **untested in the suite**: panel and assignment_outcomes round-trips, 4 shape
    constraints, coercion refusal, unknown discriminators, I/O purity

## AFTER (run at 2026-11-21T21:14:44Z on `ca8bf70` + docs)

| Command | Result |
|---|---|
| `python3 -m unittest discover -s tests -t .` | **60 tests, OK** (exit 0): `test_phase0` 12, `test_phase1_contracts` 21, `test_phase1_audit` 27 |
| `python3 -m scripts.phase0_demo --no-color` | exit 0 |
| `python3 -m scripts.phase1_demo --no-color` | exit 0 (all 8 gate groups ✓) |
| `python3 -m scripts.export_schemas --check` | "schemas match frozen files", exit 0 (20 schemas) |
| `shasum -a 256 -c docs/baseline/SHA256SUMS` | OK |
| `verify_manifest()` on `tests/fixtures/v21` | OK, 15 files |
| adversarial corpus (`evaluation/adversarial_contracts.py`) | **43/43** behave as expected (5 positive controls accepted) |
| `python3 -m scripts.audit_coverage docs/audits/phase1/TRACEABILITY.csv` | see `TRACEABILITY.md` |

## Fixtures used as evidence

| Fixture | Independence |
|---|---|
| Phase 0 fixture 09 (legacy records), 03 (replay) | Frozen before Phase 1 by the stdlib oracle: differential |
| `tests/fixtures/contracts/hand_sensor.json`, `hand_cluster_study.json` | Hand-written; no emulator or builder code |
| `tests/fixtures/compat/v1_records.json` | Hand-written raw v1 records with hand-written expectations from v3.2 §2.4 |
| Cold-chain emulator world | Generated. Used for round-trips and corpus mutation only: **self-consistency evidence, not independent evidence** |

## Not run / not claimed

- **No performance measurements:** none are due in Phase 1.
- **No statistical validity claims:** Phase 1 has no estimators.
- **"Domain agnostic":** established only at the contract level (stop condition, hand sensor fixture).
  End-to-end generality is a Phase 6 (G13/I18) obligation.
