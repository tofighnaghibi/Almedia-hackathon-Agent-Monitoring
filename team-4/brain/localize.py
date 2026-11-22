"""Where a rate moved: every place the dimensions can name is scored, none is skipped.

With four dimensions of 4, 3, 5 and 3 values there are 479 places (a country, a country and a
platform, ... down to one finest cell) plus the whole. Each is scored by how much better the
counts of the finest cells are explained by "the rate moved by one common factor inside this
place and nowhere else" than by "nothing moved":

- for a proportion the gain is the binomial log-likelihood ratio, cell by cell, with the common
  factor estimated from the place's own counts. It is exact for small counts: one cell with 0 of 3
  is weak evidence, however wild its ratio looks;
- for any other ratio it is the Gaussian gain on the residual, with the spread history showed.

A place that contains the real one is diluted (its common factor fits neither part), a place inside
it has only part of the evidence, so the real place has the highest gain. Naming a place among 180
is a stronger claim than naming one among 4, so every place pays ln(number of places of its kind).
The scan is exhaustive because it is cheap here (16 group-bys over 180 cells); no greedy order of
dimensions is needed, and a place in a combination nobody watches is found as well.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from itertools import combinations

import numpy as np
import pandas as pd

from brain.stats import binomial_surprise

EPS = 1e-9
CLEAR_MARGIN = 3.0    # in log-likelihood: the chosen place must beat a different one by about a factor of 20
QUIET_MAX_SURPRISE = 2.0   # the rest of a narrowed place: two-sided p above 0.01 against its expected rate
QUIET_MAX_Z = 3.0          # the same for a ratio judged on its historical spread


@dataclass
class Place:
    segment: dict
    score: float          # gain minus the price of naming this place
    gain: float           # log-likelihood gain of a common shift here over no change
    share: float          # part of the whole deviation of the numerator that lies inside
    observed: float
    expected: float
    numerator: float
    denominator: float
    cells: int
    counted: float = 0.0  # the numerator as it was judged (the surplus for a ratio judged on its surplus)
    awaited: float = 0.0  # the same numerator at the expected rate

    @property
    def dims(self) -> tuple[str, ...]:
        return tuple(self.segment)


@dataclass
class Choice:
    place: Place          # the place to report
    verdict: str          # "confirmed": the monitor's place; "refined": a place nested with it; "kept": the monitor's
    clear: bool           # no different place explains the data nearly as well
    rival: Place | None   # the best place that is neither inside nor around the chosen one


def nested(a: dict, b: dict) -> bool:
    return a.items() <= b.items() or b.items() <= a.items()


def scan(dims, keys, num, den, rate, kind: str = "proportion", unit_var: float = 1.0, shift: float = 0.0) -> list[Place]:
    """Score every place. `keys` are the value tuples of the finest cells along `dims`; `num`, `den`
    their counts in the window; `rate` the expected rate of each cell. For kind "ratio", `unit_var` is
    the variance of (num - rate * den) / sqrt(den) in history. `shift` is added to the reported rates
    (1 for a ratio judged on its surplus over the denominator). Best place first."""
    dims = tuple(dims)
    frame = pd.DataFrame(list(keys), columns=list(dims))
    k, n, p = (np.asarray(a, dtype=float) for a in (num, den, rate))
    ok = np.isfinite(k) & np.isfinite(n) & np.isfinite(p) & (n > 0)
    frame["k"], frame["n"] = np.where(ok, k, 0.0), np.where(ok, n, 0.0)
    frame["p"] = np.where(ok, p, 0.0)
    frame["m"] = frame["p"] * frame["n"]           # expected numerator
    frame["r"] = frame["k"] - frame["m"]           # deviation
    total_r = float(frame["r"].sum())
    n_kinds = 2 ** len(dims)
    out: list[Place] = []
    for size in range(len(dims) + 1):
        for subset in combinations(dims, size):
            by = list(subset)
            if by:
                K = frame.groupby(by, sort=False)["k"].transform("sum")
                M = frame.groupby(by, sort=False)["m"].transform("sum")
            else:
                K = pd.Series(frame["k"].sum(), index=frame.index)
                M = pd.Series(frame["m"].sum(), index=frame.index)
            with np.errstate(divide="ignore", invalid="ignore"):
                theta = np.where(M > 0, K / M, 1.0)            # the common factor of the place a cell lies in
                if kind == "ratio":
                    v = np.maximum(frame["n"] * unit_var, EPS)
                    frame["a"] = frame["r"] * frame["m"] / v
                    frame["b"] = frame["m"] ** 2 / v
                    frame["g"] = 0.0
                else:
                    p0 = np.clip(frame["p"], EPS, 1 - EPS)
                    q = np.clip(theta * frame["p"], EPS, 1 - EPS)
                    frame["g"] = np.where(frame["n"] > 0,
                                          frame["k"] * np.log(q / p0) + (frame["n"] - frame["k"]) * np.log((1 - q) / (1 - p0)),
                                          0.0)
                    frame["a"] = frame["b"] = 0.0
            cols = ["k", "n", "m", "r", "g", "a", "b"]
            if by:
                agg = frame.groupby(by, sort=False)[cols].sum()
                counts = frame.groupby(by, sort=False).size()
                rows = [(key if isinstance(key, tuple) else (key,), agg.loc[key], int(counts.loc[key])) for key in agg.index]
            else:
                rows = [((), frame[cols].sum(), len(frame))]
            price = math.log(len(rows)) + math.log(n_kinds)
            for key, s, count in rows:
                if s["n"] <= 0:
                    continue
                gain = float(s["a"] ** 2 / (2 * s["b"])) if kind == "ratio" and s["b"] > 0 else float(max(s["g"], 0.0))
                out.append(Place(
                    segment=dict(zip(subset, key)), score=gain - price, gain=gain,
                    share=float(np.clip(s["r"] / total_r, 0.0, 1.0)) if total_r else 0.0,
                    observed=float(s["k"] / s["n"] + shift), expected=float(s["m"] / s["n"] + shift),
                    numerator=float(s["k"] + shift * s["n"]), denominator=float(s["n"]), cells=count,
                    counted=float(s["k"]), awaited=float(s["m"])))
    out.sort(key=lambda pl: (-round(pl.score, 6), -len(pl.segment)))
    return out


def quiet_outside(inner: Place, outer: Place, kind: str = "proportion", unit_var: float = 1.0) -> bool:
    """Is the part of `outer` that is not in `inner` unremarkable? Narrowing a place claims that
    nothing moved in the rest of it; the rest is tested as one pool against its expected rate."""
    n = outer.denominator - inner.denominator
    k, m = outer.counted - inner.counted, outer.awaited - inner.awaited
    if n <= 0 or m <= 0:
        return True
    if kind == "ratio":
        return abs(k - m) / math.sqrt(n * max(unit_var, EPS)) < QUIET_MAX_Z
    surprise, _ = binomial_surprise(np.array([round(k)]), np.array([n]), np.array([min(max(m / n, EPS), 1 - EPS)]))
    return float(surprise[0]) < QUIET_MAX_SURPRISE


def one_place(places: list[Place], wider: Place, here: Place, kind: str, unit_var: float) -> bool:
    """Widening claims one common factor in all of `wider`: the rest of it must have moved too, and the
    whole must explain the counts at least as well as its parts of the anchor's kind taken one by one."""
    parts = [p for p in places if set(p.segment) == set(here.segment) and wider.segment.items() <= p.segment.items()]
    return wider.score >= sum(max(p.score, 0.0) for p in parts) and not quiet_outside(here, wider, kind, unit_var)


def choose(places: list[Place], anchor: dict, margin: float = CLEAR_MARGIN, kind: str = "proportion",
           unit_var: float = 1.0) -> Choice:
    """The place to report, given the place the monitor raised the incident on.

    The scan's best place is taken when it is the monitor's place, or lies around it and is clearly
    better, or lies inside it, is clearly better and leaves the rest of the monitor's place quiet.
    A best place somewhere else belongs to another incident: the monitor's place is kept. Either
    way the result says whether a different place explains the data nearly as well."""
    by_segment = {frozenset(p.segment.items()): p for p in places}
    here = by_segment.get(frozenset(anchor.items()))
    top = places[0]
    if here is None or top.segment == anchor:
        chosen, verdict = top, "confirmed" if here is not None else "refined"
    elif here.score > 0 and nested(top.segment, anchor) and top.score - here.score >= margin and (
            one_place(places, top, here, kind, unit_var) if top.segment.items() < anchor.items()
            else quiet_outside(top, here, kind, unit_var)):
        chosen, verdict = top, "refined"
    else:
        chosen, verdict = here, "kept"
    rival = next((p for p in places if not nested(p.segment, chosen.segment)), None)
    clear = rival is None or chosen.score - rival.score >= margin
    return Choice(place=chosen, verdict=verdict, clear=clear, rival=rival)
