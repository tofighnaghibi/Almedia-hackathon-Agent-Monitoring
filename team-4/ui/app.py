"""Showcase UI: the metric graph, the incident feed, and buttons that inject faults into the simulator.

    uvicorn ui.app:app --port 8000        then open http://127.0.0.1:8000

/ is the main console (design 3, "Investigation canvas", ui/static/v3/); /classic is the earlier console
(ui/static/index.html); /data browses the simulator tables; /designs links the candidate designs.

Reads: the graph export (docs/contracts/graph.json), incidents/*.json written by brain/, and the simulator
database. It never writes to brain/. The buttons call the same functions as `python -m sim.inject`.
"""
from __future__ import annotations

import json
import math
import os
import re
import sqlite3
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from sim import db, inject
from sim.clock import sync_current_hour, wall_now
from ui import domains as domain_map
from sim.inject import AlreadyEnded, AlreadyInjected, NoSuchInjection
from sim.world import CATEGORIES, CHANNELS, COUNTRIES, PLATFORMS
from sim.schema import SOURCES

ROOT = Path(__file__).resolve().parent.parent
GRAPH_JSON = Path(os.environ.get("GRAPH_JSON", ROOT / "docs/contracts/graph.json"))
INCIDENTS_DIR = Path(os.environ.get("INCIDENTS_DIR", ROOT / "incidents"))
INCOMING_DIR = Path(os.environ.get("INCOMING_METRICS_DIR", ROOT / "incoming_metrics"))
EXAMPLE_INCIDENT = ROOT / "docs/contracts/incident_example.json"
MARKET_DB = Path(os.environ.get("MARKET_DB", ROOT / "data/market.db"))
ENV_FILE = ROOT / ".env"      # the repository's .env: brain/narrate.py reads the key from the same file

CLOCK_MODE = os.environ.get("MARKET_CLOCK", "replay")
if CLOCK_MODE not in ("wall", "replay"):
    raise ValueError("MARKET_CLOCK must be wall or replay")


def current_hour(con):
    if CLOCK_MODE == "wall":
        try:
            return sync_current_hour(con, wall_now())
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc
    return db.last_hour(con)


app = FastAPI(title="Eye of the System")
class NoCacheStatic(StaticFiles):
    """The console is a local demo: a changed style or script must show up on the next reload."""

    async def get_response(self, path, scope):
        response = await super().get_response(path, scope)
        response.headers["Cache-Control"] = "no-cache"
        return response


app.mount("/static", NoCacheStatic(directory=Path(__file__).parent / "static"), name="static")


STATIC = Path(__file__).parent / "static"


def _page(name: str, assets: tuple[str, ...] = ("console.css",)) -> HTMLResponse:
    """A page whose stylesheet and script links carry each file's modification time, so a changed style or script
    is fetched again even by a browser that cached the old one."""
    html = (STATIC / name).read_text(encoding="utf-8")
    for asset in assets:
        version = int((STATIC / asset).stat().st_mtime)
        html = html.replace(f'"/static/{asset}"', f'"/static/{asset}?v={version}"')
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


MAIN_DESIGN = 3      # the console served at /: design 3, "Investigation canvas" (ui/static/v3/)


@app.get("/")
def index():
    return _page(f"v{MAIN_DESIGN}/index.html",
                 (f"v{MAIN_DESIGN}/style.css", f"v{MAIN_DESIGN}/route.js", f"v{MAIN_DESIGN}/app.js"))


@app.get("/classic")
def classic():
    """The console before design 3: graph and incidents side by side, the DEMO bar folded at the top."""
    return _page("index.html")


DESIGNS = {1: "Ops room", 2: "Analyst brief", 3: "Investigation canvas", 4: "Presenter cockpit"}


@app.get("/v/{n}")
def design(n: int):
    """One of the four candidate designs of the console (ui/static/v<n>/), served beside the current one."""
    page = STATIC / f"v{n}" / "index.html"
    if n not in DESIGNS or not page.is_file():
        raise HTTPException(404, f"no design {n}")
    return HTMLResponse(page.read_text(encoding="utf-8"), headers={"Cache-Control": "no-cache"})


@app.get("/designs")
def designs():
    """A page that links the main console, the classic one and the four candidate designs, each with the same live
    data. Design 3 is the main console."""
    rows = "".join(
        f'<li><a href="/v/{n}">{n}. {name}</a> <span>/v/{n}</span>'
        + (" <b>the main console, served at /</b>" if n == MAIN_DESIGN else "") + "</li>"
        for n, name in DESIGNS.items() if (STATIC / f"v{n}" / "index.html").is_file())
    html = (
        '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width">'
        '<title>Eye of the System: designs</title><style>'
        'body{font:16px "Segoe UI Variable","Segoe UI",system-ui,sans-serif;margin:48px;color:#14171f;background:#f5f6f8}'
        'h1{font-size:28px;margin:0 0 8px}p{color:#4a5262;margin:0 0 24px}ul{list-style:none;padding:0;margin:0}'
        'li{margin:0 0 12px}a{color:#2f4ac6;font-weight:600;text-decoration:none;font-size:20px}a:hover{text-decoration:underline}'
        'span{color:#6b7280;font-family:"Cascadia Mono",Consolas,monospace;font-size:13px;margin-left:8px}'
        'b{font-weight:600;font-size:13px;margin-left:8px;color:#4a5262}'
        '@media (prefers-color-scheme: dark){body{background:#12151c;color:#e8ecf2}p,span,b{color:#9aa3b2}a{color:#8ea2ff}}'
        '</style></head><body><h1>Eye of the System: four designs</h1>'
        '<p>The same monitor, the same live data and the same API; only the console differs.</p>'
        f'<ul><li><a href="/">Main console ({MAIN_DESIGN}. {DESIGNS[MAIN_DESIGN]})</a> <span>/</span></li>'
        '<li><a href="/classic">Classic console</a> <span>/classic</span></li>'
        f'{rows}</ul></body></html>')
    return HTMLResponse(html, headers={"Cache-Control": "no-cache"})


@app.get("/api/domains")
def domains():
    return domain_map.as_json()


@app.get("/api/graph")
def graph():
    if not GRAPH_JSON.exists():
        raise HTTPException(404, f"graph export not found: {GRAPH_JSON} (python -m brain.graph --export PATH)")
    return json.loads(GRAPH_JSON.read_text(encoding="utf-8"))


def _no_constant(name: str):
    raise ValueError(f"{name} is not valid JSON")


def _read_incident(path: Path) -> dict | None:
    """One incident file, or None when it is not an incident: unreadable, half written, NaN or Infinity (Python's
    json.dumps writes them, browsers refuse them), not an object, or without a text id and metric."""
    try:
        data = json.loads(path.read_text(encoding="utf-8"), parse_constant=_no_constant)
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("id"), str):
        return None
    # explained and pending records carry no outcome metric; notes/ is still read for older runs.
    if not isinstance(data.get("metric"), str):
        if data.get("status") not in ("pending", "explained"):
            return None
        data["metric"] = "data_arrival" if data["status"] == "pending" else "calendar"
    return data


FEED_STATUS = {"open": 0, "ongoing": 0, "resolved": 1, "explained": 2, "pending": 3}
SEVERITIES = ("high", "medium", "low")


def feed_order(record: dict) -> tuple:
    """The feed's order, the same as brain.incidents.feed_rank: live first, then resolved, explained, pending;
    within a status the severe ones first, then the newest."""
    try:
        newest_first = -pd.Timestamp(record.get("last_seen")).value
    except (TypeError, ValueError):
        newest_first = 0
    sev = record.get("severity")
    return (FEED_STATUS.get(record.get("status"), 4), SEVERITIES.index(sev) if sev in SEVERITIES else len(SEVERITIES),
            newest_first)


def _incidents() -> tuple[list[dict], bool, int]:
    """(incidents, is_example, number of files ignored). The contract example is shown only when the folder does not
    exist; once the monitor has made it, an empty folder means "nothing found". Non-alert status notes are included."""
    if INCIDENTS_DIR.is_dir():
        items, skipped = [], 0
        paths = sorted(INCIDENTS_DIR.glob("*.json"))
        known = {p.stem for p in paths}
        paths += [p for p in sorted((INCIDENTS_DIR / "notes").glob("*.json")) if p.stem not in known]
        for p in paths:
            d = _read_incident(p)
            if d is None:
                skipped += 1
            elif p.parent == INCIDENTS_DIR or d.get("status") in ("explained", "pending"):
                items.append(d)
        return sorted(items, key=feed_order), False, skipped
    if EXAMPLE_INCIDENT.exists():
        return [json.loads(EXAMPLE_INCIDENT.read_text(encoding="utf-8"))], True, 0
    return [], False, 0


@app.get("/api/incidents")
def incidents():
    items, is_example, skipped = _incidents()
    return {"example": is_example, "incidents": items, "skipped": skipped}


@app.get("/api/state")
def state():
    if not MARKET_DB.exists():
        return {"ready": False, "error": f"no database at {MARKET_DB}; run python -m sim.run --backfill 28d"}
    con = db.connect(MARKET_DB)
    try:
        last = current_hour(con)
        meta = db.read_meta(con)
        injected = [{"id": i["id"], "kind": i["kind"], "at": con.execute("SELECT injected_at FROM ground_truth WHERE rowid = ?", (i["id"],)).fetchone()[0],
                     "scope": i["scope"], "description": i["description"], "active": i["active"]}
                    for i in inject.list_injections(con, last + pd.Timedelta(hours=1))]
        return {"ready": True, "last_hour": last.strftime("%Y-%m-%dT%H:00:00Z"), "start": meta["start"].strftime("%Y-%m-%dT%H:00:00Z"),
                "countries": list(meta["countries"]), "injected": injected,
                "clock_mode": CLOCK_MODE, "display_timezone": "Europe/Berlin",
                "server_now": wall_now().isoformat()}
    finally:
        con.close()


class Inject(BaseModel):
    kind: str
    country: str | None = None
    platform: str | None = None
    category: str | None = None
    source: str | None = None
    severity: float | None = None   # payment_failure: 0.35 when not given; traffic_drop: 0.5
    share: float = 0.5              # mix_shift: the favoured category's share of sessions
    lift: float = 0.6               # discount_push: relative rise of the discount per euro
    hours: int = Field(default=12, ge=1)
    delay_hours: float = 2
    when: str = "now"  # now = current data hour; wall mode synchronizes it to the real current hour


@app.post("/api/inject")
def do_inject(req: Inject):
    if not MARKET_DB.exists():
        raise HTTPException(409, "no database yet")
    con = db.connect(MARKET_DB)
    try:
        return _inject(con, req)
    except AlreadyInjected as exc:
        raise HTTPException(409, str(exc))
    finally:
        con.close()


def _inject(con, req: "Inject"):
    last = current_hour(con)
    # "now" is the latest hour of data: the effect shows at once and carries on as the clock moves
    start = last if req.when == "now" else last - pd.Timedelta(hours=5)
    end = start + pd.Timedelta(hours=req.hours)
    if req.kind in ("payment_failure", "traffic_drop"):
        if req.country not in COUNTRIES or req.platform not in PLATFORMS:
            raise HTTPException(422, "country and platform must be valid")
        severity = req.severity if req.severity is not None else (0.35 if req.kind == "payment_failure" else 0.5)
        if not 0 < severity < 1:
            raise HTTPException(422, "severity must be between 0 and 1")
        if req.kind == "payment_failure":
            n = inject.payment_failure(con, req.country, req.platform, start, severity, end=end)
        else:
            n = inject.traffic_drop(con, req.country, req.platform, start, end, severity)
    elif req.kind == "feed_unavailable":
        n = inject.feed_unavailable(con, start, end)
    elif req.kind == "mix_shift":
        if req.category not in CATEGORIES:
            raise HTTPException(422, f"category must be one of {CATEGORIES}")
        if not 0 < req.share < 1:
            raise HTTPException(422, "share must be between 0 and 1")
        n = inject.mix_shift(con, req.category, start, end, req.share)
    elif req.kind == "discount_push":
        if not 0 < req.lift <= 5:
            raise HTTPException(422, "lift must be above 0 and at most 5")
        n = inject.discount_push(con, start, end, req.lift)
    elif req.kind == "stockout_surge":
        if req.category not in CATEGORIES:
            raise HTTPException(422, f"category must be one of {CATEGORIES}")
        n = inject.stockout_surge(con, req.category, start, end, country=req.country if req.country in COUNTRIES else None)
    elif req.kind == "search_regression":
        if req.country not in COUNTRIES:
            raise HTTPException(422, "country must be valid")
        n = inject.search_regression(con, req.country, start, 0.5, end=end)
    elif req.kind == "ad_budget_exhausted":
        if req.country not in COUNTRIES:
            raise HTTPException(422, "country must be valid")
        n = inject.ad_budget_exhausted(con, req.country, start, end, boost_hours=max(1, req.hours // 3))
    elif req.kind == "promo":
        if req.country not in COUNTRIES:
            raise HTTPException(422, "country must be valid")
        n = inject.promo(con, req.country, start, end)
    elif req.kind == "data_delay":
        if req.source not in SOURCES:
            raise HTTPException(422, f"source must be one of {SOURCES}")
        n = inject.data_delay(con, req.source, start, end, req.delay_hours)
    else:
        raise HTTPException(422, "unknown kind")
    return {"ok": True, "kind": req.kind, "start": start.strftime("%Y-%m-%dT%H:00:00Z"), "replayed_hours": n}


class End(BaseModel):
    id: int


@app.post("/api/end")
def end_fault(req: End):
    """Demo: someone rolls the change back. The injection stops from the latest hour, so the effect shows at once."""
    if not MARKET_DB.exists():
        raise HTTPException(409, "no database yet")
    con = db.connect(MARKET_DB)
    try:
        at = current_hour(con)
        inject.end_injection(con, req.id, at)
        return {"ok": True, "id": req.id, "ended_from": at.strftime("%Y-%m-%dT%H:00:00Z")}
    except NoSuchInjection as exc:
        raise HTTPException(404, str(exc))
    except AlreadyEnded as exc:
        raise HTTPException(409, str(exc))
    finally:
        con.close()


@app.get("/api/metric/{name}")
def metric_result(name: str):
    """What the monitor did with a contributed metric: <name>.result.json next to the YAML (404 until it exists)."""
    safe = "".join(ch for ch in name if ch.isalnum() or ch == "_")
    path = INCOMING_DIR / f"{safe}.result.json"
    if not safe or not path.is_file():
        raise HTTPException(404, "no result yet")
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=_no_constant)
    except (OSError, ValueError):
        raise HTTPException(404, "the result is not readable yet")


class NewMetric(BaseModel):
    yaml_text: str


@app.post("/api/metric")
def new_metric(req: NewMetric):
    """Hand a new metric definition to the brain: saved as a file that the extension agent picks up."""
    try:
        spec = yaml.safe_load(req.yaml_text)
    except yaml.YAMLError as exc:
        raise HTTPException(422, f"not valid YAML: {exc}")
    if not isinstance(spec, dict) or not spec.get("name") or not (spec.get("sql") or spec.get("formula")):
        raise HTTPException(422, "a metric needs a name and a sql or a formula")
    name = "".join(ch for ch in str(spec["name"]) if ch.isalnum() or ch == "_")
    INCOMING_DIR.mkdir(parents=True, exist_ok=True)
    path = INCOMING_DIR / f"{name}.yaml"
    path.write_text(req.yaml_text, encoding="utf-8")
    try:
        saved = path.relative_to(ROOT).as_posix()
    except ValueError:                      # the folder was configured outside the repository
        saved = path.as_posix()
    return {"ok": True, "saved": saved, "name": name}


# ---------------------------------------------------------------- runtime settings (docs: the settings contract)
# <data folder>/settings.json is read by the live monitor and the accelerated clock; a value there wins over the
# command line (--narrate, --speed), a missing one falls back to it. The defaults are brain.monitor.Config's and
# sim.live's --speed, written out here because ui/ does not import brain/.
SETTING_DEFAULTS = {
    "narration": {"enabled": False},
    "clock": {"seconds_per_hour": 2.0},
    "detection": {"min_surprise": 7.0, "min_effect": 0.10, "volume_min_effect": 0.20, "release_min_surprise": 4.0},
}
SETTING_RANGES = {
    "clock": {"seconds_per_hour": (0.25, 60)},
    "detection": {"min_surprise": (5, 10), "min_effect": (0.03, 0.50), "volume_min_effect": (0.05, 0.60),
                  "release_min_surprise": (3, 7)},
}
STATUS_FRESH_S = 10           # the monitor writes its status on every poll: older than this, it is not running
LOCAL_CLIENTS = {"127.0.0.1", "::1", "testclient"}     # testclient: FastAPI's TestClient
KEY_LINE = re.compile(r"^\s*(?:export\s+)?ANTHROPIC_API_KEY\s*=")


def _data_dir() -> Path:
    return Path(MARKET_DB).parent


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_atomic(path: Path, text: str) -> None:
    """Write through a temp file in the same folder and os.replace, so a reader never sees half a file. Windows
    refuses the replace while another process has the file open for a moment: try again briefly."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
            f.write(text)
        for attempt in range(20):
            try:
                os.replace(tmp, path)
                return
            except PermissionError:
                if attempt == 19:
                    raise
                time.sleep(0.05)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def _read_json(path: Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"), parse_constant=_no_constant)
    except FileNotFoundError:
        return None
    except (OSError, ValueError) as exc:
        raise ValueError(f"{path.name} is not readable: {exc}") from exc


def _is_number(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v)


def _merged_settings() -> tuple[dict, dict | None, list[str]]:
    """(settings.json merged with the defaults, the file as read or None, warnings). A value of the wrong type
    falls back to the default and one out of range is clamped, each with a warning: the readers do the same."""
    merged = json.loads(json.dumps(SETTING_DEFAULTS))
    warnings: list[str] = []
    try:
        raw = _read_json(_data_dir() / "settings.json")
    except ValueError as exc:
        return merged, None, [f"{exc}; the defaults are shown"]
    if raw is None:
        return merged, None, []
    if not isinstance(raw, dict):
        return merged, None, ["settings.json is not an object; the defaults are shown"]
    for section, fields in SETTING_DEFAULTS.items():
        got = raw.get(section)
        if not isinstance(got, dict):
            continue
        for name, default in fields.items():
            if name not in got:
                continue
            v = got[name]
            if isinstance(default, bool):
                if isinstance(v, bool):
                    merged[section][name] = v
                else:
                    warnings.append(f"{section}.{name} is not true or false; the default is used")
                continue
            if not _is_number(v):
                warnings.append(f"{section}.{name} is not a number; the default is used")
                continue
            lo, hi = SETTING_RANGES[section][name]
            if not lo <= v <= hi:
                warnings.append(f"{section}.{name} = {v} is outside {lo} to {hi}; clamped")
                v = min(max(v, lo), hi)
            merged[section][name] = float(v)
    if isinstance(raw.get("updated_at"), str):
        merged["updated_at"] = raw["updated_at"]
    return merged, raw, warnings


def _validate(body) -> dict:
    """The fields of a PUT, checked against the types and ranges; a 422 names the first one that is wrong."""
    if not isinstance(body, dict):
        raise HTTPException(422, "the settings must be a JSON object")
    out: dict = {}
    for section, fields in body.items():
        if section == "updated_at":
            continue
        if section not in SETTING_DEFAULTS:
            raise HTTPException(422, f"unknown section {section!r}: one of {', '.join(SETTING_DEFAULTS)}")
        if not isinstance(fields, dict):
            raise HTTPException(422, f"{section} must be an object")
        for name, v in fields.items():
            if name not in SETTING_DEFAULTS[section]:
                raise HTTPException(422, f"unknown setting {section}.{name}")
            if isinstance(SETTING_DEFAULTS[section][name], bool):
                if not isinstance(v, bool):
                    raise HTTPException(422, f"{section}.{name} must be true or false")
            else:
                lo, hi = SETTING_RANGES[section][name]
                if not _is_number(v):
                    raise HTTPException(422, f"{section}.{name} must be a number from {lo} to {hi}")
                if not lo <= v <= hi:
                    raise HTTPException(422, f"{section}.{name} = {v} is outside {lo} to {hi}")
                v = float(v)
            out.setdefault(section, {})[name] = v
    return out


def _save_settings(values: dict) -> dict:
    """Merge the values into settings.json (what is not given stays as it was in the file) and stamp it."""
    try:
        raw = _read_json(_data_dir() / "settings.json")
    except ValueError:
        raw = None                      # a broken file is replaced by what is saved now
    raw = raw if isinstance(raw, dict) else {}
    for section, fields in values.items():
        if not isinstance(raw.get(section), dict):
            raw[section] = {}
        raw[section].update(fields)
    raw["updated_at"] = _utc_now()
    _write_atomic(_data_dir() / "settings.json", json.dumps(raw, indent=2) + "\n")
    return raw


def _env_lines() -> list[str]:
    """The .env's lines with their own line endings, so the lines this does not touch are written back as they were."""
    try:
        with open(ENV_FILE, encoding="utf-8", newline="") as f:
            return f.read().splitlines(keepends=True)
    except FileNotFoundError:
        return []


def _key_in_file() -> bool:
    for line in _env_lines():
        if KEY_LINE.match(line):
            value = line.split("=", 1)[1].strip().strip("'\"")
            if value:
                return True
    return False


def _key_source() -> str | None:
    if os.environ.get("ANTHROPIC_API_KEY", "").strip():
        return "environment"
    return ".env" if _key_in_file() else None


def _monitor_alive(status) -> bool:
    if not isinstance(status, dict) or not isinstance(status.get("updated_at"), str):
        return False
    try:
        at = pd.Timestamp(status["updated_at"])
    except (TypeError, ValueError):
        return False
    at = at.tz_localize("UTC") if at.tzinfo is None else at.tz_convert("UTC")
    return abs((pd.Timestamp.now(tz="UTC") - at).total_seconds()) <= STATUS_FRESH_S


def _newest_hour(status) -> str | None:
    if isinstance(status, dict) and isinstance(status.get("newest_hour"), str):
        return status["newest_hour"]
    if not Path(MARKET_DB).exists():
        return None
    try:
        con = _ro()
        try:
            (ts,) = con.execute("SELECT MAX(ts) FROM facts_hourly").fetchone()
        finally:
            con.close()
    except (sqlite3.Error, HTTPException):
        return None
    return None if ts is None else pd.Timestamp(ts).strftime("%Y-%m-%dT%H:00:00Z")


def _require_local(request: Request) -> None:
    host = request.client.host if request.client else None
    if host not in LOCAL_CLIENTS:
        raise HTTPException(403, "the API key can be changed only from this computer")


def _settings_reply() -> dict:
    settings, raw, warnings = _merged_settings()
    try:
        status = _read_json(_data_dir() / "brain_status.json")
    except ValueError as exc:
        status, warnings = None, warnings + [str(exc)]
    if status is not None and not isinstance(status, dict):
        status = None
    source = _key_source()
    return {
        "settings": settings,
        "file": raw,
        "defaults": SETTING_DEFAULTS,
        "ranges": {s: {n: {"min": lo, "max": hi} for n, (lo, hi) in f.items()} for s, f in SETTING_RANGES.items()},
        "warnings": warnings,
        "status": status,
        "monitor_alive": _monitor_alive(status),
        "environment": {
            "market_db": str(MARKET_DB), "incidents_dir": str(INCIDENTS_DIR), "incoming_metrics_dir": str(INCOMING_DIR),
            "graph_json": str(GRAPH_JSON), "repository": str(ROOT), "settings_file": str(_data_dir() / "settings.json"),
            "status_file": str(_data_dir() / "brain_status.json"), "env_file": str(ENV_FILE),
            "clock_mode": CLOCK_MODE, "display_timezone": "Europe/Berlin", "newest_hour": _newest_hour(status),
            "key_set": source is not None, "key_source": source,
        },
        "server_now": _utc_now(),
    }


@app.get("/api/settings")
def get_settings():
    """The runtime settings merged with the defaults, their ranges, the monitor's status (brain_status.json, null
    until a monitor writes it) and the paths this console works with. Never the API key, only whether one is set."""
    return _settings_reply()


@app.put("/api/settings")
async def put_settings(request: Request):
    try:
        body = json.loads(await request.body(), parse_constant=_no_constant)
    except ValueError:
        raise HTTPException(422, "the settings must be JSON, without NaN or Infinity")
    values = _validate(body)
    saved = _save_settings(values)
    return {"ok": True, "updated_at": saved["updated_at"], "settings": _merged_settings()[0]}


@app.post("/api/settings/reset")
def reset_settings():
    saved = _save_settings(json.loads(json.dumps(SETTING_DEFAULTS)))
    return {"ok": True, "updated_at": saved["updated_at"], "settings": _merged_settings()[0]}


def _write_env(lines: list[str]) -> None:
    _write_atomic(Path(ENV_FILE), "".join(lines))


@app.post("/api/settings/key")
async def save_key(request: Request):
    """Write or replace ANTHROPIC_API_KEY=<key> in the repository's .env. The key is never sent back or logged; the
    body is parsed here, not by a model, so a refused value is not echoed in a validation error either."""
    _require_local(request)
    try:
        body = json.loads(await request.body())
    except ValueError:
        raise HTTPException(422, "send {\"key\": \"...\"}")
    key = body.get("key") if isinstance(body, dict) else None
    if not isinstance(key, str):
        raise HTTPException(422, "the key must be text")
    key = key.strip()
    if not key:
        raise HTTPException(422, "the key is empty")
    if len(key) > 300:
        raise HTTPException(422, "the key is longer than 300 characters")
    if any(ch.isspace() or ord(ch) < 32 or ch in "\"'#" for ch in key):
        raise HTTPException(422, "the key must not contain spaces, line breaks, quotes or #")
    lines, done = [], False
    for line in _env_lines():
        if KEY_LINE.match(line):
            if not done:
                ending = line[len(line.rstrip("\r\n")):] or "\n"
                lines.append(f"ANTHROPIC_API_KEY={key}{ending}")
                done = True
            continue
        lines.append(line)
    if not done:
        if lines and not lines[-1].endswith(("\n", "\r")):
            lines[-1] += "\n"
        lines.append(f"ANTHROPIC_API_KEY={key}\n")
    _write_env(lines)
    return {"ok": True, "key_set": True, "key_source": _key_source()}


@app.delete("/api/settings/key")
def remove_key(request: Request):
    """Remove the ANTHROPIC_API_KEY line from the repository's .env; the other lines stay as they are."""
    _require_local(request)
    lines = _env_lines()
    kept = [line for line in lines if not KEY_LINE.match(line)]
    if len(kept) != len(lines):
        _write_env(kept)
    source = _key_source()
    return {"ok": True, "key_set": source is not None, "key_source": source}

# ---------------------------------------------------------------- data browser (read only)
BROWSABLE = ("facts_hourly", "ingestion_log", "releases", "calendar", "ground_truth", "sim_meta")
FILTERABLE = ("country", "platform", "category", "channel", "source", "kind")


def _ro() -> sqlite3.Connection:
    if not MARKET_DB.exists():
        raise HTTPException(409, "no database yet")
    return sqlite3.connect(f"file:{Path(MARKET_DB).resolve().as_posix()}?mode=ro", uri=True)


@app.get("/data")
def data_page():
    return _page("data.html")


@app.get("/api/db/tables")
def db_tables():
    con = _ro()
    try:
        out = []
        for t in BROWSABLE:
            cols = [r[1] for r in con.execute(f"PRAGMA table_info({t})")]
            if cols:
                out.append({"name": t, "columns": cols, "rows": con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]})
        return out
    finally:
        con.close()


@app.get("/api/db/rows")
def db_rows(table: str, limit: int = 50, offset: int = 0, newest_first: bool = True, ts_from: str | None = None,
            ts_to: str | None = None, country: str | None = None, platform: str | None = None,
            category: str | None = None, channel: str | None = None, source: str | None = None, kind: str | None = None):
    if table not in BROWSABLE:
        raise HTTPException(422, f"table must be one of {BROWSABLE}")
    limit, offset = max(1, min(limit, 500)), max(0, offset)
    con = _ro()
    try:
        cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
        where, args = [], []
        for name, value in (("country", country), ("platform", platform), ("category", category),
                            ("channel", channel), ("source", source), ("kind", kind)):
            if value and name in cols:
                where.append(f"{name} = ?")
                args.append(value)
        time_col = "ts" if "ts" in cols else "start_ts" if "start_ts" in cols else "injected_at" if "injected_at" in cols else None
        if time_col and ts_from:
            where.append(f"{time_col} >= ?")
            args.append(ts_from)
        if time_col and ts_to:
            where.append(f"{time_col} < ?")
            args.append(ts_to)
        clause = (" WHERE " + " AND ".join(where)) if where else ""
        order = f" ORDER BY {time_col} {'DESC' if newest_first else 'ASC'}" if time_col else ""
        total = con.execute(f"SELECT COUNT(*) FROM {table}{clause}", args).fetchone()[0]
        rows = con.execute(f"SELECT * FROM {table}{clause}{order} LIMIT ? OFFSET ?", args + [limit, offset]).fetchall()
        return {"table": table, "columns": cols, "total": total, "rows": rows}
    finally:
        con.close()
