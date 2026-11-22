"""The console's runtime settings (settings.json next to the database), applied by the live monitor without a
restart, and the monitor's status file (brain_status.json) that the console reads back."""
from __future__ import annotations

import json
import os
import shutil
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import anthropic
import pandas as pd
import pytest

import brain.run as run
from brain import narrate, settings
from brain.data import Store
from brain.graph import MetricGraph
from brain.monitor import Config, Monitor
from brain.narrate import Narrator

FAKE_KEY = "sk-ant-test-000"          # never a real key; short enough that the guardrail scan ignores it
OTHER_KEY = "sk-ant-test-111"
NOW = pd.Timestamp("2026-11-03T12:00:00Z")
H = pd.Timedelta(hours=1)
STATUS_KEYS = {"pid", "updated_at", "newest_hour", "watched_series", "incidents_folder", "narration", "applied",
               "applied_at", "settings_error"}

_moves = [0]


def write(path: Path, obj) -> None:
    """Write like the console, then move the modification time on: two writes in one clock step still differ."""
    text = obj if isinstance(obj, str) else json.dumps(obj)
    path.write_text(text, encoding="utf-8", newline="\n")
    _moves[0] += 1
    st = path.stat()
    os.utime(path, ns=(st.st_atime_ns, st.st_mtime_ns + _moves[0] * 1_000_000_000))


# ------------------------------------------------------------------------------------------- reading the file
def test_a_missing_file_says_nothing_and_is_not_an_error(tmp_path):
    s = settings.read(tmp_path / "settings.json")
    assert s.values == {} and s.warnings == () and s.error is None


def test_the_defaults_are_todays_values():
    from sim.live import DEFAULT_SPEED
    cfg = Config()
    assert settings.DEFAULTS == {
        "narration.enabled": False, "clock.seconds_per_hour": DEFAULT_SPEED,
        "detection.min_surprise": cfg.min_surprise, "detection.min_effect": cfg.min_effect,
        "detection.volume_min_effect": cfg.volume_min_effect,
        "detection.release_min_surprise": cfg.release_min_surprise}


def test_a_full_file_is_read_as_written(tmp_path):
    p = tmp_path / "settings.json"
    write(p, {"narration": {"enabled": True}, "clock": {"seconds_per_hour": 0.5},
              "detection": {"min_surprise": 8, "min_effect": 0.2, "volume_min_effect": 0.3, "release_min_surprise": 5},
              "updated_at": "2026-11-22T11:20:00Z"})
    s = settings.read(p)
    assert s.error is None and s.warnings == ()
    assert s.values == {"narration.enabled": True, "clock.seconds_per_hour": 0.5, "detection.min_surprise": 8.0,
                        "detection.min_effect": 0.2, "detection.volume_min_effect": 0.3,
                        "detection.release_min_surprise": 5.0}
    assert all(type(v) is float for k, v in s.values.items() if k != "narration.enabled")


@pytest.mark.parametrize("name,given,used", [
    ("clock.seconds_per_hour", 0.1, 0.25), ("clock.seconds_per_hour", 100, 60.0),
    ("detection.min_surprise", 3, 5.0), ("detection.min_surprise", 12, 10.0),
    ("detection.min_effect", 0.01, 0.03), ("detection.min_effect", 0.9, 0.5),
    ("detection.volume_min_effect", 0.0, 0.05), ("detection.volume_min_effect", 1.0, 0.6),
    ("detection.release_min_surprise", 1, 3.0), ("detection.release_min_surprise", 9, 7.0),
])
def test_a_value_out_of_range_is_clamped_with_a_warning(tmp_path, name, given, used):
    section, key = name.split(".")
    write(tmp_path / "settings.json", {section: {key: given}})
    s = settings.read(tmp_path / "settings.json")
    assert s.values == {name: used} and s.error is None
    [warning] = s.warnings
    assert name in warning and f"{used:g}" in warning


@pytest.mark.parametrize("obj,name", [
    ({"narration": {"enabled": "yes"}}, "narration.enabled"),
    ({"narration": {"enabled": 1}}, "narration.enabled"),
    ({"detection": {"min_surprise": "8"}}, "detection.min_surprise"),
    ({"detection": {"min_surprise": True}}, "detection.min_surprise"),
    ({"clock": {"seconds_per_hour": [2]}}, "clock.seconds_per_hour"),
    ({"detection": "strict"}, "detection"),
])
def test_a_value_of_the_wrong_type_is_left_out_with_a_warning(tmp_path, obj, name):
    write(tmp_path / "settings.json", obj)
    s = settings.read(tmp_path / "settings.json")
    assert s.values == {} and s.error is None
    [warning] = s.warnings
    assert name in warning


def test_nan_and_null_are_not_numbers(tmp_path):
    write(tmp_path / "settings.json", '{"detection": {"min_surprise": NaN, "min_effect": null}}')
    s = settings.read(tmp_path / "settings.json")
    assert s.values == {} and len(s.warnings) == 1 and "min_surprise" in s.warnings[0]   # null = not set


@pytest.mark.parametrize("text", ["{not json", "[1, 2]", "", '"fast"', "\x00\xff"])
def test_a_broken_file_counts_as_empty_and_says_why(tmp_path, text):
    p = tmp_path / "settings.json"
    if text == "\x00\xff":
        p.write_bytes(b"\xff\xfe\xfa")
    else:
        write(p, text)
    s = settings.read(p)
    assert s.values == {} and s.error and "settings.json" in s.error


def test_the_files_live_next_to_the_database(tmp_path):
    db = tmp_path / "data" / "market.db"
    assert settings.settings_path(db) == (tmp_path / "data" / "settings.json").resolve()
    assert settings.status_path(db) == (tmp_path / "data" / "brain_status.json").resolve()
    assert settings.settings_path("market.db") == Path("settings.json").resolve()


def test_the_status_file_is_written_whole_utf8_lf_without_leftovers(tmp_path):
    p = tmp_path / "brain_status.json"
    settings.write_status(p, {"pid": 1, "folder": "D:/é"})
    settings.write_status(p, {"pid": 2, "folder": "D:/é"})
    raw = p.read_bytes()
    assert b"\r\n" not in raw and raw.endswith(b"\n")
    assert json.loads(raw.decode("utf-8")) == {"pid": 2, "folder": "D:/é"}
    assert [f.name for f in tmp_path.iterdir()] == ["brain_status.json"]


# ------------------------------------------------------------------------------------------- the live loop
class OneHourClock:
    """A clock whose newest hour never changes."""

    def __init__(self, store):
        pass

    def newest_hour(self):
        return NOW


class FakeNarrator:
    def __init__(self, status="on"):
        self.status, self.model, self.closed = status, "m", False

    def close(self):
        self.closed = True


def fake_monitor():
    return SimpleNamespace(store=None, config=Config(), tick=lambda: SimpleNamespace())


def fake_book(tmp_path, narrator=None):
    return SimpleNamespace(narrator=narrator, update=lambda tick: [], folder=tmp_path / "incidents")


@pytest.fixture
def loop(monkeypatch):
    monkeypatch.setattr(run, "Clock", OneHourClock)
    monkeypatch.setattr(run.time, "sleep", lambda seconds: None)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)


def one_poll(monitor, book, control):
    run.live(monitor, book, poll=0.0, max_ticks=1, quiet=True, control=control)


def status(tmp_path) -> dict:
    return json.loads((tmp_path / "brain_status.json").read_text(encoding="utf-8"))


def detection(cfg) -> tuple:
    return cfg.min_surprise, cfg.min_effect, cfg.volume_min_effect, cfg.release_min_surprise


def test_the_live_loop_applies_new_thresholds_without_a_restart(tmp_path, loop, capsys):
    monitor, book = fake_monitor(), fake_book(tmp_path)
    control = run.Control(tmp_path / "market.db", monitor, book, narrate=False, env_file=tmp_path / ".env")
    one_poll(monitor, book, control)
    assert monitor.config == Config()                                   # no file: today's values

    write(tmp_path / "settings.json", {"detection": {"min_surprise": 8.5, "min_effect": 0.2,
                                                     "volume_min_effect": 0.3, "release_min_surprise": 5}})
    one_poll(monitor, book, control)
    assert detection(monitor.config) == (8.5, 0.2, 0.3, 5.0)
    assert replace(monitor.config, min_surprise=7.0, min_effect=0.1, volume_min_effect=0.2,
                   release_min_surprise=4.0) == Config()               # nothing else moved

    write(tmp_path / "settings.json", {"detection": {"min_surprise": 6}})
    one_poll(monitor, book, control)
    assert detection(monitor.config) == (6.0, 0.1, 0.2, 4.0)            # a value left out goes back to the default
    said = [line for line in capsys.readouterr().out.splitlines() if line.startswith("settings:")]
    assert len(said) == 3 and all(line.startswith("settings: applied") for line in said)
    assert "min_surprise 6" in said[-1]


def test_an_unchanged_file_is_not_applied_again(tmp_path, loop, capsys):
    monitor, book = fake_monitor(), fake_book(tmp_path)
    write(tmp_path / "settings.json", {"detection": {"min_surprise": 9}})
    control = run.Control(tmp_path / "market.db", monitor, book, env_file=tmp_path / ".env")
    run.live(monitor, book, poll=0.0, max_ticks=3, quiet=True, control=control)
    assert capsys.readouterr().out.count("settings: applied") == 1


def test_a_broken_file_falls_back_to_the_defaults_and_says_why(tmp_path, loop, capsys):
    monitor, book = fake_monitor(), fake_book(tmp_path)
    control = run.Control(tmp_path / "market.db", monitor, book, env_file=tmp_path / ".env")
    write(tmp_path / "settings.json", {"detection": {"min_surprise": 9}})
    one_poll(monitor, book, control)
    assert monitor.config.min_surprise == 9.0 and status(tmp_path)["settings_error"] is None
    write(tmp_path / "settings.json", '{"detection": {"min_surprise": 9')
    one_poll(monitor, book, control)
    assert monitor.config == Config()
    error = status(tmp_path)["settings_error"]
    assert error and "settings.json" in error
    assert any(line.startswith("settings: ") and "settings.json" in line
               for line in capsys.readouterr().out.splitlines())


def test_narration_is_switched_on_and_off_by_the_file(tmp_path, loop):
    built = []

    def make():
        built.append(FakeNarrator())
        return built[-1]

    monitor, book = fake_monitor(), fake_book(tmp_path)
    control = run.Control(tmp_path / "market.db", monitor, book, narrate=False, env_file=tmp_path / ".env",
                          make_narrator=make)
    one_poll(monitor, book, control)
    assert book.narrator is None and built == [] and status(tmp_path)["narration"]["status"] == "not asked"

    write(tmp_path / "settings.json", {"narration": {"enabled": True}})
    one_poll(monitor, book, control)
    assert book.narrator is built[0]
    st = status(tmp_path)
    assert st["narration"]["status"] == "on" and st["applied"]["narration_enabled"] is True

    write(tmp_path / "settings.json", {"narration": {"enabled": False}})
    one_poll(monitor, book, control)
    assert book.narrator is None and built[0].closed and len(built) == 1   # closed: nothing more is submitted
    assert status(tmp_path)["narration"]["status"] == "not asked"


def test_the_command_line_is_the_starting_value_while_the_file_says_nothing(tmp_path, loop):
    built = []

    def make():
        built.append(FakeNarrator())
        return built[-1]

    first = FakeNarrator()
    monitor, book = fake_monitor(), fake_book(tmp_path, first)
    control = run.Control(tmp_path / "market.db", monitor, book, narrate=True, env_file=tmp_path / ".env",
                          make_narrator=make)
    one_poll(monitor, book, control)
    write(tmp_path / "settings.json", {"detection": {"min_surprise": 8}})   # says nothing about narration
    one_poll(monitor, book, control)
    assert book.narrator is first and built == [] and not first.closed
    write(tmp_path / "settings.json", {"narration": {"enabled": False}})
    one_poll(monitor, book, control)
    assert book.narrator is None and first.closed
    (tmp_path / "settings.json").unlink()                                   # back to --narrate
    one_poll(monitor, book, control)
    assert book.narrator is built[0]


def test_narration_asked_without_a_key_is_off_and_says_so(tmp_path, loop):
    monitor, book = fake_monitor(), fake_book(tmp_path)
    write(tmp_path / "settings.json", {"narration": {"enabled": True}})
    control = run.Control(tmp_path / "market.db", monitor, book, env_file=tmp_path / ".env")
    one_poll(monitor, book, control)
    st = status(tmp_path)["narration"]
    assert st["status"].startswith("off: no key") and st["key_set"] is False


@pytest.fixture
def fake_sdk(monkeypatch):
    """anthropic.Anthropic that records the key it was given and never touches the network."""
    seen = []

    class FakeAnthropic:
        def __init__(self, **kw):
            seen.append(kw.get("api_key"))

    monkeypatch.setattr(anthropic, "Anthropic", FakeAnthropic)
    return seen


def test_a_new_key_in_env_builds_a_new_narrator_without_a_restart(tmp_path, loop, fake_sdk, capsys):
    env = tmp_path / ".env"
    write(tmp_path / "settings.json", {"narration": {"enabled": True}})
    monitor, book = fake_monitor(), fake_book(tmp_path)
    control = run.Control(tmp_path / "market.db", monitor, book, env_file=env)
    one_poll(monitor, book, control)
    assert book.narrator.status.startswith("off: no key") and status(tmp_path)["narration"]["key_set"] is False

    write(env, f"OTHER=1\nANTHROPIC_API_KEY={FAKE_KEY}\n")
    one_poll(monitor, book, control)
    first = book.narrator
    assert first.status == "on" and fake_sdk == [FAKE_KEY]
    assert status(tmp_path)["narration"] == {"status": "on", "model": narrate.MODEL, "key_set": True}

    write(env, f"ANTHROPIC_API_KEY={OTHER_KEY}\n")                          # replaced from the console
    one_poll(monitor, book, control)
    assert book.narrator is not first and fake_sdk[-1] == OTHER_KEY and book.narrator.status == "on"
    assert first._pool._shutdown

    write(env, "OTHER=1\n")                                                  # removed from the console
    one_poll(monitor, book, control)
    assert book.narrator.status.startswith("off: no key") and status(tmp_path)["narration"]["key_set"] is False
    assert "ANTHROPIC_API_KEY" not in os.environ                            # read from the file, not cached
    out = capsys.readouterr().out
    assert "sk-ant" not in out


def test_a_changed_env_without_narration_builds_nothing(tmp_path, loop, fake_sdk):
    env = tmp_path / ".env"
    monitor, book = fake_monitor(), fake_book(tmp_path)
    control = run.Control(tmp_path / "market.db", monitor, book, env_file=env)
    one_poll(monitor, book, control)
    write(env, f"ANTHROPIC_API_KEY={FAKE_KEY}\n")
    one_poll(monitor, book, control)
    assert book.narrator is None and fake_sdk == [] and status(tmp_path)["narration"]["key_set"] is True


def test_the_narrator_takes_the_key_from_the_file_before_the_environment(tmp_path, monkeypatch, fake_sdk):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    env = tmp_path / ".env"
    write(env, f"ANTHROPIC_API_KEY={OTHER_KEY}\n")
    assert Narrator(env_file=env).status == "on" and fake_sdk == [OTHER_KEY]
    env.unlink()
    assert Narrator(env_file=env).status == "on" and fake_sdk[-1] in (None, FAKE_KEY)
    assert narrate.key_set(env) is True
    monkeypatch.delenv("ANTHROPIC_API_KEY")
    assert narrate.key_set(env) is False and narrate.key_set(None) is False


def test_the_status_file_says_what_is_applied_and_never_holds_the_key(tmp_path, loop, monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", FAKE_KEY)
    write(tmp_path / ".env", f"ANTHROPIC_API_KEY={OTHER_KEY}\n")
    write(tmp_path / "settings.json", {"detection": {"min_surprise": 12}})      # clamped to 10, and said
    monitor, book = fake_monitor(), fake_book(tmp_path)
    control = run.Control(tmp_path / "market.db", monitor, book, env_file=tmp_path / ".env")
    one_poll(monitor, book, control)
    text = (tmp_path / "brain_status.json").read_text(encoding="utf-8")
    st = json.loads(text)
    assert set(st) == STATUS_KEYS
    assert st["pid"] == os.getpid() and st["newest_hour"] == "2026-11-03T12:00:00Z"
    assert st["incidents_folder"] == str((tmp_path / "incidents").resolve())
    assert st["narration"] == {"status": "not asked", "model": narrate.MODEL, "key_set": True}
    assert st["applied"] == {"narration_enabled": False, "min_surprise": 10.0, "min_effect": 0.1,
                             "volume_min_effect": 0.2, "release_min_surprise": 4.0}
    assert "detection.min_surprise" in st["settings_error"]
    for stamp in (st["updated_at"], st["applied_at"]):
        assert pd.Timestamp(stamp).tzinfo is not None and stamp.endswith("Z")
    assert isinstance(st["watched_series"], int)
    assert "sk-ant" not in text and FAKE_KEY not in text and OTHER_KEY not in text


def test_the_status_is_written_on_every_poll_even_when_the_tick_fails(tmp_path, loop, monkeypatch, capsys):
    def boom():
        raise RuntimeError("database is locked")

    def sleep(seconds, polls=[]):
        polls.append(seconds)
        if len(polls) >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(run.time, "sleep", sleep)
    monitor = SimpleNamespace(store=None, config=Config(), tick=boom)
    book = fake_book(tmp_path)
    control = run.Control(tmp_path / "market.db", monitor, book, env_file=tmp_path / ".env")
    with pytest.raises(KeyboardInterrupt):
        run.live(monitor, book, poll=0.0, quiet=True, control=control)
    assert status(tmp_path)["newest_hour"] == "2026-11-03T12:00:00Z"
    assert "tick failed" in capsys.readouterr().err


# ------------------------------------------------------------------------------------------- on real data
FAULT = pd.Timestamp("2026-11-03T12:00:00Z")


@pytest.fixture(scope="module")
def faulty_db(tmp_path_factory):
    from sim import db as simdb
    from sim import inject
    from sim.run import backfill
    path = backfill(str(tmp_path_factory.mktemp("settings") / "m.db"), 10 * 24, "2026-10-25T00:00:00Z", seed=11)
    con = simdb.connect(path)
    try:
        inject.payment_failure(con, "DE", "Android", FAULT, 0.4, end=FAULT + 3 * H)
    finally:
        con.close()
    return path


def test_every_threshold_is_read_from_the_config_at_tick_time(faulty_db, tmp_path):
    shutil.copy(faulty_db, tmp_path / "s.db")
    store = Store(tmp_path / "s.db")
    try:
        monitor = Monitor(MetricGraph.load(), store)
        loose = monitor.tick(as_of=FAULT + 2 * H)
        assert loose.alerts
        assert run.watched_series(monitor) == 15 * 48 + 2 * 20          # README: 720 rate series and 40 counts
        monitor.config = replace(monitor.config, min_surprise=1e9, min_effect=1e9, volume_min_effect=1e9,
                                 release_min_surprise=1e9)
        strict = monitor.tick(as_of=FAULT + 2 * H)
        assert strict.alerts == []
    finally:
        store.conn.close()


def test_the_replay_never_reads_the_settings(faulty_db, tmp_path, monkeypatch):
    folder = tmp_path / "db"
    folder.mkdir()
    shutil.copy(faulty_db, folder / "m.db")
    write(folder / "settings.json", {"narration": {"enabled": True}, "detection": {"min_surprise": 10}})
    monkeypatch.setattr(run, "Control", lambda *a, **k: pytest.fail("the replay read settings.json"))
    monkeypatch.setattr(narrate.Narrator, "__init__", lambda *a, **k: pytest.fail("the replay built a narrator"))
    assert run.main(["--replay", "--db", str(folder / "m.db"), "--incidents", str(tmp_path / "inc"), "--fresh",
                     "--quiet", "--from", "2026-11-03T12:00:00Z", "--to", "2026-11-03T13:00:00Z"]) == 0
    assert not (folder / "brain_status.json").exists()
