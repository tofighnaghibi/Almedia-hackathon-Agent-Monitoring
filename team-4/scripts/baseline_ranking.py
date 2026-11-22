"""A plain ranking of the most anomalous factor and cell: the baseline the brain has to beat (README 3.5).

    python -m scripts.baseline_ranking --db <seed db> --out <folder> --z <threshold>

It is what a competent analyst builds in an afternoon, kept in the scoring harness so that the page of results can
show what the metric graph, the statistics and the investigation add over it. It is scored by the same scorer
(sim/score.py) on the same runs; scripts/summarize_evaluation.py --baseline puts the two side by side.

Rules (every one of them is here; nothing else decides):

- Data. It reads two tables of the run's database, read only: facts_hourly and releases. It never reads ground_truth
  or sim_meta, nor the calendar, the ingestion_log or the links of the metric graph, and it does not import the
  brain's investigator. A database without a non-empty -wal file is opened immutable, so no side file is created.
- What is watched. The same rates as the monitor, brain.monitor.watch_list(MetricGraph.load()): metric name,
  numerator column, denominator column (15 rates today), in the same cuts, brain.monitor.CUTS: the whole market,
  country, platform, category, channel, country x platform, country x category (48 series per rate with 4 countries).
  So the comparison is about everything else.
- Rate. For every rate, cut, cell and hour t: sum(numerator) / sum(denominator) of that cell and hour (no rate when
  the denominator is 0).
- Expectation. The mean, and the spread the sample standard deviation (ddof 1), of the same cell's rate at the same
  hour of the day on each of the previous 21 days (t - 24 h, ..., t - 504 h: strictly before t, so causal). The history
  is taken as it is: earlier alerts and fault hours stay in it.
- Judged. A cell-hour is judged when it has at least 14 such history points, that history varied, its denominator at
  t is at least 30 and t is at least 80 hours after the first hour of the database (the warm-up of the brain's replay:
  min_history_hours + reread_hours of brain.monitor.Config). The 14 points mean nothing is judged before day 15. A
  history that never varied (spread 0, or below 1e-12: equal rates leave a rounding spread of about 1e-17), such as an
  order rate of exactly 1 in a small cell, has nothing to measure a change against, so that cell-hour is not judged.
- Score. z = (rate - mean) / spread.
- Ranking. Every hour, of all judged series with abs(z) >= the threshold only the one with the largest abs(z) is kept:
  the plain ranking, the most anomalous factor and cell. Ties: the finer cell (more dimensions), then the metric's
  place in the watch list, then the cell's place in the cut order.
- Incidents. The kept series of hour t joins an incident of the same metric whose place is equal to or nested with its
  own (one place's items contain the other's) and whose last alert is at most 2 hours before t; that incident's
  last_seen becomes t (on several, the one alerted last). Otherwise it opens a new incident: first_seen = t, written
  YYYY-MM-DDTHH:00:00Z, segment = its place as a dict ({} for the whole market), metric = the rate. Every incident
  is written with status resolved once the run is over.
- Cause, the incident's only hypothesis, status supported (a plain ranking states its guess): the latest release
  (on equal times the later row) whose country equals the place's country (any country when the place names none),
  whose platform equals the place's platform when both name one (a release with an empty platform matches any),
  released from 12 hours before first_seen up to 2 hours after it, skipping a release whose description says
  rollback -> kind release, cause "release <system>: <description>" (the brain's text, docs/contracts/README.md
  item 3). Otherwise kind metric, cause "<metric> <rose|fell> in <place>" (place values joined by " x ", or "the whole
  market"), from the sign of z at first_seen. It never looks at what was injected.
- Output. One JSON file per incident, base-0001.json and on: id, status, first_seen, last_seen, metric, segment,
  threshold (the --z it was run with), drivers (one: the metric, its place, the observed and expected rate and z at
  first_seen), hypotheses (the cause) and a one-sentence report; then threshold.txt with the threshold alone, written
  also when nothing was raised (the scorer reads *.json only). --out is emptied first; a folder that holds anything
  but base-<n>.json files and threshold.txt (the brain's inc-*.json, a notes folder) is refused and left alone, so the
  brain's incidents can never be wiped.

The first version had a floor instead: z = (rate - mean) / max(spread, 1e-6), and judged a history that never varied
like any other. One lost order in a cell whose order rate had always been exactly 1 then scored an abs(z) of 13,000 to
97,000, at any threshold: 644 of its 804 false alarms on the twenty runs (323 of 396 on the ten holdout runs) came from
that alone, and its threshold rule could only take the largest z tried (12). An independent review found the floor
unfair to the plain ranking, and it was replaced by the rule above, which leaves such cell-hours unjudged. The change
only helps the plain ranking: it takes away alarms that had no spread to be measured against, and a cell-hour whose
history spread is at least 1e-6 is judged exactly as before (on the twenty runs, all but 362 of about 9.2 million judged
cell-hours; those 362 are take_rate, whose abs(z) there is now at most 5.3).
"""
from __future__ import annotations

import argparse
import json
import math
import re
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from brain.graph import MetricGraph
from brain.monitor import CUTS, Config, watch_list

HOUR = pd.Timedelta(hours=1)
HISTORY_DAYS = 21           # days of the same hour behind the expectation
MIN_POINTS = 14             # history points a cell-hour needs to be judged
MIN_DENOMINATOR = 30        # denominator a cell-hour needs at t
NEVER_VARIED = 1e-12        # a history spread below this never varied (equal rates leave a rounding spread of ~1e-17)
JOIN_HOURS = 2              # an alert joins an incident whose last alert is at most this many hours before
RELEASE_BEFORE = pd.Timedelta(hours=12)
RELEASE_AFTER = pd.Timedelta(hours=2)
WARMUP_HOURS = Config().min_history_hours + Config().reread_hours    # 80: the brain's replay starts there
DIMS = ("country", "platform", "category", "channel")
TS = "%Y-%m-%dT%H:00:00Z"
OWN_FILE = re.compile(r"base-\d{4,}\.json")
RECORD = "threshold.txt"    # the run's threshold, written even when it raised nothing


def iso(ts) -> str:
    return pd.Timestamp(ts).strftime(TS)


def rates() -> list[tuple[str, str, str]]:
    """(metric, numerator column, denominator column) of every rate the monitor watches."""
    return [(w.metric, w.numerator, w.denominator) for w in watch_list(MetricGraph.load())]


# ------------------------------------------------------------------------------------ reading
def read_tables(db) -> tuple[pd.DataFrame, pd.DataFrame]:
    """facts_hourly (the columns the rates need) and releases, read only. Nothing else is read."""
    path = Path(db)
    if not path.is_file():
        raise FileNotFoundError(f"no database at {path}")
    wal = Path(f"{path}-wal")
    mode = "mode=ro" if wal.is_file() and wal.stat().st_size else "mode=ro&immutable=1"
    columns = sorted({c for _, n, d in rates() for c in (n, d)})
    con = sqlite3.connect(f"{path.resolve().as_uri()}?{mode}", uri=True)
    try:
        facts = pd.read_sql_query(f"SELECT ts, {', '.join(DIMS)}, {', '.join(columns)} FROM facts_hourly", con)
        releases = pd.read_sql_query("SELECT ts, system, country, platform, description FROM releases ORDER BY rowid", con)
    finally:
        con.close()
    return facts, releases


# ------------------------------------------------------------------------------------ scoring every cell-hour
@dataclass
class Scores:
    hours: pd.DatetimeIndex     # every hour from the first to the last of the data
    metrics: list[str]
    cells: list[dict]           # the place of each series, over all cuts; {} is the whole market
    rate: np.ndarray            # [metric, hour, cell]
    expected: np.ndarray        # [metric, hour, cell]: mean of the same hour over the previous 21 days
    z: np.ndarray               # [metric, hour, cell]: nan where the cell-hour is not judged
    spread: np.ndarray | None = None   # [metric, hour, cell]: standard deviation of the history


def _cubes(facts: pd.DataFrame, columns: list[str]) -> tuple[pd.DatetimeIndex, list[dict], dict[str, np.ndarray]]:
    """Hourly sums of each column for every cell of every cut, as arrays [hour, cell]."""
    stamps, at = np.unique(facts["ts"].to_numpy(dtype=str), return_inverse=True)
    stamps = pd.to_datetime(stamps, utc=True)
    first = stamps.min()
    hours = pd.date_range(first, stamps.max(), freq="h")
    h = ((stamps - first) / HOUR).astype(int).to_numpy()[at]
    codes = {d: pd.factorize(facts[d], sort=True) for d in DIMS}
    cells, blocks = [], []
    for dims in CUTS:
        code, keys = np.zeros(len(facts), dtype=np.int64), [()]
        for d in dims:
            c, values = codes[d]
            code = code * len(values) + c
            keys = [k + (str(v),) for k in keys for v in values]
        cells += [dict(zip(dims, k)) for k in keys]
        blocks.append((code, len(keys)))
    sums = {}
    for col in columns:
        weights = facts[col].to_numpy(dtype=float)
        sums[col] = np.concatenate([np.bincount(h * n + code, weights=weights, minlength=len(hours) * n).reshape(len(hours), n)
                                    for code, n in blocks], axis=1)
    return hours, cells, sums


def scores(facts: pd.DataFrame, first_judged: int = WARMUP_HOURS) -> Scores:
    watched = rates()
    hours, cells, sums = _cubes(facts, sorted({c for _, n, d in watched for c in (n, d)}))
    n_hours = len(hours)
    late_enough = (np.arange(n_hours) >= first_judged)[:, None]
    rate, expected, spreads, z = (np.full((len(watched), n_hours, len(cells)), np.nan) for _ in range(4))
    for m, (_, num, den) in enumerate(watched):
        d = sums[den]
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.where(d > 0, sums[num] / d, np.nan)
        history = np.full((HISTORY_DAYS,) + r.shape, np.nan)
        for k in range(1, min(HISTORY_DAYS, (n_hours - 1) // 24) + 1):     # a shorter history has fewer days
            history[k - 1, 24 * k:] = r[:n_hours - 24 * k]
        valid = np.isfinite(history)
        points = valid.sum(axis=0)
        with np.errstate(divide="ignore", invalid="ignore"):
            mean = np.where(valid, history, 0.0).sum(axis=0) / points
            spread = np.sqrt((np.where(valid, history - mean, 0.0) ** 2).sum(axis=0) / (points - 1))
            score = (r - mean) / spread
            varied = spread >= NEVER_VARIED                                  # nan (fewer than 2 points) is not
        judged = (points >= MIN_POINTS) & varied & (d >= MIN_DENOMINATOR) & np.isfinite(r) & late_enough
        rate[m], expected[m], spreads[m], z[m] = r, mean, spread, np.where(judged, score, np.nan)
    return Scores(hours, [w[0] for w in watched], cells, rate, expected, z, spreads)


# ------------------------------------------------------------------------------------ ranking and incidents
@dataclass
class Pick:
    hour: pd.Timestamp
    metric: str
    place: dict
    z: float
    rate: float
    expected: float


def rank(s: Scores, z: float) -> list[Pick]:
    """Per hour, the one series with the largest abs(z) at or above the threshold; ties go to the finer cell."""
    size = np.array([len(c) for c in s.cells])
    strength = np.abs(s.z)
    picks = []
    for h in range(len(s.hours)):
        with np.errstate(invalid="ignore"):
            m, c = np.nonzero(strength[:, h, :] >= z)
        if not len(m):
            continue
        best = np.lexsort((c, m, -size[c], -strength[m, h, c]))[0]
        m, c = int(m[best]), int(c[best])
        picks.append(Pick(s.hours[h], s.metrics[m], dict(s.cells[c]), float(s.z[m, h, c]), float(s.rate[m, h, c]),
                          float(s.expected[m, h, c])))
    return picks


def _nested(a: dict, b: dict) -> bool:
    return a.items() <= b.items() or b.items() <= a.items()


def place_text(place: dict) -> str:
    return " x ".join(place.values()) or "the whole market"


def _text(value) -> str:
    """A text cell; NULL (None or NaN, depending on the pandas string type) is empty."""
    return "" if value is None or (isinstance(value, float) and math.isnan(value)) else str(value)


def _records(releases: pd.DataFrame) -> list[dict]:
    rows = []
    for r in releases.to_dict("records"):
        row = {k: _text(r.get(k)) for k in ("ts", "system", "country", "platform", "description")}
        rows.append(dict(row, at=pd.Timestamp(row["ts"])))
    return rows


def _pick(rows: list[dict], place: dict, first_seen: pd.Timestamp) -> dict | None:
    best = None
    for row in rows:                    # in table order, so on equal times the later row wins
        if not first_seen - RELEASE_BEFORE <= row["at"] <= first_seen + RELEASE_AFTER:
            continue
        if "rollback" in row["description"].lower():
            continue
        if place.get("country") and row["country"] != place["country"]:
            continue
        if place.get("platform") and row["platform"] and row["platform"] != place["platform"]:
            continue
        if best is None or row["at"] >= best["at"]:
            best = row
    return best


def pick_release(releases: pd.DataFrame, place: dict, first_seen) -> dict | None:
    """The latest release in the place from 12 h before first_seen to 2 h after it, rollbacks left out."""
    return _pick(_records(releases), place, pd.Timestamp(first_seen))


def _cause(rows: list[dict], place: dict, first_seen, metric: str, z: float) -> dict:
    hit = _pick(rows, place, pd.Timestamp(first_seen))
    if hit is not None:
        return {"cause": f"release {hit['system']}: {hit['description']}", "kind": "release", "status": "supported"}
    return {"cause": f"{metric} {'rose' if z > 0 else 'fell'} in {place_text(place)}", "kind": "metric",
            "status": "supported"}


def cause(releases: pd.DataFrame, place: dict, first_seen, metric: str, z: float) -> dict:
    """The incident's one hypothesis: the release rule, else the metric that moved."""
    return _cause(_records(releases), place, first_seen, metric, z)


def incidents(picks: list[Pick], releases: pd.DataFrame, threshold: float | None = None) -> list[dict]:
    """Join the hourly picks into incidents and write each one in the shape the scorer reads, with the threshold the
    picks were made at (left out only when none is given)."""
    rows = _records(releases)
    opened: list[dict] = []
    for p in picks:
        joins = [n for n, i in enumerate(opened) if i["first"].metric == p.metric and _nested(i["first"].place, p.place)
                 and p.hour - i["last"] <= JOIN_HOURS * HOUR]
        if joins:
            n = max(joins, key=lambda n: (opened[n]["last"], n))
            opened[n]["last"] = p.hour
            opened[n]["hours"] += 1
        else:
            opened.append({"first": p, "last": p.hour, "hours": 1})
    out = []
    for n, i in enumerate(opened, 1):
        p = i["first"]
        hypothesis = _cause(rows, p.place, p.hour, p.metric, p.z)
        moved = "rose" if p.z > 0 else "fell"
        report = (f"{p.metric} {moved} to {p.rate:.4g} from an expected {p.expected:.4g} in {place_text(p.place)} at "
                  f"{p.hour:%Y-%m-%d %H:00} UTC (z {p.z:+.1f}): the most anomalous series of {i['hours']} hour(s) up to "
                  f"{i['last']:%Y-%m-%d %H:00} UTC; guessed cause: {hypothesis['cause']}")
        incident = {"id": f"base-{n:04d}", "status": "resolved", "first_seen": iso(p.hour), "last_seen": iso(i["last"]),
                    "metric": p.metric, "segment": dict(p.place)}
        if threshold is not None:
            incident["threshold"] = float(threshold)
        incident.update(drivers=[{"metric": p.metric, "segment": dict(p.place), "observed": p.rate,
                                  "expected": p.expected, "z": p.z}],
                        hypotheses=[hypothesis], report=report + ".")
        out.append(incident)
    return out


def run(db, z: float) -> list[dict]:
    """The baseline's incidents for one database at one threshold; every one records the threshold."""
    facts, releases = read_tables(db)
    return incidents(rank(scores(facts), z), releases, threshold=z)


# ------------------------------------------------------------------------------------ writing
def foreign_files(out: Path) -> list[str]:
    """What in the folder this script did not write: anything but base-<n>.json files and its threshold.txt."""
    if not out.exists():
        return []
    if not out.is_dir():
        return [out.name]
    return sorted(p.name for p in out.iterdir() if not (p.is_file() and (OWN_FILE.fullmatch(p.name) or p.name == RECORD)))


def write(out: Path, found: list[dict], threshold: float) -> None:
    """The incident files, then threshold.txt (the threshold, written last and also when nothing was raised: a folder
    without incident files is a measured zero only with it)."""
    texts = {f"{i['id']}.json": json.dumps(i, indent=1, allow_nan=False) + "\n" for i in found}
    texts[RECORD] = f"{float(threshold)!r}\n"
    foreign = foreign_files(out)
    if foreign:
        raise ValueError(f"{out} holds files this script did not write ({', '.join(foreign[:5])}); it is left alone")
    out.mkdir(parents=True, exist_ok=True)
    for p in out.iterdir():
        p.unlink()
    for name, text in texts.items():
        with open(out / name, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", required=True, help="the run's simulator database")
    ap.add_argument("--out", type=Path, required=True, help="folder for base-*.json; emptied first, never one with other files")
    ap.add_argument("--z", type=float, required=True, help="threshold on abs(z) for the most anomalous series of an hour")
    a = ap.parse_args(argv)
    if not (math.isfinite(a.z) and a.z > 0):
        ap.error("the threshold must be a positive number")
    if not Path(a.db).is_file():
        ap.error(f"no database at {a.db}")
    foreign = foreign_files(a.out)
    if foreign:
        ap.error(f"{a.out} holds files this script did not write ({', '.join(foreign[:5])}); it is left alone")
    began = time.time()
    found = run(a.db, a.z)
    try:
        write(a.out, found, a.z)
    except ValueError as e:
        ap.error(str(e))
    print(f"{a.out}: {len(found)} incident(s), abs(z) >= {a.z:g}, {time.time() - began:.1f} s")
    return 0


if __name__ == "__main__":
    sys.exit(main())
