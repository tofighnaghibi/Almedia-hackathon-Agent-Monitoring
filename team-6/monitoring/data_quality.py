"""L4 Data Quality checks for marketplace_hourly tables.

Window convention: ``[start, end)`` — ``start`` inclusive, ``end`` exclusive.
All timestamps are UTC timezone-aware.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

import pandas as pd


@dataclass(frozen=True)
class DQResult:
    """Result of a data freshness and completeness check."""

    fresh: bool
    complete: bool
    completeness: float
    missing: list[dict[str, Any]]
    latest_hour: pd.Timestamp | None
    detail: str


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


def _normalize_expected_slices(
    expected_slices: Mapping[str, Sequence[str]] | Sequence[Mapping[str, str]],
    segment: Mapping[str, str] | None,
) -> tuple[list[str], list[tuple[str, ...]]]:
    if isinstance(expected_slices, Mapping):
        dims = list(expected_slices.keys())
        if not dims:
            raise ValueError("expected_slices cannot be empty")
        per_dim: list[list[str]] = []
        for dim in dims:
            vals = list(expected_slices[dim])
            if segment and dim in segment:
                vals = [v for v in vals if v == segment[dim]]
            if not vals:
                raise ValueError(f"No expected values for dimension {dim!r} under segment {segment}")
            per_dim.append(vals)
        combos: list[tuple[str, ...]] = [()]
        for vals in per_dim:
            combos = [c + (v,) for c in combos for v in vals]
        return dims, combos

    rows = list(expected_slices)
    if not rows:
        raise ValueError("expected_slices cannot be empty")
    dims = list(rows[0].keys())
    if segment:
        rows = [r for r in rows if all(r.get(k) == v for k, v in segment.items())]
    if not rows:
        raise ValueError(f"No expected slices match segment {segment}")
    return dims, [tuple(str(r[d]) for d in dims) for r in rows]


def check_dq(
    table_df: pd.DataFrame,
    start: Any,
    end: Any,
    now: Any,
    expected_slices: Mapping[str, Sequence[str]] | Sequence[Mapping[str, str]],
    freshness_minutes: float = 90.0,
    completeness_min: float = 0.95,
    segment: Mapping[str, str] | None = None,
) -> DQResult:
    """Check freshness and slice completeness over ``[start, end)`` in UTC.

    Args:
        table_df: Marketplace hourly DataFrame (not mutated).
        start: Window start (inclusive, UTC).
        end: Window end (exclusive, UTC).
        now: Current simulation clock (UTC). Never calls ``datetime.now()``.
        expected_slices: Mapping of ``dim -> allowed values`` or sequence of slice dicts.
        freshness_minutes: Maximum allowed age in minutes relative to ``now``.
        completeness_min: Minimum fraction of expected rows required (in ``[0, 1]``).
        segment: Optional dimension filter (e.g. ``{"country": "DE"}``).
    """
    start_ts = _to_utc(start)
    end_ts = _to_utc(end)
    now_ts = _to_utc(now)

    if start_ts >= end_ts:
        raise ValueError(f"start ({start_ts}) must be strictly before end ({end_ts})")
    if freshness_minutes < 0:
        raise ValueError(f"freshness_minutes must be non-negative, got {freshness_minutes}")
    if not (0.0 <= completeness_min <= 1.0):
        raise ValueError(f"completeness_min must be in [0, 1], got {completeness_min}")
    if "timestamp" not in table_df.columns:
        raise ValueError("table_df must contain a 'timestamp' column")

    dims, slice_combos = _normalize_expected_slices(expected_slices, segment)
    for dim in dims:
        if dim not in table_df.columns:
            raise ValueError(f"table_df is missing expected slice column {dim!r}")

    expected_hours = pd.date_range(start_ts, end_ts, freq="1h", inclusive="left", tz="UTC")
    if len(expected_hours) == 0:
        raise ValueError(f"Window [{start_ts}, {end_ts}) contains no hourly buckets")

    df = table_df.copy()
    ts_col = pd.to_datetime(df["timestamp"], utc=True)
    df["__ts__"] = ts_col
    mask = (ts_col >= start_ts) & (ts_col < end_ts)
    if segment:
        for k, v in segment.items():
            if k not in df.columns:
                raise ValueError(f"Unknown segment dimension {k!r}")
            mask &= df[k] == v

    sub = df.loc[mask]
    expected_total = len(expected_hours) * len(slice_combos)

    if sub.empty:
        missing_all = [
            {"timestamp": h.strftime("%Y-%m-%dT%H:%M:%SZ"), **dict(zip(dims, combo))}
            for h in expected_hours
            for combo in slice_combos
        ]
        omitted = max(0, len(missing_all) - 50)
        suffix = f" ({omitted} more omitted)" if omitted else ""
        return DQResult(
            fresh=False,
            complete=False,
            completeness=0.0,
            missing=missing_all[:50],
            latest_hour=None,
            detail=f"no rows in window [{start_ts.isoformat()}, {end_ts.isoformat()}){suffix}",
        )

    present_keys = set(
        zip(sub["__ts__"], *(sub[d].astype(str) for d in dims))
    )
    missing: list[dict[str, Any]] = []
    present_expected = 0
    for h in expected_hours:
        for combo in slice_combos:
            if (h, *combo) in present_keys:
                present_expected += 1
            else:
                missing.append(
                    {"timestamp": h.strftime("%Y-%m-%dT%H:%M:%SZ"), **dict(zip(dims, combo))}
                )

    completeness = present_expected / expected_total
    complete = completeness >= completeness_min

    latest_hour: pd.Timestamp = sub["__ts__"].max()
    details: list[str] = []

    ingested_col = next(
        (c for c in ("ingested_at", "ingested_at_utc") if c in sub.columns), None
    )
    if ingested_col is not None and sub[ingested_col].notna().any():
        latest_ingested = pd.to_datetime(sub[ingested_col], utc=True).max()
        age_min = (now_ts - latest_ingested).total_seconds() / 60.0
        fresh = 0.0 <= age_min <= freshness_minutes
        details.append(f"ingested_at age={age_min:.1f}m (limit {freshness_minutes}m)")
    else:
        age_min = (now_ts - latest_hour).total_seconds() / 60.0
        fresh = 0.0 <= age_min <= freshness_minutes
        details.append(
            f"no ingested_at column; using latest data hour age={age_min:.1f}m (limit {freshness_minutes}m)"
        )

    details.append(f"completeness={completeness:.2%} ({present_expected}/{expected_total})")
    if len(missing) > 50:
        details.append(f"{len(missing)} missing rows ({len(missing) - 50} more omitted)")
    elif missing:
        details.append(f"{len(missing)} missing rows")

    return DQResult(
        fresh=bool(fresh),
        complete=bool(complete),
        completeness=float(completeness),
        missing=missing[:50],
        latest_hour=latest_hour,
        detail="; ".join(details),
    )
