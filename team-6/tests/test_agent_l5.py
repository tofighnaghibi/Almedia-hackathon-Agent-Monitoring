"""Tests for L5 investigation agent: state, hypotheses, menu, choosers, and loop."""

from __future__ import annotations

import json
import tempfile
from typing import Any

import pandas as pd
import pytest

from agent.chooser import FallbackChooser, LLMChooser
from agent.hypotheses import Hypothesis, classify_symptom_vs_cause, label_hypothesis
from agent.llm_client import ChatClient, extract_first_json_object
from agent.loop import Limits, investigate
from agent.menu import build_menu
from agent.state import InvestigationState
from graph import GraphEdge, MetricGraph
from monitoring.data_quality import DQResult
from monitoring.event_calendar import EventCalendar
from monitoring.triage import AdaptedAnomaly


class _FakeTools:
    """Deterministic fake tools implementing InvestigationTools."""

    def __init__(
        self,
        *,
        formula_driver: str = "payment_success_rate",
        formula_share: float = 0.94,
        localized_segment: dict[str, str] | None = None,
        drill_share: float = 0.92,
        counterfactual_passes: bool = True,
        cause_anomalous: bool = False,
        fail_first_call: bool = False,
    ) -> None:
        self.formula_driver = formula_driver
        self.formula_share = formula_share
        self.localized_segment = localized_segment if localized_segment is not None else {"country": "DE", "platform": "android"}
        self.drill_share = drill_share
        self.counterfactual_passes = counterfactual_passes
        self.cause_anomalous = cause_anomalous
        self.fail_first_call = fail_first_call
        self.calls: list[str] = []

    def decompose_formula(self, metric: str, start: Any, end: Any, segment: dict[str, str]) -> dict[str, Any]:
        self.calls.append(f"decompose:{metric}")
        if self.fail_first_call:
            self.fail_first_call = False
            raise RuntimeError("simulated decompose failure")
        return {
            "formula": "orders = payment_attempts * payment_success_rate",
            "actual": 400.0,
            "baseline": 1000.0,
            "drivers": [
                {
                    "metric": self.formula_driver,
                    "share": self.formula_share,
                    "actual": 0.35,
                    "baseline": 0.95,
                    "rel_change": -0.63,
                }
            ],
            "note": "",
        }

    def drill_dimension(self, metric: str, start: Any, end: Any, segment: dict[str, str]) -> dict[str, Any]:
        self.calls.append(f"drill:{metric}")
        return {
            "metric": metric,
            "localized_segment": dict(self.localized_segment),
            "share": self.drill_share,
            "summary": f"{metric} localized to {self.localized_segment} ({100 * self.drill_share:.0f}%)",
        }

    def check_cause_edge(
        self, from_metric: str, to_metric: str, start: Any, end: Any, segment: dict[str, str], lag_hours: int = 0
    ) -> dict[str, Any]:
        self.calls.append(f"cause:{from_metric}->{to_metric}")
        return {"from_metric": from_metric, "actual": 10.0, "baseline": 10.0, "anomalous": self.cause_anomalous}

    def counterfactual(
        self, metric: str, start: Any, end: Any, suspect_segment: dict[str, str], component: str | None = None
    ) -> dict[str, Any]:
        self.calls.append(f"counterfactual:{suspect_segment}")
        return {
            "passed": self.counterfactual_passes,
            "recovery_share": 0.92 if self.counterfactual_passes else 0.15,
            "detail": "recovers 92%" if self.counterfactual_passes else "only recovers 15%",
        }

    def business_impact(self, metric: str, start: Any, end: Any, segment: dict[str, str]) -> dict[str, Any]:
        self.calls.append("impact")
        return {"lost_orders": 600, "lost_revenue_eur": 36000.0}


class _MockChatClient:
    """Mocked ChatClient returning scripted replies or raising errors."""

    model = "mock-qwen"

    def __init__(self, replies: list[Any]) -> None:
        self._replies = list(replies)
        self.idx = 0

    def complete_json(self, system: str, user: str) -> dict[str, Any]:
        del system, user
        item = self._replies[min(self.idx, len(self._replies) - 1)]
        self.idx += 1
        if isinstance(item, Exception):
            raise item
        if isinstance(item, str):
            return extract_first_json_object(item)
        return item


def _make_graph(confirmed: bool = True) -> MetricGraph:
    mg = MetricGraph()
    status = "confirmed" if confirmed else "uncertain"
    mg.add_edge(
        GraphEdge(
            "payment_attempts",
            "orders",
            "formula",
            formula="orders = payment_attempts * payment_success_rate",
            status=status,
        )
    )
    mg.add_edge(
        GraphEdge("out_of_stock_rate", "orders", "possible_cause", confidence=0.5, status="uncertain")
    )
    return mg


def _make_calendar(with_release: bool = True, with_promo: bool = False) -> EventCalendar:
    rows = []
    if with_release:
        rows.append(
            {
                "event_id": "evt_rel",
                "start_time": "2026-10-15T09:45:00Z",
                "end_time": None,
                "event_type": "release",
                "planned": False,
                "country": "DE",
                "platform": "android",
                "category": None,
                "description": "Android checkout v4.2",
                "expected_effect": None,
            }
        )
    if with_promo:
        rows.append(
            {
                "event_id": "evt_promo",
                "start_time": "2026-10-15T00:00:00Z",
                "end_time": "2026-10-16T00:00:00Z",
                "event_type": "campaign",
                "planned": True,
                "country": "DE",
                "platform": None,
                "category": None,
                "description": "Planned promo",
                "expected_effect": "+25% orders",
            }
        )
    if not rows:
        return EventCalendar([])
    return EventCalendar.from_dataframe(pd.DataFrame(rows))


def _make_anomaly(metric: str = "orders") -> AdaptedAnomaly:
    return AdaptedAnomaly(
        metric=metric,
        segment={"country": "DE"},
        start=pd.Timestamp("2026-10-15T10:00:00Z"),
        end=pd.Timestamp("2026-10-15T14:00:00Z"),
        expected=1000.0,
        lower=900.0,
        upper=1100.0,
        actual=400.0,
        score=-8.5,
    )


def _dq_ok(s: Any, e: Any, seg: dict[str, str]) -> DQResult:
    del s, e, seg
    return DQResult(True, True, 1.0, [], pd.Timestamp("2026-10-15T13:00:00Z"), "ok")


def _dq_bad(s: Any, e: Any, seg: dict[str, str]) -> DQResult:
    del s, e, seg
    return DQResult(False, False, 0.5, [{"country": "DE"}], None, "missing rows")


def test_fallback_loop_confirms_payment_fault() -> None:
    finding = investigate(
        _make_anomaly(),
        _make_graph(confirmed=True),
        _FakeTools(counterfactual_passes=True),
        _make_calendar(with_release=True),
        _dq_ok,
        chooser=FallbackChooser(),
        limits=Limits(),
    )
    assert finding["stop_reason"] == "explained_and_confirmed"
    assert finding["explained_share"] >= 0.8
    assert finding["hypotheses"][0]["status"] == "confirmed"
    assert finding["business_impact"]["lost_orders"] == 600


def test_unconfirmed_mechanism_or_failed_counterfactual_stays_uncertain() -> None:
    # 1. Unconfirmed mechanism edge
    f1 = investigate(
        _make_anomaly(),
        _make_graph(confirmed=False),
        _FakeTools(counterfactual_passes=True),
        _make_calendar(with_release=True),
        _dq_ok,
        chooser=FallbackChooser(),
    )
    assert f1["hypotheses"][0]["status"] == "uncertain"
    assert "mechanism_confirmed" in f1["hypotheses"][0]["missing_conditions"]

    # 2. Confirmed mechanism edge, but counterfactual fails -> MUST stay uncertain
    f2 = investigate(
        _make_anomaly(),
        _make_graph(confirmed=True),
        _FakeTools(counterfactual_passes=False),
        _make_calendar(with_release=True),
        _dq_ok,
        chooser=FallbackChooser(),
    )
    assert f2["hypotheses"][0]["status"] == "uncertain"
    assert "counterfactual_passed" in f2["hypotheses"][0]["missing_conditions"]


def test_label_rule_requires_counterfactual() -> None:
    h = Hypothesis(
        cause="payment_success_rate drop",
        source="formula_component",
        node="payment_success_rate",
        localized=True,
        timing_aligned=True,
        mechanism_confirmed=True,
        counterfactual_passed=False,
    )
    labeled = label_hypothesis(h)
    assert labeled.status == "uncertain"
    assert "counterfactual_passed" in labeled.missing_conditions


def test_incomplete_data_stops_immediately_with_zero_tool_calls() -> None:
    tools = _FakeTools()
    finding = investigate(
        _make_anomaly(),
        _make_graph(),
        tools,
        _make_calendar(),
        _dq_bad,
        chooser=FallbackChooser(),
    )
    assert finding["stop_reason"] == "insufficient_evidence"
    assert tools.calls == []
    assert all(h["status"] != "confirmed" for h in finding["hypotheses"])


def test_planned_event_overlapping_is_noted_and_not_cause() -> None:
    finding = investigate(
        _make_anomaly(),
        _make_graph(confirmed=True),
        _FakeTools(),
        _make_calendar(with_release=True, with_promo=True),
        _dq_ok,
        chooser=FallbackChooser(),
    )
    assert any(e.get("type") == "planned_events" for e in finding["evidence_list"])
    assert finding["hypotheses"][0]["node"] != "evt_promo"


def test_symptom_vs_first_cause() -> None:
    mg = _make_graph(confirmed=True)
    # Upstream parent payment_success_rate is anomalous -> orders is a symptom
    assert classify_symptom_vs_cause("orders", mg, lambda m: m in {"orders", "payment_success_rate"}) == "symptom"
    # payment_success_rate has no anomalous parents -> first_cause
    assert classify_symptom_vs_cause("payment_success_rate", mg, lambda m: m in {"orders", "payment_success_rate"}) == "first_cause"


def test_menu_never_mixes_two_formulas() -> None:
    mg = _make_graph(confirmed=True)
    mg.add_edge(
        GraphEdge(
            "checkouts",
            "orders",
            "formula",
            formula="orders = checkouts * checkout_to_order_rate",
            status="confirmed",
        )
    )
    state = InvestigationState(
        anomaly={"metric": "orders", "segment": {}, "actual": 400.0, "expected": 1000.0},
        active_formula_by_metric={"orders": "orders = payment_attempts * payment_success_rate"},
    )
    menu = build_menu(state, mg)
    formula_items = [m for m in menu if m.kind == "decompose_formula"]
    assert len(formula_items) == 1
    assert formula_items[0].args["formula"] == "orders = payment_attempts * payment_success_rate"


def test_llm_chooser_mocked_and_cache_replay(tmp_path) -> None:
    # 1. Extract JSON with reasoning text around it
    parsed = extract_first_json_object(
        "Let me think...\n```json\n{\"menu_number\": 1, \"decision\": \"continue\", \"reason\": \"Check formula\"}\n```\nDone."
    )
    assert parsed["menu_number"] == 1

    # 2. Mocked ChatClient with off-menu number and TimeoutError -> falls back and records
    fb_client = _MockChatClient(
        [
            {"menu_number": 999, "decision": "continue", "reason": "bad number"},
            TimeoutError("request timed out"),
            {"menu_number": 1, "decision": "continue", "reason": "Proceed"},
            {"menu_number": 1, "decision": "continue", "reason": "Proceed"},
        ]
    )
    fb_chooser = LLMChooser(client=fb_client, mode="live")
    f_fb = investigate(
        _make_anomaly(), _make_graph(), _FakeTools(), _make_calendar(), _dq_ok, chooser=fb_chooser
    )
    assert any("fallback_used" in t["reason"] for t in f_fb["trail"])

    # 3. Record -> Replay gives identical trail
    rec_client = _MockChatClient(
        [
            "Thinking... {\"menu_number\": 1, \"decision\": \"continue\", \"reason\": \"Check events\"}",
            {"menu_number": 1, "decision": "continue", "reason": "Decompose orders"},
            {"menu_number": 1, "decision": "continue", "reason": "Drill down"},
            {"menu_number": 1, "decision": "continue", "reason": "Counterfactual"},
        ]
    )
    rec = LLMChooser(client=rec_client, cache_dir=tmp_path, mode="record")
    f_rec = investigate(
        _make_anomaly(), _make_graph(), _FakeTools(), _make_calendar(), _dq_ok, chooser=rec
    )
    rep = LLMChooser(client=None, cache_dir=tmp_path, mode="replay", model_name="mock-qwen")
    f_rep = investigate(
        _make_anomaly(), _make_graph(), _FakeTools(), _make_calendar(), _dq_ok, chooser=rep
    )
    assert f_rec["trail"] == f_rep["trail"]

    # Replay with missing entry raises clear error
    empty_rep = LLMChooser(client=None, cache_dir=tmp_path / "empty", mode="replay", model_name="mock-qwen")
    with pytest.raises(KeyError, match="Missing LLM cache entry"):
        empty_rep.choose(InvestigationState(anomaly=_make_anomaly().__dict__), build_menu(InvestigationState(anomaly={"metric": "orders", "segment": {}}), _make_graph()))

    # Missing env vars cause ChatClient to raise clear ValueError naming missing vars
    with pytest.MonkeyPatch.context() as mp:
        mp.delenv("LLM_BASE_URL", raising=False)
        mp.delenv("LLM_MODEL", raising=False)
        mp.delenv("LLM_API_KEY", raising=False)
        with pytest.raises(ValueError, match="LLM_BASE_URL"):
            ChatClient()


def test_limits_and_failing_tool_skipped() -> None:
    tools = _FakeTools(fail_first_call=True)
    finding = investigate(
        _make_anomaly(),
        _make_graph(),
        tools,
        _make_calendar(),
        _dq_ok,
        chooser=FallbackChooser(),
        limits=Limits(max_steps=2),
    )
    assert finding["stop_reason"] == "max_steps_reached"
    assert len(finding["trail"]) == 2
    assert any("tool_error" in t["observation"] for t in finding["trail"])

    # max_llm_calls stops LLMChooser
    mock_client = _MockChatClient(
        [{"menu_number": 1, "decision": "continue", "reason": "step"}] * 5
    )
    llm_chooser = LLMChooser(client=mock_client, mode="live")
    f_llm = investigate(
        _make_anomaly(),
        _make_graph(),
        _FakeTools(),
        _make_calendar(),
        _dq_ok,
        chooser=llm_chooser,
        limits=Limits(max_llm_calls=1),
    )
    assert f_llm["stop_reason"] == "max_llm_calls_reached"


def test_determinism_and_unknown_metric_drill_only() -> None:
    f1 = investigate(
        _make_anomaly(), _make_graph(), _FakeTools(), _make_calendar(), _dq_ok, chooser=FallbackChooser()
    )
    f2 = investigate(
        _make_anomaly(), _make_graph(), _FakeTools(), _make_calendar(), _dq_ok, chooser=FallbackChooser()
    )
    assert json.dumps(f1, sort_keys=True) == json.dumps(f2, sort_keys=True)

    # Anomaly whose metric is not in graph -> drill_dimension only
    f_unknown = investigate(
        _make_anomaly(metric="unregistered_metric"),
        _make_graph(),
        _FakeTools(),
        _make_calendar(),
        _dq_ok,
        chooser=FallbackChooser(),
    )
    actions = {t["action"] for t in f_unknown["trail"]}
    assert "decompose_formula" not in actions
    assert "drill_dimension" in actions
