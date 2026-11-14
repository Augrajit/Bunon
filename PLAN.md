# PLAN — AI Procurement Analyst for Subcontracted Garment Orders

Status: **approved 14 Nov 2026 with 8 changes (v2 of this plan). Day 1 in progress.**
Written: 14 Nov 2026. Submission: Fri 20 Nov 2026 (deployed link + public GitHub repo).
Repo: https://github.com/Augrajit/Bunon

> **Hours per day are still not filled in.** This plan assumes **6 coding hours/day**.
> If the real number is **under 5 h/day, the cut order applies from Day 1**: English-only intake, independent risk estimate before shocks, minimal charts.
> Allocation and re-allocation are never cut.

### Changes in v2 (from review)
1. A third comparison plan: **naive baseline + the same buffer % as ours**. All three are shown (§5, §8).
2. Explicit planned slack: production must finish `planned_slack_days` before the deadline. **Checkpoint: once the thin path runs, verify that P(meet) ≥ 0.90 is reachable for the demo order, and report before building further** (§5, §9).
3. A first re-allocation (exclude one factory, re-solve) moves to Day 2. Day 4 only adds locked quantities (§9).
4. Reliability v2 (HistGB) is dropped entirely, and so is scikit-learn (§4).
5. Compliance rule: **"RSC or valid BSCI"**. Funnel recomputed: **442** (§1).
6. The public repo holds only the processed DB: pseudonymous IDs, district, coordinates rounded to 2 decimals. No names, no `os_id`, no raw CSV. The name mapping stays local (§2.1, §10).
7. Weekday labels fixed: 15 Nov 2026 is a Sunday (§9).
8. New first stretch item for Day 5: natural-language steering of `OptimizerParams` (§7, §9).

---

## 0. Things found while inspecting the folder

| Finding | Impact |
|---|---|
| The brief says `data/raw/factories.csv`. That file does not exist. The only file is `./facilities.csv` (9.4 MB). | Moved to `data/raw/factories.csv` on Day 1 (approved). `data/raw/` is gitignored. |
| The folder is not a git repo. | `git init`, remote `https://github.com/Augrajit/Bunon` (exists, empty). |
| `python` on PATH is **Espressif's bundled 3.11.15** (`C:\Espressif\tools\python`), with no pandas. | Install a separate Python 3.11 / venv on Day 1 (approved). Don't install packages into the ESP-IDF interpreter. |
| Printing the CSV failed with a `cp1252` UnicodeEncodeError (the file has Bangla text). | Always read and write files with `encoding="utf-8"`. Set `PYTHONUTF8=1` in the venv. |
| The CSV has personal contact fields: `claim_point_of_contact`, `claim_point_of_contact_email` (26 rows each), plus phone numbers. | Drop these at load time. Never commit them, never show them, never send them to an LLM. |

---

## 1. Data report: `facilities.csv` (Open Supply Hub export)

Profiled with Python `csv` (stdlib). Every row has exactly 200 fields; no malformed rows.

**Shape:** 3,214 rows × 200 columns. One row per facility, keyed by `os_id` (all unique).
`country_code` is `BD` for every row. `contribution_date` ranges from 2019-03-23 to 2026-10-31.

**Column fill:** 96 columns are completely empty. 69 are under 5% filled, 19 are 5–50% filled, and 16 are over 50% filled.

### 1.1 Columns we will use

| Column | Filled | What is actually in it | How we use it / fill the gap |
|---|---|---|---|
| `os_id` | 100% | `BD` + 13 alphanumerics | Join key. Mapped to a pseudonym `factory_id` (`F0001`…). Never sent to the LLM. |
| `name` | 100% | 3,170 unique. 75 duplicate-name groups (150 rows) | Kept for the mapping table only. Duplicate names stay separate rows because they have different `os_id`s and are often different units. |
| `address` | 100% | free text | Gives `district` by keyword match (`dhaka` 1,398, `gazipur` 937, `narayanganj` 546, `savar` 384, `chattogram`+`chittagong` 397, `ashulia` 267, `mirpur` 201, `tongi` 149, `mymensingh` 105). Rows with no match get `district="unknown"`. One district per row, chosen by priority order (the specific area beats "dhaka"). |
| `lat`, `lng` | 100% | All rows fall inside the Bangladesh bounding box (lat 21.86–25.86, lng 88.20–92.30) | Haversine distance. **1,743 rows share an exact coordinate with another row.** The biggest cluster is 63 rows at one point in the Narayanganj area, so these are likely area centroids, not exact geocodes. We flag them `coord_shared=True` and accept roughly ±5 km error. That is small next to the road-distance factor. |
| `is_closed` | 100% | `False` 3,196 / `True` 18 | Drop the 18 closed rows. |
| `number_of_workers` | 98.3% (54 empty) | Pipe-joined, often conflicting values from many contributors, e.g. `1569\|1485\|1600`, and ranges like `1001-5000`. **1,833 rows have conflicting values.** | Parse each part (a range becomes its midpoint) and take the median. Distribution after parsing: p10 = 85, p25 = 191, **median = 470**, p75 = 1,250, p90 = 2,500, max 16,834. Some values are 0. Treat 0 and empty as missing and impute the median for the district, with `workers_imputed=True`. |
| `product_type` | 93.7% | free text, pipe-joined | Used, together with `processing_type`, `processing_type_facility_type_raw` and `claim_product_types`, to set `tee_capable` (regex `t[\s-]?shirt\|tee\b`). **1,748 open rows match.** A wider regex that adds knit/polo/jersey gives 2,156; we use the narrow one. |
| `processing_type` | 100% | pipe-joined. Every row includes `sewing`; `knitting` 712, `printing` 392, `dyeing` 354 | Binary capability features for the reliability model. |
| `facility_type` | 99.9% | `final product assembly` 3,211, `warehousing / distribution` 2,769, `textile or material production` 911 | Weak signal. Use only `textile or material production` (in-house fabric), as a feature. |
| `parent_company` | 19.5% | group names | **Dropped** (group names can identify factories). Correlation by group is not modelled (§6). |
| `accord_inspections_and_remediation_program.rsc_presence` | 27.0% | always `Yes` (sometimes repeated) | **Compliance rule, part 1:** RSC-covered. |
| `amfori_compliance_status.bsci_audit.expiration_date` | 9.5% (306) | dates, sometimes several, pipe-joined | **Compliance rule, part 2:** valid BSCI = **any** listed expiration date ≥ `as_of` (2026-11-20). |
| `wrap_certification.certification_status` / `.expiration_date` | 13.2% | `active` / `inactive`, repeated | Shown in the UI only. Not part of the rule. |
| `slcp_assessment.most_recent_assessment_status` | 14.9% | mostly `Verification Finalized` | Shown in the UI only. It is not a pass/fail certification. |
| `claim_minimum_order_quantity`, `claim_average_lead_time` | 1.1%, 1.3% | free text (`1000`, `10K pcs`, `90-120 days`) | Too sparse to use. Used only to sanity-check the synthetic MOQ and lead-time ranges. |

**Rows with at least one of RSC / WRAP / BSCI / SLCP:** 1,013 (context only).
**Candidate funnel from real data, rule "RSC or valid BSCI" at as_of 2026-11-20:**
3,214 → 3,196 open → 1,748 T-shirt-capable → **442 compliant** (420 RSC, 136 valid BSCI, with some overlap). Checking BSCI validity at the deadline (2026-12-20) instead would give 439.
**442 is the number used everywhere** (UI, docs, pitch). After this point, spare capacity and deadline feasibility are synthetic.

### 1.2 Columns we drop

The 96 empty columns (heatwatch, trase_*, wri_aqueduct_*, ulula_*, mudem_*, etc.), the claim_energy_* columns, the CBA/wikirate/rise_wpp columns (<2% filled), and all personal-contact columns.

### 1.3 What the file does NOT contain (all synthetic, see §3)

Declared capacity in pieces, spare capacity, price, lead time, minimum batch, any delivery history, acceptance history, and transport cost. **The file has no performance data at all.**

---

## 2. Module contracts

The layout is a flat package, built strictly in this order. Every contract is a Pydantic v2 model in `schemas.py`.

```
bunon/
  config.py        # all assumptions & constants in one place (as_of date, seeds, rates)
  schemas.py       # every Pydantic contract below
  data_gen.py      # clean OSH + generate synthetic history  -> SQLite
  reliability.py   # forecasts per factory
  optimizer.py     # MIP + baseline + buffer search + re-plan
  simulation.py    # Monte Carlo bundle risk
  llm.py           # provider interface + disk cache (only file that imports an SDK)
  agent.py         # intake, memo, pseudonymiser, number validator
app.py             # Streamlit UI
tests/test_<module>.py
```

### 2.1 `data_gen`
- **In:** `raw_csv_path: Path`, `seed: int`, `as_of: date`, `SynthConfig`.
- **Out:** SQLite `data/processed/bunon.db` with three tables, plus a `DataGenReport` (counts per funnel stage, rows dropped and why).
  Also **`data/private/mapping.csv`** (`factory_id, os_id, name`), which is **gitignored and never published**.
  **The public DB contains no name, no `os_id` and no address. Coordinates are rounded to 2 decimals (about 1.1 km).** A test asserts this.
  - `factories` (real-derived, public): `factory_id: str`, `district: str`, `lat: float` (2 dp), `lng: float` (2 dp), `coord_shared: bool`, `workers: int`, `workers_imputed: bool`, `tee_capable: bool`, `has_knitting: bool`, `has_printing: bool`, `has_dyeing: bool`, `has_textile_prod: bool`, `rsc: bool`, `bsci_valid: bool`, `bsci_expiry: date | None`, `compliant: bool` (= rsc ∨ bsci_valid). `parent_company` is dropped because group names can identify factories.
  - `factory_synth` (synthetic, `is_synthetic=1`): `factory_id`, `declared_daily_capacity: int`, `declared_spare_daily: int`, `unit_price_bdt: float`, `min_batch: int`, `setup_days: int`.
  - `history` (synthetic): `job_id: str`, `factory_id`, `offered_date: date`, `accepted: bool`, `committed_qty: int | None`, `declared_capacity_at_time: int`, `promised_date: date | None`, `delivered_qty: int | None`, `delay_days: int | None`, `unit_price_bdt: float | None`, `shock_tag: str | None`.
- **Depends on:** pandas, numpy, sqlite3.

### 2.2 `reliability`
- **In:** the `history` and `factories` tables, `as_of: date`.
- **Out:** `list[ReliabilityForecast]`:
  `factory_id: str`, `n_jobs: int`, `thin_history: bool`,
  `fill_mu: float`, `fill_sd_param: float`, `fill_sd_noise: float` (logit-scale posterior and predictive),
  `fill_p10: float`, `fill_p50: float`, `fill_p90: float`,
  `p_late: float`, `late_days_shape: float`, `late_days_scale: float`,
  `p_catastrophe: float`, `accept_prob: float`, `model_version: str`.
- Also returns `ReliabilityEval`: holdout MAE, 80%-interval coverage by history bucket, and Brier score for `p_late`, each against the naive "declared = delivered" assumption.
- **Depends on:** data_gen output, numpy/pandas only.

### 2.3 `optimizer`
- **In:** `AllocationRequest`:
  `order: OrderConstraints`, `candidates: list[Candidate]`, `params: OptimizerParams`.
  - `Candidate`: `factory_id`, `unit_price_bdt`, `transport_bdt_per_piece`, `transit_days: int`, `slack_days: int`, `window_capacity_declared: int`, `window_capacity_risk_adj: int`, `min_batch: int`, `proven: bool`, `q_effective: float`.
  - `OptimizerParams`: `buffer_pct: float`, `planned_slack_days: int = 4`, `max_factories: int = 5`, `min_factories: int = 1`, `unproven_cap_pct: float = 0.15`, `lot_size: int = 100`, `missing_piece_cost_bdt: float`, `fixed_cost_per_factory_bdt: float`, `excluded_ids: list[str]`, `locked_min_qty: dict[str, int]`.
- **Out:** `AllocationPlan`:
  `plan_id: str`, `method: Literal["optimised","baseline","baseline_buffered"]`, `status: Literal["optimal","infeasible","not_solved"]`,
  `lines: list[AllocationLine(factory_id, qty, unit_price_bdt, transport_bdt, risk_penalty_bdt, share)]`,
  `target_qty: int`, `total_allocated: int`, `buffer_qty: int`,
  `cost_production`, `cost_transport`, `cost_fixed`, `cost_risk_penalty`, `cost_total: float`,
  `binding_constraints: list[str]`, `max_feasible_qty: int | None`, `solver_seconds: float`.
- Also returns `BufferFrontier` (a list of buffer_pct → plan, P(meet), cost) and `ReplanResult` (old plan, new plan, line-by-line diff).
- **Depends on:** reliability forecasts (for `q_effective` and risk-adjusted capacity), simulation (for the buffer search only), PuLP + CBC.

### 2.4 `simulation`
- **In:** `AllocationPlan`, `dict[factory_id, ReliabilityForecast]`, `dict[factory_id, slack_days]`, `ShockParams`, `n_sims: int = 20_000`, `seed: int`.
- **Out:** `BundleRisk`: `plan_id`, `n_sims`, `seed`, `p_meet_qty: float`, `p_meet_se: float`, `expected_delivered: float`, `delivered_p5: int`, `delivered_p50: int`, `shortfall_p95: int`, `histogram: list[tuple[int, int]]`, `fail_contribution: dict[str, float]` (share of failing draws in which each factory was short), `correlation_mode: Literal["independent","shocks"]`.
- **Depends on:** numpy only.

### 2.5 `agent` (+ `llm`)
- `llm.LLMClient.complete_json(task: str, prompt: str, schema: type[BaseModel], cache_key_extra: dict) -> tuple[BaseModel, CallMeta]`.
  `CallMeta`: `source: Literal["cache","live","template"]`, `provider`, `model`, `prompt_version`, `cache_key`.
- `agent.parse_order(email_text: str, own_capacity_qty: int) -> IntakeResult(order: OrderConstraints, issues: list[str], meta: CallMeta)`.
- `agent.write_memo(facts: FactSheet, kind: Literal["initial","replan"]) -> DecisionMemo(summary, recommendation, rejected_alternatives: list[str], risks: list[str], human_actions: list[str], rendered_md: str, meta: CallMeta)`.
- `OrderConstraints`: `product: Literal["basic_tshirt"]`, `total_qty: int`, `own_capacity_qty: int` (entered by the user, not taken from the email), `subcontract_qty: int` (computed in code as total − own), `deadline: date`, `delivery_point: str | None`, `fabric: str | None`, `gsm: int | None`, `quality_aql: float | None`, `compliance_required: list[str]`, `fob_usd_per_piece: float | None`, `late_penalty_pct_per_week: float | None`, `penalty_text: str | None`, `source_spans: dict[str, str]`, `missing_fields: list[str]`.
- `FactSheet`: `dict[str, int | float | str]` with flat keys such as `opt.p_meet`, `opt.cost_total`, `base.p_meet`, `opt.line.F0123.qty`.
- **Depends on:** all modules above (read-only), plus the provider SDK, only inside `llm.py`.

### 2.6 `ui` (`app.py`)
- **In:** everything above. **Out:** the screens, plus a `decisions` table in SQLite (`ts`, `step`, `plan_id`, `approved_by: str`, `note`).
- It never sends anything. Draft purchase-order text is offered as a download only.

---

## 3. Synthetic data design

Everything in this section is generated with a fixed `seed` recorded in DATA_PROVENANCE.md and written to the DB with an `is_synthetic` flag. Every UI screen that shows these values carries a "synthetic" badge. Synthetic history is generated **only for the 1,748 open T-shirt-capable factories.**

**Per-factory hidden traits** (never stored; only their effects are):

| Variable | Distribution | Why |
|---|---|---|
| `true_daily_capacity` | `workers × sewing_share × pcs_per_operator_day`, with `sewing_share ~ U(0.55, 0.70)`. `pcs_per_operator_day` is a **config assumption for basic tees; value unknown, teammates to research** | Ties capacity to a real column |
| over-declaration `d` | `exp(N(0.10, 0.12))`, so declared capacity is about 10% above true at the median and some factories under-declare | The core reason for "declared vs delivered" |
| `declared_daily_capacity` | `true × d` | |
| `declared_spare_daily` | 35% of factories: 0 (fully booked). The rest: `min(Beta(2, 200) × declared_daily, 1,000)` pcs/day | Subcontract capacity is scarce. **Calibrated on Day 1** to the rule below: median feasible candidate = 13.8% of Q, 231 feasible, top 5 cover 1.67 Q, largest single factory 35% of Q. The implied spare share (about 1%) is the main thing teammates' research should replace |
| base reliability `θ` (logit fill) | `N(2.2, 0.9) + 0.3·compliant + 0.15·z(log workers)` | Mean fill about 0.88. The weak links to real columns give the model something to learn. **We planted these links, and the plan says so openly (§11)** |
| `p_late` | logistic of `−1.0 − 0.6·z(θ) + N(0, 0.5)` | Late and short deliveries are correlated, but not perfectly |
| `late_scale` | `U(2, 6)` days | |
| `accept_prob` | `Beta(6, 2)` | Used for declines |
| `unit_price_bdt` | lognormal around `base_cm_price_bdt` (**unknown, teammates to research**), sd 12%, correlation with θ about **+0.2** (v2 fix: v1 had the sign wrong) | Cheap factories are slightly less reliable on average. That is what makes the baseline lose, but noise keeps it from being deterministic |
| `min_batch` | choose from {500, 1,000, 2,000, 3,000}, weighted by size | matches the sparse `claim_minimum_order_quantity` values (1,000 / 3,000 / 5,000) |
| `setup_days` | `U{3..7}` | |

**History length** (some factories get almost none):
`n_offers ~` mixture of 20% `Poisson(1)` (thin), 30% `Poisson(6)`, 50% `Poisson(18)`, spread over 2023-11-01 to 2026-10-31.

**Per job:**
1. `accepted ~ Bernoulli(accept_prob)`. Declined jobs have no delivery fields.
2. `committed_qty = utilisation × declared_capacity × job_days`, with `utilisation ~ U(0.3, 1.1)`.
3. `fill = sigmoid(θ + N(0, 0.35))`, then **an overstretch effect:** if `committed / (true capacity × job_days) > 0.9`, fill × `U(0.75, 0.95)`. This is how "declared vs delivered" becomes realistic: factories that over-declare and are pushed hard under-deliver, and the model can learn it from `utilisation`.
4. `late ~ Bernoulli(p_late)`; `delay_days ~ Gamma(1.5, late_scale)` if late, else 0.
5. **Seasonality:** Eid months get fill × 0.9 (approximate months, labelled approximate). Monsoon (Jun–Sep) adds +2 days of delay on average.
6. **Correlated shocks:** each (district, month) has a 3% chance of a shock (unrest, power, flooding). All jobs in that district that month get fill × `U(0.5, 0.8)` and delay +`U{5..15}` days. Recorded in `shock_tag` and **not** used as a model feature, because shocks are unpredictable.
7. **Outliers:** 2% catastrophic jobs (fill `U(0, 0.4)`, e.g. a fire or strike). 1% data-entry errors (delivered_qty × 10, or a negative delay) that the cleaner must catch. That rule has its own test.

**"No hero" guarantees (as tests):** Spearman(price, θ) must be in [+0.05, +0.35] (cheaper means slightly less reliable). The number of factories in the top 5% for both low price and high reliability must be **no more than chance would give** (independence: 1,748 × 0.05 × 0.05 ≈ 4.4), so none were planted. A handful of naturally good-and-cheap factories is realistic, not a hero. In the demo order, no single factory's risk-adjusted window capacity may exceed 40% of Q, so no one factory can carry the order alone.

**Calibration rule set before the first run** (written down so we don't tune for drama): choose the spare-share distribution so that the median feasible candidate can make **8–15% of the demo subcontract quantity** in the window. That forces a 3–5 factory solution. `seed=42` is fixed before the first run, and the demo shows whatever it produces.

---

## 4. Reliability model

**Targets** (job level, accepted jobs only): `fill = delivered_qty / committed_qty` (clipped to [0.01, 1.05], on the logit scale), and `delay_days`.

**v1, Day 2: empirical Bayes, no ML.**
- Population prior for logit fill: `N(μ, τ²)`, with μ and τ fitted by method of moments across factories.
- Factory posterior: `post_var = 1/(1/τ² + n/σ²)`, `post_mean = post_var·(μ/τ² + Σ logit(fill)/σ²)`. Predictive for a new job: `N(post_mean, post_var + σ²)`.
- **Thin history follows from the maths:** with n = 0 the forecast is the prior (wide). With n = 20 it is tight. `thin_history = n_jobs < 3`, and those factories are "unproven" for the 10–20% cap.
- `p_late`, `p_catastrophe` and `accept_prob`: Beta-Binomial with a population prior. Late-days scale: pooled Gamma.

**There is no v2. The HistGB prior was dropped in review, and scikit-learn is not a dependency.** The model is the empirical-Bayes forecast above and nothing else. In the pitch it is described as "a statistical reliability model with honest uncertainty", not as machine learning.

**Evaluation:** time-split, holding out the last 6 months (2026-05 to 2026-10). Report MAE of the fill forecast, **coverage of the p10–p90 interval** (target about 80%) by history bucket (0–2, 3–10, 10+ jobs), and Brier score for `p_late`. Compare the model with the naive belief (fill = 1, on time), which is exactly what the baseline assumes.

---

## 5. Optimiser formulation (PuLP + CBC)

**Pre-filter** (hard rules applied before the MIP and repeated as constraints):
`tee_capable ∧ ¬closed ∧ (rsc ∨ bsci_valid) ∧ window_days_i > 0 ∧ window_capacity_declared_i ≥ min_batch_i`, minus `excluded_ids`.

**Derived inputs per candidate i:**
- `road_km_i = haversine(i, delivery_point) × 1.35` (circuity factor, an assumption).
- `transit_days_i = ceil(road_km_i / 200) + 1`.
- `transport_i = truck_cost_bdt_per_km × road_km_i / pcs_per_truck` (both unknown, research needed).
- **Explicit planned slack (v2):** production is planned to finish `S = planned_slack_days` (default 4) before the latest possible finish date.
  `latest_finish_i = deadline − qc_days − transit_days_i` (`qc_days = 3` at the exporter).
  `planned_finish_i = latest_finish_i − S`.
- `window_days_i = planned_finish_i − as_of − setup_days_i` (production days available).
- `slack_i = S` by construction: the number of days a factory can run late and still make the deadline. The simulation uses this.
- `C_i = declared_spare_daily_i × window_days_i` (declared).
- `C̃_i = C_i × fill_mean_i` (**risk-adjusted capacity**). This uses the mean, not p10, so tail risk is handled once, by buffer + Monte Carlo, not three times.
- `q_i = fill_mean_i × P(delay_i ≤ slack_i)`.
- **The trade-off S controls:** a larger S means fewer production days (less capacity) but more tolerance for delay. S is a slider in the UI. At the Day 2 checkpoint, report P(meet) for S ∈ {0, 2, 4, 6, 8}.
- `ρ_i = L × (1 − q_i)`, the risk penalty per allocated piece. `L = missing_piece_cost_bdt` comes from the parsed penalty: `L = fob_bdt × total_qty × penalty_pct_per_week × weeks_at_risk / Q`. The whole order fails if any piece is short, so each piece carries a share of the whole order's penalty. This is a heuristic, exposed as a slider.

**Variables:** `z_i ∈ ℤ≥0` (lots of `lot_size` = 100 pieces), `x_i = 100·z_i`; `y_i ∈ {0,1}` (factory used).

**Objective:**
`min Σ_i (p_i + transport_i + ρ_i)·x_i + F·Σ_i y_i`
(F = fixed onboarding/QC cost per factory. It discourages using five factories when three will do.)

**Constraints:**
1. Demand with buffer: `Σ x_i ≥ (1 + b)·Q`
2. Risk-adjusted capacity: `x_i ≤ C̃_i·y_i`
3. Minimum batch: `x_i ≥ min_batch_i·y_i`
4. Number of factories: `1 ≤ Σ y_i ≤ 5`
5. Unproven cap: `Σ_{i∈U} x_i ≤ u·(1 + b)·Q`, u ∈ [0.10, 0.20], default 0.15. U = `thin_history`.
6. Deadline: `y_i = 0` if `window_days_i ≤ 0` (pre-filter). The time limit is already inside `C̃_i`. **The deadline is never relaxed.**
7. Compliance: `y_i = 0` if not compliant (pre-filter, and asserted in tests). **Never relaxed.**
8. Re-plan locks: `x_i ≥ locked_min_qty_i` for factories that already confirmed.

**Buffer search (the "priced buffer"):** solve for b ∈ {0, 2.5, 5, 7.5, 10, 15, 20%}, simulate each plan, and recommend the **cheapest plan with P(meet) ≥ target** (default 0.90, user-set). Buffer price = `cost_total(b*) − cost_total(0)`. Shown as a cost-vs-probability curve. That is 7 solves, each under a second at about 300 candidates.

**Infeasibility:** report `max_feasible_qty` (re-solve maximising Σx with the same hard constraints) and tell the human the order cannot be covered. Only b, then u, then max_factories may be loosened, and only by the human through UI controls. Deadline and compliance are not offered.

**Three plans, all evaluated by the same Monte Carlo (v2):**
1. **Baseline** ("declared capacity + lowest price"): same pre-filter and same S, so it is equally compliant and deadline-feasible. Sort by `unit_price_bdt`, fill each factory up to its **declared** `C_i` (respecting min batch) until Q, with no buffer, at most 5 factories, and no reliability information.
2. **Baseline + buffer:** the same greedy rule, filled to `(1 + b*)·Q`, where b* is *our* recommended buffer. **This separates the two sources of gain:** how much comes from simply ordering extra, and how much from choosing reliable factories. If baseline + buffer gets close to ours, we say so.
3. **Ours (optimised):** the MIP with buffer search.

**Re-allocation, Day 2 version:** exclude one factory and re-solve (same `as_of`, no locks).
**Re-allocation, Day 4 version (adds locks):** the disruption sets `as_of += k days`. All windows shrink. The decliner is excluded. Other confirmed factories get `locked_min_qty` = their current qty, because cutting a confirmed PO is bad practice. New factories are allowed. Then the same buffer search runs. Output: old vs new lines, Δcost, ΔP(meet).

---

## 6. Risk simulation (Monte Carlo)

Fully vectorised numpy, `N = 20,000` draws, seeded. Per draw:
1. **National shock:** with probability `p_nat` (estimated from history as the share of months with shocks in ≥ 3 districts, else a config default), all factories get fill × `U(0.85, 0.95)` and delay +`U{2..5}`.
2. **District shocks:** each district d independently with `p_d`, **estimated from the synthetic history's shock frequency** rather than hand-set. Every allocated factory in d gets fill × `U(0.5, 0.8)` and delay +`U{5..15}`. **This is the correlated-failure model:** two factories in Ashulia fail together. The optimiser cannot see this (it is linear), so the UI shows the P(meet) with shocks on vs off. That makes the case for spreading across districts. An optional "max 60% per district" constraint exists as a toggle, off by default.
3. **Factory level:** draw the parameter `θ_i ~ N(post_mean, post_var)` (model uncertainty, which is why thin-history factories look riskier), then `fill_i = sigmoid(θ_i + N(0, σ²))`. Catastrophe with `p_catastrophe_i` gives fill `U(0, 0.4)`.
4. **Delay:** `late ~ Bernoulli(p_late_i)`, days `~ Gamma`.
5. `on_time_i = x_i·min(fill_i, 1)` if `delay_i ≤ slack_i` (where `slack_i = S`, the planned slack from §5), else **0**. That is conservative: late goods miss the vessel. Partial on-time lots are not modelled.
6. `success = Σ on_time_i ≥ Q`.

Output: `P = mean(success)`, `SE = √(P(1−P)/N)` (about 0.002 at P = 0.9), the delivered-quantity histogram, and each factory's contribution to failures.
**Not modelled (stated in the UI):** parent-company group correlation, partial late lots, and quality rejection (AQL).
**Tests:** one factory with fill = 1 and no lateness gives P = 1. Two independent factories match the analytic product within 3 SE. Same seed gives the same result.
**Fallback if cut:** the independent normal approximation of Σ on_time_i, with no shocks.

---

## 7. LLM use

Three calls in the scripted demo, all cached:

| # | Call | Input | Output schema |
|---|---|---|---|
| 1 | `parse_order` | buyer email text (English; Bangla is a stretch goal) | `OrderConstraints` minus the computed fields, plus `source_spans` |
| 2 | `write_memo(kind="initial")` | `FactSheet` (pseudonymous IDs only) for the optimised plan, the baseline, the buffer frontier and the risks | `DecisionMemo` with placeholders |
| 3 | `write_memo(kind="replan")` | `FactSheet` for the old plan, new plan, diff and disruption | `DecisionMemo` with placeholders |
| 4 | `steer` (**Day 5 stretch, first in line**) | a manager's instruction in plain language, e.g. "no more than 3 factories, and keep new factories under 10%", plus the current `OptimizerParams` | `ParamChange(changes: list[{field, new_value, source_span}], unsupported: list[str])` |

**Natural-language steering (call 4):**
- The LLM may set **only fields of `OptimizerParams`**: `buffer_pct`, `planned_slack_days`, `max_factories`, `min_factories`, `unproven_cap_pct`, `excluded_ids`, and the target P.
- Pydantic bounds reject out-of-range values. `deadline`, `subcontract_qty` and compliance are **not** in the schema, so the LLM cannot touch them. A request like "push the deadline" goes to `unsupported`, and the UI says "the deadline is fixed".
- **Same validator rules as intake:** each numeric `new_value` needs a `source_span` from the instruction that parses to that value (so "under 10%" → `unproven_cap_pct = 0.10`). Factory references must be existing pseudonymous IDs.
- The UI shows a before/after diff of the params, and the human clicks Apply. Then the normal solve and simulation run. The LLM never sees or produces plan numbers in this call.

**Enforcing "no invented numbers":**
- **Memo: the LLM cannot write digits at all.** It must reference numbers as `{{opt.p_meet}}` and factories as `{{fac:F0123}}`. The validator (a) strips placeholders and rejects any remaining `[0-9০-৯]`, (b) rejects any placeholder key not in the FactSheet, and (c) the renderer fills values from the FactSheet with fixed formatting (percentages, BDT with thousands separators). So every number shown in a memo comes from a tool result by construction. On failure, retry once with the validator's error. If it fails a second time, use a **deterministic template memo** labelled "template". The badge shows `LLM (cached)` / `LLM (live)` / `template`.
- **Intake:** every numeric field must come with a `source_span` that is an exact substring of the email and that parses to the same value. The parser handles `150,000`, Indian grouping `1,50,000`, `150k`, `1.5 lakh`, and Bangla digits. Dates must parse from their span. Anything that fails becomes `null`, goes into `missing_fields`, and the UI asks the human to fill it in. The human confirms the parsed form before anything runs. `subcontract_qty` is computed by code, never by the LLM.
- **Tests** (`tests/test_agent.py`, with a fake LLM client and no network): a memo containing `"91%"` is rejected; an unknown key is rejected; a valid memo renders and **every number in the rendered text is in the FactSheet values** (property check); an intake whose quantity is not in the email is nulled; the lakh and Bangla-digit cases parse.

**Pseudonymisation:** `agent.pseudonymise()` builds the FactSheet from `factory_id` only. A test runs the scripted demo and asserts that **no real `name` or `os_id` appears in any prompt string** (prompts are logged in the cache files). Mapping back happens only in the renderer.

**Caching:** `data/llm_cache/{sha256(provider|model|prompt_version|canonical_input_json)}.json` holds the prompt, raw response and parsed output. `LLM_MODE=offline` (the default on the deployed app) reads only from cache, and a cache miss becomes the template. `LLM_MODE=live` needs an API key in Streamlit secrets. The scripted demo works with no key.

**Provider:** **not chosen yet.** Per the brief, on Day 3 I will read the current docs of the free-tier candidates and pick a model based on structured-output/JSON-schema support, free-tier rate limits, and availability from Bangladesh. Until then, no model names. Settings: temperature 0, JSON/structured mode if supported, otherwise prompt plus Pydantic validation.

---

## 8. The demo story

**Every input here is fictional, and the email is labelled as such in the UI.**

- **Exporter ("you"):** a pseudonymous exporter whose consolidation point is in Gazipur (a district point, not a real factory row). Own capacity in the window: **100,000 pieces**, entered in the UI.
- **Buyer email (English):** a European retailer orders **150,000 basic crew-neck T-shirts**, 100% cotton single jersey, 160 GSM, AQL 2.5, delivered by **20 Dec 2026**. It requires RSC coverage or a valid BSCI audit, FOB USD 2.40/pc, and a penalty of 5% of FOB per week late, with cancellation after two weeks. The structure echoes the brief: capacity 1,000, order 1,500, subcontract 500, scaled ×100.
- **Subcontract quantity Q = 50,000** (computed). Demo as-of date fixed at **20 Nov 2026** in `config.py`, so the demo is reproducible after submission.
- **Screens and the numbers a judge sees:**
  1. Parsed order card with source quotes highlighted; the human clicks Confirm.
  2. Funnel: **3,214 → 3,196 open → 1,748 T-shirt-capable → 442 compliant (RSC or valid BSCI)** (real) → *n* with spare capacity → *n* deadline-feasible (synthetic; *n* is unknown until data_gen runs).
  3. Reliability table for the top 15 candidates: declared vs forecast capacity, a p10–p90 band, and a "thin history" tag.
  4. **Three plans side by side: baseline, baseline + same buffer, ours.** For each: factories, cost (BDT), and **P(on-time full delivery)** as the headline, plus the cost premium and the expected penalty avoided (penalty × ΔP). The middle column shows how much of the gain is "just order more" and how much is factory choice. **Target, not promise:** ours at or above 0.90. Real values are unknown until it runs and will be reported as they come out (Day 2 checkpoint).
  5. Buffer curve: cost vs P(meet) for b = 0…20%, with the recommended b* marked.
  6. **Disruption:** on day 3, **the factory with the largest allocation in our plan declines.** It is chosen by rule, not by hand. The re-plan shows the new lines, Δcost and the new P(meet), compared with "what the baseline would do".
  7. Decision memo (cached LLM), with Approve / Reject buttons logged to `decisions`. Draft PO text is a download only.
- **Live part for judges:** paste your own email (live LLM if a key is set) and move the sliders (target P, unproven cap, risk weight, shocks on/off). The optimiser and simulation always run live.
- **Robustness (stretch, Day 5):** 200 random synthetic orders, showing the distribution of P(ours) − P(baseline). This answers "did you cherry-pick the seed?".

---

## 9. Schedule, Sun 15 – Fri 20 Nov (assuming 6 h/day)

**Rule:** if your real hours are H/day, multiply. **Under 5 h/day, apply the cut order from Day 1** (English only → independent risk estimate → minimal charts). Allocation and re-allocation are never cut.

| Day | Tasks (hours) | End-of-day check |
|---|---|---|
| **Sun 15** (started early, Sat 14 evening) | venv + git + `.gitignore`, move CSV (0.75). `config.py` + `schemas.py` (1). `data_gen` cleaning: parse workers, districts, tee_capable, RSC/BSCI, drop PII, round coordinates, write private mapping (1.5). Synthetic generator (1.75). Tests: funnel = 442, no-hero, outlier catcher, **no names or os_id in the public DB** (0.75). DATA_PROVENANCE draft (0.25). | `bunon.db` built from seed 42; funnel counts match §1 |
| **Mon 16** | `reliability` empirical Bayes (1.5). `optimizer`: MIP + baseline + baseline + buffer, with explicit slack S (2). Independent-only `simulation` (0.5). **First re-allocation: exclude one factory, re-solve** (0.5). Thin `app.py`: order form (no LLM) → three plans → P(meet) (1). **Deploy to Streamlit Community Cloud** and confirm CBC runs there (0.5). **CHECKPOINT: run the demo order with a buffer sweep and S sweep, and report to you whether P(meet) ≥ 0.90 is reachable. No further building until you've seen it.** | **Thin end-to-end path live at a public URL + checkpoint report** |
| **Tue 17** | Read Gemini free-tier docs (current terms, model, structured output, limits), write `llm.py` + cache (1.5). `parse_order` + span/number validator + tests (1.75). `write_memo` + placeholder validator + template fallback + tests (2). Pseudonymiser + test. PROMPTS.md (0.75). | Intake and memo work offline from cache |
| **Wed 18** | Monte Carlo with district/national shocks + tests (1.5). Buffer search + frontier (1). **Re-plan adds locked quantities** + `as_of` shift (1). Full 7-step UI with session state and approval log (2.5). | Full scripted story runs start to finish on the deployed URL |
| **Thu 19** | **Buffer day.** Bug fixes from a teammate walkthrough (≤ 3). Then, in order and only if green: **(1) natural-language steering**, (2) chart polish, (3) Bangla intake, (4) robustness run. README + SPEC final (1). Code freeze 22:00. | Teammates can run the demo without me |
| **Fri 20** | Clean-clone redeploy test, cold-start timing, final tags, submit (2). | Submitted |

---

## 10. Risks and fallbacks

| Risk | Likelihood | Fallback |
|---|---|---|
| CBC binary fails on Streamlit Cloud (Linux) or on Windows | medium | Found on Day 2 by deploying early. Swap the backend to `scipy.optimize.milp` (HiGHS) behind the same function. |
| Free-tier LLM: rate limits, quota, model renamed, or blocked region | high | Cache + offline mode + template memo. The demo needs no live call. |
| Synthetic data gives a useless result (baseline equals ours, or a trivial one-factory plan) | medium | The calibration rule in §3 is set before the first run. If it still fails, change the *order* (Q, deadline), not the factories, and document it. |
| P(meet) ≥ 0.90 not reachable for the demo order | medium | Found at the Day 2 checkpoint. Report it to you first. The options are order size, deadline in the fictional email, or the target, never the factory data. |
| Public DB re-identifies factories (district + rounded coordinates + exact worker count can be matched against the public OSH site) | medium | Coordinates rounded to 2 dp (decided). Also round `workers` to the nearest 50 in the public DB. **Proposed, needs your OK.** |
| MIP infeasible in the live demo when a judge drags sliders | medium | `max_feasible_qty` diagnostics and a clear message. Sliders have bounded ranges. |
| Streamlit re-runs wipe state between steps | medium | Everything goes in `st.session_state`, keyed by step; solves are cached with `st.cache_data`. |
| Encoding and path bugs (Windows dev vs Linux deploy) | high (already seen) | UTF-8 everywhere, `pathlib`, CI-free smoke test on a clean clone on Day 6. |
| Real factory names shown next to synthetic "unreliable" scores in a public demo | designed out | Pseudonyms only, in the UI and the repo (decided). Names live only in the gitignored `data/private/mapping.csv`. |
| Open Supply Hub licence or attribution terms unknown | low/medium | Teammates confirm. DATA_PROVENANCE carries the attribution. Only the processed DB is published. |
| Hours per day lower than assumed | unknown | Cut order from the brief, triggered at the end of Day 3. |

---

## 11. Honest assessment

1. **The reliability model on synthetic data mostly recovers what we planted.** That is fine to demo *if we say it*: we show calibration (interval coverage) and the thin-history behaviour, not accuracy claims. **Decided:** empirical Bayes only. HistGB is dropped.
2. **Triple hedging is redundant.** Risk-adjusted capacity at p10, plus a risk penalty, plus a buffer, would over-buffer. **Proposed:** risk-adjusted capacity at the *mean*, the risk penalty for *who* gets allocated, and the simulation-priced buffer for *how much extra*. Each does one job.
3. **"Agent" overstates it.** There is no autonomous loop. It is a fixed pipeline with three LLM calls. **Proposed:** call it an "LLM analyst over optimisation tools" in the pitch. Judges scoring Ethical AI will reward the honesty.
4. **The memo is the part most at risk of being "AI for show".** Keep it short, structured, placeholder-only, with a template fallback. Its real value is turning tool output into a decision a manager can approve.
5. **Compliance uses the real signals.** **Decided:** "RSC or valid BSCI" → 442 eligible T-shirt factories. Missing means "no record", not "failed". We never label a real factory non-compliant.
6. **Ethics: synthetic bad scores next to real names.** **Decided:** pseudonyms + district only, in the UI and the public repo. Real names exist only in the local mapping.
7. **The unproven cap is also a fairness feature.** Capping unproven factories at 15% (rather than excluding them) gives small and new factories a way in. Worth a line in the pitch.
8. **SQLite is borderline necessary** for read-only data. Keep it, because the approvals log (human-in-the-loop audit trail) is a real use.
9. **Bangla intake:** first in the cut order. Plan it as a Day 5 stretch only. The validator handles Bangla digits anyway.
10. **Feasibility:** at 6 h/day the full scope fits with about 1 day of buffer. At ≤ 4 h/day it does not; apply the cut order on Day 3.

---

## 12. Decisions and remaining questions

**Decided (14 Nov review):**
| # | Decision |
|---|---|
| 2 | CSV moved to `data/raw/factories.csv`. Raw file, names and `os_id` stay out of git. Only the processed DB is public. |
| 3 | Real signals, rule "RSC or valid BSCI" → 442. |
| 4 | Pseudonyms only. |
| 5 | Gemini free tier. Current terms, model and limits are verified from docs on Day 3 (Tue 17). |
| 6 | Streamlit Community Cloud. |
| 7 | Research asks go to the three teammates → `RESEARCH_ASKS.md`. |
| 8 | Gazipur consolidation point. Confirm with a real exporter if one is reachable. |
| 9 | Largest-allocation factory declines on day 3. |
| 10 | A separate Python 3.11 venv. |
| 11 | PROMPTS.md covers both product prompts and development prompts. |

**Still open:**
1. **Hours per day.** Still unfilled, so 6 h is assumed. Under 5 h, the cut order applies from Day 1.
2. **Round `workers` to the nearest 50 in the public DB** to make re-identification harder (§10)? Default until answered: yes.

---

## Files I will maintain

| File | Contents |
|---|---|
| `SPEC.md` | Business rules, module contracts (generated from `schemas.py`), the optimiser formulation, and the "no invented numbers" design. Kept in sync as code changes. |
| `DATA_PROVENANCE.md` | Source (Open Supply Hub export, download date, licence/attribution: **to confirm**), the column profile from §1, each cleaning rule, every synthetic variable with its distribution and seed, every assumption constant from `config.py` and who provided it, the PII-dropping rule, and what is and isn't in the public DB. |
| `RESEARCH_ASKS.md` | The teammates' research list (Q7), with the placeholder value currently in `config.py` next to each ask. |
| `PROMPTS.md` | Each product prompt (intake_v1, memo_v1, …) with purpose, schema, validator, sample input and output, and version history. Plus a section disclosing AI tooling used in development (Claude Code as pair programmer) and the key development prompts. |
| `README.md` | What it does, deployed link, a 3-minute demo walkthrough, how to run locally and offline, the clear synthetic-data notice, limitations, and team credits. |
