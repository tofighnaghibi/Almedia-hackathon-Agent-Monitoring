"""Work that is identical across sessions, done once: the taste model, the fitted default world (seed 42), and the
launch forecast + 20-country backtest for Argentina.

- At Docker build time: `python -m argus.precompute` writes a pickle, so a cold start on a slow CPU only unpickles.
- At API startup: `warm_in_background()` loads that file if its key matches this code, otherwise computes in a
  background thread (requests that need a piece before it is ready compute it themselves; nothing waits on a cold path
  longer than doing the work directly).

The key is a hash of the argus/*.py sources, so a stale cache from older code is never used.
"""
from __future__ import annotations

import hashlib
import os
import pickle
import sys
import threading
import time
from pathlib import Path

PKG = Path(__file__).resolve().parent
DEFAULT_PATH = PKG.parent / ".cache" / "precomputed.pkl"
SEED = 42
LAUNCH = [("AR", SEED)]
_started = False
_done = threading.Event()        # set once the background warm-up finished (loaded or computed)


def wait_ready(timeout: float = 30.0) -> None:
    """If a warm-up is running, wait for it: loading the cache is far cheaper than recomputing it in a request."""
    if _started:
        _done.wait(timeout)


def cache_path() -> Path:
    return Path(os.environ.get("ARGUS_CACHE", DEFAULT_PATH))


def code_key() -> str:
    h = hashlib.sha1()
    for f in sorted(PKG.glob("*.py")):
        h.update(f.name.encode())
        h.update(f.read_bytes())
    return h.hexdigest()


def build() -> dict:
    from . import launch, world

    taste = world.taste_model()
    template = world.template_world(SEED)
    start = world.template_world(SEED, "start")
    for iso, seed in LAUNCH:
        launch.launch(iso, seed)
    return {"key": code_key(), "taste": taste, "template": template, "start": start,
            "launch": {k: launch._LAUNCH[k] for k in LAUNCH}, "learn": {s: launch._CACHE[s] for _, s in LAUNCH}}


def install(blob: dict) -> None:
    from . import launch, world

    world._TASTE = blob["taste"]                 # the template's agent shares this exact object (same pickle)
    world._TEMPLATES[SEED] = blob["template"]
    world._TEMPLATES[(SEED, "start")] = blob["start"]
    launch._LAUNCH.update(blob["launch"])
    launch._CACHE.update(blob["learn"])


def load(path: Path | None = None) -> bool:
    path = path or cache_path()
    try:
        with open(path, "rb") as f:
            blob = pickle.load(f)
        if blob.get("key") != code_key():
            return False
        install(blob)
        return True
    except Exception:  # noqa: BLE001 - missing, stale or incompatible cache: just recompute
        return False


def save(blob: dict, path: Path | None = None) -> Path:
    path = path or cache_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "wb") as f:
        pickle.dump(blob, f, protocol=pickle.HIGHEST_PROTOCOL)
    tmp.replace(path)
    return path


def warm() -> str:
    if load():
        return "loaded"
    blob = build()
    try:
        save(blob)
    except OSError:
        pass
    return "computed"


def warm_in_background() -> None:
    global _started
    if _started or os.environ.get("ARGUS_NO_WARM"):
        return
    _started = True

    def run() -> None:
        t = time.time()
        try:
            how = warm()
        finally:
            _done.set()
        print(f"argus precompute: {how} in {time.time() - t:.1f} s", file=sys.stderr, flush=True)

    threading.Thread(target=run, name="argus-warm", daemon=True).start()


if __name__ == "__main__":
    os.environ.setdefault("ARGUS_NO_DOTENV", "1")
    t = time.time()
    p = save(build())
    print(f"wrote {p} ({p.stat().st_size / 1e6:.1f} MB) in {time.time() - t:.1f} s")
