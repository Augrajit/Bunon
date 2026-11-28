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
| `unit_price_bdt` | CM (cut-make) rate: 60 BDT, a **placeholder derived by Bunon** from the interview's per-set figure (calculation in §6.1; the interviewee gave no T-shirt rate) × lognormal, sd 12%, rank correlation with θ about +0.2 (cheaper is slightly less reliable). Realised: p10 51.1, median 60.0, p90 69.6. Was 30 BDT before SPEC.md D12 |
| `min_batch` | from {500, 1,000, 2,000, 3,000}, larger for bigger factories |
| `setup_days` | U{3..7} |

### History (`history`)

- Jobs per factory: a mixture of Poisson(1) 20%, Poisson(6) 30%, Poisson(18) 50%, offered between 2023-11-01 and 2026-10-31.
- **Realised:** 19,497 offers, 14,574 accepted. Clean accepted jobs per factory: 0–2 jobs for 432 factories, 3–10 for 683, 11+ for 633.
- Delivered = committed × fill. Two-part fill (SPEC.md D2): delivered in full with p_full = sigmoid(θ), θ ~ N(1.0, 0.8) + 0.3 if compliant + 0.15 × size; otherwise a short delivery, fill = sigmoid(2.2 + 0.3·z(θ) + N(0, 0.5)). Then reduced by:
  - **over-stretch:** committed above TRUE capacity → × U(0.85, 0.97). Factories that over-declare get over-stretched more often.
  - **district shocks:** 3% per (district, month) → × U(0.5, 0.8), +5–15 days, for every job in that district that month
  - **national shocks:** 2% per month → × U(0.85, 0.95), +2–5 days
  - **catastrophes:** 1% of jobs → fill U(0, 0.4)
- Delay: late with P(late) (+0.05 in Jun–Sep monsoon), days ~ Gamma(1.5, scale), +1 in the monsoon. **Eid months** (approximate: Apr & Jun 2024, Mar & Jun 2025, Mar & May 2026) add 2–4 days to a job that is already late; they cost days, not quantity (SPEC.md D3).
- **QC at the exporter (SPEC.md D12, placeholders until the interview gives figures):** each factory has a true defect rate (Beta, mean 2%, slightly lower for more reliable factories) and a chance that a whole delivered lot fails final QC (Beta, mean 2%). Per job: `qc_rejected_qty` = delivered × a job rate around the factory's rate, or all delivered pieces if the lot fails (`lot_rejected`). Generated from a separate random stream, so every earlier synthetic value is unchanged.
- **Data-entry errors, deliberately injected:** 1% of accepted jobs, either ×10 quantity or a negative delay. 151 rows, caught by `flag_bad_history_rows`.
- **Realised on clean accepted jobs** (14,423; recomputed 26 Nov 2026): mean fill 0.93 (p10 0.82, median 1.0; 56% delivered in full), 24% late, 4.8% shock-tagged. QC: mean defect rate 2.1% (median 1.3%, p90 5.5%) on lots that pass; 2.0% of lots rejected whole. Clean accepted jobs per factory: 0–2 for 430 factories, 3–10 for 687, 11+ for 631.

### "No hero" checks (tests)

- Spearman(price, θ) is in [0.05, 0.35].
- Factories in the top 5% for both low price and high θ: no more than 1.5× what independence would give.
- No single factory can cover more than 40% of the demo subcontract quantity.

---

## 3. Assumptions that need real numbers

Every assumption lives in `bunon/config.py`, marked `ASSUMPTION` or `PLACEHOLDER`, and is listed with its placeholder in `RESEARCH_ASKS.md`.
**The most influential one** is spare capacity (`SPARE_SHARE_BETA`, `SPARE_DAILY_CAP`). It was calibrated once on 14 Nov 2026 to the rule fixed in PLAN.md §3, before any optimisation result existed: "the median feasible candidate can make 8–15% of the subcontract quantity". Result: 13.8%.

| Assumption | Value | Provided by |
|---|---|---|
| pcs per sewing operator per day | 40 | placeholder |
| sewing share of workers | 55–70% | placeholder |
| fully booked share / spare share / cap | 35% / Beta(2, 200) / 1,000 pcs/day | placeholder, calibrated |
| CM price, basic tee | 60 BDT | **placeholder derived by Bunon** from the interview's ~150 BDT per set (§6.1); not stated by the interviewee |
| exporter-supplied material | 76.5 BDT per piece | **placeholder derived by Bunon** from the interview's 300–400 BDT/kg fabric price (§6.1); not stated by the interviewee |
| delivery tolerance | 2% (success = good pieces ≥ 98% of Q) | interview (principle) + placeholder (value) |
| QC defect rate / whole-lot rejection | mean 2% / mean 2% | placeholder |
| expected loss if the subcontracted part fails | 4,400,000 BDT default; a manager input in the app | placeholder |
| FOB, basic tee | USD 2.40 | placeholder |
| late penalty (buyer email, fictional) | 5% of FOB per week; no longer sets the loss (D12) | placeholder |
| truck cost / pcs per truck | 60 BDT/km / 12,000 | placeholder |
| delivery point | Gazipur (24.00, 90.42) | placeholder; confirm with a real exporter |

---

## 4. AI tooling disclosure

The code was written with Claude Code (Anthropic) as pair programmer. Development prompts and product prompts are listed in `PROMPTS.md`.

## 5. Images

| File | Source | Author | Licence | Size |
|---|---|---|---|---|
| `assets/hero_sewing.svg` (fallback if the photo is missing) | Original vector illustration drawn by hand in SVG for this project, 18 Nov 2026. No photo, stock image or third-party artwork was used or traced. | Bunon project (written with an AI coding assistant) | Original work of this project; no third-party licence applies | 6.1 KB |
| `assets/factory_floor.jpg` (login background, welcome banner) | Wikimedia Commons, [File:Working_conditions_of_Garment_workers_in_Bangladesh.jpg](https://commons.wikimedia.org/wiki/File:Working_conditions_of_Garment_workers_in_Bangladesh.jpg) (taken 24 Jul 2013). Downloaded 19 Nov 2026 and resized from 4608×3456 to 1600×1200 (JPEG q82); no other edits. The full-size original stays local in `data/image/` (gitignored). | Fahad Faisal | [CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/); the resized copy is shared under the same licence | 260 KB |

The app shows the credit "Photo: Fahad Faisal, CC BY-SA 4.0, via Wikimedia Commons (resized)", with links to the source page and the licence, on the login and welcome pages. The SVG is shown only if the photo file is missing, with its own credit. No other images are embedded. A reference photo of unknown licence suggested during design review was not used.

## 6. Expert interview (single-source input)

| | |
|---|---|
| Source | One anonymised interview with a businessperson who works in garment subcontracting in Bangladesh, conducted by the Bunon team, November 2026. Name withheld; the notes stay with the team. |
| Status | **Single-source expert input.** Directions are used; the numbers stay labelled placeholders until confirmed (a follow-up interview is planned). |
| Used in | SPEC.md D12, `bunon/config.py` (section "Success, quality and loss" and the CM price block) |

What it changed:
- **Success rule:** an order succeeds if the good pieces delivered by the deadline reach (1 − tolerance) × the subcontracted quantity, not 100% of it. Tolerance placeholder 2%, adjustable in the app.
- **Quality:** QC rejects some pieces, and a whole lot can occasionally fail; only good pieces count. Defect rate and lot rejection are placeholders (2% each).
- **Delays** are frequent but usually short (1–2 days). Buyers often keep a hidden cushion of 5–7 days, but **Bunon plans to the buyer's stated date** and does not use that cushion.
- **Prices (as stated):** sewing about 150 BDT per **set** (top + trousers); fabric 300–400 BDT per **kg**. The interviewee did **not** give a T-shirt sewing rate or a material cost per shirt; the two per-piece figures Bunon uses are its own placeholders derived from these (§6.1). On a CM basis the exporter supplies the fabric (to be confirmed).
- **Loss on failure** is the manager's estimate, entered in the app; it is not read from the buyer email.
- **Safety margin** (minimum over-allocation) is optional, default 0%.
- **Delivery point:** subcontractors deliver to the exporter's own factory (the Gazipur point).

### 6.0 Follow-up interview (28 Nov 2026, same source, anonymised; SPEC.md D31)

**Single-source expert input.** Applied as one calibration change (D31):
- **Success:** the programme must ship by the buyer's date, because lateness causes cancellation. Defects and short pieces are a cost, not a failure. Rejects are sold at a loss (salvage value: PLACEHOLDER, 30 BDT per piece). Tolerance stays a research question, not a gate.
- **Failures are rare.** No figure was given. The synthetic lateness is calibrated so that the naive plan succeeds about 90% of the time (a PLACEHOLDER anchor; `python -m bunon.calibrate`).
- **QC defect rate: 3.5%**, the midpoint of the interviewee's 3-4%. It is set in the synthetic-history generator (`config.QC_DEFECT_MEAN`), and the database was regenerated. QC uses its own random stream, so every other column of the history is unchanged.
- **Whole-lot rejection is rare** and is salvaged at a loss, not lost. The chance is a PLACEHOLDER, 0.5%.
- **The exporter supplies the fabric** (confirmed). Fabric per shirt comes from the anonymised delivery orders, not from the interview:
  - 1 kg per dozen in sizes 4/6X (about 0.083 kg per piece);
  - 1.40 kg per dozen in 7/16 (about 0.117 kg per piece);
  - 2% fabric waste.
  - S01's .1 kg is a known document error and is not used.
- **Typical delay cause:** worker absence and low productivity. The daily update now carries attendance (workers present against planned), and the warning signal is output against the daily target.
- **The exporter's floor representative** posts the daily update from inside the factory. A factory's own report is shown next to it, and differences above a PLACEHOLDER threshold (5%) are flagged.
- **Factory ranking:** capacity, then workers, then capability, then quality.
- **New entrants:** a factory with no history can be trusted through a vouch and a site visit. The demo profile (F0882) is simulated and hand-written (`data/demo/factory_profiles.json`).

### 6.1 How the two per-piece prices were derived (Bunon's placeholders, not interview figures)

Only the two inputs marked "interview" were said by the interviewee. Every other number in these calculations is Bunon's own assumption, chosen on 26 Nov 2026 before any result was computed, and labelled `PLACEHOLDER` in `bunon/config.py`.

**CM (sewing) price per T-shirt: 60 BDT**

| Step | Value | Source |
|---|---|---|
| Sewing price per set (top + trousers) | 150 BDT | interview ("about 150 BDT per set") |
| Share of a set's sewing price that a basic T-shirt takes | 40% | **Bunon assumption**: a basic tee has fewer operations than trousers (no fly, pockets or waistband), so somewhat under half |
| CM per T-shirt = 150 × 0.40 | **60 BDT** | derived placeholder (`BASE_CM_PRICE_BDT`) |

Each factory's synthetic price then varies around 60 BDT (lognormal, sd 12%; realised p10 51.1, median 60.0, p90 69.6).

**Exporter-supplied material per T-shirt: 76.5 BDT**

| Step | Value | Source |
|---|---|---|
| Fabric price | 300–400 BDT/kg | interview |
| Price used | 350 BDT/kg | **Bunon assumption**: midpoint of the stated range |
| Fabric per shirt | 0.19 kg | **Bunon estimate**: about 1.2 m² of 160 GSM single jersey per adult basic tee including cutting waste (1.2 × 0.160 kg ≈ 0.19 kg); not measured |
| Fabric cost = 350 × 0.19 | 66.5 BDT | derived |
| Trims (label, thread, poly bag) | 10 BDT | **Bunon assumption**, no source |
| Material per shirt = 66.5 + 10 | **76.5 BDT** | derived placeholder (`MATERIAL_COST_BDT_PER_PIECE`) |

The material cost is the same at every factory (the exporter supplies it), so it does not change which factories are chosen; it does set the price of every buffer piece. Both figures, and the three Bunon assumptions inside them, are in RESEARCH_ASKS.md (items 4 and 14) for confirmation.

## 7. Anonymised delivery orders (`data/demo/docs/`, DOCS_PROPOSAL.md §a)

- **Source:** 10 real delivery orders shared by a teammate (from one exporter's DO generator). The PDFs stay on the
  local machine in an ignored folder; they are never committed, never sent to an LLM and never used raw in the demo or
  tests (`tests/test_no_documents.py`).
- **Cleaning:** `python -m bunon.anonymise <folder>` (local only) replaces the issuing company, buyer, factory,
  address / attn, PO numbers, SKUs, style codes, emails, file paths and the graphic words of each item description
  with consistent fakes (`COMPANY A`, `BUYER A`, `FACTORY A`, POs `7000000001…`, SKUs `8000000001…`, styles
  `S01–S10`, graphics `GFX01…`). The mapping is in `data/private/doc_mapping.json` (gitignored).
- **Kept as in the source:** layout, sizes, colours, packs, quantities, fabric rates, waste, trims and every error
  (for example S01's 4/6X fabric at .1 KG per dozen).
- **Checks:** a privacy scan refuses to write if any real value or distinctive word of a real name is left;
  `tests/test_anonymise.py` checks the published files hold only fakes, and against the private mapping where it
  exists.
- **Not included:** the scanned 22-page style sheet. A teammate enters its values by hand as the answer key.
- **Fabric (D32):** the style sheet gives the fabric of every style as cotton spandex and states no weight. The demo's buyer email follows it.

## 8. Demo fixtures added in D31

- `data/demo/factory_profiles.json`: one simulated new-entrant profile (F0882: owner background, a vouch, a site visit). Developer placeholder text, labelled simulated.
- `data/demo/floor_reports.json`: the floor representative's scripted day-12 count (form defaults only; a person posts it). Developer placeholder.
- `data/calibration.json`: the lateness multiplier chosen by `python -m bunon.calibrate --target 0.90`.
- `tests/fixtures/bunon_pre_d31.db`: the pre-D31 synthetic database (QC 2%, lot rejection 2%), frozen for the piece-split regression tests. Same public, pseudonymous, synthetic content as `data/processed/bunon.db`.

## 9. Costing sheets, challans and a second delivery-order layout (D34; research input only, not published)

- **What:** two real costing workbooks (kids' knit polos 2017-2018, kids' denim trousers), and photos of supplier delivery challans (fabric, cartons, accessories) and of two delivery orders (an Excel-made layout and a printed one with a handwritten total). Shared by a teammate.
- **Where:** only on the developer's machine, in the ignored `data/Documents/`. They hold real company names, addresses, phone numbers, a tax ID, PO numbers, SKUs and a brand name. They are never committed, never uploaded, never sent to an LLM, and images are never sent at all (text replacement cannot anonymise a photo).
- **How they are used:** as research input only (RESEARCH_ASKS.md, "Evidence received"). No figure from them is in the repo and no `config.py` value changes. A later feature (fabric delivery evidence, HANDOFF §4) will use values a person types by hand and anonymises with the existing mapping, so the same companies get the same fakes.
- **Read with:** `openpyxl` / `xlrd` installed in a scratch folder (not project requirements); the photos were viewed locally.
