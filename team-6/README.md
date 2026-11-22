# Eye of the System: Fully Agentic Monitoring

**Team 6:** Anila Ijaz and Ayesha Fazal Lashkarwala

> In this repository the project lives in `team-6/`. After cloning, run every command below from that folder (`cd Almedia-hackathon-Agent-Monitoring/team-6`).

An autonomous monitoring system for a simulated online marketplace. It keeps a
**graph of business metrics and how they relate**, watches them hour by hour,
decides on its own what is a real problem, investigates it by following the
graph, and writes up what it found, with the evidence and what is still
uncertain. No person asks it questions or guides an investigation.

Built for the Almedia track "Eye of the System: Fully Agentic Monitoring".

## Quick start

Tested with Python 3.13.

**1. Get the code and install**

```bash
git clone https://github.com/Anila-Ijaz/Eye-of-the-system-Fully-Agentic-Monitoring.git
cd Eye-of-the-system-Fully-Agentic-Monitoring
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # macOS / Linux
pip install -r requirements.txt
```

**2. Start the demo dashboard**

```bash
python -m demo
```

A browser tab opens at **http://127.0.0.1:8050**. If it doesn't, open that
address yourself. No API key is needed: without one, the system uses its
rule-based chooser and template report text.

The demo first builds 4 weeks of marketplace history, which takes a moment.
Then it streams 6 simulated days, one hour per tick. A scripted timeline
injects faults and innocent changes, and adds a new metric halfway. The
monitor isn't told any of this. When the 6 days are done, the run stops and
everything is saved to `data/generated/demo_run/`.

**3. Or run it in the terminal only**

```bash
python -m demo --headless           # prints alerts as they happen, then the scorecard
python -m demo --headless --quiet   # only alerts and the scorecard
```

The full 6-day run takes about 7 minutes without the model (about 2.7 s per
simulated hour), and about 15 minutes with the model (about 6 s per hour).

**4. Run the tests**

```bash
python -m pytest        # 210 tests, about 6 minutes; no API key or network needed
```

## Using the dashboard

| Area | What it shows |
|---|---|
| Top bar | Simulated clock; **Pause / Resume**, **Step 1h**, speed (Fastest, 1, 2 or 4 s per hour); light/dark toggle; "model-chosen steps: X of Y" when the model is on |
| Left: **Issues** | Every issue the monitor opened, with status (open, resolved) and severity. Click one to see its report |
| Middle, **Issue report** tab | Headline (confirmed or most likely), business impact, evidence, what is still uncertain, recommended actions, and the investigation trail. Each step shows whether the model or the rules chose it, and why |
| Middle, **Metric graph** tab | The metrics and the formula and cause links between them, with their status. A new metric appears here once the system has placed it |
| Middle, **What it watches** tab | The current watch list and why each metric is on it |
| Middle, **Scorecard** tab | The hidden ground truth compared with what the monitor reported. This is the only part that reads the ground truth |
| Right: **Agent decisions** | Live feed: data checks, anomalies, triage decisions, new issues, investigations, alerts |
| Inject buttons | **Inject fault**, **Innocent change** and **New metric** start a scenario now. Use them with `--manual`, or on top of the scripted run |

What to look for in the scripted run (the live period starts 29 Oct 00:00 UTC):

| Clock (UTC) | What to look at |
|---|---|
| 29 Oct 04:00 | Payment issue in DE/Android: **confirmed**, release named as the trigger |
| 29 Oct 12:00 onwards | UK promo changes in the feed: "explained by planned event … not an incident" |
| 30 Oct 07:00–13:00 | "Data incomplete" issue for DE/iOS, closed when the late rows arrive |
| 30 Oct 12:00 | Feed line `new metric orders_per_search`; the new node in **Metric graph** |
| 31 Oct 04:00 | Stock-out in electronics: **most likely**, with what is missing |
| 1 Nov 05:00 | Search release in DE: **confirmed** |
| 2 Nov 05:00 | Ad budget in FR: **most likely** |
| End | **Scorecard** tab |

| Time | What happens (hidden from the monitor) |
|---|---|
| +3 h | payment failure on Android in Germany, right after a checkout release |
| +8 h | planned promo in the UK (+25%, on the calendar) |
| +30 h | late data for Germany/iOS (rows arrive hours late) |
| +36 h | a new metric arrives: `orders_per_search`, with only a definition |
| +50 h | supplier stock-out in electronics |
| +76 h | a search release breaks German search results |
| +100 h | ad budget spent too fast in France; public holiday in the US |

Other options:
- `--manual`: no scripted timeline; inject from the page.
- `--days N`: number of simulated live days.
- `--speed 1`: an extra second between hours.
- `--port 8060`: use another port if 8050 is taken.
- `--no-browser`: don't open a browser tab.
- `--no-llm`: rules only, even when a key is set.
- `--llm-cache-mode record|replay`: see "Run it with Claude" below.
- `--out DIR`: where reports and the scorecard go.

### What the system reports (6-day run, seed 42)

| Scenario | Result |
|---|---|
| Payment failure DE/Android | found after 1 h; **confirmed** payment success rate in DE/Android; release named as the trigger; resolved |
| Planned promo UK | explained by the planned event; no incident |
| Late data DE/iOS | data-quality issue, business metrics held back; resolved by the backfill |
| Stock-out electronics | found after 2 h; root out-of-stock rate in electronics (a hypothesis: the graph only says "possible cause") |
| Search release DE | found after 1 h; **confirmed** search click-through in DE; release named; resolved |
| Ad budget FR | found after 1 h; root ad impressions in France (a hypothesis: no event explains the timing) |
| Holiday US | explained by the planned event; no incident |

That is 10 alerts in 6 days: one when each problem was opened, one when it
was resolved, and one when the stock-out's root cause sharpened. Hundreds of
anomalous signals were grouped into those issues. Reports, alerts, the feed
and the scorecard are saved to `data/generated/demo_run/`. The run with Claude
choosing the steps gave the same scorecard.

## How it works

Every simulated hour the runner ([runtime/runner.py](runtime/runner.py)) does this, without a person:

1. **Data.** The hour's rows arrive, plus late rows for earlier hours.
   Missing slices open a data-quality issue, and a backfill resolves it.
2. **Watchlist.** Once a day it picks what to watch from metric priorities
   (graph importance, business weight, noisiness), plus the root KPIs and the
   root causes of open issues.
3. **Detection.** Every watched metric × grouping (total, country,
   country × platform, category) × window (1 h, 6 h) is scored.
4. **Triage.** Each new anomaly is checked against data quality and the event
   calendar: held back, explained as planned, or reported.
5. **Issues.** Reported anomalies join the issue they belong to, or open one.
6. **Investigation.** The agent loop follows the graph, localizes the change,
   checks events, upstream causes and counterfactuals, and decides when to stop.
7. **Reports and alerts.** Alerts go out only when an issue opens, its root
   cause changes, it resolves, or it recurs.

| Layer | What it decides, and how | Code |
|---|---|---|
| L1 Simulator | hourly marketplace (4 countries × 3 platforms × 4 categories): funnel, ads, recommendations, product-level inventory; live mode with runtime injection | [simulator/](simulator/) |
| L2 Metric graph | 33 metrics; formula, breakdown and possible-cause edges, each checked on the data; DuckDB engine for any window/segment | [graph/](graph/) |
| L3 Extension | a new metric with only a definition: formula search on every hour and segment, seasonality-adjusted lagged correlation, validation, registration | [extension/](extension/), [graph/extend.py](graph/extend.py) |
| L4 Monitoring | expected = median of the same window 1–4 weeks earlier, with growth; range from the backtested error, never below count noise; z ≥ 5, persistence, completeness; triage against data quality and the calendar | [monitoring/](monitoring/) |
| L5 Investigation | Shapley contributions along formulas; drill-down with rate/mix effects (localized only if the rest stayed normal); hypotheses must be localized + timing-aligned + mechanism-confirmed + counterfactual-passed to be **confirmed**; symptoms ranked after causes | [investigation/](investigation/), [agent/](agent/) |
| L6 Reporting | issues by root cause (merge, recurrence, quiet tracking of low-impact noise); report with numbers, impact, evidence, uncertainty, actions, trail | [reporting/](reporting/) |
| L7 Demo | live dashboard; scripted timeline; scorecard against the ground truth | [demo/](demo/) |

Only the scorecard reads the simulator's ground truth. The monitor sees rows,
the event calendar (release log, planned campaigns) and the metric graph.

### Run it with Claude

When `ANTHROPIC_API_KEY` and `LLM_MODEL` are set, Claude chooses the
investigation steps and writes the report summaries. No flag is needed;
`--no-llm` turns it off. Keys and model ids come only from the environment.
Never commit them.

1. Copy the example settings and fill in your key and model:
   ```bash
   cp .env.example .env        # Windows: copy .env.example .env
   ```
   ```ini
   ANTHROPIC_API_KEY=your-key  # read by the anthropic SDK; never printed or logged
   LLM_MODEL=...               # chooses the next step
   LLM_REPORT_MODEL=...        # writes report text (optional, default LLM_MODEL)
   ```
   `.env` is git-ignored. The demo and the scripts read it on start. A
   variable already set in your shell takes precedence.
2. Record one run. This calls the API (about 200 calls for the 6 days) and
   saves every answer to `data/llm_cache/`:
   ```bash
   python -m demo --llm-cache-mode record
   ```
3. Replay it as often as you like: the same run, no API calls, no cost:
   ```bash
   python -m demo --llm-cache-mode replay
   ```
   Replay needs only `LLM_MODEL`, not the key. Don't use the inject or
   add-metric buttons during record or replay: an action that wasn't
   recorded has no saved answers, and those steps fall back to the rules.

`data/llm_cache/` is git-ignored, so a fresh clone has no recording to
replay. Record once first.

| The model decides | Code decides |
|---|---|
| which menu step runs next, and why (at most two sentences, citing evidence) | the menu, every number, hypothesis status and confidence, the confirmed/uncertain label, when to stop |
| the report summary text | the structured finding |

- **Step choice.** A reply is used only if it names a number on the menu and
  gives a reason that cites evidence from the prompt. Anything else falls back
  to the rule-based chooser for that step: invalid JSON, an off-menu number,
  an empty or evidence-free reason, an error, or no key. The model has no stop
  option. A stop request is ignored and recorded as `llm_stop_ignored`. Each
  trail step records `chosen_by` (`llm` or `fallback`) and the reason or the
  fallback cause. The dashboard shows "model-chosen steps: X of Y".
- **Report text.** Every number, percentage and euro amount in the model's text
  must appear in the structured finding (rounding allowed). The text may not
  call an unconfirmed cause confirmed. Otherwise the template text is used and
  the reason is recorded.
- **Cache.** `--llm-cache-mode record|replay|live` (or `LLM_CACHE_MODE`), stored
  in `--llm-cache-dir` (or `LLM_CACHE_DIR`, default `data/llm_cache`). The key
  is a hash of the model and the exact prompt. Replay needs no key and no
  network, and it reproduces the recorded trails and reports exactly.
- **Checks you run with a key:** `python scripts/live_check_llm.py` (one call,
  then record and replay of the fault-window investigation), and
  `python scripts/llm_scoreboard.py --n 4` (injected faults with the model
  versus the fallback only).

## Layer by layer

```bash
python -m simulator --scenarios all                               # L1: data with scenarios
python -m graph --data data/generated/scenarios                   # L2: check every graph edge
python -m extension --metric extension/examples/new_metrics.yaml --apply   # L3: add new metrics
python -m monitoring.detect                                       # L4: anomalies, last 7 days
python -m investigation.investigate --metric orders --segment country=DE   # L5: investigate
python -m pytest                                                  # all tests
```

Each folder has its own write-up: [simulator/README.md](simulator/README.md),
[graph/README.md](graph/README.md), [extension/README.md](extension/README.md),
[monitoring/DETECTION.md](monitoring/DETECTION.md),
[investigation/INVESTIGATION.md](investigation/INVESTIGATION.md).
