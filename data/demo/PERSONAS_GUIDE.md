# How to write the factory personas (for the Bunon team)

The demo shows Bunon talking to five **simulated** subcontract factories. You write what each factory says; Bunon's AI only reads it. Your words are frozen in `personas.json` so the live demo always shows the same messages. The app labels every persona and message as **simulated**.

## The story the messages must fit

- **Order:** a fictional buyer orders 150,000 basic T-shirts. The exporter makes 100,000 in-house and needs **50,000 from subcontractors**.
- **Dates:** the order arrives on **Sunday 3 January 2027 (story day 0)**. The buyer's ex-factory deadline is **Sunday 31 January 2027 (day 28)**. Delivery is to the exporter's factory in Gazipur. The exporter supplies the fabric (CM basis).
- **Day 0:** Bunon sends each factory a request: pieces, date, and a rate to quote. The exact pieces per factory come from the planning engine; you'll get them when the developer fills in `factory_id`. Until then, write realistic round numbers; they will be aligned.
- **Day 2:** each factory replies: accept, decline or counter (fewer pieces, a different rate, or a later date).
- **Days 5–20:** the factories that took work send short daily progress updates. One of them gets into trouble, e.g. a power cut, fabric arriving late, or a line moved to another buyer's order. The trouble should be serious enough that the exporter needs to act.
- **Rescue (around day 9–10):** Bunon asks another factory to take extra pieces; that factory replies.
- **Days 24–28:** delivery notes ("all 9,000 pcs sent today").

## The five personas (change them if you know better)

| persona_id | Who | Writes in |
|---|---|---|
| `steady_en` | Reliable, formal | English |
| `quick_banglish` | Fast, says yes to everything, then runs late | Banglish |
| `price_banglish` | Bargains on the rate | Banglish |
| `declines_bn` | Lines are full, says no politely | Bangla |
| `slow_en` | Answers late and briefly | English |

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

## Check your file

```
.venv\Scripts\python.exe -m bunon.personas
```

It prints `OK`, or lists each problem with the message id.
