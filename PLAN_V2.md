# PLAN_V2: Bunon as the trust layer for garment subcontracting

**Status:** approved 26 Nov 2026 with the decisions below. Sections further down are the plan as proposed; where they differ, these decisions win.

**Decisions (product owner, 26 Nov 2026):**
- Front end: **C**, Streamlit plus `bunon/service.py` with no Streamlit imports.
- Personas: **hand-written by the teammates**, frozen in `data/demo/personas.json`, labelled simulated. The fixture format comes first so they can fill it in.
- Cost model: **CM basis** (the exporter supplies fabric) as a labelled placeholder until the interview confirms it. The sewing rate and fabric kg per shirt stay placeholders.
- Demo order dates move into **January 2027**.
- QC: placeholders as proposed (defects and whole-lot rejection), labelled, to be replaced after the interview.
- Factory view on stage: a **phone frame on the laptop**.
- Steering moves behind "show the maths". The what-if analyst stays visible as a small **"Ask Bunon"** box on the order timeline, outside the scripted demo.
- Cut order: **0a, 0b**, then (a) attribution, (b) exporter payment record, (c) Bangla, (d) visual polish.
- Working method: a **fresh reviewer subagent** checks each milestone against SPEC.md, the no-numbers rule, the approval rule and the offline demo, and reports before work continues.
- The README / SPEC / PROMPTS inconsistencies from audit item 6 are fixed as part of SPEC D12.
- Open with the product owner: the Gemini tier of the current key (checked in AI Studio) and the final submission deadline.
**Target:** live final on 26 Dec 2026 (3-minute pitch with live demo); final submission assumed about 24 Dec (to confirm).
**Budget assumed:** one coder, full time, 26 Nov – 24 Dec ≈ 29 days × ~8 productive hours ≈ **230 hours**.

---

## 0. Summary

- The existing code is a solid engine for one moment of the new loop (step 2, "plan"), plus parsers for two kinds of text (factory replies, news). About half of the new loop is new work: offers with price, daily progress updates, a progress-based forecast, rescue options, QC records and track records.
- **Front end: I recommend staying on Streamlit** (restructured around the order timeline, with a phone-width factory view). A React/FastAPI rewrite costs an estimated 70–100 extra hours out of ~230. That cost would come out of the loop the judges score. You decide (section 3).
- **Things in the brief I would change** (details in section 10):
  1. Hand-write the persona replies, or generate them once and then edit them by hand. Purely AI-generated replies being parsed by AI is circular, and judges will notice.
  2. "Notices vague or repeated excuses" should become code that counts stated causes, with the AI only classifying each cause with a quote. The UI should say "no specific cause given", never "excuse".
  3. Delay attribution may say "outside event" only when a matching recorded event exists.
  4. A 9-step loop cannot all be clicked live in a 3-minute pitch. The demo needs a story clock and pre-advanced checkpoints (section 7).
- **LLM:** keep Gemini 3.5 Flash-Lite. A live call works today, and the demo runs from cache. The free tier exists, but Google may use free-tier content to improve its products. That is fine for simulated demo text, but not for a real-user test with real factory messages, which should use the paid tier. Fallback: Groq `openai/gpt-oss-120b` (section 6).

---

## 1. Audit (26 Nov 2026, changed nothing)

| # | Question | Answer |
|---|---|---|
| 1 | Factory reply parsing | **Yes.** `agent.parse_reply` / `validate_reply` / `ReplyLLM` (`bunon/agent.py` ~L527–630): English, Bangla, Banglish → accept / reject / counter with quantity, delay and conditions, each with a verified quote; the code settles the decision. Feeds `optimizer.replan_from_reply` (counter quantity locked exactly; a delay cuts that factory's slack). Tests: 9 in `tests/test_agent.py` (Banglish numbers, counter quantity, accept-with-lower-quantity → counter, delay and condition, invented number dropped, quote not in reply → human, Bangla reject, two re-plan tests). In the UI: section 7 "Factory reply", 4 demo replies (`data/demo/factory_replies.json`), all cached. **Missing for the new loop:** price, ship date as a date (not only "n days late"), progress updates, more than one factory at a time. |
| 2 | Disruption from news text | **Yes.** `agent.parse_news` / `validate_news` (area, type, duration, severity, confidence, each quoted; Bangla place names checked) → fixed `config.SEVERITY_TABLE` → `optimizer.event_from_severity` / `assess_news` (current plan's P under the event + a plan made knowing it). Tests: 5 in `tests/test_agent.py` plus `test_engine.py::test_known_event_lowers_current_plan_and_replan_avoids_it`. UI: section 7 "News", 3 cached demo items. |
| 3 | Tool-calling agent / steering | **Both yes.** Steering: `agent.steer` (D5), 3 tests, UI "Ask for a change". Tool-calling analyst: `bunon/analyst.py` (D8), Gemini native function calling over `run_simulation`, `solve_plan`, `compare_plans`, max 6 calls, placeholders only; 9 tests in `tests/test_analyst.py`; UI section 8 with 3 cached examples. |
| 4 | LLM provider, live call, cache | Gemini `gemini-3.5-flash-lite` via `google-genai` 3.3.0 (`bunon/llm.py`, the only SDK import). **Live call works:** one connectivity call today returned valid JSON in 2.3 s. Cache: 60 answers, all this model: analyst 38 (steps), memo_replan 11, memo_initial 1, intake 1, reply 4, news 3, steer 2. The public app runs live with caps (40/visitor, 400/day); the scripted story never needs the network. |
| 5 | Tests, deployment | `pytest -q`: **121 passed** (20 sweep cases deselected; full sweep passed 19 Nov, 20/20). Deployed app at buunon.streamlit.app shows the 19 Nov login page (commit 1eef387); the only later commit is CLAUDE.md, which does not affect the app. So it matches `main`. |
| 6 | Broken / half-finished / not matching SPEC | No failing behaviour found. Inconsistencies: (a) README says decisions D1–D9; SPEC.md now has D1–D11. (b) PROMPTS.md line 12 and the `config.py` comment say "free tier", but SPEC D4 and PROMPTS.md line 195 say the project was not eligible and a paid key was used. Which tier the current key is on needs one check in AI Studio. (c) The decision log and the live-call counter live in `data/runtime` on Streamlit Cloud and reset when the container restarts (known, D9). (d) One cached what-if answer labels only one factory with a district ("F0288 (gazipur)"); harmless but odd on screen. (e) `app.py` is a single 971-line file, organised by algorithm, which the new direction replaces. (f) Cost model: `MATERIAL_COST_BDT_PER_PIECE` (180 BDT) is charged as if the subcontractor buys fabric, but the demo reply says "Please send the fabric": in CM subcontracting the exporter supplies fabric. See open question Q4. |

---

## 2. Reuse, change, retire

| Part | Decision | Notes |
|---|---|---|
| `data_gen`, public DB, privacy tests | **Reuse, extend** | Add synthetic QC columns to `history` (defect rate, whole-lot rejection) and synthetic progress-rate behaviour per factory. Rebuild with the same seed; the privacy tests stay. |
| `reliability` | **Reuse, extend** | Add per-factory QC defect rate (Beta, pooled prior) and lot-rejection probability. Keep empirical Bayes. |
| `optimizer` (candidates, MIP, re-plans, events) | **Reuse, change** | New success rule; offers become caps and prices on candidates; ranking adds distance (haversine to the Gazipur point already exists) and track record; manager-entered loss on failure replaces the penalty parsed from email; safety margin optional, default 0%. |
| `simulation` | **Change** | Success = good pieces ≥ (1 − tolerance) × Q, counting good pieces only; add defect draws and lot rejection; add a "from today" mode that starts from reported progress. |
| `agent.parse_order` (intake) | **Reuse, small change** | Add quality terms (AQL etc.) as quoted text fields. Remove penalty-to-cost: loss on failure is a manager input. |
| `agent.parse_reply` | **Reuse, extend** | Becomes the offer parser: add price (BDT/piece, quoted) and ship date; validate against the request sent to that factory. |
| `agent.write_memo`, placeholders, `check_text`, templates | **Reuse as the pattern for every generated text** | Message drafts, warnings, rescue explanations and attribution reasons all use `{{key}}` placeholders and the same checker. |
| `agent.parse_news` + severity table | **Reuse** | Feeds the forecast and attribution (corroborating "outside event"). |
| `analyst.py` (what-if) | **Keep, demote** | Moves to the "show the maths" view. Not in the core loop. |
| `agent.steer` | **Keep, demote** | Planning-settings panel only. |
| `llm.py` (cache, modes, caps) | **Reuse, extend** | Add a second provider class (fallback). Same cache key rules. |
| `decisions.py` | **Change** | Becomes part of the event store (section 4): approvals are events. |
| `app.py` (section-per-algorithm UI) | **Retire / rewrite** | Replaced by the order-timeline app (section 3). Engine screens move to "show the maths". The login and theme carry over. |
| `checkpoint.py`, `warm_cache.py` | **Reuse** | `warm_cache` grows to cover the persona script. |
| Three-plan comparison (naive vs Bunon) | **Keep in "show the maths"** | Still the clearest proof that the forecast matters; not on the main timeline. |
| Tests (121 + sweep) | **Keep; sweep rewritten** | The sweep follows the new timeline. The engine tests whose numbers change with the new success rule get their expected values recomputed and logged in SPEC.md. |

---

## 3. Front-end decision (you decide)

| | A. Streamlit, restructured (recommended) | B. React + FastAPI over the engine | C. Streamlit now, FastAPI-ready core |
|---|---|---|---|
| What | `st.navigation` pages: Exporter (order timeline), Factory (phone-width), Show the maths. Chat via `st.chat_message`. Role switcher in the sidebar. | FastAPI wraps `bunon/` as endpoints; React (Vite) app with exporter and factory views; separate hosting for API and front end. | A, but every action goes through a plain-Python service layer (`bunon/service.py`) with no Streamlit imports, so a web front end can be added later. |
| Extra hours vs today | ~25 h restructure | ~95–125 h (API ~20, front end ~50–70, auth-free role routing, deploy two services ~10, end-to-end tests ~15) | ~30 h |
| Demo risk | Low: same host and patterns that have run since 15 Nov; reruns can feel slow (6 s "confirm → plans" today; needs caching). | Higher: two deployments, CORS, cold starts on free hosting, a new e2e test stack, all learned and debugged in December. | Low. |
| Looks "production-grade"? | Medium. Clean custom CSS helps; judges may recognise Streamlit. | High, if finished. A half-finished React app looks worse than a finished Streamlit one. | Medium, plus a credible architecture story ("the core is UI-independent"). |
| Fits 230 h? | Yes, with ~20 h slack. | Only by cutting attribution, payments, Bangla and polish up front. | Yes, with ~15 h slack. |

**Recommendation: C.** Build on Streamlit, but put all order logic in a service layer with no Streamlit code. That keeps the demo safe and lets you say truthfully that the core is ready for a web or WhatsApp front end. Revisit only if the thin loop is done well before 4 Dec. **Decision needed from you.**

---

## 4. Data model (SQLite, one file per environment, demo fixture reloadable)

All rows carry `is_simulated` (persona or synthetic) and timestamps on the **story clock** (`story_day` 0…N plus a calendar date), so the demo can jump between days.

| Table | Key fields | Notes |
|---|---|---|
| `roles` / `demo_users` | role (`owner`, `merchandiser`, `production_manager`, `qc_inspector`, `factory_owner`, `floor_supervisor`), factory_id (factory roles) | Role switcher only; no passwords. |
| `orders` | order_id, buyer (fictional), product, total_qty, own_capacity_qty, subcontract_qty (computed), deadline (buyer date), tolerance_pct, safety_margin_pct, loss_on_failure_bdt (manager input), quality_terms, source_spans (JSON), status | `subcontract_qty` computed in code. |
| `sourcing_rounds` | round_id, order_id, story_day, factory_ids, requested_qty per factory, requested_date | One round = several requests at once. |
| `messages` | msg_id, order_id, factory_id, direction (out/in), channel (`in_app`), author_role, text, language, draft_of (LLM draft id), approved_by_role, approved_at, sent_at, is_simulated | Outgoing messages exist as drafts until a human approves; "sent" means "shown in the factory inbox" in the app. |
| `offers` | offer_id, msg_id, factory_id, decision (accept/reject/counter), qty, price_bdt_per_piece, ship_date, conditions, quotes (JSON), parse_status | From the extended reply parser. |
| `allocations` | alloc_id, order_id, version, factory_id, qty, price, window, approved_by_role | Each confirm or rescue makes a new version. |
| `progress_updates` | update_id, factory_id, order_id, story_day, text, pieces_done_cum, pieces_today, delay_days, cause_text, cause_category, quotes, parse_status | `pieces_*` and `delay_days` must be quoted, else the supervisor confirms them. |
| `forecasts` | order_id, story_day, factory_id, expected_good_by_deadline, p10/p90, p_meet_order, last_rescue_day | Computed, stored for the timeline and for "show the maths". |
| `rescue_options` | option_id, order_id, story_day, kind (`move`, `surge`, `accept_shortfall`, `escalate_buyer`), details (JSON), p_meet_after, cost_delta_bdt, chosen, approved_by_role | |
| `qc_records` | qc_id, factory_id, order_id, story_day, received, good, rejected, lot_rejected, inspector_role | Entered by the QC role. |
| `payments` | payment_id, order_id, factory_id, amount_bdt, due_day, paid_day | Exporter side of the record (cut item b). |
| `events` | event_id, kind (`offer_accepted`, `delivered`, `qc_result`, `delay_reported`, `payment_made`, `news_event`, …), subject (factory or exporter), order_id, story_day, payload (JSON), source (`platform`, `synthetic_history`) | **Only platform events feed track records**; synthetic history is imported as `source=synthetic_history` and labelled. |
| `attributions` | delay_id, factory_id, order_id, category (`factory`, `outside_event`, `exporter_side`, `unclear`), reason_quote, evidence_event_ids, ai_suggestion, human_decision, disputed_by_factory | AI suggests; a human decides; the factory can dispute. |
| `share_settings` | factory_id, field, visibility (`private`, `exporters_worked_with`, `all_exporters`) | "Factories control what they share." |

---

## 5. Module contracts (new parts)

The same rule everywhere: **code computes every number; the LLM reads and writes words; generated text cites numbers only through `{{key}}` placeholders checked by `agent.check_text`; nothing is sent without a human approval event.**

| Module | Input → output | LLM or code | Guardrails and tests |
|---|---|---|---|
| `service.py` | UI actions (`create_order`, `start_round`, `approve_message`, `record_reply`, `confirm_split`, `post_update`, `run_forecast`, `choose_rescue`, `record_qc`, `close_order`) → state changes + events | Code | No Streamlit imports. Every approval writes an event with the acting role. |
| `sourcing.py` | order, ranked candidates → `SourcingRound` with one draft message per factory (language per factory) | LLM drafts words; quantity, date and price ask come from code as placeholders | Draft blocked if it contains digits outside placeholders; tests: drafts render the right numbers per factory; nothing is marked sent without approval. |
| `offers.py` (extends `parse_reply`) | reply text + the request sent → `Offer` (decision, qty, price, ship date, conditions, quotes) | LLM extracts; code validates quotes and settles the decision | Each number must be quoted; a price far outside the configured range (placeholder) is flagged, not dropped. Tests in English, Banglish, Bangla per field. |
| `split.py` | offers + candidates → proposed allocation | Code (existing MIP with offer caps and prices) | Respects offered qty, price, date, compliance, district cap; test: never allocates more than offered. |
| `followup.py` | daily update text → `ProgressUpdate` (pieces, delay days, cause text and category) | LLM extracts; code checks | "line e current nai, 2 din late hobe" → delay 2, cause "power cut" (category `outside_event` only as a *suggestion*). Tests for Banglish number words, missing numbers → supervisor confirms. |
| `forecast.py` | allocation + updates + reliability + QC model → per-factory expected good pieces by deadline (with band), P(order meets tolerance), last rescue day | Code (Monte Carlo "from today") | Last rescue day = latest day on which the best alternative can still make the projected shortfall: deadline − QC days − transport − setup − ⌈shortfall / spare daily capacity⌉. Tests: monotone in shortfall; equals deadline arithmetic on a hand-built case. |
| `warning.py` | forecast change → warning (severity from code thresholds) + plain-language explanation | Code decides; LLM explains with placeholders | No warning text without a forecast fact behind it. |
| `rescue.py` | forecast + candidates + offers → options (`move` X pieces to factory Y, `surge` at the same factory, `accept_shortfall` if within tolerance, `escalate_buyer`) with P and cost each; LLM drafts the messages for the chosen option | Code computes; LLM writes | Reuses `replan_locked` (pieces already made stay locked). Tests: options are feasible; "accept shortfall" offered only if within tolerance. |
| `qc.py` | inspector input → QC record + events | Code (form) | Good + rejected = received. |
| `trackrecord.py` | events → metrics per factory (acceptance rate, on-time rate, QC pass rate, average delay days) and per exporter (on-time payment rate) | Code | Only `source=platform` events count toward the "verified" record; synthetic history shown separately and labelled. Tests: metrics recompute exactly from events. |
| `attribution.py` (cut a) | a delay + its update texts + recorded events in the same district/days → suggested category with quoted reason and evidence ids | LLM suggests; **code rule:** `outside_event` only if a matching recorded event exists, else at most `unclear` | Human decides; factory can dispute; tests for both branches. |
| `personas.py` | persona definitions (factory id, writing style, language, reliability profile) → the scripted message set for the demo | Scripted text (hand-written or LLM-drafted then edited), frozen in `data/demo/personas.json` | Labelled "simulated factory" everywhere; the replies are fixtures, so the demo cannot fail. |

---

## 6. LLM provider

**Checked 26 Nov 2026 in the official docs** ([rate limits](https://ai.google.dev/gemini-api/docs/rate-limits), [pricing](https://ai.google.dev/gemini-api/docs/pricing), now served from aistudio.google.com/docs):

- `gemini-3.5-flash-lite` is on the **free tier** ("Free of charge" in the pricing table) and supports structured output and function calling. Paid price: $0.30 per 1M input tokens, $2.50 per 1M output tokens.
- The docs do not publish free-tier RPM/RPD numbers. They say limits apply per project, are shown in AI Studio, and that requests-per-day reset at midnight Pacific. Free tier needs an "active project or free trial; some projects need verification".
- **Free-tier content is "used to improve our products"; paid-tier content is not.**

**Decision proposed:**
- Keep Gemini 3.5 Flash-Lite. Development and the demo use only fictional or simulated text and pseudonymous IDs, so the free tier is acceptable there if your project is eligible.
- Any real-user test with real factory or exporter messages uses the **paid tier**. At this volume the cost is cents.
- Check in AI Studio which tier the current key is on (see audit 6b).

**Fallback:** Groq `openai/gpt-oss-120b`, a "production" model on Groq. Its docs list Developer-plan base limits of 30 RPM / 1K RPD ([rate limits](https://console.groq.com/docs/rate-limits), [models](https://console.groq.com/docs/models)). The docs page lists no separate free-plan numbers, Groq's data-use terms are not yet checked, and JSON-schema output with tool calling must be tested on that model before relying on it. It would be added as a second `Provider` class in `llm.py` behind the same cache. Only one account per provider, keys only in `.env` and Streamlit secrets, and the existing caps stay: never rotate accounts or evade limits.

**Demo safety does not depend on the provider:** every scripted step replays from cache, and a test runs the whole demo offline.

---

## 7. The demo script

**Constraint:** the pitch is 3 minutes in total, so the live clicking gets about 90–100 s. Nine steps cannot each be clicked from scratch in that time. Proposal:

- **A story clock with checkpoints.** The order starts on day 0. Buttons jump to the next event ("Day 3: replies in", "Day 9: update from F2184", …). Each jump replays fixtures and cached answers instantly.
- **Two views side by side** on the projector: exporter (laptop width) and factory (phone frame). The factory view shows the same messages from the factory's side.

**The order:** reuse the current fictional order: 150,000 basic T-shirts, 100,000 in-house, **50,000 subcontracted**, buyer deadline 20 Dec 2026, delivery to the Gazipur point. Open question Q5 is whether to move the dates after the final. Tolerance 2%, so success means at least 49,000 good pieces by the deadline. Safety margin 0%, and a manager-entered loss on failure.

**Factories:** five to six pseudonymous factories from the current candidate set, each a **simulated persona** with a writing style and a reliability profile:
- formal English, reliable
- Banglish, fast, over-promises
- Banglish, counters on price
- Bangla, declines
- English, slow to answer

The exact IDs are chosen when the fixture is built, from factories that make the story work on the engine's numbers. No story outcome is forced: if the engine's numbers don't produce a rescue moment, the persona's progress updates are changed (they are simulated) and the change is documented. The engine itself is never tuned for the story.

| Clock | Screen (role) | Judge sees | ~s |
|---|---|---|---|
| Day 0 | Exporter / merchandiser: paste buyer email → fields with quotes → confirm | "50,000 to subcontract, by 20 Dec, AQL 2.5", every value quoted | 10 |
| Day 0 | Ranked factories (distance, capacity, compliance, record) → "Ask 5 factories" → 5 drafts in English/Banglish/Bangla → approve all | AI drafts, human approves, nothing sent before that | 15 |
| Day 2 | Factory view: replies arrive (simulated, labelled); exporter view: offers table (qty, price, date per factory, quoted) | One Banglish counter on price, one Bangla decline | 15 |
| Day 2 | Proposed split from offers → owner approves | Split, cost, P(≥ 49,000 good by deadline) **[engine]** | 10 |
| Day 9 | Factory supervisor posts "line e current nai, 2 din late hobe" → parsed: 2 days late, cause "power cut" | Forecast for that factory drops; order P drops **[engine]**; warning: "last day to rescue: day [engine]" | 15 |
| Day 9 | Rescue options with P and cost → production manager picks "move [engine] pieces to F…" → AI drafts the message → approve | Recovered P and cost **[engine]** | 15 |
| Day 24 | QC inspector records good / rejected per factory | Good pieces vs tolerance | 5 |
| Day 24 | Track records update: factory acceptance, on-time, QC pass; delay cause suggested as "outside event (power cut), corroborated by [news event]" or "unclear", human confirms | Two-sided record from verified platform events only | 10 |

Numbers marked **[engine]** are computed by the engine on the frozen fixture when it is built. A test asserts them, so the script, README and video stay consistent. They cannot be written here honestly before the new success rule and QC model exist.

---

## 8. Schedule

| Dates | Goal | Done when |
|---|---|---|
| **Wed 26 – Fri 28 Nov** | Interview inputs into config (tolerance, QC model, prices, loss input, margin 0%); log the interview in DATA_PROVENANCE.md as single-source expert input; new success rule in `simulation`; QC columns in synthetic history; recompute and log the changed numbers (SPEC D12) | Tests green with recomputed values; new numbers in SPEC.md |
| **Sat 29 Nov – Tue 2 Dec** | Data model + event store + `service.py`; app restructured around one order's timeline (exporter page, factory page, show-the-maths page, role switcher, story clock); intake and plan moved onto the timeline | The old demo path works in the new layout |
| **Wed 3 – Thu 4 Dec** | **Thin end-to-end loop:** order → sourcing drafts (approve) → scripted replies → offers → split → one scripted update → forecast → one rescue option → QC form → record table. Crude UI allowed. | **4 Dec: the full loop clicks through offline**, with a sweep-style test over it |
| 5 – 9 Dec | Factory side (phone view, accept/reject/counter, daily update form, own record); sourcing round with several factories; offer parser with price and date; personas fixture v1 | Factory view usable on a phone width; offer parser tests in 3 languages |
| 10 – 16 Dec | Follow-up parser; forecast from progress; warnings; rescue options (move / surge / accept shortfall); track records; attribution (if not cut); payments (if not cut) | All loop steps have real logic and tests; demo fixture frozen with asserted numbers |
| 17 – 22 Dec | Polish, "show the maths" view, real-user test (one merchandiser, 30–45 min, paid tier if real text), fixes from it, docs (SPEC, PROMPTS, DATA_PROVENANCE, README), video | User-test notes in SPEC.md; video under 181 s |
| 23 – 24 Dec | **Freeze**, release check (clean clone, privacy scan, live timing), submission | Tagged release; deployed app matches the tag |
| 25 – 26 Dec | Rehearsal only; no code changes | |

---

## 9. Cut order if behind

As you set it: (a) attribution of delay cause → (b) exporter payment record → (c) Bangla (keep Banglish and English) → (d) visual polish.

**Never cut:** order → sourcing → follow-up → warning → rescue → record.

I'd add two cuts before (a), because they are cheaper to lose:

- **(0a)** the phone-frame factory view becomes the same page at narrow width.
- **(0b)** the what-if analyst and steering leave the UI. They stay in the code and tests and can be shown from the repo.

---

## 10. Risks, fallbacks and an honest assessment

| Risk | Fallback |
|---|---|
| Engine numbers change with the new success rule, and the old README/video numbers become wrong | Recompute all published numbers in one step (SPEC D12); a test asserts the demo fixture's numbers. |
| Streamlit reruns feel slow on stage | Cache engine results per story-clock checkpoint; precompute the demo fixture's forecasts. |
| Provider outage or quota at the final | The scripted demo is fully offline; the live LLM is only for judges' own text. |
| Streamlit Cloud cold start (30–40 s) | Wake it 10 min before; keep a local copy running as backup on the laptop. |
| Scope: ~210 h of estimated work in ~230 h | The thin loop by 4 Dec is the checkpoint; if it slips past 6 Dec, apply the cut order immediately. |

**Honest assessment:**

1. **Personas.** If the AI generates factory replies and the AI then parses them, the demo shows the model agreeing with itself. Better: write the persona messages by hand (ideally checked by the interviewee for realism), or generate drafts and edit them, then freeze them. Label them simulated.
2. **"Notices vague or repeated excuses."** As an AI judgement this is a fairness risk: it could penalise informal writing or Banglish, and it feeds a record that affects a factory's business. Proposed: the AI only classifies the stated cause with a quote; code counts repeats; the UI says "no specific cause given" or "third delay this order citing power", never "excuse"; a human decides.
3. **Delay attribution** affects a factory's record, so it must be evidence-based (an outside event requires a recorded event), override-able, and disputable by the factory. This is the "AI never decides against a party alone" rule. It's worth keeping, but it's correctly first in the cut order.
4. **"Verified track record" in the demo is simulated.** Every event in the demo comes from personas or synthetic history. The pitch must say "verified once on the platform; in this demo, simulated". Claiming otherwise would undercut the trust story.
5. **QC model without data.** The defect rate and lot-rejection probability will be placeholders. The interview gives a direction, not values; mark them as such.
6. **Hidden buyer cushion (5–7 days).** The brief plans to the buyer's stated date, which is right. The cushion should not appear in the model (using it would plan to be late); at most one sentence in the pitch.
7. **Redundant for the new story:** the three-plan comparison and the what-if analyst. They're good engineering, but off the main loop; keep them in "show the maths".
8. **Feasible:** everything else. The hardest new piece is the progress-based forecast plus "last rescue day". That is new modelling, kept deliberately simple (section 5).

---

## 11. Open questions

1. **Final submission:** exact deadline and required items (MCP disclosure is listed: Bunon uses none; fine to state).
2. **Front end:** A, B or C (section 3)? I recommend C.
3. **Personas:** hand-written, or LLM-drafted and edited by you? Can the interviewee review them for realism?
4. **Cost model:** in your subcontracting, who supplies fabric (exporter, i.e. CM only)? If yes, I'll remove fabric from the subcontractor price and count it only as the exporter's cost of buffer pieces. Also needed:
   - the T-shirt-only sewing rate, or keep a placeholder derived from 150 BDT per set
   - fabric kg per shirt (placeholder ~0.18–0.20 kg at 160 GSM)
5. **Demo dates:** keep the 20 Dec 2026 deadline (before the final, so it's a past order on stage), or move the fictional order into January 2027?
6. **Quality:** any rough figures from the interview for defect percentage and how often a whole lot is rejected? Otherwise placeholders (e.g. 2% defects, 2% lot rejection).
7. **Real-user test:** who (a merchandiser?), when (17–22 Dec), and are they OK with the paid-tier LLM seeing their text? Is written consent needed?
8. **Gemini key:** is the current key's project on the free or paid tier (AI Studio shows it)? OK to keep a small paid budget with an alert for the real-user test?
9. **Factory side on stage:** a phone in hand mirrored to the projector, or a phone frame on the laptop?
10. **Keep the what-if analyst and steering visible**, or move them behind "show the maths" (my suggestion)?
11. **Team section and roles** for the submission (names and roles only).
