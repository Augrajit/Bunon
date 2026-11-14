# Bunon: AI procurement analyst for subcontracted garment orders

> **Work in progress** for the International AI Builders Congress (Bangladesh), IndustrySphere: "Supply Chain Nexus".
> **The factory list is real (Open Supply Hub, pseudonymised). All performance data is synthetic.** See [DATA_PROVENANCE.md](DATA_PROVENANCE.md).

When a Bangladeshi garment exporter can't make a whole buyer order in-house, Bunon picks the set of subcontract factories most likely to deliver on time. It adds a priced buffer, and re-plans when a factory declines. A human approves every decision, and nothing is sent automatically.

**Live demo: https://buunon.streamlit.app/** (Streamlit Community Cloud. The first load after a quiet period can take a minute while the app wakes up.)

**Hosting note:** after a push that changes files in `bunon/`, open the live app and run the full story once. If any step shows an error, use Manage app → ⋮ → Reboot app (Streamlit Cloud can keep old modules loaded), then check again. The sidebar shows the server's Python version.

## Run locally

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows; use `source .venv/bin/activate` elsewhere
pip install -r requirements-dev.txt
pytest -q
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

**The 3-minute demo:** click **Read the email** → **Confirm order** → read the plans and the memo → **Approve** → **Simulate: F2184 declines on day 3** → **Approve** the re-plan → open the **Decision log**. Optional: under "Ask for a change", click an example instruction → **Interpret** → **Apply these changes**. On the public app, LLM answers are replayed from the cache, so it needs no API key.

Design decisions and their before/after numbers are logged in [SPEC.md](SPEC.md). The latest results are in [CHECKPOINT_DAY2.md](CHECKPOINT_DAY2.md).

See [PLAN.md](PLAN.md) for the full plan.
