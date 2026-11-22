"""Stitch marketplace adapter: marketplace-recon's sample format → core contracts (outside the core; CP-038).

Input: the `stitch_samples` output of marketplace-recon (a .zip or a directory with samples/*.json). Each file holds
one variable's v2.0 definition plus samples of {observation, evidence}. marketplace-recon builds against a Phase 1
copy of the contracts, so two known drifts are handled here, explicitly and counted (user decision 2026-11-22):

* evidence records without `schema_version` (required since CP-002): the version "2.0" is injected ONLY if the record
  then passes the contract unchanged in every other respect; otherwise it is quarantined with the reason;
* timestamps with a non-UTC offset (rejected on the wire since CP-003): converted to UTC by the adapter, as CP-003
  assigns to adapters, with the original offset kept in `clocks.source_timezone`; reported separately.
Anything else that fails is quarantined, never repaired. The zip is read in memory; nothing is extracted or executed.
Variable definitions are offered through `definitions()` (outside the port protocol; no manifest change for now).
"""
from __future__ import annotations

import collections
import copy
import json
import re
import zipfile
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from pydantic import TypeAdapter

from causal_core import api
from causal_core.contracts.evidence import EvidenceRecord

from .canonical_port import CanonicalEvidencePort

_EVIDENCE = TypeAdapter(EvidenceRecord)
_OFFSET_TS = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(:\d{2}(\.\d+)?)?[+-]\d{2}:\d{2}$")


@dataclass
class StitchLoad:
    definitions: dict = field(default_factory=dict)          # variable_id → VariableDefinition
    meta: dict = field(default_factory=dict)                 # variable_id → layer/status/sources/role
    observations: list = field(default_factory=list)         # accepted wire dicts
    evidence: list = field(default_factory=list)             # accepted EvidenceRecord
    quarantine: list = field(default_factory=list)           # (file, item, reason)
    counts: collections.Counter = field(default_factory=collections.Counter)


def _files(path: Path):
    if path.suffix == ".zip":
        with zipfile.ZipFile(path) as z:
            for name in sorted(z.namelist()):
                if name.endswith(".json") and "/" in name and not name.startswith("__MACOSX"):
                    yield Path(name).name, json.loads(z.read(name))
    else:
        root = path / "samples" if (path / "samples").is_dir() else path
        for p in sorted(root.glob("*.json")):
            yield p.name, json.loads(p.read_text())


def _to_utc(obj, offsets: set):
    """Convert every ISO timestamp with a non-UTC offset in a JSON tree to UTC 'Z'; collect the offsets seen."""
    if isinstance(obj, dict):
        return {k: _to_utc(v, offsets) for k, v in obj.items()}
    if isinstance(obj, list):
        return [_to_utc(v, offsets) for v in obj]
    if isinstance(obj, str) and _OFFSET_TS.match(obj) and not obj.endswith(("+00:00", "-00:00")):
        offsets.add(obj[-6:])
        return datetime.fromisoformat(obj).astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    return obj


def _first_error(exc) -> str:
    """The first error with its field: pydantic puts the count on line 1 and the field/message on lines 2-3."""
    lines = [l.strip() for l in str(exc).splitlines() if l.strip() and not l.strip().startswith("For further")]
    return " · ".join(lines[:3])[:300]


def _accept_observation(raw: dict, dfn, counts) -> tuple[dict | None, str]:
    """(accepted wire dict or None, reason). Only the CP-003 conversion is applied; nothing else is repaired."""
    try:
        api.parse_observation(raw, dfn)
        counts["observations_accepted"] += 1
        return raw, ""
    except Exception as e:  # noqa: BLE001
        if "must be UTC on the wire" not in str(e):
            return None, _first_error(e)
    offsets: set = set()
    fixed = _to_utc(copy.deepcopy(raw), offsets)
    clocks = dict(fixed.get("clocks") or {})
    clocks["source_timezone"] = "UTC" + ",".join(sorted(offsets))
    fixed["clocks"] = clocks
    try:
        api.parse_observation(fixed, dfn)
        counts["observations_converted_to_utc"] += 1
        return fixed, ""
    except Exception as e2:  # noqa: BLE001
        return None, _first_error(e2)


def load_stitch(path: str | Path) -> StitchLoad:
    out = StitchLoad()
    for fname, obj in _files(Path(path)):
        out.counts["files"] += 1
        try:
            dfn = api.parse_definition(obj["definition"])
        except Exception as e:  # noqa: BLE001
            out.quarantine.append((fname, "definition", _first_error(e)))
            out.counts["definitions_quarantined"] += 1
            continue
        out.definitions[dfn.variable_id] = dfn
        out.counts["definitions"] += 1
        ext = obj["definition"].get("extensions") or {}
        out.meta[dfn.variable_id] = {"layer": ext.get("mkt:layer"), "status": ext.get("mkt:status"),
                                     "sources": ext.get("mkt:sources"), "kind": dfn.kind,
                                     "environment": dfn.scope.environment_id, "environment_kind": dfn.scope.environment_kind}
        for i, s in enumerate(obj.get("samples", [])):
            out.counts["observations_seen"] += 1
            accepted, why = _accept_observation(s["observation"], dfn, out.counts)
            if accepted is not None:
                out.observations.append(accepted)
            else:
                out.quarantine.append((fname, f"observation[{i}]", why))
                out.counts["observations_quarantined"] += 1
            ev = s.get("evidence")
            if ev is None:
                continue
            out.counts["evidence_seen"] += 1
            try:
                out.evidence.append(_EVIDENCE.validate_json(json.dumps(ev)))
                out.counts["evidence_accepted"] += 1
            except Exception as e:  # noqa: BLE001
                if "schema_version" in str(e) and "schema_version" not in ev:
                    try:                                   # inject only if everything else complies
                        out.evidence.append(_EVIDENCE.validate_json(json.dumps({**ev, "schema_version": "2.0"})))
                        out.counts["evidence_schema_version_injected"] += 1
                        continue
                    except Exception as e2:  # noqa: BLE001
                        e = e2
                out.quarantine.append((fname, f"evidence[{i}]", _first_error(e)))
                out.counts["evidence_quarantined"] += 1
    for o in out.observations:
        m = out.meta.get(o["variable_id"], {})
        role = next((e.provenance_role for e in out.evidence if e.evidence_id == f"ev:{o['observation_id']}"), None)
        if role:
            m.setdefault("roles", collections.Counter())[role] += 1
    return out


class StitchEvidencePort(CanonicalEvidencePort):
    """Read-only port over the accepted Stitch observations; definitions offered outside the protocol."""

    def __init__(self, load: StitchLoad, provider_id: str = "stitch-marketplace-adapter", **kw):
        super().__init__(load.observations, provider_id, **kw)
        self._load = load

    def definitions(self) -> dict:
        return dict(self._load.definitions)
