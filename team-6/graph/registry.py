"""Metric registry (L2, Person A).

Loads metric definitions (contracts/contracts.md section 2), checks their
structure, and keeps them by name. Checking a definition *against the data*
(does the calculation run, do the dimensions exist) is done by
``MetricEngine.validate``; ``MetricRegistry.add(defn, engine=...)`` does both,
which is the entry point L3 uses when a new metric arrives.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any, Iterable, Iterator, Optional

import yaml

if TYPE_CHECKING:  # pragma: no cover
    from .engine import MetricEngine

ALLOWED_UNITS = {"count", "ratio", "currency_eur", "rate"}
DEFAULT_SOURCE_TABLE = "marketplace_hourly"
DEFAULT_METRICS_FILE = Path(__file__).with_name("metrics.yaml")

_NAME_RE = re.compile(r"^[a-z][a-z0-9_]*$")
_REQUIRED = ("name", "description", "unit", "calculation", "dimensions")
_OPTIONAL = ("source_table", "higher_is_better", "business_weight")
# A calculation is a single aggregate expression, never a statement.
_FORBIDDEN_SQL = re.compile(
    r"\b(select|from|join|with|insert|update|delete|drop|alter|create|attach|detach|"
    r"copy|pragma|install|load|export|import|call|set)\b|;|--|/\*",
    re.IGNORECASE,
)


class MetricDefinitionError(ValueError):
    """A metric definition is malformed or does not work on the data."""


@dataclass(frozen=True)
class MetricDefinition:
    name: str
    description: str
    unit: str
    calculation: str
    dimensions: tuple[str, ...]
    source_table: str = DEFAULT_SOURCE_TABLE
    higher_is_better: Optional[bool] = None
    business_weight: float = 0.5

    def __post_init__(self) -> None:
        errors = structural_errors(self)
        if errors:
            raise MetricDefinitionError(f"metric {self.name!r}: " + "; ".join(errors))

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "MetricDefinition":
        if not isinstance(data, dict):
            raise MetricDefinitionError(f"metric definition must be a mapping, got {type(data).__name__}")
        name = data.get("name", "<unnamed>")
        missing = [k for k in _REQUIRED if k not in data]
        unknown = sorted(set(data) - set(_REQUIRED) - set(_OPTIONAL))
        problems = []
        if missing:
            problems.append(f"missing fields {missing}")
        if unknown:
            problems.append(f"unknown fields {unknown} (allowed: {list(_REQUIRED + _OPTIONAL)})")
        if problems:
            raise MetricDefinitionError(f"metric {name!r}: " + "; ".join(problems))
        dims = data["dimensions"]
        if isinstance(dims, str) or not isinstance(dims, Iterable):
            raise MetricDefinitionError(f"metric {name!r}: dimensions must be a list")
        weight = data.get("business_weight", 0.5)
        return cls(
            name=data["name"],
            description=str(data["description"]).strip(),
            unit=data["unit"],
            calculation=str(data["calculation"]).strip(),
            dimensions=tuple(dims),
            source_table=data.get("source_table") or DEFAULT_SOURCE_TABLE,
            higher_is_better=data.get("higher_is_better"),
            business_weight=float(weight) if weight is not None else 0.5,
        )

    def to_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "name": self.name,
            "description": self.description,
            "unit": self.unit,
            "calculation": self.calculation,
            "dimensions": list(self.dimensions),
        }
        if self.source_table != DEFAULT_SOURCE_TABLE:
            out["source_table"] = self.source_table
        out["higher_is_better"] = self.higher_is_better
        out["business_weight"] = self.business_weight
        return out

    def columns_used(self, known_columns: Iterable[str]) -> list[str]:
        """Table columns referenced in the calculation (for L3 connection finding)."""
        tokens = set(re.findall(r"[A-Za-z_][A-Za-z0-9_]*", self.calculation))
        return sorted(c for c in known_columns if c in tokens)

    @property
    def is_additive(self) -> bool:
        """True for a plain SUM of one expression: segment values add up to the total."""
        return bool(re.fullmatch(r"SUM\([^()]*\)", self.calculation, re.IGNORECASE)) or self.calculation.upper() == "COUNT(*)"


def structural_errors(defn: MetricDefinition) -> list[str]:
    """Checks that need no data."""
    errors = []
    if not isinstance(defn.name, str) or not _NAME_RE.match(defn.name):
        errors.append("name must be snake_case (a-z, 0-9, _), starting with a letter")
    if not defn.description:
        errors.append("description is empty")
    if defn.unit not in ALLOWED_UNITS:
        errors.append(f"unit {defn.unit!r} not in {sorted(ALLOWED_UNITS)}")
    if not defn.calculation:
        errors.append("calculation is empty")
    elif _FORBIDDEN_SQL.search(defn.calculation):
        errors.append("calculation must be a single aggregate expression (no statements, subqueries or comments)")
    if not all(isinstance(d, str) and _NAME_RE.match(d) for d in defn.dimensions):
        errors.append("dimensions must be column names")
    if len(set(defn.dimensions)) != len(defn.dimensions):
        errors.append("dimensions contain duplicates")
    if not isinstance(defn.source_table, str) or not _NAME_RE.match(defn.source_table):
        errors.append("source_table must be a table name")
    if defn.higher_is_better not in (True, False, None):
        errors.append("higher_is_better must be true, false or null")
    if not 0.0 <= defn.business_weight <= 1.0:
        errors.append("business_weight must be between 0 and 1")
    return errors


@dataclass
class MetricRegistry:
    _metrics: dict[str, MetricDefinition] = field(default_factory=dict)

    # --- loading ---------------------------------------------------------
    @classmethod
    def load(cls, path: str | Path = DEFAULT_METRICS_FILE) -> "MetricRegistry":
        """Load a YAML file (a list of definitions, or one) or a folder of them."""
        reg = cls()
        p = Path(path)
        files = sorted(p.glob("*.y*ml")) if p.is_dir() else [p]
        for f in files:
            for item in _read_yaml_items(f):
                reg.add(MetricDefinition.from_dict(item))
        return reg

    @classmethod
    def default(cls) -> "MetricRegistry":
        return cls.load(DEFAULT_METRICS_FILE)

    # --- changes ---------------------------------------------------------
    def add(
        self,
        defn: MetricDefinition | dict[str, Any],
        engine: Optional["MetricEngine"] = None,
        replace: bool = False,
    ) -> MetricDefinition:
        """Add a metric. With ``engine`` it must also pass validation on the data."""
        if isinstance(defn, dict):
            defn = MetricDefinition.from_dict(defn)
        if defn.name in self._metrics and not replace:
            raise MetricDefinitionError(f"metric {defn.name!r} already exists")
        if engine is not None:
            report = engine.validate(defn)
            if not report.ok:
                raise MetricDefinitionError(f"metric {defn.name!r} failed validation: " + "; ".join(report.errors))
        self._metrics[defn.name] = defn
        return defn

    def remove(self, name: str) -> None:
        del self._metrics[self._require(name).name]

    # --- access ----------------------------------------------------------
    def get(self, name: str) -> MetricDefinition:
        return self._require(name)

    def names(self) -> list[str]:
        return list(self._metrics)

    def __contains__(self, name: object) -> bool:
        return name in self._metrics

    def __iter__(self) -> Iterator[MetricDefinition]:
        return iter(self._metrics.values())

    def __len__(self) -> int:
        return len(self._metrics)

    def to_yaml(self, path: str | Path) -> Path:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(
            yaml.safe_dump([m.to_dict() for m in self], sort_keys=False, allow_unicode=True),
            encoding="utf-8",
        )
        return out

    def _require(self, name: str) -> MetricDefinition:
        try:
            return self._metrics[name]
        except KeyError:
            raise KeyError(f"unknown metric {name!r}") from None


def _read_yaml_items(path: Path) -> list[dict[str, Any]]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    if data is None:
        return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return data
    raise MetricDefinitionError(f"{path}: expected a list of metric definitions")
