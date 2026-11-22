"""Scoreboard: the model as step chooser versus the rule-based fallback, on injected faults.

For each run, one fault is injected into a fresh simulated marketplace two
hours into the live period, and the autonomous monitor runs for --hours
hours: once with the model (needs ANTHROPIC_API_KEY and LLM_MODEL, or
--cache-mode replay) and once with the fallback only. The ground truth is
read only here, to score the result. Per run and arm it prints:

- cause: was the injected fault's root cause found (FOUND / MISSED)
- label: confirmed or uncertain for that cause
- steps: investigation steps for that issue / for all investigations in the run
- wrong: other incidents alerted with a confirmed root cause (a confirmed wrong cause)
- uncertain: alerted incidents whose root cause stayed uncertain
- other: alerted incidents that match no injected fault

    python scripts/llm_scoreboard.py --n 4                    # every fault once, both arms
    python scripts/llm_scoreboard.py --n 4 --arms fallback    # no model, no network
    python scripts/llm_scoreboard.py --n 4 --cache-mode record --cache-dir data/llm_cache/scoreboard
"""

from __future__ import annotations

import argparse
import math
import sys
import time
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from agent.env import load_env  # noqa: E402
from demo.script import score  # noqa: E402
from runtime.runner import AutonomousMonitor, build_llm  # noqa: E402
from simulator.stream import LiveMarketplace  # noqa: E402

FAULTS = ["payment_failure", "bad_search_release", "stock_out", "ad_budget_exhaustion"]
INJECT_AT = 2


def run_one(preset: str, seed: int, hours: int, arm: str, cache_mode: str | None, cache_dir: str | None,
            history_days: int) -> dict[str, Any]:
    live = LiveMarketplace(history_days=history_days, live_days=max(1, math.ceil((hours + INJECT_AT + 1) / 24)),
                           seed=seed)
    mon = AutonomousMonitor(live, use_llm=False if arm == "fallback" else None,
                            llm_cache_mode=cache_mode, llm_cache_dir=cache_dir)
    if arm == "llm" and not mon.llm.enabled:
        raise SystemExit(f"the model arm cannot run: {mon.llm.note}")
    t0 = time.perf_counter()
    mon.run(hours=hours, script={INJECT_AT: [("inject", preset)]})
    sc = score(mon)
    row = next(r for r in sc["scenarios"] if r["kind"] == "fault")
    alerted = [i for i in mon.tracker.issues.values() if i.status != "merged" and i.alerts and i.kind == "incident"]
    hit = mon.tracker.issues.get(row.get("issue") or "")
    st = mon.llm_stats()
    return {
        "preset": preset, "seed": seed, "arm": arm, "cause": row["result"], "root": row.get("root", "-"),
        "label": ("confirmed" if row.get("confirmed") else "uncertain") if row["result"] == "FOUND" else "-",
        "steps_issue": len((hit.finding or {}).get("trail", [])) if hit else 0,
        "steps_all": st["steps_total"], "steps_llm": st["steps_llm"],
        "wrong": sum(1 for i in alerted if i is not hit and (i.root or {}).get("status") == "confirmed"),
        "uncertain": sum(1 for i in alerted if i.root and i.root.get("status") != "confirmed"),
        "other": len(sc["summary"]["other_alerted_issues"]),
        "seconds": round(time.perf_counter() - t0),
        "calls": st["calls"], "tokens": st["tokens_in"] + st["tokens_out"],
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="model vs fallback on injected faults")
    ap.add_argument("--n", type=int, default=4, help="number of fault runs (cycles through the 4 faults)")
    ap.add_argument("--hours", type=int, default=30, help="simulated hours per run (default 30)")
    ap.add_argument("--seed", type=int, default=42, help="seed of the first round; later rounds add 1")
    ap.add_argument("--history-days", type=int, default=28)
    ap.add_argument("--arms", choices=["both", "llm", "fallback"], default="both")
    ap.add_argument("--cache-mode", choices=["live", "record", "replay"], default=None,
                    help="model reply cache for the model arm (default: LLM_CACHE_MODE or live)")
    ap.add_argument("--cache-dir", default=None)
    args = ap.parse_args(argv)
    arms = ["llm", "fallback"] if args.arms == "both" else [args.arms]
    load_env()  # from .env (shell variables win)
    if "llm" in arms:
        setup = build_llm(None, args.cache_mode, args.cache_dir)
        if not setup.enabled:
            print(f"the model arm cannot run: {setup.note} (use --arms fallback, or set the variables)",
                  file=sys.stderr)
            return 1
        print(setup.note)

    rows = []
    for i in range(args.n):
        preset, seed = FAULTS[i % len(FAULTS)], args.seed + i // len(FAULTS)
        for arm in arms:
            r = run_one(preset, seed, args.hours, arm, args.cache_mode, args.cache_dir, args.history_days)
            rows.append(r)
            print(f"{r['preset']:22} seed {r['seed']}  {arm:8}  {r['cause']:6} {r['label']:9} root={r['root']}  "
                  f"steps {r['steps_issue']}/{r['steps_all']} (model {r['steps_llm']})  wrong {r['wrong']}  "
                  f"uncertain {r['uncertain']}  other {r['other']}  [{r['seconds']}s"
                  + (f", {r['calls']} calls, {r['tokens']} tokens]" if arm == "llm" else "]"), flush=True)

    print("\n" + "=" * 88)
    print(f"{'arm':10}{'cause found':>13}{'confirmed':>11}{'steps/issue':>13}{'steps/run':>11}"
          f"{'wrong':>7}{'uncertain':>11}{'other':>7}")
    for arm in arms:
        rs = [r for r in rows if r["arm"] == arm]
        found = sum(r["cause"] == "FOUND" for r in rs)
        print(f"{arm:10}{f'{found}/{len(rs)}':>13}{sum(r['label'] == 'confirmed' for r in rs):>11}"
              f"{sum(r['steps_issue'] for r in rs) / max(1, len(rs)):>13.1f}"
              f"{sum(r['steps_all'] for r in rs) / max(1, len(rs)):>11.1f}"
              f"{sum(r['wrong'] for r in rs):>7}{sum(r['uncertain'] for r in rs):>11}{sum(r['other'] for r in rs):>7}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
