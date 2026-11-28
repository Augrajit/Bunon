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
| 19 | **When does a production manager act on a forecast?** How far must the chance of on-time delivery fall between two daily updates before you want a warning? | percentage points | 5 points (Bunon placeholder) | The warning after a second update |
| 20 | **Per-style shortfall a buyer accepts**: when one style (or PO line) of a programme ships short, what share short is accepted without penalty or a claim? (Each style is now checked on its own, D30.) | % of the style's quantity | 2% (same placeholder as ask 11) | Defines success per style; see `SENSITIVITY.md` |
| 21 | **Extra pieces cut per style**: how many extra pieces does a factory cut per style to cover rejects and shortages (the "over-cut")? Is there a usual percentage, and does the buyer pay for them? | % of the style's quantity | Bunon searches 0–25% and recommends one | The buffer in whole-style plans |
| 22 | **Real defect and whole-lot rejection rates** for subcontracted kids' screen-print tees: the share of pieces rejected at final QC, and how often a whole lot fails | % of pieces; % of lots | synthetic history (defects about 0.5%); lot rejection about 1.7% (ask 11/12 placeholders) | Good pieces counted |
| 23 | **How often a subcontractor delivers short, and by how much** (suggested by `SENSITIVITY.md`: the largest driver of the low chance) | % of jobs; % of pieces | synthetic history: about 30% of jobs short | The chance that every style is complete |
| 24 | **How rare are failures?** In how many subcontracted programmes out of 100 does the programme miss the buyer's date (and get cancelled or penalised)? | % of programmes | the naive plan is calibrated to 90% success (PLACEHOLDER anchor, D31) | Sets the lateness calibration; see `SENSITIVITY.md` for 80% and 95% |
| 25 | **Salvage value of a rejected piece** (or a rejected lot) when it is sold at a loss | BDT per piece | 30 (Bunon placeholder) | Cost of rejects |
| 26 | **Whole-lot rejection**: how often a whole delivered lot fails final QC ("rare") | % of lots | 0.5% (Bunon placeholder) | Cost of rejects |
| 27 | **When does output against the daily target need action?** Below what share of the planned daily output does a production manager want a warning? | % of target | 90% (Bunon placeholder) | The day-12 warning |
| 28 | **Floor count vs the factory's report**: what difference between the floor representative's count and the factory's report should be flagged? | % of pieces | 5% (Bunon placeholder) | The mismatch flag |
| 29 | **New entrants**: how recent must a site visit be, and who counts as a voucher? | days; who | 90 days; anyone named (Bunon placeholder) | Which factories with no history may get a trial style |
| 30 | **Fabric price per kg** for the kids' knit programme (cotton spandex), as the exporter pays it | BDT/kg | 350 (interview: 300-400) | Cash cost totals only (see below); the real costing sheets suggest a much higher figure |
| 31 | **Waste allowance on trims and accessories**: one real delivery order books 2%, another 3% | % | 2% (fabric, from the delivery orders) | Booking checks in §c |

## Evidence received (29 Nov 2026, D34)

The team shared two real costing workbooks and photos of supplier delivery challans and delivery orders. They are kept only on the developer's machine in the ignored `data/Documents/` and are **not** in this repo. Below is what they suggest, without their figures. They are research input: no value in `config.py` changes from them.

| Source | What it suggests | Asks |
|---|---|---|
| Two costing workbooks: kids' knit polos (2017-2018) and kids' denim trousers | **CM:** the polo rate per piece is close to Bunon's tee placeholder; the trouser rate is much higher, as expected for a heavier, washed garment. **Fabric price per kg:** about twice the interview's range, but for different fabrics, years and products. **FOB:** the polos' FOB per piece brackets the demo's. The sheets also show a finance cost and itemised trims and accessories. | 8, 14, 16, 30 |
| Supplier delivery challans (fabric, cartons, accessories) to the subcontractor, made out to the exporter | The exporter supplies the fabric and trims, in rolls and kg by colour and pack. The challans carry the delivery date and handwritten corrections. This is the evidence for the planned "fabric delivery" check. | 23 (cause of short or late output), HANDOFF item 1 |
| A second, Excel-made delivery-order layout, and a printed delivery order with a handwritten booking total | Delivery orders come in more than one layout; trims waste differs (2% vs 3%); people annotate printed orders by hand. | 31; §b and §c test cases |

**Why the fabric price barely matters now.**
- **Who pays:** the exporter supplies the fabric, so it costs the same whichever factory sews.
- **The plan:** the optimiser's choice of factories and styles does not change with it.
- **The over-cut:** it is 0% (D31), and a higher fabric price would only make an over-cut less attractive.
- **The short-piece cost:** it uses lost FOB, not fabric cost.
- **Net effect:** it changes the cash cost totals shown, not the decision. Ask 30 is therefore low priority.

**Not needed:** anything about rumours, news, weather, maps or ERP systems. Those are out of scope for 20 Nov.
