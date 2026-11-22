"""L5 Hypothesis generation, symptom-vs-cause classification, ranking, and labeling."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Literal

from graph.edges import GraphEdge
from graph.metric_graph import MetricGraph
from monitoring.data_quality import DQResult
from monitoring.event_calendar import ExplanationResult

HypothesisSource = Literal[
    "graph_cause_edge",
    "event",
    "data_quality",
    "formula_component",
    "segment",
]
HypothesisStatus = Literal["confirmed", "uncertain", "rejected"]


@dataclass
class Hypothesis:
    """Candidate explanation for an anomaly."""

    cause: str
    source: HypothesisSource
    node: str
    segment: dict[str, str] = field(default_factory=dict)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    confidence: float = 0.0
    status: HypothesisStatus = "uncertain"
    explained_share: float = 0.0
    timing_aligned: bool = False
    localized: bool = False
    mechanism_confirmed: bool = False
    counterfactual_passed: bool = False
    missing_conditions: list[str] = field(default_factory=list)
    contradicted: bool = False
    is_symptom: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def label_hypothesis(h: Hypothesis) -> Hypothesis:
    """Apply the strict 4-condition confirmation rule to ``h``.

    A hypothesis is ``confirmed`` ONLY if all four hold:
      1. ``localized``: change is localized to a slice
      2. ``timing_aligned``: timing aligns with an event or upstream change
      3. ``mechanism_confirmed``: the mechanism edge in the graph is confirmed
      4. ``counterfactual_passed``: counterfactual check passes
    Contradicted hypotheses become ``rejected``; anything else is ``uncertain``
    with ``missing_conditions`` listing every unmet condition.
    """
    if h.contradicted:
        h.status = "rejected"
        h.missing_conditions = []
        return h

    missing: list[str] = []
    if not h.localized:
        missing.append("localized_to_slice")
    if not h.timing_aligned:
        missing.append("timing_aligned")
    if not h.mechanism_confirmed:
        missing.append("mechanism_confirmed")
    if not h.counterfactual_passed:
        missing.append("counterfactual_passed")

    h.missing_conditions = missing
    h.status = "confirmed" if not missing else "uncertain"
    return h


def classify_symptom_vs_cause(
    node: str,
    graph: MetricGraph,
    is_anomalous_fn: Callable[[str], bool],
) -> Literal["symptom", "first_cause", "normal"]:
    """Classify ``node`` as ``symptom`` if any non-rejected causal parent is anomalous."""
    if not is_anomalous_fn(node):
        return "normal"

    parents: set[str] = set()
    if node in graph.graph:
        for u, _, data in graph.graph.in_edges(node, data=True):
            edge: GraphEdge = data["edge"]
            if u == node or edge.status == "rejected":
                continue
            if edge.type in {"formula", "possible_cause"}:
                parents.add(u)

    for parent in sorted(parents):
        if is_anomalous_fn(parent):
            return "symptom"
    return "first_cause"


def rank_hypotheses(hypotheses: list[Hypothesis]) -> list[Hypothesis]:
    """Rank hypotheses deterministically using a documented scoring formula.

    Formula:
        ``score = (0.40 * clamp(explained_share, 0, 1)``
        ``       + 0.20 * (1.0 if timing_aligned else 0.0)``
        ``       + 0.20 * (1.0 if mechanism_confirmed else 0.0)``
        ``       + 0.20 * clamp(confidence, 0, 1))``
        multiplied by ``0.5`` if ``is_symptom`` is True.
    Ordering: possible first causes before symptoms; then ``confirmed`` >
    ``uncertain`` > ``rejected``, then descending ``score``, then ascending
    ``(cause, node)``. A confirmed symptom (cart adds fell) never outranks the
    cause it is a symptom of (out-of-stock rate rose).
    """
    status_order = {"confirmed": 0, "uncertain": 1, "rejected": 2}

    def _score(h: Hypothesis) -> float:
        share = max(0.0, min(1.0, float(h.explained_share)))
        conf = max(0.0, min(1.0, float(h.confidence)))
        base = (
            0.40 * share
            + 0.20 * (1.0 if h.timing_aligned else 0.0)
            + 0.20 * (1.0 if h.mechanism_confirmed else 0.0)
            + 0.20 * conf
        )
        return base * (0.5 if h.is_symptom else 1.0)

    return sorted(
        hypotheses,
        key=lambda h: (h.status == "rejected", h.is_symptom, status_order[h.status], -round(_score(h), 6),
                       h.cause, h.node),
    )


def event_scope(ev: Any) -> dict[str, str]:
    """The dimensions an event is limited to, e.g. {"country": "DE", "platform": "android"}."""
    return {d: getattr(ev, d) for d in ("country", "platform", "category") if getattr(ev, d, None)}


def scope_covers(scope: dict[str, str], segment: dict[str, str]) -> bool:
    """True when an event with ``scope`` applies to the whole of ``segment``."""
    return all(segment.get(d) == v for d, v in scope.items())


def build_initial_hypotheses(
    metric: str,
    segment: dict[str, str],
    graph: MetricGraph,
    events: ExplanationResult | None = None,
    dq: DQResult | None = None,
) -> list[Hypothesis]:
    """Build initial hypotheses from graph cause edges, events, and formula components."""
    hyps: list[Hypothesis] = []
    seen_keys: set[tuple[str, str, str]] = set()

    if dq is not None and (not dq.fresh or not dq.complete):
        hyps.append(
            Hypothesis(
                cause=f"Incomplete or stale data for {metric}",
                source="data_quality",
                node=metric,
                segment=dict(segment),
                evidence=[{"type": "data_quality", "detail": dq.detail}],
                confidence=0.9,
                status="uncertain",
            )
        )

    if events is not None:
        for ev in events.data_delay_events:
            hyps.append(
                Hypothesis(
                    cause=f"Data delay event {ev.event_id}: {ev.description or 'late data'}",
                    source="data_quality",
                    node=metric,
                    segment=dict(segment),
                    evidence=[{"type": "data_delay", "event_id": ev.event_id}],
                    confidence=0.8,
                    timing_aligned=True,
                )
            )
        for ev in events.unplanned_events:
            hyps.append(
                Hypothesis(
                    cause=f"Unplanned {ev.event_type} {ev.event_id}: {ev.description or ev.event_type}",
                    source="event",
                    node=metric,
                    segment=dict(segment),
                    evidence=[{"type": "event", "event_id": ev.event_id, "description": ev.description,
                               "scope": event_scope(ev)}],
                    confidence=0.65,
                    timing_aligned=True,
                )
            )

    if metric in graph.graph:
        for u, _, data in graph.graph.in_edges(metric, data=True):
            edge: GraphEdge = data["edge"]
            if edge.status == "rejected":
                continue
            if edge.type == "possible_cause":
                key = ("graph_cause_edge", u, edge.key)
                if key not in seen_keys:
                    seen_keys.add(key)
                    hyps.append(
                        Hypothesis(
                            cause=f"Upstream cause {u} -> {metric}",
                            source="graph_cause_edge",
                            node=u,
                            segment=dict(segment),
                            evidence=[{"type": "graph_edge", "edge": edge.to_dict()}],
                            confidence=float(edge.confidence),
                            mechanism_confirmed=(edge.status == "confirmed"),
                        )
                    )
            elif edge.type == "formula":
                key = ("formula_component", u, edge.key)
                if key not in seen_keys:
                    seen_keys.add(key)
                    hyps.append(
                        Hypothesis(
                            cause=f"Formula component {u} of {metric}",
                            source="formula_component",
                            node=u,
                            segment=dict(segment),
                            evidence=[{"type": "formula_edge", "formula": edge.formula}],
                            confidence=0.5 if edge.status != "confirmed" else 0.7,
                            mechanism_confirmed=(edge.status == "confirmed"),
                        )
                    )

    return [label_hypothesis(h) for h in hyps]
