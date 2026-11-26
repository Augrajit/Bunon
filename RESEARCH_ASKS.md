# Research asks for the team

These are the numbers the prototype currently **guesses**. Each one is a placeholder in `bunon/config.py`, marked `ASSUMPTION`, and shown as "assumed" in the UI.
A sourced figure, even a rough range from one phone call, beats our guess.

**For each answer, please send:** the value or range, the unit, the source (a URL, a report name, or "phone call with X, merchandiser at a Y-sized factory"), and the date.
Put answers in the team chat or straight into this file. I'll copy them into `config.py` and `DATA_PROVENANCE.md`.

**Priority:** item 13 (whole-lot rejection) is listed first: it is the most sensitive number in the model since D12. Items 1–4 and 11–12 also change the demo result. Item numbers are fixed IDs (code comments cite them), so the table is not renumbered. 5–8 and 14–15 make it more credible. Items 11–15 come from the expert interview (DATA_PROVENANCE.md §6) and need a confirmed figure.

| # | Question | Unit | Placeholder now | Why it matters |
|---|---|---|---|---|
| 13 | **Whole-lot rejection**: how often does a subcontracted lot fail final QC entirely, and what happens then (rework days? lost?) | % of lots | mean 2% (Beta conc. 50; prior strength 50); a rejected lot counts as 0 good pieces | **The most sensitive placeholder since D12**: the main reason Bunon's plan falls to 87.5% |
| 1 | **Sewing output for a basic crew-neck T-shirt**: pieces per sewing operator per day (8 h), or pieces per line per day plus operators per line | pcs/operator/day | 40 | Turns `number_of_workers` (real data) into capacity |
| 2 | **Share of a factory's workers who are sewing operators** | % | 55–70% | Same |
| 3 | **Spare capacity when a factory takes subcontract work**: typical share of monthly capacity, and how often factories are fully booked in Nov–Dec | % | 35% fully booked; the rest about 1% of declared capacity spare (peak season), capped at 1,000 pcs/day per factory. **Most important ask:** this was calibrated so the demo needs 3–5 factories, not measured | Decides how many factories an order needs |
| 4 | **Subcontract CM price for a basic tee** (cut-make, fabric supplied by the exporter), and the typical spread between cheap and expensive factories. Interview (Nov 2026): about 150 BDT per *set* (top + trousers); we need the T-shirt-only rate | BDT/piece | 60 ± 12% (150 × 40%, since D12) | Production cost in the objective |
| 5 | **Buyer late-delivery penalty terms** for basic knitwear: % of FOB per week? cancellation after N weeks? air freight at supplier's cost? | % / weeks | 5% of FOB per week; cancel after 2 weeks | Sets the risk penalty |
| 6 | **Truck cost** for a covered van, e.g. Ashulia/Narayanganj/Chattogram → Gazipur, and **pieces per truck** (folded, cartoned tees) | BDT/km, pcs | 60 BDT/km, 12,000 pcs | Transport cost |
| 7 | **Delivery point**: do subcontractors deliver to the main exporter's factory (we assume Gazipur) or straight to the port or CFS? | — | exporter's factory, Gazipur | Distances and transit days. Please confirm with a real exporter if you can reach one |
| 8 | **Typical FOB for a basic 160 GSM cotton tee** in large volume | USD/piece | 2.40 | Fictional email + penalty size |
| 9 | **Minimum batch** a subcontractor will accept for basic tees | pcs | 500–3,000 | Minimum-batch constraint |
| 10 | **Open Supply Hub data licence and attribution wording** for the export we use | — | unknown | Required for DATA_PROVENANCE.md |
| 11 | **Accepted shortfall (tolerance)**: what share short of the ordered quantity do buyers accept without penalty? | % | 2% (interview: "a small shortfall is accepted") | Defines success; changes every probability |
| 12 | **QC defect rate** at the exporter's final inspection for subcontracted basic tees: typical and bad-factory values | % of pieces | mean 2% (spread: Beta conc. 40 between factories, 200 between jobs) | Good pieces counted |
| 14 | **Fabric** consumption and price for a 160 GSM basic tee, incl. cutting waste; trims per piece | kg/piece, BDT/kg, BDT | 0.19 kg (Bunon estimate); 350 BDT/kg (midpoint of the interview's 300–400); trims 10 BDT (Bunon guess) → 76.5 BDT/piece derived | Cost of every piece, buffer included |
| 15 | **Expected loss if the subcontracted part fails** (penalty, air freight, lost margin, a cancelled order): what a manager would enter | BDT | 4,400,000 default; a manager input in the app | The optimiser's weight on risk |
| 16 | **Share of a set's sewing price taken by the T-shirt alone**: if a set (top + trousers) is sewn for about 150 BDT, what part of that is the T-shirt? (Checks Bunon's 40% assumption behind the 60 BDT CM rate; DATA_PROVENANCE.md §6.1) | % of the set price | 40% (Bunon assumption) | Sets the CM rate of every factory |
| 17 | **Payment terms to subcontractors**: how many days after delivery does an exporter usually pay a subcontract factory for CM, and how often is it late? | days | 30 (Bunon placeholder) | The exporter's side of the track record |
| 18 | **Overtime (surge) at a late factory**: how much extra daily output can a subcontractor get from overtime, and what premium on the CM rate does it ask? | % of daily output, % of CM | +15% output, +25% CM (Bunon placeholders) | The "overtime at the same factory" rescue option |

**Not needed:** anything about rumours, news, weather, maps or ERP systems. Those are out of scope for 20 Nov.
