"""The investigator: from "a rate moved" to where, how much, and why, with the evidence for each step.

Called for every open incident on every tick. It asks, in this order, and records each question
and its answer in the trail:

1. What moved, and which of it is cause, which symptom? A metric that another moved metric may
   affect (a link of the graph, in the direction the link predicts) is a symptom of that metric.
2. Where? Every place the dimensions can name is scored on the finest cells (brain/localize.py).
   The monitor's place is confirmed, narrowed or kept; a different place that explains the data
   nearly as well is reported as doubt.
3. What did it cost? The outcome's change is split over the atomic factors along the formulas of
   the graph (brain/attribution.py): the moved rates against their expected values, every other
   rate against its own baseline, so the report can say how much of the gap the incident explains.
4. Why? Every candidate explanation is tested and scored, and the ones that fail stay in the report
   with the reason: releases around the onset (is the system linked to what moved, does the scope
   fit the place, did the change start with the release), the moved metric itself, a planned event,
   late data, and a shift in the mix of segments with unchanged rates.
5. Stop: every candidate is tested; the last step of the trail says whether the leading explanation is
   supported and how far ahead of the next one it is.

Nothing here is specific to a metric, a country or a kind of fault: the candidates come from the
graph's links and system tags and from the rows of the context tables.
"""
from __future__ import annotations

import math

import numpy as np
import pandas as pd

from brain.attribution import attribute, leaves
from brain.clock import HOUR
from brain.data import iso_hour
from brain.evaluator import NotComputable
from brain.graph import DEMAND_SYSTEM
from brain.impact import tree_metrics
from brain.localize import choose, scan
from brain.monitor import Alert, Monitor, Tick, compatible
from brain.stats import ratio_surprise, robust_scale

RELEASE_BEFORE_HOURS = 2      # a release this long before the estimated onset is "at the onset"
RELEASE_AFTER_HOURS = 2       # the onset estimate can be early when the first hours were weak
RELEASE_LOOKBACK_HOURS = 12   # older ones count only if the hours in between already lean the same way
FOLLOW_MIN_SURPRISE = 1.3     # one targeted question along a link, direction predicted: p below 0.05
NORMAL_MAX_SURPRISE = 2.0     # below this a window is "normal" for the purpose of a comparison
MIX_MAX_SHARE = 0.2           # a mix shift that explains less than this share of the change is rejected
MIX_EXPLAINS_SHARE = 0.8      # ... and one that explains at least this share is supported: an innocent change
PLANNED_MAX_LIFT = 1.0        # a planned event is taken to lift a metric linked to the calendar by at most +100 %
PACED_SYSTEMS = ("pacing",)   # systems that spend a budget on a schedule: a rise, then a fall, is the budget spent early
# What would show a first cause behind a system's metric, and is not in the data. A metric of these
# systems that moved without a release is reported as the last cause the data can show.
UPSTREAM_RECORDS = {"inventory": "supplier deliveries", "purchasing": "purchase orders"}
INSIDE_MIN_SHARE = 0.5        # a release that covers only part of the place must hold at least this much of the change
MAX_REJECTED_RELEASES = 4     # rejected releases kept in the report (the best scoring ones)
CHECK_MIN_SURPRISE = 3.0      # a factor of the checked chain "moved": p below 0.001 over the hours checked,
CHECK_MIN_EFFECT = 0.03       # and by at least 3 % (in a big place a tiny drift is significant, but it is not the story)
VOLUME_WEEKS = 3              # a count is compared with the same hours of up to three earlier weeks
PLACES_SHOWN = 5
DIM_ORDER = ("country", "platform", "category", "channel")
SCOPE_DIMS = ("country", "platform")   # the dimensions a release row can name


def ordered(segment: dict) -> dict:
    return {d: segment[d] for d in DIM_ORDER if d in segment}


def seg_text(segment: dict) -> str:
    return " x ".join(str(v) for v in ordered(segment).values()) or "all segments"


def num(x, digits: int = 3) -> str:
    if x is None or not math.isfinite(x):
        return "n/a"
    return f"{x:.{digits}g}" if abs(x) < 1000 else f"{x:,.0f}"


def when(ts: pd.Timestamp) -> str:
    return ts.strftime("%Y-%m-%d %H:%M UTC")


class Investigator:
    def __init__(self, monitor: Monitor):
        self.monitor = monitor
        self.graph = monitor.graph
        self.store = monitor.store

    # ------------------------------------------------------------ 1. what moved
    def _origins(self, metric: str) -> list[str]:
        """The metric and, for a rate, its numerator: cause links are declared on either."""
        spec = self.graph.specs[metric]
        return [metric] + (list(spec.formula.ratio_parts()[0]) if spec.formula is not None and spec.formula.kind == "ratio" else [])

    def _links(self, metric: str):
        return [e for o in self._origins(metric) for e in self.graph.out_edges(o, "may_affect")]

    def symptoms(self, alerts: list[Alert]) -> dict[str, str]:
        """moved metric -> the moved metric that may cause it, when it moved the way the link predicts."""
        by_metric = {a.metric: a for a in alerts}
        out: dict[str, str] = {}
        for a in alerts:
            for e in self._links(a.metric):
                b = by_metric.get(e.dst)
                if b is None or b.metric == a.metric or out.get(a.metric) == b.metric:
                    continue
                sign = e.attrs.get("sign", 0)
                same = (b.observed > b.expected) == (a.observed > a.expected)
                if not sign or (sign > 0) == same:
                    out.setdefault(b.metric, a.metric)
        return out

    def follow(self, alerts: list[Alert], segment: dict, start, end, skip=()) -> list[dict]:
        """Metrics that a moved metric may affect and that did move the way the link predicts, in the
        same place and hours. They were too weak for the hourly scan, but they carry the damage:
        out-of-stock listings cost orders through the add-to-cart rate. `shown` False: the link
        predicts a direction but the data cannot show it yet; the metric still enters the cost."""
        out, seen = [], set()
        for a in alerts:
            for e in self._links(a.metric):
                if e.dst in seen:
                    continue
                w = self.monitor.window(e.dst, segment, start, end, skip)
                if w is None:
                    continue
                sign = e.attrs.get("sign", 0)
                moved = 1 if w["observed"] > w["expected"] else -1
                as_predicted = not sign or moved == sign * (1 if a.observed > a.expected else -1)
                shown = w["score"] >= FOLLOW_MIN_SURPRISE and as_predicted
                if not shown and not sign:
                    continue   # a link without a direction says nothing until the movement is clear
                seen.add(e.dst)
                out.append(dict(w, via=a.metric, sign=sign, shown=shown, note=e.attrs.get("note", "")))
        return out

    # ---------------------------------------------------------------- 2. where
    def phases(self, metric: str, segment: dict, start, end, skip=()) -> dict:
        """The first hour of the phase the metric is in now, and the earlier phase if there was one. A rate
        that first rose and then fell (an ad budget spent early, then gone) cancels itself out over the
        whole incident: where it happened can only be read from hours in which it moved one way. The phase
        begins after the last hour that moved clearly the other way. `before`: those hours of the other way,
        with `factor` (observed over expected, on average) and `hours` (from the first to the last of them)."""
        floor = self.monitor.config.min_effect
        clear = [h for h in self.monitor.hourly(metric, segment, start, end, skip)
                 if h["score"] >= NORMAL_MAX_SURPRISE and h["expected"] and abs(h["observed"] / h["expected"] - 1) >= floor]
        begin, before = pd.Timestamp(start), []
        if clear:
            rising = clear[-1]["z"] > 0
            before = [h for h in clear if (h["z"] > 0) != rising]
            if before:
                begin = before[-1]["hour"] + HOUR
        out = {"begin": begin, "rising": bool(clear) and clear[-1]["z"] > 0, "before": before}
        if before:
            out["factor"] = float(np.mean([h["observed"] / h["expected"] for h in before]))
            out["hours"] = int((before[-1]["hour"] - before[0]["hour"]) / HOUR) + 1
        return out

    def phase_start(self, metric: str, segment: dict, start, end, skip=()) -> pd.Timestamp:
        return self.phases(metric, segment, start, end, skip)["begin"]

    def localize(self, metric: str, anchor: dict, start, end, skip=()):
        """(choice, places by segment, cells) for the metric, or (None, {}, None) without cell data."""
        cells = self.monitor.cells(metric, start, end, skip)
        if cells is None or float(np.sum(cells["den"])) <= 0:
            return None, {}, None
        places = scan(cells["dims"], cells["keys"], cells["num"], cells["den"], cells["rate"],
                      cells["kind"], cells["unit_var"], cells["shift"])
        if not places:
            return None, {}, cells
        if not any(p.segment == anchor for p in places):
            return None, {}, cells   # the monitor's place has no counts in this window
        choice = choose(places, anchor, kind=cells["kind"], unit_var=cells["unit_var"])
        return choice, {frozenset(p.segment.items()): p for p in places}, cells

    @staticmethod
    def mix(cells: dict, place: dict) -> dict | None:
        """Split the rate's change inside a place into "the rates of its cells moved" and "the weights
        of its cells moved" (more traffic in cells that always had a lower rate)."""
        dims = cells["dims"]
        inside = np.array([all(key[dims.index(d)] == v for d, v in place.items()) for key in cells["keys"]])
        den, hist = cells["den"][inside], cells["history_den"][inside]
        if den.sum() <= 0 or hist.sum() <= 0:
            return None
        p0 = cells["rate"][inside] + cells["shift"]
        with np.errstate(divide="ignore", invalid="ignore"):
            p1 = np.where(den > 0, cells["num"][inside] / den + cells["shift"], p0)
        w1, w0 = den / den.sum(), hist / hist.sum()
        mix = float(((w1 - w0) * p0).sum())
        rate = float((w1 * (p1 - p0)).sum())
        size = abs(mix) + abs(rate)
        return {"mix": mix, "rate": rate, "share": abs(mix) / size if size else 0.0}

    # ------------------------------------------------------- the chain, top-down
    def is_count(self, metric: str) -> bool:
        """A volume read straight from a column (sessions), not a rate."""
        spec = self.graph.specs.get(metric)
        return spec is not None and spec.unit == "count" and spec.formula is None and bool(spec.column)

    def volume(self, metric: str, segment: dict, start, end, skip=()) -> dict | None:
        """A count in a place over [start, end) against the same hours of up to VOLUME_WEEKS earlier
        weeks, or of the earlier days when there is less than a week of history: counts follow the hour
        of day and the day of week, so no pooled rate can stand in for them. The spread is what the same
        comparison showed hour by hour in the week before. None without history."""
        if not self.is_count(metric):
            return None
        start, end = pd.Timestamp(start), pd.Timestamp(end)
        week, day = 7 * 24 * HOUR, 24 * HOUR
        try:
            frame = self.monitor.evaluator.frame([metric], start - VOLUME_WEEKS * week, end, segment, by_hour=True)
        except (NotComputable, KeyError):
            return None
        values = {pd.Timestamp(t): float(v) for t, v in zip(frame["ts"], frame[metric])}

        def reference(h):
            for step, n in ((week, VOLUME_WEEKS), (day, 7)):
                refs = [values.get(h - k * step) for k in range(1, n + 1) if h - k * step < start]
                refs = [v for v in refs if v is not None and math.isfinite(v)]
                if refs:
                    return float(np.mean(refs))
            return None

        held = {pd.Timestamp(h) for h in skip}
        obs = exp = squares = 0.0
        hours, h = 0, start
        while h < end:
            v = values.get(h)
            r = reference(h) if h not in held and v is not None and math.isfinite(v) else None
            if r is not None:
                obs, exp, squares, hours = obs + v, exp + r, squares + r * r, hours + 1
            h += HOUR
        if not hours or exp <= 0:
            return None
        rel = []
        h = start - week
        while h < start:
            v, r = values.get(h), reference(h)
            if v is not None and math.isfinite(v) and r:
                rel.append((v - r) / r)
            h += HOUR
        spread = robust_scale(rel)
        sd = spread * math.sqrt(squares) if math.isfinite(spread) else 2.0 * math.sqrt(exp)
        score, z = ratio_surprise(np.array([obs]), np.array([1.0]), np.array([exp]), np.array([max(sd, 1e-9)]))
        return {"metric": metric, "observed": obs, "expected": exp, "hours": hours,
                "score": float(score[0]), "z": float(z[0])}

    def look(self, metric: str, segment: dict, start, end, skip=()) -> dict | None:
        """One targeted look at a metric in a place: a count against earlier weeks, a rate against its baseline."""
        return self.volume(metric, segment, start, end, skip) if self.is_count(metric) else \
            self.monitor.window(metric, segment, start, end, skip)

    def chain(self, moved) -> tuple[str, list[str]] | None:
        """The count of the outcome tree that the moved factors feed, with every factor it is made of and
        its price: orders (sessions x the funnel rates) and the order value for a funnel rate, ad clicks
        and the click price for an ad rate. The brief's path runs down this chain: traffic, then each step."""
        outcomes = self.graph.top_outcomes()
        if not outcomes:
            return None
        specs, tree = self.graph.specs, tree_metrics(self.graph, outcomes[0])
        counts = [(m, leaves(self.graph, m)) for m in tree
                  if specs[m].unit == "count" and specs[m].investigative_formula is not None]
        fed = [c for c in counts if c[0] in moved] or [c for c in counts if any(x in c[1] for x in moved)] or counts
        if not fed:
            return None
        node, factors = max(fed, key=lambda c: len(c[1]))
        for p in tree:
            f = specs[p].investigative_formula
            if f is not None and f.kind == "product" and node in f.factors():
                factors = factors + [x for x in f.factors()
                                     if x != node and specs[x].investigative_formula is None and x not in factors]
        return node, factors

    def check(self, moved, segment: dict, start, end, skip=()) -> dict | None:
        """Every factor of the chain in the place and hours: observed against expected, normal or moved.
        Rates against the monitor's baseline (one targeted look each), counts against earlier weeks."""
        found = self.chain(moved)
        if found is None:
            return None
        node, factors = found
        rows = []
        for m in factors:
            w = self.look(m, segment, start, end, skip)
            if w is None or not w["expected"]:
                rows.append({"metric": m, "state": "no baseline"})
                continue
            effect = w["observed"] / w["expected"] - 1.0
            state = "moved" if w["score"] >= CHECK_MIN_SURPRISE and abs(effect) >= CHECK_MIN_EFFECT else "normal"
            rows.append(dict(w, metric=m, effect=effect, state=state))
        return {"node": node, "rows": rows, "segment": segment, "start": pd.Timestamp(start)}

    def label(self, metric: str) -> str:
        return f"traffic ({metric})" if DEMAND_SYSTEM in self.graph.specs[metric].systems and self.is_count(metric) else metric

    def check_text(self, found: dict) -> tuple[str, str]:
        """(trail line, report sentence) of a check."""
        parts = []
        for r in found["rows"]:
            if r["state"] == "no baseline":
                parts.append(f"{r['metric']}: no baseline here")
                continue
            per = r.get("hours")
            obs, exp = (r["observed"] / per, r["expected"] / per) if per else (r["observed"], r["expected"])
            parts.append(f"{r['metric']} {num(obs)}{'/h' if per else ''} vs {num(exp)}{'/h' if per else ''}, "
                         + ("normal" if r["state"] == "normal" else f"moved {100 * r['effect']:+.0f} %"))
        where = seg_text(found["segment"])
        line = f"{found['node']} in {where} since {when(found['start'])}, factor by factor: " + "; ".join(parts)
        moved = [r for r in found["rows"] if r["state"] == "moved"]
        normal = [r for r in found["rows"] if r["state"] == "normal"]
        traffic = [r["metric"] for r in normal if self.label(r["metric"]) != r["metric"]]
        if moved:
            sentence = (f" Of the factors of {found['node']} in {where}, "
                        + ", ".join(f"{self.label(r['metric'])} moved ({100 * r['effect']:+.0f} %)" for r in moved))
            if normal:
                sentence += "; the others" + (f", {self.label(traffic[0])} included," if traffic else "") + " stayed normal"
            sentence += "."
        elif normal:
            sentence = (f" Every factor of {found['node']} in {where}"
                        + (f", {self.label(traffic[0])} included," if traffic else "") + " stayed normal over these hours.")
        else:
            sentence = ""
        return line, sentence

    # ----------------------------------------------------------- 3. what it cost
    def explain(self, alerts: list[Alert], followed: list[dict], segment: dict, start, end, skip=(),
                frozen: dict | None = None) -> dict | None:
        """What the moved rates cost, in the top outcome's unit, in a segment and window.

        Two comparisons, for two questions:
        - the effect: the outcome as observed against the outcome with only the moved rates back at
          their expected values and everything else as observed. That is "had this not happened",
          whatever else is going on in the same place (a promo, a second fault);
        - the gap: the outcome as observed against the state with every judged rate at its own
          baseline, split over all factors. It shows what else is off besides the moved rates.

        `skip`: hours whose data has not arrived; they are in neither comparison. `frozen` keeps the
        expected rates of the first report of an incident, so a long incident does not drift into
        its own baseline. None when no moved metric is on the outcome's formula tree or the outcome
        cannot be computed there."""
        graph, ev = self.graph, self.monitor.evaluator
        outcome = graph.top_outcomes()[0] if graph.top_outcomes() else None
        if outcome is None:
            return None
        tree, atoms = tree_metrics(graph, outcome), leaves(graph, outcome)
        known = {a.metric: a.expected for a in alerts}
        known.update({f["metric"]: f["expected"] for f in followed})
        moved = [m for m in atoms if m in known]
        if not moved:
            return None
        below = {m: graph.specs[m].formula.ratio_parts()[1] for m in atoms
                 if graph.specs[m].formula is not None and graph.specs[m].formula.kind == "ratio"}
        names = list(dict.fromkeys(tree + [d for ds in below.values() for d in ds]))
        try:
            hourly = ev.sums(names, start, end, segment, by_hour=True)
            if len(skip):
                hourly = hourly[~hourly["ts"].isin(list(skip))]
            if hourly.empty:
                return None
            frame = ev.derive(hourly.drop(columns=["ts"]).sum(min_count=1).to_frame().T, names)
        except NotComputable:
            return None
        observed = {m: float(frame[m].iloc[0]) for m in names}
        expected = {}
        place = tuple(sorted(segment.items()))
        for m in atoms:
            if m in moved and self.is_count(m):
                # a volume (sessions): the same hours of earlier weeks, scaled to the hours summed here
                v = self.volume(m, segment, start, end, skip)
                expected[m] = observed[m] * v["expected"] / v["observed"] if v is not None and v["observed"] > 0 else observed[m]
                continue
            rate = frozen.get((m, place)) if frozen is not None else None
            if rate is None:
                rate = self.monitor.expected_rate(m, segment)
                if rate is None and m in known:
                    rate = known[m]
                if frozen is not None and rate is not None and math.isfinite(rate):
                    frozen[(m, place)] = rate
            expected[m] = rate if rate is not None and math.isfinite(rate) else observed[m]   # not judged: taken as it is
        for m, parts in below.items():
            # nothing to divide by (no orders at all, so no order value and no take rate): the rate cannot
            # be judged and multiplies a zero, so it is taken at its expected value. A missing sum stays missing.
            if not math.isfinite(observed[m]) and all(observed.get(d) == 0 for d in parts) and math.isfinite(expected[m]):
                observed[m] = expected[m]
        restored = {m: expected[m] if m in moved else observed[m] for m in atoms}
        try:
            cost = attribute(graph, outcome, observed, restored)
            whole = attribute(graph, outcome, observed, expected)
        except ValueError:      # more changed factors than the exact split takes
            return None
        if cost is None:
            return None
        effect = cost.delta
        gap = whole.delta if whole is not None else effect
        others = sorted(((m, v) for m, v in (whole.by_factor.items() if whole is not None else [])
                         if m not in moved and abs(v) >= 0.5), key=lambda kv: -abs(kv[1]))
        counted = {}
        for m in tree:   # what is counted along the way (orders, clicks), from the same two states
            spec = graph.specs[m]
            if m != outcome and spec.unit == "count" and spec.direction == "up" and spec.investigative_formula is not None:
                sub = attribute(graph, m, observed, restored)
                if sub is not None and abs(sub.delta) >= 0.5:
                    counted[m] = sub.delta
        return {
            "outcome": outcome, "unit": graph.specs[outcome].unit, "moved": moved, "method": cost.method,
            "observed": observed, "expected": expected, "actual": cost.actual, "effect": effect,
            "gap": gap, "others": others, "counted": counted,
            # the incident's share of everything that is off in this place; None when the rest outweighs it
            "explained": min(effect / gap, 1.0) if abs(gap) > 1e-9 and effect * gap > 0 else None,
            "unclear": [f["metric"] for f in followed if f["metric"] in moved and not f["shown"]],
            "contributions": [
                {"node": m, "share_of_delta": min(max(cost.by_factor[m] / effect, 0.0), 1.0) if abs(effect) > 1e-9 else 0.0,
                 "delta": cost.by_factor[m], "observed": observed[m], "expected": expected[m]}
                for m in sorted(moved, key=lambda m: -abs(cost.by_factor[m]))],
        }

    # ------------------------------------------------------------------ 4. why
    def releases(self, inc, tick: Tick, primary: Alert, moved: list[str], place: dict, segment: dict, end, places: dict,
                 skip=()) -> list[dict]:
        """Every release from RELEASE_LOOKBACK_HOURS before the onset to now, as a scored hypothesis."""
        graph, mon = self.graph, self.monitor
        rows = self.store.read_sql(
            "SELECT ts, system, country, platform, description FROM releases WHERE ts <= ? AND ts >= ? ORDER BY ts",
            (iso_hour(tick.now), iso_hour(inc.onset - RELEASE_LOOKBACK_HOURS * HOUR)))
        where = seg_text(place)
        place_scope = {d: place[d] for d in SCOPE_DIMS if d in place}
        falling = primary.observed < primary.expected
        best: dict[tuple, dict] = {}
        self.rollbacks_skipped = 0
        for r in rows.to_dict("records"):
            description = r["description"] if isinstance(r["description"], str) else ""   # the column may be NULL
            if "rollback" in description.lower():
                self.rollbacks_skipped += 1
                continue   # by the contract a rollback row ends a release; it is not a new cause
            scope = {d: r[d] for d in SCOPE_DIMS if isinstance(r[d], str) and r[d]}
            ts = pd.Timestamp(r["ts"])
            linked = [m for m in moved if r["system"] in graph.specs[m].systems]
            evidence = []

            # is the system linked to what moved?
            if primary.metric in linked:
                link = 1.0
                evidence.append(f"system {r['system']} is linked to {', '.join(linked)} in the graph")
            elif linked:
                link = 0.5
                evidence.append(f"system {r['system']} is linked to {', '.join(linked)}, a follow-on movement, "
                                f"not to {primary.metric}")
            else:
                link = 0.0
                evidence.append(f"system {r['system']} is not linked to any moved metric in the graph")

            # does the scope fit the place? A system that is itself a channel (recommendations) acts in that channel
            inside_share = None
            acts_in = r["system"] if r["system"] in self.channels() else None
            if acts_in is not None and place.get("channel") not in (None, acts_in):
                relation, fit = "conflict", 0.0
                evidence.append(f"a {r['system']} release acts in the {acts_in} channel; the change is in the "
                                f"{place['channel']} channel")
            elif any(d in place_scope and place_scope[d] != v for d, v in scope.items()):
                relation, fit = "conflict", 0.0
                text = f"scope {seg_text(scope)} does not include {where}"
                w = self.look(primary.metric, scope, inc.onset, end, skip) \
                    if self.is_count(primary.metric) or mon.has_series(primary.metric, scope) else None
                if w is not None:
                    state = "normal" if w["score"] < NORMAL_MAX_SURPRISE else "moved too"
                    text += f"; {primary.metric} inside its scope is {num(w['observed'])} against {num(w['expected'])} ({state})"
                evidence.append(text)
            elif scope == place_scope:
                relation, fit = "exact", 1.0
                evidence.append(f"scope {seg_text(scope)} is exactly where the change is")
            elif scope.items() < place_scope.items():
                relation, fit = "wider", 0.6
                evidence.append(f"scope {seg_text(scope)} is wider than the change, which is only in {where}")
            else:
                relation = "narrower"
                part = places.get(frozenset({**place, **scope}.items()))
                inside_share = part.share if part is not None else None
                fit = 0.6 if inside_share is not None and inside_share >= 0.8 else 0.2
                evidence.append(f"scope {seg_text(scope)} covers only part of {where}"
                                + (f": {inside_share:.0%} of the change lies inside it" if inside_share is not None else ""))
            if acts_in is not None and place.get("channel") == acts_in:
                evidence.append(f"a {r['system']} release acts in the {acts_in} channel, where the change is")

            # did the change start with the release?
            lag = int((inc.onset - ts) / HOUR)
            if -RELEASE_AFTER_HOURS <= lag <= RELEASE_BEFORE_HOURS:
                timing = 1.0
                evidence.append(f"released {when(ts)}, " + ("the hour the change began" if lag == 0 else
                                                           f"{abs(lag)} h {'before' if lag > 0 else 'after'} the estimated onset"))
            elif lag > RELEASE_BEFORE_HOURS:
                lean = self.look(primary.metric, segment, ts, inc.onset, skip)
                leans = lean is not None and lean["score"] >= FOLLOW_MIN_SURPRISE and (lean["observed"] < lean["expected"]) == falling
                timing = 0.8 if leans else 0.5 if lean is None else 0.1
                evidence.append(f"released {when(ts)}, {lag} h before the estimated onset; " + (
                    f"the hours in between already lean the same way ({num(lean['observed'])} against {num(lean['expected'])})"
                    if leans else "no data has arrived for the hours in between, so they cannot tell" if lean is None else
                    f"the hours in between were normal ({num(lean['observed'])} against {num(lean['expected'])})"))
            else:
                timing = 0.0
                evidence.append(f"released {when(ts)}, {-lag} h after the change began")

            evidence.append("supported needs the system linked, the scope exactly the place and the timing at the onset: "
                            f"linked {'yes' if link == 1.0 else 'partly' if link else 'no'}, scope {relation}, "
                            f"timing {'yes' if timing >= 0.8 else 'no'}")
            score = 0.35 * link + 0.35 * fit + 0.30 * timing
            if link == 0.0 or relation == "conflict" or timing <= 0.1 or \
                    (relation == "narrower" and (inside_share is None or inside_share < INSIDE_MIN_SHARE)):
                status = "rejected"
            elif link == 1.0 and relation == "exact" and timing >= 0.8:
                status = "supported"
            else:
                status = "plausible"
            h = {"cause": f"release {r['system']}: {description}", "kind": "release", "status": status, "score": round(score, 2),
                 "evidence": evidence, "_ts": ts, "_lag": abs(lag)}
            key = (r["system"], tuple(sorted(scope.items())), description)
            old = best.get(key)   # the same row twice (a repeated click on the same fault): the better placed one
            if old is None or (h["score"], -h["_lag"]) > (old["score"], -old["_lag"]):
                best[key] = h
        return list(best.values())

    def planned(self, event: dict, alerts: list[Alert]) -> dict:
        """Does a planned event explain what moved? A calendar entry (a promotion) predicts a rise in every
        metric the graph links to the calendar (visits, add-to-cart, platform-funded discounts), by at most
        PLANNED_MAX_LIFT; it predicts nothing for the other metrics. Supported only when every moved metric
        is linked and rose within that size; plausible when it fits the linked ones but others moved too."""
        what = event["description"] or event["kind"]
        evidence, fit, rose, unlinked = [], [], [], []
        for a in alerts:
            if not any(n.id == "calendar" for n in self.graph.context_of(a.metric)):
                unlinked.append(a.metric)
                continue
            effect = a.effect
            if not math.isfinite(effect) or effect <= 0:
                evidence.append(f"{what} predicts a rise in {a.metric}; it fell ({100 * effect:+.0f} %): the wrong direction")
                fit.append(False)
            elif effect > PLANNED_MAX_LIFT:
                evidence.append(f"{what} predicts a rise in {a.metric}; it rose {100 * effect:+.0f} %, more than the "
                                f"{100 * PLANNED_MAX_LIFT:+.0f} % a planned event is taken to give")
                fit.append(False)
            else:
                evidence.append(f"{what} predicts a rise in {a.metric}; it rose {100 * effect:+.0f} %, which fits in direction and size")
                fit.append(True)
                rose.append(f"{a.metric} {100 * effect:+.0f} %")
        if unlinked:
            evidence.append(f"the graph does not link the calendar to {', '.join(unlinked)}: the event predicts nothing there")
        if fit and all(fit):
            status, score = ("supported", 0.8) if not unlinked else ("plausible", 0.4)
        else:
            status, score = "rejected", 0.1
        reason = (f"{what} ({when(event['start'])} to {when(event['end'])}) predicts a rise in what moved and it rose "
                  f"within the {100 * PLANNED_MAX_LIFT:+.0f} % a planned event is taken to give: {', '.join(rose)}.")
        return {"status": status, "score": score, "evidence": evidence, "reason": reason}

    def channels(self) -> set[str]:
        """The values of the channel dimension: a release of a system with one of these names acts in that channel."""
        if not hasattr(self, "_channels"):
            self._channels = {str(v) for v in self.store.distinct("facts_hourly", "channel") if v is not None}
        return self._channels

    # --------------------------------------------------------------- the report
    def report(self, inc, tick: Tick) -> dict:
        graph, mon = self.graph, self.monitor
        alerts = sorted(inc.metrics.values(), key=lambda a: -a.score)
        end = inc.last_seen + HOUR
        volume_sources = {s for m in inc.metrics if self.is_count(m) for s in graph.sources(m)}
        held = sorted(h for h, ms in tick.held.items() if set(ms) & set(inc.metrics))
        held = sorted(set(held) | {h for h, srcs in tick.late.items() if set(srcs) & volume_sources})
        trail, uncertain = [], []

        # 1. what moved: causes before symptoms
        symptom_of = self.symptoms(alerts)
        primary = next((a for a in alerts if a.metric not in symptom_of), alerts[0])
        direction = "fell" if primary.observed < primary.expected else "rose"
        is_volume = primary.kind == "volume"
        per = " per hour" if is_volume else ""
        counts = (f"the same hours of earlier weeks" if is_volume else
                  f"{num(primary.numerator)} of {num(primary.denominator)}" if primary.kind == "proportion" else
                  f"{num(primary.numerator)} / {num(primary.denominator)}")
        trail.append({
            "action": "watch", "target": primary.metric,
            "result": f"{num(primary.observed)}{per} against an expected {num(primary.expected)}{per} in {seg_text(primary.segment)} "
                      f"({counts}; newest {min(primary.hours, 3)} bad hour(s); surprise 10^-{primary.score:.0f}, z {primary.z:+.1f})"})
        diluted = sorted({seg_text(a.segment) for a in tick.alerts
                          if a.metric == primary.metric and a.segment != primary.segment
                          and compatible(a.segment, primary.segment)})
        trail.append({"action": "compare", "target": "watched series",
                      "result": f"strongest in {seg_text(primary.segment)}" + (
                          f"; weaker in {', '.join(diluted[:6])}" if diluted else "; no other series of this metric moved")})

        # 2. where
        phase = self.phases(primary.metric, inc.segment, inc.onset, end, held)
        since = phase["begin"]
        turned = ""     # a rise, then a fall (or the reverse): said in the report
        if phase["before"] and not phase["rising"]:
            spec = graph.specs[primary.metric]
            name = (spec.description.split(" - ")[0].rstrip(".") or primary.metric) if spec.description else primary.metric
            turned = f" Over the incident, {name[0].lower() + name[1:]} first rose (x{phase['factor']:.1f} for {phase['hours']} h), then fell"
            turned += (": spending ran ahead of schedule and the budget ran out." if set(spec.systems) & set(PACED_SYSTEMS) else ".")
        choice, places, cells = self.localize(primary.metric, inc.segment, since, end, held)
        place = dict(choice.place.segment) if choice is not None else dict(inc.segment)
        segment = place if mon.has_series(primary.metric, place) else dict(inc.segment)   # where rates can be compared
        where = seg_text(place)
        own = choice.place if choice is not None and place != primary.segment else None   # the headline rate is another series'
        seen_in = f" in the watched series {seg_text(primary.segment)}" if own is not None else ""
        obs, exp = (own.observed, own.expected) if own is not None else (primary.observed, primary.expected)
        localization = []
        if choice is not None:
            ranked = sorted(places.values(), key=lambda p: (-p.score, -len(p.segment)))
            shown = [choice.place] + [p for p in ranked if p is not choice.place][:PLACES_SHOWN - 1]
            localization = [{"dimension": " x ".join(ordered(p.segment)) or "all", "segment": seg_text(p.segment),
                             "share_of_delta": p.share, "surprise": p.score, "observed": p.observed,
                             "expected": p.expected, "chosen": p is choice.place} for p in shown]
            text = (f"{len(places)} places scored on {len(cells['keys'])} finest cells; {where} holds "
                    f"{choice.place.share:.0%} of the deviation (gain {choice.place.gain:.0f})")
            if since > inc.onset:
                text += f"; read from the hours since {when(since)}, when the rate changed direction"
            if choice.verdict == "refined":
                how = "narrowed" if inc.segment.items() < place.items() else \
                    "widened" if place.items() < inc.segment.items() else "moved"
                text += f"; {how} from the monitor's series {seg_text(inc.segment)}"
            if choice.rival is not None:
                text += f"; best different place {seg_text(choice.rival.segment)} (gain {choice.rival.gain:.0f})"
            trail.append({"action": "drill", "target": primary.metric, "result": text})
            if ranked[0] is not choice.place and choice.verdict == "kept":
                uncertain.append(f"The scan's best place is {seg_text(ranked[0].segment)}, not {where}: "
                                 "another change may be running there, or the place is wider than reported.")
            elif not choice.clear:
                uncertain.append(f"Place: {seg_text(choice.rival.segment)} explains the counts almost as well as {where}.")
        elif is_volume:
            trail.append({"action": "drill", "target": primary.metric, "result": "a volume is placed by the monitor's cut "
                          f"({seg_text(inc.segment)}); the finest cells are scanned for rates only"})
        else:
            trail.append({"action": "drill", "target": primary.metric, "result": "no cell data for this window; the monitor's series is reported"})

        for a in alerts:
            if a is primary:
                continue
            via = symptom_of.get(a.metric)
            trail.append({"action": "watch", "target": a.metric,
                          "result": f"also moved in {seg_text(a.segment)}: {num(a.observed)} against {num(a.expected)} (z {a.z:+.1f})"
                                    + (f"; the graph says {via} may affect it, so it is read as a symptom" if via else "")})
        followed = self.follow(alerts, segment, inc.onset, end, held)
        for f in followed:
            if f["metric"] in inc.metrics or not f["shown"]:
                continue   # it has its own "also moved" line, or is not clear yet
            trail.append({"action": "follow", "target": f["metric"],
                          "result": f"{f['via']} may affect it ({f['note'] or 'link in the graph'}): "
                                    f"{num(f['observed'])} against {num(f['expected'])} in {seg_text(segment)} over the incident "
                                    f"(z {f['z']:+.1f}), "
                                    + ("the direction the link predicts" if f["sign"] else "the link predicts no direction")})
        # top-down, as the brief walks it: every factor of the chain the moved rates feed, traffic first
        checked = self.check(list(inc.metrics) + [f["metric"] for f in followed if f["shown"]], segment, since, end, held)
        check_line, check_sentence = self.check_text(checked) if checked is not None else ("", "")
        chain_moved = [r for r in (checked["rows"] if checked is not None else []) if r["state"] == "moved"]
        if is_volume:
            # a count is a product of factors: what it cost is what its moved factors cost (traffic when sessions
            # fell and the rates did not), so they enter the decomposition like followed rates
            followed = followed + [{"metric": r["metric"], "expected": r["expected"], "observed": r["observed"],
                                    "z": r["z"], "via": primary.metric, "sign": 0, "shown": True, "note": ""}
                                   for r in chain_moved if r["metric"] not in inc.metrics]

        body = {
            "id": inc.id, "status": inc.status,
            "first_seen": iso_hour(inc.first_seen), "last_seen": iso_hour(inc.last_seen),
            "metric": primary.metric, "segment": ordered(place),
            "fingerprint": f"{primary.metric}|{seg_text(inc.segment)}",
            "change": {"observed": primary.observed, "expected": primary.expected,
                       "delta_pct": 100 * primary.effect, "z": primary.z,
                       "window": {"start": iso_hour(inc.onset), "end": iso_hour(end)}},
            "drivers": [{"metric": a.metric, "segment": ordered(a.segment), "observed": a.observed,
                         "expected": a.expected, "delta_pct": 100 * a.effect, "z": a.z, "surprise": a.score,
                         "test": a.kind, "onset": iso_hour(a.onset),
                         "role": f"symptom of {symptom_of[a.metric]}" if a.metric in symptom_of else "cause candidate"}
                        for a in alerts],
            "localization": localization,
        }

        # 3. what it cost
        waiting = sorted(tick.late)      # hours with any source still missing are in no sum
        cost = self.explain(alerts, followed, segment, inc.onset, end, waiting, getattr(inc, "frozen", None))
        loss = ""
        if cost is not None:
            outcome, unit, effect = cost["outcome"], cost["unit"], cost["effect"]
            restored = cost["actual"] - effect      # the outcome with the moved rates back at their expected values
            body["metric"] = outcome
            body["change"] = {"observed": cost["actual"], "expected": restored, "delta": effect,
                              "delta_pct": 100 * effect / restored if restored else None,
                              "window": {"start": iso_hour(inc.onset), "end": iso_hour(end)}}
            if segment != place:
                body["change"]["segment"] = ordered(segment)
                uncertain.append(f"The effect is measured in {seg_text(segment)}, the nearest series the monitor keeps a "
                                 f"baseline for; it contains {where}.")
            body["impact"] = {f"{outcome}_{unit}" if unit != "count" else outcome: effect, **cost["counted"]}
            body["contributions"] = cost["contributions"]
            if cost["explained"] is not None:
                body["explained_share"] = cost["explained"]
            rest = cost["gap"] - effect
            trail.append({"action": "decompose", "target": outcome, "result": (
                f"{num(cost['actual'], 4)} observed in {seg_text(segment)} since {when(inc.onset)}; with "
                f"{', '.join(cost['moved'])} back at the expected rate it would be {num(restored, 4)}: "
                + ", ".join(f"{c['node']} {c['delta']:+,.0f} {unit}" for c in cost["contributions"])
                + f" ({cost['method']} over every path of the formulas). Against the state with every judged rate at its "
                  f"baseline the gap is {cost['gap']:+,.0f} {unit}, so the other rates account for {rest:+,.0f}"
                + (" (largest: " + ", ".join(f"{m} {v:+,.0f}" for m, v in cost["others"][:3]) + ")" if cost["others"] else "")
                + (f"; {len(waiting)} hour(s) still waiting for data are left out" if waiting else ""))})
            for m in cost["unclear"]:
                f = next(x for x in followed if x["metric"] == m)
                uncertain.append(f"The effect so far runs partly through {m}, which the link from {f['via']} predicts but the "
                                 f"data does not show yet ({num(f['observed'])} against {num(f['expected'])}, z {f['z']:+.1f}): "
                                 "the amount, even its sign, can still change.")
            also = "".join(f", {d:+,.0f} {m}" for m, d in cost["counted"].items())
            loss = f" Effect on {outcome} in {seg_text(segment)} so far: {effect:+,.0f} {unit}{also}."
            if turned.endswith("ran out."):
                loss += " The extra ad revenue of the first phase is budget spent early, not earned: the hours after it go without."
        elif any(m in tree_metrics(graph, o) for o in graph.top_outcomes()[:1] for m in inc.metrics):
            uncertain.append("The effect on the outcome could not be computed for this window: a factor of the formulas "
                             "has no value or no baseline there.")
        if checked is not None:
            trail.append({"action": "check", "target": f"factors of {checked['node']}", "result": check_line})

        # 4. why
        trail.append({"action": "check", "target": "freshness",
                      "result": "all sources for these metrics have arrived" if not held else
                      "hours still waiting for data: " + ", ".join(when(h) for h in held)})
        covering = [e for e in tick.events
                    if e["start"] < end and inc.onset < e["end"]
                    and (e["country"] is None or place.get("country") in (None, e["country"]))]
        for a in alerts:   # the monitor's own cover, which also takes the hours just after an event
            x = a.explained_by
            if x is not None and not any(iso_hour(e["start"]) == x["start"] and e["country"] == x["country"] for e in covering):
                covering.append({"start": pd.Timestamp(x["start"]), "end": pd.Timestamp(x["end"]), "country": x["country"],
                                 "kind": x["kind"], "description": x["description"]})
        # the monitor already judged a covering event that does not fit (direction or size): its verdict stands,
        # so an event the monitor did not accept can never make the incident innocent here
        partial = {}
        for a in alerts:
            x = getattr(a, "partial_explanation", None)
            if not x:
                continue
            key = (iso_hour(pd.Timestamp(x["start"])), x.get("country"))
            partial[key] = x.get("why") or "the change does not fit the event in direction or size"
            if not any(iso_hour(e["start"]) == key[0] and e["country"] == key[1] for e in covering):
                covering.append({"start": pd.Timestamp(x["start"]), "end": pd.Timestamp(x["end"]), "country": x.get("country"),
                                 "kind": x.get("kind"), "description": x.get("description")})

        def fit_of(e):
            why = partial.get((iso_hour(e["start"]), e["country"]))
            if why is None:
                return self.planned(e, alerts)
            return {"status": "rejected", "score": 0.1, "reason": why,
                    "evidence": [f"the monitor raised this change although the event covers it: {why}"]}
        events = [(e, fit_of(e)) for e in covering]
        trail.append({"action": "check", "target": "calendar", "result": (
            f"no planned event covers {where} in these hours" if not events else
            "; ".join(f"{e['description'] or e['kind']} ({when(e['start'])} to {when(e['end'])}) overlaps: "
                      + "; ".join(fit["evidence"]) + f" ({fit['status']})" for e, fit in events))})

        moved = list(inc.metrics) + [f["metric"] for f in followed if f["shown"] and f["metric"] not in inc.metrics]
        candidates = self.releases(inc, tick, primary, moved, place, segment, end, places, held)
        for h in sorted(candidates, key=lambda h: h["_ts"]):
            trail.append({"action": "check", "target": h["cause"].split(":")[0].replace("release ", "release:"),
                          "result": f"{h['cause'].split(': ', 1)[-1]}: {h['status']} (score {h['score']:.2f}); " + "; ".join(h["evidence"])})
        if not candidates:
            trail.append({"action": "check", "target": "releases", "result": (
                f"no release in the {RELEASE_LOOKBACK_HOURS} h before the onset or since"
                + (f" ({self.rollbacks_skipped} rollback row(s) in that time end a release and are not causes)"
                   if self.rollbacks_skipped else ""))})
        kept = [h for h in candidates if h["status"] != "rejected"]
        rejected = sorted((h for h in candidates if h["status"] == "rejected"), key=lambda h: -h["score"])[:MAX_REJECTED_RELEASES]
        hypotheses = kept + rejected

        if not any(h["status"] == "supported" for h in hypotheses):
            # the moved metric itself is the first cause when a link of the graph that has a direction
            # predicted a second movement and that movement is there; otherwise it stays a candidate
            confirmed = [f"{f['metric']} moved as the link from {primary.metric} predicts: {num(f['observed'])} "
                         f"against {num(f['expected'])} (z {f['z']:+.1f})"
                         for f in followed if f["via"] == primary.metric and f["sign"] and f["shown"]]
            if confirmed:
                inc.confirmed = confirmed   # once confirmed it stays so while the incident is open: a later
            if is_volume and chain_moved:   # tick with a thinner window must not take the label back
                # a count: the moved factor of its chain is what changed (traffic when sessions moved and the rates did not)
                first = chain_moved[0]
                factor = "traffic" if self.label(first["metric"]) != first["metric"] else first["metric"]
                rates_normal = [r["metric"] for r in checked["rows"] if r["state"] == "normal" and not self.is_count(r["metric"])]
                hypotheses.append({
                    "cause": f"{factor} {'fell' if first['effect'] < 0 else 'rose'} in {where}"
                             + ("" if factor == first["metric"] else f" ({first['metric']} {100 * first['effect']:+.0f} %)")
                             + ("; every rate of the chain stayed normal" if len(chain_moved) == 1 and rates_normal else ""),
                    "kind": "metric", "status": "plausible", "score": 0.4,
                    "evidence": [f"{primary.metric} {num(primary.observed)} per hour against {num(primary.expected)} "
                                 f"(the same hours of earlier weeks), z {primary.z:+.1f}",
                                 f"of the factors of {checked['node']}, only "
                                 + ", ".join(f"{r['metric']} ({100 * r['effect']:+.0f} %)" for r in chain_moved) + " moved",
                                 "no release, planned event or late source fits"]})
            else:
                # a planned event that fits explains the follow-on movement too: the metric is then no first cause
                confirmed_here = bool(inc.confirmed) and not any(fit["status"] == "supported" for _, fit in events)
                records = [UPSTREAM_RECORDS[s] for s in graph.specs[primary.metric].systems if s in UPSTREAM_RECORDS]
                if records:
                    # the last cause the data can show: say so, and say what would show the one before it
                    level = next((n for n in self._origins(primary.metric)[1:] if self.graph.out_edges(n, "may_affect")),
                                 primary.metric)
                    cause = (f"{level} {direction} in {where} ({primary.metric} {num(primary.observed)} against "
                             f"{num(primary.expected)}); no first cause beyond them is in the data "
                             f"(the simulator has no {' or '.join(records)})")
                    uncertain.append(f"What made {level} {'fall' if direction == 'fell' else 'rise'} cannot be told: "
                                     f"{' and '.join(records)} are not in the data, so a late delivery cannot be told "
                                     "from too small an order.")
                else:
                    cause = f"{primary.metric} {direction} in {where} without a matching release" + ("" if covering else " or planned event")
                hypotheses.append({
                    "cause": cause,
                    "kind": "metric", "status": "supported" if confirmed_here else "plausible",
                    "score": 0.7 if confirmed_here else 0.4,
                    "evidence": [f"{num(primary.observed)} against {num(primary.expected)} expected{seen_in}, z {primary.z:+.1f}"]
                    + inc.confirmed})

        for e, fit in events:
            hypotheses.append({
                "cause": f"planned event: {e['description'] or e['kind']}", "kind": "calendar", "status": fit["status"],
                "score": fit["score"], "_fit": fit,
                "evidence": [f"{when(e['start'])} to {when(e['end'])} overlaps the incident in {where}"] + fit["evidence"]})
        hypotheses.append({
            "cause": "late or missing data", "kind": "freshness", "status": "rejected", "score": 0.0,
            "evidence": ["every source these metrics depend on had arrived for the hours of the incident" if not held else
                         f"{len(held)} hour(s) are still waiting for data and are left out of every comparison above; "
                         "the incident rests on the hours that have arrived"]})
        split = self.mix(cells, segment) if cells is not None else None
        mix_h = None
        if split is not None:
            mix_h = {
                "cause": f"a shift in the mix of segments inside {seg_text(segment)}, with unchanged rates", "kind": "metric",
                "status": "rejected" if split["share"] < MIX_MAX_SHARE else
                "supported" if split["share"] >= MIX_EXPLAINS_SHARE else "plausible",
                "score": round(0.5 * split["share"], 2),
                "evidence": [f"{primary.metric}: the cells' own rates account for {split['rate']:+.4f}, "
                             f"the changed weights of the cells for {split['mix']:+.4f} ({split['share']:.0%} of the change)",
                             f"supported when the weights explain at least {MIX_EXPLAINS_SHARE:.0%} of the change"]}
            hypotheses.append(mix_h)

        rank = {"supported": 0, "plausible": 1, "rejected": 2}
        hypotheses.sort(key=lambda h: (rank[h["status"]], -h["score"], h.get("_lag", 0)))
        fits = {id(h): h.pop("_fit", None) for h in hypotheses}
        for h in hypotheses:
            h.pop("_ts", None)
            h.pop("_lag", None)
        # a mix shift that explains the change outranks "the moved metric itself": that hypothesis only restates the
        # change, and the links it follows move with the mix too. A supported release still comes first.
        if mix_h is not None and mix_h["status"] == "supported" and \
                not any(h["kind"] == "release" and h["status"] == "supported" for h in hypotheses):
            hypotheses.remove(mix_h)
            hypotheses.insert(0, mix_h)
        lead = hypotheses[0]
        # innocent: the leading supported explanation is a mix shift or a planned event, and no release fits
        innocent = None
        if lead["status"] == "supported" and not any(h["kind"] == "release" and h["status"] == "supported" for h in hypotheses):
            if lead is mix_h:
                innocent = {"kind": "mix", "reason": (
                    f"A shift in the mix of segments inside {seg_text(segment)} explains {split['share']:.0%} of the change "
                    f"in {primary.metric}: the changed weights of the cells account for {split['mix']:+.4f}, the cells' own "
                    f"rates for {split['rate']:+.4f}.")}
            elif lead["kind"] == "calendar" and fits.get(id(lead)) is not None:
                innocent = {"kind": "calendar", "reason": fits[id(lead)]["reason"]}
        if innocent is not None:
            body["innocent"] = innocent
        second = hypotheses[1] if len(hypotheses) > 1 else None
        trail.append({"action": "compare", "target": "explanations", "result": (
            f"{len(hypotheses)} tested: " + "; ".join(f"{h['cause']} - {h['status']} ({h['score']:.2f})" for h in hypotheses[:6])
            + ("; ..." if len(hypotheses) > 6 else ""))})
        standing = [h for h in hypotheses[1:] if h["status"] != "rejected"]
        rival = max(standing, key=lambda h: h["score"]) if standing else None
        if lead["status"] != "supported":
            verdict = "no explanation is supported yet: every candidate was tested, the leading one stays a candidate"
        elif rival is None:
            verdict = "the leading explanation is supported and every other one was rejected"
        elif lead["score"] > rival["score"]:
            verdict = f"the leading explanation is supported and ahead of the next one standing by {lead['score'] - rival['score']:.2f}"
        else:
            verdict = (f"the leading explanation is supported, but {rival['cause']} ({rival['status']}) scores "
                       f"{rival['score']:.2f} against {lead['score']:.2f}")
        trail.append({"action": "stop", "target": "investigation", "result": verdict})

        # 5. what stays open
        if primary.kind != "volume" and \
                (primary.observed - primary.expected) * (primary.episode_observed - primary.expected) < 0:
            uncertain.append(f"{primary.metric} changed direction during the incident; the effect so far nets both phases.")
        if primary.hours < 2 and inc.status == "open":
            uncertain.append("Only one hour of evidence so far.")
        if sum(h["status"] == "supported" for h in hypotheses) > 1:
            uncertain.append("More than one explanation fits in system, place and time.")
        counted = [r for r in (checked["rows"] if checked is not None else []) if self.is_count(r["metric"])]
        if cost is not None and not counted:
            uncertain.append("Traffic is taken as given: only rates are judged, so a change in sessions is not part of the effect.")
        for r in counted:
            if r["state"] == "moved" and (cost is None or r["metric"] not in cost["moved"]):
                uncertain.append(f"{self.label(r['metric']).capitalize()} moved too ({100 * r['effect']:+.0f} % against the same "
                                 "hours of earlier weeks); the effect above counts only the moved rates.")
            elif r["state"] == "no baseline":
                uncertain.append(f"{r['metric']} has too little history here to say whether it moved.")
        traffic_moved = [r for r in chain_moved if self.is_count(r["metric"]) and self.label(r["metric"]) != r["metric"]]
        if is_volume and lead["status"] != "supported" and traffic_moved:
            uncertain.append(
                "What removed the traffic is not in the data: a tracking loss, an app store problem or an outage "
                "would all look like this." if traffic_moved[0]["effect"] < 0 else
                "What brought the traffic is not in the data: a campaign missing from the calendar, bots or "
                "double-counted tracking would all look like this.")

        body["hypotheses"] = hypotheses
        body["trail"] = trail
        body["uncertain"] = uncertain
        if innocent is not None:
            verdict_text = f"Most likely cause: {lead['cause']}. {innocent['reason']} An innocent change, not a fault."
        elif lead["status"] == "supported":
            verdict_text = f"Most likely cause: {lead['cause']}."
        elif is_volume and traffic_moved:
            verdict_text = (f"No cause is confirmed: traffic {'fell' if traffic_moved[0]['effect'] < 0 else 'rose'} in {where}; "
                            "no release, event or late source fits.")
        else:
            verdict_text = "No cause is confirmed yet."
        body["report"] = (
            f"{primary.metric} {'fell' if obs < exp else 'rose'} to {num(obs)}{per} from an expected {num(exp)}{per} in "
            f"{where}, starting {when(inc.onset)}.{turned}{loss}{check_sentence} "
            + verdict_text
            + (f" {sum(h['status'] == 'rejected' for h in hypotheses)} other explanation(s) were tested and rejected."
               if any(h["status"] == "rejected" for h in hypotheses) else ""))
        return body
