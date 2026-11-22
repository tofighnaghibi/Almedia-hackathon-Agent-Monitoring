"""A world = simulated reality + Argus (twin, agent). Scenario injection and deterministic demo scenes."""
from __future__ import annotations

import copy
import threading

from . import config as cfg
from .agent import Agent, RealityView
from .graph import MetricGraph
from .reality import PlannedEvent, Reality
from .taste import TasteModel
from .twin import Twin

_TASTE: TasteModel | None = None
_TEMPLATES: dict = {}    # seed -> fitted pristine world; (seed, "start") -> world after the start scene. Copied per session
_LOCK = threading.RLock()
SCENES = ["start", "promo", "bot_farm", "investigate", "approve", "verify", "new_metric", "tracking_break"]
INJECT_TYPES = ("promo", "bot_farm", "tracking_break", "data_delay")
DEMO_FARM_RAMP_H = 8


def taste_model() -> TasteModel:
    global _TASTE
    with _LOCK:
        if _TASTE is None:
            _TASTE = TasteModel()
        return _TASTE


def template_world(seed: int, scene: str | None = None) -> "World":
    """The fitted pristine world for a seed (14-day history, twin learned, agent ready), built once.
    With scene="start": that world one hour later, as the start scene leaves it (identical for every session).
    Only "start" is supported: later scenes depend on what the session did before."""
    key = seed if scene is None else (seed, scene)
    with _LOCK:
        if key not in _TEMPLATES:
            w = World.__new__(World)
            if scene is None:
                w._fresh(seed)
            else:
                w._copy_from(template_world(seed))
                w.step(1)                                   # exactly what demo("start") does after the reset
                w.scenes_done = ["start"]
            _TEMPLATES[key] = w
        return _TEMPLATES[key]


def _platform(name: str | None, default: str) -> str:
    if not name:
        return default
    for p in cfg.PLATFORMS:
        if p.lower() == name.lower():
            return p
    raise ValueError(f"unknown platform '{name}'")


def _country(iso: str) -> str:
    iso = (iso or "").upper()
    if iso == "UK":
        iso = "GB"
    if iso not in cfg.ISO_INDEX:
        raise ValueError(f"unknown country '{iso}'")
    return iso


class World:
    def __init__(self, seed: int = 42) -> None:
        self.reset(seed)

    def reset(self, seed: int | None = None) -> None:
        """New world. The default seed is a cheap copy of a precomputed template (identical across sessions)."""
        seed = getattr(self, "seed", 42) if seed is None else seed
        if seed == 42 and seed not in _TEMPLATES:
            from .precompute import wait_ready
            wait_ready()
        tpl = _TEMPLATES.get(seed)
        if tpl is None and seed == 42:
            tpl = template_world(seed)
        if tpl is not None:
            self._copy_from(tpl)
            return
        self._fresh(seed)

    def _copy_from(self, tpl: "World") -> None:
        taste = taste_model() if _TASTE is None else _TASTE
        frames = getattr(self, "_frames", None)
        self.__dict__.update(copy.deepcopy(tpl, {id(taste): taste}).__dict__)
        self._frames = frames

    def _fresh(self, seed: int) -> None:
        self.seed = seed
        self.reality = Reality(seed=self.seed)
        self.reality.run(cfg.HISTORY_HOURS)
        self.view = RealityView(self.reality)
        self.twin = Twin(calendar=self.view.calendar)
        self.twin.learn(self.view.observed)
        self.agent = Agent(self.view, self.twin, taste_model(), MetricGraph(), seed=self.seed)
        self.scenes_done: list[str] = []
        self.n_events = 0

    def step(self, hours: int = 1) -> None:
        for _ in range(int(hours)):
            self.reality.step()
            self.agent.on_hour()
            if getattr(self, "_frames", None) is not None:
                self._frames.append(self.agent.frame())

    def demo_frames(self, scene: str) -> dict:
        """demo(scene) plus compact frames: the hour it starts from, then one per simulated hour it advanced
        (including scenes run first to get there), oldest first, ending at the returned state."""
        self._frames = [] if scene == "start" else [self.agent.frame()]   # where playback starts
        try:
            out = self.demo(scene)
            frames = self._frames
        finally:
            self._frames = None
        if not frames or frames[-1]["t"] != self.agent.t:
            frames.append(self.agent.frame())           # e.g. "start" is a copy: one frame of where it lands
        out["frames"] = frames
        return out

    def inject(self, kind: str, country: str, platform: str | None = None, ramp_h: int | None = None) -> dict:
        iso = _country(country)
        if kind == "promo":
            self.n_events += 1
            ev = PlannedEvent(f"promo-{self.n_events}", "Weekend Boost", iso, [0, 1], self.reality.t,
                              self.reality.t + 48)
            self.reality.add_event(ev)
            return {"type": kind, "country": iso, "calendar": ev.to_dict()}
        if kind == "bot_farm":
            plat = _platform(platform, "Android")
            farm = self.reality.add_bot_farm(iso, plat)
            if ramp_h:
                farm.ramp = int(ramp_h)
            return {"type": kind, "country": iso, "platform": plat, "start": self.reality.t}
        if kind == "tracking_break":
            plat = _platform(platform, "iOS")
            self.reality.add_tracking_break(iso, plat, [0, 1])
            return {"type": kind, "country": iso, "platform": plat, "categories": cfg.CATEGORIES[:2],
                    "start": self.reality.t}
        if kind == "data_delay":
            self.reality.add_data_delay(iso)
            return {"type": kind, "country": iso, "start": self.reality.t}
        raise ValueError(f"unknown inject type '{kind}'")

    # ------------------------------------------------------------------ demo scenes
    def _latest(self, iso: str) -> dict | None:
        for inc in reversed(self.agent.incidents):
            if inc["country"] == iso:
                return inc
        return None

    def _step_until_incident(self, iso: str, max_hours: int) -> dict | None:
        before = self._latest(iso)
        for _ in range(max_hours):
            self.step(1)
            inc = self._latest(iso)
            if inc is not None and inc is not before:
                return inc
        return None

    def demo(self, scene: str) -> dict:
        if scene not in SCENES:
            raise KeyError(scene)
        if scene == "start":
            if (42, "start") not in _TEMPLATES:
                from .precompute import wait_ready
                wait_ready()
            if (42, "start") in _TEMPLATES or 42 in _TEMPLATES:
                self._copy_from(template_world(42, "start"))      # identical for every session: copy, don't recompute
                return {"scene": scene, "state": self.agent.state()}
            self.reset(42)
        else:
            for prev in SCENES[: SCENES.index(scene)]:
                if prev not in self.scenes_done:
                    self.demo(prev)
        out: dict = {"scene": scene}
        # Story clock (labels = end of hour): start shows Mon 01:00; the promo runs from 01:00 and is explained by
        # 03:00; the bot farm starts at 03:00, leaves the normal zone with the 04:00 data and is detected at 06:00.
        if scene == "start":
            self.step(1)
        elif scene == "promo":
            self.inject("promo", "DE")
            self.step(2)
        elif scene == "bot_farm":
            # Demo farm builds its 320 accounts over 8 h (default 10 h): at 03:00 UTC Canada is near its evening
            # sign-up peak, and this keeps detection at 06:00, three hours in (see docs/ASSUMPTIONS.md).
            self.inject("bot_farm", "CA", "Android", ramp_h=DEMO_FARM_RAMP_H)
            inc = self._step_until_incident("CA", 12)
            out["incident_id"] = inc["id"] if inc else None
        elif scene == "investigate":
            self.step(2)
            inc = self._latest("CA")
            out["incident_id"] = inc["id"] if inc else None
        elif scene == "approve":
            inc = self._latest("CA")
            if inc is not None and inc["applied"] is None:
                self.agent.approve(inc["id"], inc["recommended"])
            self.step(1)
            out["incident_id"] = inc["id"] if inc else None
        elif scene == "verify":
            inc = self._latest("CA")
            for _ in range(30):
                if inc is None or inc["verification"]["status"] != "pending":
                    break
                self.step(1)
            out["incident_id"] = inc["id"] if inc else None
        elif scene == "new_metric":
            out["metric"] = self.agent.add_metric("margin_per_signup = margin / signups")
            self.step(1)
        elif scene == "tracking_break":
            self.inject("tracking_break", "GB", "iOS")
            inc = self._step_until_incident("GB", 12)
            out["incident_id"] = inc["id"] if inc else None
        if scene not in self.scenes_done:
            self.scenes_done.append(scene)
        out["state"] = self.agent.state()
        return out
