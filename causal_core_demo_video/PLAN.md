# Demo videos: an autonomous monitoring agent that knows when to act

Ad-hoc folder, outside the core. Updated 22 Nov 2026. Nothing here changes `causal_core/`.

- **Audience:** leadership. **Format:** one video per use case, **3 minutes max** each, captions on screen
  (optional draft voice-over), names anonymized, neutral branding. Approved actions run only in a demo-only sandbox.
- **One message:** the agent watches continuously, separates data problems from real changes, re-derives every
  number it relies on, suggests a fix with a best guess and a low and high estimate, and acts only after a person approves, only where
  it is allowed to.

## Ground rules (shown on screen)

1. Actions are proposals until a person approves them; approved actions run in a sandbox (mock Statsig project;
   the marketplace emulator's intervention gateway / the Astronomy Shop's feature flags). The core has no
   production write path (I15); real actions belong to Phase 8, which is not authorized.
2. Real where it can be, labelled where it can't. Injected incidents always show their truth at the end of a beat.
3. Every number on screen is produced by a run and saved under `outputs/`; scenes only read those files.

## Video 1 — experiment watcher (Statsig-shaped) · **built**

`python3 -m agent.uc1` → `outputs/uc1/`; `node scenes/record.mjs uc1` → `videos/uc1.mp4` (2:23). See README.
Beats: board (9 real anonymized experiments, re-derived to 2e-14; one real split failure) → broken split (EXP-10)
→ false alarm avoided (EXP-12) → real harm and stop (EXP-11) → "ship it?" denied → close.

## Video 2 — marketplace incident on the Astronomy Shop (replaces the earlier Stitch-only plan)

**Why the change:** marketplace-recon now ships processed Olist tables and fitted parameters (`artifacts/`, commit
21c1f68), so its emulator runs here; the OpenTelemetry Astronomy Shop gives a real storefront with real service
telemetry. The video shows the agent acting **side by side with what happens inside the engine**.

**Screen layout (1920×1080):**

| Left, ~60%: the world and the agent | Right, ~40%: inside the engine |
|---|---|
| The Astronomy Shop storefront, driven by emulator shoppers (headless browser capture); a strip of live telemetry (payment errors, orders, payments) | Architecture map: sources (emulator contract port, redacted OTel telemetry port) → evidence port requests → planner stages FRAME → AUDIT_INPUTS → SELECT_CAPABILITIES → ACQUIRE → EVALUATE → CHALLENGE → REPORT → policy gate → ledger / report sink / model outputs. Each block lights up when it runs, with its real duration |
| The agent's console: wake-ups, alarms, diagnosis, proposal card, approval, execution receipt | Live counters that update: observations ingested, monitor evidence vs alarm line, methods chosen and refused (with reasons), ledger rows, report revision n (provisional → final) |

**Beats (≈2:45):**

| Beat | Injected (truth known) | Agent (left) | Engine (right) |
|---|---|---|---|
| 1. Normal | none | shoppers browse and buy; checks pass | one monitoring task: stages light up, ~ms timings, report r1 |
| 2. Payment failures | `storefront.payment_failure` = 25% on the shop (scenario side, via the gateway) | payment error rate alarms within minutes; orders fall; diagnosis: payments fail, demand is unchanged | telemetry port → sequential monitor crosses the line; ratio check; methods that can't apply are refused with reasons |
| 3. Fix in the sandbox | — | proposal "turn the payment-failure flag off" with expected recovery range → approval → executed via the shop's flag service → recovery tracked against the range | what-if result written to model outputs (not evidence); report revision r2 → r3 |
| 4. A look-alike | `logging.drop_rate` 0.4 on order events (8a), no business change | orders look down, payments per order up → "data problem, not demand" → proposes a pipeline ticket, no business action | identity/ratio check, data-quality verdict; policy: report allowed, business action not proposed |
| 5. Close | — | truth reveal for each beat; "0 production writes" | totals: tasks, stage time, ledger rows |

**Honesty notes for this video:** the real core runs on marketplace-recon's port today (`scripts/core_on_emulator.py`);
its recorded results show the logging-drop look-alike detected in 2 of 3 seeds, one seed refused (autocorrelation),
and a known issue that order-count monitoring misses the intervention week (handover items 1–2). The closed loop with
a hidden measurement was run with a stand-in, not the core. Shop runs are not reproducible by design; the video
uses one recorded run and says so.

**Needs (downloads, your go-ahead):** a Python venv with duckdb, polars, pyarrow, pydantic, numpy, scipy, fastexcel
(PyPI, ~150 MB, kept outside marketplace-recon); the OpenTelemetry demo 3.1.0 repo (GitHub) and its container images
(several GB) via Docker Desktop, which is installed but not running.

## Video 3 — the Babbel app and the all_events stream (new, scoping)

The graph engine watches the app's event stream and reasons from screen → event → funnel → metric. What exists:
`global_silver_production.events.all_events` lands in ~2 minutes (the Statsig `all_events` source refreshes once a day);
columns include `name` (e.g. `app:install`, `registration:success`), `meta__udid`, `uuid`, `tracking_uuid`,
`meta__created_at`, `meta__os_name`, `meta__country`, `locale`. This Mac has no Xcode (Command Line Tools only) and no
Android emulator, and no app source checkout.

Two ways to read "the app in an emulator" (decision needed):

- **A. App emulator (recommended first):** a simulated Babbel app, like the marketplace emulator, emitting an
  all_events-shaped stream with event names and funnel ratios taken from read-only, aggregated all_events queries.
  Incidents with known truth: a release that drops `registration:success` on iOS (real drop vs tracking loss),
  the empty-user-ID bucket, a lesson crash. Buildable here now.
- **B. Real app in a device emulator:** the real app in an iOS Simulator or Android emulator, driven by the agent,
  with its events read back from silver all_events within ~2 minutes. Needs Xcode or Android Studio, an app build for
  the emulator from the app team, a test account, and agreement on which tracking environment test events go to.

## Folder

```
causal_core_demo_video/
  PLAN.md  README.md
  agent/     sandbox.py (proposal → approval → sandbox execution → receipt), uc1.py
  scenes/    build_data.py, uc1.html, record.mjs (headless Chrome → ffmpeg)
  outputs/   run outputs the scenes read (uc1/, uc2/)
  videos/    uc1.mp4, stills/
  data/      numbers extracted earlier (scene_numbers_2026-11-22.json)
```
