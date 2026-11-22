"""Forward-looking scenario: launch the marketplace in a country Argus does not watch yet.

What Argus predicts is per-user economics; the user count is an input (default: assumed from similar countries).
Transfer learning from the most similar watched countries, then a 4-week Monte Carlo in the twin's terms:

  similarity   public features only: GDP per capita, Android share, region, language (World Bank, StatCounter)
  taste        the taste model scores sampled users of each neighbour; the launch market gets their weighted mix
  activity     active hours per daily user and impressions per hour, from the neighbours
  payout level log(value) ~ log(GDP per capita) across watched countries, plus the neighbours' residual
  start rate   learned across watched countries: log(starts/impression) = base_k + γ·log(taste_k / feed share_k)
  finishing    completions per start and margin per completion (per platform × category) from the neighbours
  rewards      start elasticity to the reward share, from the neighbours' past reward tests

Backtest over all 20 watched countries (each hidden in turn, predicted from the other 19), against the simulator's
true next 4 weeks: best offers, margin per user, best reward level. Learned from the twin's 14-day learning window,
so the result is deterministic per seed and cached.
"""
from __future__ import annotations

import threading

import numpy as np

from . import config as cfg

WEEKS = 4
DAYS = WEEKS * 7
N_PATHS = 400
ADOPTION_DAYS = 7.0      # simulated: launch users build up as 1 - exp(-day / 7); ~98% of steady state on day 28
REWARD_GRID = [0.45, 0.50, 0.55, 0.60, 0.65]

# public: main language (ISO 639-1); "family" groups Romance and Germanic languages for a partial match.
LANGUAGE = {"US": "en", "CA": "en", "MX": "es", "BR": "pt", "GB": "en", "DE": "de", "FR": "fr", "NL": "nl",
            "ES": "es", "IT": "it", "PL": "pl", "SE": "sv", "TR": "tr", "ZA": "en", "IN": "en", "ID": "id",
            "PH": "en", "JP": "ja", "KR": "ko", "AU": "en", "AR": "es"}
FAMILY = {"es": "romance", "pt": "romance", "it": "romance", "fr": "romance", "en": "germanic", "de": "germanic",
          "nl": "germanic", "sv": "germanic"}
# Launch candidates (not among the 20 watched). public: StatCounter mobile OS share, October 2026.
CANDIDATES = {"AR": {"iso": "AR", "name": "Argentina", "ios_share": 0.1422, "region": "Latin America", "tz": -3,
                     "lat": -38.4, "lng": -63.6}}
# simulated: fraud history of the watched countries = bot-farm incidents per quarter before the demo
# (invented; Latin America and Asia see more emulator farms on Android). Session incidents are added on top.
FARMS_PER_QUARTER = {"US": 1.2, "CA": 0.6, "MX": 1.8, "BR": 2.4, "GB": 0.7, "DE": 0.5, "FR": 0.6, "NL": 0.3,
                     "ES": 0.8, "IT": 0.7, "PL": 0.9, "SE": 0.2, "TR": 1.6, "ZA": 1.5, "IN": 3.0, "ID": 2.6,
                     "PH": 2.2, "JP": 0.3, "KR": 0.4, "AU": 0.4}
FARM_LOSS_DAYS = 2.0     # simulated: a farm costs ~2 days of the country's margin before it is caught and cleaned up
SIGMA_FLOOR = 0.05

_CACHE: dict[int, dict] = {}
_LAUNCH: dict[tuple[str, int], dict] = {}     # per (country, seed): everything that does not depend on users
_LOCK = threading.Lock()


def _features(iso: str) -> dict:
    if iso in cfg.ISO_INDEX:
        c = cfg.COUNTRIES[cfg.ISO_INDEX[iso]]
        return {"iso": iso, "name": c.name, "ios_share": c.ios_share, "region": c.region, "tz": c.tz}
    return dict(CANDIDATES[iso])


def distance(a: str, b: str) -> float:
    fa, fb = _features(a), _features(b)
    d = abs(np.log(cfg.GDP_PC[a] / cfg.GDP_PC[b])) / 0.5            # a factor 1.65 in income = 1
    d += abs(fa["ios_share"] - fb["ios_share"]) / 0.15              # 15 points of Android share = 1
    d += 0.0 if fa["region"] == fb["region"] else 1.0
    la, lb = LANGUAGE[a], LANGUAGE[b]
    d += 0.0 if la == lb else (0.4 if FAMILY.get(la, la) == FAMILY.get(lb, lb) else 0.8)
    return float(d)


def neighbours(iso: str, pool: list[str], k: int = 3) -> list[tuple[str, float]]:
    ds = sorted((distance(iso, n), n) for n in pool if n != iso)[:k]
    w = np.exp(-np.array([d for d, _ in ds]))
    w /= w.sum()
    return [(n, float(x)) for (_, n), x in zip(ds, w)]


# ---------------------------------------------------------------------------------------------- learned stats
def _true_future(seed: int, reward_share: float | None) -> list[dict]:
    """Simulator ground truth for the 4 weeks after the learning window (backtest only, never used to predict)."""
    from .reality import Reality

    r = Reality(seed=seed)
    r.run(cfg.HISTORY_HOURS)
    if reward_share is not None:
        for c in range(cfg.C):
            r.apply({"type": "set_reward", "country": c, "reward_share": reward_share})
    r.run(DAYS * 24)
    return r.true[cfg.HISTORY_HOURS:]


def _learn(seed: int) -> dict:
    """Per-country stats from the twin's learning window, plus the simulator's truth for the backtest."""
    if seed in _CACHE:
        return _CACHE[seed]
    from .reality import Reality
    from .world import taste_model

    r = Reality(seed=seed)
    r.run(cfg.HISTORY_HOURS)
    obs = r.observed[: cfg.HISTORY_HOURS]
    days = cfg.HISTORY_HOURS / 24
    S = lambda k: np.sum([o[k] for o in obs], axis=0)  # noqa: E731
    act, imp, st, co = S("active_users"), S("impressions"), S("starts"), S("completions")
    marg, rev = S("revenue") - S("rewards"), S("revenue")
    dau = np.array([c.dau for c in cfg.COUNTRIES], dtype=float)                # own business data: daily users
    tm = taste_model()
    taste = np.zeros((cfg.C, cfg.K))
    for c in range(cfg.C):
        share = np.array([cfg.COUNTRIES[c].ios_share, 1 - cfg.COUNTRIES[c].ios_share])
        taste[c] = sum(share[p] * tm.country_demand(r.sample_users(c, p, 600)) for p in range(cfg.P))
    stats = {
        "taste": taste,                                                        # (C,K) taste-model demand
        "dau_pc": dau / np.array([cfg.POPULATION[c.iso] for c in cfg.COUNTRIES]),
        "hours_per_user": act.sum(1) / days / dau,                             # active hours per daily user
        "imp_per_user": imp.sum((1, 2)) / act.sum(1),                          # impressions per active hour
        "feed": imp.sum(1) / imp.sum((1, 2))[:, None],                         # (C,K) impression shares
        "sr": st.sum(1) / imp.sum(1),                                          # (C,K) starts per impression
        "cr": co / st,                                                         # (C,P,K)
        "mpc": marg / co,                                                      # (C,P,K) margin per completion
        "hist_cat_margin": marg.sum(1),                                        # (C,K) margin by category (history)
        "cph": co / act[:, :, None],                                           # (C,P,K) completions per active hour
        "reward_tests": r.reward_tests(),                                      # (C,) measured start elasticity
    }
    # payout level seen in the data: Android revenue per completion vs. the offer list price (US Android)
    stats["value"] = np.median(rev[:, 1] / co[:, 1] / (cfg.PAYOUT * cfg.ACCEPTANCE), axis=1)

    # ---- ground truth (backtest only)
    fut = _true_future(seed, None)
    tm_ = lambda rows: np.sum([(x["revenue"] - x["rewards"]).sum(1) for x in rows], axis=0) / DAYS  # noqa: E731
    cat_margin = tm_(fut)                                                       # (C,K) margin per day by category
    stats["true_day_margin"] = cat_margin.sum(1)
    stats["true_margin_per_user"] = stats["true_day_margin"] / dau
    stats["true_cat_margin"] = cat_margin
    stats["true_cat_margin_per_imp"] = cat_margin / (np.sum([x["impressions"].sum(1) for x in fut], axis=0) / DAYS)
    grid = np.zeros((cfg.C, len(REWARD_GRID)))
    for g, rs in enumerate(REWARD_GRID):
        grid[:, g] = stats["true_day_margin"] if rs == cfg.REWARD_SHARE else tm_(_true_future(seed, rs)).sum(1)
    stats["true_reward_grid"] = grid
    stats["true_best_reward"] = np.array(REWARD_GRID)[grid.argmax(1)]
    _CACHE[seed] = stats
    return stats


def _wmean_log(vals: np.ndarray, w: np.ndarray) -> float:
    return float(np.sum(w * np.log(vals)))


def _value_fit(st: dict, pool: list[str], iso: str, nb: list[tuple[str, float]]) -> float:
    """log payout level = a + b log GDP over the pool, plus the neighbours' weighted residual."""
    lg = np.log([cfg.GDP_PC[p] for p in pool])
    lv = np.log(st["value"][[cfg.ISO_INDEX[p] for p in pool]])
    b, a = np.polyfit(lg, lv, 1)
    res = {p: lv[i] - (a + b * lg[i]) for i, p in enumerate(pool)}
    return float(a + b * np.log(cfg.GDP_PC[iso]) + sum(x * res[n] for n, x in nb))


def _loo_sigma(st: dict, pool: list[str]) -> dict:
    """Out-of-sample error of each transferred input, measured by hiding each pool country in turn. Used as the
    forecast's spread, so the p10-p90 range is calibrated on data rather than chosen."""
    err = {"value": [], "hours": [], "dau_pc": [], "elasticity": []}
    for h in pool:
        rest = [p for p in pool if p != h]
        nb = neighbours(h, rest)
        ni = np.array([cfg.ISO_INDEX[n] for n, _ in nb])
        w = np.array([x for _, x in nb])
        i = cfg.ISO_INDEX[h]
        err["value"].append(_value_fit(st, rest, h, nb) - np.log(st["value"][i]))
        err["hours"].append(_wmean_log(st["hours_per_user"][ni], w) - np.log(st["hours_per_user"][i]))
        err["dau_pc"].append(_wmean_log(st["dau_pc"][ni], w) - np.log(st["dau_pc"][i]))
        err["elasticity"].append(_wmean_log(st["reward_tests"][ni], w) - np.log(st["reward_tests"][i]))
    return {k: max(float(np.sqrt(np.mean(np.square(e)))), SIGMA_FLOOR) for k, e in err.items()}


def predict(iso: str, seed: int = 42, pool: list[str] | None = None, n: int = N_PATHS) -> dict:
    """Per-user forecast for `iso`, learning only from `pool` (watched countries, target excluded)."""
    st = _learn(seed)
    pool = [c.iso for c in cfg.COUNTRIES if c.iso != iso] if pool is None else [p for p in pool if p != iso]
    idx = np.array([cfg.ISO_INDEX[p] for p in pool])
    nb = neighbours(iso, pool)
    ni = np.array([cfg.ISO_INDEX[n] for n, _ in nb])
    w = np.array([x for _, x in nb])
    f = _features(iso)
    share = np.array([f["ios_share"], 1 - f["ios_share"]])
    rng = np.random.default_rng([seed, 7041, sum(map(ord, iso))])
    sig = _loo_sigma(st, pool)

    taste = (w[:, None] * st["taste"][ni]).sum(0)                              # taste-model demand of neighbours
    taste /= taste.sum()
    v_m = _value_fit(st, pool, iso, nb)
    h_m = _wmean_log(st["hours_per_user"][ni], w)
    e_m = _wmean_log(st["reward_tests"][ni], w)
    dau_pc = float(np.exp(_wmean_log(st["dau_pc"][ni], w)))
    # start rate: log sr = base_k + γ log(taste / feed), fitted over the pool
    X = np.log(st["taste"][idx] / st["feed"][idx])
    Y = np.log(st["sr"][idx])
    Xc, Yc = X - X.mean(0), Y - Y.mean(0)
    gamma = float(np.sum(Xc * Yc) / np.sum(Xc * Xc))
    base = Y.mean(0) - gamma * X.mean(0)
    sr_s = max(float(np.std(Yc - gamma * Xc)), SIGMA_FLOOR)
    feed = taste * cfg.PAYOUT ** 1.1                                           # production rule on predicted taste
    feed /= feed.sum()
    ipu = float(np.exp(np.sum(w * np.log(st["imp_per_user"][ni]))))
    cr = np.exp((w[:, None, None] * np.log(st["cr"][ni])).sum(0))             # (P,K)
    mpc_unit = (w[:, None, None] * (st["mpc"][ni] / st["value"][ni][:, None, None])).sum(0)
    rpc_unit = cfg.PAYOUT[None, :] * cfg.PLATFORM_PAYOUT[:, None] * cfg.ACCEPTANCE
    rew_unit = rpc_unit / cfg.ACCEPTANCE - mpc_unit                           # reward cost per completion / value

    value = np.exp(v_m + sig["value"] * rng.standard_normal(n))
    hours = np.exp(h_m + sig["hours"] * rng.standard_normal(n))
    elast = np.exp(e_m + sig["elasticity"] * rng.standard_normal(n))
    sr = np.exp(base[None, :] + gamma * np.log(taste / feed)[None, :] + sr_s * rng.standard_normal((n, cfg.K)))
    # completions per active hour by platform × category: the neighbours' observed rates (their taste and feed are
    # already in the data), with the category spread among the neighbours as uncertainty
    lc = np.log(st["cph"][ni])                                                 # (3,P,K)
    lc_m = (w[:, None, None] * lc).sum(0)
    lc_s = np.maximum(np.sqrt((w[:, None, None] * (lc - lc_m) ** 2).sum(0)), 0.1)
    comp = np.exp(lc_m[None] + lc_s[None] * rng.standard_normal((n, 1, cfg.K))) * share[None, :, None]

    def per_user(rs: float) -> np.ndarray:                                    # margin per daily user (n,K)
        mult = (rs / cfg.REWARD_SHARE) ** elast
        m = (comp * (rpc_unit - rew_unit * rs / cfg.REWARD_SHARE)[None]).sum(1)
        return m * (mult * value * hours)[:, None]

    pu = per_user(cfg.REWARD_SHARE)
    grid = np.array([np.median(per_user(rs).sum(1)) for rs in REWARD_GRID])
    cat = pu.mean(0)
    return {"iso": iso, "neighbours": nb, "sigma": sig, "taste": taste, "feed": feed, "gamma": gamma,
            "per_user": pu.sum(1), "margin_share_by_category": cat / cat.sum(),
            "reward_grid_per_user": grid, "best_reward": REWARD_GRID[int(grid.argmax())],
            "elasticity": float(np.exp(e_m)), "value": float(np.exp(v_m)), "hours_per_user": float(np.exp(h_m)),
            "dau_pc": dau_pc, "sigma_dau_pc": sig["dau_pc"]}


def _q(x: np.ndarray, scale: float = 1.0) -> dict:
    return {k: float(np.percentile(x, p)) * scale for k, p in (("p10", 10), ("p50", 50), ("p90", 90))}


def backtest(seed: int = 42) -> dict:
    """Hold out each of the 20 watched countries, predict it from the other 19, compare with the simulator's truth."""
    st = _learn(seed)
    rows = []
    for c in cfg.COUNTRIES:
        h, i = c.iso, cfg.ISO_INDEX[c.iso]
        pr = predict(h, seed, [x.iso for x in cfg.COUNTRIES if x.iso != h])
        top3 = [int(k) for k in np.argsort(pr["margin_share_by_category"])[::-1][:3]]
        true_top = int(np.argmax(st["true_cat_margin"][i]))
        q = _q(pr["per_user"], 1000)
        true_pu = float(st["true_margin_per_user"][i]) * 1000
        true_rs = float(st["true_best_reward"][i])
        steps = round(abs(pr["best_reward"] - true_rs) / 0.05)
        # naive baseline without similarity: the average category ranking of the other 19 countries
        others = [j for j in range(cfg.C) if j != i]
        hs = st["hist_cat_margin"][others]
        naive = [int(k) for k in np.argsort((hs / hs.sum(1, keepdims=True)).mean(0))[::-1][:3]]
        rows.append({"country": h, "name": c.name, "learned_from": [n for n, _ in pr["neighbours"]],
                     "offers": {"predicted_top3": [cfg.CATEGORIES[k] for k in top3], "true_top": cfg.CATEGORIES[true_top],
                                "hit": true_top in top3, "top1_hit": true_top == top3[0],
                                "naive_top3": [cfg.CATEGORIES[k] for k in naive], "naive_hit": true_top in naive,
                                "naive_top1_hit": true_top == naive[0]},
                     "per_1000_users": {"predicted": round(q["p50"], 2), "range": [round(q["p10"], 2), round(q["p90"], 2)],
                                        "true": round(true_pu, 2), "error": round(q["p50"] / true_pu - 1, 3),
                                        "inside_p10_p90": bool(q["p10"] <= true_pu <= q["p90"])},
                     "reward": {"predicted": pr["best_reward"], "true_best": true_rs, "steps_off": steps,
                                "within_one_step": steps <= 1}})
    n = len(rows)
    errs = [abs(r["per_1000_users"]["error"]) for r in rows]
    offers_hit = sum(r["offers"]["hit"] for r in rows)
    top1 = sum(r["offers"]["top1_hit"] for r in rows)
    naive_hit = sum(r["offers"]["naive_hit"] for r in rows)
    naive_top1 = sum(r["offers"]["naive_top1_hit"] for r in rows)
    inside = sum(r["per_1000_users"]["inside_p10_p90"] for r in rows)
    rew1 = sum(r["reward"]["within_one_step"] for r in rows)
    rew0 = sum(r["reward"]["steps_off"] == 0 for r in rows)
    return {
        "n": n, "countries": rows,
        "offers": {"true_top_in_top3": offers_hit, "true_top_is_first": top1,
                   "naive_baseline": {"true_top_in_top3": naive_hit, "true_top_is_first": naive_top1,
                                      "rule": "same ranking everywhere: average of the other 19 countries"},
                   "text": f"best offers right in {offers_hit} of {n}"},
        "margin_per_user": {"median_abs_error": round(float(np.median(errs)), 3),
                            "max_abs_error": round(max(errs), 3), "inside_p10_p90": inside,
                            "text": f"margin per user typically within ±{np.median(errs) * 100:.0f}%, "
                                    f"{inside} of {n} inside the range"},
        "reward": {"within_one_step": rew1, "exact": rew0, "step": 0.05,
                   "text": f"best reward level within one step in {rew1} of {n}"},
        "method": "Each watched country is hidden in turn and predicted from the other 19 only, then compared with "
                  "the simulator's true next 4 weeks: the category that earns the most margin, margin per daily "
                  "user, and the reward share (45-65%) that earns the most margin.",
    }


def _fraud_risk(nb: list[tuple[str, float]], day_margin: float, incidents: list[dict] | None) -> dict:
    session = {}
    for inc in incidents or []:
        if inc.get("root") in ("new_accounts", "active_users") or "farm" in str(inc.get("title", "")).lower():
            session[inc["country"]] = session.get(inc["country"], 0) + 1
    rate = sum(x * (FARMS_PER_QUARTER[n] + session.get(n, 0)) for n, x in nb)  # farms per quarter
    chance = 1 - np.exp(-rate * DAYS / 91)
    level = "high" if chance >= 0.5 else "medium" if chance >= 0.25 else "low"
    return {"level": level, "farm_chance_4w": round(float(chance), 2),
            "expected_loss_usd_4w": round(float(chance * FARM_LOSS_DAYS * day_margin)),
            "farms_per_quarter_neighbours": {n: FARMS_PER_QUARTER[n] + session.get(n, 0) for n, _ in nb},
            "text": f"{level.capitalize()}: about a {chance:.0%} chance of a bot farm in the first 4 weeks, "
                    f"based on {', '.join(_features(n)['name'] for n, _ in nb)}",
            "source": "simulated fraud history of the neighbours (plus bot-farm issues in this session)"}


def _tested_line(bt: dict) -> str:
    """The on-screen backtest line: how often Argus picked the #1 offer, with the naive baseline when it is clearly worse."""
    n, top1 = bt["n"], bt["offers"]["true_top_is_first"]
    naive = bt["offers"]["naive_baseline"]["true_top_is_first"]
    line = f"Tested on {n} countries we know: picked the #1 offer in {top1} of {n}"
    return line + (f" (the global average: {naive} of {n})" if top1 - naive > 2 else "")


def launch(iso: str = "AR", seed: int = 42, incidents: list[dict] | None = None, users: int | None = None) -> dict:
    iso = (iso or "AR").upper()
    if iso in cfg.ISO_INDEX:
        raise ValueError(f"{iso} is already watched; launch candidates: {sorted(CANDIDATES)}")
    if iso not in CANDIDATES:
        raise ValueError(f"unknown launch country '{iso}'; candidates: {sorted(CANDIDATES)}")
    if users is not None and not 1_000 <= int(users) <= 50_000_000:
        raise ValueError("users must be between 1,000 and 50,000,000 daily users")
    key = (iso, seed)
    if key not in _LAUNCH:
        from .precompute import wait_ready
        wait_ready()
    if key not in _LAUNCH:
        with _LOCK:                     # one computation per key, even if a warm-up thread is already on it
            if key not in _LAUNCH:
                _LAUNCH[key] = _launch(iso, seed)
    base = _LAUNCH[key]
    pr = base["_pred"]
    f = _features(iso)
    assumed = users is None
    n_users = round(pr["dau_pc"] * cfg.POPULATION[iso], -3) if assumed else int(users)
    adopt = 1 - np.exp(-np.arange(1, DAYS + 1) / ADOPTION_DAYS)
    day28 = _q(pr["per_user"], n_users * adopt[-1])
    rnd = lambda q: {k: round(v) for k, v in q.items()}  # noqa: E731
    out = {k: v for k, v in base.items() if not k.startswith("_")}
    out["users"] = {
        "daily_users": int(n_users), "assumed": assumed,
        "source": (f"assumed: daily users per capita of {', '.join(_features(n)['name'] for n, _ in pr['neighbours'])}"
                   f" × population of {f['name']} ({cfg.POPULATION[iso]:,})") if assumed else "input",
        "note": "An assumption, not a prediction: user scale is the part that does not transfer well "
                "(see backtest)." if assumed else "Given by the caller."}
    out["margin_per_day_usd"] = rnd(day28)
    out["margin_per_day_weekly_usd"] = [rnd(_q(pr["per_user"], n_users * adopt[7 * w:7 * w + 7].mean()))
                                        for w in range(WEEKS)]
    out["margin_4w_total_usd"] = rnd(_q(pr["per_user"], n_users * adopt.sum()))
    out["fraud_risk"] = _fraud_risk(pr["neighbours"], day28["p50"], incidents)
    tag = "assumed" if assumed else "planned"
    out["total_line"] = (f"At {n_users:,.0f} daily users ({tag}): about +${day28['p50']:,.0f} a day after 4 weeks")
    return out


def _launch(iso: str, seed: int) -> dict:
    f = _features(iso)
    pr = predict(iso, seed)
    per1000 = _q(pr["per_user"], 1000)
    grid = pr["reward_grid_per_user"] * 1000
    cur = grid[REWARD_GRID.index(cfg.REWARD_SHARE)]
    order = np.argsort(pr["margin_share_by_category"])[::-1][:3]
    mix = [{"category": cfg.CATEGORIES[k], "margin_share": round(float(pr["margin_share_by_category"][k]), 3),
            "taste": round(float(pr["taste"][k]), 3)} for k in order]
    bt = backtest(seed)
    names = [_features(n)["name"] for n, _ in pr["neighbours"]]
    r2 = lambda q: {k: round(v, 2) for k, v in q.items()}  # noqa: E731
    return {
        "_pred": pr,
        "country": {"iso": iso, "name": f["name"], "region": f["region"], "lat": f["lat"], "lng": f["lng"],
                    "ios_share": f["ios_share"], "gdp_per_capita_usd": cfg.GDP_PC[iso],
                    "population": cfg.POPULATION[iso]},
        "horizon_days": DAYS,
        "margin_per_1000_users_day_usd": r2(per1000),
        "best_offer_mix": mix,
        "best_reward": {"reward_share": pr["best_reward"], "current_share": cfg.REWARD_SHARE,
                        "uplift_per_1000_users_day_usd": round(float(grid.max() - cur), 2),
                        "grid": [{"reward_share": rs, "margin_per_1000_users_day_usd": round(float(g), 2)}
                                 for rs, g in zip(REWARD_GRID, grid)],
                        "elasticity": round(pr["elasticity"], 2),
                        "note": "Starts respond to the reward share as measured in the neighbours' past reward tests; "
                                "longer-term loyalty effects are not modelled."},
        "learned_from": [{"iso": n, "name": _features(n)["name"], "weight": round(x, 2),
                          "distance": round(distance(iso, n), 2)} for n, x in pr["neighbours"]],
        "assumptions": {"payout_level_vs_us": round(pr["value"], 3), "active_hours_per_user": round(pr["hours_per_user"], 2),
                        "adoption": f"users build up as 1 - exp(-day/{ADOPTION_DAYS:g})",
                        "taste_start_elasticity": round(pr["gamma"], 3), "paths": N_PATHS},
        "backtest": bt,
        "headline": f"Launching in {f['name']}: about ${per1000['p50']:,.0f} margin per 1,000 users a day",
        "learned_line": f"Learned from {', '.join(names[:-1])} and {names[-1]}",
        "backtest_line": _tested_line(bt),
        "range_line": f"Likely ${per1000['p10']:,.0f}–${per1000['p90']:,.0f} per 1,000 users a day (p10–p90)",
    }
