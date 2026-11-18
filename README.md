# Bunon: AI procurement analyst for subcontracted garment orders

> **Prototype, preliminary submission (20 Nov 2026)** for the International AI Builders Congress (Bangladesh), IndustrySphere: "Supply Chain Nexus".
> **The factory list is real (Open Supply Hub, pseudonymised). All performance data is synthetic.** See [DATA_PROVENANCE.md](DATA_PROVENANCE.md).

When a Bangladeshi garment exporter can't make a whole buyer order in-house, Bunon picks the set of subcontract factories most likely to deliver on time. It adds a priced buffer, and re-plans when a factory declines, counter-offers or is hit by a disruption in the news. A human approves every decision, and nothing is sent automatically.

**How the AI is used.** An optimiser (PuLP/CBC) and a Monte Carlo simulation produce every number. The LLM (Gemini) handles language: it reads the buyer's email, factory replies in English, Bangla or Banglish, and news headlines; it decides which tools to run for a what-if question; and it writes the decision memo. It never writes a number itself: extracted values must come with a word-for-word quote that the code checks, and memos and answers cite tool results through placeholders. Anything that fails a check falls back to hand entry or a fixed template.

**Demo results** (synthetic performance data, seed 42; order of 50,000 subcontracted pieces, target 90%):

| | P(on-time full delivery) | Cost |
|---|---|---|
| Naive: declared capacity + lowest price | 4.6% | |
| Naive + the same 20% buffer | 89.5% | |
| **Bunon** (5 factories, 20% buffer) | **91.4%** | 12,417,585 BDT |
| Naive rule, buffer needed to match Bunon | 22.5% buffer | +192,021 BDT |
| After the largest factory declines on day 3 (re-plan) | 91.1% | +27,548 BDT |

Factory funnel (real Open Supply Hub data): 3,214 → 3,196 open → 1,748 make T-shirts → 442 RSC or valid BSCI → 231 with spare capacity and a feasible deadline (synthetic).

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
| Factory replies (English / Bangla / Banglish): accept / reject / counter, quantity and delay feed the re-plan | done |
| News headlines (English / Bangla): area, type, duration, severity → fixed table → simulated impact and re-plan | done |
| What-if analyst: Gemini function calling over simulate / solve / compare (max 6 calls, every call logged) | done |
| Live LLM with caps (40 calls per visitor, 400 per day); clear messages when no plan or re-plan is feasible | done |
| Tests: 121 (`pytest -q`) + 20-setting app click-through (`pytest -m sweep`) | passing |
| Real values for the placeholder assumptions (fill/lateness, costs; see RESEARCH_ASKS.md) | waiting for research |

**The 3-minute demo:** click **Read the email** → **Confirm order** → read the plans and the memo → **Approve** → **Simulate: F2184 declines on day 3** → **Approve** the re-plan. Then, in section 7, open **Factory reply**, click the **F2184 · Banglish** example → **Read the reply** → **Re-plan with this reply**; open **News**, click the **gazipur flood bn · Bangla** example → **Read the news** → **Assess the impact**. In section 8, click an example question → **Ask** and open the logged tool calls. Finish with the **Decision log**. Every example replays from the cache; your own text goes to Gemini live, within the caps.

**Release check (18 Nov 2026):** a fresh clone of this repo on Windows, with a new virtual environment, no API key and no UTF-8 override, installs in about 1.5 minutes and passes all tests (5 skipped: they need the private raw data, which is not published). A scan of every published file, including the database and the cached LLM answers, against the private list of real factory names, IDs and addresses found no matches. Live app: about 6 s to the first screen and 6 s from "Confirm order" to plans and memo while awake; 30–40 s to wake after a quiet period.

Design decisions and their before/after numbers are logged in [SPEC.md](SPEC.md) (D1–D9). The Day 2 calibration checkpoint is in [CHECKPOINT_DAY2.md](CHECKPOINT_DAY2.md).

See [PLAN.md](PLAN.md) for the full plan.
