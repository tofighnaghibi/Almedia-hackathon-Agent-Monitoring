/* Eye of the System, version 2 "Analyst brief". No build step: plain ES5-style script, served at /static/v2/app.js.
   The data logic follows ui/static/index.html (the reference console); the page is organised around the brief. */
(function () {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';
  var $ = function (id) { return document.getElementById(id); };
  var graph = null, meta = {}, domains = [], domOf = {}, incidents = [], example = false, skipped = 0, graphKey = '';
  var dom = 'all', selInc = null, selNode = null, view = 'brief', drawn = null;
  var LIVE = ['open', 'ongoing'], COUNTED = ['open', 'ongoing', 'resolved'], KNOWN = ['open', 'ongoing', 'resolved', 'explained', 'pending'];
  var params = new URLSearchParams(location.search);
  var still = window.matchMedia && window.matchMedia('(prefers-reduced-motion: reduce)').matches;

  // ------------------------------------------------------------ helpers
  // anything the monitor writes may have the wrong type: show it as text, never let it stop the page
  function str(v) { return v == null ? '' : (typeof v === 'object' ? JSON.stringify(v) : String(v)); }
  function list(v) { return Array.isArray(v) ? v : []; }
  function obj(v) { return v && typeof v === 'object' && !Array.isArray(v) ? v : {}; }
  function text(s) { return document.createTextNode(str(s)); }
  function el(tag, cls, kids) { var e = document.createElement(tag); if (cls) e.className = cls; (kids || []).forEach(function (k) { if (k == null || k === false) return; e.appendChild(k && k.nodeType ? k : text(k)); }); return e; }
  function get(url) { return fetch(url).then(function (r) { if (!r.ok) throw new Error(r.status); return r.json(); }); }
  function post(url, body) {
    return fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })
      .then(function (r) { return r.json().then(function (j) { if (!r.ok) throw new Error(j.detail || r.status); return j; }); });
  }
  function num(n) { return typeof n === 'number' && isFinite(n) ? n : null; }
  function signed(s) { return s.charAt(0) === '-' ? '−' + s.slice(1) : s; }      // a true minus sign
  function fmt(n, d) { n = num(Number(n)); return n == null ? '–' : signed(n.toLocaleString('en', { maximumFractionDigits: d == null ? 1 : d })); }
  // a readout figure: whole numbers from 100 up, two decimals from 1, three significant digits below
  function short(n) {
    n = num(n); if (n == null) return '–';
    var a = Math.abs(n);
    return signed(a >= 100 ? n.toLocaleString('en', { maximumFractionDigits: 0 }) : a >= 1 ? n.toLocaleString('en', { maximumFractionDigits: 2 })
      : n.toLocaleString('en', { maximumSignificantDigits: 3 }));
  }
  function pct(n) { n = num(n); return n == null ? '–' : (n > 0 ? '+' : '') + fmt(n) + '%'; }
  function absPct(n) { n = Math.abs(n); return (n < 10 ? fmt(n, 1) : fmt(n, 0)) + '%'; }
  function compactEur(v) {      // -1,101 -> -1.1k, for the rail
    v = num(v); if (v == null) return '';
    var a = Math.abs(v), s = a >= 1e6 ? fmt(v / 1e6, 1) + 'M' : a >= 1e4 ? fmt(v / 1e3, 0) + 'k' : a >= 1e3 ? fmt(v / 1e3, 1) + 'k' : fmt(v, 0);
    return (v > 0 ? '+' : '') + s + ' EUR';
  }
  function parseNum(s) { s = str(s).replace(/,/g, '').replace('−', '-'); var v = parseFloat(s); return isFinite(v) ? v : null; }
  // is this change good or bad for the business? by the metric's own direction (up or down is good)
  function tone(metric, delta) {
    var d = obj(meta[metric]).direction; delta = num(delta);
    if (!delta || (d !== 'up' && d !== 'down')) return '';
    return (d === 'up') === (delta > 0) ? 'good' : 'bad';
  }
  // a gain is green only in an explained incident: a fault can raise a figure for a while, which is no gain
  function toneIn(i, metric, delta) { var t = tone(metric, delta); return t === 'good' && status(i) !== 'explained' ? '' : t; }
  function euroTone(i, v) { v = num(v); return !v ? '' : v < 0 ? 'bad' : status(i) === 'explained' ? 'good' : ''; }
  // the incident's effect in euros: on contribution when it has one, else the first euro figure
  function euroOf(i) {
    var imp = obj(i.impact);
    var k = num(imp.contribution_eur) != null ? 'contribution_eur' : Object.keys(imp).filter(function (x) { return /_eur$/.test(x) && num(imp[x]) != null; })[0];
    return k ? num(imp[k]) : null;
  }
  function segText(s) { var o = obj(s), k = Object.keys(o); return k.length ? k.map(function (x) { return str(o[x]); }).join(' × ') : 'all'; }
  function when(t) { return str(t).replace('T', ' ').replace(':00Z', '').replace(/-/g, '‑'); }
  // an incident time, in UTC like the monitor's own texts: "Mon 23 Nov, 20:00"
  function day(t) {
    var d = new Date(str(t)); if (!isFinite(d)) return str(t);
    var p = {};
    new Intl.DateTimeFormat('en-GB', { timeZone: 'UTC', weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
      .formatToParts(d).forEach(function (x) { p[x.type] = x.value; });
    return p.weekday + ' ' + p.day + ' ' + p.month + ', ' + p.hour + ':' + p.minute;
  }
  function hourOnly(t) { var d = new Date(str(t)); return isFinite(d) ? ('0' + d.getUTCHours()).slice(-2) + ':00' : ''; }
  // The polls redraw the chips, the rail and the brief every few seconds while the clock runs. Keyboard focus would
  // fall to the page each time, so remember the focused control and focus its twin after the redraw.
  function focusMemo() {
    var a = document.activeElement; if (!a || a === document.body) return function () {};
    var box = ['domains', 'feed', 'brief', 'metric', 'end-list', 'extra'].map($).filter(function (c) { return c && c.contains(a); })[0];
    if (!box) return function () {};
    var key = function (e) { return e.getAttribute('data-k') || e.textContent; }, k = key(a), tag = a.tagName;
    return function () {
      if (document.contains(a)) return;
      var twin = Array.prototype.filter.call(box.querySelectorAll(tag), function (e) { return key(e) === k; })[0];
      if (twin) try { twin.focus({ preventScroll: true }); } catch (e) { twin.focus(); }
    };
  }
  function svg(tag, attrs, parent) { var e = document.createElementNS(NS, tag); for (var k in attrs) e.setAttribute(k, attrs[k]); if (parent) parent.appendChild(e); return e; }
  function note(msg) { $('notice').textContent = msg || ''; }
  function store(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }

  // plain words for the metric names the monitor uses
  var NAMES = { sessions: 'Traffic (sessions)', gmv: 'GMV', aov: 'Average order value', contribution: 'Contribution', orders: 'Orders',
    ad_ctr: 'Ad click-through rate', ad_cpc: 'Ad cost per click', search_ctr: 'Search click-through rate', feed_ctr: 'Feed click-through rate',
    add_to_cart_rate: 'Add-to-cart rate', add_to_cart: 'Adds to cart', marketplace_fees: 'Marketplace fees', data_arrival: 'Data arrival',
    calendar: 'Planned event' };
  function human(m) { m = str(m); if (NAMES[m]) return NAMES[m]; var s = m.replace(/_/g, ' '); return s.charAt(0).toUpperCase() + s.slice(1); }
  var COUNTRY = { DE: 'Germany', FR: 'France', ES: 'Spain', PL: 'Poland' };
  function placeText(seg) {
    seg = obj(seg); var out = [];
    if (seg.country) out.push('in ' + (COUNTRY[seg.country] || seg.country));
    if (seg.platform) out.push(seg.platform === 'Web' ? 'on the web' : 'on ' + seg.platform);
    if (seg.category) out.push((seg.country ? 'for ' : 'in ') + seg.category);
    if (seg.channel) out.push('via ' + seg.channel + ' traffic');
    return out.length ? out.join(' ') : 'across the marketplace';
  }

  // ------------------------------------------------------------ incidents: status, scope, words
  function status(i) { return str(i.status) || 'open'; }
  function stOf(i) { var s = status(i); return 'st-' + (KNOWN.indexOf(s) >= 0 ? s : 'other'); }
  function domainOf(metric) { return domOf[metric] || 'other'; }
  function domainLabel(id) { if (id === 'all') return 'All domains'; var d = domains.filter(function (x) { return x.id === id; })[0]; return d ? d.label : 'Other'; }
  function incidentNodes(i) {
    var s = {}; s[str(i.metric)] = true;
    list(i.contributions).forEach(function (c) { if (c && c.node) s[str(c.node)] = true; });
    // metrics the monitor also watched move during the investigation count for their own domain too
    list(i.trail).forEach(function (t) { t = obj(t); if (t.action === 'watch' && t.target && meta[t.target]) s[str(t.target)] = true; });
    return Object.keys(s);
  }
  function touches(i, d) { return d === 'all' || incidentNodes(i).some(function (m) { return domainOf(m) === d; }); }
  function inScope() { return incidents.filter(function (i) { return touches(i, dom); }); }
  function liveCount(d) { return incidents.filter(function (i) { return LIVE.indexOf(status(i)) >= 0 && touches(i, d); }).length; }
  function byId(id) { return incidents.filter(function (x) { return x.id === id; })[0]; }

  // the moved metric and its change: the leading driver, else the incident's own metric
  // When the chain check measured the same metric, its figure (since the onset, like the four big numbers) is used,
  // so the headline, the rail and the chain check never show two different percentages for one metric.
  function lead(i) {
    var d = obj(list(i.drivers)[0]), ch = obj(i.change);
    if (d.metric && num(d.delta_pct) != null) {
      var c = null; try { c = chainOf(i); } catch (e) { c = null; }
      var row = c ? c.rows.filter(function (r) { return r.metric === str(d.metric) && r.moved && num(r.dev) != null; })[0] : null;
      return { metric: str(d.metric), pct: row ? row.dev : num(d.delta_pct), seg: d.segment || i.segment };
    }
    return { metric: str(i.metric), pct: num(ch.delta_pct), seg: i.segment };
  }
  function topHyp(i) {
    var hs = list(i.hypotheses).map(obj);
    return hs.filter(function (h) { return h.status === 'supported'; })[0] || hs.filter(function (h) { return h.status === 'plausible'; })[0] || null;
  }
  // "Payment success rate fell 42% in Germany on Android"
  function headline(i) {
    if (status(i) === 'pending') { var h = topHyp(i) || obj(list(i.hypotheses)[0]); return h.cause ? 'Waiting for data: ' + str(h.cause) : 'Waiting for late data'; }
    var L = lead(i);
    if (L.pct == null) return human(L.metric) + ' ' + placeText(L.seg);
    if (Math.abs(L.pct) < 0.05) return human(L.metric) + ' held steady ' + placeText(L.seg);
    return human(L.metric) + ' ' + (L.pct < 0 ? 'fell ' : 'rose ') + absPct(L.pct) + ' ' + placeText(L.seg);
  }
  // the cause without its kind prefix ("release payments-sdk: ...", "planned event: ...")
  function causeText(h) {
    var c = str(h.cause);
    if (h.kind === 'release' || h.kind === 'calendar') c = c.replace(/^[^:]{1,48}:\s+/, '');
    return c;
  }
  var KIND = { release: 'a release', calendar: 'a planned event', freshness: 'late data', metric: 'a metric', mix: 'a mix shift' };

  // ------------------------------------------------------------ domains and counts
  function renderChips() {
    var nav = $('domains'); nav.textContent = '';
    var l = [{ id: 'all', label: 'All' }].concat(domains);
    if (graph && list(graph.nodes).some(function (n) { return n.kind === 'metric' && !domOf[n.id]; })) l.push({ id: 'other', label: 'Other' });
    l.forEach(function (d) {
      var n = liveCount(d.id);
      var b = el('button', 'chip' + (d.id === dom ? ' on' : ''), [d.label, n ? el('span', 'n', [String(n)]) : null]);
      b.type = 'button'; b.setAttribute('data-k', 'chip:' + d.id);
      b.setAttribute('aria-pressed', d.id === dom ? 'true' : 'false');
      if (n) b.title = n + ' live incident' + (n > 1 ? 's' : '') + ' in ' + d.label;
      b.onclick = function () { dom = d.id; selNode = null; var s = inScope(); selInc = s.length ? s[0].id : null; renderAll(); syncURL(); };
      nav.appendChild(b);
    });
    renderCounts();
  }
  // the top bar: how many incidents are in each state, across every domain; explained and pending are not alerts
  function renderCounts() {
    var c = $('counts'); c.textContent = '';
    KNOWN.forEach(function (s) {
      var k = incidents.filter(function (i) { return status(i) === s; }).length;
      if (!k && COUNTED.indexOf(s) < 0) return;
      c.appendChild(el('span', 'count st-' + s + (k ? '' : ' zero'), [el('b', null, [String(k)]), ' ' + s]));
    });
  }

  // ------------------------------------------------------------ the rail
  var GROUPS = [['Needs attention', ['open', 'ongoing']], ['Resolved', ['resolved']], ['Not alerts', ['explained', 'pending']]];
  var seen = null, arrived = {}, lastSt = {}, changedSt = {};
  function railItem(i) {
    var st = status(i), quiet = st === 'explained' || st === 'pending', eur = euroOf(i);
    var top = [el('span', 'inc-status', [st === 'explained' ? 'Explained' : st.charAt(0).toUpperCase() + st.slice(1)])];
    if (!quiet && i.severity) top.push(el('span', 'sev ' + str(i.severity), [str(i.severity)]));
    top.push(el('span', 'inc-time', [day(i.first_seen)]));
    var foot = [];
    if (eur != null && !quiet) foot.push(el('span', 'chg ' + euroTone(i, eur), [compactEur(eur)]));
    var L = lead(i);
    if (L.metric !== str(i.metric) && num(obj(i.change).delta_pct) != null && !quiet)
      foot.push(el('span', null, [human(i.metric) + ' ' + pct(obj(i.change).delta_pct)]));
    var ik = str(obj(i.innocent).kind), IK = { mix: 'a mix shift', calendar: 'a planned event', freshness: 'late data' };
    if (st === 'explained') foot.push(el('span', null, [ik ? 'Innocent: ' + (IK[ik] || ik) : (topHyp(i) && KIND[topHyp(i).kind] ? 'No alert: ' + KIND[topHyp(i).kind] : 'No alert')]));
    if (i.recurrence_of) foot.push(el('span', 'again', ['back again']));
    var b = el('button', 'inc ' + stOf(i) + (quiet ? ' quiet' : '') + (i.id === selInc ? ' sel' : '') + (arrived[i.id] ? ' arrived' : ''), [
      el('span', 'inc-top', top), el('span', 'inc-head', [headline(i)]), foot.length ? el('span', 'inc-foot', foot) : null]);
    b.type = 'button'; b.setAttribute('data-k', 'inc:' + str(i.id));
    b.setAttribute('aria-current', i.id === selInc ? 'true' : 'false');
    b.title = str(i.id) + ', first seen ' + when(i.first_seen) + ' UTC';
    b.onclick = function () { pickInc(i.id); };
    return b;
  }
  function renderFeed() {
    $('feed-title').textContent = dom === 'all' ? 'Incidents' : 'Incidents in ' + domainLabel(dom);
    var f = $('feed'); f.textContent = '';
    var l = inScope();
    if (!l.length) f.appendChild(el('p', 'rail-empty', [dom === 'all' ? 'No incidents. The monitor is reading every new hour of data.' : 'No incident touches ' + domainLabel(dom) + '.']));
    GROUPS.forEach(function (g) {
      var items = l.filter(function (i) { return g[1].indexOf(status(i)) >= 0 || (g[0] === 'Not alerts' && KNOWN.indexOf(status(i)) < 0); });
      if (!items.length) return;
      var box = el('div', 'rail-group', [el('h3', null, [g[0], el('span', 'k', [String(items.length)])])]);
      items.forEach(function (i) { try { box.appendChild(railItem(i)); } catch (err) { box.appendChild(el('p', 'rail-empty', ['An incident (' + str(i.id) + ') could not be shown: ' + err.message])); } });
      f.appendChild(box);
    });
    if (skipped) f.appendChild(el('p', 'rail-empty small', [skipped + ' file' + (skipped > 1 ? 's' : '') + ' in the incidents folder ignored (not an incident, or still being written).']));
    var s = f.querySelector('.inc.sel');
    if (s && railSel !== selInc) {
      railSel = selInc;
      var fb = f.parentNode.getBoundingClientRect(), rb = s.getBoundingClientRect();
      if (rb.top < fb.top + 40 || rb.bottom > fb.bottom) f.parentNode.scrollTop += rb.top - fb.top - 80;
    }
  }
  var railSel = null;

  // ------------------------------------------------------------ the brief
  function stepSec(n, title, sub, body) {
    return el('section', 'step', [
      el('div', 'step-label', [el('span', 'step-n', [String(n)]), el('div', null, [el('h2', null, [title]), sub ? el('p', null, [sub]) : null])]),
      el('div', 'step-body', body)]);
  }
  function metricLink(m, label) {
    var b = el('button', 'mlink', [label || human(m)]); b.type = 'button';
    b.title = str(m) + ': open its definition in How the business works';
    b.onclick = function () { showMetric(str(m)); };
    return b;
  }
  // The chain check: the factors of the moved outcome, observed against expected. Read from the monitor's own
  // "check factors of <metric>" step when it has one, else from the contributions along the graph.
  function chainOf(i) {
    var shares = {}, rows = [], parent = null;
    list(i.contributions).forEach(function (c) { c = obj(c); if (c.node) shares[str(c.node)] = num(c.share_of_delta); });
    var step = list(i.trail).map(obj).filter(function (t) { return t.action === 'check' && /^factors of /.test(str(t.target)); })[0];
    if (step) {
      parent = str(step.target).replace(/^factors of /, '');
      var body = str(step.result).split(/factor by factor:\s*/)[1] || '';
      body.split(/;\s*/).forEach(function (part) {
        var m = part.match(/^\s*([A-Za-z0-9_]+)\s+(\S+?)\s+vs\s+(\S+?),\s*(normal|moved)(.*)$/);
        if (!m) return;
        var o = parseNum(m[2]), e = parseNum(m[3]), dev = o != null && e ? (o - e) / Math.abs(e) * 100 : null;
        var pm = m[5].match(/([+\-−]?[\d.,]+)\s*%/); if (pm) dev = parseNum(pm[1]);
        rows.push({ metric: m[1], obs: m[2], exp: m[3], dev: dev, moved: m[4] === 'moved', share: shares[m[1]] });
      });
    }
    if (!rows.length) {
      parent = null;
      var cs = list(i.contributions).map(obj).filter(function (c) { return c.node; }), best = null;
      cs.forEach(function (c) { if (!best || (num(c.share_of_delta) || 0) > (num(best.share_of_delta) || 0)) best = c; });
      rows = cs.map(function (c) {
        var o = num(c.observed), e = num(c.expected);
        return { metric: str(c.node), obs: short(o), exp: short(e), dev: o != null && e ? (o - e) / Math.abs(e) * 100 : null,
          moved: c === best && (num(c.share_of_delta) || 0) > 0, share: num(c.share_of_delta) };
      });
    } else {      // a contribution the check did not list still belongs to the chain
      list(i.contributions).map(obj).forEach(function (c) {
        if (!c.node || rows.some(function (r) { return r.metric === c.node; })) return;
        var o = num(c.observed), e = num(c.expected);
        rows.push({ metric: str(c.node), obs: short(o), exp: short(e), dev: o != null && e ? (o - e) / Math.abs(e) * 100 : null, moved: false, share: num(c.share_of_delta) });
      });
    }
    return rows.length ? { parent: parent, rows: rows } : null;
  }
  function niceMax(v) { var steps = [10, 25, 50, 100, 200, 500]; for (var k = 0; k < steps.length; k++) if (v <= steps[k]) return steps[k]; return Math.ceil(v / 500) * 500; }
  function devBar(dev, max, cls) {
    var bar = el('div', 'dev' + (cls ? ' ' + cls : ''), [el('span', 'zero')]);
    if (num(dev) != null) {
      var w = Math.min(50, Math.abs(dev) / max * 50), fill = el('i', dev < 0 ? 'neg' : 'pos');
      fill.style.setProperty('--w', Math.max(w, 0.6) + '%');
      bar.appendChild(fill);
    }
    return bar;
  }
  function renderChain(i, ch) {
    var c = chainOf(i), outRow = { metric: str(i.metric), obs: short(ch.observed), exp: short(ch.expected), dev: num(ch.delta_pct) };
    if (!c && outRow.dev == null) return null;
    var rows = c ? c.rows : [];
    var max = niceMax(Math.max.apply(null, [5].concat(rows.concat([outRow]).map(function (r) { return Math.abs(num(r.dev) || 0); }))));
    var grid = el('div', 'chain', []);
    var line = function (r, kind) {
      var verdict;
      if (kind === 'outcome') verdict = el('span', 'verdict-text ' + toneIn(i, r.metric, r.dev), [pct(r.dev)]);
      else if (r.moved) verdict = el('span', 'verdict-text moved', ['moved ' + pct(r.dev)]);
      else verdict = el('span', 'verdict-text normal', [r.dev == null ? 'normal' : 'normal, ' + pct(r.dev)]);
      var right = [verdict];
      if (num(r.share) != null && kind !== 'outcome' && (r.moved || r.share > 0.005)) right.push(el('span', 'share', ['explains ' + fmt(r.share * 100, 0) + '%']));
      var row = el('div', 'chain-row ' + (kind || '') + (r.moved ? ' moved' : ''), [
        el('div', 'chain-name', [metricLink(r.metric)]),
        devBar(r.dev, max, kind === 'outcome' ? 'outcome' : r.moved ? 'hot' : ''),
        el('span', 'oe', [r.obs + ' vs ' + r.exp]),
        el('div', 'chain-verdict', right)]);
      row.title = human(r.metric) + ': ' + r.obs + ' observed, ' + r.exp + ' expected';
      return row;
    };
    if (outRow.dev != null) grid.appendChild(line(outRow, 'outcome'));
    if (rows.length) {
      var label = c.parent ? [c.parent === str(i.metric) ? 'Its factors' : 'Factors of ', c.parent === str(i.metric) ? null : metricLink(c.parent, human(c.parent).toLowerCase()), c.parent === str(i.metric) ? null : ', which it is made of']
        : ['Along the graph, by share of the change'];
      grid.appendChild(el('div', 'chain-group', label));
      var g = el('div', 'chain-kids', []);
      rows.forEach(function (r) { g.appendChild(line(r, 'factor')); });
      grid.appendChild(g);
    }
    grid.appendChild(el('div', 'chain-axis', [el('span', null, [signed('-' + max) + '%']), el('span', null, ['0']), el('span', null, ['+' + max + '%']),
      el('span', 'axis-k', ['observed vs expected'])]));
    return grid;
  }
  function renderBrief() {
    var b = $('brief'); b.textContent = ''; b.className = 'brief';
    var i = byId(selInc);
    if (example) b.appendChild(el('p', 'example-banner', ['Showing the contract example: there is no live incident yet.']));
    if (!i) {
      var l = inScope();
      var q = el('div', 'quiet-state', [
        el('h1', 'headline', [l.length ? 'Pick an incident on the left' : dom === 'all' ? 'Nothing needs attention' : 'Nothing touches ' + domainLabel(dom)]),
        el('p', null, [l.length ? 'Its brief opens here: what moved, the cause, the cost and how the monitor found it.'
          : 'The monitor reads every new hour of data. When a metric moves, the incident appears on the left and its brief opens here.'])]);
      if (!l.length && dom !== 'all') {
        var all = el('button', 'quiet-bench', ['Show all domains']); all.type = 'button';
        all.onclick = function () { dom = 'all'; selNode = null; var s = inScope(); selInc = s.length ? s[0].id : null; renderAll(); syncURL(); };
        q.appendChild(all);
      } else if (!incidents.length && !benchOpen()) {
        var ob = el('button', 'quiet-bench', ['Open the test bench to inject a fault']); ob.type = 'button';
        ob.onclick = function () { setBench(true, true); renderBrief(); };
        q.appendChild(ob);
      }
      b.appendChild(q);
      return;
    }
    try { briefOf(b, i); } catch (err) { b.appendChild(el('p', 'rail-empty', ['This report could not be shown: ' + err.message])); }
    var fresh = lastBrief !== selInc; lastBrief = selInc;
    if (fresh && !still) { b.classList.add('grow'); requestAnimationFrame(function () { requestAnimationFrame(function () { b.classList.remove('grow'); }); }); }
    if (fresh) $('stage').scrollTop = 0;
  }
  var lastBrief = null;
  function briefOf(b, i) {
    var st = status(i), ch = obj(i.change), w = obj(ch.window), imp = obj(i.impact), h = topHyp(i);
    b.className = 'brief ' + stOf(i);
    // --- the hero: status line, the headline sentence, the cause
    var metaLine = [el('span', 'status-tag' + (changedSt[i.id] ? ' flash' : ''), [st === 'explained' ? 'Explained' : st.charAt(0).toUpperCase() + st.slice(1)])];
    if (i.severity && st !== 'explained' && st !== 'pending') metaLine.push(el('span', 'sev ' + str(i.severity), [str(i.severity).charAt(0).toUpperCase() + str(i.severity).slice(1) + ' severity']));
    metaLine.push(el('span', null, ['First seen ' + day(i.first_seen) + ' UTC']));
    if (i.last_seen && i.last_seen !== i.first_seen) metaLine.push(el('span', null, ['last ' + day(i.last_seen)]));
    metaLine.push(el('span', 'mono id', [str(i.id)]));
    // the same fault came back: the earlier incident is one click away, on the status line
    if (i.recurrence_of) {
      var back = el('button', 'again-link', ['Back again after ' + str(i.recurrence_of) + ' →']);
      back.type = 'button'; back.title = 'Same metric and place as ' + str(i.recurrence_of) + '. Open that incident';
      back.onclick = function () { pickInc(str(i.recurrence_of)); };
      metaLine.push(back);
    }
    var hero = el('header', 'hero', [el('div', 'hero-meta', metaLine), el('h1', 'headline', [headline(i)])]);
    var inn = obj(i.innocent);
    if (h) {
      var label = st === 'explained' ? 'Explained by' : h.status === 'supported' ? 'Cause' : 'Possible cause';
      hero.appendChild(el('p', 'cause-line', [el('span', 'cause-k', [label]), el('span', 'cause-v', [causeText(h)]),
        el('span', 'cause-m', [(KIND[h.kind] ? KIND[h.kind] : str(h.kind)) + (num(h.score) != null ? ', score ' + h.score.toFixed(2) : '')])]));
    } else if (st !== 'pending') hero.appendChild(el('p', 'cause-line none', [el('span', 'cause-k', ['Cause']), el('span', 'cause-v', ['not found yet'])]));
    if (inn.reason) hero.appendChild(el('div', 'why-no-alert', [el('h2', null, ['Why no alert']), el('p', null, [str(inn.reason)])]));
    // demo only: the bench injection behind a live incident can be ended right here, next to the cause it produced
    var inj = LIVE.indexOf(st) >= 0 && !benchOpen() ? injectionFor(i) : null;      // with the bench open, its own list has the button
    if (inj) {
      var endB = el('button', 'end-go', ['End it (rollback)']); endB.type = 'button';
      endB.onclick = function () { endInjection(inj, endB); };
      hero.appendChild(el('div', 'demo-act', [el('span', 'demo-tag', ['Demo']),
        el('p', null, ['This fault came from the test bench: ', el('span', 'mono', [str(inj.kind)]), ' ' + injWhere(inj) + '. End it and watch the incident resolve.']), endB]));
    }
    b.appendChild(hero);

    // --- four big numbers, about the outcome the incident was measured on
    var effect = euroOf(i);
    if ([ch.observed, ch.expected, ch.delta_pct, effect].some(function (v) { return num(v) != null; })) {
      var fig = function (label, value, cls, unit) { return el('div', 'fig', [el('span', 'fig-l', [label]), el('span', 'fig-v ' + (cls || ''), [value, unit ? el('small', null, [unit]) : null])]); };
      b.appendChild(el('div', 'figures', [
        el('p', 'fig-cap', [metricLink(i.metric), ' ' + placeText(i.segment) + (w.start ? ', ' + day(w.start) + ' to ' + day(w.end) + ' UTC' : '')]),
        el('div', 'fig-row', [
          fig('Observed', short(ch.observed)), fig('Expected', short(ch.expected)),
          fig('Change', pct(ch.delta_pct), 'chg ' + toneIn(i, i.metric, ch.delta_pct)),
          fig('Effect', effect == null ? '–' : (effect > 0 ? '+' : '') + short(effect), 'chg ' + euroTone(i, effect), effect == null ? null : 'EUR')])]));
    } else b.appendChild(el('p', 'muted', [st === 'pending' ? 'No figures yet: the data for these hours has not arrived.' : 'No figures in this report.']));

    // --- the narration, when a model wrote it and every number in it was checked; else the monitor's summary
    var story = obj(i.narrative), told = story.source === 'model' && !!str(story.text).trim();
    if (told) {
      var asOf = Date.parse(str(story.as_of)), last = Date.parse(str(i.last_seen)), kids = [el('h2', null, ['In brief'])];
      if (isFinite(asOf) && isFinite(last) && asOf < last) kids.push(el('p', 'brief-stale', ['written at ' + ('0' + new Date(asOf).getUTCHours()).slice(-2) + ':00 UTC; the figures above are newer']));
      kids.push(el('p', 'brief-text', [str(story.text)]),
        el('p', 'brief-note', ['Written by ' + (str(story.model) || 'a language model') + ' from this report' + (story.as_of ? ' at ' + when(story.as_of) + ' UTC' : '') + '; every number in it was checked against the evidence.']));
      b.appendChild(el('section', 'in-brief', kids));
    }

    var n = 0, steps = el('div', 'steps', []);
    // 1. what moved and what stayed normal (the chain check) + what changed
    var chain = renderChain(i, ch);
    var facts = [['Metric', metricLink(i.metric, str(i.metric))], ['Segment', segText(i.segment)],
      ['Change', num(ch.delta_pct) != null ? pct(ch.delta_pct) + (num(ch.z) != null ? ' (z ' + fmt(ch.z) + ')' : '') : '–']];
    if (num(ch.delta) != null) facts.push(['Difference', fmt(ch.delta)]);
    if (w.start || w.end) facts.push(['Window', when(w.start) + ' to ' + when(w.end) + ' UTC']);
    var changed = el('dl', 'facts', []);
    facts.forEach(function (p) { changed.appendChild(el('div', null, [el('dt', null, [p[0]]), el('dd', null, [p[1]])])); });
    steps.appendChild(stepSec(++n, 'What moved, what stayed normal', 'Each factor observed against what was expected for these hours. Bars run from zero; “explains” is its share of the change.',
      (chain ? [chain] : [el('p', 'muted', ['The monitor checked no factors for this record.'])]).concat([el('h3', 'sub-h', ['What changed']), changed])));

    // 2. where in the segments
    var loc = list(i.localization).map(obj);
    if (loc.length) {
      var rowsL = loc.map(function (x) {
        var share = (num(x.share_of_delta) || 0) * 100, bar = el('span', 'sharebar', [el('i')]);
        bar.firstChild.style.setProperty('--w', Math.max(0, Math.min(100, share)) + '%');
        return el('div', 'loc' + (x.chosen ? ' chosen' : ''), [
          el('span', 'loc-place', [el('span', 'mono', [str(x.segment)]), x.chosen ? el('span', 'chosen-tag', ['chosen']) : null, el('span', 'loc-dim', [str(x.dimension)])]),
          el('span', 'loc-share', [bar, el('span', 'mono', [fmt(share, 0) + '%'])]),
          el('span', 'mono loc-sur', [num(x.surprise) != null ? signed(x.surprise.toLocaleString('en', { minimumFractionDigits: 1, maximumFractionDigits: 1 })) : ''])]);
      });
      steps.appendChild(stepSec(++n, 'Where it happened', 'Places ranked by how much of the change they hold. The chosen one is where the monitor looked for the cause.',
        [el('div', 'loc head', [el('span', null, ['Place']), el('span', null, ['Share of the change']), el('span', null, ['Surprise'])])].concat(rowsL)));
    }

    // 3. the cause: the leading explanation with its evidence, then the rivals it beat
    var hs = list(i.hypotheses).map(obj);
    if (hs.length) {
      var keep = hs.filter(function (x) { return x.status !== 'rejected'; }), out = hs.filter(function (x) { return x.status === 'rejected'; });
      var hyp = function (x, compact) {
        return el('div', 'hyp ' + str(x.status) + (compact ? ' compact' : ''), [
          el('div', 'hyp-top', [el('span', 'verdict ' + str(x.status), [str(x.status) || '–']), el('span', 'hyp-cause', [str(x.cause)])]),
          el('div', 'hyp-meta', [(x.kind ? (KIND[x.kind] || str(x.kind)) : '') + (num(x.score) != null ? (x.kind ? ' · ' : '') + 'score ' + x.score.toFixed(2) : '')]),
          list(x.evidence).length ? el('ul', 'evidence', list(x.evidence).map(function (e) { return el('li', null, [str(e)]); })) : null]);
      };
      var body = keep.map(function (x) { return hyp(x, false); });
      if (num(i.explained_share) != null && keep.length) body.unshift(el('p', 'explains', ['The leading explanation accounts for ', el('b', null, [fmt(i.explained_share * 100, 0) + '%']), ' of the change.']));
      if (out.length) body.push(el('div', 'ruled-out', [el('h3', 'sub-h', ['Ruled out (' + out.length + ')'])].concat(out.map(function (x) { return hyp(x, true); }))));
      steps.appendChild(stepSec(++n, st === 'explained' ? 'Why it is innocent' : 'The cause', 'Every explanation the monitor tested, with the evidence it weighed.', body));
    }

    // 4. the cost
    var ik = Object.keys(imp).filter(function (k) { return num(Number(imp[k])) != null; });
    if (ik.length) {
      var dec = function (s) { var p = s.indexOf('.'); return p < 0 ? 0 : s.length - p; };
      var vals = ik.map(function (k) { var v = Number(imp[k]); return fmt(v, Number.isInteger(v) ? 0 : 2); }), D = Math.max.apply(null, vals.map(dec).concat(0));
      var cost = el('div', 'cost', []);
      ik.forEach(function (k, j) {
        var eur = /_eur$/.test(k), v = Number(imp[k]);
        cost.appendChild(el('div', 'cost-row' + (k === 'contribution_eur' ? ' main' : ''), [
          el('span', 'cost-k', [human(k.replace(/_eur$/, '')), el('span', 'mono key', [k])]),
          el('span', 'cost-v mono ' + (eur ? euroTone(i, v) : ''), [(v > 0 ? '+' : '') + vals[j] + ' '.repeat(D - dec(vals[j])), el('small', null, [eur ? ' EUR' : ''])])]));
      });
      steps.appendChild(stepSec(++n, 'The cost', st === 'explained' ? 'What the change was worth while it lasted.' : 'Lost so far, in ' + placeText(i.segment).replace(/^in /, '') + '.', [cost]));
    }

    // 5. still uncertain
    if (list(i.uncertain).length) steps.appendChild(stepSec(++n, 'Still uncertain', 'What the evidence does not settle yet.',
      [el('ul', 'uncertain', i.uncertain.map(function (u) { return el('li', null, [str(u)]); }))]));

    // 6. the trail
    var VERB = { watch: 'Watched', compare: 'Compared', drill: 'Drilled down', drill_down: 'Drilled down', decompose: 'Decomposed', check: 'Checked',
      check_context: 'Checked context', detect: 'Detected', stop: 'Stopped' };
    var tr = list(i.trail).map(function (s, k) {
      s = obj(s);
      return el('li', 'tstep' + (s.action === 'stop' ? ' last' : ''), [el('span', 'tnum', [String(k + 1)]),
        el('div', 'tbody', [el('div', 'thead', [el('span', 'tverb', [VERB[s.action] || str(s.action)]), s.target ? el('span', 'ttarget mono', [str(s.target)]) : null]),
          el('p', 'tresult', [str(s.result)])])]);
    });
    if (tr.length) steps.appendChild(stepSec(++n, 'How it was found', tr.length + ' steps of the investigation, in order.', [el('ol', 'trail', tr)]));
    if (i.report) steps.appendChild(stepSec(++n, 'Summary', 'The whole report in the monitor’s own words.', [el('p', 'summary', [str(i.report)])]));
    b.appendChild(steps);
  }

  // ------------------------------------------------------------ the metric panel (graph view)
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
    var cause = function (e, other) { return e[other] + ' ' + (e.sign > 0 ? '+' : e.sign < 0 ? '−' : '?') + (num(e.prior) != null ? ', prior ' + e.prior : '') + (e.status ? ', ' + e.status : '') + (e.note ? ': ' + e.note : ''); };
    var affects = by('may_affect').filter(function (e) { return e.source === m; }).map(function (e) { return cause(e, 'target'); });
    if (affects.length) out.push(['May affect', affects.join('; ')]);
    var affected = by('may_affect').filter(function (e) { return e.target === m; }).map(function (e) { return cause(e, 'source'); });
    if (affected.length) out.push(['May be affected by', affected.join('; ')]);
    return out;
  }
  function kv(pairs) { var d = el('dl', 'kv', []); pairs.forEach(function (p) { d.appendChild(el('div', null, [el('dt', null, [p[0]]), el('dd', null, [p[1]])])); }); return d; }
  function mono(s) { return el('span', 'mono', [str(s)]); }
  // the extension agent's answer to a contributed metric (<name>.result.json): its message and every check it ran
  var answers = {};
  function checksList(a) {
    var rows = list(a.checks).map(function (c) {
      c = obj(c); var s = str(c.status);
      return el('li', 'check', [el('span', 'verdict ' + s, [s || '–']), el('div', null, [el('div', 'check-name', [str(c.check)]), el('div', 'check-detail', [str(c.detail)])])]);
    });
    return rows.length ? el('ul', 'checks', rows) : el('p', 'muted', ['The answer lists no checks.']);
  }
  function loadAnswer(name) {
    return get('/api/metric/' + encodeURIComponent(name)).then(function (r) { answers[name] = r; if (selNode === name) renderMetricPane(); return r; });
  }
  function renderMetricPane() {
    var d = $('metric'); d.textContent = '';
    $('view-graph').classList.toggle('has-metric', !!selNode);
    requestAnimationFrame(fitGraph);
    try {
      if (selNode) return metricOf(d, selNode);
      var i = byId(selInc);
      if (i) {
        d.appendChild(el('p', 'pane-k', ['Lit in the graph']));
        d.appendChild(el('h2', 'pane-title ' + stOf(i), [headline(i)]));
        d.appendChild(el('p', 'pane-text', ['The path from the rate that moved up to the outcome is drawn in the incident’s status colour. Click any box for its definition, breakdown and links.']));
        var go = el('button', 'primary', ['Read the brief']); go.type = 'button'; go.onclick = function () { setView('brief'); };
        d.appendChild(go);
        return;
      }
      d.appendChild(el('p', 'pane-k', ['Metric details']));
      d.appendChild(el('p', 'pane-text', ['Click a box in the graph for its definition, status, origin and links.']));
    } catch (err) { d.textContent = ''; d.appendChild(el('p', 'muted', ['This could not be shown: ' + err.message])); }
  }
  function metricOf(d, name) {
    var m = meta[name], a = answers[name];
    var close = el('button', 'icon-btn pane-close', ['×']); close.type = 'button'; close.setAttribute('aria-label', 'Close the metric details');
    close.onclick = function () { selNode = null; renderMetricPane(); applyStyles(); };
    d.appendChild(el('div', 'pane-headrow', [el('p', 'pane-k', ['Metric']), close]));
    d.appendChild(el('h2', 'pane-title', [human(name)]));
    d.appendChild(el('p', 'pane-id mono', [name]));
    if (!m) {      // not in the graph: the answer that refused it (an alias, a failed check), when there is one
      if (a) { d.appendChild(el('p', 'pane-status', [el('span', 'verdict ' + str(a.status), [str(a.status)])])); d.appendChild(el('p', 'pane-text', [str(a.message)])); d.appendChild(el('h3', 'sub-h', ['Checks of the extension agent'])); d.appendChild(checksList(a)); }
      else { d.appendChild(el('p', 'muted', ['This metric is not in the graph.'])); loadAnswer(name).catch(function () {}); }
      return;
    }
    d.appendChild(el('p', 'pane-text', [m.description || 'No description.']));
    var pairs = [['Domain', domainLabel(domainOf(name))], ['Status', m.status || '–'], ['Origin', m.origin || '–'], ['Unit', m.unit || '–'], ['Direction', m.direction || '–']];
    if (m.formula) pairs.push(['Formula', mono(m.formula)]);
    if (m.decomposition) pairs.push(['Made of', mono(m.decomposition)]);
    if (m.column) pairs.push(['Column', mono(m.column)]);
    if (list(m.systems).length) pairs.push(['Systems', m.systems.join(', ')]);
    if (m.source) pairs.push(['Data source', m.source]);
    pairs.push(['Dimensions', list(m.dimensions).join(', ') || '–']);
    d.appendChild(kv(pairs));
    var links = linksOf(name);
    d.appendChild(el('h3', 'sub-h', ['Links']));
    d.appendChild(links.length ? kv(links) : el('p', 'muted', ['No links recorded for this metric.']));
    if (a) { d.appendChild(el('h3', 'sub-h', ['Checks of the extension agent'])); d.appendChild(el('p', 'pane-text', [str(a.message)])); d.appendChild(checksList(a)); }
    else if (m.origin === 'contributed') loadAnswer(name).catch(function () {});
    var inc = incidents.filter(function (i) { return incidentNodes(i).indexOf(name) >= 0; });
    d.appendChild(el('h3', 'sub-h', ['Incidents involving it']));
    if (!inc.length) d.appendChild(el('p', 'muted', ['No incident involves this metric.']));
    inc.forEach(function (i) {
      var b = el('button', 'involved ' + stOf(i), [el('span', 'dot'), headline(i), el('span', 'mono muted', [' ' + str(i.id)])]); b.type = 'button';
      b.onclick = function () { pickInc(i.id); setView('brief'); };
      d.appendChild(b);
    });
  }

  // ------------------------------------------------------------ the graph (layout as in the reference console)
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
      var st = status(i); if (!rank[st]) return;                  // explained and pending do not colour the graph
      incidentNodes(i).forEach(function (m) { if (!h[m] || rank[st] > rank[h[m]]) h[m] = st; });
    });
    return h;
  }
  function drawGraph() {
    $('graph-title').textContent = dom === 'all' ? 'How the business works' : 'How the business works: ' + domainLabel(dom);
    var wrap = $('graphwrap');
    Array.prototype.slice.call(wrap.querySelectorAll('.empty')).forEach(function (e) { wrap.removeChild(e); });
    var g = $('g'); g.textContent = '';
    drawn = { edges: [], nodes: [] }; traceKey = '';
    var roots = list(graph && graph.trees).map(filterTree).filter(Boolean);
    if (!roots.length) { wrap.appendChild(el('p', 'empty', ['No formula tree for this domain.'])); renderExtra(null); return; }
    var cols = columns(roots), all = { nodes: [], edges: [], w: 0, h: 0 };
    roots.forEach(function (r) { var L = layout(r, all.h, cols); all.nodes = all.nodes.concat(L.nodes); all.edges = all.edges.concat(L.edges); all.w = Math.max(all.w, L.w); all.h += L.h + 8; });
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
      var pick = function () { selNode = i.n.metric; renderMetricPane(); applyStyles(); };
      gr.addEventListener('click', pick);
      gr.addEventListener('keydown', function (e) { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); pick(); } });
      drawn.nodes.push({ el: gr, metric: i.n.metric, item: i });
    });
    // possible causes: dashed lines with the direction of the effect, when both ends are on screen
    list(graph && graph.edges).filter(function (e) { return e.kind === 'may_affect' && pos[e.source] && pos[e.target]; }).forEach(function (e) {
      var a = pos[e.source], b = pos[e.target], mx = (a.x + b.x) / 2 + 40, my = (a.y + b.y) / 2;
      svg('path', { d: 'M' + (a.x + a.hw) + ',' + a.y + ' Q' + (mx + 40) + ',' + my + ' ' + (b.x + b.hw) + ',' + b.y, 'class': 'edge may' }, ge);
      var sg = svg('g', { 'class': 'sign', transform: 'translate(' + (mx + (a.hw + b.hw) / 4) + ',' + my + ')' }, ge);
      svg('circle', { r: 9 }, sg); var s = svg('text', { dy: '0.35em' }, sg); s.textContent = e.sign > 0 ? '+' : e.sign < 0 ? '−' : '?';
      svg('title', {}, sg).textContent = e.source + ' may ' + (e.sign > 0 ? 'raise ' : e.sign < 0 ? 'lower ' : 'change ') + e.target + (e.note ? ': ' + e.note : '');
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
    x.appendChild(el('span', 'extra-k', [dom === 'all' ? 'Not on a formula tree' : 'Also in this domain, not on a formula tree']));
    rest.forEach(function (m) { var b = el('button', 'extra-m' + (selNode === m ? ' on' : ''), [m]); b.type = 'button'; b.onclick = function () { selNode = m; renderMetricPane(); applyStyles(); renderExtra(shown); }; x.appendChild(b); });
  }
  function applyStyles() {
    if (!drawn || !drawn.layer) return;
    var h = health(), inc = byId(selInc), on = {};
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
  // the box the change is attributed to (the largest share along the graph), else the leading driver, else the metric
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
  // The graph fits its pane: as wide as the pane allows (at most 1.3 times its own size) and no taller than the pane,
  // so the whole tree is in view; below about 9 px names the pane scrolls instead.
  function fitGraph() {
    var g = $('g'), wrap = $('graphwrap'), v = g.viewBox && g.viewBox.baseVal;
    if (!drawn || !v || !v.width || !v.height || !wrap.clientWidth) return;
    drawn.box = { w: v.width, h: v.height };
    var cs = getComputedStyle(wrap), w = wrap.clientWidth - parseFloat(cs.paddingLeft) - parseFloat(cs.paddingRight);
    var hh = wrap.clientHeight - parseFloat(cs.paddingTop) - parseFloat(cs.paddingBottom) - 2;
    if (hh > 120) w = Math.min(w, hh * v.width / v.height);
    g.style.width = Math.floor(Math.max(v.width * 0.68, Math.min(w, v.width * 1.3))) + 'px';
  }
  // The trace: when the graph is shown with an incident selected, its path is drawn once in the status colour, from
  // the moved rate up to the outcome. Polls and redraws do not replay it.
  var traceKey = '', tracing = null;
  function trace(inc) {
    var key = inc && view === 'graph' ? str(inc.id) + '|' + dom : '';
    if (key === traceKey) return;
    traceKey = key;
    var g = $('g'), layer = drawn.layer;
    tracing = null; layer.textContent = ''; g.classList.remove('tracing');
    var lit = drawn.edges.filter(function (e) { return e.el.classList.contains('on'); });
    if (!key || still || !lit.length || !window.requestAnimationFrame) return;
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

  // ------------------------------------------------------------ selection and views
  function syncURL() {
    try {
      var p = new URLSearchParams(location.search);
      if (selInc) p.set('incident', selInc); else p.delete('incident');
      if (view === 'graph') p.set('view', 'graph'); else p.delete('view');
      history.replaceState(null, '', location.pathname + (p.toString() ? '?' + p.toString() : ''));
    } catch (e) { /* a sandboxed preview may refuse it */ }
  }
  function pickInc(id) { selInc = id; selNode = null; renderFeed(); renderBrief(); renderMetricPane(); applyStyles(); syncURL(); }
  function showMetric(m) { selNode = m; setView('graph'); renderMetricPane(); applyStyles(); }
  function setView(v) {
    view = v;
    $('view-brief').hidden = v !== 'brief'; $('view-graph').hidden = v !== 'graph';
    $('shell').classList.toggle('graph-view', v === 'graph');
    [['tab-brief', 'brief'], ['tab-graph', 'graph']].forEach(function (t) {
      var b = $(t[0]); b.setAttribute('aria-selected', t[1] === v ? 'true' : 'false'); b.tabIndex = t[1] === v ? 0 : -1;
    });
    if (v === 'graph') { fitGraph(); applyStyles(); }
    syncURL();
  }
  $('tab-brief').onclick = function () { setView('brief'); };
  $('tab-graph').onclick = function () { setView('graph'); };
  $('tab-brief').parentNode.addEventListener('keydown', function (e) {
    if (e.key !== 'ArrowLeft' && e.key !== 'ArrowRight') return;
    var v = view === 'brief' ? 'graph' : 'brief'; setView(v); $(v === 'brief' ? 'tab-brief' : 'tab-graph').focus();
  });

  function renderAll() { renderChips(); drawGraph(); renderFeed(); renderBrief(); renderMetricPane(); }

  // ------------------------------------------------------------ loading
  function takeGraph(g) {
    graph = g; meta = {}; domOf = {}; answers = {};
    list(g.nodes).forEach(function (n) { if (n.kind === 'metric') meta[n.id] = n; });
    domains.forEach(function (d) { list(d.metrics).forEach(function (m) { domOf[m] = d.id; }); });
  }
  function incidentSignature() { return JSON.stringify([incidents, skipped]); }
  var wallMode = false, clockFirst = null, accelerated = false, HOUR_MS = 3600000;
  // Live: one data hour per real hour. Simulated, accelerated: the data clock runs ahead of real time. Replay: a
  // finished database read as it is
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
  // bench times, in Berlin like the clock above: "Thu 26 Nov, 08:00 Berlin"
  function displayHour(ts) {
    var d = new Date(str(ts)); if (!isFinite(d)) return str(ts);
    var p = {};
    new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Berlin', weekday: 'short', day: 'numeric', month: 'short', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
      .formatToParts(d).forEach(function (x) { p[x.type] = x.value; });
    return p.weekday + ' ' + p.day + ' ' + p.month + ', ' + p.hour + ':' + p.minute + ' Berlin';
  }
  function clockParts(ts) {
    var p = {};
    new Intl.DateTimeFormat('en-GB', { timeZone: 'Europe/Berlin', weekday: 'short', day: '2-digit', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', hourCycle: 'h23' })
      .formatToParts(new Date(ts)).forEach(function (x) { p[x.type] = x.value; });
    return { day: p.weekday + ' ' + p.day + ' ' + p.month + ' ' + p.year, hour: p.hour + ':' + p.minute };
  }
  function refresh() {
    get('/api/state').then(function (s) {
      wallMode = s.clock_mode === 'wall';
      var c = $('clock'), parts = s.ready ? clockParts(s.last_hour) : null;
      var label = s.ready ? clockMode(s) : (wallMode ? 'Live' : 'Simulated');
      var m = $('clock-mode'); m.textContent = label; m.className = 'clock-mode' + (wallMode ? ' live' : label === 'Replay' ? ' replay' : '');
      m.title = wallMode ? 'Current hour: one data hour per real hour' : label === 'Replay' ? 'A finished database of simulated data, read hour by hour'
        : 'Simulated data: the simulator writes an hour of data every few seconds, faster than real time';
      c.textContent = '';
      if (parts) { c.appendChild(el('span', 'clock-date', [parts.day])); c.appendChild(el('span', 'clock-hour', [parts.hour])); c.appendChild(el('span', 'clock-tz', ['Berlin'])); }
      else c.textContent = str(s.error);
      fillEnd(list(s.injected).filter(function (i) { return i.active; }));
    }).catch(function (e) { $('clock').textContent = 'Clock unavailable: ' + e.message; });
    get('/api/graph').then(function (g) {
      var k = JSON.stringify(g);
      if (k !== graphKey) { graphKey = k; takeGraph(g); var back = focusMemo(); try { renderAll(); } catch (e) { note('Could not draw the graph: ' + e.message); } back(); }
    }).catch(function () {});
    get('/api/incidents').then(function (r) {
      var before = incidentSignature();
      incidents = list(r.incidents); skipped = r.skipped || 0;
      var flipped = example && !r.example; example = !!r.example;
      // new incidents slide into the rail once; the first load only records what is there
      var ids = incidents.map(function (i) { return str(i.id); }), fresh = [];
      if (seen) ids.forEach(function (id) { if (!seen[id]) fresh.push(id); });
      seen = {}; ids.forEach(function (id) { seen[id] = true; });
      arrived = {}; fresh.forEach(function (id) { arrived[id] = true; });
      // a live incident that the monitor closed: say so once, and wash its card and its status tag
      var closed = [];
      changedSt = {};
      incidents.forEach(function (i) {
        var was = lastSt[i.id];
        if (was && was !== status(i)) { changedSt[i.id] = true; arrived[i.id] = true; if (LIVE.indexOf(was) >= 0 && status(i) === 'resolved') closed.push(i.id); }
      });
      lastSt = {}; incidents.forEach(function (i) { lastSt[i.id] = status(i); });
      $('exbadge').textContent = '';
      if (example) $('exbadge').appendChild(el('p', 'example-pill', ['Contract example: no live incident yet']));
      var scope = inScope();
      if (flipped || !incidents.some(function (i) { return i.id === selInc; })) selInc = scope.length ? scope[0].id : null;
      note('');
      if (before !== incidentSignature() || flipped) {
        var back = focusMemo();
        try { renderChips(); renderFeed(); renderBrief(); renderMetricPane(); applyStyles(); } catch (e) { note('Could not draw the incidents: ' + e.message); }
        back();
        fresh.forEach(function (id) {
          var i = byId(id); if (!i || LIVE.indexOf(status(i)) < 0) return;
          toast('New incident: ' + headline(i) + '.', [['Open it', function () { pickInc(id); setView('brief'); }]]);
        });
        closed.forEach(function (id) {
          var i = byId(id); if (!i) return;
          var msg = 'Resolved: ' + headline(i) + '. The rate is back to normal.';
          logLine(msg, null, 'ok');
          toast(msg, id === selInc ? null : [['Open it', function () { pickInc(id); setView('brief'); }]]);
        });
      }
    }).catch(function () { note('Could not read the incidents.'); });
  }

  // ------------------------------------------------------------ the test bench
  // the faults the bench can write into the simulator: kind, label, settings it uses, hours it lasts unless someone
  // ends it, and its severity when it takes one (the same requests as the reference console)
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
  function fill(id, values) { var s = $(id); values.forEach(function (v) { var o = document.createElement('option'); o.value = v; o.textContent = v; s.appendChild(o); }); }
  KINDS.forEach(function (k) { var o = document.createElement('option'); o.value = k[0]; o.textContent = k[1]; $('f-kind').appendChild(o); });
  fill('f-country', ['DE', 'FR', 'ES', 'PL']); fill('f-platform', ['Android', 'iOS', 'Web']); fill('f-category', ['electronics', 'fashion', 'home', 'beauty', 'sports']);
  fill('pr-country', ['FR', 'DE', 'ES', 'PL']); fill('ms-category', ['fashion', 'electronics', 'home', 'beauty', 'sports']); fill('dd-source', ['payments', 'orders', 'traffic', 'search', 'recommendations', 'ads', 'inventory']);
  function showParams() {
    var need = KINDS.filter(function (k) { return k[0] === $('f-kind').value; })[0][2];
    ['country', 'platform', 'category'].forEach(function (p) { $('w-' + p).hidden = need.indexOf(p) < 0; });
    $('f-go').textContent = 'Inject ' + KINDS.filter(function (k) { return k[0] === $('f-kind').value; })[0][1].toLowerCase();
  }
  $('f-kind').onchange = showParams; showParams();

  // the answers log in the bench, and a toast that shows the newest line for a few seconds
  var toastTimer = null;
  function logLine(msg, extra, cls) {
    var log = $('log'), empty = log.querySelector('.log-empty'); if (empty) log.removeChild(empty);
    var li = el('li', 'log-item' + (cls ? ' ' + cls : ''), [el('span', 'log-time', [new Date().toTimeString().slice(0, 5)]), el('div', 'log-msg', [msg].concat(extra || []))]);
    log.insertBefore(li, log.firstChild);
    while (log.children.length > 12) log.removeChild(log.lastChild);
    return li;
  }
  function toast(msg, actions) {
    var t = $('toast'); t.textContent = '';
    t.appendChild(el('span', 'toast-msg', [msg]));
    (actions || []).forEach(function (a) { var b = el('button', 'linkish', [a[0]]); b.type = 'button'; b.onclick = function () { a[1](); t.hidden = true; }; t.appendChild(b); });
    t.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { t.hidden = true; }, actions && actions.length ? 9000 : 6000);
  }
  function say(msg, cls) { logLine(msg, null, cls); if (!benchOpen()) toast(msg); }

  // injections still in effect, so a demo can end one (the rollback)
  var endKey = '', active = [];
  function injWhere(i) {
    var where = ['country', 'platform', 'category', 'source'].map(function (d) { return obj(i.scope)[d]; }).filter(Boolean).join(' · ');
    return (where || 'everywhere') + ', from ' + displayHour(obj(i.scope).start);
  }
  function endInjection(i, b) {
    b.disabled = true; say('Ending ' + i.kind + '…');
    post('/api/end', { id: parseInt(i.id, 10) }).then(function (r) {
      say('Ended ' + i.kind + ' from ' + displayHour(r.ended_from) + '. The monitor will see the rate come back and resolve the incident.', 'ok'); endKey = ''; refresh();
    }).catch(function (e) { b.disabled = false; say('Failed: ' + e.message, 'bad'); });
  }
  // the bench fault behind a live incident: same place (no dimension in conflict, at least one shared), started before
  // the incident was last seen; the closest match, newest first
  function injectionFor(inc) {
    var seg = obj(inc.segment), last = Date.parse(str(inc.last_seen)), best = null, bestN = -1;
    active.forEach(function (j) {
      if (!KINDS.some(function (k) { return k[0] === j.kind; })) return;
      var sc = obj(j.scope), n = 0, clash = false;
      ['country', 'platform', 'category'].forEach(function (d) { if (!sc[d]) return; if (seg[d] === sc[d]) n++; else if (seg[d]) clash = true; });
      if (clash || isFinite(last) && Date.parse(str(sc.start)) > last) return;
      if (!n && ['country', 'platform', 'category'].some(function (d) { return sc[d]; })) return;
      if (n > bestN || (n === bestN && Date.parse(str(sc.start)) > Date.parse(str(obj(best.scope).start)))) { best = j; bestN = n; }
    });
    return best;
  }
  function fillEnd(l) {
    var key = JSON.stringify(l.map(function (i) { return i.id; }));
    $('end-grp').hidden = !l.length;
    if (key === endKey) return;
    endKey = key; active = l;
    var ul = $('end-list'); ul.textContent = '';
    l.forEach(function (i) {
      var b = el('button', 'end-go', ['End it (rollback)']); b.type = 'button';
      b.onclick = function () { endInjection(i, b); };
      ul.appendChild(el('li', null, [el('div', 'end-what', [el('span', 'mono', [str(i.kind)]), el('span', 'end-where', [injWhere(i)])]), b]));
    });
    var back = focusMemo();
    try { renderBrief(); } catch (e) { /* the brief redraws on the next poll */ }
    back();
  }
  function act(body, label) {
    say(label + '…');
    post('/api/inject', body).then(function (r) { say(label + ' from ' + displayHour(r.start) + '. Injected into the data; the monitor has to find it.', 'ok'); refresh(); })
      .catch(function (e) { say('Failed: ' + e.message, 'bad'); });
  }
  $('f-go').onclick = function () {
    var kind = $('f-kind').value, label = KINDS.filter(function (k) { return k[0] === kind; })[0][1];
    act(faultBody(kind, function (p) { return $('f-' + p).value; }), label + ' injected');
  };
  $('pr-go').onclick = function () { act({ kind: 'promo', country: $('pr-country').value, hours: 12, when: 'now' }, 'Promo started'); };
  $('ms-go').onclick = function () { act(mixShiftBody($('ms-category').value), 'Mix shift started'); };
  $('dd-go').onclick = function () { act({ kind: 'data_delay', source: $('dd-source').value, hours: 3, delay_hours: 2, when: 'now' }, 'Late data scheduled'); };
  // adding a metric: hand it over, then wait for the monitor's answer (<name>.result.json) with its checks
  function answerLine(res) {
    res = obj(res); var name = str(res.name);
    if (name) answers[name] = res;
    var extra = [];
    if (list(res.checks).length) {
      var det = el('details', 'log-checks', [el('summary', null, [list(res.checks).length + ' checks']), checksList(res)]);
      extra.push(det);
    }
    var ok = /accept|ok|added|placed|valid/i.test(str(res.status));
    if (name) { var b = el('button', 'linkish', [ok ? 'Show ' + name + ' in the graph' : 'Show why ' + name + ' was refused']); b.type = 'button'; b.onclick = function () { showMetric(name); }; extra.push(b); }
    logLine(el('span', null, [el('span', 'verdict ' + str(res.status), [str(res.status) || 'answer']), ' ', str(res.message) || 'Monitor: ' + str(res.status)]), extra, ok ? 'ok' : '');
    if (!benchOpen()) toast(str(res.message) || 'Monitor: ' + str(res.status), name ? [['Show checks', function () { showMetric(name); }]] : null);
  }
  function handOver(yamlText) {
    post('/api/metric', { yaml_text: yamlText }).then(function (r) {
      say('Handed to the monitor: ' + r.saved + '. Waiting for its answer…');
      var tries = 0;
      var poll = function () {
        get('/api/metric/' + encodeURIComponent(r.name)).then(function (res) { answerLine(res); refresh(); })
          .catch(function () { if (++tries < 20) setTimeout(poll, 2000); else say('Handed to the monitor; it has not answered yet.'); });
      };
      setTimeout(poll, 1500);
    }).catch(function (e) { say('Failed: ' + e.message, 'bad'); });
  }
  // a definition and a query, no formula: the agent finds the formula itself, checks it and places the metric
  $('nm-go').onclick = function () {
    var y = 'name: refund_rate\ndescription: Share of orders refunded\nsql: >\n  SELECT ts, country, platform, category, channel,\n         SUM(refunds) * 1.0 / NULLIF(SUM(orders), 0) AS value\n  FROM facts_hourly GROUP BY ts, country, platform, category, channel\ndimensions: [country, platform, category, channel]\nunit: ratio\n';
    handOver(y);
  };
  // an existing rate (payment_success_rate) under another name: the agent refuses it as an alias and says of what
  $('nm-dup').onclick = function () {
    var dup = 'name: payment_rate\ndescription: Successful payments per payment attempt\nsql: >\n  SELECT ts, country, platform, category, channel,\n         SUM(payment_successes) * 1.0 / NULLIF(SUM(payment_attempts), 0) AS value\n  FROM facts_hourly GROUP BY ts, country, platform, category, channel\ndimensions: [country, platform, category, channel]\nunit: ratio\n';
    handOver(dup);
  };

  // the bench opens as a third column, so the rail and the brief stay in view while faults go in
  function benchOpen() { return !$('bench').hidden; }
  function setBench(open, focus) {
    $('bench').hidden = !open; $('shell').classList.toggle('bench-open', open);
    $('bench-toggle').setAttribute('aria-expanded', open ? 'true' : 'false');
    $('bench-toggle').classList.toggle('on', open);
    store('v2-bench', open ? '1' : '0');
    if (open) $('toast').hidden = true;
    try { renderBrief(); } catch (e) { /* drawn again on the next poll */ }
    if (focus) (open ? $('f-kind') : $('bench-toggle')).focus();
    requestAnimationFrame(fitGraph);
  }
  $('bench-toggle').onclick = function () { setBench(!benchOpen(), true); };
  $('bench-close').onclick = function () { setBench(false, true); };
  document.addEventListener('keydown', function (e) { if (e.key === 'Escape' && benchOpen() && $('bench').contains(document.activeElement)) setBench(false, true); });

  // ------------------------------------------------------------ start
  // ?incident=<id> opens that brief; ?view=graph opens the graph; ?bench=open opens the test bench
  var asked = params.get('incident'); if (asked) selInc = asked;
  if (params.get('view') === 'graph') setView('graph');
  if (params.get('metric')) { selNode = params.get('metric'); setView('graph'); }
  setBench(params.get('bench') === 'open' || (params.get('bench') !== 'closed' && store('v2-bench') === '1'), false);
  function setMast() { document.documentElement.style.setProperty('--mast-h', $('masthead').offsetHeight + 'px'); }
  setMast();
  window.addEventListener('resize', function () { setMast(); fitGraph(); });
  if (window.ResizeObserver) new ResizeObserver(function () { fitGraph(); }).observe($('graphwrap'));
  renderBrief(); renderMetricPane();
  get('/api/domains').then(function (d) { domains = list(d); }).catch(function () {}).then(function () { refresh(); setInterval(refresh, 2000); });
})();
