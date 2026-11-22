"""Argus as an MCP server (Model Context Protocol, stdio transport) so external agents can use the twin.

Run from backend/:  python -m argus.mcp_server
Dependency-free: newline-delimited JSON-RPC 2.0 on stdin/stdout (logs go to stderr).

Tools
  get_world_state                    globe state: clock, countries off "ok", incidents (issue cards), explained changes
  explain_incident(id)               cause, proof (counterfactual), decision trail, fixes, engineer report
  simulate_fix(id, fix)              7-day Monte Carlo rollout of one fix vs doing nothing (nothing is applied)
  what_if(country, change, ...)      sandbox copy of the world: inject a change, run N hours, see what Argus would do
  advance_time(hours)                step the world (the agent runs every simulated hour)
  what_if_launch(country, users?)    forecast launching in a new country: margin per 1,000 users, offers, rewards
  run_demo_scene(scene)              jump to a demo scene (start, promo, bot_farm, ...)
Approving fixes is deliberately not exposed: applying changes stays human-on-the-loop (web UI / REST API).

Environment: ARGUS_SEED (default 42), ARGUS_MCP_SCENE (optional demo scene to start from), LLM_* as for the API.
"""
from __future__ import annotations

import copy
import json
import os
import sys
import traceback

from . import config as cfg
from . import llm

SERVER_INFO = {"name": "argus-twin", "version": "0.1.0"}
PROTOCOL_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26", "2024-11-05"]
CHANGES = ["promo", "bot_farm", "tracking_break", "data_delay"]

TOOLS = [
    {"name": "get_world_state",
     "description": "Current state of the simulated rewarded-ad marketplace as Argus sees it: clock, countries that are "
                    "not healthy, open incidents with plain-language issue cards, and changes explained by the calendar.",
     "inputSchema": {"type": "object", "properties": {}, "additionalProperties": False}},
    {"name": "explain_incident",
     "description": "Explain one incident: root cause, counterfactual proof (explained share), decision trail, "
                    "hypotheses, recommended fix with 7-day margin impact, and the engineer report.",
     "inputSchema": {"type": "object", "properties": {"id": {"type": "string", "description": "Incident id, e.g. inc-1"}},
                     "required": ["id"], "additionalProperties": False}},
    {"name": "simulate_fix",
     "description": "Fast-forward one fix for an incident 7 days in the twin (40 Monte Carlo paths) and compare it with "
                    "doing nothing. Read-only: nothing is applied.",
     "inputSchema": {"type": "object", "properties": {
         "id": {"type": "string", "description": "Incident id"},
         "fix": {"type": "string", "description": "Fix id from explain_incident (e.g. verify_new_accounts, none)"}},
         "required": ["id", "fix"], "additionalProperties": False}},
    {"name": "what_if",
     "description": "Sandbox: copy the world, inject a change in one country, run it forward and report what Argus "
                    "detects and how margin moves vs the same hours without the change. The real world is untouched.",
     "inputSchema": {"type": "object", "properties": {
         "country": {"type": "string", "description": "ISO code, e.g. CA, DE, GB"},
         "change": {"type": "string", "enum": CHANGES},
         "platform": {"type": "string", "enum": ["iOS", "Android"]},
         "hours": {"type": "integer", "minimum": 1, "maximum": 72, "default": 12}},
         "required": ["country", "change"], "additionalProperties": False}},
    {"name": "what_if_launch",
     "description": "Forecast launching the marketplace in a country Argus does not watch yet: transfers taste, "
                    "activity and payout level from the most similar watched countries and predicts margin per 1,000 "
                    "daily users (p10-p90), the best offer mix and reward level, and fraud risk; the total at a given "
                    "or assumed user count over 4 weeks; plus a hold-out backtest on all 20 watched countries. Read-only.",
     "inputSchema": {"type": "object", "properties": {
         "country": {"type": "string", "description": "ISO code of the launch market, e.g. AR (Argentina)",
                     "default": "AR"},
         "users": {"type": "integer", "minimum": 1000, "description": "Expected daily users after 4 weeks "
                   "(optional; default is an assumption from similar countries)"}},
         "additionalProperties": False}},
    {"name": "advance_time",
     "description": "Advance the simulated world by N hours; Argus runs its loop every hour.",
     "inputSchema": {"type": "object", "properties": {"hours": {"type": "integer", "minimum": 1, "maximum": 168}},
                     "required": ["hours"], "additionalProperties": False}},
    {"name": "run_demo_scene",
     "description": "Jump to a deterministic demo scene: start, promo, bot_farm, investigate, approve, verify, "
                    "new_metric, tracking_break (earlier scenes run first).",
     "inputSchema": {"type": "object", "properties": {"scene": {"type": "string"}},
                     "required": ["scene"], "additionalProperties": False}},
]


class ToolError(Exception):
    pass


class ArgusTools:
    def __init__(self) -> None:
        self._world = None

    @property
    def world(self):
        if self._world is None:
            from .world import World
            self._world = World(seed=int(os.environ.get("ARGUS_SEED", 42)))
            scene = os.environ.get("ARGUS_MCP_SCENE")
            if scene:
                self._world.demo(scene)
        return self._world

    def _incident(self, inc_id: str) -> dict:
        inc = self.world.agent.get_incident(str(inc_id))
        if inc is None:
            raise ToolError(f"unknown incident '{inc_id}'; call get_world_state for ids")
        return inc

    # ---------------------------------------------------------------- tools
    def get_world_state(self) -> dict:
        st = self.world.agent.state()
        return {"clock": st["clock"]["label"],
                "countries_not_ok": [{k: c[k] for k in ("iso", "name", "status", "health_z")}
                                     for c in st["countries"] if c["status"] != "ok"],
                "incidents": st["incidents"], "explained": st["explained"], "attention": st["attention"]}

    def explain_incident(self, id: str) -> dict:  # noqa: A002 - MCP argument name
        agent = self.world.agent
        d = agent.incident_detail(self._incident(id))
        cf = d["counterfactual"]
        return {
            "card": d["card"], "status": d["status"], "severity": d["severity"], "root_mechanism": d["root"],
            "hypotheses": d["hypotheses"],
            "counterfactual": None if cf is None else {"label": cf["label"], "explained_share": cf["explained_share"]},
            "left_zone_at": d["left_zone_at"], "series": d["series_meta"],
            "trail": d["trail"],
            "fixes": [{k: f[k] for k in ("id", "name", "description", "delta_p50_usd", "events", "advertiser_risk")}
                      for f in d["fixes"]],
            "recommended": d["recommended"], "applied": d["applied"], "verification": d["verification"],
            "engineer_report": d["reports"]["engineer"],
        }

    def simulate_fix(self, id: str, fix: str) -> dict:  # noqa: A002
        w = self.world
        inc = self._incident(id)
        ids = [f["id"] for f in inc["fixes"]]
        if fix not in ids:
            raise ToolError(f"fix must be one of {ids}")
        now = w.view.t - 1
        ctx = dict(inc["ctx"], elapsed=inc["ctx"]["elapsed"] + max(now - inc["detected_at"], 0))
        opts = ["none"] + ([fix] if fix != "none" else [])
        res = w.twin.fast_forward(inc["c"], opts, ctx, now, seed=w.agent.seed)[-1]
        rnd = lambda xs: [round(x) for x in xs]  # noqa: E731
        return {"incident": inc["id"], "fix": fix, "from_hour": now, "days": res["days"],
                "cumulative_margin_usd": {"p10": rnd(res["p10"]), "p50": rnd(res["p50"]), "p90": rnd(res["p90"])},
                "vs_do_nothing_usd": {q: rnd(v) for q, v in res["delta_daily"].items()},
                "delta_p50_usd_7d": round(res["delta_p50_usd"]),
                "events": res["events"], "applied": False,
                "note": "Read-only rollout in the twin; approving a fix is a human action in the Argus UI."}

    def what_if(self, country: str, change: str, platform: str | None = None, hours: int = 12) -> dict:
        from .world import taste_model
        if change not in CHANGES:
            raise ToolError(f"change must be one of {CHANGES}")
        hours = int(max(1, min(int(hours), 72)))
        taste = taste_model()                               # share the (read-only) taste model, copy the rest;
        base = copy.deepcopy(self.world, {id(taste): taste})  # each copy needs its own fresh memo
        sand = copy.deepcopy(self.world, {id(taste): taste})
        try:
            injected = sand.inject(change, country, platform)
        except ValueError as e:
            raise ToolError(str(e)) from e
        iso = injected["country"]
        c = cfg.ISO_INDEX[iso]
        before = {i["id"] for i in sand.agent.incidents}
        t0 = sand.reality.t
        base.step(hours)
        sand.step(hours)
        margin = lambda w: sum(float((r["revenue"][c] - r["rewards"][c]).sum()) for r in w.view.observed[t0:])  # noqa: E731
        new = [sand.agent.incident_summary(i) for i in sand.agent.incidents if i["id"] not in before]
        return {"sandbox": True, "change": injected, "hours": hours,
                "country_status_after": sand.agent.country_status(c),
                "argus_detected": [{"title": i["title"], "severity": i["severity"], "card": i["card"]} for i in new],
                "explained": [e for e in sand.agent.explained if e["country"] == iso and e["t"] >= t0],
                "margin_vs_no_change_usd": round(margin(sand) - margin(base)),
                "note": "Sandbox copy; the session world is unchanged."}

    def what_if_launch(self, country: str = "AR", users: int | None = None) -> dict:
        from . import launch
        incidents = [{"country": i["country"], "root": i["root"], "title": i["title"]} for i in self.world.agent.incidents]
        try:
            return launch.launch(country, self.world.seed, incidents, users)
        except ValueError as e:
            raise ToolError(str(e)) from e

    def advance_time(self, hours: int) -> dict:
        hours = int(max(1, min(int(hours), 168)))
        self.world.step(hours)
        return self.get_world_state()

    def run_demo_scene(self, scene: str) -> dict:
        from .world import SCENES
        if scene not in SCENES:
            raise ToolError(f"scene must be one of {SCENES}")
        out = self.world.demo(scene)
        return {"scene": scene, "incident_id": out.get("incident_id"), "state": self.get_world_state()}


# -------------------------------------------------------------------- JSON-RPC over stdio
def handle(msg: dict, tools: ArgusTools) -> dict | None:
    """Handle one JSON-RPC message; returns the response, or None for notifications."""
    mid = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}
    if mid is None:                                   # notification (e.g. notifications/initialized)
        return None

    def ok(result: dict) -> dict:
        return {"jsonrpc": "2.0", "id": mid, "result": result}

    def err(code: int, message: str) -> dict:
        return {"jsonrpc": "2.0", "id": mid, "error": {"code": code, "message": message}}

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[1]
        return ok({"protocolVersion": version, "capabilities": {"tools": {"listChanged": False}},
                   "serverInfo": SERVER_INFO,
                   "instructions": "Argus: a causal digital twin of a simulated rewarded-ad marketplace. Start with "
                                   "get_world_state or run_demo_scene('bot_farm'), then explain_incident."})
    if method == "ping":
        return ok({})
    if method == "tools/list":
        return ok({"tools": TOOLS})
    if method == "tools/call":
        name, args = params.get("name"), params.get("arguments") or {}
        fn = getattr(tools, name, None) if name in {t["name"] for t in TOOLS} else None
        if fn is None:
            return err(-32602, f"unknown tool '{name}'")
        try:
            result = fn(**args)
            return ok({"content": [{"type": "text", "text": json.dumps(result, default=str)}],
                       "structuredContent": json.loads(json.dumps(result, default=str)), "isError": False})
        except (ToolError, TypeError, ValueError) as e:
            return ok({"content": [{"type": "text", "text": f"Error: {e}"}], "isError": True})
    return err(-32601, f"method not found: {method}")


def main() -> int:
    llm.load_dotenv()
    tools = ArgusTools()
    print(f"argus MCP server ready (stdio), LLM: {llm.model() or 'templates'}", file=sys.stderr, flush=True)
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except json.JSONDecodeError:
            resp = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "parse error"}}
        else:
            try:
                resp = handle(msg, tools)
            except Exception as e:  # noqa: BLE001 - never crash the server on one bad request
                traceback.print_exc(file=sys.stderr)
                resp = {"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32603, "message": str(e)}}
        if resp is not None:
            sys.stdout.write(json.dumps(resp, default=str) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
