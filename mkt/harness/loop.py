"""M3 harness: output sink (Contracts OutputSinkPort), measurement policy gate, sealed answers, evaluator.

The core publishes TaskRevisions and EvidenceGaps to the sink. A policy gate (not the core) decides
whether an EvidenceGap's recommended measurement is granted in the emulator. Answers are sealed in
the privileged directory before the core runs and opened only by the evaluator.
"""
from __future__ import annotations

import hashlib
import json
import pathlib

from causal_core.contracts.evidence import EvidenceGap
from causal_core.contracts.tasks import TaskRevision


class FileSink:
    """Contracts.ports.OutputSinkPort: append-only, honours the idempotency key."""

    def __init__(self, path: pathlib.Path):
        self.path = pathlib.Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.seen: dict[str, str] = {}

    def publish(self, revision: TaskRevision) -> str:
        if revision.idempotency_key in self.seen:
            return self.seen[revision.idempotency_key]
        receipt = "rcpt-" + revision.idempotency_key[:16]
        with open(self.path, "a") as f:
            f.write(json.dumps({"receipt": receipt, "revision": revision.model_dump(mode="json")}) + "\n")
        self.seen[revision.idempotency_key] = receipt
        return receipt

    def publish_gaps(self, gaps: tuple[EvidenceGap, ...]) -> None:
        with open(self.path, "a") as f:
            for g in gaps:
                f.write(json.dumps({"gap": g.model_dump(mode="json")}) + "\n")


class MeasurementPolicy:
    """Grants 'independent_measurement:<var>' only for hidden streams declared as that variable's identity partners.
    Partners are configured by the scenario author (privileged), not inferred from the core."""

    def __init__(self, partners: dict[str, tuple[str, ...]], max_grants: int = 2):
        self.partners, self.max_grants, self.granted = partners, max_grants, []

    def decide(self, gap: EvidenceGap, emulator) -> list[str]:
        out = []
        for rec in gap.recommended_measurements:
            kind, _, var = rec.partition(":")
            if kind != "independent_measurement":
                continue
            for p in self.partners.get(var, ()):
                if len(self.granted) < self.max_grants and emulator.grant_measurement(p):
                    self.granted.append(p)
                    out.append(p)
        return out


def seal(priv_dir: pathlib.Path, answer: dict) -> str:
    priv_dir.mkdir(parents=True, exist_ok=True)
    body = json.dumps(answer, sort_keys=True)
    (priv_dir / "answer.json").write_text(body)
    return hashlib.sha256(body.encode()).hexdigest()


def open_seal(priv_dir: pathlib.Path, digest: str) -> dict:
    body = (priv_dir / "answer.json").read_text()
    if hashlib.sha256(body.encode()).hexdigest() != digest:
        raise RuntimeError("sealed answer was modified")
    return json.loads(body)
