"""The autonomous monitor (runs L1-L6 every simulated hour, no human in the loop).

    live = LiveMarketplace()
    mon = AutonomousMonitor(live)
    mon.run(hours=24)            # or mon.tick() one hour at a time
    mon.inject("payment_failure")  # a fault the monitor is not told about
    mon.add_metric({...})          # a new metric with only a definition

Each tick (one simulated hour):

1. **Data**: the hour's rows arrive (plus late rows for earlier hours).
   Freshness and completeness are checked; missing slices open a
   data-quality issue, a backfill resolves it.
2. **Watchlist**: once a day the monitor picks what to watch from metric
   priorities (graph importance, business weight, noisiness) plus the root
   KPIs and the root causes of open issues.
3. **Detection**: every watched metric x grouping x window is scored for the
   new hour (live detector, same rules as batch).
4. **Episodes**: anomalous cells become episodes (metric, segment,
   direction), closed after 3 quiet hours.
5. **Triage**: each new episode (and every 6 hours while it lasts) is checked
   against data quality and the event calendar: held back, explained as
   planned, or reported.
6. **Issues**: reported episodes join the issue they belong to, or open one.
7. **Investigation**: new issues (highest priority first, a budget per hour)
   go to the agent loop; open issues are re-investigated every 6 hours.
8. **Reports and alerts**: one alert when an issue is opened, when its root
   cause changes, when it resolves, or when it recurs.

The monitor never sees the simulator's ground truth: only rows, the event
calendar (release log, planned campaigns) and the metric graph.
"""

from __future__ import annotations

import time
import warnings
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

import numpy as np
import pandas as pd

from agent.chooser import FallbackChooser, LLMChooser
from agent.loop import Limits, RealInvestigationTools, investigate
from extension.pipeline import add_new_metric
from graph.__main__ import DEFAULT_GRAPH
from graph.bridge import run_checks
from graph.engine import MetricEngine
from graph.metric_graph import MetricGraph
from monitoring.data_quality import check_dq
from monitoring.detect import DEFAULT_GROUPINGS, Detector, DetectorConfig, Episode
from monitoring.event_calendar import EventCalendar
from monitoring.live import LiveDetector
from monitoring.priorities import score_metrics
from monitoring.triage import triage
from reporting.issues import DIMS, Issue, IssueTracker, TrackedEpisode, seg_key
from reporting.report import LLMNarrator, build_report
from simulator.stream import LiveMarketplace

HOUR = pd.Timedelta(hours=1)
ALWAYS_WATCH = ["orders", "revenue", "sessions", "row_count"]


@dataclass
class LLMSetup:
    """What the monitor uses to choose steps and write report text."""

    chooser: Any
    narrator: Optional[LLMNarrator]
    enabled: bool
    mode: str
    model: Optional[str]
    report_model: Optional[str]
    note: str
    client: Any = None


def build_llm(use_llm: Optional[bool] = None, cache_mode: Optional[str] = None,
              cache_dir: Optional[str] = None) -> LLMSetup:
    """Model as the primary chooser and report writer when it can be used, rules otherwise.

    ``use_llm=None`` (default): on when ANTHROPIC_API_KEY and LLM_MODEL are set,
    or in replay mode with LLM_MODEL set (replay needs no key and no network).
    ``use_llm=False`` forces the rule-based path; ``True`` asks for the model
    but still falls back (with a note) if it cannot be set up. Never raises.
    """
    import os

    from agent.llm_client import ClaudeClient, LLMCache, llm_configured

    try:
        cache = LLMCache(mode=cache_mode, cache_dir=cache_dir)
    except ValueError as exc:
        return LLMSetup(FallbackChooser(), None, False, "live", None, None, f"LLM off: {exc}")
    model = os.environ.get("LLM_MODEL", "").strip() or None
    report_model = os.environ.get("LLM_REPORT_MODEL", "").strip() or model
    if use_llm is False:
        return LLMSetup(FallbackChooser(), None, False, cache.mode, None, None, "LLM off (rule-based chooser)")
    if cache.mode == "replay":
        if not model:
            return LLMSetup(FallbackChooser(), None, False, cache.mode, None, None,
                            "LLM off: replay mode needs LLM_MODEL (the cache is keyed by model)")
        return LLMSetup(LLMChooser(client=None, cache=cache, model_name=model),
                        LLMNarrator(client=None, cache=cache, model=report_model), True, cache.mode, model,
                        report_model, f"LLM replay from {cache.dir} (no network)")
    ok, why = llm_configured()
    if not ok:
        return LLMSetup(FallbackChooser(), None, False, cache.mode, None, None, f"LLM off: {why}")
    try:
        client = ClaudeClient()
    except Exception as exc:  # noqa: BLE001 - monitoring must run without the model
        return LLMSetup(FallbackChooser(), None, False, cache.mode, None, None, f"LLM off: {exc}")
    return LLMSetup(LLMChooser(client=client, cache=cache), LLMNarrator(client=client, cache=cache), True,
                    cache.mode, client.model, client.report_model,
                    f"LLM on: steps by {client.model}, reports by {client.report_model} (cache {cache.mode})",
                    client)


@dataclass
class FeedItem:
    time: str
    kind: str  # data, watch, detect, triage, issue, investigate, alert, inject, metric, system
    text: str
    issue: Optional[str] = None
    level: str = "info"  # info, decision, alert

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


class AutonomousMonitor:
    def __init__(
        self,
        live: LiveMarketplace,
        watch_budget: int = 16,
        investigate_budget: int = 2,
        reinvestigate_hours: int = 6,
        use_llm: Optional[bool] = None,
        on_feed: Optional[Callable[[FeedItem], None]] = None,
        llm_cache_mode: Optional[str] = None,
        llm_cache_dir: Optional[str] = None,
    ):
        warnings.filterwarnings("ignore", category=RuntimeWarning)
        self.live = live
        self.watch_budget = watch_budget
        self.investigate_budget = investigate_budget
        self.reinvestigate = HOUR * reinvestigate_hours
        self.on_feed = on_feed
        self.feed: list[FeedItem] = []
        self.alerts: list[dict[str, Any]] = []
        self.timings: list[float] = []

        hist = live.history()
        self.table = hist.sort_values("timestamp", kind="stable").reset_index(drop=True)
        self._ts_index = pd.DatetimeIndex(pd.to_datetime(self.table["timestamp"], utc=True))
        self.slices = {d: sorted(hist[d].unique()) for d in DIMS}
        self.engine = MetricEngine(tables={"marketplace_hourly": hist})
        self.graph = MetricGraph.load(DEFAULT_GRAPH)
        checks = run_checks(self.engine, self.graph)
        for name in self.engine.registry.names():
            self.graph.graph.add_node(name)
        self.detector = LiveDetector(self.engine, live.live_start, DetectorConfig(windows=(1, 6)), DEFAULT_GROUPINGS)
        self.tools = RealInvestigationTools(self.engine, self.graph, detector=Detector(self.engine))
        self.tracker = IssueTracker()
        self.calendar = EventCalendar.from_dataframe(live.events)
        self._n_events = len(live.events)
        self.llm = build_llm(use_llm, llm_cache_mode, llm_cache_dir)
        self.use_llm = self.llm.enabled
        self.chooser = self.llm.chooser
        self.narrator = self.llm.narrator
        self.steps = {"llm": 0, "total": 0}
        self.now = live.live_start
        self._open: dict[tuple[str, str, str], TrackedEpisode] = {}
        self._triage: dict[str, tuple[str, pd.Timestamp, str]] = {}  # episode key -> (action, when, reason)
        self._waits: dict[str, int] = {}
        self.watch_reasons: dict[str, str] = {}
        self.last_cells = pd.DataFrame()
        self._say("system", f"monitor started at {self._t(self.now)}: {len(hist):,} rows of history, "
                            f"{len(self.engine.registry)} metrics, graph checks {checks.counts['pass']} passed")
        self._say("system", self.llm.note)
        self._pick_watchlist()

    # --- helpers -------------------------------------------------------------------------------
    @staticmethod
    def _t(ts: pd.Timestamp) -> str:
        return pd.Timestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")

    def _say(self, kind: str, text: str, issue: Optional[str] = None, level: str = "info") -> None:
        item = FeedItem(self._t(self.now), kind, text, issue, level)
        self.feed.append(item)
        if self.on_feed:
            self.on_feed(item)

    def _rows(self, start, end) -> pd.DataFrame:
        ts = self._ts_index
        i, j = ts.searchsorted(pd.Timestamp(start)), ts.searchsorted(pd.Timestamp(end))
        return self.table.iloc[i:j]

    def _dq(self, start, end, segment):
        return check_dq(self._rows(start, end), start, end, now=self.now, expected_slices=self.slices,
                        segment=segment, freshness_minutes=180)

    def _volume(self, m: str) -> bool:
        return m in self.engine.registry and self.engine.registry.get(m).is_additive

    # --- what to watch -----------------------------------------------------------------------------
    def _pick_watchlist(self) -> None:
        reg = self.engine.registry

        def history(m: str) -> list[float]:
            try:
                d = self.engine.compute(m, self.now - pd.Timedelta(days=14), self.now, grain="day")
                return d["value"].dropna().tolist()
            except ValueError:
                return []

        ranked = score_metrics(self.graph, {m.name: m for m in reg}, history)
        chosen = list(dict.fromkeys(ALWAYS_WATCH + [p.metric for p in ranked]))[: self.watch_budget + len(ALWAYS_WATCH)]
        for iss in self.tracker.issues.values():
            if iss.open and iss.root and iss.root.get("node") in reg and iss.root["node"] not in chosen:
                chosen.append(iss.root["node"])
        reasons = {p.metric: p.reason for p in ranked}
        new = self.detector.watch(chosen)
        dropped = [m for m in self.detector.metrics if m not in chosen]
        self.detector.unwatch(dropped)
        self.watch_reasons = {m: reasons.get(m, "always watched") for m in chosen}
        if new or dropped:
            self._say("watch", f"watching {len(chosen)} metrics x {len(DEFAULT_GROUPINGS)} groupings "
                               f"({len(self.detector.keys())} series); top by priority: "
                               + ", ".join(p.metric for p in ranked[:6])
                               + (f"; added {', '.join(new)}" if new and len(new) < len(chosen) else "")
                               + (f"; dropped {', '.join(dropped)}" if dropped else ""), level="decision")

    # --- external inputs ----------------------------------------------------------------------------
    def inject(self, key: str, duration_hours: Optional[float] = None) -> dict:
        """A scenario starts in the marketplace. The monitor is not told what it is."""
        entry = self.live.inject(key, duration_hours=duration_hours)
        self._say("inject", f"[demo] scenario '{key}' starts {entry['start_time']} (the monitor is not told)")
        return entry

    def add_metric(self, definition: dict) -> dict:
        """A new metric arrives with only a definition: place it in the graph and watch it."""
        res = add_new_metric(definition, self.engine, self.graph)
        conf = [e.get("formula") or f"{e['from']} -> {e['to']}" for e in res.confirmed if e["type"] != "breakdown"]
        self._say("metric", f"new metric {definition['name']}: {len(res.confirmed)} connections confirmed"
                            + (f" ({'; '.join(conf[:2])})" if conf else "")
                            + f", {len(res.uncertain)} uncertain, {len(res.extension.rejected_edges)} rejected; now watched",
                  level="decision")
        self.detector.watch([definition["name"]])
        self.watch_reasons[definition["name"]] = "new metric"
        return {"confirmed": res.confirmed, "uncertain": res.uncertain,
                "rejected": len(res.extension.rejected_edges)}

    # --- one hour --------------------------------------------------------------------------------------
    def tick(self) -> None:
        t0 = time.perf_counter()
        tk = self.live.step()
        hour, self.now = tk.hour, tk.hour + HOUR
        self.engine.append_rows("marketplace_hourly", tk.rows)
        self.table = pd.concat([self.table, tk.rows], ignore_index=True)
        if tk.late_rows:
            self.table = self.table.sort_values("timestamp", kind="stable").reset_index(drop=True)
        self._ts_index = pd.DatetimeIndex(pd.to_datetime(self.table["timestamp"], utc=True))
        if len(self.live.events) != self._n_events:
            self.calendar = EventCalendar.from_dataframe(self.live.events)
            new = self.live.events.iloc[self._n_events:]
            self._n_events = len(self.live.events)
            for ev in new.itertuples():
                self._say("data", f"calendar: {ev.event_type} {ev.event_id} ({'planned' if ev.planned else 'unplanned'})"
                                  f" {ev.description}")
        if hour.hour == 0:
            self._pick_watchlist()

        # 1. data: freshness, completeness, backfills
        if tk.late_rows:
            late_hours = sorted(set(pd.to_datetime(tk.rows["timestamp"], utc=True)) - {hour})
            self._say("data", f"{tk.late_rows} late rows arrived for {', '.join(h.strftime('%H:%M') for h in late_hours)}")
            complete = [h for h in late_hours if self._dq(h, h + HOUR, {}).complete]
            for iss in self.tracker.data_complete(complete, self.now):
                iss.report = build_report(iss, self.tracker)
                self._alert(iss, "resolved")
        dq = check_dq(self._rows(hour, self.now), hour, self.now, now=self.now, expected_slices=self.slices,
                      completeness_min=0.999)
        if not dq.complete:
            delays = [e.event_id for e in self.calendar.explains(hour, self.now, {}).data_delay_events]
            iss, created = self.tracker.data_gap(hour, dq.missing, delays, self.now)
            iss.report = build_report(iss, self.tracker)
            if created:
                self._say("data", f"{len(dq.missing)} slices missing for {hour:%H:%M} ({seg_key(iss.segment)}): "
                                  "held back as incomplete, not treated as a business change", iss.id, "decision")
                self._alert(iss, "opened")

        # 2-4. detection and episodes
        self.detector.refresh(pd.to_datetime(tk.rows["timestamp"], utc=True).min(), self.now)
        cells = self.detector.score_hour(hour)
        self.last_cells = cells
        new_eps = self._update_episodes(cells, hour)

        # 5-6. triage, issues
        for ep in self._open.values():
            due = ep.key not in self._triage or self.now - self._triage[ep.key][1] >= self.reinvestigate
            if not ep.open or not due:
                continue
            self._triage_episode(ep, is_new=ep in new_eps)

        # 7-8. investigations, reports, alerts
        self._investigate()
        for iss, kind in self.tracker.tick(self.now):
            iss.report = build_report(iss, self.tracker, self.narrator)
            self._alert(iss, kind)
        for k in [k for k, e in self._open.items() if not e.open]:
            del self._open[k]
        self.timings.append(time.perf_counter() - t0)

    def _update_episodes(self, cells: pd.DataFrame, hour: pd.Timestamp) -> list[TrackedEpisode]:
        new = []
        if cells.empty:
            return new
        an = cells[cells["is_anomaly"]]
        for _, g in an.groupby(["metric", "by", "direction"] + [d for d in DIMS if d in an.columns], dropna=False):
            r = g.loc[g["z"].abs().idxmax()]
            seg = {d: str(r[d]) for d in DIMS if d in r.index and isinstance(r[d], str) and r[d] and r[d] != "nan"}
            k = (r["metric"], seg_key(seg), r["direction"])
            ep = self._open.get(k)
            if ep is None or not ep.open:
                # a 6h window flags a change that started up to 5 hours earlier
                first = pd.Timestamp(r["window_start"])
                ep = TrackedEpisode(key=f"{k[0]}|{k[1]}|{k[2]}|{hour:%m%d%H}", metric=r["metric"], segment=seg,
                                    direction=r["direction"], impact=r["impact"], first=first, last=hour,
                                    peak_z=float(r["z"]), actual=float(r["actual"]), expected=float(r["expected"]),
                                    lower=float(r["lower"]), upper=float(r["upper"]))
                self._open[k] = ep
                self.tracker.episodes[ep.key] = ep
                new.append(ep)
            else:
                ep.last = hour
                if abs(r["z"]) > abs(ep.peak_z):
                    ep.peak_z, ep.actual, ep.expected = float(r["z"]), float(r["actual"]), float(r["expected"])
                    ep.lower, ep.upper = float(r["lower"]), float(r["upper"])
        if new:
            strongest = sorted(new, key=lambda e: -abs(e.peak_z))[:4]
            self._say("detect", f"{len(new)} new anomalies, strongest: " + "; ".join(
                f"{e.metric} {e.direction} in {seg_key(e.segment)} (z={e.peak_z:.0f})" for e in strongest))
        return new

    def _as_episode(self, ep: TrackedEpisode) -> Episode:
        rel = ep.actual / ep.expected - 1 if ep.expected else float("nan")
        return Episode(metric=ep.metric, segment=dict(ep.segment), window_hours=1, direction=ep.direction,
                       impact=ep.impact, start=ep.first, end=self.now,
                       hours=int((self.now - ep.first) / HOUR), peak_z=ep.peak_z, peak_time=ep.last + HOUR,
                       actual=ep.actual, expected=ep.expected, lower=ep.lower, upper=ep.upper,
                       rel_change=rel, abs_change=ep.actual - ep.expected, ongoing=True, windows=[1, 6])

    def _triage_episode(self, ep: TrackedEpisode, is_new: bool) -> None:
        if ep.metric == "row_count":
            self._triage[ep.key] = ("insufficient_data", self.now, "row count is a data-quality signal")
            return
        d = triage(self._as_episode(ep), self.calendar, self._dq, volume_metric=self._volume)
        before = self._triage.get(ep.key, ("", None, ""))[0]
        if d.action == "report" and d.events.planned_events and self._waits.get(ep.key, 0) < 3:
            # a planned event covers this segment: give it a few hours to settle
            self._waits[ep.key] = self._waits.get(ep.key, 0) + 1
            self._triage[ep.key] = ("wait", self.now - self.reinvestigate + HOUR, d.reasons[0] if d.reasons else "")
            return
        self._triage[ep.key] = (d.action, self.now, d.reasons[0] if d.reasons else "")
        if d.action == "suppress_planned":
            if before != "suppress_planned":
                ev = ", ".join(e.event_id for e in d.events.planned_events)
                self._say("triage", f"{ep.metric} {ep.direction} in {seg_key(ep.segment)} explained by planned "
                                    f"event {ev}: not an incident", level="decision")
                closed = self.tracker.detach(ep.key, self.now, f"explained by planned event {ev}")
                if closed is not None:
                    closed.report = build_report(closed, self.tracker)
                    self._say("issue", f"{closed.id} closed: everything in it is explained by planned event {ev}",
                              closed.id, "decision")
            return
        if d.action == "insufficient_data":
            if before != "insufficient_data":
                self._say("triage", f"{ep.metric} in {seg_key(ep.segment)}: data incomplete, held back", level="decision")
            return
        iss, created = self.tracker.route(ep, self.now)
        weight = self.engine.registry.get(ep.metric).business_weight if ep.metric in self.engine.registry else 0.5
        iss.priority = max(iss.priority, weight * min(abs(ep.peak_z), 50.0))
        if created:
            self._say("issue", f"{iss.id} opened: {ep.metric} {ep.direction} {abs(100 * (ep.actual / ep.expected - 1)):.0f}% "
                               f"in {seg_key(ep.segment)} (z={ep.peak_z:.0f}); queued for investigation", iss.id, "decision")
        elif is_new:
            self._say("issue", f"{ep.metric} {ep.direction} in {seg_key(ep.segment)} attached to {iss.id} "
                               f"(same problem, not a new alert)", iss.id)

    def _investigate(self) -> None:
        due = [i for i in self.tracker.issues.values() if i.kind == "incident" and (
            i.status == "investigating" or (i.open and i.last_investigated is not None
                                             and self.now - i.last_investigated >= self.reinvestigate))]
        due.sort(key=lambda i: (i.status != "investigating", -i.priority))
        for iss in due[: self.investigate_budget]:
            eps = [self.tracker.episodes[k] for k in iss.episodes if k in self.tracker.episodes]
            primary = next((e for e in eps if e.metric == iss.metric and e.segment == iss.segment), eps[0])
            first = min(e.first for e in eps)
            ep = self._as_episode(primary)
            ep.start = first
            try:
                finding = investigate(ep, self.graph, self.tools, self.calendar, self._dq, chooser=self.chooser,
                                      limits=Limits(), now=self.now)
            except Exception as exc:  # noqa: BLE001 - a failing investigation must not stop monitoring
                self._say("investigate", f"{iss.id}: investigation failed ({type(exc).__name__}: {exc})", iss.id)
                iss.last_investigated = self.now
                continue
            self.steps["total"] += len(finding["trail"])
            self.steps["llm"] += sum(t.get("chosen_by") == "llm" for t in finding["trail"])
            alert = self.tracker.set_finding(iss, finding, self.now, downstream=self._downstream(finding))
            target = self.tracker.issues[iss.merged_into] if iss.merged_into else iss
            node = (target.root or {}).get("node")
            if node and node in self.engine.registry and node not in self.detector.metrics:
                self.detector.watch([node])  # follow the root cause itself from now on
                self.watch_reasons[node] = f"root cause of {target.id}"
            plan = self._explained_by_plan(target, finding)
            if plan:
                self.tracker.close_explained(target, self.now, plan)
                target.report = build_report(target, self.tracker)
                self._say("triage", f"{target.id} closed after investigation: {plan}; not an incident",
                          target.id, "decision")
                if target.alerts:
                    self._alert(target, "resolved")
                continue
            target.report = build_report(target, self.tracker, self.narrator)
            root = target.root or {}
            self._say("investigate", f"{target.id}: {len(finding['trail'])} steps, stop={finding['stop_reason']} -> "
                                     f"{root.get('status', '?')}: {root.get('cause', 'no cause')} in "
                                     f"{seg_key(root.get('segment') or {})}", target.id, "decision")
            if iss.merged_into:
                self._say("issue", f"{iss.id} merged into {iss.merged_into}: same root cause", iss.merged_into)
            if alert:
                self._alert(target, alert)

    def _downstream(self, finding: dict) -> set[str]:
        """Metrics the top cause feeds into, through formula edges, plus new metrics
        defined on them: their anomalies are symptoms of this issue."""
        import networkx as nx

        hyps = [h for h in finding.get("hypotheses", []) if h.get("status") != "rejected" and not h.get("is_symptom")]
        if not hyps:
            return set()
        g = nx.DiGraph()
        for e in self.graph.edges("formula"):
            g.add_edge(e.from_metric, e.to_metric)
        for e in self.graph.edges("possible_cause"):
            if e.status != "rejected":
                g.add_edge(e.from_metric, e.to_metric)
        out: set[str] = set()
        for h in hyps[:2]:
            node = h["node"]
            if node in g:
                out |= nx.descendants(g, node)
        extra = {e.from_metric for e in self.graph.edges("formula")
                 if e.to_metric in out and e.source == "auto_discovered"}
        return out | extra

    def _explained_by_plan(self, iss: Issue, finding: dict) -> Optional[str]:
        """After investigation: is the change localized inside an active planned event
        (moving the way it says)? Then it is explained, not an incident."""
        tw = finding.get("time_window") or {}
        try:
            start, end = pd.Timestamp(tw["start"]), pd.Timestamp(tw["end"])
        except (KeyError, ValueError):
            return None
        segs = [ev.get("localized_segment") for ev in finding.get("evidence_list", [])
                if ev.get("type") == "dimension_drilldown" and ev.get("localized_segment")]
        if iss.root and iss.root.get("segment"):
            segs.append(iss.root["segment"])
        eps = [self.tracker.episodes[k] for k in iss.episodes if k in self.tracker.episodes]
        direction = eps[0].direction if eps else None
        for seg in segs:
            exp = self.calendar.explains(start, end, seg)
            for ev in exp.planned_events:
                pct = ev.effect_pct
                if pct is None or direction is None or (pct < 0) == (direction == "down"):
                    return f"explained by planned {ev.event_type} {ev.event_id} (localized to {seg_key(seg)})"
        return None

    def _alert(self, iss: Issue, kind: str) -> None:
        r = iss.report or build_report(iss, self.tracker)
        iss.report = r
        worth = iss.kind == "data_quality" or r["severity"] != "low" or bool(iss.alerts)
        if kind == "resolved" and not iss.alerts:
            worth = False  # never alerted: resolve it quietly
        if not worth:
            if kind == "opened":
                self._say("issue", f"{iss.id} tracked quietly (low impact, no alert): {r['title']}", iss.id)
            if kind == "resolved":
                self._say("issue", f"{iss.id} resolved quietly: {r['title']}", iss.id)
            return
        if kind == "updated" and not any(a["type"] == "opened" for a in iss.alerts):
            kind = "opened"  # first alert for an issue that became important
        elif kind == "opened" and any(a["type"] == "opened" for a in iss.alerts):
            kind = "updated"
        a = {"time": self._t(self.now), "issue": iss.id, "type": kind, "severity": r["severity"],
             "title": r["title"], "headline": r["headline"], "status": iss.status}
        iss.alerts.append(a)
        self.alerts.append(a)
        self._say("alert", f"[{r['severity'].upper()}] {iss.id} {kind}: {r['title']}. {r['headline']}", iss.id, "alert")

    # --- running ---------------------------------------------------------------------------------------
    def run(self, hours: Optional[int] = None, script: Optional[dict[int, list[tuple[str, Any]]]] = None,
            delay: float = 0.0, should_stop: Callable[[], bool] = lambda: False) -> None:
        """Run ``hours`` ticks (default: to the end). ``script`` maps hour offset -> actions."""
        n = 0
        while not self.live.finished and (hours is None or n < hours) and not should_stop():
            offset = int((self.now - self.live.live_start) / HOUR)
            for action, arg in (script or {}).get(offset, []):
                if action == "inject":
                    self.inject(*arg) if isinstance(arg, tuple) else self.inject(arg)
                elif action == "metric":
                    self.add_metric(arg)
            self.tick()
            n += 1
            if delay:
                time.sleep(delay)

    # --- views (dashboard / API) ----------------------------------------------------------------------
    def state(self) -> dict[str, Any]:
        issues = self.tracker.visible()
        return {
            "now": self._t(self.now), "live_start": self._t(self.live.live_start), "end": self._t(self.live.end),
            "hours_done": int((self.now - self.live.live_start) / HOUR),
            "hours_total": int((self.live.end - self.live.live_start) / HOUR),
            "finished": self.live.finished,
            "tick_seconds": round(float(np.mean(self.timings[-10:])), 2) if self.timings else None,
            "counts": {s: sum(1 for i in issues if i.status == s) for s in ("investigating", "new", "ongoing", "resolved")},
            "issues": [{"id": i.id, "kind": i.kind, "status": i.status, "severity": (i.report or {}).get("severity", "low"),
                        "title": (i.report or {}).get("title", f"{i.metric} in {seg_key(i.segment)}"),
                        "headline": (i.report or {}).get("headline", "under investigation"),
                        "opened_at": self._t(i.opened_at), "resolved_at": self._t(i.resolved_at) if i.resolved_at else None,
                        "alerts": len(i.alerts), "episodes": len(i.episodes)} for i in issues],
            "alerts": self.alerts[-50:],
            "feed": [f.to_dict() for f in self.feed[-300:]],
            "watchlist": self.watch_reasons,
            "open_anomalies": [e.summary() for e in self._open.values() if e.open][:60],
            "metrics": sorted(self.engine.registry.names()),
            "llm": self.llm_stats(),
        }

    def llm_stats(self) -> dict[str, Any]:
        """Model use in this run: steps it chose, calls, tokens, fallbacks, report texts."""
        usage = getattr(self.llm.client, "usage", None)
        counts = getattr(self.chooser, "counts", {})
        nar = self.narrator
        return {
            "enabled": self.llm.enabled, "mode": self.llm.mode, "model": self.llm.model,
            "report_model": self.llm.report_model, "note": self.llm.note,
            "steps_llm": self.steps["llm"], "steps_total": self.steps["total"],
            "step_fallbacks": int(counts.get("fallback", 0)), "stop_requests_ignored": int(counts.get("stop_ignored", 0)),
            "calls": usage.calls if usage else 0, "call_failures": usage.failures if usage else 0,
            "tokens_in": usage.tokens_in if usage else 0, "tokens_out": usage.tokens_out if usage else 0,
            "reports_by_llm": nar.used if nar else 0, "reports_rejected": nar.failures if nar else 0,
        }

    def issue_detail(self, issue_id: str) -> Optional[dict[str, Any]]:
        iss = self.tracker.issues.get(issue_id)
        if iss is None:
            return None
        r = iss.report or build_report(iss, self.tracker)
        charts = []
        targets = [(iss.metric, iss.segment)]
        if iss.root and iss.root.get("node") in self.engine.registry:
            root_target = (iss.root["node"], iss.root.get("segment") or {})
            if root_target not in targets:  # don't draw the same chart twice
                targets.append(root_target)
        for m, seg in targets:
            df = self.series(m, seg, hours=96)
            if df:
                charts.append(df)
        return {"issue": iss.to_dict(), "report": r, "charts": charts}

    def series(self, metric: str, segment: dict, hours: int = 96) -> Optional[dict[str, Any]]:
        if metric not in self.engine.registry:
            return None
        if metric not in self.detector.metrics:
            self.detector.watch([metric])
        df = self.detector.baseline_frame(metric, segment, hours, self.now)
        if df.empty:
            return None
        clean = lambda s: [None if not np.isfinite(v) else round(float(v), 6) for v in s]  # noqa: E731
        return {"metric": metric, "segment": segment, "time": [self._t(t) for t in df["period"]],
                "actual": clean(df["actual"]), "expected": clean(df["expected"]),
                "lower": clean(df["lower"]), "upper": clean(df["upper"])}

    def tree(self) -> dict[str, Any]:
        """The metric graph's formula tree plus which nodes are anomalous now."""
        anomalous = {}
        for e in self._open.values():
            if e.open:
                anomalous[e.metric] = max(anomalous.get(e.metric, 0.0), abs(e.peak_z))
        roots = {i.root["node"] for i in self.tracker.issues.values() if i.open and i.root}
        edges = []
        seen = set()
        for ed in self.graph.edges():
            key = (ed.from_metric, ed.to_metric, ed.type, ed.formula or ed.dimension or "")
            if ed.type == "breakdown" or key in seen:
                continue
            seen.add(key)
            edges.append({"from": ed.from_metric, "to": ed.to_metric, "type": ed.type, "status": ed.status,
                          "formula": ed.formula, "source": ed.source})
        nodes = sorted({e["from"] for e in edges} | {e["to"] for e in edges})
        return {"nodes": [{"id": n, "anomalous": round(anomalous.get(n, 0.0), 1), "root_cause": n in roots,
                           "watched": n in self.detector.metrics} for n in nodes], "edges": edges}
