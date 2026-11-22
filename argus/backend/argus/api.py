"""FastAPI app implementing docs/API.md. Run: uvicorn argus.api:app --reload (from backend/)."""
from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from pathlib import Path

from fastapi import Body, FastAPI, Header, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import config as cfg
from . import launch, llm, meta, precompute
from .world import INJECT_TYPES, SCENES, World

llm.load_dotenv()  # LLM_* from backend/.env if present (never committed)
precompute.warm_in_background()  # fitted default world + launch forecast: from the build-time cache, else computed once

# Memory budget (Render free tier: 512 MB RAM). Measured on Linux / Python 3.11 in the Docker image:
# idle process ~65 MB, ~100 MB once the first world and the taste model are loaded; each further session ~16 MB
# after the full demo script (forecast cache bounded). 18 MB/session leaves headroom: default cap 13, ~310 MB.
MEM_BUDGET_MB = float(os.environ.get("ARGUS_MEM_BUDGET_MB", 350))
BASE_MB = 120.0          # + ~20 MB for the two precomputed template worlds (fresh / after the start scene)
SESSION_MB = 18.0
MAX_SESSIONS = int(os.environ.get("ARGUS_MAX_SESSIONS") or max(2, int((MEM_BUDGET_MB - BASE_MB) // SESSION_MB)))
SESSION_TTL = float(os.environ.get("ARGUS_SESSION_TTL_S", 30 * 60))   # idle sessions expire after 30 min
MAX_STEP_HOURS = 24 * 14


class _Session:
    def __init__(self) -> None:
        self.world = World(seed=42)
        self.lock = threading.Lock()
        self.seen = time.monotonic()


def rss_mb() -> float | None:
    """Current resident memory of this process in MB (Linux /proc; peak RSS elsewhere)."""
    try:
        with open("/proc/self/statm") as f:
            return round(int(f.read().split()[1]) * os.sysconf("SC_PAGE_SIZE") / 2**20, 1)
    except OSError:
        try:
            import resource
            import sys

            peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
            return round(peak / (2**20 if sys.platform == "darwin" else 2**10), 1)
        except Exception:  # noqa: BLE001
            return None


class Sessions:
    """Session-scoped worlds: least-recently-used eviction above MAX_SESSIONS, idle expiry after SESSION_TTL."""

    def __init__(self) -> None:
        self._items: OrderedDict[str, _Session] = OrderedDict()
        self._lock = threading.Lock()
        self.evicted = 0
        self.expired = 0

    def __len__(self) -> int:
        return len(self._items)

    def get(self, key: str | None) -> _Session:
        key = (key or "default")[:64]
        now = time.monotonic()
        with self._lock:
            for k in [k for k, s in self._items.items() if now - s.seen > SESSION_TTL]:
                del self._items[k]
                self.expired += 1
            s = self._items.get(key)
            if s is None:
                while len(self._items) >= MAX_SESSIONS:      # make room first, so memory never exceeds the cap
                    self._items.popitem(last=False)
                    self.evicted += 1
                s = _Session()
                self._items[key] = s
            self._items.move_to_end(key)
            s.seen = now
            return s


sessions = Sessions()
app = FastAPI(title="Argus", version="0.1")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["*"], allow_headers=["*"])


class StepIn(BaseModel):
    hours: int = 1


class InjectIn(BaseModel):
    type: str
    country: str
    platform: str | None = None


class ApproveIn(BaseModel):
    fix_id: str


class MetricIn(BaseModel):
    name: str
    description: str = ""
    formula: str | None = None


class LaunchIn(BaseModel):
    country: str = "AR"
    users: int | None = None        # expected daily users after 4 weeks; default: assumed from similar countries


def _world(x_session: str | None):
    return sessions.get(x_session)


@app.get("/api/state")
def get_state(x_session: str | None = Header(default=None)) -> dict:
    s = _world(x_session)
    with s.lock:
        return s.world.agent.state()


@app.post("/api/step")
def post_step(body: StepIn = Body(default=StepIn()), x_session: str | None = Header(default=None)) -> dict:
    if not 1 <= body.hours <= MAX_STEP_HOURS:
        raise HTTPException(400, f"hours must be between 1 and {MAX_STEP_HOURS}")
    s = _world(x_session)
    with s.lock:
        s.world.step(body.hours)
        return s.world.agent.state()


@app.post("/api/reset")
def post_reset(x_session: str | None = Header(default=None)) -> dict:
    s = _world(x_session)
    with s.lock:
        s.world.reset(42)
        return s.world.agent.state()


@app.post("/api/inject")
def post_inject(body: InjectIn, x_session: str | None = Header(default=None)) -> dict:
    if body.type not in INJECT_TYPES:
        raise HTTPException(400, f"type must be one of {', '.join(INJECT_TYPES)}")
    s = _world(x_session)
    with s.lock:
        try:
            ev = s.world.inject(body.type, body.country, body.platform)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return {"ok": True, "event": ev}


@app.get("/api/graph")
def get_graph(x_session: str | None = Header(default=None)) -> dict:
    s = _world(x_session)
    with s.lock:
        return s.world.agent.graph.to_dict()


@app.get("/api/graph/tree")
def get_graph_tree(x_session: str | None = Header(default=None)) -> dict:
    s = _world(x_session)
    with s.lock:
        return s.world.agent.graph.tree()


@app.get("/api/incidents")
def get_incidents(x_session: str | None = Header(default=None)) -> list[dict]:
    s = _world(x_session)
    with s.lock:
        return [s.world.agent.incident_summary(i) for i in s.world.agent.incidents]


@app.get("/api/incidents/{inc_id}")
def get_incident(inc_id: str, x_session: str | None = Header(default=None)) -> dict:
    s = _world(x_session)
    with s.lock:
        inc = s.world.agent.get_incident(inc_id)
        if inc is None:
            raise HTTPException(404, f"incident {inc_id} not found")
        return s.world.agent.incident_detail(inc)


@app.post("/api/incidents/{inc_id}/approve")
def approve(inc_id: str, body: ApproveIn, x_session: str | None = Header(default=None)) -> dict:
    s = _world(x_session)
    with s.lock:
        agent = s.world.agent
        if agent.get_incident(inc_id) is None:
            raise HTTPException(404, f"incident {inc_id} not found")
        try:
            inc = agent.approve(inc_id, body.fix_id)
        except ValueError as e:
            raise HTTPException(400, str(e)) from e
        return agent.incident_detail(inc)


@app.post("/api/metrics")
def post_metric(body: MetricIn, x_session: str | None = Header(default=None)) -> dict:
    if not body.name.strip():
        raise HTTPException(400, "name is required")
    s = _world(x_session)
    with s.lock:
        return s.world.agent.add_metric(body.name.strip()[:120], body.description.strip()[:500],
                                        (body.formula or "").strip()[:300] or None)


@app.get("/api/country/{iso}")
def get_country(iso: str, x_session: str | None = Header(default=None)) -> dict:
    iso = iso.upper()
    iso = "GB" if iso == "UK" else iso
    if iso not in cfg.ISO_INDEX:
        raise HTTPException(404, f"country {iso} not found")
    s = _world(x_session)
    with s.lock:
        return s.world.agent.country_detail(cfg.ISO_INDEX[iso])


@app.get("/api/potential")
def get_potential(x_session: str | None = Header(default=None)) -> dict:
    s = _world(x_session)
    with s.lock:
        return {"countries": list(s.world.agent.potential), "summary_line": s.world.agent.growth_summary_line()}


@app.post("/api/demo/{scene}")
def post_demo(scene: str, frames: bool = False, x_session: str | None = Header(default=None)) -> dict:
    if scene not in SCENES:
        raise HTTPException(404, f"scene must be one of {', '.join(SCENES)}")
    s = _world(x_session)
    with s.lock:
        return s.world.demo_frames(scene) if frames else s.world.demo(scene)


class OfferMixIn(BaseModel):
    country: str


@app.post("/api/what_if/offer_mix")
def post_offer_mix(body: OfferMixIn, x_session: str | None = Header(default=None)) -> dict:
    iso = body.country.upper()
    iso = "GB" if iso == "UK" else iso
    if iso not in cfg.ISO_INDEX:
        raise HTTPException(400, f"unknown country '{body.country}'")
    s = _world(x_session)
    with s.lock:
        return s.world.agent.offer_mix_rollout(cfg.ISO_INDEX[iso])


@app.post("/api/what_if/launch")
def post_launch(body: LaunchIn = Body(default=LaunchIn()), x_session: str | None = Header(default=None)) -> dict:
    s = _world(x_session)
    with s.lock:
        incidents = [{"country": i["country"], "root": i["root"], "title": i["title"]} for i in s.world.agent.incidents]
        seed = s.world.seed
    try:
        return launch.launch(body.country, seed, incidents, body.users)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/timeline")
def get_timeline(x_session: str | None = Header(default=None)) -> dict:
    s = _world(x_session)
    with s.lock:
        return s.world.agent.timeline()


@app.get("/api/meta")
def get_meta(x_session: str | None = Header(default=None)) -> dict:
    s = _world(x_session)
    with s.lock:
        cal = meta.calibration(s.world.view.observed)
    return {"data_sources": meta.data_sources(), "calibration_note": meta.CALIBRATION_NOTE, "calibration": cal}


@app.get("/api/health")
def health() -> dict:
    return {"ok": True, "llm": llm.model(), "sessions": len(sessions), "max_sessions": MAX_SESSIONS,
            "session_ttl_s": SESSION_TTL, "evicted": sessions.evicted, "expired": sessions.expired,
            "rss_mb": rss_mb(), "mem_budget_mb": MEM_BUDGET_MB}


_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"
if _DIST.is_dir():
    app.mount("/", StaticFiles(directory=_DIST, html=True), name="frontend")
