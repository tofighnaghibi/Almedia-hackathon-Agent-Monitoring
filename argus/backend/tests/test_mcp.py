"""The MCP server speaks JSON-RPC over stdio: run it as a subprocess like an MCP client would."""
import json
import os
import subprocess
import sys
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]


def test_mcp_server_stdio_roundtrip():
    env = {**os.environ, "ARGUS_NO_DOTENV": "1", "PYTHONPATH": str(BACKEND)}
    for k in ("LLM_BASE_URL", "LLM_API_KEY", "LLM_MODEL"):
        env.pop(k, None)
    msgs = [
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "test", "version": "0"}}},
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {"name": "run_demo_scene", "arguments": {"scene": "bot_farm"}}},
        {"jsonrpc": "2.0", "id": 4, "method": "tools/call", "params": {"name": "explain_incident", "arguments": {"id": "inc-1"}}},
        {"jsonrpc": "2.0", "id": 5, "method": "tools/call", "params": {"name": "simulate_fix", "arguments": {"id": "inc-1", "fix": "verify_new_accounts"}}},
        {"jsonrpc": "2.0", "id": 6, "method": "tools/call", "params": {"name": "what_if", "arguments": {"country": "GB", "change": "tracking_break", "platform": "iOS", "hours": 6}}},
        {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": {"name": "get_world_state", "arguments": {}}},
        {"jsonrpc": "2.0", "id": 8, "method": "tools/call", "params": {"name": "explain_incident", "arguments": {"id": "inc-99"}}},
        {"jsonrpc": "2.0", "id": 9, "method": "nope"},
        {"jsonrpc": "2.0", "id": 10, "method": "tools/call", "params": {"name": "what_if_launch", "arguments": {"country": "AR"}}},
    ]
    proc = subprocess.run([sys.executable, "-m", "argus.mcp_server"], input="\n".join(json.dumps(m) for m in msgs) + "\n",
                          capture_output=True, text=True, env=env, cwd=BACKEND, timeout=180)
    out = {r["id"]: r for r in map(json.loads, proc.stdout.strip().splitlines())}
    assert set(out) == {1, 2, 3, 4, 5, 6, 7, 8, 9, 10}                    # no reply to the notification
    assert out[1]["result"]["protocolVersion"] == "2025-06-18" and "tools" in out[1]["result"]["capabilities"]
    names = {t["name"] for t in out[2]["result"]["tools"]}
    assert {"get_world_state", "explain_incident", "simulate_fix", "what_if", "what_if_launch"} <= names
    assert all("inputSchema" in t for t in out[2]["result"]["tools"])
    assert out[3]["result"]["structuredContent"]["incident_id"] == "inc-1"
    ex = out[4]["result"]["structuredContent"]
    assert ex["root_mechanism"] == "new_accounts" and ex["hypotheses"][0]["verdict"] == "supported"
    sim = out[5]["result"]["structuredContent"]
    assert sim["applied"] is False and len(sim["vs_do_nothing_usd"]["p50"]) == 7 and sim["delta_p50_usd_7d"] > 0
    wi = out[6]["result"]["structuredContent"]
    assert wi["sandbox"] is True and wi["margin_vs_no_change_usd"] < 0
    st = out[7]["result"]["structuredContent"]
    assert [i["country"] for i in st["incidents"]] == ["CA"]          # what_if did not touch the session world
    assert out[8]["result"]["isError"] is True
    assert out[9]["error"]["code"] == -32601
    la = out[10]["result"]["structuredContent"]
    assert la["country"]["iso"] == "AR" and la["backtest"]["n"] == 20 and la["users"]["assumed"] is True
    assert la["fraud_risk"]["farms_per_quarter_neighbours"]["MX"] >= 1.8      # session's CA farm is not a neighbour
    assert all(json.loads(out[i]["result"]["content"][0]["text"]) == out[i]["result"]["structuredContent"]
               for i in (3, 4, 5, 6, 7))
