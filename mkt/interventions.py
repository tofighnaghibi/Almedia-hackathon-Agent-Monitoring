"""Interventions as do()-operations on named levers (docs §7).

A lever is a numeric array the simulator computes from its state (e.g. per-device checkout
error rate, per-offer price multiplier). An intervention applies `set`, `shift` or `scale` to
the lever elements whose attributes match `selector`, at times inside `window`.

Selector grammar (deliberately tiny): clauses joined by ' and ', each
    key == 'v'   |   key != 'v'   |   key in ['a', 'b']
Values starting with '@' are world aliases (e.g. '@seller_A'), resolved by the world.
"""
from __future__ import annotations

import ast
import re
from typing import Mapping

import numpy as np

from .contracts import Intervention

_CLAUSE = re.compile(r"^\s*(\w+)\s*(==|!=|in)\s*(.+?)\s*$")


def parse_selector(sel: str) -> list[tuple[str, str, list[str]]]:
    out = []
    for part in [p for p in sel.split(" and ") if p.strip()]:
        m = _CLAUSE.match(part)
        if not m:
            raise ValueError(f"bad selector clause: {part!r}")
        key, op, raw = m.groups()
        val = ast.literal_eval(raw)
        vals = [str(v) for v in (val if isinstance(val, (list, tuple)) else [val])]
        out.append((key, op, vals))
    return out


def selector_mask(sel: str, attrs: Mapping[str, np.ndarray], n: int, aliases: Mapping[str, str]) -> np.ndarray:
    mask = np.ones(n, bool)
    for key, op, vals in parse_selector(sel):
        if key not in attrs:
            raise KeyError(f"selector key {key!r} not available for this lever; have {sorted(attrs)}")
        vals = [aliases.get(v, v) for v in vals]
        hit = np.isin(attrs[key], vals)
        mask &= ~hit if op == "!=" else hit
    return mask


class Schedule:
    """Interventions of one layer, queried by lever."""

    def __init__(self, interventions: list[Intervention], aliases: Mapping[str, str] | None = None):
        self.items = interventions
        self.aliases = dict(aliases or {})

    def for_target(self, target: str) -> list[Intervention]:
        return [i for i in self.items if i.target == target]

    def apply(self, target: str, base: np.ndarray, attrs: Mapping[str, np.ndarray],
              times_us: np.ndarray | int) -> np.ndarray:
        """Return lever values after applying matching interventions.
        `times_us`: scalar tick time or per-element times (microseconds since epoch)."""
        ivs = self.for_target(target)
        if not ivs:
            return base
        out = np.array(base, dtype=float, copy=True)
        n = out.shape[0]
        t = np.broadcast_to(np.asarray(times_us, dtype=np.int64), (n,))
        for iv in ivs:
            m = selector_mask(iv.selector, attrs, n, self.aliases)
            if iv.window:
                lo, hi = (_us(iv.window[0]), _us(iv.window[1]))
                m &= (t >= lo) & (t < hi)
            if not m.any():
                continue
            if iv.operator == "set":
                out[m] = iv.value
            elif iv.operator == "shift":
                out[m] += iv.value
            elif iv.operator == "scale":
                out[m] *= iv.value
            else:
                raise ValueError(f"unknown operator {iv.operator}")
        return out


def _us(dt) -> int:
    return int(np.datetime64(dt, "us").astype(np.int64))
