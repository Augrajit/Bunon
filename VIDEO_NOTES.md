# Video script notes: engine numbers per beat (SPEC.md D30, D31)

These are the numbers the programme demo shows at each beat. A test asserts them
(`tests/test_styles.py::test_published_demo_numbers`). If that test fails, change this file, README.md and SPEC.md
to the new numbers; never the other way round. The story clock is in PLAN_V2.md §7. Factory messages, the floor
count and the new entrant's profile are developer placeholders until the team's text arrives
(`data/demo/PERSONAS_GUIDE.md`).

**Success (D31, interview):** the programme ships by the buyer's date (lateness cancels it). Short pieces and
rejects are a cost.

**Calibration:** the synthetic lateness is calibrated so that the naive plan succeeds 90% of the time. The anchor
is a placeholder; `SENSITIVITY.md` shows 80% and 95%.

| Clock | Beat | Numbers on screen |
|---|---|---|
| Day 0 | Buyer email + 10 attached delivery orders | 16,800 pcs in 10 styles (600–2,460 each), deadline 31 Jan 2027, FOB USD 2.40, AQL 2.5; styles read in code from the anonymised delivery orders |
| Day 0 | Plan | 2 factories, no over-cut: **F0288** S02, S04, S05, S07, S08, S10 (10,080 pcs); **F1558** S01, S03, S06, S09 (6,720). Chance the programme ships on time: **92.1%** (target 90%). Cash cost 1,765,173 BDT; expected cost of short pieces and rejects 233,571 BDT |
| Day 0 | Against the naive rule | Naive plan 90.0% (the calibration anchor), expected short-piece cost 459,502 BDT |
| Day 0 | New entrant | **F0882**: no past jobs, vouched for and visited on 10 Nov 2026 → eligible for one trial style under the 15% unproven cap; giving it S10 → 88.8%, +33,840 BDT (shown, not chosen) |
| Day 0 | Requests | 3 drafts: F0288, F1558 (named styles, pieces, finish by 22 Jan 2027) and the standby F2314 (up to 15,300 pcs) |
| Day 2 | Replies | F0288 and F1558 accept; F2314 stands by. Split: same factories and styles, 92.1% |
| Day 12 | F1558's update | "1,800 ready, 42 of 60 operators came, productivity low, 1 day late" → **64% of its daily target**; projects 5,400 of 6,720 (1,320 short); chance 95.6% (the late factory is now known to ship on its date); level "watch"; last rescue day **today (day 12)**, F2829 |
| Day 12 | Floor representative | Counts 1,600 (42 of 60 workers) → **"Reports differ at F1558"** (11% apart; flag above 5%, placeholder); the forecast uses 1,600 |
| Day 12 | Rescue options (from the factory's count) | **Move S03 and S09 to F2829 (+2,880 pcs): 91.8%, +29,928 BDT, expected short-piece cost 191,070 BDT**; ship short on the date: 95.6%, 541,303 BDT; overtime +540 pcs: 385,453 BDT; renegotiate 4 more days (to 4 Feb 2027): 97.8% if the buyer agrees; move part of a running style: not offered |
| Day 14 (other path) | F0288's update | "2,600 sewn, fabric late, 2 days behind" → 6,240 of 10,080 (3,840 short); the AI suggests "our side (exporter)"; no outside factory can take whole styles any more |
| Day 26 | QC and track record | from the placeholder QC script (`qc_script` in `personas_programme.json`) |

A rescue lowers the chance slightly here, because a new factory adds its own risk of lateness. It is chosen
because it cuts the expected cost of short pieces by about 350,000 BDT. The table shows both, and a person
decides.
