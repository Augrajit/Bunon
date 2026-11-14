"""Fill data/llm_cache with the scripted demo's three LLM calls, so the deployed app runs offline.

Needs GEMINI_API_KEY in the environment or in .env (gitignored). Makes at most 5 live calls
(intake, memo, re-plan memo, plus up to one retry each for the memos).

Run:  python -m bunon.warm_cache
"""
from __future__ import annotations

from bunon import agent as ag
from bunon import config as C
from bunon import optimizer as op
from bunon.data_gen import load_public
from bunon.llm import LLMClient

import json


def main() -> None:
    client = LLMClient(mode="live")
    email = C.DEMO_EMAIL_PATH.read_text(encoding="utf-8")

    intake = ag.parse_order(email, C.DEMO_OWN_CAPACITY, client)
    src = intake.meta.source if intake.meta else "failed"
    print(f"intake: {src}; issues: {intake.issues or 'none'}")
    for k, f in intake.fields.items():
        print(f"  {k:28s} {f.status:9s} {f.value!r:45.45s} <- {f.source_span!r:.70s}")
    order = intake.order or op.demo_order()
    if intake.order is None:
        print("  intake did not produce an order; continuing with the demo order for the memos")

    w = op.load_world()
    p = op.default_params(order)
    cands = op.build_candidates(w, order, p)
    comp = op.compare_three(w, order, p)
    rec = comp["ours"].recommended
    match = op.baseline_to_match(w, order, p, rec.risk.p_meet_qty)
    meta = load_public()["meta"]
    n_compliant = json.loads(meta.loc[meta["key"] == "report", "value"].iloc[0])["compliant_tee"]
    fs = ag.factsheet_for_plan(order, comp, match, cands, p.planned_slack_days, C.TARGET_P_MEET, n_compliant)
    memo = ag.write_memo(fs, "initial", client)
    print(f"\nmemo (initial): {memo.source}, attempts {memo.attempts}, errors {memo.errors or 'none'}\n{memo.rendered_md}")

    rp = op.replan_exclude(w, order, p, rec, op.largest_line(rec), reason="declined on day 3")
    memo2 = ag.write_memo(ag.factsheet_for_replan(fs, rp), "replan", client)
    print(f"\nmemo (replan): {memo2.source}, attempts {memo2.attempts}, errors {memo2.errors or 'none'}\n{memo2.rendered_md}")
    print(f"\nlive calls made: {client.calls}")


if __name__ == "__main__":
    main()
