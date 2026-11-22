# Pitch script: 3 minutes for the video, 5 minutes for the stage

Everything here is something the repository shows today. Where the system does not do something yet, the script says so instead
of implying it. Numbers come from `docs/results.md`. Read the whole page once before recording.

## Before you start (5 minutes)

1. Clean state, accelerated clock, from a clean clone: run `python -m sim.run --backfill 28d --db data/market.db --force`
   once, then start these three in their own terminals, in this order:
   `python -m brain.run --db data/market.db --incidents incidents --graph-out data/graph.json --fresh`, then
   `python -m sim.live --speed 2 --db data/market.db`, then `uvicorn ui.app:app --port 8000`. Open http://127.0.0.1:8000.
   Start the console with the four environment variables of `docs/demo.md`, step 4, and with an empty `incoming_metrics/`
   folder: without `GRAPH_JSON=data/graph.json` the console shows the tracked example graph and a metric added on stage never appears.
   To show "In brief" (the model's narration above the report), add `--narrate` to the brain command: it needs
   ANTHROPIC_API_KEY in `.env` and spends a few requests per incident. Without it the report is the template only, and
   "In brief" (docs/demo.md at 1:20, and the demo slide) does not appear.
2. Wait until the clock in the header moves and the incident dock along the bottom reads "No incidents. The monitor is
   watching every hour of data."
3. Open the **Test bench** (marked DEMO, in the header) once so you know where the buttons are, then close it (**Close**
   or Esc). Dark or light theme: add `?theme=light` or `?theme=dark`. The earlier console, with the same data and
   buttons, is at `/classic` if the canvas misbehaves.
4. Two faults of one metric that begin in the same hour in places sharing a country or a platform can be reported as one
   incident (a known limit, README 3.5); inject them an hour apart or in places that share neither a country nor a platform.

## The 3-minute script (about 390 spoken words)

**0:00 The problem.** (Header, graph canvas and the empty incident dock on screen.)
"A marketplace has thousands of numbers and nobody can watch them all. Most problems are not outages: the servers are fine, but
payments fail for Android users in Germany and orders quietly fall. This system watches the business by itself: it notices a
change, finds where and why, and explains it. No one asks it questions."

**0:20 How it understands the business.** (Point at the graph, then click one box: its definition and links open in the
panel on the right. Close the panel with the x before the fault, so the incident's report can open there by itself.)
"It starts from a graph of how the business works: contribution is fees plus ad spend minus discounts, orders are sessions times
conversion, conversion is a chain of step rates. Lines have meanings: exact formulas, breakdowns, possible causes, and context
such as releases and promos."

**0:40 A real fault.** (**Test bench** in the header: Payment failure, DE, Android, **Inject fault**. **Close** the bench.)
"I now break it: a payment release goes wrong for Android in Germany. The monitor was not told. Every hour it judges every step
rate in 48 slices of the market, and visits and orders against the same hour of earlier weeks..." (wait for the incident)
"...it has found it."

**1:00 The report.** (The incident's card slides into the dock, its report opens on the right and the view flies to the
path from orders down to payment success, lit in the card's colour; the steps that stayed normal are marked "held". If the
panel shows something else, click the card.)
"It went down the chain like an analyst: traffic normal, add to cart and checkout normal, payment success down, in Android in
Germany. It says what this costs in euros, and the cause: the payments release, the only one whose system, place and hour all
fit. The other explanations are listed as rejected, with the reason, and so is what is still uncertain."

**1:25 How it decides.** (Click **Replay the investigation**, at the top right of the canvas or in the report. Press
**Next** (or the right arrow key) about once per sentence while speaking: each step flies to the metrics it touched, rings
them with a numbered pin and shows its result over the canvas, and the same step is lit in the report's trail. The payment
fault has ten steps: watch, compare, drill into places, split along the formula, check the factors of orders, check
freshness, the calendar and the release, compare the explanations, stop. It need not reach the last one. **Stop replay**
or Esc before 1:50.)
"How it decides: it watches every slice it can afford, because a fault in one cell vanishes in the totals. It follows a cause
link only in the direction the link predicts. It scores four hundred places by how much better one shift explains the counts,
minus the price of searching. It stops when every candidate the graph offers has been tested, and says how decisive that was."

**1:50 It closes by itself.** (**Test bench**: pick the fault in **In progress**, click **End it (rollback)**. Keep the
bench open for the next steps.)
"Someone rolls the release back. A few normal hours later the incident is resolved, on evidence, not because alerts went quiet."

**2:00 Not everything unusual is a problem.** (In the bench: **Promo (12 h)** in FR; then **Mix shift (innocent)** if time
allows. Each shows up as a quiet **explained** card in the dock, grey with a dashed edge, not as an alert.)
"A planned promo lifts orders in France: it finds the promo in the calendar and checks that the rise has the promo's direction
and size. No alert. Traffic moving toward one category moves the market's rates too; it measures that the mix explains it. No
alert either."

**2:15 A new metric.** (In the bench, under New metric: **Add refund_rate**, then **Add a duplicate metric**. The answers
appear in the bench; close it to see the new `refunds` tree on the canvas.)
"A new metric arrives as a query, nothing else. The agent finds the formula itself, refunds over orders, the only one of a
thousand candidates; finds its place in the graph, takes the context of what it is built from and starts watching it. A
duplicate of an existing rate is refused, with the reason."

**2:35 Does it work, and the close.** (Open `docs/results.md`.)
"On ten new runs it had never seen it found all 120 faults, all in the right place, 116 with the cause confirmed, with no
false alarm, and all 80 innocent changes stayed quiet. Eleven stress cases test what those runs do not; ten pass, and the
miss is listed. The data
is our own simulator, so this is an engineering check, not proof on real traffic. It watches the business, not the servers."

## What not to say

- The new-metric buttons work end to end for the console's `refund_rate` (found from its query alone) and for a duplicate (refused
  as an alias); README 3.3. Do not claim more than that section says: the formula search covers one or two column metrics (19 of
  the 36), without constants, and only a ratio of two columns is watched.
- "Every slice it can afford" means the seven cuts of README 3.4, not every combination: a fault in a combination no cut holds is
  found when it is large enough to show in a cut, then narrowed by the localization.
- Changes under 10 % of a rate are not reported, except for a share near 1, where it is enough that the failing share moves by
  half of itself: failures rising from 6 % to 14 % are caught (`docs/stress.md`), smaller ones are not.
- Do not say "100%" without the cause number (116 of 120 on the holdout runs; the four unconfirmed are stock-outs) and the
  caveat on the data.
- No report on these runs supports two causes, but one can: a harmless release of the same system and place within two hours
  of the onset is supported beside the real one (README 3.5). Do not say every cause is always singled out.
- The holdout is seeds 111 to 120, made after the last change. Seeds 101 to 110, the first version's holdout, give 120/120,
  118 causes and 1 false alarm with this version; do not call them independent any more.
- "Holdout" means new random runs of our own simulator, with the same four kinds of fault. Do not call it a test on real data.
- Do not call all twenty runs independent: ten are a holdout, ten were used while the brain was built. `docs/results.md` keeps them apart.
- Do not call the wall-clock mode a demo mode: it moves one data hour per real hour. Use the accelerated clock on stage.

## Timings measured from a clean start (accelerated clock, `--speed 2`)

- Payment failure in Germany on Android: the incident appears about 5 seconds after the click, with the payments release as the cause.
- "End it (rollback)": the incident turns resolved about 8 seconds later (three normal data hours).
- Promo in France: an `explained` record, grey, no alert. Late data: `pending` for about 6 to 8 seconds, then it disappears by itself.
- Rehearsed again on 22 Nov, 01:00 CET, with the redesigned console and all open pull requests merged locally, fresh 28-day
  backfill, `--speed 2`: incident 6 s after the click (seen at the next data hour), resolved 8 s after the rollback, promo
  `explained` within 6 s, late data `pending` at once; **Add refund_rate** answered within about 10 s while the clock ran
  (validation over 30 days of data), and the new `refunds` tree appeared in the graph.

- Rehearsed again on 22 Nov, 08:10 CET, on `e220dfc` (the final `b7b856c` adds two monitor and incident fixes that do not change these steps), fresh 28-day backfill, `--speed 2`, in the browser:
  payment failure DE x Android injected at 14:00 on the header clock: incident about 2 s after the click, the payments
  release supported; **End it (rollback)**: resolved 6 s later; **Promo (12 h)** FR: `explained` within 1.5 s, no alert;
  **Mix shift (innocent)** toward fashion: two `explained` records whose reason is the mix, no alert; **Add refund_rate**:
  answered in 4.6 s, "formula refunds / orders found from the SQL, the only one of 1045 candidates", validated on 29 days,
  watched from the next tick; **Add a duplicate metric**: answered in 7.2 s, rejected as an alias of payment_success_rate.

- Rehearsed on the new main console (design 3) on 22 Nov, 10:50 CET, fresh 28-day backfill, `--speed 2`, at 1280 x 800:
  payment failure DE x Android from **Test bench**: incident about 1 s after the click, opened in the report with the
  payments release supported; **Replay the investigation** steps through ten steps on the canvas (arrow keys work); close
  the bench before the replay, it covers the canvas. **End it (rollback)**: resolved 5 s later; **Promo (12 h)** FR:
  `explained` in 2.6 s; **Add refund_rate**: "formula refunds / orders found from the SQL, the only one of 1045 candidates"
  in 3.6 s, with **Show the checks**.

## If something goes wrong

- No incident after 20 seconds: say "it is still collecting evidence" and move on to the promo; or play the recording.
- A fault in a small segment at night needs more hours of evidence; use Germany, Android or iOS payments, or a category such as electronics.
- To start over: stop the three processes, empty `incidents/` and `incoming_metrics/`, run the first command again, start them in the same order.

## The 5-minute slides

1. **The problem** (30 s): too many numbers; failures that are not outages; nobody asks the question.
2. **How it sees the business** (45 s): the metric graph figure of `docs/brain-architecture.html` (formula, breakdown, possible cause, context); the parts picture of `docs/system-overview.html` if there is time.
3. **The loop that runs by itself** (60 s): read, expect, detect, locate, explain, keep tracking; one fault is one incident, updated in place.
4. **Demo** (60 s): a screenshot of the console with an incident and the lit path.
5. **Results** (60 s): the table from `docs/results.md`; say the caveat on the same slide.
6. **What failed and what next** (45 s): the misses listed on `docs/results.md` and `docs/stress.md` (a fault in two sibling places is named at the place that holds both; small changes in rates that are not near 1); stock-outs stop at the stock-out rate because suppliers are not simulated; new metrics with constants; all data is simulated.

## Recording the video (3:00 at most)

The rules ask for the pitch, how the solution works and a live demo, with a team member speaking to camera. One way that
needs nothing beyond Windows 11:

1. **Prepare** (10 minutes): a clean start as in "Before you start"; the console in a browser window of 1280 x 800 (the
   projector size the page is tuned for), light theme (`?theme=light`), zoom 100 %; notifications off; the Test bench closed
   and the report panel pinned (the **Pinned** button in its header), so the report keeps its own column beside the graph.
   Let the clock run a few data hours into the day before you inject: night hours have small counts.
2. **Camera parts**: open with 15 s to camera (who you are, the problem in one sentence) and close with 15 s (the result and
   the one-line close). The Camera app records them.
3. **Screen part**: Xbox Game Bar (Win+Alt+R) records the browser window. Follow the 3-minute script from 0:25 to 2:40 and
   say it over the screen as you click. The timings above say how long each step takes to show.
4. **Cut** in Clipchamp (built into Windows 11): camera opening, screen part, the results slide (the "Measured" table of
   the README or `docs/results.md`), camera close. Keep it under 3:00; export at 1080p.
5. **Upload** to YouTube as Unlisted (or Google Drive, anyone with the link can view). Open the link in a private window to
   check that it plays for someone who is not signed in. Put the link in the form and in the README.

