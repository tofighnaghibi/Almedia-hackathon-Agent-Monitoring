"""Graph node/edge contracts: all 13 edge kinds kept (v3.2 §3.1).

Phase 1 freezes the record shape and per-edge rules. The bitemporal store,
views and admission (cross-edge rules such as cycle checks) arrive in Phase 2.
"""
from __future__ import annotations

from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from .common import Contract, EnvironmentScope, Id, Probability, ProvenanceLineage, SchemaVersion, Text

EdgeKind = Literal["EXACT_ARITHMETIC", "AGGREGATION_COMPOSITION", "DIMENSIONAL_DECOMPOSITION",
                   "ENTITY_RELATIONSHIP", "TEMPORAL_WINDOW", "TEMPORAL_LAG", "TEMPORAL_FEEDBACK",
                   "OBSERVATION_EFFECT", "OBSERVATIONAL_ASSOCIATION", "CAUSAL_HYPOTHESIS",
                   "CAUSALLY_IDENTIFIED", "LATENT_CONFOUNDING", "UNKNOWN_RELATIONSHIP"]
EdgeStatus = Literal["ACTIVE", "QUARANTINED", "SUPERSEDED", "REFUTED", "REVIEW_REQUIRED"]
NodeKind = Literal["VARIABLE", "ENTITY", "INTERVENTION", "STUDY", "OBSERVATION_MECHANISM", "LATENT",
                   "EFFECT_CLAIM"]
Layer = Literal["DOMAIN", "OBSERVATION"]


class LagBounds(Contract):
    min_s: float = Field(ge=0)
    max_s: float = Field(ge=0)

    @model_validator(mode="after")
    def _ordered(self) -> "LagBounds":
        if self.min_s > self.max_s:
            raise ValueError("min lag exceeds max lag")
        return self


class Validity(Contract):
    valid_from: AwareDatetime | None = None
    valid_to: AwareDatetime | None = None


class GraphNode(Contract):
    schema_version: SchemaVersion
    node_id: Id
    kind: NodeKind
    layer: Layer = "DOMAIN"
    variable_id: Id | None = None

    @model_validator(mode="after")
    def _variable_link(self) -> "GraphNode":
        if self.kind == "VARIABLE" and self.variable_id is None:
            raise ValueError("a VARIABLE node must reference a variable_id")
        if self.kind == "OBSERVATION_MECHANISM" and self.layer != "OBSERVATION":
            raise ValueError("observation mechanisms live in the OBSERVATION layer")
        return self


class GraphEdge(Contract):
    schema_version: SchemaVersion
    edge_id: Id
    kind: EdgeKind
    source: Id
    target: Id
    layer: Layer = "DOMAIN"
    status: EdgeStatus
    status_reasons: tuple[Text, ...] = ()
    lineage: ProvenanceLineage
    scope: EnvironmentScope
    validity: Validity = Validity()
    lag: LagBounds | None = None
    entity_match_confidence: Probability | None = None
    identification_ref: Id | None = None

    @model_validator(mode="after")
    def _edge_rules(self) -> "GraphEdge":
        if self.source == self.target and self.kind != "TEMPORAL_FEEDBACK":
            raise ValueError("self-loop only allowed as positive-lag TEMPORAL_FEEDBACK")
        if self.kind in {"TEMPORAL_LAG", "TEMPORAL_FEEDBACK"} and self.lag is None:
            raise ValueError(f"{self.kind} requires lag bounds")
        if self.kind == "TEMPORAL_FEEDBACK" and self.lag and self.lag.min_s <= 0:
            raise ValueError("feedback needs a strictly positive lag (instantaneous cycles unsupported)")
        if self.kind == "OBSERVATION_EFFECT" and self.layer != "OBSERVATION":
            raise ValueError("OBSERVATION_EFFECT lives in the OBSERVATION layer and cannot become a domain cause")
        if self.kind == "CAUSALLY_IDENTIFIED" and self.status == "ACTIVE" and self.identification_ref is None:
            raise ValueError("an ACTIVE CAUSALLY_IDENTIFIED edge needs an identification_ref")
        if self.status in {"QUARANTINED", "REFUTED", "REVIEW_REQUIRED"} and not self.status_reasons:
            raise ValueError(f"status {self.status} needs at least one reason")
        if self.kind == "ENTITY_RELATIONSHIP" and self.entity_match_confidence is None:
            raise ValueError("ENTITY_RELATIONSHIP needs an entity_match_confidence")
        return self
