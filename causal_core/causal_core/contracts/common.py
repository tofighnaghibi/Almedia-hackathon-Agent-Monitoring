"""Shared building blocks for schema v2.0 contracts (v3.2 §2).

Every contract is frozen, closed (unknown fields are errors) and strict (no
silent coercion: "3" is not 3, 3.0 is not an int, a naive datetime is not a
time). Optional fields are *conditional*, not defaulted guesses: absence means
"not declared", and the operation that needs a field checks for it.
"""
from __future__ import annotations

import re
from typing import Annotated, Literal

from datetime import datetime, timedelta

from pydantic import (AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field,
                      StringConstraints, model_validator)

SCHEMA_VERSION = "2.0"
SchemaVersion = Literal["2.0"]


class Contract(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid", strict=True, allow_inf_nan=False)


Id = Annotated[str, StringConstraints(min_length=1, max_length=200,
                                      pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:\-/]*$")]
Text = Annotated[str, StringConstraints(min_length=1, pattern=r"\S")]
Sha256 = Annotated[str, StringConstraints(pattern=r"^[0-9a-f]{64}$")]
Probability = Annotated[float, Field(ge=0.0, le=1.0)]
Grain = Annotated[str, StringConstraints(pattern=r"^[1-9][0-9]*(m|h|d|w)$")]


def _require_utc(value: datetime) -> datetime:
    """v3.2 §2.2: UTC on the wire. Adapters convert and record the source zone; the core never guesses (CP-003)."""
    if value.utcoffset() != timedelta(0):
        raise ValueError(f"timestamps must be UTC on the wire (got offset {value.utcoffset()}); convert in the "
                         "adapter and record the original zone in source_timezone")
    return value


UtcDatetime = Annotated[AwareDatetime, AfterValidator(_require_utc)]

# ── clocks (v3.2 §2.2) ─────────────────────────────────────────────────────

ClockQuality = Literal["KNOWN", "ESTIMATED", "UNKNOWN"]


class Clock(Contract):
    """A timestamp with an honest quality label. UNKNOWN never carries a value."""

    quality: ClockQuality
    value: UtcDatetime | None = None

    @model_validator(mode="after")
    def _consistent(self) -> "Clock":
        if self.quality == "UNKNOWN" and self.value is not None:
            raise ValueError("UNKNOWN clock must not carry a value (do not manufacture timestamps)")
        if self.quality != "UNKNOWN" and self.value is None:
            raise ValueError(f"{self.quality} clock needs a value")
        return self


class TimeWindow(Contract):
    start: UtcDatetime
    end: UtcDatetime

    @model_validator(mode="after")
    def _ordered(self) -> "TimeWindow":
        if self.end <= self.start:
            raise ValueError("window end must be after start")
        return self


# ── scope and provenance (v3.2 §2.1, §6.1) ─────────────────────────────────

EnvironmentKind = Literal["production", "staging", "emulator", "offline_replay", "legacy_unscoped"]


class EnvironmentScope(Contract):
    environment_id: Id
    environment_kind: EnvironmentKind
    data_cut: UtcDatetime | None = None
    dataset_version: Text | None = None
    generator_version: Text | None = None

    @model_validator(mode="after")
    def _generated_needs_version(self) -> "EnvironmentScope":
        if self.environment_kind == "emulator" and self.generator_version is None:
            raise ValueError("emulator scope requires generator_version")
        return self


AttestationMethod = Literal["adapter_declared", "signed", "none"]


class SourceAttestation(Contract):
    """Who vouches for a record. A string label is not trust; 'signed' needs a signature."""

    source_id: Id
    method: AttestationMethod
    attested_at: UtcDatetime | None = None
    signature: Text | None = None

    @model_validator(mode="after")
    def _signed_has_signature(self) -> "SourceAttestation":
        if self.method == "signed" and self.signature is None:
            raise ValueError("method 'signed' requires a signature")
        return self


OriginKind = Literal["original", "synthetic_generated", "synthetic_replica", "model_prediction",
                     "external_study", "legacy_unknown"]


class ProvenanceLineage(Contract):
    """Ancestry, not independence: distinct roots do not imply independent evidence (I10)."""

    source_id: Id
    origin_kind: OriginKind
    lineage_root_id: Id
    parent_evidence_ids: tuple[Id, ...] = ()
    generator_version: Text | None = None
    content_hash: Sha256
    transformation_hash: Sha256 | None = None

    @model_validator(mode="after")
    def _origin_rules(self) -> "ProvenanceLineage":
        generated = {"synthetic_generated", "synthetic_replica", "model_prediction"}
        if self.origin_kind in generated and self.generator_version is None:
            raise ValueError(f"origin_kind {self.origin_kind} requires generator_version")
        if self.origin_kind == "synthetic_replica" and not self.parent_evidence_ids:
            raise ValueError("a synthetic_replica must name the evidence it replicates")
        return self


# ── namespaced extensions: carried, never interpreted by core ──────────────

_NS_KEY = re.compile(r"^[a-z0-9_]+:[A-Za-z0-9_.\-]+$")


def _namespaced(d: dict) -> dict:
    bad = [k for k in d if not _NS_KEY.match(k)]
    if bad:
        raise ValueError(f"extension keys must be namespaced like 'vendor:key': {bad}")
    return d


Extensions = Annotated[dict[str, str | int | float | bool | None], AfterValidator(_namespaced)]
