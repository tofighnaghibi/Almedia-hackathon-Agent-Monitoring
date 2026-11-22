# The 3-minute pitch (about 420 words, read at a calm pace)

*The marketplace numbers below come from one reference run. Each run differs slightly (the shop's failures are random);
after you record, use the numbers on your video's results slide (also in NARRATION_UC2.md).*

**The problem (0:00–0:30)**
Every number we run the business on can lie to us. A split can be broken without anyone noticing. A dashboard
reads minus 40% because half the day hasn't loaded yet. Orders drop and nobody knows whether it's the data, the market,
or a broken checkout. Today, people find out by digging, usually after the damage is done.

**What we built (0:30–1:20)**
We built two things.
First, a causal reasoning engine. It reads our data through read-only connections, and it answers every question the
same careful way: it checks its inputs, picks only the methods that fit, gathers the evidence, challenges its own
assumptions, and writes a report that says how sure it is. When it can't answer, it says so and says why. Every step is
recorded, so any number can be traced back.
Second, an agent on top of it. It watches all the time, on the same schedule our data actually arrives. When something
happens, it works out what kind of problem it is and proposes what to do, with the expected effect. A person approves.
Only then does it act, and only in a sandbox. The engine itself has no way to change anything in production.

**Proof (1:20–2:30)**
We showed it on two real setups.
On our experiments: it re-checks every experiment after each nightly load. On real, anonymized experiments it
reproduces the platform's numbers to the 13th decimal place, and it found one real experiment whose split doesn't match
the plan. In a replay with planted problems, it paused an experiment with a broken split, stopped a variant that was
costing 5% of trial starts, with the expected gain stated up front, and it ignored a scary dashboard drop that was
only incomplete data.
On a marketplace: a real online shop running locally, with shoppers simulated from real marketplace orders. We broke
payments without telling it. It raised the alarm from service telemetry, ruled out a drop in demand, located the fault
in the payment service, and proposed the fix with an expected gain of about 43 orders, between 29 and 62. A person
approved, it fixed it in the sandbox, and the orders it saved, about 46 by the scenario's own answer key, fell inside
that range. Each engine check took a few milliseconds. Zero writes to production.

**Why it matters, and the ask (2:30–3:00)**
This turns hours of investigation into minutes, and it does it in a way we can trust: it shows its evidence, admits
uncertainty, and never acts alone. The next step is to connect it, read-only, to our live experiment and service data.
Letting it act in production is a separate decision, and it stays with you.
