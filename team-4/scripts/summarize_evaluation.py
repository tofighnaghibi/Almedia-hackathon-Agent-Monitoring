"""Summarize real monitor outputs from independently generated scoring runs (never invent incidents).

python -m scripts.summarize_evaluation --runs /path/to/runs --seeds 4,8,101,102 --holdout 101,102 \
    --commit <brain commit> --warmup-hours 80 --out docs/results.md
Expected files: seed<N>.db and seed<N>-incidents/*.json, produced by sim.score generate and brain.run --replay.

--holdout names the seeds that no version of the brain was run on before this evaluation. They get their own
line, so numbers the brain was built on are never read as independent. The script cannot check that claim;
whoever runs it makes it.
--warmup-hours is the history the replay needs before its first judgement (brain.run --replay starts after
min_history_hours + reread_hours, 80 with the defaults). With it the false-alarm rate is per watched day.

--baseline also scores the plain ranking of scripts/baseline_ranking.py and adds the section "Against a plain
ranking". It reads three kinds of folder, each written by that script with its threshold recorded (in threshold.txt
and in every incident): seed<N>-baseline-z<Z>/, the threshold sweep, on the seeds outside --holdout only;
seed<N>-baseline/, every seed at the threshold the sweep gives by the rule of pick_threshold, which --baseline-z must
name; and seed<N>-baseline-z12-all/, every seed at 12, the second point (the first version's threshold). A folder whose
recorded threshold is not the one it should hold, a --baseline-z that is not the rule's pick and a holdout seed with
sweep folders are refused: the threshold is never chosen on the holdout. The first version's numbers in the section
(FIRST_VERSION) were measured on its own output before it was replaced. Every other number in the section is counted
from the scored runs. The page also says that the grid of thresholds was fixed before the sweep ran and that the first
version had been run on the holdout; like --holdout, the script cannot check either, whoever runs it makes the claim.

The page is the rendering of the JSON written beside it and of nothing else: `from_json` rebuilds it, and a
test compares the two tracked files, so the page cannot be edited by hand without the test failing.
"""
from __future__ import annotations

import argparse
import json
import math
import os
import re
from functools import partial
from pathlib import Path

import pandas as pd

from sim import score as sc

LIMITS = [
    '- Holdout means new random seeds of the same simulator: the same four fault kinds (three of each per run), the same ranges of severity and duration and the same scoring rules as the runs the brain was built on. It does not test other kinds of fault, other sizes or another scorer.',
    '- The late-data events only move the arrival time of a source (ingestion_log); the values themselves are complete. Data that never arrives is not tested.',
    '- Events never overlap in these schedules, so two faults at the same time are not tested.',
    '- One routine deployment per simulated day (random system, country and platform) is in the data as a possible rival cause. Many faults have none close to them, and one of the same system in the same place within two hours of a fault is rare; when it happens the report can leave both supported (see the list above).',
    '- A cause is counted as correct when a supported hypothesis names a release in the fault\'s place (any system for payment, a search release in the country for search), the pacing release or ad load (ad budget), or the stockouts. The scorer does not check a payment release\'s system or any release\'s version, and does not take a point away for a second supported cause, which is why those reports are listed.',
    '- The new-metric hand-over is not part of these runs; it has its own tests (`tests/test_extension.py`).',
    '- Replays test detection across synthetic hours. Wall-clock mode follows real time and cannot compress multi-hour recovery into seconds.',
]
PLAIN = (
    'The plain ranking (`scripts/baseline_ranking.py`) is what an analyst could build in an afternoon, scored by the same '
    'scorer on the same runs. It watches the same rates in the same cuts as the brain and judges each hour on its own: '
    'every series against the mean and standard deviation of its own rate at the same hour of the day over the previous '
    '21 days, leaving out a series whose history there never varied, and it keeps the one series with the largest '
    'absolute z-score at or above the threshold. It does not use the calendar, sums over three hours, the targeted test '
    'after each release, the search over all places or the investigation of causes. Its cause, always written as '
    'supported, is the latest release whose country and platform do not contradict the place (any country when the place '
    'names none), from 12 hours before to 2 hours after the first alert; a rollback is never named; otherwise the metric '
    'that moved.'
)
STOCKOUTS = (' Of its {m} unconfirmed {causes} {where} at z {z:g}, {n} {are} in a category, where the rule names whatever '
             'release falls in the window')
FIRST = (
    ' The first version gave a history that never varied a spread of 1e-6 and had been run on the {which}: it found '
    '{detected}/{faults} at z {z:g}{now}; it named the right cause for {correct}, raised {false_alarms} false alarms '
    '({never_varied} of them from never-varied histories) and stayed silent through {silent}/{innocents} innocent events. '
    'Its floor was removed after a review of those alarms, and the threshold was then picked on the built-on seeds only.'
)
STOCKOUT = 'stockout_surge'   # its place is a category, which names no country: the cause rule takes any release there
# The first version of scripts/baseline_ranking.py (spread floor 1e-6, z 12) on the ten runs of the first holdout (seeds 101
# to 110), scored by sim/score.py
# from its own incident files before it was replaced; never_varied: its false alarms whose spread was the floor.
FIRST_VERSION = dict(z=12.0, faults=120, detected=94, correct=73, false_alarms=396, never_varied=323, innocents=80,
                     silent=33)
SECOND = 'finds more, alarms more'
SECOND_Z = 12.0           # the second point: the first version's threshold, named before this version saw the holdout
SECOND_FOLDER = f'baseline-z{SECOND_Z:g}-all'
MAX_FALSE_ALARMS = 1      # the threshold rule: the smallest z with at most this many false alarms on the built-on seeds
SWEEP_FOLDER = re.compile(r'seed(\d+)-baseline-z(\d+(?:\.\d+)?)')
RECORD = 'threshold.txt'  # written by scripts/baseline_ranking.py beside its incidents, also when it raised none


def collect(runs: Path, seeds: list[int], folder: str = 'incidents', load=sc.load_incidents) -> list[dict]:
    """Score every run from its own files (seed<N>-<folder>, read by `load`). A missing database or an empty
    incidents folder is an error."""
    reports = []
    for seed in seeds:
        path = runs / f'seed{seed}.db'
        if not path.is_file():
            raise FileNotFoundError(f'missing database: {path}')
        events, start, end = sc.load_truth(path)
        incidents = load(runs / f'seed{seed}-{folder}')
        days = (end - start + pd.Timedelta(hours=1)) / pd.Timedelta(days=1)
        reports.append(dict(seed=seed, days=days, **sc.score(events, incidents, end, days)))
    return reports


def _number(value) -> str:
    if value is None:
        return 'none'
    return f'{value:g}' if isinstance(value, (int, float)) and not isinstance(value, bool) else repr(value)


def plain_incidents(folder: Path, z: float) -> list[dict]:
    """The plain ranking's incidents in a folder that scripts/baseline_ranking.py wrote at threshold z: its record
    (threshold.txt) and every incident must say z. With the record, a folder without incidents is a measured zero."""
    folder = Path(folder)
    if not folder.is_dir():
        raise ValueError(f'{folder}: plain-ranking folder does not exist')
    record = folder / RECORD
    if not record.is_file():
        raise ValueError(f'{folder}: no {RECORD}, so not a finished run of scripts/baseline_ranking.py')
    try:
        recorded = float(record.read_text(encoding='utf-8').strip())
    except ValueError:
        raise ValueError(f'{record}: not a number') from None
    incidents = sc.load_incidents(folder) if any(folder.glob('*.json')) else []
    said = []                                         # the distinct thresholds the record and the incidents give
    for v in [recorded] + [i.get('threshold') for i in incidents]:
        if v not in said:
            said.append(v)
    if any(v != z for v in said):
        raise ValueError(f"{folder}: recorded threshold {', '.join(map(_number, said))}, not {z:g}")
    return incidents


def _mean(values: list[float]) -> str:
    return f'{sum(values) / len(values):.2f}' if values else 'n/a'


def tally(reports: list[dict], warmup_hours: float = 0) -> dict:
    faults = [p for r in reports for p in r['per_fault']]
    found = [p for p in faults if p['detected']]
    right = [p for p in found if p.get('correct_cause')]
    days = sum(r['days'] for r in reports)
    return dict(
        runs=len(reports), faults=len(faults), detected=len(found),
        localized=sum(bool(p.get('localized')) for p in found), correct=len(right),
        ties=sum(p.get('supported_causes', 1) > 1 for p in right),
        false_alarms=sum(r['false_alarms'] for r in reports), days=days,
        watched=days - len(reports) * warmup_hours / 24,
        innocents=sum(r['innocents'] for r in reports), silent=sum(r['innocents_silent'] for r in reports),
        mean_delay=_mean([p['delay_h'] for p in found]),
    )


def _row(label: str, t: dict) -> str:
    return (f"| {label} | {t['runs']} | {t['detected']}/{t['faults']} | {t['localized']}/{t['detected']} | "
            f"{t['correct']}/{t['detected']} | {t['false_alarms']} | {t['silent']}/{t['innocents']} | {t['mean_delay']} |")


def _rate(label: str, t: dict, watched: bool) -> str:
    days = t['watched'] if watched else t['days']
    per_day = f"{t['false_alarms'] / days:.3f}/day" if days > 0 else 'n/a'
    span = f"in {days:.0f} watched days" if watched else f"over {days:.0f} simulated days"
    return f"{label}: {t['false_alarms']} {span} ({per_day})"


def _names(reports: list[dict]) -> str:
    return ', '.join(str(r['seed']) for r in reports)


def _scope(scope: dict) -> str:
    return ', '.join(f"{d}={scope[d]}" for d in sc.DIMS if d in scope)


def pick_threshold(sweep: list[dict]) -> tuple[float, bool]:
    """The rule for the plain ranking's threshold, applied to the sweep on the seeds the brain was built on: the
    smallest z with at most MAX_FALSE_ALARMS false alarms; when none gets there, the z with the fewest (the smaller on
    a tie). Returns the z and whether the bound was reached."""
    within = [s['z'] for s in sweep if s['false_alarms'] <= MAX_FALSE_ALARMS]
    if within:
        return min(within), True
    fewest = min(s['false_alarms'] for s in sweep)
    return min(s['z'] for s in sweep if s['false_alarms'] == fewest), False


def unconfirmed(runs: list[dict]) -> tuple[int, int]:
    """The detected faults whose report does not support the injected cause, as (stock-outs in a category, all of
    them). A stock-out's place is a category, which names no country, so the plain ranking's cause rule takes whatever
    release falls in its window there."""
    wrong = [p for r in runs for p in r['per_fault'] if p['detected'] and not p.get('correct_cause')]
    return sum(p['kind'] == STOCKOUT and 'category' in p['fault'] and 'country' not in p['fault'] for p in wrong), len(wrong)


def _by_kind(runs: list[dict]) -> str:
    """'26/30 ad_budget_exhausted, 0/30 payment_failure, ... and 23/30 stockout_surge': detected of all, per fault kind."""
    kinds: dict[str, list[int]] = {}
    for r in runs:
        for p in r['per_fault']:
            k = kinds.setdefault(p['kind'], [0, 0])
            k[0] += bool(p['detected'])
            k[1] += 1
    parts = [f'{found}/{total} {kind}' for kind, (found, total) in sorted(kinds.items())]
    return ', '.join(parts[:-1]) + ' and ' + parts[-1] if len(parts) > 1 else ''.join(parts)


def _picked(sweep: list[dict], built: list[dict], everything: list[dict]) -> str:
    """The picked threshold's label: what the rule asked of it on the built-on seeds and the brain's false alarms
    there and in all runs, all counted. Without a sweep nothing is claimed."""
    if not sweep:
        return ''
    _, reached = pick_threshold(sweep)
    asked = ('at most {} false alarm on the built-on seeds'.format(MAX_FALSE_ALARMS) if reached else
             'the fewest false alarms on the built-on seeds, {}'.format(min(s['false_alarms'] for s in sweep)))
    brain = (f"; the brain: {tally(built)['false_alarms']} there, {tally(everything)['false_alarms']} in all runs"
             if built else '')
    return f' (threshold picked for {asked}{brain})'


def sweep_folders(runs: Path) -> dict[int, dict[float, str]]:
    """Per seed, the thresholds of its sweep folders (seed<N>-baseline-z<Z>) and their folder suffixes."""
    found: dict[int, dict[float, str]] = {}
    for p in sorted(runs.iterdir()) if runs.is_dir() else []:
        m = SWEEP_FOLDER.fullmatch(p.name)
        if m and p.is_dir():
            found.setdefault(int(m.group(1)), {})[float(m.group(2))] = f'baseline-z{m.group(2)}'
    return found


def _against(reports: list[dict], holdout: set, warmup_hours: float, baseline: dict) -> list[str]:
    """The section that sets the brain beside the plain ranking of scripts/baseline_ranking.py: the brain, the plain
    ranking at the threshold the rule picked and at the second point, on the summary's columns; one sentence per
    threshold on what it finds by fault kind; the sweep on the built-on seeds; one paragraph on what the plain ranking
    is, what its cause rule gets wrong and what its first version did."""
    z, second, first = baseline['z'], baseline.get('second'), baseline.get('first_version')
    has_holdout = any(r['seed'] in holdout for r in reports)
    sweep = baseline.get('sweep') or []
    fresh = lambda rs: [r for r in rs if r['seed'] in holdout]                            # noqa: E731
    keep, where = (fresh, 'on the holdout') if has_holdout else ((lambda rs: rs), 'in all runs')
    points = [(z, baseline['runs'])] + ([(second['z'], second['runs'])] if second else [])
    picked = _picked(sweep, [r for r in reports if r['seed'] not in holdout], reports)

    def rows(label: str, only) -> list[str]:
        out = [_row(f'Brain, {label}', tally(only(reports), warmup_hours)),
               _row(f'Plain ranking at z {z:g}{picked}, {label}', tally(only(baseline['runs']), warmup_hours))]
        if second:
            out.append(_row(f"Plain ranking at z {second['z']:g} ({SECOND}), {label}", tally(only(second['runs']), warmup_hours)))
        return out

    lines = ['', '## Against a plain ranking', '',
             '| Monitor, seeds | Runs | Faults detected | Localized | Correct supported cause | False alarms | Innocent events silent | Mean delay (h) |',
             '|---|---:|---:|---:|---:|---:|---:|---:|']
    if has_holdout:
        lines += rows('holdout', fresh)
    lines += rows('all runs', lambda rs: rs)
    kinds = []
    for pz, runs in points:
        found = _by_kind(keep(runs))
        if found:
            kinds.append(f"At z {pz:g} {'it' if kinds else 'the plain ranking'} finds {found} faults {where}.")
    if kinds:
        lines += ['', ' '.join(kinds)]
    if sweep:
        chosen, reached = pick_threshold(sweep)
        grid = ', '.join(format(s['z'], 'g') for s in sorted(sweep, key=lambda s: s['z']))
        rule = (f"The threshold was picked on the {sweep[0]['runs']} runs of seeds used while the brain was built, never on "
                f'the holdout, by one rule: the smallest threshold tried with at most {MAX_FALSE_ALARMS} false alarm there, '
                'else the one with the fewest. '
                + (f'It gives {chosen:g}.' if reached else
                   f'None tried had at most {MAX_FALSE_ALARMS} false alarm there, so it gives {chosen:g}, the one with the '
                   'fewest.')
                + f' The grid of thresholds tried ({grid}) was fixed before the sweep ran.')
        if second:
            rule += (f" The second point, {second['z']:g}, is the first version's threshold"
                     + ('; both were fixed before this version was run on the holdout.' if has_holdout else '.'))

        def label(s: dict) -> str:
            marks = (['picked'] if s['z'] == z else []) + (['second point'] if second and s['z'] == second['z'] else [])
            return f"{s['z']:g}" + (f" ({', '.join(marks)})" if marks else '')

        lines += ['', rule, '',
                  '| Threshold (z), seeds used while the brain was built | Runs | Faults detected | Localized | Correct supported cause | False alarms | Innocent events silent | Mean delay (h) |',
                  '|---|---:|---:|---:|---:|---:|---:|---:|']
        lines += [_row(label(s), s) for s in sorted(sweep, key=lambda s: s['z'])]
    paragraph = PLAIN
    wrong = [(pz, *unconfirmed(keep(runs))) for pz, runs in points]
    wrong = [(pz, n, m) for pz, n, m in wrong if m]
    if wrong:
        (pz, n, m), rest = wrong[0], wrong[1:]
        paragraph += (STOCKOUTS.format(m=m, causes='cause' if m == 1 else 'causes', where=where, z=pz, n=n,
                                       are='is a stock-out' if n == 1 else 'are stock-outs')
                      + ''.join(f'; at z {z2:g}, {n2} of {m2}' for z2, n2, m2 in rest) + '.')
    if first:
        same = [runs for pz, runs in points if pz == first['z']]       # this version at the first version's threshold
        now = ''
        # its numbers are from the first holdout (seeds 101 to 110); compare only when this page's holdout is that one
        first_holdout = set(range(101, 111))
        on_first = has_holdout and set(holdout) <= first_holdout
        if on_first and same:
            t = tally(fresh(same[0]))
            now = f", this version {t['detected']}/{t['faults']}"
        which = 'holdout' if on_first or not has_holdout else 'first holdout (seeds 101 to 110, not this page\'s holdout)'
        paragraph += FIRST.format(**first, now=now, which=which)
    lines += ['', paragraph]
    return lines


def render(reports: list[dict], holdout=(), commit: str | None = None, warmup_hours: float = 0,
           baseline: dict | None = None) -> list[str]:
    """The page as lines. `holdout`: seeds no version of the brain was run on before this evaluation. `baseline`:
    the plain ranking's picked threshold `z` and its scored `runs` (same seeds), the `second` point (`z`, `runs`), the
    `sweep` on the built-on seeds (tallies per threshold) and the `first_version`'s numbers on the holdout."""
    holdout = set(holdout)
    fresh = [r for r in reports if r['seed'] in holdout]
    seen = [r for r in reports if r['seed'] not in holdout]
    everything = tally(reports, warmup_hours)
    groups = ([('holdout', tally(fresh, warmup_hours))] if fresh and seen else []) + [('all runs', everything)]
    mark = lambda r: ' (holdout)' if r['seed'] in holdout else ''   # noqa: E731

    lines = ['# Results — simulated marketplace', '',
             'Generated by `python -m scripts.summarize_evaluation` from actual `brain.run --replay` incident files'
             + (f', brain at commit `{commit}`.' if commit else '.'),
             'Synthetic data, 21 clean baseline days per run, routine deployments included. These are engineering checks '
             'on a simulator, not production performance.']
    if fresh:
        lines.append(f'Holdout seeds ({_names(fresh)}) were generated and replayed once for this page: no version of the '
                     'brain was run on them before.'
                     + (f' The other seeds ({_names(seen)}) were also run while the brain was built, so their numbers are '
                        'not independent.' if seen else ''))
    else:
        lines.append('None of these seeds is a holdout: all were also run while the brain was built, so the numbers are '
                     'not independent.')
    lines += ['',
              '| Seeds | Runs | Faults detected | Localized | Correct supported cause | False alarms | Innocent events silent | Mean delay (h) |',
              '|---|---:|---:|---:|---:|---:|---:|---:|']
    if fresh:
        lines.append(_row('Holdout (run once)', tally(fresh, warmup_hours)))
    if fresh and seen:
        lines.append(_row('Used while the brain was built', tally(seen, warmup_hours)))
    lines.append(_row('All', everything))
    lines += ['',
              'Localized and correct cause are counted among the detected faults. A cause counts only when the report '
              'names it as `supported`. Delay is counted from the first affected data hour (0 means caught in that hour).']
    if everything['ties']:
        lines.append(f"In {everything['ties']} of the {everything['correct']} reports with the correct cause a second cause "
                     'is supported beside it (listed below).')
    rates = 'False alarms. ' + '; '.join(_rate(label, t, bool(warmup_hours)) for label, t in groups) + '.'
    if warmup_hours:
        data_days = ' and '.join(format(t['days'], '.0f') for _, t in groups)
        rates += (f' Watched days leave out the first {warmup_hours:g} hours of each run, before the replay\'s first '
                  f'judgement ({data_days} days of data).')
    lines.append(rates)
    if baseline:
        lines += _against(reports, holdout, warmup_hours, baseline)

    lines += ['', '## By fault kind (all runs)', '',
              '| Fault | Detected | Localized | Correct supported cause | Mean delay (h) |', '|---|---:|---:|---:|---:|']
    kinds: dict[str, list[dict]] = {}
    for r in reports:
        for p in r['per_fault']:
            kinds.setdefault(p['kind'], []).append(p)
    for kind in sorted(kinds):
        ps = kinds[kind]
        found = [p for p in ps if p['detected']]
        lines.append(f"| {kind} | {len(found)}/{len(ps)} | {sum(bool(p.get('localized')) for p in found)}/{len(found)} | "
                     f"{sum(bool(p.get('correct_cause')) for p in found)}/{len(found)} | {_mean([p['delay_h'] for p in found])} |")

    lines += ['', '## Per run', '',
              '| Run | Faults detected | Correct supported cause (of detected) | False alarms | Innocent events silent |',
              '|---|---:|---:|---:|---:|']
    for r in reports:
        correct = sum(bool(p.get('correct_cause')) for p in r['per_fault'])
        lines.append(f"| Seed {r['seed']}, {r['days']:.0f} days{', holdout' if r['seed'] in holdout else ''} | "
                     f"{r['detected']}/{r['faults']} | {correct} of {r['detected']} | {r['false_alarms']} | "
                     f"{r['innocents_silent']}/{r['innocents']} |")

    lines += ['', '## Misses and unsupported causes', '']
    listed = 0
    for r in reports:
        for p in r['per_fault']:
            if p['detected'] and p.get('localized') and p.get('correct_cause'):
                continue
            if not p['detected']:
                why = 'not detected (no matching monitor incident)'
            else:
                where = 'detected and localized' if p.get('localized') else 'detected, not localized to the injected scope'
                wrong = p.get('supported_causes', 0)
                why = where + ('' if p.get('correct_cause') else
                               f'; {wrong} cause(s) supported, none of them the injected one' if wrong else
                               '; cause not confirmed')
            lines.append(f"- Seed {r['seed']}{mark(r)}: {p['kind']} at {p['fault'].get('start')}, {_scope(p['fault'])}: {why}.")
            listed += 1
    if not listed:
        lines.append('None: every fault was detected, localized and given its cause.')

    lines += ['', '## Reports that support more than one cause', '']
    listed = 0
    for r in reports:
        for p in r['per_fault']:
            extra = p.get('supported_causes', 1) - 1
            if p['detected'] and p.get('correct_cause') and extra > 0:
                lines.append(f"- Seed {r['seed']}{mark(r)}: {p['kind']} at {p['fault'].get('start')}, {_scope(p['fault'])}, "
                             f"incident {p.get('incident')}: the right cause is supported, and {extra} other beside it.")
                listed += 1
    if not listed:
        lines.append('None.')

    lines += ['', '## False alarms', '']
    listed = 0
    for r in reports:
        alarms = r.get('false_alarm_incidents')
        if alarms is None and r['false_alarms']:
            lines.append(f"- Seed {r['seed']}{mark(r)}: {r['false_alarms']} false alarm(s), not itemised by the scorer.")
            listed += 1
        for a in alarms or []:
            status = f" ({a['status']})" if a.get('status') else ''
            lines.append(f"- Seed {r['seed']}{mark(r)}: {a['id']}, {a.get('metric') or 'metric not named'} in "
                         f"{_scope(a.get('segment') or {}) or 'the whole market'}, first seen {a['first_seen']}{status}.")
            listed += 1
    if not listed:
        lines.append('None.')

    lines += ['', '## Limits', ''] + LIMITS
    return lines


def write(out: Path, lines: list[str], reports: list[dict], meta: dict | None = None) -> None:
    """UTF-8 and LF whatever the platform: the page has an em dash and is tracked with LF endings.
    The JSON beside the page holds everything the page was rendered from."""
    meta = dict(meta or {})
    saved = {'holdout': sorted(meta.get('holdout') or []), 'commit': meta.get('commit'),
             'warmup_hours': meta.get('warmup_hours') or 0, 'runs': reports}
    if meta.get('baseline'):
        saved['baseline'] = meta['baseline']
    texts = {out.with_suffix('.json'): json.dumps(saved, indent=2, default=str) + '\n', out: '\n'.join(lines) + '\n'}
    out.parent.mkdir(parents=True, exist_ok=True)
    temps = {target: target.with_name(target.name + '.tmp') for target in texts}
    try:
        for target, text in texts.items():          # both are complete on disk before either takes its place
            with open(temps[target], 'w', encoding='utf-8', newline='\n') as f:
                f.write(text)
        for target in texts:                        # the JSON first: the page is never newer than its numbers
            os.replace(temps[target], target)
    finally:
        for temp in temps.values():
            temp.unlink(missing_ok=True)


def from_json(path: Path) -> list[str]:
    """The page, rebuilt from the JSON that was written beside it."""
    saved = json.loads(Path(path).read_text(encoding='utf-8'))
    return render(saved['runs'], saved['holdout'], saved['commit'], saved['warmup_hours'], saved.get('baseline'))


def _seeds(text: str) -> list[int]:
    return [int(s) for s in text.split(',') if s.strip()]


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--runs', type=Path, required=True)
    ap.add_argument('--seeds', default='4,8')
    ap.add_argument('--holdout', default='', help='seeds, among --seeds, that the brain was never run on before')
    ap.add_argument('--commit', help='the commit of the brain that produced the incident files')
    ap.add_argument('--warmup-hours', type=float, default=0, help='history per run before the first judgement')
    ap.add_argument('--out', type=Path, default=Path('docs/results.md'))
    ap.add_argument('--baseline', action='store_true',
                    help='also score the plain ranking (seed<N>-baseline) and its threshold sweep (seed<N>-baseline-z<Z>)')
    ap.add_argument('--baseline-z', type=float, help='the threshold the plain ranking in seed<N>-baseline was run with')
    args = ap.parse_args(argv)
    if not (math.isfinite(args.warmup_hours) and args.warmup_hours >= 0):
        ap.error('the warm-up must be a number of hours from 0')
    if args.baseline_z is not None and not args.baseline:
        ap.error('--baseline-z is given without --baseline')
    if args.baseline and args.baseline_z is None:
        ap.error('--baseline needs --baseline-z, the threshold the plain ranking was run with')
    if args.baseline and not (math.isfinite(args.baseline_z) and args.baseline_z > 0):
        ap.error('the plain ranking\'s threshold must be a positive number')
    if args.out.suffix.lower() == '.json':
        ap.error('the page cannot be a .json file: its numbers are written beside it under that name')
    try:
        seeds, holdout = _seeds(args.seeds), _seeds(args.holdout)
    except ValueError:
        ap.error('seeds must be whole numbers separated by commas')
    if not seeds:
        ap.error('no seeds given')
    if len(set(seeds)) != len(seeds):
        ap.error('seeds must be distinct')
    for s in holdout:
        if s not in seeds:
            ap.error(f'holdout seed {s} is not among the runs (--seeds)')
    try:
        reports = collect(args.runs, seeds)
    except (FileNotFoundError, ValueError) as e:      # a missing database, a missing, empty or malformed incidents folder
        ap.error(str(e))
    for r in reports:
        if args.warmup_hours / 24 >= r['days']:
            ap.error(f"the warm-up is not shorter than run {r['seed']} ({r['days']:.0f} days)")
    meta = dict(holdout=holdout, commit=args.commit, warmup_hours=args.warmup_hours)
    if args.baseline:
        meta['baseline'] = _baseline(ap, args, seeds, holdout)
    write(args.out, render(reports, **meta), reports, meta)
    t = tally(reports)
    print(f"{args.out}: {t['detected']}/{t['faults']} detected; {t['false_alarms']} false alarms; "
          f"{t['silent']}/{t['innocents']} innocent events silent")
    if args.baseline:
        for point in (meta['baseline'], meta['baseline']['second']):
            b = tally(point['runs'])
            print(f"plain ranking at z {point['z']:g}: {b['detected']}/{b['faults']} detected; {b['false_alarms']} false "
                  f"alarms; {b['silent']}/{b['innocents']} innocent events silent")
    return 0


def _baseline(ap, args, seeds: list[int], holdout: list[int]) -> dict:
    """The plain ranking's threshold sweep, its runs at the threshold the sweep gives and at the second point, each
    folder read only at the threshold it recorded."""
    folders = sweep_folders(args.runs)
    for s in holdout:
        if folders.get(s):
            ap.error(f'holdout seed {s} has threshold-sweep folders (seed{s}-baseline-z...): the threshold must be '
                     'chosen without the holdout')
    built = [s for s in seeds if s not in holdout]
    if not built:
        ap.error('--baseline needs seeds outside the holdout to choose its threshold on')
    tried = folders.get(built[0], {})
    for s in built:
        if folders.get(s, {}) != tried:
            ap.error(f'the threshold sweep differs between seeds: seed {s} has {sorted(folders.get(s, {}))}, '
                     f'seed {built[0]} has {sorted(tried)}')
    if not tried:
        ap.error('no threshold sweep (seed<N>-baseline-z<Z> folders) for the seeds outside the holdout')
    try:
        sweep = [dict(z=z, **tally(collect(args.runs, built, folder, partial(plain_incidents, z=z)), args.warmup_hours))
                 for z, folder in sorted(tried.items())]
    except (FileNotFoundError, ValueError) as e:
        ap.error(str(e))
    chosen, _ = pick_threshold(sweep)
    if chosen != args.baseline_z:
        ap.error(f'--baseline-z {args.baseline_z:g} is not the threshold the sweep gives ({chosen:g}): the smallest with '
                 f'at most {MAX_FALSE_ALARMS} false alarm on the seeds outside the holdout, else the one with the fewest')
    try:
        runs = collect(args.runs, seeds, 'baseline', partial(plain_incidents, z=args.baseline_z))
        second = collect(args.runs, seeds, SECOND_FOLDER, partial(plain_incidents, z=SECOND_Z))
    except (FileNotFoundError, ValueError) as e:
        ap.error(str(e))
    return dict(z=args.baseline_z, runs=runs, second=dict(z=SECOND_Z, runs=second), sweep=sweep,
                first_version=dict(FIRST_VERSION))


if __name__ == '__main__':
    raise SystemExit(main())
