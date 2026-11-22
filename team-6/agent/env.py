"""Load settings from a local ``.env`` file into the environment.

Only the command-line entry points call this (the demo and the scripts), so
tests never pick up a real key. A variable already set in the shell wins over
the file. Values are never printed or logged.

    KEY=value          # comment lines and blank lines are skipped
    KEY="quoted value"
    export KEY=value   # "export " is allowed
"""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def load_env(path: str | Path | None = None) -> list[str]:
    """Set variables from ``path`` (default: .env in the project folder) that are not set yet.

    Returns the names that were set (never the values). A missing file is fine.
    """
    f = Path(path) if path is not None else ROOT / ".env"
    if not f.is_file():
        return []
    loaded = []
    for line in f.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):]
        name, value = line.split("=", 1)
        name, value = name.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        elif value.startswith("#"):  # "KEY=   # comment": empty
            value = ""
        elif " #" in value:
            value = value.split(" #", 1)[0].strip()
        if name and value and not os.environ.get(name, "").strip():
            os.environ[name] = value
            loaded.append(name)
    return loaded
