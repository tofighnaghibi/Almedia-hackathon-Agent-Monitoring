"""The model at the decision points (mocked: no network in any test).

The model chooses the next menu step and writes report text. Code decides the
menu, every number, statuses, labels and when an investigation stops. Every
model failure falls back to the rule-based path, and replay reproduces a
recorded run exactly.
"""

from __future__ import annotations

import copy
import json
import logging
import re
from types import SimpleNamespace
from typing import Any

import httpx
import anthropic
import pandas as pd
import pytest

from agent import numbers
from agent.chooser import FallbackChooser, LLMChooser
from agent.llm_client import ClaudeClient, LLMCache, LLMUnavailable, extract_first_json_object
from agent.loop import investigate
from reporting.issues import IssueTracker, TrackedEpisode
from reporting.report import LLMNarrator, build_report
from runtime.runner import build_llm
from test_agent_l5 import _dq_ok, _FakeTools, _make_anomaly, _make_calendar, _make_graph

NOW = pd.Timestamp("2026-10-15T15:00:00Z")
EVIDENCE_REASON = "orders fell in country DE, so this step tests the top hypothesis."


class ScriptedClient:
    """A mocked model: ``reply(user_prompt, call_no)`` gives each answer (or raises it)."""

    model = "mock-claude"
    report_model = "mock-claude-report"

    def __init__(self, reply):
        self.reply = reply
        self.prompts: list[str] = []
        self.models: list[Any] = []

    def complete_json(self, system: str, user: str, model: str | None = None) -> dict[str, Any]:
        self.prompts.append(user)
        self.models.append(model)
        out = self.reply(user, len(self.prompts))
        if isinstance(out, Exception):
            raise out
        if isinstance(out, str):
            return extract_first_json_object(out)
        return out


def _menu_numbers(prompt: str) -> list[int]:
    return [int(n) for n in re.findall(r"^(\d+)\. \[", prompt, flags=re.M)]


def _last_item(prompt: str, _n: int) -> dict[str, Any]:
    return {"menu_number": _menu_numbers(prompt)[-1], "reason": EVIDENCE_REASON}


def _run(chooser) -> dict[str, Any]:
    return investigate(_make_anomaly(), _make_graph(), _FakeTools(), _make_calendar(), _dq_ok,
                       chooser=chooser, now=NOW)


# --- the chooser ---------------------------------------------------------------------------------

def test_model_choice_is_used_and_recorded():
    client = ScriptedClient(_last_item)
    finding = _run(LLMChooser(client=client, mode="live"))
    trail = finding["trail"]
    assert trail and all(t["chosen_by"] == "llm" for t in trail)
    assert all(t["reason"] == EVIDENCE_REASON and t["fallback_cause"] is None for t in trail)
    # the model's pick (the last item) was run, not the rule-based pick
    first_menu = _menu_numbers(client.prompts[0])
    assert trail[0]["menu_number"] == first_menu[-1]
    rules = _run(FallbackChooser())
    assert [t["action"] for t in trail] != [t["action"] for t in rules["trail"]]
    # the prompt carries anomaly, evidence, hypotheses and the menu with cost and gain
    p = client.prompts[1]
    for part in ("ANOMALY", "EVIDENCE SO FAR", "HYPOTHESES", "MENU", "cost", "expected gain", "actual 400"):
        assert part in p
    # code still decides the stop: the stop reason is one of the code rules
    assert finding["stop_reason"] in {"explained_and_confirmed", "max_steps_reached", "menu_empty",
                                      "min_gain_reached", "max_llm_calls_reached"}


@pytest.mark.parametrize("reply, cause", [
    ("not json at all", "LLM error: ValueError"),                                 # invalid JSON
    ({"menu_number": 999, "reason": EVIDENCE_REASON}, "off-menu number: 999"),    # off-menu number
    ({"menu_number": 1, "reason": "This is the best next step."}, "reason cites no evidence"),
    ({"menu_number": 1, "reason": "   "}, "empty or non-string reason"),
    ({"menu_number": "two", "reason": EVIDENCE_REASON}, "non-integer menu_number"),
    (RuntimeError("connection reset"), "LLM error: RuntimeError: connection reset"),  # exception
    (TimeoutError("timed out"), "LLM error: TimeoutError"),
])
def test_every_bad_reply_falls_back_to_the_rules(reply, cause):
    finding = _run(LLMChooser(client=ScriptedClient(lambda u, n: reply), mode="live"))
    rules = _run(FallbackChooser())
    assert all(t["chosen_by"] == "fallback" and cause in t["fallback_cause"] for t in finding["trail"])
    assert "fallback_used" in finding["trail"][0]["reason"]
    # the same steps as the rule-based run, and the same conclusion
    assert [t["query"] for t in finding["trail"]] == [t["query"] for t in rules["trail"]]
    assert finding["hypotheses"][0]["status"] == rules["hypotheses"][0]["status"]


def test_missing_key_falls_back_and_never_crashes(monkeypatch):
    with pytest.raises(LLMUnavailable, match="LLM_MODEL"):
        ClaudeClient()
    monkeypatch.setenv("LLM_MODEL", "some-model")
    with pytest.raises(LLMUnavailable, match="ANTHROPIC_API_KEY"):
        ClaudeClient()
    finding = _run(LLMChooser(mode="live"))  # no client, no key: every step by the rules
    assert finding["trail"] and all(t["chosen_by"] == "fallback" for t in finding["trail"])
    assert "LLM unavailable: ANTHROPIC_API_KEY is not set" in finding["trail"][0]["fallback_cause"]
    assert finding["stop_reason"] == "explained_and_confirmed"
    for use in (None, True):
        setup = build_llm(use)
        assert not setup.enabled and isinstance(setup.chooser, FallbackChooser) and setup.narrator is None
        assert "ANTHROPIC_API_KEY" in setup.note


@pytest.mark.parametrize("reply", [
    {"decision": "stop", "reason": "orders in DE are explained"},
    {"menu_number": 1, "stop": True, "reason": "orders in DE are explained"},
    {"menu_number": "stop", "reason": "orders in DE are explained"},
])
def test_a_stop_request_is_ignored_and_the_investigation_completes(reply):
    finding = _run(LLMChooser(client=ScriptedClient(lambda u, n: reply), mode="live"))
    first = finding["trail"][0]
    assert first["llm_stop_ignored"] is True and first["fallback_cause"] == "llm_stop_ignored"
    assert first["chosen_by"] == "fallback"
    # it ran to the code's own stop rule and reached the same conclusion as the rules
    assert finding["stop_reason"] == "explained_and_confirmed"
    assert finding["hypotheses"][0]["status"] == "confirmed"
    assert len(finding["trail"]) == len(_run(FallbackChooser())["trail"])


def test_localizing_first_does_not_cut_the_investigation_short():
    """A model may drill down before decomposing: the cause must still be found."""
    def drill_first(prompt, n):
        items = re.findall(r"^(\d+)\. \[(\w+)\] (.*)$", prompt, flags=re.M)
        pick = next((int(k) for k, kind, d in items if kind == "drill_dimension" and "Drill down orders" in d),
                    int(items[-1][0]))
        return {"menu_number": pick, "reason": EVIDENCE_REASON}

    # 97% localized: without the fix every gain fell under min_gain after this first step
    finding = investigate(_make_anomaly(), _make_graph(), _FakeTools(drill_share=0.97), _make_calendar(), _dq_ok,
                          chooser=LLMChooser(client=ScriptedClient(drill_first), mode="live"), now=NOW)
    assert finding["trail"][0]["query"].startswith("drill_down(orders")
    assert "decompose_formula" in [t["action"] for t in finding["trail"]]
    assert finding["hypotheses"][0]["node"] == "payment_success_rate"
    assert finding["hypotheses"][0]["status"] == "confirmed"


def test_checking_events_stays_on_the_menu_while_timing_is_unproven():
    """Localized (97% explained by the segment) is not timed: events must still be worth checking,
    even when a calendar-event hypothesis is already timing-aligned."""
    from agent.menu import build_menu
    from agent.state import InvestigationState

    seg = {"country": "DE"}
    state = InvestigationState(
        anomaly={"metric": "orders", "segment": seg, "actual": 400.0, "expected": 1000.0},
        explained_share=0.97, explained_parts={"__self__": 0.97},
        hypotheses=[
            {"node": "orders", "source": "segment", "segment": seg, "status": "uncertain", "localized": True,
             "explained_share": 0.97, "is_symptom": False, "missing_conditions": ["timing_aligned"]},
            {"node": "evt_rel", "source": "event", "segment": seg, "status": "uncertain", "localized": False,
             "timing_aligned": True, "explained_share": 0.0, "is_symptom": False,
             "missing_conditions": ["localized_to_slice"]},
        ])
    ev = [m for m in build_menu(state, _make_graph()) if m.kind == "check_events"]
    assert ev and ev[0].expected_gain >= 0.15
    state.hypotheses[0]["missing_conditions"] = []
    state.hypotheses[0]["timing_aligned"] = True
    ev = [m for m in build_menu(state, _make_graph()) if m.kind == "check_events"]
    assert ev[0].expected_gain < 0.05  # once timing is shown, it is no longer worth much


def test_a_mix_of_model_and_fallback_steps_is_recorded_per_step():
    replies = {1: {"menu_number": 1, "reason": EVIDENCE_REASON}, 2: ValueError("bad json")}
    finding = _run(LLMChooser(client=ScriptedClient(lambda u, n: replies.get(n, _last_item(u, n))),
                              mode="live"))
    by = [t["chosen_by"] for t in finding["trail"]]
    assert by[0] == "llm" and by[1] == "fallback" and "llm" in by[2:]


# --- record / replay -------------------------------------------------------------------------------

def test_replay_reproduces_the_recorded_investigation_without_a_client(tmp_path):
    replies = {2: RuntimeError("overloaded")}  # a recorded failure is replayed as the same fallback
    rec_client = ScriptedClient(lambda u, n: replies.get(n, _last_item(u, n)))
    rec = _run(LLMChooser(client=rec_client, cache_dir=tmp_path, mode="record"))
    assert list(tmp_path.glob("*.json"))
    rep = _run(LLMChooser(client=None, cache_dir=tmp_path, mode="replay", model_name="mock-claude"))
    assert json.dumps(rec, sort_keys=True) == json.dumps(rep, sort_keys=True)
    assert {t["chosen_by"] for t in rep["trail"]} == {"llm", "fallback"}
    # replay with a different model name has no entries: a clear error from the chooser,
    # and the loop falls back instead of crashing
    other = LLMChooser(client=None, cache_dir=tmp_path, mode="replay", model_name="another-model")
    f = _run(other)
    assert all(t["chosen_by"] == "fallback" and "Missing LLM cache entry" in t["fallback_cause"]
               for t in f["trail"])


def test_cache_mode_comes_from_the_environment(monkeypatch, tmp_path):
    monkeypatch.setenv("LLM_CACHE_MODE", "replay")
    monkeypatch.setenv("LLM_CACHE_DIR", str(tmp_path))
    c = LLMCache()
    assert c.mode == "replay" and c.dir == tmp_path
    with pytest.raises(ValueError, match="Invalid cache mode"):
        LLMCache(mode="sometimes")
    assert LLMCache.key("m", "s", "u") != LLMCache.key("m2", "s", "u")


# --- the report text -------------------------------------------------------------------------------

def _issue(status="confirmed"):
    tr, t0 = IssueTracker(), pd.Timestamp("2026-10-29T00:00Z")
    ep = TrackedEpisode(key="psr|DE|down|00", metric="payment_success_rate",
                        segment={"country": "DE", "platform": "android"}, direction="down", impact="bad",
                        first=t0, last=t0, peak_z=-9.0, actual=50.0, expected=100.0, lower=80.0, upper=120.0)
    iss, _ = tr.route(ep, t0)
    finding = {"hypotheses": [{"node": "payment_success_rate", "segment": {"country": "DE", "platform": "android"},
                               "status": status, "cause": "payment_success_rate change", "source": "formula_component",
                               "is_symptom": False, "missing_conditions": [] if status == "confirmed" else ["timing_aligned"],
                               "explained_share": 0.95, "evidence": []}],
               "evidence_list": [], "business_impact": {"lost_orders": 100, "lost_revenue_eur": 7998.7},
               "time_window": {"start": "2026-10-29T00:00:00Z", "end": "2026-10-29T02:00:00Z"},
               "change_size": {"absolute": -50.0, "relative_pct": -50.0, "score": -9.0}}
    tr.set_finding(iss, finding, t0)
    return iss, tr


def test_narrator_text_with_a_wrong_number_is_replaced_by_the_template():
    iss, tr = _issue()
    before = copy.deepcopy(iss.finding)
    template = build_report(iss, tr)
    bad = build_report(iss, tr, narrator=LLMNarrator(ScriptedClient(
        lambda u, n: {"narrative": "Payment success fell 50% in DE on android; about 4,321 orders were lost."})))
    assert bad["narrative"] is None and bad["text_by"] == "template"
    assert "4,321" in bad["text_fallback_reason"]
    assert bad["headline"] == template["headline"] and bad["what_happened"] == template["what_happened"]
    assert iss.finding == before  # the structured finding is never changed


def test_narrator_text_with_rounded_true_numbers_is_used():
    iss, tr = _issue()
    client = ScriptedClient(lambda u, n: {"narrative": "Payment success fell 50% in DE on android, which explains "
                                                       "95% of the change: 100 orders and about EUR 8,000 lost."})
    r = build_report(iss, tr, narrator=LLMNarrator(client))
    assert r["text_by"] == "llm" and r["narrative"].startswith("Payment success fell 50%")
    assert r["text_fallback_reason"] is None
    assert client.models == ["mock-claude-report"]  # the report model, not the step model
    assert json.loads(client.prompts[0])["label"] == "confirmed"


def test_narrator_may_not_change_the_label():
    iss, tr = _issue(status="uncertain")
    r = build_report(iss, tr, narrator=LLMNarrator(ScriptedClient(
        lambda u, n: {"narrative": "The payment failure in DE is confirmed as the cause."})))
    assert r["narrative"] is None and "unconfirmed cause confirmed" in r["text_fallback_reason"]
    ok = build_report(iss, tr, narrator=LLMNarrator(ScriptedClient(
        lambda u, n: {"narrative": "Payment success in DE is the most likely cause, not yet confirmed."})))
    assert ok["text_by"] == "llm"


@pytest.mark.parametrize("reply", [RuntimeError("down"), {"narrative": ""}, {"text": "wrong key"}])
def test_narrator_failures_keep_the_template(reply):
    iss, tr = _issue()
    r = build_report(iss, tr, narrator=LLMNarrator(ScriptedClient(lambda u, n: reply)))
    assert r["narrative"] is None and r["text_by"] == "template" and r["text_fallback_reason"]


def test_narrator_replay_reproduces_the_recorded_report(tmp_path):
    iss, tr = _issue()
    text = {"narrative": "Payment success fell 50% in DE on android; 100 orders were lost."}
    rec = build_report(iss, tr, narrator=LLMNarrator(ScriptedClient(lambda u, n: text),
                                                     cache=LLMCache("record", tmp_path)))
    rep = build_report(iss, tr, narrator=LLMNarrator(None, cache=LLMCache("replay", tmp_path),
                                                     model="mock-claude-report"))
    assert rec == rep and rep["text_by"] == "llm"


def test_number_check_allows_rounding_and_truncation_only():
    facts = {"lost_revenue_eur": 7998.7, "share": 0.94, "rel": -60.0, "orders": 100,
             "expected": 175188.65, "big": 2490015.31}
    assert numbers.unknown_numbers("EUR 8,000, 94%, 60.0%, 100 orders, 7,998.70", facts) == []
    # truncated (175,188 for 175,188.65) and scaled ("2.49 million", "2.5M", "175k") are fine
    assert numbers.unknown_numbers("175,188 expected; EUR 2.49 million (2.5M); about 175k", facts) == []
    assert numbers.unknown_numbers("EUR 9,000 and 4,321 orders", facts) == ["9,000", "4,321"]
    assert numbers.unknown_numbers("101 orders", facts) == ["101"]  # one above is not rounding
    assert numbers.unknown_numbers("EUR 2.6 million", facts) == ["2.6 million"]


def test_a_weaker_reinvestigation_does_not_flip_the_label_or_alert():
    iss, tr = _issue("confirmed")
    t1 = pd.Timestamp("2026-10-29T06:00Z")
    confirmed = copy.deepcopy(iss.finding)
    weaker = copy.deepcopy(confirmed)
    weaker["hypotheses"][0].update(status="uncertain", missing_conditions=["timing_aligned"])
    assert tr.set_finding(iss, weaker, t1) is None
    assert iss.root["status"] == "confirmed" and iss.finding == confirmed
    assert iss.history[-1]["event"] == "re-investigated"
    # a different cause is still news
    other = copy.deepcopy(weaker)
    other["hypotheses"][0].update(node="checkout_rate", cause="checkout_rate change")
    assert tr.set_finding(iss, other, t1) == "updated" and iss.root["node"] == "checkout_rate"


# --- .env loading ------------------------------------------------------------------------------------

def test_env_file_is_loaded_and_the_shell_wins(monkeypatch, tmp_path):
    from agent.env import load_env

    for n in ("ANTHROPIC_API_KEY", "LLM_MODEL", "LLM_REPORT_MODEL", "LLM_EFFORT", "LLM_CACHE_MODE"):
        monkeypatch.delenv(n, raising=False)  # restored to unset after the test
    monkeypatch.setenv("LLM_CACHE_MODE", "replay")
    f = tmp_path / ".env"
    f.write_text("# comment\nANTHROPIC_API_KEY=sk-fake-for-test\nexport LLM_MODEL=\"model-a\"\n"
                 "LLM_REPORT_MODEL=            # empty, with a comment\nLLM_EFFORT=low   # inline\n"
                 "LLM_CACHE_MODE=live\n", encoding="utf-8")
    assert sorted(load_env(f)) == ["ANTHROPIC_API_KEY", "LLM_EFFORT", "LLM_MODEL"]
    import os
    assert os.environ["LLM_MODEL"] == "model-a" and os.environ["LLM_EFFORT"] == "low"
    assert "LLM_REPORT_MODEL" not in os.environ
    assert os.environ["LLM_CACHE_MODE"] == "replay"  # already set in the shell: kept
    assert load_env(tmp_path / "missing.env") == []


# --- the Claude client (fake SDK object: no network) ------------------------------------------------

class _FakeMessages:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls: list[dict[str, Any]] = []

    def create(self, **kw):
        self.calls.append(kw)
        out = self.outcomes.pop(0)
        if isinstance(out, Exception):
            raise out
        return out


def _resp(text, stop="end_turn"):
    return SimpleNamespace(content=[SimpleNamespace(type="thinking", thinking=""), SimpleNamespace(type="text", text=text)],
                           usage=SimpleNamespace(input_tokens=120, output_tokens=30), stop_reason=stop)


def _bad_request(msg):
    req = httpx.Request("POST", "https://api.anthropic.com/v1/messages")
    return anthropic.BadRequestError(msg, response=httpx.Response(400, request=req), body=None)


def test_claude_client_parses_logs_usage_and_never_shows_the_key(monkeypatch, caplog):
    secret = "sk-ant-test-not-a-real-key-123"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    monkeypatch.setenv("LLM_MODEL", "model-from-env")
    msgs = _FakeMessages([_resp('Sure. {"menu_number": 2, "reason": "x"} done'), _resp('{"narrative": "n"}')])
    c = ClaudeClient(sdk_client=SimpleNamespace(messages=msgs))
    with caplog.at_level(logging.DEBUG, logger="agent.llm"):
        assert c.complete_json("sys", "user") == {"menu_number": 2, "reason": "x"}
        c.complete_json("sys", "user", model="report-model")
    assert [k["model"] for k in msgs.calls] == ["model-from-env", "report-model"]
    assert msgs.calls[0]["temperature"] == 0.0 and msgs.calls[0]["max_tokens"] == 1024
    assert c.usage.calls == 2 and c.usage.tokens_in == 240 and c.usage.tokens_out == 60
    assert "tokens_in=120" in caplog.text
    assert secret not in caplog.text and secret not in repr(c) and secret not in str(vars(c))


def test_claude_client_drops_a_rejected_parameter_once_and_fails_cleanly(monkeypatch):
    monkeypatch.setenv("LLM_MODEL", "m")
    msgs = _FakeMessages([_bad_request("temperature: not supported on this model"), _resp('{"ok": 1}'),
                          _resp('{"ok": 2}')])
    c = ClaudeClient(sdk_client=SimpleNamespace(messages=msgs))
    assert c.complete_json("s", "u") == {"ok": 1}
    assert "temperature" in msgs.calls[0] and "temperature" not in msgs.calls[1]
    c.complete_json("s", "u")
    assert "temperature" not in msgs.calls[2]  # stays off for the rest of the run
    for out, err in [(_resp("no json here"), ValueError), (_resp("", stop="refusal"), ValueError),
                     (_resp('{"a": ', stop="max_tokens"), ValueError), (_bad_request("model not found"),
                                                                       anthropic.BadRequestError)]:
        bad = ClaudeClient(sdk_client=SimpleNamespace(messages=_FakeMessages([out])))
        with pytest.raises(err):
            bad.complete_json("s", "u")
        assert bad.usage.failures == 1
