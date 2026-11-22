"""The simulator's accelerated clock follows clock.seconds_per_hour in settings.json next to its database."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from sim import live

_moves = [0]


def write(path, obj) -> None:
    text = obj if isinstance(obj, str) else json.dumps(obj)
    path.write_text(text, encoding="utf-8", newline="\n")
    _moves[0] += 1
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + _moves[0] * 1_000_000_000))


@pytest.mark.parametrize("obj,start,speed,warned", [
    ({"clock": {"seconds_per_hour": 0.5}}, 2.0, 0.5, False),
    ({"clock": {"seconds_per_hour": 7}}, 2.0, 7.0, False),
    ({}, 2.0, 2.0, False),
    ({"detection": {"min_surprise": 8}}, 3.0, 3.0, False),
    (None, 0.0, 0.0, False),                                       # no file: --speed, even 0
    ({"clock": {"seconds_per_hour": None}}, 2.0, 2.0, False),
    ({"clock": {"seconds_per_hour": 0.1}}, 2.0, 0.25, True),       # clamped to the range 0.25 .. 60
    ({"clock": {"seconds_per_hour": 100}}, 2.0, 60.0, True),
    ({"clock": {"seconds_per_hour": "fast"}}, 2.0, 2.0, True),
    ({"clock": {"seconds_per_hour": True}}, 2.0, 2.0, True),
    ({"clock": "fast"}, 2.0, 2.0, True),
    ([1, 2], 2.0, 2.0, True),
])
def test_seconds_per_hour_from_the_settings(obj, start, speed, warned):
    got, warning = live.seconds_per_hour(obj, start)
    assert got == speed and bool(warning) == warned


def test_the_speed_follows_the_file_and_goes_back_to_the_command_line(tmp_path):
    said = []
    speed = live.Speed(tmp_path / "market.db", 2.0, say=said.append)
    assert speed.current() == 2.0 and said == []
    write(tmp_path / "settings.json", {"clock": {"seconds_per_hour": 0.5}})
    assert speed.current() == 0.5
    assert speed.current() == 0.5 and len(said) == 1 and "0.5" in said[0]   # said once per change
    write(tmp_path / "settings.json", "{broken")
    assert speed.current() == 2.0 and "settings.json" in said[-1]
    (tmp_path / "settings.json").unlink()
    assert speed.current() == 2.0


def test_waiting_an_hour_takes_up_a_new_speed_while_it_waits():
    now = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    speeds = iter([60.0, 60.0] + [1.0] * 10)                          # changed from 60 s to 1 s during the wait
    live.wait_hour(SimpleNamespace(current=lambda: next(speeds)), clock=lambda: now[0], sleep=sleep)
    assert now[0] == pytest.approx(1.0) and max(sleeps) <= live.WAIT_STEP


def test_waiting_an_hour_at_a_steady_speed_waits_that_long():
    now = [0.0]

    def sleep(seconds):
        now[0] += seconds

    live.wait_hour(SimpleNamespace(current=lambda: 2.0), clock=lambda: now[0], sleep=sleep)
    assert now[0] == pytest.approx(2.0)
    now[0] = 0.0
    live.wait_hour(SimpleNamespace(current=lambda: 0.0), clock=lambda: now[0], sleep=sleep)
    assert now[0] == 0.0                                              # --speed 0: no waiting


def test_the_accelerated_loop_reads_the_speed_from_the_file(tmp_path, monkeypatch):
    from sim.run import backfill
    path = backfill(str(tmp_path / "x.db"), 48, "2026-10-25T00:00:00Z", seed=5)
    now = [0.0]
    sleeps = []

    def sleep(seconds):
        sleeps.append(seconds)
        now[0] += seconds

    monkeypatch.setattr(live.time, "sleep", sleep)
    monkeypatch.setattr(live.time, "monotonic", lambda: now[0])
    assert live.run_live(path, speed=0, hours=2) == 2 and sleeps == []          # no file: --speed 0
    write(tmp_path / "settings.json", {"clock": {"seconds_per_hour": 0.25}})
    assert live.run_live(path, speed=0, hours=3) == 3
    assert now[0] == pytest.approx(0.5)                                          # two waits between three hours
