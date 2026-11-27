# Bunon: AI procurement analyst for subcontracted garment orders

> **Prototype, preliminary submission (20 Nov 2026)** for the International AI Builders Congress (Bangladesh), IndustrySphere: "Supply Chain Nexus".
> **The factory list is real (Open Supply Hub, pseudonymised). All performance data is synthetic.** See [DATA_PROVENANCE.md](DATA_PROVENANCE.md).

When a Bangladeshi garment exporter can't make a whole buyer order in-house, Bunon picks the set of subcontract factories most likely to deliver on time. It adds a priced buffer, and re-plans when a factory declines, counter-offers or is hit by a disruption in the news. A human approves every decision, and nothing is sent automatically.

**How the AI is used.** An optimiser (PuLP/CBC) and a Monte Carlo simulation produce every number. The LLM (Gemini) handles language: it reads the buyer's email, factory replies in English, Bangla or Banglish, and news headlines; it decides which tools to run for a what-if question; and it writes the decision memo, the messages to factories (English, Banglish, Bangla) and the warning after a daily update; it also suggests why a factory was late, which a person decides and the factory can dispute. It never writes a number itself: extracted values must come with a word-for-word quote that the code checks, and memos and answers cite tool results through placeholders. Anything that fails a check falls back to hand entry or a fixed template.

**Demo results** (synthetic performance data, seed 42; recomputed 28 Nov 2026, SPEC.md D30). The demo order is a fictional buyer's kids' T-shirt programme of **10 styles, 16,800 pieces, all subcontracted**, modelled on 10 anonymised real delivery orders (`data/demo/docs/`), due 3 → 31 January 2027.
- **A style always goes whole to one factory**: the interviewee confirmed a style is never split, because a smaller quantity is loss-making for the subcontractor.
- **Success:** every style has at least 98% of its quantity as good pieces after QC by the deadline.
- **Target:** 90%.

| | P(every style on time, within tolerance) | Cost |
|---|---|---|
| Naive: declared capacity + lowest price | 1.2% | 2,105,932 BDT |
| Naive + the same 20% over-cut | 13.4% | 2,519,298 BDT |
| **Bunon** (3 factories, 20% over-cut per style) | **32.0%**: far below the 90% target, and the app says so | 2,653,954 BDT |
| Naive rule, over-cut needed to match Bunon | 57.5% | +659,766 BDT |
| After the largest factory declines on day 3 (re-plan) | 33.1% | |

**Why so low.** With whole styles, a factory that delivers short leaves its last style short, and one style's over-cut cannot cover another style. The same plan read as one undivided product would show 88%. `SENSITIVITY.md` varies one input at a time; no default is tuned.
- **Largest driver:** short deliveries in the synthetic history, where about 30% of jobs deliver short. With none, the chance would be 73.8%.
- **Next:** the over-cut and the per-style tolerance.
- **Small effect:** defects and whole-lot rejection move it by a point or two.
- **Research asks 20–23** ask for the real values.

The pre-D30 piece-split demo (50,000 pieces over 5 factories, 87.5%) is kept as a regression test of the piece split (`tests/test_loop.py::test_piece_split_demo_numbers`).

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
| Tests: 226 (`pytest -q`) + 30 app click-throughs: 24 extreme settings, the factory app, the story-clock jumps (`pytest -m sweep`) | passing |
| Real values for the placeholder assumptions (fill/lateness, costs; see RESEARCH_ASKS.md) | waiting for research |

**The 3-minute demo** (branch `v2`; numbers in [VIDEO_NOTES.md](VIDEO_NOTES.md)):
1. **Continue as demo exporter** → **Start a new order** → **Read the email**. The 10 attached delivery orders are read in code (no AI).
2. **Confirm order** → **Approve** the plan. Three factories get whole styles: F0288 S01, S02, S04, S10; F2427 S05–S08; F2786 S03, S09.
3. **Approve and send the requests** → **Next: day 2.** The simulated factories reply; F2786 counters on the rate.
4. **Approve** the split.
5. **Next: day 12.** F2427's Banglish update says "2,000 ready, 1 day late", which projects 6,000 of 8,280 pieces. The warning gives the chance and the last day to rescue: day 13.
6. **Choose: Move an unstarted style to another factory** (S07 to the standby F2829) → **Approve** → **Approve and send the rescue requests**.
7. **Next: day 26** → switch the role to **Exporter · Quality inspector** → **Record QC** → the **Track record** and the **Timeline**.

Other paths:
- **Wait: day 14.** F0288's late fabric adds a second shortfall. By then no outside factory can take a whole style, so only overtime and renegotiating the date are left.
- **Factory · Floor supervisor:** switch to this role to see a factory's phone.
- **Show the maths:** the plan comparison, steering and the decline / reply / news what-ifs.
- **Jump ahead:** for a short pitch, the buttons under the confirmed order (Day 2, Day 12, Day 14, Day 26) press these buttons for you with the default choices. Every approval is still recorded under your role.

Every step replays from the cache.

**Release check (18 Nov 2026):** a fresh clone of this repo on Windows, with a new virtual environment, no API key and no UTF-8 override, installs in about 1.5 minutes and passes all tests (5 skipped: they need the private raw data, which is not published). A scan of every published file, including the database and the cached LLM answers, against the private list of real factory names, IDs and addresses found no matches. Live app: about 6 s to the first screen and 6 s from "Confirm order" to plans and memo while awake; 30–40 s to wake after a quiet period.

Design decisions and their before/after numbers are logged in [SPEC.md](SPEC.md) (D1–D12). The Day 2 calibration checkpoint is in [CHECKPOINT_DAY2.md](CHECKPOINT_DAY2.md).

## Roadmap (not built)

- **Automatic news check of a stated cause:** when a factory says a delay came from an outside event (for example a power cut), search Bangladeshi news for an outage in that area on those dates and offer the article as evidence. A person would still record the event and decide the cause. Today a person records news on the order by hand.

See [PLAN.md](PLAN.md) for the full plan.
