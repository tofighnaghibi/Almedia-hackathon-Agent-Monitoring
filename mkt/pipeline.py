"""Cached stages: ingest -> calibrate. Each artifact key hashes its inputs, config and code."""
from __future__ import annotations

import json
import pathlib

from . import calibrate, registry
from .adapters import olist, retailrocket
from .config import Config
from .store import ArtifactStore, code_hash, stable_hash


def canonical_olist(cfg: Config, force: bool = False) -> pathlib.Path:
    store = ArtifactStore(cfg.artifacts)
    m = registry.load("olist", cfg.artifacts)
    key = stable_hash({"src": m.version, "code": code_hash(olist)})
    if not force and (p := store.get("canonical-olist", key)):
        return p
    with store.write("canonical-olist", key, {"source": m.ns, "source_version": m.version,
                                               "licence": m.licence, "timezone": "America/Sao_Paulo (naive local)"}) as tmp:
        stats = olist.build(m, tmp)
        (tmp / "stats.json").write_text(json.dumps(stats))
    return store.path("canonical-olist", key)


def params(cfg: Config, force: bool = False) -> tuple[dict, str]:
    """Return (params, params_key). Retailrocket is optional: without it the funnel is assumed (A-02b)."""
    store = ArtifactStore(cfg.artifacts)
    canon = canonical_olist(cfg)
    try:
        rr = registry.load("retailrocket", cfg.artifacts)
    except registry.MissingSource:
        rr = None
    key = stable_hash({"canon": canon.name, "rr": rr.version if rr else None, "fit_until": cfg.fit_until,
                       "code": code_hash(calibrate, retailrocket)})
    p = store.get("params", key)
    if force or not p:
        funnel = retailrocket.funnel_stats(rr) if rr else None
        P = calibrate.fit(canon, cfg.fit_until, funnel)
        hold = calibrate.holdout_stats(canon, cfg.fit_until)
        with store.write("params", key, {"canonical": canon.name, "retailrocket": rr.version if rr else None,
                                         "fit_until": cfg.fit_until}) as tmp:
            (tmp / "params.json").write_text(json.dumps(P, indent=1))
            (tmp / "holdout.json").write_text(json.dumps(hold, indent=1))
        p = store.path("params", key)
    return json.loads((p / "params.json").read_text()), key


def holdout(cfg: Config) -> dict:
    _, key = params(cfg)
    return json.loads((ArtifactStore(cfg.artifacts).path("params", key) / "holdout.json").read_text())
