"""L4 Triage: combine data quality, planned events, and anomaly bounds.

Window convention: ``[start, end)`` — ``start`` inclusive, ``end`` exclusive.
All timestamps are UTC timezone-aware.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Literal, Mapping, Protocol, runtime_checkable

import pandas as pd

from monitoring.event_calendar import EventCalendar, ExplanationResult
from monitoring.data_quality import DQResult

Action = Literal["report", "suppress_planned", "insufficient_data"]
DQCheckerFn = Callable[[pd.Timestamp, pd.Timestamp, dict[str, str]], DQResult]


@runtime_checkable
class AnomalyProtocol(Protocol):
    """Expected shape of an anomaly passed into ``triage``."""

    metric: str
    segment: dict[str, str]
    start: pd.Timestamp
    end: pd.Timestamp
    expected: float
    lower: float
    upper: float
    actual: float
    score: float


@dataclass(frozen=True)
class AdaptedAnomaly:
    """Normalized anomaly adapter supporting ``monitoring.detect.Episode`` and dicts."""

    metric: str
    segment: dict[str, str]
    start: pd.Timestamp
    end: pd.Timestamp
    expected: float
    lower: float
    upper: float
    actual: float
    score: float


@dataclass(frozen=True)
class TriageDecision:
    """Triage outcome with explicit reasons, DQ check, and event context."""

    action: Action
    reasons: list[str]
    dq: DQResult
    events: ExplanationResult


def _to_utc(ts: Any) -> pd.Timestamp:
    parsed = pd.Timestamp(ts)
    if pd.isna(parsed):
        raise ValueError(f"Invalid timestamp: {ts!r}")
    if parsed.tzinfo is None:
        return parsed.tz_localize("UTC")
    return parsed.tz_convert("UTC")


def adapt_anomaly(anomaly: Any) -> AdaptedAnomaly:
    """Adapt ``monitoring.detect.Episode``, finding dicts, or ``AnomalyProtocol``."""
    if isinstance(anomaly, Mapping):
        tw = anomaly.get("time_window", {})
        exp = anomaly.get("expected", {})
        chg = anomaly.get("change_size", {})
        start = _to_utc(anomaly.get("start", tw.get("start")))
        end = _to_utc(anomaly.get("end", tw.get("end")))
        exp_val = float(exp["value"] if isinstance(exp, Mapping) else exp)
        lower = float(exp["lower"] if isinstance(exp, Mapping) else anomaly["lower"])
        upper = float(exp["upper"] if isinstance(exp, Mapping) else anomaly["upper"])
        score = float(chg["score"] if isinstance(chg, Mapping) and "score" in chg else anomaly.get("score", 0.0))
        return AdaptedAnomaly(
            metric=str(anomaly["metric"]),
            segment=dict(anomaly.get("segment") or {}),
            start=start,
            end=end,
            expected=exp_val,
            lower=lower,
            upper=upper,
            actual=float(anomaly["actual"]),
            score=score,
        )

    score = float(getattr(anomaly, "score", getattr(anomaly, "peak_z", 0.0)))
    return AdaptedAnomaly(
        metric=str(anomaly.metric),
        segment=dict(anomaly.segment),
        start=_to_utc(anomaly.start),
        end=_to_utc(anomaly.end),
        expected=float(anomaly.expected),
        lower=float(anomaly.lower),
        upper=float(anomaly.upper),
        actual=float(anomaly.actual),
        score=score,
    )


def triage(
    anomaly: Any,
    calendar: EventCalendar,
    dq_checker: DQCheckerFn,
    default_multiplier: float = 1.5,
    volume_metric: Callable[[str], bool] | None = None,
) -> TriageDecision:
    """Decide whether to ``report``, ``suppress_planned``, or mark ``insufficient_data``.

    ``volume_metric(name)`` says whether a planned event's stated effect
    ("+25% sessions") applies to the metric. Pass e.g.
    ``lambda m: registry.get(m).is_additive``; by default it applies to all.
    """
    item = adapt_anomaly(anomaly)
    if item.start >= item.end:
        raise ValueError(f"Anomaly start ({item.start}) must be before end ({item.end})")

    dq = dq_checker(item.start, item.end, item.segment)
    events = calendar.explains(item.start, item.end, item.segment, metric=item.metric)

    # 1. Data quality first.
    if not dq.fresh or not dq.complete:
        reasons = [f"data quality failed: {dq.detail}"]
        if events.data_delay_events:
            reasons.append(
                "matching data_delay event(s): "
                + ", ".join(e.event_id for e in events.data_delay_events)
            )
        return TriageDecision("insufficient_data", reasons, dq, events)

    # 2. Planned events (campaign / holiday) that cover the whole segment.
    if events.planned_events:
        ev_names = ", ".join(f"{e.event_id} ({e.event_type})" for e in events.planned_events)
        factor = calendar.expected_effect_factor(item.start, item.end, item.segment, metric=item.metric)
        is_volume = volume_metric(item.metric) if volume_metric is not None else True
        if factor is not None and is_volume:
            # "+25% sessions": the expectation moves by the stated effect; the
            # range is then widened for uncertainty about the effect size
            expected, lower, upper = item.expected * factor, item.lower * factor, item.upper * factor
            mult = default_multiplier
            how = f"expected x{factor:.2f} from the stated effect, range widened x{mult:.2f}"
        else:
            expected, lower, upper = item.expected, item.lower, item.upper
            mult = (default_multiplier if factor is not None else calendar.sensitivity_multiplier(
                item.start, item.end, item.segment, metric=item.metric, default_multiplier=default_multiplier))
            how = f"range widened x{mult:.2f}"
        widened_lower = expected - mult * max(0.0, expected - lower)
        widened_upper = expected + mult * max(0.0, upper - expected)

        if widened_lower <= item.actual <= widened_upper:
            return TriageDecision(
                "suppress_planned",
                [
                    f"planned event(s) {ev_names} active; actual={item.actual:.2f} "
                    f"within adjusted range [{widened_lower:.2f}, {widened_upper:.2f}] ({how})"
                ],
                dq,
                events,
            )

        reasons = [
            f"planned event(s) {ev_names} active, but actual={item.actual:.2f} "
            f"is outside the adjusted range [{widened_lower:.2f}, {widened_upper:.2f}] ({how})"
        ]
    else:
        reasons = [
            f"anomaly on {item.metric} (actual={item.actual:.2f}, expected={item.expected:.2f}, score={item.score:.2f})"
        ]

    # Planned events covering only part of the segment are context, not an explanation.
    if events.partial_planned_events:
        part = ", ".join(f"{e.event_id} ({e.event_type}, {_scope(e)})" for e in events.partial_planned_events)
        reasons.append(f"planned event(s) {part} cover only part of this segment; drill down to separate them")

    # 3. Report, attaching any unplanned events (e.g. releases) as candidate causes.
    if events.unplanned_events:
        causes = ", ".join(f"{e.event_id}: {e.description or e.event_type}" for e in events.unplanned_events)
        reasons.append(f"candidate unplanned event(s): {causes}")

    return TriageDecision("report", reasons, dq, events)


def _scope(ev: Any) -> str:
    dims = [f"{d}={getattr(ev, d)}" for d in ("country", "platform", "category") if getattr(ev, d)]
    return ", ".join(dims) or "everywhere"
