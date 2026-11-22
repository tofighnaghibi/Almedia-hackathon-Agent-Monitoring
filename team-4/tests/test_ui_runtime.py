"""Execute the shipped UI JavaScript in Node; no server or network required."""
import subprocess
from pathlib import Path


PAGE = Path(__file__).resolve().parents[1] / 'ui/static/index.html'


def run_ui(check):
    script = PAGE.read_text(encoding='utf-8').rsplit('<script>', 1)[1].split('</script>')[0]
    # Keep the real functions and state, omitting control wiring and startup I/O.
    script = script.split('  // ------------------------------------------------------------- controls')[0]
    harness = r"""
const assert = require('node:assert/strict');
class Element {
  constructor() { this.children = []; this.nodeType = 1; this.value = ''; }
  set textContent(value) { this.children = []; this.value = String(value); }
  get textContent() { return this.value + this.children.map(c => c.textContent).join(''); }
  appendChild(child) { this.children.push(child); return child; }
  addEventListener(type, fn) { (this.listeners = this.listeners || {})[type] = fn; }
}
const elements = {};
const document = {
  getElementById: id => elements[id] || (elements[id] = new Element()),
  createElement: () => new Element(),
  createTextNode: value => { const e = new Element(); e.textContent = value; return e; }
};
"""
    # the program goes in on stdin, not on the command line, which Windows limits to 32k characters
    result = subprocess.run(['node', '-'], input=harness + script + '\n' + check + '\n})();',
                            capture_output=True, text=True, encoding='utf-8', timeout=15)
    assert result.returncode == 0, result.stdout + result.stderr


def test_graph_signature_tracks_metadata_tree_and_typed_edge_changes():
    run_ui(r"""
const original = {nodes:[{id:'orders',status:'validated',description:'old'}],
  trees:[{metric:'orders',children:[]}],
  edges:[{kind:'may_affect',source:'a',target:'b',sign:1,prior:0.1}]};
for (const mutate of [g => g.nodes[0].description = 'new',
                      g => g.trees[0].children.push({metric:'sessions'}),
                      g => g.edges[0].sign = -1, g => g.edges[0].prior = 0.9]) {
  const changed = structuredClone(original); mutate(changed);
  assert.notEqual(graphSignature(original), graphSignature(changed));
}
assert.equal(graphSignature(original), graphSignature(structuredClone(original)));
""")


def test_incident_signature_tracks_report_values_not_only_lengths():
    run_ui(r"""
const original = [{id:'i',status:'open',last_seen:'t',report:'abc',
  change:{observed:1},hypotheses:[{score:0.1,evidence:['old']}],
  contributions:[{share_of_delta:0.1}],localization:[{segment:'DE'}],
  impact:{orders:1},trail:[{result:'old'}],uncertain:['old']}];
for (const mutate of [i => i.report = 'xyz', i => i.change.observed = 9,
  i => i.hypotheses[0].score = 0.9, i => i.hypotheses[0].evidence[0] = 'new',
  i => i.contributions[0].share_of_delta = 0.9, i => i.localization[0].segment = 'FR',
  i => i.impact.orders = 9, i => i.trail[0].result = 'new', i => i.uncertain[0] = 'new']) {
  incidents = structuredClone(original); const before = incidentSignature();
  mutate(incidents[0]); assert.notEqual(before, incidentSignature());
}
""")


def test_example_to_live_same_id_opens_report_and_later_preserves_node_selection():
    run_ui(r"""
incidents = [{id:'same',metric:'orders',report:'example'}];
example = true; selInc = 'same'; selNode = 'orders'; mode = 'node';
let response = {example:false,incidents:[{id:'same',metric:'orders',report:'live'}]};
// Replace transport and unrelated graph/feed rendering; retain refresh and report rendering.
get = url => url === '/api/incidents' ? Promise.resolve(response) : new Promise(() => {});
renderChips = renderFeed = applyStyles = () => {};
(async () => {
  refresh(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(mode, 'incident'); assert.equal(selInc, 'same');
  assert.equal($('detail-title').textContent, 'Report: same');
  assert.ok($('detail').textContent.includes('live'));
  mode = 'node'; selNode = 'orders';
  response = {example:false,incidents:[{id:'same',metric:'orders',report:'next'}]};
  refresh(); await new Promise(resolve => setImmediate(resolve));
  assert.equal(mode, 'node'); assert.equal(selNode, 'orders');
})().catch(error => { console.error(error); process.exitCode = 1; });
""")


def test_all_domains_off_tree_button_opens_metric_details():
    run_ui(r"""
graph = {nodes:[{id:'orders',kind:'metric'},{id:'refunds',kind:'metric'}]};
meta = {orders:graph.nodes[0],refunds:graph.nodes[1]};
dom = 'all'; applyStyles = () => {};
renderExtra({orders:true});
const buttons = $('extra').children.filter(e => e.textContent === 'refunds');
assert.equal(buttons.length, 1);
buttons[0].onclick();
assert.equal(mode, 'node'); assert.equal(selNode, 'refunds');
assert.equal($('detail-title').textContent, 'Metric: refunds');
""")


def test_poll_renders_same_length_report_edit_and_skipped_count_change():
    run_ui(r"""
incidents = [{id:'i',metric:'orders',report:'old'}]; selInc = 'i';
let response = {example:false,skipped:0,incidents:[{id:'i',metric:'orders',report:'new'}]};
get = url => url === '/api/incidents' ? Promise.resolve(response) : new Promise(() => {});
renderChips = applyStyles = () => {};
(async () => {
  refresh(); await new Promise(resolve => setImmediate(resolve));
  assert.ok($('detail').textContent.includes('new'));
  response = {...response,skipped:2};
  refresh(); await new Promise(resolve => setImmediate(resolve));
  assert.ok($('feed').textContent.includes('2 files'));
})().catch(error => { console.error(error); process.exitCode = 1; });
""")
