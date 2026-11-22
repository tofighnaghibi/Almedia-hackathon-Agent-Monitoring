"""Tests for the showcase UI's API."""
import json

import pytest
from fastapi.testclient import TestClient

from sim.run import backfill
import ui.app as ui

START = "2026-10-25T00:00:00Z"


@pytest.fixture()
def client(tmp_path, monkeypatch):
    db_path = backfill(str(tmp_path / "market.db"), 48, START, seed=1)
    monkeypatch.setattr(ui, "MARKET_DB", db_path)
    monkeypatch.setattr(ui, "INCIDENTS_DIR", tmp_path / "incidents")
    monkeypatch.setattr(ui, "INCOMING_DIR", tmp_path / "incoming")
    return TestClient(ui.app)


def test_index_and_graph_are_served(client):
    for page in ("/", "/classic"):
        assert "Eye of the System" in client.get(page).text, page
    g = client.get("/api/graph").json()
    assert g["nodes"] and g["trees"]


def test_graph_missing_is_a_clear_404(client, monkeypatch, tmp_path):
    monkeypatch.setattr(ui, "GRAPH_JSON", tmp_path / "nope.json")
    r = client.get("/api/graph")
    assert r.status_code == 404 and "graph export not found" in r.json()["detail"]


def test_state_reports_the_clock_and_no_database(client, monkeypatch, tmp_path):
    s = client.get("/api/state").json()
    assert s["ready"] and s["last_hour"] == "2026-10-26T23:00:00Z" and s["injected"] == []
    monkeypatch.setattr(ui, "MARKET_DB", tmp_path / "missing.db")
    assert client.get("/api/state").json()["ready"] is False


def test_incidents_fall_back_to_the_marked_example_then_show_real_ones(client, tmp_path):
    r = client.get("/api/incidents").json()
    assert r["example"] is True and r["incidents"][0]["id"] == "inc-0001"
    d = tmp_path / "incidents"
    d.mkdir()
    (d / "a.json").write_text(json.dumps({"id": "inc-A", "last_seen": "2026-10-26T10:00:00Z", "metric": "orders"}))
    (d / "b.json").write_text(json.dumps({"id": "inc-B", "last_seen": "2026-10-26T12:00:00Z", "metric": "gmv"}))
    (d / "half.json").write_text('{"id": "inc-C", "last_se')           # a file being written: skipped, no crash
    r = client.get("/api/incidents").json()
    assert r["example"] is False and [i["id"] for i in r["incidents"]] == ["inc-B", "inc-A"]


def test_inject_works_and_is_recorded(client):
    r = client.post("/api/inject", json={"kind": "payment_failure", "country": "DE", "platform": "Android", "severity": 0.4})
    assert r.status_code == 200 and r.json()["start"] == "2026-10-26T23:00:00Z"      # the latest hour: it shows at once
    kinds = [i["kind"] for i in client.get("/api/state").json()["injected"]]
    assert kinds == ["payment_failure"]
    assert client.post("/api/inject", json={"kind": "promo", "country": "FR", "hours": 6}).status_code == 200
    assert client.post("/api/inject", json={"kind": "data_delay", "source": "payments"}).status_code == 200


@pytest.mark.parametrize("body", [
    {"kind": "payment_failure", "country": "XX", "platform": "Android"},
    {"kind": "payment_failure", "country": "DE", "platform": "Android", "severity": 1.5},
    {"kind": "promo", "country": "XX"},
    {"kind": "data_delay", "source": "nothing"},
    {"kind": "explode"},
])
def test_inject_rejects_bad_input_and_changes_nothing(client, body):
    assert client.post("/api/inject", json=body).status_code == 422
    assert client.get("/api/state").json()["injected"] == []


def test_inject_without_a_database_is_a_409(client, monkeypatch, tmp_path):
    monkeypatch.setattr(ui, "MARKET_DB", tmp_path / "missing.db")
    assert client.post("/api/inject", json={"kind": "promo", "country": "FR"}).status_code == 409


def test_new_metric_is_handed_over_as_a_file(client, tmp_path):
    y = "name: refund_rate\nformula: refunds / orders\nunit: ratio\n"
    r = client.post("/api/metric", json={"yaml_text": y})
    assert r.status_code == 200 and (tmp_path / "incoming" / "refund_rate.yaml").read_text() == y


@pytest.mark.parametrize("text", ["name: [unclosed", "just a string", "description: no name\nformula: a / b", "name: x\n"])
def test_new_metric_rejects_bad_definitions(client, tmp_path, text):
    assert client.post("/api/metric", json={"yaml_text": text}).status_code == 422
    assert not (tmp_path / "incoming").exists()


def test_metric_name_cannot_escape_the_folder(client, tmp_path):
    r = client.post("/api/metric", json={"yaml_text": "name: ../../evil\nformula: a / b\n"})
    assert r.status_code == 200 and r.json()["name"] == "evil"
    assert (tmp_path / "incoming" / "evil.yaml").exists()


def test_clicking_the_same_button_twice_does_not_stack(client):
    body = {"kind": "payment_failure", "country": "DE", "platform": "Web", "severity": 0.4}
    assert client.post("/api/inject", json=body).status_code == 200
    r = client.post("/api/inject", json=body)
    assert r.status_code == 409 and "already scheduled" in r.json()["detail"]
    assert len(client.get("/api/state").json()["injected"]) == 1


def test_the_page_marks_the_controls_as_demo(client):
    html = client.get("/classic").text
    assert 'class="demo-tag"' in html and "not part of the monitor" in html


# ------------------------------------------------------------- data browser
def test_data_browser_lists_tables_and_pages_through_rows(client):
    tables = {t["name"]: t for t in client.get("/api/db/tables").json()}
    assert tables["facts_hourly"]["rows"] == 48 * 180 and "payment_successes" in tables["facts_hourly"]["columns"]
    r = client.get("/api/db/rows", params={"table": "facts_hourly", "limit": 5}).json()
    assert len(r["rows"]) == 5 and r["total"] == 48 * 180 and r["rows"][0][0] == "2026-10-26T23:00:00Z"   # newest first
    r = client.get("/api/db/rows", params={"table": "facts_hourly", "country": "DE", "platform": "iOS", "category": "fashion",
                                           "channel": "organic", "ts_from": "2026-10-26T20:00:00Z", "limit": 50}).json()
    assert r["total"] == 4 and {row[1] for row in r["rows"]} == {"DE"}


def test_data_browser_refuses_unknown_tables_and_cannot_be_used_for_sql(client):
    assert client.get("/api/db/rows", params={"table": "sqlite_master"}).status_code == 422
    assert client.get("/api/db/rows", params={"table": "facts_hourly; DROP TABLE facts_hourly"}).status_code == 422
    r = client.get("/api/db/rows", params={"table": "facts_hourly", "country": "DE' OR '1'='1"}).json()
    assert r["total"] == 0                                              # a value, never SQL
    assert client.get("/api/db/rows", params={"table": "facts_hourly", "limit": 100000}).json()["rows"].__len__() == 500
    assert "Simulator data" in client.get("/data").text


def test_data_browser_shows_the_answer_table_but_cannot_change_anything(client):
    client.post("/api/inject", json={"kind": "promo", "country": "FR", "hours": 6})
    r = client.get("/api/db/rows", params={"table": "ground_truth"}).json()
    assert [row[1] for row in r["rows"]] == ["promo"]
    assert client.post("/api/db/rows", json={}).status_code == 405


def test_the_three_other_fault_kinds_can_be_injected_and_checked(client):
    ok = [{"kind": "stockout_surge", "category": "electronics"},
          {"kind": "search_regression", "country": "DE"},
          {"kind": "ad_budget_exhausted", "country": "FR", "hours": 12}]
    for body in ok:
        assert client.post("/api/inject", json=body).status_code == 200, body
    assert [i["kind"] for i in client.get("/api/state").json()["injected"]] == [b["kind"] for b in ok]
    for bad in ({"kind": "stockout_surge", "category": "cars"}, {"kind": "search_regression", "country": "XX"},
                {"kind": "ad_budget_exhausted"}):
        assert client.post("/api/inject", json=bad).status_code == 422, bad
    assert client.post("/api/inject", json=ok[1]).status_code == 409           # the same click twice


def test_the_page_offers_the_fault_kinds(client):
    html = client.get("/classic").text
    for word in ("stockout_surge", "search_regression", "ad_budget_exhausted", "Inject fault"):
        assert word in html


# ------------------------------------------------------------------ domains
def test_domains_list_real_metrics_once_each(client):
    domains = client.get("/api/domains").json()
    graph = client.get("/api/graph").json()
    known = {n["id"] for n in graph["nodes"] if n["kind"] == "metric"}
    listed = [m for d in domains for m in d["metrics"]]
    assert len({d["id"] for d in domains}) == len(domains)
    assert not [m for m in listed if m not in known], "a domain lists a metric the graph does not have"
    assert len(listed) == len(set(listed)), "a metric is in two domains"
    assert {"payments", "ads", "inventory", "search"} <= {d["id"] for d in domains}


def test_the_page_has_the_domain_selector_and_keeps_the_demo_controls_folded(client):
    html = client.get("/classic").text
    assert 'id="domains"' in html and "Business domain" in html
    assert '<details id="demo-controls">' in html and "open" not in html.split('<details id="demo-controls"')[1][:3]


# ------------------------------------------------------------- visual style
def _tokens(block):
    import re
    return dict(re.findall(r"--([a-z-]+):\s*(#[0-9A-Fa-f]{6})", block))


def _contrast(a, b):
    def lum(h):
        h = h.lstrip("#")
        r, g, bl = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
        f = lambda c: c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
        return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(bl)
    hi, lo = sorted((lum(a), lum(b)), reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def test_console_colours_meet_contrast_in_both_themes():
    css = (ui.Path(ui.__file__).parent / "static" / "console.css").read_text(encoding="utf-8")
    light = _tokens(css.split("@media")[0])
    dark = _tokens(css.split(':root[data-theme="dark"] {')[1].split("}")[0])
    text_pairs = [("ink", "bg"), ("ink", "block"), ("muted", "bg"), ("muted", "block"), ("sel-fg", "sel-bg"),
                  ("hot", "block"), ("hot", "hot-bg"), ("amber", "block"), ("amber", "amber-bg"), ("ok", "block"), ("ok", "ok-bg")]
    for name, t in (("light", light), ("dark", dark)):
        for a, b in text_pairs:
            assert _contrast(t[a], t[b]) >= 4.5, f"{name}: {a} on {b}"
        for a, b in (("accent", "bg"), ("accent", "block"), ("edge", "block")):
            assert _contrast(t[a], t[b]) >= 1.8 if a == "edge" else _contrast(t[a], t[b]) >= 3, f"{name}: {a} on {b}"


def test_style_stays_flat_and_both_pages_share_it(client):
    import re
    css = (ui.Path(ui.__file__).parent / "static" / "console.css").read_text(encoding="utf-8")
    rules = re.sub(r"/\*.*?\*/", "", css, flags=re.S)                  # comments may talk about gradients
    assert "gradient" not in rules and "box-shadow" not in rules and "text-shadow" not in rules
    for page in ("/classic", "/data"):
        html = client.get(page).text
        assert "/static/console.css" in html and "gradient" not in html and "box-shadow" not in html
        assert "prefers-color-scheme" not in html                      # themes live in one place
    assert client.get("/static/console.css").status_code == 200


def test_title_and_domain_selector_stay_at_the_top_when_scrolling(client):
    html = client.get("/classic").text
    head, body = html.split("</head>")
    assert ".sticky-top { position: sticky; top: 0;" in head
    block = body.split('<div class="sticky-top">')[1].split("<main>")[0]
    assert 'id="domains"' in block and "Eye of the System" in block          # both sit inside the sticky container
    assert 'id="demo-controls"' not in block.split('id="domains"')[0]


def test_the_graph_card_stays_in_view_next_to_a_long_report(client):
    html = client.get("/classic").text
    assert 'id="graph-card"' in html and "#graph-card { position: sticky;" in html
    assert "--top-h" in html and "setTop" in html                      # the offset follows the real height of the top bar


# ----------------------------------------------------------------- rollback
def test_a_fault_can_be_ended_from_the_page_and_stops_being_active(client):
    assert client.post("/api/inject", json={"kind": "payment_failure", "country": "DE", "platform": "Android"}).status_code == 200
    injected = client.get("/api/state").json()["injected"]
    assert injected[0]["active"] is True and isinstance(injected[0]["id"], int)
    r = client.post("/api/end", json={"id": injected[0]["id"]})
    assert r.status_code == 200 and r.json()["ended_from"] == "2026-10-26T23:00:00Z"
    assert client.get("/api/state").json()["injected"][0]["active"] is False
    assert client.post("/api/end", json={"id": injected[0]["id"]}).status_code == 409      # already ended
    assert client.post("/api/end", json={"id": 999}).status_code == 404
    # and the same fault can be started again
    assert client.post("/api/inject", json={"kind": "payment_failure", "country": "DE", "platform": "Android"}).status_code == 200


def test_the_page_has_the_end_control_next_to_the_other_demo_controls(client):
    html = client.get("/classic").text
    assert 'id="end-grp"' in html and "End it (rollback)" in html and "/api/end" in html
    assert html.index('id="end-grp"') < html.index('id="demo-controls"') + 4000        # inside the folded demo bar


def test_static_files_are_not_cached_so_a_changed_style_shows_at_once(client):
    r = client.get("/static/console.css")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    assert "[hidden] { display: none !important; }" in r.text


def test_pages_link_the_stylesheet_with_its_modification_time(client):
    import re
    for page in ("/classic", "/data"):
        r = client.get(page)
        assert re.search(r'/static/console\.css\?v=\d+"', r.text), page
        assert r.headers["cache-control"] == "no-cache"


# ------------------------------------------------------- one odd file must not stop the feed (issue 4)
def test_odd_incident_files_are_skipped_and_counted_not_fatal(client, tmp_path):
    d = tmp_path / "incidents"
    d.mkdir()
    good = {"id": "inc-0001", "metric": "orders", "status": "open", "last_seen": "2026-10-26T12:00:00Z"}
    (d / "good.json").write_text(json.dumps(good))
    (d / "later.json").write_text(json.dumps(dict(good, id="inc-0002", last_seen="2026-10-26T15:00:00Z")))
    (d / "null_seen.json").write_text(json.dumps(dict(good, id="inc-0003", last_seen=None)))         # kept, sorted last
    (d / "number_seen.json").write_text(json.dumps(dict(good, id="inc-0004", last_seen=12345)))
    (d / "a_list.json").write_text("[1, 2]")
    (d / "null.json").write_text("null")
    (d / "nan.json").write_text('{"id": "inc-0005", "metric": "orders", "change": {"z": NaN}}')      # json.dumps writes NaN
    (d / "infinity.json").write_text('{"id": "inc-0006", "metric": "orders", "z": Infinity}')
    (d / "state.json").write_text(json.dumps({"tick": 7}))                                              # not an incident
    (d / "number_id.json").write_text(json.dumps({"id": 7, "metric": "orders"}))
    (d / "no_metric.json").write_text(json.dumps({"id": "inc-0007"}))
    (d / "half.json").write_text('{"id": "inc-0008", "metr')
    r = client.get("/api/incidents")
    assert r.status_code == 200
    body = r.json()
    assert body["example"] is False
    assert [i["id"] for i in body["incidents"]][:2] == ["inc-0002", "inc-0001"] and {i["id"] for i in body["incidents"]} == {"inc-0001", "inc-0002", "inc-0003", "inc-0004"}
    assert body["skipped"] == 8


def test_the_example_shows_only_while_the_incident_folder_does_not_exist(client, tmp_path):
    assert client.get("/api/incidents").json()["example"] is True                 # no folder yet: the monitor has not started
    (tmp_path / "incidents").mkdir()
    r = client.get("/api/incidents").json()
    assert r["example"] is False and r["incidents"] == []                          # an empty folder means nothing found


def test_non_alert_notes_are_shown_and_other_subfolders_are_ignored(client, tmp_path):
    d = tmp_path / "incidents"
    (d / "notes").mkdir(parents=True)
    (d / "notes" / "inc-0009.json").write_text(json.dumps({"id": "inc-0009", "metric": "orders", "status": "explained"}))
    other = d / "other"
    other.mkdir()
    (other / "stray.json").write_text(json.dumps({"id": "stray", "metric": "orders", "status": "open"}))
    assert [i["id"] for i in client.get("/api/incidents").json()["incidents"]] == ["inc-0009"]


# ------------------------------------------------------------- the answer to adding a metric (issue 3)
def test_the_result_of_a_new_metric_is_read_from_its_result_file(client, tmp_path):
    assert client.get("/api/metric/refund_rate").status_code == 404                # not answered yet
    client.post("/api/metric", json={"yaml_text": "name: refund_rate\nformula: refunds / orders\n"})
    assert client.get("/api/metric/refund_rate").status_code == 404
    (tmp_path / "incoming" / "refund_rate.result.json").write_text(json.dumps({"status": "validated", "message": "placed under orders"}))
    r = client.get("/api/metric/refund_rate")
    assert r.status_code == 200 and r.json()["message"] == "placed under orders"
    (tmp_path / "incoming" / "half.result.json").write_text('{"status": ')
    assert client.get("/api/metric/half").status_code == 404                       # half written: try again later
    assert client.get("/api/metric/..%2Fsecret").status_code in (404, 422)


def test_the_page_reads_the_typed_links_and_survives_odd_fields(client):
    html = client.get("/classic").text
    for word in ("may_affect", "Breaks down by", "May be affected by", "Context", "Defined from", "Status", "Origin",
                 "Where in the segments", "/api/metric/", "function str(", "function list("):
        assert word in html, word
    assert "explained" in client.get("/static/console.css").text                   # grey, not counted


def test_page_metric_yaml_is_accepted_by_brain(client, tmp_path):
    import re
    from brain.metric_spec import load_specs
    html = client.get('/classic').text
    literal = re.search(r"var y = '([^']+)';", html).group(1)
    yaml_text = literal.replace('\\n', '\n')
    response = client.post('/api/metric', json={'yaml_text': yaml_text})
    assert response.status_code == 200
    specs = load_specs(tmp_path / 'incoming' / 'refund_rate.yaml')
    assert len(specs) == 1 and specs[0].name == 'refund_rate'


def test_the_report_opens_with_the_model_narrative_only_when_it_passed_the_check():
    """brain/narrate.py writes `narrative` into the incident: a text from the model whose numbers were all checked
    against the evidence (source 'model'), or a failure with source 'none'. Only the first is shown, as "In brief",
    before "What changed", and the page says who wrote it, from what, when, and what was checked."""
    from tests.test_ui_runtime import run_ui
    run_ui(r"""
const story = 'Payment success in DE x Android fell to 0.662 from an expected 0.938.';
const base = {id:'inc-9',metric:'contribution',status:'open',change:{observed:607.9,expected:849.1,delta_pct:-28.4},
  report:'template report'};
const shown = narrative => {
  incidents = [Object.assign({}, base, narrative === undefined ? {} : {narrative})]; selInc = 'inc-9'; mode = 'incident';
  renderDetail(); return $('detail').textContent;
};
let page = shown({text:story,source:'model',model:'claude-opus-5-5',as_of:'2026-11-22T23:00:00Z',status:'open',
                  checked:'every number appears in the evidence',reason:null});
assert.ok(page.includes('In brief'), page);
assert.ok(page.includes(story));
assert.ok(page.indexOf('In brief') < page.indexOf('What changed'), 'In brief comes first');
assert.ok(page.includes('Written by claude-opus-5-5 from this report'));
assert.ok(page.includes('every number in it was checked against the evidence'));
assert.ok(page.includes('template report'));                       // the template summary stays
for (const failed of [{text:null,source:'none',model:'claude-opus-5-5',reason:'timeout after 20 s'},
                      {text:'',source:'model',model:'claude-opus-5-5'}, {text:story,source:'none'}, 'odd', null, undefined]) {
  page = shown(failed);
  assert.ok(!page.includes('In brief'), JSON.stringify(failed));
  assert.ok(page.includes('What changed'));
}
""")


def test_metrics_the_monitor_also_watched_move_count_for_their_domain(client):
    """A watched metric in the investigation trail (stockout_rate next to an ad fault that began in the same hour) lights its own domain."""
    html = client.get("/classic").text
    assert "t.action === 'watch'" in html and "meta[t.target]" in html
    assert html.count("t.action === 'watch'") == 2          # the domain counts and the highlight both use it


def test_page_faults_outlast_the_demo_script_and_the_ad_fault_keeps_its_shape(client):
    """The page's faults end by themselves (issue 11). At 2 s per simulated hour the script's rollback step comes
    50 s after the click and the close 130 s after it: a 12 hour fault would be over after 24 s, before the rollback.
    The ad budget fault keeps 12 hours, because its shape (boost for hours // 3, then nothing) depends on it."""
    import re
    html = client.get("/classic").text
    hours = {kind: int(h) for kind, h in re.findall(r"\['(\w+)', '[^']+', \[[^\]]*\], (\d+), (?:[\d.]+|null)\]", html)}
    assert set(hours) == {"payment_failure", "stockout_surge", "search_regression", "ad_budget_exhausted",
                          "traffic_drop", "feed_unavailable", "discount_push"}
    seconds_per_hour, click_to_close = 2, 130
    for kind in ("payment_failure", "stockout_surge", "search_regression", "traffic_drop", "feed_unavailable",
                 "discount_push"):
        assert hours[kind] * seconds_per_hour > click_to_close, kind
    assert hours["ad_budget_exhausted"] == 12
    assert "hours: hours, when: 'now'" in html      # the button sends the value of its own fault


def test_the_candidate_designs_are_served_beside_the_current_console(client, tmp_path, monkeypatch):
    page = ui.STATIC / "v9" / "index.html"
    monkeypatch.setitem(ui.DESIGNS, 9, "Test design")
    page.parent.mkdir(exist_ok=True)
    try:
        page.write_text("<!doctype html><title>Eye of the System v9</title>", encoding="utf-8")
        assert "Eye of the System v9" in client.get("/v/9").text
        listing = client.get("/designs").text
        assert 'href="/v/9"' in listing and 'href="/"' in listing
    finally:
        page.unlink()
        page.parent.rmdir()
    assert client.get("/v/9").status_code == 404
    assert client.get("/v/77").status_code == 404


# ------------------------------------------------- the main console is design 3, the old one stays at /classic
def _main_page(client) -> tuple[str, str]:
    """The main console's HTML as served at / and the script it loads, fetched through the link in the page."""
    import re
    html = client.get("/").text
    src = re.search(r'<script src="(/static/v3/app\.js\?v=\d+)"></script>', html).group(1)
    return html, client.get(src).text


def test_the_main_console_is_design_3_with_its_own_files_fetched_again_when_changed(client):
    import re
    r = client.get("/")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    html = r.text
    for path in ("/static/v3/style.css", "/static/v3/route.js", "/static/v3/app.js"):
        version = int((ui.STATIC / path.removeprefix("/static/")).stat().st_mtime)
        assert f'{path}?v={version}"' in html, path               # a changed file is fetched again on the next reload
        assert client.get(f"{path}?v={version}").status_code == 200
    assert "/static/console.css" not in html                       # the classic style is not loaded on the canvas
    for word in ('id="canvas"', 'id="feed"', 'id="sheet"', 'id="bench"', "Replay the investigation", 'href="/data"'):
        assert word in html, word
    assert not re.search(r'/static/v3/(style\.css|route\.js|app\.js)"', html), "a file without its version"
    assert html.index("/static/v3/route.js?v=") < html.index("/static/v3/app.js?v=")   # app.js uses V3Route


def test_the_classic_console_is_kept_at_classic(client):
    r = client.get("/classic")
    assert r.status_code == 200 and r.headers["cache-control"] == "no-cache"
    assert '<details id="demo-controls">' in r.text and "/static/console.css?v=" in r.text
    assert "/static/v3/" not in r.text


def test_the_designs_page_names_the_main_and_the_classic_console(client):
    listing = client.get("/designs").text
    assert 'href="/classic"' in listing and "Classic console" in listing
    main = listing.split('href="/v/3"')[1].split("</li>")[0]
    assert "main console" in main.lower()                          # design 3 is the one served at /


def test_the_main_console_moves_a_box_with_the_right_button_and_puts_them_back(client):
    """A box on the canvas is moved by dragging it with the right button (no context menu on the canvas); the moves
    are kept in this browser, and the Layout button, shown only after a move, puts every box back."""
    import re
    html, js = _main_page(client)
    assert "right-drag a box to place it" in html
    assert re.search(r'<button id="z-layout"[^>]*\bhidden>Layout</button>', html)
    assert "c.addEventListener('contextmenu', function (e) { e.preventDefault(); });" in js
    assert "if (e.button !== 2) return;" in js and "closest('.node[data-key]')" in js
    assert "store('v3-moved', JSON.stringify(moved))" in js         # kept in this browser across reloads
    assert "$('z-layout').hidden = !Object.keys(moved).length;" in js
    assert "$('z-layout').onclick = function () { moved = {}; saveMoved(); drawGraph(); };" in js


def _object(js: str, kind: str) -> str:
    """The object literal the page posts for one kind, as written in the script: { kind: '<kind>', ... }."""
    import re
    m = re.search(r"\{\s*kind:\s*'" + kind + r"'[^{}]*\}", js)
    assert m, f"the main console has no request body for {kind}"
    return m.group(0)


def _fields(literal: str, values: dict) -> dict:
    """A request body from the page's object literal; a value read from a control or an argument comes from `values`."""
    import re
    out = {}
    for key, raw in re.findall(r"(\w+):\s*([^,}]+)", literal):
        raw = raw.strip()
        if raw.startswith("'"):
            out[key] = raw.strip("'")
        elif re.fullmatch(r"-?\d+(\.\d+)?", raw):
            out[key] = float(raw) if "." in raw else int(raw)
        else:
            out[key] = values[raw]
    return out


def test_the_main_console_sends_the_demo_requests_and_the_api_accepts_them(client, tmp_path):
    """The bodies the demo uses (docs/demo.md), read from the main console's script and posted as the page posts them:
    the payment fault, its rollback, the promo, the mix shift, the late data, refund_rate and the duplicate metric."""
    import re
    import yaml
    from brain.metric_spec import load_specs
    html, js = _main_page(client)

    # the payment fault: one row of the fault list, sent with its own hours and severity
    row = re.search(r"\['payment_failure', '[^']+', \['country', 'platform'\], (\d+), ([\d.]+)\]", js)
    assert row and "body.severity = k[4]" in js and re.search(r"var body = \{ kind: kind, hours: hours, when: 'now' \}", js)
    assert "post('/api/inject', body)" in js and 'id="f-go"' in html
    fault = {"kind": "payment_failure", "country": "DE", "platform": "Android", "hours": int(row.group(1)),
             "severity": float(row.group(2)), "when": "now"}
    assert client.post("/api/inject", json=fault).status_code == 200

    # the rollback of that fault
    assert re.search(r"post\('/api/end', \{ id: id \}\)", js) and "End it (rollback)" in html
    first = client.get("/api/state").json()["injected"][0]["id"]
    assert client.post("/api/end", json={"id": first}).status_code == 200

    # the innocent changes and the late data
    controls = {"$('pr-country').value": "FR", "$('dd-source').value": "payments", "category": "fashion"}
    for kind, button in (("promo", 'id="pr-go"'), ("mix_shift", 'id="ms-go"'), ("data_delay", 'id="dd-go"')):
        body = _fields(_object(js, kind), controls)
        assert body["kind"] == kind and body["when"] == "now" and button in html, kind
        assert client.post("/api/inject", json=body).status_code == 200, body
    assert [i["kind"] for i in client.get("/api/state").json()["injected"]] == [
        "payment_failure", "promo", "mix_shift", "data_delay"]

    # the new metric (the query only: the brain finds the formula) and the duplicate under another name
    defs = {var: re.search(rf"var {var} = '([^']+)';", js).group(1).replace("\\n", "\n") for var in ("y", "dup")}
    assert 'id="nm-go">Add refund_rate<' in html and re.search(r'id="nm-dup"[^>]*>Add a duplicate metric<', html)
    spec, dup = yaml.safe_load(defs["y"]), yaml.safe_load(defs["dup"])
    assert spec["name"] == "refund_rate" and "formula" not in spec and "SUM(refunds)" in spec["sql"]
    assert dup["name"] == "payment_rate" and "formula" not in dup and "SUM(payment_successes)" in dup["sql"]
    assert "post('/api/metric', { yaml_text: yamlText })" in js
    for text in defs.values():
        assert client.post("/api/metric", json={"yaml_text": text}).status_code == 200
    assert [s.name for s in load_specs(tmp_path / "incoming" / "refund_rate.yaml")] == ["refund_rate"]


def test_main_console_faults_outlast_the_demo_script(client):
    """The same rule as for the classic page: a fault lasts longer than the 130 s from the click to the close."""
    import re
    _, js = _main_page(client)
    hours = {kind: int(h) for kind, h in re.findall(r"\['(\w+)', '[^']+', \[[^\]]*\], (\d+), (?:[\d.]+|null)\]", js)}
    assert set(hours) == {"payment_failure", "stockout_surge", "search_regression", "ad_budget_exhausted",
                          "traffic_drop", "feed_unavailable", "discount_push"}
    for kind, h in hours.items():
        assert h == 12 if kind == "ad_budget_exhausted" else h * 2 > 130, kind
