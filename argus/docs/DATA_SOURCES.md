# Data sources

Public numbers used to calibrate the simulator (`backend/argus/config.py`). Everything not listed here is simulated
(see [ASSUMPTIONS.md](ASSUMPTIONS.md)). All pages were accessed on **21 Nov 2026**. The same list is served at
`GET /api/meta` (`backend/argus/meta.py`).

## 1. Almedia / Freecash company figures
Source: PPC Land, "Freecash passes $500M in user payouts as Almedia nears $1bn run rate", **15 Oct 2026** (company-supplied,
unaudited) — https://ppc.land/freecash-passes-500m-in-user-payouts-as-almedia-nears-1bn-run-rate/

| Figure | Value | Used for |
|---|---|---|
| Annual revenue run rate | $1B | Total simulated advertiser revenue ≈ $1B / 365 = **$2.74M/day** |
| Lifetime user payouts | > $500M | Context only |
| Monthly earners | > 9M | Sanity check: calibrated user scale totals ~8.9M |
| Users | ~95M | Context only |
| Countries and territories with payouts | 217 | Context (we simulate 20) |
| Lifetime payout share: United States | 57.9% | Share of simulated user rewards |
| Lifetime payout share: Germany | 9.7% | Share of simulated user rewards |
| Lifetime payout share: United Kingdom | 8.2% | Share of simulated user rewards |
| Lifetime payout share: Canada | 3.7% | Share of simulated user rewards |
| Lifetime payout share: France | 3.2% | Share of simulated user rewards |
| Top five combined | 82.7% | The other 15 simulated countries share the remaining 17.3% |

The run rate annualises an undisclosed period; the payout shares are lifetime and cover 217 countries, while our 20
countries take 100% of simulated rewards.

## 2. Mobile OS share by country (iOS share per country)
Source: StatCounter Global Stats, mobile OS market share, **October 2026** — https://gs.statcounter.com/os-market-share/mobile/
(per-country pages). All 20 values were fetched; none are approximate.

| Country | iOS | Android | Page |
|---|---|---|---|
| United States | 51.96% | 48.02% | https://gs.statcounter.com/os-market-share/mobile/united-states-of-america |
| Canada | 64.74% | 35.26% | https://gs.statcounter.com/os-market-share/mobile/canada |
| Mexico | 25.89% | 74.10% | https://gs.statcounter.com/os-market-share/mobile/mexico |
| Brazil | 26.23% | 73.76% | https://gs.statcounter.com/os-market-share/mobile/brazil |
| United Kingdom | 50.14% | 49.85% | https://gs.statcounter.com/os-market-share/mobile/united-kingdom |
| Germany | 27.70% | 72.29% | https://gs.statcounter.com/os-market-share/mobile/germany |
| France | 35.83% | 64.15% | https://gs.statcounter.com/os-market-share/mobile/france |
| Netherlands | 31.55% | 68.44% | https://gs.statcounter.com/os-market-share/mobile/netherlands |
| Spain | 27.25% | 72.75% | https://gs.statcounter.com/os-market-share/mobile/spain |
| Italy | 38.04% | 61.95% | https://gs.statcounter.com/os-market-share/mobile/italy |
| Poland | 38.96% | 61.02% | https://gs.statcounter.com/os-market-share/mobile/poland |
| Sweden | 55.59% | 44.40% | https://gs.statcounter.com/os-market-share/mobile/sweden |
| Türkiye | 23.22% | 76.78% | https://gs.statcounter.com/os-market-share/mobile/turkey |
| South Africa | 18.21% | 81.78% | https://gs.statcounter.com/os-market-share/mobile/south-africa |
| India | 5.10% | 94.84% | https://gs.statcounter.com/os-market-share/mobile/india |
| Indonesia | 21.28% | 78.68% | https://gs.statcounter.com/os-market-share/mobile/indonesia |
| Philippines | 13.71% | 86.28% | https://gs.statcounter.com/os-market-share/mobile/philippines |
| Japan | 53.80% | 46.18% | https://gs.statcounter.com/os-market-share/mobile/japan |
| South Korea | 39.32% | 60.67% | https://gs.statcounter.com/os-market-share/mobile/south-korea |
| Australia | 62.09% | 37.90% | https://gs.statcounter.com/os-market-share/mobile/australia |

## 3. Gaming CPI benchmarks (country payout level, iOS vs Android)
Source: AppsFlyer, gaming CPI benchmarks for **Q3 2026**, published **5 Nov 2026** — https://www.appsflyer.com/?p=549948
Puzzle is the genre with the widest country coverage, so it anchors the calibration.

| Country | Puzzle CPI Android | Puzzle CPI iOS | iOS / Android | Value factor (Android CPI / US) |
|---|---|---|---|---|
| United States | $4.81 | $9.48 | 1.97 | 1.000 |
| Germany | $3.10 | $4.23 | 1.36 | 0.644 |
| United Kingdom | $3.45 | $4.71 | 1.37 | 0.717 |
| Canada | $3.62 | $5.37 | 1.48 | 0.753 |
| France | $1.56 | $2.85 | 1.83 | 0.324 |
| Australia | $4.54 | $6.04 | 1.33 | 0.944 |
| Japan | $2.40 | — | — | 0.499 |
| Mexico | $0.44 | — | — | 0.091 |
| Brazil | $0.49 | — | — | 0.102 |
| Indonesia | $0.26 | — | — | 0.054 |
| Philippines | $0.29 | — | — | 0.060 |
| India | $0.10 | — | — | 0.021 |

Derived constants:
- **Country value factor** (`Country.value`): puzzle Android CPI relative to the US (table above).
- **iOS payout multiplier** (`PLATFORM_PAYOUT`): **1.538** = geometric mean of the six iOS/Android ratios (1.33–1.97).
- No published CPI for NL, ES, IT, PL, SE, TR, ZA, KR: their earlier simulated value is scaled by a regional ratio
  (Europe 0.667 from DE/GB/FR; South Africa by the Latin America ratio 0.322; South Korea by Japan's ratio 0.554).
  These are marked *simulated* in `config.py`.

## 4. Launch market (Enter a new country): Argentina
Mexico and Poland are already among the 20 watched countries, so the launch candidate is **Argentina**. It has the most
complete public data of the unwatched candidates we checked (Argentina, Colombia, Chile): a StatCounter country
page, World Bank income and population figures, and a clear language and region match with watched markets (Spanish:
Mexico, Spain; Latin America: Mexico, Brazil). AppsFlyer publishes no Argentina CPI, so its payout level is predicted
(GDP regression plus the neighbours' residual) rather than taken from data.

| Figure | Value | Source |
|---|---|---|
| iOS share of mobile OS, Argentina | 14.22% (Android 85.77%), October 2026 | StatCounter — https://gs.statcounter.com/os-market-share/mobile/argentina |
| GDP per capita, current USD, 2024 (all 20 watched + AR) | AR $13,970; MX $13,988; BR $10,311; ES $35,327; TR $15,893; US $86,170; … (full list in `backend/argus/config.py`, `GDP_PC`) | World Bank WDI, `NY.GDP.PCAP.CD` — https://api.worldbank.org/v2/country/ARG/indicator/NY.GDP.PCAP.CD?date=2024 |
| Population, 2024 | AR 45,696,159; … (`config.POPULATION`) | World Bank WDI, `SP.POP.TOTL` — https://api.worldbank.org/v2/country/ARG/indicator/SP.POP.TOTL?date=2024 |
| Main language | es (AR, MX, ES), pt (BR), … (`LANGUAGE`) | Public knowledge |

Similarity uses only these public features (income, Android share, region, language). Population also sets the
default (assumed) user count. The fraud history of the watched countries (`FARMS_PER_QUARTER`) and the per-country
reward response are **simulated**.

## 5. Calibration result (seed 42, last 7 days of history)

| Country | Share of rewards (sim) | Published payout share |
|---|---|---|
| United States | 57.88% | 57.9% |
| Canada | 3.71% | 3.7% |
| Mexico | 1.24% | — (share of remaining 17.3%) |
| Brazil | 1.88% | — (share of remaining 17.3%) |
| United Kingdom | 8.19% | 8.2% |
| Germany | 9.69% | 9.7% |
| France | 3.20% | 3.2% |
| Netherlands | 1.34% | — (share of remaining 17.3%) |
| Spain | 1.41% | — (share of remaining 17.3%) |
| Italy | 1.29% | — (share of remaining 17.3%) |
| Poland | 0.84% | — (share of remaining 17.3%) |
| Sweden | 0.99% | — (share of remaining 17.3%) |
| Türkiye | 0.46% | — (share of remaining 17.3%) |
| South Africa | 0.32% | — (share of remaining 17.3%) |
| India | 0.86% | — (share of remaining 17.3%) |
| Indonesia | 0.55% | — (share of remaining 17.3%) |
| Philippines | 0.49% | — (share of remaining 17.3%) |
| Japan | 2.49% | — (share of remaining 17.3%) |
| South Korea | 1.27% | — (share of remaining 17.3%) |
| Australia | 1.88% | — (share of remaining 17.3%) |

Revenue: **$2,740,947/day** simulated vs $2,739,726/day from the published run rate.
