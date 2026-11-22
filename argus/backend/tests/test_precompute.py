"""Precomputed, session-independent work: default world copied per session, launch forecast computed once."""
import time

from argus import launch, precompute, world
from argus.world import World


def test_default_world_is_an_independent_copy_of_the_template():
    a, b = World(seed=42), World(seed=42)
    tpl = world.template_world(42)
    assert a.agent is not b.agent and a.reality is not tpl.reality and a.twin is not b.twin
    assert a.agent.taste is b.agent.taste is world.taste_model()          # the read-only taste model is shared
    a.inject("bot_farm", "CA", "Android")
    a.step(6)
    assert b.reality.t == tpl.reality.t == a.reality.t - 6 and not b.reality.farms and not tpl.reality.farms
    t = time.time()
    b.reset(42)
    assert time.time() - t < 0.5                                         # a copy, not a refit
    assert b.reality.t == tpl.reality.t and b.agent.incidents == []
    fresh = World.__new__(World)
    fresh._fresh(42)                                                      # same state as building from scratch
    assert fresh.reality.t == b.reality.t
    assert (fresh.view.observed[-1]["revenue"] == b.view.observed[-1]["revenue"]).all()


def test_precompute_roundtrip(tmp_path, monkeypatch):
    monkeypatch.setenv("ARGUS_CACHE", str(tmp_path / "p.pkl"))
    blob = precompute.build()
    precompute.save(blob)
    launch._LAUNCH.clear()
    assert precompute.load() is True and ("AR", 42) in launch._LAUNCH
    t = time.time()
    r = launch.launch("AR", 42, users=123_000)
    assert time.time() - t < 0.2 and r["users"]["daily_users"] == 123_000
    blob["key"] = "stale"
    precompute.save(blob)
    assert precompute.load() is False                                    # code changed: never use a stale cache


def test_start_scene_copy_equals_running_it():
    a = World(seed=42)
    a.demo("start")
    b = World.__new__(World)
    b._fresh(42)
    b.step(1)                                                            # the start scene, computed the slow way
    assert a.reality.t == b.reality.t and a.scenes_done == ["start"]
    assert a.agent.state() == b.agent.state()
    a.demo("bot_farm")                                                   # later scenes still run on the copy
    assert any(i["country"] == "CA" for i in a.agent.incidents)
