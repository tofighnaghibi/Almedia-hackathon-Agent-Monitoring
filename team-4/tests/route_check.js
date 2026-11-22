// Runs ui/static/v3/route.js under node for tests/test_route.py and prints what the tests check, as JSON.
// Input on stdin: { cases: { name: { boxes: [{id, x, y, hw, hh}], links: [{from, to, kind}], crossings: bool } },
//                   random: { seed, count }, speed: { links, boxes, runs } }   (link ends are indexes into boxes)
// Output: for each case, one entry per link (its geometry, today's formula path, and the faults found); the faults
// found over the random placements; whether two runs agree; and how long a layout takes.
'use strict';
var path = require('path');
var R = require(path.join(__dirname, '..', 'ui', 'static', 'v3', 'route.js'));

// the formula link as route.js drew it before links could pick their sides: the d string the untouched layout keeps
function todayD(a, b) {
  var r1 = function (v) { return Math.round(v * 10) / 10; };
  var sx = a.x + a.hw, sy = a.y, ex = b.x - b.hw, ey = b.y, c = (ex - sx) / 2;
  return 'M' + r1(sx) + ',' + r1(sy) + ' C' + r1(sx + c) + ',' + r1(sy) + ' ' + r1(ex - c) + ',' + r1(ey) + ' ' + r1(ex) + ',' + r1(ey);
}
function inside(b, x, y, grow) { return Math.abs(x - b.x) < b.hw + grow && Math.abs(y - b.y) < b.hh + grow; }
var SAMPLES = 400;
function samples(g) {
  var out = [];
  for (var k = 1; k < SAMPLES; k++) out.push(R.point(g.p, k / SAMPLES));
  return out;
}
function finite(g) {
  var nums = [];
  g.p.forEach(function (q) { nums.push(q[0], q[1]); });
  ['x', 'y', 'nx', 'ny'].forEach(function (k) { nums.push(g.start[k], g.end[k]); });
  ['x', 'y', 'tx', 'ty'].forEach(function (k) { nums.push(g.mid[k]); });
  return nums.every(function (v) { return typeof v === 'number' && isFinite(v); });
}
// a side normal that does not face the other end: the link leaves (or arrives) away from where it goes
function hooked(g) {
  return !(g.start.nx * (g.end.x - g.start.x) + g.start.ny * (g.end.y - g.start.y) > 0 &&
           g.end.nx * (g.start.x - g.end.x) + g.end.ny * (g.start.y - g.end.y) > 0);
}
// whether an end lies on the side it names, within that side's length
function onSide(box, end) {
  var e = 1e-6;
  if (end.side === 'l' || end.side === 'r') return Math.abs(end.x - (box.x + (end.side === 'r' ? box.hw : -box.hw))) < e && Math.abs(end.y - box.y) <= box.hh + e;
  return Math.abs(end.y - (box.y + (end.side === 'b' ? box.hh : -box.hh))) < e && Math.abs(end.x - box.x) <= box.hw + e;
}
// samples strictly inside the link's own boxes (shrunk by half a unit, so its ends on their edges do not count)
function ownSamples(g, a, b) {
  return samples(g).filter(function (q) { return inside(a, q[0], q[1], -0.5) || inside(b, q[0], q[1], -0.5); }).length;
}
function segCross(p, q, r, s) {
  var d = (q[0] - p[0]) * (s[1] - r[1]) - (q[1] - p[1]) * (s[0] - r[0]);
  if (d === 0) return false;
  var t = ((r[0] - p[0]) * (s[1] - r[1]) - (r[1] - p[1]) * (s[0] - r[0])) / d;
  var u = ((r[0] - p[0]) * (q[1] - p[1]) - (r[1] - p[1]) * (q[0] - p[0])) / d;
  return t > 0 && t < 1 && u > 0 && u < 1;
}
// whether two curves cross anywhere but within 2 units of an end they share
function curvesCross(g, h) {
  var A = [[g.start.x, g.start.y]].concat(samples(g), [[g.end.x, g.end.y]]);
  var B = [[h.start.x, h.start.y]].concat(samples(h), [[h.end.x, h.end.y]]);
  var ends = [[g.start.x, g.start.y], [g.end.x, g.end.y]];
  var near = function (q) { return ends.some(function (e) { return Math.abs(e[0] - q[0]) < 2 && Math.abs(e[1] - q[1]) < 2; }); };
  for (var i = 1; i < A.length; i += 1) {
    for (var j = 1; j < B.length; j += 1) {
      if (segCross(A[i - 1], A[i], B[j - 1], B[j]) && !near(A[i])) return true;
    }
  }
  return false;
}

function runCase(c) {
  var links = c.links.map(function (l) { return { from: c.boxes[l.from], to: c.boxes[l.to], kind: l.kind }; });
  var geo = R.layout(links, c.boxes);
  var out = geo.map(function (g, i) {
    var l = links[i], pts = samples(g);
    var through = c.boxes.filter(function (b) {
      return b !== l.from && b !== l.to && pts.some(function (q) { return inside(b, q[0], q[1], 0); });
    }).map(function (b) { return b.id; });
    var xs = pts.map(function (q) { return q[0]; });
    return {
      kind: l.kind, from: l.from.id, to: l.to.id, d: g.d, p: g.p, start: g.start, end: g.end, mid: g.mid,
      today: l.kind === 'tree' ? todayD(l.from, l.to) : null, finite: finite(g), hooked: hooked(g),
      onSides: onSide(l.from, g.start) && onSide(l.to, g.end), own: ownSamples(g, l.from, l.to), through: through,
      xmin: Math.min.apply(null, xs), xmax: Math.max.apply(null, xs)
    };
  });
  var result = { count: geo.length, links: out };
  if (c.crossings) {
    result.crossings = [];
    for (var i = 0; i < geo.length; i++) for (var j = i + 1; j < geo.length; j++) if (curvesCross(geo[i], geo[j])) result.crossings.push([i, j]);
  }
  return result;
}

// a seeded generator, so the random placements are the same on every run
function rng(seed) {
  return function () {
    seed |= 0; seed = seed + 0x6D2B79F5 | 0;
    var t = Math.imul(seed ^ seed >>> 15, 1 | seed);
    t = t + Math.imul(t ^ t >>> 7, 61 | t) ^ t;
    return ((t ^ t >>> 14) >>> 0) / 4294967296;
  };
}
function apart(a, b) { return Math.abs(a.x - b.x) >= a.hw + b.hw + 1 || Math.abs(a.y - b.y) >= a.hh + b.hh + 1; }
function randomBoxes(r, n, w, h) {
  var boxes = [];
  for (var tries = 0; boxes.length < n && tries < 500; tries++) {
    var b = { id: 'b' + boxes.length, x: r() * w, y: r() * h, hw: 45 + r() * 95, hh: 14 + r() * 9 };
    if (boxes.every(function (o) { return apart(o, b); })) boxes.push(b);
  }
  return boxes;
}
// a few hundred placements of 2 to 6 boxes that do not overlap, with links among them of both kinds
function runRandom(spec) {
  var r = rng(spec.seed), faults = { hooks: [], own: [], shape: [] }, total = 0;
  for (var n = 0; n < spec.count; n++) {
    var boxes = randomBoxes(r, 2 + Math.floor(r() * 5), 1400, 900), links = [];
    for (var k = 0; k < boxes.length + 1; k++) {
      var i = k === 0 ? 0 : Math.floor(r() * boxes.length), j = k === 0 ? 1 : Math.floor(r() * boxes.length);
      if (i === j || j >= boxes.length) continue;
      links.push({ from: boxes[i], to: boxes[j], kind: r() < 0.4 ? 'tree' : 'may' });
    }
    var geo = R.layout(links, boxes);
    total += links.length;
    geo.forEach(function (g, k) {
      var l = links[k], where = { placement: n, link: k, from: l.from, to: l.to, kind: l.kind, d: g.d };
      if (!finite(g) || !onSide(l.from, g.start) || !onSide(l.to, g.end)) faults.shape.push(where);
      if (hooked(g)) faults.hooks.push(where);
      if (ownSamples(g, l.from, l.to)) faults.own.push(where);
    });
  }
  return { placements: spec.count, links: total, hooks: faults.hooks.slice(0, 5), hookCount: faults.hooks.length,
           own: faults.own.slice(0, 5), ownCount: faults.own.length, shape: faults.shape.slice(0, 5), shapeCount: faults.shape.length };
}

// a canvas as busy as the console gets: boxes in columns and rows, formula links between neighbouring columns and
// possible causes between any two boxes
function busyCanvas(spec) {
  var r = rng(99), boxes = [], links = [];
  for (var i = 0; i < spec.boxes; i++) {
    var col = i % 8, row = Math.floor(i / 8);
    boxes.push({ id: 'n' + i, x: 120 + col * 260 + (r() - 0.5) * 60, y: 40 + row * 70 + (r() - 0.5) * 30, hw: 85 + r() * 40, hh: 14 + r() * 9 });
  }
  for (var k = 0; k < spec.links; k++) {
    var a = Math.floor(r() * boxes.length), b = Math.floor(r() * boxes.length);
    if (a === b) b = (b + 1) % boxes.length;
    links.push({ from: boxes[a], to: boxes[b], kind: k < spec.links / 2 ? 'tree' : 'may' });
  }
  return { boxes: boxes, links: links };
}
function runSpeed(spec) {
  var c = busyCanvas(spec), times = [], k;
  for (k = 0; k < 20; k++) R.layout(c.links, c.boxes);
  for (k = 0; k < spec.runs; k++) {
    var t0 = process.hrtime.bigint();
    R.layout(c.links, c.boxes);
    times.push(Number(process.hrtime.bigint() - t0) / 1e6);
  }
  times.sort(function (a, b) { return a - b; });
  var mean = times.reduce(function (s, v) { return s + v; }, 0) / times.length;
  return { links: c.links.length, boxes: c.boxes.length, runs: spec.runs, msMean: mean, msMedian: times[times.length >> 1], msMax: times[times.length - 1] };
}

function main(job) {
  var out = { cases: {} };
  Object.keys(job.cases || {}).forEach(function (name) { out.cases[name] = runCase(job.cases[name]); });
  if (job.random) out.random = runRandom(job.random);
  if (job.speed) {
    out.speed = runSpeed(job.speed);
    var c = busyCanvas(job.speed);
    out.deterministic = JSON.stringify(R.layout(c.links, c.boxes)) === JSON.stringify(R.layout(c.links, c.boxes));
  }
  out.api = { layout: typeof R.layout, point: typeof R.point, tangent: typeof R.tangent };
  return out;
}

var input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', function (s) { input += s; });
process.stdin.on('end', function () { process.stdout.write(JSON.stringify(main(JSON.parse(input || '{}')))); });
