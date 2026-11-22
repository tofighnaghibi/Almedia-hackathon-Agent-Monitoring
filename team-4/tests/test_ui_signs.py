"""The mark on a possible-cause link of the main console (ui/static/v3): four styles the owner can compare on the real
page, picked with ?signs=1..4 or with the Legend's "Cause mark" choices and kept in the browser. The page is fetched
through the app like test_ui.py does; the drawing code of app.js runs in Node with a tiny stand-in for the SVG page."""
import json
import re
import subprocess

import pytest
from fastapi.testclient import TestClient

import ui.app as ui


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setattr(ui, "MARKET_DB", tmp_path / "market.db")
    monkeypatch.setattr(ui, "INCIDENTS_DIR", tmp_path / "incidents")
    monkeypatch.setattr(ui, "INCOMING_DIR", tmp_path / "incoming")
    return TestClient(ui.app)


def _page(client) -> tuple[str, str]:
    """The main console's HTML as served at / and the script it loads, fetched through the versioned link in the page."""
    html = client.get("/").text
    src = re.search(r'<script src="(/static/v3/app\.js\?v=\d+)"></script>', html).group(1)
    return html, client.get(src).text


def _legend(html: str) -> str:
    return html.split('id="legend-body"')[1].split('<div class="tools">')[0]


STYLES = ["Tag on the line", "Sign at the arrow", "Trend chip", "Arrow, bar or ring"]


def test_the_legend_offers_the_four_cause_marks_as_radio_choices(client):
    html, _ = _page(client)
    legend = _legend(html)
    assert 'role="radiogroup"' in legend and "Cause mark" in legend
    # four native radio buttons: reached with Tab, switched with the arrow keys, each with its label
    assert re.findall(r'<input type="radio" name="cause-mark" value="(\d)"', legend) == ["1", "2", "3", "4"]
    for name in STYLES:
        assert name in legend, name
    # the Links column shows a possible cause raising, lowering and of unknown sign, drawn in the style picked
    links = legend.split('class="lg-col lg-links"')[1].split('class="lg-col lg-pick"')[0]
    for sample in ("lg-sign-up", "lg-sign-down", "lg-sign-any"):
        assert f'id="{sample}"' in links, sample
    assert "Formula" in links and "may raise" in links and "May lower" in links


def test_the_script_takes_the_style_from_the_address_then_from_this_browser(client):
    _, js = _page(client)
    assert "new URLSearchParams(location.search).get('signs')" in js
    assert "store('v3-signs'" in js                                   # remembered through the page's store() helper
    assert "setAttribute('data-signs'" in js                          # so the style sheet can style each version
    assert "drawSign(E, e, g, p);" in js                              # every cause link draws its mark after its line
    assert 'input[name="cause-mark"]' in js


def test_the_style_sheet_dims_marks_with_their_links_and_uses_only_theme_colours():
    css = (ui.STATIC / "v3" / "style.css").read_text(encoding="utf-8")
    assert re.search(r"\.dim \.sign \{ opacity: \.45; \}", css)       # the same as .dim .edge.may
    assert ':root[data-signs="4"] .edge.may { stroke-width: var(--may-w' in css
    rules = [r for r in css.splitlines() if re.match(r"\s*\.(sign|lg-target|lg-pick|legend \.sign)", r)]
    assert len(rules) >= 10
    for rule in rules:
        assert "#" not in rule, rule                                   # colours come from the theme's tokens


# ------------------------------------------------------------------------------- the drawing code, run in Node
HARNESS = r"""
const vm = require('node:vm');
const assert = require('node:assert/strict');
function point(p, t) {
  const u = 1 - t, a = u * u * u, b = 3 * u * u * t, c = 3 * u * t * t, d = t * t * t;
  return [a * p[0][0] + b * p[1][0] + c * p[2][0] + d * p[3][0], a * p[0][1] + b * p[1][1] + c * p[2][1] + d * p[3][1]];
}
function tangent(p, t) {
  const u = 1 - t, a = 3 * u * u, b = 6 * u * t, c = 3 * t * t;
  const x = a * (p[1][0] - p[0][0]) + b * (p[2][0] - p[1][0]) + c * (p[3][0] - p[2][0]);
  const y = a * (p[1][1] - p[0][1]) + b * (p[2][1] - p[1][1]) + c * (p[3][1] - p[2][1]);
  const n = Math.hypot(x, y) || 1; return [x / n, y / n];
}
class El {
  constructor(tag) { this.tag = tag; this.attrs = {}; this.children = []; this.text = ''; this.on = {};
    const props = this.props = {}; this.style = { setProperty: (k, v) => { props[k] = v; } }; }
  setAttribute(k, v) { this.attrs[k] = String(v); }
  getAttribute(k) { return this.attrs[k]; }
  appendChild(c) { this.children.push(c); return c; }
  addEventListener(type, fn) { this.on[type] = fn; }
  set textContent(v) { this.children = []; this.text = String(v); }
  get textContent() { return this.text + this.children.map(c => c.textContent).join(''); }
}
const all = (e, out = []) => { out.push(e); e.children.forEach(c => all(c, out)); return out; };
const classes = e => all(e).flatMap(x => (x.attrs['class'] || '').split(' ').filter(Boolean));
const withClass = (e, cls) => all(e).filter(x => (x.attrs['class'] || '').split(' ').includes(cls));
const matrix = el => el.attrs.transform.match(/matrix\(([^)]+)\)/)[1].split(' ').map(Number);
// one page: the block of app.js that draws the marks, with the page's helpers it uses
function page(search, kept) {
  const ids = {}; ['lg-sign-up', 'lg-sign-down', 'lg-sign-any'].forEach(id => ids[id] = new El('svg'));
  const radios = [1, 2, 3, 4].map(v => { const r = new El('input'); r.value = String(v); r.checked = false; return r; });
  const box = { kept: Object.assign({}, kept), redraws: 0, ids, radios, root: new El('html'), url: null };
  const sb = {
    S: 1.2, NODE_H: 32, drawn: null, R: { point, tangent }, URL, URLSearchParams,
    location: { search, href: 'http://127.0.0.1:8060/' + search },
    history: { replaceState: (a, b, u) => { box.url = String(u); } },
    document: { documentElement: box.root, querySelectorAll: q => q.includes('cause-mark') ? radios : [] },
    $: id => ids[id] || null,
    store: (k, v) => { if (v === undefined) return k in box.kept ? box.kept[k] : null; box.kept[k] = String(v); },
    str: v => v == null ? '' : String(v), num: n => typeof n === 'number' && isFinite(n) ? n : null,
    list: v => Array.isArray(v) ? v : [],
    drawGraph: () => { box.redraws++; },
    svg: (tag, attrs, parent) => { const e = new El(tag); for (const k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; }
  };
  vm.runInNewContext(BLOCK, sb);
  box.sb = sb;
  return box;
}
// a link's geometry as route.js gives it: one cubic curve that comes from the open side and enters `side` of a box at
// (ex, ey) travelling against that side's normal, the way the router lets a link in without a hook
const NORMAL = { l: [-1, 0], r: [1, 0], t: [0, -1], b: [0, 1] };
function link(ex, ey, side) {
  const n = NORMAL[side], sx = ex + n[0] * 300 - n[1] * 120, sy = ey + n[1] * 300 + n[0] * 120;
  const p = [[sx, sy], [sx - n[0] * 80, sy - n[1] * 80], [ex + n[0] * 80, ey + n[1] * 80], [ex, ey]];
  const m = point(p, 0.5), t = tangent(p, 0.5);
  return { d: `M${sx},${sy} C${p[1]} ${p[2]} ${ex},${ey}`, p, start: { x: sx, y: sy, nx: -n[0], ny: -n[1], side: 'l' },
    end: { x: ex, y: ey, nx: n[0], ny: n[1], side }, mid: { x: m[0], y: m[1], tx: t[0], ty: t[1] } };
}
const cause = (sign, prior) => ({ source: 'stockouts', target: 'add_to_cart_rate', sign, prior, status: 'assumed',
  note: 'unavailable products cannot be added to the cart' });
// draw one mark as drawGraph does: a fresh drawing (its own `drawn`), the dashed line first, then its mark
function draw(P, style, e, g, nodes) {
  P.sb.signStyle = style; P.sb.drawn = { nodes: nodes || [], edges: [], at: {} };
  const layer = new El('g'), line = new El('path'); layer.appendChild(line);
  const sg = P.sb.drawSign(layer, e, g, line);
  return { layer, line, sg };
}
"""


def run_signs(js: str, check: str):
    block = re.search(r"\n(  // -+ the mark on a possible cause: four styles.*?)\n  function drawGraph\(\) \{", js, re.S)
    assert block, "the block of app.js that draws the cause marks"
    program = HARNESS.replace("vm.runInNewContext(BLOCK", "vm.runInNewContext(" + json.dumps(block.group(1))) + check
    result = subprocess.run(["node", "-"], input=program, capture_output=True, text=True, encoding="utf-8", timeout=30)
    assert result.returncode == 0, result.stdout + result.stderr


def test_the_style_comes_from_the_address_else_this_browser_else_style_1(client):
    _, js = _page(client)
    run_signs(js, r"""
let P = page('?signs=3', {});
assert.equal(P.sb.signStyle, 3); assert.equal(P.kept['v3-signs'], '3');            // the address picks and is remembered
assert.equal(P.root.attrs['data-signs'], '3');
assert.deepEqual(P.radios.map(r => r.checked), [false, false, true, false]);
assert.equal(page('', { 'v3-signs': '2' }).sb.signStyle, 2);                        // else the remembered one
assert.equal(page('', {}).sb.signStyle, 1);                                         // else style 1
assert.equal(page('?signs=9', { 'v3-signs': '4' }).sb.signStyle, 4);                // a wrong value is ignored
assert.equal(page('?signs=x', { 'v3-signs': 'zzz' }).sb.signStyle, 1);
""")


def test_a_choice_in_the_legend_redraws_the_graph_and_is_remembered(client):
    _, js = _page(client)
    run_signs(js, r"""
let P = page('?theme=dark&signs=2', {});
P.sb.drawn = { nodes: [] };                                                         // a graph is on the page
assert.ok(classes(P.ids['lg-sign-down']).includes('sign-vee'));                     // the samples show style 2
const four = P.radios[3]; four.checked = true; four.on.change({ target: four });
assert.equal(P.sb.signStyle, 4); assert.equal(P.kept['v3-signs'], '4'); assert.equal(P.redraws, 1);
assert.equal(P.root.attrs['data-signs'], '4');
assert.ok(P.url.includes('signs=4') && P.url.includes('theme=dark'), P.url);        // a reload keeps the choice
assert.ok(classes(P.ids['lg-sign-down']).includes('sign-bar'));                     // the samples follow at once
assert.ok(classes(P.ids['lg-sign-up']).includes('sign-tri'));
assert.ok(classes(P.ids['lg-sign-any']).includes('sign-ring'));
// with no style in the address the choice is only kept in the browser; before the graph loads nothing is redrawn
P = page('', {}); const three = P.radios[2]; three.checked = true; three.on.change({ target: three });
assert.equal(P.kept['v3-signs'], '3'); assert.equal(P.url, null); assert.equal(P.redraws, 0);
""")


def test_each_style_draws_its_own_mark_for_raises_lowers_and_unknown(client):
    _, js = _page(client)
    run_signs(js, r"""
const P = page('', {}), g = link(400, 40, 'l');
const want = {
  1: { up: ['sign-tag', 'sign-glyph', 'sign-head'], down: ['sign-tag', 'sign-glyph', 'sign-head'], any: ['sign-tag', 'sign-glyph', 'sign-head'] },
  2: { up: ['sign-vee', 'sign-glyph', 'sign-halo'], down: ['sign-vee', 'sign-glyph', 'sign-halo'], any: ['sign-vee', 'sign-glyph', 'sign-halo'] },
  3: { up: ['sign-chip', 'sign-trend', 'sign-chev'], down: ['sign-chip', 'sign-trend', 'sign-chev'], any: ['sign-chip', 'sign-trend', 'sign-chev'] },
  4: { up: ['sign-tri'], down: ['sign-bar', 'sign-cut'], any: ['sign-ring'] }
};
const marks = ['sign-tag', 'sign-head', 'sign-vee', 'sign-halo', 'sign-chip', 'sign-chev', 'sign-trend', 'sign-tri', 'sign-bar', 'sign-ring'];
const glyphs = {};
for (const style of [1, 2, 3, 4]) for (const [sign, kind] of [[1, 'up'], [-1, 'down'], [0, 'any']]) {
  const { sg } = draw(P, style, cause(sign, 0.5), g);
  const has = classes(sg);
  assert.ok(has.includes('sign') && has.includes('sign-' + kind) && has.includes('sign-v' + style), has.join(' '));
  for (const c of want[style][kind]) assert.ok(has.includes(c), `style ${style} ${kind}: ${c} in ${has}`);
  for (const c of marks) if (!want[style][kind].includes(c)) assert.ok(!has.includes(c), `style ${style} ${kind}: no ${c}`);
  if (style !== 4) glyphs[style + kind] = withClass(sg, 'sign-glyph')[0].attrs.d;
  assert.equal(withClass(sg, 'sign-hit').length, 1);                                 // the tooltip shows along the line
}
// the sign is told apart by its glyph: plus, minus, question mark; style 3 draws a rising or a falling trend line
for (const style of [1, 2, 3]) assert.equal(new Set(['up', 'down', 'any'].map(k => glyphs[style + k])).size, 3, 'style ' + style);
""")


def test_the_tooltip_says_may_raise_may_lower_or_may_affect_in_words(client):
    _, js = _page(client)
    run_signs(js, r"""
const P = page('', {}), g = link(400, 40, 'l');
const words = sign => all(draw(P, 1, cause(sign, 0.6), g).sg).find(x => x.tag === 'title').textContent;
assert.equal(words(-1), 'stockouts may lower add_to_cart_rate: unavailable products cannot be added to the cart (prior 0.6, assumed)');
assert.ok(words(1).startsWith('stockouts may raise add_to_cart_rate: '));
assert.ok(words(0).startsWith('stockouts may affect add_to_cart_rate: '));
for (const style of [2, 3, 4]) assert.ok(all(draw(P, style, cause(1, 0.6), g).sg).some(x => x.tag === 'title'));
// the Legend's samples carry no tooltip of their own
assert.ok(!all(P.ids['lg-sign-up']).some(x => x.tag === 'title'));
""")


def test_end_marks_sit_where_the_link_enters_its_box_on_any_side(client):
    _, js = _page(client)
    run_signs(js, r"""
const P = page('', {});
for (const side of ['l', 'r', 't', 'b']) {
  const g = link(500, 100, side), back = [-g.end.nx, -g.end.ny];
  for (const [style, cls] of [[1, 'sign-head'], [2, 'sign-vee'], [4, 'sign-tri']]) {
    const m = matrix(withClass(draw(P, style, cause(1, 0.5), g).sg, cls)[0]);
    assert.ok(Math.abs(m[4] - g.end.x) < 0.01 && Math.abs(m[5] - g.end.y) < 0.01, `${side} ${style}: tip at the box`);
    assert.ok(m[0] * back[0] + m[1] * back[1] > 0.99, `${side} ${style}: points the way the link travels in`);
  }
  // the open circle (sign unknown) touches the box from outside, on the line
  const ring = withClass(draw(P, 4, cause(0, 0.5), g).sg, 'sign-ring')[0].attrs;
  const off = [ring.cx - g.end.x, ring.cy - g.end.y];
  assert.ok(off[0] * g.end.nx + off[1] * g.end.ny > 0, side + ': the circle is outside the box');
}
""")


def test_style_4_draws_more_likely_causes_with_heavier_dashes(client):
    _, js = _page(client)
    run_signs(js, r"""
const P = page('', {}), g = link(400, 40, 'l');
const weight = prior => parseFloat(draw(P, 4, cause(-1, prior), g).line.props['--may-w']);
assert.ok(weight(0.3) < weight(0.6) && weight(0.6) < weight(0.9));
assert.ok(weight(0) >= 0.5 && weight(1) <= 4.5);
assert.equal(draw(P, 1, cause(-1, 0.9), g).line.props['--may-w'], undefined);      // the other styles keep one weight
""")


def test_a_middle_mark_moves_off_a_box_and_off_a_mark_drawn_before_it(client):
    _, js = _page(client)
    run_signs(js, r"""
const P = page('', {}), g = link(400, 40, 'l');
const at = sg => sg.children.find(x => x.tag === 'g').attrs.transform.match(/translate\(([^,]+),([^)]+)\)/).slice(1).map(Number);
const free = at(draw(P, 1, cause(1, 0.5), g).sg);
assert.ok(Math.abs(free[0] - g.mid.x) < 0.1 && Math.abs(free[1] - g.mid.y) < 0.1);     // nothing in the way: the middle
const boxOnMid = { x: g.mid.x, y: g.mid.y, hw: 40 };
for (const style of [1, 3]) {
  const q = at(draw(P, style, cause(1, 0.5), g, [boxOnMid]).sg);
  const clear = Math.abs(q[0] - boxOnMid.x) > boxOnMid.hw || Math.abs(q[1] - boxOnMid.y) > 16;
  assert.ok(clear, `style ${style}: the mark left the box at ${q}`);
}
// two links whose middles meet: the second mark keeps off the first in the same drawing
P.sb.signStyle = 1; P.sb.drawn = { nodes: [] };
const layer = new El('g'), a = at(P.sb.drawSign(layer, cause(1, 0.5), g, new El('path')));
const b = at(P.sb.drawSign(layer, cause(-1, 0.5), g, new El('path')));
assert.ok(Math.hypot(a[0] - b[0], a[1] - b[1]) > 10, `${a} ${b}`);
// a formula link drawn earlier crosses the middle: the mark moves along its own line, off the other one
P.sb.drawn = { nodes: [] };
const cross = new El('g'), x = g.mid.x, y = g.mid.y;
const other = new El('path'); other.setAttribute('class', 'edge');
other.setAttribute('d', `M${x},${y - 120} C${x},${y - 40} ${x},${y + 40} ${x},${y + 120}`);
const own = new El('path'); own.setAttribute('class', 'edge may'); cross.appendChild(other); cross.appendChild(own);
const c = at(P.sb.drawSign(cross, cause(1, 0.5), g, own));
assert.ok(Math.abs(c[0] - x) > 6.5 * P.sb.S + 1, `the mark at ${c} still sits on the line x = ${x}`);
""")
