"""Where the links on the metric graph leave and enter their boxes (ui/static/v3/route.js), checked under node.

tests/route_check.js loads route.js, lays out the cases below and reports the geometry and its faults as JSON; the
tests here assert on that report. A hook is a link whose start side does not face its end, or whose end side does
not face its start: it leaves away from its target and turns back (the owner's screenshot of 22.11: a possible cause
between search_ctr and views_per_session swung far right and came back in from the wrong side).
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed: route.js is checked by running it under node")

# The owner's canvas at 12:10 on 22.11 (an 800 px wide pane): the default layout with a few boxes moved by right-drag,
# search_ctr far right of the rest. Boxes: (name, x, y, hw, hh), centre and half sizes in world units; a metric shown a
# second time is "name#2". Links: (from, to, kind), formula links first, then the possible causes.
OWNER_BOXES = [
    ("sessions", 1060, 51, 102, 23),
    ("views_per_session", 1339.5, 117, 137.5, 23),
    ("add_to_cart_rate", 1339.5, 183, 137.5, 23),
    ("checkout_rate", 1339.5, 249, 137.5, 23),
    ("payment_attempt_rate", 1339.5, 315, 137.5, 23),
    ("payment_success_rate", 1339.5, 381, 137.5, 23),
    ("order_rate", 1339.5, 447, 137.5, 23),
    ("conversion_rate", 1060, 282, 102, 23),
    ("orders", 818.5, 166.5, 99.5, 23),
    ("aov", 818.5, 513, 99.5, 23),
    ("gmv", 589, 339.75, 90, 23),
    ("take_rate", 589, 579, 90, 23),
    ("marketplace_fees", 349, 459.38, 110, 23),
    ("sessions#2", 1060, 645, 102, 23),
    ("ad_load", 1060, 711, 102, 23),
    ("ad_impressions", 818.5, 678, 99.5, 23),
    ("ad_ctr", 818.5, 777, 99.5, 23),
    ("ad_clicks", 579.74, 705.27, 90, 23),
    ("ad_cpc", 589, 843, 90, 23),
    ("ad_spend", 354.56, 787.1, 110, 23),
    ("gmv#2", 589, 909, 90, 23),
    ("discount_rate", 589, 975, 90, 23),
    ("discounts", 349, 942, 110, 23),
    ("contribution", 113.5, 700.69, 85.5, 23),
    ("stockouts", 127.5, 1148.44, 119.5, 23),
    ("stockout_rate", 402.5, 1141.03, 119.5, 23),
    ("product_views", 657.5, 1154, 119.5, 23),
    ("add_to_cart", 912.5, 1154, 119.5, 23),
    ("checkouts", 1167.5, 1154, 119.5, 23),
    ("payment_attempts", 147.5, 1220, 119.5, 23),
    ("payment_successes", 413.62, 1210.74, 119.5, 23),
    ("refunds", 657.5, 1220, 119.5, 23),
    ("searches", 912.5, 1220, 119.5, 23),
    ("search_clicks", 1167.5, 1220, 119.5, 23),
    ("search_ctr", 2026.03, 537.55, 119.5, 23),
    ("feed_impressions", 452.52, 1363.81, 119.5, 23),
    ("feed_clicks", 657.5, 1286, 119.5, 23),
    ("feed_ctr", 912.5, 1286, 119.5, 23),
]
OWNER_LINKS = [
    ("orders", "sessions", "tree"),
    ("conversion_rate", "views_per_session", "tree"),
    ("conversion_rate", "add_to_cart_rate", "tree"),
    ("conversion_rate", "checkout_rate", "tree"),
    ("conversion_rate", "payment_attempt_rate", "tree"),
    ("conversion_rate", "payment_success_rate", "tree"),
    ("conversion_rate", "order_rate", "tree"),
    ("orders", "conversion_rate", "tree"),
    ("gmv", "orders", "tree"),
    ("gmv", "aov", "tree"),
    ("marketplace_fees", "gmv", "tree"),
    ("marketplace_fees", "take_rate", "tree"),
    ("contribution", "marketplace_fees", "tree"),
    ("ad_impressions", "sessions#2", "tree"),
    ("ad_impressions", "ad_load", "tree"),
    ("ad_clicks", "ad_impressions", "tree"),
    ("ad_clicks", "ad_ctr", "tree"),
    ("ad_spend", "ad_clicks", "tree"),
    ("ad_spend", "ad_cpc", "tree"),
    ("contribution", "ad_spend", "tree"),
    ("discounts", "gmv#2", "tree"),
    ("discounts", "discount_rate", "tree"),
    ("contribution", "discounts", "tree"),
    ("stockouts", "add_to_cart_rate", "may"),
    ("stockouts", "feed_ctr", "may"),
    ("discount_rate", "add_to_cart_rate", "may"),
    ("search_ctr", "views_per_session", "may"),
    ("feed_ctr", "views_per_session", "may"),
    ("feed_ctr", "add_to_cart_rate", "may"),
]


def _case(boxes, links, crossings=False):
    """A case for the harness: boxes as (name, x, y, hw, hh); links as (from name, to name, kind)."""
    at = {b[0]: i for i, b in enumerate(boxes)}
    return {
        "boxes": [{"id": n, "x": x, "y": y, "hw": hw, "hh": hh} for n, x, y, hw, hh in boxes],
        "links": [{"from": at[a], "to": at[b], "kind": k} for a, b, k in links],
        "crossings": crossings,
    }


def _untouched(node_h, dy, widths):
    """Boxes and formula links placed as app.js lays out a formula tree nobody has moved: one column per depth, 40
    units apart, the leaves stacked dy apart and each parent centred on its children; then possible causes."""
    tree = ("contribution", [
        ("marketplace_fees", [("gmv", [("orders", [("sessions", []), ("conversion_rate", [
            ("views_per_session", []), ("add_to_cart_rate", []), ("checkout_rate", []), ("payment_attempt_rate", []),
            ("payment_success_rate", []), ("order_rate", [])])]), ("aov", [])]), ("take_rate", [])]),
        ("ad_spend", [("ad_clicks", [("ad_impressions", [("sessions", []), ("ad_load", [])]), ("ad_ctr", [])]),
                      ("ad_cpc", [])]),
        ("discounts", [("gmv", []), ("discount_rate", [])]),
    ])
    xs, at = [], 28
    for w in widths:
        xs.append(at + w / 2)
        at += w + 40
    boxes, links, leaf, seen = [], [], [0], {}

    def walk(node, depth):
        name, children = node
        key = name if name not in seen else name + "#2"
        seen[name] = True
        ys = [walk(c, depth + 1) for c in children]
        y = (min(y for _, y in ys) + max(y for _, y in ys)) / 2 if ys else 28 + leaf[0] * dy + node_h / 2
        if not ys:
            leaf[0] += 1
        boxes.append((key, xs[depth], y, widths[depth] / 2, node_h / 2))
        for child, _ in ys:
            links.append((key, child, "tree"))
        return key, y

    walk(tree, 0)
    causes = [("discount_rate", "add_to_cart_rate", "may"), ("ad_ctr", "sessions#2", "may"),
              ("take_rate", "views_per_session", "may"), ("order_rate", "aov", "may")]
    return _case(boxes, links + causes)


B = lambda name, x, y, hw=100, hh=20: (name, x, y, hw, hh)  # noqa: E731  a box, wide and short as on the canvas

CASES = {
    "owner": _case(OWNER_BOXES, OWNER_LINKS),
    "untouched": _untouched(32, 46, [180, 210, 190, 200, 220, 280]),
    "untouched_small": _untouched(28, 40, [150, 170, 160, 170, 190, 240]),
    # the screenshot's link on its own: the source far right of and below its target
    "far_right_above": _case([B("search_ctr", 2026.03, 537.55, 119.5, 23), B("views_per_session", 1339.5, 117, 137.5, 23)],
                             [("search_ctr", "views_per_session", "may")]),
    "target_left": _case([B("src", 600, 100), B("dst", 100, 160)], [("src", "dst", "may"), ("src", "dst", "tree")]),
    "target_below": _case([B("src", 300, 60), B("dst", 330, 300)], [("src", "dst", "may"), ("src", "dst", "tree")]),
    "target_above_left": _case([B("src", 500, 400), B("dst", 420, 80)], [("src", "dst", "may"), ("src", "dst", "tree")]),
    # a column of boxes between a cause and its effect, with room to pass above it
    "column": _case([B("cause", 60, 40, 80), B("effect", 600, 300, 80)] + [B(f"col{k}", 300, 100 + 60 * k, 60) for k in range(5)],
                    [("cause", "effect", "may")]),
    # two causes reaching the same box from above-left and below-left; a third from far left
    "ports": _case([B("target", 600, 200), B("s_up", 100, 50), B("s_down", 100, 350), B("s_far", -250, 240)],
                   [("s_up", "target", "may"), ("s_down", "target", "may"), ("s_far", "target", "may")], crossings=True),
    # the same, with a formula link already meeting the target's left side in its middle
    "ports_with_formula": _case([B("target", 600, 200), B("parent", 280, 240), B("s_up", 100, 50), B("s_down", 100, 350)],
                                [("parent", "target", "tree"), ("s_up", "target", "may"), ("s_down", "target", "may")],
                                crossings=True),
    # nothing clean exists: overlapping boxes, a box linked to itself, a box with no size
    "degenerate": _case([B("a", 100, 100), B("b", 130, 110), B("dot", 400, 400, 0, 0)],
                        [("a", "b", "may"), ("a", "b", "tree"), ("a", "a", "may"), ("dot", "a", "may"), ("dot", "dot", "tree")]),
}


@pytest.fixture(scope="module")
def report():
    job = {"cases": CASES, "random": {"seed": 20261122, "count": 300}, "speed": {"links": 40, "boxes": 60, "runs": 200}}
    run = subprocess.run([NODE, str(ROOT / "tests" / "route_check.js")], input=json.dumps(job), capture_output=True,
                         text=True, timeout=120, cwd=ROOT)
    assert run.returncode == 0, run.stderr
    return json.loads(run.stdout)


def _link(report, case, frm, to, kind="may"):
    return next(g for g in report["cases"][case]["links"] if g["from"] == frm and g["to"] == to and g["kind"] == kind)


def test_route_js_offers_layout_point_and_tangent(report):
    assert report["api"] == {"layout": "function", "point": "function", "tangent": "function"}


def test_every_link_gets_one_geometry_in_order_with_finite_numbers_and_ends_on_its_boxes(report):
    for name, case in CASES.items():
        got = report["cases"][name]
        assert got["count"] == len(case["links"]), name
        for want, g in zip(case["links"], got["links"]):
            assert (g["from"], g["to"], g["kind"]) == (case["boxes"][want["from"]]["id"], case["boxes"][want["to"]]["id"], want["kind"])
            assert g["finite"], (name, g)
            assert g["d"].startswith("M") and " C" in g["d"], (name, g["d"])
            assert len(g["p"]) == 4 and g["start"]["side"] in "lrtb" and g["end"]["side"] in "lrtb"
            assert g["onSides"], (name, g["from"], g["to"], g["start"], g["end"])
            assert [g["p"][0], g["p"][3]] == [[g["start"]["x"], g["start"]["y"]], [g["end"]["x"], g["end"]["y"]]]


def test_formula_links_of_an_untouched_layout_are_drawn_exactly_as_before(report):
    for name in ("untouched", "untouched_small", "owner"):
        trees = [g for g in report["cases"][name]["links"] if g["kind"] == "tree"]
        assert trees
        for g in trees:
            assert g["d"] == g["today"], (name, g["from"], g["to"])
            assert (g["start"]["side"], g["end"]["side"]) == ("r", "l")


def test_no_link_hooks_or_runs_through_its_own_boxes_on_the_owners_canvas(report):
    for g in report["cases"]["owner"]["links"]:
        assert not g["hooked"], (g["from"], g["to"], g["d"])
        assert g["own"] == 0, (g["from"], g["to"], g["d"])


def test_the_screenshot_link_runs_straight_between_its_boxes_without_swinging_past_them(report):
    for case in ("owner", "far_right_above"):
        g = _link(report, case, "search_ctr", "views_per_session")
        assert not g["hooked"] and g["own"] == 0
        # it leaves search_ctr towards the target and arrives on the side of views_per_session that faces search_ctr
        assert (g["start"]["side"], g["end"]["side"]) == ("l", "r"), g["d"]
        assert g["xmin"] >= 1339.5 + 137.5 - 1 and g["xmax"] <= 2026.03 - 119.5 + 1, g["d"]
        assert g["through"] == []


def test_hand_made_placements_have_no_hooks(report):
    for case in ("target_left", "target_below", "target_above_left", "far_right_above", "column", "ports", "ports_with_formula"):
        for g in report["cases"][case]["links"]:
            assert not g["hooked"], (case, g["from"], g["to"], g["d"])
            assert g["own"] == 0, (case, g["from"], g["to"], g["d"])
    below = report["cases"]["target_below"]["links"][0]
    assert (below["start"]["side"], below["end"]["side"]) == ("b", "t")
    left = report["cases"]["target_left"]["links"][0]
    assert (left["start"]["side"], left["end"]["side"]) == ("l", "r")


def test_random_placements_never_hook_or_cut_through_their_own_boxes(report):
    r = report["random"]
    assert r["placements"] == 300 and r["links"] > 600
    assert r["shapeCount"] == 0, r["shape"]
    assert r["hookCount"] == 0, r["hooks"]
    assert r["ownCount"] == 0, r["own"]


def test_a_possible_cause_goes_around_a_column_of_boxes_when_there_is_room(report):
    g = report["cases"]["column"]["links"][0]
    assert g["through"] == [], g["d"]


def test_causes_sharing_a_side_get_their_own_points_in_an_order_that_does_not_cross(report):
    got = report["cases"]["ports"]
    up, down, far = (_link(report, "ports", s, "target") for s in ("s_up", "s_down", "s_far"))
    assert {up["end"]["side"], down["end"]["side"], far["end"]["side"]} == {"l"}
    ys = [up["end"]["y"], far["end"]["y"], down["end"]["y"]]
    assert ys == sorted(ys) and len(set(ys)) == 3, ys
    assert got["crossings"] == []


def test_causes_keep_off_the_point_where_a_formula_link_meets_the_box(report):
    got = report["cases"]["ports_with_formula"]
    tree = _link(report, "ports_with_formula", "parent", "target", "tree")
    up, down = (_link(report, "ports_with_formula", s, "target") for s in ("s_up", "s_down"))
    assert tree["d"] == tree["today"] and tree["end"]["y"] == 200
    for g in (up, down):
        assert g["end"]["side"] == "l" and abs(g["end"]["y"] - 200) >= 3, g["end"]
    assert up["end"]["y"] < 200 < down["end"]["y"]
    assert got["crossings"] == []


def test_boxes_that_overlap_or_link_to_themselves_still_get_a_finite_link(report):
    got = report["cases"]["degenerate"]
    assert got["count"] == 5 and all(g["finite"] for g in got["links"])


def test_layout_is_the_same_every_time(report):
    assert report["deterministic"] is True


def test_layout_is_quick_enough_to_run_on_every_frame_of_a_drag(report):
    s = report["speed"]
    print(f"\nroute.js layout: {s['links']} links x {s['boxes']} boxes, mean {s['msMean']:.3f} ms, "
          f"median {s['msMedian']:.3f} ms, max {s['msMax']:.3f} ms over {s['runs']} runs")
    assert (s["links"], s["boxes"]) == (40, 60)
    assert s["msMean"] < 50, s
