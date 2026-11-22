/* Eye of the System, v1 "Ops room". The data logic (graph layout, the route, the report, the bench's request bodies)
   follows /static/index.html, the reference console; the queue, the time ribbon, the case file, the command palette
   and the keyboard are this version's own. */
(function () {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';
  var $ = function (id) { return document.getElementById(id); };
  var graph = null, meta = {}, domains = [], domOf = {}, incidents = [], example = false, skipped = 0, graphKey = '';
  var dom = 'all', selInc = null, selNode = null, mode = 'incident', drawn = null, state = null;
  var LIVE = ['open', 'ongoing'], KNOWN = ['open', 'ongoing', 'resolved', 'explained', 'pending'];
  var HOUR = 3600000, WINDOW_H = 48;

  // ------------------------------------------------------------ helpers (as in the reference console)
  function str(v) { return v == null ? '' : (typeof v === 'object' ? JSON.stringify(v) : String(v)); }
  function list(v) { return Array.isArray(v) ? v : []; }
  function obj(v) { return v && typeof v === 'object' && !Array.isArray(v) ? v : {}; }
  function text(s) { return document.createTextNode(str(s)); }
  function el(tag, cls, kids) { var e = document.createElement(tag); if (cls) e.className = cls; (kids || []).forEach(function (k) { e.appendChild(k && k.nodeType ? k : text(k)); }); return e; }
  function get(url) { return fetch(url).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); }); }
  function post(url, body) {
    return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.detail || r.status); return j; }); });
  }
  function num(n) { return typeof n === 'number' && isFinite(n) ? n : null; }
  function signed(s) { return s.charAt(0) === '-' ? '−' + s.slice(1) : s; }
  function fmt(n, d) { n = num(Number(n)); return n == null ? '-' : signed(n.toLocaleString('en', { maximumFractionDigits: d == null ? 1 : d })); }
  function short(n) {
    n = num(n); if (n == null) return '–';
    var a = Math.abs(n);
    return signed(a >= 100 ? n.toLocaleString('en', { maximumFractionDigits: 0 }) : a >= 1 ? n.toLocaleString('en', { maximumFractionDigits: 2 })
      : n.toLocaleString('en', { maximumSignificantDigits: 3 }));
  }
  function pct(n) { n = num(n); return n == null ? '–' : (n > 0 ? '+' : '') + signed(fmt(n)) + '%'; }
  function tone(metric, delta) {
    var d = obj(meta[metric]).direction; delta = num(delta);
    if (!delta || (d !== 'up' && d !== 'down')) return '';
    return (d === 'up') === (delta > 0) ? 'good' : 'bad';
  }
  function toneIn(i, metric, delta) { var t = tone(metric, delta); return t === 'good' && status(i) !== 'explained' ? '' : t; }
  function euroTone(i, v) { v = num(v); return !v ? '' : v < 0 ? 'bad' : status(i) === 'explained' ? 'good' : ''; }
  function euroOf(i) {
    var imp = obj(i.impact);
    var k = num(imp.contribution_eur) != null ? 'contribution_eur' : Object.keys(imp).filter(function (x) { return /_eur$/.test(x) && num(imp[x]) != null; })[0];
    return k ? num(imp[k]) : null;
  }
  function eurText(v) { return (v > 0 ? '+' : '') + short(v) + ' EUR'; }
  function segText(s) { var o = obj(s), k = Object.keys(o); return k.length ? k.map(function (x) { return str(o[x]); }).join(' × ') : 'all'; }
  function when(t) { return str(t).replace('T', ' ').replace(':00Z', '').replace(/-/g, '‑'); }
  function svg(tag, attrs, parent) { var e = document.createElementNS(NS, tag); for (var k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; }
  function note(msg) { $('notice').textContent = msg || ''; }
  function reduced() { return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches; }

  // Berlin time for every hour shown, like the reference
  var fmtParts = new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Berlin', weekday: 'short', day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' });
  function parts(ts) { var p = {}; fmtParts.formatToParts(new Date(ts)).forEach(function (x) { p[x.type] = x.value; }); return p; }
  function clockParts(ts) { var p = parts(ts); return { day: p.weekday + ' ' + p.day + ' ' + p.month, year: p.year, hour: p.hour + ':' + p.minute }; }
  function displayHour(ts) {
    return new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Berlin', day: '2-digit', month: '2-digit', year: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(ts)) + ' Europe/Berlin';
  }
  function shortHour(ts) { var t = Date.parse(str(ts)); if (!isFinite(t)) return str(ts); var p = parts(t); return p.weekday + ' ' + p.hour + ':' + p.minute; }

  // ------------------------------------------------------------ domains
  function domainOf(metric) { return domOf[metric] || 'other'; }
  function domainLabel(id) { if (id === 'all') return 'All domains'; var d = domains.filter(function (x) { return x.id === id; })[0]; return d ? d.label : 'Other'; }
  function incidentNodes(i) {
    var s = {}; s[str(i.metric)] = true;
    list(i.contributions).forEach(function (c) { if (c && c.node) s[str(c.node)] = true; });
    list(i.trail).forEach(function (t) { t = obj(t); if (t.action === 'watch' && t.target && meta[t.target]) s[str(t.target)] = true; });
    return Object.keys(s);
  }
  function status(i) { return str(i.status) || 'open'; }
  function stOf(i) { var s = status(i); return 'st-' + (KNOWN.indexOf(s) >= 0 ? s : 'other'); }
  function touches(i, d) { return d === 'all' || incidentNodes(i).some(function (m) { return domainOf(m) === d; }); }
  function inScope() { return incidents.filter(function (i) { return touches(i, dom); }); }
  function liveCount(d) { return incidents.filter(function (i) { return LIVE.indexOf(status(i)) >= 0 && touches(i, d); }).length; }
  function byId(id) { return incidents.filter(function (i) { return i.id === id; })[0]; }

  function setDomain(id) { dom = id; selNode = null; mode = 'incident'; var s = ordered(); selInc = s.length ? s[0].id : null; renderAll(); scrollChip(); }
  function renderChips() {
    var nav = $('domains'); nav.textContent = '';
    var l = [{ id: 'all', label: 'All' }].concat(domains);
    if (graph && list(graph.nodes).some(function (n) { return n.kind === 'metric' && !domOf[n.id]; })) l.push({ id: 'other', label: 'Other' });
    l.forEach(function (d) {
      var n = liveCount(d.id);
      var b = el('button', 'chip' + (d.id === dom ? ' on' : ''), [d.label].concat(n ? [el('span', 'n', [String(n)])] : []));
      b.setAttribute('aria-pressed', d.id === dom ? 'true' : 'false');
      if (n) b.title = n + ' live incident' + (n > 1 ? 's' : '') + ' touch ' + d.label;
      b.onclick = function () { setDomain(d.id); };
      nav.appendChild(b);
    });
    renderCounts();
  }
  function scrollChip() { var on = $('domains').querySelector('.chip.on'); if (on && on.scrollIntoView) on.scrollIntoView({ block: 'nearest', inline: 'nearest' }); }
  function renderCounts() {
    var c = $('counts'); c.textContent = '';
    KNOWN.forEach(function (s) {
      var k = incidents.filter(function (i) { return status(i) === s; }).length;
      if (!k && s === 'pending') return;
      c.appendChild(el('span', 'count st-' + s + (k ? '' : ' zero'), [el('b', null, [String(k)]), s]));
    });
  }

  // ------------------------------------------------------------- graph (layout as in the reference)
  function filterTree(n) {
    if (dom === 'all') return n;
    var kids = list(n.children).map(filterTree).filter(Boolean);
    if (domainOf(n.metric) === dom || kids.length) return { metric: n.metric, role: n.role, repeated: n.repeated, children: kids };
    return null;
  }
  var CHAR = 7.2, NODE_H = 22, DY = 30, GAP = 18;
  function columns(roots) {
    var chars = [], x = [], hw = [], at = 8;
    var walk = function (n, d) { chars[d] = Math.max(chars[d] || 0, str(n.metric).length); list(n.children).forEach(function (c) { walk(c, d + 1); }); };
    roots.forEach(function (r) { walk(r, 0); });
    chars.forEach(function (c, d) { var w = Math.max(72, Math.ceil(c * CHAR) + 14); hw[d] = w / 2; x[d] = at + w / 2; at += w + GAP; });
    return { x: x, hw: hw };
  }
  function layout(root, y0, cols) {
    var nodes = [], edges = [], leaf = 0;
    (function walk(n, depth, parent) {
      var item = { n: n, d: depth, x: cols.x[depth], hw: cols.hw[depth], y: 0 }, ys = [];
      list(n.children).forEach(function (c) { ys.push(walk(c, depth + 1, item).y); });
      item.y = ys.length ? (Math.min.apply(null, ys) + Math.max.apply(null, ys)) / 2 : y0 + (leaf++) * DY + 28;
      nodes.push(item); if (parent) edges.push([parent, item]);
      return item;
    })(root, 0, null);
    return { nodes: nodes, edges: edges, w: Math.max.apply(null, nodes.map(function (i) { return i.x + i.hw; })) + 8, h: leaf * DY + 18 };
  }
  function health() {
    var h = {}, rank = { open: 3, ongoing: 2, resolved: 1 };
    incidents.forEach(function (i) {
      var st = status(i);
      if (!rank[st]) return;
      incidentNodes(i).forEach(function (m) { if (!h[m] || rank[st] > rank[h[m]]) h[m] = st; });
    });
    return h;
  }
  function pickNode(m) { selNode = m; mode = 'node'; renderDetail(); applyStyles(); renderExtraSel(); }
  function drawGraph() {
    $('graph-title').textContent = 'Metric graph · ' + domainLabel(dom);
    var wrap = $('graphwrap');
    Array.prototype.slice.call(wrap.querySelectorAll('.empty')).forEach(function (e) { wrap.removeChild(e); });
    var g = $('g'); g.textContent = '';
    drawn = { edges: [], nodes: [] };
    traceKey = '';
    var roots = list(graph && graph.trees).map(filterTree).filter(Boolean);
    if (!roots.length) { wrap.appendChild(el('div', 'empty', ['No formula tree for this domain. Its metrics are listed below.'])); renderExtra(null); return; }
    var cols = columns(roots), all = { nodes: [], edges: [], w: 0, h: 0 };
    roots.forEach(function (r) {
      var L = layout(r, all.h, cols);
      all.nodes = all.nodes.concat(L.nodes); all.edges = all.edges.concat(L.edges);
      all.w = Math.max(all.w, L.w); all.h += L.h + 8;
    });
    g.setAttribute('viewBox', '0 0 ' + all.w + ' ' + all.h);
    var ge = svg('g', {}, g), gt = svg('g', {}, g), gn = svg('g', {}, g), gm = svg('g', {}, g), shown = {}, pos = {};
    drawn.layer = gt; drawn.tags = gm;
    all.edges.forEach(function (pair) {
      var a = pair[0], b = pair[1], x1 = a.x + a.hw, x2 = b.x - b.hw, c = (x2 - x1) / 2;
      var p = svg('path', { d: 'M' + x1 + ',' + a.y + ' C' + (x1 + c) + ',' + a.y + ' ' + (x2 - c) + ',' + b.y + ' ' + x2 + ',' + b.y, 'class': 'edge' }, ge);
      drawn.edges.push({ el: p, from: a.n.metric, to: b.n.metric, depth: b.d });
    });
    all.nodes.forEach(function (i) {
      shown[i.n.metric] = true; if (!pos[i.n.metric]) pos[i.n.metric] = i;
      var outcome = !!obj(meta[i.n.metric]).outcome;
      var gr = svg('g', { 'class': 'node' + (i.n.repeated ? ' repeated' : '') + (outcome ? ' root' : ''), tabindex: 0, role: 'button', 'aria-label': str(i.n.metric), transform: 'translate(' + (i.x - i.hw) + ',' + (i.y - NODE_H / 2) + ')' }, gn);
      svg('rect', { width: 2 * i.hw, height: NODE_H, rx: 5 }, gr);
      var t = svg('text', { x: i.hw, y: NODE_H / 2, dy: '0.35em' }, gr); t.textContent = str(i.n.metric);
      var pick = function () { pickNode(i.n.metric); };
      gr.addEventListener('click', pick);
      gr.addEventListener('keydown', function (e) {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); }
        if (e.key === 'ArrowDown' || e.key === 'ArrowUp') { e.preventDefault(); stepNode(gr, e.key === 'ArrowDown' ? 1 : -1); }
      });
      drawn.nodes.push({ el: gr, metric: i.n.metric, item: i });
    });
    list(graph && graph.edges).filter(function (e) { return e.kind === 'may_affect' && pos[e.source] && pos[e.target]; }).forEach(function (e) {
      var a = pos[e.source], b = pos[e.target], mx = (a.x + b.x) / 2 + 40, my = (a.y + b.y) / 2;
      svg('path', { d: 'M' + (a.x + a.hw) + ',' + a.y + ' Q' + (mx + 40) + ',' + my + ' ' + (b.x + b.hw) + ',' + b.y, 'class': 'edge may' }, ge);
      var sg = svg('g', { 'class': 'sign', transform: 'translate(' + (mx + (a.hw + b.hw) / 4) + ',' + my + ')' }, ge);
      svg('circle', { r: 9 }, sg); var s = svg('text', { dy: '0.35em' }, sg); s.textContent = e.sign > 0 ? '+' : e.sign < 0 ? '−' : '?';
      svg('title', {}, sg).textContent = 'Possible cause: ' + e.source + ' may move ' + e.target + (e.sign > 0 ? ' up' : e.sign < 0 ? ' down' : '');
    });
    renderExtra(shown);
    fitGraph();
    applyStyles();
  }
  // arrow keys walk the boxes in reading order (top to bottom, then left to right)
  function stepNode(from, dir) {
    var l = drawn.nodes.slice().sort(function (a, b) { return a.item.x - b.item.x || a.item.y - b.item.y; });
    var k = l.map(function (n) { return n.el; }).indexOf(from), n = l[k + dir];
    if (n) n.el.focus();
  }
  function renderExtra(shown) {
    var x = $('extra'); x.textContent = '';
    if (!graph) return;
    var rest = (domains.filter(function (d) { return d.id === dom; })[0] || { metrics: [] }).metrics;
    if (dom === 'all') rest = list(graph.nodes).filter(function (n) { return n.kind === 'metric'; }).map(function (n) { return n.id; });
    if (dom === 'other') rest = list(graph.nodes).filter(function (n) { return n.kind === 'metric' && !domOf[n.id]; }).map(function (n) { return n.id; });
    rest = rest.filter(function (m) { return !(shown && shown[m]) && meta[m]; });
    if (!rest.length) return;
    x.appendChild(text(dom === 'all' ? 'Off the formula tree:' : 'Also in this domain, off the tree:'));
    rest.forEach(function (m, k) { var b = el('button', null, [m]); b.dataset.m = m; b.dataset.k = k; b.onclick = function () { pickNode(m); }; x.appendChild(b); });
    renderExtraSel(); extraMore();
  }
  // a fade at the right edge says the row of off-tree metrics scrolls on
  function extraMore() { var x = $('extra'); x.classList.toggle('more', x.scrollLeft + x.clientWidth < x.scrollWidth - 2); }
  $('extra').addEventListener('scroll', extraMore);
  // an off-tree metric the selected incident moved (search_ctr, say) is lit in its status colour and comes first in the row
  function renderExtraSel() {
    var x = $('extra'), inc = mode === 'incident' ? byId(selInc) : null, lit = {};
    if (inc) { incidentNodes(inc).forEach(function (m) { lit[m] = true; }); list(inc.drivers).forEach(function (d) { d = obj(d); if (d.metric) lit[str(d.metric)] = true; }); }
    var bs = Array.prototype.slice.call(x.querySelectorAll('button'));
    bs.forEach(function (b) {
      b.className = '';
      if (mode === 'node' && b.dataset.m === selNode) b.classList.add('picked');
      if (lit[b.dataset.m]) { b.classList.add('lit'); b.classList.add(stOf(inc)); }
    });
    var want = bs.slice().sort(function (a, b) { return (lit[b.dataset.m] ? 1 : 0) - (lit[a.dataset.m] ? 1 : 0) || a.dataset.k - b.dataset.k; });
    if (want.some(function (b, k) { return b !== bs[k]; })) {          // re-order only when it changes, so focus stays put
      want.forEach(function (b) { x.appendChild(b); });
      if (want.length && lit[want[0].dataset.m]) x.scrollLeft = 0;
      extraMore();
    }
  }
  function applyStyles() {
    if (!drawn || !drawn.layer) return;
    var h = health(), inc = byId(selInc), on = {};
    if (inc && mode === 'incident') {
      on[inc.metric] = true;
      var kids = {};
      drawn.edges.forEach(function (e) { (kids[e.from] = kids[e.from] || []).push(e.to); });
      var path = function (from, target, seen) {
        if (from === target) return [from];
        if (seen[from]) return null; seen[from] = true;
        var ks = kids[from] || [];
        for (var k = 0; k < ks.length; k++) { var r = path(ks[k], target, seen); if (r) return [from].concat(r); }
        return null;
      };
      list(inc.contributions).forEach(function (c) { if (!c || !c.node) return; on[c.node] = true; (path(inc.metric, c.node, {}) || []).forEach(function (m) { on[m] = true; }); });
      list(inc.trail).forEach(function (t) { t = obj(t); if (t.action === 'watch' && t.target && meta[t.target]) on[t.target] = true; });
    }
    var g = $('g'), lit = Object.keys(on).length > 0;
    g.classList.toggle('dim', lit);
    KNOWN.concat(['other']).forEach(function (k) { g.classList.remove('st-' + k); });
    if (lit) g.classList.add(stOf(inc));
    drawn.nodes.forEach(function (n) {
      var c = n.el.classList;
      ['h-open', 'h-ongoing', 'h-resolved'].forEach(function (k) { c.remove(k); });
      if (h[n.metric]) c.add('h-' + h[n.metric]);
      c.toggle('on', !!on[n.metric]);
      c.toggle('picked', mode === 'node' && selNode === n.metric);
      c.toggle('context', dom !== 'all' && domainOf(n.metric) !== dom);
    });
    drawn.edges.forEach(function (e) { e.el.classList.toggle('on', !!(on[e.from] && on[e.to])); });
    markMoved(lit ? inc : null);
    trace(lit ? inc : null);
    renderExtraSel();
  }
  function movedOf(inc) {
    var at = {}, best = null;
    drawn.nodes.forEach(function (n) { if (!at[n.metric] || (at[n.metric].item.n.repeated && !n.item.n.repeated)) at[n.metric] = n; });
    list(inc.contributions).forEach(function (c) { c = obj(c); if (at[c.node] && (!best || (num(c.share_of_delta) || 0) > (num(best.share_of_delta) || 0))) best = c; });
    if (best) return { node: at[best.node], observed: best.observed, expected: best.expected };
    var d = obj(list(inc.drivers)[0]), ch = obj(inc.change);
    if (at[d.metric]) return { node: at[d.metric], observed: d.observed, expected: d.expected };
    return at[inc.metric] ? { node: at[inc.metric], observed: ch.observed, expected: ch.expected } : null;
  }
  function markMoved(inc) {
    var layer = drawn.tags; layer.textContent = '';
    drawn.nodes.forEach(function (n) { n.el.classList.remove('moved'); });
    var m = inc && movedOf(inc);
    if (!m) return;
    m.node.el.classList.add('moved');
    if (num(m.observed) == null || num(m.expected) == null) return;
    var i = m.node.item, label = short(m.observed) + ' vs ' + short(m.expected), w = Math.ceil(label.length * 6.8) + 12, H = 15;
    var vb = drawn.box, top = i.y - NODE_H / 2, spots = [[i.x + i.hw - w, top - H - 3], [i.x - i.hw - GAP - w, i.y - H / 2], [i.x + i.hw - w, top + NODE_H + 3]];
    var clear = function (s) {
      return s[0] >= 0 && s[1] >= 0 && (!vb || s[1] + H <= vb.h) && !drawn.nodes.some(function (n) {
        var o = n.item; return o !== i && o.x + o.hw > s[0] - 2 && o.x - o.hw < s[0] + w + 2 && o.y + NODE_H / 2 > s[1] - 2 && o.y - NODE_H / 2 < s[1] + H + 2;
      });
    };
    var at = spots.filter(clear)[0] || [spots[0][0], top - H + 1];
    var tag = svg('g', { 'class': 'moved-tag', transform: 'translate(' + at[0] + ',' + at[1] + ')' }, layer);
    svg('rect', { width: w, height: H, rx: 3 }, tag);
    var t = svg('text', { x: 6, y: 11 }, tag); t.textContent = label;
    svg('title', {}, tag).textContent = 'observed ' + short(m.observed) + ', expected ' + short(m.expected);
  }
  // the graph fills its pane: as large as width and height allow (at most 1.35 times its size); below 0.74 it scrolls
  function fitGraph() {
    var g = $('g'), wrap = $('graphwrap'), v = g.viewBox && g.viewBox.baseVal;
    if (!v || !v.width || !v.height || !drawn) return;
    drawn.box = { w: v.width, h: v.height };
    var cs = getComputedStyle(wrap);
    var w = wrap.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight) - 2;
    var h = wrap.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom) - 2;
    var s = Math.min(w / v.width, h > 80 ? h / v.height : Infinity, 1.35);
    s = Math.max(0.66, s);
    g.style.width = Math.floor(v.width * s) + 'px';
  }
  // on a narrow screen the graph scrolls; a newly selected incident brings its moved box into view
  function revealMoved() {
    var wrap = $('graphwrap'), m = drawn && drawn.nodes.filter(function (n) { return n.el.classList.contains('moved'); })[0];
    if (!m || (wrap.scrollWidth <= wrap.clientWidth + 2 && wrap.scrollHeight <= wrap.clientHeight + 2)) return;
    var r = m.el.getBoundingClientRect(), w = wrap.getBoundingClientRect();
    if (r.left < w.left || r.right > w.right) wrap.scrollLeft += (r.left + r.right) / 2 - (w.left + w.right) / 2;
    if (r.top < w.top || r.bottom > w.bottom) wrap.scrollTop += (r.top + r.bottom) / 2 - (w.top + w.bottom) / 2;
  }
  var traceKey = '', tracing = null;
  function trace(inc) {
    var key = inc ? str(inc.id) + '|' + dom : '';
    if (key === traceKey) return;
    traceKey = key;
    revealMoved();
    var g = $('g'), layer = drawn.layer;
    tracing = null; layer.textContent = ''; g.classList.remove('tracing');
    var lit = drawn.edges.filter(function (e) { return e.el.classList.contains('on'); });
    if (!inc || reduced() || !lit.length || !window.requestAnimationFrame) return;
    var depths = lit.map(function (e) { return e.depth; }).filter(function (d, k, a) { return a.indexOf(d) === k; }).sort(function (a, b) { return b - a; });
    var items = lit.map(function (e) {
      var p = e.el.cloneNode(false); p.setAttribute('class', 'trace-line'); layer.appendChild(p);
      var len = p.getTotalLength(); p.style.strokeDasharray = len + ' ' + len; p.style.strokeDashoffset = String(-len);
      return { el: p, len: len, j: depths.indexOf(e.depth) };
    });
    g.classList.add('tracing');
    var run = {}, t0 = null, T = 700, n = depths.length;
    var done = function () { if (tracing !== run) return; tracing = null; layer.textContent = ''; g.classList.remove('tracing'); };
    tracing = run;
    var frame = function (now) {
      if (tracing !== run) return;
      if (t0 == null) t0 = now;
      var t = Math.min(1, (now - t0) / T), p = 1 - Math.pow(1 - t, 3);
      items.forEach(function (it) { var f = Math.max(0, Math.min(1, p * n - it.j)); it.el.style.strokeDashoffset = String(-it.len * (1 - f)); });
      if (t < 1) window.requestAnimationFrame(frame); else done();
    };
    window.requestAnimationFrame(frame);
    setTimeout(done, T + 500);
  }

  // ------------------------------------------------------------- the queue: grouped by status, live on top
  var GROUPS = [
    { label: 'Live', sts: ['open', 'ongoing'], st: 'st-open', note: 'severity first' },
    { label: 'Resolved', sts: ['resolved'], st: 'st-resolved' },
    { label: 'Explained, no alert', sts: ['explained'], st: 'st-explained' },
    { label: 'Waiting for data', sts: ['pending'], st: 'st-pending' }];
  function groupOf(i) { var s = status(i); for (var k = 0; k < GROUPS.length; k++) if (GROUPS[k].sts.indexOf(s) >= 0) return k; return GROUPS.length; }
  // the queue's order (the API's order within each group: severity first, then the newest) is also j / k's order
  function ordered() {
    return inScope().map(function (i, k) { return { i: i, k: k, g: groupOf(i) }; })
      .sort(function (a, b) { return a.g - b.g || a.k - b.k; }).map(function (x) { return x.i; });
  }
  var arrived = {}, firstLoad = true, FRESH_MS = 60000;
  function isFresh(i) { return arrived[i.id] && Date.now() - arrived[i.id] < FRESH_MS; }
  function sevMeter(sev) {
    var n = { high: 3, medium: 2, low: 1 }[str(sev)] || 0, m = el('span', 'sevm');
    for (var k = 1; k <= 3; k++) m.appendChild(el('i', k <= n ? 'on' : null));
    m.title = n ? str(sev) + ' severity' : 'no severity';
    return m;
  }
  function selectInc(id, opts) {
    selInc = id; mode = 'incident'; selNode = null;
    renderFeed(); renderDetail(true); applyStyles(); renderRibbon(); renderExtraSel();
    if (!opts || !opts.keepFocus) { var r = $('feed').querySelector('.inc.sel'); if (r && opts && opts.focus) r.focus({ preventScroll: true }); }
    syncUrl();
  }
  var feedSel = null;
  function renderFeed() {
    $('feed-title').textContent = 'Incidents' + (dom === 'all' ? '' : ' · ' + domainLabel(dom));
    var f = $('feed'), keepScroll = f.scrollTop, hadFocus = document.activeElement && document.activeElement.classList.contains('inc');
    f.textContent = '';
    var l = ordered(), live = l.filter(function (i) { return LIVE.indexOf(status(i)) >= 0; }).length;
    $('feed-sub').textContent = l.length ? live + ' live of ' + l.length : '';
    if (!l.length) f.appendChild(el('div', 'empty', [dom === 'all' ? 'No incidents. The monitor is watching every hour of data.' : 'No incident touches ' + domainLabel(dom) + '.']));
    var lastG = -1;
    l.forEach(function (i) {
      try {
        var g = groupOf(i);
        if (g !== lastG) {
          lastG = g; var G = GROUPS[g] || { label: 'Other', st: 'st-other' }, gst = g === 0 && !l.some(function (x) { return status(x) === 'open'; }) ? 'st-ongoing' : G.st;
          var cnt = l.filter(function (x) { return groupOf(x) === g; }).length;
          f.appendChild(el('div', 'grp-head ' + gst, [el('span', 'dot'), el('b', null, [G.label]), String(cnt)].concat(G.note && cnt > 1 ? [el('span', 'note', [G.note])] : [])));
        }
        var ch = obj(i.change), st = status(i), d = obj(list(i.drivers)[0]);
        var quiet = st === 'explained' || st === 'pending';
        var moved = d.metric ? str(d.metric) : str(i.metric), dp = d.metric ? d.delta_pct : ch.delta_pct, eur = euroOf(i);
        var line = [el('span', 'inc-metric', [moved])];
        if (num(dp) != null) line.push(el('span', 'chg ' + toneIn(i, moved, dp), [pct(dp)]));
        var sub = [el('span', null, [segText(i.segment) + (moved !== str(i.metric) ? ' · ' + str(i.metric) + (num(ch.delta_pct) != null ? ' ' + pct(ch.delta_pct) : '') : '')])];
        if (eur != null) sub.push(el('span', 'chg eur ' + euroTone(i, eur), [eurText(eur)]));
        // the foot: when (never cut), then the id, which gives way; a quiet record's long id is left to the case file
        var foot = [el('span', 'when', [st === 'resolved' && i.resolved_at ? shortHour(i.first_seen) + ' to ' + shortHour(i.resolved_at)
          : (LIVE.indexOf(st) >= 0 ? st + ' since ' : '') + shortHour(i.first_seen)])];
        if (!quiet) foot.push(el('span', 'id', [str(i.id)]));
        if (st === 'explained' && obj(i.innocent).kind) foot.push(el('span', 'tag innocent', ['innocent: ' + str(obj(i.innocent).kind)]));
        if (st === 'pending') foot.push(el('span', 'tag innocent', ['data late']));
        var kids = [sevMeter(quiet ? null : i.severity), el('div', 'inc-line', line), el('div', 'inc-sub', sub), el('div', 'inc-foot', foot)];
        if (i.recurrence_of) kids.push(el('div', 'inc-tags', [el('span', 'tag again', ['back again']), el('span', null, ['of ' + str(i.recurrence_of)])]));
        var row = el('div', 'inc ' + stOf(i) + (quiet ? ' quiet' : '') + (i.id === selInc && mode === 'incident' ? ' sel' : '') + (isFresh(i) ? ' fresh' : ''), kids);
        row.tabIndex = 0; row.dataset.id = i.id; row.setAttribute('role', 'listitem');
        row.setAttribute('aria-label', st + ' ' + (i.severity && !quiet ? i.severity + ' severity ' : '') + moved + ' ' + segText(i.segment) + ', ' + i.id);
        row.title = 'First seen ' + when(i.first_seen) + ' UTC';
        row.addEventListener('click', function () { selectInc(i.id, { keepFocus: true }); });
        row.addEventListener('keydown', function (e) { if (e.key === 'Enter') selectInc(i.id, { focus: true }); });
        f.appendChild(row);
      } catch (err) { f.appendChild(el('div', 'empty', ['An incident (' + str(i.id) + ') could not be shown: ' + err.message])); }
    });
    if (skipped) f.appendChild(el('div', 'empty', [skipped + ' file' + (skipped > 1 ? 's' : '') + ' in the incidents folder ignored (not an incident, or still being written).']));
    f.scrollTop = keepScroll;
    var r = f.querySelector('.inc.sel');
    if (hadFocus && r) r.focus({ preventScroll: true });
    if (!r) { feedSel = null; return; }
    if (selInc === feedSel) return;
    feedSel = selInc;
    var fb = f.getBoundingClientRect(), rb = r.getBoundingClientRect(), head = 34;
    if (rb.top < fb.top + head) f.scrollTop += rb.top - fb.top - head; else if (rb.bottom > fb.bottom) f.scrollTop += rb.bottom - fb.bottom + 4;
  }

  // ------------------------------------------------------------- the time ribbon (signature)
  var spanEls = {}, tickEls = {}, injEls = {};
  function nowTs() { return state && state.ready ? Date.parse(str(state.last_hour)) + HOUR : null; }
  function spanOf(i, now) {
    var seen = Date.parse(str(i.first_seen)); if (!isFinite(seen)) return null;
    var onset = Date.parse(str(obj(obj(i.change).window).start)), st = status(i), end;
    if (LIVE.indexOf(st) >= 0) end = now;
    else if (st === 'resolved') end = Date.parse(str(i.resolved_at)) || Date.parse(str(i.last_seen)) + HOUR;
    else end = Date.parse(str(i.last_seen)) + HOUR || Date.parse(str(obj(obj(i.change).window).end));
    if (!isFinite(end) || end < seen + HOUR) end = seen + HOUR;
    return { i: i, seen: seen, onset: isFinite(onset) && onset < seen ? onset : seen, end: Math.min(end, now), running: LIVE.indexOf(st) >= 0 };
  }
  function keyed(map, id, make, parent) { var e = map[id]; if (!e) { e = map[id] = make(); parent.appendChild(e); } e._seen = true; return e; }
  function sweep(map) { Object.keys(map).forEach(function (k) { if (!map[k]._seen) { map[k].remove(); delete map[k]; } else map[k]._seen = false; }); }
  // Every item on the ribbon is a mark in time plus its label: inside the bar when it fits, else after it, else (near
  // "now") before it. Items are packed into lanes by the room their mark and label take, so no two labels collide.
  // When the lanes run out, an item keeps its mark but drops its outside label (the tooltip and the queue still name it),
  // so two labels are never drawn over each other.
  function pack(items, W, maxLanes) {
    var ends = [];
    var free = function (x0) { for (var k = 0; k < ends.length; k++) if (ends[k] + 10 <= x0) return k; return ends.length; };
    items.forEach(function (s) {
      s.lw = Math.min(260, Math.ceil(s.label.length * 6.7) + 10); s.bare = false; s.inside = false;
      if (s.bw >= Math.min(s.lw, 120) + 4) { s.lx = s.xb; s.inside = true; }      // a long enough bar holds its label, cut if need be
      else if (s.xb + s.bw + 6 + s.lw <= W) s.lx = s.xb + s.bw + 6;
      else s.lx = Math.max(0, s.x0 - 6 - s.lw);
      s.e0 = Math.min(s.x0, s.lx); s.e1 = Math.max(s.x1, s.inside ? s.x1 : s.lx + s.lw);
      var k = free(s.e0);
      if (k >= maxLanes) {
        s.bare = !s.inside; s.e0 = s.x0; s.e1 = s.x1; s.lx = s.xb;
        k = free(s.e0);
        if (k >= maxLanes) { k = 0; for (var j = 1; j < ends.length; j++) if (ends[j] < ends[k]) k = j; }
      }
      s.lane = k; ends[k] = Math.max(ends[k] || 0, s.e1);
    });
    return Math.max(ends.length, 0);
  }
  function renderRibbon() {
    var now = nowTs(), lanesEl = $('rb-lanes'), axis = $('rb-axis'), body = $('rb-body');
    if (now == null) return;
    // the window fits what happened: 24 hours at the least, in 6-hour steps, up to 48 hours
    var far = now - WINDOW_H * HOUR, first = now;
    inScope().forEach(function (i) { var s = spanOf(i, now); if (s && s.end > far) first = Math.min(first, s.onset); });
    list(state.injected).forEach(function (j) {
      var sc = obj(j.scope), s = Date.parse(str(sc.start)), e = Date.parse(str(sc.end));
      if (isFinite(s) && (isFinite(e) ? Math.min(e, now) : now) > far) first = Math.min(first, s);
    });
    var winH = Math.max(24, Math.min(WINDOW_H, Math.ceil(((now - Math.max(first, far)) / HOUR + 3) / 6) * 6));
    $('rb-title').textContent = 'Last ' + winH + ' simulated hours';
    $('ribbon').setAttribute('aria-label', 'The last ' + winH + ' simulated hours');
    var W = body.clientWidth || 1000, t0 = now - winH * HOUR;
    var X = function (t) { return Math.max(0, Math.min(W, (t - t0) / (winH * HOUR) * W)); };
    var P = function (x) { return x / W * 100; };
    // ticks every 6 hours of Berlin time; midnight carries the day
    for (var t = Math.ceil(t0 / HOUR) * HOUR; t <= now; t += HOUR) {
      var p = parts(t), h = +p.hour;
      if (h % 6) continue;
      var tk = keyed(tickEls, 't' + t, function () { return el('div', 'tick', [el('span')]); }, axis);
      tk.className = 'tick' + (h === 0 ? ' day' : '');
      tk.firstChild.textContent = h === 0 ? p.weekday + ' ' + p.day : p.hour + ':00';
      tk.style.left = P(X(t)) + '%';
      tk.style.display = X(t) > W - 40 ? 'none' : '';
    }
    sweep(tickEls);
    // the bench track: what the test bench injected (ground truth the monitor never reads), on its own lanes
    var inj = list(state.injected).map(function (j) {
      var sc = obj(j.scope), s = Date.parse(str(sc.start)), e = Date.parse(str(sc.end));
      var where = ['country', 'platform', 'category', 'source'].map(function (d) { return sc[d]; }).filter(Boolean).join(' ');
      e = isFinite(e) ? Math.min(e, now) : now;
      var x0 = X(s), x1 = Math.max(x0 + 4, X(e));
      return { j: j, s: s, e: e, x0: x0, x1: x1, xb: x0, bw: x1 - x0, label: str(j.kind).replace(/_/g, ' ') + (where ? ' ' + where : '') };
    }).filter(function (x) { return isFinite(x.s) && x.e > t0; }).sort(function (a, b) { return a.s - b.s; });
    var benchLanes = Math.min(3, pack(inj, W, 3)), INJ = 13;
    inj.forEach(function (x) {
      var e = keyed(injEls, 'j' + x.j.id, function () { return el('div', 'inj', [el('i'), el('span')]); }, lanesEl);
      e.className = 'inj' + (x.j.active ? '' : ' ended');
      e.title = 'Test bench: ' + str(x.j.description || x.j.kind) + ', from ' + displayHour(x.s) + (x.j.active ? ' (still running)' : ' (ended)');
      e.style.left = P(x.e0) + '%'; e.style.width = P(x.e1 - x.e0) + '%'; e.style.top = (x.lane * INJ) + 'px';
      var line = e.firstChild, lab = e.lastChild;
      line.style.left = (x.x0 - x.e0) + 'px'; line.style.width = (x.x1 - x.x0) + 'px';
      lab.textContent = x.label; lab.style.left = (x.lx - x.e0) + 'px'; lab.style.maxWidth = x.lw + 'px'; lab.hidden = x.bare;
    });
    sweep(injEls);
    var benchH = benchLanes ? benchLanes * INJ + 4 : 0;
    // incident spans: lead-in (detection lag), the bar (first seen to resolved or now), the label
    var spans = inScope().map(function (i) { return spanOf(i, now); }).filter(Boolean);
    var before = spans.filter(function (s) { return s.end <= t0; }).length;
    spans = spans.filter(function (s) { return s.end > t0; }).sort(function (a, b) { return a.onset - b.onset; });
    var LANE = 22;
    spans.forEach(function (s) {
      var i = s.i, d = obj(list(i.drivers)[0]);
      s.moved = d.metric ? str(d.metric) : str(i.metric);
      s.label = s.moved + ' ' + segText(i.segment);
      s.x0 = X(s.onset); s.xb = X(s.seen); s.x1 = Math.max(s.xb + 6, X(s.end)); s.bw = s.x1 - s.xb;
    });
    var lanes = Math.max(1, pack(spans, W, 4));
    spans.forEach(function (s) {
      var i = s.i, top = benchH + s.lane * LANE, st = status(i);
      var b = keyed(spanEls, i.id, function () {
        var x = el('button', 'span', [el('i', 'lag'), el('i', 'bar'), el('span', 'lbl')]);
        x.addEventListener('click', function () { selectInc(x.dataset.id); });
        return x;
      }, lanesEl);
      b.dataset.id = i.id;
      b.className = 'span ' + stOf(i) + (s.running ? ' running' : '') + (s.inside ? ' inside' : '') + (i.id === selInc && mode === 'incident' ? ' sel' : '') + (isFresh(i) ? ' fresh' : '');
      b.style.left = P(s.e0) + '%'; b.style.width = P(s.e1 - s.e0) + '%'; b.style.top = top + 'px';
      var lag = b.children[0], bar = b.children[1], lbl = b.children[2];
      lag.style.left = (s.x0 - s.e0) + 'px'; lag.style.width = Math.max(0, s.xb - s.x0) + 'px'; lag.hidden = s.xb - s.x0 < 1;
      bar.style.left = (s.xb - s.e0) + 'px'; bar.style.width = s.bw + 'px';
      lbl.style.left = (s.lx - s.e0 + (s.inside ? 7 : 0)) + 'px'; lbl.style.maxWidth = (s.inside ? s.bw - 10 : s.lw) + 'px';
      lbl.textContent = s.moved; lbl.appendChild(el('em', null, [segText(i.segment)])); lbl.hidden = s.bare;
      b.setAttribute('aria-label', i.id + ', ' + st + ', ' + s.moved + ' ' + segText(i.segment) + ', first seen ' + shortHour(s.seen) + (s.running ? ', still running' : ', ended ' + shortHour(s.end)));
      b.title = i.id + ' · ' + st + '\n' + s.label + (s.onset < s.seen ? '\nchange began ' + shortHour(s.onset) : '') + '\nfirst seen ' + shortHour(s.seen) + (s.running ? ', still running' : '\nended ' + shortHour(s.end));
    });
    sweep(spanEls);
    var empty = lanesEl.querySelector('.rb-empty');
    if (!spans.length && !inj.length) {
      if (!empty) empty = lanesEl.appendChild(el('div', 'rb-empty'));
      empty.textContent = 'Nothing in the last ' + winH + ' simulated hours' + (dom === 'all' ? '' : ' in ' + domainLabel(dom)) + '. A new incident appears here as a span, from the hour it was first seen.';
    } else if (empty) empty.remove();
    body.style.height = (18 + benchH + lanes * LANE) + 'px';
    $('rb-earlier').textContent = before ? before + ' earlier, before the window' : '';
    var np = clockParts(now - HOUR);
    $('rb-now-label').textContent = 'now'; $('rb-now-label').appendChild(el('small', null, [np.hour]));
  }

  // ------------------------------------------------------------- the case file
  var jumps = [];
  function section(title, kids, cls, jump) {
    var d = el('section', cls || null, [el('h3', null, [title])]); kids.forEach(function (k) { d.appendChild(k); });
    if (jump) { d.dataset.jump = jump; jumps.push(d); }
    return d;
  }
  function kv(pairs, cls) { var k = el('div', 'kv' + (cls ? ' ' + cls : '')); pairs.forEach(function (p) { k.appendChild(el('span', null, [p[0]])); k.appendChild(el('span', null, [p[1]])); }); return k; }
  function mono(s) { return el('span', 'mono', [str(s)]); }
  function linksOf(m) {
    var out = [], E = list(graph && graph.edges);
    var by = function (kind) { return E.filter(function (e) { return e.kind === kind; }); };
    var dims = by('breakdown').filter(function (e) { return e.source === m; }).map(function (e) { return e.dimension; });
    if (dims.length) out.push(['Breaks down by', dims.join(', ')]);
    var partsL = by('formula').filter(function (e) { return e.source === m; }).map(function (e) { return e.target + (e.role ? ' (' + e.role + ')' : ''); });
    if (partsL.length) out.push(['Made of', partsL.join(', ')]);
    var feeds = by('formula').filter(function (e) { return e.target === m; }).map(function (e) { return e.source; });
    if (feeds.length) out.push(['Feeds', feeds.join(', ')]);
    var defs = by('definition').filter(function (e) { return e.source === m; }).map(function (e) { return e.target + (e.role ? ' (' + e.role + ')' : ''); });
    if (defs.length) out.push(['Defined from', defs.join(', ')]);
    var ctx = by('context').filter(function (e) { return e.target === m; }).map(function (e) { return e.source + (e.via ? ' (' + e.via + ')' : ''); });
    if (ctx.length) out.push(['Context', ctx.join(', ')]);
    var cause = function (e, other) { return e[other] + ' ' + (e.sign > 0 ? '+' : e.sign < 0 ? '−' : '?') + (num(e.prior) != null ? ', prior ' + e.prior : '') + (e.status ? ', ' + e.status : '') + (e.note ? ': ' + e.note : ''); };
    var affects = by('may_affect').filter(function (e) { return e.source === m; }).map(function (e) { return cause(e, 'target'); });
    if (affects.length) out.push(['May affect', affects.join('; ')]);
    var affected = by('may_affect').filter(function (e) { return e.target === m; }).map(function (e) { return cause(e, 'source'); });
    if (affected.length) out.push(['May be affected by', affected.join('; ')]);
    return out;
  }
  var answers = {};
  function checksSection(a) {
    var rows = list(a.checks).map(function (c) {
      c = obj(c); var s = str(c.status);
      return el('div', 'hyp check', [el('span', 'verdict ' + s, [s || '-']),
        el('div', null, [el('div', 'hyp-cause', [str(c.check)]), el('div', 'small muted', [str(c.detail)])])]);
    });
    return section('Checks of the extension agent', [el('p', 'about', [str(a.message)])].concat(rows.length ? rows : [el('div', 'empty', ['The answer lists no checks.'])]), null, 'Checks');
  }
  function loadAnswer(name) {
    return get('/api/metric/' + encodeURIComponent(name)).then(function (r) {
      answers[name] = r; if (mode === 'node' && selNode === name) renderDetail(); return r;
    });
  }
  function showChecks(name) { pickNode(name); }
  function renderNode(d) {
    var m = meta[selNode], a = answers[selNode];
    var t = $('detail-title'); t.textContent = selNode;
    $('detail-status').appendChild(el('span', 'sevword', ['metric definition']));
    if (!m) {
      if (a) { $('detail-status').textContent = ''; $('detail-status').appendChild(el('span', 'pill st-other', [str(a.status)])); d.appendChild(checksSection(a)); }
      else { d.appendChild(el('div', 'empty', ['This metric is not in the graph.'])); loadAnswer(selNode).catch(function () {}); }
      return;
    }
    var pairs = [['Domain', domainLabel(domainOf(selNode))], ['Status', m.status || '-'], ['Origin', m.origin || '-'], ['Unit', m.unit || '-'], ['Direction', m.direction || '-']];
    if (m.formula) pairs.push(['Formula', mono(m.formula)]);
    if (m.decomposition) pairs.push(['Made of', mono(m.decomposition)]);
    if (m.column) pairs.push(['Column', mono(m.column)]);
    if (list(m.systems).length) pairs.push(['Systems', m.systems.join(', ')]);
    if (m.source) pairs.push(['Data source', m.source]);
    pairs.push(['Dimensions', list(m.dimensions).join(', ') || '-']);
    d.appendChild(section('Definition', [el('p', 'about summary', [m.description || 'No description.']), kv(pairs)], null, 'Definition'));
    var links = linksOf(selNode);
    d.appendChild(section('Links by kind', links.length ? [kv(links)] : [el('div', 'empty', ['No links recorded for this metric.'])], null, 'Links'));
    if (a) d.appendChild(checksSection(a));
    else if (m.origin === 'contributed') loadAnswer(selNode).catch(function () {});
    var inc = incidents.filter(function (i) { return incidentNodes(i).indexOf(selNode) >= 0; });
    var box = inc.length ? inc.map(function (i) {
      var b = el('button', 'involved', [str(i.id) + ' ' + str(i.metric) + ' (' + status(i) + ')']);
      b.onclick = function () { selectInc(i.id); }; return b;
    }) : [el('div', 'empty', ['No incident involves this metric.'])];
    d.appendChild(section('Incidents involving it', [el('div', null, box)], null, 'Incidents'));
  }
  function cell(label, value, cls) { return el('div', 'cell', [el('span', 'label', [label]), el('span', 'big ' + (cls || ''), [value])]); }
  function renderIncident(d, i) {
    $('detail-title').textContent = str(i.id);
    var ds = $('detail-status');
    ds.appendChild(el('span', 'pill ' + stOf(i), [status(i)]));
    if (i.severity && status(i) !== 'explained' && status(i) !== 'pending') { var sw = el('span', 'sevword ' + stOf(i), [sevMeter(i.severity), str(i.severity)]); ds.appendChild(sw); }
    d.className = 'detail ' + stOf(i);
    if (obj(i.innocent).reason) d.appendChild(section('Why no alert', [el('p', 'summary', [str(obj(i.innocent).reason)])], 'why', 'Why no alert'));
    if (i.recurrence_of) {
      var back = el('button', 'linkish', ['Open ' + str(i.recurrence_of)]);
      back.addEventListener('click', function () { selectInc(str(i.recurrence_of)); });
      d.appendChild(el('div', 'again-link', [el('span', 'tag again', ['back again']), el('span', null, ['Same metric and place as ' + str(i.recurrence_of) + '.']), back]));
    }
    var ch = obj(i.change), w = obj(ch.window), imp = obj(i.impact), effect = euroOf(i);
    var figs = [ch.observed, ch.expected, ch.delta_pct, effect].some(function (v) { return num(v) != null; }) ? el('div', 'readout', [
      cell('Observed', short(ch.observed)), cell('Expected', short(ch.expected)),
      cell('Change', pct(ch.delta_pct), 'chg ' + toneIn(i, i.metric, ch.delta_pct)),
      cell('Effect, EUR', effect == null ? '–' : (effect > 0 ? '+' : '') + short(effect), 'chg ' + euroTone(i, effect))])
      : el('p', 'muted', [status(i) === 'pending' ? 'No figures yet: the data for these hours has not arrived.' : 'No figures in this report.']);
    var fsec = el('section', null, [figs]); fsec.dataset.jump = 'Figures'; jumps.push(fsec); d.appendChild(fsec);
    var story = obj(i.narrative), told = story.source === 'model' && !!str(story.text).trim();
    if (told) {
      var brief = [];
      var asOf = Date.parse(str(story.as_of)), seen = Date.parse(str(i.last_seen));
      if (isFinite(asOf) && isFinite(seen) && asOf < seen)
        brief.push(el('p', 'brief-stale', ['written at ' + ('0' + new Date(asOf).getUTCHours()).slice(-2) + ':00 UTC; the figures above are newer']));
      brief.push(el('p', 'brief', [str(story.text)]),
        el('p', 'brief-note', ['Written by ' + (str(story.model) || 'a language model') + ' from this report' + (story.as_of ? ' at ' + when(story.as_of) + ' UTC' : '')
          + '; every number in it was checked against the evidence.']));
      d.appendChild(section('In brief', brief, 'brief-sec', 'In brief'));
    } else if (i.report) d.appendChild(section('Summary', [el('p', 'summary', [str(i.report)])], null, 'Summary'));
    var rows = [['Metric', mono(i.metric)], ['Place', segText(i.segment)],
                ['Change', num(ch.delta_pct) != null ? pct(ch.delta_pct) + (num(ch.z) != null ? ' (z ' + fmt(ch.z) + ')' : '') : '-']];
    if (num(ch.delta) != null) rows.push(['Difference', fmt(ch.delta)]);
    if (w.start || w.end) rows.push(['Window', when(w.start) + ' to ' + when(w.end) + ' UTC']);
    rows.push(['First seen', when(i.first_seen) + ' UTC']);
    if (i.resolved_at) rows.push(['Resolved', when(i.resolved_at) + ' UTC']);
    d.appendChild(section('What changed', [kv(rows)], null, 'Changed'));
    var ik = Object.keys(imp).map(function (k) { var v = Number(imp[k]); return [k, fmt(v, Number.isInteger(v) ? 0 : 2)]; });
    var dec = function (s) { var p = s.indexOf('.'); return p < 0 ? 0 : s.length - p; }, D = Math.max.apply(null, ik.map(function (p) { return dec(p[1]); }).concat(0));
    if (ik.length) d.appendChild(section('Business impact', [kv(ik.map(function (p) { return [mono(p[0]), mono(p[1] + ' '.repeat(D - dec(p[1])))]; }), 'nums')], null, 'Impact'));
    var cs = list(i.contributions).map(function (c) {
      c = obj(c);
      var share = (num(c.share_of_delta) || 0) * 100;
      var bar = el('div', 'share-bar'), b = el('i'); b.style.width = Math.max(0, Math.min(100, share)) + '%'; bar.appendChild(b);
      return el('div', 'contrib', [el('div', 'row2', [mono(c.node), el('span', 'mono', [fmt(share, 0) + '% of the change'])]), bar,
        el('div', 'small muted mono', [short(c.observed) + ' against ' + short(c.expected) + ' expected'])]);
    });
    if (cs.length) d.appendChild(section('Where along the graph', cs, null, 'Graph'));
    var loc = list(i.localization).map(function (x) {
      x = obj(x);
      return el('div', 'loc' + (x.chosen ? ' chosen' : ''), [
        el('span', 'seg', [mono(str(x.dimension) + ' = ' + str(x.segment))].concat(x.chosen ? [el('span', 'chosen-tag', ['chosen'])] : [])),
        el('span', 'mono', [fmt((num(x.share_of_delta) || 0) * 100, 0) + '%']),
        el('span', 'mono muted', [num(x.surprise) != null ? fmt(x.surprise, 2) : ''])]);
    });
    if (loc.length) d.appendChild(section('Where in the segments', [el('div', 'loc head', [el('span', null, ['Place']), el('span', null, ['Of change']), el('span', null, ['Surprise'])])].concat(loc), null, 'Segments'));
    var hs = list(i.hypotheses).map(function (h) {
      h = obj(h);
      var sc = num(h.score), meter = el('span', 'meter'), mi = el('i'); mi.style.width = (sc == null ? 0 : Math.max(0, Math.min(1, sc)) * 100) + '%'; meter.appendChild(mi);
      var about = el('div', 'score', sc != null ? [meter, 'score ' + fmt(sc, 2)].concat(h.kind ? [' · ' + str(h.kind)] : []) : (h.kind ? [str(h.kind)] : []));
      return el('div', 'hyp ' + str(h.status), [el('span', 'verdict ' + str(h.status), [str(h.status)]),
        el('div', null, [el('div', 'hyp-cause', [str(h.cause)]), about,
          el('ul', 'evidence', list(h.evidence).map(function (e) { return el('li', null, [str(e)]); }))])]);
    });
    if (hs.length) d.appendChild(section('Hypotheses', hs, null, 'Hypotheses'));
    var tr = list(i.trail).map(function (s) {
      s = obj(s);
      return el('li', null, [el('div', 'step-head', [el('span', 'verb', [str(s.action)]), el('span', 'step-target', [str(s.target)])]), el('div', 'step-result', [str(s.result)])]);
    });
    if (tr.length) d.appendChild(section('Investigation trail · ' + tr.length + ' steps', [el('ol', 'trail', tr)], null, 'Trail'));
    if (list(i.uncertain).length) d.appendChild(section('Still uncertain', [el('ul', 'uncertain', i.uncertain.map(function (u) { return el('li', null, [str(u)]); }))], null, 'Uncertain'));
    if (told && i.report) d.appendChild(section('Summary', [el('p', 'summary', [str(i.report)])], null, 'Summary'));
  }
  var detailKey = '';
  function renderDetail(fresh) {
    var d = $('detail'), sc = $('detail-scroll'), top = sc.scrollTop;
    d.textContent = ''; d.className = 'detail'; $('detail-status').textContent = ''; jumps = [];
    var back = $('back'), inc = byId(selInc);
    back.hidden = !(mode === 'node' && selNode && inc);
    if (!back.hidden) $('back-label').textContent = 'Back to ' + inc.id;
    try {
      if (mode === 'node' && selNode) renderNode(d);
      else if (!inc) { $('detail-title').textContent = 'Case file'; d.appendChild(el('div', 'empty', [incidents.length ? 'Select an incident in the queue or on the ribbon, or a box in the graph.' : 'No incident yet. Inject a fault from the test bench (b) or the command palette (/).'])); }
      else renderIncident(d, inc);
    } catch (err) { d.textContent = ''; d.appendChild(el('div', 'empty', ['This could not be shown: ' + err.message])); }
    var key = mode + '|' + (mode === 'node' ? selNode : selInc);
    sc.scrollTop = key === detailKey && !fresh ? top : 0;
    detailKey = key;
    var j = $('jump'); j.textContent = '';
    if (jumps.length > 2) jumps.forEach(function (s) {
      var b = el('button', null, [s.dataset.jump]);
      b.onclick = function () { sc.scrollTop = s.offsetTop - 6; };
      j.appendChild(b);
    });
  }

  function renderAll() { renderChips(); drawGraph(); renderFeed(); renderDetail(); renderRibbon(); }

  // ----------------------------------------------------------- loading
  function takeGraph(g) {
    graph = g; meta = {}; domOf = {}; answers = {};
    list(g.nodes).forEach(function (n) { if (n.kind === 'metric') meta[n.id] = n; });
    domains.forEach(function (d) { d.metrics.forEach(function (m) { domOf[m] = d.id; }); });
  }
  function incidentSignature() { return JSON.stringify([incidents, skipped]); }
  var wallMode = false, clockFirst = null, accelerated = false, stateKey = '';
  function clockMode(s) {
    if (s.clock_mode === 'wall') { clockFirst = null; accelerated = false; return 'Live'; }
    var hour = Date.parse(str(s.last_hour)), now = Date.parse(str(s.server_now));
    if (isFinite(hour) && isFinite(now)) {
      if (hour > now) accelerated = true;
      if (!clockFirst) clockFirst = { hour: hour, now: now };
      else if (hour - clockFirst.hour > now - clockFirst.now + HOUR) accelerated = true;
    }
    return accelerated ? 'Simulated, accelerated' : 'Replay';
  }
  function refresh() {
    get('/api/state').then(function (s) {
      state = s; wallMode = s.clock_mode === 'wall';
      var c = $('clock'), p = s.ready ? clockParts(s.last_hour) : null;
      var label = s.ready ? clockMode(s) : (wallMode ? 'Live' : 'Simulated');
      $('clock-mode').textContent = label;
      $('clock-mode').className = 'clock-mode' + (wallMode ? ' live' : '');
      $('clock-mode').title = wallMode ? 'Current hour: one data hour per real hour'
        : label === 'Replay' ? 'A finished database of simulated data, read hour by hour'
        : 'Simulated data: the simulator writes an hour of data every few seconds, faster than real time';
      c.textContent = '';
      if (p) { c.appendChild(text(p.day)); c.appendChild(el('b', null, [p.hour])); c.title = 'Newest hour of data, ' + displayHour(s.last_hour); } else c.textContent = str(s.error);
      fillEnd(list(s.injected).filter(function (i) { return i.active; }));
      var k = JSON.stringify([s.last_hour, s.injected]);
      if (k !== stateKey) { stateKey = k; renderRibbon(); }
    }).catch(function (e) { $('clock').textContent = 'Clock unavailable: ' + e.message; });
    get('/api/graph').then(function (g) {
      var sig = JSON.stringify(g);
      if (sig !== graphKey) { graphKey = sig; takeGraph(g); try { renderAll(); } catch (e) { note('Could not draw the graph: ' + e.message); } }
    }).catch(function () {});
    get('/api/incidents').then(function (r) {
      var before = incidentSignature();
      incidents = list(r.incidents); skipped = r.skipped || 0;
      var fresh = [];
      incidents.forEach(function (i) { if (!(i.id in arrived)) { arrived[i.id] = firstLoad ? 0 : Date.now(); if (!firstLoad) fresh.push(i.id); } });
      firstLoad = false;
      // a new live incident opens by itself unless a live one is already being read: fault in, report up, no hunting
      var cur = byId(selInc);
      if (fresh.length && mode === 'incident' && (!cur || LIVE.indexOf(status(cur)) < 0)) {
        var nl = ordered().filter(function (i) { return fresh.indexOf(i.id) >= 0 && LIVE.indexOf(status(i)) >= 0; })[0];
        if (nl) { selInc = nl.id; syncUrl(); }
      }
      var flipped = example && !r.example; example = !!r.example;
      $('exbadge').textContent = '';
      if (example) $('exbadge').appendChild(el('span', 'example', ['Showing the contract example: no live incident yet']));
      if (flipped || !incidents.some(function (i) { return i.id === selInc; })) {
        var sc = ordered(); selInc = sc.length ? sc[0].id : null; if (flipped) mode = 'incident';
      }
      note('');
      if (before !== incidentSignature() || flipped) { try { renderChips(); renderFeed(); renderDetail(); applyStyles(); renderRibbon(); } catch (e) { note('Could not draw the incidents: ' + e.message); } }
    }).catch(function () { note('Could not read the incidents.'); });
  }
  // a minute after an incident arrives its pulse stops
  setInterval(function () {
    Array.prototype.forEach.call(document.querySelectorAll('.fresh'), function (e) { var i = byId(e.dataset.id); if (!i || !isFresh(i)) e.classList.remove('fresh'); });
  }, 5000);

  // ------------------------------------------------------------- the test bench (bodies exactly as the reference)
  var KINDS = [
    ['payment_failure', 'Payment failure', ['country', 'platform'], 72, 0.4],
    ['stockout_surge', 'Stockouts', ['category'], 72, 0.4],
    ['search_regression', 'Search regression', ['country'], 72, 0.4],
    ['ad_budget_exhausted', 'Ad budget gone early', ['country'], 12, 0.4],
    ['traffic_drop', 'Traffic drop', ['country', 'platform'], 72, 0.5],
    ['feed_unavailable', 'Feed shows unavailable', [], 72, null],
    ['discount_push', 'Discount push', [], 72, null]];
  var OPTS = { country: ['DE', 'FR', 'ES', 'PL'], platform: ['Android', 'iOS', 'Web'], category: ['electronics', 'fashion', 'home', 'beauty', 'sports'] };
  var PROMO = ['FR', 'DE', 'ES', 'PL'], MIX = ['fashion', 'electronics', 'home', 'beauty', 'sports'], SOURCES = ['payments', 'orders', 'traffic', 'search', 'recommendations', 'ads', 'inventory'];
  function faultBody(kind, value) {
    var k = KINDS.filter(function (x) { return x[0] === kind; })[0], hours = k[3];
    var body = { kind: kind, hours: hours, when: 'now' };
    if (k[4] != null) body.severity = k[4];
    k[2].forEach(function (p) { body[p] = value(p); });
    return body;
  }
  function mixShiftBody(category) { return { kind: 'mix_shift', category: category, hours: 12, when: 'now' }; }
  function promoBody(country) { return { kind: 'promo', country: country, hours: 12, when: 'now' }; }
  function delayBody(source) { return { kind: 'data_delay', source: source, hours: 3, delay_hours: 2, when: 'now' }; }
  var REFUND_YAML = 'name: refund_rate\ndescription: Share of orders refunded\nsql: >\n  SELECT ts, country, platform, category, channel,\n         SUM(refunds) * 1.0 / NULLIF(SUM(orders), 0) AS value\n  FROM facts_hourly GROUP BY ts, country, platform, category, channel\ndimensions: [country, platform, category, channel]\nunit: ratio\n';
  var DUP_YAML = 'name: payment_rate\ndescription: Successful payments per payment attempt\nsql: >\n  SELECT ts, country, platform, category, channel,\n         SUM(payment_successes) * 1.0 / NULLIF(SUM(payment_attempts), 0) AS value\n  FROM facts_hourly GROUP BY ts, country, platform, category, channel\ndimensions: [country, platform, category, channel]\nunit: ratio\n';

  function fill(id, values) { var s = $(id); values.forEach(function (v) { var o = document.createElement('option'); o.value = v; o.textContent = v; s.appendChild(o); }); }
  KINDS.forEach(function (k) { var o = document.createElement('option'); o.value = k[0]; o.textContent = k[1]; $('f-kind').appendChild(o); });
  fill('f-country', OPTS.country); fill('f-platform', OPTS.platform); fill('f-category', OPTS.category);
  fill('pr-country', PROMO); fill('ms-category', MIX); fill('dd-source', SOURCES);
  function showParams() {
    var need = KINDS.filter(function (k) { return k[0] === $('f-kind').value; })[0][2];
    ['country', 'platform', 'category'].forEach(function (p) { $('f-' + p).hidden = need.indexOf(p) < 0; });
  }
  $('f-kind').onchange = showParams; showParams();

  // messages: in the bench when it is open, as a toast in the corner otherwise (and always for the monitor's answers)
  function toast(msg, opts) {
    opts = opts || {};
    var b = $('toast'); b.textContent = ''; b.appendChild(typeof msg === 'string' ? text(msg) : msg.cloneNode(true));
    if (opts.link) b.appendChild(opts.link(true));
    if (!$('bench').hidden && !opts.float) return;
    var t = el('div', 'toast' + (opts.err ? ' err' : ''), [el('div', 't-title', [opts.title || 'Test bench']), typeof msg === 'string' ? text(msg) : msg]);
    if (opts.link) t.appendChild(opts.link(false));
    var box = $('toasts');
    if (opts.replace && box.lastChild && box.lastChild.dataset.k === opts.replace) box.removeChild(box.lastChild);
    t.dataset.k = opts.replace || '';
    box.appendChild(t);
    while (box.children.length > 3) box.removeChild(box.firstChild);
    setTimeout(function () { if (t.parentNode) t.parentNode.removeChild(t); }, opts.ms || 7000);
  }
  var endKey = '', activeInj = [];
  function fillEnd(l) {
    activeInj = l;
    var key = JSON.stringify(l.map(function (i) { return i.id; }));
    $('end-grp').hidden = !l.length;
    if (key === endKey) return;
    endKey = key;
    var sel = $('end-select'), keep = sel.value;
    sel.textContent = '';
    l.forEach(function (i) {
      var o = document.createElement('option'); o.value = i.id; o.textContent = injLabel(i);
      sel.appendChild(o);
    });
    if (keep) sel.value = keep;
  }
  function injLabel(i) {
    var where = ['country', 'platform', 'category', 'source'].map(function (d) { return obj(i.scope)[d]; }).filter(Boolean).join(' ');
    return i.kind + ' ' + where + ' from ' + displayHour(obj(i.scope).start);
  }
  function endInjection(id) {
    toast('Ending...', { replace: 'end' });
    post('/api/end', { id: id }).then(function (r) { toast('Rolled back from ' + displayHour(r.ended_from) + '. The monitor resolves the incident once the data is healthy again.', { replace: 'end', ms: 9000 }); refresh(); })
      .catch(function (e) { toast('Failed: ' + e.message, { err: true, replace: 'end' }); });
  }
  $('end-go').onclick = function () { var id = parseInt($('end-select').value, 10); if (id) endInjection(id); };
  function act(body, label) {
    toast(label + '...', { replace: 'act' });
    post('/api/inject', body).then(function (r) { toast(label + ' from ' + displayHour(r.start) + '. Written into the data; the monitor has to find it before the graph changes.', { replace: 'act', ms: 9000 }); refresh(); })
      .catch(function (e) { toast('Failed: ' + e.message, { err: true, replace: 'act' }); });
  }
  function injectFault(kind, value) { var label = KINDS.filter(function (k) { return k[0] === kind; })[0][1]; act(faultBody(kind, value), label + ' injected'); }
  $('f-go').onclick = function () { injectFault($('f-kind').value, function (p) { return $('f-' + p).value; }); };
  $('pr-go').onclick = function () { act(promoBody($('pr-country').value), 'Promo started'); };
  $('ms-go').onclick = function () { act(mixShiftBody($('ms-category').value), 'Mix shift started'); };
  $('dd-go').onclick = function () { act(delayBody($('dd-source').value), 'Late data scheduled'); };
  function answerToast(res) {
    res = obj(res); var name = str(res.name);
    if (name) answers[name] = res;
    var link = name && list(res.checks).length ? function () { var b = el('button', 'linkish', [' Show checks']); b.style.marginLeft = '6px'; b.onclick = function () { showChecks(name); }; return b; } : null;
    toast(str(res.message) || ('Monitor: ' + str(res.status)), { link: link, title: 'The monitor answered: ' + name, replace: 'metric', ms: 15000 });
  }
  function handOver(yamlText) {
    post('/api/metric', { yaml_text: yamlText }).then(function (r) {
      toast('Handed to the monitor: ' + r.saved + '. Waiting for its answer...', { replace: 'metric', ms: 40000 });
      var tries = 0;
      var poll = function () {
        get('/api/metric/' + encodeURIComponent(r.name)).then(function (res) { answerToast(res); refresh(); })
          .catch(function () { if (++tries < 20) setTimeout(poll, 2000); else toast('Handed to the monitor; it has not answered yet.', { replace: 'metric' }); });
      };
      setTimeout(poll, 1500);
    }).catch(function (e) { toast('Failed: ' + e.message, { err: true }); });
  }
  $('nm-go').onclick = function () { handOver(REFUND_YAML); };
  $('nm-dup').onclick = function () { handOver(DUP_YAML); };

  function setBench(open) {
    var b = $('bench');
    b.hidden = !open; $('bench-open').setAttribute('aria-expanded', open ? 'true' : 'false');
    document.body.classList.toggle('bench-open', open);
    if (open) { document.documentElement.style.setProperty('--bench-h', (b.offsetHeight + 8) + 'px'); $('f-kind').focus(); }
    else if (document.activeElement && b.contains(document.activeElement)) $('bench-open').focus();
    setTimeout(fitGraph, 0);
  }
  $('bench-open').onclick = function () { setBench($('bench').hidden); };
  $('bench-close').onclick = function () { setBench(false); };

  // ------------------------------------------------------------- the command palette
  function commands() {
    var c = [];
    activeInj.forEach(function (i) { c.push({ g: 'Roll back', l: 'End it (rollback): ' + injLabel(i), run: function () { endInjection(i.id); } }); });
    KINDS.forEach(function (k) {
      var combos = [{}];
      k[2].forEach(function (p) { var n = []; combos.forEach(function (cb) { OPTS[p].forEach(function (v) { var x = JSON.parse(JSON.stringify(cb)); x[p] = v; n.push(x); }); }); combos = n; });
      combos.forEach(function (cb) {
        var where = k[2].map(function (p) { return cb[p]; }).join(' · ');
        c.push({ g: 'Inject', l: k[1] + (where ? ' · ' + where : ''), h: k[3] + ' h', k: k[0], run: function () { injectFault(k[0], function (p) { return cb[p]; }); } });
      });
    });
    PROMO.forEach(function (p) { c.push({ g: 'Innocent', l: 'Promo (12 h) in ' + p, run: function () { act(promoBody(p), 'Promo started'); } }); });
    MIX.forEach(function (m) { c.push({ g: 'Innocent', l: 'Mix shift (innocent) toward ' + m, run: function () { act(mixShiftBody(m), 'Mix shift started'); } }); });
    c.push({ g: 'Metric', l: 'Add refund_rate', h: 'posts the query only', run: function () { handOver(REFUND_YAML); } });
    c.push({ g: 'Metric', l: 'Add a duplicate metric (payment_rate)', h: 'should be refused', run: function () { handOver(DUP_YAML); } });
    SOURCES.forEach(function (s) { c.push({ g: 'Data', l: s + ' data arrives 2 h late', run: function () { act(delayBody(s), 'Late data scheduled'); } }); });
    ordered().forEach(function (i) {
      var d = obj(list(i.drivers)[0]);
      c.push({ g: 'Go to', l: i.id + '  ' + (d.metric || i.metric) + '  ' + segText(i.segment), h: status(i), run: function () { selectInc(i.id); } });
    });
    [{ id: 'all', label: 'All' }].concat(domains).forEach(function (d) { c.push({ g: 'Domain', l: 'Show ' + d.label, run: function () { setDomain(d.id); } }); });
    c.push({ g: 'View', l: $('bench').hidden ? 'Open the test bench' : 'Close the test bench', h: 'b', run: function () { setBench($('bench').hidden); } });
    c.push({ g: 'View', l: 'Focus the graph', h: 'g', run: focusGraph });
    c.push({ g: 'View', l: 'Switch to ' + (isDark() ? 'light' : 'dark') + ' theme', run: toggleTheme });
    c.push({ g: 'View', l: 'Open the data browser', run: function () { location.href = '/data'; } });
    return c;
  }
  var palCmds = [], palShown = [], palAt = 0, palReturn = null;
  function openPalette() {
    palReturn = document.activeElement;
    palCmds = commands(); $('palette').hidden = false; $('pal-input').value = ''; filterPalette(); $('pal-input').focus();
  }
  function closePalette() { $('palette').hidden = true; if (palReturn && palReturn.focus && document.contains(palReturn)) palReturn.focus(); }
  function filterPalette() {
    var q = $('pal-input').value.toLowerCase().trim().split(/\s+/).filter(Boolean);
    palShown = palCmds.filter(function (c) { var s = (c.g + ' ' + c.l + ' ' + (c.k || '') + ' ' + (c.h || '')).toLowerCase(); return q.every(function (w) { return s.indexOf(w) >= 0; }); });
    palAt = 0; drawPalette();
  }
  function drawPalette() {
    var ul = $('pal-list'); ul.textContent = '';
    if (!palShown.length) { ul.appendChild(el('li', 'none', ['No command matches. Try inject, roll back, promo, mix, refund, late, or an incident id.'])); return; }
    palShown.forEach(function (c, k) {
      var li = el('li', k === palAt ? 'act' : null, [el('span', 'grp', [c.g]), el('span', 'lbl', [c.l])].concat(c.h ? [el('span', 'hint', [c.h])] : []));
      li.setAttribute('role', 'option'); li.id = 'pal-' + k; li.setAttribute('aria-selected', k === palAt ? 'true' : 'false');
      li.onmousemove = function () { if (palAt !== k) { palAt = k; markPal(); } };
      li.onclick = function () { runPal(k); };
      ul.appendChild(li);
    });
    $('pal-input').setAttribute('aria-activedescendant', 'pal-' + palAt);
  }
  function markPal() {
    Array.prototype.forEach.call($('pal-list').children, function (li, k) { li.classList.toggle('act', k === palAt); li.setAttribute('aria-selected', k === palAt ? 'true' : 'false'); });
    var a = $('pal-list').children[palAt]; if (a && a.scrollIntoView) a.scrollIntoView({ block: 'nearest' });
    $('pal-input').setAttribute('aria-activedescendant', 'pal-' + palAt);
  }
  function runPal(k) { var c = palShown[k]; if (!c) return; $('palette').hidden = true; palReturn = null; c.run(); }
  $('pal-input').addEventListener('input', filterPalette);
  $('pal-input').addEventListener('keydown', function (e) {
    if (e.key === 'ArrowDown') { e.preventDefault(); palAt = Math.min(palShown.length - 1, palAt + 1); markPal(); }
    else if (e.key === 'ArrowUp') { e.preventDefault(); palAt = Math.max(0, palAt - 1); markPal(); }
    else if (e.key === 'Enter') { e.preventDefault(); runPal(palAt); }
    else if (e.key === 'Escape') { e.preventDefault(); closePalette(); }
  });
  $('palette').addEventListener('mousedown', function (e) { if (e.target === $('palette')) closePalette(); });
  $('cmd-open').onclick = openPalette;

  // ------------------------------------------------------------- keyboard, theme, url
  function focusGraph() {
    if (!drawn || !drawn.nodes.length) return;
    var n = drawn.nodes.filter(function (x) { return x.el.classList.contains('moved'); })[0] || drawn.nodes.filter(function (x) { return x.el.classList.contains('on'); })[0] || drawn.nodes[0];
    n.el.focus();
  }
  function moveInc(dir) {
    var l = ordered(); if (!l.length) return;
    var k = l.map(function (i) { return i.id; }).indexOf(selInc);
    var n = mode !== 'incident' || k < 0 ? (dir > 0 ? 0 : l.length - 1) : Math.max(0, Math.min(l.length - 1, k + dir));
    selectInc(l[n].id, { focus: true });
  }
  document.addEventListener('keydown', function (e) {
    var t = e.target, typing = t && (t.tagName === 'INPUT' || t.tagName === 'SELECT' || t.tagName === 'TEXTAREA' || t.isContentEditable);
    if ((e.ctrlKey || e.metaKey) && (e.key === 'k' || e.key === 'K')) { e.preventDefault(); if ($('palette').hidden) openPalette(); else closePalette(); return; }
    if (e.key === 'Escape') {
      if (!$('palette').hidden) { closePalette(); return; }
      if (!$('bench').hidden) { setBench(false); return; }
      if (mode === 'node' && byId(selInc)) { selectInc(selInc); return; }
      return;
    }
    if (typing || e.ctrlKey || e.metaKey || e.altKey || !$('palette').hidden) return;
    if (e.key === '/') { e.preventDefault(); openPalette(); }
    else if (e.key === 'j') { e.preventDefault(); moveInc(1); }
    else if (e.key === 'k') { e.preventDefault(); moveInc(-1); }
    else if (e.key === 'g') { e.preventDefault(); focusGraph(); }
    else if (e.key === 'b') { e.preventDefault(); setBench($('bench').hidden); }
  });
  $('back').onclick = function () { if (byId(selInc)) selectInc(selInc); };
  function isDark() { var t = document.documentElement.dataset.theme; return t ? t === 'dark' : !!(window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches); }
  function toggleTheme() { document.documentElement.dataset.theme = isDark() ? 'light' : 'dark'; syncUrl(true); }
  $('theme-btn').onclick = toggleTheme;
  function syncUrl(withTheme) {
    if (!window.history || !history.replaceState) return;
    var u = new URL(location.href);
    if (selInc && mode === 'incident') u.searchParams.set('incident', selInc);
    if (withTheme) u.searchParams.set('theme', document.documentElement.dataset.theme);
    history.replaceState(null, '', u.pathname + u.search);
  }

  if (window.ResizeObserver) new ResizeObserver(function () { fitGraph(); }).observe($('graphwrap'));
  window.addEventListener('resize', function () { fitGraph(); renderRibbon(); extraMore(); if (!$('bench').hidden) document.documentElement.style.setProperty('--bench-h', ($('bench').offsetHeight + 8) + 'px'); });

  // ?incident=inc-0001 opens that report at load; ?bench=1 opens the test bench; ?commands=<text> opens the palette
  var q = new URLSearchParams(location.search), asked = q.get('incident');
  if (asked) selInc = asked;
  if (q.get('metric')) { selNode = q.get('metric'); mode = 'node'; }
  if (q.get('bench') === '1') setTimeout(function () { setBench(true); }, 0);
  if (q.get('commands') != null) setTimeout(function () { openPalette(); $('pal-input').value = q.get('commands'); filterPalette(); }, 600);
  get('/api/domains').then(function (d) { domains = list(d); }).catch(function () {}).then(function () { refresh(); setInterval(refresh, 2000); });
})();
