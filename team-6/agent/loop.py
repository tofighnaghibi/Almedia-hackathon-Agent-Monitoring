"""L5 Agent Investigation Loop: orchestrates DQ gate, hypotheses, menu, chooser, and Finding."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol

import numpy as np
import pandas as pd

from graph.edges import GraphEdge
from graph.engine import MetricEngine
from graph.metric_graph import MetricGraph
from investigation.compare import Window, compare, compare_total
from investigation.decompose import decompose
from investigation.drilldown import drill_down
from monitoring.data_quality import DQResult
from monitoring.event_calendar import EventCalendar, ExplanationResult
from monitoring.triage import adapt_anomaly

from agent.chooser import Choice, Chooser, FallbackChooser
from agent.hypotheses import (
    Hypothesis,
    build_initial_hypotheses,
    classify_symptom_vs_cause,
    event_scope,
    label_hypothesis,
    rank_hypotheses,
    scope_covers,
)
from agent.menu import MenuItem, action_signature, build_menu
from agent.state import InvestigationState

TIME_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
DQCheckerFn = Callable[[pd.Timestamp, pd.Timestamp, dict[str, str]], DQResult]


@dataclass(frozen=True)
class Limits:
    """Budget and stopping limits for an investigation."""

    max_steps: int = 12
    max_llm_calls: int = 12  # one per step at most: the step budget, not the model budget, binds
    explained_threshold: float = 0.8
    min_gain: float = 0.05


class InvestigationTools(Protocol):
    """Tool interface used by the L5 loop (backed by Anila's investigation/ tools)."""

    def decompose_formula(
        self, metric: str, start: pd.Timestamp, end: pd.Timestamp, segment: dict[str, str]
    ) -> dict[str, Any]: ...

    def drill_dimension(
        self, metric: str, start: pd.Timestamp, end: pd.Timestamp, segment: dict[str, str]
    ) -> dict[str, Any]: ...

    def check_cause_edge(
        self,
        from_metric: str,
        to_metric: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
        segment: dict[str, str],
        lag_hours: int = 0,
    ) -> dict[str, Any]: ...

    def counterfactual(
        self,
        metric: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
        suspect_segment: dict[str, str],
        component: str | None = None,
    ) -> dict[str, Any]: ...

    def business_impact(
        self, metric: str, start: pd.Timestamp, end: pd.Timestamp, segment: dict[str, str]
    ) -> dict[str, Any]: ...


class RealInvestigationTools:
    """Adapter wrapping Anila's ``investigation/`` functions and ``MetricEngine``."""

    def __init__(
        self,
        engine: MetricEngine,
        graph: MetricGraph,
        detector: Any = None,
        weeks: int = 4,
    ) -> None:
        self.engine = engine
        self.graph = graph
        self.detector = detector
        self.weeks = int(weeks)

    def decompose_formula(
        self, metric: str, start: pd.Timestamp, end: pd.Timestamp, segment: dict[str, str]
    ) -> dict[str, Any]:
        win = Window.of(start, end)
        tree = decompose(self.engine, self.graph, metric, win, segment, weeks=self.weeks)
        drivers = tree.drivers(min_share=0.2)

        nodes: list[dict[str, Any]] = []

        def walk(n: Any, parent: str | None) -> None:
            if parent is not None:
                nodes.append({
                    "metric": n.metric,
                    "parent": parent,
                    "share": float(n.share) if np.isfinite(n.share) else 0.0,
                    "rel_change": n.rel_change,
                    "decomposed": bool(n.children),  # explained by deeper components
                })
            for c in n.children:
                walk(c, n.metric)

        walk(tree, None)
        return {
            "formula": tree.formula,
            "actual": tree.actual,
            "baseline": tree.baseline,
            "nodes": nodes,
            "drivers": [
                {
                    "metric": d.metric,
                    "share": float(d.share) if np.isfinite(d.share) else 0.0,
                    "actual": d.actual,
                    "baseline": d.baseline,
                    "rel_change": d.rel_change,
                }
                for d in drivers
            ],
            "note": tree.note,
        }

    def drill_dimension(
        self, metric: str, start: pd.Timestamp, end: pd.Timestamp, segment: dict[str, str]
    ) -> dict[str, Any]:
        win = Window.of(start, end)
        res = drill_down(self.engine, metric, win, segment, weeks=self.weeks)
        return {
            "metric": metric,
            "localized_segment": res.segment,
            "share": float(res.share_of_root) if np.isfinite(res.share_of_root) else 0.0,
            "summary": "; ".join(s.summary() for s in res.steps),
        }

    def check_cause_edge(
        self,
        from_metric: str,
        to_metric: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
        segment: dict[str, str],
        lag_hours: int = 0,
    ) -> dict[str, Any]:
        del to_metric
        shift = pd.Timedelta(hours=int(lag_hours))
        win = Window.of(start - shift, end - shift)
        actual, baseline = compare_total(self.engine, from_metric, win, segment, weeks=self.weeks)
        anomalous = False
        if self.detector is not None and from_metric in self.engine.registry:
            eps = self.detector.detect([from_metric], by=list(segment.keys()), segment=segment, start=win.start, end=win.end)
            anomalous = len(eps) > 0
        elif np.isfinite(actual) and np.isfinite(baseline) and abs(baseline) > 1e-12:
            anomalous = abs(actual - baseline) / abs(baseline) >= 0.05
        return {
            "from_metric": from_metric,
            "actual": actual,
            "baseline": baseline,
            "anomalous": bool(anomalous),
        }

    def counterfactual(
        self,
        metric: str,
        start: pd.Timestamp,
        end: pd.Timestamp,
        suspect_segment: dict[str, str],
        component: str | None = None,
        parent_segment: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """Remove the suspect segment: does what is left look normal?

        ``target`` is the anomaly segment, or the suspect itself when it *is*
        the anomaly segment. ``rest`` is the target without the suspect (or,
        in the second case, the market outside it). recovery =
        1 - |relative change of rest| / |relative change of target|: close to 1
        when the change is confined to the suspect, for counts and ratios alike.
        """
        del component
        if not suspect_segment:
            return {"passed": False, "recovery_share": 0.0, "detail": "empty suspect segment"}

        win = Window.of(start, end)
        parent_seg = dict(parent_segment or {})
        narrower = suspect_segment != parent_seg and all(suspect_segment.get(k) == v for k, v in parent_seg.items())
        if narrower:  # e.g. DE/android inside DE: compare with the rest of DE
            base_seg, target_seg = parent_seg, parent_seg
        else:  # the suspect is the anomaly segment: compare with the market outside it
            base_seg, target_seg = {}, dict(suspect_segment)
        extra = [d for d in suspect_segment if d not in base_seg]
        if not extra:
            return {"passed": False, "recovery_share": 0.0, "detail": "suspect segment equals the whole market"}

        rel_target = _rel(*compare_total(self.engine, metric, win, target_seg, weeks=self.weeks))
        groups = compare(self.engine, metric, win, base_seg, by=extra, weeks=self.weeks)
        is_suspect = np.ones(len(groups), dtype=bool)
        for d in extra:
            is_suspect &= groups[d].astype(str).to_numpy() == str(suspect_segment[d])
        rest = groups[~is_suspect]
        if rest.empty or not np.isfinite(rel_target) or abs(rel_target) < 1e-12:
            return {"passed": False, "recovery_share": 0.0, "detail": "nothing to compare with"}
        if self.engine.registry.get(metric).is_additive:
            ra, rb = rest["actual"].sum(), rest["baseline"].sum()
        else:
            ra = float((rest["actual"] * rest["w_actual"]).sum() / rest["w_actual"].sum())
            rb = float((rest["baseline"] * rest["w_baseline"]).sum() / rest["w_baseline"].sum())
        rel_rest = _rel(ra, rb)
        recovery = float(np.clip(1.0 - abs(rel_rest) / abs(rel_target), 0.0, 1.0)) if np.isfinite(rel_rest) else 0.0
        where = "the rest of the market" if target_seg == suspect_segment else f"the rest of {target_seg or 'the market'}"
        return {
            "passed": recovery >= 0.7,
            "recovery_share": recovery,
            "detail": (f"{metric} {100 * rel_target:+.1f}% in {target_seg}, {100 * rel_rest:+.1f}% in {where} "
                       f"without {suspect_segment}: the suspect explains {100 * recovery:.0f}%"),
        }

    def business_impact(
        self, metric: str, start: pd.Timestamp, end: pd.Timestamp, segment: dict[str, str]
    ) -> dict[str, Any]:
        del metric
        win = Window.of(start, end)
        impact: dict[str, Any] = {}
        for m, key in (("orders", "lost_orders"), ("revenue", "lost_revenue_eur")):
            if m in self.engine.registry:
                a, b = compare_total(self.engine, m, win, segment, weeks=self.weeks)
                if np.isfinite(a) and np.isfinite(b):
                    diff = b - a
                    impact[key] = int(round(diff)) if m == "orders" else round(float(diff), 2)
        return impact


def _rel(actual: float, baseline: float) -> float:
    return actual / baseline - 1 if np.isfinite(actual) and np.isfinite(baseline) and abs(baseline) > 1e-12 else float("nan")


def _mechanism_confirmed(graph: MetricGraph, metric: str, node: str) -> bool:
    """True when ``node`` reaches ``metric`` through confirmed formula edges.

    A driver several steps down (search_ctr -> search_clicks -> ... -> orders)
    has a confirmed mechanism when every formula on the way was confirmed on
    the data, not only when it is a direct input.
    """
    if metric not in graph.graph or node not in graph.graph:
        return False
    import networkx as nx

    confirmed = nx.DiGraph()
    for u, v, data in graph.graph.edges(data=True):
        edge: GraphEdge = data["edge"]
        if edge.type == "formula" and edge.status == "confirmed" and u != v:
            confirmed.add_edge(u, v)
    return node in confirmed and metric in confirmed and nx.has_path(confirmed, node, metric)


def _descendants(tree: dict[str, dict[str, Any]], metric: str) -> set[str]:
    """Metrics below ``metric`` in the contribution tree."""
    out, frontier = set(), {metric}
    while frontier:
        kids = {m for m, n in tree.items() if n.get("parent") in frontier} - out
        out |= kids
        frontier = kids
    return out


def _ancestors(tree: dict[str, dict[str, Any]], metric: str) -> set[str]:
    out, cur = set(), tree.get(metric, {}).get("parent")
    while cur is not None and cur not in out:
        out.add(cur)
        cur = tree.get(cur, {}).get("parent")
    return out


def _apply_counterfactuals(state: InvestigationState, hyps: list[Hypothesis], anomaly_metric: str) -> None:
    """A counterfactual on metric M for segment S counts for localized hypotheses
    about S whose node is M, or for any of them when M is the anomaly metric."""
    for e in state.evidence:
        if e.get("type") != "counterfactual" or "suspect_segment" not in e:
            continue
        for h in hyps:
            if not h.localized or h.segment != e["suspect_segment"]:
                continue
            if e.get("metric") in (h.node, anomaly_metric):
                if e.get("metric") == anomaly_metric and h.counterfactual_passed and not e.get("passed"):
                    continue  # a pass on the hypothesis's own metric is the stronger test
                h.counterfactual_passed = bool(e.get("passed"))


def _refresh(state: InvestigationState, hyps: list[Hypothesis]) -> None:
    """Re-derive evidence-based conditions from what the investigation has found.

    - explained share: sum over localized drivers of (driver share x localized
      share); the anomaly metric's own localization counts if nothing else does
    - timing: a formula/segment hypothesis is timing-aligned only when an
      unplanned event found by check_events covers its (localized) segment,
      or an anomalous upstream cause points at it
    """
    _apply_counterfactuals(state, hyps, str(state.anomaly.get("metric")))
    parts = dict(state.explained_parts)
    own = parts.pop("__self__", 0.0)
    state.explained_share = float(min(1.0, max(own, sum(parts.values()))))
    for h in hyps:
        if h.source in {"formula_component", "segment"} and h.localized:
            covered = any(scope_covers(e.get("scope") or {}, h.segment) for e in state.events_found)
            if covered:
                h.timing_aligned = True


def _hyp_for(hyps: list[Hypothesis], node: str, source: str) -> Hypothesis | None:
    return next((h for h in hyps if h.node == node and h.source == source), None)


def _execute_item(
    item: MenuItem,
    state: InvestigationState,
    hyps: list[Hypothesis],
    graph: MetricGraph,
    tools: InvestigationTools,
    calendar: EventCalendar,
    start: pd.Timestamp,
    end: pd.Timestamp,
    anomaly_metric: str,
    anomaly_segment: dict[str, str],
) -> tuple[str, str]:
    """Execute one menu item, updating ``state`` and ``hyps``; returns ``(query, observation)``.

    Evidence only updates the hypotheses it is about: a drill-down of metric X
    localizes hypotheses about X (and releases whose scope covers the result),
    nothing else.
    """
    kind = item.kind
    args = item.args

    if kind == "decompose_formula":
        formula = str(args.get("formula", ""))
        if formula:
            state.active_formula_by_metric[args["metric"]] = formula
        res = tools.decompose_formula(args["metric"], start, end, args["segment"])
        drivers = res.get("drivers") or []
        # the whole tree if the tools give it; otherwise the drivers as leaves of the anomaly
        nodes = res.get("nodes") or [
            {"metric": d["metric"], "parent": args["metric"], "share": d.get("share", 0.0),
             "rel_change": d.get("rel_change"), "decomposed": False} for d in drivers
        ]
        if not nodes:
            return f"decompose({args['metric']})", f"no drivers found ({res.get('note', '')})"

        for n in nodes:
            m = str(n["metric"])
            share = max(0.0, min(1.0, abs(float(n.get("share", 0.0)))))
            state.tree[m] = {"parent": n.get("parent"), "share": share, "decomposed": bool(n.get("decomposed"))}
            h = _hyp_for(hyps, m, "formula_component")
            if h is None:
                h = Hypothesis(
                    cause=f"Formula component {m} of {args['metric']}",
                    source="formula_component",
                    node=m,
                    segment=dict(args["segment"]),
                )
                hyps.append(h)
            h.mechanism_confirmed = _mechanism_confirmed(graph, anomaly_metric, m)
            h.explained_share = share  # the decomposition is authoritative
            h.confidence = min(0.95, 0.4 + 0.5 * share)
            # a node explained by deeper components is a symptom of them
            h.is_symptom = bool(n.get("decomposed"))
            h.evidence.append({"type": "formula_contribution", **n})

        shown = list(drivers) or [n for n in nodes if not n.get("decomposed")][:3]
        obs = ", ".join(f"{d['metric']} ({100 * abs(float(d['share'])):.0f}%)" for d in shown)
        state.evidence.append({"type": "formula_decomposition", "formula": res.get("formula"), "drivers": drivers,
                               "nodes": nodes})
        return f"decompose({args['metric']}) via {res.get('formula')}", obs

    if kind == "drill_dimension":
        drilled = args["metric"]
        res = tools.drill_dimension(drilled, start, end, args["segment"])
        loc_seg = dict(res.get("localized_segment") or {})
        share = max(0.0, min(1.0, abs(float(res.get("share", 0.0)))))
        if share > 0 and loc_seg:
            if drilled == anomaly_metric:
                state.explained_parts["__self__"] = share
                h = next((x for x in hyps if x.source == "segment" and x.node == drilled), None)
                if h is None:
                    h = Hypothesis(cause=f"Localized change in {drilled} at {loc_seg}", source="segment",
                                   node=drilled, segment=loc_seg)
                    hyps.append(h)
                h.cause = f"Localized change in {drilled} at {loc_seg}"
                h.segment, h.localized = loc_seg, share >= 0.5
                h.explained_share = share
                h.confidence = min(0.9, 0.4 + 0.5 * share)
                # where the change is, not why: a mechanism only when nothing can be decomposed
                h.mechanism_confirmed = drilled not in graph.graph or not formulas_exist(graph, drilled)
                h.is_symptom = bool(state.tree)
            else:
                driver_share = state.tree.get(drilled, {}).get("share", 0.0)
                state.explained_parts[drilled] = driver_share * share
                h = _hyp_for(hyps, drilled, "formula_component")
                if h is not None:
                    h.segment, h.localized = loc_seg, share >= 0.5
            # a release whose scope covers this segment is now localized too
            for h in hyps:
                if h.source == "event":
                    scope = next((e.get("scope") for e in h.evidence if e.get("type") == "event"), None) or {}
                    if scope and scope_covers(scope, loc_seg):
                        h.segment, h.localized = loc_seg, share >= 0.5
                        part = state.tree.get(drilled, {}).get("share", 1.0) * share
                        h.explained_share = max(h.explained_share, part)
        state.evidence.append({"type": "dimension_drilldown", **res})
        return (f"drill_down({drilled}, {args['segment']})",
                str(res.get("summary") or f"localized={loc_seg} ({100 * share:.0f}%)"))

    if kind == "check_cause_edge":
        cause, target = args["from_metric"], args["to_metric"]
        res = tools.check_cause_edge(cause, target, start, end, args["segment"], args.get("lag_hours", 0))
        anomalous = bool(res.get("anomalous"))
        anomalous_nodes = {target} | ({cause} if anomalous else set())
        role = classify_symptom_vs_cause(target, graph, lambda n: n in anomalous_nodes)
        h = _hyp_for(hyps, cause, "graph_cause_edge")
        if h is None:
            edge = next((d["edge"] for u, _, d in graph.graph.in_edges(target, data=True)
                         if u == cause and d["edge"].type == "possible_cause"), None)
            h = Hypothesis(cause=f"Upstream cause {cause} -> {target}", source="graph_cause_edge", node=cause,
                           segment=dict(args["segment"]),
                           confidence=float(edge.confidence) if edge is not None else 0.5,
                           mechanism_confirmed=bool(edge is not None and edge.status == "confirmed"))
            hyps.append(h)
        if not anomalous:
            h.contradicted = True
        else:
            h.timing_aligned = True
            h.segment = dict(args["segment"])
            feeds = any(e.from_metric == anomaly_metric and e.to_metric == target for e in graph.edges("formula"))
            target_share = state.tree.get(target, {}).get("share", 1.0 if target == anomaly_metric or feeds else 0.0)
            if feeds:  # the anomaly metric is then a symptom of this upstream cause
                for x in hyps:
                    if x.node == anomaly_metric and x.source in ("segment", "formula_component"):
                        x.is_symptom = True
            h.explained_share = max(h.explained_share, target_share)
            h.localized = h.localized or any(x.localized and x.segment == h.segment for x in hyps if x is not h)
            h.evidence.append({"type": "upstream_check", **res})
            # what sits below the target in the tree is now explained by the cause
            below = _descendants(state.tree, target) - {cause} - _ancestors(state.tree, cause)
            for x in hyps:
                if x.source == "formula_component" and (x.node in below or x.node == target):
                    x.is_symptom = True
        state.evidence.append({"type": "cause_edge_check", "to_metric": target, **res})
        return (f"check_cause({cause} -> {target}, {args['segment']})",
                f"anomalous={anomalous}, downstream_role={role}")

    if kind == "check_events":
        lookback = pd.Timedelta(hours=int(args.get("lookback_hours", 1)))
        exp = calendar.explains(start - lookback, end, anomaly_segment, metric=anomaly_metric)
        for ev in exp.unplanned_events:
            if not any(e.get("event_id") == ev.event_id for e in state.events_found):
                state.events_found.append({"event_id": ev.event_id, "scope": event_scope(ev),
                                           "description": ev.description})
            if not any(e.get("event_id") == ev.event_id for e in state.evidence):
                state.evidence.append({"type": "event_match", "event_id": ev.event_id,
                                       "description": ev.description, "scope": event_scope(ev)})
        obs = (f"found {len(exp.unplanned_events)} unplanned, {len(exp.planned_events)} planned, "
               f"{len(exp.data_delay_events)} data_delay")
        if exp.unplanned_events:
            obs += ": " + "; ".join(f"{ev.event_id} {event_scope(ev)}" for ev in exp.unplanned_events)
        return f"check_events({anomaly_segment}, lookback={int(lookback.total_seconds() // 3600)}h)", obs

    if kind == "counterfactual":
        try:
            res = tools.counterfactual(
                args["metric"],
                start,
                end,
                args["suspect_segment"],
                args.get("component"),
                parent_segment=anomaly_segment,
            )
        except TypeError:
            res = tools.counterfactual(
                args["metric"], start, end, args["suspect_segment"], args.get("component")
            )
        passed = bool(res.get("passed"))
        record = {"type": "counterfactual", "metric": args["metric"], "suspect_segment": dict(args["suspect_segment"]),
                  **res}
        state.evidence.append(record)
        _apply_counterfactuals(state, hyps, anomaly_metric)
        return f"counterfactual(remove={args['suspect_segment']})", str(res.get("detail") or f"passed={passed}")

    return kind, "no-op"


def formulas_exist(graph: MetricGraph, metric: str) -> bool:
    return any(e.to_metric == metric and e.status != "rejected" for e in graph.edges("formula"))


def _build_finding(
    state: InvestigationState,
    hyps: list[Hypothesis],
    impact: dict[str, Any],
    now: pd.Timestamp,
) -> dict[str, Any]:
    a = state.anomaly
    metric = str(a["metric"])
    seg = dict(a.get("segment") or {})
    seg_key = "|".join(f"{k}={v}" for k, v in sorted(seg.items())) or "all"
    top_cause = hyps[0].node if hyps else metric
    fp_raw = f"{metric}|{seg_key}|{top_cause}"
    finding_id = "fnd_" + hashlib.sha1(f"{fp_raw}|{a['start']}".encode()).hexdigest()[:12]

    actual = float(a["actual"])
    expected = float(a["expected"])
    abs_chg = actual - expected
    rel_pct = (100.0 * abs_chg / expected) if abs(expected) > 1e-12 else 0.0

    unmet = hyps[0].missing_conditions if hyps else ["no_hypotheses_generated"]
    if hyps and hyps[0].status == "confirmed":
        uncertainty = "None — top hypothesis confirmed across all 4 conditions."
    elif state.stop_reason == "insufficient_evidence":
        uncertainty = "Data quality check failed; investigation halted before causal attribution."
    else:
        uncertainty = f"Unmet confirmation conditions for top hypothesis: {', '.join(unmet)}"

    now_iso = now.strftime(TIME_FORMAT)
    return {
        "finding_id": finding_id,
        "fingerprint": fp_raw,
        "metric": metric,
        "segment": seg,
        "time_window": {"start": a["start"], "end": a["end"]},
        "expected": {"value": expected, "lower": float(a["lower"]), "upper": float(a["upper"])},
        "actual": actual,
        "change_size": {"absolute": round(abs_chg, 4), "relative_pct": round(rel_pct, 2), "score": float(a["score"])},
        "business_impact": impact,
        "evidence_list": list(state.evidence),
        "trail": list(state.trail),
        "hypotheses": [h.to_dict() for h in hyps],
        "explained_share": round(float(state.explained_share), 4),
        "stop_reason": state.stop_reason,
        "uncertainty": uncertainty,
        "status": "new",
        "created_at": now_iso,
        "updated_at": now_iso,
    }


def investigate(
    anomaly: Any,
    graph: MetricGraph,
    tools: InvestigationTools,
    calendar: EventCalendar,
    dq_checker: DQCheckerFn,
    chooser: Chooser | None = None,
    limits: Limits | None = None,
    now: Any = None,
    available_tools: set[str] | None = None,
) -> dict[str, Any]:
    """Run the L5 investigation loop and return a Section 4 Finding dict."""
    item = adapt_anomaly(anomaly)
    if item.start >= item.end:
        raise ValueError(f"Anomaly start ({item.start}) must be before end ({item.end})")

    lim = limits or Limits()
    active_chooser: Chooser = chooser or FallbackChooser(min_gain=lim.min_gain)
    now_ts = pd.Timestamp(now).tz_convert("UTC") if (now is not None and pd.Timestamp(now).tzinfo) else (
        pd.Timestamp(now, tz="UTC") if now is not None else item.end
    )

    state = InvestigationState(
        anomaly={
            "metric": item.metric,
            "segment": dict(item.segment),
            "start": item.start.strftime(TIME_FORMAT),
            "end": item.end.strftime(TIME_FORMAT),
            "expected": item.expected,
            "lower": item.lower,
            "upper": item.upper,
            "actual": item.actual,
            "score": item.score,
        }
    )

    # 1. Data-quality gate first.
    dq = dq_checker(item.start, item.end, dict(item.segment))
    if not dq.fresh or not dq.complete:
        state.stop_reason = "insufficient_evidence"
        state.evidence.append({"type": "data_quality", "detail": dq.detail, "completeness": dq.completeness})
        state.trail.append(
            {
                "step": 1,
                "action": "check_data_quality",
                "query": f"check_dq({item.segment})",
                "observation": dq.detail,
                "reason": "Data quality gate failed; stopping with insufficient evidence",
                "chosen_by": "fallback",
                "menu_number": None,
                "fallback_cause": "data-quality gate (code rule, runs before any choice)",
            }
        )
        hyps = build_initial_hypotheses(item.metric, item.segment, graph, dq=dq)
        return _build_finding(state, hyps, {}, now_ts)

    # 2. Check calendar (with 2h lookback for unplanned releases right before window).
    events = calendar.explains(item.start - pd.Timedelta(hours=2), item.end, item.segment, metric=item.metric)
    if events.planned_events:
        state.evidence.append(
            {
                "type": "planned_events",
                "events": [e.event_id for e in events.planned_events],
                "note": "Planned event(s) overlap window; investigating excess change",
            }
        )

    # 3. Build initial hypotheses.
    hyps = build_initial_hypotheses(item.metric, item.segment, graph, events=events, dq=dq)

    # 4. Investigation loop.
    while True:
        _refresh(state, hyps)
        hyps = rank_hypotheses([label_hypothesis(h) for h in hyps])
        state.hypotheses = [h.to_dict() for h in hyps]

        if state.explained_share >= lim.explained_threshold and hyps and hyps[0].status == "confirmed":
            state.stop_reason = "explained_and_confirmed"
            break
        if state.steps_used >= lim.max_steps:
            state.stop_reason = "max_steps_reached"
            break

        menu = build_menu(state, graph, available_tools=available_tools)
        if not menu:
            state.stop_reason = "menu_empty"
            break
        if all(m.expected_gain < lim.min_gain for m in menu):
            state.stop_reason = "min_gain_reached"
            break

        is_llm = hasattr(active_chooser, "client")
        if is_llm and state.llm_calls_used >= lim.max_llm_calls:
            state.stop_reason = "max_llm_calls_reached"
            break

        try:
            choice = active_chooser.choose(state.snapshot(), menu)
        except Exception as exc:  # noqa: BLE001 - e.g. a replay cache miss: rule-based step instead
            fb = FallbackChooser(min_gain=lim.min_gain).choose(state.snapshot(), menu)
            choice = Choice(fb.item_id, fb.reason, fb.decision, fb.stop_reason, used_fallback=True,
                            fallback_reason=f"fallback_used: chooser error: {type(exc).__name__}: {exc}"[:300],
                            chosen_by="fallback", llm_called=is_llm, menu_number=fb.menu_number)
        if is_llm and getattr(choice, "llm_called", not choice.used_fallback):
            state.llm_calls_used += 1
        if choice.used_fallback:
            state.fallbacks_used += 1
            state.fallback_reasons.append(choice.fallback_reason)

        # Only code stops an investigation: the model's reply has no stop option, and
        # LLMChooser turns a stop request into a rule-based step (llm_stop_ignored).
        if choice.decision == "stop" or choice.item_id is None:
            state.stop_reason = choice.stop_reason or "chooser_stopped"
            break

        chosen_item = next((m for m in menu if m.id == choice.item_id), None)
        if chosen_item is None:
            state.stop_reason = "invalid_choice"
            break

        sig = action_signature(chosen_item.kind, chosen_item.args)
        state.completed_actions.append(sig)
        state.steps_used += 1

        try:
            query, obs = _execute_item(
                chosen_item, state, hyps, graph, tools, calendar, item.start, item.end, item.metric, item.segment
            )
        except Exception as exc:
            query = f"{chosen_item.kind}({chosen_item.args})"
            obs = f"tool_error: {exc}"

        reason = choice.reason
        if choice.used_fallback and choice.fallback_reason:
            reason = f"{reason} ({choice.fallback_reason})"
        chosen_by = "llm" if getattr(choice, "chosen_by", "fallback") == "llm" else "fallback"
        if chosen_by == "llm":
            cause = None
        elif choice.fallback_reason:
            cause = choice.fallback_reason.removeprefix("fallback_used: ")
        else:
            cause = "rule-based chooser (no LLM in use)"

        entry = {
            "step": state.steps_used,
            "action": chosen_item.kind,
            "query": query,
            "observation": obs,
            "reason": reason,
            "chosen_by": chosen_by,
            "menu_number": chosen_item.number,
            "fallback_cause": cause,
        }
        if getattr(choice, "stop_ignored", False):
            entry["llm_stop_ignored"] = True
        state.trail.append(entry)

    _refresh(state, hyps)
    hyps = rank_hypotheses([label_hypothesis(h) for h in hyps])
    state.hypotheses = [h.to_dict() for h in hyps]

    try:
        impact = tools.business_impact(item.metric, item.start, item.end, dict(item.segment))
    except Exception:
        impact = {}

    return _build_finding(state, hyps, impact, now_ts)
