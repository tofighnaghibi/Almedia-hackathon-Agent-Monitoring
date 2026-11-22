"""Tiny helpers for writing Excalidraw files from Python."""
import json
import random

_E: list = []


def reset(seed: int = 1):
    random.seed(seed)
    _E.clear()


def _id():
    return "e%07d" % random.randrange(10**7)


def base(t, x, y, w, h, **k):
    d = dict(id=_id(), type=t, x=x, y=y, width=w, height=h, angle=0,
             strokeColor=k.get("stroke", "#1e1e1e"), backgroundColor=k.get("bg", "transparent"),
             fillStyle="solid", strokeWidth=k.get("sw", 2), strokeStyle=k.get("style", "solid"),
             roughness=1, opacity=k.get("opacity", 100), groupIds=[], frameId=None,
             roundness={"type": 3} if t == "rectangle" else None, seed=random.randrange(1, 2**31),
             version=1, versionNonce=random.randrange(1, 2**31), isDeleted=False,
             boundElements=[], updated=1, link=None, locked=False)
    _E.append(d)
    return d


def text(s, x, y, w, size=16, align="left", container=None, color="#1e1e1e"):
    n = len(s.split("\n"))
    t = base("text", x, y, w, n * size * 1.25, stroke=color)
    t.update(text=s, originalText=s, fontSize=size, fontFamily=1, textAlign=align,
             verticalAlign="middle" if container else "top", containerId=container,
             lineHeight=1.25, autoResize=True, roundness=None)
    return t


def box(s, x, y, w, bg, size=15, stroke="#1e1e1e", style="solid", minh=0):
    n = len(s.split("\n"))
    h = max(minh, n * size * 1.25 + 20)
    r = base("rectangle", x, y, w, h, bg=bg, stroke=stroke, style=style)
    t = text(s, x + 8, y + (h - n * size * 1.25) / 2, w - 16, size, "center", r["id"])
    r["boundElements"].append({"type": "text", "id": t["id"]})
    return r


def lane(title, x, y, w, h, bg="#f8f9fa", size=20):
    r = base("rectangle", x, y, w, h, bg=bg, stroke="#adb5bd", sw=1, style="dashed", opacity=60)
    text(title, x + 14, y + 8, w - 28, size, color="#495057")
    return r


def _edge(a, side):
    cx, cy = a["x"] + a["width"] / 2, a["y"] + a["height"] / 2
    return {"r": (a["x"] + a["width"], cy), "l": (a["x"], cy), "t": (cx, a["y"]), "b": (cx, a["y"] + a["height"])}[side]


def arrow(a, b, sa="r", sb="l", label=None, color="#1e1e1e", style="solid", lsize=13):
    (x1, y1), (x2, y2) = _edge(a, sa), _edge(b, sb)
    ar = base("arrow", x1, y1, x2 - x1, y2 - y1, stroke=color, style=style)
    ar.update(points=[[0, 0], [x2 - x1, y2 - y1]], lastCommittedPoint=None,
              startBinding={"elementId": a["id"], "focus": 0, "gap": 4},
              endBinding={"elementId": b["id"], "focus": 0, "gap": 4},
              startArrowhead=None, endArrowhead="arrow", roundness={"type": 2})
    a["boundElements"].append({"type": "arrow", "id": ar["id"]})
    b["boundElements"].append({"type": "arrow", "id": ar["id"]})
    if label:
        text(label, (x1 + x2) / 2 - 60, (y1 + y2) / 2 - 18, 120, lsize, "center", color=color if color != "#1e1e1e" else "#495057")
    return ar


def save(path):
    with open(path, "w") as f:
        json.dump({"type": "excalidraw", "version": 2, "source": "https://excalidraw.com",
                   "elements": _E, "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None}, "files": {}}, f, indent=1)
    return len(_E)


def tech_legend(items, x, y, cols=3, colw=1000, title="Technologies used (what it is · why we use it)", box_fn=None, text_fn=None):
    """Grid of (name, explanation) entries. Returns the y below the legend."""
    import textwrap
    bx = box_fn or box
    tx = text_fn or text
    tx(title, x, y, cols * colw, 20)
    rows = -(-len(items) // cols)
    y0 = y + 38
    heights = []
    for r in range(rows):
        h = 0
        for c in range(cols):
            i = c * rows + r
            if i < len(items):
                n = len(textwrap.wrap(items[i][1], int((colw - 300) / 7.6)))
                h = max(h, max(40, n * 16 + 12))
        heights.append(h)
    for c in range(cols):
        yy = y0
        for r in range(rows):
            i = c * rows + r
            if i < len(items):
                name, desc = items[i]
                bx(name, x + c * colw, yy, 270, "#f1f3f5", 13, minh=34)
                tx("\n".join(textwrap.wrap(desc, int((colw - 300) / 7.6))), x + c * colw + 282, yy + 2, colw - 300, 13, color="#343a40")
            yy += heights[r] + 8
    return y0 + sum(heights) + 8 * rows
