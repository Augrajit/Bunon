# Research asks for the team

These are the numbers the prototype currently **guesses**. Each one is a placeholder in `bunon/config.py`, marked `ASSUMPTION`, and shown as "assumed" in the UI.
A sourced figure, even a rough range from one phone call, beats our guess.

**For each answer, please send:** the value or range, the unit, the source (a URL, a report name, or "phone call with X, merchandiser at a Y-sized factory"), and the date.
Put answers in the team chat or straight into this file. I'll copy them into `config.py` and `DATA_PROVENANCE.md`.

**Priority:** items 1–4 change the demo result. 5–8 make it more credible.

| # | Question | Unit | Placeholder now | Why it matters |
|---|---|---|---|---|
| 1 | **Sewing output for a basic crew-neck T-shirt**: pieces per sewing operator per day (8 h), or pieces per line per day plus operators per line | pcs/operator/day | 40 | Turns `number_of_workers` (real data) into capacity |
| 2 | **Share of a factory's workers who are sewing operators** | % | 55–70% | Same |
| 3 | **Spare capacity when a factory takes subcontract work**: typical share of monthly capacity, and how often factories are fully booked in Nov–Dec | % | 35% fully booked; the rest about 1% of declared capacity spare (peak season), capped at 1,000 pcs/day per factory. **Most important ask:** this was calibrated so the demo needs 3–5 factories, not measured | Decides how many factories an order needs |
| 4 | **Subcontract CM price for a basic tee** (cut-make, fabric supplied by the exporter), and the typical spread between cheap and expensive factories | BDT/piece | 30 ± 12% | Production cost in the optimiser |
| 5 | **Buyer late-delivery penalty terms** for basic knitwear: % of FOB per week? cancellation after N weeks? air freight at supplier's cost? | % / weeks | 5% of FOB per week; cancel after 2 weeks | Sets the risk penalty |
| 6 | **Truck cost** for a covered van, e.g. Ashulia/Narayanganj/Chattogram → Gazipur, and **pieces per truck** (folded, cartoned tees) | BDT/km, pcs | 60 BDT/km, 12,000 pcs | Transport cost |
| 7 | **Delivery point**: do subcontractors deliver to the main exporter's factory (we assume Gazipur) or straight to the port or CFS? | — | exporter's factory, Gazipur | Distances and transit days. Please confirm with a real exporter if you can reach one |
| 8 | **Typical FOB for a basic 160 GSM cotton tee** in large volume | USD/piece | 2.40 | Fictional email + penalty size |
| 9 | **Minimum batch** a subcontractor will accept for basic tees | pcs | 500–3,000 | Minimum-batch constraint |
| 10 | **Open Supply Hub data licence and attribution wording** for the export we use | — | unknown | Required for DATA_PROVENANCE.md |

**Not needed:** anything about rumours, news, weather, maps or ERP systems. Those are out of scope for 20 Nov.
