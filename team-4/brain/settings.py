"""The console's runtime settings and the live monitor's status, both next to the market database.

settings.json is written by the console (atomically); every field is optional:

    {"narration": {"enabled": false},
     "clock": {"seconds_per_hour": 2.0},
     "detection": {"min_surprise": 7.0, "min_effect": 0.10, "volume_min_effect": 0.20, "release_min_surprise": 4.0},
     "updated_at": "2026-11-22T11:20:00Z"}

A value in the file wins. A missing one (or null) falls back to the command line (--narrate; sim.live --speed),
then to the default in FIELDS, which are today's brain.monitor.Config and sim.live --speed. A value outside its
range is clamped and the reader says so; a value of the wrong type is left out and the reader says so; a file
that cannot be parsed counts as empty, with the reason in `error`. A missing file is not an error: it is the
state before the console saved anything. The replay (brain.run --replay) and the scoring never read it.

brain_status.json is written by the live monitor on every poll (brain/run.py, Control) and read by the console.
It never holds the API key or any part of it.

Standard library only.
"""
from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

SETTINGS_NAME = "settings.json"
STATUS_NAME = "brain_status.json"

# "section.name" -> (default, lowest, highest); a switch has no range
FIELDS: dict[str, tuple] = {
    "narration.enabled": (False, None, None),
    "clock.seconds_per_hour": (2.0, 0.25, 60.0),          # the accelerated clock only, never --wall-clock
    "detection.min_surprise": (7.0, 5.0, 10.0),           # the scan's -log10 p: 7 is p below 1e-7
    "detection.min_effect": (0.10, 0.03, 0.50),           # a rate's relative floor
    "detection.volume_min_effect": (0.20, 0.05, 0.60),    # a count's relative floor
    "detection.release_min_surprise": (4.0, 3.0, 7.0),    # the watch after a release
}
DEFAULTS = {name: spec[0] for name, spec in FIELDS.items()}
DETECTION = ("min_surprise", "min_effect", "volume_min_effect", "release_min_surprise")   # brain.monitor.Config


@dataclass(frozen=True)
class Settings:
    values: dict                       # "section.name" -> value: only what the file says, checked and clamped
    warnings: tuple[str, ...] = ()     # values clamped or left out
    error: str | None = None           # the file could not be used at all: it counts as empty

    def get(self, name: str, fallback=None):
        return self.values.get(name, fallback)

    def problems(self) -> str | None:
        """Everything a reader should be told, in one line, or None."""
        said = ([self.error] if self.error else []) + list(self.warnings)
        return "; ".join(said) or None


def settings_path(db) -> Path:
    return Path(db).resolve().parent / SETTINGS_NAME


def status_path(db) -> Path:
    return Path(db).resolve().parent / STATUS_NAME


def stamp(path) -> tuple | None:
    """What tells a changed file cheaply: modification time and size, None when there is no file."""
    if path is None:
        return None
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    return st.st_mtime_ns, st.st_size


def _shown(value) -> str:
    try:
        text = json.dumps(value, ensure_ascii=False)
    except (TypeError, ValueError):
        text = repr(value)
    return text if len(text) <= 40 else text[:37] + "..."


def parse(obj) -> Settings:
    """Check a parsed settings.json."""
    if not isinstance(obj, dict):
        return Settings({}, error=f"{SETTINGS_NAME} is not a JSON object; the command line and the defaults are in use")
    values, warnings, bad_sections = {}, [], set()
    for name, (default, low, high) in FIELDS.items():
        section, key = name.split(".")
        part = obj.get(section)
        if part is None:
            continue
        if not isinstance(part, dict):
            if section not in bad_sections:
                bad_sections.add(section)
                warnings.append(f"{section} {_shown(part)} is not an object: left out")
            continue
        value = part.get(key)
        if value is None:
            continue
        if isinstance(default, bool):
            if isinstance(value, bool):
                values[name] = value
            else:
                warnings.append(f"{name} {_shown(value)} is not true or false: left out")
            continue
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            warnings.append(f"{name} {_shown(value)} is not a number: left out")
            continue
        used = float(min(max(value, low), high))
        if used != value:
            warnings.append(f"{name} {value:g} is outside {low:g} to {high:g}: {used:g} is used")
        values[name] = used
    return Settings(values, tuple(warnings))


def read(path) -> Settings:
    """Read settings.json. A missing file says nothing; a broken one counts as empty and says why. Any other
    OSError (on Windows, the file being replaced at that moment) is raised: the caller tries again later."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8-sig")
    except FileNotFoundError:
        return Settings({})
    except UnicodeDecodeError:
        return Settings({}, error=f"{path.name} is not UTF-8 text; the command line and the defaults are in use")
    try:
        obj = json.loads(text)
    except ValueError as e:
        why = f"{e.msg}, line {e.lineno}" if isinstance(e, json.JSONDecodeError) else str(e)
        return Settings({}, error=f"{path.name} could not be parsed ({why}); the command line and the defaults are in use")
    return parse(obj)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_status(path, obj: dict) -> None:
    """Write whole, UTF-8 with LF, under a temporary name, then rename. On Windows the rename fails while a
    reader holds the old file open, so it is retried a few times; then the error is raised."""
    path = Path(path)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write(json.dumps(obj, indent=1, ensure_ascii=False, allow_nan=False) + "\n")
    for attempt in range(10):
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            if attempt == 9:
                try:
                    os.remove(tmp)
                except OSError:
                    pass
                raise
            time.sleep(0.02)


__all__ = ["Settings", "FIELDS", "DEFAULTS", "DETECTION", "settings_path", "status_path", "stamp", "parse", "read",
           "write_status", "utc_now", "SETTINGS_NAME", "STATUS_NAME"]
