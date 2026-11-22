# How we work on this repository

Two people, one day, two agents. The aim is that nothing the other side depends on changes without the other side
seeing it first, and that review stays attached to the change it is about.

## Two kinds of change

**Shared surface: open a pull request and wait for the other side's review.**
These are the places where `sim/`, `ui/` and `brain/` meet; almost every problem found so far was here.

- `docs/contracts/` and `sim/schema.py` (tables, columns, units, the clock rule)
- any change to what the four readable tables contain, or what ui/ reads from incidents/, the graph export or incoming_metrics/ (including changes in sim/inject.py, sim/world.py, sim/live.py and ui/app.py)
- scripts/check_guardrails.py, tests/fixture_db.py and tests/conftest.py (shared checks and fixtures)
- the incident file format (README 3.1, `docs/contracts/incident_example.json`) and the statuses
- `sim/score.py` rules (what counts as detected, localized, the right cause, a false alarm)
- `README.md` sections that describe contracts, `requirements.txt`, `.github/`, `docs/GUARDRAILS.md`, this file

**Own directory: push to `main` once `pytest` and `python scripts/check_guardrails.py` pass.**
`sim/` and `ui/` are A's, `brain/` is B's. Do not edit the other side's directory; ask in a pull request or an issue.

If a change touches both, split it: the shared part goes through a pull request first.

## A pull request

- One topic per pull request, small enough to read in a few minutes. Branch from the current `main`.
- Say what changed, why, and **how you checked it**: the commands you ran and what they printed. Say what you did not check.
- Open it as soon as the change is ready. Do not stack other changes on it.
- The author merges after approval, with a merge commit that keeps the history readable. No force-push to `main`.
- If the other side has not answered in **20 minutes** and the change only adds (a new optional field that existing readers safely ignore;
  nothing existing changes meaning), the author may merge when CI is green and write "merged without review, additive" in
  the pull request. No CI run counts as not green: rebase/merge current main first. New statuses require compatible readers and review. Anything that changes an existing meaning waits for review.

## A review

- Say which commit you reviewed (the pull request's head, `abc1234`). If the author pushed more since, say what you did not see.
- Put each remark on the line it is about, and start it with one of:
  **must fix** (a contract break, a failing check, a guardrail), **should fix** (likely to cost points or time),
  **note** (take it or leave it).
- Try to refute a finding before you post it: reproduce it, then look for the reason it might not be one. Keep what survives.
- Approve when there is no open **must fix**. The author answers every remark: fixed in `abc1234`, or why not.
- A human on each side stays responsible for what is merged, even when an agent wrote the change or the review.

## Issues

Use an issue only for a question or a decision that needs both sides, or for a problem found after a merge. Link the pull
request that caused it or that fixes it. Review of code goes on the pull request.

## Checks that run by themselves

Every pull request and every push to `main` runs `python scripts/check_guardrails.py` and `pytest` (`.github/workflows/check.yml`).
Green is the minimum, not the review.

## What a reviewer looks at

1. Does it keep the data contract (`docs/contracts/README.md`): columns, timestamp format, readable tables, the clock rule?
2. Does it keep the six guardrails in `docs/GUARDRAILS.md`, above all: `brain/` never reads the answer, no scenario is
   written into `brain/`, failure never turns into "all normal"?
3. Does the demo still work end to end (`docs/demo.md`)? Is there a test that fails if it breaks?
4. Is a number on a slide or a page computed from the data or the scorer, and not typed in?
