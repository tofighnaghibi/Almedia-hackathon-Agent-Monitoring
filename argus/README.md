# Argus

**A self-driving analyst that lives inside a causal digital twin of a rewarded-ad marketplace.**
HackLab #2 Berlin Robotics & AI Hackathon, Almedia track ("Eye of the System"), 21–22 Nov 2026.

Rewarded-ad marketplaces like Freecash move fast: a bot farm looks like growth, a broken advertiser postback looks
like a dip, a planned promo looks like an anomaly. Argus watches 20 countries × 6 KPIs every hour against a digital
twin that learned the business from 14 days of data. When something breaks, it localises the problem (country →
platform × offer category → individual users), walks the causal graph to separate root cause from symptoms, **proves
the cause with a counterfactual in the twin**, fast-forwards every candidate fix 7 days before touching anything,
recommends one, and — after a human clicks Approve — checks 24 h later whether its prediction came true. Each
incident is explained three ways: a plain issue card for the CEO, an action plan for the manager, and the
z-scores, Shapley contributions and query trail for the engineer.

**Try it live: https://argus-cmlk.onrender.com** (first load may take ~50 s on the free tier while the container wakes up).

## Architecture

```mermaid
flowchart TB
    R["Reality (simulator)"]
    subgraph T["Twin"]
        direction LR
        L1["L1 Business"] --> L2["L2 Segment"] --> L3["L3 Users"]
    end
    subgraph A["Agent loop"]
        direction LR
        D["Detect"] --> LO["Localise"] --> P["Prove cause"] --> F["Test fixes"] --> AP["Approve"] --> V["Verify"]
    end
    W["Views: globe · issue card · evidence"]
    R -->|hourly metrics| T
    T --> A
    A --> W
```

Approved fixes are applied back to Reality, and the next hours of data show whether they worked.

Design notes and the original build plan: [`docs/HANDOFF.md`](docs/HANDOFF.md); API contract: [`docs/API.md`](docs/API.md).

Argus never reads the simulator's hidden state (bot labels, farms, breaks); a test enforces it. It sees only the
observed metric stream, the public marketing calendar, unlabeled user samples and the actions it applied.

## What is real vs simulated

- **Real (public data, cited):** scale ($1B run rate → $2.74M revenue/day), each country's share of user payouts
  (US 57.9%, DE 9.7%, GB 8.2%, CA 3.7%, FR 3.2%), iOS share per country (StatCounter, Oct 2026), country payout
  levels and the iOS premium (AppsFlyer CPI benchmarks, Q3 2026). See [`docs/DATA_SOURCES.md`](docs/DATA_SOURCES.md)
  and the in-app Data panel (`GET /api/meta`).
- **Simulated:** offer categories and conversion rates, taste archetypes and regional mixes, the production feed,
  noise, the incidents, advertiser reactions and fix effects. See [`docs/ASSUMPTIONS.md`](docs/ASSUMPTIONS.md).
- No private Almedia data is used.

## How Argus answers the Almedia brief

Track: **"Eye of the System: Fully Agentic Monitoring"**: an autonomous analyst that holds a graph of business
metrics and runs watch → detect → investigate → explain on its own.

| Brief component | What Argus does | Where to see it |
|---|---|---|
| **1. Metric graph** | 10 core metrics plus added ones. Four typed edges, each used for a different kind of reasoning: **formula** (margin = revenue − rewards) → exact decomposition; **breakdown** (country → platform → offer category) → find the segment; **possible cause** (new accounts → active users → impressions → starts → completions → revenue / rewards / quality) → root cause given parents; **known event** (marketing calendar) → explain innocent changes. A tree view lists outcomes, their formula inputs and their causes. | Graph view; `GET /api/graph`, `GET /api/graph/tree` |
| **2. Automatic extension** | Type `margin_per_signup = margin / signups`. Argus maps words to metrics (`signups` → new accounts), refuses to guess when a word is **ambiguous** (`users`: active or new?), rejects unknown names with "did you mean", checks the **units** (USD per new account; USD + completions is refused), checks it **computes on 14 days of data** at every breakdown level, works out how it **aggregates** (ratio of sums), refuses **duplicates** (same formula, or the same values on the data), **places** it under its inputs with the breakdowns they share, **starts monitoring** it with a seasonal baseline, and writes an "Added because…" note. Nobody places it by hand. | Add-metric flow; `POST /api/metrics` |
| **3. Continuous monitoring** | Every simulated hour: 20 countries × 6 KPIs (+ added metrics) against the twin's baseline by **hour of day × weekday/weekend** per country and platform. **Data freshness/completeness**: a country delivering < 90% of rows is "waiting for data", never an alert; the backfill is logged. **Planned events**: the calendar is part of the forecast, and a naive detector runs alongside, so a promo shows as "explained" instead of an alarm. | Globe statuses; Germany promo; data delay |
| **4. Autonomous investigation** | Opens the breakdown (platform × offer category covering 80% of the change), walks possible-cause edges upstream testing each link **given its parents**, separates the **first cause** from **symptoms**, quantifies contributions (**exact Shapley** over the mechanisms, plus segment shares), checks **calendar** and **data gaps** as rival explanations, descends to individual users only when impact × uncertainty is high, compares **five competing explanations** by counterfactual replay, and stops when one explains ≥ 80% and the others are rejected. | Decision trail and evidence view of an issue |
| **5. Communication and state** | Each alert carries numbers (how it looks vs what is at stake), the investigation trail, business impact over 7 days, and remaining uncertainty (explained share, p10–p90 bands, "proven / likely / unclear"). Reports for CEO, manager and engineer. Issues are tracked **Open → Still going → Fix applied → Resolved**; later alerts for the same problem are merged (`alerts_merged`), a problem that disappears on its own resolves itself, and a returning one links to the earlier issue. | Issue card, status line, `GET /api/state` → incidents |

**The three demo moments** (all autonomous after the click):

| Moment | Scene | What Argus does on its own |
|---|---|---|
| **Inject a FAULT** | `bot_farm` (also `tracking_break`) | Sign-ups in Canada (Android) leave the normal zone at 04:00 while revenue *rises*; at 06:00 it opens a high-severity issue, finds the root (new accounts), checks 600 users (fake-account cluster on 2 emulators), proves it (99% explained), rejects four alternatives, tests four fixes 7 days ahead and recommends one |
| **Inject an INNOCENT CHANGE** | `promo` (also `data_delay`) | Germany's Weekend Boost moves rewards; the naive detector would alert, Argus marks it "explained by the calendar". A late data delivery is "waiting for data", not an alert |
| **ADD A NEW METRIC** | `new_metric` | `margin_per_signup = margin / signups` is resolved, validated, placed under margin and new accounts, and monitored in 20 countries, with an "Added because…" note; `margin / users` gets a clear "ambiguous, pick one" answer |

**How it decides** (the questions the judges ask):

- **What to watch, at which granularity.** Always the cheap level: every country × 6 business KPIs (+ added metrics),
  hourly, in 3-hour windows, as z-scores against the seasonal twin forecast with backtest uncertainty by time of day.
  Amber at |z| ≥ 4 for one hour; an issue only if it persists 2 h *and* matters (impact > $150/h, or |z| ≥ 8).
  Finer levels (platform × offer category, then users) are opened only for countries that alert.
- **Which edges to follow.** Known-event edges first (is a planned event explaining it?), then breakdown edges to
  localise, then possible-cause edges upstream from the alerting KPI inside the top segment, testing each link given
  its observed parents. Formula edges are used to decompose the money change exactly (Shapley over the mechanisms).
- **Which segments to open.** The platform × offer-category segments that together carry 80% of the absolute money
  deviation (when sign-ups alert, the platform with the strongest sign-up signal). Individual users only when the
  root is on the user side and impact ≥ $50/h: impact × uncertainty.
- **When the evidence is enough.** When one hypothesis explains ≥ 80% of the change in a counterfactual replay and the
  alternatives are rejected (< 30%); a fraud verdict also needs the user-level cluster. Otherwise it is reported as
  "likely" or "unclear" and Argus keeps watching. After a fix, it checks 24 h later whether its prediction held.

**Limits worth stating:** the causal graph is specified from how the business works (its parameters are learned);
issues are kept apart by cause family per country and platform (users, revenue confirmation, reward pricing), so two
different problems of the same family on the same platform and country at once would be merged; the marketplace and
its faults are simulated (see below).

## Technical approach

Plain numpy and a ridge regression: no deep learning, no learned causal structure. The graph is specified from how the
business works; the twin learns its parameters from 14 days of observed data. Code paths are in `backend/argus/`.

| Method | What it means here | Where |
|---|---|---|
| **Autonomous agent with a causal world model** | Every simulated hour the agent observes, detects, investigates and recommends, using the twin as its model of the business | `agent.py` `Agent.on_hour`, `twin.py` `Twin` |
| **Structural causal model (SCM)** | Hand-specified DAG: new accounts → active users → impressions → starts → completions → revenue / rewards / quality; each mechanism is a learned ratio with log-normal noise, plus calendar and action effects | `graph.py` `MetricGraph`, `twin.py` `Twin.learn`, `Twin.factors` |
| **Causal root-cause analysis (residual given parents)** | Each mechanism's observed child is compared with its expectation *given its observed parents*; the root is the most upstream mechanism with abs(z) > 3, downstream deviations that are normal given their parents are symptoms | `twin.py` `Twin.given_parents`, `agent.py` `Agent.investigate` (DAG walk) |
| **Counterfactual simulation / interventions (do-operator)** | Proof: replay the window with do(cause mechanisms := twin baseline) while every other mechanism keeps its observed residual; explained share = removed change / total change (absolute) | `twin.py` `Twin.chain`, `Twin.explained_share`, `agent.py` `Agent.counterfactual` |
| **Exact Shapley-value attribution** | Δmargin split across the 8 mechanisms of the root segment by evaluating all 2^8 coalitions | `twin.py` `Twin.shapley` |
| **Monte Carlo scenario rollouts (simulation-in-the-loop)** | Every candidate fix is run 7 days ahead, 40 paths each, with common random numbers so options are compared on the same futures; includes the advertiser-reaction model | `twin.py` `Twin.fast_forward`, `agent.py` `Agent._fixes` |
| **Uncertainty quantification, prediction intervals** | Leave-one-out backtest σ per country × KPI × time of day (inflated ×1.3 out of sample); ±2σ normal-zone bands; p10/p50/p90 for every fix and for paired deltas vs doing nothing | `twin.py` `Twin.learn` (`l1_sigma`), `agent.py` `Agent.series`, `Twin.fast_forward` |
| **Multi-fidelity hierarchical digital twin, adaptive fidelity** | L1 business (country KPIs), L2 segment (platform × offer category mechanisms), L3 users (on demand). The agent descends to L3 only when impact × uncertainty is high: root on the user side and impact ≥ $50/h | `twin.py` `Twin.l1_z` / `given_parents`, `taste.py`, `agent.py` `Agent.investigate` |
| **Closed-loop self-calibration** | 24 h after an approved fix, actual margin is compared with the predicted band; a miss widens that country's uncertainty (×1.2) | `agent.py` `Agent._verify` |
| **Human-on-the-loop approval** | Argus recommends; nothing touches the marketplace until a person approves a fix | `agent.py` `Agent.approve`, `POST /api/incidents/{id}/approve` |
| **Agent trace** | Every investigation records its steps (L1 → L2 → L3 → hypotheses → fixes → approval → verification) | incident `trail`, `GET /api/timeline` |
| **Grounded generation** | The LLM only rewrites tool results: it receives the facts as JSON, and every number in its output is checked against them; any unmatched number → deterministic template instead | `llm.py`, `agent.py` `Agent._llm_reports`, `ungrounded_numbers` |
| **Evaluation harness** | Seeds × injected faults (promo, bot farm, tracking break, data delay); detection latency, explained share, recommended fix, verification, false alarms in clean weeks | `argus/evaluate.py` (`python -m argus.evaluate`), `tests/test_e2e.py` |
| **Tool interface for other agents (MCP)** | The twin's state, explanations, fix rollouts and sandboxed what-ifs as Model Context Protocol tools (stdio, JSON-RPC) | `argus/mcp_server.py` (`python -m argus.mcp_server`) |
| **Taste coherence as a behavioural-consistency anomaly signal** | Real people's completions match the tastes their life signals predict; fake accounts don't. Low coherence + payout focus + shared devices form the fraud cluster | `taste.py` `TasteModel.score_users` |

Latest harness run (seeds 42, 1–8): bot farm detected 9/9 in 3–4 h, 88–99% explained, all predictions verified;
tracking break 9/9 in 3–4 h; 0 false incidents in 9 clean weeks.

## Demo scenes

The **Demo** menu in the top bar (or `POST /api/demo/{scene}`) jumps a fixed-seed world to each part of the story;
calling a later scene first runs the earlier ones.

| Scene | What happens |
|---|---|
| `start` | Fresh world after 14 days of history; the globe is green, 20 countries × 6 KPIs watched |
| `promo` | Planned "Weekend Boost" in Germany: a naive detector alerts, Argus logs it as explained by the calendar |
| `bot_farm` | Fake accounts on Android in Canada from Mon 03:00: revenue goes *up*; sign-ups leave the normal zone at 04:00 and Argus opens a high-severity incident at 06:00, three hours in |
| `investigate` | Decision trail: country → Android × fintech/strategy → root cause new accounts → user-level taste check → counterfactual (~94% explained) |
| `approve` | Human approves the recommended fix (verify new accounts); Canada returns to green within ~2 h |
| `verify` | 24 h later: actual margin lands inside the predicted band → "Prediction verified" |
| `new_metric` | `margin_per_signup = margin / signups` is resolved, validated (units, data, aggregation), placed in the graph and monitored, with an "Added because…" note |
| `tracking_break` | Advertisers stop confirming UK iOS game completions: root cause revenue, no user-level run needed, pause + notify |

**Enter a new country** (`POST /api/what_if/launch {"country": "AR", "users": optional}`, last 15 s of the video):
- **Prediction for Argentina** (not among the 20 watched): about **$25 margin per 1,000 users a day** (p10–p90 $18–$36).
  Learned from Mexico, Brazil and Türkiye.
- **Recommendations:** an offer mix of casual games, streaming and fintech, and a 65% reward share.
- **Fraud risk:** medium.
- **User count:** an input. By default it is *assumed* from the similar countries, which gives about 176k daily users
  and about +$4.3k a day after 4 weeks.
- **Backtest:** each of the 20 watched countries is hidden in turn and predicted from the other 19, then checked against
  the simulator's true next 4 weeks.

  | Check | Argus | Global average (same ranking everywhere) |
  |---|---|---|
  | Best offers: true top category in Argus's top 3 | 20 of 20 | 18 of 20 |
  | Best offers: true top category ranked first | 15 of 20 | 11 of 20 |
  | Margin per user | median error 24%, 17 of 20 inside p10–p90 | — |
  | Best reward level within one 5-point step of the true best | 19 of 20 (exact in 10) | — |

Keyboard shortcuts (**coming**): `1`–`8` jump to the scenes above and `Space` plays/pauses. They arrive with the
frontend redesign currently in progress; until then the Demo menu and the top-bar controls (play, +1 h, +6 h, inject,
add metric) drive everything.

## Honest limitations

- **The marketplace is simulated.** Scale, country mix, platform split and payout levels are calibrated to public
  figures; conversion rates, taste, incidents and advertiser behaviour are invented. Numbers are illustrative, not
  forecasts for Almedia.
- **Incidents are injected**, and the twin was designed with these failure modes in mind; unknown failure types would
  surface as "anomaly, cause unclear" rather than a confident diagnosis.
- **Fix effects in the twin are assumptions** (e.g. verification removes 95% of fake activity), validated only against
  the simulator. Verification after 24 h checks the prediction, not the assumption.
- **Potential mode is conservative and model-dependent:** the twin shows +2–3.5% margin from a taste-matched feed;
  the simulator's truth for the same feed is higher, because the twin's taste model is flatter than reality.
- **Issues are separated by cause family** (users / revenue confirmation / reward pricing) per country and platform;
  two problems of the same family on the same platform and country at the same time would be merged.
- **Launch totals are only as good as the user count.** Per-user economics, offers and rewards transfer well, but user
  scale in the simulator follows the payout-share calibration, not public features. That's why the user count is an
  input or a labelled assumption. Predicting the *total* margin with the assumed user count gives a median hold-out
  error of 32% over all 20 watched countries (worst India, +232%; truth inside p10–p90 for 17 of 20).
- **Free-tier hosting:** sessions live in memory (up to 12, idle expiry 30 min, LRU eviction); a restart resets them.
  Work that is identical for every session (the fitted default world, the start scene, the Argentina forecast and
  its backtest) is precomputed into the Docker image (`python -m argus.precompute`), so "Fresh world" is a copy
  (~15 ms locally) and the launch forecast a lookup (~2 ms). Render's own cold start after idling is not affected.
- LLM-written reports are optional; without a key Argus uses deterministic templates.

## Run the backend
```bash
cd backend
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest -q          # unit + end-to-end demo tests
.venv/bin/uvicorn argus.api:app --reload --port 8000
./run_demo_server.sh                   # for video recording: port 8000, no --reload
```
API contract: [`docs/API.md`](docs/API.md). Jump to a video scene with `POST /api/demo/{scene}`
(`start`, `promo`, `bot_farm`, `investigate`, `approve`, `verify`, `new_metric`, `tracking_break`).
Optional LLM (OpenAI-compatible): set `LLM_BASE_URL`, `LLM_API_KEY`, `LLM_MODEL`; deterministic templates otherwise.

## Use the twin from other agents (MCP)

Argus also runs as a [Model Context Protocol](https://modelcontextprotocol.io) server over stdio, so any MCP client
(Claude Desktop, Claude Code, other agents) can query and stress-test the twin. No extra dependency: the server is
plain JSON-RPC on the standard library.

```bash
cd backend && .venv/bin/python -m argus.mcp_server      # stdio; logs on stderr
```

| Tool | What it does |
|---|---|
| `get_world_state` | Clock, countries that are not healthy, open incidents with issue cards, explained changes |
| `explain_incident(id)` | Root cause, counterfactual proof, decision trail, hypotheses, fixes, engineer report |
| `simulate_fix(id, fix)` | 7-day Monte Carlo rollout of one fix vs doing nothing (read-only) |
| `what_if(country, change, platform?, hours?)` | Sandbox copy of the world: inject `promo` / `bot_farm` / `tracking_break` / `data_delay`, run it forward, report what Argus detects and the margin difference |
| `what_if_launch(country, users?)` | Forecast entering a new country (Argentina): margin per 1,000 users, offer mix, reward level, fraud risk, total at a given or assumed user count, 20-country hold-out backtest |
| `advance_time(hours)`, `run_demo_scene(scene)` | Drive the simulation |

Approving fixes is deliberately not exposed: changing the marketplace stays a human decision in the UI.

Claude Desktop (`claude_desktop_config.json`), with your own path:
```json
{
  "mcpServers": {
    "argus": {
      "command": "/path/to/argus/backend/.venv/bin/python",
      "args": ["-m", "argus.mcp_server"],
      "env": { "PYTHONPATH": "/path/to/argus/backend", "ARGUS_MCP_SCENE": "bot_farm" }
    }
  }
}
```
`ARGUS_MCP_SCENE` is optional (start from a demo scene); `ARGUS_SEED` picks the world. Claude Code:
`claude mcp add argus -e PYTHONPATH=/path/to/argus/backend -- /path/to/argus/backend/.venv/bin/python -m argus.mcp_server`.

## Run in Docker (frontend + API on one port)
```bash
docker build -t argus .
docker run -p 7860:7860 argus            # open http://localhost:7860
# optional LLM reports: add -e LLM_BASE_URL=... -e LLM_API_KEY=... -e LLM_MODEL=...
```
Port 7860 matches Hugging Face Spaces (Docker SDK). Secrets are never baked into the image.

## Deploy on Render
1. Push this repo to GitHub (done: `ETohidi/argus`).
2. In Render: **New → Blueprint**, pick the repo. Render reads `render.yaml` and creates one free web service
   (Docker runtime, root `Dockerfile`, health check `/api/health`).
3. When prompted, paste `LLM_API_KEY` (marked `sync: false`, so it lives only in Render). `LLM_BASE_URL` and `LLM_MODEL`
   are preset; leave the key empty to run with template reports.
4. Open the service URL: the globe is served at `/`, the API at `/api`. Render sets `$PORT`; uvicorn listens on it.

Free instances sleep when idle; the first request after a pause takes ~30–60 s (the world is rebuilt per session).
