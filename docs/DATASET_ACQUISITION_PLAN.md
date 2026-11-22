# Dataset acquisition and verification plan

*Status: plan only. Nothing in it has been started. Written 21 Nov 2026.*

**Rule agreed on 21 Nov 2026:** no stitching, and none of D7 (approve architecture incl. stitching), D8 (prototype fate) or the Astronomy Shop decisions A1–A5, until every dataset below is either **verified** or **explicitly dropped** by you. D9 ("optional downloads") was itself a dataset task, so it is absorbed into this plan.

Evidence labels as elsewhere: **[M]** measured here · **[D]** documented by the source · **[U]** from memory or unverified. Every [U] below must become [M] or be corrected during verification.

---

## 1. Where everything stands now

| Dataset | Role in stitching (primary / secondary rows) | Current state | What is still missing |
|---|---|---|---|
| **Olist** | P 16 / S 2 | Downloaded, measured **[M]** (§4.1b of ARCHITECTURE.md) | Formal verification card (§3) |
| **Olist Marketing Funnel** | P 1 / S 1 | Downloaded, measured **[M]** | Verification card |
| **Retailrocket** | P 2 / S 5 | Downloaded, measured **[M]** | Verification card; price property ("790") unverified |
| **Amazon-M2** | P 1 / S 1 | Official files downloaded; byte-identical to the Kaggle copy **[M]** | Verification card; licence terms not read |
| **ESCI** | P 1 / S 2 | Only IDs + locales extracted **[M]** | Text columns (query, title, brand, label) not downloaded |
| **Amazon Reviews 2023** | S 4 | ID map + All_Beauty only **[M]**; 750 GB total **[M]** | A *filtered* subset for the mapped categories |
| **Open Bandit** | P 3 | Not downloaded; random log 0.70 GB on HF **[M]** | Download + verify |
| **Criteo uplift** | P 1 / S 1 | Not downloaded; 0.31 GB on HF **[M]** | Download + verify |
| **BCB SGS** (macro API) | P 1 | Not fetched | Fetch 3 series + verify |
| **Holidays (computed)** | S 1 | Rules written, not run | Cross-check against an official calendar |
| **YelpChi** | P 1 | Not downloaded | Find canonical source, download, verify |
| **M5 (Walmart)** | S 3 | Not downloaded | **You:** accept Kaggle competition rules |
| **Instacart** | S 1 | Not downloaded | **You:** accept Kaggle competition rules |
| **UCI Online Retail II** | S 3 | Not downloaded | Direct download |
| **Yahoo! R3** | S 1 (with Coat) | Not downloaded | **You:** apply via Yahoo Webscope (approval can take days) [U] |
| **Coat** | S 1 (with Yahoo R3) | Not downloaded | Direct download (Cornell) [U] |
| **Amazon fraud graph** (CARE-GNN) | S 1 | Not downloaded | Download from the paper's repo |
| **iPinYou** | S 1 | Not downloaded | Very large in full [U]; plan a subset |
| **Hillstrom** | S 1 | Not downloaded | Direct download |
| **NAB / LogHub** | S 1 | Not downloaded | GitHub downloads |
| **Tmall / Taobao?** | S 2 (unverified) | Not inspected | **First identify** which Alibaba dataset is meant (§2, step T) |
| ~~Keepa~~ | — | **Dropped 21 Nov 2026** (paid; rule: drop anything paid) | — |
| **Astronomy Shop** | P 1 / S 2 | Cloned at pinned `7a88536` **[M]** | Running it is A2, which **waits** for the gate. Its "dataset" verification here is limited to the pinned source |

---

## 2. Phases

### Phase 0: your actions and prerequisites (no downloads by me)

| # | Action | Why | Who |
|---|---|---|---|
| 0.1 | Accept Kaggle competition rules for **M5 Forecasting – Accuracy** and **Instacart Market Basket Analysis** (browser, logged in) | The API refuses competition data until the rules are accepted | **You** |
| 0.2 | Apply for **Yahoo! R3** on Yahoo Webscope, or tell me to drop it and use Coat alone | Webscope requires an application and approval [U] | **You** |
| 0.3 | **Tmall/Alibaba:** tell me which dataset you meant, or let me shortlist candidates in step T (e.g. Taobao User Behavior, IJCAI-15 Tmall repeat buyers) [U] | The brief only says "Tmall/Alibaba"; several datasets exist with different licences (usually Tianchi account + research-only terms) [U] | **You** decide; I research |
| 0.4 | ~~Decide Keepa~~ **Done: dropped** (rule: no paid sources) | — | — |
| 0.5 | Decide **Open Bandit BTS log** (6.3 GB **[M]**): needed only for the off-policy-evaluation reference | Large; the random log alone covers the position curve | **You** |
| 0.6 | Confirm storage at `~/data/mkt/raw/<dataset>/<version>/` on `/home` (301 GB free **[M]**). Existing downloads in `~/.cache/mkt-probe/data` get moved or re-registered there | `/` has only 23 GB free **[M]** | **You** confirm |

### Phase 1: formal verification of what is already in hand (no new downloads)

Olist, Olist Funnel, Retailrocket, Amazon-M2 and the existing Reviews 2023 / ESCI extracts. Most facts are already measured. This phase writes the verification card for each (§3): sha256, licence text captured, documented vs measured differences, known biases.

### Phase 2: primary sources (highest priority: these rows have no data without them)

Order: **Open Bandit (random) → Criteo → ESCI text columns → BCB SGS → YelpChi**.

### Phase 3: secondary sources

Order by how many rows they back: **M5 (3) → UCI (3) → Reviews 2023 filtered subset (4) → Instacart → Hillstrom → Coat (+ Yahoo R3 if approved) → Amazon fraud graph → NAB/LogHub → iPinYou subset**.

### Step T: Tmall/Alibaba investigation (parallel, research only)

Identify the candidate datasets. For each, check access, licence, schema and size from their documentation, and whether it really fills the session-length and funnel rows better than Retailrocket. Output a one-page recommendation. Nothing is downloaded until you choose.

### Phase 4: verification report and gate

- One report listing every dataset as **verified / verified with caveats / dropped**.
- Re-check the stitching matrix: does every row still have its primary? Promote secondaries or mark rows "assumed" where a primary was dropped.
- Update `stitching.excalidraw` borders to the verified status.
- **Gate:** you review the report. Only after your sign-off: D7 → D8 → A1–A5.

---

## 3. What "verified" means (the same card for every dataset)

| # | Check | Pass condition |
|---|---|---|
| V1 | Provenance | Source URL, version or date, download date; sha256 of every file |
| V2 | Licence | Licence or terms text saved next to the data; use restrictions noted (non-commercial, no redistribution, attribution) |
| V3 | Size and counts | Row counts per table **[M]** vs. documented counts **[D]**; any difference explained |
| V4 | Schema | Columns and types match the documentation; unexpected or missing columns listed |
| V5 | Semantics | Dataset-specific facts that the stitching step relies on (table §4), measured |
| V6 | Coverage | Time range, geography or locale, population; gaps and truncation (e.g. Olist's thin first and last months) |
| V7 | Quality | Nulls, duplicates, impossible values, timestamp anomalies, ID namespace |
| V8 | Sample | 20 random records read by eye; anything surprising written down |
| V9 | Biases and limits | How the data was sampled or selected; what it can't support |
| V10 | Fitness | Which stitching rows it backs, and whether it can actually support them (yes / partly / no) |

Data handling: each dataset goes in its own new directory. Downloaded files are treated as untrusted data: nothing inside them is executed, and Python reads them with `-I`. Archives are listed before extracting.

---

## 4. Dataset-specific checks (V5) and expected sizes

Documented figures are from memory unless marked; verification replaces them with measurements.

| Dataset | Access | Expected size | Key semantic checks (V5) |
|---|---|---|---|
| Open Bandit (random/all) | HF `zozonext/open-bandit` **[M]** | 0.70 GB **[M]** | Columns include `position`, `click`, `propensity_score`; propensities constant under the random policy [U]; 3 positions; CTR by position; time range |
| Criteo uplift v2.1 | HF `criteo/criteo-uplift` **[M]**, CC BY-NC-SA **[M]** | 0.31 GB gz **[M]** | ~13.9M rows [D]; treatment share ~85% [D]; exposure only possible when treated; visit and conversion rates per arm |
| ESCI text columns | HF mirror `tasksource/esci` **[M]** (official GitHub as cross-check) | ≈2.5 GB **[M]** | Product counts per locale match §4.1 **[M]**; E/S/C/I label mix; mirror vs. official parquet row-count equality |
| Reviews 2023 subset | HF, per-category files **[M]** | Filtered to ESCI-matched items: est. 1–3 GB [U] | Parent vs. child ASIN handling; review timestamps range; price-field coverage per category (15.7% in All_Beauty **[M]**) |
| BCB SGS | Public JSON API [D] | < 1 MB | Series 433 (IPCA), 432 (Selic), 1 (USD/BRL) [U: codes]; daily vs. monthly frequency; 2016–2018 complete |
| YelpChi | Source to confirm (Rayana & Akoglu / CARE-GNN repo) [U] | ~tens of MB [U] | ~45,954 reviews, ~14.5% filtered as fake [U]; label meaning (Yelp filter, not ground-truth fraud) |
| Amazon fraud graph | CARE-GNN repo [U] | small [U] | ~11,944 users, label definition (helpful-vote based) [U] |
| M5 | Kaggle competition (rules) | ~0.5 GB [U] | 30,490 series, 1,969 days [U]; `sell_prices` weekly; SNAP and event flags |
| Instacart | Kaggle competition (rules) | ~0.7 GB unzipped [U] | ~3.4M orders, ~206k users [U]; no absolute dates (only relative days) |
| UCI Online Retail II | UCI direct download [D] | ~45 MB xlsx [U] | ~1.07M rows **[D]**, 2009-12-01…2011-12-09 **[D]**; invoices starting "C" = cancellations |
| Hillstrom | MineThatData blog CSV [U] | ~5 MB | 64,000 customers, 3 arms (men's email, women's email, none) [U] |
| Coat | Cornell (Schnabel et al.) [U] | tiny | 290 users × 300 items, with a randomly-exposed test set [U] |
| Yahoo! R3 | Webscope (application) [U] | small | Random-exposure test set present [U] |
| iPinYou | Official site / make-ipinyou-data [U] | Full logs very large [U] | Plan: one season subset; bid, pay price, impression, click, conversion fields |
| NAB / LogHub | GitHub [U] | NAB ~20 MB; LogHub samples small [U] | Labelled anomaly windows (NAB); log templates (LogHub samples, not full) |
| Tmall/Taobao | Tianchi (account, research terms) [U] | Taobao User Behavior ~3.5 GB zip [U] | Step T decides |

**Disk estimate:** about 15 GB for everything except the Open Bandit BTS log (+6.3 GB) and full iPinYou. Fine on `/home`.

---

## 5. How it would be carried out (once you approve this plan)

Same pattern as stitching: **plan → script → you run or approve the run.**

1. A small `scripts/datasets.py` with one spec per dataset (source, expected files, licence, checks V1–V10). `datasets.py plan` lists what is present, what's missing and who must act. `datasets.py fetch <name>` downloads one dataset into its own directory. `datasets.py verify <name>` writes the verification card to `docs/datasets/<name>.md`.
2. I fetch and verify one phase at a time and stop after each phase for you to read the cards.
3. Anything blocked on you (rules, applications, Tmall choice, Keepa, BTS log) is listed at the top of each phase report.

**Order of stops for your review:** after Phase 1 (in-hand cards), Phase 2 (primaries), Phase 3 (secondaries), step T, and Phase 4 (gate report).

---

## 6. Decisions needed from you to start Phase 0

1. Approve this plan (and the script-first approach in §5).
2. Do 0.1 (Kaggle rules for M5 and Instacart) and 0.2 (Yahoo R3 application, or drop it).
3. Answer 0.3 (which Tmall/Alibaba dataset), 0.4 (Keepa: drop?), 0.5 (Open Bandit BTS: yes/no) and 0.6 (storage location).
