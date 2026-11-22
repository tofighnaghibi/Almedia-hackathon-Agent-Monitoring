"""Content-addressed artifact cache with atomic writes (docs §8.5)."""
from __future__ import annotations

import hashlib
import json
import pathlib
import shutil
import tempfile
from contextlib import contextmanager
from typing import Any, Iterator

from . import __version__


def stable_hash(obj: Any, n: int = 16) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()[:n]


def code_hash(*modules: Any) -> str:
    """Hash of the source of the given modules + package version (invalidates cache on code edits)."""
    h = hashlib.sha256(__version__.encode())
    for m in modules:
        h.update(pathlib.Path(m.__file__).read_bytes())
    return h.hexdigest()[:12]


def file_sha256(path: pathlib.Path, chunk: int = 1 << 20) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while b := f.read(chunk):
            h.update(b)
    return h.hexdigest()


class ArtifactStore:
    def __init__(self, root: pathlib.Path):
        self.root = pathlib.Path(root)

    def path(self, stage: str, key: str) -> pathlib.Path:
        return self.root / stage / key

    def get(self, stage: str, key: str) -> pathlib.Path | None:
        p = self.path(stage, key)
        return p if (p / "manifest.json").exists() else None

    @contextmanager
    def write(self, stage: str, key: str, manifest: dict) -> Iterator[pathlib.Path]:
        """Yield a temp dir; on success it is atomically renamed into place with a manifest.
        On error the temp dir is removed and any previous artifact is left untouched."""
        final = self.path(stage, key)
        final.parent.mkdir(parents=True, exist_ok=True)
        tmp = pathlib.Path(tempfile.mkdtemp(prefix=f".{key}-", dir=final.parent))
        try:
            yield tmp
            (tmp / "manifest.json").write_text(json.dumps(manifest, indent=1, default=str))
            if final.exists():
                shutil.rmtree(final)
            tmp.rename(final)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
