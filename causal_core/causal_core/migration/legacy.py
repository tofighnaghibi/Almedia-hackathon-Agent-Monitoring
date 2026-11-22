"""One-way migration of v1.0 / v1.1 / v1.2 records to schema v2.0 (v3.2 §2.4).

Rules:
- every input gets a receipt with the sha256 of its exact bytes, and the
  original text is kept;
- migration never raises: anything it cannot vouch for is QUARANTINED with
  reasons;
- migration never grants scope, independence, covariance or authority. Legacy
  records land in LEGACY_UNSCOPED with a content-derived lineage root.
  Historic causal certificates become REVIEW_REQUIRED.
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime
from typing import Literal, Union

from pydantic import Field

from ..contracts.common import UtcDatetime
from ..contracts.common import Contract, SchemaVersion, Sha256, Text
from ..contracts.graph import GraphEdge
from ..contracts.observations import Observation
from ..contracts.variables import MetricDefinition

MIGRATOR_VERSION = "legacy-v1x-to-v2.0/1"
LEGACY_SOURCE = "legacy_v1"
_LEGACY_SCOPE = {"environment_id": "legacy_unscoped", "environment_kind": "legacy_unscoped"}


class MigrationReceipt(Contract):
    original_sha256: Sha256
    original_bytes: int = Field(ge=0)
    source_schema_version: Text | None = None
    source_kind: Text | None = None
    target_schema_version: Literal["2.0"] = "2.0"
    migrator_version: Text
    migrated_at: UtcDatetime


class MigrationResult(Contract):
    schema_version: SchemaVersion
    receipt: MigrationReceipt
    outcome: Literal["MIGRATED", "MIGRATED_REVIEW_REQUIRED", "QUARANTINED"]
    record: Union[Observation, GraphEdge, MetricDefinition, None] = None
    reasons: tuple[Text, ...] = ()
    original_text: str | None = None


def _lineage(sha: str) -> dict:
    return {"source_id": LEGACY_SOURCE, "origin_kind": "legacy_unknown",
            "lineage_root_id": legacy_lineage_root(sha), "content_hash": sha}


def legacy_lineage_root(sha: str) -> str:
    return "legacy:" + sha[:16]


# ── v1 formula strings: a tiny recursive-descent parser, never eval ────────

_TOKEN = re.compile(r"\s*(?:(?P<num>-?\d+(?:\.\d+)?)|(?P<name>[A-Za-z_][A-Za-z0-9_.:\-]*)|(?P<sym>[(),]))")
_OPS = {"add", "sub", "mul", "div"}


def parse_v1_formula(text: str) -> dict:
    """'div(sub(m_a, add(m_b, m_c)), m_d)' -> FormulaNode wire dict. Only add/sub/mul/div."""
    tokens, pos = [], 0
    while pos < len(text.rstrip()):
        m = _TOKEN.match(text, pos)
        if not m or m.end() == pos:
            raise ValueError(f"unexpected character at {pos}: {text[pos:pos + 10]!r}")
        tokens.append(next((k, v) for k, v in m.groupdict().items() if v is not None))
        pos = m.end()

    def expr(i: int) -> tuple[dict, int]:
        kind, val = tokens[i]
        if kind == "num":
            return {"node": "const", "value": float(val)}, i + 1
        if kind != "name":
            raise ValueError(f"expected operand, got {val!r}")
        if i + 1 < len(tokens) and tokens[i + 1] == ("sym", "("):
            if val not in _OPS:
                raise ValueError(f"operation {val!r} not allowed (only {sorted(_OPS)})")
            left, j = expr(i + 2)
            if tokens[j] != ("sym", ","):
                raise ValueError("expected ','")
            right, j = expr(j + 1)
            if tokens[j] != ("sym", ")"):
                raise ValueError("expected ')'")
            return {"node": "binop", "op": val, "left": left, "right": right}, j + 1
        return {"node": "ref", "variable_id": val}, i + 1

    try:
        node, end = expr(0)
    except IndexError:
        raise ValueError("formula ended early") from None
    if end != len(tokens):
        raise ValueError("trailing tokens after formula")
    return node


# ── per-kind migrators: build a v2 wire dict, validate through the public parsers ──


def _observation(obj: dict, sha: str) -> tuple[dict, str, list[str]]:
    stats, window = obj.get("stats") or {}, obj.get("window")
    if "n" not in stats:
        raise _Quarantine("MISSING_SAMPLE_SIZE: v1 SufficientStats without n cannot become AggregateStats")
    payload = {"shape": "aggregate_stats", "n": stats["n"]}
    for key in ("numerator", "denominator", "se", "sum_sq"):
        if key in stats:
            payload[key] = stats[key]
    if window:
        payload["window"] = window
    clocks = {"available_at": {"quality": "UNKNOWN"}}
    if obj.get("event_time"):
        clocks["observed_at"] = {"quality": "KNOWN", "value": obj["event_time"]}
    if obj.get("as_of"):
        clocks["as_of"] = obj["as_of"]
    key = f"{obj['metric_id']}:{(window or {}).get('start', 'nowindow')}"
    return ({"schema_version": "2.0", "observation_id": "legacy-obs:" + sha[:16],
             "variable_id": obj["metric_id"], "payload": payload, "source_id": LEGACY_SOURCE,
             "record_key": key, "revision": obj.get("revision_id", 1), "lineage": _lineage(sha),
             "scope": _LEGACY_SCOPE, "clocks": clocks},
            "MIGRATED", ["iid and covariance not inferred (UNKNOWN)", "available_at UNKNOWN: not replay-safe"])


def _edge(obj: dict, sha: str) -> tuple[dict, str, list[str]]:
    kind = {"HYPOTHESIZED_CAUSAL": "CAUSAL_HYPOTHESIS"}.get(obj["edge_kind"], obj["edge_kind"])
    edge = {"schema_version": "2.0", "edge_id": "legacy-edge:" + sha[:16], "kind": kind,
            "source": obj["source"], "target": obj["target"], "layer": obj.get("layer", "DOMAIN"),
            "status": obj.get("status", "ACTIVE"), "lineage": _lineage(sha), "scope": _LEGACY_SCOPE}
    if "min_lag_seconds" in obj:
        edge["lag"] = {"min_s": obj["min_lag_seconds"], "max_s": obj.get("max_lag_seconds", obj["min_lag_seconds"])}
    notes, outcome = [], "MIGRATED"
    if kind != obj["edge_kind"]:
        notes.append(f"{obj['edge_kind']} renamed {kind}; no certification upgrade")
    if kind == "CAUSALLY_IDENTIFIED":
        reasons = [r for r, present in (("MISSING_ESTIMAND", "estimand" in obj),
                                        ("MISSING_ASSUMPTION_AUDIT", "assumption_audit" in obj),
                                        ("MISSING_SCOPE", False)) if not present]
        edge.update(status="REVIEW_REQUIRED", status_reasons=reasons,
                    identification_ref="legacy:" + str(obj.get("identification_certificate_id", "none")))
        outcome = "MIGRATED_REVIEW_REQUIRED"
        notes.append("historic certificate keeps no authority until re-audited")
    return edge, outcome, notes


def _metric(obj: dict, sha: str) -> tuple[dict, str, list[str]]:
    d = {"schema_version": "2.0", "kind": "metric", "variable_id": obj["metric_id"],
         "value_schema": {"type": "numeric", "dtype": "real"}, "scope": _LEGACY_SCOPE,
         "provenance": {"source_id": LEGACY_SOURCE, "method": "none"}}
    notes = []
    if obj.get("unit"):
        d["unit"] = obj["unit"]
    if obj.get("aggregation_kind") in {"sum", "count", "mean", "ratio", "quantile", "min", "max"}:
        d["aggregation_kind"] = obj["aggregation_kind"]
    if obj.get("time_grain"):
        d["time_semantics"] = {"grain": obj["time_grain"], "window": "unknown"}
        notes.append("window kind not declared in v1: recorded as 'unknown'")
    if obj.get("population"):
        d["population_coverage"] = {"population_id": obj["population"], "coverage": "unknown"}
    if obj.get("formula"):
        d["formula_ast"] = parse_v1_formula(obj["formula"])
    return d, "MIGRATED", notes


class _Quarantine(Exception):
    pass


def migrate_legacy_record(raw: str | bytes, *, migrated_at: datetime) -> MigrationResult:
    from ..api import ContractError, parse_definition, parse_graph_edge, parse_observation  # api re-exports us

    data = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
    sha = hashlib.sha256(data).hexdigest()
    receipt = dict(original_sha256=sha, original_bytes=len(data), migrator_version=MIGRATOR_VERSION,
                   migrated_at=migrated_at)

    def quarantine(*reasons: str, text: str | None = None, ver=None, kind=None) -> MigrationResult:
        return MigrationResult(schema_version="2.0", receipt=MigrationReceipt(**receipt, source_schema_version=ver, source_kind=kind),
                               outcome="QUARANTINED", reasons=tuple(reasons), original_text=text)

    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return quarantine("NOT_UTF8")
    try:
        obj = json.loads(text)
    except json.JSONDecodeError as exc:
        return quarantine(f"NOT_JSON: {exc.msg}", text=text)
    if not isinstance(obj, dict):
        return quarantine("NOT_A_RECORD", text=text)
    ver, kind = obj.get("schema_version"), obj.get("kind")
    if ver not in {"1.0", "1.1", "1.2"}:
        return quarantine(f"UNSUPPORTED_SCHEMA_VERSION: {ver!r}", text=text, ver=str(ver), kind=kind)

    migrators = {"Observation": (_observation, parse_observation),
                 "GraphEdge": (_edge, parse_graph_edge),
                 "MetricDefinition": (_metric, parse_definition)}
    if kind == "ExperimentRecord":
        return quarantine("UNATTESTED_DESIGN: v1 ExperimentRecord randomization claims cannot be verified "
                          "and its srm_p_value was source-supplied (D6)",
                          "RE_INGEST_AS_STUDY_DESIGN with an attested adapter", text=text, ver=ver, kind=kind)
    if kind not in migrators:
        return quarantine(f"UNSUPPORTED_LEGACY_KIND: {kind!r}", text=text, ver=ver, kind=kind)
    build, validate = migrators[kind]
    try:
        wire, outcome, notes = build(obj, sha)
        record = validate(wire)
    except _Quarantine as exc:
        return quarantine(str(exc), text=text, ver=ver, kind=kind)
    except ContractError as exc:
        return quarantine(*(f"MIGRATION_INVALID: {d}" for d in exc.details), text=text, ver=ver, kind=kind)
    except (KeyError, ValueError) as exc:
        detail = f"missing field {exc}" if isinstance(exc, KeyError) else str(exc)
        return quarantine(f"MIGRATION_INVALID: {detail}", text=text, ver=ver, kind=kind)
    return MigrationResult(schema_version="2.0", receipt=MigrationReceipt(**receipt, source_schema_version=ver, source_kind=kind),
                           outcome=outcome, record=record, reasons=tuple(notes), original_text=text)
