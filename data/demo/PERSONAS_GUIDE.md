# How to write the factory personas (for the Bunon team)

The demo shows Bunon talking to six **simulated** subcontract factories. You write what each factory says; Bunon's AI only reads it. Your words are frozen in `personas.json` so the live demo always shows the same messages. The app labels every persona and message as **simulated**.

## NEW (28 Nov 2026, SPEC.md D31): the programme story after the interview calibration

This replaces the D30 brief and the older 50,000-piece story further down. Please write the persona messages for
this one.

- **Order:** a fictional buyer's kids' T-shirt programme of **10 styles, 16,800 pieces**, all subcontracted (styles S01–S10 from the anonymised delivery orders in `data/demo/docs/`). A style is never split.
- **Dates:**
  - Day 0 is Sunday 3 January 2027.
  - The deadline is Sunday 31 January 2027 (day 28). Late means cancelled.
  - Each factory finishes by 22 January 2027.
- **What the app asks each factory** (no over-cut):

| Factory | Persona | Styles asked | Pieces asked |
|---|---|---|---|
| F0288 | `slow_en` (English) | S02, S04, S05, S07, S08, S10 | 10,080 |
| F1558 | `quick_banglish` (Banglish): says yes, then falls behind | S01, S03, S06, S09 | 6,720 |
| F2314 | `standby_en` (English): standby, asked for its spare capacity | none at first | 15,300 |
| F2829 | `rescue_en` (English): takes the rescue | S03 and S09 on day 12 | 2,880 |

- **Day 2:** F0288 and F1558 accept. F2314 says it can stand by. Please don't name a rate unless it is the factory's real asking rate: a rate more than 2% from the plan's reads as a counter.
- **Day 12, F1558's update:** the cause is **worker absence and low productivity** (interview), with attendance. For example "1,800 ready, 42 of 60 operators came, productivity low, 1 day late".
- **Day 12, the exporter's floor representative** (a new exporter role) counts on the floor. For example 1,600 pieces, 42 of 60 present, "two lines short of operators". The scripted values are in `data/demo/floor_reports.json`.
- **Rescue reply, F2829:** accepts the two styles.
- **Day 14, F0288's update:** keep the late-fabric story (the exporter's side). For example "2,600 sewn, fabric from the dyeing unit came late, 2 days behind".
- **New entrant F0882** (`data/demo/factory_profiles.json`): an owner background, who vouches and when, and a site visit and its findings. No history; it is shown with a trial style.
- **Not used:** `steady_en`, `declines_bn` and `price_banglish`.
- **Quantities in your text:** if a message names pieces, use the numbers above. Every number in `expected` must be written in the text; `workers_present` and `workers_planned` are new fields.

## The story the messages must fit (before D30, kept for reference)

- **Order:** a fictional buyer orders 150,000 basic T-shirts. The exporter makes 100,000 in-house and needs **50,000 from subcontractors**.
- **Dates:** the order arrives on **Sunday 3 January 2027 (story day 0)**. The buyer's ex-factory deadline is **Sunday 31 January 2027 (day 28)**. Delivery is to the exporter's factory in Gazipur. The exporter supplies the fabric (CM basis).
- **Day 0:** Bunon sends each factory a request: pieces, date, and a rate to quote. The exact pieces per factory come from the planning engine; you'll get them when the developer fills in `factory_id`. Until then, write realistic round numbers; they will be aligned.
- **Day 2:** each factory replies: accept, decline or counter (fewer pieces, a different rate, or a later date).
- **Days 5–20:** the factories that took work send short daily progress updates. One of them gets into trouble, e.g. a power cut, fabric arriving late, or a line moved to another buyer's order. The trouble should be serious enough that the exporter needs to act.
- **Day 12:** the scripted daily update (production started after the split was agreed on day 2, plus the factory's setup days).
- **Rescue (days 12–13):** Bunon asks another factory to take extra pieces; that factory replies.
- **Days 24–28:** delivery notes ("all 9,000 pcs sent today").

## Placeholders already in the file

The developer has put **stand-in messages** in `personas.json` (each marked `"placeholder": true`) so the app's loop could be built. Replace their `text` with your own writing and set `"placeholder": false`; keep `msg_id`, `kind` and `story_day`, and update `expected` to what you wrote. There is a sixth persona, `standby_en` (factory F2829), the best factory outside the first plan, asked as a standby. `qc_script` at the end holds the scripted QC outcome as shares (also placeholders).

The factory IDs are already filled in: F1856, F1064, F0824, F2184, F0288 and F2829. The pieces the app asks each factory for: F1856 13,200; F1064 13,100; F0824 12,400; F2184 12,400; F0288 11,400; F2829 15,200 (standby). If your text names a quantity, use these.

## The six personas (change them if you know better)

| persona_id | Who | Writes in |
|---|---|---|
| `steady_en` | Reliable, formal | English |
| `quick_banglish` | Fast, says yes to everything, then runs late | Banglish |
| `price_banglish` | Bargains on the rate | Banglish |
| `declines_bn` | Lines are full, says no politely | Bangla |
| `slow_en` | Answers late and briefly | English |
| `standby_en` | The standby factory outside the first plan | English |

## How to fill a message

Copy the example persona's message block. Fields:

- `msg_id`: unique, lower case, e.g. `quick_banglish_update_day9`.
- `story_day`: the day it is sent (0–40).
- `kind`: `offer_reply`, `progress_update`, `rescue_reply`, `delivery_note` or `other`.
- `text`: **exactly what the factory writes**, the way real people write on WhatsApp: short, informal, typos are fine. Use real Banglish and Bangla as people actually write it.
- `expected`: what you *meant*. This is the answer key the parser is tested against:
  - `decision` (`accept` / `reject` / `counter`) for offer and rescue replies
  - `qty` (pieces), `price_bdt_per_piece`, `ship_date` (as `2027-01-25`)
  - `pieces_done` (cumulative pieces finished) and `delay_days` for updates
  - `cause` in plain words (e.g. "power cut")

**Rule:** every number in `expected` must be written in the `text`. Digits are always safe (`9000`, `9,000`, `৯০০০`), and so are `9 hajar` and `৯ হাজার`. If you write a number as a word, like "dui din", that's realistic and welcome. Run the check below: it will flag the number, and the developer will teach the parser that word.

## Things not to write

- No real factory, company or person names. No phone numbers.
- No real news about a real factory.

## After you change a text

The live demo replays the AI's reading of each message from a cache. A changed text is a new message: until the developer re-runs `python -m bunon.warm_cache` (and one live walk of the app), the offline demo cannot read it, so the offer shows as "read by hand" and the split may become infeasible. Tell the developer when you have changed texts.

## Check your file

```
.venv\Scripts\python.exe -m bunon.personas
```

It prints `OK`, or lists each problem with the message id.
