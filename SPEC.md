# SPEC: Bunon

The living specification. PLAN.md is the original plan. This file records what the system actually does and **why it changed**.

## Business rules (fixed)

- The buyer's deadline is fixed. Only quantities, factory choice and plans change.
- All-or-nothing: if on-time delivered pieces < the subcontract quantity Q, the order fails.
- Compliance is a hard constraint: **RSC-covered OR valid BSCI audit** at the as-of date.
- Humans approve every decision. Nothing is sent automatically.
- The LLM explains and plans. It never invents or edits numbers.

Module contracts live in `bunon/schemas.py`. The optimiser formulation is in PLAN.md §5, with the changes logged below.

---

## Decision log

### D1: 14 Nov 2026: plan v2 accepted
Eight review changes; see PLAN.md "Changes in v2".

### D2: 15 Nov 2026: Day 2 checkpoint → recalibration, partial lots, material cost

**Checkpoint result (before), Day 1 calibration, commit `488ffde`:**

| Setting | Ours best P(meet) | Buffer | Baseline | Baseline + same buffer |
|---|---|---|---|---|
| As planned ("late = 0"), buffer ≤ 20% | 0.487 (S = 6) | 20% | 0.000 | 0.118 |
| "Late = 0", buffer ≤ 60% | 0.725 (S = 4) | 50% | 0.000 | 0.319 |
| Partial lots, buffer ≤ 50% | 0.952 (S = 4) | 40% | 0.000 | 0.724 |

Realised synthetic history: mean fill 0.79, 36% of jobs late, about 7.3 days late on average.
Cause: placeholder parameters that were too pessimistic, plus a fill model in which no job is ever delivered in full.

**Decisions (product owner):**
1. **Partial lots is the default** delivery model: work finished by the latest ship date counts. "Late = 0" remains a toggle for comparison.
2. **The buffer ceiling stays at 25%.** A 40% buffer is not commercially credible. Grid: 0, 2.5, 5, 7.5, 10, 12.5, 15, 20, 25%.
3. **Recalibrate fill and lateness to realism, not to the P(meet) result.** The values below were chosen and written here *before* regenerating. Whatever comes out is reported as is.
4. **Material cost** (fabric + trims, supplied by the exporter) is charged on every allocated piece, so buffer pieces carry their real price.

**Values chosen before re-running.** All are placeholders pending research, and **none is sourced**: they are engineering judgment about how a subcontractor that keeps getting work behaves.

| Parameter | Before | After | Why |
|---|---|---|---|
| Fill model | logit-normal, never 1.0 | **two-part:** a job is delivered in full with probability `p_full_i`; otherwise fill is logit-normal below 1 | Factories cut 3–5% extra to cover rejects, so delivering the full quantity is the normal outcome. A model that can never deliver in full makes any plan without buffer fail by construction |
| `p_full_i` (full-delivery probability) | — | sigmoid(N(1.0, 0.8) + 0.3·compliant + 0.15·z(size)): median about 0.73 | Most jobs from a factory you'd shortlist arrive in full; weak factories about 40–50% |
| Fill when short | sigmoid(N(2.2, 0.9)) for all jobs | sigmoid(2.2 + 0.3·z(reliability) + N(0, 0.5)): mean about 0.89 | A short delivery typically misses 5–15% (rejects, cutting problems) |
| Over-stretch | committed > 0.9 × true capacity → × U(0.75, 0.95); utilisation U(0.3, 1.1) | committed > **1.0** × true capacity → × U(0.85, 0.97); utilisation U(0.4, 1.0) | Factories mostly accept within their claims. A real shortfall only occurs when a job exceeds *true* capacity (about 15% of jobs, from over-declaration) |
| Eid month factor | × 0.90 | × 0.95 | The holiday is planned around; it costs a few days, not a tenth of the month |
| P(late), median factory | sigmoid(−1.0 …) ≈ 0.27 | sigmoid(**−1.6** − 0.6·z + N(0, 0.5)) ≈ 0.17 | A subcontractor near the exporter is chased daily; slipping on 1 job in 5–6 is plausible, 1 in 3 is not |
| Days late, when late | Gamma(1.5, U(2, 6)), ceil → about 7 days | Gamma(1.5, **U(1, 3)**), ceil → about 3.5 days | Typical slips are a few days; week-plus delays are the tail (shocks, catastrophes) |
| Monsoon | +0.10 P(late), +2 days | +0.05, +1 day | Not relevant to a December order; reduced so it doesn't distort the factory averages |
| Catastrophe rate | 2% of jobs | 1% of jobs | A fire or a long strike hitting one specific job is rare |
| Shocks (district 3%/month, national 2%/month, and their sizes) | — | **unchanged** | They are the correlated-risk story and were not part of the critique |
| Material cost (fabric + trims) | not charged | **180 BDT/piece** | Materials are about 60–70% of FOB for a basic knit tee: 0.65 × USD 2.40 × 122 ≈ 190 BDT. Rounded down; placeholder |
| Surplus buffer pieces | — | salvage value 0 | Conservative: the buffer price is its full cost |

**Expectation stated in review:** a non-zero plain baseline and a recommended buffer under 15%. If either fails, stop and report.

**After:** re-run on 15 Nov 2026 with the values above, unchanged after seeing results. Full tables in `CHECKPOINT_DAY2.md`.

Realised synthetic history (clean accepted jobs): mean fill 0.926; **47% delivered in full** (intended about 73% before the job-level effects); 24% late, by 4.2 days on average; 4.8% shock-tagged.
Why 47% and not 73%: over-stretch (about 15% of jobs), Eid months (about 17% of jobs) and shocks each turn an otherwise-full job into a short one. The Eid effect is the least realistic: a holiday should delay a job, not shorten it. **Noted, not changed.**
Model calibration on the holdout: 10.0% of jobs fall below p10 (target 10%). Predicted P(full) is 0.491 vs 0.483 observed. RMSE 0.072 vs 0.097 for naive.

| Setting (shocks on) | Baseline P | Baseline + b* P | Ours P | b* | Ours cost | Buffer price |
|---|---|---|---|---|---|---|
| Partial lots, S = 4 (default) | **0.033** | 0.892 | **0.913** | **20%** | 12.45M BDT | 2.06M BDT |
| Partial lots, S = 6 | 0.038 | 0.910 | 0.918 | 20% | 12.45M | 2.06M |
| "Late = 0", S = 4 | 0.033 | 0.615 | 0.699 (target not met) | 25% | 12.93M | 2.57M |

Frontier at S = 4 (ours / baseline + same buffer): 10% → 0.745 / 0.600; 12.5% → 0.832 / 0.723; **15% → 0.880 / 0.802**; 20% → 0.913 / 0.892.

**Against the stated expectation:**
- Non-zero plain baseline: **met, but tiny** (0.033).
- Recommended buffer under 15%: **not met** (20%). The best plan at 15% reaches 0.880.
- At equal buffer, factory choice adds +0.08 at 15% and +0.02 at 20%. Most of the gain at the recommended buffer comes from the buffer itself.
- The recommended plan puts 65% of the volume in one district (ashulia_savar), because the MIP does not see correlated shocks.

**Status:** stopped for a product decision, per instruction 5. Thin UI and deploy are not started.

### D3: 15 Nov 2026: implementation fix, district cap, one re-run

**Decisions (product owner):**
1. The "buffer < 15%" expectation was a rule of thumb and is **dropped as a gate**. The 25% ceiling and the 0.90 target stay.
2. **Implementation fix against a pre-stated value:** Eid was implemented as a *quantity* loss (× 0.95), but D2 states "it costs a few days". It is now a **delay of 2–4 days, added only to jobs that were already late**. D2 also says the holiday "is planned around", and adding it to every Eid-month job would have raised late jobs from 24% to 38%, against D2's "1 in 5–6" intent. This is the same pattern as the monsoon effect.
3. **District cap on by default for our optimiser:** at most 60% of the allocation in one district. Reason: the optimiser ignored correlated district risk that the simulation models. The baselines stay naive (no cap).
4. One re-run, reported without further changes. **No more calibration changes until research values arrive.**

**On "~73% of jobs delivered in full":** D2 states 0.73 as the **median per-factory P(full) before job-level effects**, and the generator now matches it (median 0.747). **Overall, 56% of jobs are delivered in full**, because the pre-stated over-stretch (~15–18% of jobs), shocks (~5%) and catastrophes (1%) turn full jobs into short ones. Reaching 73% *overall* would need a median per-factory P(full) of about 0.97, which contradicts D2's "weak factories about 40–50%". The reliability mean was therefore **not** changed. If the intent was 73% overall, that is a calibration change for when research arrives.

**Before (D2) → after (D3)**, S = 4, partial lots, shocks on:

| | D2 | D3 |
|---|---|---|
| History: share of jobs in full / mean fill / late / days late | 47% / 0.926 / 24% / 4.2 | 56% / 0.934 / 24% / 4.6 |
| Model: below p10 / P(full) pred vs obs | 10.0% / 0.491 vs 0.483 | 8.3% / 0.592 vs 0.590 |
| Baseline P / cost | 0.033 / 10.27M | 0.046 / 10.27M |
| Baseline + b* P / cost | 0.892 / 12.33M | 0.895 / 12.33M |
| **Ours** P / b* / cost / buffer price | 0.913 / 20% / 12.45M / 2.06M | **0.914 / 20% / 12.42M / 2.05M** |
| Ours district shares | ashulia_savar 65%, gazipur 35% | **ashulia_savar 60%** (at the cap), gazipur 40% |
| Baseline + b* district shares | — | ashulia_savar 77%, gazipur 23% |

**Equal-reliability comparison** (smallest buffer at which the naive baseline reaches our P; search allowed up to 60%):

| Lot model | S | Ours: buffer / cost | Baseline to match: buffer / cost | Ours saves |
|---|---|---|---|---|
| Partial lots | 0 | 25% / 12.99M | 32.5% / 13.61M | 0.62M |
| Partial lots | 2 | 20% / 12.48M | 27.5% / 13.10M | 0.62M |
| **Partial lots** | **4** | **20% / 12.42M** | **22.5% / 12.61M** | **0.19M** |
| Partial lots | 6 | 20% / 12.45M | 22.5% / 12.63M | 0.18M |
| "Late = 0" | 4 | 15% / 11.98M (P 0.665) | 35% / 13.89M | 1.91M |

**Reading:** at the default setting, reliability-aware allocation saves 2.5 points of buffer and about 0.19M BDT (1.5%) for the same P(meet). The gap grows with tighter slack (0.62M at S ≤ 2) and is large under the strict "late = 0" view (1.9M). Most of the protection still comes from the buffer itself, and that is the honest pitch: **"we price the buffer and spend less of it"**, not "we pick magic factories".
Every plan uses only Ashulia/Savar and Gazipur factories, because they are closest to the Gazipur delivery point. Re-allocation after the largest factory (F2184) declines: P 0.911, +27,548 BDT.

### D4: 15 Nov 2026: LLM layer and the full walk-through (Days 3–4)

- **LLM:** `gemini-3.5-flash-lite` via `google-genai` 3.3.0, chosen from the current docs (see PROMPTS.md). The project was not eligible for the free tier, so a paid key was used (about 10 calls in total). The public app runs **offline** and replays cached answers. A new email on the public app goes to the manual order form; live mode needs `BUNON_LLM_MODE=live` and a key.
- **Locked re-plan:** when a factory declines on day k, factories that already confirmed keep their quantity as a **lower bound** (they may be asked for more, never less) and keep their original production window. New factories start on day k, so their window is k days shorter. The deadline does not move. On the demo order the locks do not bind: the re-plan matches exclude-and-re-solve exactly (P 0.911, +27,548 BDT, F1856 added).
- **Human in the loop:** the order is confirmed by a human after intake. The plan and the re-plan each have Approve / Reject, logged in `data/runtime/decisions.db` (gitignored, ephemeral on the host) with the acting **role**, never a name. Approval unlocks a **draft** PO text download; nothing is ever sent.
- **UI at narrow widths:** the funnel shows as two rows of three, and the approval buttons were widened.

### D5: 15 Nov 2026: natural-language steering (Day 5 stretch 1)

- **What:** "Ask for a change" turns a manager's sentence into changes to the sidebar settings only: target, slack, max factories, unproven cap, district cap, excluded factories. The human sees "now → proposed" with their own words quoted, and applies the change or not. Every applied change is logged.
- **Guardrails:** the deadline, quantity and compliance are not in the schema, so a request like "move the deadline" is shown as "Not changed". Values must be quoted from the instruction and inside the slider ranges (tests in `tests/test_agent.py`).
- **What it showed (demo order, synthetic data):**
  - "No more than three factories, new factories under 10%" → no plan within the 25% buffer ceiling reaches 90%; the best is 17.5%. The app says so instead of hiding it. That shows the trade-off a manager is asking for.
  - "Don't use F2184, plan 6 days of slack" → ours 92.7%; the naive rule needs a 25% buffer (+380,636 BDT) to match.
- New controls: a max-factories slider and a district-cap slider (100% = no cap) replace the on/off toggle. The defaults are unchanged, so the cached memos still apply. Checked at a 480 px content width: no metric or button is clipped.
