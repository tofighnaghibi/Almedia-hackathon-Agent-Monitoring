"""Static description of the simulated Freecash-like rewarded-ad marketplace.

Calibrated to public data where it exists (see docs/DATA_SOURCES.md); everything else is invented for the demo.
Each constant is marked "public" (taken or derived from a cited source) or "simulated" (a modelling choice).
Calibration targets (public, Almedia release 15 Oct 2026): ~$1B annual revenue run rate (= ~$2.74M/day) and the
lifetime payout shares US 57.9%, DE 9.7%, GB 8.2%, CA 3.7%, FR 3.2%; the other 15 countries share the remaining
17.3% in proportion to their earlier simulated sizes.
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class Country:
    iso: str
    name: str
    lat: float
    lng: float
    dau: int          # simulated: user scale, calibrated so the share of rewards matches published payout shares
    ios_share: float  # public: StatCounter mobile OS share, October 2026
    value: float      # public/simulated: advertiser payout multiplier vs. US (Android CPI ratio, AppsFlyer Q3 2026)
    tz: int           # simulated: UTC offset used for the daily rhythm
    region: str


COUNTRIES: list[Country] = [
    Country("US", "United States", 39.8, -98.6, 1_922_000, 0.5196, 1.000, -6, "North America"),  # value public: AppsFlyer puzzle Android CPI / US
    Country("CA", "Canada", 56.1, -106.3, 151_000, 0.6474, 0.753, -5, "North America"),  # value public: AppsFlyer puzzle Android CPI / US
    Country("MX", "Mexico", 23.6, -102.5, 585_000, 0.2589, 0.091, -6, "Latin America"),  # value public: AppsFlyer puzzle Android CPI / US
    Country("BR", "Brazil", -14.2, -51.9, 779_000, 0.2623, 0.102, -3, "Latin America"),  # value public: AppsFlyer puzzle Android CPI / US
    Country("GB", "United Kingdom", 54.0, -2.0, 382_000, 0.5014, 0.717, 0, "Europe"),  # value public: AppsFlyer puzzle Android CPI / US
    Country("DE", "Germany", 51.2, 10.4, 579_000, 0.2770, 0.644, 1, "Europe"),  # value public: AppsFlyer puzzle Android CPI / US
    Country("FR", "France", 46.6, 2.2, 361_000, 0.3583, 0.324, 1, "Europe"),  # value public: AppsFlyer puzzle Android CPI / US
    Country("NL", "Netherlands", 52.1, 5.3, 93_000, 0.3155, 0.534, 1, "Europe"),  # value simulated: no public CPI, scaled by the Europe CPI ratio (0.667)
    Country("ES", "Spain", 40.5, -3.7, 146_000, 0.2725, 0.367, 1, "Europe"),  # value simulated: no public CPI, scaled by the Europe CPI ratio (0.667)
    Country("IT", "Italy", 41.9, 12.6, 126_000, 0.3804, 0.367, 1, "Europe"),  # value simulated: no public CPI, scaled by the Europe CPI ratio (0.667)
    Country("PL", "Poland", 51.9, 19.1, 111_000, 0.3896, 0.267, 1, "Europe"),  # value simulated: no public CPI, scaled by the Europe CPI ratio (0.667)
    Country("SE", "Sweden", 60.1, 18.6, 59_000, 0.5559, 0.534, 1, "Europe"),  # value simulated: no public CPI, scaled by the Europe CPI ratio (0.667)
    Country("TR", "Türkiye", 39.0, 35.2, 135_000, 0.2322, 0.133, 3, "Europe"),  # value simulated: no public CPI, scaled by the Europe CPI ratio (0.667)
    Country("ZA", "South Africa", -30.6, 22.9, 177_000, 0.1821, 0.081, 2, "Africa"),  # value simulated: no public CPI, scaled by the Latin America CPI ratio
    Country("IN", "India", 20.6, 78.9, 2_022_000, 0.0510, 0.021, 5, "Asia"),  # value public: AppsFlyer puzzle Android CPI / US
    Country("ID", "Indonesia", -0.8, 113.9, 452_000, 0.2128, 0.054, 7, "Asia"),  # value public: AppsFlyer puzzle Android CPI / US
    Country("PH", "Philippines", 12.9, 121.8, 380_000, 0.1371, 0.060, 8, "Asia"),  # value public: AppsFlyer puzzle Android CPI / US
    Country("JP", "Japan", 36.2, 138.3, 174_000, 0.5380, 0.499, 9, "Asia"),  # value public: AppsFlyer puzzle Android CPI / US
    Country("KR", "South Korea", 35.9, 127.8, 121_000, 0.3932, 0.388, 9, "Asia"),  # value simulated: no public CPI, scaled by the JP CPI ratio
    Country("AU", "Australia", -25.3, 133.8, 61_000, 0.6209, 0.944, 10, "Oceania"),  # value public: AppsFlyer puzzle Android CPI / US
]
C = len(COUNTRIES)
ISO_INDEX = {c.iso: i for i, c in enumerate(COUNTRIES)}

PLATFORMS = ["iOS", "Android"]
P = len(PLATFORMS)

# Advertiser categories: the "offer" is the ad. Games are the biggest group,
# but Freecash-style walls also carry apps, surveys and shopping cashback.
CATEGORIES = ["Casual games", "Strategy games", "Fintech apps", "Streaming apps", "Shopping cashback", "Surveys"]
CATEGORY_KEYS = ["casual", "strategy", "fintech", "streaming", "shopping", "surveys"]
K = len(CATEGORIES)

PAYOUT = np.array([3.0, 6.0, 14.0, 4.0, 2.0, 0.9])            # simulated: payout per completion (USD, US Android)
START_RATE = np.array([0.060, 0.040, 0.020, 0.050, 0.050, 0.080])  # simulated: starts per impression
COMPLETION_RATE = np.array([0.35, 0.22, 0.30, 0.45, 0.40, 0.70])   # simulated: completions per start
QUALITY = np.array([0.30, 0.38, 0.50, 0.40, 0.25, 0.60])          # simulated: valuable users per completion
REWARD_SHARE = 0.55            # simulated: share of advertiser payout that goes to the user
ACCEPTANCE = 0.97              # simulated: share of completions the advertiser confirms and pays
IMPRESSIONS_PER_ACTIVE = 6.0   # simulated: offer-wall impressions per active user-hour
NEW_ACCOUNT_STAY = 3           # simulated: a new account stays active for ~3 hours on day one
PLATFORM_COMPLETION = np.array([1.08, 0.95])  # simulated: iOS users finish slightly more often
# public (derived): iOS/Android payout multiplier = geometric mean of puzzle CPI ratios iOS/Android in US, DE, GB,
# CA, FR, AU (1.33-1.97), AppsFlyer Q3 2026. Android = 1.
PLATFORM_PAYOUT = np.array([1.538, 1.0])

# simulated: taste archetypes (life-signal based personas) and their affinity to each advertiser category.
ARCHETYPES = ["Puzzle commuter", "Competitive gamer", "Money-minded", "Binge watcher", "Bargain hunter", "Quick earner"]
ARCHETYPE_AFFINITY = np.array([
    # casual strategy fintech streaming shopping surveys
    [0.55, 0.10, 0.05, 0.10, 0.10, 0.10],
    [0.10, 0.60, 0.05, 0.15, 0.05, 0.05],
    [0.05, 0.05, 0.55, 0.05, 0.15, 0.15],
    [0.10, 0.05, 0.05, 0.60, 0.10, 0.10],
    [0.10, 0.05, 0.10, 0.10, 0.50, 0.15],
    [0.15, 0.05, 0.05, 0.05, 0.15, 0.55],
])
A = len(ARCHETYPES)

# Archetype mixture per country (invented, regionally plausible flavour).
# simulated: archetype mixture per region (invented). Order: puzzle commuter (casual), competitive gamer (strategy),
# money-minded (fintech), binge watcher (streaming), bargain hunter (shopping), quick earner (surveys).
# North America / Europe / Oceania lean streaming + fintech; Latin America / Africa / Asia lean casual games +
# surveys; Japan and South Korea have a strong strategy-games audience. See docs/ASSUMPTIONS.md.
_REGION_MIX = {
    "North America": [0.12, 0.12, 0.26, 0.30, 0.12, 0.08],
    "Europe": [0.14, 0.10, 0.22, 0.30, 0.14, 0.10],
    "Oceania": [0.14, 0.12, 0.22, 0.30, 0.14, 0.08],
    "Latin America": [0.32, 0.08, 0.05, 0.10, 0.10, 0.35],
    "Africa": [0.28, 0.06, 0.06, 0.10, 0.10, 0.40],
    "Asia": [0.32, 0.14, 0.05, 0.08, 0.08, 0.33],
}
_COUNTRY_MIX = {
    "JP": [0.20, 0.42, 0.06, 0.14, 0.08, 0.10],
    "KR": [0.18, 0.46, 0.06, 0.12, 0.08, 0.10],
}


def country_archetype_mix() -> np.ndarray:
    rng = np.random.default_rng(7)
    mix = np.zeros((C, A))
    for i, c in enumerate(COUNTRIES):
        base = np.array(_COUNTRY_MIX.get(c.iso, _REGION_MIX[c.region]))
        mix[i] = base * rng.uniform(0.8, 1.2, A)
        mix[i] /= mix[i].sum()
    return mix


# simulated: life signals collected at onboarding (the recommender's inputs).
SIGNALS = ["commutes daily", "plays console games", "reads finance news", "watches series weekly",
           "uses coupons", "student", "parent", "night owl", "follows esports", "travels often",
           "has a side hustle", "listens to podcasts"]
S = len(SIGNALS)
# simulated: probability that an archetype shows each life signal.
ARCHETYPE_SIGNALS = np.array([
    [0.80, 0.15, 0.20, 0.40, 0.30, 0.30, 0.40, 0.20, 0.05, 0.30, 0.20, 0.60],
    [0.30, 0.80, 0.10, 0.40, 0.10, 0.50, 0.15, 0.70, 0.75, 0.15, 0.15, 0.30],
    [0.50, 0.15, 0.85, 0.20, 0.35, 0.10, 0.40, 0.20, 0.05, 0.50, 0.55, 0.60],
    [0.30, 0.25, 0.10, 0.90, 0.25, 0.35, 0.35, 0.60, 0.15, 0.25, 0.10, 0.40],
    [0.35, 0.10, 0.25, 0.30, 0.90, 0.20, 0.60, 0.15, 0.05, 0.20, 0.35, 0.25],
    [0.40, 0.15, 0.15, 0.25, 0.40, 0.55, 0.25, 0.40, 0.05, 0.10, 0.60, 0.20],
])

HISTORY_HOURS = 14 * 24          # simulated: learning window of the twin

# public: calibration targets (Almedia release, 15 Oct 2026); used by tests and /api/meta
PUBLISHED_PAYOUT_SHARE = {"US": 0.579, "DE": 0.097, "GB": 0.082, "CA": 0.037, "FR": 0.032}
PUBLISHED_RUN_RATE_USD = 1_000_000_000
TARGET_REVENUE_DAY = PUBLISHED_RUN_RATE_USD / 365

# public: World Bank WDI 2024 (NY.GDP.PCAP.CD in current USD, SP.POP.TOTL), fetched 21 Nov 2026.
GDP_PC = {"US": 86170, "CA": 55016, "MX": 13988, "BR": 10311, "GB": 53341, "DE": 56104, "FR": 46103, "NL": 67465,
          "ES": 35327, "IT": 40430, "PL": 25104, "SE": 57223, "TR": 15893, "ZA": 6267, "IN": 2592, "ID": 4925,
          "PH": 3985, "JP": 33797, "KR": 36239, "AU": 64610, "AR": 13970}
POPULATION = {"US": 340_003_797, "CA": 41_262_329, "MX": 130_861_007, "BR": 211_998_573, "GB": 69_281_000,
              "DE": 83_516_593, "FR": 68_551_653, "NL": 17_993_485, "ES": 48_848_840, "IT": 58_952_704,
              "PL": 36_559_233, "SE": 10_569_709, "TR": 85_518_661, "ZA": 64_007_187, "IN": 1_450_935_791,
              "ID": 283_487_931, "PH": 115_843_670, "JP": 123_975_371, "KR": 51_751_065, "AU": 27_194_286,
              "AR": 45_696_159}

# simulated (hidden ground truth): how strongly offer starts respond to the user reward share, per country.
# Lower-income markets are more reward-sensitive. Starts scale as (share / 0.55) ** elasticity when a country's reward
# share is changed with a "set_reward" intervention. Off by default, so baseline data and calibration are unchanged.
def reward_elasticity_truth() -> np.ndarray:
    rng = np.random.default_rng(11)
    gdp = np.array([GDP_PC[c.iso] for c in COUNTRIES], dtype=float)
    return np.clip(1.0 + 0.5 * np.log(80_000 / gdp) + rng.normal(0, 0.15, C), 0.6, 3.5)
