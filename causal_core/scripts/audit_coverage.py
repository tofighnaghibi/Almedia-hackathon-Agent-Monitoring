"""Recompute audit coverage measures from a TRACEABILITY.csv (Phase 1 audit, Step 2).

    python -m scripts.audit_coverage docs/audits/phase1/TRACEABILITY.csv

Three measures with explicit denominators, never combining "deferred" with "passed":
  (a) due-now requirements implemented (PASS or PARTIAL)
  (b) due-now requirements with executable positive AND negative evidence (PASS, test cited, negative cited)
  (c) requirements with a named owner (all rows, including future obligations)
"""
from __future__ import annotations

import csv
import sys
from collections import Counter


def due_now(row: dict) -> bool:
    return row["phase_due"].strip()[:1] in {"0", "1"}


def measures(rows: list[dict]) -> dict:
    now = [r for r in rows if due_now(r)]
    implemented = [r for r in now if r["status"] in {"PASS", "PARTIAL"}]
    tested = [r for r in now if r["status"] == "PASS" and ("tests/" in r["proof"] or "corpus" in r["proof"])
              and r["negative_case"].strip() not in {"", "—"}]
    owned = [r for r in rows if r["owner_component"].strip() not in {"", "unassigned"}]
    return {"rows": len(rows), "due_now": len(now),
            "a_implemented": (len(implemented), len(now)), "b_pos_and_neg_tested": (len(tested), len(now)),
            "c_owned": (len(owned), len(rows)),
            "status_before": dict(Counter(r["status_before"] for r in rows)),
            "status_after": dict(Counter(r["status"] for r in rows)),
            "decisions": dict(Counter(r["decision"] for r in rows))}


def main(argv: list[str]) -> int:
    with open(argv[1], newline="") as fh:
        m = measures(list(csv.DictReader(fh)))
    for k, v in m.items():
        print(f"{k:<22}{f'{v[0]}/{v[1]}' if isinstance(v, tuple) else v}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
