"""The simulated "reality": a Freecash-like rewarded-ad marketplace.

This module plays the real business. It holds hidden ground truth (bot farms,
broken tracking, delayed data) that Argus never reads directly. Argus only
sees the hourly metric stream, user-level samples without labels, and the
public marketing calendar.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from . import config as cfg

NODES_CP = ["new_accounts", "active_users"]

# Scripted onboarding answers used by the bot farm (student, commuter, podcasts, coupons / night owl, series).
BOT_TEMPLATES = [np.array([1, 0, 0, 0, 1, 1, 0, 0, 0, 0, 0, 1]), np.array([0, 0, 0, 1, 0, 1, 0, 1, 0, 0, 0, 1])]
NODES_CPK = ["impressions", "starts", "completions", "revenue", "rewards", "quality"]


def daily_profile(local_hour: np.ndarray | int) -> np.ndarray:
    """Activity by local hour: low in the morning, peak around 20:00."""
    return 0.6 + 0.4 * np.cos(2 * np.pi * (np.asarray(local_hour) - 20) / 24)


def weekend_factor(t: int, tz: np.ndarray) -> np.ndarray:
    day = ((t + tz) // 24) % 7  # sim day 0 = Monday
    return np.where(day >= 5, 1.12, 1.0)


@dataclass
class PlannedEvent:
    """An entry in the public marketing calendar (known to Argus)."""
    id: str
    name: str
    country: str
    categories: list[int]
    start: int
    end: int
    reward_share: float = 0.70
    start_mult: float = 1.30
    active_mult: float = 1.10

    def active(self, t: int) -> bool:
        return self.start <= t < self.end

    def to_dict(self) -> dict:
        return {"id": self.id, "name": self.name, "country": self.country,
                "categories": [cfg.CATEGORIES[k] for k in self.categories],
                "start": self.start, "end": self.end, "reward_share": self.reward_share,
                "start_mult": self.start_mult, "active_mult": self.active_mult}


@dataclass
class BotFarm:
    country: int
    platform: int
    start: int
    target: int = 320
    ramp: int = 10
    accounts: float = 0.0
    strength: float = 1.0   # reduced by interventions
    stopped: bool = False


@dataclass
class TrackingBreak:
    country: int
    platform: int
    categories: list[int]
    start: int
    fixed_at: int | None = None


@dataclass
class Reality:
    seed: int = 42
    t: int = 0
    events: list[PlannedEvent] = field(default_factory=list)
    farms: list[BotFarm] = field(default_factory=list)
    breaks: list[TrackingBreak] = field(default_factory=list)
    delays: dict[int, int] = field(default_factory=dict)   # country -> hour of delayed delivery
    interventions: list[dict] = field(default_factory=list)

    def __post_init__(self) -> None:
        self.tz = np.array([c.tz for c in cfg.COUNTRIES])
        self.dau = np.array([c.dau for c in cfg.COUNTRIES], dtype=float)
        self.ios = np.array([c.ios_share for c in cfg.COUNTRIES])
        self.share = np.stack([self.ios, 1 - self.ios], axis=1)              # (C,P)
        self.value = np.array([c.value for c in cfg.COUNTRIES])
        self.mix = cfg.country_archetype_mix()                               # (C,A)
        self.pref = self.mix @ cfg.ARCHETYPE_AFFINITY                        # (C,K)
        self.pref /= self.pref.sum(axis=1, keepdims=True)
        # Production feed: taste-aware but clearly payout-heavy (impressions ∝ taste × payout).
        # Simulation assumption, see docs/ASSUMPTIONS.md: this leaves a few % of margin on the table.
        alloc = self.pref * cfg.PAYOUT ** 1.1
        self.alloc = alloc / alloc.sum(axis=1, keepdims=True)                # (C,K)
        self.reward_elasticity = cfg.reward_elasticity_truth()               # hidden (C,)
        self.exposure = np.zeros((cfg.C, cfg.K))                             # advertiser fraud exposure (hours)
        self.true: list[dict] = []
        self.observed: list[dict] = []
        self.hidden: list[dict] = []
        self.new_hist: list[np.ndarray] = []

    # ------------------------------------------------------------------ helpers
    def start_rate(self, alloc: np.ndarray) -> np.ndarray:
        """Starts per impression: higher when the feed matches local taste."""
        fit = np.clip((self.pref / np.maximum(alloc, 1e-6)) ** 0.6, 0.4, 2.0)
        return cfg.START_RATE[None, :] * fit                                 # (C,K)

    def _intervention(self, kind: str, c: int, p: int | None = None) -> dict | None:
        for iv in self.interventions:
            if iv["type"] == kind and iv["country"] == c and (p is None or iv.get("platform") in (None, p)):
                return iv
        return None

    # ------------------------------------------------------------------ one hour
    def step(self) -> dict:
        t = self.t
        rng = np.random.default_rng([self.seed, t])
        Cn, Pn, Kn = cfg.C, cfg.P, cfg.K
        local = (t + self.tz) % 24
        prof = daily_profile(local) * weekend_factor(t, self.tz)            # (C,)

        reward_share = np.full((Cn, Pn, Kn), cfg.REWARD_SHARE)
        start_mult = np.ones((Cn, Pn, Kn))
        active_mult = np.ones((Cn, Pn))
        new_mult = np.ones((Cn, Pn))
        alloc_mult = np.ones((Cn, Pn, Kn))
        accept = np.full((Cn, Pn, Kn), cfg.ACCEPTANCE)

        for ev in self.events:
            if ev.active(t):
                ci = cfg.ISO_INDEX[ev.country]
                for k in ev.categories:
                    reward_share[ci, :, k] = ev.reward_share
                    start_mult[ci, :, k] *= ev.start_mult
                active_mult[ci, :] *= ev.active_mult

        for iv in self.interventions:
            c, p = iv["country"], iv.get("platform")
            ps = [p] if p is not None else list(range(Pn))
            if iv["type"] == "verify_new_accounts":
                for pp in ps:
                    new_mult[c, pp] *= 0.88
            elif iv["type"] == "cap_rewards":
                for pp in ps:
                    for k in iv["categories"]:
                        reward_share[c, pp, k] = 0.35
                        start_mult[c, pp, k] *= 0.85
            elif iv["type"] == "set_reward":
                for pp in ps:
                    reward_share[c, pp, :] = iv["reward_share"]
                    start_mult[c, pp, :] *= (iv["reward_share"] / cfg.REWARD_SHARE) ** self.reward_elasticity[c]
            elif iv["type"] == "pause_offers":
                if t < iv.get("until", 10**9):
                    for pp in ps:
                        for k in iv["categories"]:
                            alloc_mult[c, pp, k] = 0.0

        # Advertisers react to fraud: chargebacks after 24 h, budget cut after 48 h.
        accept *= np.where(self.exposure >= 24, 0.5, 1.0)[:, None, :]
        alloc_mult *= np.where(self.exposure >= 48, 0.4, 1.0)[:, None, :]

        for br in self.breaks:
            if t >= br.start and (br.fixed_at is None or t < br.fixed_at):
                for k in br.categories:
                    accept[br.country, br.platform, k] = 0.15

        # --- humans
        base_new = self.dau[:, None] * self.share * 0.0012 * (0.5 + 0.5 * prof[:, None])
        new_h = base_new * new_mult * np.exp(rng.normal(0, 0.06, (Cn, Pn)))
        self.new_hist.append(new_h)
        recent_new = sum(self.new_hist[-cfg.NEW_ACCOUNT_STAY:])
        returning = self.dau[:, None] * self.share * 0.11 * prof[:, None] * active_mult
        active_h = (returning + recent_new) * np.exp(rng.normal(0, 0.03, (Cn, Pn)))

        alloc = self.alloc[:, None, :] * alloc_mult
        sr = self.start_rate(self.alloc)[:, None, :] * start_mult
        cr = cfg.COMPLETION_RATE[None, None, :] * cfg.PLATFORM_COMPLETION[None, :, None]
        payout = cfg.PAYOUT[None, None, :] * self.value[:, None, None] * cfg.PLATFORM_PAYOUT[None, :, None]

        def noisy(x: np.ndarray, base: float = 0.03) -> np.ndarray:
            sigma = np.sqrt(base ** 2 + 1.0 / np.maximum(x, 1.0))
            return x * np.exp(rng.normal(0, 1, x.shape) * sigma)

        imp_h = noisy(active_h[:, :, None] * cfg.IMPRESSIONS_PER_ACTIVE * alloc)
        st_h = noisy(imp_h * sr)
        co_h = noisy(st_h * cr)
        q_h = noisy(co_h * cfg.QUALITY[None, None, :], 0.05)

        # --- bots (hidden ground truth)
        bot_new = np.zeros((Cn, Pn))
        bot_active = np.zeros((Cn, Pn))
        bot_imp = np.zeros((Cn, Pn, Kn))
        bot_alloc = np.array([0, 0.35, 0.65, 0, 0, 0])
        for farm in self.farms:
            if t < farm.start or farm.stopped:
                continue
            c, p = farm.country, farm.platform
            ver = self._intervention("verify_new_accounts", c, p)
            cap = self._intervention("cap_rewards", c, p)
            strength = farm.strength * (0.4 if cap else 1.0)
            if farm.accounts < farm.target and not ver:
                created = min(farm.target / farm.ramp, farm.target - farm.accounts)
                farm.accounts += created
                bot_new[c, p] += created
            bot_active[c, p] += farm.accounts * 0.6 * strength
            bot_imp[c, p] += bot_active[c, p] * 10 * bot_alloc
        bot_st = bot_imp * 0.15
        bot_co = noisy(bot_st * 0.35)
        bot_q = bot_co * 0.02

        new_obs = new_h + bot_new
        active = active_h + bot_active
        imp = imp_h + bot_imp
        st = st_h + bot_st
        co = co_h + bot_co
        # Offer mix within a category shifts hour to hour (payout per completion varies by offer), and the
        # share advertisers confirm wobbles a little. Drawn last so the other noise streams stay unchanged.
        mix = np.exp(rng.normal(0, 0.06, (Cn, Pn, Kn)))
        accept = np.minimum(accept * np.exp(rng.normal(0, 0.015, (Cn, Pn, Kn))), 1.0)
        rev = co * payout * mix * accept
        rew = co * payout * mix * reward_share
        qual = q_h + bot_q

        # Advertiser fraud exposure (per country x category).
        bot_share_ck = bot_co.sum(axis=1) / np.maximum(co.sum(axis=1), 1e-9)
        self.exposure = np.where(bot_share_ck > 0.2, self.exposure + 1, np.maximum(0, self.exposure - 0.5))

        row = {"t": t, "new_accounts": new_obs, "active_users": active, "impressions": imp, "starts": st,
               "completions": co, "revenue": rev, "rewards": rew, "quality": qual,
               "completeness": np.ones(Cn)}
        self.true.append(row)
        obs = {k: (v.copy() if isinstance(v, np.ndarray) else v) for k, v in row.items()}
        if t in self.delays.values():
            for c, th in self.delays.items():
                if th == t:
                    for k in NODES_CP + NODES_CPK:
                        obs[k][c] *= 0.45
                    obs["completeness"][c] = 0.45
        self.observed.append(obs)
        # Backfill delayed hours two hours later.
        for c, th in list(self.delays.items()):
            if t == th + 2:
                for k in NODES_CP + NODES_CPK:
                    self.observed[th][k][c] = self.true[th][k][c]
                self.observed[th]["completeness"][c] = 1.0
                del self.delays[c]
        self.hidden.append({"bot_active": bot_active, "bot_completions": bot_co, "exposure": self.exposure.copy(),
                            "accept": accept})
        self.t += 1
        return obs

    def run(self, hours: int) -> None:
        for _ in range(hours):
            self.step()

    # ------------------------------------------------------------------ L3 samples
    def sample_users(self, c: int, p: int, n: int = 600) -> list[dict]:
        """User-level records for one segment, as an analyst could pull them from the event log.

        No bot label is included. Bots appear in proportion to their share of active accounts.
        """
        rng = np.random.default_rng([self.seed, 991, c, p, self.t])
        last = self.hidden[-1] if self.hidden else None
        bot_share = 0.0
        if last is not None:
            act = self.true[-1]["active_users"][c, p]
            bot_share = float(last["bot_active"][c, p] / max(act, 1e-9))
        n_bots = int(round(n * bot_share * 1.6))  # bots are always online, so they dominate recent-activity samples
        users = []
        devices_h = [f"Phone {m}" for m in range(40)]
        devices_b = ["Emu-A1", "Emu-A1", "Emu-X7"]
        for i in range(n - n_bots):
            a = rng.choice(cfg.A, p=self.mix[c])
            taste = rng.dirichlet(cfg.ARCHETYPE_AFFINITY[a] * 30)
            signals = (rng.random(cfg.S) < cfg.ARCHETYPE_SIGNALS[a]).astype(int)
            comp = rng.poisson(taste * rng.uniform(2, 9))
            users.append({"id": f"u{c}{p}{i:04d}", "signals": signals.tolist(), "completions": comp.tolist(),
                          "device": str(rng.choice(devices_h)), "account_age_days": int(rng.exponential(90)) + 1,
                          "_bot": False})
        for i in range(n_bots):
            # Farms script the onboarding answers: the same few templates, unrelated to what they do.
            template = BOT_TEMPLATES[i % len(BOT_TEMPLATES)].copy()
            flip = rng.random(cfg.S) < 0.06
            signals = np.where(flip, 1 - template, template)
            comp = rng.poisson(np.array([0.1, 0.35, 0.65, 0.05, 0.05, 0.05]) * rng.uniform(8, 14))
            users.append({"id": f"b{c}{p}{i:04d}", "signals": signals.tolist(), "completions": comp.tolist(),
                          "device": str(rng.choice(devices_b)), "account_age_days": int(rng.integers(0, 3)),
                          "_bot": True})
        order = rng.permutation(len(users))
        return [users[i] for i in order]

    def reward_tests(self) -> np.ndarray:
        """Results of past reward A/B tests per watched country (observable): measured start elasticity with noise."""
        rng = np.random.default_rng([self.seed, 12])
        return self.reward_elasticity * np.exp(rng.normal(0, 0.08, cfg.C))

    # ------------------------------------------------------------------ scenario controls
    def add_event(self, ev: PlannedEvent) -> None:
        self.events.append(ev)

    def add_bot_farm(self, country: str, platform: str, start: int | None = None) -> BotFarm:
        farm = BotFarm(cfg.ISO_INDEX[country], cfg.PLATFORMS.index(platform), self.t if start is None else start)
        self.farms.append(farm)
        return farm

    def add_tracking_break(self, country: str, platform: str, categories: list[int]) -> None:
        self.breaks.append(TrackingBreak(cfg.ISO_INDEX[country], cfg.PLATFORMS.index(platform), categories, self.t))

    def add_data_delay(self, country: str) -> None:
        self.delays[cfg.ISO_INDEX[country]] = self.t

    def apply(self, intervention: dict) -> None:
        iv = dict(intervention)
        iv.setdefault("start", self.t)
        self.interventions.append(iv)
        for farm in self.farms:
            if farm.country != iv["country"] or iv.get("platform") not in (None, farm.platform):
                continue
            if iv["type"] == "block_cluster":
                farm.accounts, farm.stopped = 0.0, True
            elif iv["type"] == "verify_new_accounts":
                farm.accounts *= 0.05
        if iv["type"] == "notify_advertiser":
            for br in self.breaks:
                if br.country == iv["country"] and br.fixed_at is None:
                    br.fixed_at = self.t + 18
        if iv["type"] == "pause_offers":
            iv.setdefault("until", self.t + 18)
