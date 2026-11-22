# Data contract between sim/ (A) and brain/ (B)

For whoever reads this repo next, human or agent. If something here is wrong, change it in the same commit as the
code and say so in the commit message.

## What the simulator promises

- One SQLite file, `data/market.db` (not in git; generate it, see below). Tables: `facts_hourly`, `ingestion_log`,
  `releases`, `calendar`, `ground_truth`, `sim_meta`. The DDL is `docs/contracts/schema.sql`
  (`python -m sim.schema` prints it). `brain/` may read the first four only; `ground_truth` is the hidden answer.
- Timestamps are UTC hour starts, `2026-11-01T13:00:00Z`. Money is EUR. Counts are integers.
- `facts_hourly`: one row per hour and segment (country x platform x category x channel = 4 x 3 x 5 x 3 = 180 segments).
  Columns are the ones in `tests/fixture_db.py`. `stockouts` counts out-of-stock listings that customers ran into
  (about 1.5% of product views); `discounts` are platform-funded.
- `contribution = marketplace_fees + ad_spend - discounts` (as in `brain/metrics/money.yaml`).
- Releases carry a system: `payments-sdk`, `search`, `pacing`. A rollback is a new release row saying "rollback".
- Faults the simulator can inject: payment_failure, stockout_surge, search_regression, ad_budget_exhausted; innocent
  changes: promo (also in `calendar`), data_delay (only visible in `ingestion_log`). See `docs/demo.md`.

## Get the same database

```
git pull
pip install -r requirements.txt          # scipy was added
python -m sim.run --backfill 28d --force  # about 3 s, seed 42
sqlite3 data/market.db "select count(*), sum(sessions), sum(orders), round(sum(gmv),2), round(sum(ad_spend),2) from facts_hourly;"
```

Expected: `120960|3081260|181185|11631523.55|64859.08`. `tests/test_sim.py::test_default_database_has_the_published_totals`
checks the same numbers, so `pytest` tells you if your copy differs (usually a different numpy or scipy version).
If the generator changes on purpose, update the numbers in that test, in this file and say so in the commit.

## Run the pieces

```
python -m sim.live --speed 2                  # one simulated hour every 2 s
python -m sim.inject payment_failure --country DE --platform Android --now
python -m sim.inject end --id 1               # rollback
uvicorn ui.app:app --port 8000                # console; /data browses the tables read only
python -m sim.score generate && python -m sim.score score   # one run: needs the brain's replay in eval_incidents/, writes data/eval-results.md
```

See `docs/system-overview.html` for the whole picture (world, brain, console, scoring); the brain has its own page, `docs/brain-architecture.html`.
Both are generated from the code: `python docs/build_system_overview.py` and `python docs/build_brain_architecture.py`.

## Open questions for B (answer by editing this file)

1. Which command starts the monitor, so `docs/demo.md` can use it? `[B]`
2. Is `contribution` as above, and is the column list in `schema.sql` what you want? If not, change it here and in `sim/`.
3. Can each metric in `brain/metrics/*.yaml` get a `domain:` field? The console groups metrics by business domain with
   a table in `ui/domains.py`; a field on the metric would remove the second copy.
4. Will `brain/` pick up `incoming_metrics/*.yaml` (the console's "Add refund_rate" button writes there)?

## Agreed with B in the review of 21 Nov (issue 14)

1. **Clock.** `clock = MAX(facts_hourly.ts) + 1 hour`. A source's hour has arrived when `arrived_at <= clock + 10 minutes`.
   On time `arrived_at = ts + 1 h + 2..9 min`; a data delay adds `delay_hours`. The jitter must stay under 10 minutes.
2. **`first_seen`** is the newest data hour at the tick that raised the incident. The onset goes to `change.window.start`.
   The scorer measures delay as `first_seen - fault start`.
3. **Hypothesis `kind`**: `release`, `metric`, `calendar`, `freshness`. The cause of a release is built from the row:
   `release <system>: <description>`. A row whose description says rollback ends that release; it is not a new cause.
4. **Statuses `explained` and `pending`**, same folder, same file shape. `explained` carries the metric, segment, change and
   one hypothesis of kind `calendar`. `pending` carries the sources and the held hours. The scorer skips both; the console
   shows them in grey and keeps them out of the open count and the node colours. The brain writes them in the incidents
   folder as `explained-*.json` and `pending-*.json`.
5. **Result of adding a metric**: the brain writes `<name>.result.json` next to the YAML in `$INCOMING_METRICS_DIR`
   and re-exports the graph to `$GRAPH_JSON`. The console reads it with `GET /api/metric/{name}`. Fields: `name`,
   `status` (`validated`, `tentative` or `rejected`), `message` (one sentence for a person), `placed` (where it sits in
   the graph and why), `checks` (a list of `check`, `status`, `detail`, in the order they ran), `links_proposed` (the
   links it has now and the identities that were not applied, each with its reason), `formula`, `formula_source`
   (`given` or `inferred`), `alias_of` and `source` (the file) are always present; `formula` and `formula_source` are
   null when the definition was rejected before a formula was known. `data` (the hours it was checked on) is there only
   when the data were read: a result rejected at the read, SQL or formula-search step has none. The console needs only
   `status` and `message`; the rest is evidence. A YAML that is empty or still being written has no result yet.
6. **Injections start at the latest hour** (`now`), so the newest hour is rewritten; the brain re-reads the last hours on every tick.
7. **Answers to the open questions** (from B): the monitor starts with `python -m brain.run`; `contribution` and the column
   list are right; each metric may carry a `domain` field; `incoming_metrics/*.yaml` is picked up.
