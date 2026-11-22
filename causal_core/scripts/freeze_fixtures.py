"""Freeze the Phase 0 reference fixtures.

    python -m scripts.freeze_fixtures            # write new cases, refuse to change frozen ones
    python -m scripts.freeze_fixtures --force    # deliberate re-freeze (record why in the PR)

For every case: build (emulator or hand data) -> write visible data CSV ->
re-load it -> derive expectations from *what was stored* -> write fixture
JSON. Truth goes to evaluation/truth/, never into the fixture directory.
"""
from __future__ import annotations

import argparse
import sys

from evaluation import fixture_io as io
from evaluation.reference_cases import CASES

FIXTURE_SCHEMA = "phase0-reference/1"


def freeze(force: bool = False) -> int:
    io.FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    io.TRUTH_DIR.mkdir(parents=True, exist_ok=True)
    written, unchanged, pending, refused = [], [], [], []

    for case in CASES:
        built = case.build()
        rows, data_meta, csv_text = None, None, None
        if built.table is not None:
            csv_path = io.FIXTURE_DIR / f"{case.case_id}.data.csv"
            tmp = csv_path.with_suffix(".csv.tmp")
            io.write_table(built.table, tmp)
            rows = io.load_table(tmp, built.table.columns, built.table.types)
            csv_text = tmp.read_text()
            data_meta = {"file": csv_path.name, "columns": list(built.table.columns),
                         "types": list(built.table.types), "n_rows": len(rows),
                         "sha256": io.sha256_file(tmp)}
            tmp.unlink()
        try:
            derived = case.derive(built.input, rows)
        except NotImplementedError as exc:
            pending.append((case.case_id, str(exc)))
            continue

        fixture = {"fixture_schema": FIXTURE_SCHEMA, "case_id": case.case_id, "title": case.title,
                   "tests_what": case.tests_what, "refs": list(case.refs), "gates": list(case.gates),
                   "source": built.source, "behavior": case.behavior,
                   "input": built.input, "data": data_meta, **derived}
        text = io.dumps(fixture)
        path = io.FIXTURE_DIR / f"{case.case_id}.json"
        if path.exists() and path.read_text() != text and not force:
            refused.append(case.case_id)
            continue
        if path.exists() and path.read_text() == text:
            unchanged.append(case.case_id)
        else:
            written.append(case.case_id)
        path.write_text(text)
        if csv_text is not None:
            (io.FIXTURE_DIR / data_meta["file"]).write_text(csv_text)
        if built.truth is not None:
            (io.TRUTH_DIR / f"{case.case_id}.json").write_text(io.dumps({"case_id": case.case_id, **built.truth}))

    io.MANIFEST.write_text("\n".join(io.manifest_lines()) + "\n")

    print(f"written {len(written)}, unchanged {len(unchanged)}, pending {len(pending)}, refused {len(refused)}")
    for cid, why in pending:
        print(f"  PENDING  {cid}: {why}")
    for cid in refused:
        print(f"  REFUSED  {cid}: frozen fixture would change; rerun with --force if intended")
    return 1 if refused else 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--force", action="store_true", help="overwrite frozen fixtures that would change")
    return freeze(ap.parse_args(argv).force)


if __name__ == "__main__":
    sys.exit(main())
