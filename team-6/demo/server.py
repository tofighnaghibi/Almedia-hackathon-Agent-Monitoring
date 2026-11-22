"""Live dashboard server (L7, Person A).

Runs the autonomous monitor in a background thread, one simulated hour after
another, and serves a single-page dashboard plus a small JSON API on
http://127.0.0.1:8050 (local only). Only the Python standard library is used.

    GET  /                  dashboard
    GET  /api/state         clock, issues, alerts, live feed, watchlist, scenarios
    GET  /api/issue?id=     one issue: report and charts
    GET  /api/series?metric=&segment=country:DE,platform:android
    GET  /api/tree          metric graph (formula and cause edges) with live status
    GET  /api/score         ground truth vs what was reported (for the demo audience)
    POST /api/control       {"action": "pause" | "resume" | "step" | "speed", "value": seconds}
    POST /api/inject        {"key": "payment_failure"}
    POST /api/metric        {"name": "orders_per_search"}
"""

from __future__ import annotations

import json
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional
from urllib.parse import parse_qs, urlparse

import yaml

from runtime.runner import AutonomousMonitor
from simulator.stream import LiveMarketplace

from .script import NEW_METRIC, save_run, score

HTML = Path(__file__).with_name("dashboard.html")
EXAMPLES = Path(__file__).resolve().parent.parent / "extension" / "examples" / "new_metrics.yaml"


class DemoController:
    def __init__(self, live: LiveMarketplace, script: Optional[dict], delay: float, use_llm: Optional[bool],
                 out_dir: str, llm_cache_mode: Optional[str] = None, llm_cache_dir: Optional[str] = None):
        self.mon = AutonomousMonitor(live, use_llm=use_llm, llm_cache_mode=llm_cache_mode,
                                     llm_cache_dir=llm_cache_dir)
        self.script = script or {}
        self.delay = delay
        self.out_dir = out_dir
        self.lock = threading.RLock()
        self.paused = False
        self._step = False
        self.saved = False
        self.examples = self._examples()

    def _examples(self) -> dict[str, dict]:
        out = {NEW_METRIC["name"]: NEW_METRIC}
        try:
            for d in yaml.safe_load(EXAMPLES.read_text(encoding="utf-8")) or []:
                out.setdefault(d["name"], d)
        except (OSError, yaml.YAMLError):
            pass
        return out

    # --- the monitor thread --------------------------------------------------------------
    def loop(self) -> None:
        mon = self.mon
        while True:
            if mon.live.finished:
                if not self.saved:
                    with self.lock:
                        save_run(mon, self.out_dir, score(mon))
                        self.saved = True
                time.sleep(0.5)
                continue
            if self.paused and not self._step:
                time.sleep(0.1)
                continue
            with self.lock:
                offset = int((mon.now - mon.live.live_start).total_seconds() // 3600)
                for action, arg in self.script.get(offset, []):
                    if action == "inject":
                        mon.inject(arg)
                    elif action == "metric":
                        mon.add_metric(arg)
                self.script.pop(offset, None)
                mon.tick()
                self._step = False
            time.sleep(max(0.05, self.delay))

    # --- API ------------------------------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        with self.lock:
            s = self.mon.state()
            s["paused"] = self.paused
            s["delay"] = self.delay
            s["presets"] = self.mon.live.preset_list()
            s["examples"] = [{"name": k, "description": v.get("description", ""),
                              "added": k in self.mon.engine.registry} for k, v in self.examples.items()]
            s["scripted"] = [{"at": f"+{h}h", "action": a, "what": x["name"] if isinstance(x, dict) else x}
                             for h, acts in sorted(self.script.items()) for a, x in acts]
            s["saved_to"] = self.out_dir if self.saved else None
            return s

    def control(self, action: str, value: Any = None) -> dict:
        if action == "pause":
            self.paused = True
        elif action == "resume":
            self.paused = False
        elif action == "step":
            self._step = True
        elif action == "speed":
            self.delay = max(0.0, float(value or 0))
        return {"paused": self.paused, "delay": self.delay}

    def inject(self, key: str) -> dict:
        with self.lock:
            return self.mon.inject(key)

    def add_metric(self, name: str) -> dict:
        if name not in self.examples:
            raise KeyError(name)
        with self.lock:
            if name in self.mon.engine.registry:
                return {"already": True}
            return self.mon.add_metric(self.examples[name])

    def score(self) -> dict:
        with self.lock:
            return score(self.mon)


def _handler(ctl: DemoController):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):  # quiet
            pass

        def _send(self, code: int, body: Any, ctype: str = "application/json") -> None:
            data = body if isinstance(body, bytes) else json.dumps(body, default=str).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            u = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(u.query).items()}
            try:
                if u.path in ("/", "/index.html"):
                    return self._send(200, HTML.read_bytes(), "text/html; charset=utf-8")
                if u.path == "/api/state":
                    return self._send(200, ctl.state())
                if u.path == "/api/issue":
                    with ctl.lock:
                        d = ctl.mon.issue_detail(q.get("id", ""))
                    return self._send(200 if d else 404, d or {"error": "no such issue"})
                if u.path == "/api/series":
                    seg = dict(p.split(":", 1) for p in q.get("segment", "").split(",") if ":" in p)
                    with ctl.lock:
                        d = ctl.mon.series(q.get("metric", ""), seg, int(q.get("hours", 96)))
                    return self._send(200 if d else 404, d or {"error": "no series"})
                if u.path == "/api/tree":
                    with ctl.lock:
                        return self._send(200, ctl.mon.tree())
                if u.path == "/api/score":
                    return self._send(200, ctl.score())
                return self._send(404, {"error": "not found"})
            except Exception as exc:  # noqa: BLE001 - report, keep serving
                return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

        def do_POST(self):  # noqa: N802
            u = urlparse(self.path)
            try:
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                if u.path == "/api/control":
                    return self._send(200, ctl.control(body.get("action", ""), body.get("value")))
                if u.path == "/api/inject":
                    return self._send(200, ctl.inject(body.get("key", "")))
                if u.path == "/api/metric":
                    return self._send(200, ctl.add_metric(body.get("name", "")))
                return self._send(404, {"error": "not found"})
            except KeyError as exc:
                return self._send(400, {"error": f"unknown: {exc}"})
            except Exception as exc:  # noqa: BLE001
                return self._send(500, {"error": f"{type(exc).__name__}: {exc}"})

    return Handler


def serve(live: LiveMarketplace, script: Optional[dict] = None, port: int = 8050, delay: float = 0.0,
          use_llm: Optional[bool] = None, out_dir: str = "data/generated/demo_run", open_browser: bool = True,
          llm_cache_mode: Optional[str] = None, llm_cache_dir: Optional[str] = None) -> None:
    print("starting the monitor (learning from the history)...", flush=True)
    ctl = DemoController(live, script, delay, use_llm, out_dir, llm_cache_mode, llm_cache_dir)
    print(ctl.mon.llm.note, flush=True)
    threading.Thread(target=ctl.loop, daemon=True).start()
    server = ThreadingHTTPServer(("127.0.0.1", port), _handler(ctl))
    url = f"http://127.0.0.1:{port}"
    print(f"dashboard: {url}   (Ctrl+C to stop)", flush=True)
    if open_browser:
        threading.Timer(1.0, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nstopping")
    finally:
        server.server_close()
