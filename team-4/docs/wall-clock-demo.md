# Real-time local demo

Run from the repository root. The simulator still produces synthetic hourly data. Wall mode aligns its latest
hour with the real current hour; current-hour totals are synthetic, not observed partial-hour totals.
The UI shows Europe/Berlin; database and incident contracts stay UTC. Detection across several hours still takes
real hours in this mode. Use the existing accelerated replay for a three-minute pitch.

Use a NEW database and separate runtime output folders; preserve the old replay database and output.

```
python -m sim.run --backfill 28d --until-now --db data/realtime.db
python -m sim.live --wall-clock --db data/realtime.db
```

For the UI set MARKET_DB=data/realtime.db, MARKET_CLOCK=wall, INCIDENTS_DIR=incidents-wall,
INCOMING_METRICS_DIR=incoming_metrics-wall, then start uvicorn ui.app:app --port 8000.
Start the monitor with the SAME paths (`python -m brain.run --db data/realtime.db --incidents incidents-wall --incoming incoming_metrics-wall`). UI state/inject/rollback also synchronize on access,
so an idle page catches up when used. A future database or one over 48h behind is rejected; never silently truncate it.
Initialize a new database with --until-now instead. All JSON contract timestamps remain UTC.

Reusing an active fault is still refused to prevent stacking. End the active fault or use another segment.
Replay mode (default MARKET_CLOCK=replay) and sim.live --speed 2 retain their previous semantics.
