"""Generate docs/astronomy-integration.excalidraw (ASTRONOMY_SHOP_INTEGRATION.md, design for approval)."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).parent))
from excal import arrow, base, box, lane, reset, save, tech_legend, text  # noqa: E402

reset(3)
REUSE, ADAPT, REPLACE, BYPASS, OURS, CORE, PRIV, STATE = "#b2f2bb", "#ffec99", "#d0bfff", "#e9ecef", "#a5d8ff", "#ffffff", "#ffc9c9", "#ffd8a8"

text("Astronomy Shop as an optional storefront for the marketplace emulator  (design, awaiting approval A1–A5)", 0, -110, 2600, 28)
text("The emulator is the only business truth. The storefront executes sessions and returns results. Upstream code is not changed "
     "(pinned opentelemetry-demo @ 7a88536). Full detail: docs/ASTRONOMY_SHOP_INTEGRATION.md",
     0, -68, 2600, 17, color="#495057")

lane("Causal core (domain-agnostic)", -20, -20, 400, 720)
ing = box("A  Canonical ingestion", 0, 50, 360, CORE, 16, minh=70)
cg = box("B  Causal graph +\nreasoning engine", 0, 220, 360, CORE, 16, minh=90)
out = box("C  Alerts, explanations,\naction proposals", 0, 410, 360, CORE, 16, minh=90)
arrow(ing, cg, "b", "t"); arrow(cg, out, "b", "t")

lane("Port: MarketplaceSource", 430, -20, 300, 720)
port = box("poll(watermark)\nact(ActionRequest)\ncapabilities()", 450, 200, 260, "#f3f0ff", 16, minh=110)
real = box("Real source adapter\nOlist, Amazon, Tmall*", 450, 380, 260, CORE, 14)
rep = box("Historical replay adapter", 450, 460, 260, CORE, 14)
emad = box("Emulator adapter", 450, 530, 260, OURS, 15)
arrow(out, port, "r", "l", "act"); arrow(port, ing, "l", "r", "observations")
for a in (real, rep, emad):
    arrow(port, a, "b", "t", color="#adb5bd")

lane("Emulator (authoritative state)", 780, -20, 470, 720)
gate = box("Action gateway\nallowlist, eligibility, budgets,\naudit, rejects invalid actions", 800, 40, 430, OURS, 15)
kern = box("Simulator kernel\n1 h vectorised ticks + event queue\nseeded named RNG streams\nsnapshot / restore", 800, 190, 430, STATE, 15, minh=120)
led = box("Business ledger (event-sourced)\nreservations → commit / release", 800, 380, 430, STATE, 15)
obs = box("Observation process\ncoverage, delay, sampling, ID loss,\naggregation, MNAR → watermarked stream", 800, 500, 430, OURS, 15)
arrow(emad, gate, "r", "l", "act")
arrow(obs, emad, "l", "r", "poll")
arrow(gate, kern, "b", "t"); arrow(kern, led, "b", "t"); arrow(led, obs, "b", "t")

lane("Astronomy Shop (optional projection, storefront mode only)", 1310, -20, 900, 720, bg="#f8f9fa")
drv = box("Session driver\nreplaces Locust", 1330, 40, 260, OURS, 15)
cs = box("Catalog sync\nfeatured offer → catalog.products", 1330, 150, 260, OURS, 14)
sr = box("sim-recommendation\nsame RecommendationService proto", 1330, 260, 260, REPLACE, 14)
fa = box("Flag adapter\nwrites demo.flagd.json", 1330, 370, 260, OURS, 14)
ta = box("Telemetry adapter\nredact → operational metrics", 1330, 500, 260, OURS, 14)
fe = box("frontend-proxy → frontend\n/api/products, cart, checkout", 1680, 40, 500, REUSE, 15)
svc = box("product-catalog · cart+valkey · checkout · payment ·\nshipping+quote · currency · email · ad · images", 1680, 150, 500, REUSE, 14)
db = box("astronomy-db (Postgres) catalog.products", 1680, 260, 500, ADAPT, 14)
fd = box("flagd (21 operational fault flags)", 1680, 340, 500, ADAPT, 14)
col = box("otel-collector + layered emulator config\n(file/OTLP export + redaction)", 1680, 420, 500, ADAPT, 14)
byp = box("Bypassed: load-generator (Locust), flagd-ui, accounting,\nfraud-detection, kafka (except optional interactive bridge),\nJaeger / Grafana / Prometheus / OpenSearch, agent / mcp / chatbot",
          1680, 540, 500, BYPASS, 13, style="dashed")
arrow(kern, drv, "r", "l", "session plans", "#1971c2")
arrow(drv, kern, "b", "r", "command results", "#e8590c")
arrow(kern, cs, "r", "l"); arrow(kern, sr, "r", "l")
arrow(gate, fa, "r", "l", "operational faults", "#7048e8")
arrow(drv, fe, "r", "l", "HTTP + baggage")
arrow(fe, svc, "b", "t"); arrow(cs, db, "r", "l"); arrow(sr, svc, "r", "l", "gRPC")
arrow(fa, fd, "r", "l"); arrow(svc, col, "b", "t", color="#adb5bd")
arrow(col, ta, "l", "r", "OTLP"); arrow(ta, obs, "l", "r", "telemetry metrics")

lane("Privileged plane (evaluation harness only: separate process, directory, credentials)", 780, 760, 1430, 260, bg="#fff5f5")
truth = box("Truth store\nlatent state, mechanisms,\ninjected causes, counterfactual twins", 800, 820, 400, PRIV, 15)
corr = box("Correlation table\nsim ids ↔ storefront order /\npayment tx / trace ids", 1700, 820, 480, PRIV, 15)
ev = box("Evaluation harness\nLevels 1–4, sealed scenarios", 1250, 820, 400, PRIV, 15, minh=80)
arrow(kern, truth, "b", "t", color="#e03131", style="dashed")
arrow(drv, corr, "b", "t", color="#e03131", style="dashed")
arrow(truth, ev, "r", "l", color="#e03131"); arrow(corr, ev, "l", "r", color="#e03131")
arrow(out, ev, "b", "t", "outputs only", "#495057", "dashed")

X = 2270
lane("Key findings + approval", X, -20, 640, 1010, bg="#fff9db")
text("Verified in source @ 7a88536", X + 20, 30, 600, 19)
for i, t in enumerate([
    "• No seller, offer, inventory or rank field in pb/demo.proto",
    "• Recommendations: random shuffle, max 5 (Java)",
    "• Checkout charges before shipping; no idempotency,\n   no refund on ship failure",
    "• Catalog is Postgres, read every request (docs say JSON)",
    "• Load generator: Locust → k6 → back to Locust",
    "• Fault flags leak into spans (feature_flag attrs,\n   'loyalty_level=gold' error text) → redaction needed",
    "• Kafka + accounting only in compose.full.yaml",
    "• Minimal storefront subset ≈ 1.9 GB vs 4.2 GB default",
]):
    text(t, X + 20, 70 + i * 52, 600, 15)
text("Classification", X + 20, 500, 600, 19)
for i, (c, lab) in enumerate(((REUSE, "Reuse"), (ADAPT, "Adapt (config / DB / file)"), (REPLACE, "Replace (same contract)"),
                              (BYPASS, "Bypass"), (OURS, "New, ours (outside upstream)"), (STATE, "Authoritative state"), (PRIV, "Privileged"))):
    base("rectangle", X + 20, 540 + i * 32, 28, 22, bg=c)
    text(lab, X + 58, 542 + i * 32, 560, 15)
text("Approve", X + 20, 780, 600, 19)
text("A1 architecture  ·  A2 run M0 experiments (Docker)\nA3 reuse prototype kernel (D8)  ·  A4 add Tmall/Alibaba\nA5 interactive human mode stays optional",
     X + 20, 815, 600, 15)
text("* Tmall/Alibaba: not yet verified", X + 20, 900, 600, 13, color="#e03131")

TECH = [
    ("Astronomy Shop", "OpenTelemetry's official demo web shop: ~20 microservices in 10+ languages. Used only as an optional storefront that produces real service behaviour."),
    ("OpenTelemetry (OTel)", "Open standard for traces, metrics and logs from services. Every shop service emits OTel data."),
    ("Spans / traces", "A span is one timed operation (e.g. 'charge card'); a trace links all spans of one request across services."),
    ("OTLP", "OpenTelemetry's wire protocol for sending telemetry between services and the collector."),
    ("OTel Collector", "Service that receives, processes (e.g. redacts) and exports telemetry. We add one config layer to export to our telemetry adapter."),
    ("Redaction", "Removing attributes from telemetry (flag names, fault messages, card data) so hidden scenario answers don't leak to the monitor."),
    ("gRPC + protobuf (demo.proto)", "How shop services call each other; demo.proto defines every request/response. Our sim-recommendation implements the same contract."),
    ("Docker Compose", "Runs the shop's containers together from YAML files. Override files let us switch services off or swap them without editing upstream."),
    ("flagd / OpenFeature", "Feature-flag service and standard API. The shop's 21 fault flags (payment failure, slow shipping...) are read from one JSON file we can edit."),
    ("PostgreSQL", "Database holding the shop's product catalog (read on every request), so prices we sync there show up immediately."),
    ("Valkey", "In-memory key-value store (Redis fork) holding shopping carts, with a 60-minute expiry."),
    ("Kafka", "Message queue: checkout publishes orders to the 'orders' topic. Only used for the optional interactive order bridge."),
    ("Envoy (frontend-proxy)", "Reverse proxy that exposes the shop on port 8080; the session driver sends its HTTP requests here."),
    ("Next.js frontend", "The shop's web app and HTTP API (/api/products, /api/cart, /api/checkout) that the driver calls."),
    ("Locust", "Python load-testing tool; the shop's default traffic generator. Replaced by our driver in experiments, kept for stress tests."),
    ("W3C baggage", "Key-value pairs carried with a request across services (e.g. session.id, synthetic_request=true), used for correlation."),
    ("Observability backends", "Jaeger, Grafana, Prometheus, OpenSearch: dashboards and stores for humans to inspect telemetry. Bypassed; the reasoning engine reads canonical observations instead."),
    ("MCP", "Model Context Protocol: lets AI agents call tools. The shop ships an MCP server; it must not be wired to the reasoning engine."),
    ("Pydantic v2", "Python library for typed, validated data models; used to define the versioned observation and action contracts."),
    ("Event-sourced ledger", "Business state stored as an append-only list of events; current state is derived from it, so it can be audited and replayed."),
    ("Watermark", "The processing-time cut-off: poll(watermark) returns only events that were available by then."),
    ("Snapshot / restore", "Save the full simulator state at a point and resume from it later, giving identical results to an uninterrupted run."),
    ("Record / replay", "Storefront results are recorded, so a headless rerun with those recordings reproduces the business ledger exactly."),
    ("Allowlist / budget gateway", "Checks every action against permitted levers, eligible populations and spending limits, logs it, and rejects invalid ones."),
]
tech_legend(TECH, 0, 1080, cols=3, colw=1000)

out_p = pathlib.Path(__file__).resolve().parents[1] / "astronomy-integration.excalidraw"
print(out_p, save(out_p), "elements")
