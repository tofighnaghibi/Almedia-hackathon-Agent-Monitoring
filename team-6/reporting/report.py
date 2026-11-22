"""Report writer: the story of an issue, with its evidence (L6, Person A).

    report = build_report(issue, tracker)          # dict: title, severity, sections, markdown
    report = build_report(issue, tracker, narrator=LLMNarrator())   # optional LLM narrative

Every number in a report comes from the investigation or the detector. The
optional LLM narrator only rewrites the summary in plain language: it gets the
facts as JSON, and its text is rejected (the template is kept) if it contains a
number that isn't among those facts.

Recommended actions are suggestions for a person. The system never acts on
the business itself (no rollbacks, no budget changes).
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

import pandas as pd

from .issues import Issue, IssueTracker, seg_key

SEVERITY_EUR = [(500_000, "critical"), (100_000, "high"), (20_000, "medium")]  # revenue lost per day

ACTIONS = {
    "payment_success_rate": "Check payment provider / gateway error rates for {seg}; if a release went out just before, roll it back.",
    "payment_attempt_rate": "Check the payment step of checkout for {seg} (errors, timeouts).",
    "checkout_rate": "Check the checkout flow for {seg} (errors, slow pages, a recent release).",
    "search_ctr": "Review the latest search ranking change for {seg}: compare result relevance before and after it.",
    "searches_per_session": "Check search entry points and traffic sources for {seg}.",
    "add_to_cart_rate": "Check product availability and product pages for {seg} (out-of-stock items, prices).",
    "out_of_stock_rate": "Check stock levels, supplier deliveries and open purchase orders for {seg}; restock the top sellers first.",
    "avg_active_listings": "Check stock levels and supplier deliveries for {seg}.",
    "ad_impressions": "Check campaign budget pacing and daily caps for {seg}: was the budget spent early in the day?",
    "ad_spend": "Check campaign budget pacing and bids for {seg}.",
    "ad_clicks": "Check campaign delivery and budget pacing for {seg}.",
    "cost_per_click": "Check bid changes for {seg}.",
    "recommendation_ctr": "Check the latest recommendation model change for {seg}.",
    "sessions": "Check traffic sources and marketing activity for {seg}.",
    "row_count": "Check the data pipeline for {seg}; numbers for this segment are incomplete until the backfill arrives.",
}


def severity(issue: Issue) -> str:
    """From the revenue being lost per day (projected from the investigated
    window), so a big failure is critical in its first hour, not only later."""
    if issue.kind == "data_quality":
        return "medium"
    f = issue.finding or {}
    impact = f.get("business_impact") or {}
    lost = max(float(impact.get("lost_revenue_eur") or 0.0), 0.0)
    tw = f.get("time_window") or {}
    try:
        hours = max(1.0, (pd.Timestamp(tw["end"]) - pd.Timestamp(tw["start"])) / pd.Timedelta(hours=1))
    except (KeyError, ValueError):
        hours = 24.0
    per_day = lost / hours * 24.0
    for limit, name in SEVERITY_EUR:
        if per_day >= limit:
            return name
    return "low"


def _seg(seg: dict) -> str:
    return ", ".join(f"{k}={v}" for k, v in seg.items()) or "all segments"


def _num(x: Any, digits: int = 0) -> str:
    try:
        return f"{float(x):,.{digits}f}"
    except (TypeError, ValueError):
        return "?"


def build_report(issue: Issue, tracker: IssueTracker, narrator: Optional["LLMNarrator"] = None) -> dict[str, Any]:
    f = issue.finding or {}
    root = issue.root or {}
    eps = [tracker.episodes[k] for k in issue.episodes if k in tracker.episodes]
    first = next((e for e in eps if e.metric == issue.metric), eps[0] if eps else None)
    sev = severity(issue)

    # --- what happened ------------------------------------------------------------------
    if issue.kind == "data_quality":
        dd = issue.data_detail
        title = f"Data incomplete for {_seg(issue.segment)}"
        headline = (f"{dd.get('missing_rows', 0)} rows for {_seg(issue.segment)} did not arrive on time "
                    f"({len(dd.get('pending_hours', [])) or 'all'} hour(s) still pending)."
                    if issue.open else
                    f"Rows for {_seg(issue.segment)} arrived late; they were backfilled at {dd.get('backfilled_at', '?')}.")
        what = [headline, "Metrics for this segment were held back as incomplete data, not reported as business changes."]
    else:
        rel = (first.actual / first.expected - 1) if first and first.expected else float("nan")
        what = [f"{issue.metric} {'fell' if first and first.direction == 'down' else 'rose'} "
                f"{abs(100 * rel):.1f}% in {_seg(issue.segment)}: {_num(first.actual, 2)} vs an expected "
                f"{_num(first.expected, 2)} (normal range {_num(first.lower, 2)}–{_num(first.upper, 2)}), "
                f"peak score z={first.peak_z:.1f}."] if first else []
        if root:
            share = root.get("explained_share", 0.0)
            confirmed = root.get("status") == "confirmed"
            rseg = root.get("segment") or {}
            word = "down" if first and first.direction == "down" else "up"
            pct = f" {abs(100 * rel):.0f}%" if rel == rel else ""
            same = root.get("node") == issue.metric
            title = f"{issue.metric} {word}{pct} in {_seg(issue.segment)}" + (
                "" if same and rseg == issue.segment else f", driven by {root.get('node')} in {_seg(rseg)}")
            src = root.get("source")
            if src == "segment":
                what_cause = f"the change in {root.get('node')} is confined to {_seg(rseg)}"
            elif src == "graph_cause_edge":
                what_cause = f"{root.get('cause')} in {_seg(rseg)}"
            else:
                what_cause = f"{root.get('node')} in {_seg(rseg)}"
            headline = (f"{'Confirmed' if confirmed else 'Most likely (not yet confirmed)'}: {what_cause}"
                        + (f" ({100 * share:.0f}% of the change)." if share else "."))
            trig = _trigger(f, rseg)
            if trig:
                headline += f" Likely trigger: {trig}."
                root = {**root, "event_id": root.get("event_id") or trig.split()[1]}
        else:
            title = f"{issue.metric} {'down' if first and first.direction == 'down' else 'up'} in {_seg(issue.segment)}"
            headline = "Under investigation."

    # --- evidence ---------------------------------------------------------------------------
    evidence = []
    for ev in f.get("evidence_list", []):
        t = ev.get("type")
        if t == "formula_decomposition":
            parts = ", ".join(f"{d['metric']} {100 * float(d['share']):.0f}%" for d in ev.get("drivers", []))
            if parts:
                evidence.append(f"Formula {ev.get('formula')}: contributions {parts}.")
        elif t == "dimension_drilldown":
            evidence.append(f"Localized: {ev.get('summary')}")
        elif t == "counterfactual":
            evidence.append(f"Counterfactual: {ev.get('detail')}")
        elif t == "event_match":
            evidence.append(f"Event {ev.get('event_id')} ({_seg(ev.get('scope') or {})}): {ev.get('description')}")
        elif t == "cause_edge_check":
            evidence.append(f"Upstream {ev.get('from_metric')} -> {ev.get('to_metric')}: "
                            f"{'anomalous' if ev.get('anomalous') else 'normal'} "
                            f"({_num(ev.get('baseline'), 3)} -> {_num(ev.get('actual'), 3)}).")
        elif t == "planned_events":
            evidence.append(f"Planned event(s) {', '.join(ev.get('events', []))} overlap; only the excess was investigated.")
    if issue.kind == "data_quality":
        dd = issue.data_detail
        if dd.get("events"):
            evidence.append(f"Matching data_delay event(s): {', '.join(dd['events'])}.")

    # --- hypotheses / uncertainty -------------------------------------------------------
    hyps = [h for h in f.get("hypotheses", []) if h.get("status") != "rejected"]
    others = [h for h in hyps if h.get("node") != root.get("node") and not h.get("is_symptom")][:3]
    rejected = [h for h in f.get("hypotheses", []) if h.get("status") == "rejected"][:3]
    uncertainty = [f.get("uncertainty")] if f.get("uncertainty") else []
    if root.get("missing"):
        uncertainty.append("Not yet shown: " + ", ".join(m.replace("_", " ") for m in root["missing"]) + ".")
    for h in others:
        uncertainty.append(f"Also possible: {h['cause']} in {_seg(h.get('segment') or {})} ({h['status']}).")
    ruled_out = [f"{h['cause']}" for h in rejected]

    # --- impact / actions -----------------------------------------------------------------
    imp = f.get("business_impact") or {}
    impact = {}
    if imp.get("lost_orders") is not None:
        impact["lost_orders"] = imp.get("lost_orders")
    if imp.get("lost_revenue_eur") is not None:
        impact["lost_revenue_eur"] = imp.get("lost_revenue_eur")
    actions = []
    if root.get("event_id"):
        actions.append(f"Look at event {root['event_id']} first: it happened in the affected segment right before the change.")
    node = root.get("node") or issue.metric
    if node in ACTIONS:
        actions.append(ACTIONS[node].format(seg=_seg(root.get("segment") or issue.segment)))
    elif node:
        actions.append(f"Look at recent changes affecting {node} in {_seg(root.get('segment') or issue.segment)}.")

    symptoms = [e.summary() for e in eps if e.metric != node or e.segment != root.get("segment")]
    report = {
        "issue_id": issue.id, "kind": issue.kind, "status": issue.status, "severity": sev,
        "title": title, "headline": headline, "what_happened": what,
        "root_cause": root, "evidence": evidence, "business_impact": impact,
        "uncertainty": uncertainty, "ruled_out": ruled_out, "recommended_actions": actions,
        "symptoms": symptoms[:12], "trail": f.get("trail", []),
        "opened_at": issue.to_dict()["opened_at"], "resolved_at": issue.to_dict()["resolved_at"],
        "narrative": None, "text_by": "template", "text_fallback_reason": None,
    }
    if narrator is not None and issue.kind == "incident" and root:
        if hasattr(narrator, "write"):
            text, why = narrator.write(report, f)
        else:  # any object with narrate(report) -> text or None
            text = narrator.narrate(report)
            why = None if text else "narrator returned no text"
        report["narrative"] = text
        report["text_by"] = "llm" if text else "template"
        report["text_fallback_reason"] = why
    report["markdown"] = to_markdown(report)
    return report


def _trigger(finding: dict, segment: dict) -> Optional[str]:
    """An unplanned event found by the investigation whose scope covers the segment."""
    for ev in finding.get("evidence_list", []):
        if ev.get("type") != "event_match":
            continue
        scope = ev.get("scope") or {}
        if all(segment.get(k) == v for k, v in scope.items()):
            return f"event {ev.get('event_id')} ({ev.get('description')})"
    return None


def to_markdown(r: dict[str, Any]) -> str:
    lines = [f"## [{r['severity'].upper()}] {r['issue_id']}: {r['title']}", "",
             f"Status: **{r['status']}**, opened {r['opened_at']}" + (f", resolved {r['resolved_at']}" if r["resolved_at"] else ""),
             "", f"**{r['headline']}**", ""]
    if r.get("narrative"):
        lines += [r["narrative"], ""]
    lines += ["### What happened"] + [f"- {x}" for x in r["what_happened"]]
    if r["business_impact"]:
        lines += ["", "### Business impact"] + [
            f"- {k.replace('_', ' ')}: {_num(v)}" for k, v in r["business_impact"].items()]
    if r["evidence"]:
        lines += ["", "### Evidence"] + [f"- {x}" for x in r["evidence"]]
    if r["uncertainty"]:
        lines += ["", "### What is still uncertain"] + [f"- {x}" for x in r["uncertainty"]]
    if r["ruled_out"]:
        lines += ["", "### Ruled out"] + [f"- {x}" for x in r["ruled_out"]]
    if r["recommended_actions"]:
        lines += ["", "### Recommended actions"] + [f"- {x}" for x in r["recommended_actions"]]
    if r["trail"]:
        chosen = [t for t in r["trail"] if t.get("chosen_by")]
        head = "### Investigation trail"
        if chosen:
            head += f" (model-chosen steps: {sum(t['chosen_by'] == 'llm' for t in chosen)} of {len(chosen)})"
        lines += ["", head] + [
            f"{t['step']}. **{t['action']}**" + (f" [{t['chosen_by']}]" if t.get("chosen_by") else "")
            + f" {t['query']} → {t['observation']}" for t in r["trail"]]
    if r["symptoms"]:
        lines += ["", "### Also affected (symptoms)"] + [
            f"- {s['metric']} {s['direction']} in {seg_key(s['segment'])} ({s['rel_change_pct']}%)" for s in r["symptoms"]]
    return "\n".join(lines)


class LLMNarrator:
    """The report's summary text, written by the report model from the structured finding.

    The model gets only the facts (the finding's numbers, the code-decided
    label, evidence, impact, uncertainty, actions) and writes the text. Then
    every number, percentage and euro amount in that text must be one of the
    facts (rounding allowed), and the text may not call an unconfirmed cause
    confirmed (or the reverse). If either check fails, or the call fails, the
    model text is discarded, the template text stays, and the reason is
    recorded. The structured finding is never changed.

    The model is LLM_REPORT_MODEL (default LLM_MODEL), through ClaudeClient
    unless a client is passed. Replies go through the same record/replay
    cache as the step chooser.
    """

    SYSTEM = ("You write the summary of an incident report for marketplace managers, from JSON facts produced "
              "by a monitoring system. Write 2-4 plain sentences: what changed, where, the business impact, the "
              "most likely cause, and what is still uncertain. Use only numbers that appear in the facts, as "
              "written or rounded. The field \"label\" says whether the cause is confirmed; never call an "
              "unconfirmed cause confirmed. Reply with exactly one JSON object: {\"narrative\": \"...\"}")

    def __init__(self, client: Any = None, cache: Any = None, model: Optional[str] = None):
        from agent.llm_client import LLMCache

        self.client = client
        self.cache = cache if cache is not None else LLMCache()
        self.model = model
        self.failures = 0
        self.used = 0
        self.last_reason: Optional[str] = None
        self.unavailable: Optional[str] = None
        self._memo: dict[tuple[str, str], tuple[Optional[str], Optional[str]]] = {}

    @staticmethod
    def facts(report: dict[str, Any], finding: Optional[dict[str, Any]] = None) -> dict[str, Any]:
        f = finding or {}
        root = report.get("root_cause") or {}
        out = {
            "title": report.get("title"),
            "label": "confirmed" if root.get("status") == "confirmed" else "not confirmed (most likely cause only)",
            "headline": report.get("headline"),
            "root_cause": {k: root.get(k) for k in ("node", "segment", "status", "explained_share", "missing",
                                                     "event_id") if root.get(k) is not None},
            "what_happened": report.get("what_happened"),
            "business_impact": report.get("business_impact"),
            "evidence": report.get("evidence"),
            "uncertainty": report.get("uncertainty"),
            "recommended_actions": report.get("recommended_actions"),
        }
        for k in ("metric", "segment", "time_window", "expected", "actual", "change_size", "explained_share"):
            if k in f:
                out[k] = f[k]
        return out

    def narrate(self, report: dict[str, Any], finding: Optional[dict[str, Any]] = None) -> Optional[str]:
        """The checked model text, or None (the template text is used; see ``last_reason``)."""
        text, why = self.write(report, finding)
        self.last_reason = why
        return text

    def write(self, report: dict[str, Any], finding: Optional[dict[str, Any]] = None) -> tuple[Optional[str], Optional[str]]:
        """(text, None) when the model text passed every check, else (None, why it was rejected)."""
        from agent.llm_client import ClaudeClient, client_model

        facts = self.facts(report, finding)
        user = json.dumps(facts, default=str, sort_keys=True)
        if self.cache.mode != "replay" and self.client is None:
            if self.unavailable is None:
                try:
                    self.client = ClaudeClient()
                except Exception as exc:  # noqa: BLE001 - no key / no model: template text
                    self.unavailable = str(exc)
            if self.client is None:
                return None, f"LLM unavailable: {self.unavailable}"
        model = self.model or client_model(self.client, report=True)
        if (model, user) in self._memo:  # the same facts again (a report rebuilt without news)
            return self._memo[(model, user)]
        out = self._write(model, user, facts)
        self._memo[(model, user)] = out
        return out

    def _write(self, model: str, user: str, facts: dict[str, Any]) -> tuple[Optional[str], Optional[str]]:
        from agent import numbers
        from agent.llm_client import describe_error

        try:
            reply = self.cache.call(self.client, model, self.SYSTEM, user, call_model=model)
            text = " ".join(str((reply or {}).get("narrative", "")).split())
        except Exception as exc:  # noqa: BLE001 - the model text is optional
            self.failures += 1
            return None, f"LLM error: {describe_error(exc)}"[:300]
        why = None
        if not text:
            why = "empty narrative"
        elif len(text) > 1200:
            why = "narrative too long"
        else:
            bad = numbers.unknown_numbers(text, facts)
            if bad:
                why = f"numbers not in the finding: {', '.join(bad[:5])}"
            else:
                why = _label_violation(text, facts["label"] == "confirmed")
        if why:
            self.failures += 1
            return None, why
        self.used += 1
        return text, None


def _label_violation(text: str, confirmed: bool) -> Optional[str]:
    """The confirmed/uncertain label is code's: the text must not contradict it."""
    low = text.lower()
    if confirmed:
        if re.search(r"\bnot (?:yet )?confirmed\b|\bunconfirmed\b", low):
            return "text calls a confirmed cause unconfirmed"
        return None
    for m in re.finditer(r"\bconfirm(?:ed|s)?\b", low):
        before = low[max(0, m.start() - 16): m.start()]
        if not re.search(r"\bnot\b|n't\b|\bno\b|\byet to\b|\bto be\b|\bbefore\b|\bwithout\b", before):
            return "text calls an unconfirmed cause confirmed"
    return None


def _numbers(text: str) -> set[str]:
    """Numbers in ``text`` as normalized strings ("100" stays "100", "8,000.50" -> "8000.5")."""
    out = set()
    for m in re.findall(r"\d[\d,]*(?:\.\d+)?", text):
        s = m.replace(",", "")
        if "." in s:
            s = s.rstrip("0").rstrip(".")
        out.add(s or "0")
    return out
