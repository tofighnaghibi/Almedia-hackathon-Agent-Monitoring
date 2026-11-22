// v4 "Presenter cockpit". The reference console's logic (ui/static/index.html), rearranged around business-health
// tiles, a Now banner, the incidents beside their report, the graph folded below, a test-bench drawer and Presenter
// mode: a cue bar that walks the demo script and calls the API itself.
(function () {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';
  var $ = function (id) { return document.getElementById(id); };
  var graph = null, meta = {}, domains = [], domOf = {}, incidents = [], example = false, skipped = 0, graphKey = '';
  var dom = 'all', selInc = null, selNode = null, drawn = null, lastState = null, known = null;
  var LIVE = ['open', 'ongoing'], COUNTED = ['open', 'ongoing', 'resolved'], KNOWN = ['open', 'ongoing', 'resolved', 'explained', 'pending'];
  var SEV = { high: 3, medium: 2, low: 1 };
  var FUNNEL = ['traffic', 'search', 'recommendations', 'cart', 'payments', 'orders', 'revenue'], SUPPORT = ['ads', 'inventory'];

  // ---------------------------------------------------------------- helpers (as in the reference console)
  function str(v) { return v == null ? '' : (typeof v === 'object' ? JSON.stringify(v) : String(v)); }
  function list(v) { return Array.isArray(v) ? v : []; }
  function obj(v) { return v && typeof v === 'object' && !Array.isArray(v) ? v : {}; }
  function text(s) { return document.createTextNode(str(s)); }
  function el(tag, cls, kids) { var e = document.createElement(tag); if (cls) e.className = cls; (kids || []).forEach(function (k) { if (k == null) return; e.appendChild(k && k.nodeType ? k : text(k)); }); return e; }
  function get(url) { return fetch(url, { cache: 'no-store' }).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); }); }
  function post(url, body) {
    return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(typeof j.detail === 'string' ? j.detail : r.status); return j; }); });
  }
  function num(n) { return typeof n === 'number' && isFinite(n) ? n : null; }
  function signed(s) { return s.charAt(0) === '-' ? '\u2212' + s.slice(1) : s; }
  function fmt(n, d) { n = num(Number(n)); return n == null ? '-' : signed(n.toLocaleString('en', { maximumFractionDigits: d == null ? 1 : d })); }
  function short(n) {
    n = num(n); if (n == null) return '\u2013';
    var a = Math.abs(n);
    return signed(a >= 100 ? n.toLocaleString('en', { maximumFractionDigits: 0 }) : a >= 1 ? n.toLocaleString('en', { maximumFractionDigits: 2 })
      : n.toLocaleString('en', { maximumSignificantDigits: 3 }));
  }
  function pct(n) { n = num(n); return n == null ? '\u2013' : (n > 0 ? '+' : '') + fmt(n) + '%'; }
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
  // a tile's euro figure: compact (4.2k), with a true minus sign
  function eurCompact(v) {
    var a = Math.abs(v), s = a >= 1e6 ? (a / 1e6).toFixed(1) + 'M' : a >= 1e4 ? Math.round(a / 1e3) + 'k' : a >= 1e3 ? (a / 1e3).toFixed(1) + 'k' : Math.round(a).toString();
    return (v < 0 ? '\u2212' : v > 0 ? '+' : '') + s + '\u00a0\u20ac';
  }
  function segText(s) { var o = obj(s), k = Object.keys(o); return k.length ? k.map(function (x) { return str(o[x]); }).join(' \u00d7 ') : 'all'; }
  function placeText(s) { var o = obj(s), k = Object.keys(o); return k.length ? 'in ' + k.map(function (x) { return str(o[x]); }).join(', ') : 'across the whole shop'; }
  function when(t) { return str(t).replace('T', ' ').replace(':00Z', '').replace(/-/g, '\u2011'); }
  function hourOf(t) { var m = /T(\d\d:\d\d)/.exec(str(t)); return m ? m[1] : when(t); }
  function svg(tag, attrs, parent) { var e = document.createElementNS(NS, tag); for (var k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; }
  function still() { return window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches; }
  // presenter progress lives in this tab only (sessionStorage): a reload mid-demo keeps the step, but a rehearsal in
  // another window or yesterday never leaves the cue at step 8 with stale "done" marks
  var store = {
    get: function (k) { try { return window.sessionStorage.getItem('v4.' + k); } catch (e) { return null; } },
    set: function (k, v) { try { window.sessionStorage.setItem('v4.' + k, v); } catch (e) { /* private window: fine */ } }
  };
  try { window.localStorage.removeItem('v4.cue'); window.localStorage.removeItem('v4.present'); } catch (e) { /* fine */ }
  var toastTimer = null;
  function toast(msg, extra) {
    var t = $('toast'); t.textContent = ''; t.appendChild(text(msg)); if (extra) t.appendChild(extra);
    t.hidden = false; clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.hidden = true; }, 6000);
  }

  // ---------------------------------------------------------------- domains and status
  function domainOf(metric) { return domOf[metric] || 'other'; }
  function domainLabel(id) { if (id === 'all') return 'the whole business'; var d = domains.filter(function (x) { return x.id === id; })[0]; return d ? d.label : 'Other'; }
  function incidentNodes(i) {
    var s = {}; s[str(i.metric)] = true;
    list(i.contributions).forEach(function (c) { if (c && c.node) s[str(c.node)] = true; });
    list(i.trail).forEach(function (t) { t = obj(t); if (t.action === 'watch' && t.target && meta[t.target]) s[str(t.target)] = true; });
    list(i.drivers).forEach(function (d) { d = obj(d); if (d.metric && meta[d.metric]) s[str(d.metric)] = true; });
    return Object.keys(s);
  }
  function status(i) { return str(i.status) || 'open'; }
  function isLive(i) { return LIVE.indexOf(status(i)) >= 0; }
  function stOf(i) { var s = status(i); return 'st-' + (KNOWN.indexOf(s) >= 0 ? s : 'other'); }
  function touches(i, d) { return d === 'all' || incidentNodes(i).some(function (m) { return domainOf(m) === d; }); }
  function inScope() { return incidents.filter(function (i) { return touches(i, dom); }); }
  function sevRank(i) { return SEV[str(i.severity)] || 0; }
  function liveOrder(a, b) {  // open before ongoing, then severity, then the euro loss
    return (status(a) === 'open' ? 0 : 1) - (status(b) === 'open' ? 0 : 1) || sevRank(b) - sevRank(a)
      || Math.abs(euroOf(b) || 0) - Math.abs(euroOf(a) || 0);
  }
  function worst(l) { return l.filter(isLive).slice().sort(liveOrder)[0]; }   // the most severe live incident
  function hasOther() { return !!graph && list(graph.nodes).some(function (n) { return n.kind === 'metric' && !domOf[n.id]; }); }
  // the series the monitor reads in a domain: each metric's total and each of its breakdowns
  function seriesCount(d) {
    if (!graph) return 0;
    return list(graph.nodes).filter(function (n) {
      var m = n.kind === 'metric' ? n.id : n.kind === 'breakdown' ? str(n.id).split('@')[0] : null;
      return m && meta[m] && (d === 'all' || domainOf(m) === d);
    }).length;
  }
  // incident: a live (open or ongoing) incident touches the domain. Watching: none is live, but in the last 24
  // simulated hours something was resolved, explained or is waiting for data here. Calm: nothing at all.
  function recent(i) {
    var now = Date.parse(str(lastState && lastState.last_hour)), seen = Date.parse(str(i.last_seen || i.first_seen));
    return !isFinite(now) || !isFinite(seen) || now - seen <= 24 * HOUR_MS;
  }
  function healthOf(d) {
    var live = incidents.filter(function (i) { return isLive(i) && touches(i, d); });
    var st = live.length ? 'incident' : incidents.some(function (i) { return touches(i, d) && recent(i); }) ? 'watching' : 'calm';
    var eur = live.map(euroOf).filter(function (v) { return v != null; });
    return { st: st, n: live.length, eur: eur.length ? eur.reduce(function (a, b) { return a + b; }, 0) : null };
  }

  // ---------------------------------------------------------------- the masthead
  function renderCounts() {
    var c = $('counts'); c.textContent = '';
    ['open', 'ongoing', 'resolved', 'explained'].forEach(function (s) {
      var k = incidents.filter(function (i) { return status(i) === s; }).length;
      c.appendChild(el('span', 'count st-' + s + (k ? '' : ' zero'), [el('b', null, [String(k)]), s]));
    });
  }

  var spotSels = [];
  function applySpot(entrance) {
    Array.prototype.slice.call(document.querySelectorAll('.spot')).forEach(function (x) { x.classList.remove('spot', 'spot-kept'); });
    if (!P.on) return;
    spotSels.forEach(function (s) { var e = document.querySelector(s); if (e) { e.classList.add('spot'); if (!entrance) e.classList.add('spot-kept'); } });
  }

  // ---------------------------------------------------------------- business health tiles
  var pulsed = {};
  function setDom(id) {
    dom = id; selNode = null;
    var s = inScope(), w = worst(s);
    selInc = w ? w.id : s.length ? s[0].id : null;
    renderAll();
  }
  function tile(id, label, cls) {
    var h = healthOf(id), on = dom === id;
    var word = { incident: 'Incident', watching: 'Watching', calm: 'Calm' }[h.st];
    var b = el('button', 'tile ' + h.st + (on ? ' on' : '') + (cls ? ' ' + cls : ''), [
      el('span', 'tile-name', [label]),
      el('span', 'tile-state', [word]),
      el('span', 'tile-figs', [el('span', 'tile-n', [String(h.n)]), el('span', 'tile-eur', [h.eur != null ? eurCompact(h.eur) : 'live'])])]);
    b.type = 'button'; b.dataset.dom = id;
    b.setAttribute('aria-pressed', on ? 'true' : 'false');
    b.title = label + ': ' + word.toLowerCase() + ', ' + h.n + ' live incident' + (h.n === 1 ? '' : 's') + (h.eur != null ? ', ' + eurText(h.eur) : '')
      + (on ? '. Click again to see the whole business.' : '. Click to scope the page to it.');
    b.onclick = function () { setDom(on && id !== 'all' ? 'all' : id); };
    return b;
  }
  function renderTiles() {
    var t = $('tiles'); t.textContent = '';
    t.appendChild(tile('all', 'Whole business', 'whole'));
    t.appendChild(el('span', 'sep'));
    var by = {}; domains.forEach(function (d) { by[d.id] = d; });
    var funnel = FUNNEL.filter(function (id) { return by[id]; });
    funnel.forEach(function (id, k) {
      var b = tile(id, by[id].label, 'funnel' + (k === funnel.length - 1 ? ' last' : ''));
      if (k < funnel.length - 1) { var f = svg('svg', { 'class': 'flow', viewBox: '0 0 10 10', 'aria-hidden': 'true' }); svg('path', { d: 'M3 1l4 4-4 4', fill: 'none', stroke: 'currentColor', 'stroke-width': '1.6' }, f); b.appendChild(f); }
      t.appendChild(b);
    });
    var rest = SUPPORT.filter(function (id) { return by[id]; }).concat(domains.map(function (d) { return d.id; }).filter(function (id) { return FUNNEL.indexOf(id) < 0 && SUPPORT.indexOf(id) < 0; }));
    if (rest.length || hasOther()) t.appendChild(el('span', 'sep'));
    rest.forEach(function (id) { t.appendChild(tile(id, by[id].label)); });
    if (hasOther()) t.appendChild(tile('other', 'Other'));
    Object.keys(pulsed).forEach(function (id) { var x = t.querySelector('[data-dom="' + id + '"]'); if (x && !still()) x.classList.add('pulse'); });
    pulsed = {};
    applySpot(false);
  }

  // ---------------------------------------------------------------- the Now banner
  function causeOf(i) {
    var hs = list(i.hypotheses).map(obj);
    var best = function (st) { return hs.filter(function (h) { return h.status === st; }).sort(function (a, b) { return (num(b.score) || 0) - (num(a.score) || 0); })[0]; };
    var h = best('supported'); if (h) return { word: 'Likely cause', text: str(h.cause) };
    h = best('plausible'); if (h) return { word: 'Possible cause', text: str(h.cause) };
    return null;
  }
  function movedLine(i) {
    var ch = obj(i.change), d = obj(list(i.drivers)[0]);
    var moved = d.metric ? str(d.metric) : str(i.metric), dp = num(d.metric ? d.delta_pct : ch.delta_pct);
    return { moved: moved, dp: dp, verb: dp == null ? 'moved' : dp < 0 ? 'fell ' + fmt(Math.abs(dp), 0) + '%' : 'rose ' + fmt(dp, 0) + '%' };
  }
  function renderNow() {
    var box = $('now'), body = $('now-body'), act = $('now-act');
    body.textContent = ''; act.textContent = '';
    var scope = inScope(), w = worst(scope), where = dom === 'all' ? '' : ' in ' + domainLabel(dom);
    if (example) {
      box.className = 'now example';
      body.appendChild(el('p', 'now-line', ['Showing the contract example: no live incident yet.']));
      body.appendChild(el('p', 'now-sub', ['The monitor has not written its incidents folder. The report below is the example from the contract.']));
      return;
    }
    if (!w) {
      box.className = 'now calm';
      var n = seriesCount(dom);
      body.appendChild(el('p', 'now-line', ['All quiet' + where + ': the monitor is watching ' + n + ' series']));
      var res = scope.filter(function (i) { return status(i) === 'resolved'; }).length, exp = scope.filter(function (i) { return status(i) === 'explained'; }).length;
      body.appendChild(el('p', 'now-sub', [graph ? (list(graph.nodes).filter(function (x) { return x.kind === 'metric' && (dom === 'all' || domainOf(x.id) === dom); }).length + ' metrics, each as a total and broken down by place. ') : '',
        res || exp ? res + ' resolved and ' + exp + ' explained so far.' : 'Nothing has moved beyond its expected range.']));
      return;
    }
    var st = status(w), m = movedLine(w), eur = euroOf(w), cause = causeOf(w), ch = obj(w.change);
    box.className = 'now incident';
    var line = [el('span', 'mono', [m.moved]), ' ' + m.verb + ' ' + placeText(w.segment)];
    if (m.moved !== str(w.metric) && num(ch.delta_pct) != null) line.push(', ', el('span', 'mono', [str(w.metric)]), ' ' + pct(ch.delta_pct));
    if (eur != null) line.push(', ', el('span', 'cost', [eur < 0 ? 'costing ' + short(Math.abs(eur)) + ' EUR so far' : 'moving ' + eurText(eur)]));
    line.push('.');
    body.appendChild(el('p', 'now-line', line));
    var live = scope.filter(isLive).length;
    body.appendChild(el('p', 'now-sub', cause ? [el('b', null, [cause.word + ': ']), cause.text + '.', live > 1 ? '  ' + (live - 1) + ' more live.' : '']
      : ['Cause not found yet: the investigation is still running.' + (live > 1 ? '  ' + (live - 1) + ' more live.' : '')]));
    if (w.id !== selInc) {
      var b = el('button', 'primary', ['Open its report']); b.type = 'button';
      b.onclick = function () { pickInc(w.id); };
      act.appendChild(b);
    } else act.appendChild(el('span', 'chip ' + stOf(w), [st]));
  }

  // ---------------------------------------------------------------- incidents
  function sevGlyph(s) { var g = el('span', 'sevg ' + str(s), [el('i'), el('i'), el('i')]); g.setAttribute('aria-label', str(s) ? str(s) + ' severity' : 'no severity'); g.title = g.getAttribute('aria-label'); return g; }
  var GROUPS = [['live', 'Live', function (s) { return LIVE.indexOf(s) >= 0; }], ['resolved', 'Resolved', function (s) { return s === 'resolved'; }],
    ['explained', 'Innocent: explained, no alert', function (s) { return s === 'explained'; }], ['pending', 'Waiting for data', function (s) { return s === 'pending'; }],
    ['other', 'Other', function (s) { return KNOWN.indexOf(s) < 0; }]];
  var fresh = {};
  function pickInc(id) {
    selInc = id; selNode = null;
    renderFeed(); renderReport(true); renderNow(); applyStyles();
    var r = $('feed').querySelector('.inc.sel'); if (r) r.scrollIntoView({ block: 'nearest' });
  }
  function renderFeed() {
    $('feed-title').textContent = dom === 'all' ? 'Incidents' : 'Incidents in ' + domainLabel(dom);
    var f = $('feed'), top = f.scrollTop; f.textContent = '';
    var l = inScope();
    $('feed-note').textContent = l.length ? l.filter(isLive).length + ' live of ' + l.length : '';
    if (!l.length) f.appendChild(el('p', 'empty', [dom === 'all' ? 'No incidents. The monitor is watching.' : 'No incident touches ' + domainLabel(dom) + '.']));
    GROUPS.forEach(function (g) {
      var rows = l.filter(function (i) { return g[2](status(i)); });
      if (!rows.length) return;
      if (g[0] === 'live') rows = rows.slice().sort(liveOrder);       // the worst live incident first, as in the Now banner
      f.appendChild(el('div', 'group-h ' + g[0], [el('b', null, [String(rows.length)]), g[1]]));
      rows.forEach(function (i) {
        try { f.appendChild(incRow(i)); }
        catch (err) { f.appendChild(el('p', 'empty', ['An incident (' + str(i.id) + ') could not be shown: ' + err.message])); }
      });
    });
    if (skipped) f.appendChild(el('p', 'empty', [skipped + ' file' + (skipped > 1 ? 's' : '') + ' in the incidents folder ignored (not an incident, or still being written).']));
    // a new live incident is at the top of the list: bring it into view instead of keeping an old scroll position
    var arrived = l.some(function (i) { return fresh[i.id] && isLive(i); });
    f.scrollTop = arrived ? 0 : top;
    applySpot(false);
  }
  function incRow(i) {
    var ch = obj(i.change), st = status(i), quiet = st === 'explained' || st === 'pending', m = movedLine(i), eur = euroOf(i);
    var line = [el('span', 'inc-metric', [m.moved])];
    var figs = [];
    if (m.dp != null) figs.push(el('span', 'chg ' + toneIn(i, m.moved, m.dp), [pct(m.dp)]));
    if (eur != null) figs.push(el('span', 'chg eur ' + euroTone(i, eur), [eurText(eur)]));
    var sub = [segText(i.segment)];
    if (m.moved !== str(i.metric)) sub.push(' \u00b7 ', el('span', 'mono', [str(i.metric) + (num(ch.delta_pct) != null ? ' ' + pct(ch.delta_pct) : '')]));
    sub.push(' \u00b7 ', el('span', 'mono', [hourOf(i.first_seen)]), ' \u00b7 ', el('span', 'mono', [str(i.id)]));
    var flags = [];
    if (i.recurrence_of) flags.push(el('span', 'flag again', ['Back again, see ' + str(i.recurrence_of)]));
    if (st === 'explained' && obj(i.innocent).kind) flags.push(el('span', 'flag innocent', ['Innocent: ' + str(obj(i.innocent).kind).replace(/_/g, ' ')]));
    var row = el('div', 'inc ' + stOf(i) + (quiet ? ' quiet' : '') + (i.id === selInc && !selNode ? ' sel' : '') + (fresh[i.id] && !still() ? ' arrive' : ''), [
      quiet ? el('span', 'inc-sev') : sevGlyph(i.severity),
      el('span', 'inc-line', line),
      el('span', 'chip ' + stOf(i), [st])].concat(figs.length ? [el('span', 'inc-figs', figs)] : [],
      [el('span', 'inc-sub', sub)], flags.length ? [el('span', 'inc-flags', flags)] : []));
    row.tabIndex = 0; row.dataset.id = str(i.id); row.title = 'First seen ' + when(i.first_seen) + ' UTC';
    row.setAttribute('role', 'button');
    row.addEventListener('click', function () { pickInc(i.id); });
    row.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pickInc(i.id); } });
    return row;
  }

  // ---------------------------------------------------------------- the report
  function section(title, kids, cls, aside) {
    var h = el('h3', null, [title].concat(aside ? [el('span', 'aside', [aside])] : []));
    var d = el('section', 'sec' + (cls ? ' ' + cls : ''), [h]); kids.forEach(function (k) { d.appendChild(k); }); return d;
  }
  function kv(pairs, cls) { var k = el('div', 'kv' + (cls ? ' ' + cls : '')); pairs.forEach(function (p) { k.appendChild(el('span', null, [p[0]])); k.appendChild(el('span', null, [p[1]])); }); return k; }
  function mono(s) { return el('span', 'mono', [str(s)]); }
  function fig(label, value, cls, unit) { return el('div', 'fig ' + (cls || ''), [el('span', 'lbl', [label]), el('span', 'big', [value].concat(unit ? [el('small', null, [' ' + unit])] : []))]); }
  var reportKey = '';
  function renderReport(force) {
    var i = incidents.filter(function (x) { return x.id === selInc; })[0];
    var key = i ? JSON.stringify(i) : 'none:' + dom;
    if (!force && key === reportKey) return;
    var sameInc = i && reportKey && reportKey.indexOf('"id":' + JSON.stringify(i.id)) >= 0;
    reportKey = key;
    var head = $('report-head'), body = $('report-body'), sc = $('report-scroll'), top = sc.scrollTop;
    head.textContent = ''; body.textContent = ''; body.className = 'report';
    try {
      if (!i) {
        head.appendChild(el('h2', null, ['Report']));
        body.appendChild(el('p', 'empty', [inScope().length ? 'Select an incident to read its report.' : 'No incident to report' + (dom === 'all' ? '' : ' in ' + domainLabel(dom)) + '. When the monitor finds one, its report opens here.']));
        return;
      }
      buildReport(head, body, i);
    } catch (err) { body.textContent = ''; body.appendChild(el('p', 'empty', ['This report could not be shown: ' + err.message])); }
    sc.scrollTop = sameInc && !force ? top : 0;
    var pinned = reportPin && reportPin.id === selInc && P.on && document.querySelector(reportPin.sel);
    if (pinned) sc.scrollTop = pinY(sc, pinned);
    applySpot(false);
  }
  var reportPin = null;     // presenter step 3: the report section kept in view while the live report is rebuilt
  function buildReport(head, d, i) {
    var st = status(i), ch = obj(i.change), w = obj(ch.window), imp = obj(i.impact), effect = euroOf(i), quiet = st === 'explained' || st === 'pending';
    var title = el('h2', null, [mono(i.metric), ' ' + (num(ch.delta_pct) != null ? pct(ch.delta_pct) + ' ' : '') + placeText(i.segment)]);
    title.id = 'report-title';
    var meta2 = el('div', 'report-meta', [el('span', 'mono', [str(i.id)]), el('span', null, ['First seen ' + when(i.first_seen) + ' UTC']),
      i.last_seen ? el('span', null, ['last seen ' + when(i.last_seen) + ' UTC']) : null]);
    var stBox = el('div', 'report-status', (!quiet && i.severity ? [sevGlyph(i.severity), el('span', 'sev-word', [str(i.severity) + ' severity'])] : []).concat([el('span', 'chip ' + stOf(i), [st])]));
    stBox.id = 'report-status';
    head.appendChild(el('div', null, [title, meta2])); head.appendChild(stBox);
    d.className = 'report ' + stOf(i);
    if (obj(i.innocent).reason) d.appendChild(section('Why no alert', [el('p', 'summary', [str(obj(i.innocent).reason)])], 'why'));
    if (i.recurrence_of) {
      var back = el('button', 'linkish', ['Open ' + str(i.recurrence_of)]); back.type = 'button';
      back.onclick = function () { if (!incidents.some(function (x) { return x.id === str(i.recurrence_of); })) { toast(str(i.recurrence_of) + ' is not in the feed.'); return; } dom = 'all'; renderTiles(); pickInc(str(i.recurrence_of)); };
      d.appendChild(el('div', 'back-again', [el('span', 'flag again', ['Back again']), 'Same metric and place as ' + str(i.recurrence_of) + ', which was resolved.', back]));
    }
    d.appendChild([ch.observed, ch.expected, ch.delta_pct, effect].some(function (v) { return num(v) != null; }) ? el('div', 'readout', [
      fig('Observed', short(ch.observed)), fig('Expected', short(ch.expected)),
      fig('Change', pct(ch.delta_pct), 'chg ' + toneIn(i, i.metric, ch.delta_pct)),
      fig('Effect', effect == null ? '\u2013' : (effect > 0 ? '+' : '') + short(effect), 'effect chg ' + euroTone(i, effect), effect == null ? null : 'EUR')])
      : el('p', 'muted', [st === 'pending' ? 'No figures yet: the data for these hours has not arrived.' : 'No figures in this report.']));
    var story = obj(i.narrative), told = story.source === 'model' && !!str(story.text).trim();
    if (told) {
      var brief = [];
      var asOf = Date.parse(str(story.as_of)), seen = Date.parse(str(i.last_seen));
      if (isFinite(asOf) && isFinite(seen) && asOf < seen)
        brief.push(el('p', 'brief-stale', ['written at ' + ('0' + new Date(asOf).getUTCHours()).slice(-2) + ':00 UTC; the figures above are newer']));
      brief.push(el('p', 'brief', [str(story.text)]),
        el('p', 'brief-note', ['Written by ' + (str(story.model) || 'a language model') + ' from this report' + (story.as_of ? ' at ' + when(story.as_of) + ' UTC' : '')
          + '; every number in it was checked against the evidence.']));
      d.appendChild(section('In brief', brief, 'brief-box'));
    } else if (i.report) d.appendChild(section('Summary', [el('p', 'summary', [str(i.report)])]));
    var rows = [['Metric', mono(i.metric)], ['Segment', segText(i.segment)],
      ['Change', num(ch.delta_pct) != null ? pct(ch.delta_pct) + (num(ch.z) != null ? ' (z ' + fmt(ch.z) + ')' : '') : '-']];
    if (num(ch.delta) != null) rows.push(['Difference', fmt(ch.delta)]);
    if (w.start || w.end) rows.push(['Window', when(w.start) + ' to ' + when(w.end) + ' UTC']);
    var ik = Object.keys(imp).map(function (k) { var v = Number(imp[k]); return [k, fmt(v, Number.isInteger(v) ? 0 : 2)]; });
    var dec = function (s) { var p = s.indexOf('.'); return p < 0 ? 0 : s.length - p; }, D = Math.max.apply(null, ik.map(function (p) { return dec(p[1]); }).concat(0));
    var facts = [section('What changed', [kv(rows)])];
    if (ik.length) facts.push(section('Business impact', [kv(ik.map(function (p) { return [mono(p[0]), p[1] + ' '.repeat(D - dec(p[1]))]; }), 'nums')]));
    d.appendChild(el('div', 'facts', facts));
    // the chain: the outcome, then each box the change runs through, with its share of the change
    var cs = list(i.contributions).map(obj);
    if (cs.length) {
      var lead = cs.reduce(function (a, b) { return (num(b.share_of_delta) || 0) > (num(a.share_of_delta) || 0) ? b : a; }, cs[0]);
      var links = [el('div', 'link top', [el('span', 'node-dot'), el('span', 'nm', [metricBtn(i.metric)]), el('span', 'share', [pct(ch.delta_pct)])])];
      cs.forEach(function (c) {
        var share = (num(c.share_of_delta) || 0) * 100, bar = el('div', 'bar'), b = el('i'); b.style.width = Math.max(0, Math.min(100, share)) + '%'; bar.appendChild(b);
        links.push(el('div', 'link' + (c === lead ? ' lead' : ''), [el('span', 'node-dot'), el('span', 'nm', [metricBtn(c.node)]),
          el('span', 'share', [fmt(share, 0) + '% of the change']), bar, el('span', 'vs', [short(c.observed) + ' against ' + short(c.expected) + ' expected' + (c.edge ? ' \u00b7 ' + str(c.edge) : '')])]));
      });
      var chain = section('Where along the graph', [el('div', 'chain', links)], 'chain-sec', 'the chain check: which box carries the change');
      chain.id = 'sec-chain';
      var see = el('button', 'linkish', ['See it on the graph']); see.type = 'button'; see.onclick = function () { openGraph(true); };
      chain.appendChild(see);
      d.appendChild(chain);
    }
    var loc = list(i.localization).map(function (x) {
      x = obj(x);
      return el('div', 'loc' + (x.chosen ? ' chosen' : ''), [
        el('span', 'seg', [str(x.dimension) + ' = ' + str(x.segment)].concat(x.chosen ? [el('span', 'chosen-tag', ['chosen'])] : [])),
        el('span', null, [fmt((num(x.share_of_delta) || 0) * 100, 0) + '%']),
        el('span', 'muted', [num(x.surprise) != null ? fmt(x.surprise, 2) : ''])]);
    });
    if (loc.length) d.appendChild(section('Where in the segments', [el('div', 'loc head', [el('span', null, ['Place']), el('span', null, ['Of change']), el('span', null, ['Surprise'])])].concat(loc)));
    var hs = list(i.hypotheses).map(obj), firstSupported = null;
    var hrows = hs.map(function (h) {
      var sc = num(h.score), bar = el('span', 'score'), bi = el('i'); bi.style.width = Math.max(0, Math.min(1, sc || 0)) * 100 + '%'; bar.appendChild(bi);
      var r = el('div', 'hyp ' + str(h.status), [el('span', 'verdict ' + str(h.status), [str(h.status) || '-']),
        el('div', null, [el('div', 'hyp-cause', [str(h.cause)]),
          el('div', 'hyp-meta', (sc != null ? [bar] : []).concat([[sc != null ? 'score ' + fmt(sc, 2) : '', str(h.kind)].filter(Boolean).join(' \u00b7 ')])),
          el('ul', 'evidence', list(h.evidence).map(function (e) { return el('li', null, [str(e)]); }))])]);
      if (h.status === 'supported' && !firstSupported) { firstSupported = r; r.id = 'sec-cause'; }
      return r;
    });
    if (hrows.length) d.appendChild(section('Hypotheses', hrows, null, hs.filter(function (h) { return h.status === 'supported'; }).length + ' supported, '
      + hs.filter(function (h) { return h.status === 'plausible'; }).length + ' plausible, ' + hs.filter(function (h) { return h.status === 'rejected'; }).length + ' rejected'));
    var tr = list(i.trail).map(function (s) {
      s = obj(s);
      return el('li', null, [el('div', 'step-head', [el('span', 'verb', [str(s.action).replace(/_/g, ' ')]), el('span', 'step-target', [str(s.target)])]), el('div', 'step-result', [str(s.result)])]);
    });
    if (tr.length) d.appendChild(section('Investigation trail', [el('ol', 'trail', tr)], null, tr.length + ' steps, in the order the monitor took them'));
    if (list(i.uncertain).length) d.appendChild(section('Still uncertain', [el('ul', 'uncertain', i.uncertain.map(function (u) { return el('li', null, [str(u)]); }))]));
    if (told && i.report) d.appendChild(section('Summary', [el('p', 'summary', [str(i.report)])]));
  }
  function metricBtn(m) {
    var b = el('button', null, [str(m)]); b.type = 'button'; b.title = 'Show ' + str(m) + ' on the graph';
    b.onclick = function () { selNode = str(m); openGraph(true); renderMetric(); applyStyles(); };
    return b;
  }

  // ---------------------------------------------------------------- the graph (the reference console's drawing)
  function filterTree(n) {
    if (dom === 'all') return n;
    var kids = list(n.children).map(filterTree).filter(Boolean);
    if (domainOf(n.metric) === dom || kids.length) return { metric: n.metric, role: n.role, repeated: n.repeated, children: kids };
    return null;
  }
  var CHAR = 7.2, NODE_H = 22, DY = 30, GAP = 24;
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
  var graphDirty = true;
  function graphOpen() { return $('howto').open; }
  function drawGraph() {
    if (!graphOpen()) { graphDirty = true; return; }
    graphDirty = false;
    $('graph-title').textContent = 'Metric graph: ' + (dom === 'all' ? 'all domains' : domainLabel(dom));
    var wrap = $('graphwrap');
    Array.prototype.slice.call(wrap.querySelectorAll('.empty')).forEach(function (e) { wrap.removeChild(e); });
    var g = $('g'); g.textContent = '';
    drawn = { edges: [], nodes: [] }; traceKey = '';
    var roots = list(graph && graph.trees).map(filterTree).filter(Boolean);
    if (!roots.length) { wrap.appendChild(el('p', 'empty', ['No formula tree for this domain.'])); renderExtra(null); return; }
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
      svg('rect', { width: 2 * i.hw, height: NODE_H, rx: 6 }, gr);
      var t = svg('text', { x: i.hw, y: NODE_H / 2, dy: '0.35em' }, gr); t.textContent = str(i.n.metric);
      var pick = function () { selNode = i.n.metric; renderMetric(); applyStyles(); };
      gr.addEventListener('click', pick);
      gr.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } });
      drawn.nodes.push({ el: gr, metric: i.n.metric, item: i });
    });
    list(graph && graph.edges).filter(function (e) { return e.kind === 'may_affect' && pos[e.source] && pos[e.target]; }).forEach(function (e) {
      var a = pos[e.source], b = pos[e.target], mx = (a.x + b.x) / 2 + 40, my = (a.y + b.y) / 2;
      svg('path', { d: 'M' + (a.x + a.hw) + ',' + a.y + ' Q' + (mx + 40) + ',' + my + ' ' + (b.x + b.hw) + ',' + b.y, 'class': 'edge may' }, ge);
      var sg = svg('g', { 'class': 'sign', transform: 'translate(' + (mx + (a.hw + b.hw) / 4) + ',' + my + ')' }, ge);
      svg('circle', { r: 9 }, sg); var s = svg('text', { dy: '0.35em' }, sg); s.textContent = e.sign > 0 ? '+' : e.sign < 0 ? '\u2212' : '?';
    });
    renderExtra(shown);
    fitGraph();
    applyStyles();
  }
  function renderExtra(shown) {
    var x = $('extra'); x.textContent = '';
    if (!graph) return;
    var rest = (domains.filter(function (d) { return d.id === dom; })[0] || { metrics: [] }).metrics;
    if (dom === 'all') rest = list(graph.nodes).filter(function (n) { return n.kind === 'metric'; }).map(function (n) { return n.id; });
    if (dom === 'other') rest = list(graph.nodes).filter(function (n) { return n.kind === 'metric' && !domOf[n.id]; }).map(function (n) { return n.id; });
    rest = rest.filter(function (m) { return !(shown && shown[m]) && meta[m]; });
    if (!rest.length) return;
    x.appendChild(text(dom === 'all' ? 'Not on the formula tree:' : 'Also in this domain, not on the formula tree:'));
    rest.forEach(function (m) { var b = el('button', null, [m]); b.type = 'button'; b.onclick = function () { selNode = m; renderMetric(); applyStyles(); }; x.appendChild(b); });
  }
  function selected() { return incidents.filter(function (i) { return i.id === selInc; })[0]; }
  function applyStyles() {
    if (!drawn || !drawn.layer || !graphOpen()) return;
    var h = health(), inc = selected(), on = {};
    if (inc) {
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
      c.toggle('picked', selNode === n.metric);
      c.toggle('context', dom !== 'all' && domainOf(n.metric) !== dom);
    });
    drawn.edges.forEach(function (e) { e.el.classList.toggle('on', !!(on[e.from] && on[e.to])); });
    markMoved(lit ? inc : null);
    trace(lit ? inc : null);
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
    var i = m.node.item, label = short(m.observed) + ' vs ' + short(m.expected), w = Math.ceil(label.length * 6.6) + 10, H = 14;
    var vb = drawn.box, top = i.y - NODE_H / 2, spots = [[i.x + i.hw - w, top - H - 3], [i.x - i.hw - GAP - w, i.y - H / 2], [i.x + i.hw - w, top + NODE_H + 3]];
    var clear = function (s) {
      return s[0] >= 0 && s[1] >= 0 && (!vb || s[1] + H <= vb.h) && !drawn.nodes.some(function (n) {
        var o = n.item; return o !== i && o.x + o.hw > s[0] - 2 && o.x - o.hw < s[0] + w + 2 && o.y + NODE_H / 2 > s[1] - 2 && o.y - NODE_H / 2 < s[1] + H + 2;
      });
    };
    var at = spots.filter(clear)[0] || [spots[0][0], top - H + 1];
    var tag = svg('g', { 'class': 'moved-tag', transform: 'translate(' + at[0] + ',' + at[1] + ')' }, layer);
    svg('rect', { width: w, height: H, rx: 3 }, tag);
    var t = svg('text', { x: 5, y: 10.5 }, tag); t.textContent = label;
    svg('title', {}, tag).textContent = 'observed ' + short(m.observed) + ', expected ' + short(m.expected);
  }
  // the graph is as wide as its area allows, at most 1.3 times its own size, never below a readable 0.74
  function fitGraph() {
    var g = $('g'), wrap = $('graphwrap'), v = g.viewBox && g.viewBox.baseVal;
    if (!v || !v.width || !v.height || !drawn) return;
    drawn.box = { w: v.width, h: v.height };
    var cs = getComputedStyle(wrap), w = wrap.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    g.style.width = Math.floor(Math.max(v.width * 0.74, Math.min(w, v.width * 1.3))) + 'px';
  }
  // the trace: the selected incident's path draws itself once, from the moved rate up to the outcome
  var traceKey = '', tracing = null;
  function trace(inc) {
    var key = inc ? str(inc.id) + '|' + dom : '';
    if (key === traceKey) return;
    traceKey = key;
    var g = $('g'), layer = drawn.layer;
    tracing = null; layer.textContent = ''; g.classList.remove('tracing');
    var lit = drawn.edges.filter(function (e) { return e.el.classList.contains('on'); });
    if (!inc || still() || !lit.length || !window.requestAnimationFrame) return;
    var depths = lit.map(function (e) { return e.depth; }).filter(function (d, k, a) { return a.indexOf(d) === k; }).sort(function (a, b) { return b - a; });
    var items = lit.map(function (e) {
      var p = e.el.cloneNode(false); p.setAttribute('class', 'trace-line'); layer.appendChild(p);
      var len = p.getTotalLength(); p.style.strokeDasharray = len + ' ' + len; p.style.strokeDashoffset = String(-len);
      return { el: p, len: len, j: depths.indexOf(e.depth) };
    });
    g.classList.add('tracing');
    var run = {}, t0 = null, T = 800, n = depths.length;
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
  function openGraph(scroll) {
    var h = $('howto');
    if (!h.open) h.open = true; else if (graphDirty) drawGraph();
    if (scroll) setTimeout(function () { h.scrollIntoView({ behavior: still() ? 'auto' : 'smooth', block: 'start' }); }, 30);
  }
  $('howto').addEventListener('toggle', function () { if ($('howto').open) { drawGraph(); renderMetric(); } });

  // ---------------------------------------------------------------- the metric card (beside the graph)
  function linksOf(m) {
    var out = [], E = list(graph && graph.edges);
    var by = function (kind) { return E.filter(function (e) { return e.kind === kind; }); };
    var dims = by('breakdown').filter(function (e) { return e.source === m; }).map(function (e) { return e.dimension; });
    if (dims.length) out.push(['Breaks down by', dims.join(', ')]);
    var parts = by('formula').filter(function (e) { return e.source === m; }).map(function (e) { return e.target + (e.role ? ' (' + e.role + ')' : ''); });
    if (parts.length) out.push(['Made of', parts.join(', ')]);
    var feeds = by('formula').filter(function (e) { return e.target === m; }).map(function (e) { return e.source; });
    if (feeds.length) out.push(['Feeds', feeds.join(', ')]);
    var defs = by('definition').filter(function (e) { return e.source === m; }).map(function (e) { return e.target + (e.role ? ' (' + e.role + ')' : ''); });
    if (defs.length) out.push(['Defined from', defs.join(', ')]);
    var ctx = by('context').filter(function (e) { return e.target === m; }).map(function (e) { return e.source + (e.via ? ' (' + e.via + ')' : ''); });
    if (ctx.length) out.push(['Context', ctx.join(', ')]);
    var cause = function (e, other) { return e[other] + ' ' + (e.sign > 0 ? '+' : e.sign < 0 ? '\u2212' : '?') + (num(e.prior) != null ? ', prior ' + e.prior : '') + (e.status ? ', ' + e.status : '') + (e.note ? ': ' + e.note : ''); };
    var affects = by('may_affect').filter(function (e) { return e.source === m; }).map(function (e) { return cause(e, 'target'); });
    if (affects.length) out.push(['May affect', affects.join('; ')]);
    var affected = by('may_affect').filter(function (e) { return e.target === m; }).map(function (e) { return cause(e, 'source'); });
    if (affected.length) out.push(['May be affected by', affected.join('; ')]);
    return out;
  }
  var answers = {};
  function checkRows(a) {
    return list(a.checks).map(function (c) {
      c = obj(c); var s = str(c.status);
      return el('div', 'check', [el('span', 'verdict ' + s, [s || '-']), el('div', null, [el('div', 'what', [str(c.check)]), el('div', 'small', [str(c.detail)])])]);
    });
  }
  function checksSection(a) {
    var rows = checkRows(a);
    return section('Checks of the extension agent', [el('p', 'about', [str(a.message)])].concat(rows.length ? rows : [el('p', 'muted', ['The answer lists no checks.'])]));
  }
  function loadAnswer(name) {
    return get('/api/metric/' + encodeURIComponent(name)).then(function (r) { answers[name] = r; if (selNode === name) renderMetric(); return r; });
  }
  function renderMetric() {
    var head = $('metric-head'), d = $('metric-body');
    head.textContent = ''; d.textContent = '';
    if (!selNode) {
      head.appendChild(el('h3', null, ['Metric']));
      d.appendChild(el('p', 'empty', ['Click a box in the graph, or a metric under it, to see its definition, status, origin and links.']));
      return;
    }
    var m = meta[selNode], a = answers[selNode];
    head.appendChild(el('h3', null, [mono(selNode)]));
    if (!m) {
      if (a) { head.appendChild(el('span', 'chip st-other', [str(a.status)])); d.appendChild(checksSection(a)); }
      else { d.appendChild(el('p', 'muted', ['This metric is not in the graph.'])); loadAnswer(selNode).catch(function () {}); }
      return;
    }
    head.appendChild(el('span', 'chip ' + (m.status === 'validated' ? 'st-resolved' : 'st-other'), [str(m.status) || 'no status']));
    var pairs = [['Domain', dom === 'all' || domainOf(selNode) !== dom ? (domainOf(selNode) === 'other' ? 'Other' : domainLabel(domainOf(selNode))) : domainLabel(dom)], ['Status', m.status || '-'], ['Origin', m.origin || '-'], ['Unit', m.unit || '-'], ['Direction', m.direction || '-']];
    if (m.formula) pairs.push(['Formula', mono(m.formula)]);
    if (m.decomposition) pairs.push(['Made of', mono(m.decomposition)]);
    if (m.column) pairs.push(['Column', mono(m.column)]);
    if (list(m.systems).length) pairs.push(['Systems', m.systems.join(', ')]);
    if (m.source) pairs.push(['Data source', m.source]);
    pairs.push(['Dimensions', list(m.dimensions).join(', ') || '-']);
    d.appendChild(section('Definition', [el('p', 'about', [m.description || 'No description.']), kv(pairs)]));
    var links = linksOf(selNode);
    d.appendChild(section('Links by kind', links.length ? [kv(links)] : [el('p', 'muted', ['No links recorded for this metric.'])]));
    if (a) d.appendChild(checksSection(a));
    else if (m.origin === 'contributed') loadAnswer(selNode).catch(function () {});
    var inc = incidents.filter(function (i) { return incidentNodes(i).indexOf(selNode) >= 0; });
    var box = inc.length ? inc.map(function (i) {
      var b = el('button', 'involved', [str(i.id) + ' ' + str(i.metric) + ' (' + status(i) + ')']); b.type = 'button';
      b.onclick = function () { if (!touches(i, dom)) { dom = 'all'; renderTiles(); } pickInc(i.id); window.scrollTo({ top: 0, behavior: still() ? 'auto' : 'smooth' }); }; return b;
    }) : [el('p', 'muted', ['No incident involves this metric.'])];
    d.appendChild(section('Incidents involving it', box));
  }
  function showChecks(name) { selNode = name; openGraph(true); renderMetric(); applyStyles(); }

  function renderAll() { renderCounts(); renderTiles(); renderNow(); renderFeed(); renderReport(true); drawGraph(); renderMetric(); sizeStage(); }

  // ---------------------------------------------------------------- loading and polling
  function takeGraph(g) {
    graph = g; meta = {}; domOf = {}; answers = {};
    list(g.nodes).forEach(function (n) { if (n.kind === 'metric') meta[n.id] = n; });
    domains.forEach(function (d) { list(d.metrics).forEach(function (m) { domOf[m] = d.id; }); });
  }
  var clockFirst = null, accelerated = false, HOUR_MS = 3600000;
  function clockMode(s) {
    if (s.clock_mode === 'wall') { clockFirst = null; accelerated = false; return 'Live'; }
    var hour = Date.parse(str(s.last_hour)), now = Date.parse(str(s.server_now));
    if (isFinite(hour) && isFinite(now)) {
      if (hour > now) accelerated = true;
      if (!clockFirst) clockFirst = { hour: hour, now: now };
      else if (hour - clockFirst.hour > now - clockFirst.now + HOUR_MS) accelerated = true;
    }
    return accelerated ? 'Simulated, accelerated' : 'Replay';
  }
  function displayHour(ts) {
    return new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Berlin', day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).format(new Date(ts)) + ' Berlin';
  }
  function clockParts(ts) {
    var p = {};
    new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Berlin', weekday: 'short', day: '2-digit', month: 'short', year: 'numeric',
      hour: '2-digit', minute: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(ts)).forEach(function (x) { p[x.type] = x.value; });
    return { day: p.weekday + ' ' + p.day + ' ' + p.month + ' ' + p.year, hour: p.hour + ':' + p.minute };
  }
  function refresh() {
    get('/api/state').then(function (s) {
      lastState = s;
      var wall = s.clock_mode === 'wall', label = s.ready ? clockMode(s) : (wall ? 'Live' : 'Simulated');
      var cm = $('clock-mode'); cm.textContent = label; cm.className = 'clock-mode' + (wall ? ' live' : '');
      cm.title = wall ? 'Current hour: one data hour per real hour' : label === 'Replay' ? 'A finished database of simulated data, read hour by hour'
        : 'Simulated data: the simulator writes an hour of data every few seconds, faster than real time';
      if (s.ready) { var p = clockParts(s.last_hour); $('clock-date').textContent = p.day; $('clock-hour').textContent = p.hour; $('clock-date').title = 'Europe/Berlin, simulated data'; }
      else { $('clock-date').textContent = str(s.error); $('clock-hour').textContent = ''; }
      fillEnd(list(s.injected).filter(function (i) { return i.active; }));
      cueTick();
    }).catch(function (e) { $('clock-date').textContent = 'Clock unavailable: ' + e.message; });
    get('/api/graph').then(function (g) {
      var k = JSON.stringify(g);
      if (k !== graphKey) { graphKey = k; takeGraph(g); graphDirty = true; try { renderAll(); } catch (e) { toast('Could not draw the graph: ' + e.message); } }
    }).catch(function () {});
    get('/api/incidents').then(function (r) {
      var before = JSON.stringify([incidents, skipped, example]);
      var flipped = example && !r.example;
      incidents = list(r.incidents); skipped = r.skipped || 0; example = !!r.example;
      // which incidents are new since the last poll: their rows slide in and their tiles pulse once
      var ids = {}; incidents.forEach(function (i) { ids[i.id] = true; });
      fresh = {};
      if (known) incidents.forEach(function (i) { if (!known[i.id]) { fresh[i.id] = true; incidentNodes(i).forEach(function (m) { pulsed[domainOf(m)] = true; }); pulsed.all = true; } });
      known = ids;
      if (flipped || !incidents.some(function (i) { return i.id === selInc; })) { var s = inScope(), w = worst(s); selInc = w ? w.id : s.length ? s[0].id : null; }
      if (before !== JSON.stringify([incidents, skipped, example])) {
        try { renderCounts(); renderTiles(); renderNow(); renderFeed(); renderReport(false); applyStyles(); if (selNode) renderMetric(); sizeStage(); }
        catch (e) { toast('Could not draw the incidents: ' + e.message); }
      }
      cueTick();
    }).catch(function () { $('now-body').textContent = ''; $('now-body').appendChild(el('p', 'now-line', ['Could not read the incidents. Retrying every 2 s.'])); });
  }

  // ---------------------------------------------------------------- the test bench
  var KINDS = [
    ['payment_failure', 'Payment failure', ['country', 'platform'], 72, 0.4],
    ['stockout_surge', 'Stockouts', ['category'], 72, 0.4],
    ['search_regression', 'Search regression', ['country'], 72, 0.4],
    ['ad_budget_exhausted', 'Ad budget gone early', ['country'], 12, 0.4],
    ['traffic_drop', 'Traffic drop', ['country', 'platform'], 72, 0.5],
    ['feed_unavailable', 'Feed shows unavailable', [], 72, null],
    ['discount_push', 'Discount push', [], 72, null]];
  function faultBody(kind, value) {
    var k = KINDS.filter(function (x) { return x[0] === kind; })[0], hours = k[3];
    var body = { kind: kind, hours: hours, when: 'now' };
    if (k[4] != null) body.severity = k[4];
    k[2].forEach(function (p) { body[p] = value(p); });
    return body;
  }
  function mixShiftBody(category) { return { kind: 'mix_shift', category: category, hours: 12, when: 'now' }; }
  function promoBody(country) { return { kind: 'promo', country: country, hours: 12, when: 'now' }; }
  var REFUND_YAML = 'name: refund_rate\ndescription: Share of orders refunded\nsql: >\n  SELECT ts, country, platform, category, channel,\n         SUM(refunds) * 1.0 / NULLIF(SUM(orders), 0) AS value\n  FROM facts_hourly GROUP BY ts, country, platform, category, channel\ndimensions: [country, platform, category, channel]\nunit: ratio\n';
  var DUP_YAML = 'name: payment_rate\ndescription: Successful payments per payment attempt\nsql: >\n  SELECT ts, country, platform, category, channel,\n         SUM(payment_successes) * 1.0 / NULLIF(SUM(payment_attempts), 0) AS value\n  FROM facts_hourly GROUP BY ts, country, platform, category, channel\ndimensions: [country, platform, category, channel]\nunit: ratio\n';

  function fill(id, values) { var s = $(id); values.forEach(function (v) { var o = document.createElement('option'); o.value = v; o.textContent = v; s.appendChild(o); }); }
  KINDS.forEach(function (k) { var o = document.createElement('option'); o.value = k[0]; o.textContent = k[1]; $('f-kind').appendChild(o); });
  fill('f-country', ['DE', 'FR', 'ES', 'PL']); fill('f-platform', ['Android', 'iOS', 'Web']); fill('f-category', ['electronics', 'fashion', 'home', 'beauty', 'sports']);
  fill('pr-country', ['FR', 'DE', 'ES', 'PL']); fill('ms-category', ['fashion', 'electronics', 'home', 'beauty', 'sports']); fill('dd-source', ['payments', 'orders', 'traffic', 'search', 'recommendations', 'ads', 'inventory']);
  function showParams() {
    var k = KINDS.filter(function (x) { return x[0] === $('f-kind').value; })[0];
    ['country', 'platform', 'category'].forEach(function (p) { $('w-' + p).hidden = k[2].indexOf(p) < 0; });
    $('f-note').textContent = 'Runs ' + k[3] + ' simulated hours (' + Math.round(k[3] * 2 / 60 * 10) / 10 + ' min at 2 s an hour) unless you end it' + (k[4] != null ? '; severity ' + k[4] : '') + '.';
  }
  $('f-kind').onchange = showParams; showParams();

  var answerLog = [];
  function logAnswer(entry) {
    answerLog.unshift(entry); answerLog = answerLog.slice(0, 12);
    var box = $('answers'); box.textContent = '';
    answerLog.forEach(function (a, k) {
      var head = el('div', 'answer-head', [a.name ? mono(a.name) : null, a.status ? el('span', 'verdict ' + (/^(validated|accepted|ok|added)$/.test(a.status) ? 'pass' : /^(refused|rejected|alias|failed)/.test(a.status) || a.err ? 'fail' : 'warn'), [a.status]) : null, el('time', null, [a.at])]);
      var item = el('div', 'answer' + (a.err ? ' err' : ''), [head, el('p', null, [a.message])]);
      if (a.checks && a.checks.length) {
        if (k === 0) checkRows({ checks: a.checks }).forEach(function (r) { item.appendChild(r); });
        else item.appendChild(el('p', 'bnote', [a.checks.length + ' checks']));
      }
      box.appendChild(item);
    });
  }
  function nowStamp() { var d = new Date(); return ('0' + d.getHours()).slice(-2) + ':' + ('0' + d.getMinutes()).slice(-2) + ':' + ('0' + d.getSeconds()).slice(-2); }
  function report(msg, err, more) { logAnswer({ message: msg, err: !!err, at: nowStamp() }); if (!benchOpen()) toast(msg, more); }

  var endKey = '';
  function fillEnd(l) {
    var key = JSON.stringify(l.map(function (i) { return i.id; }));
    if (key === endKey) return;
    endKey = key;
    var box = $('end-list'); box.textContent = '';
    if (!l.length) { box.appendChild(el('p', 'bnote', ['Nothing injected is running.'])); return; }
    l.forEach(function (i) {
      var where = ['country', 'platform', 'category', 'source'].map(function (d) { return obj(i.scope)[d]; }).filter(Boolean).join(' ');
      var b = el('button', null, ['End it (rollback)']), name = str(i.kind) + (where ? ' ' + where : ''); b.type = 'button';
      b.onclick = function () { endInjection(i.id, b, name); };
      // a fault is red; a promo, a mix shift or late data is an innocent change and stays neutral
      var innocent = /^(promo|mix_shift|data_delay)$/.test(str(i.kind));
      var kindWord = str(i.kind) === 'data_delay' ? 'Late data' : innocent ? 'Innocent change' : 'Fault';
      box.appendChild(el('div', 'run' + (innocent ? ' innocent' : ''), [el('span', 'what', [name]), el('span', 'since', [kindWord + ', from ' + displayHour(obj(i.scope).start)]), b]));
    });
  }
  function endInjection(id, btn, name) {
    if (btn) btn.disabled = true;
    return post('/api/end', { id: id }).then(function (r) { report((name ? name + ' ended' : 'Ended') + ' from ' + displayHour(r.ended_from) + '. The monitor should see the metric recover.'); refresh(); return r; })
      .catch(function (e) { if (btn) btn.disabled = false; report('Could not end it: ' + e.message, true); throw e; });
  }
  function act(body, label) {
    return post('/api/inject', body).then(function (r) { report(label + ' from ' + displayHour(r.start) + '. Written into the data; the monitor has to find it.'); refresh(); return r; })
      .catch(function (e) { report(label + ' failed: ' + e.message, true); throw e; });
  }
  $('f-go').onclick = function () {
    var kind = $('f-kind').value, label = KINDS.filter(function (k) { return k[0] === kind; })[0][1];
    act(faultBody(kind, function (p) { return $('f-' + p).value; }), label + ' injected').catch(function () {});
  };
  $('pr-go').onclick = function () { act(promoBody($('pr-country').value), 'Promo started').catch(function () {}); };
  $('ms-go').onclick = function () { act(mixShiftBody($('ms-category').value), 'Mix shift started').catch(function () {}); };
  $('dd-go').onclick = function () { act({ kind: 'data_delay', source: $('dd-source').value, hours: 3, delay_hours: 2, when: 'now' }, 'Late data scheduled').catch(function () {}); };
  function handOver(yamlText) {
    return post('/api/metric', { yaml_text: yamlText }).then(function (r) {
      report('Handed to the monitor as ' + str(r.saved).split(/[\\/]/).pop() + '. Waiting for its answer...');
      return new Promise(function (resolve, reject) {
        var tries = 0;
        var poll = function () {
          get('/api/metric/' + encodeURIComponent(r.name)).then(function (res) { resolve(res); })
            .catch(function () { if (++tries < 30) setTimeout(poll, 2000); else reject(new Error('the monitor has not answered yet')); });
        };
        setTimeout(poll, 1500);
      });
    }).then(function (res) {
      res = obj(res); var name = str(res.name);
      if (name) answers[name] = res;
      var more = null;
      if (name && list(res.checks).length) { more = el('button', 'linkish', ['Show checks']); more.type = 'button'; more.onclick = function () { closeBench(); showChecks(name); }; }
      logAnswer({ name: name, status: str(res.status), message: str(res.message) || 'Monitor: ' + str(res.status), checks: list(res.checks), at: nowStamp() });
      if (!benchOpen()) toast(str(res.message) || 'Monitor: ' + str(res.status), more);
      refresh();
      return res;
    }).catch(function (e) { report('Adding the metric failed: ' + e.message, true); throw e; });
  }
  $('nm-go').onclick = function () { handOver(REFUND_YAML).catch(function () {}); };
  $('nm-dup').onclick = function () { handOver(DUP_YAML).catch(function () {}); };

  function benchOpen() { return !$('bench').hidden; }
  // while the drawer is open the page behind it is inert, so Tab stays in the drawer (and the presenter's cue bar)
  function setInert(on) { ['shell', 'masthead'].forEach(function (id) { var e = id === 'masthead' ? document.querySelector('.masthead') : $(id); if (e) e.inert = on; }); }
  function openBench() { $('bench').hidden = false; $('scrim').hidden = false; setInert(true); $('bench-btn').setAttribute('aria-expanded', 'true'); $('bench-close').focus(); }
  function closeBench() { if (!benchOpen()) return; $('bench').hidden = true; $('scrim').hidden = true; setInert(false); $('bench-btn').setAttribute('aria-expanded', 'false'); }
  $('bench-btn').onclick = function () { if (benchOpen()) closeBench(); else openBench(); };
  $('bench-close').onclick = function () { closeBench(); $('bench-btn').focus(); };
  $('scrim').onclick = closeBench;

  // ---------------------------------------------------------------- presenter mode
  // Eight steps of the demo. Each has one primary action through the API and one line to say. Steps 2 and 4 watch
  // the incidents and finish by themselves; the cue's violet spotlight marks what to talk about.
  var P = { on: false, step: 0, before: null, inc: null, injId: null, waitFrom: null, after: {}, done: {}, msg: '' };
  try { var saved = JSON.parse(store.get('cue') || 'null'); if (saved && typeof saved.step === 'number') { P.step = saved.step; P.inc = saved.inc || null; P.injId = saved.injId || null; P.done = saved.done || {}; P.before = saved.before || null; } } catch (e) { /* ignore */ }
  function saveCue() { store.set('cue', JSON.stringify({ step: P.step, inc: P.inc, injId: P.injId, done: P.done, before: P.before })); }
  function idsNow() { var s = {}; incidents.forEach(function (i) { s[i.id] = true; }); return s; }
  function newSince(before, test) { return incidents.filter(function (i) { return !(before || {})[i.id] && (!test || test(i)); }); }
  // the spotlight is kept as selectors: the polls redraw tiles, rows and the report, and the ring follows them
  // (without replaying its entrance)
  function spot(sels, noScroll) {
    spotSels = !sels ? [] : Array.isArray(sels) ? sels : [sels];
    applySpot(true);
    var first = spotSels.map(function (s) { return document.querySelector(s); }).filter(Boolean)[0];
    if (first && !noScroll) first.scrollIntoView({ behavior: still() ? 'auto' : 'smooth', block: 'nearest' });
  }
  function tileEl(id) { return '#tiles [data-dom="' + id + '"]'; }
  function rowEl(id) { return '#feed .inc[data-id="' + id + '"]'; }
  var STEPS = [
    { label: 'Inject the fault', title: '1. Break payments', go: 'Inject payment failure: DE, Android',
      say: 'We break payments for Android users in Germany. Nobody tells the monitor: it has to find this in the data.',
      run: function () {
        // a new run of the demo: forget what an earlier run found, resolved or filed
        P.before = idsNow(); P.done = {}; P.after = {}; P.inc = null; P.injId = null; P.rolled = false; saveCue();
        return act(faultBody('payment_failure', function (p) { return { country: 'DE', platform: 'Android' }[p]; }), 'Payment failure injected').then(function (r) {
          P.waitFrom = lastState && lastState.last_hour; P.injId = null; done(0); setStep(1);
          return r;
        });
      },
      cue: function () { if (dom !== 'all') setDom('all'); spot(tileEl('payments')); } },
    { label: 'Wait for it', title: '2. Wait for the incident', go: 'Open the new incident',
      say: 'The monitor reads one simulated hour every two seconds. Watch the Payments tile: it turns red when the incident is found.',
      watch: function () {
        var hit = newSince(P.before, function (i) { return isLive(i) && touches(i, 'payments'); })[0] || newSince(P.before, isLive)[0];
        if (hit && !P.done[1]) { P.inc = hit.id; done(1); if (dom !== 'all' && !touches(hit, dom)) dom = 'all'; pickInc(hit.id); renderTiles(); spot(rowEl(hit.id)); setTimeout(function () { if (P.step === 1) setStep(2); }, 2200); }
        return hit;
      },
      run: function () { var hit = STEPS[1].watch(); if (hit) { setStep(2); return Promise.resolve(); } return Promise.reject(new Error('No new incident yet. It usually takes 4 to 8 simulated hours.')); },
      cue: function () { spot(tileEl('payments')); } },
    { label: 'Read the report', title: '3. Read the report', go: 'Show the cause',
      say: 'It walked the formula tree from orders down to payment success, narrowed it to Germany and Android, and named the cause.',
      run: function () { showChainAndCause(P.view === 'chain' ? 'cause' : 'chain'); done(2); return Promise.resolve(); },
      cue: function () { if (P.inc && P.inc !== selInc && incidents.some(function (i) { return i.id === P.inc; })) pickInc(P.inc); showChainAndCause(); } },
    { label: 'Roll back', title: '4. Roll back and watch it resolve', go: 'End the fault (rollback)',
      say: 'We roll the change back. The monitor sees payments recover and closes the incident on its own.',
      run: function () {
        var inj = list(lastState && lastState.injected).filter(function (i) { return i.active && i.kind === 'payment_failure'; })[0];
        if (!inj) return Promise.reject(new Error('No payment failure is running. Inject it in step 1 first.'));
        // the incident to watch: the one step 2 found while it is live, else the live payments incident, else the
        // selected live one
        var mine = incidents.filter(function (i) { return i.id === P.inc; })[0];
        if (!mine || !isLive(mine)) {
          var cur = selected();
          P.inc = (worst(incidents.filter(function (i) { return touches(i, 'payments'); })) || (cur && isLive(cur) ? cur : null) || worst(incidents) || {}).id || null;
        }
        delete P.done[3];
        return endInjection(inj.id, null, ['payment_failure', obj(inj.scope).country, obj(inj.scope).platform].filter(Boolean).join(' ')).then(function () { P.injId = inj.id; P.rolled = true; saveCue(); renderCue(); });
      },
      watch: function () {
        var i = incidents.filter(function (x) { return x.id === P.inc; })[0];
        if (i && status(i) === 'resolved' && !P.done[3]) { done(3); if (selInc !== i.id) pickInc(i.id); spot('#report-status'); }
        return i && status(i) === 'resolved';
      },
      cue: function () { var i = P.inc; if (i && i !== selInc && incidents.some(function (x) { return x.id === i; })) pickInc(i); spot('#report-status'); } },
    { label: 'Promo', title: '5. A planned promo', go: 'Start a promo in France (12 h)',
      say: 'A promo lifts orders in France. It is on the calendar, so the monitor explains the jump instead of raising an alarm.',
      run: function () { P.before = idsNow(); return act(promoBody('FR'), 'Promo started').then(function () { done(4); }); },
      watch: function () { return watchNew(4); },
      cue: function () { spot('#feed'); } },
    { label: 'Mix shift', title: '6. An innocent mix shift', go: 'Shift traffic toward fashion (12 h)',
      say: 'Traffic moves toward fashion and every rate stays the same. Totals move, nothing broke: the report says why there is no alert.',
      run: function () { P.before = idsNow(); return act(mixShiftBody('fashion'), 'Mix shift started').then(function () { done(5); }); },
      watch: function () { return watchNew(5); },
      cue: function () { spot('#feed'); } },
    { label: 'Add refund_rate', title: '7. Add a new metric', go: 'Add refund_rate',
      say: 'We hand over a new metric with only a query. The agent finds its formula, checks it and places it on the graph.',
      run: function () { return handOver(REFUND_YAML).then(function (res) { done(6); P.msg = answerLine(res); renderCue(); }); },
      cue: function () { spot(null); } },
    { label: 'Add a duplicate', title: '8. Add a duplicate', go: 'Add payment_rate (a duplicate)',
      say: 'Same query as payment success rate under another name. The agent refuses it as an alias and says of what.',
      run: function () { return handOver(DUP_YAML).then(function (res) { done(7); P.msg = answerLine(res); renderCue(); }); },
      cue: function () { spot(null); } }];
  function answerLine(res) {
    res = obj(res); var c = list(res.checks).map(obj), n = function (s) { return c.filter(function (x) { return x.status === s; }).length; };
    return str(res.message) + (c.length ? ' (' + c.length + ' checks: ' + n('pass') + ' pass, ' + n('warn') + ' warn, ' + n('fail') + ' fail)' : '');
  }
  function watchNew(k) {
    if (!P.done[k] || !P.before) return null;
    var hit = newSince(P.before)[0];
    if (hit && P.after[k] !== hit.id) { P.after[k] = hit.id; if (!touches(hit, dom)) { dom = 'all'; renderTiles(); } pickInc(hit.id); spot(rowEl(hit.id)); }
    return hit;
  }
  // step 3 lights both the chain check and the supported cause; the button scrolls between them
  function showChainAndCause(which) {
    var chain = $('sec-chain'), cause = $('sec-cause');
    spot(['#sec-chain', '#sec-cause'], true);
    var first = which === 'cause' ? cause || chain : chain || cause;
    P.view = first === cause ? 'cause' : 'chain';
    STEPS[2].go = P.view === 'chain' && cause ? 'Show the cause' : 'Show the chain';
    if (first) {
      var sc = $('report-scroll');
      if (sc.scrollHeight > sc.clientHeight + 1) {
        // the live report is rebuilt on every poll, which would cut a smooth scroll short: pin the section, and
        // keep it in view across rebuilds until the presenter scrolls the report by hand
        var pin = reportPin = { id: selInc, sel: first === cause ? '#sec-cause' : '#sec-chain' };
        sc.scrollTo({ top: pinY(sc, first), behavior: still() ? 'auto' : 'smooth' });
        // a smooth scroll can stall (a background tab, a busy frame): land on the section regardless
        setTimeout(function () { var e = reportPin === pin && document.querySelector(pin.sel); if (e && Math.abs(pinY(sc, e) - sc.scrollTop) > 4) sc.scrollTop = pinY(sc, e); }, 900);
      }
      else first.scrollIntoView({ behavior: still() ? 'auto' : 'smooth', block: 'center' });      // a narrow screen: the page scrolls
    }
    else P.msg = 'This incident has no chain or supported cause yet. Its report is still being written.';
  }
  function pinY(sc, e) { return Math.max(0, e.getBoundingClientRect().top - sc.getBoundingClientRect().top + sc.scrollTop - 16); }
  ['wheel', 'touchstart', 'keydown', 'mousedown'].forEach(function (ev) { $('report-scroll').addEventListener(ev, function () { reportPin = null; }, { passive: true }); });
  function done(k) { P.done[k] = true; saveCue(); }
  function setStep(k) { P.step = Math.max(0, Math.min(STEPS.length - 1, k)); P.msg = ''; if (P.step !== 2) reportPin = null; saveCue(); if (P.on) STEPS[P.step].cue(); renderCue(); }
  var busy = false;
  function renderCue() {
    var ol = $('cue-steps'); ol.textContent = '';
    STEPS.forEach(function (s, k) {
      var b = el('button', null, [el('span', 'lbl', [(k + 1) + ' ' + s.label])]); b.type = 'button';
      b.title = s.title; if (k === P.step) b.setAttribute('aria-current', 'step');
      b.onclick = function () { setStep(k); };
      ol.appendChild(el('li', k === P.step ? 'cur' : P.done[k] ? 'done' : null, [b]));
    });
    var s = STEPS[P.step];
    $('cue-title').textContent = s.title + (P.done[P.step] ? ' \u00b7 done' : '');
    $('cue-say').textContent = s.say;
    var go = $('cue-go'); go.textContent = s.go; go.disabled = busy;
    $('cue-back').disabled = P.step === 0;
    $('cue-next').textContent = P.step === STEPS.length - 1 ? 'Finish' : 'Next step';
    var st = $('cue-status'); st.textContent = '';
    if (P.msg) { st.textContent = P.msg; return; }
    if (P.step === 1 && !P.done[1]) {
      if (!P.before) { st.textContent = 'Run step 1 first: inject the payment failure.'; return; }
      var hours = P.waitFrom && lastState ? Math.round((Date.parse(lastState.last_hour) - Date.parse(P.waitFrom)) / HOUR_MS) : null;
      st.appendChild(el('span', 'wait', ['Watching the data' + (hours != null ? ': ' + hours + ' simulated hour' + (hours === 1 ? '' : 's') + ' read since the fault' : '') + '. This step moves on when the incident appears.']));
    } else if (P.step === 1) st.appendChild(el('span', null, [el('b', null, [str(P.inc)]), ' found. Moving to the report.']));
    else if (P.step === 3 && P.rolled && !P.done[3]) st.appendChild(el('span', 'wait', ['Rolled back. Waiting for ' + str(P.inc) + ' to resolve.']));
    else if (P.step === 3 && P.done[3]) st.appendChild(el('span', null, [el('b', null, [str(P.inc)]), ' is resolved.']));
    else if ((P.step === 4 || P.step === 5) && P.done[P.step] && !P.after[P.step]) st.appendChild(el('span', 'wait', ['Written into the data. Waiting for the monitor to file it.']));
    else if ((P.step === 4 || P.step === 5) && P.after[P.step]) st.appendChild(el('span', null, ['The monitor filed ', el('b', null, [P.after[P.step]]), '. It is selected above.']));
  }
  function cueTick() {
    if (!P.on) return;
    var s = STEPS[P.step];
    if (s.watch) s.watch();
    if (!busy) renderCue();
  }
  $('cue-go').onclick = function () {
    var s = STEPS[P.step]; busy = true; P.msg = ''; renderCue();
    s.run().then(function () { busy = false; renderCue(); }, function (e) { busy = false; P.msg = e.message; renderCue(); });
  };
  $('cue-back').onclick = function () { setStep(P.step - 1); };
  $('cue-next').onclick = function () { if (P.step === STEPS.length - 1) { togglePresenter(false); return; } setStep(P.step + 1); };
  $('cue-close').onclick = function () { togglePresenter(false); };
  function togglePresenter(on) {
    P.on = on == null ? !P.on : on;
    $('cue').hidden = !P.on; document.body.classList.toggle('presenting', P.on);
    $('present-btn').setAttribute('aria-pressed', P.on ? 'true' : 'false');
    store.set('present', P.on ? '1' : '0');
    if (P.on) { renderCue(); sizeCue(); STEPS[P.step].cue(); renderCue(); }
    else spot(null);
    sizeStage();
  }
  $('present-btn').onclick = function () { togglePresenter(); };
  function sizeCue() { var c = $('cue'); if (!c.hidden) document.documentElement.style.setProperty('--cue-h', c.offsetHeight + 'px'); }

  // ---------------------------------------------------------------- keyboard, theme, layout
  document.addEventListener('keydown', function (e) {
    var t = e.target, typing = t && (t.tagName === 'INPUT' || t.tagName === 'SELECT' || t.tagName === 'TEXTAREA' || t.isContentEditable);
    if (e.key === 'Escape') { if (benchOpen()) { closeBench(); $('bench-btn').focus(); } return; }
    if (typing || e.ctrlKey || e.metaKey || e.altKey) return;
    var k = e.key.toLowerCase();
    if (k === 'p') { e.preventDefault(); togglePresenter(); }
    else if (k === 'b') { e.preventDefault(); if (benchOpen()) closeBench(); else openBench(); }
    else if (k === 'g') { e.preventDefault(); var h = $('howto'); h.open = !h.open; if (h.open) openGraph(true); }
  });
  $('theme-btn').onclick = function () {
    var root = document.documentElement, cur = root.dataset.theme || (window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
    root.dataset.theme = cur === 'dark' ? 'light' : 'dark';
  };
  // the stage fills the screen below the banner, leaving the folded graph's bar in view
  function sizeStage() {
    var st = $('stage');
    if (window.innerWidth <= 900) { st.style.removeProperty('height'); return; }
    var top = st.getBoundingClientRect().top + window.scrollY, cue = P.on ? $('cue').offsetHeight : 0;
    var h = Math.max(380, window.innerHeight - top - cue - 14 - 52 - 14);
    document.documentElement.style.setProperty('--stage-h', h + 'px');
    fitGraph();
  }
  window.addEventListener('resize', function () { sizeCue(); sizeStage(); });
  if (window.ResizeObserver) { var ro = new ResizeObserver(function () { sizeCue(); sizeStage(); }); ro.observe($('now')); ro.observe($('tiles')); ro.observe($('cue')); }

  var params = new URLSearchParams(location.search), asked = params.get('incident');
  if (asked) selInc = asked;
  var wantPresent = params.get('present') === '1' || (params.get('present') !== '0' && store.get('present') === '1');
  get('/api/domains').then(function (d) { domains = list(d); }).catch(function () {}).then(function () {
    renderTiles(); refresh(); setInterval(refresh, 2000); sizeStage();
    // ?present=1&step=3 opens presenter mode at a step (for the video); it waits for the first incidents
    var askedStep = parseInt(params.get('step'), 10);
    if (askedStep >= 1 && askedStep <= STEPS.length) { P.step = askedStep - 1; wantPresent = true; }
    if (wantPresent) setTimeout(function () { togglePresenter(true); }, 1200);
    if (params.get('bench') === '1') openBench();
    if (params.get('graph') === '1') openGraph(false);
  });
})();
