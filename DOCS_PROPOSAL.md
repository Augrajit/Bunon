# Proposal: using the real order documents (27 Nov 2026, for approval)

Nothing here is built yet, apart from the privacy steps in §0. Style codes are written as **S01–S10** because the real
codes come from the buyer's POs. The mapping is in the private folder, not in the repo.

## 0. Privacy steps (done first, 27 Nov 2026)

- **Never committed.** I checked every branch, tag, stash, reflog and remote ref (`origin`: `main`, `v2` and 10 tags).
  No object in the history matches `data/documents`, `data/Documents`, any `.pdf` or the file names. Nothing needed
  removing, so there was no history rewrite and no force-push.
- **Ignored from now on.** `.gitignore` now has `data/documents/`, `data/Documents/` (both spellings, because Git on
  Linux and macOS is case-sensitive) and `data/**/*.pdf`. A new test, `tests/test_no_documents.py`, fails if any PDF
  is tracked or any code refers to the documents folder. Commit 2dcd1a1 on `v2`.
- **Rules for everything below:**
  - The raw documents are read only on the local machine.
  - Only the anonymised output (§a) is ever sent to Gemini (free tier) or used in the demo and tests.
  - The anonymised output is published only after the privacy scan passes: no real name, PO, SKU, email or address
    from the private list, the same check `tests/test_data_gen.py` runs for factory names.

**What the files are:**
- **10 delivery orders** (S01–S10). They come from one generator, have a text layer and run 3 pages each: header
  (buyer, PO numbers, style, item, quantity, factory, dates), a PO / pack / size / colour / SKU breakdown, fabric by
  colour and size range (rate per dozen, waste, net, booking), accessories and sewing (basis, waste, net, booking),
  packing notes and a "Review" list of missing items.
- **1 style sheet** (22 pages). It is scanned images; the only text is "PRICE: $2.65". It needs OCR on the local
  machine before it can be anonymised. It is never sent to Gemini as an image, because an image cannot be anonymised
  by text replacement.

## a. Anonymiser (`bunon/anonymise.py`, local only)

- **How it works:**
  - `pdftotext -layout` gives the text of each delivery order.
  - Rules find the sensitive values by their place in the layout: buyer, factory, issuing company, PO numbers, SKUs,
    emails, addresses and file paths (the footer holds a local path with a user name).
  - Each value is replaced with a **consistent** fake: the same PO always becomes the same fake PO, across every
    document. Fakes keep the format, so a 10-digit PO stays a 10-digit PO and an email stays an email.
  - The mapping is stored in `data/private/doc_mapping.json` (gitignored).
- **What is kept on purpose:** structure, sizes, colours, packs, quantities, rates, waste and **every error**, such as
  the misplaced comments column ("326 PCS" against sewing thread) or a rate of .1 kg/dzn.
- **Output:** `data/demo/docs/S01.txt …` plus a JSON of the parsed tables.
- **Privacy scan:** the private list of found values must not appear in any output. Every unknown capitalised token
  in the header fields is flagged for a person to confirm.
- **The style sheet:** needs local OCR first (Tesseract, about 2 h to install and wire up), then the same
  anonymiser. Until then it stays out of scope.
- **Tests:** the mapping is consistent across files; every number in the original appears unchanged in the output;
  the privacy scan holds.
- **Estimate:** 7 h, plus 3 h for the OCR step.

## b. Document intake (`agent.parse_document`, task `doc_v1`)

- **Input:** the anonymised text of one delivery order (or OCR'd style sheet). Never the raw PDF.
- **Output:** a structured order with styles, colours, packs (pieces per prepack, size ratio), sizes, quantity per
  PO / colour / size, fabric rate per dozen and waste per range, trims with basis and waste, print (from the item
  text, for example screen print), price (style sheet), ship date and factory details.
- **Same validator rules as order intake:**
  - Every value comes with a word-for-word quote.
  - Code checks that the quote states the value; a failed value becomes empty and a person fills it in.
- **New `textnum` cases:**
  - kg and "KG" with a leading point (".1 KG"), percentages, "per dozen"
  - size labels such as "5/6", "10/12", "14/16", "4/6X", which must never be read as dates or fractions
  - "4-02 5/6-02 6X-02" pack ratios
- **UI:** an "Upload a delivery order" box in the intake step. Anonymised demo files only in the hosted app.
- **Estimate:** 10 h.

## c. Cross-document checks in code (`bunon/doccheck.py`, no LLM)

- **Within one document:**
  - the sum of the size lines equals the header quantity
  - net fabric equals pieces ÷ 12 × rate per dozen
  - booking equals net × (1 + waste)
  - trims net equals pieces × basis
  - missing ship date, cancel date, factory address or contact
- **Across documents:**
  - style-sheet totals against delivery-order totals (needs the OCR step)
  - the fabric rate per dozen against other styles in the same fabric and size range, flagged when it is far from
    their median
- **Worked example from the real files:**
  - S01 books the 4/6X range at **0.1 kg/dzn**. All nine other styles book 4/6X at **1 kg/dzn**, and every 7/16
    range is at 1.40.
  - S01's net is therefore 3 kg per colour for 360 pieces instead of about 30 kg, so its booking is about 54 kg short
    across its two colours.
  - The check reports it with both rates and the styles compared.
- **Output:** a list of findings with the numbers that triggered each, shown in the intake step. A person decides.
  The AI may explain a finding under the placeholder rule, but code finds it.
- **Estimate:** 6 h.

## d. Accuracy evaluation (`bunon/doc_eval.py`)

- **Answer key:** written by hand, by a teammate rather than the AI, for the 10 anonymised delivery orders (and the
  style sheet after OCR). About 3 h of their time.
- **Harness:** field-level precision and recall per field type (quantity, rate, waste, size, date, factory) and the
  share left empty by the validator. A run after any prompt change, with a table added to SPEC.
- **Estimate:** 4 h for the harness, plus the teammate's 3 h.

## e. Engine impact if each style goes whole to one factory (the question we asked)

Answered by the interviewee: a style is never split, because a smaller quantity is loss-making for the
subcontractor. That makes §f the plan.

## f. Engine change: assign whole styles (or whole POs) to factories

**What changes:**
- **Optimiser:** the decision is a binary "style s goes to factory f", not a number of pieces.
  - Each style fits within one factory's capacity window, and a factory can take several styles in sequence.
  - The buffer becomes over-cut within a style (a few percent of extra pieces) plus a standby factory for each style,
    not extra pieces spread over factories.
  - The PuLP model stays a MIP with a different structure.
- **Simulation:** per style. The success rule (D12) holds per programme: good pieces of every style ≥ (1 − tolerance)
  × its quantity by its ship date. It can also be read per style, for the buyer.
- **The demo order** becomes a programme of several styles (6–10), modelled on the anonymised delivery orders:
  styles of 1,380–2,460 pieces in two size ranges and two colours each.
- **Rescue options:**
  - move an **unstarted** style to another factory
  - overtime at the same factory (`SURGE_SHARE`, placeholder)
  - accept a shortfall within tolerance
  - renegotiate the date with the buyer
  "Move the shortfall" over several factories goes away.
- **Update and forecast:** per style at a factory, so the D22 combined forecast carries over with styles as the unit.

**Estimate: 34–42 h.**

| Part | Hours |
|---|---|
| Optimiser model and buffer search | 10–12 |
| Simulation per style and success rule | 4–5 |
| Rescue options | 5–6 |
| Demo programme fixture and personas | 4–5 (team text for the persona messages, which change) |
| App sections (plan table, offers per style, rescue, QC) | 6–8 |
| Tests and sweep | 5–6 |

**Effect on tests and the pinned numbers:**
- **Every pinned demo number changes:** split 84.1%, projected 9,800, shortfall 3,300, chance 81.8%, last rescue day
  16, move 85.6% / +34,813 BDT, and the day-14 figures.
- `test_published_demo_numbers`, the README table, SPEC and the video script all have to be redone.
- **Engine tests:** about a third of `test_engine.py` (piece-split constraints, the lot size) is rewritten. The loop
  tests around `rescue_move` are rewritten. The reliability and quote-rule tests stay.
- **Every cached LLM answer that cites a number becomes invalid** and must be re-warmed: memo, warning, drafts,
  analyst, replies with quantities. The persona messages need new quantities from the team.
- **Sweep:** the 24 settings stay, with new assertions.

## g. Learn from past orders (`bunon/library.py`)

- **The library:** the anonymised styles (§a, §b) as past orders. For each: fabric, size range, rate per dozen,
  waste, trims per piece, print, quantity, and price where known.
- **For a new delivery order:**
  - code retrieves similar styles (same fabric and size range; then by item words such as "boyfriend tee" or
    "screen print")
  - it flags each value far from them, for example more than 30% from the median (a placeholder threshold)
  - it shows the comparison table: this style, the similar styles, the median, the gap
- **The AI's part:** it explains a flag in one or two sentences under the placeholder rule. It never computes or
  chooses.
- **Tests:** retrieval by fabric and range; S01's 4/6X rate is flagged against the nine others, and the others are
  not flagged.
- **Estimate:** 8 h (after a and b).

## Approved order (27 Nov 2026)

The product owner set the order: **§a, then §f, then §b, §c, §d, §g.**
- **§a** covers the delivery orders only: **done** (D29).
- **Style-sheet OCR is skipped.** A teammate hand-enters the style-sheet values as the answer key; they also feed the
  style-sheet vs delivery-order check in §c.
- **§f rescue options:**
  - move an unstarted style
  - overtime
  - accept a shortfall within tolerance
  - renegotiate the date
- **"Move part of a running style"** is not built until the interviewee confirms it ever happens. It stays a disabled
  option in config.
- **§f numbers:** all demo numbers are recomputed and re-pinned in one step, then the cache is re-warmed and the
  README, SPEC and video script notes are updated.
- **Personas:** the team gets the new styles and quantities as soon as §f's demo programme is fixed.

## Proposed order and total (original proposal, superseded above)

1. §0 privacy steps: **done**.
2. §a anonymiser (7 h). The teammate writes the §d answer key at the same time, on the anonymised files.
3. §b document intake (10 h).
4. §d evaluation harness (4 h). Run it before the intake is shown to anyone.
5. §c cross-document checks (6 h).
6. §g learn from past orders (8 h).
7. §f whole-style engine (34–42 h). It goes last because it resets every pinned number, the cache and the video
   script. It is worth doing only with enough time left to re-record.
8. Optional: OCR of the style sheet (3 h), which unlocks the style-sheet vs delivery-order total check.

**Totals:** a–d and g about 35 h; f 34–42 h; about 70–77 h in all.

**If time is short,** do §a, §b, §c and §g on the current engine first: they stand on their own and show the
document value. Then decide on §f with the deadline in view.
