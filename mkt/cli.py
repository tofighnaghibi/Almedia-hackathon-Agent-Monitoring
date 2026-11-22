"""mkt command-line interface (docs §11)."""
from __future__ import annotations

import argparse
import json
import pathlib
import sys
import time
from datetime import datetime, timedelta

from .config import Config


def _p(obj) -> None:
    print(json.dumps(obj, indent=1, default=str))


def cmd_source(a, cfg):
    from . import registry
    if a.action == "add":
        m = registry.register(a.ns, cfg.data, cfg.artifacts, pathlib.Path(a.path) if a.path else None)
        _p({"registered": m.ns, "version": m.version, "licence": m.licence, "path": m.path})
    else:
        _p([{"ns": m.ns, "version": m.version, "licence": m.licence, "path": m.path} for m in registry.list_sources(cfg.artifacts)])


def cmd_semantics(a, cfg):
    from . import registry, semantics
    src = pathlib.Path(registry.load("olist", cfg.artifacts).path)
    res = semantics.audit(src)
    out = pathlib.Path("docs/p0"); out.mkdir(parents=True, exist_ok=True)
    (out / "phase1_semantics.json").write_text(json.dumps(res, indent=1, default=str))
    (out / "PHASE1_SOURCE_SEMANTICS.md").write_text(semantics.report_md(res))
    _p({"gates": res["gates"], "report": str(out / "PHASE1_SOURCE_SEMANTICS.md")})
    return 0 if all(v == "PASS" for v in res["gates"].values()) else 1


def cmd_ingest(a, cfg):
    from . import pipeline
    p = pipeline.canonical_olist(cfg, force=a.force)
    _p({"canonical": str(p), **json.loads((p / "stats.json").read_text())})


def cmd_profile(a, cfg):
    import polars as pl
    from . import pipeline
    p = pipeline.canonical_olist(cfg)
    rows = []
    for f in sorted(p.glob("*.parquet")):
        df = pl.read_parquet(f)
        for c in df.columns:
            s = df[c]
            rows.append({"table": f.stem, "column": c, "dtype": str(s.dtype), "rows": df.height,
                         "null_pct": round(100 * s.null_count() / max(df.height, 1), 2),
                         "distinct": s.n_unique(), "min": str(s.min()) if s.dtype.is_numeric() or s.dtype.is_temporal() else "",
                         "max": str(s.max()) if s.dtype.is_numeric() or s.dtype.is_temporal() else ""})
    with pl.Config(tbl_rows=200, tbl_cols=10, fmt_str_lengths=30):
        print(pl.DataFrame(rows))


def cmd_fit(a, cfg):
    from . import pipeline
    P, key = pipeline.params(cfg, force=a.force)
    _p({"params_key": key, "provenance": P["_provenance"], "daily_orders": P["daily_orders"],
        "holdout": pipeline.holdout(cfg)})


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="mkt", description=__doc__)
    ap.add_argument("--config", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("source", help="register or list raw sources")
    s.add_argument("action", choices=["add", "list"]); s.add_argument("ns", nargs="?"); s.add_argument("--path")
    s.set_defaults(fn=cmd_source)
    s = sub.add_parser("semantics", help="phase-1 Olist field dictionary + gates S1/S2"); s.set_defaults(fn=cmd_semantics)
    s = sub.add_parser("ingest", help="build canonical Olist tables + events"); s.add_argument("--force", action="store_true"); s.set_defaults(fn=cmd_ingest)
    s = sub.add_parser("profile", help="profile canonical tables"); s.set_defaults(fn=cmd_profile)
    s = sub.add_parser("fit", help="calibrate mechanisms (with held-out stats)"); s.add_argument("--force", action="store_true"); s.set_defaults(fn=cmd_fit)
    a = ap.parse_args(argv)
    cfg = Config.load(a.config)
    if a.cmd == "source" and a.action == "add" and not a.ns:
        ap.error("source add needs a namespace")
    rc = a.fn(a, cfg)
    return int(rc or 0)


if __name__ == "__main__":
    sys.exit(main())
