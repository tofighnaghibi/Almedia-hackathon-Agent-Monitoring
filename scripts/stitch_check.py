"""Second step: gates for the stitched rows the simulator already consumes (plan rows with consumed_by).

    python -I scripts/stitch_check.py            # fast gates: chain realism (S5), session fidelity (F1-F6), invariance tests
    python -I scripts/stitch_check.py --s10      # also the session layer's value check (S10 v2, ~30 min)

Writes docs/stitch/checks.json and redraws docs/stitch-pipeline.excalidraw. Rows that are fitted only have no gate yet:
their simulator layer has to be built first (each with preregistered checks).
"""
import json
import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[1]
PY = [sys.executable, "-I"]
GATES = [  # (name, command, rows it covers)
    ("S5 chain realism vs held-out 2018", PY + ["scripts/phase3_fidelity.py", "stitch-check"],
     ["L0-seasonality", "L1-buyers", "L1-sellers", "L3-handling", "L4-traffic", "L5-orders", "L5-delivery", "L6-score", "L6-timing"]),
    ("F1-F6 session fidelity", PY + ["scripts/p1_2_fidelity.py"], ["L1-devices", "L4-sessions", "L4-funnel", "L4-exposure"]),
    ("S7/S3 invariance + contract conformance tests", [sys.executable, "-m", "pytest", "-q", "-x", "tests/test_p1_2_sessions.py",
     "tests/test_phase5_intervention.py", "tests/test_contracts_conformance.py"], ["L1-devices", "L4-sessions", "L4-funnel", "L4-exposure"]),
]
if "--s10" in sys.argv:
    GATES.append(("S10 v2 session-layer value", PY + ["scripts/p1_2_s10_v2.py"], ["L4-sessions", "L4-funnel", "L4-exposure", "L1-devices"]))

results = []
for name, cmd, rows in GATES:
    t = time.perf_counter()
    p = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
    tail = (p.stdout + p.stderr).strip().splitlines()[-3:]
    ok = p.returncode == 0 and not any("FAIL" in l and "PASS" not in l for l in tail[-1:])
    if name.startswith("S5"):                    # decision #6 (accepted by you): F2 weekday share is a documented exception
        out = p.stdout + p.stderr
        fails = [l for l in out.splitlines() if l.strip().startswith("F") and " FAIL " in f" {l} "]
        ok = "9/10" in out and all(l.strip().startswith("F2") for l in fails) or "S5: PASS" in out
        tail = tail + (["accepted: F2 weekday exception (STRATEGY_CHANGES #6)"] if ok and "9/10" in out else [])
    results.append({"gate": name, "pass": ok, "rows": rows, "seconds": round(time.perf_counter() - t, 1), "tail": tail})
    print(f"{'PASS' if ok else 'FAIL'}  {name}  ({results[-1]['seconds']} s)\n      " + "\n      ".join(tail))
(ROOT / "docs/stitch").mkdir(parents=True, exist_ok=True)
(ROOT / "docs/stitch/checks.json").write_text(json.dumps(results, indent=1))
subprocess.run(PY + ["docs/tools/make_stitch_pipeline_excalidraw.py"], cwd=ROOT, capture_output=True)
sys.exit(0 if all(r["pass"] for r in results) else 1)
