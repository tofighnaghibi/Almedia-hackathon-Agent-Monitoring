"""Graph admission request and verdict (v3.2 §3.3 eight steps; plan §D.4).

The request is everything a source wants added in one atomic unit; the verdict records, step by
step, what happened to it. Both are persisted with the graph delta.
"""
from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from .common import Contract, EnvironmentScope, Id, SchemaVersion, SourceAttestation, Text, UtcDatetime
from .evidence import EffectClaim
from .graph import EdgeStatus, GraphEdge, GraphNode, StructuralCertificate
from .variables import DimensionSpec, EntitySpec, VariableDefinition

MAX_EDGES_PER_REQUEST = 200


class EffectClaimAttachment(Contract):
    """An EffectClaim attached to the two nodes it concerns. It is an artifact, never an edge."""

    claim: EffectClaim
    treatment_node: Id
    outcome_node: Id


class AdmissionRequest(Contract):
    schema_version: SchemaVersion
    request_id: Id
    source_id: Id
    record_id: Id                       # immutable source record id: the dedup key with source_id
    attestation: SourceAttestation
    scope: EnvironmentScope
    data_cut: UtcDatetime               # identity checks may only use evidence available by now
    effective_from: UtcDatetime | None = None   # domain-valid time of a (re)definition
    definition: VariableDefinition | None = None
    nodes: tuple[GraphNode, ...] = ()
    edges: tuple[GraphEdge, ...] = Field(default=(), max_length=MAX_EDGES_PER_REQUEST)
    entity_specs: tuple[EntitySpec, ...] = ()
    dimension_specs: tuple[DimensionSpec, ...] = ()
    certificates: tuple[StructuralCertificate, ...] = ()
    effect_claims: tuple[EffectClaimAttachment, ...] = ()

    @model_validator(mode="after")
    def _non_empty(self) -> "AdmissionRequest":
        if not (self.definition or self.nodes or self.edges or self.certificates or self.effect_claims
                or self.entity_specs or self.dimension_specs):
            raise ValueError("an admission request must carry something to admit")
        return self


StepStatus = Literal["PASS", "NOT_APPLICABLE", "QUARANTINE", "REJECT", "SKIPPED"]


class StepResult(Contract):
    step: int = Field(ge=1, le=8)
    name: Text
    status: StepStatus
    details: tuple[Text, ...] = ()


class EdgeOutcome(Contract):
    edge_id: Id
    kind: Text
    outcome: Literal["ADMITTED", "QUARANTINED", "REJECTED"]
    status: EdgeStatus | None = None
    reasons: tuple[Text, ...] = ()


class AdmissionVerdict(Contract):
    schema_version: SchemaVersion
    request_id: Id
    outcome: Literal["COMMITTED", "IDEMPOTENT_NOOP", "REJECTED"]
    graph_version: int | None = Field(default=None, ge=0)
    steps: tuple[StepResult, ...]
    edges: tuple[EdgeOutcome, ...] = ()
    notified_subscribers: tuple[Id, ...] = ()
    downstream_reverify: tuple[Id, ...] = ()
