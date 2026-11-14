# Bunon: AI procurement analyst for subcontracted garment orders

> **Work in progress** for the International AI Builders Congress (Bangladesh), IndustrySphere: "Supply Chain Nexus".
> **The factory list is real (Open Supply Hub, pseudonymised). All performance data is synthetic.** See [DATA_PROVENANCE.md](DATA_PROVENANCE.md).

When a Bangladeshi garment exporter can't make a whole buyer order in-house, Bunon picks the set of subcontract factories most likely to deliver on time. It adds a priced buffer, and re-plans when a factory declines. A human approves every decision, and nothing is sent automatically.

Deployed demo: _coming Day 2_.

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
| `reliability`, `optimizer`, `simulation` | Day 2 |
| `agent` (LLM intake + memo) | Day 3 |
| Streamlit UI | Day 2 (thin) → Day 4 (full) |

See [PLAN.md](PLAN.md) for the full plan.
