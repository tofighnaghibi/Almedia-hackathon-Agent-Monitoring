"""Argus: the self-driving analyst loop (runs once per simulated hour).

Argus sees reality only through `RealityView`: the observed metric stream, the
public marketing calendar, unlabeled user samples and the actions it applies.
"""
from __future__ import annotations

import json
import re

import numpy as np

from . import config as cfg
from . import llm
from .graph import MECHANISMS, MetricGraph, Node, evaluate, parse_expression
from .taste import TasteModel
from .twin import KPIS, STAY, TOP_PAYOUT, USER_SIDE, Twin

WINDOW = 3
ALERT_Z = 4.0
ROOT_Z = 3.0
WATCH_Z = 4.0                 # amber: |z| >= 4 for one hour
QUIET_Z = 3.0                 # "healthy" for resolution purposes
INCIDENT_HOURS = 2            # an incident needs the alert to persist this long ...
INCIDENT_IMPACT = 150.0       # ... and impact above this (USD/h) ...
INCIDENT_Z = 8.0              # ... or a deviation this extreme
L3_MIN_IMPACT = 50.0          # USD/h before a user-level run is worth it
SUPPORTED, PARTIAL = 0.8, 0.3
QUIET_TO_RESOLVE = 6
SEV_HIGH_USD, SEV_HIGH_SOON_USD = 25_000, 10_000       # 7-day cost of doing nothing (USD)
SEV_MEDIUM_USD, SEV_MEDIUM_SOON_USD = 5_000, 1_000     # "soon" = advertisers react within 24 h
FAMILY = {**{m: "users" for m in USER_SIDE}, "revenue": "revenue", "rewards": "rewards"}   # cause families
NEW_CAUSE_Z = 5.0             # a second cause in a country with an open issue needs a strong root, 2 h in a row
STATUS_LABEL = {"open": "Open", "persisting": "Still going", "fix_applied": "Fix applied", "verified": "Fix verified",
                "resolved": "Resolved"}
EVENT_LABEL = {"chargeback": "Advertisers start reclaiming money", "budget_cut": "Advertiser cuts the budget",
               "tracking_fixed": "Advertiser fixes tracking"}
RESIDUAL_FRAUD = {"none": 1.0, "cap_rewards": 0.4, "verify_new_accounts": 0.05, "block_cluster": 0.0}

KPI_LABEL = {"new_accounts": "new accounts", "completions": "completions", "revenue": "revenue",
             "rewards": "rewards", "margin": "margin", "quality_per_completion": "quality per completion"}
MECH_LABEL = {"new_accounts": "New accounts", "active_users": "Active users", "impressions": "Offer impressions",
              "starts": "Offer starts", "completions": "Completions", "revenue": "Advertiser revenue",
              "rewards": "User rewards", "quality": "Valuable users"}
KPI_KEY_OF_LABEL = {v: k for k, v in KPI_LABEL.items()}
SERIES_METRIC = {"new_accounts": ("New sign-ups per hour", "per hour", "New sign-ups"),
                 "completions": ("Completed offers per hour", "per hour", "Completed offers"),
                 "revenue": ("Revenue per hour", "USD/h", "Revenue"),
                 "rewards": ("Rewards paid per hour", "USD/h", "Rewards paid"),
                 "margin": ("Margin per hour", "USD/h", "Margin"),
                 "quality_per_completion": ("Valuable users per completed offer", "share", "Valuable users")}
FIX_INFO = {
    "none": ("Do nothing", "Keep monitoring; no change to the marketplace."),
    "block_cluster": ("Block the fake accounts", "Suspend the flagged accounts and their device fingerprints in {country} "
                                       "({platform}); withhold their pending rewards."),
    "verify_new_accounts": ("Verify new accounts", "Require phone and device verification for new {platform} "
                                                   "accounts in {country}."),
    "cap_rewards": ("Cap rewards", "Lower the user reward share on strategy-game and fintech offers to 35% for "
                                   "{platform} users in {country}."),
    "notify_advertiser": ("Notify advertiser", "Send the advertisers' tracking team the postback gap so they fix "
                                               "tracking."),
    "pause_offers": ("Pause offers", "Hide the affected {categories} offers for {platform} users in {country} for "
                                     "18 h, so users are not rewarded for unconfirmed completions."),
    "pause_notify": ("Pause + notify", "Pause the affected offers for 18 h and get the advertiser to fix "
                                       "tracking."),
}


class RealityView:
    """The only window Argus has on the simulated business."""

    def __init__(self, reality) -> None:
        self._reality = reality

    @property
    def t(self) -> int:
        return self._reality.t

    @property
    def observed(self) -> list[dict]:
        return self._reality.observed

    @property
    def calendar(self) -> list:
        return self._reality.events

    def sample_users(self, c: int, p: int, n: int = 600) -> list[dict]:
        users = self._reality.sample_users(c, p, n)
        return [{k: v for k, v in u.items() if not k.startswith("_")} for u in users]  # no labels

    def apply(self, intervention: dict) -> None:
        self._reality.apply(intervention)


GROWTH_NOUN = ["casual games", "strategy games", "fintech apps", "streaming apps", "shopping cashback", "surveys"]
SHORT_CAT = ["casual", "strategy", "fintech", "streaming", "shopping", "surveys"]
UNUSUAL_WORD = {"new_accounts": "sign-ups", "revenue": "revenue", "completions": "completed offers",
                "rewards": "rewards", "quality_per_completion": "valuable users per completed offer"}


def _and(xs: list[str], word: str = "and") -> str:
    return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + f" {word} " + xs[-1]


def _ratio_text(r: float) -> str:
    """1.58 -> '1.6× usual'; 0.14 -> '−86% vs usual'; 1.2 -> '+20% vs usual'."""
    if r >= 1.5:
        return f"{r:.1f}× usual"
    pct = round((r - 1) * 100)
    return f"{'+' if pct >= 0 else '−'}{abs(pct)}% vs usual"


SHORT_COUNTRY = {"GB": "the UK", "US": "the US", "NL": "the Netherlands", "PH": "the Philippines"}
OFFER_NOUN = ["casual-game", "strategy-game", "fintech", "streaming", "shopping-cashback", "survey"]


def _country_phrase(name: str) -> str:
    return f"the {name}" if name.startswith(("United", "Philippines", "Netherlands")) else name


DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DOW_FULL = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def hour_label(t: int) -> str:
    """Simulated UTC time at which hour t's data is complete, e.g. "Mon 04:00" for the hour 03:00-04:00.

    Convention for every label in the API: data are labelled by the end of the hour ("data through 04:00"),
    and the clock shows the end of the last hour Argus has processed. Sim day 0 starts on a Monday.
    """
    day, hour = divmod(int(t) + 1, 24)
    return f"{DOW[day % 7]} {hour:02d}:00"


_NUM = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _numbers(text: str) -> list[float]:
    out = []
    for m in _NUM.finditer(text):
        try:
            out.append(float(m.group().replace(",", "")))
        except ValueError:
            continue
    return out


def ungrounded_numbers(text: str, source: str) -> list[float]:
    """Numbers in generated text that do not appear in the source (facts + templates), allowing for rounding,
    percent <-> fraction and USD/h <-> USD per 3 h window. Small counts (<= 24, e.g. hours, sentences) are allowed."""
    allowed: set[float] = set()
    for v in _numbers(source):
        allowed.update({v, round(v), round(v, 1), round(v * 100), round(v * 100, 1), round(v / 100, 3)})
    bad = []
    for x in _numbers(text):
        if x <= 24 or x in allowed:
            continue
        if any(abs(x - a) <= max(0.5, 0.006 * abs(a)) for a in allowed):
            continue
        bad.append(x)
    return bad


def fake_count_phrase(n_flagged: int, n_sample: int, total: int) -> str:
    """One wording everywhere: the sample count and the estimated total."""
    return f"{n_flagged} flagged in a sample of {n_sample}, about {total} in total"


def _fmt_usd(x: float) -> str:
    return f"${x:,.0f}" if abs(x) >= 1 else f"${x:,.2f}"


def _signed_usd(x: float) -> str:
    return ("+" if x >= 0 else "−") + _fmt_usd(abs(x))


def _country_sums(r: dict) -> dict:
    out = {"new_accounts": r["new_accounts"].sum(1), "active_users": r["active_users"].sum(1)}
    for k in ("impressions", "starts", "completions", "revenue", "rewards", "quality"):
        out[k] = r[k].sum((1, 2))
    return out


def _with_formulas(s: dict) -> dict:
    s = dict(s)
    s["margin"] = s["revenue"] - s["rewards"]
    s["quality_per_completion"] = s["quality"] / np.maximum(s["completions"], 1e-9)
    return s


class Agent:
    def __init__(self, view: RealityView, twin: Twin, taste: TasteModel, graph: MetricGraph | None = None,
                 seed: int = 0) -> None:
        self.view, self.twin, self.taste = view, twin, taste
        self.graph = graph or MetricGraph()
        self.seed = seed
        self.incidents: list[dict] = []
        self.explained: list[dict] = []
        self._explained_idx: dict[tuple, int] = {}
        self.consec = np.zeros((cfg.C, len(KPIS)))
        self.quiet = np.zeros(cfg.C)
        self.zmax_hist: list[np.ndarray] = []
        self.wait_info: dict[int, dict] = {}
        self.wait_log: list[dict] = []
        self.waiting = np.zeros(cfg.C, dtype=bool)
        self.explaining = np.zeros(cfg.C, dtype=bool)
        self.extensions: dict[str, dict] = {}
        self.llm_rejections: list[dict] = []           # LLM reports that failed the grounding check
        self.cand_prev: dict[int, set] = {}             # root candidates per country in the previous hour
        self.investigations = 0
        self.l3_runs = 0
        self.opened_segments: set[tuple[int, int, int]] = set()   # (country, platform, category) walked at L2
        self.users_checked = 0                                     # user records pulled for account-level checks
        self.last: dict | None = None
        self.t = view.t - 1
        self.potential = self.compute_potential()

    # ================================================================== hourly loop
    def on_hour(self) -> None:
        row = self.view.observed[-1]
        t = row["t"]
        self.t = t
        self.twin.observe(row)
        ts = [t - i for i in range(WINDOW)][::-1]
        res = self.twin.l1_z(t)
        naive = self.twin.l1_z(t, calendar=False)
        z, zn = res["z"], naive["z"]

        # 1. data check
        comp = np.min([self.twin.rows[x]["completeness"] for x in ts], axis=0)
        self.waiting = comp < 0.9
        for c in range(cfg.C):
            if self.waiting[c] and c not in self.wait_info:
                i = int(np.argmax(np.abs(zn[c])))
                self.wait_info[c] = {"t": t, "naive_z": float(zn[c, i]), "rel": float(naive["rel"][c, i]), "kpi": KPI_LABEL[KPIS[i]],
                                     "share": float(self.twin.rows[t]["completeness"][c])}
                self.wait_log.append({"t": t, "country": cfg.COUNTRIES[c].iso,
                                      "share": self.wait_info[c]["share"]})
            elif not self.waiting[c] and c in self.wait_info:
                info = self.wait_info.pop(c)
                self.explained.append({"t": info["t"], "country": cfg.COUNTRIES[c].iso,
                                       "event": f"Late data ({info['share']:.0%} of rows delivered): waited, "
                                                f"backfilled after {t - info['t']} h, no alert",
                                       "naive_z": round(info["naive_z"], 1),
                                       "twin_z": round(float(z[c, np.argmax(np.abs(z[c]))]), 1),
                                       **self._explained_line(c, info["rel"], "late data", info["kpi"])})

        # 2. watch: twin vs naive
        ext = self._extension_z(t)
        hit = (np.abs(z) >= ALERT_Z) & ~self.waiting[:, None]
        self.consec = np.where(hit, self.consec + 1, 0)
        alert = self.consec >= INCIDENT_HOURS
        allz = np.concatenate([z, ext], axis=1) if ext.size else z
        imax = np.argmax(np.abs(allz), axis=1)
        self.health = allz[np.arange(cfg.C), imax]
        self.zmax_hist.append(np.abs(z).max(1))
        self.explaining[:] = False
        for ev in self.view.calendar:
            if not ev.active(t):
                continue
            c = cfg.ISO_INDEX[ev.country]
            i = int(np.argmax(np.abs(zn[c])))
            if abs(zn[c, i]) >= ALERT_Z and np.abs(z[c]).max() < ALERT_Z:
                self.explaining[c] = True
                key = (ev.id, c)
                entry = {"t": t, "country": ev.country, "event": f"{ev.name} ({', '.join(ev.to_dict()['categories'])})"
                         f": {KPI_LABEL[KPIS[i]]} {naive['rel'][c, i]:+.0%} vs a normal week, "
                         f"as expected for this event",
                         "naive_z": round(float(zn[c, i]), 1), "twin_z": round(float(z[c, i]), 1),
                         **self._explained_line(c, float(naive["rel"][c, i]),
                                                "planned promo" if ev.reward_share > cfg.REWARD_SHARE else ev.name,
                                                KPI_LABEL[KPIS[i]])}
                if key not in self._explained_idx:
                    self._explained_idx[key] = len(self.explained)
                    self.explained.append(entry)
                elif abs(entry["naive_z"]) > abs(self.explained[self._explained_idx[key]]["naive_z"]):
                    entry["t"] = self.explained[self._explained_idx[key]]["t"]
                    self.explained[self._explained_idx[key]] = entry

        # 3-8. investigate new alerts, most impactful first
        d = res["obs"] - res["fc"]
        impact = np.abs(d[:, 2:5]).max(1) / WINDOW
        for c in np.argsort(-impact):
            if not alert[c].any():
                continue
            active = self._active_incidents(int(c))
            if active:
                # Same country: merge only into the issue with the same cause (platform × cause family); a strong
                # root of another family that holds for 2 h is a different problem and gets its own issue.
                cands = self._root_candidates(int(c), [t - i for i in range(WINDOW)][::-1])
                now_keys = {(cp, fam) for _, cp, _, _, fam in cands}
                prev = self.cand_prev.get(int(c), set())
                self.cand_prev[int(c)] = now_keys
                open_keys = {(i["p"], i["family"]) for i in active}
                new_cause = None
                matched = [i for i in active if (i["p"], i["family"]) in now_keys]
                for _, cp, ck, croot, fam in cands:
                    if (cp, fam) in open_keys or (cp, fam) not in prev:
                        continue
                    parent = self._downstream_of(active, cp, ck, croot, t)
                    if parent is not None:          # predicted consequence of an open issue, not a new problem
                        if parent not in matched:
                            matched.append(parent)
                        continue
                    new_cause = new_cause or (cp, ck, fam)
                for inc in matched or [active[-1]]:
                    self._merge_alert(inc, t)
                if new_cause is not None:
                    self.investigate(int(c), t, res, alert[c], focus=new_cause)
                continue
            if impact[c] <= INCIDENT_IMPACT and np.abs(z[c][alert[c]]).max() < INCIDENT_Z:
                continue                                                  # stays amber ("watch")
            self.investigate(int(c), t, res, alert[c])

        # 9-10. verification and resolution
        self.quiet = np.where(np.abs(z).max(1) < QUIET_Z, self.quiet + 1, 0)
        for inc in self.incidents:
            if inc["status"] == "fix_applied" and inc["verification"]["status"] == "pending" and t >= inc["due"]:
                self._verify(inc, t)
            if inc["status"] in ("verified", "fix_applied") and t >= inc["due"] and self.quiet[inc["c"]] >= QUIET_TO_RESOLVE:
                if inc["status"] == "verified" or inc["verification"]["status"] == "missed":
                    inc["status"] = "resolved"
                    inc["resolved_at"] = t
                    inc["trail"].append({"t": t, "level": "L1", "action": "Close the issue",
                                         "finding": f"{cfg.COUNTRIES[inc['c']].name} healthy for "
                                                    f"{QUIET_TO_RESOLVE} h"})
            elif (inc["status"] in ("open", "persisting") and inc["applied"] is None
                  and self.quiet[inc["c"]] >= QUIET_TO_RESOLVE and t - inc["last_seen_at"] >= QUIET_TO_RESOLVE):
                inc["status"] = "resolved"                # went away by itself
                inc["resolved_at"] = t
                inc["trail"].append({"t": t, "level": "L1", "action": "Close the issue",
                                     "finding": f"Recovered without action: {cfg.COUNTRIES[inc['c']].name} healthy "
                                                f"for {QUIET_TO_RESOLVE} h"})
        self.last = res
        if t % 24 == 0:
            self.potential = self.compute_potential()

    def _active_incident(self, c: int) -> dict | None:
        act = self._active_incidents(c)
        return act[0] if act else None

    def _active_incidents(self, c: int) -> list[dict]:
        return [inc for inc in self.incidents if inc["c"] == c and inc["status"] != "resolved"]

    def _merge_alert(self, inc: dict, t: int) -> None:
        """Another alert for the same problem: fold it into the issue instead of reporting it again."""
        inc["alerts_merged"] += 1
        inc["last_seen_at"] = t
        if inc["status"] == "open":
            inc["status"] = "persisting"
            inc["trail"].append({"t": t, "level": "L1", "action": "Re-check the country one hour later",
                                 "finding": "Still going; same cause, merged instead of a new report"})
        elif inc["status"] == "verified":
            inc["status"] = "persisting"
            inc["trail"].append({"t": t, "level": "L1", "action": "Watch after verification",
                                 "finding": "The numbers moved away again; issue re-opened"})

    def _downstream_of(self, active: list[dict], p: int, k: int, root: str, t: int) -> dict | None:
        """Is this root the advertiser reaction an open fraud issue predicted (same categories, mechanism, timing)?

        Chargebacks lower revenue per completion; a budget cut lowers impressions. Both hit the farm's offer
        categories on every platform. Matches are recorded on the issue as downstream effects.
        """
        for inc in active:
            if inc["kind"] not in ("fraud", "user") or k not in inc["fix_categories"]:
                continue
            pred = {e["label"]: e["after_h"] for e in inc["fixes"][0]["events"]}
            kind = ("chargeback" if root == "revenue" else "budget_cut" if root == "impressions" else None)
            if kind is None or EVENT_LABEL[kind] not in pred:
                continue
            after = t - inc["detected_at"]
            expected = pred[EVENT_LABEL[kind]]
            if not 0.5 * expected <= after <= 2 * expected + 24:
                continue
            key = [kind, cfg.PLATFORMS[p], cfg.CATEGORIES[k]]
            seen = inc.setdefault("downstream", [])
            if key not in [[d["effect"], d["platform"], d["category"]] for d in seen]:
                what = ("Advertisers reclaiming money" if kind == "chargeback" else "Advertiser cut the budget")
                seen.append({"effect": kind, "platform": cfg.PLATFORMS[p], "category": cfg.CATEGORIES[k],
                             "t": t, "label": hour_label(t), "after_h": after, "predicted_after_h": expected,
                             "text": f"{what}: {cfg.CATEGORIES[k].lower()} on {cfg.PLATFORMS[p]}, caused by the "
                                     f"farm, as predicted (~{expected} h, seen after {after} h)"})
                inc["trail"].append({"t": t, "level": "L2", "action": "Check a new change against open issues",
                                     "finding": seen[-1]["text"] + "; attached to this issue, not reported again"})
            return inc
        return None

    def _root_candidates(self, c: int, ts: list[int]) -> list[tuple]:
        """Strong roots per platform × category segment: (|z|, platform, category, root, cause family)."""
        gp = self.twin.given_parents(ts)
        out = []
        for p in range(cfg.P):
            zu = {m: self.twin.z_of(gp[m], c, p) for m in ("new_accounts", "active_users")}
            for k in range(cfg.K):
                mz = {**zu, **{m: self.twin.z_of(gp[m], c, p, k) for m in MECHANISMS if m not in zu}}
                root = next((m for m in MECHANISMS if abs(mz[m]) > ROOT_Z), None)
                if root is not None and abs(mz[root]) >= NEW_CAUSE_Z:
                    out.append((abs(mz[root]), p, k, root, FAMILY[root]))
        return sorted(out, reverse=True)

    def _elapsed(self, c: int) -> int:
        n = 0
        for zz in reversed(self.zmax_hist[:-1]):
            if zz[c] < 2:
                break
            n += 1
        return n + 1

    # ================================================================== investigation
    def investigate(self, c: int, t: int, res: dict, alert_row: np.ndarray,
                    focus: tuple[int, int, str] | None = None) -> dict:
        """focus = (platform, category, family) of a new cause found while another issue is open in the country."""
        self.investigations += 1
        tw = self.twin
        country = cfg.COUNTRIES[c]
        ts = [t - i for i in range(WINDOW)][::-1]
        z, rel = res["z"][c], res["rel"][c]
        dl = (res["obs"] - res["fc"])[c]
        alerting = [KPIS[i] for i in np.where(alert_row)[0]]
        impact = float(np.abs(dl[2:5]).max() / WINDOW)
        trail: list[dict] = []
        events = [ev for ev in self.view.calendar if ev.active(t) and ev.country == country.iso]
        trail.append({"t": t, "level": "L1",
                      "action": f"Watch {cfg.C} countries × {len(KPIS) + len(self.extensions)} KPIs against the "
                                f"twin forecast (|z| ≥ {ALERT_Z:.0f} for 2 h)",
                      "finding": f"{country.name}: " + ", ".join(f"{KPI_LABEL[k]} {z[KPIS.index(k)]:+.1f}σ "
                                                               f"({rel[KPIS.index(k)]:+.1%})" for k in alerting)
                                 + (f"; planned event active: {events[0].name}" if events
                                    else "; no planned event in the calendar")})

        # --- L2: localise by platform x category
        rows = [tw.rows[x] for x in ts]
        fcs = [tw.forecast(x) for x in ts]
        dsum = lambda key: sum(r[key][c] for r in rows) - sum(f[key][c] for f in fcs)  # noqa: E731
        d_rev, d_rew = dsum("revenue"), dsum("rewards")
        score = np.abs(d_rev) + np.abs(d_rew)                                       # (P,K)
        gp = tw.given_parents(ts)
        if focus is not None:
            p = focus[0]
        elif "new_accounts" in alerting:
            p = int(np.argmax([abs(tw.z_of(gp["new_accounts"], c, pp)) for pp in range(cfg.P)]))
        else:
            p = int(np.argmax(score.sum(1)))
        p_share = float(score[p].sum() / max(score.sum(), 1e-9))
        order = np.argsort(-score[p])
        if focus is not None:
            order = [focus[1]] + [kk for kk in order if kk != focus[1]]
        cats, cum = [], 0.0
        for k in order:
            cats.append(int(k))
            cum += score[p, k]
            if cum >= 0.8 * score[p].sum():
                break
        k = cats[0]
        seg_txt = ", ".join(f"{cfg.CATEGORIES[kk]} ({score[p, kk] / max(score[p].sum(), 1e-9):.0%})" for kk in cats)
        new_txt = ""
        if "new_accounts" in alerting:
            new_txt = f"; new accounts on {cfg.PLATFORMS[p]} z={tw.z_of(gp['new_accounts'], c, p):+.1f}"
        trail.append({"t": t, "level": "L2", "action": "Break the deviation down by platform × advertiser category",
                      "finding": f"{cfg.PLATFORMS[p]} carries {p_share:.0%} of the money deviation; segments covering "
                                 f"80%: {seg_txt}{new_txt}"})

        # --- L2: walk the DAG upstream
        mz = {m: tw.z_of(gp[m], c, p, None if m in ("new_accounts", "active_users") else k) for m in MECHANISMS}
        marg = self._marginal_z(c, p, k, ts)
        root = next((m for m in MECHANISMS if abs(mz[m]) > ROOT_Z), None)
        broken = [m for m in MECHANISMS if m != root and abs(mz[m]) > ROOT_Z]
        symptoms = [n for n in marg if n != root and n not in broken and abs(marg[n]) > ROOT_Z]
        kpi_alert = alerting[0]
        walk = (f"root: {MECH_LABEL[root]} (z={mz[root]:+.1f} given parents)" if root
                else "no mechanism deviates given its parents")
        if broken:
            walk += "; also broken: " + ", ".join(f"{MECH_LABEL[m]} ({mz[m]:+.1f})" for m in broken)
        if symptoms:
            walk += "; symptoms (normal given parents): " + ", ".join(symptoms)
        self.opened_segments.add((int(c), int(p), int(k)))
        trail.append({"t": t, "level": "L2",
                      "action": f"Walk the causal graph upstream from {KPI_LABEL[kpi_alert]} in "
                                f"{cfg.PLATFORMS[p]} × {cfg.CATEGORIES[k]}",
                      "finding": walk})
        graph = self._incident_graph(mz, marg, root, broken, symptoms)

        # --- L3 (adaptive abstraction)
        l3 = None
        cluster = False
        est_total = self._excess_accounts(c, p, t)        # fake accounts in total, from excess sign-ups
        if root in ("new_accounts", "active_users") and impact >= L3_MIN_IMPACT:
            users = self.view.sample_users(c, p, 600)
            sc = self.taste.score_users(users)
            self.l3_runs += 1
            self.users_checked += int(sc["n_users"])
            cf, co = sc["coherence_flagged"], sc["coherence_others"]
            cluster = (sc["n_flagged"] >= 5 and sc["device_concentration"] >= 0.6 and cf is not None
                       and cf < co - 0.2)
            devs = [d for d, _ in sc["top_devices"]]
            l3 = {"summary": {"n_users": sc["n_users"], "n_flagged": sc["n_flagged"], "estimated_total": est_total,
                              "coherence_flagged": None if cf is None else round(cf, 2),
                              "coherence_others": round(co, 2), "top_devices": devs,
                              "payout_focus_flagged": None if sc["payout_focus_flagged"] is None
                              else round(sc["payout_focus_flagged"], 2),
                              "payout_focus_others": round(sc["payout_focus_others"], 2)},
                  "points": [{"coherence": pt["coherence"], "payout_focus": pt["payout_focus"],
                              "flagged": pt["flagged"], "device": pt["device"], "account_age_days": pt["age"]}
                             for pt in sc["points"] if pt["coherence"] is not None]}
            l3["links"], l3["groups"] = self._device_links(l3["points"])
            if cluster:
                finding = (f"Fake-account cluster ({fake_count_phrase(sc['n_flagged'], sc['n_users'], est_total)}): "
                           f"taste coherence {cf:.2f} vs {co:.2f} for everyone else, payout focus "
                           f"{sc['payout_focus_flagged']:.2f} vs {sc['payout_focus_others']:.2f}, shared devices "
                           f"{', '.join(devs)}")
            else:
                finding = (f"No coherent fraud cluster ({sc['n_flagged']} flagged); new users have normal taste "
                           f"coherence ({co:.2f})")
            trail.append({"t": t, "level": "L3",
                          "action": f"Impact {_fmt_usd(impact)}/h × high uncertainty (user-side root): pull "
                                    f"{sc['n_users']} unlabeled user records from {country.name} {cfg.PLATFORMS[p]} "
                                    f"and score them with the taste model",
                          "finding": finding})
        else:
            why = (f"root is {MECH_LABEL[root].lower()}, a mechanism with no user-level ambiguity" if root
                   and root not in ("new_accounts", "active_users") else f"impact {_fmt_usd(impact)}/h is low")
            trail.append({"t": t, "level": "L3", "action": "Decide whether to descend to individual users",
                          "finding": f"Skipped (impact × uncertainty low): {why}"})

        # --- hypotheses (counterfactuals in the twin)
        kpi = max(alerting, key=lambda kk: abs(z[KPIS.index(kk)]))         # explain the change that alerted
        on_p = None
        if focus is not None:               # a second cause in the same country: explain it on its own platform
            kpi = {"users": kpi if kpi == "new_accounts" else "margin", "revenue": "revenue",
                   "rewards": "rewards"}.get(focus[2], "margin")
            on_p = p
        ki = KPIS.index(kpi)
        delta = float(dl[ki])
        e_user = tw.explained_share(c, ts, {p: USER_SIDE}, kpi, on_p)
        e_track = tw.explained_share(c, ts, {pp: ["revenue"] for pp in range(cfg.P)}, kpi, on_p)
        e_rew = tw.explained_share(c, ts, {pp: ["rewards"] for pp in range(cfg.P)}, kpi, on_p)
        fc_cal = sum(tw._l1(tw.forecast(x))[c, ki] for x in ts)
        fc_nocal = sum(tw._l1(tw.forecast(x, calendar=False))[c, ki] for x in ts)
        obs_k = float(res["obs"][c, ki])
        e_cal = float(np.clip((fc_cal - fc_nocal) / (obs_k - fc_nocal), 0, 1)) if abs(obs_k - fc_nocal) > 1e-9 else 0.0
        miss = sum((1 - tw.rows[x]["completeness"][c]) * tw._l1(tw.forecast(x))[c, ki] for x in ts)
        e_gap = float(np.clip(-miss / delta, 0, 1)) if abs(delta) > 1e-9 else 0.0

        def verdict(e: float, cap: str | None = None) -> str:
            v = "supported" if e >= SUPPORTED else "partial" if e >= PARTIAL else "rejected"
            return "partial" if cap == "partial" and v == "supported" else v

        plat = cfg.PLATFORMS[p]
        if l3 and cluster:
            user_name, kind = (f"Fake accounts on {plat} ("
                               f"{fake_count_phrase(l3['summary']['n_flagged'], l3['summary']['n_users'], est_total)}; "
                               f"devices {', '.join(l3['summary']['top_devices'][:2])})"), "fraud"
        elif l3:
            user_name, kind = f"Organic growth: new real users on {plat}", "organic"
        else:
            user_name, kind = f"More users or different user behaviour on {plat}", "user"
        hyps = [
            {"name": user_name, "explained": e_user,
             "verdict": verdict(e_user, None if kind in ("fraud", "organic") else "partial")},
            {"name": "Advertisers' tracking broke (they confirm fewer completed offers)", "explained": e_track,
             "verdict": verdict(e_track)},
            {"name": "Reward pricing change", "explained": e_rew, "verdict": verdict(e_rew)},
            {"name": "Planned marketing event (calendar)", "explained": e_cal, "verdict": verdict(e_cal)},
            {"name": "Late or missing data", "explained": e_gap, "verdict": verdict(e_gap)},
        ]
        # Which mechanisms each hypothesis switches off in a counterfactual replay (None: not a chain mechanism).
        cause_specs = {user_name: {p: list(USER_SIDE)},
                       hyps[1]["name"]: {pp: ["revenue"] for pp in range(cfg.P)},
                       hyps[2]["name"]: {pp: ["rewards"] for pp in range(cfg.P)}}
        for h in hyps:
            h["explained"] = round(float(np.clip(h["explained"], 0, 1)), 3) + 0.0
        hyps.sort(key=lambda h: -h["explained"])
        cause_spec = cause_specs.get(hyps[0]["name"]) if hyps[0]["verdict"] == "supported" else None
        if kind == "fraud" and hyps[0]["name"] == user_name and hyps[0]["verdict"] == "supported":
            pass
        elif e_track >= SUPPORTED:
            kind = "tracking"
        elif kind == "fraud":
            kind = "user"
        dtxt = _signed_usd(delta / WINDOW) if kpi in ("revenue", "rewards", "margin") else f"{delta / WINDOW:+,.1f}"
        trail.append({"t": t, "level": "L2", "action": f"Test hypotheses by counterfactual in the twin "
                                                      f"(Δ {KPI_LABEL[kpi]} {dtxt}/h)",
                      "finding": "; ".join(f"{h['name'].split(' (')[0]}: {h['explained']:.0%} {h['verdict']}"
                                           for h in hyps)})

        sh = tw.shapley(c, p, ts, "margin")
        contributions = [{"mechanism": m, "value_usd": round(v / WINDOW, 2)} for m, v in sh["phi"].items()]

        # --- fixes tested in the twin
        if kind == "fraud":
            opts, fcats = ["none", "block_cluster", "verify_new_accounts", "cap_rewards"], TOP_PAYOUT
        elif kind == "user":
            opts, fcats = ["none", "verify_new_accounts", "cap_rewards"], TOP_PAYOUT
        elif kind == "tracking":
            ratio = sum(r["revenue"][c, p] for r in rows) / np.maximum(sum(f["revenue"][c, p] for f in fcs), 1e-9)
            fcats = [int(kk) for kk in np.where(ratio < 0.7)[0]] or [k]
            opts = ["none", "notify_advertiser", "pause_offers", "pause_notify"]
        else:
            opts, fcats = ["none"], cats
        ctx = {"kind": "tracking" if kind == "tracking" else "fraud", "platform": p, "categories": fcats,
               "elapsed": self._elapsed(c)}
        inc = {"id": f"inc-{len(self.incidents) + 1}", "c": c, "p": p, "k": k, "kind": kind, "kpi": kpi,
               "fingerprint": [country.iso, plat, root], "status": "open", "country": country.iso,
               "platform": plat, "category": cfg.CATEGORIES[k], "categories": [cfg.CATEGORIES[x] for x in cats],
               "fix_categories": fcats, "detected_at": t, "impact_usd_h": round(impact, 1),
               "severity": "low", "risk_7d_usd": 0, "reaction_h": None,
               "trail": trail, "graph": graph, "contributions": contributions, "hypotheses": hyps, "l3": l3,
               "ctx": ctx, "applied": None, "applied_at": None, "due": None,
               "verification": {"status": "pending", "predicted": None, "actual": None},
               "z": {KPI_LABEL[kk]: round(float(z[i]), 1) for i, kk in enumerate(KPIS)},
               "mechanism_z": {m: round(v, 1) for m, v in mz.items()}, "root": root, "broken": broken,
               "symptoms": symptoms, "delta_usd_h": round(delta / WINDOW, 1),
               "delta_revenue_usd_h": round(float(dl[KPIS.index("revenue")]) / WINDOW, 1),
               "delta_margin_usd_h": round(float(dl[KPIS.index("margin")]) / WINDOW, 1),
               "excess_accounts": est_total, "cause_spec": cause_spec,
               "family": FAMILY.get(root, "unknown"),
               "alerts_merged": 0, "last_seen_at": t, "resolved_at": None,
               "recurrence_of": next((o["id"] for o in reversed(self.incidents)
                                      if o["fingerprint"] == [country.iso, plat, root]), None)}
        inc["fixes"] = self._fixes(inc, opts, t)
        inc["recommended"] = self._recommend(inc["fixes"], kind)
        rec_fix = next(f for f in inc["fixes"] if f["id"] == inc["recommended"])
        # Risk = what doing nothing costs over 7 days vs the recommended fix (p50), plus how soon advertisers react.
        inc["risk_7d_usd"] = round(max(rec_fix["delta_p50_usd"], 0))
        # Advertiser reaction time from the twin's simulated do-nothing future (median over Monte Carlo paths).
        cb = next((x["after_h"] for x in inc["fixes"][0]["events"]
                   if x["label"] == EVENT_LABEL["chargeback"]), None)
        inc["reaction_h"] = (cb if kind in ("fraud", "user")
                             else 0 if kind == "tracking" else None)          # tracking: losses accrue now
        inc["severity"] = self._severity(inc["risk_7d_usd"], inc["reaction_h"])
        rec = next(f for f in inc["fixes"] if f["id"] == inc["recommended"])
        trail.append({"t": t, "level": "L1", "action": f"Fast-forward {len(opts)} options 7 days in the twin "
                                                      f"(40 Monte Carlo runs each)",
                      "finding": f"Recommend {rec['name']}: 7-day margin {_signed_usd(rec['delta_p50_usd'])} vs "
                                 f"doing nothing (p50)" if rec["id"] != "none"
                                 else "Doing nothing is best; keep monitoring"})
        inc["left_zone_at"] = self.series(inc)["left_zone_at"]     # frozen at detection (series window moves on)
        inc["title"] = self._title(inc)
        inc["reports"] = self._reports(inc)
        self.incidents.append(inc)
        return inc

    @staticmethod
    def _device_links(points: list[dict], cap: int = 120) -> tuple[list[dict], list[dict]]:
        """Links between flagged accounts that share a device (indices into points), and flagged counts per device."""
        by_dev: dict[str, list[int]] = {}
        for i, pt in enumerate(points):
            if pt["flagged"]:
                by_dev.setdefault(pt["device"], []).append(i)
        groups = sorted(({"device": d, "count": len(ix)} for d, ix in by_dev.items()), key=lambda g: -g["count"])
        links: list[dict] = []
        for g in groups:
            ix = by_dev[g["device"]]
            pairs = [(ix[i], ix[i + 1]) for i in range(len(ix) - 1)] + [(ix[0], ix[i]) for i in range(2, len(ix))]
            for a, b in pairs:
                if len(links) >= cap:
                    return links, groups
                links.append({"a": a, "b": b})
        return links, groups

    def _marginal_z(self, c: int, p: int, k: int, ts: list[int]) -> dict:
        """z of each node vs the twin's marginal forecast in segment (c,p,k)."""
        tw = self.twin
        sig = {"new_accounts": tw.sig_new[c, p], "active_users": tw.sig_ret[c, p]}
        for m in ("impressions", "starts", "completions", "revenue", "rewards", "quality"):
            sig[m] = tw.sig[m][c, p, k]
        chain_sig = {}
        acc = 0.0
        for m in ("new_accounts", "active_users", "impressions", "starts", "completions"):
            acc += sig[m] ** 2
            chain_sig[m] = np.sqrt(acc)
        for m in ("revenue", "rewards", "quality"):
            chain_sig[m] = np.sqrt(acc + sig[m] ** 2)
        chain_sig["margin"] = chain_sig["revenue"]
        out = {}
        for n in list(chain_sig):
            o = e = v = 0.0
            for x in ts:
                r, f = tw.rows[x], tw.forecast(x)
                if n == "margin":
                    ov, ev = r["revenue"][c, p, k] - r["rewards"][c, p, k], f["revenue"][c, p, k] - f["rewards"][c, p, k]
                elif n in ("new_accounts", "active_users"):
                    ov, ev = r[n][c, p], f[n][c, p]
                else:
                    ov, ev = r[n][c, p, k], f[n][c, p, k]
                o, e, v = o + ov, e + ev, v + (ev * chain_sig[n]) ** 2
            out[n] = float((o - e) / np.sqrt(v + 1e-9))
        q_o = sum(tw.rows[x]["quality"][c, p, k] for x in ts) / max(sum(tw.rows[x]["completions"][c, p, k] for x in ts), 1e-9)
        q_e = sum(tw.forecast(x)["quality"][c, p, k] for x in ts) / max(sum(tw.forecast(x)["completions"][c, p, k] for x in ts), 1e-9)
        out["quality_per_completion"] = float((q_o / q_e - 1) / max(sig["quality"] / np.sqrt(len(ts)), 1e-3))
        return out

    def _incident_graph(self, mz: dict, marg: dict, root, broken, symptoms) -> dict:
        g = self.graph.to_dict()
        nodes = []
        for n in g["nodes"]:
            nid = n["id"]
            if nid in MECHANISMS:
                zz = mz[nid]
            else:
                zz = marg.get(nid, 0.0)
            state = ("root" if nid == root else "broken" if nid in broken else "symptom" if nid in symptoms
                     or (nid not in MECHANISMS and abs(zz) > ROOT_Z) else "normal")
            nodes.append({"id": nid, "label": n["label"], "z": round(float(zz), 1), "state": state})
        nodes.append({"id": "calendar", "label": "Planned events (marketing calendar)", "z": 0.0, "state": "normal"})
        edges = [{"source": e["source"], "target": e["target"], "type": e["type"]} for e in g["edges"]]
        return {"nodes": nodes, "edges": edges}

    # ================================================================== fixes
    def _fixes(self, inc: dict, opts: list[str], now: int) -> list[dict]:
        ff = self.twin.fast_forward(inc["c"], opts, inc["ctx"], now, seed=self.seed)
        country = cfg.COUNTRIES[inc["c"]].name
        cats = ", ".join(cfg.CATEGORY_KEYS[k] for k in inc["fix_categories"])
        e = inc["ctx"]["elapsed"]
        times = {r["id"]: {x["event"]: x["after_h"] for x in r["events"]} for r in ff}
        out = []
        for r in ff:
            name, desc = FIX_INFO[r["id"]]
            out.append({"id": r["id"], "name": name,
                        "description": desc.format(country=country, platform=inc["platform"], categories=cats),
                        "days": r["days"], "p10": [round(x) for x in r["p10"]], "p50": [round(x) for x in r["p50"]],
                        "p90": [round(x) for x in r["p90"]], "delta_p50_usd": round(r["delta_p50_usd"]),
                        "delta_daily": {q: [round(x) for x in v] for q, v in r["delta_daily"].items()},
                        "events": [{"after_h": x["after_h"], "label": EVENT_LABEL[x["event"]],
                                    "probability": x["probability"]} for x in r["events"]],
                        "advertiser_risk": self._risk(inc, r["id"], times.get(r["id"], {}), cats)})
        return out

    @staticmethod
    def _severity(risk_7d: float, reaction_h: int | None) -> str:
        soon = reaction_h is not None and reaction_h <= 24
        if risk_7d >= SEV_HIGH_USD or (soon and risk_7d >= SEV_HIGH_SOON_USD):
            return "high"
        if risk_7d >= SEV_MEDIUM_USD or (soon and risk_7d >= SEV_MEDIUM_SOON_USD):
            return "medium"
        return "low"

    @staticmethod
    def _risk(inc: dict, fix: str, times: dict, cats: str) -> str:
        if inc["kind"] in ("fraud", "user"):
            cb, bc = times.get("chargeback"), times.get("budget_cut")
            react = (f"chargebacks on {cats} (about half of revenue) in ~{cb} h" if cb is not None else
                     "no advertiser reaction expected within 7 days")
            if bc is not None:
                react += f", budget cut (−60%) in ~{bc} h"
            return {
                "none": f"High: {react}" if cb is not None else f"Medium: {react}",
                "block_cluster": "Low: 30% chance the farm returns at half strength within 1–2 days",
                "verify_new_accounts": "Low: about 12% fewer genuine sign-ups on this platform",
                "cap_rewards": "Medium: leaves ~40% of the fake accounts active; the margin gain comes from paying "
                               "genuine users less (about 15% fewer starts), so it treats a symptom, not the cause"
                               + (f"; advertisers still react in ~{cb} h" if cb is not None else ""),
            }[fix]
        if inc["kind"] == "tracking":
            return {
                "none": f"High: users keep earning full rewards on {cats} while advertisers confirm only a fraction",
                "notify_advertiser": "Medium: losses continue until the advertiser fixes tracking (12–30 h)",
                "pause_offers": "Medium: offers return after 18 h with tracking still broken",
                "pause_notify": "Low: offers paused until tracking is fixed",
            }[fix]
        return "Low"

    @staticmethod
    def _recommend(fixes: list[dict], kind: str = "") -> str:
        """Best p50 among options with acceptable p10; a fraud fix must also remove the fake accounts."""
        none = fixes[0]
        ok = [f for f in fixes if f["p10"][-1] >= none["p10"][-1]]
        if kind in ("fraud", "user"):
            removes = [f for f in fixes if RESIDUAL_FRAUD.get(f["id"], 0.0) <= 0.2]
            ok = [f for f in ok if f in removes] or removes or ok
        best = max(ok, key=lambda f: f["p50"][-1])
        return best["id"]

    def _interventions(self, inc: dict, fix: str, t: int) -> list[dict]:
        c, p = inc["c"], cfg.PLATFORMS.index(inc["platform"])
        cats = inc["fix_categories"]
        base = {"country": c, "start": t}
        ivs = {
            "none": [],
            "block_cluster": [{"type": "block_cluster", "platform": p}],
            "verify_new_accounts": [{"type": "verify_new_accounts", "platform": p}],
            "cap_rewards": [{"type": "cap_rewards", "platform": p, "categories": list(TOP_PAYOUT)}],
            "notify_advertiser": [{"type": "notify_advertiser"}],
            "pause_offers": [{"type": "pause_offers", "platform": p, "categories": cats, "until": t + 18}],
            "pause_notify": [{"type": "pause_offers", "platform": p, "categories": cats, "until": t + 18},
                             {"type": "notify_advertiser"}],
        }[fix]
        return [{**base, **iv} for iv in ivs]

    def approve(self, inc_id: str, fix_id: str) -> dict:
        inc = self.get_incident(inc_id)
        if inc is None:
            raise KeyError(inc_id)
        if fix_id not in [f["id"] for f in inc["fixes"]]:
            raise ValueError(f"unknown fix '{fix_id}' for {inc_id}")
        if inc["applied"] is not None:
            raise ValueError(f"{inc_id} already has an applied fix ({inc['applied']})")
        now = self.view.t - 1
        # Fresh fast-forward from the moment of approval: this is the prediction we will be held to.
        opts = ["none"] + ([fix_id] if fix_id != "none" else [])
        inc["ctx"]["elapsed"] = inc["ctx"]["elapsed"] + (now - inc["detected_at"])
        pred = self.twin.fast_forward(inc["c"], opts, inc["ctx"], now, seed=self.seed)[-1]
        for iv in self._interventions(inc, fix_id, self.view.t):
            self.view.apply(dict(iv))
            self.twin.add_action(iv)
        inc["applied"], inc["applied_at"], inc["due"] = fix_id, now, now + 24
        inc["status"] = "fix_applied"
        inc["verification"] = {"status": "pending",
                               "predicted": [round(pred["p10"][0]), round(pred["p50"][0]), round(pred["p90"][0])],
                               "actual": None}
        name = FIX_INFO[fix_id][0]
        inc["trail"].append({"t": now, "level": "L1", "action": f"Human approved: {name}",
                             "finding": f"Applied to the marketplace; predicted 24 h margin in "
                                        f"{cfg.COUNTRIES[inc['c']].name} {_fmt_usd(pred['p10'][0])}–"
                                        f"{_fmt_usd(pred['p90'][0])} (p50 {_fmt_usd(pred['p50'][0])})"})
        return inc

    def _verify(self, inc: dict, t: int) -> None:
        c = inc["c"]
        actual = float(sum((self.twin.rows[x]["revenue"][c] - self.twin.rows[x]["rewards"][c]).sum()
                           for x in range(inc["applied_at"] + 1, inc["applied_at"] + 25)))
        lo, mid, hi = inc["verification"]["predicted"]
        ok = lo <= actual <= hi
        inc["verification"] = {"status": "verified" if ok else "missed", "predicted": [lo, mid, hi],
                               "actual": round(actual)}
        inc["verified_at"] = t
        if ok:
            inc["status"] = "verified"
            finding = f"Prediction verified: actual {_fmt_usd(actual)} within band {_fmt_usd(lo)}–{_fmt_usd(hi)}"
        else:
            self.twin.l1_sigma[c] *= 1.2
            finding = (f"Prediction missed: actual {_fmt_usd(actual)} outside {_fmt_usd(lo)}–{_fmt_usd(hi)}; "
                       f"twin uncertainty for {cfg.COUNTRIES[c].name} recalibrated (+20%)")
        inc["trail"].append({"t": t, "level": "L1", "action": "Compare 24 h actual margin with the prediction",
                             "finding": finding})

    # ================================================================== reports
    def _title(self, inc: dict) -> str:
        name = _country_phrase(cfg.COUNTRIES[inc["c"]].name)
        return {
            "fraud": f"Fake accounts on {inc['platform']} in {name}",
            "tracking": f"Advertisers stopped confirming {' and '.join(OFFER_NOUN[k] for k in inc['fix_categories'][:2])} "
                        f"offers on {inc['platform']} in {name}",
            "organic": f"Many new real users on {inc['platform']} in {name}",
        }.get(inc["kind"], f"Unusual {KPI_LABEL[inc['kpi']]} in {name}")

    # ------------------------------------------------------------------ "Watch Argus think" replay
    WALK = ["margin", "revenue", "rewards", "completions", "starts", "impressions", "active_users", "new_accounts"]
    PLAIN = {"margin": "margin", "revenue": "revenue", "rewards": "rewards", "completions": "completed offers",
             "starts": "offer starts", "impressions": "offers shown", "active_users": "active users",
             "new_accounts": "new sign-ups", "quality_per_completion": "valuable users per completed offer"}

    def replay(self, inc: dict) -> dict:
        """The story of how Argus found this issue, step by step, in plain words (all from the incident's data)."""
        c, t = inc["c"], inc["detected_at"]
        tw, cname = self.twin, _country_phrase(cfg.COUNTRIES[c].name)
        # L1: the country KPI that alerted (the one with the largest |z| at detection), last 24 h with the usual band
        z = inc["z"]
        lab_to_kpi = {KPI_LABEL[k]: k for k in KPIS}
        kpi = lab_to_kpi[max(z, key=lambda k: abs(z[k]))]
        ki = KPIS.index(kpi)
        pts = []
        for x in range(max(min(tw.rows), t - 23), t + 1):
            g = ((x + cfg.COUNTRIES[c].tz) % 24) // (24 // 4)
            sig = float(tw.l1_sigma[c, g, ki]) * np.sqrt(WINDOW)
            act = float(tw._kpi_values(tw._l1(tw.rows[x]))[c, ki])
            exp = float(tw._kpi_values(tw._l1(tw.forecast(x)))[c, ki])
            pts.append({"t": x, "label": hour_label(x), "actual": round(act, 3), "expected": round(exp, 3),
                        "lo": round(exp * (1 - 2 * sig), 3), "hi": round(exp * (1 + 2 * sig), 3)})
        out_ts = {pt["t"] for pt in pts if not pt["lo"] <= pt["actual"] <= pt["hi"]}
        left = None
        if out_ts:
            left = max(out_ts)
            while left - 1 in out_ts:
                left -= 1
        name = self.PLAIN[kpi]
        verb = "left their" if name.endswith("s") else "left its"
        if left is not None:
            l1_sentence = f"{name[0].upper() + name[1:]} in {cname} {verb} usual range at {hour_label(left).split()[1]}."
        else:
            l1_sentence = (f"{name[0].upper() + name[1:]} in {cname} stayed off its usual level for "
                           f"{WINDOW} hours, found at {hour_label(t).split()[1]}.")
        l1 = {"sentence": l1_sentence, "metric": f"{name[0].upper() + name[1:]} per hour, {cname}"
              if kpi != "quality_per_completion" else f"{name[0].upper() + name[1:]}, {cname}", "kpi": kpi,
              "series": pts, "left_at": None if left is None else {"t": left, "label": hour_label(left)}}
        # L2: which platform × category, at detection time
        segs, _ = self._segment_cells(c, t)
        zkey = "revenue" if inc["family"] == "revenue" else "rewards" if inc["family"] == "rewards" else "completions"
        cells = [{"platform": sg["platform"], "category": sg["category"], "z": sg["kpi_z"][zkey],
                  "driving": sg["platform"] == inc["platform"] and sg["category"] in inc["categories"]} for sg in segs]
        where = f"{cname}, {inc['platform']}"
        if inc["kind"] == "tracking":
            where += ", " + " and ".join(OFFER_NOUN[k] for k in inc["fix_categories"][:2]) + " offers"
        l2 = {"sentence": f"It's {where}.", "cells": cells, "measure": zkey}
        # walk: from margin backwards until the cause
        affected = set(inc["symptoms"]) | set(inc["broken"])
        steps = []
        for node in self.WALK:
            v = "cause" if node == inc["root"] else "affected" if node in affected else "fine"
            steps.append({"node": node, "label": self.PLAIN[node], "verdict": v})
            if v == "cause":
                break
        groups: list[list] = []
        for st in steps:
            if groups and groups[-1][0] == st["verdict"] and st["verdict"] != "cause":
                groups[-1][1].append(st["label"])
            else:
                groups.append([st["verdict"], [st["label"]]])
        def names(xs: list[str]) -> str:
            return xs[0] if len(xs) == 1 else ", ".join(xs[:-1]) + " and " + xs[-1]
        parts = [f"{names(g)}: the cause" if v == "cause" else f"{names(g)} moved" if v == "affected"
                 else f"{names(g)} fine" for v, g in groups]
        if inc["root"] is None:
            parts.append("no single cause found")
        walk_sentence = " → ".join(parts)
        walk = {"sentence": walk_sentence[0].upper() + walk_sentence[1:] + ".", "steps": steps}
        # L3: individual users
        if inc["l3"]:
            sm = inc["l3"]["summary"]
            flagged = [p_["device"] for p_ in inc["l3"]["points"] if p_["flagged"]]
            counts = sorted({d: flagged.count(d) for d in flagged}.values(), reverse=True)
            n_dev, cum = 0, 0
            for k_ in counts:
                n_dev, cum = n_dev + 1, cum + k_
                if cum >= 0.8 * max(len(flagged), 1):
                    break
            if inc["kind"] == "fraud":
                l3_sentence = (f"{sm['n_flagged']} of {sm['n_users']} users checked look fake: most share {n_dev} "
                               f"devices, and their interests don't fit what they do.")
            else:
                l3_sentence = (f"Checked {sm['n_users']} users: the new ones have interests that fit what they do, "
                               f"so they look real.")
        else:
            l3_sentence = "Users behave as usual, so no need to check single accounts."
        # proof: counterfactual in the twin
        top = inc["hypotheses"][0]
        lead = {"fraud": "Without the fake accounts", "tracking": "If advertisers confirmed offers as usual",
                "organic": "Without the new users"}.get(inc["kind"], "Without this cause")
        proof = {"sentence": f"{lead}, {top['explained']:.0%} of the change disappears.",
                 "explained": top["explained"]}
        return {"l1": l1, "l2": l2, "walk": walk, "l3": {"sentence": l3_sentence, "ran": bool(inc["l3"])},
                "proof": proof}

    @staticmethod
    def short_title(inc: dict) -> tuple[str, str | None]:
        """Card title (~6 words) and the rest of the full title as a detail line (None when nothing is left)."""
        c = cfg.COUNTRIES[inc["c"]]
        where = SHORT_COUNTRY.get(c.iso, c.name)
        plat = inc["platform"]
        if inc["kind"] == "fraud":
            return f"Fake accounts on {plat} in {where}", None
        if inc["kind"] == "tracking":
            cats = " and ".join(OFFER_NOUN[k] for k in inc["fix_categories"][:2])
            return f"Advertisers stopped confirming offers in {where}", f"{cats[0].upper() + cats[1:]} offers on {plat}"
        if inc["kind"] == "organic":
            return f"Many new real users in {where}", f"On {plat}"
        return f"Unusual {KPI_LABEL[inc['kpi']]} in {where}", None

    def _reports(self, inc: dict) -> dict:
        name = cfg.COUNTRIES[inc["c"]].name
        top = inc["hypotheses"][0]
        rejected = [h["name"].split(" (")[0].lower() for h in inc["hypotheses"] if h["verdict"] == "rejected"]
        rec = next(f for f in inc["fixes"] if f["id"] == inc["recommended"])
        none = inc["fixes"][0]
        cats = ", ".join(cfg.CATEGORIES[k] for k in inc["fix_categories"])
        headline = {
            "fraud": "fake accounts are inflating offer completions. It looks like growth, but advertisers will "
                     "claw the revenue back",
            "tracking": f"advertisers confirm only a fraction of {cats} completions on {inc['platform']}, while we "
                        f"keep paying users",
            "organic": "a wave of genuine new users",
        }.get(inc["kind"], f"{KPI_LABEL[inc['kpi']]} moved away from what we expected")
        ceo = (f"{name}: {headline}. Impact about {_fmt_usd(inc['impact_usd_h'])}/h. Cause: {top['name']} "
               f"({top['explained']:.0%} of the change explained in the twin)"
               + (f"; ruled out: {', '.join(rejected)}" if rejected else "") + ". "
               + (f"Recommendation: {rec['name'].lower()}, worth {_signed_usd(rec['delta_p50_usd'])} margin over 7 "
                  f"days vs doing nothing. Risk of doing nothing: {none['advertiser_risk'].split(': ', 1)[-1]}."
                  if rec["id"] != "none" else "Recommendation: no action, keep monitoring."))
        mech = ", ".join(f"{MECH_LABEL[m].lower()} ({inc['mechanism_z'][m]:+.1f} times the usual swing)"
                         for m in ([inc["root"]] if inc["root"] else []) + inc["broken"])
        follow = {
            "fraud": f"Tell the {cats} advertisers before their fraud checks do; review {inc['platform']} onboarding "
                     f"in {name}.",
            "tracking": f"Open a ticket with the {cats} advertisers' tracking team with the postback gap.",
        }.get(inc["kind"], "Keep watching this market.")
        manager = (f"Where: {name} · {inc['platform']} · {', '.join(inc['categories'])}. What broke: "
                   f"{mech or 'nothing specific'}. Knock-on effects: {', '.join(inc['symptoms']) or 'none'}. Action: {rec['name']}. "
                   f"{rec['description']} Expected 7-day margin {_signed_usd(rec['delta_p50_usd'])} vs doing nothing "
                   f"(p10 {_fmt_usd(rec['p10'][-1])}, p90 {_fmt_usd(rec['p90'][-1])}). Then: {follow}")
        shap = ", ".join(f"{x['mechanism']} {_signed_usd(x['value_usd'])}/h" for x in
                         sorted(inc["contributions"], key=lambda x: -abs(x["value_usd"]))[:4])
        l3 = ""
        if inc["l3"]:
            s = inc["l3"]["summary"]
            l3 = (f" L3 behavioural-consistency check (taste coherence): "
                  f"{fake_count_phrase(s['n_flagged'], s['n_users'], inc['excess_accounts'])}; coherence "
                  f"{s['coherence_flagged']} vs {s['coherence_others']}, devices {', '.join(s['top_devices'])}.")
        top_h = inc["hypotheses"][0]
        cf = (f" Counterfactual replay (do(cause mechanisms := twin baseline), other residuals kept): "
              f"{top_h['name'].split(' (')[0]} explains {top_h['explained']:.1%} of Δ{inc['kpi']} "
              f"({top_h['verdict']}; threshold {SUPPORTED:.0%}).")
        engineer = (f"Window t={inc['detected_at'] - WINDOW + 1}..{inc['detected_at']} (3 h). L1 z vs twin forecast: "
                    + ", ".join(f"{k} {v:+.1f}" for k, v in inc["z"].items())
                    + f". Causal root-cause analysis on the SCM, residual z given parents ({inc['platform']}, "
                      f"{inc['category']}): "
                    + ", ".join(f"{m} {v:+.1f}" for m, v in inc["mechanism_z"].items())
                    + f". Root: {inc['root']}. Exact Shapley attribution of Δmargin (2^8 coalitions): {shap}.{l3}{cf} "
                      f"Fixes: Monte Carlo rollouts, 40 paths × 7 days, p10–p90 prediction intervals.")
        return {"ceo": ceo, "manager": manager, "engineer": engineer, "source": "template"}

    def reports_for(self, inc: dict) -> dict:
        """Reports for the incident detail: LLM-written when configured (cached per incident state), else templates."""
        if not llm.configured():
            return inc["reports"]
        key = (inc["status"], inc["applied"], inc["verification"]["status"])
        cache = inc.setdefault("_llm_reports", {})
        if key not in cache:
            cache[key] = self._llm_reports(inc) or inc["reports"]
        return cache[key]

    def _llm_reports(self, inc: dict) -> dict | None:
        rec = next(f for f in inc["fixes"] if f["id"] == inc["recommended"])
        facts = {
            "incident": inc["title"], "status": inc["status"], "country": cfg.COUNTRIES[inc["c"]].name,
            "platform": inc["platform"], "segments": inc["categories"], "impact_usd_per_hour": inc["impact_usd_h"],
            "root_mechanism": inc["root"], "broken": inc["broken"], "symptoms": inc["symptoms"],
            "mechanism_z": inc["mechanism_z"], "l1_z": inc["z"],
            "hypotheses": [{k: h[k] for k in ("name", "explained", "verdict")} for h in inc["hypotheses"]],
            "user_level_check": inc["l3"]["summary"] if inc["l3"] else None,
            "fake_accounts": (fake_count_phrase(inc["l3"]["summary"]["n_flagged"], inc["l3"]["summary"]["n_users"],
                                                inc["excess_accounts"]) if inc["kind"] == "fraud" and inc["l3"] else None),
            "fixes": [{"name": f["name"], "description": f["description"],
                       "margin_7d_vs_doing_nothing_usd": f["delta_p50_usd"], "risk": f["advertiser_risk"]}
                      for f in inc["fixes"]],
            "recommended": rec["name"], "applied": FIX_INFO[inc["applied"]][0] if inc["applied"] else None,
            "verification": inc["verification"],
        }
        drafts = {k: inc["reports"][k] for k in ("ceo", "manager", "engineer")}
        out = llm.chat_json(
            "You are Argus, an analyst for a rewarded-ad marketplace (advertisers pay per completed offer, users get a "
            "reward share). Write one incident report per audience from the facts. Use only numbers that appear in "
            "the facts; never invent figures. Keys: ceo (max 3 sentences: business impact in money, cause, the "
            "decision needed), manager (max 4 sentences: segment, campaigns/advertisers affected, the action and "
            "follow-up), engineer (max 5 sentences: mechanisms and z-scores, root vs symptoms, counterfactual "
            "results, what was queried). 'root_mechanism' is the cause; 'broken' are other mechanisms that are "
            "themselves abnormal; only 'symptoms' are symptoms. If 'applied' is set the decision has been taken: report "
            "it and the verification status instead of asking for approval. When you mention how many fake accounts "
            "there are, use exactly the 'fake_accounts' wording. Plain text, no markdown.",
            json.dumps({"facts": facts, "template_drafts": drafts}, default=str), max_tokens=1200)
        if not out or not all(isinstance(out.get(k), str) and 20 <= len(out[k]) <= 2000 for k in drafts):
            return None
        source_text = json.dumps({"facts": facts, "drafts": drafts}, default=str)
        unmatched = sorted({x for k in drafts for x in ungrounded_numbers(out[k], source_text)})
        if unmatched:                                  # grounded generation: any number not in the tool results
            self.llm_rejections.append({"incident": inc["id"], "unmatched": unmatched[:10]})
            return None                                # -> deterministic template
        return {**{k: out[k].strip() for k in drafts}, "source": "llm", "model": llm.model(),
                "grounding": "every number checked against the tool results"}

    # ================================================================== metric extension
    def _known(self) -> set[str]:
        return set(MECHANISMS) | {"margin", "quality_per_completion"}

    def _propose(self, name: str, description: str) -> tuple[str | None, str]:
        known = sorted(self._known())
        if llm.configured():
            out = llm.chat_json(
                "Map a business metric request to a formula over known hourly metrics: " + ", ".join(known)
                + ". Use only + - * / ( ) and these names.", f"name: {name}\ndescription: {description}\n"
                                                             'Return {"expr": "..."}.')
            if out and isinstance(out.get("expr"), str):
                try:
                    parse_expression(out["expr"], self._known())
                    return out["expr"], "LLM"
                except (ValueError, SyntaxError):
                    pass
        return keyword_formula(name, description, self._known())

    def add_metric(self, name: str, description: str = "", formula: str | None = None) -> dict:
        """Automatic extension: resolve, validate (names, units, data, aggregation), place, monitor, explain."""
        from . import extension as ext

        checks: list[dict] = []

        def result(status: str, message: str, **extra) -> dict:
            ok = status == "added"
            return {"status": status, "message": message, "node": extra.pop("node", None),
                    "validation": {"ok": ok, "checks": checks}, **extra}

        label, text = ext.split_name_formula(name, description, formula)
        nid = re.sub(r"[^a-z0-9]+", "_", (label or "metric").lower()).strip("_")[:48] or "metric"
        if not re.match(r"^[a-z]", nid):
            nid = "m_" + nid
        known = self._known()
        if text is not None:
            res = ext.resolve_formula(text, known)
            if res["ambiguous"]:
                word, cands = next(iter(res["ambiguous"].items()))
                options = [re.sub(rf"(?<![a-z_]){re.escape(word)}(?![a-z_])", c, res["expr"]) for c in cands]
                names = " or ".join(self.graph.nodes[c].label.lower() for c in cands)
                msg = f"'{word}' is ambiguous: it could mean {names}. Pick one and add the metric again."
                checks.append({"name": "names", "ok": False, "detail": msg})
                return result("ambiguous", msg, ambiguity={"term": word, "candidates": cands,
                                                           "suggested_formulas": options})
            if res["unknown"]:
                word, sugg = next(iter(res["unknown"].items()))
                msg = (f"Unknown metric '{word}'." + (f" Did you mean {', '.join(sugg)}?" if sugg else "")
                       + " Known metrics: " + ", ".join(sorted(known)) + ".")
                checks.append({"name": "names", "ok": False, "detail": msg})
                return result("invalid", msg)
            expr, source = res["expr"], "your formula"
            mapped = res["mapped"]
        else:
            expr, source = self._propose(label, description)
            mapped = []
            if expr is None:
                msg = ("Could not work out a formula from the description. Write it as "
                       "'name = formula', e.g. margin_per_signup = margin / signups.")
                checks.append({"name": "formula", "ok": False, "detail": msg})
                return result("invalid", msg)
        checks.append({"name": "formula", "ok": True,
                       "detail": f"{nid} = {expr} (from {source})"
                                 + ("; read " + ", ".join(f"'{a}' as {b}" for a, b in mapped) if mapped else "")})
        try:
            names, agg = parse_expression(expr, known)
        except (ValueError, SyntaxError) as e:
            msg = f"The formula is not valid: {e}."
            checks.append({"name": "names", "ok": False, "detail": msg})
            return result("invalid", msg)
        checks.append({"name": "names", "ok": True, "detail": "All inputs are known metrics: " + ", ".join(names)})
        try:
            unit = ext.unit_text(ext.dimension(expr))
            checks.append({"name": "dimensions", "ok": True, "detail": f"Units are consistent: result in {unit}"})
        except ext.DimensionError as e:
            msg = f"The units don't match: {e}."
            checks.append({"name": "dimensions", "ok": False, "detail": msg})
            return result("invalid", msg)
        same = [n for n in self.graph.nodes.values()
                if n.expr and n.expr.replace(" ", "") == expr.replace(" ", "")] + \
               [self.graph.nodes[n] for n in names if n == expr.strip()]
        if same or nid in self.graph.nodes:
            other = same[0].id if same else nid
            msg = (f"Already in the graph as '{other}'" + (f" ({self.graph.nodes[other].expr})"
                                                            if self.graph.nodes[other].expr else "")
                   + ": not added twice.")
            checks.append({"name": "duplicate", "ok": False, "detail": msg})
            return result("duplicate", msg, node=self.graph.nodes[other].to_dict())
        # Same values as an existing metric on the data (e.g. rewards * 1)? Then it is not a new metric.
        recent = [self.twin.rows[x] for x in sorted(self.twin.rows)[-24:]]
        with np.errstate(divide="ignore", invalid="ignore"):
            def series_of(e: str) -> np.ndarray:
                return np.array([np.asarray(evaluate(e, _with_formulas(_country_sums(r))), dtype=float)
                                 * np.ones(cfg.C) for r in recent])
            cand = series_of(expr)
            for other in list(MECHANISMS) + [n.id for n in self.graph.nodes.values() if n.expr]:
                oexpr = other if other in MECHANISMS else self.graph.nodes[other].expr
                ov = series_of(oexpr)
                if np.allclose(cand, ov, rtol=1e-9, atol=1e-9, equal_nan=True):
                    msg = (f"On the data this is identical to the existing metric '{other}'"
                           + (f" ({oexpr})" if oexpr != other else "") + ": not added twice.")
                    checks.append({"name": "duplicate", "ok": False, "detail": msg})
                    return result("duplicate", msg, node=self.graph.nodes[other].to_dict())
        checks.append({"name": "duplicate", "ok": True,
                       "detail": "New metric: no existing metric has this formula or the same values on the data"})

        # Does the formula hold on the data? Evaluate on 14 days of observed history at every breakdown level.
        place = ext.placement(names)
        with_k = "offer category" in place["breakdown"]
        hist = [self.twin.rows[x] for x in sorted(self.twin.rows)][-24 * 14:]

        def seg_vals(r: dict) -> dict:
            v = {}
            for k in MECHANISMS:
                a = r[k]
                v[k] = (a if with_k else a.sum(2)) if a.ndim == 3 else (a[..., None] if with_k else a)
            return _with_formulas(v)

        with np.errstate(divide="ignore", invalid="ignore"):
            seg = np.array([np.asarray(evaluate(expr, seg_vals(r)), dtype=float) for r in hist])
            sums = {k: sum(seg_vals(r)[k] for r in hist[-24:]) for k in MECHANISMS}
            sums = _with_formulas(sums)
            seg_day = np.asarray(evaluate(expr, sums), dtype=float)
            day = _with_formulas({k: sum(_country_sums(r)[k] for r in hist[-24:]) for k in _country_sums(hist[-1])})
            country_val = np.asarray(evaluate(expr, day), dtype=float) * np.ones(cfg.C)
        defined = float(np.isfinite(seg).mean())
        finite = bool(np.isfinite(country_val).all())
        checks.append({"name": "holds_on_data", "ok": finite and defined >= 0.5,
                       "detail": (f"Computed on {len(hist)} h of observed data at {' × '.join(place['breakdown'][1:])} "
                                  f"level: {defined:.0%} of segment-hours defined; "
                                  f"{cfg.COUNTRIES[cfg.ISO_INDEX['CA']].name} last 24 h = "
                                  f"{country_val[cfg.ISO_INDEX['CA']]:,.4g} {unit}")
                       if finite else "The formula is undefined on the observed data (e.g. division by zero)"})
        if agg == "ratio":
            seg_mean = float(np.nanmean(seg_day))
            glob = float(np.nansum([evaluate(expr, {k: np.nansum(v) for k, v in sums.items()})]))
            checks.append({"name": "aggregation", "ok": True,
                           "detail": f"Ratio: not additive. Country and global values are Σnumerator / Σdenominator "
                                     f"({glob:,.4g}), not the average of segments ({seg_mean:,.4g})"})
        else:
            seg_total = np.nansum(seg_day.reshape(cfg.C, -1), axis=1)
            err = float(np.nanmax(np.abs(seg_total - country_val) / np.maximum(np.abs(country_val), 1e-9)))
            checks.append({"name": "aggregation", "ok": err < 1e-6,
                           "detail": f"Additive: segments sum to the country total (max error {err:.1e})"})
        if not all(ch["ok"] for ch in checks):
            msg = next(ch["detail"] for ch in checks if not ch["ok"])
            return result("invalid", msg)

        sigma = self._extension_sigma(expr)
        kinds = {n: self.graph.nodes[n].kind for n in names}
        kind = "ratio" if agg == "ratio" else ("money" if "money" in kinds.values() else "count")
        node = Node(nid, label or nid, unit, kind, "L2", names, "formula", expr=expr, aggregation=agg,
                    origin="extension", confidence={"your formula": 1.0, "LLM": 0.9}.get(source, 0.8),
                    description=description or f"{label} = {expr}")
        self.graph.add(node)
        self.extensions[nid] = {"expr": expr, "sigma": sigma, "added_at": self.t}
        z_now = self._extension_z(self.t)[:, -1]
        worst = int(np.argmax(np.abs(z_now)))
        checks.append({"name": "monitoring", "ok": True,
                       "detail": f"Watched in {cfg.C} countries against its seasonal baseline "
                                 f"(backtest error {float(sigma.mean()):.1%} per 3 h)"})
        parents_txt = " and ".join(self.graph.nodes[n].label.lower() for n in names)
        note = (f"Added because you asked for '{label}'. Argus read it as {expr}"
                + (f" ('{mapped[0][0]}' = {mapped[0][1]})" if mapped else "")
                + f", placed it under {parents_txt} as a formula edge, checked the units ({unit}) and that it "
                  f"computes on {len(hist) // 24} days of data, and now watches it in {cfg.C} countries"
                + (" as a ratio of sums." if agg == "ratio" else "."))
        node.note = note
        monitoring = {"watched": True, "countries": cfg.C, "baseline_error_pct": round(float(sigma.mean()) * 100, 1),
                      "now": {"country": cfg.COUNTRIES[worst].iso, "z": round(float(z_now[worst]), 1)},
                      "watched_series": cfg.C * (len(KPIS) + len(self.extensions))}
        out = node.to_dict()
        out["note"] = note
        return result("added", f"Added '{label}' and started monitoring it.", node=out, placement=place,
                      monitoring=monitoring, note=note)

    def _added_metrics_state(self) -> list[dict]:
        """Metrics added through automatic extension, with their current deviation (z) per country."""
        if not self.extensions or self.t not in self.twin.rows:
            return []
        z = self._extension_z(self.t)
        out = []
        for j, (nid, e) in enumerate(self.extensions.items()):
            n = self.graph.nodes[nid]
            worst = int(np.argmax(np.abs(z[:, j])))
            out.append({"id": nid, "label": n.label, "expr": e["expr"], "unit": n.unit, "added_at": e.get("added_at"),
                        "status": "watch" if abs(z[worst, j]) >= WATCH_Z else "ok",
                        "max_z": {"country": cfg.COUNTRIES[worst].iso, "z": round(float(z[worst, j]), 1)},
                        "note": n.note})
        return out

    def _ext_values(self, rows: list[dict]) -> dict:
        s = {k: sum(_country_sums(r)[k] for r in rows) for k in _country_sums(rows[0])}
        return _with_formulas(s)

    def _extension_sigma(self, expr: str) -> np.ndarray:
        ts = sorted(self.twin.rows)[-24 * 14:]
        rels = []
        for i in range(WINDOW + STAY, len(ts)):
            w = ts[i - WINDOW + 1: i + 1]
            with np.errstate(divide="ignore", invalid="ignore"):
                o = np.asarray(evaluate(expr, self._ext_values([self.twin.rows[x] for x in w])), dtype=float)
                f = np.asarray(evaluate(expr, self._ext_values([self.twin.forecast(x) for x in w])), dtype=float)
            rels.append((o - f) / np.maximum(np.abs(f), 1e-9))
        rels = np.nan_to_num(np.array(rels))
        return np.maximum(np.sqrt((rels ** 2).mean(0)) * 1.3, 0.01) * np.ones(cfg.C)

    def _extension_z(self, t: int) -> np.ndarray:
        if not self.extensions:
            return np.zeros((cfg.C, 0))
        ts = [t - i for i in range(WINDOW)]
        o_vals = self._ext_values([self.twin.rows[x] for x in ts])
        f_vals = self._ext_values([self.twin.forecast(x) for x in ts])
        out = []
        for e in self.extensions.values():
            with np.errstate(divide="ignore", invalid="ignore"):
                o = np.asarray(evaluate(e["expr"], o_vals), dtype=float)
                f = np.asarray(evaluate(e["expr"], f_vals), dtype=float)
            out.append(np.nan_to_num((o - f) / np.maximum(np.abs(f), 1e-9) / e["sigma"]))
        return np.stack(out, axis=1)

    # ================================================================== potential (taste model)
    def compute_potential(self) -> list[dict]:
        t = self.view.t - 1
        rows = [self.view.observed[x] for x in range(max(0, t - 23), t + 1)]
        self.potential_alloc = getattr(self, "potential_alloc", {})
        out = []
        demands = [self.taste.country_demand(self.view.sample_users(c, 0, 150) + self.view.sample_users(c, 1, 150))
                   for c in range(cfg.C)]
        global_demand = np.mean(demands, axis=0)
        for c, country in enumerate(cfg.COUNTRIES):
            demand = demands[c]
            imp = sum(r["impressions"][c].sum(0) for r in rows)
            st = sum(r["starts"][c].sum(0) for r in rows)
            co = sum(r["completions"][c].sum(0) for r in rows)
            mg = sum((r["revenue"][c] - r["rewards"][c]).sum(0) for r in rows)
            a_cur = imp / imp.sum()
            s, cr, m = st / np.maximum(imp, 1e-9), co / np.maximum(st, 1e-9), mg / np.maximum(co, 1e-9)

            def margin_k(a: np.ndarray) -> np.ndarray:
                # Assumed taste elasticity: start rate rises with under-served demand (exponent 0.6).
                return imp.sum() * a * s * (a_cur / a) ** 0.6 * cr * m

            def margin(a: np.ndarray) -> float:
                return float(margin_k(a).sum())

            cur = margin(a_cur)
            best, a_best = cur, a_cur
            for lam in np.linspace(0, 1, 21):
                a = a_cur ** (1 - lam) * demand ** lam
                a = a / a.sum()
                v = margin(a)
                if v > best:
                    best, a_best = v, a
            gain = margin_k(a_best) - margin_k(a_cur)        # margin dollars each category adds per day
            # Top categories: under-served here (the taste-matched feed adds margin) and distinctive for local taste
            # (demand index vs the global average), so the story differs by region.
            index = demand / global_demand
            under = [int(k) for k in np.where(gain > 0)[0]]
            top = sorted(under, key=lambda k: -index[k])[:2]
            explain = self.taste.explain_signals(top[0]) if top else []
            self.potential_alloc[c] = a_best
            mk_cur, mk_best = margin_k(a_cur), margin_k(a_best)
            self.potential_ratio = getattr(self, "potential_ratio", {})
            self.potential_ratio[c] = np.where(np.abs(mk_cur) > 1e-9, mk_best / np.where(mk_cur == 0, 1, mk_cur), 1.0)
            liked = demand / demand.sum()
            seen = [int(k) for k in np.argsort(a_cur)[::-1][:2]]
            if a_cur[seen[1]] < 0.75 * a_cur[seen[0]]:
                seen = seen[:1]
            out.append({"iso": country.iso, "potential_usd_day": round(max(best - cur, 0.0), 0),
                        "potential_pct": round(max(best / cur - 1, 0.0) * 100, 1) if cur > 0 else 0.0,
                        "current_margin_usd_day": round(cur, 0),
                        "demand_index": {cfg.CATEGORIES[k]: round(float(index[k]), 2) for k in range(cfg.K)},
                        "top_categories": [cfg.CATEGORIES[k] for k in top], "explain": explain,
                        "mix": [{"category": cfg.CATEGORIES[k], "liked": round(float(liked[k]), 3),
                                 "seen": round(float(a_cur[k]), 3)} for k in range(cfg.K)],
                        "mostly_seen": [cfg.CATEGORIES[k] for k in seen],
                        "mostly_seen_shares": {cfg.CATEGORIES[k]: round(float(a_cur[k]), 3) for k in seen},
                        "line": self._growth_line(country.name, top, seen, max(best - cur, 0.0))})
        return out

    @staticmethod
    def _growth_line(name: str, like: list[int], seen: list[int], usd_day: float) -> str:
        """Growth-mode sentence (screen rule: no 'match', 'taste', 'feed', 'potential')."""
        words = lambda ks: " and ".join(GROWTH_NOUN[k] for k in ks)  # noqa: E731
        who = _country_phrase(name)
        who = who[0].upper() + who[1:]
        if not like or usd_day < 1:
            return f"{who}: users already mostly see what they like."
        if set(like) & set(seen):
            return (f"{who}: users like {words(like)} and already see some, but more would add about "
                    f"+${usd_day:,.0f} a day.")
        return (f"{who}: users like {words(like)}, but mostly see {words(seen)}. Showing more of what they like: "
                f"about +${usd_day:,.0f} a day.")

    MIX_GAIN_SIGMA = 0.35    # assumed: uncertainty of the gain itself (how strongly starts follow what users like)

    def offer_mix_rollout(self, c: int, n: int = 40, hours: int = 168) -> dict:
        """Read-only 7-day twin rollout: today's offer mix vs the better-fit mix (from Growth mode), cumulative margin.

        Both options use the twin's hourly forecast for the country and the same random paths (common random numbers):
        a level shift (backtest error), hourly noise, and for the better mix an uncertain size of the per-category gain.
        """
        tw, now = self.twin, self.t
        ratio = self.potential_ratio.get(c, np.ones(cfg.K))
        mg = np.stack([(tw.forecast(now + 1 + h)["revenue"][c] - tw.forecast(now + 1 + h)["rewards"][c]).sum(0)
                       for h in range(hours)])                                          # (H,K) margin by category
        sig = float(tw.l1_sigma[c, :, 4].mean())
        rng = np.random.default_rng([self.seed, c, 4242, now])
        cur, bet = np.zeros((n, hours)), np.zeros((n, hours))
        for i in range(n):
            level = np.exp(rng.normal(0, sig)) * np.exp(rng.normal(0, sig, hours))
            g = np.exp(rng.normal(0, self.MIX_GAIN_SIGMA))
            cur[i] = np.cumsum(mg.sum(1) * level)
            bet[i] = np.cumsum((mg * (1 + (ratio - 1) * g)).sum(1) * level)
        day_idx = [24 * d - 1 for d in range(1, hours // 24 + 1)]
        q = lambda x: {k: [round(float(v)) for v in np.percentile(x[:, day_idx], p, axis=0)]  # noqa: E731
                       for k, p in (("p10", 10), ("p50", 50), ("p90", 90))}
        gain = bet[:, -1] - cur[:, -1]
        gw = {k: round(float(np.percentile(gain, p))) for k, p in (("p10", 10), ("p50", 50), ("p90", 90))}
        return {"country": cfg.COUNTRIES[c].iso, "days": list(range(1, hours // 24 + 1)), "current": q(cur),
                "better": q(bet), "gain_week_usd": gw, "gain_day_usd": round(gw["p50"] / (hours / 24)),
                "line": f"{'+' if gw['p50'] >= 0 else '−'}${abs(gw['p50']):,.0f} over the week",
                "applied": False}

    def growth_summary_line(self) -> str:
        tot = sum(p["potential_usd_day"] for p in self.potential)
        top = max(self.potential, key=lambda p: p["potential_usd_day"])
        name = _country_phrase(cfg.COUNTRIES[cfg.ISO_INDEX[top["iso"]]].name)
        return f"Showing each country more of what its users like: about +${tot:,.0f} a day in total · biggest in {name}"

    # ================================================================== views
    def get_incident(self, inc_id: str) -> dict | None:
        return next((i for i in self.incidents if i["id"] == inc_id), None)

    @staticmethod
    def _explained_line(c: int, rel: float, noun: str, kpi: str) -> dict:
        """change_pct = signed KPI move vs the normal (naive) baseline; line = 'Germany +31% · planned promo, no alarm'."""
        pct = int(round(rel * 100))
        return {"change_pct": pct, "kpi": kpi, "line": f"{cfg.COUNTRIES[c].name} {pct:+d}% · {noun}, no alarm"}

    @staticmethod
    def found_line(inc: dict) -> str:
        """'Found by Argus at 06:00, nobody asked · advertisers would react in ~22 h' (story clock, end of hour)."""
        line = f"Found by Argus at {hour_label(inc['detected_at']).split()[1]}, nobody asked"
        r = inc.get("reaction_h")
        if r is None:
            return line
        return line + (" · losses are happening now" if r <= 0 else f" · advertisers would react in ~{int(round(r))} h")

    def incident_summary(self, inc: dict) -> dict:
        out = {k: inc[k] for k in ("id", "title", "country", "status", "severity", "impact_usd_h", "risk_7d_usd",
                                   "reaction_h")}
        out.update(self._state_fields(inc))
        out["card"] = self.card(inc)
        out["found_line"] = self.found_line(inc)
        out["short_title"], out["title_detail"] = self.short_title(inc)
        return out

    @staticmethod
    def _state_fields(inc: dict) -> dict:
        """Issue tracking: plain state, how long it has been going, how many alerts were merged into it."""
        left = inc.get("left_zone_at")
        return {"status_label": STATUS_LABEL[inc["status"]],
                "first_seen": left["label"] if left else hour_label(inc["detected_at"]),
                "last_seen": hour_label(inc["last_seen_at"]),
                "resolved_at": hour_label(inc["resolved_at"]) if inc.get("resolved_at") is not None else None,
                "resolved_t": inc.get("resolved_at"),                    # numeric hour, like detected_at
                "alerts_merged": inc["alerts_merged"], "recurrence_of": inc.get("recurrence_of"),
                "downstream": inc.get("downstream", [])}

    def _excess_accounts(self, c: int, p: int, t: int, hours: int = 12) -> int:
        """Sign-ups above the twin's baseline on this platform over the recent anomaly (estimate of new accounts)."""
        n = 0.0
        for x in range(t - hours + 1, t + 1):
            if x in self.twin.rows:
                n += max(float(self.twin.rows[x]["new_accounts"][c, p] - self.twin.forecast(x)["new_accounts"][c, p]), 0)
        return int(round(n))

    def card(self, inc: dict) -> dict:
        """Plain-language issue card for the UI (no z-scores; those stay in the engineer report)."""
        name = _country_phrase(cfg.COUNTRIES[inc["c"]].name)
        plat = inc["platform"]
        cats = " and ".join(OFFER_NOUN[k] for k in inc["fix_categories"][:2])
        top = inc["hypotheses"][0]
        rec = next(f for f in inc["fixes"] if f["id"] == inc["recommended"])
        word = "proven" if top["verdict"] == "supported" else "likely" if top["verdict"] == "partial" else "unclear"
        proof = f"{word}, {top['explained']:.0%} explained"
        d_rev, d_mg = inc["delta_revenue_usd_h"], inc["delta_margin_usd_h"]
        if inc["kind"] == "fraud":
            looks = f"+{_fmt_usd(max(d_rev, 0))}/h of 'growth'"
            happened = (f"About {inc['excess_accounts']} fake accounts appeared on {plat} in {name} and are completing "
                        f"high-paying {cats} offers.")
            why = (f"Their stated interests don't match what they do and they share emulator devices ({proof}).")
        elif inc["kind"] == "tracking":
            looks = f"{_signed_usd(d_rev)}/h of revenue"
            happened = (f"Advertisers stopped confirming most {cats} completions from {plat} users in {name}, but we "
                        f"still pay those users their rewards.")
            why = f"Revenue per completion collapsed while user activity stayed normal ({proof})."
        elif inc["kind"] == "organic":
            looks = f"{_signed_usd(d_rev)}/h of revenue"
            happened = f"Many new real users joined on {plat} in {name}."
            why = f"Their tastes look genuine, so this is real growth ({proof})."
        else:
            looks = f"{_signed_usd(d_mg)}/h of margin"
            happened = f"{KPI_LABEL[inc['kpi']].capitalize()} in {name} moved away from what we expected."
            why = f"The change starts at {MECH_LABEL[inc['root']].lower() if inc['root'] else 'an unknown step'} ({proof})."
        if rec["id"] == "none":
            todo = "No action needed; keep watching."
        else:
            short = {"block_cluster": "suspend the fake accounts and their devices",
                     "verify_new_accounts": f"require phone and device checks for new {plat} sign-ups",
                     "cap_rewards": "lower rewards on the targeted offers",
                     "notify_advertiser": "ask the advertisers to fix their tracking",
                     "pause_offers": "pause the affected offers for 18 h",
                     "pause_notify": "pause the affected offers and ask the advertisers to fix tracking"}[rec["id"]]
            todo = f"{rec['name']}: {short}. Saves about {_fmt_usd(rec['delta_p50_usd'])} over the next 7 days."
        v = inc["verification"]["status"]
        status_line = {
            "open": "Needs a decision", "persisting": "Needs a decision",
            "fix_applied": "Fix applied · verifying" if v == "pending" else "Fix applied · prediction missed, model recalibrated",
            "verified": "Prediction verified", "resolved": "Resolved" + (" · prediction verified" if v == "verified" else ""),
        }[inc["status"]]
        if inc["status"] in ("fix_applied", "verified", "resolved") and inc["applied"]:
            todo = f"Done: {FIX_INFO[inc['applied']][0].lower()} applied."
        return {"title": inc["title"], "looks_like": looks, "at_stake_7d_usd": inc["risk_7d_usd"],
                "what_happened": happened, "why": why, "what_to_do": todo, "status_line": status_line}

    # ------------------------------------------------------------------ visual series
    def _seg_cats(self, inc: dict) -> list[int]:
        return list(inc["fix_categories"]) if inc["kind"] == "tracking" else [inc["k"]]

    @staticmethod
    def _seg_value(r: dict, kpi: str, c: int, p: int, cats: list[int]) -> float:
        if kpi in ("new_accounts", "active_users"):
            return float(r[kpi][c, p])

        def g(key: str) -> float:
            return float(np.asarray(r[key][c, p])[cats].sum())
        if kpi == "margin":
            return g("revenue") - g("rewards")
        if kpi == "quality_per_completion":
            return g("quality") / max(g("completions"), 1e-9)
        return g(kpi)

    def _seg_sigma(self, kpi: str, c: int, p: int, k: int) -> float:
        """Hourly relative (log) noise of the segment's KPI, from the twin's learned mechanism noise."""
        tw = self.twin
        if kpi == "new_accounts":
            return float(tw.sig_new[c, p])
        if kpi == "active_users":
            return float(tw.sig_ret[c, p])
        if kpi == "quality_per_completion":
            return float(tw.sig["quality"][c, p, k])
        acc = tw.sig_ret[c, p] ** 2 + sum(tw.sig[m][c, p, k] ** 2 for m in ("impressions", "starts", "completions"))
        if kpi in ("revenue", "margin"):
            acc += tw.sig["revenue"][c, p, k] ** 2
        elif kpi == "rewards":
            acc += tw.sig["rewards"][c, p, k] ** 2
        elif kpi == "quality":
            acc += tw.sig["quality"][c, p, k] ** 2
        return float(np.sqrt(acc))

    def series(self, inc: dict, hours: int = 48) -> dict:
        """Alerting KPI for the root segment, hourly up to now, with the twin's expectation and a ±2σ band."""
        tw = self.twin
        c, p, kpi = inc["c"], inc["p"], inc["kpi"]
        cats = self._seg_cats(inc)
        sig = self._seg_sigma(kpi, c, p, cats[0])
        start = max(min(tw.rows), self.t - hours + 1)
        pts = []
        for t in range(start, self.t + 1):
            actual = self._seg_value(tw.rows[t], kpi, c, p, cats)
            exp = self._seg_value(tw.forecast(t), kpi, c, p, cats)
            lo = exp - abs(exp) * (1 - np.exp(-2 * sig))
            hi = exp + abs(exp) * (np.exp(2 * sig) - 1)
            pts.append({"t": t, "label": hour_label(t), "actual": round(actual, 3), "expected": round(exp, 3),
                        "lo": round(float(lo), 3), "hi": round(float(hi), 3)})
        # First hour of the excursion that led to detection: the start of the last run of out-of-band hours
        # at or before detection.
        left = None
        outside = [pt for pt in pts if pt["t"] <= inc["detected_at"] and not pt["lo"] <= pt["actual"] <= pt["hi"]]
        if "left_zone_at" in inc:
            outside = []
            left = inc["left_zone_at"]["t"] if inc["left_zone_at"] else None
        if outside:
            run_end = outside[-1]["t"]
            left = run_end
            out_ts = {pt["t"] for pt in outside}
            while left - 1 in out_ts:
                left -= 1
        seg_name = f"{cfg.COUNTRIES[c].name} · {inc['platform']}"
        if kpi not in ("new_accounts", "active_users"):
            seg_name += " · " + ", ".join(cfg.CATEGORIES[x] for x in cats)
        return {"kpi": kpi, "label": f"{KPI_LABEL[kpi].capitalize()}, {seg_name}",
                "unit": "USD/h" if kpi in ("revenue", "rewards", "margin") else "ratio"
                if kpi == "quality_per_completion" else "per hour",
                "points": pts, "left_zone_at": None if left is None else {"t": left, "label": hour_label(left)}}

    def counterfactual(self, inc: dict, ser: dict) -> dict | None:
        """Twin replay of the series hours with the supported cause removed (its mechanisms set to expected)."""
        spec = inc.get("cause_spec")
        if not spec:
            return None
        tw = self.twin
        c, p, kpi = inc["c"], inc["p"], inc["kpi"]
        cats = self._seg_cats(inc)
        M = np.ones((2, cfg.P, len(MECHANISMS)), dtype=bool)
        for pp, mechs in spec.items():
            for m in mechs:
                M[1, int(pp), MECHANISMS.index(m)] = False
        start = ser["left_zone_at"]["t"] if ser["left_zone_at"] else inc["detected_at"]
        pts = []
        for pt in ser["points"]:
            t = pt["t"]
            if t < start:
                v = pt["actual"]                       # before the excursion the cause was not there
            else:
                segs = tw.chain(c, [t], M)["segments"]
                if kpi in ("new_accounts", "active_users"):
                    v = float(segs[kpi][1, p])
                else:
                    g = {k: float(segs[k][1, p, cats].sum()) for k in ("completions", "revenue", "rewards", "quality")}
                    v = (g["revenue"] - g["rewards"] if kpi == "margin" else
                         g["quality"] / max(g["completions"], 1e-9) if kpi == "quality_per_completion" else g[kpi])
            pts.append({"t": t, "value": round(v, 3)})
        top = inc["hypotheses"][0]
        if inc["kind"] == "fraud" and inc["l3"]:
            s = inc["l3"]["summary"]
            label = f"Without the fake accounts ({fake_count_phrase(s['n_flagged'], s['n_users'], inc['excess_accounts'])})"
        elif inc["kind"] == "tracking":
            label = "Without the tracking break"
        else:
            label = "Without: " + top["name"].split(" (")[0].lower()
        return {"label": label, "series": pts, "explained_share": top["explained"]}

    def timeline(self) -> dict:
        """What Argus knows, in time order: calendar, first unusual hour, detection, approval, verification, waits."""
        ev: list[dict] = []
        for e in self.view.calendar:
            if e.start <= self.t:
                ev.append({"t": e.start, "label": f"Planned: {e.name} ({', '.join(e.to_dict()['categories'])})",
                           "kind": "planned", "country": e.country, "time_label": hour_label(e.start - 1)})
        for inc in self.incidents:
            iso = inc["country"]
            left = inc.get("left_zone_at")
            if left is not None:
                ev.append({"t": left["t"], "label": f"First unusual hour: {KPI_LABEL[inc['kpi']]} on "
                                                    f"{inc['platform']} left the normal zone",
                           "kind": "anomaly", "country": iso})
            ev.append({"t": inc["detected_at"], "label": f"Detected: {inc['title']}", "kind": "detected", "country": iso})
            if inc["applied_at"] is not None:
                ev.append({"t": inc["applied_at"], "label": f"Approved: {FIX_INFO[inc['applied']][0]}",
                           "kind": "approved", "country": iso})
            if inc.get("verified_at") is not None:
                ok = inc["verification"]["status"] == "verified"
                ev.append({"t": inc["verified_at"], "label": "Prediction verified" if ok else
                           "Prediction missed, model recalibrated", "kind": "verified", "country": iso})
        for w in self.wait_log:
            ev.append({"t": w["t"], "label": f"Waiting for data ({w['share']:.0%} of rows arrived)", "kind": "waiting",
                       "country": w["country"]})
        for e in self.explained:
            if not e["event"].startswith("Late data"):
                ev.append({"t": e["t"], "label": "Explained: " + e["event"].split(":")[0], "kind": "explained",
                           "country": e["country"]})
        order = {"planned": 0, "explained": 1, "waiting": 2, "anomaly": 3, "detected": 4, "approved": 5, "verified": 6}
        ev.sort(key=lambda x: (x["t"], order[x["kind"]]))
        for x in ev:
            x.setdefault("time_label", hour_label(x["t"]))   # planned events start at the beginning of their hour
        return {"events": ev}

    def incident_detail(self, inc: dict) -> dict:
        keys = ("id", "title", "status", "country", "platform", "category", "detected_at", "trail", "graph",
                "contributions", "hypotheses", "l3", "fixes", "recommended", "applied", "verification", "reports",
                "severity", "impact_usd_h", "risk_7d_usd", "reaction_h", "kind", "root")
        out = {k: inc[k] for k in keys}
        out.update(self._state_fields(inc))
        out["found_line"] = self.found_line(inc)
        out["reports"] = self.reports_for(inc)
        out["replay"] = self.replay(inc)
        out["short_title"], out["title_detail"] = self.short_title(inc)
        out["card"] = self.card(inc)
        ser = self.series(inc)
        out["series"] = ser["points"]
        out["series_meta"] = {k: ser[k] for k in ("kpi", "label", "unit")}
        out["left_zone_at"] = ser["left_zone_at"]
        out["counterfactual"] = self.counterfactual(inc, ser)
        return out

    def country_status(self, c: int) -> str:
        if self.waiting[c]:
            return "waiting"
        if any(i["c"] == c and i["status"] in ("open", "persisting") for i in self.incidents):
            return "incident"
        if self.explaining[c]:
            return "explained"
        if self.last is not None and (np.abs(self.health[c]) >= WATCH_Z or self.consec[c].any()):
            return "watch"
        return "ok"

    ATTENTION_CAP_USD = 250_000.0     # $ at stake per week that maps to attention 1.0 (fixed, so scenes compare)

    def attention_usd(self, c: int, l1: dict | None) -> float:
        """Money at stake × confidence, for the globe. 0 for ok / explained / waiting countries.

        incident: Σ open issues' 7-day risk (do nothing vs fix) × the share of the move the proven cause explains;
        watch: the current margin-side deviation held for a week × the chance it becomes an issue (|z| 4 → 0.2, 8 → 1);
        0 while a fix a human approved is being verified there.
        """
        st = self.country_status(c)
        if st == "incident":
            return float(sum(i["risk_7d_usd"] * (i["hypotheses"][0]["explained"] if i.get("hypotheses") else 0.5)
                             for i in self.incidents if i["c"] == c and i["status"] in ("open", "persisting")))
        if st == "watch" and any(i["c"] == c and i["status"] == "fix_applied" for i in self.incidents):
            return 0.0                    # a human already acted; the move is the fix working (being verified)
        if st == "watch" and l1 is not None:
            dev = float(np.abs(l1["obs"][c, 2:5] - l1["fc"][c, 2:5]).max())       # USD/h, revenue/rewards/margin
            prob = float(np.clip((abs(self.health[c]) - 3) / 5, 0, 1))
            return dev * 24 * 7 * prob
        return 0.0

    def _attention_fields(self, c: int, l1: dict | None) -> dict:
        usd = self.attention_usd(c, l1)
        return {"attention": round(min(usd / self.ATTENTION_CAP_USD, 1.0), 3), "attention_usd": round(usd)}

    def clock(self) -> dict:
        t = self.t
        day, hour = divmod(t + 1, 24)                    # clock = end of the last processed hour
        dow = DOW[day % 7]
        return {"t": t, "label": f"Day {day + 1} · {dow} {hour:02d}:00 UTC", "utc_hour": hour,
                "day_of_week": dow, "day": day + 1}

    def frame(self) -> dict:
        """Compact per-hour snapshot for time-lapse playback: enough to repaint the globe and the issue list."""
        l1 = self.twin.l1_z(self.t) if self.last is not None else None
        return {"t": self.t, "clock": self.clock(),
                "countries": [{"iso": co.iso, "status": self.country_status(c),
                               "health_z": round(float(self.health[c]), 2) if self.last is not None else 0.0,
                               **self._attention_fields(c, l1)} for c, co in enumerate(cfg.COUNTRIES)],
                "incidents": [{"id": i["id"], "country": i["country"], "status": i["status"],
                               "status_label": STATUS_LABEL[i["status"]], "severity": i["severity"],
                               "short_title": self.short_title(i)[0]} for i in self.incidents]}

    def state(self) -> dict:
        t = self.t
        l1 = self.twin.l1_z(t) if self.last is not None else None
        last = self.view.observed[-1]
        s = _with_formulas(_country_sums(last))
        pot = {p["iso"]: p["potential_usd_day"] for p in self.potential}
        pct = {p["iso"]: p["potential_pct"] for p in self.potential}
        day, hour = divmod(t + 1, 24)                    # clock = end of the last processed hour
        dow = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"][day % 7]
        countries = []
        for c, country in enumerate(cfg.COUNTRIES):
            countries.append({
                "iso": country.iso, "name": country.name, "lat": country.lat, "lng": country.lng,
                "status": self.country_status(c),
                "health_z": round(float(self.health[c]), 2) if self.last is not None else 0.0,
                "kpis": {k: round(float(s[k][c]), 4 if k == "quality_per_completion" else 2) for k in KPIS},
                "potential_usd_day": pot.get(country.iso, 0.0),
                "potential_pct": pct.get(country.iso, 0.0),
                **self._attention_fields(c, l1),
            })
        return {
            "clock": self.clock(),
            "countries": countries,
            "incidents": [self.incident_summary(i) for i in self.incidents],
            "explained": list(self.explained),
            "added_metrics": self._added_metrics_state(),
            "attention": {"watched_series": cfg.C * (len(KPIS) + len(self.extensions)),
                          "investigations": self.investigations, "l3_runs": self.l3_runs,
                          "segments_total": cfg.C * cfg.P * cfg.K, "segments_opened": len(self.opened_segments),
                          "users_total": int(sum(co.dau for co in cfg.COUNTRIES)), "users_checked": self.users_checked},
        }

    def country_detail(self, c: int) -> dict:
        segs, platforms = self._segment_cells(c, self.t)
        return {"iso": cfg.COUNTRIES[c].iso, "name": cfg.COUNTRIES[c].name, "segments": segs, "platforms": platforms,
                "summary": self.country_summary(c, segs, platforms)}

    def _segment_cells(self, c: int, t: int) -> tuple[list[dict], list[dict]]:
        """Heatmap evidence for the 3 h window ending at hour t: z per platform × category, and per platform."""
        ts = [t - i for i in range(WINDOW)]
        tw = self.twin
        rows, fcs = [tw.rows[x] for x in ts], [tw.forecast(x) for x in ts]
        segs = []
        platforms = []
        gp = tw.given_parents(sorted(ts))
        for p in range(cfg.P):
            # Platform-level users: sign-ups vs seasonal baseline; active users given sign-ups (returning users).
            nz = round(tw.z_of(gp["new_accounts"], c, p), 1)
            az = round(tw.z_of(gp["active_users"], c, p), 1)
            n_o = sum(r["new_accounts"][c, p] for r in rows)
            n_f = sum(x["new_accounts"][c, p] for x in fcs)
            platforms.append({"platform": cfg.PLATFORMS[p], "new_accounts_z": nz, "active_users_z": az,
                              "new_accounts_h": round(float(n_o / WINDOW), 1),
                              "new_accounts_expected_h": round(float(n_f / WINDOW), 1)})
            for k in range(cfg.K):
                kz = {"new_accounts": nz}
                kr = {"new_accounts": round(float(n_o / max(n_f, 1e-9)), 3)}
                for key, m in (("completions", "completions"), ("revenue", "revenue"), ("rewards", "rewards")):
                    o = sum(r[key][c, p, k] for r in rows)
                    f = sum(x[key][c, p, k] for x in fcs)
                    kr[key] = round(float(o / max(f, 1e-9)), 3)
                    sig = np.sqrt(sum(tw.sig[mm][c, p, k] ** 2 for mm in ("impressions", "starts", "completions"))
                                  + tw.sig_ret[c, p] ** 2 + (tw.sig[m][c, p, k] ** 2 if m != "completions" else 0))
                    kz[key] = round(float((o / max(f, 1e-9) - 1) / (sig / np.sqrt(WINDOW))), 1)
                qo = sum(r["quality"][c, p, k] for r in rows) / max(sum(r["completions"][c, p, k] for r in rows), 1e-9)
                qf = sum(x["quality"][c, p, k] for x in fcs) / max(sum(x["completions"][c, p, k] for x in fcs), 1e-9)
                kz["quality_per_completion"] = round(float((qo / qf - 1) / (tw.sig["quality"][c, p, k] / np.sqrt(WINDOW))), 1)
                kr["quality_per_completion"] = round(float(qo / max(qf, 1e-9)), 3)
                dm = sum(r["revenue"][c, p, k] - r["rewards"][c, p, k] for r in rows) - \
                    sum(x["revenue"][c, p, k] - x["rewards"][c, p, k] for x in fcs)
                segs.append({"platform": cfg.PLATFORMS[p], "category": cfg.CATEGORIES[k], "kpi_z": kz, "kpi_ratio": kr,
                             "deviation_usd_h": round(float(dm / WINDOW), 2)})
        return segs, platforms

    # ------------------------------------------------------------------ simplified country view
    def country_summary(self, c: int, segs: list[dict], platforms: list[dict]) -> dict:
        """One sentence, 2-3 numbers vs usual, margin over the last 24 h with the usual band, and what is moving."""
        tw, t, name = self.twin, self.t, cfg.COUNTRIES[c].name
        status = self.country_status(c)
        ts = [t - i for i in range(WINDOW)]
        o = sum(tw._l1(tw.rows[x])[c] for x in ts) / WINDOW
        f = sum(tw._l1(tw.forecast(x))[c] for x in ts) / WINDOW

        def money(v: float) -> str:
            return f"${v / 1000:.1f}k" if abs(v) >= 1000 else f"${v:,.0f}"

        numbers = [
            {"label": "Margin", "value": round(float(o[4]), 1), "usual": round(float(f[4]), 1), "unit": "USD/h",
             "line": f"{money(o[4])} an hour · usual {money(f[4])}"},
            {"label": "New sign-ups", "value": round(float(o[0])), "usual": round(float(f[0])), "unit": "per hour",
             "line": f"{o[0]:,.0f} an hour · usual {f[0]:,.0f}"},
            {"label": "Completed offers", "value": round(float(o[1])), "usual": round(float(f[1])), "unit": "per hour",
             "line": f"{o[1]:,.0f} an hour · usual {f[1]:,.0f}"},
        ]
        # chart: margin per hour over the last 24 h with the usual band (single-hour ±2σ from the twin's backtest
        # error); for an explained country the KPI that moved instead. `normal` = a normal day (same weekday and hour,
        # no planned event): the naive baseline an explained event is compared against.
        ex = ([e for e in self.explained if e["country"] == cfg.COUNTRIES[c].iso] if status == "explained" else [])
        ki = 4
        if ex and ex[-1].get("kpi") in KPI_KEY_OF_LABEL:
            ki = KPIS.index(KPI_KEY_OF_LABEL[ex[-1]["kpi"]])
        pts = []
        for x in range(max(min(tw.rows), t - 23), t + 1):
            g = ((x + cfg.COUNTRIES[c].tz) % 24) // (24 // 4)
            sig = float(tw.l1_sigma[c, g, ki]) * np.sqrt(WINDOW)
            exp = float(tw._kpi_values(tw._l1(tw.forecast(x)))[c, ki])
            nor = float(tw._kpi_values(tw._l1(tw.forecast(x, calendar=False)))[c, ki])
            val = float(tw._kpi_values(tw._l1(tw.rows[x]))[c, ki])
            nd = 3 if ki == 5 else 1
            pts.append({"t": x, "label": hour_label(x), "value": round(val, nd), "usual": round(exp, nd),
                        "lo": round(float(exp * (1 - 2 * sig)), nd), "hi": round(float(exp * (1 + 2 * sig)), nd),
                        "normal": round(nor, nd)})
        metric, unit, short = SERIES_METRIC[KPIS[ki]]
        change_label = None
        if ex and "change_pct" in ex[-1]:
            change_label = f"{short} {ex[-1]['change_pct']:+d}% vs a normal {DOW_FULL[(t + 1) // 24 % 7]}"
        # what is moving: the heatmap's evidence (twin z, calendar-aware), at most 2 segments with |z| >= ALERT_Z
        cand = []
        for pl in platforms:
            if abs(pl["new_accounts_z"]) >= ALERT_Z:
                p = cfg.PLATFORMS.index(pl["platform"])
                on = [sg for sg in segs if sg["platform"] == pl["platform"]]
                top = max(on, key=lambda sg: abs(sg["kpi_z"]["completions"]))
                cat = top["category"] if abs(top["kpi_z"]["completions"]) >= ALERT_Z else None
                ratio = pl["new_accounts_h"] / max(pl["new_accounts_expected_h"], 1e-9)
                cand.append((abs(pl["new_accounts_z"]), pl["platform"], cat, "new_accounts", ratio, p))
        for sg in segs:
            for kpi in ("revenue", "completions", "rewards"):
                z = sg["kpi_z"][kpi]
                if abs(z) >= ALERT_Z:
                    p, k = cfg.PLATFORMS.index(sg["platform"]), cfg.CATEGORIES.index(sg["category"])
                    ob = sum(tw.rows[x][kpi][c, p, k] for x in ts)
                    fc = sum(tw.forecast(x)[kpi][c, p, k] for x in ts)
                    cand.append((abs(z), sg["platform"], sg["category"], kpi, ob / max(fc, 1e-9), p))
        cand.sort(key=lambda x: -x[0])
        moving, seen = [], set()
        word = {"new_accounts": "sign-ups", "revenue": "revenue", "completions": "completed offers", "rewards": "rewards"}
        for _, plat, cat, kpi, ratio, _p in cand:
            key = (plat, kpi if kpi == "new_accounts" else cat)
            if key in seen or (kpi != "new_accounts" and (plat, "new_accounts") in seen and kpi == "completions"):
                continue
            seen.add(key)
            where = plat + (f" · {cat.lower()}" if cat else "")
            move = _ratio_text(ratio)
            moving.append({"line": f"{where}: {word[kpi]} {move}", "platform": plat, "category": cat, "kpi": kpi,
                           "ratio": round(float(ratio), 2)})
            if len(moving) == 2:
                break
        # ok countries: [] (single-segment blips below the alert rule stay in segments[], not in the words)
        unusual = self._unusual(c, segs, platforms) if status != "ok" else []
        # one plain sentence
        if status == "incident":
            inc = [i for i in self.incidents if i["c"] == c and i["status"] in ("open", "persisting")][-1]
            verdict = inc["card"]["title"] if "card" in inc else self._title(inc)
            verdict = verdict.replace(f" in {_country_phrase(name)}", "").replace(f" in {name}", "")
        elif status == "explained":
            ex = [e for e in self.explained if e["country"] == cfg.COUNTRIES[c].iso]
            verdict = ex[-1]["line"].replace(f"{name} ", "", 1) if ex and "line" in ex[-1] else "Planned event, no alarm"
        elif status == "waiting":
            verdict = "Data arriving late: waiting before judging, no alarm"
        elif status == "watch":
            verdict = f"Something unusual: watching {moving[0]['line'].split(': ')[1] if moving else 'closely'}"
        else:
            verdict = "All fine"
            moving = []
        return {"status": status, "verdict": verdict, "numbers": numbers, "unusual": unusual,
                "segments_total": cfg.P * cfg.K,
                "series": {"metric": metric, "kpi": KPIS[ki], "unit": unit, "change_label": change_label, "points": pts,
                           "events": self._calendar_marks(c, pts[0]["t"], pts[-1]["t"])}, "moving": moving}

    def _unusual(self, c: int, segs: list[dict], platforms: list[dict]) -> list[dict]:
        """Every unusual segment (twin z, calendar-aware, |z| >= ALERT_Z) in plain words, categories that move the same
        way merged into one line, ranked by money at stake (|margin deviation| USD/h)."""
        fraud_p = {i["platform"] for i in self.incidents
                   if i["c"] == c and i["kind"] in ("fraud", "organic", "user") and i["status"] in ("open", "persisting")}
        out = []
        for pl in platforms:                                  # sign-ups are platform-level: "all offers"
            z = pl["new_accounts_z"]
            if abs(z) >= ALERT_Z:
                ratio = pl["new_accounts_h"] / max(pl["new_accounts_expected_h"], 1e-9)
                money = sum(abs(sg["deviation_usd_h"]) for sg in segs if sg["platform"] == pl["platform"])
                out.append({"platform": pl["platform"], "categories": None, "kpi": "new_accounts",
                            "ratio": round(float(ratio), 2), "z": z, "view": "who", "money_usd_h": round(money, 1)})
        for plat in cfg.PLATFORMS:
            for kpi in ("revenue", "completions", "rewards", "quality_per_completion"):
                for sign in (1, -1):
                    grp = [sg for sg in segs if sg["platform"] == plat and sign * sg["kpi_z"][kpi] >= ALERT_Z]
                    if not grp:
                        continue
                    ratio = float(np.exp(np.mean([np.log(max(sg["kpi_ratio"][kpi], 1e-6)) for sg in grp])))
                    z = max((sg["kpi_z"][kpi] for sg in grp), key=abs)
                    view = "who" if plat in fraud_p else "why"
                    cats = [sg["category"] for sg in grp]
                    out.append({"platform": plat, "categories": None if len(cats) == cfg.K else cats, "kpi": kpi,
                                "ratio": round(ratio, 2), "z": z, "view": view,
                                "money_usd_h": round(sum(abs(sg["deviation_usd_h"]) for sg in grp), 1)})
        out.sort(key=lambda u: (-u["money_usd_h"], -abs(u["z"])))
        for u in out:
            cats = u["categories"]
            where = ("all offers" if cats is None else cats[0].lower() if len(cats) == 1
                     else _and([SHORT_CAT[cfg.CATEGORIES.index(x)] for x in cats], "&"))
            u["line"] = f"{u['platform']} · {where}: {UNUSUAL_WORD[u['kpi']]} {_ratio_text(u['ratio'])}"
        return out

    def _calendar_marks(self, c: int, t0: int, t1: int) -> list[dict]:
        """Planned events of country c that start or end inside [t0, t1], for small chart markers."""
        out = []
        for ev in self.view.calendar:
            if ev.country != cfg.COUNTRIES[c].iso:
                continue
            noun = "Planned promo" if ev.reward_share > cfg.REWARD_SHARE else ev.name
            if t0 <= ev.start <= t1:
                out.append({"t": ev.start, "label": noun, "kind": "start", "time_label": hour_label(ev.start)})
            if t0 <= ev.end - 1 <= t1:
                out.append({"t": ev.end - 1, "label": f"{noun} ends", "kind": "end", "time_label": hour_label(ev.end - 1)})
        return out


# ====================================================================== keyword fallback
_PHRASES = [
    ("reward cost", "rewards"), ("user rewards", "rewards"), ("rewards", "rewards"), ("reward", "rewards"),
    ("advertiser revenue", "revenue"), ("revenue", "revenue"), ("contribution margin", "margin"),
    ("margin", "margin"), ("completed offers", "completions"), ("completed offer", "completions"),
    ("completions", "completions"), ("completion", "completions"), ("new accounts", "new_accounts"),
    ("new account", "new_accounts"), ("sign ups", "new_accounts"), ("signups", "new_accounts"),
    ("sign-ups", "new_accounts"), ("active users", "active_users"), ("active user", "active_users"),
    ("users", "active_users"), ("user", "active_users"), ("impressions", "impressions"),
    ("impression", "impressions"), ("offer starts", "starts"), ("starts", "starts"), ("start", "starts"),
    ("valuable users", "quality"), ("quality", "quality"),
]
_OPS = [(" divided by ", " / "), (" per ", " / "), (" minus ", " - "), (" plus ", " + "), (" times ", " * ")]
_RULES = [
    (("start rate",), "starts / impressions"), (("completion rate",), "completions / starts"),
    (("take rate",), "margin / revenue"), (("margin rate",), "margin / revenue"), (("arpu",), "revenue / active_users"),
    (("revenue per user",), "revenue / active_users"), (("reward rate",), "rewards / revenue"),
]


def _phrase_to_expr(text: str) -> str:
    s = " " + text.lower().strip() + " "
    for a, b in _OPS:
        s = s.replace(a, b)
    for a, b in _PHRASES:
        s = re.sub(rf"(?<![a-z_]){re.escape(a)}(?![a-z_])", f" {b} ", s)
    tokens = re.findall(r"[a-z_]+|\d+\.?\d*|[()+\-*/]", s)
    allowed = {b for _, b in _PHRASES}
    return " ".join(tok for tok in tokens if tok in allowed or not tok[0].isalpha())


def keyword_formula(name: str, description: str, known: set[str]) -> tuple[str | None, str]:
    """Deterministic fallback: explicit '= formula', fixed rules, then 'X per Y' phrases."""
    text = f"{description}".strip()
    if "=" in text:
        rhs = text.split("=", 1)[1]
        for cand in (rhs.strip(), _phrase_to_expr(rhs)):
            try:
                parse_expression(cand, known)
                return cand, "user"
            except (ValueError, SyntaxError):
                continue
    low = f"{name} {description}".lower()
    for keys, expr in _RULES:
        if any(k in low for k in keys):
            return expr, "keyword rules"
    for cand in (_phrase_to_expr(name), _phrase_to_expr(description)):
        cand = re.sub(r"\s+", " ", cand).strip()
        if not cand:
            continue
        try:
            parse_expression(cand, known)
            return cand, "keyword rules"
        except (ValueError, SyntaxError):
            continue
    return None, "keyword rules"
