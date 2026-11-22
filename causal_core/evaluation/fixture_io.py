"""Read/write helpers for the frozen fixture corpus (JSON + CSV + sha256 manifest)."""
from __future__ import annotations

import csv
import hashlib
import json
from pathlib import Path

from emulator.scenarios import Table

PROJECT_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_DIR = PROJECT_ROOT / "tests" / "fixtures" / "v21"
TRUTH_DIR = PROJECT_ROOT / "evaluation" / "truth"
MANIFEST = FIXTURE_DIR / "MANIFEST.sha256"
BASELINE_DIR = PROJECT_ROOT / "docs" / "baseline"

_CAST = {"str": str, "int": int, "float": float}


def clean(obj, ndigits: int = 12):
    """Round floats recursively so JSON is stable across platforms."""
    if isinstance(obj, float):
        return round(obj, ndigits)
    if isinstance(obj, dict):
        return {k: clean(v, ndigits) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [clean(v, ndigits) for v in obj]
    return obj


def dumps(obj) -> str:
    return json.dumps(clean(obj), indent=2, ensure_ascii=False) + "\n"


def sha256_file(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_table(table: Table, path: Path) -> None:
    with path.open("w", newline="") as fh:
        w = csv.writer(fh, lineterminator="\n")
        w.writerow(table.columns)
        w.writerows(table.rows)


def load_table(path: Path, columns, types) -> list[dict]:
    with path.open(newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        if tuple(header) != tuple(columns):
            raise ValueError(f"{path.name}: header {header} != declared {list(columns)}")
        return [{c: _CAST[t](v) for c, t, v in zip(columns, types, row)} for row in reader]


def load_fixture(case_id: str, fixture_dir: Path = FIXTURE_DIR) -> tuple[dict, list[dict] | None]:
    fx = json.loads((fixture_dir / f"{case_id}.json").read_text())
    data = fx.get("data")
    rows = None
    if data:
        rows = load_table(fixture_dir / data["file"], data["columns"], data["types"])
    return fx, rows


def manifest_lines(fixture_dir: Path = FIXTURE_DIR) -> list[str]:
    files = sorted(p for p in fixture_dir.iterdir()
                   if p.is_file() and p.name != MANIFEST.name and not p.name.startswith("."))
    return [f"{sha256_file(p)}  {p.name}" for p in files]


def verify_manifest(fixture_dir: Path = FIXTURE_DIR) -> dict:
    """Compare files on disk with MANIFEST.sha256 (missing, extra and changed files)."""
    recorded = {}
    for line in (fixture_dir / MANIFEST.name).read_text().splitlines():
        digest, name = line.split("  ", 1)
        recorded[name] = digest
    actual = dict(reversed(line.split("  ", 1)) for line in manifest_lines(fixture_dir))
    return {"ok": recorded == actual,
            "checked": len(recorded),
            "changed": sorted(n for n in recorded if n in actual and actual[n] != recorded[n]),
            "missing": sorted(set(recorded) - set(actual)),
            "unexpected": sorted(set(actual) - set(recorded))}


def verify_baseline_corpus() -> dict:
    bad, n = [], 0
    for line in (BASELINE_DIR / "SHA256SUMS").read_text().splitlines():
        digest, name = line.split("  ", 1)
        n += 1
        if sha256_file(BASELINE_DIR / name) != digest:
            bad.append(name)
    return {"ok": not bad, "checked": n, "changed": bad}
