"""L5 choosers: the deterministic FallbackChooser and the model-backed LLMChooser.

Role split. The model chooses which menu step runs next and says why. Code
decides everything else: the menu, every number, hypothesis status and
confidence, the confirmed/uncertain label, and when the investigation
stops. The model's reply has no "stop" option. If it asks to stop anyway,
that request is ignored and recorded as ``llm_stop_ignored``, and the
step is chosen by the rule-based chooser.

A model reply is used only if it names a number on the menu and gives a
reason that cites evidence from the prompt: a metric, a segment value, an
event id, or a number from the tools. Anything else (invalid JSON, an
off-menu number, an empty or evidence-free reason, an exception, a missing
key) falls back to FallbackChooser for that step, and the cause is recorded.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from agent import numbers
from agent.llm_client import ClaudeClient, LLMCache, client_model, describe_error
from agent.menu import MenuItem
from agent.state import InvestigationState

CacheMode = Literal["replay", "record", "live"]

_SYSTEM_PROMPT = (
    "You choose the next step of an autonomous investigation into one anomaly in marketplace metrics.\n"
    "You get the anomaly, the evidence found so far (all numbers come from data tools), the current "
    "hypotheses with the status code gave them, and a numbered menu: the only steps that can run next, each "
    "with an estimated cost and an expected gain computed by code.\n"
    "Pick the one step that best tests or separates the open hypotheses. You cannot stop the investigation, "
    "change any number, or change any status: code decides those.\n"
    "Reply with exactly one JSON object and nothing else:\n"
    '{"menu_number": <a number from the menu>, "reason": "<at most two sentences>"}\n'
    "The reason must cite specific evidence from the prompt: a metric name, a segment value, an event id, or "
    "a number. Do not invent numbers."
)


@dataclass(frozen=True)
class Choice:
    """The selected next step (or a code-based stop decision)."""

    item_id: str | None
    reason: str
    decision: Literal["continue", "stop"]
    stop_reason: str = ""
    used_fallback: bool = False
    fallback_reason: str = ""
    chosen_by: Literal["llm", "fallback"] = "fallback"
    llm_called: bool = False  # the model (or its cache) was consulted for this step
    menu_number: int | None = None
    stop_ignored: bool = False


class Chooser(Protocol):
    """Protocol for L5 step selection."""

    def choose(self, state: InvestigationState, menu: list[MenuItem]) -> Choice: ...


class FallbackChooser:
    """Deterministic chooser picking max ``expected_gain / estimated_cost``."""

    def __init__(self, min_gain: float = 0.05) -> None:
        self.min_gain = float(min_gain)

    def choose(self, state: InvestigationState, menu: list[MenuItem]) -> Choice:
        del state
        if not menu:
            return Choice(None, "Menu is empty", "stop", stop_reason="menu_empty")

        viable = [m for m in menu if m.expected_gain >= self.min_gain]
        if not viable:
            return Choice(
                None,
                f"All remaining items have expected_gain < {self.min_gain}",
                "stop",
                stop_reason="min_gain_reached",
            )

        best = max(
            viable,
            key=lambda m: (m.expected_gain / max(m.estimated_cost, 1e-9), -m.number),
        )
        ratio = best.expected_gain / max(best.estimated_cost, 1e-9)
        return Choice(
            item_id=best.id,
            reason=f"highest expected gain per cost ({best.expected_gain:.3f} / {best.estimated_cost:.2f} = {ratio:.3f})",
            decision="continue",
            menu_number=best.number,
        )


# --- the prompt --------------------------------------------------------------------------------

def _fmt(x: Any) -> str:
    try:
        v = float(x)
    except (TypeError, ValueError):
        return str(x)
    return f"{v:.4g}" if abs(v) < 1e6 else f"{v:,.0f}"


def _seg(seg: dict | None) -> str:
    return ", ".join(f"{k}={v}" for k, v in sorted((seg or {}).items())) or "all"


def _evidence_sections(state: InvestigationState) -> list[str]:
    """Anomaly, evidence and hypotheses: the part of the prompt a reason may cite."""
    a = state.anomaly
    exp = a.get("expected")
    rel = ""
    try:
        if exp not in (None, 0):
            rel = f", change {100 * (float(a['actual']) / float(exp) - 1):+.1f}%"
    except (KeyError, TypeError, ValueError, ZeroDivisionError):
        rel = ""
    lines = [
        "ANOMALY",
        f"metric {a.get('metric')} in {_seg(a.get('segment'))}, window {a.get('start')} to {a.get('end')}: "
        f"actual {_fmt(a.get('actual'))}, expected {_fmt(exp)} (range {_fmt(a.get('lower'))} to "
        f"{_fmt(a.get('upper'))}){rel}, score z={_fmt(a.get('score'))}",
        "",
        "EVIDENCE SO FAR (from data tools)",
    ]
    ev = [f"- {t.get('action')}: {t.get('query')} -> {str(t.get('observation'))[:300]}" for t in state.trail[-8:]]
    for e in state.evidence:
        if e.get("type") == "planned_events":
            ev.append(f"- planned events overlap the window: {', '.join(map(str, e.get('events', [])))}")
    lines += ev or ["- none yet"]
    lines += ["", "HYPOTHESES (status set by code)"]
    hyps = [h for h in state.hypotheses][:6]
    for h in hyps:
        missing = ", ".join(h.get("missing_conditions") or []) or "none"
        lines.append(f"- {h.get('cause')} [node {h.get('node')}, segment {_seg(h.get('segment'))}, "
                     f"status {h.get('status')}, confidence {_fmt(h.get('confidence', 0.0))}, "
                     f"explains {_fmt(h.get('explained_share', 0.0))}, missing: {missing}]")
    if not hyps:
        lines.append("- none yet")
    lines.append(f"explained share so far {_fmt(state.explained_share)}; steps used {state.steps_used}")
    return lines


def _format_menu_prompt(state: InvestigationState, menu: list[MenuItem]) -> str:
    lines = _evidence_sections(state) + ["", "MENU (choose one number)"]
    for item in menu:
        lines.append(f"{item.number}. [{item.kind}] {item.description} | cost {item.estimated_cost:.2f} | "
                     f"expected gain {item.expected_gain:.3f}")
    return "\n".join(lines)


def _evidence_terms(state: InvestigationState) -> tuple[set[str], set[float]]:
    """Names and numbers a reason may cite to count as citing evidence."""
    a = state.anomaly
    names = {str(a.get("metric"))}
    names |= {str(v) for v in (a.get("segment") or {}).values()}
    for h in state.hypotheses:
        names.add(str(h.get("node")))
        names |= {str(v) for v in (h.get("segment") or {}).values()}
    names |= set(state.tree)
    names |= {str(e.get("event_id")) for e in state.events_found if e.get("event_id")}
    names |= {str(x) for e in state.evidence if e.get("type") == "planned_events" for x in e.get("events", [])}
    names.discard("None")
    nums = numbers.values_in("\n".join(_evidence_sections(state)))
    return {n for n in names if n}, nums


def _cites_evidence(reason: str, state: InvestigationState) -> bool:
    names, nums = _evidence_terms(state)
    low = reason.lower()
    for n in names:
        for form in {n.lower(), n.lower().replace("_", " ")}:
            if re.search(rf"(?<![a-z0-9_]){re.escape(form)}(?![a-z0-9_])", low):
                return True
    return any(x.significant and numbers.matches(x, nums) for x in numbers.extract(reason))


def _two_sentences(text: str) -> str:
    parts = re.split(r"(?<=[.!?])\s+", " ".join(text.split()))
    return " ".join(parts[:2])[:400]


def _asks_to_stop(reply: dict[str, Any]) -> bool:
    if str(reply.get("decision", "")).strip().lower() == "stop" or reply.get("stop") is True:
        return True
    return any(str(reply.get(k, "")).strip().lower() == "stop" for k in ("menu_number", "action", "next"))


def _cache_key(model: str, system: str, user: str) -> str:
    return LLMCache.key(model, system, user)


# --- the model-backed chooser ---------------------------------------------------------------------

class LLMChooser:
    """Model chooses the step (validated); FallbackChooser takes over on any failure.

    ``mode`` (live / record / replay) and ``cache_dir`` set up the reply cache;
    left out, they come from LLM_CACHE_MODE / LLM_CACHE_DIR. Without a client,
    a ClaudeClient is created on first use (needs ANTHROPIC_API_KEY and
    LLM_MODEL); if that fails every step falls back, with the cause recorded.
    Replay needs no client and makes no network call.
    """

    def __init__(
        self,
        client: Any = None,
        fallback: FallbackChooser | None = None,
        cache_dir: str | Path | None = None,
        mode: CacheMode | None = None,
        model_name: str | None = None,
        cache: LLMCache | None = None,
    ) -> None:
        self.client = client
        self.fallback = fallback or FallbackChooser()
        self.cache = cache if cache is not None else LLMCache(mode=mode, cache_dir=cache_dir)
        self.mode: CacheMode = self.cache.mode
        self.cache_dir = self.cache.dir
        self.model_name = model_name or client_model(client)
        self.unavailable: str | None = None
        self.counts = {"llm": 0, "fallback": 0, "stop_ignored": 0}

    def _fallback_choice(self, state: InvestigationState, menu: list[MenuItem], why: str,
                         called: bool = True, stop_ignored: bool = False) -> Choice:
        fb = self.fallback.choose(state, menu)
        self.counts["fallback"] += 1
        self.counts["stop_ignored"] += int(stop_ignored)
        return Choice(
            item_id=fb.item_id,
            reason=fb.reason,
            decision=fb.decision,
            stop_reason=fb.stop_reason,
            used_fallback=True,
            fallback_reason=f"fallback_used: {why}"[:300],
            chosen_by="fallback",
            llm_called=called,
            menu_number=fb.menu_number,
            stop_ignored=stop_ignored,
        )

    def choose(self, state: InvestigationState, menu: list[MenuItem]) -> Choice:
        if not menu:
            return Choice(None, "Menu is empty", "stop", stop_reason="menu_empty")

        user_prompt = _format_menu_prompt(state, menu)
        if self.mode != "replay" and self.client is None:
            if self.unavailable is None:
                try:
                    self.client = ClaudeClient()
                    self.model_name = self.client.model
                except Exception as exc:  # noqa: BLE001 - no key / no model: rule-based steps
                    self.unavailable = str(exc)
            if self.client is None:
                return self._fallback_choice(state, menu, f"LLM unavailable: {self.unavailable}", called=False)

        try:
            reply = self.cache.call(self.client, self.model_name, _SYSTEM_PROMPT, user_prompt)
        except KeyError:
            raise  # replay without a recorded entry: a clear error (the loop falls back)
        except Exception as exc:  # noqa: BLE001 - any model failure falls back for this step
            return self._fallback_choice(state, menu, f"LLM error: {describe_error(exc)}")
        return self._validate_reply(reply, state, menu)

    def _validate_reply(self, reply: Any, state: InvestigationState, menu: list[MenuItem]) -> Choice:
        if not isinstance(reply, dict):
            return self._fallback_choice(state, menu, "LLM reply is not a JSON object")
        if _asks_to_stop(reply):
            return self._fallback_choice(state, menu, "llm_stop_ignored", stop_ignored=True)

        menu_num = reply.get("menu_number")
        if isinstance(menu_num, str) and menu_num.strip().isdigit():
            menu_num = int(menu_num.strip())
        if isinstance(menu_num, bool) or not isinstance(menu_num, int):
            return self._fallback_choice(state, menu, f"non-integer menu_number: {menu_num!r}")
        by_num = {item.number: item for item in menu}
        if menu_num not in by_num:
            return self._fallback_choice(state, menu, f"off-menu number: {menu_num}")

        reason = reply.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            return self._fallback_choice(state, menu, "empty or non-string reason")
        clean = _two_sentences(reason)
        if not _cites_evidence(clean, state):
            return self._fallback_choice(state, menu, f"reason cites no evidence: {clean[:120]!r}")

        self.counts["llm"] += 1
        return Choice(item_id=by_num[menu_num].id, reason=clean, decision="continue", chosen_by="llm",
                      llm_called=True, menu_number=menu_num)
