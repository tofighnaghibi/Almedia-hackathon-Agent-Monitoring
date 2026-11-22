"""The causal engine as its own local service (demo-only). The agent talks to it over HTTP; the engine holds the
evidence, the ledger and the reasoning, and sees only what is sent to its API (contract objects: observations and
reasoning tasks). It has no way to change anything outside its own ledger.

    python -m agent.engine_service [--port 8770]        # binds 127.0.0.1 only; scripts/uc2_engine.sh start|stop|status

    GET  /                                   status page: process, sessions, recent tasks (links to their full traces)
    GET  /health                             {"status", "pid", "version", "uptime_s", "sessions", "tasks_run"}
    POST /sessions                           → {"session_id"}: a fresh ledger and evidence store
    POST /sessions/<sid>/observations        [observation, ...]  → {"accepted", "held"}  (each one parsed against the contracts)
    POST /sessions/<sid>/tasks               {"task": ReasoningTask, "monitors": {variable: {"null_mean", "null_sd",
                                              "family_size"}}, "requested_action": str|null, "label": str}  → the trace
    GET  /sessions/<sid>/tasks/<task_id>     the stored trace and the engine's final report envelope
    GET  /sessions/<sid>/ledger              row counts per ledger table
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import threading
import time
import uuid
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parents[1]
CORE = Path(os.environ.get("UC2_CORE") or HERE.parent / "causal_core")
sys.path.insert(0, str(CORE))

from adapters.outputs.sqlite_sinks import IdempotentSink  # noqa: E402
from adapters.sources.canonical_port import CanonicalEvidencePort  # noqa: E402
from causal_core import api  # noqa: E402
from causal_core.orchestration.coordinator import TaskCoordinator  # noqa: E402
from causal_core.orchestration.planner import MonitorSpec, TaskContext  # noqa: E402
from causal_core.storage.ledger import Ledger  # noqa: E402

DATA = Path(os.environ.get("UC2_ENGINE_DATA") or Path.home() / "data/mkt/engine-service")
STARTED = time.time()


def version() -> str:
    v = subprocess.run(["git", "-C", str(CORE), "rev-parse", "--short", "HEAD"], capture_output=True, text=True).stdout.strip()
    if not v and (CORE / "VERSION.txt").exists():
        v = (CORE / "VERSION.txt").read_text().split()[1][:7]
    return v or "unknown"


VERSION = version()


def jsonable(o):
    if hasattr(o, "item"):
        return o.item()
    if hasattr(o, "isoformat"):
        return o.isoformat()
    return str(o)


class TracingPort(CanonicalEvidencePort):
    """The engine's evidence port for one task, with every capability request logged."""

    def __init__(self, observations, provider_id):
        super().__init__(observations, provider_id)
        self.log = []

    def query(self, request):
        t0 = time.perf_counter()
        r = super().query(request)
        self.log.append({"request": request.request, "variables": list(getattr(request, "variables", ()) or ()),
                         "status": r.status, "observations": len(r.observations), "ms": round((time.perf_counter() - t0) * 1000, 3)})
        return r


class Session:
    def __init__(self, sid: str):
        self.sid, self.dir = sid, DATA / sid
        self.dir.mkdir(parents=True, exist_ok=True)
        self.ledger = Ledger(self.dir / "ledger.db")
        self.sink = IdempotentSink(self.dir / "sink.db")
        self.observations: list[dict] = []
        self.traces: dict[str, dict] = {}
        self.reports: dict[str, dict] = {}
        self.order: list[str] = []

    def counts(self) -> dict:
        out = {}
        for (name,) in self.ledger.read("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"):
            out[name] = self.ledger.read(f"SELECT COUNT(*) FROM {name}")[0][0]
        return out

    def add(self, obs: list[dict]) -> int:
        for o in obs:
            CanonicalEvidencePort([o], "contract-check")          # raises if the observation breaks the contracts
        self.observations.extend(obs)
        return len(obs)

    def run(self, body: dict) -> dict:
        raw, label = body["task"], body.get("label", "")
        specs = {v: MonitorSpec(float(m.get("null_mean", 0.0)), float(m.get("null_sd", 1.0)),
                                family_size=int(m.get("family_size", 1))) for v, m in (body.get("monitors") or {}).items()}
        task = api.parse_reasoning_task(raw)
        port = TracingPort(self.observations, "engine-service-evidence")
        co = TaskCoordinator(self.ledger, port, self.sink, context=TaskContext(monitors=specs))
        before = self.counts()
        t0 = time.perf_counter()
        ra = body.get("requested_action")
        r = co.run(task, requested_action=ra) if ra else co.run(task)
        wall_ms = (time.perf_counter() - t0) * 1000
        after = self.counts()
        res = {}
        for m, x in r.state.results:
            v = getattr(x, "values", None) or {}
            res[m] = {"status": x.status, "refusal": getattr(x, "refusal_code", None),
                      "values": {k: v[k] for k in ("alarm", "direction", "log_wealth", "threshold", "points", "e_value") if k in v}}
        q = raw["question"]
        tr = {"task_id": raw["task_id"], "label": label, "sim": raw["data_cut"]["as_of"], "question": q["question_kind"],
              "variables": q.get("variables"), "outcome": r.state.outcome, "wall_ms": round(wall_ms, 2),
              "timings_ms": dict(r.timings_ms), "requests": port.log,
              "methods": [{"method": m, "status": st, "reason": (list(rs) or [""])[0][:160]} for m, st, rs in r.state.applicability],
              "results": res,
              "revisions": [{"revision": rep.get("revision"), "provisional": rep.get("provisional"),
                             "status": rep.get("status") or rep.get("outcome")} for rep in r.reports],
              "policy": [{"verdict": d.verdict, "reason": (list(d.reasons) or [""])[0][:200]} for d in r.decisions],
              "ledger_delta": {k: after[k] - before.get(k, 0) for k in after if after[k] != before.get(k, 0)},
              "ledger_totals": after,
              "findings": [f.statement[:200] for f in r.state.findings][:3],
              "stage_log": [[st, v, n[:200]] for st, v, n in r.state.trace],
              "challenges": list(r.state.challenges)[:4], "data_cut": raw["data_cut"]["as_of"],
              "engine": {"pid": os.getpid(), "session": self.sid, "version": VERSION}}
        self.traces[tr["task_id"]] = tr
        self.reports[tr["task_id"]] = r.reports[-1] if r.reports else {}
        self.order.append(tr["task_id"])
        return tr


SESSIONS: dict[str, Session] = {}
LOCK = threading.Lock()
TASKS_RUN = [0]


def status_page() -> str:
    rows = []
    for s in list(SESSIONS.values())[-3:][::-1]:            # newest session first
        for tid in s.order[-15:][::-1]:
            t = s.traces[tid]
            mon = (t["results"].get("savi_eprocess_v1") or {}).get("values", {})
            rows.append(f"<tr><td>{t['sim'][:16].replace('T', ' ')}</td><td>{t['label']}</td><td>{t['question']}</td>"
                        f"<td>{'ALARM ' + str(mon.get('direction') or '') if mon.get('alarm') else ''}</td><td>{t['outcome']}</td>"
                        f"<td>{(t['policy'] or [{}])[-1].get('verdict', '')}</td><td>{t['wall_ms']:.0f} ms</td>"
                        f"<td><a href='/sessions/{s.sid}/tasks/{tid}' target='_blank'>{tid}</a></td></tr>")
    up = time.time() - STARTED
    return f"""<!doctype html><html><head><meta charset="utf-8"><title>Engine service</title><meta http-equiv="refresh" content="3">
<style>body{{font-family:-apple-system,Helvetica,Arial,sans-serif;background:#0e1115;color:#e9ecf0;margin:18px}}
h1{{font-size:20px}} .m{{color:#b1b9c4;font-size:14px}} table{{border-collapse:collapse;font-size:13px;margin-top:12px;width:100%}}
td,th{{border-bottom:1px solid #2b323c;padding:5px 8px;text-align:left}} th{{color:#7f8996;font-weight:600}} a{{color:#7fb2f0}}</style></head>
<body><h1>● Causal engine service</h1>
<div class="m">process {os.getpid()} · version {VERSION} · up {int(up // 60)} min {int(up % 60)} s · listening on this machine only ·
sessions {len(SESSIONS)} · tasks run {TASKS_RUN[0]} · data {DATA}</div>
<div class="m" style="margin-top:6px">It holds the evidence it is sent, the ledger and the reasoning. It cannot change anything outside its ledger. Refreshes every 3 s.</div>
<table><tr><th>data up to (sim)</th><th>task</th><th>question</th><th>result</th><th>outcome</th><th>policy</th><th>time</th><th>full trace + report</th></tr>
{''.join(rows) or '<tr><td colspan=8 class=m>no tasks yet</td></tr>'}</table></body></html>"""


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body, ctype: str = "application/json"):
        data = (body if isinstance(body, str) else json.dumps(body, default=jsonable)).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype + "; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = int(self.headers.get("Content-Length", 0))
        return json.loads(self.rfile.read(n) or b"null")

    def do_GET(self):
        parts = [p for p in self.path.split("?")[0].split("/") if p]
        if not parts:
            return self._send(200, status_page(), "text/html")
        if parts == ["health"]:
            return self._send(200, {"status": "ok", "pid": os.getpid(), "version": VERSION, "uptime_s": round(time.time() - STARTED),
                                    "sessions": len(SESSIONS), "tasks_run": TASKS_RUN[0], "data": str(DATA)})
        if len(parts) >= 3 and parts[0] == "sessions" and parts[1] in SESSIONS:
            s = SESSIONS[parts[1]]
            if parts[2] == "ledger":
                with LOCK:
                    return self._send(200, s.counts())
            if parts[2] == "tasks" and len(parts) == 4 and parts[3] in s.traces:
                return self._send(200, {"trace": s.traces[parts[3]], "report": s.reports.get(parts[3])})
        return self._send(404, {"error": "not found"})

    def do_POST(self):
        parts = [p for p in self.path.split("?")[0].split("/") if p]
        try:
            if parts == ["sessions"]:
                sid = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S") + "-" + uuid.uuid4().hex[:6]
                with LOCK:
                    SESSIONS[sid] = Session(sid)
                return self._send(200, {"session_id": sid, "pid": os.getpid(), "version": VERSION})
            if len(parts) == 3 and parts[0] == "sessions" and parts[1] in SESSIONS:
                s, body = SESSIONS[parts[1]], self._body()
                if parts[2] == "observations":
                    with LOCK:
                        n = s.add(body if isinstance(body, list) else [body])
                    return self._send(200, {"accepted": n, "held": len(s.observations)})
                if parts[2] == "tasks":
                    with LOCK:
                        tr = s.run(body)
                        TASKS_RUN[0] += 1
                    return self._send(200, tr)
            return self._send(404, {"error": "not found"})
        except Exception as e:  # noqa: BLE001 - report contract and engine errors to the caller
            return self._send(400, {"error": f"{type(e).__name__}: {str(e)[:500]}"})

    def log_message(self, *a):
        pass


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=int(os.environ.get("UC2_ENGINE_PORT", 8770)))
    a = ap.parse_args()
    DATA.mkdir(parents=True, exist_ok=True)
    print(f"causal engine service {VERSION} · pid {os.getpid()} · http://127.0.0.1:{a.port}/ · data {DATA}", flush=True)
    ThreadingHTTPServer(("127.0.0.1", a.port), Handler).serve_forever()
    return 0


if __name__ == "__main__":
    sys.exit(main())
