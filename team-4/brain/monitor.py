"""The monitor: every tick it judges the newest hours of every watched rate in every watched cut,
and the volumes (sessions, orders) in the coarser cuts.

What is watched comes from the graph: every atomic rate (a ratio of two columns), on the formula
tree or not. Money outcomes such as contribution are not judged directly, because a fault in one
cell is lost in their noise; they are used for the impact once something has moved
(brain/impact.py). Sessions and orders are judged as counts (below): a drop in traffic or orders
with unchanged rates moves no rate at all.

For a rate in a series (one cut of the dimensions, one value combination):

- expected rate = pooled numerator / pooled denominator over the visible history of that series,
  leaving out the hours being judged, hours already flagged as bad, hours covered by a calendar
  event and hours whose data has not arrived;
- an hour is bad when its counts, alone or summed with the two hours before, are too surprising
  against that rate (brain/stats.py) and the rate moved by at least `min_effect` (a proportion above
  0.5: or its complement by `complement_min_effect`, see COMPLEMENT_MIN_EFFECT); an hour of a bad
  summed window is marked bad only when it alone moved that way at p below 0.01, so an episode ends
  at its last faulty hour and its newest-hours rate is the fault's rate;
- hours whose sources have not arrived are held: not judged, not healthy;
- the last hours are re-read on every tick, so a rewritten past is seen too;
- a calendar event explains an alert only when the change goes the way the event predicts and is
  not much larger than earlier events of that kind and place did (EVENT_EFFECTS); otherwise the
  alert is raised with the event as `partial_explanation`.

Volumes (sessions, orders) are judged as counts, in the whole market, per country, per platform and
per country x platform, because an outage or a lost app-store listing removes visits and orders
without moving any rate:

- expected count of an hour = mean of the same hour of the week over the previous `volume_weeks`
  weeks (this absorbs the daily and weekly cycle), leaving out weeks that were flagged, covered by a
  calendar event or part of an open incident;
- the spread is negative-binomial: variance = (mu + a * mu^2) * (1 + 1/k) for a mean of k weeks, with
  the extra-Poisson part `a` estimated per series from how the same hour differed between weeks;
- an hour is bad on the same rules as a rate (p below 1e-7 alone or summed with the two hours
  before), and the count moved by at least `volume_min_effect` (20 %);
- an orders alert is dropped when the sessions or a rate in the same place already moved in that
  episode: the orders are then the symptom of that change, not a second problem.

The monitor reports alerts; brain/incidents.py turns them into incident files.
"""
from __future__ import annotations

import warnings
from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from scipy import stats as sps

from brain.clock import HOUR, Clock
from brain.data import Store, iso_hour
from brain.evaluator import Evaluator
from brain.graph import MetricGraph
from brain.metric_spec import DIMENSIONS
from brain.stats import LN10, _z_from_log_tail, binomial_surprise, ratio_surprise, robust_scale, scaled_residual

MIN_DISPERSION_HOURS = 48
CELL_PRIOR_VOLUME = 20.0   # a finest cell's own history counts against this much of the pooled rate
WEEK = 168                 # hours

CUTS: tuple[tuple[str, ...], ...] = (
    (), ("country",), ("platform",), ("category",), ("channel",),
    ("country", "platform"), ("country", "category"),
)
VOLUMES: tuple[str, ...] = ("sessions", "orders")
VOLUME_CUTS: tuple[tuple[str, ...], ...] = ((), ("country",), ("platform",), ("country", "platform"))
VOLUME_MIN_SLOTS = 24       # hours of the week seen in at least two weeks before `a` is trusted
VOLUME_MIN_DISPERSION = 1e-4  # a: never assume counts steadier than a 1 % hour-to-hour spread
VOLUME_TRIM = 5.0           # a week-to-week spread this many times the fitted one is left out of the fit

# What a planned event does, by kind of calendar row: the metrics it moves and which way (+1 up, -1 down).
# A promo brings more visits and orders, more add-to-carts per view, and gives more away. A calendar
# row explains an alert only when the change goes this way and is no larger than EVENT_SIZE_FACTOR
# times the largest effect earlier events of that kind and place had on that series (when there were
# any); otherwise the alert is raised and the row is kept as `partial_explanation`. A kind or metric
# missing here is not checked for direction.
EVENT_EFFECTS: dict[str, dict[str, int]] = {
    "promo": {"sessions": 1, "orders": 1, "add_to_cart_rate": 1, "discount_rate": 1},
}
EVENT_SIZE_FACTOR = 2.0

# The effect floor of a proportion is judged on its smaller side. Near 1 the rate itself hardly moves
# when its complement does: payment failures doubling from 6 % to 14 % is an 8.5 % fall of the success
# rate, under the 10 % floor. So for a proportion (or the surplus of an excess) whose expected rate is
# above 0.5, the floor is also passed when the complement moved by this share of itself, (1 - observed)
# / (1 - expected) - 1; the binomial tests (p below 1e-7, 1e-4 after a release) are unchanged. The
# floor is passed when either side moved enough, so no series is judged more strictly than before
# (checkout_rate sits at 0.49-0.51).
# Why 50 % and not 10 %: payment failures differ between series, 3.8-7.7 % against 5.7 % in the whole
# market (built-on seeds 4 and 80), so a shift in the mix alone can move an aggregate's failure rate by
# up to about 35 % with no fault anywhere; at 10 % only the binomial test would stand against that.
# 50 % stays above it and well below a real fault: the small payment stress case moves failures by
# +133 % (+103 % in its weakest alerting window). On the ten built-on seeds 10 %, 25 % and 50 % all
# kept 0 false alarms and 120/120 found, and all three passed the same ten of the eleven stress cases.
COMPLEMENT_MIN_EFFECT = 0.50


@dataclass(frozen=True)
class Config:
    lookback_hours: int = 21 * 24     # history a baseline may use
    reread_hours: int = 8             # newest hours re-read and re-judged on every tick
    min_history_hours: int = 72       # usable hours a series needs before it is judged
    min_history_volume: float = 200   # and this much pooled denominator
    windows: tuple[int, ...] = (1, 3)  # an hour alone, and summed with the two before it
    min_surprise: float = 7.0         # proportions: two-sided exact p below 1e-7
    min_z: float = 6.0                # other ratios: residual beyond 6 historical spreads,
    beyond_history: float = 1.25      # and this many times the largest residual clean history showed,
    min_ratio_volume: float = 30      # on at least this much denominator (small counts are too lumpy)
    min_effect: float = 0.10          # and the rate itself moved by at least 10 %,
    complement_min_effect: float = COMPLEMENT_MIN_EFFECT   # or, above 0.5, its complement by this much
    onset_min_surprise: float = 2.0   # an earlier hour of a summed window joins the episode at p below 0.01
    active_hours: int = 3             # an alert is live while its last bad hour is this recent
    episode_gap_hours: int = 2        # bad hours this close belong to one episode
    release_watch_hours: int = 24     # after a release, its metrics are tested in its scope this long
    release_min_surprise: float = 4.0  # one question per release and metric: p below 1e-4 is enough
    cuts: tuple[tuple[str, ...], ...] = CUTS
    volumes: tuple[str, ...] = VOLUMES        # counts judged against the same hour of earlier weeks
    volume_cuts: tuple[tuple[str, ...], ...] = VOLUME_CUTS
    volume_weeks: int = 3                     # the expected count: mean of the same hour over this many weeks
    volume_min_effect: float = 0.20           # and the count moved by at least 20 %
    volume_min_expected: float = 30.0         # on at least this many expected events in the window


@dataclass(frozen=True)
class Watched:
    metric: str
    numerator: str      # column
    denominator: str    # column
    sources: tuple[str, ...]
    calendar: bool      # the graph links the metric to the event calendar
    counts: bool        # numerator and denominator are both counts of events


@dataclass(frozen=True)
class Volume:
    metric: str
    column: str
    sources: tuple[str, ...]   # judged only when these have arrived (for orders: every watched rate's)


@dataclass
class Alert:
    metric: str
    kind: str                 # "proportion", "excess" or "ratio": the test that was used; "volume" for a count
    segment: dict
    onset: pd.Timestamp       # first hour of the episode
    last_bad: pd.Timestamp    # newest bad hour
    hours: int                # bad hours in the episode
    observed: float           # rate now: over the newest bad hours (at most three); a volume: count per hour
    expected: float           # baseline rate; a volume: expected count per hour over the same hours
    numerator: float          # counts behind `observed`; a volume: observed count summed over those hours
    denominator: float        # a volume: expected count summed over those hours
    episode_observed: float   # rate over the whole episode; a volume: count per hour over the episode
    score: float              # strongest surprise in the episode, -log10 p
    z: float                  # normal-equivalent of that
    explained_by: dict | None = None   # a calendar row that covers it and fits it (direction and size)
    partial_explanation: dict | None = None   # a covering calendar row that does not fit it, with "why"
    trigger: str = "scan"              # "scan": the hourly scan; "release": the watch after a release

    @property
    def effect(self) -> float:
        return self.observed / self.expected - 1.0 if self.expected else float("nan")


@dataclass
class Tick:
    now: pd.Timestamp | None
    newest: pd.Timestamp | None
    alerts: list[Alert] = field(default_factory=list)
    late: dict = field(default_factory=dict)        # hour -> sources not arrived
    held: dict = field(default_factory=dict)        # hour -> metrics not judged because of that
    judged_series: int = 0
    events: list = field(default_factory=list)      # calendar rows visible at this tick


def floor_moved(observed, rate, shift: float, kind: str, cfg: Config):
    """How far a rate moved, in units of its effect floor (an absolute value of 1 or more passes), signed
    the way the rate moved. `observed` and `rate` are on the tested scale (the surplus for an excess).

    The rate itself counts against cfg.min_effect. A proportion (or excess surplus) expected above 0.5
    also counts its complement against cfg.complement_min_effect, and the larger of the two is kept:
    both have the sign of observed - rate, so the direction is the rate's either way."""
    moved = ((observed + shift) / (rate + shift) - 1.0) / cfg.min_effect
    if kind not in ("proportion", "excess"):
        return moved
    high = np.asarray(rate) > 0.5
    if not high.any():
        return moved
    comp = np.where(high, (observed - rate) / np.where(high, 1.0 - rate, 1.0), 0.0) / cfg.complement_min_effect
    return np.where(np.abs(comp) > np.abs(moved), comp, moved)


def compatible(a: dict, b: dict) -> bool:
    """Two segments can describe the same place: no shared dimension disagrees."""
    return all(b[d] == v for d, v in a.items() if d in b)


def nested(a: dict, b: dict) -> bool:
    """One place lies inside the other (or they are the same place)."""
    return a.items() <= b.items() or b.items() <= a.items()


def leading(alerts: list[Alert]) -> list[Alert]:
    """Per metric, the strongest alert of each place: a fault in one cell also shows, diluted, in
    every series that contains the cell, and weaker in every series that splits it."""
    out: list[Alert] = []
    for a in sorted(alerts, key=lambda a: (-a.score, -len(a.segment))):   # on a tie the narrower place leads
        if not any(k.metric == a.metric and compatible(a.segment, k.segment) for k in out):
            out.append(a)
    return out


def watch_list(graph: MetricGraph) -> list[Watched]:
    out = []
    for name, spec in graph.specs.items():
        if not spec.atomic or spec.formula is None or spec.formula.kind != "ratio":
            continue
        num, den = spec.formula.ratio_parts()
        if len(num) != 1 or len(den) != 1:
            continue
        ncol, dcol = graph.specs[num[0]].column, graph.specs[den[0]].column
        if not ncol or not dcol:
            continue
        calendar = any(n.id == "calendar" for n in graph.context_of(name))
        counts = graph.specs[num[0]].unit == "count" and graph.specs[den[0]].unit == "count"
        out.append(Watched(name, ncol, dcol, tuple(graph.sources(name)), calendar, counts))
    return out


def volume_list(graph: MetricGraph, watched: list[Watched], names=VOLUMES) -> list[Volume]:
    """The counts judged as volumes. Orders wait for every source a rate is read from: an orders
    change is judged only once the rates that could explain it are judged too."""
    every = {s for w in watched for s in w.sources}
    out = []
    for name in names:
        spec = graph.specs.get(name)
        if spec is None or not spec.column:
            continue
        own = set(graph.sources(name))
        out.append(Volume(name, spec.column, tuple(sorted(own if name == "sessions" else own | every))))
    return out


def count_surprise(x, mean, var):
    """Two-sided tail of a count against a negative binomial with this mean and variance
    (variance above the mean: extra-Poisson spread). Returns (surprise, z) like brain/stats.py."""
    x, mean, var = (np.asarray(a, dtype=float) for a in (x, mean, var))
    ok = np.isfinite(x) & np.isfinite(mean) & np.isfinite(var) & (mean > 0) & (var > mean)
    xx, mm, vv = np.where(ok, np.round(x), 0.0), np.where(ok, mean, 1.0), np.where(ok, var, 2.0)
    xx = np.maximum(xx, 0.0)
    r = mm ** 2 / (vv - mm)
    p = r / (r + mm)
    with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
        lower = sps.nbinom.logcdf(xx, r, p)
        upper = sps.nbinom.logsf(xx - 1, r, p)
        # far in the tail the exact value underflows: the normal tail keeps it finite and ordered
        approx = sps.norm.logsf(np.abs(xx - mm) / np.sqrt(vv))
    lower = np.where(np.isfinite(lower), lower, np.where(xx < mm, approx, 0.0))
    upper = np.where(np.isfinite(upper), upper, np.where(xx > mm, approx, 0.0))
    tail = np.minimum(np.minimum(lower, upper), 0.0)
    surprise = np.maximum(-(tail + np.log(2.0)) / LN10, 0.0)
    z = _z_from_log_tail(np.minimum(tail + np.log(2.0), 0.0) - np.log(2.0), np.where(upper < lower, 1.0, -1.0))
    z = np.where(xx == np.round(mm), 0.0, z)
    return np.where(ok, surprise, np.nan), np.where(ok, z, np.nan)


class Cube:
    """Hourly sums of the base columns for one cut, as arrays [hour, series]."""

    def __init__(self, dims: tuple[str, ...], columns: list[str]):
        self.dims = tuple(dims)
        self.columns = list(columns)
        self.t0: pd.Timestamp | None = None
        self.rows = 0
        self.keys: list[tuple] = []
        self._kidx: dict[tuple, int] = {}
        self.data = {c: np.full((0, 0), np.nan) for c in columns}

    def row(self, ts) -> int:
        return int((pd.Timestamp(ts) - self.t0) / HOUR)

    def hour(self, row: int) -> pd.Timestamp:
        return self.t0 + row * HOUR

    def segment(self, k: int) -> dict:
        return dict(zip(self.dims, self.keys[k]))

    def add_columns(self, columns) -> None:
        """Keep more base columns from now on. Their past hours are NaN until the lookback is loaded again."""
        for c in columns:
            if c not in self.data:
                self.columns.append(c)
                self.data[c] = np.full((self.rows, len(self.keys)), np.nan)

    def _grow(self, rows: int) -> None:
        for c, old in self.data.items():
            if old.shape == (rows, len(self.keys)):
                continue
            new = np.full((rows, len(self.keys)), np.nan)
            new[:old.shape[0], :old.shape[1]] = old
            self.data[c] = new
        self.rows = rows

    def update(self, frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> None:
        """Replace the hours [start, end) by what the frame holds; a series absent there is NaN."""
        if self.t0 is None:
            self.t0 = start
        keys = [tuple(k) for k in frame[list(self.dims)].itertuples(index=False, name=None)] if self.dims \
            else [()] * len(frame)
        for k in dict.fromkeys(keys):
            if k not in self._kidx:
                self._kidx[k] = len(self.keys)
                self.keys.append(k)
        r0, r1 = self.row(start), self.row(end)
        self._grow(max(self.rows, r1))
        if frame.empty:
            for c in self.columns:
                self.data[c][r0:r1, :] = np.nan
            return
        ri = ((frame["ts"] - self.t0) / HOUR).astype(int).to_numpy()
        ci = np.fromiter((self._kidx[k] for k in keys), dtype=int, count=len(keys))
        for c in self.columns:
            self.data[c][r0:r1, :] = np.nan
            self.data[c][ri, ci] = frame[c].to_numpy(dtype=float)


class Monitor:
    def __init__(self, graph: MetricGraph, store: Store, config: Config = Config()):
        self.graph = graph
        self.store = store
        self.config = config
        self.evaluator = Evaluator(graph, store)
        self.watched = watch_list(graph)
        self.volumes = volume_list(graph, self.watched, config.volumes)
        self.sources = sorted({s for w in self.watched for s in w.sources} | {s for v in self.volumes for s in v.sources})
        columns = sorted({c for w in self.watched for c in (w.numerator, w.denominator)} | {v.column for v in self.volumes})
        self.cubes = {cut: Cube(cut, columns) for cut in dict.fromkeys(tuple(config.cuts) + tuple(config.volume_cuts))}
        self.leaf = Cube(DIMENSIONS, columns)         # the finest cells: not judged, kept for localization
        self._flags: dict[tuple, np.ndarray] = {}     # (metric, cut) -> bad hours [hour, series]
        self._rates: dict[tuple, tuple] = {}          # (metric, cut) -> (rate, usable) of the last tick
        self._vols: dict[tuple, dict] = {}            # (volume, cut) -> {"a", "ok", "usable"} of the last tick
        self._kinds: dict[str, str] = {}
        self._loaded_until: pd.Timestamp | None = None
        self.open_episodes: list[tuple] = []      # (metric, segment, onset) of incidents that are still open
        self._watch_seen: set[tuple] = set()      # (release, metric) that has alerted, by scan or watch
        self._watch_closed: set[tuple] = set()    # ... and whose episode is over: never raised again
        self._recent: dict[tuple, Alert] = {}     # (metric, place) -> its alert on a recent tick (_drop_tails)
        self._tails_now: pd.Timestamp | None = None

    def rewatch(self) -> list[str]:
        """Bring the watch list in line with the graph after the extension agent changed it: a rate that
        joined is judged from the next tick, a rate that left is dropped. Returns the names that are new."""
        known = {w.metric for w in self.watched}
        self.watched = watch_list(self.graph)
        self.volumes = volume_list(self.graph, self.watched, self.config.volumes)
        self.sources = sorted({s for w in self.watched for s in w.sources} | {s for v in self.volumes for s in v.sources})
        cubes = list(self.cubes.values()) + [self.leaf]
        missing = sorted({c for w in self.watched for c in (w.numerator, w.denominator)} - set(cubes[0].columns))
        if missing:
            for cube in cubes:
                cube.add_columns(missing)
            self._loaded_until = None   # a new column has no history in the cubes: the next tick loads the lookback again
        return [w.metric for w in self.watched if w.metric not in known]

    # ------------------------------------------------------------------ data
    def _refresh(self, now: pd.Timestamp) -> None:
        cfg = self.config
        if self._loaded_until is None:
            # the volumes compare an hour with the same hour `volume_weeks` weeks back
            start = now - max(cfg.lookback_hours, cfg.volume_weeks * WEEK + cfg.reread_hours) * HOUR
        else:
            start = max(min(self._loaded_until, now) - cfg.reread_hours * HOUR, self.cubes[()].t0)
        for cut, cube in list(self.cubes.items()) + [(self.leaf.dims, self.leaf)]:
            frame = self.store.sums(cube.columns, start, now, group_by=cut, by_hour=True)
            cube.update(frame, start, now)
        self._loaded_until = now

    def _calendar(self, now: pd.Timestamp) -> list[dict]:
        rows = self.store.read_sql(
            "SELECT start_ts, end_ts, country, kind, description FROM calendar WHERE start_ts <= ?", (iso_hour(now),))
        out = []
        for r in rows.to_dict("records"):
            country = r["country"] if isinstance(r["country"], str) and r["country"] else None
            out.append({"start": pd.Timestamp(r["start_ts"]), "end": pd.Timestamp(r["end_ts"]), "country": country,
                        "kind": r["kind"], "description": r["description"]})
        return out

    def _calendar_mask(self, cube: Cube, events: list[dict]) -> np.ndarray:
        mask = np.zeros((cube.rows, len(cube.keys)), dtype=bool)
        pos = cube.dims.index("country") if "country" in cube.dims else None
        for e in events:
            r0, r1 = max(cube.row(e["start"]), 0), min(cube.row(e["end"]), cube.rows)
            if r1 <= r0:
                continue
            if pos is None or e["country"] is None:
                mask[r0:r1, :] = True
            else:
                cols = [k for k, key in enumerate(cube.keys) if key[pos] == e["country"]]
                mask[r0:r1, cols] = True
        return mask

    def _flag(self, metric: str, cube: Cube) -> np.ndarray:
        old = self._flags.get((metric, cube.dims))
        shape = (cube.rows, len(cube.keys))
        if old is None or old.shape != shape:
            new = np.zeros(shape, dtype=bool)
            if old is not None:
                new[:old.shape[0], :old.shape[1]] = old
            self._flags[(metric, cube.dims)] = old = new
        return old

    def _kind(self, w: Watched) -> str:
        """Which test fits the metric, decided from its own history.

        proportion: both sides count events, the numerator never exceeded the denominator, and the
                    hour-to-hour spread is what a binomial would give (so the exact test is honest).
        excess:     the same for the surplus over the denominator, for a ratio that never fell
                    below 1 (payment attempts per checkout: the surplus is the retries).
        ratio:      everything else, including money: judged against its own historical spread."""
        if w.metric in self._kinds:
            return self._kinds[w.metric]
        kind = "ratio"
        if w.counts:
            kind = next((name for shift, name in ((0.0, "proportion"), (1.0, "excess"))
                         if self._binomial_fits(w, shift)), "ratio")
        total = self.cubes[()].data[w.denominator]
        if total.size and int(np.isfinite(total).any(axis=1).sum()) >= self.config.min_history_hours:
            self._kinds[w.metric] = kind    # decided for good only on enough hours of real data
        return kind

    def _binomial_fits(self, w: Watched, shift: float, max_dispersion: float = 1.5) -> bool:
        ratios = []
        for cube in self.cubes.values():
            d = cube.data[w.denominator]
            n = cube.data[w.numerator] - shift * d
            both = np.isfinite(n) & np.isfinite(d) & (d > 0)
            if np.any(n[both] < 0) or np.any(n[both] > d[both]):
                return False
            sum_n, sum_d = np.where(both, n, 0.0).sum(axis=0), np.where(both, d, 0.0).sum(axis=0)
            with np.errstate(divide="ignore", invalid="ignore"):
                rate = sum_n / sum_d
                resid = np.where(both, (n - rate * d) / np.sqrt(d), np.nan)
                seen = both.sum(axis=0) >= MIN_DISPERSION_HOURS
                ratio = np.nanvar(np.where(seen, resid, np.nan), axis=0) / (rate * (1 - rate)) if seen.any() else \
                    np.array([])
            ratios += [x for x in np.atleast_1d(ratio) if np.isfinite(x)]
        return bool(ratios) and float(np.median(ratios)) <= max_dispersion

    # ---------------------------------------------------------------- judging
    def tick(self, as_of=None) -> Tick:
        clock = Clock(self.store, as_of)
        newest, now = clock.newest_hour(), clock.now
        tick = Tick(now=now, newest=newest)
        if newest is None:
            return tick
        self._refresh(now)
        cfg = self.config
        rows = self.cubes[()].rows
        j0 = max(0, rows - cfg.reread_hours)
        judged_hours = [self.cubes[()].hour(r) for r in range(j0, rows)]
        tick.late = clock.late(judged_hours, self.sources)
        events = tick.events = self._calendar(now)
        volumes = []
        for cut, cube in self.cubes.items():
            cal = self._calendar_mask(cube, events)
            if cut in cfg.cuts:
                for w in self.watched:
                    late_rows = [cube.row(h) for h, srcs in tick.late.items() if srcs & set(w.sources)]
                    for h, srcs in tick.late.items():
                        if srcs & set(w.sources):
                            tick.held.setdefault(h, set()).add(w.metric)
                    tick.alerts += self._judge(cube, w, j0, late_rows, cal, events)
                    tick.judged_series += len(cube.keys)
            if cut in cfg.volume_cuts:
                for v in self.volumes:
                    late_rows = [cube.row(h) for h, srcs in tick.late.items() if srcs & set(v.sources)]
                    for h, srcs in tick.late.items():
                        if srcs & set(v.sources):
                            tick.held.setdefault(h, set()).add(v.metric)
                    volumes += self._judge_volume(cube, v, j0, late_rows, cal, events)
                    tick.judged_series += len(cube.keys)
        tick.alerts += self._release_watch(now, tick, events)
        tick.alerts += self._symptoms_dropped(volumes, tick.alerts)
        tick.alerts = self._drop_tails(tick.alerts, now)
        return tick

    def _drop_tails(self, alerts: list[Alert], now: pd.Timestamp) -> list[Alert]:
        """A series that contains or crosses a fault's place can stay bad an hour or two longer than
        the fault's own series (one noisy hour is enough). While both are live, `leading` keeps the
        stronger; once the stronger has ended, the weaker would lead on its own and open a second
        incident for the same problem. So an alert is dropped when a stronger alert of the same metric
        in a compatible place, seen on an earlier tick, overlapped its episode, has ended, and ended at
        most `episode_gap_hours` before this one's newest bad hour. Bad for longer, it is news again."""
        cfg = self.config
        if self._tails_now is not None and now < self._tails_now:
            self._recent = {}            # the clock went back (a fresh replay): forget
        self._tails_now = now
        gap = cfg.episode_gap_hours * HOUR
        live = {(a.metric, tuple(sorted(a.segment.items()))) for a in alerts}
        kept = []
        for a in alerts:
            tail = any(key not in live and r.metric == a.metric and r.segment != a.segment
                       and compatible(r.segment, a.segment) and r.score > a.score
                       and a.onset <= r.last_bad and r.onset <= a.last_bad and a.last_bad <= r.last_bad + gap
                       for key, r in self._recent.items())
            if not tail:
                kept.append(a)
        for a in alerts:
            self._recent[(a.metric, tuple(sorted(a.segment.items())))] = a
        horizon = now - (cfg.active_hours + cfg.episode_gap_hours) * HOUR
        self._recent = {k: r for k, r in self._recent.items() if r.last_bad >= horizon}
        return kept

    def _judge(self, cube: Cube, w: Watched, j0: int, late_rows: list[int], cal: np.ndarray,
               events: list[dict]) -> list[Alert]:
        cfg = self.config
        kind = self._kind(w)
        rows = cube.rows
        N = cube.data[w.numerator].copy()
        D = cube.data[w.denominator].copy()
        for r in late_rows:
            N[r, :] = np.nan
            D[r, :] = np.nan
        flags = self._flag(w.metric, cube)
        shift = 0.0
        if kind == "excess":
            N, shift = N - D, 1.0

        # baseline: visible history before the hours being judged
        b0 = max(0, j0 - cfg.lookback_hours)
        usable = np.isfinite(N[b0:j0]) & np.isfinite(D[b0:j0]) & (D[b0:j0] > 0) & ~flags[b0:j0] & ~cal[b0:j0]
        for metric, segment, onset in self.open_episodes:
            # while an incident is open its hours are not history, flagged or not: at night a running
            # fault is too thin to flag, and would otherwise drift into its own baseline
            if metric == w.metric and set(segment) == set(cube.dims):
                k = cube._kidx.get(tuple(segment[d] for d in cube.dims))
                if k is not None:
                    usable[max(cube.row(onset) - b0, 0):, k] = False
        sum_n = np.where(usable, N[b0:j0], 0.0).sum(axis=0)
        sum_d = np.where(usable, D[b0:j0], 0.0).sum(axis=0)
        with np.errstate(divide="ignore", invalid="ignore"):
            rate = np.where(sum_d > 0, sum_n / sum_d, np.nan)
        ok = (usable.sum(axis=0) >= cfg.min_history_hours) & (sum_d >= cfg.min_history_volume) & (rate > 0)
        if kind == "ratio":
            resid = np.where(usable, scaled_residual(N[b0:j0], D[b0:j0], rate), np.nan)
            scale = np.array([robust_scale(resid[:, k]) for k in range(resid.shape[1])]) if resid.size else \
                np.full(len(cube.keys), np.nan)
            with np.errstate(invalid="ignore"):
                extreme = np.nanmax(np.abs(resid), axis=0) if resid.size else np.full(len(cube.keys), np.nan)
            ok &= np.isfinite(scale) & np.isfinite(extreme)
        else:
            ok &= rate < 1
        self._rates[(w.metric, cube.dims)] = (rate + shift, ok, scale if kind == "ratio" else None, kind)

        # the hours being judged, alone and summed with the hours before
        bad = np.zeros((rows - j0, len(cube.keys)), dtype=bool)
        best = np.full(bad.shape, np.nan)
        best_z = np.full(bad.shape, np.nan)
        single = np.full(bad.shape, np.nan)   # how far each hour alone is from the expected rate
        single_score = np.full(bad.shape, np.nan)
        for width in cfg.windows:
            Nw, Dw = np.full(bad.shape, np.nan), np.full(bad.shape, np.nan)
            for i, r in enumerate(range(j0, rows)):
                if r - width + 1 >= 0:
                    Nw[i] = N[r - width + 1:r + 1].sum(axis=0)   # NaN if any hour of the window is missing or held
                    Dw[i] = D[r - width + 1:r + 1].sum(axis=0)
            if kind in ("proportion", "excess"):
                score, z = binomial_surprise(Nw, Dw, np.broadcast_to(rate, Nw.shape))
                enough = score >= cfg.min_surprise
            else:
                score, z = ratio_surprise(Nw, Dw, np.broadcast_to(rate, Nw.shape), np.broadcast_to(scale, Nw.shape))
                with np.errstate(invalid="ignore"):
                    enough = (np.abs(z) >= cfg.min_z) & (Dw >= cfg.min_ratio_volume) \
                        & (np.abs(scaled_residual(Nw, Dw, rate)) >= cfg.beyond_history * extreme)
            with np.errstate(divide="ignore", invalid="ignore"):
                moved = floor_moved(Nw / Dw, rate, shift, kind, cfg)   # in units of the floor: 1 passes
            if width == 1:
                single, single_score = moved, score
            hit = ok & enough & (np.abs(moved) >= 1.0)
            better = hit & ~(best >= score)
            best = np.where(better, score, best)
            best_z = np.where(better, z, best_z)
            self._mark(bad, hit, moved, single, single_score, width, 1.0)
        flags[j0:rows] = bad

        # one alert per series whose newest bad hour is recent
        alerts = []
        for k in np.flatnonzero(bad.any(axis=0)):
            col = flags[:, k]
            last = int(np.flatnonzero(col)[-1])
            if last < rows - cfg.active_hours:
                continue
            first = self._episode_start(col, last, cfg.episode_gap_hours)
            badrows = first + np.flatnonzero(col[first:last + 1])   # the episode's bad hours, gaps left out
            ep_n = np.nansum(N[badrows, k])
            ep_d = np.nansum(D[badrows, k])
            newest = badrows[-max(cfg.windows):]                    # the state now: the newest bad hours
            now_n = np.nansum(N[newest, k])
            now_d = np.nansum(D[newest, k])
            recent = slice(max(first, j0) - j0, last - j0 + 1)
            i = int(np.nanargmax(best[recent, k])) if np.isfinite(best[recent, k]).any() else None
            alert = Alert(
                metric=w.metric, kind=kind, segment=cube.segment(k), onset=cube.hour(first), last_bad=cube.hour(last),
                hours=int(col[first:last + 1].sum()),
                observed=float(now_n / now_d + shift) if now_d > 0 else float("nan"),
                expected=float(rate[k] + shift), numerator=float(now_n + shift * now_d), denominator=float(now_d),
                episode_observed=float(ep_n / ep_d + shift) if ep_d > 0 else float("nan"),
                score=float(best[recent, k][i]) if i is not None else 0.0,
                z=float(best_z[recent, k][i]) if i is not None else 0.0)
            if w.calendar:
                self._explain(alert, events, self._rate_past(cube, k, N, D, shift, rate[k] + shift),
                              self._rate_beyond(kind, now_n, now_d, shift, rate[k] + shift,
                                                scale[k] if kind == "ratio" else None))
            alerts.append(alert)
        return alerts

    def _rate_past(self, cube: Cube, k: int, N, D, shift: float, expected: float):
        """The effect an earlier calendar event had on this rate series: rate over its hours vs baseline."""
        def past(e: dict) -> float | None:
            r0, r1 = max(cube.row(e["start"]), 0), min(cube.row(e["end"]), cube.rows)
            if r1 <= r0:
                return None
            n, d = N[r0:r1, k], D[r0:r1, k]
            both = np.isfinite(n) & np.isfinite(d)
            sum_d = float(d[both].sum())
            if sum_d < self.config.min_ratio_volume:
                return None
            return (float(n[both].sum()) / sum_d + shift) / expected - 1.0
        return past

    def _rate_beyond(self, kind: str, now_n: float, now_d: float, shift: float, expected: float, scale):
        """Is the rate of the newest bad hours surely beyond `expected x (1 + limit)`, the way it moved?"""
        def beyond(limit: float, sign: int) -> bool:
            if now_d <= 0:
                return False
            capped = expected * (1.0 + sign * limit)
            if sign * ((now_n / now_d + shift) - capped) <= 0:
                return False
            base = capped - shift
            if kind == "ratio":
                score, _ = ratio_surprise(np.array([now_n]), np.array([now_d]), np.array([base]), np.array([scale]))
            else:
                if not 0 < base < 1:
                    return bool(base <= 0) if sign < 0 else bool(base >= 1)
                score, _ = binomial_surprise(np.array([now_n]), np.array([now_d]), np.array([base]))
            return bool(np.isfinite(score[0]) and score[0] >= self.config.release_min_surprise)
        return beyond

    def _mark(self, bad, hit, effect, single, single_score, width: int, min_effect: float) -> None:
        """Mark the bad hours of one window width, in place.

        A window of one hour is that hour's own evidence. A sum over several hours says "somewhere in
        here", so each of its hours, the newest one included, is bad only when it alone moved the same
        way by at least min_effect and is itself unusual (p below 0.01). A summed window at p below
        1e-7 always holds such an hour: three hours each above p 0.01 sum to about p 5e-5 at most.
        Without this the normal hours right after a fault ended were marked bad while the window still
        reached back into the fault, which made the episode two hours too long and halved the change
        reported for its newest hours. For a rate, `effect` and `single` are in units of its floor
        (floor_moved) and min_effect is 1."""
        if width == 1:
            bad |= hit
            return
        with np.errstate(invalid="ignore"):
            own = (np.sign(single) == np.sign(effect)) & (np.abs(single) >= min_effect) \
                & (single_score >= self.config.onset_min_surprise)
        bad |= hit & own
        # an earlier hour of the window is bad too on the same terms
        for i in range(bad.shape[0]):
            for back in range(1, width):
                if i - back >= 0:
                    with np.errstate(invalid="ignore"):
                        bad[i - back] |= hit[i] & (np.sign(single[i - back]) == np.sign(effect[i])) \
                            & (np.abs(single[i - back]) >= min_effect) \
                            & (single_score[i - back] >= self.config.onset_min_surprise)

    @staticmethod
    def _episode_start(col: np.ndarray, last: int, gap: int) -> int:
        """First hour of the episode that ends at `last`: bad hours at most `gap` hours apart."""
        first = r = last
        while r >= 0 and first - r <= gap:
            if col[r]:
                first = r
            r -= 1
        return first

    # ---------------------------------------------------------------- volumes
    def _volume_usable(self, metric: str, cube: Cube, X: np.ndarray, flags: np.ndarray, cal: np.ndarray) -> np.ndarray:
        """Hours that may serve as an earlier week: counted, not flagged, no calendar event, no open incident."""
        usable = np.isfinite(X) & (X >= 0) & ~flags & ~cal
        for m, segment, onset in self.open_episodes:
            if m == metric and set(segment) == set(cube.dims):
                k = cube._kidx.get(tuple(segment[d] for d in cube.dims))
                if k is not None:
                    usable[max(cube.row(onset), 0):, k] = False
        return usable

    def _volume_expected(self, X: np.ndarray, usable: np.ndarray, rows: np.ndarray):
        """Mean of the same hour of the week over the previous weeks, and how many weeks it has.
        X and usable are [hour, series]; rows are the hours asked about."""
        weeks = self.config.volume_weeks
        total = np.zeros((len(rows), X.shape[1]))
        seen = np.zeros((len(rows), X.shape[1]))
        for w in range(1, weeks + 1):
            back = rows - w * WEEK
            valid = (back >= 0) & (back < X.shape[0])
            if not valid.any():
                continue
            b = back[valid]
            u = usable[b]
            total[valid] += np.where(u, X[b], 0.0)
            seen[valid] += u
        with np.errstate(divide="ignore", invalid="ignore"):
            return np.where(seen > 0, total / seen, np.nan), seen

    def _volume_dispersion(self, X: np.ndarray, usable: np.ndarray, j0: int) -> np.ndarray:
        """Extra-Poisson spread `a` per series (variance = mu + a mu^2), from how the same hour of the
        week differed between the previous weeks. A week that differs wildly (a past fault nobody
        flagged) is left out of the fit. NaN when too few hours were seen twice."""
        weeks = self.config.volume_weeks
        lo = j0 - weeks * WEEK
        block = np.full((weeks * WEEK, X.shape[1]), np.nan)
        s0 = max(lo, 0)
        if j0 > s0:
            block[s0 - lo:] = np.where(usable[s0:j0], X[s0:j0], np.nan)
        block = block.reshape(weeks, WEEK, X.shape[1])
        m = np.isfinite(block).sum(axis=0)
        with np.errstate(invalid="ignore", divide="ignore"), warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)
            mean = np.nanmean(block, axis=0)
            var = np.nanvar(block, axis=0, ddof=1)
        keep = (m >= 2) & np.isfinite(mean) & np.isfinite(var) & (mean > 0)
        a = np.full(X.shape[1], np.nan)
        for _ in range(2):
            wgt = np.where(keep, m - 1, 0)
            num = (wgt * np.where(keep, var - mean, 0.0)).sum(axis=0)
            den = (wgt * np.where(keep, mean, 0.0) ** 2).sum(axis=0)
            with np.errstate(invalid="ignore", divide="ignore"):
                a = np.maximum(np.where(den > 0, num / den, np.nan), VOLUME_MIN_DISPERSION)
                fitted = mean + a * mean ** 2
                keep = keep & (var <= VOLUME_TRIM * fitted)
        return np.where(keep.sum(axis=0) >= VOLUME_MIN_SLOTS, a, np.nan)

    def _judge_volume(self, cube: Cube, v: Volume, j0: int, late_rows: list[int], cal: np.ndarray,
                      events: list[dict]) -> list[Alert]:
        cfg = self.config
        rows = cube.rows
        X = cube.data[v.column].copy()
        for r in late_rows:
            X[r, :] = np.nan
        flags = self._flag(v.metric, cube)
        usable = self._volume_usable(v.metric, cube, X, flags, cal)
        usable[j0:] = False                                   # the hours being judged are not history
        a = self._volume_dispersion(X, usable, j0)
        lo = max(j0 - max(cfg.windows) + 1, 0)
        E, seen = self._volume_expected(X, usable, np.arange(lo, rows))
        with np.errstate(invalid="ignore", divide="ignore"):
            V = (E + a * E ** 2) * (1.0 + 1.0 / seen)
        ok = np.isfinite(a)
        self._vols[(v.metric, cube.dims)] = {"a": a, "ok": ok, "usable": usable}

        bad = np.zeros((rows - j0, len(cube.keys)), dtype=bool)
        best = np.full(bad.shape, np.nan)
        best_z = np.full(bad.shape, np.nan)
        single = np.full(bad.shape, np.nan)
        single_score = np.full(bad.shape, np.nan)
        for width in cfg.windows:
            Xw, Ew, Vw = (np.full(bad.shape, np.nan) for _ in range(3))
            for i, r in enumerate(range(j0, rows)):
                if r - width + 1 >= lo:
                    Xw[i] = X[r - width + 1:r + 1].sum(axis=0)     # NaN if any hour is missing or held
                    Ew[i] = E[r - width + 1 - lo:r + 1 - lo].sum(axis=0)
                    Vw[i] = V[r - width + 1 - lo:r + 1 - lo].sum(axis=0)
            score, z = count_surprise(Xw, Ew, Vw)
            with np.errstate(divide="ignore", invalid="ignore"):
                effect = Xw / Ew - 1.0
                enough = (score >= cfg.min_surprise) & (Ew >= cfg.volume_min_expected)
            if width == 1:
                single, single_score = effect, score
            hit = ok & enough & (np.abs(effect) >= cfg.volume_min_effect)
            better = hit & ~(best >= score)
            best = np.where(better, score, best)
            best_z = np.where(better, z, best_z)
            self._mark(bad, hit, effect, single, single_score, width, cfg.volume_min_effect)
        flags[j0:rows] = bad

        alerts = []
        for k in np.flatnonzero(bad.any(axis=0)):
            col = flags[:, k]
            last = int(np.flatnonzero(col)[-1])
            if last < rows - cfg.active_hours:
                continue
            first = self._episode_start(col, last, cfg.episode_gap_hours)
            span = first + np.flatnonzero(col[first:last + 1])     # the episode's bad hours
            ep_x = np.nansum(X[span, k])
            newest = span[-max(cfg.windows):]
            ex, weeks = self._volume_expected(X[:, k:k + 1], usable[:, k:k + 1], newest)
            now_x, now_e = float(np.nansum(X[newest, k])), float(np.nansum(ex))
            with np.errstate(invalid="ignore", divide="ignore"):
                now_v = float(np.nansum((ex + a[k] * ex ** 2) * (1.0 + 1.0 / weeks)))
            recent = slice(max(first, j0) - j0, last - j0 + 1)
            i = int(np.nanargmax(best[recent, k])) if np.isfinite(best[recent, k]).any() else None
            alert = Alert(
                metric=v.metric, kind="volume", segment=cube.segment(k), onset=cube.hour(first),
                last_bad=cube.hour(last), hours=int(col[first:last + 1].sum()),
                observed=now_x / len(newest), expected=now_e / len(newest), numerator=now_x, denominator=now_e,
                episode_observed=float(ep_x) / len(span),
                score=float(best[recent, k][i]) if i is not None else 0.0,
                z=float(best_z[recent, k][i]) if i is not None else 0.0)
            self._explain(alert, events, self._volume_past(cube, k, X, usable),
                          self._volume_beyond(now_x, now_e, now_v))
            alerts.append(alert)
        return alerts

    def _volume_past(self, cube: Cube, k: int, X, usable):
        """The effect an earlier calendar event had on this count: its hours against their earlier weeks."""
        def past(e: dict) -> float | None:
            r0, r1 = max(cube.row(e["start"]), 0), min(cube.row(e["end"]), cube.rows)
            if r1 <= r0:
                return None
            ex, _ = self._volume_expected(X[:, k:k + 1], usable[:, k:k + 1], np.arange(r0, r1))
            x = X[r0:r1, k]
            both = np.isfinite(x) & np.isfinite(ex[:, 0])
            sum_e = float(ex[both, 0].sum())
            if sum_e < self.config.volume_min_expected:
                return None
            return float(x[both].sum()) / sum_e - 1.0
        return past

    def _volume_beyond(self, now_x: float, now_e: float, now_v: float):
        def beyond(limit: float, sign: int) -> bool:
            if now_e <= 0 or not np.isfinite(now_v):
                return False
            factor = 1.0 + sign * limit
            if factor <= 0:
                return True
            capped = now_e * factor
            if sign * (now_x - capped) <= 0:
                return False
            # the spread at the capped level (scaled with the square of the level: conservative upward)
            var = max(now_v * factor ** 2, capped * 1.0001)
            score, _ = count_surprise(np.array([now_x]), np.array([capped]), np.array([var]))
            return bool(np.isfinite(score[0]) and score[0] >= self.config.release_min_surprise)
        return beyond

    def _explain(self, alert: Alert, events: list[dict], past=None, beyond=None) -> None:
        """Set explained_by when a covering calendar row fits the change: the way the event moves the
        metric (EVENT_EFFECTS), and no more than EVENT_SIZE_FACTOR times the largest effect earlier
        events of that kind and place had on this series. A row that covers but does not fit becomes
        `partial_explanation`, with the reason, and the alert is raised."""
        e = self._covering_event(alert, events)
        if e is None:
            return
        effect = alert.effect
        if not np.isfinite(effect) or effect == 0:
            alert.explained_by = e
            return
        what = e["description"] or e["kind"]
        predicted = EVENT_EFFECTS.get(e["kind"], {}).get(alert.metric)
        way = int(np.sign(effect))
        why = None
        if predicted is not None and way != predicted:
            why = (f"{what} should move {alert.metric} {'up' if predicted > 0 else 'down'}, "
                   f"but it went {'up' if way > 0 else 'down'} ({effect:+.0%})")
        elif past is not None and beyond is not None:
            seen = []
            for ev in events:
                same = ev["kind"] == e["kind"] and ev["country"] == e["country"]
                if not same or ev["end"] > alert.onset or iso_hour(ev["start"]) == e["start"]:
                    continue
                got = past(ev)
                if got is not None and np.isfinite(got):
                    seen.append(got * way)
            if seen:
                largest = max(max(seen), 0.0)
                if beyond(EVENT_SIZE_FACTOR * largest, way):
                    why = (f"{alert.metric} moved {effect:+.0%}, more than twice the largest effect earlier "
                           f"{e['kind']} events of {e['country'] or 'all countries'} had here ({largest * way:+.0%})")
        if why is None:
            alert.explained_by = e
        else:
            alert.explained_by = None
            alert.partial_explanation = dict(e, why=why)

    def _symptoms_dropped(self, volumes: list[Alert], alerts: list[Alert]) -> list[Alert]:
        """Orders = sessions x conversion. When the sessions or a rate in a compatible place moved in
        the same hours, the orders are its symptom: they go into that alert's story, not a new one."""
        sessions = [a for a in volumes if a.metric == "sessions"]
        out = []
        for a in volumes:
            if a.metric == "orders":
                if any(compatible(a.segment, s.segment) and (s.effect < 0) == (a.effect < 0) for s in sessions):
                    continue
                if any(compatible(a.segment, r.segment) for r in alerts if r.kind != "volume"):
                    continue
                if self._rate_flagged(a):
                    continue
            out.append(a)
        return out

    def _rate_flagged(self, alert: Alert) -> bool:
        """A watched rate was bad in a compatible series during the alert's episode."""
        rates = {w.metric for w in self.watched}
        for (metric, cut), flags in self._flags.items():
            if metric not in rates:
                continue
            cube = self.cubes[cut]
            r0, r1 = max(cube.row(alert.onset), 0), min(cube.row(alert.last_bad) + 1, flags.shape[0])
            if r1 <= r0:
                continue
            for k, key in enumerate(cube.keys[:flags.shape[1]]):
                if compatible(alert.segment, dict(zip(cut, key))) and flags[r0:r1, k].any():
                    return True
        return False

    def _volume_series(self, metric: str, segment: dict):
        for (m, cut), state in self._vols.items():
            if m == metric and set(cut) == set(segment):
                cube = self.cubes[cut]
                k = cube._kidx.get(tuple(segment[d] for d in cut))
                if k is not None and k < len(state["ok"]) and state["ok"][k]:
                    return cube, k, state
        return None

    def _volume_hours(self, metric: str, segment: dict, start, end, skip=()):
        """Observed count, expected count and its variance per hour of a volume series over [start, end)."""
        found = self._volume_series(metric, segment)
        v = next((x for x in self.volumes if x.metric == metric), None)
        if found is None or v is None:
            return None
        cube, k, state = found
        r0, r1 = max(cube.row(start), 0), min(cube.row(end), cube.rows)
        if r1 <= r0:
            return None
        rows = np.arange(r0, r1)
        x = cube.data[v.column][r0:r1, k].astype(float)
        usable = state["usable"][:, k:k + 1]
        e, seen = self._volume_expected(cube.data[v.column][:usable.shape[0], k:k + 1], usable, rows)
        e, seen = e[:, 0], seen[:, 0]
        a = float(state["a"][k])
        with np.errstate(invalid="ignore", divide="ignore"):
            var = (e + a * e ** 2) * (1.0 + 1.0 / seen)
        held = {cube.row(h) for h in skip}
        keep = np.isfinite(x) & np.isfinite(e) & np.isfinite(var) & np.array([r not in held for r in rows])
        return cube, rows[keep], x[keep], e[keep], var[keep]

    def _release_watch(self, now: pd.Timestamp, tick: Tick, events: list[dict]) -> list[Alert]:
        """A release is a reason to look closer. The hourly scan asks thousands of questions an hour
        and needs overwhelming evidence for each; a release asks one: did the metrics linked to this
        system change, in the scope of the release, since it went out? That single question can be
        answered with far less data, which is what a small cell at night has.

        Only metrics with an exact test are asked: for the others a p-value of 1e-4 is not trustworthy.
        Each release and metric gets one episode: once its alert (from the scan or from here) has
        ended, the question is closed, so the evidence that stays in the sum cannot raise it again."""
        cfg = self.config
        if not cfg.release_watch_hours:
            return []
        rows = self.store.read_sql(
            "SELECT ts, system, country, platform, description FROM releases WHERE ts <= ? AND ts >= ? ORDER BY ts",
            (iso_hour(now), iso_hour(now - cfg.release_watch_hours * HOUR)))
        out: list[Alert] = []
        for r in rows.to_dict("records"):
            if isinstance(r["description"], str) and "rollback" in r["description"].lower():
                continue   # by the contract a rollback row ends a release (the column may be NULL)
            scope = {d: r[d] for d in ("country", "platform") if isinstance(r[d], str) and r[d]}
            start = pd.Timestamp(r["ts"])
            for w in self.watched:
                if r["system"] not in self.graph.specs[w.metric].systems:
                    continue
                key = (r["ts"], r["system"], tuple(sorted(scope.items())), w.metric)
                if key in self._watch_closed:
                    continue
                if any(a.metric == w.metric and compatible(a.segment, scope) for a in tick.alerts):
                    self._watch_seen.add(key)
                    continue   # the scan already has it
                skip = [h for h, srcs in tick.late.items() if srcs & set(w.sources)]
                found = self._series(w.metric, scope)
                if found is None or found[2][3] == "ratio":
                    continue
                if skip and key in self._watch_seen:
                    continue   # hours are held: neither raise nor close on missing data
                cube = found[0]
                # the evidence through each hour since the release; the newest hour that added to it
                best, last, prev = None, None, 0.0
                for row in range(max(cube.row(start), 0), cube.rows):
                    through = self.window(w.metric, scope, start, cube.hour(row + 1), skip)
                    if through is None:
                        continue
                    if through["score"] > prev + 1e-9:
                        last = row
                    prev = through["score"]
                    if best is None or through["score"] > best["score"]:
                        best = through
                shift = 1.0 if found[2][3] == "excess" else 0.0   # the floor of the scan, on the same side
                live = (best is not None and last is not None and best["score"] >= cfg.release_min_surprise
                        and abs(float(floor_moved(best["observed"] - shift, best["expected"] - shift, shift,
                                                  found[2][3], cfg))) >= 1.0
                        and last >= cube.rows - cfg.active_hours)   # else nothing new for a while
                if not live:
                    if key in self._watch_seen:
                        self._watch_closed.add(key)   # it alerted and has ended
                    continue
                self._watch_seen.add(key)
                now_w = self.window(w.metric, scope, start, cube.hour(last + 1), skip)
                alert = Alert(
                    metric=w.metric, kind=found[2][3], segment=dict(scope), onset=start, last_bad=cube.hour(last),
                    hours=last - max(cube.row(start), 0) + 1, observed=now_w["observed"], expected=now_w["expected"],
                    numerator=now_w["numerator"], denominator=now_w["denominator"],
                    episode_observed=now_w["observed"], score=best["score"], z=best["z"], trigger="release")
                if w.calendar:
                    self._explain(alert, events)   # direction only: one question since a release
                out.append(alert)
        # Two releases can ask about the same metric in overlapping places (a routine one hours before the
        # real one). The older one's sum is the diluted one: of such alerts the strongest is kept.
        kept: list[Alert] = []
        for a in sorted(out, key=lambda a: (-a.score, a.onset)):
            if not any(k.metric == a.metric and compatible(k.segment, a.segment) for k in kept):
                kept.append(a)
        return kept

    @staticmethod
    def _covering_event(alert: Alert, events: list[dict]) -> dict | None:
        """A calendar row that is active during the episode, in the same country or everywhere."""
        for e in events:
            overlaps = e["start"] <= alert.last_bad and alert.onset < e["end"] + 2 * HOUR
            where = e["country"] is None or alert.segment.get("country") in (None, e["country"])
            if overlaps and where:
                return {"kind": e["kind"], "country": e["country"], "description": e["description"],
                        "start": iso_hour(e["start"]), "end": iso_hour(e["end"])}
        return None

    def _series(self, metric: str, segment: dict):
        for (m, cut), held in self._rates.items():
            if m == metric and set(cut) == set(segment):
                k = self.cubes[cut]._kidx.get(tuple(segment[d] for d in cut))
                if k is not None and held[1][k]:
                    return self.cubes[cut], k, held
        return None

    def expected_rate(self, metric: str, segment: dict) -> float | None:
        """The baseline rate of a metric in a watched series, from the last tick."""
        if self._is_volume(metric):
            return None    # a count has no single expected rate: its expected count depends on the hour
        found = self._series(metric, segment)
        return float(found[2][0][found[1]]) if found else None

    def _is_volume(self, metric: str) -> bool:
        return any(v.metric == metric for v in self.volumes) and not any(w.metric == metric for w in self.watched)

    def hourly(self, metric: str, segment: dict, start, end, skip=()) -> list[dict]:
        """The metric in a watched series hour by hour over [start, end), each hour against the
        baseline: which way it went and how surely. Hours without data or still held are left out.
        A volume: the count of each hour against the same hour of the previous weeks."""
        if self._is_volume(metric):
            got = self._volume_hours(metric, segment, start, end, skip)
            if got is None:
                return []
            cube, rows, x, e, var = got
            score, z = count_surprise(x, e, var)
            return [{"hour": cube.hour(int(r)), "observed": float(x[i]), "expected": float(e[i]),
                     "score": float(score[i]), "z": float(z[i])} for i, r in enumerate(rows) if np.isfinite(score[i])]
        found = self._series(metric, segment)
        w = next((x for x in self.watched if x.metric == metric), None)
        if not found or w is None:
            return []
        cube, k, (rate, _, scale, kind) = found
        r0, r1 = max(cube.row(start), 0), min(cube.row(end), cube.rows)
        shift = 1.0 if kind == "excess" else 0.0
        d = cube.data[w.denominator][r0:r1, k]
        n = cube.data[w.numerator][r0:r1, k] - shift * d
        base = np.full(len(d), float(rate[k]) - shift)
        if kind == "ratio":
            score, z = ratio_surprise(n, d, base, np.full(len(d), scale[k]))
        else:
            score, z = binomial_surprise(n, d, base)
        held = {cube.row(h) for h in skip}
        return [{"hour": cube.hour(r0 + i), "observed": float(n[i] / d[i] + shift), "expected": float(rate[k]),
                 "score": float(score[i]), "z": float(z[i])}
                for i in range(len(d)) if np.isfinite(score[i]) and (r0 + i) not in held]

    def has_series(self, metric: str, segment: dict) -> bool:
        if self._is_volume(metric):
            return self._volume_series(metric, segment) is not None
        return self._series(metric, segment) is not None

    def cells(self, metric: str, start, end, skip=()) -> dict | None:
        """The metric in every finest cell: counts in [start, end) and the rate each cell had before
        `start`. A cell's own history is thin, so its rate leans on the pooled rate of all cells for
        the first CELL_PRIOR_VOLUME of its denominator. For the investigator's localization."""
        w = next((x for x in self.watched if x.metric == metric), None)
        cube = self.leaf
        if w is None or cube.t0 is None or not cube.keys:
            return None
        kind = self._kind(w)
        shift = 1.0 if kind == "excess" else 0.0
        r0, r1 = max(cube.row(start), 0), min(cube.row(end), cube.rows)
        b0 = max(0, r0 - self.config.lookback_hours)
        D = cube.data[w.denominator]
        N = cube.data[w.numerator] - shift * D
        both = np.isfinite(N) & np.isfinite(D)
        for h in skip:
            if 0 <= cube.row(h) < cube.rows:
                both[cube.row(h), :] = False
        n = np.where(both, N, 0.0)
        d = np.where(both, D, 0.0)
        hist_n, hist_d = n[b0:r0].sum(axis=0), d[b0:r0].sum(axis=0)
        if hist_d.sum() <= 0:
            return None
        pooled = hist_n.sum() / hist_d.sum()
        prior = 0.0 if kind == "ratio" else CELL_PRIOR_VOLUME
        with np.errstate(divide="ignore", invalid="ignore"):
            rate = np.where(hist_d + prior > 0, (hist_n + prior * pooled) / (hist_d + prior), pooled)
        unit_var = 1.0
        if kind == "ratio":
            with np.errstate(divide="ignore", invalid="ignore"):
                resid = np.where(d[b0:r0] > 0, (n[b0:r0] - rate * d[b0:r0]) / np.sqrt(d[b0:r0]), np.nan)
            scale = robust_scale(resid.ravel())
            if not np.isfinite(scale):
                return None
            unit_var = float(scale) ** 2
        return {"dims": cube.dims, "keys": list(cube.keys), "num": n[r0:r1].sum(axis=0), "den": d[r0:r1].sum(axis=0),
                "rate": rate, "history_den": hist_d, "kind": kind, "shift": shift, "unit_var": unit_var}

    def window(self, metric: str, segment: dict, start, end, skip=(), as_rate: float | None = None,
               as_ratio: float | None = None) -> dict | None:
        """One targeted look: the metric in a watched series over [start, end) against its baseline.
        Used to follow a link of the graph from a metric that moved to one it may affect, where a
        single question is asked and the bar can be far lower than for the hourly scan.

        A volume (sessions, orders) answers in counts per hour: observed and expected are counts per
        hour, numerator and denominator the observed and expected counts summed over the window, and
        `as_rate` is a count per hour at the alert's expected level (`as_ratio`, the faulty count as a
        share of the expected one, is the better question for a count, whose level follows the hour)."""
        if self._is_volume(metric):
            return self._volume_window(metric, segment, start, end, skip, as_rate, as_ratio)
        found = self._series(metric, segment)
        w = next((x for x in self.watched if x.metric == metric), None)
        if not found or w is None:
            return None
        cube, k, (rate, _, scale, kind) = found
        r0, r1 = max(cube.row(start), 0), min(cube.row(end), cube.rows)
        shift = 1.0 if kind == "excess" else 0.0
        d = cube.data[w.denominator][r0:r1, k]
        n = cube.data[w.numerator][r0:r1, k] - shift * d
        both = np.isfinite(n) & np.isfinite(d)
        for h in skip:                      # hours whose data has not arrived are not evidence
            if r0 <= cube.row(h) < r1:
                both[cube.row(h) - r0] = False
        sum_n, sum_d = float(n[both].sum()), float(d[both].sum())
        if sum_d <= 0:
            return None
        base = float(rate[k]) - shift
        if as_rate is None and as_ratio is not None:
            as_rate = as_ratio * float(rate[k])
        if kind == "ratio":
            score, z = ratio_surprise(np.array([sum_n]), np.array([sum_d]), np.array([base]), np.array([scale[k]]))
        else:
            score, z = binomial_surprise(np.array([sum_n]), np.array([sum_d]), np.array([base]))
        out = {"metric": metric, "observed": sum_n / sum_d + shift, "expected": float(rate[k]),
               "numerator": sum_n + shift * sum_d, "denominator": sum_d, "score": float(score[0]), "z": float(z[0])}
        if as_rate is not None:
            # "telling": how surprising this much data would be if the rate were `as_rate`; a window
            # too thin to show that rate cannot show that it has gone either
            hyp = (as_rate - shift) * sum_d
            if kind == "ratio":
                telling, _ = ratio_surprise(np.array([hyp]), np.array([sum_d]), np.array([base]), np.array([scale[k]]))
            else:
                telling, _ = binomial_surprise(np.array([round(hyp)]), np.array([sum_d]), np.array([base]))
            out["telling"] = float(telling[0])
        return out

    def _volume_window(self, metric: str, segment: dict, start, end, skip=(), as_rate: float | None = None,
                       as_ratio: float | None = None) -> dict | None:
        got = self._volume_hours(metric, segment, start, end, skip)
        if got is None or len(got[1]) == 0:
            return None
        _, rows, x, e, var = got
        sum_x, sum_e, sum_v = float(x.sum()), float(e.sum()), float(var.sum())
        if sum_e <= 0:
            return None
        score, z = count_surprise(np.array([sum_x]), np.array([sum_e]), np.array([sum_v]))
        hours = len(rows)
        out = {"metric": metric, "observed": sum_x / hours, "expected": sum_e / hours, "numerator": sum_x,
               "denominator": sum_e, "score": float(score[0]), "z": float(z[0])}
        if as_ratio is None and as_rate is not None:
            as_ratio = as_rate / (sum_e / hours)
        if as_ratio is not None:
            telling, _ = count_surprise(np.array([as_ratio * sum_e]), np.array([sum_e]), np.array([sum_v]))
            out["telling"] = float(telling[0])
        return out
