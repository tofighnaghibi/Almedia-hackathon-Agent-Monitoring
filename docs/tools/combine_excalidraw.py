"""Place several .excalidraw files side by side in one file (ids are prefixed so they never collide)."""
import json
import pathlib
import sys

GAP = 300


def bounds(els):
    xs = [e["x"] for e in els] + [e["x"] + e["width"] for e in els]
    ys = [e["y"] for e in els] + [e["y"] + e["height"] for e in els]
    return min(xs), min(ys), max(xs), max(ys)


def prefixed(els, p):
    out = []
    for e in els:
        e = json.loads(json.dumps(e))
        e["id"] = p + e["id"]
        if e.get("containerId"):
            e["containerId"] = p + e["containerId"]
        for b in e.get("boundElements") or []:
            b["id"] = p + b["id"]
        for k in ("startBinding", "endBinding"):
            if e.get(k):
                e[k]["elementId"] = p + e[k]["elementId"]
        e["groupIds"] = [p + g for g in e.get("groupIds", [])]
        out.append(e)
    return out


def combine(paths, out):
    merged, cursor, top = [], None, None
    for i, path in enumerate(paths):
        els = prefixed(json.loads(pathlib.Path(path).read_text())["elements"], f"f{i}_")
        x0, y0, x1, _ = bounds(els)
        top = y0 if top is None else top
        dx = 0 if cursor is None else cursor - x0
        dy = top - y0
        for e in els:
            e["x"] += dx
            e["y"] += dy
        merged += els
        cursor = bounds(els)[2] + GAP
    pathlib.Path(out).write_text(json.dumps({"type": "excalidraw", "version": 2, "source": "https://excalidraw.com",
                                             "elements": merged, "appState": {"viewBackgroundColor": "#ffffff", "gridSize": None},
                                             "files": {}}, indent=1))
    return len(merged)


if __name__ == "__main__":
    docs = pathlib.Path(__file__).resolve().parents[1]
    srcs = sys.argv[1:] or [docs / "architecture.excalidraw", docs / "stitching.excalidraw"]
    out = docs / "architecture-and-stitching.excalidraw"
    print(out, combine(srcs, out), "elements")
