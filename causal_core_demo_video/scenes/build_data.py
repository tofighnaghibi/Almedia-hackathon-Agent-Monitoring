"""Pack a run's outputs into scenes/<uc>_data.js (window.DATA = {...}) so file:// scenes can read them without fetch.

    python3 scenes/build_data.py uc1
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE.parent / "outputs"


def uc1() -> dict:
    d = OUT / "uc1"
    j = lambda name: json.loads((d / name).read_text())
    events = [json.loads(l) for l in (d / "events.jsonl").read_text().splitlines() if l.strip()]
    actions = j("actions.json")
    return {
        "backdrop": j("crosscheck.json"),
        "simulated": {"EXP-10": "Onboarding (iOS)", "EXP-11": "Paywall copy B", "EXP-12": "Lesson screen (web)"},
        "events": [e for e in events if e["kind"] not in ("wake", "check")],
        "wakeups": sum(e["kind"] == "wake" for e in events),
        "actions": [a for a in actions if "proposal" in a],
        "refused": next(a["refused_example"] for a in actions if "refused_example" in a),
        "srm": j("srm_EXP-10.json"),
        "monitor": j("monitor_EXP-11.json"),
        "spread": j("alarm_day_spread_EXP-11.json"),
        "dq": j("dq_EXP-12.json"),
        "ship": j("ship_request.json") | {"report": None},
        "truth": j("truth.json"),
        "timings": j("timings.json"),
    }


def uc2() -> dict:
    import re
    import statistics
    from datetime import datetime
    d = OUT / "uc2"
    j = lambda name: json.loads((d / name).read_text())
    ev, tr, truth = j("events.json"), j("traces.json"), j("truth.json")
    T = lambda s: datetime.fromisoformat(s.replace("Z", "+00:00"))
    first = lambda kind: next((e for e in ev if e["kind"] == kind), None)
    alarm, prop, exe = first("incident"), first("proposal"), first("execution")
    diag = first("diagnosis")["detail"]
    m = re.search(r"attempts (\d+) vs usual (\d+)", diag)
    hm = lambda sec: f"{int(sec // 3600)}h {int(sec % 3600 // 60):02d}m" if sec >= 3600 else f"{round(sec / 60)} min"
    ff = truth.get("first_failure_sim")
    x = prop["expected"]
    close = {"detect": hm((T(alarm["sim"]) - T(ff)).total_seconds()) if ff else "—",
             "fix": hm((T(exe["sim"]) - T(alarm["sim"])).total_seconds()),
             "tasks": len(tr), "median_ms": round(statistics.median(t["wall_ms"] for t in tr), 1),
             "attempts": int(m.group(1)), "usual": int(m.group(2)),
             "exp": x["mean"], "lo": x["lower"], "hi": x["upper"],
             "failed_before": truth.get("failed_before_fix"), "failed_after": truth.get("failed_after_fix"),
             "saved": truth.get("orders_saved_vs_no_fix_expected")}
    return {"close": close, "world": j("world.json"), "events": ev, "truth": truth}


if __name__ == "__main__":
    uc = sys.argv[1] if len(sys.argv) > 1 else "uc1"
    data = {"uc1": uc1, "uc2": uc2}[uc]()
    (HERE / f"{uc}_data.js").write_text("window.DATA = " + json.dumps(data, separators=(",", ":")) + ";\n")
    print(f"wrote scenes/{uc}_data.js ({(HERE / f'{uc}_data.js').stat().st_size:,} bytes)")
