"""Run the monitor.

Live, next to the simulator's clock (started after the backfill, before `python -m sim.live`):

    python -m brain.run --fresh

With --narrate the live loop also asks a language model for a short narration of each incident,
written from the incident's own evidence and checked against it (brain/narrate.py; needs an API key
with credit, falls back to nothing but the template report otherwise).

Replay of a finished database hour by hour, as if it were live (for the scoring harness):

    python -m brain.run --replay --db data/eval.db --incidents eval_incidents --fresh

Paths default to the environment variables the console uses (MARKET_DB, INCIDENTS_DIR, GRAPH_JSON),
then to data/market.db, incidents/ and no graph export. Stop it with Ctrl+C before a database is
rebuilt: on Windows an open reader blocks deleting the file.

The live loop also follows the console's settings.json next to the database (brain/settings.py): the
detection thresholds and narration on or off apply from the next poll, without a restart, and a new key in
the repository's .env builds a new narrator. It writes brain_status.json there on every poll. --narrate is
the starting value while the file says nothing about narration. The replay never reads either file.
"""
from __future__ import annotations

import argparse
import dataclasses
import os
import sys
import time
import traceback
from pathlib import Path

import pandas as pd

from brain import settings
from brain.clock import HOUR, Clock
from brain.data import Store, iso_hour
from brain.extension import Extension
from brain.graph import MetricGraph
from brain.incidents import Book, write_json
from brain.monitor import Config, Monitor
from brain.narrate import ENV_FILE, MODEL, Narrator, describe as describe_narrator, key_set

_UNSEEN = ("never read",)     # a stamp no file has: the first poll always reads settings.json


def export_graph(graph: MetricGraph, store: Store, path: Path) -> None:
    """Validate the graph on the data, then publish it where the console reads it."""
    from brain.validate import summarize, validate_graph
    print(summarize(validate_graph(graph, store, days=7)))
    write_json(path, graph.to_dict())
    print(f"graph written to {path}")


def describe(tick, written) -> str:
    line = f"{iso_hour(tick.newest)}  alerts {len(tick.alerts):3d}"
    if tick.held:
        line += f"  held {len(tick.held)} h"
    for body in written:
        seg = " x ".join(body["segment"].values()) or "all"
        driver = body["drivers"][0]["metric"]
        line += f"\n    {body['id']} {body['status']:8s} {driver} in {seg}"
        if body["hypotheses"] and body["hypotheses"][0]["status"] == "supported":
            line += f"  <- {body['hypotheses'][0]['cause']}"
    return line


def replay(monitor: Monitor, book: Book, start=None, end=None, quiet: bool = False) -> int:
    first, last = monitor.store.time_range()
    cfg = monitor.config
    t = pd.Timestamp(start) if start else first + (cfg.min_history_hours + cfg.reread_hours) * HOUR
    stop = pd.Timestamp(end) if end else last + HOUR
    ticks, began = 0, time.time()
    while t <= stop:
        tick = monitor.tick(as_of=t)
        written = book.update(tick)
        if written and not quiet:
            print(describe(tick, written), flush=True)
        t += HOUR
        ticks += 1
    opened = len(book.incidents)
    print(f"replayed {ticks} hours in {time.time() - began:.1f} s: {opened} incident(s) in {book.folder}")
    return opened


def watched_series(monitor) -> int:
    """Series judged on each tick: every watched rate in each of its cuts, every count in each of its cuts
    (760 on the simulator's graph). 0 before the first tick has loaded the data."""
    cubes = getattr(monitor, "cubes", None) or {}
    cfg = getattr(monitor, "config", None)
    if cfg is None:
        return 0

    def places(cuts):
        return sum(len(cubes[cut].keys) for cut in cuts if cut in cubes)
    return (len(getattr(monitor, "watched", ())) * places(cfg.cuts)
            + len(getattr(monitor, "volumes", ())) * places(cfg.volume_cuts))


class Control:
    """The console's settings.json (next to the database), applied to the running monitor between ticks, and
    the monitor's status, brain_status.json in the same folder, written on every poll. Live loop only.

    A value in the file wins; a value it leaves out falls back to the starting value: the monitor's config as it
    was built, --narrate for narration. A broken file counts as empty and is reported in settings_error."""

    def __init__(self, db, monitor, book, narrate: bool = False, env_file=ENV_FILE, make_narrator=None):
        self.settings_file = settings.settings_path(db)
        self.status_file = settings.status_path(db)
        self.base = monitor.config                 # what a value left out of the file falls back to
        self.narrate = narrate                     # the command line's wish, while the file says nothing
        self.env_file = Path(env_file) if env_file is not None else None
        self.make_narrator = make_narrator or (lambda: Narrator(env_file=self.env_file))
        self.wanted = narrate                      # narration as applied
        self.error: str | None = None
        self.applied_at = settings.utc_now()
        self._seen = _UNSEEN
        self._env_seen = settings.stamp(self.env_file)
        self._key_set = key_set(self.env_file)
        self._status_failed = False

    # ------------------------------------------------------------------ settings.json and .env
    def poll(self, monitor, book) -> list[str]:
        """Apply what changed since the last poll. Returns the lines to print."""
        lines, built = [], False
        env = settings.stamp(self.env_file)
        env_changed = env != self._env_seen
        if env_changed:
            self._env_seen, self._key_set = env, key_set(self.env_file)
        seen = settings.stamp(self.settings_file)
        if seen != self._seen:
            try:
                read = settings.read(self.settings_file)
            except OSError as e:                   # being replaced at this moment: read it on the next poll
                self.error = f"{self.settings_file.name} could not be read: {type(e).__name__}"
            else:
                self._seen = seen
                built = self._apply(read, monitor, book)
                lines.append(self._said(read, monitor, book))
        if env_changed and self.wanted and not built:    # a new key: a new narrator that reads it
            self._stop_narrator(book)
            self._start_narrator(book)
            lines.append(f"settings: the key file changed; {describe_narrator(book.narrator)}")
        return lines

    def _apply(self, read: settings.Settings, monitor, book) -> bool:
        """Thresholds and narration from the file. Returns whether a narrator was built."""
        given = {name: read.get("detection." + name) for name in settings.DETECTION}
        monitor.config = dataclasses.replace(self.base, **{k: v for k, v in given.items() if v is not None})
        self.wanted = bool(read.get("narration.enabled", self.narrate))
        built = False
        if self.wanted and book.narrator is None:
            self._start_narrator(book)
            built = True
        elif not self.wanted and book.narrator is not None:
            self._stop_narrator(book)
        self.error = read.problems()
        self.applied_at = settings.utc_now()
        return built

    def _start_narrator(self, book) -> None:
        book.narrator = self.make_narrator()

    @staticmethod
    def _stop_narrator(book) -> None:
        if book.narrator is not None:
            book.narrator.close()                  # requests in flight are dropped, nothing new is submitted
            book.narrator = None

    def _said(self, read: settings.Settings, monitor, book) -> str:
        if read.error:
            return f"settings: {read.error}"
        applied = self.applied(monitor)
        line = "settings: applied " + ", ".join(f"{k} {applied[k]:g}" for k in settings.DETECTION)
        line += ", " + (describe_narrator(book.narrator) if self.wanted else "narration: not asked")
        if not self.settings_file.exists():
            line += f" (no {self.settings_file.name}: the command line and the defaults)"
        if read.warnings:
            line += "; " + "; ".join(read.warnings)
        return line

    # ------------------------------------------------------------------ brain_status.json
    def applied(self, monitor) -> dict:
        return {"narration_enabled": self.wanted, **{k: getattr(monitor.config, k) for k in settings.DETECTION}}

    def status(self, monitor, book, newest) -> dict:
        narrator = book.narrator
        if not self.wanted:
            told = "not asked"
        elif narrator is None:
            told = "off: no narrator"
        else:
            told = narrator.status
        return {
            "pid": os.getpid(),
            "updated_at": settings.utc_now(),
            "newest_hour": iso_hour(newest) if newest is not None else None,
            "watched_series": watched_series(monitor),
            "incidents_folder": str(Path(book.folder).resolve()),
            "narration": {"status": told, "model": getattr(narrator, "model", MODEL) or MODEL, "key_set": self._key_set},
            "applied": self.applied(monitor),
            "applied_at": self.applied_at,
            "settings_error": self.error,
        }

    def write_status(self, monitor, book, newest) -> None:
        try:
            settings.write_status(self.status_file, self.status(monitor, book, newest))
            self._status_failed = False
        except Exception as e:                     # the console misses a beat; the monitor goes on. Said once.
            if not self._status_failed:
                print(f"status: {self.status_file.name} not written: {type(e).__name__}: {e}", file=sys.stderr,
                      flush=True)
            self._status_failed = True


def live(monitor: Monitor, book: Book, poll: float, max_ticks: int | None = None, quiet: bool = False,
         extension: Extension | None = None, control: Control | None = None) -> None:
    last_seen, idle, ticks, newest = None, 0, 0, None
    narrator = book.narrator
    said = narrator.status if narrator is not None else None   # what the start line already told
    print("monitor is watching; Ctrl+C stops it", flush=True)
    while max_ticks is None or ticks < max_ticks:
        if control is not None:
            try:
                for line in control.poll(monitor, book):   # settings.json and .env: applied before the tick
                    print(line, flush=True)
            except Exception:
                traceback.print_exc()
            if book.narrator is not narrator:              # switched on, off or rebuilt: the line above told it
                narrator = book.narrator
                said = narrator.status if narrator is not None else None
        try:
            if extension is not None:
                try:
                    extension.poll()   # new metric definitions in the incoming folder; it answers them itself
                except Exception:      # whatever happens there must not cost the monitor its tick
                    traceback.print_exc()
            newest = Clock(monitor.store).newest_hour()
            if newest is not None and (newest != last_seen or idle >= 4):   # also re-read when nothing new came
                tick = monitor.tick()
                written = book.update(tick)
                if not quiet and (written or newest != last_seen):
                    print(describe(tick, written), flush=True)
                last_seen, idle = newest, 0
                ticks += 1
            else:
                idle += 1
        except Exception:   # a locked file or a busy database must not end the monitor: say so and try again
            traceback.print_exc()
            print("tick failed; trying again", file=sys.stderr, flush=True)
        if narrator is not None and narrator.status != said:   # a refused key shows only on a request,
            said = narrator.status                               # so say it here, even when the tick failed
            print(describe_narrator(narrator), flush=True)
        if control is not None:
            control.write_status(monitor, book, newest)
        time.sleep(poll)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=os.environ.get("MARKET_DB", "data/market.db"))
    ap.add_argument("--incidents", default=os.environ.get("INCIDENTS_DIR", "incidents"))
    ap.add_argument("--graph-out", default=os.environ.get("GRAPH_JSON"),
                    help="validate the graph and write its export here (live mode)")
    ap.add_argument("--incoming", default=os.environ.get("INCOMING_METRICS_DIR", "incoming_metrics"),
                    help="live: folder watched for new metric definitions (*.yaml); the answers are written next to "
                         "them, and --fresh removes the old answers there (the YAML files stay)")
    ap.add_argument("--fresh", action="store_true", help="start with an empty incidents folder")
    ap.add_argument("--replay", action="store_true", help="walk the whole database hour by hour, then stop")
    ap.add_argument("--from", dest="start", help="replay: first clock value (default: after the minimum history)")
    ap.add_argument("--to", dest="end", help="replay: last clock value (default: one hour after the newest data)")
    ap.add_argument("--poll", type=float, default=0.5, help="live: seconds between looks at the database")
    ap.add_argument("--max-ticks", type=int, help="live: stop after this many ticks (for tests)")
    ap.add_argument("--quiet", action="store_true")
    ap.add_argument("--narrate", action="store_true",
                    help="live: a language model narrates each incident from its evidence (brain/narrate.py); "
                         "the starting value: narration.enabled in settings.json next to the database wins")
    a = ap.parse_args(argv)

    store = Store(a.db)
    graph = MetricGraph.load()
    monitor = Monitor(graph, store, Config())
    narrator = Narrator() if a.narrate and not a.replay else None
    if a.narrate and a.replay:
        print("narration is for the live loop; the replay runs without it")
    book = Book(monitor, a.incidents, fresh=a.fresh, narrator=narrator)
    try:
        if a.replay:
            replay(monitor, book, a.start, a.end, a.quiet)
        else:
            if a.graph_out:
                export_graph(graph, store, Path(a.graph_out))
            extension = Extension(graph, store, a.incoming, a.graph_out, monitor, fresh=a.fresh, quiet=a.quiet)
            if a.narrate:
                print(describe_narrator(narrator), flush=True)
            control = Control(a.db, monitor, book, narrate=a.narrate)
            print(f"settings: following {control.settings_file}; status in {control.status_file}", flush=True)
            live(monitor, book, a.poll, a.max_ticks, a.quiet, extension, control)
    except KeyboardInterrupt:
        print("stopped")
    finally:
        for n in {id(x): x for x in (narrator, book.narrator) if x is not None}.values():
            n.close()
        store.conn.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
