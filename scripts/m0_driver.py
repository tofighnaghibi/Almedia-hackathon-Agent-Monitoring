"""M0 E1/E3 driver: N concurrent shoppers run browse -> product -> cart -> checkout against the local
Astronomy Shop (frontend-proxy), for a fixed duration. Writes per-request latencies and a memory trace.
Request shapes copied from the shop's own load generator (src/load-generator/locustfile.py @ 3.1.0)."""
import json
import random
import statistics
import subprocess
import sys
import threading
import time
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor

BASE = "http://localhost:8080"
PEOPLE = json.load(open(sys.argv[4])) if len(sys.argv) > 4 else None
DUR, CONC, OUT = float(sys.argv[1]), int(sys.argv[2]), sys.argv[3]


def call(method, path, body=None):
    req = urllib.request.Request(BASE + path, method=method, data=json.dumps(body).encode() if body is not None else None,
                                 headers={"Content-Type": "application/json"})
    t = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read(); code = r.status
    except urllib.error.HTTPError as e:
        code = e.code
    except Exception:
        code = -1
    return code, time.perf_counter() - t


products = [p["id"] for p in json.loads(urllib.request.urlopen(BASE + "/api/products?currencyCode=USD").read())]
log, lock, stop = [], threading.Lock(), time.time() + DUR


def shopper(i):
    rng = random.Random(i)
    while time.time() < stop:
        user = str(uuid.UUID(int=rng.getrandbits(128)))
        t0 = time.perf_counter()
        steps = [("GET", "/api/products?currencyCode=USD", None)]
        pid = rng.choice(products)
        steps += [("GET", f"/api/products/{pid}?currencyCode=USD", None),
                  ("GET", f"/api/recommendations?productIds={pid}&currencyCode=USD", None),
                  ("POST", "/api/cart", {"item": {"productId": pid, "quantity": rng.choice([1, 2, 3])}, "userId": user})]
        person = dict(rng.choice(PEOPLE)); person["userId"] = user
        steps += [("POST", "/api/checkout?currencyCode=USD", person)]
        res = [(p.split("?")[0].split("/")[2] if p.count("/") > 1 else p, *call(m, p, b)) for m, p, b in steps]
        with lock:
            log.append({"t": time.time(), "steps": res, "session_s": time.perf_counter() - t0})


mem = []


def sampler():
    while time.time() < stop:
        out = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{.Name}} {{.MemUsage}}"], capture_output=True, text=True).stdout
        tot = 0.0
        for line in out.splitlines():
            name, use = line.split(" ", 1)
            v = use.split("/")[0].strip()
            tot += float(v[:-3]) / (1024 if v.endswith("MiB") else 1 if v.endswith("GiB") else 1024 * 1024) if v[-3:] in ("MiB", "GiB", "KiB") else 0
        mem.append((time.time(), tot))
        time.sleep(15)


threading.Thread(target=sampler, daemon=True).start()
with ThreadPoolExecutor(CONC) as ex:
    list(ex.map(shopper, range(CONC)))
ok = [l for l in log if all(c == 200 for _, c, _ in l["steps"])]
lat = {}
for l in log:
    for step, c, s in l["steps"]:
        lat.setdefault(step, []).append(s)
summary = {"duration_s": DUR, "concurrency": CONC, "sessions": len(log), "sessions_all_ok": len(ok),
           "checkouts_per_s": sum(1 for l in log if l["steps"][-1][1] == 200) / DUR,
           "status_counts": {f"{st}:{c}": sum(1 for l in log for s2, c2, _ in l["steps"] if s2 == st and c2 == c)
                             for st in lat for c in sorted({c for l in log for s2, c, _ in l["steps"] if s2 == st})},
           "p50_s": {k: statistics.median(v) for k, v in lat.items()},
           "p95_s": {k: sorted(v)[int(0.95 * len(v))] for k, v in lat.items()},
           "session_p95_s": sorted(l["session_s"] for l in log)[int(0.95 * len(log))] if log else None,
           "memory_gib_max": max(m for _, m in mem) if mem else None, "memory_gib_median": statistics.median(m for _, m in mem) if mem else None}
summary["status_counts"] = {k: v for k, v in summary["status_counts"].items() if v}
json.dump(summary, open(OUT, "w"), indent=1)
print(json.dumps(summary, indent=1))
