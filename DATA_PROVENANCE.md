# Data provenance

**Short version:** the factory list is real (Open Supply Hub, pseudonymised). **Every performance number is synthetic**: capacity, spare capacity, prices, history, delays and acceptance.
The UI labels synthetic values as synthetic wherever they appear.

Built by `python -m bunon.data_gen`, seed `42`, as-of date `2026-11-20`.

---

## 1. Real data: Open Supply Hub export

| | |
|---|---|
| Source | Open Supply Hub (opensupplyhub.org), facilities export for Bangladesh |
| File | `facilities.csv`, 9,368,042 bytes, 3,214 rows × 200 columns. Saved locally as `data/raw/factories.csv` |
| Downloaded | 14 Nov 2026 (newest `contribution_date` in the file: 2026-10-31) |
| Licence / attribution | **To confirm** (research ask 10). Placeholder attribution: "Contains data from Open Supply Hub." |
| In the public repo? | **No.** The raw CSV is gitignored |

### What is published (`data/processed/bunon.db`, table `factories`)

Columns: `factory_id, district, lat, lng, coord_shared, workers, workers_imputed, tee_capable, has_knitting, has_printing, has_dyeing, has_textile_prod, rsc, bsci_valid, bsci_expiry, compliant`.

- **Not published:** factory name, `os_id`, address, parent company, and every personal-contact field. Names and `os_id` exist only in `data/private/mapping.csv`, which is gitignored and local to the developer's machine.
- `factory_id` (`F0001`–`F3196`) is a seeded random shuffle (seed `20261114`), not derived from `os_id` order.
- `lat`/`lng` are rounded to 2 decimals (about 1.1 km). `workers` is rounded to the nearest 50. Both make it harder to re-identify factories against the public Open Supply Hub site.
- A test (`tests/test_data_gen.py::test_public_db_contains_no_names_or_os_ids`) scans every cell of the public DB for any `os_id` or distinctive factory name.

### Cleaning rules

| Rule | Effect |
|---|---|
| Drop `is_closed == True` | 18 rows dropped → 3,196 open |
| `number_of_workers`: split on `\|`, ranges become their midpoint, take the median; 0 or empty = missing; impute the district median | 55 imputed (`workers_imputed=1`) |
| `district` from address keywords, first match wins (`config.DISTRICT_KEYWORDS`); "chattogram" only if lat < 23.0 (to avoid matching "Dhaka-Chittagong Highway") | gazipur 939, dhaka_city 682, narayanganj 585, ashulia_savar 459, chattogram 375, unknown 106, other 50 |
| `coord_shared`: exact coordinate shared with another row in the export (likely an area centroid) | 1,731 of the open rows |
| `tee_capable`: regex `t[\s-]?shirt\|tee\b` over `product_type`, `processing_type`, `processing_type_facility_type_raw`, `claim_product_types` | 1,748 |
| `rsc`: `accord_inspections_and_remediation_program.rsc_presence` contains `Yes` | 420 of the T-shirt factories |
| `bsci_valid`: latest `amfori_compliance_status.bsci_audit.expiration_date` ≥ 2026-11-20 | 136 of the T-shirt factories |
| **`compliant` = `rsc` OR `bsci_valid`** | **442** of the T-shirt factories |

A missing compliance record means "no record in Open Supply Hub". It never means "non-compliant", and the product never says it does.

---

## 2. Synthetic data

Generated only for the 1,748 T-shirt-capable open factories, from the **public** table alone, so anyone can rebuild it from the public DB and the seed. Tables `factory_synth` and `history` carry `is_synthetic = 1`.

### Hidden per-factory traits (used to generate, never stored)

| Trait | Distribution |
|---|---|
| reliability θ (logit of mean fill) | N(2.2, 0.9) + 0.3·compliant + 0.15·z(log workers) |
| over-declaration d | exp(N(0.10, 0.12)) |
| P(late) | sigmoid(−1.0 − 0.6·z(θ) + N(0, 0.5)) |
| late-days scale | U(2, 6) |
| P(accept offer) | Beta(6, 2) |

### Stored synthetic attributes (`factory_synth`)

| Column | How |
|---|---|
| `declared_daily_capacity` | workers × U(0.55, 0.70) sewing share × 40 pcs/operator/day (**assumption**) × d |
| `declared_spare_daily` | 35% fully booked (0). Otherwise min(Beta(2, 200) × declared, 1,000). **Calibrated, not measured** (see §3) |
| `unit_price_bdt` | 30 BDT (**assumption**) × lognormal, sd 12%, rank correlation with θ about +0.2 (cheaper is slightly less reliable). Realised: p10 25.6, median 30.0, p90 34.8 |
| `min_batch` | from {500, 1,000, 2,000, 3,000}, larger for bigger factories |
| `setup_days` | U{3..7} |

### History (`history`)

- Jobs per factory: a mixture of Poisson(1) 20%, Poisson(6) 30%, Poisson(18) 50%, offered between 2023-11-01 and 2026-10-31.
- **Realised:** 19,497 offers, 14,574 accepted. Clean accepted jobs per factory: 0–2 jobs for 432 factories, 3–10 for 683, 11+ for 633.
- Delivered = committed × fill. Fill = sigmoid(θ + N(0, 0.35)), reduced by:
  - **over-stretch:** committed > 0.9 × true capacity → × U(0.75, 0.95). Factories that over-declare get over-stretched more often.
  - **Eid months** (approximate: Apr & Jun 2024, Mar & Jun 2025, Mar & May 2026) → × 0.9
  - **district shocks:** 3% per (district, month) → × U(0.5, 0.8), +5–15 days, for every job in that district that month
  - **national shocks:** 2% per month → × U(0.85, 0.95), +2–5 days
  - **catastrophes:** 2% of jobs → fill U(0, 0.4)
- Delay: late with P(late) (+0.10 in Jun–Sep monsoon), days ~ Gamma(1.5, scale), +2 in the monsoon.
- **Data-entry errors, deliberately injected:** 1% of accepted jobs, either ×10 quantity or a negative delay. 154 rows, caught by `flag_bad_history_rows`.
- **Realised on clean accepted jobs:** mean fill 0.79 (p10 0.61, median 0.82), 36% late, 5.5% shock-tagged.

### "No hero" checks (tests)

- Spearman(price, θ) is in [0.05, 0.35].
- Factories in the top 5% for both low price and high θ: no more than 1.5× what independence would give.
- No single factory can cover more than 40% of the demo subcontract quantity.

---

## 3. Assumptions that need real numbers

Every assumption lives in `bunon/config.py`, marked `ASSUMPTION`, and is listed with its placeholder in `RESEARCH_ASKS.md`.
**The most influential one** is spare capacity (`SPARE_SHARE_BETA`, `SPARE_DAILY_CAP`). It was calibrated once on 14 Nov 2026 to the rule fixed in PLAN.md §3, before any optimisation result existed: "the median feasible candidate can make 8–15% of the subcontract quantity". Result: 13.8%.

| Assumption | Value | Provided by |
|---|---|---|
| pcs per sewing operator per day | 40 | placeholder |
| sewing share of workers | 55–70% | placeholder |
| fully booked share / spare share / cap | 35% / Beta(2, 200) / 1,000 pcs/day | placeholder, calibrated |
| CM price, basic tee | 30 BDT | placeholder |
| FOB, basic tee | USD 2.40 | placeholder |
| late penalty | 5% of FOB per week | placeholder |
| truck cost / pcs per truck | 60 BDT/km / 12,000 | placeholder |
| delivery point | Gazipur (24.00, 90.42) | placeholder; confirm with a real exporter |

---

## 4. AI tooling disclosure

The code was written with Claude Code (Anthropic) as pair programmer. Development prompts and product prompts are listed in `PROMPTS.md`.

## 4. Images

| File | Source | Author | Licence | Size |
|---|---|---|---|---|
| `assets/hero_sewing.svg` (fallback if the photo is missing) | Original vector illustration drawn by hand in SVG for this project, 18 Nov 2026. No photo, stock image or third-party artwork was used or traced. | Bunon project (written with an AI coding assistant) | Original work of this project; no third-party licence applies | 6.1 KB |
| `assets/factory_floor.jpg` (login background, welcome banner) | Wikimedia Commons, [File:Working_conditions_of_Garment_workers_in_Bangladesh.jpg](https://commons.wikimedia.org/wiki/File:Working_conditions_of_Garment_workers_in_Bangladesh.jpg) (taken 24 Jul 2013). Downloaded 19 Nov 2026 and resized from 4608×3456 to 1600×1200 (JPEG q82); no other edits. The full-size original stays local in `data/image/` (gitignored). | Fahad Faisal | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/); the resized copy is shared under the same licence | 260 KB |

The app shows the credit "Photo: Fahad Faisal, CC BY-SA 4.0, via Wikimedia Commons (resized)", with links to the source page and the licence, on the login and welcome pages. The SVG is shown only if the photo file is missing, with its own credit. No other images are embedded. A reference photo of unknown licence suggested during design review was not used.
