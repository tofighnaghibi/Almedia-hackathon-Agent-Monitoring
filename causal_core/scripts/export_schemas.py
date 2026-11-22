"""Freeze the schema v2.0 wire contracts as JSON Schema files (plan §C deliverable "frozen schemas").

    python -m scripts.export_schemas            # write docs/phase1/schemas/*.schema.json + SHA256SUMS
    python -m scripts.export_schemas --check    # exit 1 if the models drifted from the frozen files

A drift is not automatically wrong. It means a contract changed, and the change needs a
decision-log entry and re-approval before re-freezing.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys

from pydantic import TypeAdapter

from causal_core import contracts as c
from causal_core.contracts import admission, capabilities, evidence, graph, mechanisms, studies, tasks, variables
from causal_core.migration.legacy import MigrationResult
from evaluation.fixture_io import PROJECT_ROOT

OUT = PROJECT_ROOT / "docs" / "phase1" / "schemas"

WIRE_CONTRACTS = {
    "VariableDefinition": c.VariableDefinition, "EntitySpec": variables.EntitySpec,
    "DimensionSpec": variables.DimensionSpec, "Observation": c.Observation,
    "StudyDesign": c.StudyDesign, "EstimandSpec": studies.EstimandSpec, "ReasoningTask": c.ReasoningTask,
    "GraphNode": c.GraphNode, "GraphEdge": c.GraphEdge, "EvidenceRecord": evidence.EvidenceRecord,
    "IdentificationAssessment": evidence.IdentificationAssessment, "EffectClaim": evidence.EffectClaim,
    "EvidenceGap": evidence.EvidenceGap, "CapabilityRequest": c.CapabilityRequest,
    "CapabilityResponse": capabilities.CapabilityResponse, "ProviderManifest": capabilities.ProviderManifest,
    "TaskRevision": tasks.TaskRevision, "WorkItem": tasks.WorkItem, "Checkpoint": tasks.Checkpoint,
    "MigrationResult": MigrationResult,
    # Phase 2 (CP-007): additive wire contracts
    "StructuralCertificate": graph.StructuralCertificate, "AdmissionRequest": admission.AdmissionRequest,
    "AdmissionVerdict": admission.AdmissionVerdict,
    # Phase 7 (P1, user-authorized 2026-11-22; CP-034): additive
    "MechanismSpec": mechanisms.MechanismSpec, "CounterfactualPrediction": mechanisms.CounterfactualPrediction,
    "MechanismSet": mechanisms.MechanismSet,     # CP-034 fix (shape sets), user-approved 2026-11-22
}


def render() -> dict[str, str]:
    return {f"{name}.schema.json": json.dumps(TypeAdapter(t).json_schema(), indent=2, sort_keys=True) + "\n"
            for name, t in WIRE_CONTRACTS.items()}


def check() -> list[str]:
    """Names of schema files whose frozen copy differs from the current models (or is missing/extra)."""
    current = render()
    on_disk = {p.name: p.read_text() for p in OUT.glob("*.schema.json")} if OUT.exists() else {}
    return sorted(n for n in set(current) | set(on_disk) if current.get(n) != on_disk.get(n))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true")
    if ap.parse_args(argv).check:
        drift = check()
        print("schemas match frozen files" if not drift else f"DRIFT: {drift}")
        return 1 if drift else 0
    OUT.mkdir(parents=True, exist_ok=True)
    files = render()
    for name, text in files.items():
        (OUT / name).write_text(text)
    sums = [f"{hashlib.sha256(t.encode()).hexdigest()}  {n}" for n, t in sorted(files.items())]
    (OUT / "SHA256SUMS").write_text("\n".join(sums) + "\n")
    print(f"wrote {len(files)} schemas to {OUT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
