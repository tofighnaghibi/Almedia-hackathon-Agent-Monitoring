"""M1 + M2: the emulator API over the gated chain (+ optional session layer).

    initialize(config, seed) -> RunInfo
    step(until, storefront=None) -> StepReport        # idempotent for until <= clock
    observe() -> core_port.ContractPort               # public plane only, data cut = clock
    intervene(spec) -> InterventionEvent              # scenario/evaluator side only; never reachable from the core
    snapshot() -> dict / restore(ref)
    capabilities() -> ProviderManifest

Planes: everything under run_dir/privileged (world frames with latent fields, interventions with causes,
storefront correlation table, scenario answers) is never read by the public side. The public side is
the ContractPort built from observed events with available_at <= clock. Every gateway decision is in
run_dir/audit.jsonl.
"""
from __future__ import annotations

import hashlib
import json
import pathlib
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, time, timedelta

import numpy as np
import polars as pl

from .. import core_port
from ..sim import chain as chain_mod
from ..sim.chain import ChainParams, uniform
from ..sim.observe_chain import EmulatorPort, ObservationModel, SessionEmulatorPort, observe
from ..sim.twin import gateway
from ..engine_types import InterventionEvent, InterventionSpec

OUTCOME_OK = "order"


@dataclass(frozen=True)
class EmulatorConfig:
    start: date
    horizon_end: date                                   # latest date step() may reach (gateway horizon)
    sessions: bool = False
    hidden_variables: tuple[str, ...] = ()              # not served until a measurement is granted
    observation: ObservationModel = field(default_factory=ObservationModel)


def _h(obj) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


class Emulator:
    def __init__(self, params: ChainParams, run_dir: pathlib.Path, session_params=None):
        self.params, self.sp = params, session_params
        self.run_dir = pathlib.Path(run_dir)
        self.priv = self.run_dir / "privileged"

    # ------------------------------------------------------------------ lifecycle
    def initialize(self, config: EmulatorConfig, seed: int) -> dict:
        if config.sessions and self.sp is None:
            raise ValueError("sessions=True needs session_params")
        self.cfg, self.seed = config, seed
        self.clock = config.start
        self.specs: list[InterventionSpec] = []
        self.segments: list[dict] = []                   # {"start","end","orders","sessions","views"} (privileged frames)
        self.commands: list[dict] = []                   # storefront command results (correlation, privileged)
        self.exposed: set[str] = set()
        (self.priv / "ledger").mkdir(parents=True, exist_ok=True)
        self._audit("initialize", {"config": asdict(config), "seed": seed})
        return {"run_id": self.run_id, "clock": self.clock.isoformat(), "seed": seed}

    @property
    def run_id(self) -> str:
        return "run-" + _h([asdict(self.cfg), self.seed])[:12]

    def _audit(self, kind: str, body: dict) -> None:
        with open(self.run_dir / "audit.jsonl", "a") as f:
            f.write(json.dumps({"kind": kind, **body}, default=str) + "\n")

    # ------------------------------------------------------------------ interventions (scenario side)
    def intervene(self, spec: InterventionSpec) -> InterventionEvent:
        horizon = (datetime.combine(self.clock, time()), datetime.combine(self.cfg.horizon_end, time()))
        ev, eff = gateway(spec, set(self.params.buyer_regions), horizon)     # rejects windows starting before the clock
        if ev.status == "executed":
            self.specs.append(spec)
            ev = ev.model_copy(update={"status": "accepted"})
            with open(self.priv / "interventions.jsonl", "a") as f:
                f.write(spec.model_dump_json() + "\n")
        self._audit("intervene", {"spec_id": spec.id, "target": spec.target, "status": ev.status, "reason": ev.reason})
        return ev

    def _effects(self):
        transit = demand = cfail = None
        drops = []
        horizon = (datetime.combine(self.cfg.start, time()), datetime.combine(self.cfg.horizon_end, time()))
        for sp in self.specs:
            _, eff = gateway(sp, set(self.params.buyer_regions), horizon)
            if eff["kind"] == "transit":
                transit = eff["fn"]
            elif eff["kind"] == "demand":
                demand = eff["fn"]
            elif eff["kind"] == "checkout_fail":
                cfail = eff
            elif eff["kind"] == "flag":
                continue                                    # operational: applied to the storefront by the flag adapter
            else:
                drops += eff["drops"]
        return transit, demand, cfail, drops

    # ------------------------------------------------------------------ time
    def step(self, until: date, storefront=None) -> dict:
        """Advance the world to `until` (exclusive). storefront: optional callable(plans) -> {command_id: outcome}
        that executes buying sessions in a storefront window (M4). Nothing is committed without a result."""
        if until <= self.clock:
            return {"clock": self.clock.isoformat(), "new_orders": 0, "noop": True}
        if until > self.cfg.horizon_end:
            raise ValueError("step beyond the configured horizon")
        transit, demand, cfail, _ = self._effects()
        a, b = self.clock, until
        reached = chain_mod.simulate(self.params, a, b, seed=self.seed, transit_multiplier=transit, demand_multiplier=demand)
        outcome = np.full(reached.height, OUTCOME_OK, dtype=object)
        if cfail is not None:
            pu = reached["purchased_at"].cast(pl.Int64).to_numpy()
            hit = (pu >= cfail["w0"]) & (pu < cfail["w1"]) & (uniform(self.seed, "checkout:fail", reached["order_idx"].to_numpy()) < cfail["rate"])
            outcome[hit] = "checkout_failed"
        if storefront is not None:
            plans = self.storefront_plans(reached, outcome)
            if plans:
                results = storefront(plans)
                for pl_ in plans:
                    r = results.get(pl_["command_id"], {"outcome": "timeout"})        # no result -> never committed
                    self.commands.append({**pl_, **r})
                    if r["outcome"] != "ok":
                        outcome[pl_["row"]] = f"storefront_{r['outcome']}"
        orders = reached.filter(pl.Series(outcome == OUTCOME_OK))
        seg = {"start": a, "end": b, "orders": orders}
        if self.cfg.sessions:
            s, v = __import__("mkt.sim.sessions", fromlist=["build"]).build(reached, self.sp, self.params, a, b, self.seed,
                                                                             failed=outcome.astype(str), demand_multiplier=demand)
            seg["sessions"], seg["views"] = s, v
        if storefront is not None and self.cfg.sessions and getattr(self, "_sf_browse", 0):
            browse = self.browse_plans(seg["sessions"])               # step 3: browsing-only visits through the shop
            if browse:
                results = storefront(browse)
                self.commands += [{**b, **results.get(b["command_id"], {"outcome": "timeout"})} for b in browse]
        self.segments.append(seg)
        n = len(self.segments) - 1
        orders.write_parquet(self.priv / "ledger" / f"orders-{n:04d}.parquet")
        if self.cfg.sessions:
            seg["sessions"].write_parquet(self.priv / "ledger" / f"sessions-{n:04d}.parquet")
        if self.commands:
            pl.DataFrame(self.commands).drop("latency_s", strict=False).write_parquet(self.priv / "correlation.parquet")
        self.clock = until
        self._audit("step", {"from": a.isoformat(), "to": b.isoformat(), "orders": orders.height,
                             "lost": int((outcome != OUTCOME_OK).sum())})
        return {"clock": self.clock.isoformat(), "new_orders": orders.height, "lost": int((outcome != OUTCOME_OK).sum())}

    def storefront_plans(self, reached: pl.DataFrame, outcome: np.ndarray) -> list[dict]:
        """Buying sessions inside a storefront window (set by start_storefront) -> plans for the session driver."""
        win = getattr(self, "_sf_window", None)
        if win is None:
            return []
        pu = reached["purchased_at"]
        sel = np.flatnonzero(((pu >= win[0]) & (pu < win[1])).to_numpy() & (outcome == OUTCOME_OK))
        if win[2] is not None:
            sel = sel[:win[2]]
        idx = reached["order_idx"].to_numpy()
        return [{"row": int(i), "order_idx": int(idx[i]), "command_id": _h([self.seed, "cmd", int(idx[i])])[:20],
                 "session_alias": _h([self.seed, "alias", int(idx[i])])[:16], "purchased_at": pu[int(i)].isoformat()} for i in sel]

    def browse_plans(self, sessions: pl.DataFrame) -> list[dict]:
        """Non-buying and research visits starting inside the storefront window (sessions=True) -> browse plans.
        Browsing changes no business state: results are recorded for correlation, never committed to the ledger."""
        win = getattr(self, "_sf_window", None)
        if win is None:
            return []
        t = sessions["start_us"].cast(pl.Datetime("us"))
        sel = sessions.filter((sessions["kind"] != "buying") & (t >= win[0]) & (t < win[1])).sort("start_us").head(self._sf_browse)
        return [{"kind": "browse", "order_idx": int(r["key"]) % 2_000_000_000, "command_id": _h([self.seed, "browse", r["session_id"]])[:20],
                 "session_alias": _h([self.seed, "alias", r["session_id"]])[:16], "n_views": int(min(r["n_views"], 5)),
                 "cart": r["cart_us"] is not None} for r in sel.to_dicts()]

    def start_storefront(self, window_start: datetime, window_end: datetime, max_sessions: int | None = None,
                         browse_sessions: int = 0) -> None:
        self._sf_window = (window_start, window_end, max_sessions)
        self._sf_browse = browse_sessions
        self._audit("storefront", {"start": window_start.isoformat(), "end": window_end.isoformat(), "max_sessions": max_sessions})

    # ------------------------------------------------------------------ public plane
    def _world(self) -> pl.DataFrame:
        return pl.concat([s["orders"] for s in self.segments]) if self.segments else None

    def observe(self) -> core_port.ContractPort:
        """The public plane: observed events with available_at <= clock, served through the Contracts port."""
        if not self.segments:
            raise ValueError("step() before observe()")
        _, _, _, drops = self._effects()
        om = self.cfg.observation
        obs = observe(self._world(), om, seed=self.seed, drops=drops)
        if self.cfg.sessions:
            from ..sim.sessions import observe_sessions
            s = pl.concat([x["sessions"] for x in self.segments])
            v = pl.concat([x["views"] for x in self.segments])
            obs = pl.concat([obs, observe_sessions(s, v, seed=self.seed, web_delay_median_s=om.web_delay_median_s)], how="diagonal_relaxed")
        cut = datetime.combine(self.clock, time())
        obs = obs.filter(pl.col("available_at") <= cut).sort("available_at")
        start = datetime.combine(self.cfg.start, time())
        eng = (SessionEmulatorPort(obs, self.params, om, start, self.seed, self.sp) if self.cfg.sessions
               else EmulatorPort(obs, self.params, om, start, self.seed))
        hidden = set(self.cfg.hidden_variables) - self.exposed
        return core_port.emulator_port(eng, self.sp if self.cfg.sessions else None, hidden_variables=frozenset(hidden))

    def grant_measurement(self, variable_id: str) -> bool:
        """Policy-gated: make a hidden measurement stream visible (history included). Called by the harness policy."""
        ok = variable_id in self.cfg.hidden_variables
        if ok:
            self.exposed.add(variable_id)
        self._audit("grant_measurement", {"variable_id": variable_id, "granted": ok})
        return ok

    def capabilities(self):
        return self.observe().manifest()

    # ------------------------------------------------------------------ snapshot / restore
    def ledger_hash(self) -> str:
        w = self._world()
        # content hash (sorted rows as CSV): independent of how frames are chunked in memory
        return hashlib.sha256(w.sort("order_idx").write_csv().encode()).hexdigest() if w is not None else ""

    def snapshot(self) -> dict:
        return {"config": self.cfg, "seed": self.seed, "clock": self.clock, "specs": list(self.specs), "n_segments": len(self.segments),
                "exposed": set(self.exposed), "commands": list(self.commands), "ledger_hash": self.ledger_hash()}

    def restore(self, ref: dict) -> dict:
        self.cfg, self.seed, self.clock = ref["config"], ref["seed"], ref["clock"]
        self.specs, self.exposed, self.commands = list(ref["specs"]), set(ref["exposed"]), list(ref["commands"])
        self.segments = self.segments[:ref["n_segments"]]
        if self.ledger_hash() != ref["ledger_hash"]:
            raise RuntimeError("restore: ledger does not match the snapshot")
        self._audit("restore", {"clock": self.clock.isoformat()})
        return {"run_id": self.run_id, "clock": self.clock.isoformat()}

    def operational_flags(self) -> list[dict]:
        """Accepted operational interventions (for the storefront harness to apply through the flag adapter)."""
        horizon = (datetime.combine(self.cfg.start, time()), datetime.combine(self.cfg.horizon_end, time()))
        return [eff for sp in self.specs for _, eff in [gateway(sp, set(self.params.buyer_regions), horizon)] if eff and eff["kind"] == "flag"]
