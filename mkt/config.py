"""Project configuration (mkt.toml) and path resolution."""
from __future__ import annotations

import os
import pathlib
import tomllib
from dataclasses import dataclass

ROOT = pathlib.Path(__file__).resolve().parents[1]


def _path(p: str, base: pathlib.Path) -> pathlib.Path:
    q = pathlib.Path(os.path.expandvars(os.path.expanduser(p)))
    return q if q.is_absolute() else base / q


@dataclass(frozen=True)
class Config:
    data: pathlib.Path
    artifacts: pathlib.Path
    runs: pathlib.Path
    fit_until: str

    @classmethod
    def load(cls, path: str | os.PathLike | None = None) -> "Config":
        path = pathlib.Path(path or os.environ.get("MKT_CONFIG", ROOT / "mkt.toml"))
        raw = tomllib.loads(path.read_text()) if path.exists() else {}
        base = path.parent
        p = raw.get("paths", {})
        return cls(
            data=_path(os.environ.get("MKT_DATA", p.get("data", "~/.cache/mkt/data")), base),
            artifacts=_path(os.environ.get("MKT_ARTIFACTS", p.get("artifacts", "artifacts")), base),
            runs=_path(os.environ.get("MKT_RUNS", p.get("runs", "runs")), base),
            fit_until=raw.get("calibration", {}).get("fit_until", "2018-01-01"),
        )
