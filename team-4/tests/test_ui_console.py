"""The console's fixes for the demo (workstream extui): the new-metric buttons and their answers (X3), and the
readability of the incident feed and report (X4). The page's JavaScript runs in Node (tests/test_ui_runtime.py)."""
import re
from pathlib import Path

import yaml

from tests.test_ui_runtime import run_ui

PAGE = Path(__file__).resolve().parents[1] / "ui/static/index.html"


def html() -> str:
    return PAGE.read_text(encoding="utf-8")


def page_var(name: str) -> str:
    return re.search(rf"var {name} = '([^']+)';", html()).group(1).replace("\\n", "\n")


# ---------------------------------------------------------------------------- X3: the new-metric buttons
def test_the_refund_rate_button_posts_the_query_only():
    spec = yaml.safe_load(page_var("y"))
    assert spec["name"] == "refund_rate" and "formula" not in spec and "SUM(refunds)" in spec["sql"]
    assert 'id="nm-go">Add refund_rate<' in html()


def test_the_duplicate_button_posts_an_existing_rate_under_another_name():
    spec = yaml.safe_load(page_var("dup"))
    assert spec["name"] == "payment_rate" and "formula" not in spec
    assert "SUM(payment_successes)" in spec["sql"] and "SUM(payment_attempts)" in spec["sql"]
    assert re.search(r'id="nm-dup"[^>]*>Add a duplicate metric<', html())
    assert html().index('id="nm-dup"') < html().index("<main>")          # in the folded demo bar


CHECKS = r"""
const answer = {name:'payment_rate', status:'rejected', alias_of:'payment_success_rate',
  message:'payment_rate rejected: it is an alias of payment_success_rate: payment_successes / payment_attempts gives the same value as payment_success_rate for any data, so no new node was added',
  checks:[{check:'read',status:'pass',detail:'definition read: name and sql'},
          {check:'formula',status:'pass',detail:'found payment_successes / payment_attempts: the only one of 1045 candidates'},
          {check:'alias',status:'fail',detail:'it is an alias of payment_success_rate'}]};
"""


def test_the_toast_shows_the_answer_and_a_link_to_its_checks():
    run_ui(CHECKS + r"""
graph = {nodes:[], edges:[], trees:[]}; meta = {}; applyStyles = () => {};
answerToast(answer);
const toastText = $('toast').textContent;
assert.ok(toastText.startsWith(answer.message), toastText);
const link = $('toast').children.filter(c => c.textContent === 'Show checks');
assert.equal(link.length, 1);
link[0].onclick();
assert.equal(mode, 'node'); assert.equal(selNode, 'payment_rate');
const page = $('detail').textContent;
assert.ok(page.includes('Checks of the extension agent'), page);
assert.ok(page.includes('found payment_successes / payment_attempts: the only one of 1045 candidates'), page);
assert.ok(page.includes('alias') && page.includes('fail'), page);
assert.ok(page.includes(answer.message), page);
""")


def test_clicking_a_contributed_metric_lists_the_checks_from_its_result_file():
    run_ui(r"""
const result = {name:'refund_rate', status:'validated', message:'refund_rate added to the graph and validated',
  checks:[{check:'formula',status:'pass',detail:'found refunds / orders: the only one of 1045 candidates'},
          {check:'context',status:'pass',detail:'refunds and orders carry no release system'}]};
const asked = [];
get = url => { asked.push(url); return url === '/api/metric/refund_rate' ? Promise.resolve(result) : new Promise(() => {}); };
graph = {nodes:[{id:'refund_rate',kind:'metric',origin:'contributed',status:'validated'},
                {id:'orders',kind:'metric',origin:'core',status:'validated'}], edges:[], trees:[]};
meta = {refund_rate:graph.nodes[0], orders:graph.nodes[1]}; applyStyles = () => {};
(async () => {
  selNode = 'refund_rate'; mode = 'node'; renderDetail();
  await new Promise(resolve => setImmediate(resolve));
  assert.deepEqual(asked, ['/api/metric/refund_rate']);
  const page = $('detail').textContent;
  assert.ok(page.includes('Checks of the extension agent'), page);
  assert.ok(page.includes('found refunds / orders: the only one of 1045 candidates'), page);
  selNode = 'orders'; renderDetail();                                    // a core metric has no answer to show
  assert.ok(!$('detail').textContent.includes('Checks of the extension agent'));
  assert.deepEqual(asked, ['/api/metric/refund_rate']);
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


# ------------------------------------------------------------------------------- X4: readability
FIND = r"""
const find = (e, cls) => (e.className || '').split(' ').includes(cls) ? e
  : (e.children || []).map(c => find(c, cls)).find(Boolean);
const findAll = (e, cls, out = []) => { if ((e.className || '').split(' ').includes(cls)) out.push(e);
  (e.children || []).forEach(c => findAll(c, cls, out)); return out; };
meta = {payment_success_rate:{direction:'up'}, contribution:{direction:'up'}, ad_load:{direction:'none'}};
applyStyles = () => {};
const payment = {id:'inc-1', status:'open', metric:'contribution', segment:{country:'PL', platform:'Web'},
  first_seen:'2026-11-15T18:00:00Z', last_seen:'2026-11-15T19:00:00Z',
  change:{observed:197.2, expected:495.5, delta_pct:-60.2}, impact:{contribution_eur:-298.3, orders:-76.7},
  drivers:[{metric:'payment_success_rate', delta_pct:-65.7}], report:'template report'};
const overspend = {id:'inc-2', status:'open', metric:'contribution', segment:{country:'FR'},
  first_seen:'2026-11-15T18:00:00Z', last_seen:'2026-11-15T19:00:00Z',
  change:{observed:600, expected:500, delta_pct:20}, impact:{contribution_eur:168},
  drivers:[{metric:'ad_load', delta_pct:80}], report:'ad_load rose'};
"""


def test_a_fault_with_a_positive_euro_effect_is_not_drawn_green():
    """(a) The ad budget overspend reads as a euro gain; it is a fault, so the effect is neutral unless explained."""
    run_ui(FIND + r"""
const report = i => { incidents = [i]; selInc = i.id; mode = 'incident'; renderDetail(); return $('detail'); };
let chg = findAll(report(overspend), 'chg');
assert.ok(chg.length >= 2, chg.length);
assert.ok(chg.every(e => !e.className.includes('good')), chg.map(e => e.className).join(' | '));
chg = findAll(report(Object.assign({}, overspend, {status:'resolved'})), 'chg');
assert.ok(chg.every(e => !e.className.includes('good')));
chg = findAll(report(Object.assign({}, overspend, {status:'explained'})), 'chg');
assert.ok(chg.some(e => e.className.includes('good')), 'an explained gain may be green');
chg = findAll(report(payment), 'chg');
assert.ok(chg.filter(e => e.className.includes('bad')).length >= 2, 'a loss stays red');
// the feed too
incidents = [overspend]; dom = 'all'; selInc = null; renderFeed();
assert.ok(findAll($('feed'), 'chg').every(e => !e.className.includes('good')));
""")


def test_each_feed_row_leads_with_the_moved_metric_and_its_euro_effect():
    """(b) The row says what moved and what it cost; the outcome and the place come under it."""
    run_ui(FIND + r"""
incidents = [payment, Object.assign({}, overspend, {drivers:[], impact:{}})]; dom = 'all'; selInc = null; renderFeed();
const [first, second] = $('feed').children;
const line = find(first, 'inc-line');
assert.equal(line.children[0].textContent, 'payment_success_rate');
assert.ok(line.textContent.includes('−65.7%'), line.textContent);
assert.ok(line.textContent.includes('−298 EUR'), line.textContent);
const sub = find(first, 'inc-sub').textContent;
assert.ok(sub.includes('PL x Web') && sub.includes('contribution'), sub);
// no driver and no euro figure: the incident's own metric and change, and no empty euro slot
const line2 = find(second, 'inc-line');
assert.equal(line2.children[0].textContent, 'contribution');
assert.ok(!line2.textContent.includes('EUR'), line2.textContent);
""")


def test_an_older_narrative_is_labelled_and_without_one_the_summary_follows_the_readout():
    """(c) "In brief" written before the newest figures says so; (d) without it the template summary comes first."""
    run_ui(FIND + r"""
const page = (narrative, extra) => {
  incidents = [Object.assign({}, payment, extra || {}, narrative ? {narrative} : {})]; selInc = 'inc-1'; mode = 'incident';
  renderDetail(); return $('detail').textContent;
};
const story = {text:'Payment success fell.', source:'model', model:'m', as_of:'2026-11-15T17:00:00Z'};
let shown = page(story, {last_seen:'2026-11-15T19:00:00Z'});
assert.ok(shown.includes('written at 17:00 UTC; the figures above are newer'), shown);
shown = page(Object.assign({}, story, {as_of:'2026-11-15T19:00:00Z'}), {last_seen:'2026-11-15T19:00:00Z'});
assert.ok(!shown.includes('the figures above are newer'), shown);
// with the narrative, the template summary stays at the end
assert.ok(shown.indexOf('In brief') < shown.indexOf('What changed') && shown.indexOf('What changed') < shown.indexOf('Summary'));
// without it, the summary sits right under the readout, once
shown = page(null);
assert.ok(shown.indexOf('Summary') >= 0 && shown.indexOf('Summary') < shown.indexOf('What changed'), shown);
assert.equal(shown.split('Summary').length, 2);
assert.ok(shown.includes('template report'));
""")


def test_the_clock_says_simulated_accelerated_when_the_data_runs_ahead_of_real_time():
    """(e) "Replay" during the live accelerated demo invites "is this a recording?"."""
    run_ui(r"""
const at = (hour, now, mode) => clockMode({clock_mode: mode || 'replay', last_hour: hour, server_now: now});
assert.equal(at('2026-11-22T10:00:00Z', '2026-11-22T10:30:00+00:00', 'wall'), 'Live');
// a finished database: the data hour does not move
assert.equal(at('2026-11-21T23:00:00Z', '2026-11-22T10:00:00+00:00'), 'Replay');
assert.equal(at('2026-11-21T23:00:00Z', '2026-11-22T10:00:02+00:00'), 'Replay');
// the simulator writes an hour of data every 2 s: faster than real time, and it stays so between hours
assert.equal(at('2026-11-22T01:00:00Z', '2026-11-22T10:00:06+00:00'), 'Simulated, accelerated');
assert.equal(at('2026-11-22T01:00:00Z', '2026-11-22T10:00:08+00:00'), 'Simulated, accelerated');
// a data clock that keeps pace with real time (an hour per hour) is not accelerated
clockFirst = null; accelerated = false;
assert.equal(at('2026-11-22T09:00:00Z', '2026-11-22T09:59:58+00:00'), 'Replay');
assert.equal(at('2026-11-22T10:00:00Z', '2026-11-22T10:00:01+00:00'), 'Replay');
// a data clock ahead of real time is accelerated from the first look
clockFirst = null; accelerated = false;
assert.equal(at('2026-11-23T05:00:00Z', '2026-11-22T10:00:00+00:00'), 'Simulated, accelerated');
""")
    assert 'id="clock-mode">Simulated<' in html()


def test_the_bench_offers_the_new_simulator_kinds_with_the_fields_each_uses():
    """(f) Interface 4: traffic_drop (country, platform), feed_unavailable, mix_shift (category), discount_push."""
    run_ui(r"""
const pick = p => ({country:'DE', platform:'Android', category:'fashion'})[p];
assert.deepEqual(faultBody('traffic_drop', pick),
  {kind:'traffic_drop', severity:0.5, hours:72, when:'now', country:'DE', platform:'Android'});
assert.deepEqual(faultBody('feed_unavailable', pick), {kind:'feed_unavailable', hours:72, when:'now'});
assert.deepEqual(faultBody('discount_push', pick), {kind:'discount_push', hours:72, when:'now'});
assert.deepEqual(mixShiftBody('fashion'), {kind:'mix_shift', category:'fashion', hours:12, when:'now'});
// the faults the bench had keep their request
assert.deepEqual(faultBody('payment_failure', pick),
  {kind:'payment_failure', severity:0.4, hours:72, when:'now', country:'DE', platform:'Android'});
assert.deepEqual(faultBody('stockout_surge', pick), {kind:'stockout_surge', severity:0.4, hours:72, when:'now', category:'fashion'});
const labels = KINDS.map(k => k[1]);
for (const l of ['Traffic drop', 'Feed shows unavailable', 'Discount push']) assert.ok(labels.includes(l), l);
""")
    page = html()
    assert re.search(r'id="ms-go"[^>]*>Mix shift \(innocent\)<', page) and 'id="ms-category"' in page
    assert page.index('id="ms-go"') < page.index("<main>")                 # in the folded demo bar
    assert "mixShiftBody($('ms-category').value)" in page
