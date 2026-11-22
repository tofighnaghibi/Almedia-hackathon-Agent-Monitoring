"""Issue tracking: one issue per problem, alerts only when something changes (L6, Person B).

A live system produces many anomalous episodes for one problem: a payment
failure shows up in payment success, orders, revenue and conversion, in the
country and in the country/platform segment. The tracker turns them into
issues:

- **Routing**: a new episode joins an open issue when it overlaps in time,
  its segment is compatible (no conflicting dimension values: ``DE`` and
  ``DE/android`` are compatible, ``DE`` and ``FR`` are not), and its metric
  is part of the issue's explanation (the metrics in the investigation's
  contribution tree). Otherwise it opens a new issue, to be investigated.
- **Fingerprint**: once investigated, an issue is identified by its root
  cause: ``node|segment`` of the top hypothesis. Two open issues with the same
  fingerprint are merged. A fingerprint that comes back within
  ``reopen_hours`` of being resolved reopens the old issue ("recurred").
- **Lifecycle**: investigating -> new -> ongoing -> resolved. An issue is
  resolved when its root-cause episode (or, before one is known, every
  episode) has been quiet for ``quiet_hours``.
- **Alerts**: one when the issue is opened (after its first investigation),
  one when the root cause or its confidence changes, one when it is
  resolved, and one when it recurs. Never one per hour.

Data-quality issues (missing or late rows) are tracked the same way, with
``kind="data_quality"``.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Optional

import pandas as pd

HOUR = pd.Timedelta(hours=1)
DIMS = ("country", "platform", "category")
IMPACT_METRICS = {"orders", "revenue", "conversion_rate", "revenue_per_session", "average_order_value",
                  "return_on_ad_spend", "orders_per_search"}


def seg_key(segment: dict) -> str:
    return "|".join(f"{d}={segment[d]}" for d in DIMS if d in segment) or "all"


def compatible(a: dict, b: dict) -> bool:
    """No dimension has two different values."""
    return all(a[d] == b[d] for d in a if d in b)


@dataclass
class TrackedEpisode:
    key: str  # metric|segment|direction
    metric: str
    segment: dict[str, str]
    direction: str
    impact: str
    first: pd.Timestamp
    last: pd.Timestamp  # last flagged hour
    peak_z: float
    actual: float
    expected: float
    lower: float
    upper: float
    open: bool = True

    def summary(self) -> dict[str, Any]:
        rel = self.actual / self.expected - 1 if self.expected else float("nan")
        return {"key": self.key, "metric": self.metric, "segment": self.segment, "direction": self.direction,
                "impact": self.impact, "first": _iso(self.first), "last": _iso(self.last),
                "peak_z": round(float(self.peak_z), 2), "rel_change_pct": round(100 * rel, 1) if rel == rel else None,
                "open": self.open}


@dataclass
class Issue:
    id: str
    kind: str  # "incident" or "data_quality"
    metric: str  # what opened it
    segment: dict[str, str]
    opened_at: pd.Timestamp
    last_seen: pd.Timestamp
    status: str = "investigating"  # investigating, new, ongoing, resolved, merged
    resolved_at: Optional[pd.Timestamp] = None
    episodes: list[str] = field(default_factory=list)  # TrackedEpisode keys
    related: set[str] = field(default_factory=set)  # metrics that belong to this issue
    root: Optional[dict[str, Any]] = None
    fingerprint: Optional[str] = None
    finding: Optional[dict[str, Any]] = None
    report: Optional[dict[str, Any]] = None
    alerts: list[dict[str, Any]] = field(default_factory=list)
    history: list[dict[str, Any]] = field(default_factory=list)
    investigations: int = 0
    last_investigated: Optional[pd.Timestamp] = None
    priority: float = 0.0
    data_detail: dict[str, Any] = field(default_factory=dict)  # data-quality issues: missing hours, backfill
    merged_into: Optional[str] = None

    @property
    def open(self) -> bool:
        return self.status not in ("resolved", "merged")

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["related"] = sorted(self.related)
        for k in ("opened_at", "last_seen", "resolved_at", "last_investigated"):
            d[k] = _iso(getattr(self, k)) if getattr(self, k) is not None else None
        return d


@dataclass
class IssueTracker:
    quiet_hours: int = 3
    reopen_hours: int = 24
    attach_gap_hours: int = 2
    issues: dict[str, Issue] = field(default_factory=dict)
    episodes: dict[str, TrackedEpisode] = field(default_factory=dict)
    _n: int = 0

    # --- routing -----------------------------------------------------------------
    def route(self, ep: TrackedEpisode, now: pd.Timestamp, kind: str = "incident") -> tuple[Issue, bool]:
        """The issue ``ep`` belongs to; creates one if none fits. Returns (issue, created)."""
        self.episodes[ep.key] = ep
        known = self.issue_of(ep.key)
        if known is not None:
            self._attach(known, ep, now)
            return known, False
        def strength(iss: Issue) -> int:
            """How directly ``ep`` is part of ``iss`` (0 = not at all)."""
            if not (iss.open and iss.kind == kind and ep.first <= iss.last_seen + HOUR * self.attach_gap_hours):
                return 0
            target = iss.segment if iss.root is None else iss.root.get("segment", iss.segment)
            if not compatible(ep.segment, target):
                return 0
            root_node = (iss.root or {}).get("node")
            if ep.metric in (root_node, iss.metric):
                return 4
            if ep.metric in iss.related:
                return 3
            if kind == "incident" and ep.metric in IMPACT_METRICS and (
                    _contains(ep.segment, iss) or (iss.root is not None and ep.segment == target)):
                return 2
            return 0

        scored = [(strength(i), i) for i in self.issues.values()]
        scored = [(k, i) for k, i in scored if k > 0]
        if scored:
            iss = max(scored, key=lambda t: (t[0], -int(t[1].opened_at.value)))[1]
            self._attach(iss, ep, now)
            return iss, False
        self._n += 1
        iss = Issue(id=f"ISS-{self._n}", kind=kind, metric=ep.metric, segment=dict(ep.segment),
                    opened_at=ep.first, last_seen=ep.last, related={ep.metric})
        self.issues[iss.id] = iss
        self._attach(iss, ep, now)
        iss.history.append({"time": _iso(now), "event": "opened", "detail": f"{ep.metric} {ep.direction} in {seg_key(ep.segment)}"})
        return iss, True

    def _attach(self, iss: Issue, ep: TrackedEpisode, now: pd.Timestamp) -> None:
        if ep.key not in iss.episodes:
            iss.episodes.append(ep.key)
        iss.last_seen = max(iss.last_seen, ep.last)
        # ``related`` comes from the investigation and the graph only: attaching a symptom
        # must not widen what the issue claims to explain

    def issue_of(self, ep_key: str) -> Optional[Issue]:
        for iss in self.issues.values():
            if ep_key in iss.episodes and iss.status != "merged":
                return iss
        return None

    # --- investigation results --------------------------------------------------------
    def set_finding(self, iss: Issue, finding: dict[str, Any], now: pd.Timestamp,
                    downstream: Optional[set[str]] = None) -> Optional[str]:
        """Store an investigation. Returns the alert type to send, if any.

        ``downstream``: metrics the root cause feeds into (from the graph); their
        anomalies in compatible segments are symptoms of this issue."""
        iss.investigations += 1
        iss.last_investigated = now
        prev_root = dict(iss.root) if iss.root else None
        hyps = [h for h in finding.get("hypotheses", []) if h.get("status") != "rejected"]
        causes = [h for h in hyps if not h.get("is_symptom")] or hyps
        top = causes[0] if causes else None
        # A re-investigation that finds the same cause with less evidence (it was confirmed,
        # this window could not confirm it again) is not news: keep the confirmed finding
        # instead of flipping the label back and forth and alerting each time.
        if (prev_root is not None and iss.finding and prev_root.get("status") == "confirmed" and top is not None
                and top.get("status") != "confirmed" and top["node"] == prev_root.get("node")
                and dict(top.get("segment") or iss.segment) == prev_root.get("segment")):
            iss.history.append({"time": _iso(now), "event": "re-investigated",
                                "detail": "same cause, not re-confirmed in this window; the confirmed finding is kept"})
            return None
        iss.finding = finding
        if top is not None:
            iss.root = {"node": top["node"], "segment": dict(top.get("segment") or iss.segment),
                        "status": top["status"], "cause": top["cause"], "source": top["source"],
                        "missing": list(top.get("missing_conditions") or []),
                        "explained_share": float(top.get("explained_share") or 0.0),
                        "event_id": _event_id(top)}
            iss.fingerprint = f"{top['node']}|{seg_key(iss.root['segment'])}"
        for ev in finding.get("evidence_list", []):
            for n in ev.get("nodes", []) or []:
                if float(n.get("share", 0)) >= 0.05:
                    iss.related.add(n["metric"])
        if iss.root:
            iss.related.add(iss.root["node"])
        iss.related |= set(downstream or ())

        # same root cause as another issue: merge, or reopen a recent resolved one
        if iss.fingerprint:
            for other in self.issues.values():
                if other is iss or other.fingerprint != iss.fingerprint or other.kind != iss.kind:
                    continue
                if other.open:
                    keep, drop = (other, iss) if other.opened_at <= iss.opened_at else (iss, other)
                    self._merge(keep, drop, now)
                    return None if drop is iss else "updated"
                if other.status == "resolved" and other.resolved_at is not None and \
                        iss.opened_at - other.resolved_at <= HOUR * self.reopen_hours:
                    self._merge(other, iss, now)
                    other.status, other.resolved_at = "ongoing", None
                    other.history.append({"time": _iso(now), "event": "recurred"})
                    return "recurred"

        merged = self._absorb(iss, now)
        if merged is not None:
            return None  # a symptom of an open issue: no alert of its own
        if iss.status == "investigating":
            iss.status = "new"
            iss.history.append({"time": _iso(now), "event": "investigated", "detail": iss.root.get("cause") if iss.root else ""})
            return "opened"
        # a narrower or wider segment is a refinement, not news; a new root metric or a confirmation is
        changed = prev_root is None or iss.root is None or prev_root.get("node") != iss.root.get("node") \
            or prev_root.get("status") != iss.root.get("status")
        if changed:
            iss.history.append({"time": _iso(now), "event": "root cause updated", "detail": iss.root.get("cause") if iss.root else ""})
            return "updated"
        return None

    def _in_chain(self, cause: Issue, other: Issue) -> bool:
        """``other`` is part of ``cause``'s causal chain: the same root metric, or a
        root that is only a symptom of it (downstream, or a revenue/conversion
        metric), in a compatible and overlapping segment, at the same time."""
        c_root, o_root = cause.root or {}, other.root or {}
        if not c_root or not o_root or cause.kind != "incident" or other.kind != "incident":
            return False
        overlap = other.opened_at <= cause.last_seen + HOUR * self.attach_gap_hours and \
            cause.opened_at <= other.last_seen + HOUR * self.attach_gap_hours
        c_seg, o_seg = c_root.get("segment") or {}, o_root.get("segment") or other.segment
        if not (overlap and compatible(o_seg, c_seg) and _overlaps(o_seg, c_seg)):
            return False
        if o_root.get("node") == c_root.get("node"):
            return True
        return ((o_root.get("node") in cause.related or o_root.get("node") in IMPACT_METRICS)
                and o_root.get("node") not in self._own_causes(cause)
                and not (o_root.get("status") == "confirmed" and o_root.get("source") == "graph_cause_edge"))

    def _absorb(self, iss: Issue, now: pd.Timestamp) -> Optional[Issue]:
        """Merge issues of one causal chain, in either direction. Returns the issue
        ``iss`` was merged into, if it was found to be a symptom of an open one."""
        if iss.root is None or iss.kind != "incident":
            return None
        for other in list(self.issues.values()):
            if other is iss or not other.open or other.status == "investigating":
                continue
            if self._in_chain(iss, other) or self._in_chain(other, iss):
                keep, drop = (iss, other) if _rank(iss) >= _rank(other) else (other, iss)
                if self._in_chain(other, iss) and not self._in_chain(iss, other):
                    keep, drop = other, iss  # iss is a symptom of other's cause
                self._merge(keep, drop, now)
                if drop is iss:
                    return keep
        return None

    def close_explained(self, iss: Issue, now: pd.Timestamp, reason: str) -> None:
        """The investigation showed the change sits inside a planned event: not an incident."""
        iss.status, iss.resolved_at = "resolved", now
        iss.data_detail["explained"] = reason
        iss.history.append({"time": _iso(now), "event": "explained", "detail": reason})
        for k in iss.episodes:
            if k in self.episodes:
                self.episodes[k].open = False

    @staticmethod
    def _own_causes(iss: Issue) -> set[str]:
        """Metrics this issue's investigation found as separate (non-symptom) causes."""
        f = iss.finding or {}
        return {h["node"] for h in f.get("hypotheses", []) if not h.get("is_symptom")
                and h.get("status") == "confirmed" and h["node"] != (iss.root or {}).get("node")}

    def detach(self, ep_key: str, now: pd.Timestamp, reason: str) -> Optional[Issue]:
        """An episode turned out to be explained (e.g. by a planned event): take it
        out of its issue. Returns the issue if nothing reportable is left in it."""
        iss = self.issue_of(ep_key)
        if iss is None or not iss.open:
            return None
        iss.episodes = [k for k in iss.episodes if k != ep_key]
        if not iss.episodes:
            iss.status, iss.resolved_at = "resolved", now
            iss.history.append({"time": _iso(now), "event": "resolved", "detail": reason})
            iss.data_detail["explained"] = reason
            return iss
        return None

    def _merge(self, keep: Issue, drop: Issue, now: pd.Timestamp) -> None:
        for k in drop.episodes:
            if k not in keep.episodes:
                keep.episodes.append(k)
        keep.related |= drop.related
        keep.last_seen = max(keep.last_seen, drop.last_seen)
        keep.opened_at = min(keep.opened_at, drop.opened_at)
        if drop.finding and (keep.finding is None or drop.investigations and not keep.investigations):
            keep.finding, keep.root, keep.fingerprint = drop.finding, drop.root, drop.fingerprint
        keep.alerts = sorted(keep.alerts + [a for a in drop.alerts if a not in keep.alerts], key=lambda a: a["time"])
        if drop.status not in ("investigating",) and keep.status == "investigating":
            keep.status = drop.status
        drop.status, drop.merged_into = "merged", keep.id
        keep.history.append({"time": _iso(now), "event": "merged", "detail": f"{drop.id} has the same root cause"})

    # --- the passage of time ----------------------------------------------------------
    def tick(self, now: pd.Timestamp) -> list[tuple[Issue, str]]:
        """Close quiet episodes, move statuses on, resolve. Returns (issue, alert) pairs."""
        alerts = []
        for ep in self.episodes.values():
            if ep.open and now - (ep.last + HOUR) >= HOUR * self.quiet_hours:
                ep.open = False
        for iss in list(self.issues.values()):
            if not iss.open:
                continue
            eps = [self.episodes[k] for k in iss.episodes if k in self.episodes]
            if iss.kind == "data_quality":
                watching = eps
            else:
                root_eps = [e for e in eps if iss.root and e.metric == iss.root.get("node")
                            and e.segment == iss.root.get("segment")]
                watching = root_eps or eps
            if iss.status == "new" and now - iss.opened_at >= HOUR * 3:
                iss.status = "ongoing"
            if iss.kind == "data_quality" and iss.data_detail.get("pending_hours"):
                continue  # resolved by the backfill, see data_complete()
            if watching and not any(e.open for e in watching) and iss.status != "investigating":
                iss.status, iss.resolved_at = "resolved", now
                iss.history.append({"time": _iso(now), "event": "resolved"})
                alerts.append((iss, "resolved"))
        return alerts

    # --- data quality --------------------------------------------------------------------
    def data_gap(self, hour: pd.Timestamp, missing: list[dict], events: list[str], now: pd.Timestamp) -> tuple[Issue, bool]:
        """Rows missing for ``hour``: a data-quality issue per affected segment family."""
        seg = _common(missing)
        ep = TrackedEpisode(key=f"row_count|{seg_key(seg)}|gap", metric="row_count", segment=seg, direction="down",
                            impact="neutral", first=hour, last=hour, peak_z=0.0,
                            actual=0.0, expected=float(len(missing)), lower=0.0, upper=0.0)
        existing = next((i for i in self.issues.values() if i.kind == "data_quality" and i.open
                         and compatible(i.segment, seg) and hour <= i.last_seen + HOUR * self.attach_gap_hours), None)
        if existing:
            existing.last_seen = max(existing.last_seen, hour)
            existing.data_detail.setdefault("pending_hours", [])
            if _iso(hour) not in existing.data_detail["pending_hours"]:
                existing.data_detail["pending_hours"].append(_iso(hour))
            existing.data_detail["missing_rows"] = existing.data_detail.get("missing_rows", 0) + len(missing)
            return existing, False
        iss, _ = self.route(ep, now, kind="data_quality")
        iss.status = "new"
        iss.root = {"node": "row_count", "segment": seg, "status": "confirmed", "source": "data_quality",
                    "cause": f"rows missing for {seg_key(seg)}" + (f" (data_delay {', '.join(events)})" if events else ""),
                    "missing": [], "explained_share": 1.0, "event_id": events[0] if events else None}
        iss.fingerprint = f"row_count|{seg_key(seg)}"
        iss.data_detail = {"pending_hours": [_iso(hour)], "missing_rows": len(missing), "events": events}
        return iss, True

    def data_complete(self, hours: list[pd.Timestamp], now: pd.Timestamp) -> list[Issue]:
        """Hours that are now complete (late rows arrived). Returns issues resolved by it."""
        done = {_iso(h) for h in hours}
        resolved = []
        for iss in self.issues.values():
            if iss.kind != "data_quality" or not iss.open:
                continue
            pend = [h for h in iss.data_detail.get("pending_hours", []) if h not in done]
            if len(pend) < len(iss.data_detail.get("pending_hours", [])):
                iss.data_detail.setdefault("backfilled_at", _iso(now))
            iss.data_detail["pending_hours"] = pend
            if not pend:
                iss.status, iss.resolved_at = "resolved", now
                iss.history.append({"time": _iso(now), "event": "resolved", "detail": "late rows arrived (backfill)"})
                for k in iss.episodes:
                    if k in self.episodes:
                        self.episodes[k].open = False
                resolved.append(iss)
        return resolved

    # --- views ------------------------------------------------------------------------------
    def visible(self) -> list[Issue]:
        order = {"investigating": 0, "new": 1, "ongoing": 2, "resolved": 3}
        return sorted([i for i in self.issues.values() if i.status != "merged"],
                      key=lambda i: (order.get(i.status, 9), -i.priority, i.id))


def _overlaps(a: dict, b: dict) -> bool:
    """Two compatible segments share a fixed value, or one contains the other."""
    return any(a.get(d) == v for d, v in b.items()) or not a or not b


def _rank(iss: Issue) -> tuple:
    """Which of two merged issues to keep: a confirmed root cause, then the older one."""
    root = iss.root or {}
    return (root.get("status") == "confirmed", root.get("source") != "segment", -iss.opened_at.value)


def _contains(segment: dict, iss: Issue) -> bool:
    """``segment`` contains (or equals) the issue's segment: a broader impact metric."""
    target = iss.root["segment"] if iss.root else iss.segment
    return all(target.get(d) == v for d, v in segment.items())


def _common(rows: list[dict]) -> dict[str, str]:
    """Dimension values shared by all missing slices (e.g. country=DE, platform=ios)."""
    if not rows:
        return {}
    out = {}
    for d in DIMS:
        vals = {r.get(d) for r in rows}
        if len(vals) == 1 and None not in vals:
            out[d] = vals.pop()
    return out


def _event_id(h: dict) -> Optional[str]:
    for e in h.get("evidence", []) or []:
        if e.get("type") in ("event", "event_match") and e.get("event_id"):
            return e["event_id"]
    return None


def _iso(ts: Optional[pd.Timestamp]) -> Optional[str]:
    return None if ts is None else pd.Timestamp(ts).strftime("%Y-%m-%dT%H:%M:%SZ")
