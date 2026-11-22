# Simulation assumptions

Argus runs against a simulated marketplace (`backend/argus/reality.py`). Scale, country mix, platform split and payout
levels are calibrated to public data; everything else is invented. No private Almedia data is used. The assumptions that shape what the demo shows:

## Marketplace
- 20 countries, 2 platforms, 6 advertiser categories; ~43% contribution margin.
- **Calibrated to public data** (details and sources in [DATA_SOURCES.md](DATA_SOURCES.md)): ~$2.74M advertiser revenue
  per day (= Almedia's $1B run rate / 365); share of user rewards by country matches published lifetime payout shares
  (US 57.9%, DE 9.7%, GB 8.2%, CA 3.7%, FR 3.2%); iOS share per country from StatCounter (Oct 2026); payout level per
  country and an iOS payout premium (×1.54) from AppsFlyer puzzle-game CPI benchmarks (Q3 2026).
- Users get 55% of the advertiser payout as reward; advertisers confirm 97% of completions.
- Hourly noise: lognormal 3% plus Poisson-like counting noise on impressions, starts and completions; 6% on sign-ups.
- **Offer mix:** payout per completion varies by offer within a category (lognormal σ = 0.06 per segment-hour), and the
  share of completions advertisers confirm wobbles (σ = 0.015). Revenue and rewards per completion are therefore never
  exactly constant, so counterfactual explanations land below 100%.

## Regional taste
- Six taste archetypes (puzzle commuter, competitive gamer, money-minded, binge watcher, bargain hunter, quick earner)
  map to the six advertiser categories. Each region has its own archetype mix (`config._REGION_MIX`), ±20% per country:
  - North America, Europe, Oceania lean **streaming + fintech** (binge watchers ~30%, money-minded ~22–26%);
  - Latin America, Africa, Asia lean **casual games + surveys** (puzzle commuters ~30%, quick earners ~33–40%);
  - Japan and South Korea have a strong **strategy-games** audience (competitive gamers 42–46%).
- Users reveal their archetype only through 12 onboarding life signals; the taste model (ridge regression trained on a
  separate synthetic population) turns signals into category affinity. Its predictions are flatter than the truth
  (shrinkage), but keep the regional ordering: e.g. strategy-games demand index 1.4–1.5 in JP/KR.

## Production feed (Potential mode)
- **The simulated production feed is payout-heavy:** offer impressions per category ∝ local taste × payout^1.1
  (`alloc ∝ pref · payout^1.1`). This is a modelling choice for the demo, not a claim about Freecash's real feed.
- Start rate responds to taste fit: `start_rate ∝ (taste share / feed share)^0.6`, clipped to [0.4, 2.0].
- Argus's twin does not know the true taste shares. It estimates local demand from the taste model applied to 300
  sampled users per country, assumes the same elasticity (0.6) without the clipping, and searches feeds that blend the
  current allocation with that demand. The best one gives `potential_usd_day` / `potential_pct`.
- `top_categories`: categories the taste-matched feed would grow (positive margin gain), ranked by how distinctive local
  demand is (`demand_index` = local / global demand). Result: streaming in North America, Western Europe and Australia;
  surveys and shopping cashback in Latin America, Africa and South-East Asia; streaming and casual games in Japan and
  Korea (strategy games are already over-served there by the payout-heavy feed, despite the highest demand index).
- Validation (seed 42): the twin shows **+2.3–3.4%** margin per country; re-running the simulator with the twin's
  proposed feed yields **+5.6–8.2%**. The twin is conservative because it does not know about the clipping and its
  taste model is flatter than the truth.

## Incidents
- **Bot farm:** ramps to 320 accounts over 10 h (the `bot_farm` demo scene uses 8 h: it starts at Mon 03:00 UTC, near
  Canada's evening sign-up peak, where a 10 h ramp is detected at 07:00 instead of 06:00; the detector is unchanged); bots are always online, complete fintech and strategy-game offers,
  deliver ~no advertiser value, use emulator devices and scripted onboarding answers.
- **Advertiser reaction:** if fake completions exceed 20% of a country × category for 24 h, advertisers charge back half
  the revenue; after 48 h they cut budget (impressions × 0.4).
- **Tracking break:** advertisers confirm only 15% of completions in the affected segment until fixed (~18 h after
  notification).
- **Data delay:** one country delivers 45% of rows for one hour; backfilled two hours later.
- **Fix effects in the twin:** block (fake activity → 0, 30% chance the farm returns at half strength after 24–48 h),
  verify new accounts (fake activity × 0.05, genuine sign-ups × 0.88), cap rewards (fake activity × 0.4, reward share
  0.35 on strategy + fintech, genuine starts × 0.85). The attack trajectory is extrapolated from excess sign-ups.

## Alerting
- L1 watch: 20 countries × 6 KPIs (plus added metrics), 3 h windows, z against the twin forecast with backtest σ
  (inflated 1.3× for out-of-sample use).
- Amber ("watch") at |z| ≥ 4 for one hour; an incident needs 2+ hours and impact > $150/h, or |z| ≥ 8.

## Enter a new country (launch forecast)

`backend/argus/launch.py`, `POST /api/what_if/launch`. Argus predicts **per-user economics**. The user count is an
input; by default it is **assumed** from the similar countries' daily users per capita × population.
- **Similarity** (public features): distance = |log GDP ratio| / 0.5 + |iOS share difference| / 0.15 + 1 if the region
  differs + 0.4 / 0.8 if the language differs (same family / other). The 3 nearest watched countries are weighted ∝ e^−distance.
- **Taste profile:** the taste model scores 600 sampled users per platform in each neighbour; the launch market gets the
  weighted mix, which is reported as `taste`.
- **Offers:** completions per active hour for each platform × category come from the neighbours' observed data. Their
  taste and feed are already in those numbers.
- **Payout level:** log value ~ log GDP per capita across the watched countries, plus the neighbours' residual.
- **Active hours per daily user:** taken from the neighbours.
- **Uncertainty:** the spread of payout level, active hours and reward elasticity is the leave-one-out error of each
  transfer across the watched pool. Offer rates carry the neighbours' spread. 400 Monte Carlo paths.
- **Reward response (simulated, hidden ground truth):**
  - Changing a country's reward share scales its offer starts by (share / 55%)^e. Here e = 1 + 0.5·ln(80,000 / GDP
    per capita) + noise, clipped to 0.6–3.5, so lower-income markets are more reward-sensitive.
  - It only applies under a `set_reward` change, so the baseline data, calibration and incident demos are unchanged.
  - Argus sees only "past reward tests": e measured with 8% noise per watched country.
  - Longer-term loyalty effects of lower rewards are not modelled.
- **Simulated:**
  - Users build up as 1 − e^(−day/7), so day 28 is ~98% of the user count.
  - Fraud history per watched country is invented. A farm costs ~2 days of the country's margin.
- **User count doesn't transfer:** in the simulator, per-country user scale comes from calibration to published payout
  shares. That's why it is an input or labelled assumption, not a prediction.
