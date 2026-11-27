# SPEC: Bunon

The living specification. PLAN.md is the original plan. This file records what the system actually does and **why it changed**.

## Business rules (fixed)

- The buyer's deadline is fixed. Only quantities, factory choice and plans change.
- Success (since D12): the **good** pieces (after QC) delivered by the deadline must reach (1 − tolerance) × the subcontract quantity Q. Tolerance is a setting (placeholder 2%). Before D12: all on-time pieces ≥ Q.
- Compliance is a hard constraint: **RSC-covered OR a BSCI audit still valid on the buyer's deadline** (since D12; before: valid on the data date).
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

### D6: 17 Nov 2026: reframe of the AI role, item 1: factory replies

**Decision (product owner):** the AI role was too thin (extracting stated fields, filling a memo). Reframe: the optimiser does the numbers; the LLM handles unstructured language and chooses which tools to run. Build in order, stopping after each: (1) factory reply parsing, (2) disruption from news text, (3) a tool-calling agent, (4) steering, which already exists (D5). Items 3 and 4 are cut before deploy or tests.

**Item 1, done:**
- Replies in English, Bangla or Banglish → accept / reject / counter, with quantity, delay and conditions, each with a verified quote (PROMPTS.md A2b). The code settles the final decision.
- **A counter changes the plan:** the stated quantity is locked exactly, and a stated delay of d days cuts that factory's slack by d. A reject is a locked re-plan.
- 4 demo replies in `data/demo/factory_replies.json` (1 English, 2 Banglish, 1 Bangla). All 4 were classified correctly live, and all answers are cached.
- What the replies showed, on synthetic data:
  - Losing 5,300 pieces at F2184 (→ 9,000) or a 5-day slip at F1064 drops P(meet) below 90% (0.864 / 0.883). The re-plan **cannot recover**, because the 5-factory limit (and, for the delay, the Ashulia/Savar district cap) binds.
  - The memos now lead with that and recommend escalating to the buyer.
  - A full rejection (F2214) is recoverable: P 0.918, +57,619 BDT, because the freed factory slot lets a new factory in.
- Fixed on the way: the re-plan's reported buffer is now the actual over-allocation, not the solver's grid value.
- Tests: 102 (13 new).

### D7: 17 Nov 2026: item 2: disruptions from news text

- **What:** a headline or short news item (English or Bangla) → area, disruption type, duration, severity label and confidence, each with a checked quote (PROMPTS.md A2c). **Severity maps to simulation parameters only through the fixed `config.SEVERITY_TABLE`**; the LLM sets no numbers. The human can correct area and severity before assessing.
- **Impact:** the event is applied to every simulation draw (not sampled). The app shows (1) the current plan's P(meet) if it happens and (2) a plan made knowing about it, before any purchase orders go out, so no quantities are locked. That plan can be approved or rejected like any other.
- **Severity table (placeholders pending research):**

  | Severity | Output delivered | Share of days lost as delay | Default days |
  |---|---|---|---|
  | low | 95% | 30% | 2 |
  | medium | 85% | 60% | 3 |
  | high | 65% | 100% | 7 |

  The "high" row is harsh: a high-severity flood in Gazipur takes the current plan from 91.4% to 0.0%, because 40% of its pieces are in Gazipur. The numbers are honest under the table, but the table itself needs real values.
- **What the 3 demo items showed:** local events can be routed around for 100–160k BDT, by moving volume out of the district. A nationwide hartal cannot: the best plan reaches 49.1%. Bunon should say so early, and it does.
- Tests: 107 (5 new).

### D8: 17 Nov 2026: item 3: tool-calling analyst

- **What:** section 8, "Ask a what-if question". The LLM chooses among `run_simulation`, `solve_plan` and `compare_plans` (at most 6 calls) and answers with placeholders only. Every call (arguments, facts, errors) is logged on screen. Details and live results: PROMPTS.md A2d.
- **Division of labour:** the optimiser and simulation produce every number; the LLM handles language, chooses the tools and their arguments, and explains. Tool arguments are range-checked in code, the same ranges as the sidebar.
- **Offline:** each model step is cached by conversation hash; the 3 example questions replay with no key.
- **Worth showing judges:** for "can *any* plan reach the target during a nationwide hartal?", the model loosened all limits on its own initiative to test the claim, then reported that none does (about 49% at best).
- **Item 4 (steering into OptimizerParams)** already exists (D5); not rebuilt.
- Tests: 113 (6 new, `tests/test_analyst.py`, scripted fake provider): answer facts only; offline replay; 6-call limit; retry then template; bad arguments reported to the model; exclusion and cap respected.

### D9: 17 Nov 2026: live LLM on the public demo, with caps

- **Why:** offline-only meant any text that was not an exact cached example (or any example after a sidebar change) gave "LLM unavailable (CacheMiss)" in the reply, news, steering and what-if steps. Judges will type their own text.
- **What:** the hosted app runs with `BUNON_LLM_MODE=live`. The cache is still read first, so the scripted story costs nothing and works if Gemini is down. New text goes to Gemini within two caps (placeholders in `bunon/config.py`, overridable as secrets): **40 live calls per visitor session** and **400 per day for the whole demo** (UTC day, counter in gitignored `data/runtime/llm_budget.json`). One call = one provider request; a what-if question uses up to about 10, a memo up to 2. When a cap is hit, the step falls back exactly as offline (hand entry or template) and says why.
- **Limits of the guard:** the visitor count is per browser session, so a reload resets it; the daily cap is the real ceiling. The counter file resets when Streamlit Cloud restarts the container. Set a budget alert in Google Cloud as the backstop.
- **Caching change:** LLM steps are no longer in `st.cache_data`, which would have served one visitor's failed or capped attempt to everyone. They are kept per session; successful answers are shared through the disk cache. Plan and re-plan numbers stay in `st.cache_data`.
- **Secrets:** the key and settings go in Streamlit Cloud → app settings → Secrets; `app.py` copies the four names it uses into the environment. Never committed.
- Tests: 118 (5 new: `tests/test_budget.py`, one in `tests/test_analyst.py`): per-visitor cap, daily cap across visitors, cached answers never counted, capped analyst does not reach the provider, fake providers uncapped unless asked.
- **Found in the live check (18 Nov):** a new question, "What if Narayanganj floods for 3 days?", was answered live as "a flood lasting 2 days". The tool reported only the resulting delay (2 days), so the model, not allowed to write "three", cited the wrong fact. Fix: `run_simulation` and `solve_plan` now also report the event's length (`event_days`) and the number of affected factories (`n_affected`). Second fix: the checker rejected factory IDs that a tool had returned in a new plan (it only looked at fact keys), which pushed the Ashulia example to the template; IDs inside text facts now count as known. Two example questions were re-run live and re-cached (Ashulia: re-plan 91.7%, +162,720 BDT; hartal: same numbers as before, 10.9% → best 49.1%); the F2184 question was unchanged. Tests: 120.
- **Crash found by the user (18 Nov):** with max 3 factories and a 10% unproven cap, the plan reaches only 17.5%; when F2314 then declines, no re-plan covers the order (confirmed factories cannot be cut, the 3-factory cap leaves room for one late starter). The app showed the empty result as a plan ("approve the re-plan with 0 factories", 0 BDT) and crashed on the approval buttons. Fix: an infeasible re-plan (scripted decline, factory reply or news) now shows "No feasible re-plan", suggests loosening a sidebar limit or escalating, offers nothing to approve and makes no LLM call for a memo. Regression test in `tests/test_engine.py`.
- **Slider sweep (18 Nov):** every sidebar control at its ends, plus tight and loose combinations and each toggle off (20 settings), with every action clicked (plan, approve, scripted decline, 4 replies, 3 news items, what-if, steering), offline. No crashes. One misleading screen fixed: with max 1–2 factories no plan exists at all, and the page said "showing the most likely plan" over an empty plan; it now says "No feasible plan", why, and what to loosen, and stops there.
- The sweep is now a test: `tests/test_app_sweep.py`, 20 settings, opt-in with `pytest -m sweep` (several minutes; the normal `pytest -q` skips it). It runs offline and writes the decision log to a temporary file (`BUNON_DECISIONS_DB`), so it never touches the real log.

### D10: 18 Nov 2026: release check (Day 6)

- **Clean clone:** `git clone` from GitHub into a new folder, new Python 3.11 venv, `pip install -r requirements-dev.txt` (86 s), no `.env`, no API key, **no `PYTHONUTF8`**. `pytest -q`: 116 passed, 5 skipped (all five need `data/raw` or `data/private`, which are not published, as intended). Sweep cases `default` and the unproven-cap cases: passed. Cold start of the app: first screen about 1 s, "Confirm order" to plans and memo 4.2 s; headline numbers unchanged (naive 5%, naive + buffer 89%, Bunon 91%).
- **Privacy scan:** every file in the clean clone (99 files, including every text cell of `bunon.db` and the cached LLM prompts and answers) checked against the private raw export: 3,048 factory names, 3,214 os_ids, 3,139 addresses. **0 matches.** No `data/raw`, `data/private`, `data/runtime` or `.env` in the clone. The scan printed only counts.
- **Live app (awake), new visitor:** first screen 6.1 s, email 0.5 s, "Confirm order" to plans and memo 6.1 s, no errors. Waking after a quiet period: about 30–40 s (observed 18 Nov).
- **README:** results table, how the AI is used, full feature status and the release check. All README numbers recomputed from code before writing (4.6%, 89.5%, 91.4%, 12,417,585 BDT; 22.5% / +192,021 BDT to match; decline 91.1% / +27,548 BDT; 231 candidates).

### D11: 18 Nov 2026: UI refresh (demo login, welcome page, theme, navigation)

- **Scope:** `app.py`, `.streamlit/config.toml`, `assets/hero_sewing.svg` and the sweep test only. No engine change (`data_gen`, `reliability`, `optimizer`, `simulation`, `agent` untouched).
- **Demo login:** a landing page with prefilled demo email and password and a primary **Continue as demo exporter** button (one click). Nothing is checked or stored: the login only sets `authed` in `st.session_state`. The page says "Demo access. No account needed." Logout clears the session (except the anonymous live-call counter id) and returns to the login page. A browser reload also starts a new session, so it returns to the login page too (one click to get back in).
- **Welcome page:** hero with an original SVG illustration of garment workers at sewing machines (credit shown, recorded in DATA_PROVENANCE.md §4; 6.1 KB), the five-step "How to use Bunon" guide matching the app flow, and **Start a new order**.
- **Theme:** light base, one accent (deep indigo #3730A3), dark grey text (#1F2937), 17 px base font for projectors, rounded cards with soft shadows (metrics, bordered containers, step cards). Base theme in `.streamlit/config.toml`, the rest in one CSS block (`THEME_CSS`). Synthetic badges kept, plus a "Synthetic data prototype" badge on every page.
- **Transitions:** CSS only: 250 ms fade-in on content and cards, hover lift on cards and buttons; all switched off under `prefers-reduced-motion`.
- **Navigation:** the sidebar lists the eight steps as links to anchors on the step headers, plus **Welcome** and **Log out**.
- **Checks:** unit tests 121 passed; the sweep test now logs in, starts an order, runs every action and logs out at 20 settings; story walked with no API key in live and offline mode (no errors; cached memo; new text falls back to hand entry).
- **19 Nov 2026 addendum: redesign after review.** The first theme read as generic (emoji brand, indigo pills, shadows, hover lift, numbered circles). Replaced with a restrained business look: Inter font, slate text (#0F172A), one deep-navy accent (#1E3A5F), flat white panels with hairline borders (6 px radius, no shadows), uppercase small labels, a "B" wordmark instead of the emoji. Hover now only darkens borders (no lift); the fade-in stays (220 ms, opacity only); both off under `prefers-reduced-motion`. Login: the photo fills the window behind a dark overlay, white sign-in card on the right, credit in the corner. Welcome: a photo banner with the greeting, then five flat "STEP 01-05" panels. The synthetic badge is kept on every page (outlined amber label). Chart colours follow the palette.
- **Photo:** the user asked for a real photo of workers at sewing machines. The reference photo they shared had no known licence, so it is not used. Chosen instead: "Working conditions of Garment workers in Bangladesh" by Fahad Faisal, Wikimedia Commons, CC BY-SA 4.0 (credit with author, licence and source links shown on both pages; resized copy shared under the same licence; see DATA_PROVENANCE.md §4). The app falls back to the original SVG if `assets/factory_floor.jpg` is missing.

### D12: 26 Nov 2026: interview inputs, good pieces within a tolerance, QC, January order (PLAN_V2, 26–28 Nov)

**Context:** PLAN_V2.md was approved on 26 Nov (trust-layer direction; decisions listed at its top). This entry is the first milestone: the interview inputs and the new success rule, before the app is restructured around the order timeline. Interview: one anonymised expert (DATA_PROVENANCE.md §6), single source, so every number stays a labelled placeholder.

**Changes:**
- **Success rule:** P(good pieces delivered by the deadline ≥ (1 − tolerance) × Q). Tolerance in `OptimizerParams.tolerance_pct` (default `DELIVERY_TOLERANCE_PCT` = 2%, placeholder); each plan carries `min_good_qty`. UI slider "Accepted shortfall".
- **QC:** synthetic history gains `qc_rejected_qty` and `lot_rejected` (separate random stream: every earlier synthetic value is unchanged; verified by comparing the old and new DB). The reliability model (`eb-3`) learns a per-factory defect rate (empirical Bayes, shrunk to the pooled mean) and a whole-lot rejection chance (Beta-Binomial). The simulation draws both; a rejected lot counts as 0 good pieces. The optimiser's expected yield `q_effective` includes the good share.
- **Prices on a CM basis:** the subcontractor is paid cut-make only; the exporter supplies fabric. CM 60 BDT and material 76.5 BDT per piece, both **placeholders derived by Bunon** from the interview's per-set sewing price and per-kg fabric price (the interviewee stated neither per-shirt figure; calculation in DATA_PROVENANCE.md §6.1), charged on every piece made (was 180). Correction to PLAN_V2 audit item 6(f): the cost model was already CM-shaped (material was charged as exporter-supplied); only the values were off.
- **Loss on failure** is a manager input (`OrderConstraints.loss_on_failure_bdt`, sidebar number field), no longer computed from the email's penalty clause. Default placeholder 4,400,000 BDT (≈ the old two-week penalty, so the optimiser's risk weight is about where it was).
- **Safety margin** (`min_buffer_pct`, default 0%): a floor on the buffer the search may pick. Bunon can still recommend a larger buffer.
- **January order:** order date (planning day 0) 3 Jan 2027, buyer deadline 31 Jan 2027 (was 20 Nov → 20 Dec 2026). `DATA_AS_OF` (20 Nov 2026) is now separate from the planning date `AS_OF`. Demo email: PO NB-2701, "no later than 31 January 2027". The window is 2 days shorter (28 vs 30 days).
- **Compliance at the deadline:** a BSCI audit must still be valid on the deadline. On this order the candidate count goes from 231 to **221** (shorter window and the stricter rule together).
- **Bug fix (since D7): event re-plans counted the event's delay twice** (once by cutting slack in the optimiser's view, again in the simulation), and also cut capacity by the output factor. Now `optimizer.search_with_events` optimises on the adjusted view but scores every plan like the current one (original slack, event applied once), and capacity shrinks only for days lost beyond the slack. Used by the news step and the what-if `solve_plan`. The app now says when keeping the current plan is at least as good ("Do not re-plan for this event alone").
- **Demo fixtures aligned with the new plan:** the 4 demo replies now name the new plan's quantities, and the Bangla reject is from F0824 (F2214 is no longer in the plan). These are interim; the hand-written personas (`data/demo/personas.json`, format committed) replace them. **F2184's Banglish counter (9,000 of 12,400) now has no feasible re-plan:** with the other four factories locked, Gazipur would hold 62.6% of the pieces (cap 60%) and the 5-factory limit leaves no room for a new one. The app says "No feasible re-plan"; the README demo path uses F1064's delay counter instead.
- Sweep hardened: it now fails if intake is not cached or the plan section does not render (it had passed hollow when the email changed). The infeasible re-plan case moved to a unit test (decline on day 12 with max 4 factories), since with the January window max 3 factories has no plan at all.

**Before → after on the demo order** (seed 42, 20,000 simulations, target 90%, defaults):

| | Before (Dec order, D10) | After (Jan order, D12) |
|---|---|---|
| Candidates | 231 | 221 |
| Naive: declared capacity + lowest price | 4.6% | 1.2% |
| Naive + the same buffer | 89.5% (20%) | 77.8% (25%) |
| **Bunon** | **91.4%** (20% buffer, 12,417,585 BDT) | **87.5%** (25% buffer, 8,237,537 BDT): **below the 90% target** |
| Naive rule, buffer to match Bunon | 22.5%, +192,021 BDT | 32.5%, +250,477 BDT |
| Largest factory declines on day 3 (locked re-plan) | 91.1% | 84.0% (F1856 declines) |
| News: Gazipur flood (high) | current 0% → re-plan 83.0% | |
| News: nationwide hartal (medium, 2 days) | current 52.3%; best new plan 41.9% → app advises keeping the current plan | |

Costs fell because material dropped from 180 to 76.5 BDT per piece (the CM rate rose from 30 to 60).

**Why Bunon now misses 90% (attribution, same January order, 25% buffer ceiling):** without QC, 90.2% at tolerance 0 and 91.8% at 2%. With QC defects only: 91.4%. With whole-lot rejection only: 88.5%. With both: 87.5%. **The whole-lot rejection placeholder (about 2% per lot, five lots) is the main cause:** one rejected lot of ~13,000 pieces outweighs the 25% buffer in about one run in ten. This is the most sensitive placeholder in the model and needs a real figure from the interview. It was **not** tuned to restore 90% (project rule: calibrate to realism, not to results).

**Tests:** 137 (from 121): `tests/test_quality.py` (12, incl. the hash-seed check below; good pieces vs tolerance, lot rejection, no-QC fallback, plan threshold, QC learnt from history, compliance at the deadline, loss as manager input, safety margin, January dates, event re-plan scoring, event capacity), `tests/test_personas.py` (4). Changed expectations: January dates in the intake test, the new reply quantities, and the infeasible re-plan case. Sweep: now 24 settings (adds tolerance 0% and 5%, safety margin 25%, loss 0), offline, with the cache refreshed live (about 20 calls on fictional text).

**Doc fixes (audit item 6):** README decisions range and results; PROMPTS.md tier wording (Gemini 3.5 Flash-Lite has a free tier per Google's docs; this project has used a paid key since D4; the current key's tier is being checked); DATA_PROVENANCE.md §2 history description brought in line with the code (it still described the pre-D2 fill model and Eid as a quantity loss), duplicate section number fixed, interview logged as §6.

**Milestone review (fresh reviewer subagent, 26 Nov):** reproduced every number above and found no weakening of the no-numbers rule, the approval rule or privacy. Findings fixed before commit:
- **Blocker:** re-plan diffs were sorted by quantity only; the new plan has a tie (F0824 and F2184, 12,400 each), so the order followed Python's per-run hash seed and changed the re-plan memo prompt and its cache key. Under two of six seeds the offline demo fell back to the template. Now sorted by (quantity, factory id); a test runs the re-plan under three hash seeds, and an offline app walk under the two failing seeds hits the cache for every answer.
- `warm_cache` now warms the locked re-plan the app actually runs (it warmed the exclude-and-re-solve variant).
- The sweep covers the three new sidebar controls at their ends.
- `replan_from_reply` now includes the QC good share in its expected yield, like the other two places.
- RESEARCH_ASKS.md lists the new placeholders (items 11–15; whole-lot rejection marked the most sensitive).
- Nits: config cross-reference fixed; a plan mixing factories with and without a QC model now raises instead of silently dropping QC; `_fit_qc` returns no QC model when the history is too thin to estimate the spread.
- Not changed: news and what-if re-plans simulate each grid point twice (optimiser view, then re-scoring); acceptable at today's run time, to revisit with the rescue module.

**Open:** the nationwide-hartal case shows the optimiser minimises cost plus a risk penalty and does not maximise probability directly, so its best plan under a correlated national event can be worse than the current one; the app now says so. To revisit with the rescue module (PLAN_V2 §5).

### D13: 26 Nov 2026: order store, service layer, and the app around one order's timeline (PLAN_V2, 29 Nov – 2 Dec)

**Branch:** `v2` (main and the live app unchanged until the product owner approves a merge; first candidate: the thin loop around 4 Dec).

- **Order store (`bunon/store.py`):** SQLite with every table from PLAN_V2 §4 (orders, sourcing_rounds, messages, offers, allocations, progress_updates, forecasts, rescue_options, qc_records, payments, events, attributions, share_settings). `events` is append-only and is the order's timeline; each row carries the **story clock** (day 0 = order received, 3 Jan 2027) and a `source` (`platform`, `synthetic_history`, `simulated_persona`) so track records can later count platform evidence only. One store per visitor session (`data/runtime/sessions/<visitor>.db`, gitignored), so demo visitors never see each other's orders; restart deletes it.
- **Service layer (`bunon/service.py`, no Streamlit import, enforced by a test):** `PlanSettings` (the sidebar settings, frozen), `compute_plans` (was the app's `run_plans` body), `replan_after_decline`, `replan_after_reply`, `assess_news`, `analyst_toolbox`, `factory_record` (a factory's own record from the synthetic history, labelled so), and `OrderBook`: `confirm_order`, the story clock (`advance_to`, forward only), `propose` (once per plan id), `decide` (approve/reject; an approved plan supersedes the earlier approved one; also written to the decision log), `timeline`.
- **Roles:** exporter (owner / director, merchandiser, production manager, quality inspector) and factory (factory owner, floor supervisor), from PLAN_V2. The sidebar's demo role switcher replaces "Acting as"; only exporter roles can confirm orders or approve plans. Roles, never names.
- **App:** three views.
  - **Order timeline** (exporter, the demo): Day 0 · Order received (intake), Day 0 · Plan (headline, recommended plan, memo, approve), Day 3 · Follow-up (scripted decline, factory reply, news; each moves the story clock to day 3; preview until the sourcing and follow-up milestones), the "Ask Bunon" box, and the Timeline table of events.
  - **Show the maths** (exporter): funnel, reliability forecast (now with each factory's QC defect rate), plans compared and the buffer curve, and steering ("Ask for a change"), which moved here per the 26 Nov decision.
  - **Factory app** (factory roles): a phone frame on the laptop with the factory's inbox (empty until the sourcing milestone; nothing is ever sent automatically) and its own record from the synthetic history.
  - The planning settings moved into a collapsed sidebar expander (rendered for every role, so switching roles does not reset them).
- **Unchanged behaviour:** the engine, the prompts and every cached answer; the old demo path (read email → confirm → plan → memo → approve → decline → re-plan → reply → news → what-if) runs in the new layout. Approvals now also land on the order's timeline.

**Tests:** 148 (from 137): `tests/test_service.py` (11: no Streamlit import, every PLAN_V2 table, roles and safe session paths, compute_plans equals the engine, order lifecycle on the story clock, approval supersedes, unknown roles refused, factory record from synthetic history). The sweep follows the new layout: login → timeline → every action → the timeline records the confirmation and (when a plan exists) the approval on day ≥ 3 → Show the maths → steering → the factory app as "Floor supervisor" → logout.

**Milestone review (fresh reviewer subagent, 26 Nov):** no blockers. It verified that the engine and prompt inputs are unchanged from 252ceed and that every scripted LLM answer (intake, both memos, 4 replies and their re-plan memos, 3 news items, 3 what-if questions, 2 steering examples) still comes from the cache offline. Fixed before commit:
- **Roles enforced in the service, not only the UI:** `confirm_order`, `decide` and `record_steer` raise `PermissionError` for factory roles (tested).
- **A re-confirmed order (or a new login) starts again on day 0:** each confirmation is a new order id, and logout now deletes the session's store, as Restart does (tested).
- **Steering lands on the timeline** (`settings_changed` event with the role) via `OrderBook.record_steer`.
- **More order logic moved into the service:** building the confirmed order from the intake (`order_from_intake`) and the reply's checked quantity and delay (`reply_terms`).
- **After a deploy reload,** a stored `OrderBook` built from the old class is replaced (isinstance check).
- Nits fixed: order columns whitelisted in the store; the visitor id must be a 32-character hex id.
- Not changed: session stores are not cleaned up on the host apart from Restart and logout (ephemeral disk; to revisit); the order row keeps the settings at confirmation time; the LLM-step orchestration (memo, reply, news, what-if) stays in `app.py` as thin per-session wrappers around service and agent calls.

### D14: 26 Nov 2026: the thin loop, end to end (PLAN_V2, target 2 Dec)

**Branch:** `v2`. Placeholder persona text (developer's stand-in, flagged `placeholder: true`) until the team's hand-written personas replace it.

**The loop on the order timeline** (each step is a section; a human approves every message and plan; nothing is sent automatically; "sent" means shown in the factory's in-app inbox):
1. **Day 0 · Plan** (unchanged) → approve.
2. **Day 0 · Sourcing round:** the plan's five factories for their planned pieces, plus the best proven factory outside the plan (by forecast deliverable pieces) as a standby for its forecast capacity: F1856, F1064, F0824, F2184, F0288 and F2829. Request drafts are **fixed templates** in English, Banglish or Bangla (the persona's language); every number comes from the plan. Approve and send.
3. **Day 2 · Replies and offers:** the simulated factories answer (persona fixture). Each reply goes through the existing reply parser (quote-checked pieces and delay; the code settles accept / reject / counter). Stated prices are shown as quoted conditions; reading them as numbers is the offer-parser milestone.
4. **Day 2 · The split:** `loop.split_from_offers` re-optimises over the factories that said yes, each capped at the pieces it offered (or was asked for) and never above our forecast capacity; a stated delay cuts its slack; decliners are excluded. Owner approves.
5. **Day 12 · Daily update and warning:** a new LLM task, `update_v1` (`agent.parse_update`, same quote rule: pieces finished, days late, reason). `loop.forecast_after_update` projects the factory from the rate seen since production started (the day the split was agreed, day 2, plus its setup days), minus the stated delay, until its ship-by day, and re-simulates the order with that projection taken as certain; `loop.last_rescue_day` gives the latest day (never in the past) a factory outside the plan could still make the shortfall. The warning text is a fixed template over these numbers.
6. **Day 12 · Rescue:** `loop.rescue_options`: (a) move the shortfall (`loop.rescue_move`: the late factory locked at what it can make, taken as certain; confirmed factories keep at least their pieces and stay capped at what they offered; factories outside the round can join but start late), (b) accept the shortfall, offered only if the expected good pieces still meet the tolerance, (c) tell the buyer. A human chooses; the move is approved as a new allocation (a rejected move can be chosen again); rescue requests (template) are approved and sent; the simulated factories answer (day 13) and their replies are read by the reply parser.
7. **Day 26 · Receive and QC:** the quality inspector (only that role, enforced in the service) records received, rejected and lot-rejected pieces per factory, pre-filled from a scripted QC outcome (shares in the fixture, simulated) applied to what each factory can make (a late factory at its projection); invalid input is refused. The QC row keeps the delivery day (24–26) for the on-time check; the event sits on the story clock.
8. **Track record from this order:** per factory, from platform events only: answered, decision, delay reported, delivered on time (QC on or before the day goods are due at the exporter), QC pass rate.

**What a judge sees at default settings (seed 42; simulated factories with placeholder text):** first plan 87.5% → split from offers **84.1%**, below the 90% target, and the app says so (F0824 declines; F2829 in with 9,900) → F1064's day-12 update ("3,500 ready, 2 days late": 700 a day against 1,092 planned) → projected 9,800 of 13,100, **3,300 short** → chance **81.8%**, last day to rescue **day 16** → move the shortfall to F2829 (+3,300, within its offer of 15,200): **85.6% for +33,757 BDT**; "accept the shortfall" is also allowed (expected good pieces 51,719 ≥ 49,000) at 81.8% → QC on day 26 → F1064 delivered late (day 26 after a 2-day delay); the other four on time; QC pass 96.9–99.1%.

**Engine and store:** `bunon/loop.py` (pure, no LLM); `OrderBook` gains `draft_round`, `approve_round`, `receive` (persona messages become inbound messages, `source='simulated_persona'` on the timeline), `record_offer`, `offer_terms`, `record_update`, `record_forecast`, `choose_rescue`, `send_message`, `record_qc` and `track_record`; `Store` gains checked generic inserts for the loop tables. The persona fixture gains a sixth persona (the standby factory), a `placeholder` flag per message and a scripted `qc_script`.

**Moved:** the scripted decline, the demo replies and the news what-ifs now sit under **Show the maths** ("What-ifs on the first plan") and no longer move the story clock.

**Fixed while building:** the forecast and its rescue options are computed as of the update's own day; recomputing them on a later story day changed the plan after the rescue was approved.

**Milestone review (fresh reviewer subagent, 26 Nov):** reproduced every number, confirmed no LLM-written numbers, service-level role checks and a fully cached offline walk (identical under three hash seeds). Fixed before commit:
- **Blocker: the factory app showed unapproved request drafts in the factory's inbox.** The inbox now shows only messages a human approved and sent (or the factory's own), for the current order.
- **Production start:** the forecast assumed production from day 0; it now starts when the split is agreed (day 2) plus setup days. With that, a day-9 update came too early to judge, so the scripted update moved to **day 12** and its simulated figure to 3,500 pieces (placeholder text; changed for the story, as PLAN_V2 §7 allows for simulated personas, never the engine).
- **The move option is now scored like the forecast** (`loop.rescue_move`): the late factory's projection taken as certain, confirmed factories capped at what they offered (the earlier version used `replan_from_reply`, which ignored offer caps and asked F1856 for more than it offered: 87.8% for +371,500 BDT then, 85.6% for +33,757 BDT now).
- QC pre-fill uses what a late factory can make; invalid QC input is refused instead of crashing; a rejected move can be re-chosen; updates from factories no longer in the split are ignored; rescue replies are parsed.
- **Row ids are scoped to the order** (a re-confirmed order no longer takes over the previous one's rows); messages carry a `kind`, and the app selects by kind (older stores get the column added); QC, rescue choice and messages are idempotent.
- Nits fixed: the last rescue day is never in the past; a failed parse shows as "unreadable", not "not answered"; split and rescue approvals no longer show the "download the drafts" text; PERSONAS_GUIDE says six personas and warns that changed text needs a cache re-warm; the placeholder test now checks tagging, so un-flagging a message does not fail it.
- **Found by the full sweep after the review:** `app.py`'s module-reload list (the Streamlit Cloud hosting quirk) did not include the new `loop` module, so after a code change it kept the old `BufferPoint` class and pydantic rejected the rescue plan. `loop` is now listed, and a test checks that every `bunon` module the app uses is in the list.
- Not changed: `last_rescue_day` may name a factory that was never asked (it answers "until when could anyone still make this", not "who will"); the planner's capacity windows still start on day 0 (slightly optimistic against a day-2 start; to revisit with the offer parser).

**Parser findings on the placeholder text (all cached):** every offer and the update parse to their answer keys except F2829's standby reply ("…Rate 60 BDT per piece"), classified *counter* where the key says *accept*: a rate stated in answer to "your rate" is not a counter-offer. Harmless in the split (counter and accept are treated alike for capacity), but it goes into the offer-parser work (price as a quoted number, decision rules).

**Not yet (later milestones, as planned):** LLM-written drafts and explanations (templates for now), price as a number, several updates per factory, attribution of delay cause, the exporter's payment record, persona text from the team.

**Tests:** 164 (from 149): `tests/test_loop.py` (14, incl. move within offers, placeholder tagging and order-scoped rows; sourcing rule, draft numbers only from the plan, split never above an offer and without decliners, forecast arithmetic, last rescue day earlier for a bigger shortfall, accept-shortfall only within tolerance, update parser quote rules and offline fallback, the loop in the order book with role checks and the track record, placeholders flagged and covering the round). The sweep walks the whole loop at every setting (and to the track record at defaults), then the what-ifs under Show the maths. Cache: 10 new answers (6 offer replies, the update, 2 rescue replies, plus the superseded day-9 update), replayed offline under three hash seeds.

### D15: 26 Nov 2026: the exporter's payment record (PLAN_V2 cut b)

**Why:** the track record is two-sided (PLAN_V2 §0): factories should see whether an exporter pays on time, from platform records only.

**What:** `OrderBook.amounts_due` (per factory with recorded QC: good pieces × the CM rate of the approved allocation, due `PAYMENT_TERM_DAYS` = 30 days after the delivery day, a PLACEHOLDER, research ask 17), `record_payment` (exporter roles only, paid on today's story day, once per factory, event `payment_recorded`) and `exporter_record` (payments, paid on time, average days late, computed only from the `payments` table). The app shows a **Payments** section after the track record. The amount is computed, never typed and never from an LLM; a rejected lot is owed nothing (0 good pieces; whether a rejected lot is paid for at all is part of research ask 13).

**Numbers:** no engine number changes; no prompt changes; the cache is unaffected.

**Tests:** 166 (from 164): the amount is good pieces × rate with the due day; only exporters pay, once, and the on-time rate and days late recompute from the rows. The default sweep case now records the payments.

### D16: 26 Nov 2026: delays and stated causes counted by code (PLAN_V2 §0 item 2)

**Why:** "notices vague or repeated excuses" as an AI judgement is a fairness risk (it could penalise informal writing or Banglish). The AI only reads the stated cause with a quote (`update_v1`); code counts.

**What:** `track_record` gains `delays_reported` (updates with a verified delay), `delays_without_cause` (a delay whose cause did not pass the quote check) and `cause_repeats` (delays citing a cause already given on this order, compared case- and space-insensitively). The table says "No specific cause given" and "Same cause again", never "excuse". No engine or prompt change; cache unaffected.

**Tests:** 167: three delays (two "power cut", one without a cause) and one on-time update → 3 delays, 1 without a cause, 1 repeat.

### D17: 26 Nov 2026: the factory side in the phone view (PLAN_V2 milestone 5–9 Dec, tasks 2.1–2.2 of HANDOFF.md)

**What:** in the Factory app, a factory role (Factory owner or Floor supervisor) can answer a request it was sent (accept, counter-offer, decline; pieces it can make; days late; an optional message) and post a daily update (pieces finished, days late, reason). `OrderBook.factory_reply` and `OrderBook.factory_update` (factory roles only, checked in the service) store the numbers **as the factory typed them**: no AI reads them, so there is nothing to quote-check. The code still settles the decision with the reply parser's rule (`service.settle_decision`: a yes with fewer pieces or a delay is a counter; zero pieces is a decline). The first answer per factory stands; one update per factory per story day. Events carry `source='platform'` and the factory role.

**How the exporter sees it:** a factory that answered for itself gets no scripted persona reply (`receive` skips it); the offers table says "typed by the factory"; the daily-update section uses the latest update in the split, typed or scripted. If a factory is on track, the app says so, offers no rescue and goes on to QC (before this, a zero shortfall still showed "is behind").

**Limits:** the factory role is not tied to one factory (the demo picks the factory in a list); the exporter sees updates from day 12 on; one update drives the forecast at a time (HANDOFF task 3.1).

**Numbers:** none change at defaults (the scripted path is untouched); no prompt change; the cache is unaffected.

**Tests:** 170 unit (from 167): a reply needs a sent request and a factory role, and the first answer stands; a factory's own answer replaces its scripted reply and is a platform event; updates are stored as typed, once a day, and counted on the track record. New click-through `tests/test_factory_app.py` (`-m sweep`): F0824 accepts in the factory app instead of its scripted decline, the exporter's offers show it as typed, then F1856 posts an on-track update and the timeline goes straight to QC.

### D18: 26 Nov 2026: a factory's record across orders (HANDOFF task 4.1)

**What:** `OrderBook.factory_platform_record(factory_id)`: over every order in the visitor's store, the requests the factory was sent, how many it answered and took (accept or counter), deliveries checked at QC and the share on time, the QC pass rate (pieces-weighted) and the delays reported, computed from the platform rows through `track_record`. The Factory app shows it as **"My record on Bunon"** (labelled simulated) above the synthetic-history record, which stays separate. No engine or prompt change.

**Tests:** 172: two orders (one delivered on day 24, one on day 27, after the due day 25) → 2 of 2 answered, on time 50%, QC pass 2,950 / 3,000; an unknown factory has no record.

### D19: 26 Nov 2026: overtime ("surge") rescue option (HANDOFF task 3.3)

**What:** `loop.rescue_options` adds "Overtime at the same factory": the late factory makes `SURGE_SHARE` (15%, PLACEHOLDER) more a day at the rate seen over its remaining days (`Forecast.days_left`), never above the shortfall, at a `SURGE_PRICE_FACTOR` (1.25, PLACEHOLDER) premium on those pieces only; research ask 18. Its chance is re-simulated like the forecast (`loop._risk_with`, factored out of `forecast_after_update`; existing numbers unchanged, guarded by `test_published_demo_numbers`). Choosing it drafts an overtime request to the late factory (`loop.draft_surge_request`, a template in English, Banglish or Bangla whose numbers come from the forecast; message kind `surge_request`), sent only when an exporter approves it. QC pre-fill still uses the projection without the surge (the inspector enters what arrives).

**At defaults:** F1064 +945 with overtime (945 of 3,300 short): **83.1% for +11,692 BDT**, between accepting the shortfall (81.8%) and moving it (85.6%, +33,757 BDT).

**Tests:** 174 unit; the full 24-setting sweep passed on 26 Nov (25 click-throughs incl. the factory app); a new click-through chooses overtime, checks the draft stays a draft until approved, then sends it and goes on to QC.

### D20: 26 Nov 2026: the offer parser reads rate and ship date (`reply_v2`, HANDOFF task 2.3)

**Why:** offers carried prices only as free-text conditions, and F2829's standby reply ("Rate 60 BDT per piece", in answer to "your rate") was classified as a counter-offer (known issue from D14).

**What:**
- `agent.ReplyLLM` gains `price_bdt_per_piece` and `ship_date`, each with a verbatim quote; `REPLY_VERSION = "reply_v2"`; the prompt explains both (PROMPTS.md A2b). `validate_reply(..., planned_price)` checks the rate with `states_number` and the date with `states_date` (day first); a rate outside `PRICE_RANGE_BDT` (30–120, placeholder) is flagged for a human, not dropped.
- **Decision rule, in code:** a quoted rate more than `PRICE_MATCH_TOLERANCE` (2%, placeholder) above our planned CM rate for that factory makes a yes a counter; a counter whose only term is a rate at or within 2% of our planned rate becomes an accept. Our planned rate is the factory's synthetic CM rate, passed by the app (never shown to the LLM).
- Offers store `price_bdt_per_piece`, `ship_date` and their quotes; `OfferTerms.price`; `split_from_offers` prices a factory at its quoted rate; a ship date after the asked finish date becomes a delay in days (code) in `offer_terms`. The offers table shows the rate, our planned rate and the ship date.

**Live run (placeholder persona text):** all six offers match their answer keys in decision, quantity and rate: F1856 accept at 57 (planned 57.18), F2184 counter at 56 (planned 50.18), F2829 **accept** at 60 (planned 59.68; was counter), F1064, F0288 accept, F0824 reject. The four Show-the-maths demo replies keep their classifications.

**Numbers:** the split's allocation and every chance are unchanged (84.1% → 81.8% → move 85.6%, overtime 83.1%). The move's extra cost is **+34,813 BDT** (was +33,757): F2829's extra pieces are now priced at its quoted 60 BDT instead of the synthetic 59.68. The pinned test is updated.

**Cache:** 10 new answers under `reply_v2` (4 demo replies, 5 loop offers, F2829's rescue reply; F0824's decline has the same text and quantity as the demo's Bangla reply, so it shares that answer); replays offline under two hash seeds; the old `reply_v1` answers stay in the cache, unused. The default sweep case now asserts that no offer falls back to hand reading and that F2829 is an accept.

**Tests:** 179 unit (from 174): rate near our planned rate is not a counter, a higher rate makes a yes a counter, the asked rate is the one kept, ship dates day first, out-of-range rate flagged, quoted rates price the split and a late ship date is a delay. Full sweep: 26 passed.

### D21: 27 Nov 2026: AI-written messages to factories and the warning text (HANDOFF tasks 2.4, 3.2, 3.4)

**Why:** the request, rescue and overtime messages and the day-12 warning were fixed templates. PLAN_V2 §5 has the LLM write the words, with the numbers from code.

**What (same rule as the memo: the LLM writes no number):**
- `agent.write_message(kind, facts, language, template)` for `request`, `rescue` and `surge` (task `message_<kind>_<language>`, `message_v1`), in English, Banglish or Bangla (the factory's language). The facts come from code (`agent.message_facts`: pieces asked, finish-by date, GSM; extra pieces and the buyer's deadline). `validate_message`: `check_text` (no digits or English number words outside `{{key}}` placeholders, known keys only), no Banglish or Bangla number words (`textnum.bn_number_words`, at word edges, so "একটি" = "a/an" and "বিশেষ" = "special" are not flagged), the required placeholders present, no factory named, a word limit. One retry, then the deterministic template (`loop.draft_*`). The sourcing table, rescue and overtime drafts show "Written by: AI (checked)" or "template"; `messages.draft_of` records `llm` or `template`. A human still approves every message before it is sent.
- `agent.write_warning(facts)` (`warning_v1`): headline and explanation for the production manager from `agent.warning_facts` (the forecast and the last rescue day). Required placeholders: pieces done, the stated delay, the projection, the shortfall, the chance after the update and the last rescue date; vague amounts ("some", "a few", "several") are rejected, so the text cannot dodge a number. It must not choose a rescue option. The level is set by code (`loop.warning_level`: on track / watch / below target), shown next to the text. Template fallback: the previous fixed text, now in placeholder form.
- First live walk: the warning said "has finished some pieces and reports a delay of a few days"; that passed the digit check but hid the numbers, which led to the required-facts and vague-word rules. F0824's Bangla draft first fell back to the template because "একটি" (a/an) contains "এক" (one); "ek"/"এক" are now left out like English "one".

**At defaults (cached):** six request drafts (four English, Banglish for F1064 and F2184, Bangla for F0824), F2829's rescue request (3,300 pcs by 31 Jan 2027), F1064's overtime request (945 more, 10,745 in all), and the warning ("F1064 reports completing 3,500 pieces and projects a delay of 2 days … projected 9,800 … shortfall 3,300 … 81.8% … until 19 Jan 2027"). No engine number changes.

**Tests:** 188 unit (from 179): AI drafts render only plan numbers; five rule breaks (digit, Banglish number word, missing placeholder, named factory, unknown key) fall back to the template; Bangla/Banglish number words at word edges; an offline miss drafts with the template and `draft_round` records the source; the warning cites only forecast facts, falls back on a literal number or a vague amount, and the level is code. The default sweep case asserts all six drafts are AI-written from the cache and the warning is a cached LLM answer. Full sweep 26 passed; replay checked under two hash seeds.

**Not done:** an "explain the warning" step for other days or several updates (task 3.1), and the persona text from the team (the drafts do not depend on it).

### D22: 27 Nov 2026: forecast from several updates; a warning on a drop between updates (HANDOFF tasks 3.1, 3.2)

**Why:** the day-12 forecast used only the last update that arrived. If a second factory typed an update in the factory app, the first factory's update was dropped, and an on-time update could hide a late one (the factory-app test showed "on track" while F1064 was 3,300 short).

**What (all code, no LLM number):**
- `loop.Update(factory_id, pieces_done, delay_days, day)`, `loop.latest_updates` (the latest per factory) and `loop.forecast_from_updates(world, b, point, cands, updates)`: each factory with an update is projected from its latest one with the same arithmetic as before. All the projections are taken as certain **together** (`_certain` takes several ids, `_risk_with` takes `{factory: pieces}`), and the order is simulated once. The result is one `Forecast` per updated factory, the one furthest behind first. Each forecast carries the joint chance and the others' projections (`Forecast.others`, `.projections`, `.order_shortfall`). `forecast_after_update` is now the one-update case of it.
- Rescue options use the same footing. `rescue_move` locks every updated factory at what it can make; overtime and "accept the shortfall" are scored with all updates; the last rescue day uses the order-wide shortfall.
- `OrderBook.updates(order_id)` turns the recorded updates (typed, or read under the quote rule) into `Update`s; a delay stated with an offer is not an update. `OrderBook.previous_p(order_id, day)` gives the order's chance at the latest forecast before that day.
- A drop between updates triggers a warning: `loop.chance_dropped` / `warning_level(fc, target, prev_p)`. A fall of at least `config.WARN_DROP_PP` = 5 points (PLACEHOLDER, research ask 19) since the previous forecast from updates is "watch" (or "below target") even with no shortfall. A rise is never a warning.
- The warning text: `agent.warning_facts(..., prev_p)` adds `warn.order_shortfall` and `warn.others` with several updates, and `warn.p_prev` after an earlier forecast. These are required placeholders when present and left out otherwise, so the one-update prompt and its cached answer are unchanged.
- The app reads every factory's latest update, shows a table of the projections when there are several, and states the drop in a caption.

**Numbers (defaults, unchanged):** F1064 alone (3,500 done, 2 days late) projects 9,800 pieces, 3,300 short; the order's chance goes from 84.1% to 81.8%, and the move rescue gives 85.6%. **Example with two late factories** (not in the scripted demo): F1856 at 4,000 done and 3 days late projects 9,333 pieces (3,867 short, 79.7% alone). Together with F1064 the shortfall is 7,167 and the joint chance is 69.1%, below either one alone. No move plan is feasible under the current limits, overtime at F1856 gives 75.9%, and accepting the shortfall is no longer within tolerance.

**Tests:** 196 unit tests (from 188):
- one update through the multi path equals the old forecast, with the published 9,800 and 81.8%
- shortfalls add and the chance is joint and no higher than either factory alone
- the latest update per factory wins, and factories outside the plan are ignored
- an on-plan update elsewhere keeps the shortfall and never lowers the chance
- the move locks both late factories
- drop thresholds, including that a rise is not a warning
- service updates and previous chance
- warning facts with several updates: the template cites the order-wide shortfall and the previous chance

The factory-app click-through now has F1856's on-time typed update next to F1064's late scripted one: the table lists both, the warning shows, and the buyer is told. Full sweep passed.

**Not done:** a second scripted update day (needs the story-clock jumps of task 5.2). On the same story day a forecast row recorded before a second update keeps its earlier chance (`record_forecast` writes once per day and factory); `previous_p` reads only earlier days, so the drop rule is unaffected.

### D23: 27 Nov 2026: delay attribution: the AI suggests, code rules cut, a person decides, the factory disputes (HANDOFF task 4.2, PLAN_V2 cut a)

**Why:** a factory's record counted its delays and stated causes, but not whose doing a delay was. That matters for the next order. A late factory that was hit by a hartal is not the same as one that over-promised. PLAN_V2's rule is that the AI never decides against a party alone, and that "outside event" needs evidence.

**What:**
- **Code rules (`bunon/attribution.py`):** categories `factory`, `outside_event`, `exporter_side`, `unclear`. `matching_events` returns recorded news events in the factory's district or nationwide that run at some point between the day the split was agreed and the update.
- **AI suggestion (`agent.suggest_attribution`, `attribution_v1`):**
  - The prompt holds the update text and the matching events.
  - The answer holds a category, a reason quoted from the update, cited event ids, and an explanation with no numbers.
  - `validate_attribution` cuts the answer to `unclear` in two cases:
    - `outside_event` with no cited event that actually matches (an invented id, or none cited);
    - `factory` / `exporter_side` without a quote found in the update.
  - An explanation containing a number is dropped. An offline miss gives no suggestion, and the person decides alone.
- **Service:**
  - `record_news` (an exporter role; the area, type and severity come from `parse_news`; the length is the stated days or the severity table's default; the same item twice is one event)
  - `news_events`, `delays` (each factory's latest update with days late), `matching_events`, `record_suggestion`
  - `decide_attribution`: an exporter role decides. The rule holds for people too: `outside_event` without a matching event raises an error.
  - `dispute_attribution`: the factory's own role disputes, the dispute is kept on the row and in the timeline, and a new decision clears the flag.
  - `track_record` gains `delay_causes` (decided categories) and `causes_disputed`.
- **App:**
  - Day 12 gets "Why is it late?": recorded news (record one of the demo items), the AI suggestion with its notes and matching events, and a radio + "Record the cause".
  - The factory app shows the recorded cause with "Dispute this cause".
  - The track record gets a "Cause decided" column. The rescue is not held up by this step.

**At defaults (live, cached; F1064: "Line e current nai, 2 din late hobe"):**
- **No news recorded:** the AI suggests the factory's side ("a localized power outage on their specific production line").
- **Ashulia labour-unrest item recorded** (it matches by district and days): the AI says unclear, because unrest does not explain a power cut and no area-wide power cut is on record.
- Either way a person records the cause. No engine number changes.

**Tests:** 202 unit tests (from 196) in `tests/test_attribution.py`:
- event matching by district / national / days
- an outside event with a matching event is kept
- an outside event without one is cut to unclear (nothing recorded, an invented id, or a match but none cited)
- the factory's side without a quote from the update is cut, and an explanation with a number is dropped
- an offline miss gives no suggestion
- people decide (roles enforced; outside_event needs a matching, cited event)
- the factory disputes, the record counts the decision and the dispute, and a second decision with evidence clears it

The factory-app click-through now decides F1064's cause as the merchandiser, disputes it as the factory, and sees the dispute on the exporter side. The default sweep case asserts the suggestion replays from the cache. Full sweep passed.

**Open question:** whether "Line e current nai" (no power on the line) is the factory's side or unclear without an area-wide record is a judgement call. The AI chose the factory's side and a person can change it. Should the team prefer that a stated power cut is always "unclear" without a record, add it to the prompt's unclear rule.

### D24: 27 Nov 2026: a stated power cut is "unclear" without evidence; attribution never changes on-time

**Why (product owner's decision on the D23 open question):** under `attribution_v1` the AI read F1064's "Line e current nai" (no power on the line) as the factory's side. A stated outage is not evidence against the factory.

**What:**
- **Rule (`attribution_v2`, a prompt change):** a power cut or other utility outage (electricity, gas, water) is `unclear` unless one of two things holds:
  - (a) a recorded event is an area-wide or national outage of that utility: then `outside_event`, citing it;
  - (b) the factory's own words show it was its side (its generator or wiring failed, an unpaid bill): then `factory`, quoting those words.
  The stated outage alone is never `factory`. A person can still record any category; the D23 code rules still apply, so `outside_event` needs a matching recorded event.
- **Confirmed and tested:** a delay counts against the factory's on-time rate whatever its decided cause. `delivered_on_time` comes only from the QC day versus the due day (`track_record`), and `factory_platform_record` averages that. The decided cause is shown next to it as context (`delay_causes`) and is never read by either. New test: an "outside event" decision leaves `delivered_on_time` False and the on-time rate at 0.
- **Cache:** the two `attribution_v1` answers were removed and the `v2` ones warmed live. F1064 with no news recorded is now `unclear` ("no recorded regional outage event to support an outside cause"). With the Ashulia labour-unrest item recorded it is still `unclear` (the item is not an outage).
- **README roadmap:** an automatic news check of a stated cause (search Bangladeshi news for an outage in that area and date). Not built.

**Tests:** 203 unit tests (from 202). Full sweep passed.

### D25: 27 Nov 2026: story-clock jumps and a second update day (HANDOFF task 5.2)

**Why:** the pitch has three minutes, and the combined forecast (D22) never appeared in the scripted demo.

**What:**
- **Jump buttons** under "Order confirmed": `Day 2 · replies`, `Day 12 · update`, `Day 14 · second update`, `Day 26 · QC`. They are shown to exporter roles only, and only for checkpoints ahead of the clock.
  - A jump presses the same buttons a presenter would, with the demo's default choices: approve the plan, send the requests, next day, approve the split, next day, wait for day 14, and for day 26 also choose "move", approve it and send the rescue requests. Every approval is recorded under the current role, exactly as a click would be.
  - How it works: `_pressed(key)` beside each button, and `jump_continue()` after the view. The jump stops when nothing it waits for is on the page.
- **Second update day:**
  - `loop.UPDATE_DAY2 = 14`.
  - F0288 (English, "slow to answer") sends a placeholder update on day 14 (developer text, labelled placeholder, to be replaced by the team): "4,000 pcs sewn till today. Our fabric from the dyeing unit came late, so we are 2 days behind."
  - The button **Wait: day 14, more updates arrive** is shown until a rescue is chosen.
  - `OrderBook.rescue_day` fixes the forecast at the day the rescue was first chosen. An update that arrives after the choice reaches the factory inbox but does not change the approved rescue. So the day-12 path (rescue on day 12) keeps its published numbers.
- **Headers:** they follow the day ("Day 14 · Daily updates and warning", "Day 14 · Rescue").

**Numbers on the day-14 path** (cached live):
- F1064: 9,800 pieces, 3,300 short. F0288: 9,600 pieces, 1,800 short. 5,100 short in all.
- Chance 81.8% → 80.8%. This is not a "drop" warning (below the 5-point placeholder); the level is "below target".
- Last rescue day is day 14, today (17 Jan 2027).
- Move: F2829 +1,400 → 83.0%. Overtime at F1064 → 82.8%. F2829 accepts.
- The AI suggests "our side (exporter)" for F0288's late fabric, because the requests say the exporter supplies the fabric. A person decides.
- The day-12 numbers are unchanged.

**Tests:** 3 new click-throughs (`tests/test_jumps.py`, `-m sweep`):
- the jump to day 14 shows the two-factory table and a day-14 rescue, records two approvals under the merchandiser, and chooses no rescue;
- a rescue chosen on day 12 keeps the day-12 forecast through to QC;
- the jump to day 26 from the start ends at QC with the rescue chosen and the requests sent.

Unit tests 203. Full sweep 29 passed.
