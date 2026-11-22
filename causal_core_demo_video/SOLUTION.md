# How the solution works (one page)

**What it does.** It watches our numbers all the time and works out what kind of problem it sees: bad data, a real
change, or something broken. Then it suggests a fix, with a best guess of how much it will help and a low and high estimate. A person
approves; only then does it act, and only in a sandbox. Shown on an online shop (the OpenTelemetry Astronomy Shop,
with shoppers simulated from real marketplace orders).

**How it works.**
1. **It only reads.** The engine gets data through read-only connections and has no way to change anything.
2. **Same seven steps for every question:** understand the question, check the inputs, pick only the methods that fit
   (and say why the others don't), fetch the evidence, work out the answer, challenge its own assumptions, report.
   A quick first report comes out at once and is updated when the full answer is ready.
3. **It knows what normal looks like.** Normal is learned from an older period and then frozen, so a problem can't
   quietly become the new normal.
4. **It can check every day without crying wolf.** Its alarm stays reliable however often it looks.
5. **It says "I don't know"** when inputs are broken (for example, experiment groups of the wrong size) or a question
   is outside what it has seen, and gives the reason.
6. **Everything is written down**, so any number can be traced back to its evidence.
7. **The agent proposes, a person decides.** A rule in the engine blocks changes to production. Approved actions run
   in a sandbox that accepts only the approved change, then reads it back to confirm it happened.

**Why it works.** It never trusts a single number. It re-derives results itself from the raw evidence, and it checks
data completeness and group sizes before reporting any effect. It also tells apart problems that look the same on
a dashboard. In the shop demo, checkout attempts were normal while payments failed. That pointed to a broken payment
path, not a quieter market. It expected +43 orders from the fix (range 29 to 62); the answer key says about 46.

**How it could be improved.**
- **Faster alarms when traffic is low.** At night the shop sees only a handful of checkouts per 3-hour block, so the
  alarm came 9 simulated hours after the first failure. Checking per minute, or setting the alarm by traffic, would help.
- **Better-calibrated ranges.** In 2 of 8 test runs the stated range missed the true gain: once too high (an estimate
  made right at an early alarm runs high, because alarms fire when the sample looks worst), once too low (the range
  ignores how many shoppers will still come that day). Re-estimating one block later and adding that uncertainty
  would fix both.
- **A completeness check per data source.** Payment telemetry arrived in delayed batches.
- **Sharper what-if predictions.** In an internal test, a pre-trained table model cut the error from about 91 to 9 on
  a world with a bend and an interaction.
- **Correct handling of self-adjusting experiments (bandits)**, by reweighting the results (Hadad et al., 2021).
- **Read-only connection to live data.** Acting in production is a separate, authorized step.

**Key code** (paths in the `experimentation.cop` repository; `marketplace-recon` is a sibling repository)

| Where | What |
|---|---|
| `causal_core/causal_core/orchestration/planner.py:38` | the seven steps; method choice with reasons `:301`; self-check `:625` |
| `causal_core/causal_core/orchestration/coordinator.py:57` | runs one question; quick and final report |
| `causal_core/causal_core/orchestration/policy.py:30` | gate: reports allowed, production changes denied |
| `causal_core/causal_core/analytics/sequential.py:110` | the alarm that stays reliable when checked daily |
| `causal_core/causal_core/analytics/experiments.py:267` | experiment effects, with the group-size check |
| `causal_core/causal_core/analytics/baseline.py:88` | learns "normal" from an older period |
| `causal_core/causal_core/counterfactual/rollouts.py:69` | what-if; refuses outside experience `:200` |
| `causal_core/causal_core/storage/ledger.py:42` | the record of every step |
| `causal_core_demo_video/agent/sandbox.py:81` | sandbox: approval checked, run once, receipt kept |
| `causal_core_demo_video/agent/uc2.py:301` | shop watcher: telemetry, checks, diagnosis, proposal |
| `marketplace-recon/mkt/adapters/astronomy/telemetry.py:56` | read-only shop telemetry |

**References.** Kohavi, Tang & Xu (2020), *Trustworthy Online Controlled Experiments*. · Fabijan et al. (2019),
"Diagnosing Sample Ratio Mismatch in Online Controlled Experiments," KDD. · Deng et al. (2013), "Improving the
Sensitivity of Online Controlled Experiments by Utilizing Pre-Experiment Data," WSDM. · Ramdas et al. (2023),
"Game-Theoretic Statistics and Safe Anytime-Valid Inference," *Statistical Science*. · Waudby-Smith & Ramdas (2024),
"Estimating means of bounded random variables by betting," *JRSS-B*. · Pearl (2009), *Causality*. · Hadad et al.
(2021), "Confidence intervals for policy evaluation in adaptive experiments," *PNAS*. · OpenTelemetry Demo,
opentelemetry.io/docs/demo. · Olist Brazilian E-Commerce Public Dataset, Kaggle (CC BY-NC-SA 4.0).

