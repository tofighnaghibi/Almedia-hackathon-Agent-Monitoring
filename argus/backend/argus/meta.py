"""Public data behind the simulation (single source for docs/DATA_SOURCES.md and GET /api/meta)."""
from __future__ import annotations

import numpy as np

from . import config as cfg

ALMEDIA = ("Almedia release via PPC Land, 15 Oct 2026",
           "https://ppc.land/freecash-passes-500m-in-user-payouts-as-almedia-nears-1bn-run-rate/")
STATCOUNTER = ("StatCounter mobile OS market share, Oct 2026", "https://gs.statcounter.com/os-market-share/mobile/")
APPSFLYER = ("AppsFlyer gaming CPI benchmarks, Q3 2026 (published 5 Nov 2026)", "https://www.appsflyer.com/?p=549948")

# Puzzle-game CPI (USD), AppsFlyer Q3 2026. Android for 12 of our countries, iOS where published.
CPI_PUZZLE_ANDROID = {"US": 4.81, "DE": 3.10, "GB": 3.45, "CA": 3.62, "FR": 1.56, "AU": 4.54, "JP": 2.40,
                      "MX": 0.44, "BR": 0.49, "ID": 0.26, "PH": 0.29, "IN": 0.10}
CPI_PUZZLE_IOS = {"US": 9.48, "DE": 4.23, "GB": 4.71, "CA": 5.37, "FR": 2.85, "AU": 6.04}

CALIBRATION_NOTE = (
    "Calibrated to public data: total advertiser revenue ≈ $1B/365 = $2.74M/day (Almedia run rate); the share of "
    "user rewards by country matches Almedia's published lifetime payout shares for US, DE, GB, CA and FR (the other "
    "15 countries share the remaining 17.3% in proportion to their earlier simulated sizes; real payouts span 217 "
    "countries); iOS share per country from StatCounter (Oct 2026); country payout level from AppsFlyer puzzle-game "
    "Android CPI relative to the US, and an iOS payout multiplier of 1.54 (geometric mean of iOS/Android CPI ratios). "
    "Countries without a published CPI (NL, ES, IT, PL, SE, TR, ZA, KR) are scaled from regional ratios. Offer "
    "categories, conversion rates, taste archetypes, incidents and advertiser reactions are simulated."
)


def data_sources() -> list[dict]:
    a_label, a_url = ALMEDIA
    rows = [
        {"label": "Annual revenue run rate", "value": "$1B", "source": a_label, "url": a_url},
        {"label": "Lifetime user payouts", "value": ">$500M", "source": a_label, "url": a_url},
        {"label": "Monthly earners", "value": ">9M", "source": a_label, "url": a_url},
        {"label": "Users", "value": "~95M", "source": a_label, "url": a_url},
        {"label": "Countries and territories with payouts", "value": "217", "source": a_label, "url": a_url},
    ]
    names = {c.iso: c.name for c in cfg.COUNTRIES}
    for iso, share in cfg.PUBLISHED_PAYOUT_SHARE.items():
        rows.append({"label": f"Lifetime payout share, {names[iso]}", "value": f"{share:.1%}", "source": a_label,
                     "url": a_url})
    s_label, s_url = STATCOUNTER
    for c in cfg.COUNTRIES:
        slug = {"US": "united-states-of-america", "GB": "united-kingdom", "KR": "south-korea", "ZA": "south-africa",
                "TR": "turkey"}.get(c.iso, c.name.lower().replace(" ", "-"))
        rows.append({"label": f"iOS share of mobile OS, {c.name}", "value": f"{c.ios_share:.1%}", "source": s_label,
                     "url": s_url + slug})
    f_label, f_url = APPSFLYER
    for iso, v in CPI_PUZZLE_ANDROID.items():
        rows.append({"label": f"Puzzle CPI Android, {names[iso]}", "value": f"${v:.2f}", "source": f_label,
                     "url": f_url})
    for iso, v in CPI_PUZZLE_IOS.items():
        rows.append({"label": f"Puzzle CPI iOS, {names[iso]}", "value": f"${v:.2f}", "source": f_label, "url": f_url})
    rows.append({"label": "Launch candidate Argentina: iOS share of mobile OS", "value": "14.2%", "source": s_label,
                 "url": s_url + "argentina"})
    rows.append({"label": "GDP per capita and population, 2024 (launch similarity)", "value": "Argentina $13,970; 45.7M",
                 "source": "World Bank WDI (NY.GDP.PCAP.CD, SP.POP.TOTL)",
                 "url": "https://api.worldbank.org/v2/country/ARG/indicator/NY.GDP.PCAP.CD?date=2024"})
    return rows


def calibration(observed: list[dict], hours: int = 168) -> dict:
    """Simulated vs published: share of rewards by country and revenue per day, from the observed stream."""
    rows = observed[-hours:]
    rew = np.array([sum(r["rewards"][c].sum() for r in rows) for c in range(cfg.C)])
    rev = sum(r["revenue"].sum() for r in rows) * 24 / max(len(rows), 1)
    share = rew / max(rew.sum(), 1e-9)
    return {
        "revenue_usd_day": {"sim": round(float(rev)), "published": round(cfg.TARGET_REVENUE_DAY)},
        "rewards_share": [{"iso": c.iso, "name": c.name, "sim": round(float(share[i]), 4),
                           "published": cfg.PUBLISHED_PAYOUT_SHARE.get(c.iso)}
                          for i, c in enumerate(cfg.COUNTRIES)],
    }
