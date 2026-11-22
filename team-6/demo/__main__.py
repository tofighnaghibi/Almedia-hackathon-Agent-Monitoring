"""Run the demo.

    python -m demo                  # live dashboard: http://127.0.0.1:8050
    python -m demo --headless       # same scripted run in the terminal + scorecard
    python -m demo --manual         # dashboard without the script: inject from the page
    python -m demo --no-llm         # rules only (the model is on by itself when ANTHROPIC_API_KEY
                                    # and LLM_MODEL are set)
"""

from __future__ import annotations

import argparse
import sys
import time

from agent.env import load_env
from runtime.runner import AutonomousMonitor
from simulator.stream import LiveMarketplace

from .script import TIMELINE, describe_timeline, save_run, score, scorecard_text

SHOW = {"alert", "issue", "investigate", "triage", "inject", "metric", "watch", "system"}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Eye of the System: autonomous monitoring demo.")
    ap.add_argument("--headless", action="store_true", help="run in the terminal and print a scorecard")
    ap.add_argument("--manual", action="store_true", help="no scripted timeline (inject from the dashboard)")
    ap.add_argument("--days", type=int, default=6, help="simulated live days (default 6)")
    ap.add_argument("--history-days", type=int, default=28)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--speed", type=float, default=0.0, help="dashboard: extra seconds between hours (default 0)")
    ap.add_argument("--port", type=int, default=8050)
    ap.add_argument("--out", default="data/generated/demo_run", help="where reports and the scorecard go")
    ap.add_argument("--llm", action="store_true",
                    help="require the model (default: on automatically when ANTHROPIC_API_KEY and LLM_MODEL are set)")
    ap.add_argument("--no-llm", action="store_true", help="rule-based chooser and template report text only")
    ap.add_argument("--llm-cache-mode", choices=["live", "record", "replay"], default=None,
                    help="model reply cache (default: LLM_CACHE_MODE or live); replay needs no key or network")
    ap.add_argument("--llm-cache-dir", default=None, help="cache folder (default: LLM_CACHE_DIR or data/llm_cache)")
    ap.add_argument("--quiet", action="store_true", help="headless: only alerts and the scorecard")
    ap.add_argument("--no-browser", action="store_true", help="dashboard: don't open a browser tab")
    args = ap.parse_args(argv)
    load_env()  # ANTHROPIC_API_KEY, LLM_MODEL, ... from .env (shell variables win)
    use_llm =False if args.no_llm else (True if args.llm else None)
    llm_opts = {"llm_cache_mode": args.llm_cache_mode, "llm_cache_dir": args.llm_cache_dir}
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    print("building the marketplace (history + live period)...", flush=True)
    live = LiveMarketplace(history_days=args.history_days, live_days=args.days, seed=args.seed)
    script = None if args.manual else TIMELINE
    if script:
        print("scripted timeline (the monitor is not told any of this):")
        for line in describe_timeline(script):
            print("   " + line)

    if args.headless:
        show = {"alert"} if args.quiet else SHOW

        def printer(item):
            if item.kind in show and (item.level != "info" or item.kind in ("inject", "metric", "system", "alert")):
                print(f"{item.time[5:16]}  {item.kind:11} {item.text}", flush=True)

        mon = AutonomousMonitor(live, use_llm=use_llm, on_feed=printer, **llm_opts)
        t0 = time.perf_counter()
        mon.run(script=script)
        sc = score(mon)
        out = save_run(mon, args.out, sc)
        print(scorecard_text(sc))
        st = mon.llm_stats()
        print(f"\nLLM: {st['note']}")
        if st["enabled"]:
            print(f"     model-chosen steps {st['steps_llm']} of {st['steps_total']}; step fallbacks "
                  f"{st['step_fallbacks']} (stop requests ignored {st['stop_requests_ignored']}); API calls "
                  f"{st['calls']} ({st['call_failures']} failed), tokens in/out {st['tokens_in']}/{st['tokens_out']}; "
                  f"report texts by the model {st['reports_by_llm']}, rejected {st['reports_rejected']}")
        print(f"\nfinished {mon.state()['hours_done']} simulated hours in {time.perf_counter() - t0:.0f}s; "
              f"reports, alerts and the scorecard are in {out}")
        return 0

    from .server import serve

    serve(live, script=script, port=args.port, delay=args.speed, use_llm=use_llm, out_dir=args.out,
          open_browser=not args.no_browser, **llm_opts)
    return 0


if __name__ == "__main__":
    sys.exit(main())
