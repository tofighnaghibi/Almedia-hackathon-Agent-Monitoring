"""E3 leak scan: which telemetry fields reveal injected-fault ground truth or personal data.
Streams OTLP-JSON lines (collector file exporter) and counts hits per (signal, service, field)."""
import collections
import json
import re
import sys

PATTERNS = {   # name -> regex over attribute KEYS or string VALUES
    "flag key": re.compile(r"feature_flag|flagd|openfeature", re.I),
    "flag name": re.compile(r"paymentFailure|recommendationCacheFailure|paymentUnreachable|productCatalogFailure|adFailure|cartFailure"),
    "fault text": re.compile(r"Fail Feature Flag|feature flag enabled|Invalid token|cache failure", re.I),
    "loyalty": re.compile(r"loyalty_level", re.I),
    "email": re.compile(r"user\.email|[A-Za-z0-9._%+-]+@example\.com"),
    "card": re.compile(r"demo\.payment\.card|creditCard|4432-8015", re.I),
}
hits = collections.Counter()
examples = {}


def kvs(o, svc, sig):
    if isinstance(o, dict):
        if "key" in o and "value" in o:
            k = o["key"]; v = o["value"]
            sv = v.get("stringValue") if isinstance(v, dict) else None
            if k == "service.name" and sv:
                return
            for n, rx in PATTERNS.items():
                where = "key" if rx.search(k) else "value" if (sv and rx.search(sv)) else None
                if where:
                    hits[(sig, svc, n, k)] += 1
                    examples.setdefault((sig, svc, n, k), (sv or "")[:120])
        for kk, vv in o.items():
            if kk in ("name", "message") and isinstance(vv, str):
                for n, rx in PATTERNS.items():
                    if rx.search(vv):
                        hits[(sig, svc, n, f"<{kk}>")] += 1
                        examples.setdefault((sig, svc, n, f"<{kk}>"), vv[:120])
            elif kk == "body" and isinstance(vv, dict) and isinstance(vv.get("stringValue"), str):
                for n, rx in PATTERNS.items():
                    if rx.search(vv["stringValue"]):
                        hits[(sig, svc, n, "<log body>")] += 1
                        examples.setdefault((sig, svc, n, "<log body>"), vv["stringValue"][:120])
            else:
                kvs(vv, svc, sig)
    elif isinstance(o, list):
        for x in o:
            kvs(x, svc, sig)


for path in sys.argv[1:]:
    sig = "spans" if "span" in path else "logs"
    for line in open(path):
        rec = json.loads(line)
        for rs in rec.get("resourceSpans", []) + rec.get("resourceLogs", []):
            svc = next((a["value"].get("stringValue") for a in rs.get("resource", {}).get("attributes", []) if a["key"] == "service.name"), "?")
            kvs(rs, svc, sig)
out = [{"signal": s, "service": v, "kind": n, "field": k, "count": c, "example": examples[(s, v, n, k)]}
       for (s, v, n, k), c in hits.most_common()]
print(json.dumps({"total_hits": sum(hits.values()), "fields": out}, indent=1))
