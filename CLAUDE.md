# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

Bunon: a Streamlit prototype that picks the subcontract garment factories most likely to deliver a buyer order on time, prices the buffer, and re-plans after a decline, counter-offer or news disruption. Hackathon submission (International AI Builders Congress, Bangladesh). Live at https://buunon.streamlit.app/ (Streamlit Community Cloud, deploys from `main`).

## Commands

Windows, Python 3.11, venv in `.venv`.

```bash
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
.venv/Scripts/python.exe -m pytest -q                      # unit tests (~40 s); the sweep is excluded by pytest.ini
.venv/Scripts/python.exe -m pytest tests/test_engine.py::test_mip_respects_all_constraints -q   # one test
.venv/Scripts/python.exe -m pytest -m sweep -q             # app click-through at 24 extreme settings (~5 min, offline)
.venv/Scripts/python.exe -m streamlit run app.py           # local app (or the "bunon" config in .claude/launch.json, port 8501)
.venv/Scripts/python.exe -m bunon.data_gen                 # rebuild data/processed/bunon.db (needs local-only data/raw/factories.csv)
.venv/Scripts/python.exe -m bunon.warm_cache               # refill cached LLM answers for the scripted demo (needs GEMINI_API_KEY)
.venv/Scripts/python.exe -m bunon.personas                 # check the hand-written persona fixture (data/demo/personas.json)
.venv/Scripts/python.exe -m bunon.checkpoint               # regenerate CHECKPOINT_DAY2.md
.venv/Scripts/python.exe -m bunon.calibrate --target 0.90  # calibrate lateness to the base rate (writes data/calibration.json)
.venv/Scripts/python.exe -m bunon.sensitivity              # regenerate SENSITIVITY.md (one input at a time; anchors 0.80/0.90/0.95)
```

No linter or formatter is configured. Tests that need the private raw CSV or name mapping skip on a clean clone (5 skips is normal).

## Direction and success rule

PLAN_V2.md (approved 26 Nov 2026) is the current plan: Bunon becomes the trust layer for subcontracting, organised around one order's timeline (sourcing → follow-up → warning → rescue → record), on Streamlit with all order logic in a `bunon/service.py` free of Streamlit imports. Its decisions block at the top overrides the rest of it.

Success (SPEC.md D31, interview): **in the whole-style programme, success = every factory ships its styles by the buyer's date** (lateness cancels the programme); short pieces and QC rejects are a **cost** (lost FOB less salvage), not a failure, and tolerance is a research ask, not a gate. The synthetic lateness is **calibrated** to the interview's base rate (`python -m bunon.calibrate --target 0.90` writes `data/calibration.json`, applied in `load_world`; it moves only the least-evidenced inputs, never Bunon's logic). The piece split keeps the D12 rule (good pieces after QC ≥ (1 − tolerance) × Q). Since D30 the demo is a programme of 10 whole styles (16,800 pcs, from the anonymised delivery orders in `data/demo/docs/`): a style always goes whole to one factory, the buffer is an over-cut within each style, and **every style** must meet its own threshold. The piece split stays for orders without styles (regression tests on `op.piece_demo_order()`). QC defects and whole-lot rejection come from the reliability model (`eb-3`). Factory personas in the demo are hand-written by the team and labelled simulated; never generate them with the LLM.

## The rule everything is built around

**The LLM never produces a number.** Language goes to Gemini; every number comes from the engine. This is enforced in code, not prompts:

- Extraction (`agent.py`: order intake, factory replies, news, steering): every value must come with a verbatim quote from the source text, and `textnum.py` checks the quote literally states that value (handles lakh grouping, `150k`, crore, Bangla digits, Banglish, day-first dates). A failed value becomes `None` and the human fills it in.
- Generation (decision memo in `agent.py`, what-if answers in `analyst.py`): the LLM may write no digits or number words, only `{{key}}` placeholders resolved from a `FactSheet` of tool results, and factories only as `{{fac:F0123}}`. `agent.check_text` rejects violations → one retry → deterministic template.
- News severity is only a label; its effect comes from the fixed `SEVERITY_TABLE` in `config.py`.

When adding any LLM feature, keep this split and route numbers through facts/placeholders.

## Architecture

Engine (no LLM): `data_gen` (clean Open Supply Hub export → SQLite + seeded synthetic history) → `reliability` (empirical-Bayes per-factory forecast) → `optimizer` (PuLP/CBC MIP, naive baselines, buffer search, re-plans with locks/exclusions/shock events) → `simulation` (Monte Carlo P(on-time full delivery), seeded). `schemas.py` holds the Pydantic contracts between modules; `config.py` holds every constant and assumption.

LLM layer:
- `llm.py` is the only file that imports the provider SDK (`google-genai`, model `gemini-3.5-flash-lite`, JSON schema output, manual function calling). Every answer is cached in `data/llm_cache/<sha256>.json`; the key covers provider, model, task, prompt version and exact prompt text.
- Modes via `BUNON_LLM_MODE`: `offline` (default) reads only the cache and raises `CacheMiss` on a miss; `live` calls Gemini on a miss. Live calls are capped (`BudgetExceeded`, a `CacheMiss` subclass): 40 per visitor session, 400 per day, counter in `data/runtime/llm_budget.json`. Callers treat any miss as "fall back to template/hand entry", never as an error.
- `loop.py`: the order loop's engine steps (sourcing list and template drafts, split from offers, forecast from a daily update, last rescue day, rescue options); no LLM. `agent.parse_update` (`update_v1`) reads daily updates under the same quote rule; `agent.write_message` (`message_v1`) and `agent.write_warning` (`warning_v1`) write the messages to factories and the warning under the placeholder rule, with the `loop` templates as fallback. `agent.suggest_attribution` (`attribution_v2`) suggests a delay's cause; `attribution.py` holds the code rules (`outside_event` only with a matching recorded news event; a stated utility outage is `unclear` without evidence), an exporter role decides and the factory can dispute. A delay counts against on-time whatever its cause; the cause is context only. Persona messages marked `placeholder` are the developer's stand-ins until the team's text arrives.
- `analyst.py`: what-if tool loop over `run_simulation`, `solve_plan`, `compare_plans` (max 6 calls, each logged and shown in the UI).
- Consequence: changing a prompt string, prompt version (`*_VERSION` in `agent.py` / `analyst.py`), a demo input, **or anything that changes the engine's numbers** (they appear in memo and analyst prompts) invalidates cached answers, so the offline demo falls back to templates until they are re-generated live. Re-warm with `bunon.warm_cache`, then walk the app once in live mode; the sweep fails if intake is not cached.

UI: `app.py` renders only; order logic and engine calls go through `bunon/service.py` (no Streamlit import, tested) and state lives in `bunon/store.py` (SQLite per visitor session in `data/runtime/sessions/`, every PLAN_V2 table, an append-only `events` timeline on a story clock; `BUNON_STORE_DIR` and `BUNON_DECISIONS_DB` redirect them in tests). Demo login gate → welcome page → a demo role switcher (exporter or factory roles; only exporter roles confirm or approve) and three views: **Order timeline** (the demo: intake, plan + memo + approval, day-3 follow-up, "Ask Bunon", timeline), **Show the maths** (funnel, forecasts, plan comparison, steering) and **Factory app** (phone frame). LLM steps are memoised per session with `llm_once`, not `st.cache_data`, so one visitor's failure is not shared. Look and feel: base theme in `.streamlit/config.toml`, everything else in the single `THEME_CSS` block in `app.py`.

Branches: work happens on `v2`. Standing approval (27 Nov 2026): after each completed task, if `pytest -q` and the full `pytest -m sweep` pass, tag the current `main` as `pre-<task>`, fast-forward `main` to `v2` and push both; if any check fails, do not merge, report instead. `v0.1-prelim` tags the preliminary-round version.

Hosting quirk: Streamlit Cloud reruns `app.py` but keeps imported modules, so `app.py` hashes `bunon/*.py` each run and reloads the package when it changed. Module-level caches built from old classes are dropped there too; keep that in mind when adding caches.

## Data and privacy rules (public repo)

- Real order documents live only in the ignored `data/Documents/`. Never commit them or send them (raw or as images) to the LLM; `python -m bunon.anonymise <folder>` (local only) writes the anonymised `data/demo/docs/`, with its mapping in `data/private/`.

- The factory list is real (Open Supply Hub) but published only as pseudonymous IDs (`F0001`–`F3196`), coordinates rounded to 2 decimals, workers rounded to 50. Never commit or send to the LLM factory names, `os_id`, addresses, the raw CSV (`data/raw/`) or the mapping (`data/private/`). Tests scan prompts and every DB cell for this.
- All capacity, price and delivery-history data is synthetic (seed 42) and must stay labelled "synthetic" in the UI.
- Values marked `ASSUMPTION` / `PLACEHOLDER` in `config.py` wait for the team's research (`RESEARCH_ASKS.md`). Do not tune them to improve results.
- Nothing is ever sent to a factory automatically; every plan needs a human approval.
- Third-party assets need a recorded source, author and licence in `DATA_PROVENANCE.md`.

## Project documents

- `PLAN.md`: scope, model design and the rules above (section numbers are cited in module docstrings).
- `SPEC.md`: dated decision log (D1, D2, …). Record each design change there with its reason and before/after numbers.
- `PROMPTS.md`: the product prompts and AI-tooling disclosure; update it when a prompt changes.
- `DATA_PROVENANCE.md`: every data source, cleaning rule and image licence.
- `README.md`: demo results table and the 3-minute demo path; keep its numbers in step with the engine.

Before a UI change is committed, run both `pytest -q` and `pytest -m sweep`. Commit before starting the next change; push only when asked.
