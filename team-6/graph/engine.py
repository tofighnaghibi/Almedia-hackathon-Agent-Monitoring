"""Metric calculation engine (L2, Person A).

Computes any registered metric for any time window, segment, breakdown and
time grain, using DuckDB over the simulator tables.

    engine = MetricEngine.from_dir("data/generated/scenarios")
    engine.value("orders", "2026-10-22", "2026-10-23", {"country": "DE", "platform": "android"})
    engine.compute("payment_success_rate", start, end, by=["platform"], grain="hour")

Conventions
- Time windows are half-open: ``start <= timestamp < end`` (UTC).
- Ratios are computed from the summed parts of the group, never averaged.
- With a ``grain``, every period in the window appears, for every group seen
  in the table. A period with no data has ``value = NaN`` and
  ``n_rows = 0``, so missing data shows up instead of silently vanishing.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Mapping, Optional, Sequence

import duckdb
import numpy as np
import pandas as pd

from .registry import MetricDefinition, MetricRegistry

GRAINS = {"hour": "1h", "day": "1D", "week": "7D"}
_TIME_COLUMN = "timestamp"

Segment = Mapping[str, str | Sequence[str]]
TimeLike = str | pd.Timestamp | None


@dataclass
class ValidationReport:
    metric: str
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    value: float | None = None  # metric over the whole table
    columns_used: list[str] = field(default_factory=list)


class MetricEngine:
    def __init__(self, registry: Optional[MetricRegistry] = None, tables: Optional[Mapping[str, pd.DataFrame]] = None):
        self.registry = registry if registry is not None else MetricRegistry.default()
        self._con = duckdb.connect(":memory:")
        self._con.execute("SET TimeZone = 'UTC'")
        self._columns: dict[str, list[str]] = {}
        self._group_cache: dict[tuple[str, tuple[str, ...]], pd.DataFrame] = {}
        for name, df in (tables or {}).items():
            self.register_table(name, df)

    # --- data ------------------------------------------------------------
    @classmethod
    def from_dir(cls, path: str | Path, registry: Optional[MetricRegistry] = None) -> "MetricEngine":
        """Load ``marketplace_hourly.csv`` from a simulator output folder."""
        return cls(registry, {"marketplace_hourly": read_table_csv(Path(path) / "marketplace_hourly.csv")})

    def register_table(self, name: str, df: pd.DataFrame) -> None:
        """Make a DataFrame queryable. It needs a ``timestamp`` column (UTC)."""
        if _TIME_COLUMN not in df.columns:
            raise ValueError(f"table {name!r} needs a {_TIME_COLUMN!r} column")
        data = df.copy()
        ts = pd.to_datetime(data[_TIME_COLUMN], utc=True)
        data[_TIME_COLUMN] = ts.dt.tz_localize(None)  # stored as naive UTC
        self._con.register(f"__df_{name}", data)
        self._con.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM "__df_{name}"')
        self._con.unregister(f"__df_{name}")
        self._columns[name] = list(data.columns)
        self._group_cache = {k: v for k, v in self._group_cache.items() if k[0] != name}

    def append_rows(self, name: str, df: pd.DataFrame) -> None:
        """Add rows to a registered table (live data); columns must match it."""
        if name not in self._columns:
            self.register_table(name, df)
            return
        if df.empty:
            return
        data = df.copy()
        data[_TIME_COLUMN] = pd.to_datetime(data[_TIME_COLUMN], utc=True).dt.tz_localize(None)
        missing = [c for c in self._columns[name] if c not in data.columns]
        if missing:
            raise ValueError(f"rows for {name!r} are missing columns {missing}")
        data = data[self._columns[name]]
        self._con.register("__append", data)
        cols = ", ".join(f'"{c}"' for c in self._columns[name])
        self._con.execute(f'INSERT INTO "{name}" ({cols}) SELECT {cols} FROM "__append"')
        self._con.unregister("__append")
        self._group_cache = {k: v for k, v in self._group_cache.items() if k[0] != name}

    def tables(self) -> list[str]:
        return list(self._columns)

    def table_columns(self, name: str) -> list[str]:
        if name not in self._columns:
            raise KeyError(f"unknown table {name!r}; registered: {self.tables()}")
        return list(self._columns[name])

    def time_range(self, table: str = "marketplace_hourly") -> tuple[pd.Timestamp, pd.Timestamp]:
        """(first timestamp, last timestamp) present in the table, UTC."""
        lo, hi = self._con.execute(f'SELECT MIN("{_TIME_COLUMN}"), MAX("{_TIME_COLUMN}") FROM "{table}"').fetchall()[0]
        return _utc(lo), _utc(hi)

    def dimension_values(self, dimension: str, table: str = "marketplace_hourly") -> list[str]:
        self._check_columns(table, [dimension])
        rows = self._con.execute(f'SELECT DISTINCT "{dimension}" FROM "{table}" ORDER BY 1').fetchall()
        return [r[0] for r in rows]

    # --- computing -------------------------------------------------------
    def compute(
        self,
        metric: str | MetricDefinition,
        start: TimeLike = None,
        end: TimeLike = None,
        segment: Optional[Segment] = None,
        by: Optional[Sequence[str]] = None,
        grain: Optional[str] = None,
    ) -> pd.DataFrame:
        """Metric values as a table with columns ``[*by, period?, value, n_rows]``."""
        defn = self._defn(metric)
        out = self.compute_many([defn], start, end, segment, by, grain)
        return out.rename(columns={defn.name: "value"})

    def compute_many(
        self,
        metrics: Iterable[str | MetricDefinition],
        start: TimeLike = None,
        end: TimeLike = None,
        segment: Optional[Segment] = None,
        by: Optional[Sequence[str]] = None,
        grain: Optional[str] = None,
    ) -> pd.DataFrame:
        """Several metrics side by side (one column each), same grouping.

        All metrics must share a source table and allow the requested dimensions.
        """
        defns = [self._defn(m) for m in metrics]
        if not defns:
            raise ValueError("no metrics given")
        if len({d.name for d in defns}) != len(defns):
            raise ValueError("duplicate metrics")
        table = defns[0].source_table
        if any(d.source_table != table for d in defns):
            raise ValueError("compute_many needs metrics from one source table")
        by = list(by or [])
        segment = dict(segment or {})
        for d in defns:
            self._check_dims(d, list(segment) + by)
        self._check_columns(table, list(segment) + by)
        if grain is not None and grain not in GRAINS:
            raise ValueError(f"grain must be one of {list(GRAINS)}")
        start_ts, end_ts = _parse_time(start), _parse_time(end)
        if start_ts is not None and end_ts is not None and end_ts <= start_ts:
            raise ValueError("end must be after start")

        where, params = self._where(start_ts, end_ts, segment)
        group_cols = [f'"{c}"' for c in by]
        if grain is not None:
            group_cols.append(f"date_trunc('{_TRUNC[grain]}', \"{_TIME_COLUMN}\") AS period")
        select = group_cols + [f'({d.calculation}) AS "{d.name}"' for d in defns] + ["COUNT(*) AS n_rows"]
        sql = f'SELECT {", ".join(select)} FROM "{table}"{where}'
        n_group = len(by) + (grain is not None)
        if n_group:
            sql += " GROUP BY " + ", ".join(str(i) for i in range(1, n_group + 1))
        try:
            df = self._con.execute(sql, params).df()
        except duckdb.Error as exc:
            raise ValueError(f"query failed for {[d.name for d in defns]}: {exc}") from None

        metric_cols = [d.name for d in defns]
        for c in metric_cols:
            df[c] = pd.to_numeric(df[c], errors="coerce").astype(float)
        df["n_rows"] = df["n_rows"].astype(np.int64)
        if grain is not None:
            df["period"] = pd.to_datetime(df["period"]).dt.tz_localize("UTC")
            df = self._fill_periods(df, table, by, grain, start_ts, end_ts, segment, metric_cols)
        elif not by and df["n_rows"].iloc[0] == 0:
            df[metric_cols] = np.nan
        sort = by + (["period"] if grain is not None else [])
        if sort:
            df = df.sort_values(sort, kind="stable").reset_index(drop=True)
        return df[by + (["period"] if grain is not None else []) + metric_cols + ["n_rows"]]

    def value(
        self,
        metric: str | MetricDefinition,
        start: TimeLike = None,
        end: TimeLike = None,
        segment: Optional[Segment] = None,
    ) -> float:
        """One number: the metric over the window and segment (NaN if no data)."""
        return float(self.compute(metric, start, end, segment)["value"].iloc[0])

    # --- validation (used by L3 when a new metric arrives) ---------------
    def validate(self, defn: MetricDefinition | dict[str, Any]) -> ValidationReport:
        """Check a definition against the data: table, dimensions, calculation."""
        if isinstance(defn, dict):
            defn = MetricDefinition.from_dict(defn)
        rep = ValidationReport(metric=defn.name, ok=False)
        if defn.source_table not in self._columns:
            rep.errors.append(f"source table {defn.source_table!r} is not loaded")
            return rep
        cols = self._columns[defn.source_table]
        bad_dims = [d for d in defn.dimensions if d not in cols]
        if bad_dims:
            rep.errors.append(f"dimensions {bad_dims} are not columns of {defn.source_table}")
        rep.columns_used = defn.columns_used(cols)
        if not rep.columns_used and "count(*)" not in defn.calculation.lower():
            rep.errors.append("calculation does not reference any column of the table")
        if rep.errors:
            return rep

        try:
            total = self.compute(defn)
        except ValueError as exc:
            rep.errors.append(_short(exc))
            return rep
        try:
            by_dims = self.compute(defn, by=list(defn.dimensions), grain="day") if defn.dimensions else total
        except ValueError as exc:
            rep.errors.append(f"cannot be broken down by its dimensions: {_short(exc)}")
            return rep

        value = total["value"].iloc[0]
        rep.value = None if math.isnan(value) else float(value)
        vals = by_dims["value"].dropna()
        if rep.value is None:
            rep.warnings.append("value is empty (NULL) over the whole table")
        if defn.unit == "ratio" and len(vals) and ((vals < 0) | (vals > 1)).any():
            rep.warnings.append(f"unit is 'ratio' but values range {vals.min():.3g}..{vals.max():.3g} (outside 0..1)")
        if defn.unit in ("count", "currency_eur") and len(vals) and (vals < 0).any():
            rep.warnings.append("negative values for a count / currency metric")
        if len(vals) and vals.nunique() == 1:
            rep.warnings.append("metric is constant across all segments and days")
        rep.ok = True
        return rep

    # --- helpers ---------------------------------------------------------
    def _defn(self, metric: str | MetricDefinition) -> MetricDefinition:
        return metric if isinstance(metric, MetricDefinition) else self.registry.get(metric)

    def _check_dims(self, defn: MetricDefinition, dims: Iterable[str]) -> None:
        bad = [d for d in dims if d not in defn.dimensions]
        if bad:
            raise ValueError(f"metric {defn.name!r} cannot be sliced by {bad}; allowed: {list(defn.dimensions)}")

    def _check_columns(self, table: str, cols: Iterable[str]) -> None:
        known = self.table_columns(table)
        bad = [c for c in cols if c not in known]
        if bad:
            raise ValueError(f"table {table!r} has no columns {bad}")

    @staticmethod
    def _where(start: Optional[pd.Timestamp], end: Optional[pd.Timestamp], segment: dict) -> tuple[str, list]:
        clauses, params = [], []
        if start is not None:
            clauses.append(f'"{_TIME_COLUMN}" >= ?')
            params.append(start.tz_convert("UTC").tz_localize(None).to_pydatetime())
        if end is not None:
            clauses.append(f'"{_TIME_COLUMN}" < ?')
            params.append(end.tz_convert("UTC").tz_localize(None).to_pydatetime())
        for dim, val in segment.items():
            if isinstance(val, (list, tuple, set)):
                vals = list(val)
                if not vals:
                    raise ValueError(f"empty value list for {dim!r}")
                clauses.append(f'"{dim}" IN ({", ".join("?" for _ in vals)})')
                params.extend(vals)
            else:
                clauses.append(f'"{dim}" = ?')
                params.append(val)
        return (" WHERE " + " AND ".join(clauses) if clauses else ""), params

    def _groups(self, table: str, by: list[str], segment: dict) -> pd.DataFrame:
        """Every combination of the `by` dimensions seen in the table (any time)."""
        key = (table, tuple(by))
        if key not in self._group_cache:
            cols = ", ".join(f'"{c}"' for c in by)
            self._group_cache[key] = self._con.execute(f'SELECT DISTINCT {cols} FROM "{table}"').df()
        groups = self._group_cache[key]
        for dim, val in segment.items():
            if dim in by:
                allowed = list(val) if isinstance(val, (list, tuple, set)) else [val]
                groups = groups[groups[dim].isin(allowed)]
        return groups

    def _fill_periods(self, df, table, by, grain, start, end, segment, metric_cols) -> pd.DataFrame:
        lo, hi = self.time_range(table)
        first = (start if start is not None else lo).floor(GRAINS[grain]) if grain != "week" else _week(start or lo)
        last_excl = end if end is not None else hi + pd.Timedelta(microseconds=1)
        periods = pd.date_range(first, last_excl, freq=GRAINS[grain], inclusive="left", tz="UTC")
        if not len(periods):
            return df
        full = pd.DataFrame({"period": periods})
        if by:
            full = self._groups(table, by, segment).merge(full, how="cross")
        out = full.merge(df, on=by + ["period"], how="left")
        out["n_rows"] = out["n_rows"].fillna(0).astype(np.int64)
        out.loc[out["n_rows"] == 0, metric_cols] = np.nan
        return out


_TRUNC = {"hour": "hour", "day": "day", "week": "week"}


def read_table_csv(path: str | Path) -> pd.DataFrame:
    """Read a table written by the simulator; parses ``timestamp`` as UTC."""
    df = pd.read_csv(path)
    df[_TIME_COLUMN] = pd.to_datetime(df[_TIME_COLUMN], utc=True)
    return df


def _week(ts: pd.Timestamp) -> pd.Timestamp:
    """Monday 00:00 of the week (matches DuckDB date_trunc('week'))."""
    ts = ts.tz_convert("UTC")
    return (ts - pd.Timedelta(days=ts.dayofweek)).normalize()


def _parse_time(t: TimeLike) -> Optional[pd.Timestamp]:
    if t is None:
        return None
    ts = pd.Timestamp(t)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _utc(v: Any) -> pd.Timestamp:
    ts = pd.Timestamp(v)
    return ts.tz_localize("UTC") if ts.tzinfo is None else ts.tz_convert("UTC")


def _short(exc: Exception) -> str:
    return str(exc).splitlines()[0][:300]
