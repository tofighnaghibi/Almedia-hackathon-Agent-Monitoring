"""Live check of the model at the decision points (you run this; it makes real API calls).

1. one real call to Claude
2. the fault-window investigation (DE orders, simulated scenario data) in record mode
3. the same investigation in replay mode (no network)
then, per step: chosen_by, the chosen menu item and the reason; whether the two
trails are identical; how many steps the model chose versus the fallback.

    export ANTHROPIC_API_KEY=...        # read by the anthropic SDK; never printed
    export LLM_MODEL=claude-opus-5-5    # model for choosing steps
    python -m simulator --scenarios all # once, if data/generated/scenarios is missing
    python scripts/live_check_llm.py [--cache-dir DIR]
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd  # noqa: E402

from agent.chooser import LLMChooser  # noqa: E402
from agent.env import load_env  # noqa: E402
from agent.llm_client import ClaudeClient, LLMCache, LLMUnavailable  # noqa: E402
from agent.loop import Limits, RealInvestigationTools, investigate  # noqa: E402
from graph.bridge import run_checks  # noqa: E402
from graph.engine import MetricEngine  # noqa: E402
from graph.metric_graph import MetricGraph  # noqa: E402
from graph.registry import MetricRegistry  # noqa: E402
from monitoring.data_quality import check_dq  # noqa: E402
from monitoring.detect import Detector  # noqa: E402
from monitoring.event_calendar import EventCalendar  # noqa: E402

DATA = Path("data/generated/scenarios")
WINDOW = ("2026-10-22T10:00:00Z", "2026-10-23T10:00:00Z")  # the payment-failure window in the scenario data


def setup(data_dir: Path = DATA) -> dict[str, Any]:
    """The fault-window anomaly and everything the investigation needs."""
    reg = MetricRegistry.load()
    eng = MetricEngine.from_dir(data_dir, reg)
    mg = MetricGraph.load("graph/seed_graph.yaml")
    for name in reg.names():
        mg.graph.add_node(name)
    run_checks(eng, mg)  # confirm formula/breakdown edges on the data
    mh = pd.read_csv(data_dir / "marketplace_hourly.csv", parse_dates=["timestamp"])
    cal = EventCalendar.from_dataframe(pd.read_csv(data_dir / "events.csv"))
    det = Detector(eng)
    eps = [e for e in det.detect(["orders"], by=["country"], start=WINDOW[0], end=WINDOW[1])
           if e.segment.get("country") == "DE"]
    if not eps:
        raise RuntimeError("no DE orders anomaly found in the scenario data")
    slices = {d: sorted(mh[d].unique()) for d in ("country", "platform", "category")}

    def dq(s, e, seg):
        return check_dq(mh, s, e, now=e, expected_slices=slices, segment=seg)

    return {"anomaly": eps[0], "graph": mg, "tools": RealInvestigationTools(eng, mg, detector=det),
            "calendar": cal, "dq": dq}


def run_check(client: Any, cache_dir: Path, ctx: dict[str, Any]) -> bool:
    """Record with ``client``, replay without it; print the comparison. True if identical."""
    def run(chooser):
        return investigate(ctx["anomaly"], ctx["graph"], ctx["tools"], ctx["calendar"], ctx["dq"],
                           chooser=chooser, limits=Limits(), now=pd.Timestamp(WINDOW[1]))

    rec = run(LLMChooser(client=client, cache=LLMCache("record", cache_dir)))
    rep = run(LLMChooser(client=None, cache=LLMCache("replay", cache_dir), model_name=client.model))

    print(f"\n2) record mode: {len(rec['trail'])} steps, stop={rec['stop_reason']}, "
          f"top hypothesis {rec['hypotheses'][0]['node']} ({rec['hypotheses'][0]['status']})")
    for t in rec["trail"]:
        print(f"   #{t['step']:<2} {t['chosen_by']:8} menu {t.get('menu_number')!s:>4}  {t['action']}: {t['query']}")
        if t["chosen_by"] == "llm":
            print(f"        reason: {t['reason']}")
        else:
            print(f"        fallback: {t.get('fallback_cause')}")
    print(f"\n3) replay mode (no network): {len(rep['trail'])} steps, stop={rep['stop_reason']}")
    same_trail = rec["trail"] == rep["trail"]
    same_all = json.dumps(rec, sort_keys=True, default=str) == json.dumps(rep, sort_keys=True, default=str)
    n_llm = sum(t["chosen_by"] == "llm" for t in rec["trail"])
    print(f"\n4) trails identical: {same_trail}   (whole finding identical: {same_all})")
    print(f"5) steps chosen by the model: {n_llm}; by the fallback: {len(rec['trail']) - n_llm}"
          f"   (model-chosen steps: {n_llm} of {len(rec['trail'])})")
    usage = getattr(client, "usage", None)
    if usage is not None:
        print(f"   API calls {usage.calls} ({usage.failures} failed), tokens in/out {usage.tokens_in}/{usage.tokens_out}")
    return same_trail


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--cache-dir", default=None, help="keep the recorded replies here (default: a temp folder)")
    args = ap.parse_args(argv)
    load_env()  # from .env (shell variables win)

    print("0) environment: ANTHROPIC_API_KEY " + ("set" if os.environ.get("ANTHROPIC_API_KEY", "").strip() else "MISSING")
          + f", LLM_MODEL={os.environ.get('LLM_MODEL') or 'MISSING'}"
          + f", LLM_REPORT_MODEL={os.environ.get('LLM_REPORT_MODEL') or '(same as LLM_MODEL)'}")
    try:
        client = ClaudeClient()
    except LLMUnavailable as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    try:
        print("1) one real call...")
        reply = client.complete_json('Reply with one JSON object only.', 'Reply with {"ok": true}')
        print(f"   reply: {reply}  (tokens in/out {client.usage.tokens_in}/{client.usage.tokens_out})")
    except Exception as exc:  # noqa: BLE001 - report and stop
        print(f"ERROR: the call failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    if not DATA.exists():
        print(f"ERROR: {DATA} not found; run: python -m simulator --scenarios all", file=sys.stderr)
        return 1
    ctx = setup()
    if args.cache_dir:
        ok = run_check(client, Path(args.cache_dir), ctx)
    else:
        with tempfile.TemporaryDirectory() as tmp:
            ok = run_check(client, Path(tmp), ctx)
    return 0 if ok else 2


if __name__ == "__main__":
    raise SystemExit(main())
