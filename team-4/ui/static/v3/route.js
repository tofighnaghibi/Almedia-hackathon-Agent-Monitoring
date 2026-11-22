/* Eye of the System, v3: where the links on the metric graph leave and enter their boxes, and the curve between.
   Pure geometry with no page: app.js calls V3Route.layout() on every draw, and tests/test_route.py runs it under node.

   A box is { id, x, y, hw, hh }: its centre and half sizes in world units; id is unique per drawn box.
   A link is { from: box, to: box, kind: 'tree' | 'may' }: a formula link (parent to child) or a possible cause
   (source may affect target). layout(links, boxes) returns one geometry per link, in the same order:
     { d,                                  the SVG path: one cubic Bezier
       p: [[x, y] x 4],                    its four control points, from the link's start to its end
       start: { x, y, nx, ny, side },      where it leaves `from`; (nx, ny) the unit normal out of that side
       end:   { x, y, nx, ny, side },      where it enters `to`; (nx, ny) the unit normal out of that side, so the
                                           link arrives travelling along (-nx, -ny)
       mid:   { x, y, tx, ty } }           the point half way along, with the unit tangent towards `to`
   side is one of 'l', 'r', 't', 'b'.

   How the sides are picked, so that a link never makes a hook (leaves away from its target and turns back) wherever
   the viewer has moved the boxes:
   - a formula link whose child still sits clearly right of its parent, and whose old curve runs through no other box,
     keeps the look it always had: from the middle of the parent's right side to the middle of the child's left side,
     handles half the gap;
   - every other link tries the 16 pairs of sides, with a few handle lengths each, and keeps the cheapest: its length,
     plus a heavy cost for each box it runs through, plus a little for a top or bottom side (boxes are wide and short).
     A route is only used if each end's side faces the other end, so the curve cannot turn back; only when none does
     (the two boxes overlap) is the least bad hook drawn. When the best route with its ends in the middles of their
     sides still runs through a box, ends near the corners of top and bottom sides are tried too, which often finds the
     gap between two columns;
   - links that share a side of a box are spread along it, in the order they run beside it, so they do not cross at
     the box; they keep off the middle where the formula links of an untouched layout meet. */
(function (root) {
  'use strict';

  function point(p, t) {
    var u = 1 - t, a = u * u * u, b = 3 * u * u * t, c = 3 * u * t * t, d = t * t * t;
    return [a * p[0][0] + b * p[1][0] + c * p[2][0] + d * p[3][0], a * p[0][1] + b * p[1][1] + c * p[2][1] + d * p[3][1]];
  }
  function tangent(p, t) {
    var u = 1 - t, a = 3 * u * u, b = 6 * u * t, c = 3 * t * t;
    var x = a * (p[1][0] - p[0][0]) + b * (p[2][0] - p[1][0]) + c * (p[3][0] - p[2][0]);
    var y = a * (p[1][1] - p[0][1]) + b * (p[2][1] - p[1][1]) + c * (p[3][1] - p[2][1]);
    var n = Math.sqrt(x * x + y * y) || 1;
    return [x / n, y / n];
  }
  var NORMAL = { l: [-1, 0], r: [1, 0], t: [0, -1], b: [0, 1] };
  var SIDES = ['r', 'l', 'b', 't'];
  // the middle of a side of a box, with the unit normal out of it
  function port(box, side) {
    var n = NORMAL[side];
    return { x: box.x + n[0] * box.hw, y: box.y + n[1] * box.hh, nx: n[0], ny: n[1], side: side };
  }
  // a point on a side, `o` along it from the middle (down a left or right side, rightwards along a top or bottom)
  function anchor(box, side, o) {
    var s = port(box, side);
    if (o) { if (side === 'l' || side === 'r') s.y += o; else s.x += o; }
    return s;
  }
  function round(v) { return Math.round(v * 10) / 10; }
  function geometry(s, e, p1, p2) {
    var p = [[s.x, s.y], p1, p2, [e.x, e.y]];
    var m = point(p, 0.5), t = tangent(p, 0.5);
    return {
      d: 'M' + round(s.x) + ',' + round(s.y) + ' C' + round(p1[0]) + ',' + round(p1[1]) + ' ' + round(p2[0]) + ',' + round(p2[1]) + ' ' + round(e.x) + ',' + round(e.y),
      p: p, start: s, end: e, mid: { x: m[0], y: m[1], tx: t[0], ty: t[1] }
    };
  }
  // the formula link as it has always been drawn: the parent's right side to the child's left, handles half the gap
  function treeLook(a, b) {
    var s = port(a, 'r'), e = port(b, 'l'), c = (e.x - s.x) / 2;
    return geometry(s, e, [s.x + c, s.y], [e.x - c, e.y]);
  }
  function keyOf(b) { return b.id != null ? 'i:' + b.id : 'p:' + b.x + ',' + b.y + ',' + b.hw + ',' + b.hh; }

  // The untouched layout puts a child 40 units right of its parent; while it stays at least this far right (and the old
  // curve runs through no other box), the formula link keeps its old look
  var CLEAR = 20;

  // The cost of a route, lower is better. Each kind of fault outweighs any likely amount of the kinds after it
  var HOOK = 1e8,                  // per end whose side faces away from the other end: the curve would turn back
      OWN = 1e6, OWN_LEN = 1e4,    // running through one of its own two boxes (only a hook can)
      HIT = 300, HIT_LEN = 10,     // running through another box: per box, and per unit of length inside it
      TB = 8, TB_LEN = 0.08,       // per end on a top or bottom side: a little, and a little more the longer the link
      SHALLOW = 24, SHALLOW_K = 8, // per unit an end lies less than this far out of the other end's side (or 0.3 of the
                                   // distance, if less): such a curve meets its box at a slant, close to the corner
      BUSY = 20;                   // per end on a side where the formula links of the untouched layout meet
  var MARGIN = 4;                  // another box counts as crossed this close to its edge
  var N = 16;                      // a curve is scored as N straight pieces
  // handle lengths tried for each pair of sides, as fractions of how far the other end lies out of the side
  var FACING = [[0.5, 0.5], [0.75, 0.75], [1, 1], [1, 0.4], [0.4, 1]],          // opposite sides: an S
      CORNER = [[0.55, 0.55], [0.9, 0.9], [0.3, 0.3], [0.9, 0.4], [0.4, 0.9]],  // sides at a right angle: an L
      SAME = [[34], [80]];       // sides facing the same way (always a hook): bend this far past the further end

  var W0 = [], W1 = [], W2 = [], W3 = [];
  (function () {
    for (var i = 0; i <= N; i++) { var t = i / N, u = 1 - t; W0[i] = u * u * u; W1[i] = 3 * u * u * t; W2[i] = 3 * u * t * t; W3[i] = t * t * t; }
  })();
  var PX = [], PY = [];

  function clamp(v, lo, hi) { return v < lo ? lo : v > hi ? hi : v; }
  // how far the end lies out of the start's side (ps) and the start out of the end's side (pe): both above zero means
  // each side faces the other end
  function reach(s, e) {
    var dx = e.x - s.x, dy = e.y - s.y;
    return [s.nx * dx + s.ny * dy, -(e.nx * dx + e.ny * dy), Math.sqrt(dx * dx + dy * dy)];
  }
  // the two handle lengths of a route from s to e by a rule from FACING, CORNER or SAME. Handles of at most the reach
  // keep every control point outside both sides' lines, so a curve between sides that face each other never turns back
  function handles(s, e, rule) {
    if (rule.length === 1) {
      var ds = s.nx * s.x + s.ny * s.y, de = s.nx * e.x + s.ny * e.y, m = Math.max(ds, de) + rule[0];
      return [m - ds, m - de];
    }
    var r = reach(s, e);
    return [r[0] > 0 ? rule[0] * r[0] : clamp(rule[0] * r[2], 16, 160), r[1] > 0 ? rule[1] * r[1] : clamp(rule[1] * r[2], 16, 160)];
  }

  // the length of the segment (x0, y0)-(x1, y1) inside the rectangle [rx0, rx1] x [ry0, ry1]
  function insideLen(x0, y0, x1, y1, rx0, ry0, rx1, ry1) {
    var dx = x1 - x0, dy = y1 - y0, t0 = 0, t1 = 1, p, q, r, k;
    for (k = 0; k < 4; k++) {
      if (k === 0) { p = -dx; q = x0 - rx0; } else if (k === 1) { p = dx; q = rx1 - x0; }
      else if (k === 2) { p = -dy; q = y0 - ry0; } else { p = dy; q = ry1 - y0; }
      if (p === 0) { if (q < 0) return 0; continue; }
      r = q / p;
      if (p < 0) { if (r > t1) return 0; if (r > t0) t0 = r; } else { if (r < t0) return 0; if (r < t1) t1 = r; }
    }
    return t1 > t0 ? (t1 - t0) * Math.sqrt(dx * dx + dy * dy) : 0;
  }
  // the length of the scored curve (in PX, PY) inside a rectangle, looking only at pieces whose bounds meet it
  function curveInside(rx0, ry0, rx1, ry1) {
    var sum = 0;
    for (var i = 1; i <= N; i++) {
      var ax = PX[i - 1], ay = PY[i - 1], bx = PX[i], by = PY[i];
      if ((ax < rx0 && bx < rx0) || (ax > rx1 && bx > rx1) || (ay < ry0 && by < ry0) || (ay > ry1 && by > ry1)) continue;
      sum += insideLen(ax, ay, bx, by, rx0, ry0, rx1, ry1);
    }
    return sum;
  }

  // the boxes as flat lists of their rectangles, and where each box key sits
  function prepare(boxes) {
    var c = { n: boxes.length, x0: [], y0: [], x1: [], y1: [], at: {} };
    for (var i = 0; i < boxes.length; i++) {
      var b = boxes[i];
      c.x0.push(b.x - b.hw); c.x1.push(b.x + b.hw); c.y0.push(b.y - b.hh); c.y1.push(b.y + b.hh);
      var k = keyOf(b);
      if (!(k in c.at)) c.at[k] = i;
    }
    return c;
  }

  // cut the curve from s to e with handles h into N pieces (PX, PY); returns its length and the bounds of its control
  // points, which hold the whole curve
  var hull = [0, 0, 0, 0];
  function sample(s, e, h) {
    var p1x = s.x + s.nx * h[0], p1y = s.y + s.ny * h[0], p2x = e.x + e.nx * h[1], p2y = e.y + e.ny * h[1];
    var len = 0;
    for (var i = 0; i <= N; i++) {
      PX[i] = W0[i] * s.x + W1[i] * p1x + W2[i] * p2x + W3[i] * e.x;
      PY[i] = W0[i] * s.y + W1[i] * p1y + W2[i] * p2y + W3[i] * e.y;
      if (i) { var dx = PX[i] - PX[i - 1], dy = PY[i] - PY[i - 1]; len += Math.sqrt(dx * dx + dy * dy); }
    }
    hull[0] = Math.min(s.x, p1x, p2x, e.x); hull[1] = Math.min(s.y, p1y, p2y, e.y);
    hull[2] = Math.max(s.x, p1x, p2x, e.x); hull[3] = Math.max(s.y, p1y, p2y, e.y);
    return len;
  }
  // the cost of the boxes other than ia and ib that the sampled curve runs through, each grown by m; stops past limit
  function crossing(ctx, ia, ib, m, c, limit) {
    for (var i = 0; i < ctx.n; i++) {
      var x0 = ctx.x0[i] - m, y0 = ctx.y0[i] - m, x1 = ctx.x1[i] + m, y1 = ctx.y1[i] + m;
      if (i === ia || i === ib || x0 > hull[2] || x1 < hull[0] || y0 > hull[3] || y1 < hull[1]) continue;
      var inside = curveInside(x0, y0, x1, y1);
      if (inside > 0) { c += HIT + HIT_LEN * inside; if (c >= limit) return c; }
    }
    return c;
  }
  function boxIndex(ctx, b) { var k = keyOf(b); return k in ctx.at ? ctx.at[k] : -1; }

  // a formula link keeps its old look while the child is clearly right of the parent and the old curve runs through
  // no other box
  function keepsLook(a, b, ctx) {
    if (!((b.x - b.hw) - (a.x + a.hw) >= CLEAR)) return false;
    var s = port(a, 'r'), e = port(b, 'l'), c = (e.x - s.x) / 2;
    sample(s, e, [c, c]);
    return crossing(ctx, boxIndex(ctx, a), boxIndex(ctx, b), -1, 0, 1) === 0;
  }

  // the cost of one route; gives up and returns early once it is past `limit`
  function cost(s, e, h, fixed, limit, ctx, a, b, ia, ib, hooked) {
    var c = fixed + sample(s, e, h);
    if (c >= limit) return c;
    if (hooked) {
      // its own boxes, shrunk a little so the ends on their edges do not count
      var own = curveInside(a.x - a.hw + 1, a.y - a.hh + 1, a.x + a.hw - 1, a.y + a.hh - 1) + curveInside(b.x - b.hw + 1, b.y - b.hh + 1, b.x + b.hw - 1, b.y + b.hh - 1);
      if (own > 0) c += OWN + OWN_LEN * own;
      if (c >= limit) return c;
    }
    return crossing(ctx, ia, ib, MARGIN, c, limit);
  }

  // How much of a side an end may move along, either way from its middle: off the rounded corners
  function room(box, side) {
    var upright = side === 'l' || side === 'r', half = upright ? box.hh : box.hw;
    return Math.max(0, half - Math.min(0.35 * half, upright ? 8 : 16));
  }
  // where along a top or bottom side (wide) an end may sit, as shares of its room: off the middle it can see past a
  // neighbour or into a gap between columns. Left and right sides (short) keep their ends in the middle here
  var SPOTS = [0, -1, 1], MIDDLE = [0];
  // the handle lengths tried with an end off the middle of its side: the plainest, the squarest and the roundest
  var FEW = { FACING: [FACING[0], FACING[2]], CORNER: [CORNER[0], CORNER[1], CORNER[2]], SAME: SAME };
  var OFFSET_K = 0.5;              // per unit an end sits off the middle of its side: only worth it if it buys something

  // every route worth scoring from box a to box b: first only those whose sides face each other; the rest only if
  // there is none (the boxes overlap). Each with `low`, its cost before any box it runs through
  function routes(a, b, busy, spots) {
    var ka = keyOf(a), kb = keyOf(b), list = [];
    for (var pass = 0; pass < 2 && !list.length; pass++) {
      for (var i = 0; i < 4; i++) {
        for (var j = 0; j < 4; j++) {
          var sa = SIDES[i], sb = SIDES[j], ta = sa === 't' || sa === 'b', tb = sb === 't' || sb === 'b';
          var ra = room(a, sa), rb = room(b, sb), spotsA = ta ? spots : MIDDLE, spotsB = tb ? spots : MIDDLE;
          var dot = NORMAL[sa][0] * NORMAL[sb][0] + NORMAL[sa][1] * NORMAL[sb][1], shape = dot > 0.5 ? 'SAME' : dot < -0.5 ? 'FACING' : 'CORNER';
          var rules = spots === MIDDLE ? (shape === 'SAME' ? SAME : shape === 'FACING' ? FACING : CORNER) : FEW[shape];
          var side = ((busy[ka + '|' + sa] ? 1 : 0) + (busy[kb + '|' + sb] ? 1 : 0)) * BUSY;
          for (var u = 0; u < spotsA.length; u++) {
            for (var w = 0; w < spotsB.length; w++) {
              var oa = spotsA[u] * ra, ob = spotsB[w] * rb, s = anchor(a, sa, oa), e = anchor(b, sb, ob), r = reach(s, e);
              var hooks = (r[0] > 0 ? 0 : 1) + (r[1] > 0 ? 0 : 1);
              if ((hooks === 0) !== (pass === 0)) continue;
              var shallow = Math.min(SHALLOW, 0.3 * r[2]);
              var fixed = hooks * HOOK + side + ((ta ? 1 : 0) + (tb ? 1 : 0)) * (TB + TB_LEN * r[2]) +
                (Math.max(0, shallow - r[0]) + Math.max(0, shallow - r[1])) * SHALLOW_K + (Math.abs(oa) + Math.abs(ob)) * OFFSET_K;
              for (var v = 0; v < rules.length; v++) {
                var h = handles(s, e, rules[v]);
                list.push({ n: list.length, low: fixed + sample(s, e, h), fixed: fixed, s: s, e: e, h: h, hooked: hooks > 0,
                            plan: { sa: sa, sb: sb, d0: oa, d1: ob, rule: rules[v], clean: hooks === 0 } });
              }
            }
          }
        }
      }
    }
    return list;
  }
  // the cheapest of them: scored in order of their cost before boxes, which no box can lower, so the scoring stops at
  // the first route that cannot beat the best so far. Ends in the middles of their sides are tried first; the other
  // spots only when that best is a hook or runs through a box
  function choose(a, b, ctx, busy) {
    var ia = boxIndex(ctx, a), ib = boxIndex(ctx, b), best = null, bestCost = Infinity, clear = false;
    for (var stage = 0; stage < 2 && !clear; stage++) {
      var list = routes(a, b, busy, stage ? SPOTS : MIDDLE);
      list.sort(function (u, v) { return u.low - v.low || u.n - v.n; });
      for (var k = 0; k < list.length && list[k].low < bestCost; k++) {
        var q = list[k];
        if (stage && !q.plan.d0 && !q.plan.d1) continue;      // scored already
        var c = cost(q.s, q.e, q.h, q.fixed, bestCost, ctx, a, b, ia, ib, q.hooked);
        if (c < bestCost) { bestCost = c; best = q.plan; clear = c === q.low && !q.hooked; }
      }
    }
    // only boxes without finite numbers leave nothing to compare: draw the old formula look rather than fail
    return best || { sa: 'r', sb: 'l', d0: 0, d1: 0, rule: FACING[0], clean: false };
  }

  // where a link runs just outside the side it meets: how far along the side (from its middle) the curve is once it is
  // OUT units away from it; ordering the links of one side by this keeps them from crossing as they reach the box.
  // P: the curve's control points; end: 0 at its start, 1 at its end
  var OUT = 20;
  function besideAt(P, end, c, side) {
    var n = NORMAL[side], upright = side === 'l' || side === 'r', prevD = 0, prevT = 0, far = -Infinity, farT = 0;
    for (var k = 0; k <= 24; k++) {
      var q = point(P, end ? 1 - k / 24 : k / 24), vx = q[0] - c.x, vy = q[1] - c.y;
      var dist = vx * n[0] + vy * n[1], along = upright ? vy : vx;
      if (k && dist >= OUT) return prevT + (along - prevT) * (OUT - prevD) / ((dist - prevD) || 1);
      if (dist > far) { far = dist; farT = along; }
      prevD = dist; prevT = along;
    }
    return farT;
  }
  function curve(l, p, o0, o1) {
    if (p.keep) return treeLook(l.from, l.to);
    var s = anchor(l.from, p.sa, o0), e = anchor(l.to, p.sb, o1), h = handles(s, e, p.rule);
    return geometry(s, e, [s.x + s.nx * h[0], s.y + s.ny * h[0]], [e.x + e.nx * h[1], e.y + e.ny * h[1]]);
  }
  // the least squares fit to t that never goes down (pool adjacent violators)
  function rising(t) {
    var sum = [], cnt = [], out = [], j, k;
    for (j = 0; j < t.length; j++) {
      sum.push(t[j]); cnt.push(1);
      while (sum.length > 1 && sum[sum.length - 2] / cnt[cnt.length - 2] > sum[sum.length - 1] / cnt[cnt.length - 1]) {
        sum[sum.length - 2] += sum.pop(); cnt[cnt.length - 2] += cnt.pop();
      }
    }
    for (j = 0; j < sum.length; j++) for (k = 0; k < cnt[j]; k++) out.push(sum[j] / cnt[j]);
    return out;
  }

  // spread the links that share a side of a box along it, in the order they run beside it, so they do not cross where
  // they meet the box: each as near its chosen spot as the spacing allows, and off the middle where the formula links
  // of the untouched layout meet
  function spread(links, plan, ctx) {
    var groups = {}, names = [];
    function add(box, side, item) {
      var k = keyOf(box) + '|' + side;
      if (!groups[k]) { groups[k] = []; names.push(k); }
      item.box = box; item.side = side;
      groups[k].push(item);
    }
    links.forEach(function (l, i) {
      var p = plan[i], P = curve(l, p, p.d0, p.d1).p;
      if (p.keep) { add(l.from, 'r', { fixed: true, P: P, i: i, end: 0 }); add(l.to, 'l', { fixed: true, P: P, i: i, end: 1 }); return; }
      add(l.from, p.sa, { p: p, P: P, i: i, end: 0, want: p.d0 });
      add(l.to, p.sb, { p: p, P: P, i: i, end: 1, want: p.d1 });
    });
    names.forEach(function (k) {
      var g = groups[k], fixed = [], items = [];
      g.forEach(function (it) { (it.fixed ? fixed : items).push(it); });
      if (!items.length || (items.length === 1 && !fixed.length)) return;
      var box = items[0].box, side = items[0].side, upright = side === 'l' || side === 'r';
      var span = room(box, side), cap = upright ? 10 : 22, c = port(box, side);
      g.forEach(function (it) { it.key = besideAt(it.P, it.end, c, side); });
      items.sort(function (u, v) { return u.key - v.key || u.i - v.i || u.end - v.end; });
      var set = function (it, o) { if (it.end) it.p.o1 = o; else it.p.o0 = o; };
      if (fixed.length) {
        // the formula links hold the middle: the others go to the side of them they run on
        var keys = fixed.map(function (it) { return it.key; }).sort(function (u, v) { return u - v; });
        var mid = keys[(keys.length - 1) >> 1];
        var before = items.filter(function (it) { return it.key < mid; }), after = items.filter(function (it) { return it.key >= mid; });
        var sp = Math.min(cap, span / Math.max(before.length, after.length, 1));
        before.forEach(function (it, j) { set(it, -sp * (before.length - j)); });
        after.forEach(function (it, j) { set(it, sp * (j + 1)); });
        return;
      }
      // in this order, at least sp apart, as near the wanted spots as can be, and within the room of the side
      var m = items.length, sp2 = Math.min(cap, 2 * span / (m - 1));
      var at = rising(items.map(function (it, j) { return it.want - j * sp2; })).map(function (q, j) { return q + j * sp2; });
      var shift = Math.max(0, -span - at[0]) + Math.min(0, span - at[m - 1]);
      items.forEach(function (it, j) { set(it, at[j] + shift); });
    });
    // moving an end along its side must not turn a clean link into a hook: such a link moves only part of the way
    // from its chosen spots, short of where a side would stop facing the other end (the reach changes linearly)
    links.forEach(function (l, i) {
      var p = plan[i];
      if (p.keep || !p.clean) return;
      var A = reach(anchor(l.from, p.sa, p.d0), anchor(l.to, p.sb, p.d1)), B = reach(anchor(l.from, p.sa, p.o0), anchor(l.to, p.sb, p.o1)), f = 1;
      for (var k = 0; k < 2; k++) if (!(B[k] > 0)) f = Math.min(f, A[k] > 0 ? 0.8 * A[k] / (A[k] - B[k]) : 0);
      if (f < 1) { p.o0 = p.d0 + f * (p.o0 - p.d0); p.o1 = p.d1 + f * (p.o1 - p.d1); }
      // nor push it into a box that its chosen spots kept it out of: then it moves half way, or stays on its spots
      if (p.o0 === p.d0 && p.o1 === p.d1) return;
      var ia = boxIndex(ctx, l.from), ib = boxIndex(ctx, l.to);
      var hits = function (o0, o1) {
        var s = anchor(l.from, p.sa, o0), e = anchor(l.to, p.sb, o1);
        sample(s, e, handles(s, e, p.rule));
        return crossing(ctx, ia, ib, 0, 0, Infinity);
      };
      var want = hits(p.d0, p.d1) + 1;
      if (hits(p.o0, p.o1) <= want) return;
      var h0 = (p.o0 + p.d0) / 2, h1 = (p.o1 + p.d1) / 2;
      if (hits(h0, h1) <= want) { p.o0 = h0; p.o1 = h1; } else { p.o0 = p.d0; p.o1 = p.d1; }
    });
  }

  function layout(links, boxes) {
    links = links || [];
    var ctx = prepare(boxes || []), plan = [], busy = {}, i;
    // the formula links that keep their old look first: the sides where they meet are busy for the rest
    for (i = 0; i < links.length; i++) {
      var l = links[i];
      if (l.kind === 'tree' && keepsLook(l.from, l.to, ctx)) {
        plan[i] = { keep: true };
        busy[keyOf(l.from) + '|r'] = 1; busy[keyOf(l.to) + '|l'] = 1;
      }
    }
    for (i = 0; i < links.length; i++) {
      if (plan[i]) continue;
      plan[i] = choose(links[i].from, links[i].to, ctx, busy);
      plan[i].o0 = plan[i].d0; plan[i].o1 = plan[i].d1;
    }
    spread(links, plan, ctx);
    var out = [];
    for (i = 0; i < links.length; i++) out.push(curve(links[i], plan[i], plan[i].o0, plan[i].o1));
    return out;
  }

  var api = { layout: layout, point: point, tangent: tangent };
  if (typeof module !== 'undefined' && module.exports) module.exports = api;
  else root.V3Route = api;
})(this);
