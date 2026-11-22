"""The twin: Argus's beliefs about the marketplace.

The twin is an executable structural causal model learned from the *observed*
hourly metric stream (plus the public marketing calendar and the actions Argus
itself applied). It only uses what an analyst could see.

Chain per (country, platform), mechanisms in causal order:
  new_accounts -> active_users (= returning + new over the last 3 h)
  -> impressions -> starts -> completions -> revenue / rewards / quality
  -> margin = revenue - rewards
"""
from __future__ import annotations

from dataclasses import dataclass, field
from math import factorial

import numpy as np

from . import config as cfg
from .graph import MECHANISMS

KPIS = ["new_accounts", "completions", "revenue", "rewards", "margin", "quality_per_completion"]
CPK = ["impressions", "starts", "completions", "revenue", "rewards", "quality"]
PARENT = {"impressions": "active_users", "starts": "impressions", "completions": "starts",
          "revenue": "completions", "rewards": "completions", "quality": "completions"}
STAY = cfg.NEW_ACCOUNT_STAY
SIGMA_FLOOR = 0.01
HOUR_GROUPS = 4             # L1 residual sigma per block of 6 local hours
SIGMA_INFLATE = 1.3         # in-sample backtest is optimistic; inflate for out-of-sample use
USER_SIDE = ["new_accounts", "active_users", "impressions", "starts", "completions", "quality"]
FC_CACHE_MAX = 256           # forecast cache entries kept per world (recent window + 7-day fast-forward)
TOP_PAYOUT = [1, 2]         # strategy games + fintech
GAMES = [0, 1]


def bucket(t: int, tz: np.ndarray) -> np.ndarray:
    """Seasonal bucket per country: local hour + 24 * weekend flag."""
    local = (t + tz) % 24
    day = ((t + tz) // 24) % 7
    return local + 24 * (day >= 5)


@dataclass
class Factors:
    new: np.ndarray       # (C,P)
    active: np.ndarray    # (C,P)
    alloc: np.ndarray     # (C,P,K)
    start: np.ndarray     # (C,P,K)
    reward: np.ndarray    # (C,P,K)


@dataclass
class Twin:
    calendar: list = field(default_factory=list)   # public marketing calendar (PlannedEvent objects)
    actions: list[dict] = field(default_factory=list)  # interventions Argus applied

    def __post_init__(self) -> None:
        self.tz = np.array([c.tz for c in cfg.COUNTRIES])
        self.rows: dict[int, dict] = {}
        self._fc_cache: dict[int, dict] = {}

    # ------------------------------------------------------------------ data access
    def observe(self, row: dict) -> None:
        self.rows[row["t"]] = row

    def row(self, t: int) -> dict:
        return self.rows[t]

    # ------------------------------------------------------------------ calendar + known actions
    def factors(self, t: int, calendar: bool = True, actions: bool = True) -> Factors:
        C, P, K = cfg.C, cfg.P, cfg.K
        f = Factors(np.ones((C, P)), np.ones((C, P)), np.ones((C, P, K)), np.ones((C, P, K)), np.ones((C, P, K)))
        if calendar:
            for ev in self.calendar:
                if ev.active(t):
                    c = cfg.ISO_INDEX[ev.country]
                    for k in ev.categories:
                        f.reward[c, :, k] = ev.reward_share / cfg.REWARD_SHARE
                        f.start[c, :, k] *= ev.start_mult
                    f.active[c, :] *= ev.active_mult
        if actions:
            for iv in self.actions:
                if t < iv.get("start", 0):
                    continue
                c, p = iv["country"], iv.get("platform")
                ps = [p] if p is not None else list(range(P))
                for pp in ps:
                    if iv["type"] == "verify_new_accounts":
                        f.new[c, pp] *= 0.88
                    elif iv["type"] == "cap_rewards":
                        for k in iv["categories"]:
                            f.reward[c, pp, k] = 0.35 / cfg.REWARD_SHARE
                            f.start[c, pp, k] *= 0.85
                    elif iv["type"] == "pause_offers" and t < iv.get("until", 10**9):
                        for k in iv["categories"]:
                            f.alloc[c, pp, k] = 0.0
        return f

    def add_action(self, iv: dict) -> None:
        self.actions.append(dict(iv))
        self._fc_cache.clear()

    # ------------------------------------------------------------------ learning
    def learn(self, history: list[dict]) -> None:
        for r in history:
            self.observe(r)
        ts = np.array([r["t"] for r in history])
        T = len(ts)
        new = np.stack([r["new_accounts"] for r in history])          # (T,C,P)
        act = np.stack([r["active_users"] for r in history])
        X = {k: np.stack([r[k] for r in history]) for k in CPK}       # (T,C,P,K)
        fac = [self.factors(int(t)) for t in ts]
        amult = np.stack([f.active for f in fac])
        smult = np.stack([f.start for f in fac])
        rmult = np.stack([f.reward for f in fac])
        amult_k = np.stack([f.alloc for f in fac])

        new3 = np.zeros_like(new)
        for lag in range(STAY):
            new3[lag:] += new[: T - lag]
        ret = (act - new3) / amult
        b = np.stack([bucket(int(t), self.tz) for t in ts])          # (T,C)
        nb = 48
        C, P = cfg.C, cfg.P
        cidx = np.broadcast_to(np.arange(C), (T, C))

        def bucket_mean(x: np.ndarray, skip: int = 0) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
            s = np.zeros((C, nb, P))
            n = np.zeros((C, nb, 1))
            np.add.at(s, (cidx[skip:], b[skip:]), x[skip:])
            np.add.at(n, (cidx[skip:], b[skip:]), 1.0)
            mean = s / np.maximum(n, 1)
            # leave-one-out residuals (honest out-of-sample error)
            nn = n[cidx[skip:], b[skip:]]
            ss = s[cidx[skip:], b[skip:]]
            loo = (ss - x[skip:]) / np.maximum(nn - 1, 1)
            return mean, loo, nn

        self.base_new, loo_new, _ = bucket_mean(new)                 # (C,48,P)
        self.base_ret, loo_ret, _ = bucket_mean(ret, skip=STAY)
        self.sig_new = np.maximum(np.std(np.log(new / loo_new), axis=0), SIGMA_FLOOR)            # (C,P)
        self.sig_ret = np.maximum(np.std(np.log(np.maximum(ret[STAY:], 1) / loo_ret), axis=0), SIGMA_FLOOR)

        # Mechanism ratios per segment.
        self.mu: dict[str, np.ndarray] = {}
        self.sig: dict[str, np.ndarray] = {}
        for k in CPK:
            parent = act[..., None] * amult_k if PARENT[k] == "active_users" else X[PARENT[k]]
            child = X[k].copy()
            if k == "starts":
                child = child / smult
            elif k == "rewards":
                child = child / rmult
            mu = child.sum(0) / np.maximum(parent.sum(0), 1e-9)
            ok = (parent > 0) & (child > 0)
            lr = np.where(ok, np.log(np.maximum(child, 1e-12) / np.maximum(parent * mu, 1e-12)), np.nan)
            self.mu[k] = mu
            self.sig[k] = np.maximum(np.nan_to_num(np.nanstd(lr, axis=0), nan=0.5), SIGMA_FLOOR)

        # L1 residual sigma per (country, KPI, hour group) from an in-sample backtest of 3 h windows.
        self._fc_cache.clear()
        fc = [self.forecast(int(t)) for t in ts]
        obs_l1 = np.stack([self._l1(r) for r in history])            # (T,C,nKPI) raw sums
        fc_l1 = np.stack([self._l1(f) for f in fc])
        w = 3
        o3 = np.stack([obs_l1[i - w + 1: i + 1].sum(0) for i in range(w - 1 + STAY, T)])
        f3 = np.stack([fc_l1[i - w + 1: i + 1].sum(0) for i in range(w - 1 + STAY, T)])
        rel = self._rel_err(o3, f3)                                    # (N,C,nKPI)
        grp = np.stack([((ts[i] + self.tz) % 24) // (24 // HOUR_GROUPS) for i in range(w - 1 + STAY, T)])  # (N,C)
        self.l1_sigma = np.zeros((cfg.C, HOUR_GROUPS, len(KPIS)))
        for g in range(HOUR_GROUPS):
            for c in range(cfg.C):
                sel = grp[:, c] == g
                self.l1_sigma[c, g] = np.sqrt(np.mean(rel[sel, c] ** 2, axis=0))
        self.l1_sigma = np.maximum(self.l1_sigma * SIGMA_INFLATE, SIGMA_FLOOR)
        self.t_learned = int(ts[-1])

    # ------------------------------------------------------------------ forecasting
    def forecast(self, t: int, calendar: bool = True) -> dict:
        """Marginal expectation of every node at hour t."""
        key = t if calendar else -t - 1
        if key in self._fc_cache:
            return self._fc_cache[key]
        f = self.factors(t, calendar=calendar)
        ci = np.arange(cfg.C)
        new_t = {}
        for lag in range(STAY):
            tt = t - lag
            new_t[lag] = self.base_new[ci, bucket(tt, self.tz)] * self.factors(tt, calendar=calendar).new
        new3 = sum(new_t.values())
        ret = self.base_ret[ci, bucket(t, self.tz)] * f.active
        act = ret + new3
        imp = act[..., None] * self.mu["impressions"] * f.alloc
        st = imp * self.mu["starts"] * f.start
        co = st * self.mu["completions"]
        rev = co * self.mu["revenue"]
        rew = co * self.mu["rewards"] * f.reward
        q = co * self.mu["quality"]
        out = {"t": t, "new_accounts": new_t[0], "active_users": act, "returning": ret, "impressions": imp,
               "starts": st, "completions": co, "revenue": rev, "rewards": rew, "quality": q,
               "completeness": np.ones(cfg.C)}
        self._fc_cache[key] = out
        if len(self._fc_cache) > FC_CACHE_MAX:              # bound memory: drop the oldest-inserted forecasts
            for k in list(self._fc_cache)[: len(self._fc_cache) - FC_CACHE_MAX // 2]:
                del self._fc_cache[k]
        return out

    @staticmethod
    def _l1(r: dict) -> np.ndarray:
        """Country sums needed for the L1 KPIs: new, completions, revenue, rewards, margin, quality."""
        return np.stack([r["new_accounts"].sum(1), r["completions"].sum((1, 2)), r["revenue"].sum((1, 2)),
                         r["rewards"].sum((1, 2)), (r["revenue"] - r["rewards"]).sum((1, 2)),
                         r["quality"].sum((1, 2))], axis=-1)                     # (C,6)

    @staticmethod
    def _kpi_values(s: np.ndarray) -> np.ndarray:
        """Turn L1 sums into KPI values (last column: quality per completion)."""
        out = s.copy()
        out[..., 5] = s[..., 5] / np.maximum(s[..., 1], 1e-9)
        return out

    def _rel_err(self, o: np.ndarray, f: np.ndarray) -> np.ndarray:
        ko, kf = self._kpi_values(o), self._kpi_values(f)
        return (ko - kf) / np.maximum(np.abs(kf), 1e-9)

    def l1_z(self, t: int, window: int = 3, calendar: bool = True) -> dict:
        """L1 z per (country, KPI) over the window ending at hour t."""
        ts = range(t - window + 1, t + 1)
        o = sum(self._l1(self.rows[tt]) for tt in ts)
        f = sum(self._l1(self.forecast(tt, calendar=calendar)) for tt in ts)
        rel = self._rel_err(o, f)
        g = ((t + self.tz) % 24) // (24 // HOUR_GROUPS)
        sig = self.l1_sigma[np.arange(cfg.C), g]                    # (C,6)
        ko, kf = self._kpi_values(o), self._kpi_values(f)
        return {"z": rel / sig, "obs": ko, "fc": kf, "rel": rel}

    # ------------------------------------------------------------------ z given parents
    def given_parents(self, ts: list[int]) -> dict:
        """z per mechanism given the observed parents, summed over the window.

        Returns dict mechanism -> {"obs","exp","var"} arrays: (C,P) for new/active, (C,P,K) for the rest.
        """
        acc: dict[str, dict] = {}

        def add(name: str, obs: np.ndarray, exp: np.ndarray, sig: np.ndarray) -> None:
            a = acc.setdefault(name, {"obs": 0.0, "exp": 0.0, "var": 0.0})
            a["obs"] = a["obs"] + obs
            a["exp"] = a["exp"] + exp
            a["var"] = a["var"] + (exp * sig) ** 2

        ci = np.arange(cfg.C)
        for t in ts:
            r = self.rows[t]
            f = self.factors(t)
            b = bucket(t, self.tz)
            add("new_accounts", r["new_accounts"], self.base_new[ci, b] * f.new, self.sig_new)
            new3 = sum(self.rows[t - lag]["new_accounts"] for lag in range(STAY) if t - lag in self.rows)
            ret_exp = self.base_ret[ci, b] * f.active
            a = acc.setdefault("active_users", {"obs": 0.0, "exp": 0.0, "var": 0.0})
            a["obs"] = a["obs"] + r["active_users"]
            a["exp"] = a["exp"] + ret_exp + new3
            a["var"] = a["var"] + (ret_exp * self.sig_ret) ** 2
            add("impressions", r["impressions"], r["active_users"][..., None] * self.mu["impressions"] * f.alloc,
                self.sig["impressions"])
            add("starts", r["starts"], r["impressions"] * self.mu["starts"] * f.start, self.sig["starts"])
            add("completions", r["completions"], r["starts"] * self.mu["completions"], self.sig["completions"])
            add("revenue", r["revenue"], r["completions"] * self.mu["revenue"], self.sig["revenue"])
            add("rewards", r["rewards"], r["completions"] * self.mu["rewards"] * f.reward, self.sig["rewards"])
            add("quality", r["quality"], r["completions"] * self.mu["quality"], self.sig["quality"])
        for a in acc.values():
            a["z"] = (a["obs"] - a["exp"]) / np.sqrt(a["var"] + 1e-9)
        return acc

    @staticmethod
    def z_of(a: dict, c: int, p: int, k: int | None = None) -> float:
        """z of a given_parents entry at (c,p) [summing categories] or (c,p,k)."""
        o, e, v = a["obs"], a["exp"], a["var"]
        if np.ndim(o) == 3:
            if k is None:
                o, e, v = o[c, p].sum(), e[c, p].sum(), v[c, p].sum()
            else:
                o, e, v = o[c, p, k], e[c, p, k], v[c, p, k]
        else:
            o, e, v = o[c, p], e[c, p], v[c, p]
        return float((o - e) / np.sqrt(v + 1e-9))

    # ------------------------------------------------------------------ chain + Shapley
    def chain(self, c: int, ts: list[int], masks: np.ndarray, platform: int | None = None) -> dict:
        """Run the chain for country c over the window with mechanisms observed per coalition mask.

        masks: (M, 8) bool for the given platform (other platforms fully observed), or (M, P, 8).
        Returns KPI sums of shape (M,) for new_accounts, completions, revenue, rewards, margin, quality,
        quality_per_completion.
        """
        masks = np.asarray(masks, dtype=bool)
        if masks.ndim == 2:
            full = np.ones((masks.shape[0], cfg.P, len(MECHANISMS)), dtype=bool)
            full[:, platform] = masks
            masks = full
        M = masks.shape[0]
        out = {k: np.zeros(M) for k in ("new_accounts", "completions", "revenue", "rewards", "quality")}
        # Per-segment detail: (M,P) for new/active users, (M,P,K) for the offer funnel.
        seg = {"new_accounts": np.zeros((M, cfg.P)), "active_users": np.zeros((M, cfg.P)),
               **{k: np.zeros((M, cfg.P, cfg.K)) for k in ("completions", "revenue", "rewards", "quality")}}
        mi = {m: i for i, m in enumerate(MECHANISMS)}
        for t in ts:
            r = self.rows[t]
            f = self.factors(t)
            for p in range(cfg.P):
                m = masks[:, p]                                                    # (M,8)

                def pick(name: str, obs, exp):
                    sel = m[:, mi[name]]
                    if np.ndim(obs) == 0:
                        return np.where(sel, obs, exp)
                    return np.where(sel[:, None], obs[None, :], exp[None, :])

                news = []
                for lag in range(STAY):
                    tt = t - lag
                    if tt not in self.rows:
                        continue
                    exp_n = self.base_new[c, bucket(tt, self.tz)[c], p] * self.factors(tt).new[c, p]
                    news.append(pick("new_accounts", self.rows[tt]["new_accounts"][c, p], exp_n))
                new_now = news[0]
                new3 = sum(news)
                obs_new3 = sum(self.rows[t - lag]["new_accounts"][c, p] for lag in range(STAY) if t - lag in self.rows)
                ret_obs = r["active_users"][c, p] - obs_new3
                ret_exp = self.base_ret[c, bucket(t, self.tz)[c], p] * f.active[c, p]
                act = pick("active_users", ret_obs, ret_exp) + new3                    # (M,)
                a_obs = max(r["active_users"][c, p], 1e-9)
                r_imp = pick("impressions", r["impressions"][c, p] / a_obs, self.mu["impressions"][c, p] * f.alloc[c, p])
                imp = act[:, None] * r_imp
                safe = lambda x: np.maximum(x, 1e-9)  # noqa: E731
                r_st = pick("starts", r["starts"][c, p] / safe(r["impressions"][c, p]), self.mu["starts"][c, p] * f.start[c, p])
                st = imp * r_st
                r_co = pick("completions", r["completions"][c, p] / safe(r["starts"][c, p]), self.mu["completions"][c, p])
                co = st * r_co
                cobs = safe(r["completions"][c, p])
                rev = co * pick("revenue", r["revenue"][c, p] / cobs, self.mu["revenue"][c, p])
                rew = co * pick("rewards", r["rewards"][c, p] / cobs, self.mu["rewards"][c, p] * f.reward[c, p])
                q = co * pick("quality", r["quality"][c, p] / cobs, self.mu["quality"][c, p])
                out["new_accounts"] += new_now
                out["completions"] += co.sum(1)
                out["revenue"] += rev.sum(1)
                out["rewards"] += rew.sum(1)
                out["quality"] += q.sum(1)
                seg["new_accounts"][:, p] += new_now
                seg["active_users"][:, p] += act
                seg["completions"][:, p] += co
                seg["revenue"][:, p] += rev
                seg["rewards"][:, p] += rew
                seg["quality"][:, p] += q
        out["margin"] = out["revenue"] - out["rewards"]
        out["quality_per_completion"] = out["quality"] / np.maximum(out["completions"], 1e-9)
        out["segments"] = seg
        return out

    @staticmethod
    def all_masks(n: int = len(MECHANISMS)) -> np.ndarray:
        idx = np.arange(2 ** n)
        return ((idx[:, None] >> np.arange(n)) & 1).astype(bool)

    def shapley(self, c: int, p: int, ts: list[int], kpi: str = "margin") -> dict:
        """Exact Shapley contribution of each mechanism of (c,p) to the KPI change (observed - forecast)."""
        n = len(MECHANISMS)
        masks = self.all_masks(n)
        v = self.chain(c, ts, masks, platform=p)[kpi]
        idx = np.arange(2 ** n)
        phi = {}
        w = [factorial(s) * factorial(n - s - 1) / factorial(n) for s in range(n)]
        sizes = masks.sum(1)
        for i, m in enumerate(MECHANISMS):
            without = idx[~masks[:, i]]
            phi[m] = float(sum(w[sizes[s]] * (v[s | (1 << i)] - v[s]) for s in without))
        return {"phi": phi, "v_all": float(v[-1]), "v_none": float(v[0])}

    def explained_share(self, c: int, ts: list[int], hyp: dict[int, list[str]], kpi: str,
                        platform: int | None = None) -> float:
        """Counterfactual: share of the KPI change removed when the hypothesis mechanisms are set to expected.

        hyp: platform -> mechanisms the hypothesis explains. Chain over the whole country.
        """
        M = np.ones((3, cfg.P, len(MECHANISMS)), dtype=bool)
        M[1] = False                       # none observed
        for p, mechs in hyp.items():       # all \ H
            for m in mechs:
                M[2, p, MECHANISMS.index(m)] = False
        out = self.chain(c, ts, M)
        if platform is None:
            v = out[kpi]
        else:                               # measured on one platform only (another problem may sit on the other)
            sg = out["segments"]
            if kpi in ("new_accounts", "active_users"):
                v = sg[kpi][:, platform]
            else:
                g = {k: sg[k][:, platform].sum(1) for k in ("completions", "revenue", "rewards", "quality")}
                g["margin"] = g["revenue"] - g["rewards"]
                g["quality_per_completion"] = g["quality"] / np.maximum(g["completions"], 1e-9)
                v = g[kpi]
        total = v[0] - v[1]
        if abs(total) < 1e-9:
            return 0.0
        effect = v[0] - v[2]           # what removing H takes away
        rest = v[2] - v[1]             # movement left after removing H
        if effect * total <= 0:
            return 0.0                 # H moves the KPI the other way: explains none of the change
        # Share of the absolute movement, so it stays in [0, 1] even when the rest moves the opposite way
        # (the signed ratio effect / total exceeds 100% in that case).
        return float(abs(effect) / (abs(effect) + abs(rest)))

    # ------------------------------------------------------------------ fast-forward (Monte Carlo)
    def fast_forward(self, c: int, options: list[str], ctx: dict, now: int, hours: int = 168, n: int = 40,
                     seed: int = 0) -> list[dict]:
        """Simulate each fix option for `hours` ahead; return daily p10/p50/p90 of cumulative country margin.

        ctx: {"kind": "fraud"|"tracking", "platform": p, "categories": [k...], "elapsed": h}
        Uses common random numbers so options are comparable.
        """
        P, K = cfg.P, cfg.K
        p0 = ctx["platform"]
        H = hours
        fc = [self.forecast(now + 1 + h) for h in range(H)]
        hum_rev = np.stack([f["revenue"][c] for f in fc])          # (H,P,K)
        hum_rew = np.stack([f["rewards"][c] for f in fc])
        hum_co = np.stack([f["completions"][c] for f in fc])
        new3 = np.stack([sum(self.forecast(now + 1 + h - lag)["new_accounts"][c] for lag in range(STAY)) for h in range(H)])
        actv = np.stack([f["active_users"][c] for f in fc])
        new_share = new3 / np.maximum(actv, 1e-9)                   # (H,P)
        # Excess activity measured only over the hours since onset (up to 6), so pre-attack hours don't dilute it.
        span = int(min(6, max(1, ctx.get("elapsed", 3))))
        recent = [now - i for i in range(span) if now - i in self.rows]
        o = lambda key: np.mean([self.rows[t][key][c] for t in recent], axis=0)  # noqa: E731
        e = lambda key: np.mean([self.forecast(t)[key][c] for t in recent], axis=0)  # noqa: E731
        ex_rev = np.zeros((P, K))
        ex_rew = np.zeros((P, K))
        ex_co = np.zeros((P, K))
        ex_rev[p0] = (o("revenue") - e("revenue"))[p0]
        ex_rew[p0] = (o("rewards") - e("rewards"))[p0]
        ex_co[p0] = np.maximum((o("completions") - e("completions"))[p0], 0)
        acc_ratio = np.clip(o("revenue") / np.maximum(e("revenue"), 1e-9), 0, 1.5)      # (P,K)
        # Attack trajectory: fake activity scales with the farm's accounts, i.e. the cumulative excess sign-ups.
        ex_new = [float(self.rows[t]["new_accounts"][c, p0] - self.forecast(t)["new_accounts"][c, p0])
                  for t in range(now - 23, now + 1) if t in self.rows]
        onset = ctx.get("elapsed", 3) + 2
        acc_so_far = max(sum(max(x, 0.0) for x in ex_new[-onset:]), 1.0)
        rate = max(float(np.mean(ex_new[-3:])) if ex_new else 0.0, 0.0)
        sig = float(self.l1_sigma[c, :, 4].mean())
        hrs = np.arange(H)
        results = []
        for opt in options:
            rng = np.random.default_rng([seed, c, 7])
            cum = np.zeros((n, H))
            ev = {"chargeback": [], "budget_cut": [], "tracking_fixed": []}   # per path: hour of the event or None
            for i in range(n):
                bias = np.exp(rng.normal(0, sig))
                hn = np.exp(rng.normal(0, sig, H))
                growth = rng.uniform(1.2, 2.5)
                t_cb = rng.normal(24, 6) - ctx.get("elapsed", 3)
                t_bc = rng.normal(48, 12) - ctx.get("elapsed", 3)
                returns = rng.random() < 0.3
                t_ret = rng.uniform(24, 48)
                t_fix = rng.uniform(12, 30)
                rev = hum_rev * bias
                rew = hum_rew * bias
                if ctx["kind"] == "fraud":
                    more_h = rng.uniform(3, 12)                                       # remaining creation time
                    trend = (acc_so_far + rate * more_h) / acc_so_far
                    if trend > growth:
                        ramp = 1 + (trend - 1) * np.minimum(hrs / more_h, 1)
                    else:
                        ramp = 1 + (growth - 1) * np.minimum(hrs / 12, 1)             # (H,)
                    fm = np.ones(H)
                    human = np.ones((H, P, K))
                    rew_scale = np.ones((H, P, K))
                    if opt == "block_cluster":
                        fm = np.where(returns & (hrs >= t_ret), 0.5, 0.0)
                    elif opt == "verify_new_accounts":
                        fm = np.full(H, 0.05)
                        human[:, p0, :] *= (1 - 0.12 * new_share[:, p0])[:, None]
                    elif opt == "cap_rewards":
                        fm = np.full(H, 0.4)
                        human[:, p0, TOP_PAYOUT] *= 0.85
                        rew_scale[:, p0, TOP_PAYOUT] = 0.35 / cfg.REWARD_SHARE
                    xm = (ramp * fm)[:, None, None]
                    b_rev = ex_rev[None] * xm
                    b_rew = ex_rew[None] * xm * rew_scale
                    b_co = ex_co[None] * xm
                    rev = rev * human
                    rew = rew * human * rew_scale
                    share = b_co.sum(1) / np.maximum((hum_co * human).sum(1) + b_co.sum(1), 1e-9)   # (H,K)
                    fraud_on = share > 0.2
                    # Exposure clock: starts when fraud is on (immediately for an ongoing attack, at return otherwise).
                    first_on = np.where(fraud_on.any(0), np.argmax(fraud_on, axis=0), H)             # (K,)
                    started_now = first_on == 0
                    cb_at = np.where(started_now, t_cb, first_on + t_cb + ctx.get("elapsed", 3))
                    bc_at = np.where(started_now, t_bc, first_on + t_bc + ctx.get("elapsed", 3))
                    on_any = fraud_on.any(0)
                    for name, at in (("chargeback", cb_at), ("budget_cut", bc_at)):
                        hit = on_any & (at < H)
                        ev[name].append(float(max(at[hit].min(), 0.0)) if hit.any() else None)
                    acc = np.where(on_any[None] & (hrs[:, None] >= cb_at[None]), 0.5, 1.0)             # (H,K)
                    cut = np.where(on_any[None] & (hrs[:, None] >= bc_at[None]), 0.4, 1.0)
                    rev = (rev * cut[:, None] + b_rev) * acc[:, None]
                    rew = rew * cut[:, None] + b_rew
                else:  # tracking break: acceptance on affected segments drops to acc_ratio until fixed
                    broken = np.zeros((H, P, K), dtype=bool)
                    aff = np.zeros((P, K), dtype=bool)
                    for k in ctx["categories"]:
                        aff[p0, k] = True
                    fixed_at = t_fix if opt in ("notify_advertiser", "pause_notify") else H + 1
                    broken[:] = aff[None] & (hrs[:, None, None] < fixed_at)
                    ev["tracking_fixed"].append(float(fixed_at) if fixed_at < H else None)
                    rev = np.where(broken, rev * acc_ratio[None], rev)
                    if opt in ("pause_offers", "pause_notify"):
                        paused = aff[None] & (hrs[:, None, None] < 18)
                        rev = np.where(paused, 0.0, rev)
                        rew = np.where(paused, 0.0, rew)
                margin = (rev - rew).sum((1, 2)) * hn
                cum[i] = np.cumsum(margin)
            daily = cum[:, 23::24]
            events = []
            for name, times in ev.items():
                hits = [x for x in times if x is not None]
                if times and len(hits) >= 0.5 * len(times):          # happens in most simulated futures
                    events.append({"event": name, "after_h": int(round(float(np.median(hits)))),
                                   "probability": round(len(hits) / len(times), 2)})
            results.append({"id": opt, "p10": np.percentile(daily, 10, axis=0).tolist(),
                            "p50": np.percentile(daily, 50, axis=0).tolist(),
                            "p90": np.percentile(daily, 90, axis=0).tolist(),
                            "days": list(range(1, daily.shape[1] + 1)), "events": events, "_daily": daily})
        base = results[0]["p50"][-1]
        for r in results:
            # Paired difference vs do-nothing per simulated future (common random numbers), per day.
            d = r["_daily"] - results[0]["_daily"]
            r["delta_daily"] = {q: np.percentile(d, v, axis=0).tolist() for q, v in (("p10", 10), ("p50", 50), ("p90", 90))}
        for r in results:
            r.pop("_daily")
        for r in results:
            r["delta_p50_usd"] = r["p50"][-1] - base
        return results
