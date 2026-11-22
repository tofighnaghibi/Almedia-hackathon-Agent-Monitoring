# Guardrails

Short rules for both of us. They exist so the demo can be trusted and so neither of us has to untangle the other's code at 03:00. Adapted for a 30-hour build; the automatic part is `scripts/check_guardrails.py`.

## Hard stops (fix before you push)

1. **No secrets in git.** Keys live in `.env` (ignored). Never paste a key into code, a README, a log or a commit message.
2. **`brain/` never reads the answer.** `ground_truth` and the injection names are for scoring only. If `brain/` touches them, the demo is a fake. This is checked automatically.
3. **No hard-coded scenarios in `brain/`.** The investigator may not contain "if DE and Android then payments release". It must reach that result by following the graph.
4. **Failure must look like failure.** A query timeout, a missing source or a failed LLM call is never turned into "0", an empty list or "all normal". Missing data means the hour is *pending*, not healthy.
5. **Do not weaken a check to get green.** No deleting a failing test, loosening a threshold or catching an exception just to silence it. If a threshold changes, say why in the commit.
6. **Do not change a frozen contract alone.** Column names, units and the timestamp format (UTC, hour start, `2026-11-01T13:00:00Z`) change through the shared-surface pull request process in CONTRIBUTING.md, with matching docs/contracts updates.

## Defaults that keep the demo alive

- **The core loop must run without an LLM.** Every LLM call has a timeout (20 s) and a template fallback.
- **Own your directory.** A edits `sim/` and `ui/`, B edits `brain/`. Shared surfaces follow CONTRIBUTING.md: pull request and the other side's review.
- **Small commits, `git pull --rebase` before every push.** Never commit `.env`, `data/` or `incidents/`.
- **Say what you ran.** A commit that claims "works" names the command and the result. "Should work" is not a result.
- **Numbers on stage come from the harness.** Detection rate, false alarms and delay are produced by the scoring script, not picked by hand. State on the slides that the data is simulated.
- **Blocked for 30 minutes → tell the other and cut scope.**

## Run it

```
python scripts/check_guardrails.py      # exit code 0 = pass
```

Run it before every push. To run it automatically:

```
printf '#!/bin/sh\npython scripts/check_guardrails.py\n' > .git/hooks/pre-push && chmod +x .git/hooks/pre-push
```

What it checks: tracked and reachable-history secret patterns (redacted diagnostics, including UTF-16 and .env variants), tracked runtime data, whole-word hidden-answer names, brain imports from sim/ui, direct SQLite outside brain/data.py and local API routes. tests/test_guardrails.py also runs it in pytest. These static checks do not prove absence of arbitrary dynamic indirection or judge rules 3, 4 and 5; review those in each other's pull.
