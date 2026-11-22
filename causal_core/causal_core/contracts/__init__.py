"""Canonical schema v2.0 contracts (frozen, strict, closed)."""
from .capabilities import CapabilityRequest, CapabilityResponse, ProviderManifest
from .common import (SCHEMA_VERSION, Clock, Contract, EnvironmentScope, ProvenanceLineage, SourceAttestation,
                     TimeWindow)
from .evidence import EffectClaim, EvidenceGap, EvidenceRecord, IdentificationAssessment, Uncertainty
from .graph import GraphEdge, GraphNode
from .observations import ALLOWED_SHAPES, Observation, ObservationClocks, ValuePayload
from .studies import EstimandSpec, StudyDesign
from .tasks import Checkpoint, ReasoningTask, TaskRevision, WorkItem
from .variables import (METRIC_ONLY_FIELDS, CategoricalVariableDefinition, DimensionSpec, EntitySpec,
                        EventVariableDefinition, MetricDefinition, StateVariableDefinition, VariableDefinition)

__all__ = [n for n in dir() if not n.startswith("_")]
