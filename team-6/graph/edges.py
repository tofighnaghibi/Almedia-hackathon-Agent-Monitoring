"""Typed edge dataclass for the marketplace metric graph."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

VALID_EDGE_TYPES = frozenset({"formula", "breakdown", "possible_cause"})
VALID_STATUSES = frozenset({"confirmed", "uncertain", "rejected"})
VALID_SOURCES = frozenset({"seed", "auto_discovered", "llm_suggested"})


@dataclass
class GraphEdge:
    """Directed relationship between two metrics in the metric graph."""

    from_metric: str
    to_metric: str
    type: str
    formula: str | None = None
    dimension: str | None = None
    confidence: float = 1.0
    lag_hours: int = 0
    source: str = "seed"
    status: str = "uncertain"
    evidence: str = ""

    def __post_init__(self) -> None:
        if self.type not in VALID_EDGE_TYPES:
            raise ValueError(f"Invalid edge type: {self.type!r}")
        if self.status not in VALID_STATUSES:
            raise ValueError(f"Invalid edge status: {self.status!r}")
        if self.source not in VALID_SOURCES:
            raise ValueError(f"Invalid edge source: {self.source!r}")
        if not (0.0 <= float(self.confidence) <= 1.0):
            raise ValueError(f"Confidence must be in [0, 1], got {self.confidence}")
        if self.type == "formula" and not self.formula:
            raise ValueError("Formula edge requires a non-empty 'formula'")
        if self.type == "breakdown" and not self.dimension:
            raise ValueError("Breakdown edge requires a non-empty 'dimension'")

    @property
    def validated(self) -> bool:
        """Read-only compatibility flag: True when status is 'confirmed'."""
        return self.status == "confirmed"

    @property
    def key(self) -> str:
        """Unique key per edge semantics so multiple edges between two nodes coexist."""
        qualifier = self.formula or self.dimension or ""
        return f"{self.type}:{qualifier}"

    def to_dict(self) -> dict[str, Any]:
        """Serialize to a contract-compatible dict (including 'validated')."""
        data: dict[str, Any] = {
            "from": self.from_metric,
            "to": self.to_metric,
            "type": self.type,
        }
        if self.formula is not None:
            data["formula"] = self.formula
        if self.dimension is not None:
            data["dimension"] = self.dimension
        data["confidence"] = float(self.confidence)
        data["validated"] = self.validated
        data["status"] = self.status
        if self.evidence:
            data["evidence"] = self.evidence
        if self.lag_hours:
            data["lag_hours"] = int(self.lag_hours)
        data["source"] = self.source
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GraphEdge:
        """Load from a YAML/JSON dict, supporting legacy 'validated'-only edges."""
        if "status" in data:
            status = data["status"]
        elif data.get("validated") is True:
            status = "confirmed"
        else:
            status = "uncertain"

        return cls(
            from_metric=data["from"],
            to_metric=data["to"],
            type=data["type"],
            formula=data.get("formula"),
            dimension=data.get("dimension"),
            confidence=float(data.get("confidence", 1.0)),
            lag_hours=int(data.get("lag_hours", 0)),
            source=data.get("source", "seed"),
            status=status,
            evidence=str(data.get("evidence", "")),
        )
