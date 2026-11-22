# Demo script (3 minutes)

Status: rehearsed on 22 Nov, 01:00 CET with all open pull requests merged locally, fresh 28-day backfill, `--speed 2`: fault found 6 s after the click, resolved 8 s after the rollback, promo explained, late data held, `refund_rate` answered in about 10 s (`docs/pitch-script.md` has the timings). The three-minute timing still needs a run against a timer.

## Before we start (all terminals at the repository root)

1. Stop `sim.live`, the brain monitor, and the UI with Ctrl+C. Close database viewers too; on Windows a process
   holding SQLite open prevents replacement. Do not reset a teammate's active run.
2. Archive the previous run outside the output folders: `incidents/*.json`, the contents of `incoming_metrics/`
   (including refund_rate and its result), and `data/graph.json`. These are disposable demo outputs, not source files.
   Start with empty incident and incoming-metric folders; do not mix scoring and live incidents.
3. Backfill with explicit paths:

```
python -m sim.run --backfill 28d --db data/market.db --force
python -m brain.run --db data/market.db --incidents incidents --incoming incoming_metrics --graph-out data/graph.json --fresh
# Add --narrate to show "In brief" above each report: needs ANTHROPIC_API_KEY in .env, one request per milestone
# (new incident, new leading cause, a cost past a power of two, resolved). Without it, or without credit, the report is the template only.
# --fresh empties incidents/ (archive it first, step 2, if the run is worth keeping). Without it a restarted
# monitor leaves the previous run's incident and pending files as they were and opens duplicates beside them.
# It also removes old answers in incoming_metrics/. A definition left there is taken again at start, so the
# folder must be empty (step 2) for the new-metric step to happen in front of the audience.
python -m sim.live --speed 2 --db data/market.db
```

4. Start the UI with these environment variables, then `uvicorn ui.app:app --port 8000`:

| Variable | Value |
|---|---|
| MARKET_DB | data/market.db |
| INCIDENTS_DIR | incidents |
| INCOMING_METRICS_DIR | incoming_metrics |
| GRAPH_JSON | data/graph.json |

POSIX shells: prefix uvicorn with `MARKET_DB=data/market.db INCIDENTS_DIR=incidents
INCOMING_METRICS_DIR=incoming_metrics GRAPH_JSON=data/graph.json` (on one line).
PowerShell: set each as `$env:NAME="value"` in the UI terminal before running uvicorn.
The sim commands use `--db`, not MARKET_DB. UI variables are read once at import: restart after changing them.

Check http://127.0.0.1:8000: the clock in the header moves, the exported graph shows on the canvas and the incident dock
along the bottom reads "No incidents. The monitor is watching every hour of data."
The contract example may appear before the monitor starts; it is marked as an example, not a result.
The console at / is a canvas: the graph fills the page, the incidents are cards in the dock at the bottom, a report opens
in a panel on the right, and the **Test bench** (marked DEMO) opens from the header and holds every button below. The
earlier console, with the same data and buttons, is at http://127.0.0.1:8000/classic.
Do not start the accelerated clock five minutes early: launch it immediately before the presentation.
At speed 2, a day passes in 48 seconds. Read the header clock before injecting; night-time counts are smaller.

For scoring use a separate folder: `python -m sim.score generate --db data/eval.db --days 49`;
then `python -m brain.run --replay --db data/eval.db --incidents eval_incidents --fresh` (it writes to **eval_incidents/**), then
`python -m sim.score score --db data/eval.db --incidents eval_incidents --out data/eval-results.md`.
The page `docs/results.md` is built from many such runs by `python -m scripts.summarize_evaluation` (its docstring has the
command); a single-run report must not overwrite it.
Never point the live UI at eval_incidents. Missing or empty scoring output is an error, not a measured zero.

## Timeline

| Time | Say | Do | Must be visible |
|---|---|---|---|
| 0:00 | "A marketplace has thousands of metrics and nobody can watch them all. This system watches by itself." | Header, graph canvas and the empty incident dock on screen | Moving clock in the header, formula tree on the canvas |
| 0:20 | "It knows how the business works: orders are sessions times a chain of step rates, and payment success is one of them. Each line has a meaning." | Point at the tree; formula lines are solid, possible causes dashed with a sign (**Legend**, bottom left of the canvas); a click on a box opens its definition, breakdown and context links in the panel on the right; close it with the x | Tree from contribution down |
| 0:45 | "Fault: a payment release breaks Android payments in Germany." | Click **Test bench** in the header; choose **Payment failure** (DE, Android), click **Inject fault**; then **Close** the bench | The bench's line "Payment failure injected from ..." |
| 1:00 | "It notices, follows the graph, and tells us where and why." | Wait for the incident (about 5 to 6 s at `--speed 2`) | A card slides into the dock (**open** in red, then **ongoing** in amber once the next hours confirm it); its report opens on the right and the view flies to its path, orders down to payment success lit in the card's colour, payment success marked with its change and the steps that stayed normal marked "held" |
| 1:20 | Read the report: traffic and the other steps normal, payment success down in DE x Android; the impact in euros; the supported cause and the rejected ones; what is still uncertain. Then the replay: "This is what it did, step by step." | The report is open (click the card in the dock if the panel shows something else). Click **Replay the investigation** (in the report, or on the canvas) and press **Next** (or the right arrow key) a few times; **Stop replay** or Esc ends it | The readout (observed, expected, change, effect in EUR) and the severity; Summary, hypotheses with evidence, investigation trail; with `--narrate`, "In brief" above them a few seconds later. In the replay: "Step k of N" with the step's result over the canvas, the metrics of the step ringed with a numbered pin, the same step lit in the report's trail |
| 1:35 | "Someone rolls the release back. The incident should close by itself." | Open **Test bench**, pick the fault in **In progress**, click **End it (rollback)**, close the bench | The bench's line "Ended from ..."; after a few normal hours the card turns **resolved** (the rollback row is in the Data browser, table releases) |
| 1:45 | "Not everything unusual is a problem: a planned French promo lifts orders." | In the bench, click **Promo (12 h)** (FR) | No new red card; an **explained** card, drawn quiet (grey, dashed edge); its report says the rise has the promo's direction and size |
| 2:00 | "If data arrives late, it waits instead of crying wolf." | In the bench, click **Arrives 2 h late** (payments) | No incident for those hours; a quiet **pending** card while they are held |
| 2:15 | "A new metric arrives as a query only. The agent finds the formula itself, places the metric in the graph, takes the context of what it is built from and starts watching it. A duplicate is refused." | In the bench, under New metric, click **Add refund_rate**, then **Add a duplicate metric** | The answer in the bench (on the canvas when the bench is closed) naming the formula found (refunds / orders, the only one of the candidates) and "watched from the next tick" (2 to 10 s: it validates the query on all the data so far); a new tree `refunds` = `orders` x `refund_rate` on the canvas; a click on `refund_rate` lists "Checks of the extension agent" in the report panel; the duplicate answered as an alias of payment_success_rate |
| 2:40 | "We scored it on 240 injected faults and 160 innocent events in twenty runs, ten of them never seen before: detection, place, cause, false alarms." | Show `docs/results.md` | The table with the holdout line; numbers from `python -m scripts.summarize_evaluation`, not hand-picked |
| 2:55 | One-line close: "It watches the business, not the servers." | | |

## If something hangs

1. The simulator or UI is unresponsive: restart it (`Ctrl+C`, run the command again). Data is in `data/market.db`; the
   history stays. If the canvas itself misbehaves, open http://127.0.0.1:8000/classic: the same data and buttons, graph
   and incidents side by side, the DEMO bar folded at the top.
2. In accelerated replay only, if the brain does not report within 20 s: state that it has not produced a report. A recording is a fallback only
   after we have recorded and checked one; no recording exists in this snapshot.
3. Network or API problem: the core loop does not use the language model; the report falls back to a template.
4. Use a checked recording if available; otherwise show the graph and data and explain the live failure.

## Other faults the Test bench can inject

| Fault | What changes in the data | Where a monitor should end up |
|---|---|---|
| Payment failure | Payment success rate falls for one country and platform; a payments-sdk release is on record | orders, payment success rate, the country and platform, the release |
| Stockouts | More product views meet an unavailable listing in one category; add-to-cart rate falls, feed clicks rise | orders, add-to-cart rate, the category, stockout rate (no release) |
| Search regression | Clicks per search fall in one country; a search release is on record | orders, views per session, search clicks per search, the country, the release |
| Ad budget gone early | Sponsored impressions per session jump, then collapse after a few hours; traffic is unchanged; a pacing release is on record | ad spend, ad load (not sessions), the country |
| Traffic drop | Sessions fall by half for one country and platform, every rate unchanged; no release | sessions (a volume), the place, traffic named as the moved factor, no cause confirmed |
| Feed shows unavailable | Stock-outs and feed clicks rise in the recommendations channel; a recommendations model release is on record | stockout rate in the recommendations channel, the release |
| Discount push | The discount rate rises in the recommendations channel, orders rise, contribution falls; a recommendations ranking release is on record | discount rate, the channel, the release, the euro split |
| Mix shift (innocent) | Traffic moves toward one category, every rate in it unchanged; no calendar entry | no alert: an explained record whose reason is the mix |

The scoring script (`python -m sim.score generate`) mixes the first four with promos and late data; the other four are measured one case each by `python scripts/stress_cases.py` (`docs/stress.md`).

## Things to rehearse

- Inject at a chosen header-clock hour and record it in each rehearsal. `--now` affects the latest data hour; it does not fix the hour of day.
- Reset between rehearsals with the full stop/archive/backfill/restart list above.
- The page sends severity 0.4, which only the payment failure uses (payment success falls by 40%; a search regression from the page is 0.5); a fault from the page lasts 72 simulated hours (the ad-budget fault 12) unless rolled back; promo is 12 hours; late data delays three hours of one source, each by 2 h.
