"""Record the machine baseline and the honest state of v2.1 benchmarks.

    python -m scripts.capture_baseline

There is no v2.1 implementation, so every tier measurement is MISSING with
a reason (plan §B task 4: "note missing benchmarks instead of inventing
results"). Machine facts are read from the OS, never typed in by hand.
Package versions are read from metadata without importing them.
"""
from __future__ import annotations

import json
import os
import platform
import subprocess
import sys
from datetime import datetime, timezone
from importlib import metadata

from evaluation.fixture_io import PROJECT_ROOT

OUT = PROJECT_ROOT / "docs" / "phase0" / "baseline_benchmarks.json"


def _sysctl(key: str) -> str | None:
    try:
        return subprocess.run(["sysctl", "-n", key], capture_output=True, text=True,
                              timeout=5, check=True).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


def _version(pkg: str) -> str | None:
    try:
        return metadata.version(pkg)
    except metadata.PackageNotFoundError:
        return None


def capture() -> dict:
    mem = _sysctl("hw.memsize")
    missing = {"status": "MISSING",
               "reason": "no v2.1 implementation exists; specification-only baseline (plan §B)"}
    return {
        "captured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "machine": {
            "os": platform.platform(),
            "cpu": _sysctl("machdep.cpu.brand_string") or platform.processor(),
            "logical_cores": os.cpu_count(),
            "ram_gib": round(int(mem) / 2**30, 1) if mem else None,
        },
        "python": {"version": platform.python_version(), "executable": sys.executable},
        "phase0_dependencies": "stdlib only",
        "phase1_candidate_dependencies_installed_here": {
            p: _version(p) for p in ("pydantic", "numpy", "scipy", "networkx")},
        "targets_not_measurements": {
            "T0_p95_s": 2, "T1_p95_s": 10, "T2_attempt_p95_s": 60,
            "load_profile": "5k nodes, 100 concurrent tasks, writer saturation, hung adapter (v3.2 §5.3)"},
        "measurements": {
            "T0": missing, "T1": missing, "T2": missing,
            "graph_traversal": missing, "memory_peak": missing, "sqlite_writer": missing},
    }


def main() -> int:
    OUT.write_text(json.dumps(capture(), indent=2) + "\n")
    print(f"wrote {OUT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
