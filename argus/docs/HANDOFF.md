# Argus: build hand-off (for Claude Code / CLI)

Read this first. It records the concept and design decisions made during the brainstorming chat, what is already built, and what to build next. Deadline: **Sunday 22 Nov 2026, 16:30** (HackLab #2 Berlin, Almedia track). Submit a 3-min video, this repo (public) and slides.

## 1. Concept in one paragraph
Argus is a self-driving analyst for a Freecash-like rewarded-ad marketplace. Advertisers (mostly mobile games, also fintech, streaming, shopping cashback, surveys) pay per completed offer; users get a share as reward; Almedia keeps the margin. Argus lives inside a **hierarchical, causal digital twin** of that business. It watches every metric, finds the broken link in the causal graph, proves the cause with a counterfactual in the twin, tests fixes in the twin (fast-forward 7 days) before recommending them, applies the approved fix, verifies its prediction, and reports to the CEO / manager / engineer at their level of abstraction, all on a 3D globe.

Pillars: (1) twin as sandbox: simulation-first validation, what-if, counterfactual, fast-forward; (2) causal graph: the twin is an executable structural causal model, so symptom vs root cause and Shapley contributions are possible; (3) "Abstraction is All You Need": L1 business / L2 segment / L3 users, descend only where impact × uncertainty is high, with audience levels matching the fidelity levels; (4) taste recommender: what to advertise to whom, and **bot detection, because real people have coherent tastes and bots don't**.

## 2. Hard design rule
The simulator ("reality", `backend/argus/reality.py`) and the twin (`twin.py`) are **separate**. Reality holds hidden ground truth (bot farm, tracking break, data delay). The twin only sees the observed hourly metric stream, unlabeled user samples and the public marketing calendar, and learns its parameters from 14 days of history. Never let the twin or agent read `Reality.hidden`, `farms`, `breaks` or `_bot` labels (only tests may).

## 3. Demo script (what the judges must see)
1. Globe in **Health** mode: 20 countries green, "watching 20 countries × 6 KPIs".
2. Inject **planned promo** (Weekend Boost, Germany, games): a naive detector would alert (rewards +30%), but Argus logs "explained by planned event" (grey note on DE).
3. Inject **bot farm** (Canada, Android). Within ~3–6 sim-hours CA turns amber/red. Note that revenue goes *up* at first, so a naive monitor reads it as growth; the tell is quality per completion falling and new accounts surging.
4. Incident panel: trail of decisions → L1 (CA rewards/quality z) → L2 breakdown (Android, fintech + strategy games) → causal graph walk (root = new accounts; margin/revenue are symptoms) → L3 run (taste coherence scatter: a cluster of new, emulator-device, payout-focused accounts with coherence ~0.28 vs ~0.73) → counterfactual: "removing the cluster explains 9x% of the change: **supported**"; alternatives (calendar, data gap, tracking) rejected.
5. Fixes tested in the twin, 7-day fan charts: do nothing / block cluster / verify new Android accounts / cap rewards. Shows the advertiser chargeback (24 h) and budget cut (48 h) risk of doing nothing.
6. Human clicks **Approve** → applied to reality → CA turns green → after 24 h Argus shows "prediction verified (actual within band)".
7. **Add a new metric live** ("reward cost per completed offer = rewards / completions"): LLM proposes the formula and parents, validator checks names, identity and aggregation (ratio, so non-additive across countries), node appears in the graph and gets monitored.
8. Switch audience tabs CEO / Manager / Engineer. Switch globe to **Potential** mode: taste-model demand vs current feed allocation → "+$X/day if the feed matched local taste" per country.
9. Optional extra incident: **tracking break** (UK, iOS, games: advertiser confirms only 15% of completions). Root = revenue mechanism (acceptance); no L3 run needed (impact × uncertainty low, adaptive abstraction); fixes = pause offers / notify advertiser / both.
10. Optional: **data delay** (one country delivers 45% of rows): Argus says "waiting for data", no alert, backfill two hours later.

## 4. What exists (backend/argus)
- `config.py`: 20 countries (DAU, iOS share, value factor, timezone), 2 platforms, 6 advertiser categories (payout, start rate, completion rate, quality), 6 taste archetypes, 12 life signals.
- `reality.py`: hourly simulator. Nodes per (country, platform): new_accounts, active_users; per (country, platform, category): impressions, starts, completions, revenue, rewards, quality. Includes daily rhythm and weekend effect, planned events, bot farm (ramps 320 accounts over 10 h, always online, fintech + strategy, quality ~0), advertiser reaction (chargebacks after 24 h of >20% fraud share, budget cut after 48 h), tracking break, data delay with backfill, interventions (`block_cluster`, `verify_new_accounts`, `cap_rewards`, `pause_offers`, `notify_advertiser`), and `sample_users()` for unlabeled L3 records (bots use scripted onboarding templates and emulator devices). Sanity numbers: ~854k completions/day, ~$1.58M revenue/day, margin ~43%.
  > **Note (21 Nov 2026):** these sanity numbers predate the calibration to public data. The simulator now runs at ~$2.74M revenue/day (Almedia's $1B run rate / 365) with country payout shares, iOS shares and payout levels taken from public sources; margin is still ~43%. See [DATA_SOURCES.md](DATA_SOURCES.md) and [ASSUMPTIONS.md](ASSUMPTIONS.md).
- `taste.py`: `TasteModel` (ridge regression life signals → category affinity, trained on its own synthetic population); `score_users()` gives coherence, payout focus and a fraud cluster (seed rule + expansion over shared device fingerprints). Tested: after 14 h of a farm, 85/88 bots caught with 1 false positive; clean country flags 1/600.
- `graph.py`: `MetricGraph` with typed nodes and edges (root / mechanism / formula, exogenous calendar), `parse_expression` and `evaluate` for safe metric formulas.

## 5. To build next
### twin.py (Argus's beliefs)
- `learn(history)`: from 14 days of observed history. Per (c,p) seasonal baselines indexed by local hour × weekend flag: new_accounts, returning = active − 3 × (new over the last 3 h). Per segment ratio μ = Σchild/Σparent for impressions/active, starts/impressions, completions/starts, revenue/completions, rewards/completions, quality/completions; σ_log = std of hourly log(child / (parent·μ)).
- Calendar multipliers from planned events (active_mult, start_mult, reward_share / 0.55).
- `forecast(t)`: marginal expectation of every node (roots from baselines, chain through μ).
- `given_parents(window)`: z per mechanism over a 3 h window, with expected = Σ parent_t·μ_t and var = Σ (parent_t·μ_t·σ_log)².
- L1 residual σ per (country, KPI) from an in-sample backtest of 3 h window sums (relative error std).
- `chain(values, observed_mask)` over the window for one (c,p): new → active (= returning + 3·new) → impressions → starts → completions → revenue / rewards / quality → margin. Exact **Shapley** over the 8 mechanisms (2^8 evaluations). Counterfactual for hypothesis H = v(all) − v(all \ H); explained share = that / (v(all) − v(none)).
- `fast_forward(country, option, hours=168, n=40)`: Monte Carlo. Baseline forecast + attack excess (obs − forecast over the last 6 h, growth U(1.2, 2.5) over 12 h), advertiser-reaction rule (chargeback ~24 ± 6 h, budget cut ~48 ± 12 h). Fix effects: block (excess → 0, 30% chance the farm returns at 50% after 24–48 h); verify (excess × 0.05, human new accounts × 0.88); cap rewards (excess × 0.4, reward share 0.35 on strategy + fintech, human starts × 0.85). Tracking-break options: none / notify (fixed after U(12, 30) h) / pause / pause + notify. Return daily p10/p50/p90 of cumulative margin plus delta vs do-nothing.

### agent.py (the loop, every sim hour)
1. Data check: completeness < 0.9 → "waiting for data", no alert.
2. Watch all countries × KPIs (new_accounts, completions, revenue, rewards, margin, quality_per_completion) against the twin forecast; alert on |z| ≥ 4 for 2 consecutive hours, prioritised by business impact. Also run a naive detector (same hour, no calendar): naive fires but twin quiet and event active → "explained change" log.
3. Localise: L2 breakdown of the KPI deviation by platform × category (cover 80% of |Δ|).
4. Walk the DAG upstream for the top segment: z given parents per mechanism; root = most upstream |z| > 3; symptoms = deviating marginally but normal given parents.
5. Impact × uncertainty high and root at new_accounts / active → run L3 (`reality.sample_users` + `TasteModel.score_users`) → fraud-cluster hypothesis.
6. Hypotheses with explained share; "supported" if ≥ 0.8; others listed as rejected or partial.
7. Fixes via `fast_forward`; recommend the best p50 with acceptable p10.
8. Reports for CEO (L1, money, one paragraph), manager (segment, campaign, action), engineer (mechanism z, query trail). LLM optional via an OpenAI-compatible env (`LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`; Nebius Token Factory credits available), with deterministic templates as fallback.
9. Issue tracking: fingerprint (country, platform, root) → open / persisting / fix_applied / verified / resolved; no duplicates.
10. On approve: `reality.apply(...)`; store the prediction band; after 24 h compare actual and record "verified" or "missed → recalibrated".
11. Extension: `POST /api/metrics {name, description}`. LLM maps it to a formula over known nodes (fallback: keyword rules), then `parse_expression` → numeric identity check on history → aggregation type → add node → monitored.

### api.py (FastAPI) + one container
Session-scoped worlds (header `X-Session`, LRU ~50, 1 h expiry). Endpoints: `GET /api/state` (globe: per-country health z, status, potential, incidents, explained changes, clock), `POST /api/step {hours}`, `POST /api/reset`, `POST /api/inject {type, country, platform}`, `GET /api/graph`, `GET /api/incidents`, `GET /api/incidents/{id}`, `POST /api/incidents/{id}/approve {fix}`, `POST /api/metrics`, `GET /api/country/{iso}`, `GET /api/potential`. Serve the built frontend from `/`. Dockerfile: node build stage + python runtime; deploy to Hugging Face Spaces (Docker) or Render.

### frontend (Vite + React + TS + MapLibre globe; reuse from github.com/ETohidi/AlmediaHackathon `src/components/WorldMap.tsx`)
Top bar: clock, play / pause / +1 h / +6 h, mode (Health | Potential), demo controls (inject promo / bot farm / tracking break / data delay, add metric). Globe: country fill by health or potential; pulsing incident markers; grey "explained" markers. Right panel: incident feed → incident detail with audience tabs (CEO / Manager / Engineer), decision trail, causal graph SVG (broken mechanism red, propagation path to the KPI), Shapley bar chart, hypotheses with explained share, L3 scatter (coherence vs payout focus, cluster red), fix fan charts, Approve button, verification badge. Country click → L2 heatmap (platform × category deviation).

## 6. Run the existing pieces
```bash
cd backend
pip install numpy fastapi "uvicorn[standard]" httpx pytest
python -c "from argus.reality import Reality; r=Reality(); r.run(336); print(sum(x['revenue'].sum() for x in r.true[-24:]))"
```
