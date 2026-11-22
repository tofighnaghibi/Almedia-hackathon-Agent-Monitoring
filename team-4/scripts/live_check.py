"""Unattended live check: the real processes, a simulated day and a half, every kind of event, nobody watching.

    python -m scripts.live_check --run runs/live-check [--speed 1.0] [--narrate]

What it does, in its own new folder (it never touches data/market.db, incidents/ or incoming_metrics/):
a fresh 28-day backfill; `python -m brain.run` live (with the new-metric folder and, with --narrate, the
language-model narration); `python -m sim.live` moving the clock one simulated hour every --speed seconds;
and, at fixed simulated hours, what a person would click in the console: four faults through
`python -m sim.inject ... --now` and their rollbacks through `end --id`, a promo and a late source, and the
console's refund_rate definition dropped into the incoming folder.

What it checks at the end (exit code 1 when one fails): the monitor wrote no traceback; one incident per
fault, in the injected place, with the injected cause supported, resolved after its rollback, and nothing
else (sim.score: detected, localized, right cause, no false alarm); the promo explained and silent; the late
hours held and released; the new metric validated. It prints the summary and writes it to summary.json.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
HOURS = 36
# simulated hour after the backfill -> what a person does at the console
PLAN = {
    2: [("payment_failure", "--country", "DE", "--platform", "Android", "--severity", "0.4")],
    4: [("promo", "FR", 12)],
    5: [("new metric",)],
    8: [("end", "payment_failure")],
    10: [("data_delay", "payments", 3)],
    12: [("stockout_surge", "--category", "electronics")],
    15: [("search_regression", "--country", "ES", "--severity", "0.5")],
    18: [("end", "stockout_surge")],
    20: [("end", "search_regression")],
    22: [("ad_budget_exhausted", "--country", "PL")],
    28: [("end", "ad_budget_exhausted")],
}


def page_definition() -> str:
    """The definition the console's "Add refund_rate" button posts, taken from the page itself."""
    html = (ROOT / "ui/static/index.html").read_text(encoding="utf-8")
    return re.search(r"var y = '([^']+)';", html).group(1).replace("\\n", "\n")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--run", type=Path, required=True, help="a new folder for this run")
    ap.add_argument("--speed", type=float, default=1.0, help="seconds per simulated hour")
    ap.add_argument("--narrate", action="store_true", help="let the monitor narrate its incidents (needs API credit)")
    a = ap.parse_args(argv)
    if a.run.exists() and any(a.run.iterdir()):
        ap.error(f"{a.run} is not empty; choose a new folder")
    run = a.run.resolve()
    (run / "incoming").mkdir(parents=True, exist_ok=True)
    db = run / "market.db"
    env = dict(os.environ, PYTHONUTF8="1", PYTHONUNBUFFERED="1")
    py = sys.executable

    def call(*args):
        r = subprocess.run([py, *args], cwd=ROOT, env=env, capture_output=True, text=True, timeout=120)
        if r.returncode != 0:
            raise SystemExit(f"{' '.join(args)} failed: {r.stderr[-600:]}")
        return r.stdout.strip()

    call("-m", "sim.run", "--backfill", "28d", "--db", str(db), "--force")
    con = sqlite3.connect(db, timeout=30)
    base = pd.Timestamp(con.execute("SELECT MAX(ts) FROM facts_hourly").fetchone()[0])
    logs = {name: open(run / f"{name}.log", "w", encoding="utf-8") for name in ("brain", "sim")}
    brain = subprocess.Popen([py, "-m", "brain.run", "--db", str(db), "--incidents", str(run / "incidents"),
                              "--incoming", str(run / "incoming"), "--graph-out", str(run / "graph.json"), "--fresh",
                              "--poll", "0.25"] + (["--narrate"] if a.narrate else []),
                             cwd=ROOT, env=env, stdout=logs["brain"], stderr=subprocess.STDOUT)
    time.sleep(6)                                # the monitor reads its history before the clock moves
    clock = subprocess.Popen([py, "-m", "sim.live", "--db", str(db), "--speed", str(a.speed), "--hours", str(HOURS)],
                             cwd=ROOT, env=env, stdout=logs["sim"], stderr=subprocess.STDOUT)
    done, actions, t0 = set(), [], time.time()
    try:
        while clock.poll() is None and time.time() - t0 < HOURS * a.speed * 4 + 120:
            try:
                now = pd.Timestamp(con.execute("SELECT MAX(ts) FROM facts_hourly").fetchone()[0])
            except sqlite3.OperationalError:     # the clock is writing
                time.sleep(0.1)
                continue
            h = int((now - base) / pd.Timedelta(hours=1))
            for hour in sorted(k for k in PLAN if k <= h and k not in done):
                done.add(hour)
                for kind, *args in PLAN[hour]:
                    if kind == "new metric":
                        (run / "incoming" / "refund_rate.yaml").write_text(page_definition(), encoding="utf-8")
                        out = "refund_rate.yaml dropped"
                    elif kind == "end":
                        rid = con.execute("SELECT MAX(rowid) FROM ground_truth WHERE kind = ?", (args[0],)).fetchone()[0]
                        out = call("-m", "sim.inject", "end", "--id", str(rid), "--db", str(db))
                    elif kind in ("promo", "data_delay"):
                        where, length = args
                        flag = "--country" if kind == "promo" else "--source"
                        out = call("-m", "sim.inject", kind, flag, where, "--db", str(db),
                                   "--start", (now + pd.Timedelta(hours=1)).strftime("%Y-%m-%dT%H:00:00Z"),
                                   "--end", (now + pd.Timedelta(hours=1 + length)).strftime("%Y-%m-%dT%H:00:00Z"))
                    else:
                        out = call("-m", "sim.inject", kind, *args, "--now", "--db", str(db))
                    actions.append({"hour": h, "action": kind, "args": [str(x) for x in args], "result": out[-160:]})
                    print(f"hour {h}: {kind} {' '.join(map(str, args))}", flush=True)
            time.sleep(0.1)
        clock.wait(timeout=60)
        time.sleep(8)                            # the monitor's last ticks
    finally:
        for p in (clock, brain):
            if p.poll() is None:
                p.terminate()
                p.wait(timeout=30)
        for f in logs.values():
            f.close()
        con.close()

    sys.path.insert(0, str(ROOT))
    from sim import score as sc
    text = (run / "brain.log").read_text(encoding="utf-8", errors="replace")
    records = [json.loads(p.read_text(encoding="utf-8")) for p in sorted((run / "incidents").glob("*.json"))]
    incidents = [r for r in records if r.get("status") in ("open", "ongoing", "resolved")]
    events, start, end = sc.load_truth(db)
    days = (end - start + pd.Timedelta(hours=1)) / pd.Timedelta(days=1)
    result = sc.score(events, incidents, end, days)
    answer = run / "incoming" / "refund_rate.result.json"
    summary = {
        "tracebacks": text.count("Traceback"),
        "narration": ([l for l in text.splitlines() if l.startswith("narration:")] or ["narration: not asked"])[-1],   # the last word: a refusal comes after the start line
        "incidents": [{"id": r["id"], "status": r["status"], "moved": (r.get("drivers") or [{}])[0].get("metric"),
                       "segment": r["segment"], "first_seen": r["first_seen"],
                       "cause": next((h["cause"] for h in r.get("hypotheses", []) if h.get("status") == "supported"), None)}
                      for r in incidents],
        "explained": [r["id"] for r in records if r.get("status") == "explained"],
        "pending_left": [r["id"] for r in records if r.get("status") == "pending"],
        "hours_held": sum(" held " in l for l in text.splitlines()),
        "new_metric": json.loads(answer.read_text(encoding="utf-8"))["status"] if answer.exists() else None,
        "score": {k: result[k] for k in ("faults", "detected", "localized_rate", "correct_cause_rate", "false_alarms",
                                         "innocents", "innocents_silent")},
        "actions": actions,
    }
    checks = {
        "no traceback": summary["tracebacks"] == 0,
        "every fault found once, in place, with its cause": (result["detected"] == result["faults"] == len(incidents)
                                                             and result["localized_rate"] == 1.0
                                                             and result["correct_cause_rate"] == 1.0),
        "no false alarm": result["false_alarms"] == 0,
        "every incident resolved after its rollback": all(r["status"] == "resolved" for r in incidents),
        "innocent events silent": result["innocents_silent"] == result["innocents"],
        "promo explained": bool(summary["explained"]),
        "late hours held and released": summary["hours_held"] > 0 and not summary["pending_left"],
        "new metric validated": summary["new_metric"] == "validated",
    }
    summary["checks"] = checks
    (run / "summary.json").write_text(json.dumps(summary, indent=2, default=str) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, default=str))
    failed = [k for k, ok in checks.items() if not ok]
    print("all checks passed" if not failed else "FAILED: " + "; ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
