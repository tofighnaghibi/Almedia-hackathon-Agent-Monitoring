"""Revision-aware e-process state (v3.2 §2.2, §5.4; C03; CP-015). Pure functions only.

The *betting rule* belongs to the sequential method (Phase 4, `savi_eprocess_v1`) and is passed in, either
as `increment(z) -> log-wealth step` (fixed bet) or as a whole-path function (predictable plug-in bet,
`wealth_path_series`). This module owns only *which* record counts for each bucket:

- one contribution per bucket: the highest revision of an *original* record;
- synthetic replicas contribute nothing (I11, G07);
- a revision replaces its bucket's contribution and the wealth path is recomputed from scratch,
  so it is never added twice and a stale value is never kept;
- an equal revision with different content is a conflict: quarantined, not guessed;
- alarm events are append-only. An alarm later undone by a revision is marked SUPERSEDED, never erased.
"""
from __future__ import annotations

import math
from typing import Callable


def consume(consumed: dict, record: dict) -> tuple[dict, str]:
    """Return (new consumed map, event). Never mutates the input."""
    if record["origin_kind"] != "original":
        return consumed, "IGNORED_SYNTHETIC"
    bucket = record["bucket"]
    cur = consumed.get(bucket)
    if cur is None:
        return {**consumed, bucket: _entry(record)}, "CONSUMED"
    if record["revision"] > cur["revision"]:
        return {**consumed, bucket: _entry(record)}, "REVISED"
    if record["revision"] == cur["revision"]:
        return consumed, "IDEMPOTENT" if record["z"] == cur["z"] else "CONFLICT_EQUAL_REVISION"
    return consumed, "IGNORED_OLDER_REVISION"


def _entry(record: dict) -> dict:
    return {"id": record["id"], "revision": record["revision"], "lineage_root": record["lineage_root"], "z": record["z"]}


def wealth_path(consumed: dict, increment: Callable[[float], float]) -> tuple[float, float]:
    """(final log wealth, peak log wealth) recomputed over buckets in order."""
    total, peak = 0.0, 0.0
    for bucket in sorted(consumed):
        total += increment(consumed[bucket]["z"])
        peak = max(peak, total)
    return total, peak


def wealth_path_series(consumed: dict, path: Callable[[list[float]], list[float]]) -> tuple[float, float]:
    """(final, peak) for a path-dependent predictable bet: `path` maps the ordered z sequence to log wealth.

    Recomputed from the consumed set on every call, so a revised early value also changes every later bet."""
    log_wealth = list(path([consumed[b]["z"] for b in sorted(consumed)]))
    return (log_wealth[-1] if log_wealth else 0.0), max([0.0, *log_wealth])


def alarm_events(previous: list, peak: float, alpha: float, seq: int) -> list:
    """Append RAISED when the threshold is first crossed; SUPERSEDED when a revision undoes an open alarm."""
    threshold = math.log(1.0 / alpha)
    events = list(previous)
    open_alarm = bool(events) and events[-1]["event"] == "RAISED"
    if peak >= threshold and not open_alarm:
        events.append({"event": "RAISED", "seq": seq, "peak_log_wealth": peak})
    elif peak < threshold and open_alarm:
        events.append({"event": "SUPERSEDED", "seq": seq, "peak_log_wealth": peak,
                       "note": "a later revision removed the evidence; the earlier alarm stays in history"})
    return events
