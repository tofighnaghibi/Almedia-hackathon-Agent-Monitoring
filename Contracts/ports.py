"""Abstract ports (v3.2 §1.1). P0 ports are read-only or output-only.

There is deliberately no write, execute or actuate method on any P0 port
(I15, G17). The P1 sandbox TestExecutionPort is added in Phase 8, behind the
policy gate.
"""
from __future__ import annotations

from typing import Protocol, runtime_checkable

from .capabilities import CapabilityRequest, CapabilityResponse, ProviderManifest
from .tasks import ReasoningTask, TaskRevision


@runtime_checkable
class EvidenceAccessPort(Protocol):
    def manifest(self) -> ProviderManifest: ...
    def query(self, request: CapabilityRequest) -> CapabilityResponse: ...


@runtime_checkable
class OutputSinkPort(Protocol):
    def publish(self, revision: TaskRevision) -> str:
        """Publish a policy-vetted revision; returns the sink's receipt id. Must honor the idempotency key."""
        ...


@runtime_checkable
class HypothesisProposerPort(Protocol):
    def propose(self, task: ReasoningTask) -> tuple[str, ...]:
        """Candidate hypotheses only: no estimates, no truth, no authority."""
        ...


P0_PORTS = (EvidenceAccessPort, OutputSinkPort, HypothesisProposerPort)
