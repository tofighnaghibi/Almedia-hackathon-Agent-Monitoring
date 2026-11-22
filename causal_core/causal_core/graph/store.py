"""Bitemporal, version-indexed graph store (v3.2 §3.2; CP-006: MVCC version lists, not copy-on-write).

Knowledge time is the integer graph version; domain time is each record's validity interval.
Every node/edge/certificate/claim keeps an append-only list of versions. A snapshot is an O(1)
handle (store, version, valid_at). Reads resolve the latest version <= pin by bisection.

`apply()` is the only mutator. It is called by GraphAdmissionCoordinator *after* the delta is
durably committed, or by `rebuild()` while replaying the log (§3.2 "never leave memory ahead of disk").
"""
from __future__ import annotations

import bisect
import hashlib
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterator

from ..contracts.evidence import EffectClaim
from ..contracts.graph import GraphEdge, GraphNode, StructuralCertificate, Validity


@dataclass(frozen=True)
class NodeRecord:
    node: GraphNode
    definition: object | None          # VariableDefinition for VARIABLE nodes
    validity: Validity


@dataclass(frozen=True)
class ClaimRecord:
    treatment_node: str
    outcome_node: str
    claim: EffectClaim


@dataclass(frozen=True)
class GraphDelta:
    """Everything one admission commits atomically, at exactly one new graph version."""

    version: int
    request_id: str
    request_fingerprint: str
    source_id: str
    record_id: str
    committed_at: datetime
    nodes: tuple[NodeRecord, ...] = ()
    edges: tuple[GraphEdge, ...] = ()
    certificates: tuple[StructuralCertificate, ...] = ()
    claims: tuple[ClaimRecord, ...] = ()
    verdict_json: str = "{}"


def _active_in(validity: Validity, valid_at: datetime | None) -> bool:
    if valid_at is None:
        return True
    if validity.valid_from is not None and valid_at < validity.valid_from:
        return False
    return validity.valid_to is None or valid_at < validity.valid_to


class _Versioned:
    """Append-only (recorded_version, value) list with bisect lookup."""

    __slots__ = ("recorded", "values")

    def __init__(self) -> None:
        self.recorded: list[int] = []
        self.values: list[object] = []

    def put(self, version: int, value: object) -> None:
        if self.recorded and version < self.recorded[-1]:
            raise ValueError("versions must be appended in order")
        self.recorded.append(version)
        self.values.append(value)

    def at(self, version: int):
        i = bisect.bisect_right(self.recorded, version) - 1
        return self.values[i] if i >= 0 else None


class GraphStore:
    def __init__(self) -> None:
        self.version = 0
        self._nodes: dict[str, _Versioned] = {}
        self._edges: dict[str, _Versioned] = {}
        self._out: dict[str, list[str]] = {}
        self._in: dict[str, list[str]] = {}
        self._certs: dict[str, _Versioned] = {}
        self._claims: dict[tuple[str, str], list[tuple[int, EffectClaim]]] = {}

    # ── the only mutation path ─────────────────────────────────────────────
    def apply(self, delta: GraphDelta) -> None:
        if delta.version != self.version + 1:
            raise ValueError(f"delta {delta.version} does not follow store version {self.version}")
        v = delta.version
        by_node: dict[str, list[NodeRecord]] = {}
        for rec in delta.nodes:                     # several records for one node = valid-time segments
            by_node.setdefault(rec.node.node_id, []).append(rec)
        for nid, segments in by_node.items():
            self._nodes.setdefault(nid, _Versioned()).put(v, tuple(segments))
        for e in delta.edges:
            if e.edge_id not in self._edges:
                self._out.setdefault(e.source, []).append(e.edge_id)
                self._in.setdefault(e.target, []).append(e.edge_id)
            self._edges.setdefault(e.edge_id, _Versioned()).put(v, e)
        for c in delta.certificates:
            self._certs.setdefault(c.certificate_id, _Versioned()).put(v, c)
        for cr in delta.claims:
            self._claims.setdefault((cr.treatment_node, cr.outcome_node), []).append((v, cr.claim))
        self.version = v

    def pin(self, version: int | None = None, valid_at: datetime | None = None) -> "Snapshot":
        """O(1): no copying, just a handle. Traversal cost is paid at read time (F08)."""
        v = self.version if version is None else version
        if not 0 <= v <= self.version:
            raise ValueError(f"version {v} outside 0..{self.version}")
        return Snapshot(self, v, valid_at)


@dataclass(frozen=True)
class Snapshot:
    """Immutable read handle. Every method is side-effect free (I03, §3.2)."""

    store: GraphStore = field(repr=False)
    version: int
    valid_at: datetime | None = None

    def node_segments(self, node_id: str) -> tuple[NodeRecord, ...]:
        """All valid-time segments of a node as known at this version (knowledge time)."""
        h = self.store._nodes.get(node_id)
        segs = h.at(self.version) if h else None
        return tuple(segs or ())

    def node(self, node_id: str) -> NodeRecord | None:
        """The segment valid at `valid_at`; without a domain time, the latest-starting segment."""
        segs = self.node_segments(node_id)
        if not segs:
            return None
        if self.valid_at is None:
            return segs[-1]
        return next((r for r in segs if _active_in(r.validity, self.valid_at)), None)

    def node_ids(self) -> Iterator[str]:
        for nid in self.store._nodes:
            if self.node(nid) is not None:
                yield nid

    def edge(self, edge_id: str) -> GraphEdge | None:
        h = self.store._edges.get(edge_id)
        e = h.at(self.version) if h else None
        return e if e is not None and _active_in(e.validity, self.valid_at) else None

    def out_edges(self, node_id: str) -> Iterator[GraphEdge]:
        for eid in self.store._out.get(node_id, ()):
            e = self.edge(eid)
            if e is not None:
                yield e

    def in_edges(self, node_id: str) -> Iterator[GraphEdge]:
        for eid in self.store._in.get(node_id, ()):
            e = self.edge(eid)
            if e is not None:
                yield e

    def last_changed(self, node_id: str) -> int:
        """Latest knowledge version <= this pin that touched the node or an incident edge (stale-pin checks)."""
        versions = []
        h = self.store._nodes.get(node_id)
        if h:
            versions += [v for v in h.recorded if v <= self.version]
        for eid in (*self.store._out.get(node_id, ()), *self.store._in.get(node_id, ())):
            versions += [v for v in self.store._edges[eid].recorded if v <= self.version]
        return max(versions, default=0)

    def certificate(self, cert_id: str) -> StructuralCertificate | None:
        h = self.store._certs.get(cert_id)
        return h.at(self.version) if h else None

    def effect_claims(self, treatment: str, outcome: str) -> tuple[EffectClaim, ...]:
        return tuple(c for v, c in self.store._claims.get((treatment, outcome), ()) if v <= self.version)

    def digest(self) -> str:
        """Canonical hash of everything visible at this pin: the replay/rebuild equality check."""
        nodes = sorted((nid, rec.node.model_dump_json(), rec.validity.model_dump_json(),
                        rec.definition.model_dump_json() if rec.definition is not None else "")
                       for nid in self.store._nodes for rec in self.node_segments(nid))
        edges = sorted(e.model_dump_json() for eid in self.store._edges if (e := self.edge(eid)) is not None)
        certs = sorted(c.model_dump_json() for cid in self.store._certs if (c := self.certificate(cid)) is not None)
        claims = sorted(f"{k}:{c.model_dump_json()}" for k, lst in self.store._claims.items()
                        for v, c in lst if v <= self.version)
        blob = json.dumps([self.version, nodes, edges, certs, claims], sort_keys=True)
        return hashlib.sha256(blob.encode()).hexdigest()
