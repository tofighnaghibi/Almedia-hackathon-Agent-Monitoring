"""Render our generated .excalidraw diagrams to clean PNGs locally (no external service).

    python3 docs/tools/excalidraw_to_png.py docs/current-architecture.excalidraw docs/media/architecture/current-architecture.png

Supports what our generators emit: rectangles, ellipses, diamonds, text (incl. text bound to a shape), arrows and lines.
"""
import json
import sys

from PIL import Image, ImageDraw, ImageFont

FONT = "/usr/share/fonts/truetype/dejavu/DejaVuSans{}.ttf"
SCALE = 1.5


def color(c, default=None):
    if not c or c == "transparent":
        return default
    c = c.lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    return tuple(int(c[i:i + 2], 16) for i in (0, 2, 4))


def render(src, dst):
    els = [e for e in json.load(open(src))["elements"] if not e.get("isDeleted")]
    xs, ys = [], []
    for e in els:
        pts = e.get("points") or [[0, 0], [e.get("width", 0), e.get("height", 0)]]
        for px, py in pts:
            xs.append(e["x"] + px); ys.append(e["y"] + py)
        xs.append(e["x"] + e.get("width", 0)); ys.append(e["y"] + e.get("height", 0))
    pad = 40
    x0, y0 = min(xs) - pad, min(ys) - pad
    W, H = int((max(xs) - x0 + pad) * SCALE), int((max(ys) - y0 + pad) * SCALE)
    im = Image.new("RGB", (W, H), (255, 255, 255))
    d = ImageDraw.Draw(im)
    T = lambda x, y: ((x - x0) * SCALE, (y - y0) * SCALE)
    by_id = {e["id"]: e for e in els}
    order = {"rectangle": 0, "ellipse": 0, "diamond": 0, "line": 1, "arrow": 1, "text": 2}
    for e in sorted(els, key=lambda e: order.get(e["type"], 3)):
        t = e["type"]
        sw = max(1, int(e.get("strokeWidth", 1) * SCALE))
        stroke = color(e.get("strokeColor"), (30, 30, 30))
        fill = color(e.get("backgroundColor"))
        if t in ("rectangle", "ellipse", "diamond"):
            (a, b), (c, f) = T(e["x"], e["y"]), T(e["x"] + e["width"], e["y"] + e["height"])
            if t == "rectangle":
                r = int(12 * SCALE) if e.get("roundness") else 0
                d.rounded_rectangle((a, b, c, f), radius=r, fill=fill, outline=stroke, width=sw)
                if e.get("strokeStyle") == "dashed":            # dashed look: overdraw gaps
                    gap = 10 * SCALE
                    xx = a
                    while xx < c:
                        d.line((xx + gap / 2, b, min(xx + gap, c), b), fill=fill or (255, 255, 255), width=sw)
                        d.line((xx + gap / 2, f, min(xx + gap, c), f), fill=fill or (255, 255, 255), width=sw)
                        xx += gap
            elif t == "ellipse":
                d.ellipse((a, b, c, f), fill=fill, outline=stroke, width=sw)
            else:
                mx, my = (a + c) / 2, (b + f) / 2
                d.polygon([(mx, b), (c, my), (mx, f), (a, my)], fill=fill, outline=stroke)
        elif t in ("arrow", "line"):
            pts = [T(e["x"] + px, e["y"] + py) for px, py in e["points"]]
            d.line(pts, fill=stroke, width=sw)
            if t == "arrow" and e.get("endArrowhead") is not None and len(pts) > 1:
                import math
                (ax, ay), (bx, by) = pts[-2], pts[-1]
                ang = math.atan2(by - ay, bx - ax); L = 12 * SCALE
                for s in (0.45, -0.45):
                    d.line((bx, by, bx - L * math.cos(ang + s), by - L * math.sin(ang + s)), fill=stroke, width=sw)
        elif t == "text":
            size = max(8, int(e.get("fontSize", 16) * SCALE))
            bold = size >= int(22 * SCALE)
            font = ImageFont.truetype(FONT.format("-Bold" if bold else ""), size)
            lines = e["text"].split("\n")
            lh = size * 1.25
            box = by_id.get(e.get("containerId"))
            if box:
                bx0, by0 = T(box["x"], box["y"]); bx1, by1 = T(box["x"] + box["width"], box["y"] + box["height"])
                ty = (by0 + by1) / 2 - len(lines) * lh / 2
                for i, ln in enumerate(lines):
                    tw = d.textlength(ln, font=font)
                    d.text(((bx0 + bx1) / 2 - tw / 2, ty + i * lh), ln, font=font, fill=stroke)
            else:
                tx, ty = T(e["x"], e["y"])
                for i, ln in enumerate(lines):
                    tw = d.textlength(ln, font=font)
                    x = tx + (e.get("width", 0) * SCALE - tw) / 2 if e.get("textAlign") == "center" else tx
                    d.text((x, ty + i * lh), ln, font=font, fill=stroke)
    im.save(dst, optimize=True)
    return W, H


if __name__ == "__main__":
    print(render(sys.argv[1], sys.argv[2]))
