"""Three bounded, read-only semantic views over a pinned snapshot (v3.2 §3.4).

StructuralView: identities, compositions, decompositions, entity links.
TemporalView: windows, lags, positive-lag feedback.
CausalEvidenceView: associations, hypotheses, identified links, latents, unknowns, observation
effects, plus effect claims and certificates.

Absent adjacency is UNKNOWN, never KNOWN_ABSENT (I08). Traversal is always bounded and says when
it stopped early (TRUNCATED + reason). No method writes. Views never import the store's mutator.
"""
from __future__ import annotations

import time
from collections import deque
from dataclasses import dataclass, field
from typing import Literal

from ..contracts.evidence import EffectClaim
from ..contracts.graph import GraphEdge
from .store import Snapshot

STRUCTURAL = frozenset({"EXACT_ARITHMETIC", "AGGREGATION_COMPOSITION", "DIMENSIONAL_DECOMPOSITION",
                        "ENTITY_RELATIONSHIP"})
TEMPORAL = frozenset({"TEMPORAL_WINDOW", "TEMPORAL_LAG", "TEMPORAL_FEEDBACK"})
CAUSAL_EVIDENCE = frozenset({"OBSERVATION_EFFECT", "OBSERVATIONAL_ASSOCIATION", "CAUSAL_HYPOTHESIS",
                             "CAUSALLY_IDENTIFIED", "LATENT_CONFOUNDING", "UNKNOWN_RELATIONSHIP"})


@dataclass(frozen=True)
class TraversalBound:
    """v3.2 §3.2 starting configuration. max_wall_ms is a cancellation threshold, not a guarantee."""

    max_hops: int = 3
    max_nodes: int = 100
    max_edges: int = 400
    include_quarantined: bool = False
    include_superseded: bool = False
    max_wall_ms: float = 50.0


@dataclass(frozen=True)
class Relationship:
    status: Literal["KNOWN", "UNKNOWN"]
    active: tuple[GraphEdge, ...] = ()
    inactive: tuple[GraphEdge, ...] = ()     # quarantined / refuted / superseded / review-required


@dataclass(frozen=True)
class Neighborhood:
    seed: str
    nodes: tuple[str, ...]
    edges: tuple[GraphEdge, ...]
    truncated: bool
    reason: str | None = None
    hops_reached: int = 0


@dataclass(frozen=True)
class _View:
    snapshot: Snapshot
    kinds: frozenset = field(default=frozenset())

    def _usable(self, e: GraphEdge, bound: TraversalBound) -> bool:
        if e.kind not in self.kinds:
            return False
        if e.status == "ACTIVE":
            return True
        return (e.status == "QUARANTINED" and bound.include_quarantined) or \
               (e.status == "SUPERSEDED" and bound.include_superseded)

    def relationship(self, a: str, b: str) -> Relationship:
        """Edges of this view's kinds between a and b, either direction. Nothing recorded -> UNKNOWN."""
        edges = [e for e in self.snapshot.out_edges(a) if e.target == b and e.kind in self.kinds] + \
                [e for e in self.snapshot.out_edges(b) if e.target == a and e.kind in self.kinds]
        active = tuple(e for e in edges if e.status == "ACTIVE")
        inactive = tuple(e for e in edges if e.status != "ACTIVE")
        return Relationship("KNOWN" if active else "UNKNOWN", active, inactive)

    def neighborhood(self, seed: str, bound: TraversalBound = TraversalBound(),
                     direction: Literal["out", "in", "both"] = "both") -> Neighborhood:
        start = time.perf_counter()
        seen, edges, queue = {seed}, [], deque([(seed, 0)])
        hops, edge_ids, hop_limited = 0, set(), False
        while queue:
            if (time.perf_counter() - start) * 1000 > bound.max_wall_ms:
                return Neighborhood(seed, tuple(seen), tuple(edges), True, "MAX_WALL_MS", hops)
            node, depth = queue.popleft()
            hops = max(hops, depth)
            if depth >= bound.max_hops:
                # stop expanding, but record that reachable usable edges were left unexplored
                hop_limited = hop_limited or any(
                    self._usable(e, bound) and e.edge_id not in edge_ids for e in self._incident(node, direction))
                continue
            for e in self._incident(node, direction):
                if not self._usable(e, bound) or e.edge_id in edge_ids:
                    continue
                if len(edges) >= bound.max_edges:
                    return Neighborhood(seed, tuple(seen), tuple(edges), True, "MAX_EDGES", hops)
                other = e.target if e.source == node else e.source
                if other not in seen and len(seen) >= bound.max_nodes:
                    return Neighborhood(seed, tuple(seen), tuple(edges), True, "MAX_NODES", hops)
                edge_ids.add(e.edge_id)
                edges.append(e)
                if other not in seen:
                    seen.add(other)
                    queue.append((other, depth + 1))
        return Neighborhood(seed, tuple(seen), tuple(edges), hop_limited, "MAX_HOPS" if hop_limited else None, hops)

    def _incident(self, node: str, direction: str):
        if direction in {"out", "both"}:
            yield from self.snapshot.out_edges(node)
        if direction in {"in", "both"}:
            yield from self.snapshot.in_edges(node)


@dataclass(frozen=True)
class StructuralView(_View):
    kinds: frozenset = STRUCTURAL

    def formula_parents(self, variable_id: str) -> tuple[str, ...]:
        return tuple(sorted(e.source for e in self.snapshot.in_edges(variable_id)
                            if e.kind in {"EXACT_ARITHMETIC", "AGGREGATION_COMPOSITION"} and e.status == "ACTIVE"))

    def exact_closure_edges(self, node: str) -> tuple[GraphEdge, ...]:
        """Edges usable for *exact* arithmetic closure: entity links below confidence 1 are excluded (§3.1)."""
        return tuple(e for e in self._incident(node, "both") if e.kind in self.kinds and e.status == "ACTIVE"
                     and not (e.kind == "ENTITY_RELATIONSHIP" and (e.entity_match_confidence or 0) < 1.0))


@dataclass(frozen=True)
class TemporalView(_View):
    kinds: frozenset = TEMPORAL

    def lagged_parents(self, node: str) -> tuple[tuple[str, float, float], ...]:
        return tuple((e.source, e.lag.min_s, e.lag.max_s) for e in self.snapshot.in_edges(node)
                     if e.kind in {"TEMPORAL_LAG", "TEMPORAL_FEEDBACK"} and e.status == "ACTIVE" and e.lag)


@dataclass(frozen=True)
class CausalEvidenceView(_View):
    kinds: frozenset = CAUSAL_EVIDENCE

    def effect_claims(self, treatment: str, outcome: str) -> tuple[EffectClaim, ...]:
        return self.snapshot.effect_claims(treatment, outcome)

    def observation_mechanisms(self, variable: str) -> tuple[str, ...]:
        return tuple(sorted(e.source for e in self.snapshot.in_edges(variable)
                            if e.kind == "OBSERVATION_EFFECT" and e.status == "ACTIVE"))

    def unknowns_relevant_to(self, treatment: str, outcome: str) -> tuple[tuple[str, str], ...]:
        """Only the pairs that matter for identifying treatment -> outcome (C07, F09), not every unknown pair.

        Treatment-outcome itself, plus each recorded cause of the treatment against the outcome (a
        potential back-door path) when that pair has no recorded relationship.
        """
        pairs = [(treatment, outcome)]
        causes = {e.source for e in self.snapshot.in_edges(treatment)
                  if e.kind in {"CAUSAL_HYPOTHESIS", "CAUSALLY_IDENTIFIED", "LATENT_CONFOUNDING"}
                  and e.status == "ACTIVE"}
        pairs += [(c, outcome) for c in sorted(causes) if c != outcome]
        return tuple(p for p in pairs if self.relationship(*p).status == "UNKNOWN")
