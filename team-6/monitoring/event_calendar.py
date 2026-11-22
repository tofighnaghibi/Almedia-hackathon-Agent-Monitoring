"""L4 Planned-events calendar for distinguishing expected changes from incidents.

Window convention: ``[start, end)`` — ``start`` inclusive, ``end`` exclusive.
All timestamps are UTC timezone-aware.

Matching rules:

- Scope: an event *fully* covers a segment when every dimension the event
  names is fixed to the same value in the segment (a UK promo covers ``UK``
  and ``UK/android``). If the segment is broader (all countries), the event
  covers only *part* of it: it is kept as context but can't explain the
  whole change.
- Timing: events with a duration match when they overlap the window. A
  point-in-time event (a release) also matches when it happened up to
  ``release_lookback_hours`` before the window starts, because a cause comes
  before its effect.
- Effect: a planned event's ``expected_effect`` ("+25% sessions") shifts the
  expected value by that percentage (``expected_effect_factor``).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Mapping

import pandas as pd

_REQUIRED_COLUMNS = ("event_id", "start_time", "event_type", "planned")
_EFFECT_PCT_RE = re.compile(r"([+-]?\d+(?:\.\d+)?)\s*%")
_DIMS = ("country", "platform", "category")
DEFAULT_RELEASE_LOOKBACK_HOURS = 6.0


def _to_utc(ts: Any) -> pd.Timestamp:
    try:
        parsed = pd.Timestamp(ts)
    except Exception as exc:
        raise ValueError(f"Invalid timestamp: {ts!r}") from exc
    if pd.isna(parsed):
        raise ValueError(f"Timestamp cannot be NaT/None: {ts!r}")
    if parsed.tzinfo is None:
        return parsed.tz_localize("UTC")
    return parsed.tz_convert("UTC")


def _clean_opt_str(val: Any) -> str | None:
    if val is None or pd.isna(val):
        return None
    s = str(val).strip()
    if not s or s == "*" or s.lower() == "nan":
        return None
    return s


def _matches_dim(event_val: str | None, seg_val: str | None) -> bool:
    """True unless the event and the segment name different values."""
    if event_val is None or seg_val is None:
        return True
    return event_val == seg_val


def _covers_dim(event_val: str | None, seg_val: str | None) -> bool:
    """True when the event applies to the whole segment along this dimension."""
    return event_val is None or event_val == seg_val


@dataclass(frozen=True)
class CalendarEvent:
    """Normalized event record from the ``events`` table."""

    event_id: str
    start_time: pd.Timestamp
    end_time: pd.Timestamp | None
    event_type: str
    planned: bool
    country: str | None = None
    platform: str | None = None
    category: str | None = None
    description: str = ""
    expected_effect: str | None = None

    def dim(self, name: str) -> str | None:
        return getattr(self, name)

    @property
    def effect_pct(self) -> float | None:
        """Signed percentage from ``expected_effect`` ("+25% ..." -> 25.0), if any."""
        if not self.expected_effect:
            return None
        match = _EFFECT_PCT_RE.search(self.expected_effect)
        return float(match.group(1)) if match else None


@dataclass(frozen=True)
class ExplanationResult:
    """Categorized events overlapping a window and segment."""

    planned_events: list[CalendarEvent]
    unplanned_events: list[CalendarEvent]
    data_delay_events: list[CalendarEvent]
    # planned events that cover only part of the segment (context, never an explanation)
    partial_planned_events: list[CalendarEvent] = field(default_factory=list)


class EventCalendar:
    """In-memory queryable calendar of marketplace events."""

    def __init__(self, events: list[CalendarEvent],
                 release_lookback_hours: float = DEFAULT_RELEASE_LOOKBACK_HOURS) -> None:
        self._events = list(events)
        self.release_lookback = pd.Timedelta(hours=release_lookback_hours)

    @classmethod
    def from_dataframe(cls, df: pd.DataFrame,
                       release_lookback_hours: float = DEFAULT_RELEASE_LOOKBACK_HOURS) -> EventCalendar:
        """Load events from an ``events`` DataFrame without mutating it."""
        missing = [c for c in _REQUIRED_COLUMNS if c not in df.columns]
        if missing:
            raise ValueError(f"events DataFrame missing required columns: {missing}")

        if df.empty:
            return cls([], release_lookback_hours)

        records: list[CalendarEvent] = []
        for row in df.to_dict(orient="records"):
            start_ts = _to_utc(row["start_time"])
            raw_end = row.get("end_time")
            end_ts = None if (raw_end is None or pd.isna(raw_end) or str(raw_end).strip() == "") else _to_utc(raw_end)
            if end_ts is not None and end_ts < start_ts:
                raise ValueError(f"Event {row['event_id']!r} has end_time < start_time")
            records.append(
                CalendarEvent(
                    event_id=str(row["event_id"]),
                    start_time=start_ts,
                    end_time=end_ts,
                    event_type=str(row["event_type"]).strip(),
                    planned=bool(row["planned"]),
                    country=_clean_opt_str(row.get("country")),
                    platform=_clean_opt_str(row.get("platform")),
                    category=_clean_opt_str(row.get("category")),
                    description="" if pd.isna(row.get("description")) else str(row.get("description", "")),
                    expected_effect=_clean_opt_str(row.get("expected_effect")),
                )
            )
        return cls(records, release_lookback_hours)

    # --- matching ------------------------------------------------------------
    def _overlaps(self, ev: CalendarEvent, start_ts: pd.Timestamp, end_ts: pd.Timestamp) -> bool:
        if ev.end_time is None or ev.end_time == ev.start_time:
            return start_ts - self.release_lookback <= ev.start_time < end_ts
        return ev.start_time < end_ts and ev.end_time > start_ts

    def _match(self, start: Any, end: Any, segment: Mapping[str, str] | None):
        """(event, covers_whole_segment) for every event that overlaps and doesn't conflict."""
        start_ts, end_ts = _to_utc(start), _to_utc(end)
        if start_ts >= end_ts:
            raise ValueError(f"start ({start_ts}) must be strictly before end ({end_ts})")
        seg = {d: _clean_opt_str((segment or {}).get(d)) for d in _DIMS}
        out = []
        for ev in self._events:
            if not self._overlaps(ev, start_ts, end_ts):
                continue
            if not all(_matches_dim(ev.dim(d), seg[d]) for d in _DIMS):
                continue
            out.append((ev, all(_covers_dim(ev.dim(d), seg[d]) for d in _DIMS)))
        return out

    def events_for(
        self,
        start: Any,
        end: Any,
        segment: Mapping[str, str] | None = None,
    ) -> list[CalendarEvent]:
        """Events overlapping ``[start, end)`` that don't conflict with ``segment``
        (including those covering only part of it, and releases shortly before)."""
        return [ev for ev, _ in self._match(start, end, segment)]

    def explains(
        self,
        start: Any,
        end: Any,
        segment: Mapping[str, str] | None = None,
        metric: str | None = None,
    ) -> ExplanationResult:
        """Split matching events into planned innocent, unplanned, and data-delay.

        Only planned events that cover the whole segment count as
        ``planned_events``; the others are ``partial_planned_events``.
        """
        del metric  # reserved for future metric-specific event filtering
        planned_events: list[CalendarEvent] = []
        partial_planned: list[CalendarEvent] = []
        unplanned_events: list[CalendarEvent] = []
        data_delay_events: list[CalendarEvent] = []

        for ev, covers in self._match(start, end, segment):
            if ev.event_type == "data_delay":
                data_delay_events.append(ev)
            elif ev.planned and ev.event_type in {"campaign", "holiday"}:
                (planned_events if covers else partial_planned).append(ev)
            else:
                # Releases (even if planned=True) are never innocent explanations.
                unplanned_events.append(ev)

        return ExplanationResult(
            planned_events=planned_events,
            unplanned_events=unplanned_events,
            data_delay_events=data_delay_events,
            partial_planned_events=partial_planned,
        )

    # --- planned effects -------------------------------------------------------
    def expected_effect_factor(
        self,
        start: Any,
        end: Any,
        segment: Mapping[str, str] | None = None,
        metric: str | None = None,
    ) -> float | None:
        """Multiplier the stated effects of planned events imply ("+25%" -> 1.25,
        two events multiply), or None if no covering planned event states one."""
        factor, found = 1.0, False
        for ev in self.explains(start, end, segment, metric=metric).planned_events:
            pct = ev.effect_pct
            if pct is not None:
                factor *= 1.0 + pct / 100.0
                found = True
        return factor if found else None

    def sensitivity_multiplier(
        self,
        start: Any,
        end: Any,
        segment: Mapping[str, str] | None = None,
        metric: str | None = None,
        default_multiplier: float = 1.5,
    ) -> float:
        """Return >1.0 when a planned campaign/holiday is active, else 1.0."""
        if default_multiplier < 1.0:
            raise ValueError(f"default_multiplier must be >= 1.0, got {default_multiplier}")

        exp = self.explains(start, end, segment, metric=metric)
        if not exp.planned_events:
            return 1.0

        mult = default_multiplier
        for ev in exp.planned_events:
            pct = ev.effect_pct
            if pct is not None and abs(pct) > 0.0:
                mult = max(1.0 + abs(pct) / 100.0, 1.05)
        return float(mult)
