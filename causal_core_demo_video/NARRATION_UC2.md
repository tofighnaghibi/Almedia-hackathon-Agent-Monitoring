# Video 2 narration (3:00): preview

Preview with approximate demo times. After you record (`scripts/uc2_record.sh`), `scripts/uc2_assemble.sh` rewrites this file with the exact times and your run's numbers in place of [brackets]; the prompter (`scripts/uc2_voice.sh`) shows the same lines in sync with the video. Paced for about 2.9 words per second.


## 1 · Pitch

- **0:00** Frank owns a marketplace for telescopes. Business is booming, so Frank and his partner decide to start a family.
- **0:06** The marketplace crashes at 3 a.m.: orders are dropping, and nobody knows why yet. But Frank has rent to pay.
- **0:14** Is it a data glitch, a quieter market, or something broken in checkout?
- **0:18** Today a person digs through dashboards, logs and traces to find out, while orders keep failing.
- **0:24** We built an engine that answers that question on its own, and an agent that acts on the answer, safely.

## 2 · How it works

- **0:32** The engine accesses the event stream through ports: telemetry from the services, and data from the order pipeline.
- **0:39** Every question goes through the same seven stages.
- **0:42** One, frame it: has something changed, why, how big is the effect, or what if we act? Here: have checkout errors gone up?
- **0:50** Two, check the inputs: the right source, on time, and a baseline to compare with. If anything is missing, it stops.
- **0:57** Three, pick the methods: two of its fourteen fit here, and the other twelve are set aside, each with a reason.
- **1:05** Four, fetch the checkout numbers. Five, test them: are errors above budget, are attempts below normal?
- **1:11** Six, challenge the result: could a logging change explain it?
- **1:14** Seven, report: publish, publish a draft, propose a test, or refuse to act. Every step is written to a ledger.
- **1:22** On top, an agent turns the findings into a suggested fix, with a best guess of how much it helps, and a low and high estimate.
- **1:30** It acts only after a person approves, and only inside a sandbox.

## 3 · Demo (live run)

- **1:36** This is a real, running shop, with 200 products from a Brazilian marketplace and shoppers simulated from real orders.
- **1:43** Left: the world and what the agent sees. Right: the engine at work, every three hours.
- **1:50** Now, without telling the agent, we break payments: one in four fails.
- **2:00** As traffic picks up, the engine raises an alarm: checkout errors far above budget.
- **2:06** Checkout attempts are normal, [attempts] against [usual]. The failures sit in the payment service.
- **2:16** The engine itself may not act; its policy gate says so.
- **2:23** The agent proposes switching the faulty flag off: about [N] more orders today, [low] to [high].
- **2:31** A person approves. The change runs in the shop's flag service and is read back.
- **2:45** Payments recover in the next block.

## 4 · Close

- **2:46** So the agent found the cause, ruled out a drop in demand, and stated the expected benefits of pursuing its recommended action.
- **2:54** Next: connect it to our own telemetry, with the same guardrails.
