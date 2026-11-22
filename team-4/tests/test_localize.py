"""Localization: every place the dimensions can name is scored (README 3.5)."""
import itertools

import numpy as np
import pytest

from brain.localize import choose, scan

DIMS = ("country", "platform", "category", "channel")
VALUES = (("DE", "FR", "ES", "PL"), ("iOS", "Android", "Web"),
          ("electronics", "fashion", "home", "beauty", "sports"), ("organic", "ads", "recommendations"))
KEYS = list(itertools.product(*VALUES))


def cells(shift=None, factor=0.6, rate=0.9, volume=40, seed=0):
    """Counts per finest cell: attempts around `volume`, successes at `rate`, multiplied by `factor`
    in the cells for which shift(key) is true."""
    rng = np.random.default_rng(seed)
    den = rng.poisson(volume, len(KEYS)).astype(float)
    p = np.array([rate * (factor if shift and shift(dict(zip(DIMS, k))) else 1.0) for k in KEYS])
    num = rng.binomial(den.astype(int), p).astype(float)
    return num, den, np.full(len(KEYS), rate)


def test_every_place_is_scored():
    num, den, rate = cells()
    places = scan(DIMS, KEYS, num, den, rate)
    assert len(places) == 5 * 4 * 6 * 4          # 479 places and the whole
    assert len({tuple(sorted(p.segment.items())) for p in places}) == len(places)


@pytest.mark.parametrize("shift, expected", [
    (lambda c: c["country"] == "DE" and c["platform"] == "Android", {"country": "DE", "platform": "Android"}),
    (lambda c: c["country"] == "FR", {"country": "FR"}),
    (lambda c: c["category"] == "fashion", {"category": "fashion"}),
    (lambda c: c["platform"] == "iOS" and c["channel"] == "ads", {"platform": "iOS", "channel": "ads"}),
    (lambda c: c["country"] == "PL" and c["category"] == "home", {"country": "PL", "category": "home"}),
    (lambda c: True, {}),
])
def test_the_place_of_a_common_shift_wins(shift, expected):
    for seed in range(5):
        num, den, rate = cells(shift, seed=seed)
        best = scan(DIMS, KEYS, num, den, rate)[0]
        assert best.segment == expected, (seed, best.segment)
        assert best.share > 0.8
        assert best.observed < best.expected


def test_no_shift_scores_low_everywhere():
    for seed in range(5):
        num, den, rate = cells(seed=seed)
        assert scan(DIMS, KEYS, num, den, rate)[0].score < 3.0


def test_a_tiny_cell_with_a_wild_ratio_does_not_beat_the_real_place():
    # PL x iOS at night: 16 of 25 against 0.94, and one of its finest cells happens to be 0 of 3
    den = np.zeros(len(KEYS))
    num = np.zeros(len(KEYS))
    inside = [i for i, k in enumerate(KEYS) if k[0] == "PL" and k[1] == "iOS"]
    counts = [(3, 0), (4, 3), (3, 2), (5, 4), (4, 3), (3, 2), (3, 2)]
    for i, (n, k) in zip(inside, counts):
        den[i], num[i] = n, k
    others = [i for i, k in enumerate(KEYS) if not (k[0] == "PL" and k[1] == "iOS")]
    rng = np.random.default_rng(3)
    den[others] = rng.poisson(6, len(others))
    num[others] = rng.binomial(den[others].astype(int), 0.94)
    best = scan(DIMS, KEYS, num, den, np.full(len(KEYS), 0.94))[0]
    assert best.segment == {"country": "PL", "platform": "iOS"}


def test_a_ratio_that_is_not_a_proportion_is_localized_by_its_spread():
    rng = np.random.default_rng(1)
    den = rng.poisson(60, len(KEYS)).astype(float)
    load = np.array([3.0 * (3.0 if k[0] == "PL" else 1.0) for k in KEYS])
    num = rng.poisson(den * load).astype(float)
    best = scan(DIMS, KEYS, num, den, np.full(len(KEYS), 3.0), kind="ratio", unit_var=3.0)[0]
    assert best.segment == {"country": "PL"}
    assert best.observed > 2.5 * best.expected


def test_the_monitors_place_is_kept_unless_another_is_clearly_better():
    shift = lambda c: c["country"] == "DE" and c["platform"] == "Android"
    num, den, rate = cells(shift)
    places = scan(DIMS, KEYS, num, den, rate)
    same = choose(places, {"country": "DE", "platform": "Android"})
    assert same.place.segment == {"country": "DE", "platform": "Android"} and same.verdict == "confirmed" and same.clear
    wider = choose(places, {"country": "DE"})            # the monitor only saw the country: narrowed
    assert wider.place.segment == {"country": "DE", "platform": "Android"} and wider.verdict == "refined"
    elsewhere = choose(places, {"country": "FR"})        # another incident's place: not moved to this one
    assert elsewhere.place.segment == {"country": "FR"} and elsewhere.verdict == "kept"


def test_two_shifted_places_are_reported_as_not_clear():
    shift = lambda c: (c["country"] == "DE" and c["platform"] == "Android") or (c["country"] == "FR" and c["platform"] == "Web")
    num, den, rate = cells(shift, volume=60)
    result = choose(scan(DIMS, KEYS, num, den, rate), {"country": "DE", "platform": "Android"})
    assert result.place.segment == {"country": "DE", "platform": "Android"}
    assert not result.clear and result.rival is not None
    assert result.rival.segment == {"country": "FR", "platform": "Web"}


def test_a_place_is_not_narrowed_while_the_rest_of_it_is_not_quiet():
    # all of FR moved, half of its cells up and half down (a rate that changed direction, summed over both
    # phases): one wild cell must not be taken for the place, because the rest of FR is anything but quiet
    rng = np.random.default_rng(5)
    den = rng.poisson(200, len(KEYS)).astype(float)
    flip = rng.random(len(KEYS)) < 0.5
    p = np.array([0.5 * ((1.35 if f else 0.65) if k[0] == "FR" else 1.0) for k, f in zip(KEYS, flip)])
    wild = next(i for i, k in enumerate(KEYS) if k[0] == "FR")
    p[wild] = 0.05
    num = rng.binomial(den.astype(int), p).astype(float)
    places = scan(DIMS, KEYS, num, den, np.full(len(KEYS), 0.5))
    assert places[0].segment != {"country": "FR"} and places[0].segment.get("country") == "FR"   # the trap is set
    result = choose(places, {"country": "FR"})
    assert result.place.segment == {"country": "FR"} and result.verdict == "kept"


def test_a_place_is_narrowed_when_the_rest_of_it_is_quiet():
    shift = lambda c: c["country"] == "FR" and c["platform"] == "Web"
    num, den, rate = cells(shift, volume=80)
    result = choose(scan(DIMS, KEYS, num, den, rate), {"country": "FR"})
    assert result.place.segment == {"country": "FR", "platform": "Web"} and result.verdict == "refined"


def test_two_moved_places_are_not_widened_into_the_place_that_contains_both():
    # a payment fault on two platforms of one country, with different sizes: "the country" with one common
    # factor scores higher than either platform, but it is two places, and the rest of it is untouched
    def shift_factor(c):
        if c["country"] == "DE" and c["platform"] == "Android":
            return 0.5
        if c["country"] == "DE" and c["platform"] == "iOS":
            return 0.75
        return 1.0
    rng = np.random.default_rng(2)
    den = rng.poisson(80, len(KEYS)).astype(float)
    p = np.array([0.9 * shift_factor(dict(zip(DIMS, k))) for k in KEYS])
    num = rng.binomial(den.astype(int), p).astype(float)
    places = scan(DIMS, KEYS, num, den, np.full(len(KEYS), 0.9))
    for anchor in ({"country": "DE", "platform": "Android"}, {"country": "DE", "platform": "iOS"}):
        assert choose(places, anchor).place.segment == anchor


def test_a_place_is_widened_when_all_of_the_wider_place_moved_alike():
    num, den, rate = cells(lambda c: c["country"] == "DE", volume=60)
    result = choose(scan(DIMS, KEYS, num, den, rate), {"country": "DE", "platform": "Android"})
    assert result.place.segment == {"country": "DE"} and result.verdict == "refined"
