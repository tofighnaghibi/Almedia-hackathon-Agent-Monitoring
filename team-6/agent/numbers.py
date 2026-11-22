"""Find the numbers in a text and check them against known facts (with rounding).

Used to check model-written text: every number, percentage and euro amount in
it must be one the code produced. ``8,000`` matches 7,998.7 (a round
thousand within 1%), ``94%`` matches a share of 0.94, ``60.0%`` matches a
relative change of -60.0; ``4,321`` matches nothing unless it is a fact.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from typing import Any, Iterable

_NUM_RE = re.compile(r"(?<![\d.])(\d{1,3}(?:,\d{3})+|\d+)(\.\d+)?(\s?%|\s?(?:thousand|million|billion|bn|mn|k|K|M)\b)?")
_SCALE = {"thousand": 1e3, "k": 1e3, "K": 1e3, "million": 1e6, "mn": 1e6, "M": 1e6, "billion": 1e9, "bn": 1e9}


@dataclass(frozen=True)
class Num:
    value: float
    decimals: int
    percent: bool
    raw: str
    scale: float = 1.0  # "2.49 million" -> value 2,490,000, scale 1e6

    @property
    def significant(self) -> bool:
        """Specific enough to count as citing evidence (not a bare small count)."""
        return self.decimals > 0 or self.percent or self.value >= 10


def extract(text: str) -> list[Num]:
    out = []
    for m in _NUM_RE.finditer(text or ""):
        whole, frac, suffix = m.group(1), m.group(2) or "", (m.group(3) or "").strip()
        try:
            v = float(whole.replace(",", "") + frac)
        except ValueError:
            continue
        scale = _SCALE.get(suffix, 1.0)
        out.append(Num(v * scale, len(frac) - 1 if frac else 0, suffix == "%", m.group(0), scale))
    return out


def values_in(obj: Any) -> set[float]:
    """Every number in a JSON-like object, including numbers written inside strings."""
    found: set[float] = set()

    def walk(x: Any) -> None:
        if isinstance(x, bool) or x is None:
            return
        if isinstance(x, (int, float)):
            if math.isfinite(float(x)):
                found.add(float(x))
        elif isinstance(x, str):
            found.update(n.value for n in extract(x))
        elif isinstance(x, dict):
            for k, v in x.items():
                walk(k)
                walk(v)
        elif isinstance(x, (list, tuple, set)):
            for v in x:
                walk(v)

    walk(obj)
    return found


def _unit(n: Num) -> float:
    """The precision the number is written with (0.01 for "2.49", 10,000 for "2.49 million")."""
    unit = 10 ** (-n.decimals) * n.scale
    if n.decimals == 0 and n.scale == 1.0 and n.value >= 1000:  # "8,000" may be a rounded 7,998.7
        s = str(int(n.value))
        zeros = len(s) - len(s.rstrip("0"))
        unit = max(unit, min(10 ** zeros, 0.02 * n.value))
    return unit


def matches(n: Num, allowed: Iterable[float]) -> bool:
    """True if ``n`` is one of ``allowed`` as written, rounded or truncated to the precision
    it is written with, without sign, or as a percent of a share.

    Rounded: the fact is within half a unit either way. Truncated: the fact is up to one
    unit above (175,188 for 175,188.65). So 101 never passes for 100.
    """
    unit = _unit(n)
    for v in allowed:
        for cand in (abs(v), abs(v) * 100.0):
            d = cand - n.value
            if -0.5 * unit - 1e-9 <= d < unit - 1e-12 or abs(d) <= 1e-9:
                return True
    return False


def unknown_numbers(text: str, facts: Any) -> list[str]:
    """The numbers in ``text`` that are not among the numbers in ``facts``."""
    allowed = values_in(facts)
    return [n.raw.strip() for n in extract(text) if not matches(n, allowed)]
