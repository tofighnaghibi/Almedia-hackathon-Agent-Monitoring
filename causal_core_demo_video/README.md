# Demo videos: an autonomous monitoring agent that knows when to act

Ad-hoc, demo-only folder. Nothing here changes `causal_core/`; the core is imported read-only from `../causal_core`.
Every action the agent takes runs in a **demo-only sandbox** in this folder (a mock Statsig project, or a simulator).
Nothing is ever written to Statsig or any production system.

## Requirements

- Python 3.12+ with the core's dependencies (the same environment that runs `causal_core/` tests: numpy, scipy, pydantic 2).
- Use case 1 reads the local Statsig cache in `../hackathon/.statsig_cache` (no network, no credentials).
- Use case 2 reads `../stitch_samples.zip` (marketplace-recon's samples) for variable names, layers and identity checks.
- Recording the videos (later step): Google Chrome, Node 22+ and ffmpeg, all already on this Mac.

## Use case 1: experiment watcher (Statsig-shaped)

```bash
cd /Users/sdye/code/babbel-core/experimentation.cop/causal_core_demo_video
python3 -m agent.uc1
```

Takes about a minute. It prints the incident timeline and writes `outputs/uc1/`:

| File | What it holds |
|---|---|
| `events.jsonl` | every agent decision in time order: wake-ups, checks, incidents, diagnoses, proposals, approvals, executions |
| `board.json` | the experiment board at each wake-up (12 experiments: 9 real and anonymized, 3 simulated) |
| `crosscheck.json` | the 9 real experiments: engine effect and interval vs Statsig's, difference, split check |
| `actions.json` | proposals, approvals and sandbox receipts, plus one refused attempt to act outside the sandbox |
| `monitor_EXP-11.json` | the guardrail alarm path (evidence vs threshold) and daily trial starts before and after the stop |
| `alarm_day_spread_EXP-11.json` | how typical the alarm day is: the same monitor on 40 simulated runs |
| `srm_EXP-10.json` | daily assignment counts for the broken-split experiment |
| `dq_EXP-12.json` | the false alarm: partial same-day value, completeness, next-morning value |
| `ship_request.json` | the "ship it to everyone?" request and the policy decision |
| `reports/` | the core's report envelopes behind each incident |
| `truth.json` | the injected truth, shown only at the end of each beat |

What happens in the 21-day replay (31 Oct – 21 Nov 2026):

1. **Broken split (EXP-10, simulated):** users evaluated before their user ID is set land in one arm. The agent
   detects the mismatch on day 4, refuses to report an effect, proposes "freeze and annotate", and executes after
   approval in the mock Statsig project.
2. **Real harm (EXP-11, simulated):** variant B lowers trial starts by 5%. The guardrail monitor alarms on day 10
   (typical: 40 simulated runs alarm between day 5 and 16, median 9). The agent re-derives the effect with its range,
   proposes stopping the variant, and executes after approval.
3. **False alarm avoided (EXP-12, simulated):** a same-day dashboard reads −44% because only 57% of the day has
   loaded. The agent waits for the next daily load, and the number comes back normal.
4. **Ship request (real experiment):** the agent reports the effect, but a change to production is denied; a
   proposal goes to the owner.
5. **Real backdrop:** 9 decided experiments from the local Statsig cache, anonymized as EXP-01…EXP-09. Effects are
   re-derived and match Statsig to within 3e-14. EXP-01 is a real finding: 120,167 vs 63,427 users at a planned
   50/50 split, so the engine refuses to estimate it. The cause has not been investigated.

Record video 1 (2:23, captions on screen):

```bash
python3 scenes/build_data.py uc1 && node scenes/record.mjs uc1        # → videos/uc1.mp4
```

## Use case 2: marketplace watcher on the real Astronomy Shop

An emulator fitted on real Olist orders decides who buys what; its shoppers go through the real OpenTelemetry
Astronomy Shop (local containers). The scenario breaks payments (25% fail) without telling the agent. The agent reads
redacted telemetry through marketplace-recon's read-only telemetry port, runs the causal core after every 3-hour block,
rules out a demand drop, proposes turning the faulty flag off with an expected range, and applies it only after
approval, only to the local shop's flag file (read back through flagd).

One-time setup (already done on this Mac): `~/.venvs/mkt-emulator` (duckdb, polars, pydantic, numpy, scipy, pyarrow,
fastexcel), `~/.cache/otel-demo-3.1.0` (opentelemetry-demo @7a88536), `~/data/mkt/` folders, Docker Desktop.

```bash
scripts/uc2_shop.sh up --traces                         # start the shop (http://localhost:8080) + Jaeger + Prometheus
scripts/uc2_watch.sh                                    # watch it live, no recording, side by side (~8 min)
scripts/uc2_watch.sh --fast                             # same at full speed (~6 min)
scripts/uc2_record.sh --open                            # paced run, dashboard recorded → videos/uc2_screen.mp4
scripts/uc2_assemble.sh                                 # → videos/uc2_silent.mp4 (3:00, subtitles track) + NARRATION_UC2.md
scripts/uc2_voice.sh                                    # prompter: plays the silent cut, records your voice in sync
scripts/uc2_assemble.sh videos/uc2_voice.webm           # → videos/uc2.mp4 with your voice
scripts/uc2_bundle.sh                                   # → dist/uc2_bundle/ + dist/uc2_bundle.zip for someone else
scripts/uc2_shop.sh down                                # stop the shop
```

Watching live: `uc2_watch.sh` opens one page, side by side (http://127.0.0.1:8765/scenes/uc2_watch.html): left, the
agent dashboard (the real shop page a shopper just bought from, what the agent sees, its console, and every engine step
with the engine's own log line, timing and result; the proof line shows the engine's process id, its ledger file and
row count, and how fresh the data is); right, Jaeger traces, failed requests only, the Monitor tab (request, error and
latency rates per service, from Prometheus) or the shop itself. The dashboard alone: http://127.0.0.1:8765/scenes/uc2_live.html.
Two processes: the causal engine runs as its own local service (`agent/engine_service.py`, http://127.0.0.1:8770,
started by `scripts/uc2_engine.sh start` or automatically by watch/record), holding the evidence, the ledger and the
reasoning. The agent (`agent/uc2.py`) is a client: it sends what it observes and its questions over HTTP and gets the
engine's answers back; it does not import the engine. The service's status page (http://127.0.0.1:8770/, also the
"Engine service" button on the side-by-side page) lists every question with its result, policy verdict, time and a link
to the full trace and report. The dashboard only displays what the agent writes to `outputs/uc2/live.json`; its proof
line shows both process ids, the round trip, the ledger size and how fresh the data is.

```bash
scripts/uc2_engine.sh start | stop | status | logs
``` Jaeger
(http://localhost:8080/jaeger/ui/, service "checkout" or "payment") shows real requests inside the shop; during the fault,
failed payments appear as errors. Container logs: `docker logs -f --since 1s payment`; resource use: `docker stats`.
The agent never reads Jaeger or container logs: only the redacted span file, through marketplace-recon's telemetry port.

Outputs in `outputs/uc2/`: `blocks.json` (telemetry per block), `traces.json` (every engine task: stages and
durations, evidence requests, methods considered, results, report revisions, policy verdicts, ledger rows written),
`events.json`, `actions.json` (proposal, approval, sandbox receipt, one refused attempt outside the sandbox),
`console.json`, `timeline.json` (wall-clock edit points), `snapshots/` (dashboard state at each key event),
`truth.json` (the injected fault and what really failed, for the reveal only).

Compatibility notes for this Mac (in `deploy/compose.uc2.yaml`, upstream untouched): Docker Compose 2.17 lacks
`!override`; Docker Engine 23 serves API 1.42, so the collector's docker_stats receiver is pinned to it; the pinned
upstream compose preloads a module the 3.1.0 payment image lacks, so payment uses the image's own preload; its
recommendation health check needs bash, which the 3.1.0 image lacks, so it uses the 3.1.0 tag's check. Product pictures:
marketplace-recon's catalog sync points the 200 synced products at a picture the shop does not have, so the run points
each one at a picture the shop serves (`agent/uc2.py`, `fix_pictures`).

## Videos

Scenes are HTML pages that read `outputs/`; `scenes/record.mjs` renders them frame by frame in headless Chrome and
ffmpeg encodes them. `scenes/capture_live.mjs` records the live video-2 dashboard in real time while the scenario runs.
