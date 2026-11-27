# Video script notes: engine numbers per beat (SPEC.md D30)

These are the numbers the programme demo shows at each beat. A test asserts them
(`tests/test_styles.py::test_published_demo_numbers`). If that test fails, change this file, README.md and SPEC.md
to the new numbers; never the other way round. The story clock is in PLAN_V2.md §7; factory messages are developer
placeholders until the team's text arrives (`data/demo/PERSONAS_GUIDE.md`).

| Clock | Beat | Numbers on screen |
|---|---|---|
| Day 0 | Buyer email + 10 attached delivery orders | 16,800 pcs in 10 styles (600–2,460 each), deadline 31 Jan 2027, FOB USD 2.40, AQL 2.5; styles read in code from the anonymised delivery orders |
| Day 0 | Plan | 3 factories, 20% over-cut per style: F0288 S01, S02, S04, S10 (8,424 pcs); F2427 S05–S08 (8,280); F2786 S03, S09 (3,456). Chance every style is complete: **32.0%** (target 90%). Cost 2,653,954 BDT |
| Day 0 | Against the naive rule | Naive 1.2%; naive + the same over-cut 13.4%; the naive rule needs a 57.5% over-cut (+659,766 BDT) to match |
| Day 0 | Requests | 4 drafts: the 3 plan factories (named styles, pieces, finish by 22 Jan 2027) and the standby F2829 (up to 15,200 pcs) |
| Day 2 | Replies | F0288 and F2427 accept; F2786 counters on the rate (56 BDT); F2829 stands by (60 BDT). Split: same factories and styles, 32.0% |
| Day 12 | F2427's update | "2,000 ready, 1 day late" (power cut) → projects 6,000 of 8,280, **2,280 short**; chance **0%**; level "below target"; last rescue day **day 13** (F2829) |
| Day 12 | Rescue options | **Move S07 to F2829 (+2,304 pcs): 43.1%, +40,563 BDT**; overtime +600 pcs: 0%; accept the shortfall: not possible (S08 would have 672 of 2,411 needed); renegotiate the date: 6 more days (to 6 Feb 2027), 54.9% if the buyer agrees; move part of a running style: not offered (waiting for the interviewee) |
| Day 14 (other path) | F0288's update | "2,600 sewn, fabric late, 2 days behind" → projects 6,240 of 8,424 (2,184 short); the AI suggests "our side (exporter)"; no outside factory can take a whole style any more |
| Day 26 | QC and track record | from the placeholder QC script (`qc_script` in `personas_programme.json`) |

The low chance is honest under the per-style rule and the synthetic history; see `SENSITIVITY.md` for what drives
it. Do not tune any placeholder to raise it.
