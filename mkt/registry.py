"""Source registry: what raw sources exist, where, which files/versions, under which licence."""
from __future__ import annotations

import json
import pathlib
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone

from .store import file_sha256

# Known sources: expected files and licence (docs §3). Adapters refuse sources not listed here.
KNOWN = {
    "olist": {
        "subdir": "olist",
        "files": ["olist_orders_dataset.csv", "olist_order_items_dataset.csv",
                  "olist_order_payments_dataset.csv", "olist_order_reviews_dataset.csv",
                  "olist_customers_dataset.csv", "olist_sellers_dataset.csv",
                  "olist_products_dataset.csv", "product_category_name_translation.csv"],
        "licence": "CC BY-NC-SA 4.0",
        "kaggle": "olistbr/brazilian-ecommerce",
    },
    "retailrocket": {
        "subdir": "retailrocket",
        "files": ["events.csv"],
        "licence": "CC BY-NC-SA 4.0",
        "kaggle": "retailrocket/ecommerce-dataset",
    },
}


@dataclass
class SourceManifest:
    ns: str
    path: str
    licence: str
    files: dict[str, str] = field(default_factory=dict)   # name -> sha256
    registered_at: str = ""

    @property
    def version(self) -> str:
        from .store import stable_hash
        return stable_hash(self.files, 12)


class MissingSource(RuntimeError):
    pass


def manifest_path(artifacts: pathlib.Path, ns: str) -> pathlib.Path:
    return artifacts / "registry" / f"{ns}.json"


def register(ns: str, data_root: pathlib.Path, artifacts: pathlib.Path, path: pathlib.Path | None = None) -> SourceManifest:
    if ns not in KNOWN:
        raise MissingSource(f"unknown source '{ns}'; known: {sorted(KNOWN)}")
    spec = KNOWN[ns]
    root = pathlib.Path(path) if path else data_root / spec["subdir"]
    missing = [f for f in spec["files"] if not (root / f).exists()]
    if missing:
        raise MissingSource(f"{ns}: missing {missing} under {root}. "
                            f"Download with: kaggle datasets download {spec['kaggle']} -p {root} --unzip")
    m = SourceManifest(ns=ns, path=str(root), licence=spec["licence"],
                       files={f: file_sha256(root / f) for f in spec["files"]},
                       registered_at=datetime.now(timezone.utc).isoformat(timespec="seconds"))
    out = manifest_path(artifacts, ns)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(asdict(m), indent=1))
    return m


def load(ns: str, artifacts: pathlib.Path) -> SourceManifest:
    p = manifest_path(artifacts, ns)
    if not p.exists():
        raise MissingSource(f"source '{ns}' is not registered; run: mkt source add {ns}")
    return SourceManifest(**json.loads(p.read_text()))


def list_sources(artifacts: pathlib.Path) -> list[SourceManifest]:
    d = artifacts / "registry"
    return [SourceManifest(**json.loads(p.read_text())) for p in sorted(d.glob("*.json"))] if d.exists() else []
