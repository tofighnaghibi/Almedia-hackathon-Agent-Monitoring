"""Metric definitions as they arrive (YAML), validated into MetricSpec objects.

Contract (README §3.1) plus a few optional fields the graph uses:

    name, description, unit, dimensions
    column         -- a raw column of facts_hourly (additive counter or money)
    sql            -- a query returning ts, dimensions..., value (contributed metrics)
    formula        -- how the metric is computed from other metrics
    decomposition  -- an identity used for investigation (e.g. gmv = orders * aov);
                      defaults to the formula unless the metric is atomic
    atomic         -- true: do not decompose this metric along its formula
    outcome        -- true: a top business outcome, the root of a tree (declared, never guessed)
    additive       -- sums over segments equal the total (inferred when omitted)
    bounds         -- [min, max] sanity range, either side may be null
    systems        -- release systems that can affect it (payments, search, ...)
    source         -- the data source whose freshness it depends on
    may_affect     -- possible effects: [{metric, prior, sign, note}]; sign +1 / -1 says which way
                      the effect moves when this metric rises, 0 = unknown
    direction      -- up (higher is better), down, or none
    tolerance      -- relative tolerance for identity checks
    domain         -- the business domain the console files the metric under (optional; contracts README, item 7)
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from brain.formula import Formula, FormulaError, parse

NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
DIMENSIONS = ("country", "platform", "category", "channel")
KNOWN_FIELDS = {
    "name", "description", "unit", "dimensions", "column", "sql", "formula", "decomposition",
    "atomic", "additive", "bounds", "systems", "source", "may_affect", "direction", "tolerance",
    "outcome", "domain",
}


class SpecError(ValueError):
    pass


@dataclass(frozen=True)
class CauseLink:
    metric: str
    prior: float = 0.5
    note: str = ""
    sign: int = 0


@dataclass(frozen=True)
class MetricSpec:
    name: str
    description: str = ""
    unit: str = ""
    dimensions: tuple[str, ...] = DIMENSIONS
    column: str | None = None
    sql: str | None = None
    formula: Formula | None = None
    decomposition: Formula | None = None
    atomic: bool = False
    additive: bool | None = None
    bounds: tuple[float | None, float | None] | None = None
    systems: tuple[str, ...] = ()
    source: str | None = None
    may_affect: tuple[CauseLink, ...] = ()
    direction: str = "none"
    tolerance: float = 1e-6
    origin: str = "core"
    outcome: bool = False
    domain: str | None = None
    raw: dict = field(default_factory=dict, compare=False, repr=False)

    @property
    def investigative_formula(self) -> Formula | None:
        """The identity the investigator decomposes along: the decomposition if given,
        else the formula unless the metric is atomic."""
        if self.decomposition is not None:
            return self.decomposition
        if self.formula is not None and not self.atomic:
            return self.formula
        return None


def _formula(raw: dict, key: str, name: str) -> Formula | None:
    if raw.get(key) in (None, ""):
        return None
    try:
        return parse(str(raw[key]))
    except FormulaError as e:
        raise SpecError(f"{name}: bad {key}: {e}") from None


def spec_from_dict(raw: dict, origin: str = "core") -> MetricSpec:
    if not isinstance(raw, dict):
        raise SpecError(f"a metric must be a mapping, got {type(raw).__name__}")
    name = raw.get("name")
    if not isinstance(name, str) or not NAME_RE.match(name):
        raise SpecError(f"bad metric name {name!r}: use lower_snake_case")
    unknown = set(raw) - KNOWN_FIELDS
    if unknown:
        raise SpecError(f"{name}: unknown fields {sorted(unknown)}")
    column = raw.get("column")
    sql = raw.get("sql")
    formula = _formula(raw, "formula", name)
    decomposition = _formula(raw, "decomposition", name)
    if not (column or sql or formula):
        raise SpecError(f"{name}: needs one of column, sql or formula")
    if column and not NAME_RE.match(str(column)):
        raise SpecError(f"{name}: bad column {column!r}")
    if formula is not None and name in formula.names:
        raise SpecError(f"{name}: formula refers to itself")
    if decomposition is not None and name in decomposition.names:
        raise SpecError(f"{name}: decomposition refers to itself")

    dims = raw.get("dimensions", list(DIMENSIONS))
    if isinstance(dims, str):
        dims = [dims]
    dims = tuple(dims)
    bad_dims = [d for d in dims if d not in DIMENSIONS]
    if bad_dims:
        raise SpecError(f"{name}: unknown dimensions {bad_dims}; known: {list(DIMENSIONS)}")

    bounds = raw.get("bounds")
    if bounds is not None:
        if not (isinstance(bounds, (list, tuple)) and len(bounds) == 2):
            raise SpecError(f"{name}: bounds must be [min, max]")
        bounds = tuple(None if b is None else float(b) for b in bounds)

    links = []
    for item in raw.get("may_affect") or []:
        if isinstance(item, str):
            item = {"metric": item}
        if not isinstance(item, dict) or "metric" not in item:
            raise SpecError(f"{name}: may_affect entries need a metric")
        prior = float(item.get("prior", 0.5))
        if not 0.0 <= prior <= 1.0:
            raise SpecError(f"{name}: may_affect prior must be within [0, 1]")
        sign = item.get("sign", 0)
        if isinstance(sign, bool) or sign not in (-1, 0, 1):
            raise SpecError(f"{name}: may_affect sign must be -1, 0 or 1, got {sign!r}")
        links.append(CauseLink(metric=str(item["metric"]), prior=prior, note=str(item.get("note", "")),
                               sign=int(sign)))

    for flag in ("atomic", "additive", "outcome"):
        if flag in raw and raw[flag] is not None and not isinstance(raw[flag], bool):
            raise SpecError(f"{name}: {flag} must be true or false, got {raw[flag]!r}")

    direction = raw.get("direction", "none")
    if direction not in ("up", "down", "none"):
        raise SpecError(f"{name}: direction must be up, down or none")

    systems = raw.get("systems") or ()
    if isinstance(systems, str):
        systems = (systems,)

    domain = raw.get("domain")
    if domain is not None and (not isinstance(domain, str) or not domain.strip()):
        raise SpecError(f"{name}: domain must be a short text (the console's domain id), got {domain!r}")

    return MetricSpec(
        name=name,
        description=str(raw.get("description", "")).strip(),
        unit=str(raw.get("unit", "")),
        dimensions=dims,
        column=column,
        sql=sql,
        formula=formula,
        decomposition=decomposition,
        atomic=raw.get("atomic") is True,
        additive=raw.get("additive"),
        bounds=bounds,
        systems=tuple(systems),
        source=raw.get("source"),
        may_affect=tuple(links),
        direction=direction,
        tolerance=float(raw.get("tolerance", 1e-6)),
        origin=origin,
        outcome=raw.get("outcome") is True,
        domain=domain.strip() if domain else None,
        raw=dict(raw),
    )


def load_specs(path: str | Path, origin: str = "core") -> list[MetricSpec]:
    """Load one YAML file or every *.yaml in a directory. A file may hold one metric,
    a list of metrics, or a mapping with a `metrics:` list."""
    path = Path(path)
    files = sorted(path.glob("*.yaml")) + sorted(path.glob("*.yml")) if path.is_dir() else [path]
    specs: list[MetricSpec] = []
    for f in files:
        data = yaml.safe_load(f.read_text(encoding="utf-8"))
        if data is None:
            continue
        items = data.get("metrics") if isinstance(data, dict) and "metrics" in data else data
        if isinstance(items, dict):
            items = [items]
        for raw in items:
            try:
                specs.append(spec_from_dict(raw, origin=origin))
            except SpecError as e:
                raise SpecError(f"{f.name}: {e}") from None
    return specs
