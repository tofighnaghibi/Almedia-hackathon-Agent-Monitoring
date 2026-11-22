"""Immutable-snapshot investigation state for L5."""

from __future__ import annotations

import copy
import json
from dataclasses import dataclass, field
from typing import Any


@dataclass
class InvestigationState:
    """Tracks anomaly context, hypotheses, trail, evidence, and stop state."""

    anomaly: dict[str, Any]
    hypotheses: list[dict[str, Any]] = field(default_factory=list)
    trail: list[dict[str, Any]] = field(default_factory=list)
    evidence: list[dict[str, Any]] = field(default_factory=list)
    explained_share: float = 0.0
    steps_used: int = 0
    llm_calls_used: int = 0
    fallbacks_used: int = 0
    fallback_reasons: list[str] = field(default_factory=list)
    stop_reason: str = ""
    completed_actions: list[str] = field(default_factory=list)
    active_formula_by_metric: dict[str, str] = field(default_factory=dict)
    # contribution tree from decomposition: metric -> {share, parent, decomposed}
    tree: dict[str, dict[str, Any]] = field(default_factory=dict)
    # how much of the anomaly each localized driver explains (driver share x localized share)
    explained_parts: dict[str, float] = field(default_factory=dict)
    # unplanned events found by check_events, with their scope
    events_found: list[dict[str, Any]] = field(default_factory=list)

    def snapshot(self) -> InvestigationState:
        """Return a deep copy so callers cannot mutate shared state."""
        return copy.deepcopy(self)

    def compact_summary(self) -> str:
        """Short text summary suitable for LLM prompts (no raw data)."""
        a = self.anomaly
        seg = ", ".join(f"{k}={v}" for k, v in sorted((a.get("segment") or {}).items())) or "overall"
        top_hyps = [
            f"{h.get('cause')} [{h.get('status')}, conf={h.get('confidence', 0):.2f}]"
            for h in self.hypotheses[:3]
        ]
        recent = [
            f"#{t.get('step')} {t.get('action')}: {t.get('observation')}"
            for t in self.trail[-3:]
        ]
        return (
            f"Metric: {a.get('metric')} ({seg}), actual={a.get('actual')}, "
            f"expected={a.get('expected')}, explained_share={self.explained_share:.2f}, "
            f"steps_used={self.steps_used}\n"
            f"Top hypotheses: {'; '.join(top_hyps) if top_hyps else 'none'}\n"
            f"Recent trail: {' | '.join(recent) if recent else 'none'}"
        )

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable copy of the state."""
        return copy.deepcopy(
            {
                "anomaly": self.anomaly,
                "hypotheses": self.hypotheses,
                "trail": self.trail,
                "evidence": self.evidence,
                "explained_share": round(float(self.explained_share), 6),
                "steps_used": int(self.steps_used),
                "llm_calls_used": int(self.llm_calls_used),
                "fallbacks_used": int(self.fallbacks_used),
                "fallback_reasons": list(self.fallback_reasons),
                "stop_reason": self.stop_reason,
                "completed_actions": list(self.completed_actions),
                "active_formula_by_metric": dict(self.active_formula_by_metric),
                "tree": dict(self.tree),
                "explained_parts": dict(self.explained_parts),
                "events_found": list(self.events_found),
            }
        )

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), indent=2)
