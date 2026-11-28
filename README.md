# Bunon: AI procurement analyst for subcontracted garment orders

> **Prototype, preliminary submission (20 Nov 2026)** for the International AI Builders Congress (Bangladesh), IndustrySphere: "Supply Chain Nexus".
> **The factory list is real (Open Supply Hub, pseudonymised). All performance data is synthetic.** See [DATA_PROVENANCE.md](DATA_PROVENANCE.md).

When a Bangladeshi garment exporter can't make a whole buyer order in-house, Bunon picks the set of subcontract factories most likely to deliver on time. It adds a priced buffer, and re-plans when a factory declines, counter-offers or is hit by a disruption in the news. A human approves every decision, and nothing is sent automatically.

**How the AI is used.** An optimiser (PuLP/CBC) and a Monte Carlo simulation produce every number. The LLM (Gemini) handles language: it reads the buyer's email, factory replies in English, Bangla or Banglish, and news headlines; it decides which tools to run for a what-if question; and it writes the decision memo, the messages to factories (English, Banglish, Bangla) and the warning after a daily update; it also suggests why a factory was late, which a person decides and the factory can dispute. It never writes a number itself: extracted values must come with a word-for-word quote that the code checks, and memos and answers cite tool results through placeholders. Anything that fails a check falls back to hand entry or a fixed template.

**Demo results** (synthetic performance data, seed 42; recomputed 28 Nov 2026, SPEC.md D31). The demo order is a fictional buyer's kids' T-shirt programme of **10 styles, 16,800 pieces, all subcontracted**, modelled on 10 anonymised real delivery orders (`data/demo/docs/`), due 3 → 31 January 2027.
- **A style always goes whole to one factory:** the interviewee confirmed a style is never split.
- **Success (interview):** the programme ships by the buyer's date, because lateness causes cancellation. Short pieces and QC rejects are a cost, not a failure: lost FOB, less the salvage value of the rejects.
- **Target:** 90%.
- **Calibration:** the synthetic lateness is calibrated so that the naive plan succeeds 90% of the time. The interviewee says failures are rare; the anchor is a placeholder until a figure is given. This is calibration to an observed base rate, not tuning (`python -m bunon.calibrate`, SPEC.md D31).

| | P(programme ships on time) | Cash cost | Expected cost of short pieces |
|---|---|---|---|
| Naive: declared capacity + lowest price | 90.0% (the anchor) | 1,618,480 BDT | 459,502 BDT |
| **Bunon** (2 factories, whole styles, no over-cut) | **92.1%** | 1,765,173 BDT | 233,571 BDT |
| After the largest factory declines on day 3 (re-plan) | 91.6% | | |

**How much the headline depends on the anchor** (`SENSITIVITY.md`):

| Naive plan calibrated to | Bunon's chance |
|---|---|
| 80% | 86.8% |
| 90% | 92.1% |
| 95% | 94.7% |

At 95%, Bunon is slightly below the naive plan, because the remaining failures come from correlated shocks.

Bunon's edge at the default is less the chance (+2.1 points) than the cost of short pieces: it picks factories that deliver in full, which halves the expected shortfall cost.

The pre-D30 piece-split demo (50,000 pieces over 5 factories, 87.5%) is kept as a regression test on a frozen copy of the earlier database (`tests/test_loop.py::test_piece_split_demo_numbers`).

Factory funnel (real Open Supply Hub data): 3,214 → 3,196 open → 1,748 make T-shirts → 442 RSC or valid BSCI → 221 with spare capacity, a feasible deadline and compliance still valid on the deadline (synthetic capacity).

**Direction (26 Nov 2026):** Bunon is becoming the trust layer for garment subcontracting: sourcing rounds, daily follow-up, early warnings, rescues and two-sided track records around one order's timeline. See [PLAN_V2.md](PLAN_V2.md); work in progress.

**Live demo: https://buunon.streamlit.app/** (Streamlit Community Cloud. The first load after a quiet period can take a minute while the app wakes up.)

**LLM on the live demo:** cached answers are replayed first (the scripted story never needs the network). New text, such as your own factory reply, headline or what-if question, goes live to Gemini, capped at 40 calls per visitor session and 400 per day for the whole demo; past a cap, that step falls back to hand entry and says so. Locally, add `BUNON_LLM_MODE=live` and `GEMINI_API_KEY` to `.env` (gitignored).

**Hosting note:** Streamlit Cloud reruns `app.py` after a push but keeps already-imported modules in memory. `app.py` therefore hashes `bunon/*.py` on every run and reloads the package when it changed. After a push, still run the story once; if anything errors, Manage app → ⋮ → Reboot app. The sidebar shows the server's Python version.

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows; use `source .venv/bin/activate` elsewhere
pip install -r requirements-dev.txt
pytest -q
```

Before a release, also run the slow click-through of the app at extreme sidebar settings (several minutes, offline, no LLM calls):

```bash
pytest -m sweep
```

The processed database `data/processed/bunon.db` is committed. Rebuilding it from scratch needs the raw Open Supply Hub export at `data/raw/factories.csv`, which is not in this repo:

```bash
python -m bunon.data_gen
```

## Status

| Module | State |
|---|---|
| `data_gen`: clean real export + synthetic history | done |
| `reliability` (empirical Bayes), `optimizer` (PuLP/CBC), `simulation` (Monte Carlo with shocks) | done |
| `agent`: LLM intake (verbatim-quote checks) + decision memo (placeholder-only numbers), cached | done |
| Streamlit UI: email → confirmed order → three plans → memo → approve → day-3 decline → locked re-plan → approve; decision log; draft POs (download only) | done |
| Plain-language steering of the planning settings (values quoted from the instruction) | done |
| Factory replies (English / Bangla / Banglish): accept / reject / counter, quantity, delay, rate and ship date (`reply_v2`), each quoted, feed the split and re-plan | done |
| News headlines (English / Bangla): area, type, duration, severity → fixed table → simulated impact and re-plan | done |
| What-if analyst: Gemini function calling over simulate / solve / compare (max 6 calls, every call logged) | done |
| Live LLM with caps (40 calls per visitor, 400 per day); clear messages when no plan or re-plan is feasible | done |
| Whole-style engine (D30): a binary style-to-factory MIP, over-cut per style, per-style success in the simulation, rescue by moving an unstarted style / overtime / accepting a shortfall within tolerance / renegotiating the date | done |
| Real delivery orders anonymised on the local machine (`bunon/anonymise.py`, D29); the raw PDFs are never committed or sent to the LLM | done |
| Interview calibration (D31): ship-by-date success, quality as a cost, attendance in the daily update, the floor representative, ranking, new entrants; `python -m bunon.calibrate` | done |
| Tests: 240 (`pytest -q`) + 31 app click-throughs: 24 extreme settings, the factory app, the floor representative, the story-clock jumps (`pytest -m sweep`) | passing |
| Real values for the placeholder assumptions (fill/lateness, costs; see RESEARCH_ASKS.md) | waiting for research |

**The 3-minute demo** (branch `v2`; numbers in [VIDEO_NOTES.md](VIDEO_NOTES.md)):
1. **Continue as demo exporter** → **Start a new order** → **Read the email**. The 10 attached delivery orders are read in code (no AI).
2. **Confirm order.** The plan gives F0288 six styles and F1558 four. A box shows the new entrant F0882: no history, but a vouch and a recent site visit, and the price of a trial style for it. **Approve** the plan.
3. **Approve and send the requests** → **Next: day 2** (the simulated factories reply) → **Approve** the split.
4. **Next: day 12.** F1558's Banglish update: "1,800 ready, 42 of 60 operators came, productivity low, 1 day late". That is 64% of its daily target, a warning, and the last rescue day is today.
5. Switch the role to **Exporter · Floor representative** and **Post floor report**. The count of 1,600 differs from the factory's 1,800, so it is flagged, and the forecast uses the floor's count. Switch back to **Merchandiser**.
6. **Choose: Move an unstarted style to another factory** (S03 and S09 to F2829) → **Approve** → **Approve and send the rescue requests**.
7. **Next: day 26** → switch to **Exporter · Quality inspector** → **Record QC** → the **Track record** and the **Timeline**.

Other paths:
- **Wait: day 14.** F0288's late fabric (the exporter's side) adds a second shortfall. By then no outside factory can take whole styles.
- **Factory · Floor supervisor:** a factory's phone. Pick F0882 to see its profile.
- **Show the maths:** the ranked candidates (capacity, workers, capability, quality), the plan comparison, steering and the what-ifs.
- **Jump ahead** buttons (Day 2, Day 12, Day 14, Day 26) press the buttons for you with the default choices.

Every step replays from the cache.

**Release check:** the last full one was on 18 Nov 2026, before the programme demo. It covered a fresh clone, a new virtual environment, the tests and a privacy scan of every published file. The next one is planned for the final week (HANDOFF.md §4).

Design decisions and their before/after numbers are logged in [SPEC.md](SPEC.md) (D1–D33). The Day 2 calibration checkpoint is in [CHECKPOINT_DAY2.md](CHECKPOINT_DAY2.md).

## Roadmap (not built)

- **Reading a photo of a handwritten production register** (the floor's own count), so a floor report needs no typing.
- **WhatsApp channel** for requests, replies and daily updates.
- **Factories posting their free capacity**, so exporters see who can take work before asking.
- **Site-visit checklist, and a record of who vouches for a factory**, as structured fields. Today a simulated profile holds free text.
- **Exporter search across factory profiles.**
- **Automatic news check of a stated cause:** when a factory says a delay came from an outside event (for example a power cut), search Bangladeshi news for an outage in that area on those dates and offer the article as evidence. A person would still record the event and decide the cause. Today a person records news on the order by hand.

See [PLAN.md](PLAN.md) for the full plan.
