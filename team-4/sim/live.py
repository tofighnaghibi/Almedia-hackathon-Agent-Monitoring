"""Run the simulated clock forward: one new hour of data every few seconds.

    python -m sim.live --speed 2          # one simulated hour every 2 s
    python -m sim.live --speed 0 --hours 6  # as fast as possible, 6 hours

Injections (python -m sim.inject ... --now) are picked up when the clock reaches their start hour,
because every hour is generated with the injections on record at that moment.

The accelerated clock follows clock.seconds_per_hour in settings.json next to the database (written by the
console): a value there wins over --speed, which is the starting value and the fallback when the file says
nothing. It is looked at while waiting for the next hour (only its modification time, unless it changed), so a
change applies within the hour. --wall-clock never reads it.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import time
from pathlib import Path

from sim import db
from sim.clock import sync_current_hour

DEFAULT_SPEED = 2.0
SETTINGS_NAME = "settings.json"
SPEED_RANGE = (0.25, 60.0)     # seconds per simulated hour a settings file may ask for
WAIT_STEP = 0.5                # the file is looked at this often while waiting for the next hour


def seconds_per_hour(settings, start: float) -> tuple[float, str | None]:
    """The seconds per simulated hour a parsed settings.json asks for (None: no file), or `start` when it says
    nothing. A value outside SPEED_RANGE is clamped and one of the wrong type left out, with a warning."""
    if settings is None:
        return start, None
    if not isinstance(settings, dict):
        return start, f"{SETTINGS_NAME} is not a JSON object: --speed {start:g} is used"
    clock = settings.get("clock")
    if clock is None:
        return start, None
    if not isinstance(clock, dict):
        return start, f"clock in {SETTINGS_NAME} is not an object: --speed {start:g} is used"
    value = clock.get("seconds_per_hour")
    if value is None:
        return start, None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return start, f"clock.seconds_per_hour {value!r} is not a number: --speed {start:g} is used"
    low, high = SPEED_RANGE
    used = float(min(max(value, low), high))
    if used != value:
        return used, f"clock.seconds_per_hour {value:g} is outside {low:g} to {high:g}: {used:g} is used"
    return used, None


class Speed:
    """Seconds per simulated hour: settings.json next to the database, re-read only when it changed."""

    def __init__(self, db_path, start: float, say=None):
        self.path = Path(db_path).resolve().parent / SETTINGS_NAME
        self.start = start
        self.speed = start
        self.say = say or (lambda line: print(line, flush=True))
        self._stamp = None          # no file

    def current(self) -> float:
        try:
            st = os.stat(self.path)
            stamp = (st.st_mtime_ns, st.st_size)
        except FileNotFoundError:
            stamp = None
        except OSError:
            return self.speed
        if stamp == self._stamp:
            return self.speed
        warning = None
        if stamp is None:
            speed = self.start
        else:
            try:
                obj = json.loads(self.path.read_text(encoding="utf-8-sig"))
            except FileNotFoundError:
                obj = None
            except OSError:
                return self.speed              # being replaced at this moment: the next look reads it
            except ValueError:                 # not JSON, or not UTF-8
                obj, warning = None, f"{SETTINGS_NAME} could not be parsed: --speed {self.start:g} is used"
            speed, said = seconds_per_hour(obj, self.start)
            warning = warning or said
        self._stamp = stamp
        if warning:                            # the warning names the speed used
            self.say(f"settings: {warning}")
        elif speed != self.speed:
            self.say(f"clock: {speed:g} s per simulated hour" + (f" ({SETTINGS_NAME})" if stamp is not None else ""))
        self.speed = speed
        return speed


def wait_hour(speed, clock=None, sleep=None, step: float = WAIT_STEP) -> None:
    """Wait out one simulated hour, looking at the speed while waiting: a change applies at once."""
    clock = clock or time.monotonic
    sleep = sleep or time.sleep
    began = clock()
    while True:
        left = speed.current() - (clock() - began)
        if left <= 0:
            return
        sleep(min(left, step))


def run_live(path: str, speed: float = DEFAULT_SPEED, hours: int | None = None, on_hour=None) -> int:
    con = db.connect(path)
    speeds = Speed(path, speed)
    done = 0
    try:
        while hours is None or done < hours:
            last = db.last_hour(con)
            idx = db.hour_index(con, last) + 1
            db.write_hours(con, idx, 1)
            done += 1
            if on_hour:
                on_hour(idx)
            if hours is None or done < hours:
                wait_hour(speeds)
    finally:
        con.close()
    return done


def run_wall(path, poll=2.0):
    """Follow real hours; this never accelerates into future data (and never reads settings.json)."""
    con = db.connect(path)
    try:
        while True:
            sync_current_hour(con)
            time.sleep(poll)
    finally:
        con.close()


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", default=db.DEFAULT_DB)
    ap.add_argument("--speed", type=float, default=DEFAULT_SPEED,
                    help="seconds per simulated hour; 0 = no waiting. The starting value: clock.seconds_per_hour "
                         "in settings.json next to the database wins")
    ap.add_argument("--hours", type=int, help="stop after this many hours (default: run until stopped)")
    ap.add_argument("--wall-clock", action="store_true", help="follow real time instead of accelerating")
    a = ap.parse_args(argv)
    if a.wall_clock and a.hours is not None:
        ap.error("--hours belongs to accelerated mode")

    def show(idx):
        con = db.connect(a.db)
        print("hour", idx, db.last_hour(con).strftime("%Y-%m-%dT%H:00:00Z"), flush=True)
        con.close()

    try:
        if a.wall_clock:
            run_wall(a.db)
        else:
            run_live(a.db, a.speed, a.hours, show)
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
