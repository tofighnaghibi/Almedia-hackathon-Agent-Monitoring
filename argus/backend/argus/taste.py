"""Taste model: predicts which advertiser categories a person will enjoy from life signals.

This is the hackathon stand-in for Ehsan's cross-domain taste-transfer recommender.
It is trained on its own synthetic "historic users" (separate seed from the
simulated reality), so it is not a copy of the ground truth.

Two uses inside Argus:
  * what to advertise to whom (country demand by category, feed re-allocation),
  * telling real people from fake accounts: real people have coherent tastes,
    bots complete whatever pays most, whatever they claimed at onboarding.
"""
from __future__ import annotations

import numpy as np

from . import config as cfg

TOP_PAYOUT = np.argsort(cfg.PAYOUT)[-2:]  # strategy games + fintech


class TasteModel:
    def __init__(self, seed: int = 2026, n: int = 6000) -> None:
        rng = np.random.default_rng(seed)
        mix = np.full(cfg.A, 1 / cfg.A)
        X, Y = [], []
        for _ in range(n):
            a = rng.choice(cfg.A, p=mix)
            taste = rng.dirichlet(cfg.ARCHETYPE_AFFINITY[a] * 30)
            sig = (rng.random(cfg.S) < cfg.ARCHETYPE_SIGNALS[a]).astype(float)
            comp = rng.poisson(taste * rng.uniform(2, 9)).astype(float)
            if comp.sum() == 0:
                continue
            X.append(sig)
            Y.append(comp / comp.sum())
        X = np.c_[np.ones(len(X)), np.array(X)]
        Y = np.array(Y)
        lam = 2.0
        self.W = np.linalg.solve(X.T @ X + lam * np.eye(X.shape[1]), X.T @ Y)  # (S+1, K)
        coh = self._coherence(X[:, 1:], Y)
        self.coherence_floor = float(np.quantile(coh, 0.03))
        self.coherence_median = float(np.median(coh))
        self.weights_table = self.W[1:]

    def predict(self, signals: np.ndarray) -> np.ndarray:
        X = np.c_[np.ones(len(signals)), signals]
        p = np.clip(X @ self.W, 1e-3, None)
        return p / p.sum(axis=1, keepdims=True)

    def _coherence(self, signals: np.ndarray, observed_share: np.ndarray) -> np.ndarray:
        pred = self.predict(signals)
        num = (pred * observed_share).sum(axis=1)
        den = np.linalg.norm(pred, axis=1) * np.linalg.norm(observed_share, axis=1) + 1e-9
        return num / den

    def score_users(self, users: list[dict]) -> dict:
        """Coherence and payout focus for every sampled user, plus a fraud-cluster search."""
        sig = np.array([u["signals"] for u in users], dtype=float)
        comp = np.array([u["completions"] for u in users], dtype=float)
        total = comp.sum(axis=1)
        keep = total > 0
        share = np.where(total[:, None] > 0, comp / np.maximum(total[:, None], 1), 0)
        coh = np.where(keep, self._coherence(sig, np.maximum(share, 1e-9)), np.nan)
        focus = np.where(keep, comp[:, TOP_PAYOUT].sum(axis=1) / np.maximum(total, 1), np.nan)
        age = np.array([u["account_age_days"] for u in users])
        devices = [u["device"] for u in users]
        seed = keep & (coh < max(self.coherence_floor, 0.40)) & (focus > 0.6) & (age <= 7)
        # Expand the cluster through shared device fingerprints (accounts linked by device).
        seed_devs: dict[str, int] = {}
        for d, f in zip(devices, seed):
            if f:
                seed_devs[d] = seed_devs.get(d, 0) + 1
        shared = {d for d, n in seed_devs.items() if n >= 3}
        linked = np.array([d in shared for d in devices]) & keep & (age <= 7) & (focus > 0.5)
        flagged = seed | linked
        dev_counts: dict[str, int] = {}
        for d, f in zip(devices, flagged):
            if f:
                dev_counts[d] = dev_counts.get(d, 0) + 1
        top_devices = sorted(dev_counts.items(), key=lambda x: -x[1])[:3]
        points = [{"id": u["id"], "coherence": None if np.isnan(c) else round(float(c), 3),
                   "payout_focus": None if np.isnan(f) else round(float(f), 3),
                   "age": int(a), "flagged": bool(fl), "device": d}
                  for u, c, f, a, fl, d in zip(users, coh, focus, age, flagged, devices)]
        n_flag = int(flagged.sum())
        return {
            "n_users": len(users),
            "n_flagged": n_flag,
            "flagged_share": n_flag / max(len(users), 1),
            "coherence_flagged": float(np.nanmedian(coh[flagged])) if n_flag else None,
            "coherence_others": float(np.nanmedian(coh[keep & ~flagged])),
            "payout_focus_flagged": float(np.nanmedian(focus[flagged])) if n_flag else None,
            "payout_focus_others": float(np.nanmedian(focus[keep & ~flagged])),
            "device_concentration": (sum(c for _, c in top_devices) / n_flag) if n_flag else 0.0,
            "top_devices": top_devices,
            "flagged_mean_completions": comp[flagged].mean(axis=0).tolist() if n_flag else [0.0] * cfg.K,
            "others_mean_completions": comp[keep & ~flagged].mean(axis=0).tolist(),
            "coherence_floor": self.coherence_floor,
            "points": points,
        }

    def country_demand(self, users: list[dict]) -> np.ndarray:
        sig = np.array([u["signals"] for u in users], dtype=float)
        return self.predict(sig).mean(axis=0)

    def explain_signals(self, k: int, top: int = 3) -> list[str]:
        w = self.weights_table[:, k]
        idx = np.argsort(w)[-top:][::-1]
        return [cfg.SIGNALS[i] for i in idx]
