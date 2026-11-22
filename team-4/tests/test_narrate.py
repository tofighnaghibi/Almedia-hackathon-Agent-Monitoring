"""Narration of an incident by a language model (README 3.7): written from the structured evidence only,
checked against it, never on the monitor's path, and with the template report as the fallback."""
import json
import threading
import time
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from brain import narrate
from brain.narrate import Narrator, evidence, numbers_backed

REQ = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")

BODY = {
    "id": "inc-0001", "status": "ongoing", "first_seen": "2026-11-26T06:00:00Z", "last_seen": "2026-11-26T09:00:00Z",
    "metric": "contribution", "segment": {"country": "DE", "platform": "Android"},
    "change": {"observed": 2391.5, "expected": 3570.7, "delta": -1179.2, "delta_pct": -33.02,
               "window": {"start": "2026-11-26T05:00:00Z", "end": "2026-11-26T10:00:00Z"}},
    "drivers": [{"metric": "payment_success_rate", "observed": 0.61, "expected": 0.939, "role": "cause candidate"}],
    "impact": {"contribution_eur": -885.3, "orders": -165.9},
    "explained_share": 1.0,
    "localization": [{"dimension": "country x platform", "segment": "DE x Android", "share_of_delta": 0.99, "chosen": True}],
    "hypotheses": [
        {"cause": "release payments-sdk: payments-sdk 4.2 rollout (DE, Android)", "kind": "release", "status": "supported",
         "score": 1.0, "evidence": ["scope DE x Android is exactly where the change is"]},
        {"cause": "late or missing data", "kind": "freshness", "status": "rejected", "score": 0.0,
         "evidence": ["every source these metrics depend on had arrived"]},
    ],
    "trail": [{"action": "watch", "target": "payment_success_rate", "result": "0.61 against an expected 0.939"}],
    "uncertain": ["Traffic is taken as given."],
    "report": "payment_success_rate fell to 0.61 from an expected 0.939 in DE x Android, starting 2026-11-26 05:00 UTC.",
}

GOOD = ("Payment success in DE x Android fell to 0.61 from an expected 0.939 from 05:00 UTC on 2026-11-26. "
        "So far that cost 885 EUR of contribution and 166 orders. The supported cause is the payments-sdk 4.2 rollout "
        "in DE on Android; late data was ruled out.")


def reply(text, stop="end_turn", model="claude-opus-5-5"):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], stop_reason=stop, model=model)


class FakeClient:
    """Stands in for anthropic.Anthropic: records each request and answers from a script."""

    def __init__(self, *answers, delay=0.0):
        self.answers, self.delay, self.calls = list(answers), delay, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kw):
        self.calls.append(kw)
        if self.delay:
            time.sleep(self.delay)
        answer = self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]
        if isinstance(answer, Exception):
            raise answer
        return answer


def wait(n: Narrator, timeout=5.0):
    """Collect finished narrations, waiting for the background work like a later tick would."""
    end, out = time.time() + timeout, []
    while time.time() < end:
        out += n.ready()
        if not n.busy():
            return out + n.ready()
        time.sleep(0.01)
    raise AssertionError("narration did not finish")


# ------------------------------------------------------------------------------- what is sent and checked
def test_the_evidence_holds_the_facts_and_not_the_long_trail():
    e = evidence(BODY)
    assert e["metric"] == "contribution" and e["segment"] == {"country": "DE", "platform": "Android"}
    assert e["hypotheses"][0]["status"] == "supported" and e["impact"]["orders"] == -165.9
    assert "trail" not in e and e["report"] == BODY["report"]


@pytest.mark.parametrize("text, ok", [
    (GOOD, True),                                                     # rounded, unsigned, dates and hours
    ("Orders fell by 99%.", True),                                    # 0.99 written as a percentage
    ("It cost 1,179.2 EUR.", True),                                   # thousands separator
    ("It cost 1,200 EUR.", False),                                    # a number the evidence does not hold
    ("Payment success fell to 0.52.", False),
    ("The fault began 3 days ago.", False),
])
def test_every_number_of_a_narration_must_be_in_the_evidence(text, ok):
    good, reason = numbers_backed(text, evidence(BODY))
    assert good is ok
    if not ok:
        assert "not in the evidence" in reason


# ------------------------------------------------------------------------------- the background job
def test_a_narration_comes_back_checked_and_the_request_is_bounded():
    client = FakeClient(reply(GOOD))
    n = Narrator(client=client)
    n.submit("inc-0001", BODY)
    [(key, story)] = wait(n)
    assert key == "inc-0001" and story["source"] == "model" and story["text"] == GOOD
    assert story["as_of"] == BODY["last_seen"] and story["status"] == "ongoing"
    [call] = client.calls
    assert call["model"] == narrate.MODEL and call["output_config"] == {"effort": "low"}
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]
    sent = json.loads(call["messages"][0]["content"].split("\n", 1)[1])
    assert sent == json.loads(json.dumps(evidence(BODY)))             # only the evidence, as JSON


def test_a_narration_with_an_invented_number_is_dropped_and_not_asked_again():
    client = FakeClient(reply("Orders fell by 412 since Monday."))
    n = Narrator(client=client)
    n.submit("inc-0001", BODY)
    [(_, story)] = wait(n)
    assert story["source"] == "none" and story["text"] is None and "412" in story["reason"]
    n.submit("inc-0001", BODY)                                        # same milestone: no second request
    assert not n.busy() and len(client.calls) == 1


def test_the_same_milestone_is_narrated_once_and_a_new_one_again():
    client = FakeClient(reply(GOOD))
    n = Narrator(client=client)
    n.submit("inc-0001", BODY)
    wait(n)
    n.submit("inc-0001", dict(BODY, last_seen="2026-11-26T10:00:00Z", impact={"contribution_eur": -990.0}))
    assert len(client.calls) == 1                                     # numbers moved, the story did not
    n.submit("inc-0001", dict(BODY, status="resolved"))
    wait(n)
    assert len(client.calls) == 2


def test_a_cost_that_doubles_is_a_new_milestone_so_the_story_keeps_up_with_the_figures():
    """The figures above the narration grow every tick; the narration is asked again only when the cost in
    euros has doubled since (one request per power of two), so a long incident costs a few requests."""
    client = FakeClient(reply(GOOD))
    n = Narrator(client=client)
    n.submit("inc-0001", dict(BODY, impact={"contribution_eur": -345.0, "orders": -68.0}))
    wait(n)
    n.submit("inc-0001", dict(BODY, impact={"contribution_eur": -500.0, "orders": -99.0}))
    assert len(client.calls) == 1                                     # 345 and 500: the same power of two
    n.submit("inc-0001", dict(BODY, impact={"contribution_eur": -700.0, "orders": -140.0}))
    wait(n)
    assert len(client.calls) == 2                                     # doubled: told again


def test_no_credit_turns_narration_off_for_the_run_with_the_reason():
    err = anthropic.BadRequestError("Your credit balance is too low to access the Anthropic API.",
                                    response=httpx2.Response(400, request=REQ), body=None)
    client = FakeClient(err)
    n = Narrator(client=client)
    n.submit("inc-0001", BODY)
    [(_, story)] = wait(n)
    assert story["source"] == "none" and "credit balance is too low" in story["reason"]
    assert n.status.startswith("off") and "credit" in n.status
    n.submit("inc-0002", dict(BODY, id="inc-0002"))
    assert not n.busy() and len(client.calls) == 1


def test_the_reason_is_the_api_sentence_not_the_raw_error_body():
    body = {"type": "error", "error": {"type": "invalid_request_error", "message": "Your credit balance is too low."},
            "request_id": "req_123"}
    err = anthropic.BadRequestError("Error code: 400 - " + json.dumps(body), response=httpx2.Response(400, request=REQ),
                                    body=body)
    n = Narrator(client=FakeClient(err))
    n.submit("inc-0001", BODY)
    [(_, story)] = wait(n)
    assert story["reason"] == "the API refused the request: Your credit balance is too low."


def test_a_timeout_waits_out_a_cooldown_then_tries_the_milestone_again(monkeypatch):
    client = FakeClient(anthropic.APITimeoutError(request=REQ), reply(GOOD))
    clock = [1000.0]
    monkeypatch.setattr(narrate.time, "monotonic", lambda: clock[0])
    n = Narrator(client=client)
    n.submit("inc-0001", BODY)
    [(_, story)] = wait(n)
    assert story["source"] == "none" and "timed out" in story["reason"] and n.status == "on"
    n.submit("inc-0001", BODY)
    assert len(client.calls) == 1                                     # cooling down
    clock[0] += narrate.COOLDOWN_S + 1
    n.submit("inc-0001", BODY)
    [(_, story)] = wait(n)
    assert story["source"] == "model" and len(client.calls) == 2


@pytest.mark.parametrize("answer, word", [
    (reply("", stop="refusal"), "declined"),
    (reply(GOOD[:40], stop="max_tokens"), "cut off"),
    (reply("**Summary**\n\n" + GOOD), "plain"),
])
def test_an_answer_that_is_not_a_plain_finished_text_is_not_used(answer, word):
    n = Narrator(client=FakeClient(answer))
    n.submit("inc-0001", BODY)
    [(_, story)] = wait(n)
    assert story["source"] == "none" and word in story["reason"]


def test_without_a_key_narration_is_off_and_says_why(monkeypatch, tmp_path):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_AUTH_TOKEN", raising=False)
    n = Narrator(env_file=tmp_path / "missing.env")
    assert n.status.startswith("off") and "key" in n.status
    n.submit("inc-0001", BODY)
    assert not n.busy() and n.ready() == []


def test_a_client_that_cannot_be_built_turns_narration_off_instead_of_stopping_the_monitor(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test-0000")

    def broken(*a, **k):
        raise RuntimeError("Could not determine home directory.")
    monkeypatch.setattr(anthropic, "Anthropic", broken)
    n = Narrator(env_file=None)
    assert n.status.startswith("off") and "home directory" in n.status
    n.submit("inc-0001", BODY)
    assert not n.busy()


def test_submit_never_waits_for_the_model():
    n = Narrator(client=FakeClient(reply(GOOD), delay=0.5))
    t = time.time()
    n.submit("inc-0001", BODY)
    assert time.time() - t < 0.1 and n.busy()
    wait(n)


def test_the_key_is_never_part_of_a_reason_or_a_status(monkeypatch, tmp_path):
    secret = "sk-ant-test-" + "x" * 20
    err = anthropic.AuthenticationError(f"invalid x-api-key {secret}", response=httpx2.Response(401, request=REQ), body=None)
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    n = Narrator(client=FakeClient(err))
    n.submit("inc-0001", BODY)
    [(_, story)] = wait(n)
    assert secret not in story["reason"] and secret not in n.status


def test_only_one_request_runs_per_incident_at_a_time():
    gate = threading.Event()

    class Slow(FakeClient):
        def _create(self, **kw):
            self.calls.append(kw)
            gate.wait(2)
            return reply(GOOD)

    client = Slow(reply(GOOD))
    n = Narrator(client=client)
    n.submit("inc-0001", BODY)
    n.submit("inc-0001", dict(BODY, status="resolved"))               # a new milestone while the first runs
    assert len(client.calls) <= 1
    gate.set()
    wait(n)
    n.submit("inc-0001", dict(BODY, status="resolved"))
    wait(n)
    assert len(client.calls) == 2


# ------------------------------------------------------------------------------- inside the incident book
import shutil                                       # noqa: E402

import pandas as pd                                 # noqa: E402

from brain.data import Store                        # noqa: E402
from brain.graph import MetricGraph                 # noqa: E402
from brain.incidents import Book                    # noqa: E402
from brain.monitor import Monitor                   # noqa: E402
from sim import db as simdb                         # noqa: E402
from sim import inject                              # noqa: E402
from sim.run import backfill                        # noqa: E402

FAULT = pd.Timestamp("2026-11-03T12:00:00Z")
H = pd.Timedelta(hours=1)


@pytest.fixture(scope="module")
def faulty_db(tmp_path_factory):
    path = backfill(str(tmp_path_factory.mktemp("narrate") / "m.db"), 10 * 24, "2026-10-25T00:00:00Z", seed=11)
    con = simdb.connect(path)
    try:
        inject.payment_failure(con, "DE", "Android", FAULT, 0.4, end=FAULT + 3 * H)
    finally:
        con.close()
    return path


class Echo(FakeClient):
    """Answers with the template report of the incident it is sent, so every number is backed."""

    def _create(self, **kw):
        self.calls.append(kw)
        if self.delay:
            time.sleep(self.delay)
        sent = json.loads(kw["messages"][0]["content"].split("\n", 1)[1])
        return reply("In short: " + sent["report"])


def settle(n: Narrator, timeout=10.0):
    """Wait until every request sent has its answer, without collecting it: the next tick does that."""
    end = time.time() + timeout
    while not n.settled():
        assert time.time() < end, "narration did not finish"
        time.sleep(0.02)


def run_book(db, tmp_path, narrator, hours):
    shutil.copy(db, tmp_path / "s.db")
    monitor = Monitor(MetricGraph.load(), Store(tmp_path / "s.db"))
    book = Book(monitor, tmp_path / "incidents", fresh=True, narrator=narrator)
    for k in hours:
        book.update(monitor.tick(as_of=FAULT + k * H))
    return book


def incident_file(book):
    [path] = sorted(book.folder.glob("inc-*.json"))
    return json.loads(path.read_text(encoding="utf-8"))


def test_the_book_writes_the_narration_on_a_later_tick_and_keeps_the_template_report(faulty_db, tmp_path, monkeypatch):
    monkeypatch.setattr(narrate, "cost_scale", lambda body: None)   # the cost's growth has its own test above
    gate = threading.Event()

    class Held(Echo):
        def _create(self, **kw):
            gate.wait(10)                                             # the model answers only after the ticks below
            return super()._create(**kw)

    client = Held(reply(""))
    n = Narrator(client=client)
    book = run_book(faulty_db, tmp_path, n, range(0, 2))             # the ticks do not wait for the model
    assert "narrative" not in incident_file(book)
    gate.set()
    settle(n)
    book.update(book.monitor.tick(as_of=FAULT + 2 * H))
    body = incident_file(book)
    assert body["narrative"]["source"] == "model" and body["narrative"]["text"].startswith("In short: ")
    assert body["report"] and not body["report"].startswith("In short")   # the checked template stays the report
    assert len(client.calls) == 1                                     # one milestone, one request


def test_a_resolved_incident_is_narrated_again_and_the_file_says_so(faulty_db, tmp_path, monkeypatch):
    monkeypatch.setattr(narrate, "cost_scale", lambda body: None)
    client = Echo(reply(""))
    n = Narrator(client=client)
    book = run_book(faulty_db, tmp_path, n, range(0, 13))
    settle(n)
    book.update(book.monitor.tick(as_of=FAULT + 13 * H))
    body = incident_file(book)
    assert body["status"] == "resolved" and body["narrative"]["status"] == "resolved"
    assert "Back to normal" in body["narrative"]["text"]
    assert len(client.calls) == 2


def test_without_a_narrator_the_book_writes_what_it_always_wrote(faulty_db, tmp_path):
    body = incident_file(run_book(faulty_db, tmp_path, None, range(0, 3)))
    assert "narrative" not in body and body["report"]


def test_replay_never_narrates(faulty_db, tmp_path, capsys, monkeypatch):
    from brain.run import main as run_main
    monkeypatch.setattr(narrate.Narrator, "__init__", lambda *a, **k: pytest.fail("replay built a narrator"))
    assert run_main(["--replay", "--db", str(faulty_db), "--incidents", str(tmp_path / "inc"), "--fresh", "--quiet",
                     "--narrate", "--from", "2026-11-03T12:00:00Z", "--to", "2026-11-03T14:00:00Z"]) == 0
    assert "narration is for the live loop" in capsys.readouterr().out


def test_in_the_book_a_growing_cost_asks_again_a_few_times_not_every_tick(faulty_db, tmp_path):
    """Thirteen ticks of a fault that grows and then resolves: a handful of requests, never one per tick."""
    client = Echo(reply(""))
    n = Narrator(client=client)
    book = run_book(faulty_db, tmp_path, n, range(0, 13))
    settle(n)
    book.update(book.monitor.tick(as_of=FAULT + 13 * H))
    assert 2 <= len(client.calls) <= 6



def test_the_live_loop_says_when_narration_turns_off_after_the_start_line(faulty_db, tmp_path, capsys):
    """The start line is printed before any request, so a refused key or no credit must be said on a later line."""
    from brain.run import live
    err = anthropic.BadRequestError("Your credit balance is too low to access the Anthropic API.",
                                    response=httpx2.Response(400, request=REQ), body=None)
    n = Narrator(client=FakeClient(err))
    shutil.copy(faulty_db, tmp_path / "s.db")
    monitor = Monitor(MetricGraph.load(), Store(tmp_path / "s.db"))
    book = Book(monitor, tmp_path / "incidents", fresh=True, narrator=n)
    n.submit("inc-0001", BODY)
    settle(n)
    live(monitor, book, poll=0.0, max_ticks=1, quiet=True)
    said = [line for line in capsys.readouterr().out.splitlines() if line.startswith("narration:")]
    assert len(said) == 1 and said[0].startswith("narration: off") and "credit balance is too low" in said[0]


class _OneHourClock:
    """A clock whose newest hour never changes: every poll after the first finds nothing new."""

    def __init__(self, store):
        pass

    def newest_hour(self):
        return FAULT


def test_the_live_loop_rereads_after_four_empty_polls_with_or_without_a_narrator(monkeypatch):
    import brain.run as run
    monkeypatch.setattr(run, "Clock", _OneHourClock)
    for narrator in (None, SimpleNamespace(status="on", model="m")):
        sleeps = []
        monkeypatch.setattr(run.time, "sleep", sleeps.append)
        monitor = SimpleNamespace(store=None, tick=lambda: SimpleNamespace())
        book = SimpleNamespace(narrator=narrator, update=lambda tick: [])
        run.live(monitor, book, poll=0.0, max_ticks=3, quiet=True)
        assert len(sleeps) == 11, narrator      # ticks on polls 1, 6 and 11: a re-read after four empty polls


def test_the_live_loop_says_narration_is_off_even_while_the_ticks_fail(monkeypatch, capsys):
    import brain.run as run
    monkeypatch.setattr(run, "Clock", _OneHourClock)
    narrator = SimpleNamespace(status="on", model="m")

    def update(tick):                           # the refusal is taken first, then the database is busy
        narrator.status = "off: the API refused the request: no credit"
        raise RuntimeError("database is locked")

    def sleep(seconds, polls=[]):
        polls.append(seconds)
        if len(polls) >= 2:
            raise KeyboardInterrupt

    monkeypatch.setattr(run.time, "sleep", sleep)
    monitor = SimpleNamespace(store=None, tick=lambda: SimpleNamespace())
    with pytest.raises(KeyboardInterrupt):
        run.live(monitor, SimpleNamespace(narrator=narrator, update=update), poll=0.0, quiet=True)
    said = [line for line in capsys.readouterr().out.splitlines() if line.startswith("narration:")]
    assert said == ["narration: off: the API refused the request: no credit (m)"]
