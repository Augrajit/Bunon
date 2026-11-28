"""Fill data/llm_cache with every scripted demo LLM answer, so the deployed app runs offline.

Covers: intake, the initial and re-plan memos, 2 steering examples, the 3 news items and the 3 what-if questions. Needs GEMINI_API_KEY in the environment or in
.env (gitignored). Cached answers are reused, so a re-run only calls the model for what changed; a full
refresh is roughly 30-60 live calls (each what-if question can take several). Re-run after changing a
prompt, a demo input, or anything that changes the engine's numbers (they are part of the memo prompts).

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
    if C.DEMO_PROGRAMME:  # SPEC.md D30: the styles come from the attached delivery orders (code), as in the app
        from bunon import programme as pg
        order = pg.as_programme(order)
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

    rp = op.replan_locked(w, order, p, rec, op.largest_line(rec), days_elapsed=3)  # what the app's button runs
    memo2 = ag.write_memo(ag.factsheet_for_replan(fs, rp), "replan", client)
    print(f"\nmemo (replan): {memo2.source}, attempts {memo2.attempts}, errors {memo2.errors or 'none'}\n{memo2.rendered_md}")
    examples = ("Use no more than three factories and keep new factories under 10%.",
                "Don't use F2184 and plan 6 days of slack. Also move the deadline to 27 December.")
    ids = {c.factory_id for c in cands}
    for e in examples:
        sr = ag.steer(e, ids, client)
        print(f"\nsteer: {e}\n  source {sr.meta.source if sr.meta else 'failed'}; accepted {sr.accepted}; "
              f"rejected {sr.rejected}; unsupported {sr.unsupported}")
    news = json.loads((C.ROOT / "data" / "demo" / "news.json").read_text(encoding="utf-8"))
    for n in news:
        nr = ag.parse_news(n["text"], client)
        ev = nr.event
        print(f"\nnews {n['id']} ({n['language']}): {nr.meta.source if nr.meta else 'failed'} -> area {nr.area} "
              f"<- {nr.area_span!r}; type {nr.disruption_type}; days {nr.duration.value} ({nr.duration.status}) "
              f"<- {nr.duration.source_span!r}; severity {nr.severity} <- {nr.severity_span!r}; conf {nr.confidence}"
              f"; notes {nr.notes}")
        if ev:
            r = op.assess_news(w, order, p, rec, ev)
            print(f"  event fill x{ev.fill_mult}, +{ev.extra_delay_days} d: current {rec.risk.p_meet_qty:.3f} -> "
                  f"{r['current_with_event'].p_meet_qty:.3f}; re-plan {r['new'].risk.p_meet_qty:.3f} "
                  f"(target met: {r['new_target_met']}), cost {r['new'].plan.cost_total - rec.plan.cost_total:+,.0f}")
    from bunon import analyst
    questions = (
        "If the Ashulia stoppage turns severe, what happens to our plan, and what should we do?",
        "Should we stop using F2184 and keep any district under 50%? Compare that with the current plan.",
        "If a nationwide hartal lasts 2 days, can any plan still reach our target?",
    )
    for q in questions:
        tb = analyst.Toolbox(w, order, p, comp, fs, C.TARGET_P_MEET)
        run = analyst.answer(q, tb, client)
        print(f"\nwhat-if: {q}\n  source {run.source}; errors {run.errors or 'none'}")
        for c in run.calls:
            print(f"  step {c.step} {c.call_id} {c.name}({c.args}) -> {'ok' if c.ok else 'ERR ' + c.error} {c.facts}")
        print("  answer:", run.answer_md)
    # The thin loop (SPEC.md D14): the simulated factories' offer replies and the daily update.
    from bunon import loop as lp
    from bunon import personas as ps
    from bunon import service as sv
    b = sv.compute_plans(w, order, sv.PlanSettings(), n_compliant)
    pf = ps.load()
    offers = []
    for r in lp.sourcing_list(b):
        for m in ps.messages_for(pf, r.factory_id, "offer_reply"):
            planned_price = next(c.unit_price_bdt for c in b.cands if c.factory_id == r.factory_id)
            res = ag.parse_reply(m.text, r.qty, client, planned_price=planned_price)
            qty, delay = sv.reply_terms(res)
            price = res.price.value if res.price.status == "ok" else None
            offers.append(lp.OfferTerms(r.factory_id, res.decision or "none", qty, delay, r.qty, price))
            print(f"loop offer {m.msg_id}: {res.meta.source if res.meta else 'failed'} -> {res.decision} "
                  f"(expected {m.expected.decision}); qty {qty} (expected {m.expected.qty}); "
                  f"price {price} (expected {m.expected.price_bdt_per_piece})")
    split, _ = lp.split_from_offers(w, b, offers)
    qty = {ln.factory_id: ln.qty for ln in split.recommended.plan.lines}
    for fid in qty:
        for m in ps.messages_for(pf, fid, "progress_update"):
            u = ag.parse_update(m.text, qty[fid], client)
            print(f"loop update {m.msg_id}: {u.meta.source if u.meta else 'failed'} -> {u.status}; done "
                  f"{u.pieces_done.value} (expected {m.expected.pieces_done}); delay {u.delay_days.value} "
                  f"(expected {m.expected.delay_days}); cause {u.cause.value!r}")
    print(f"\nlive calls made: {client.calls}")


if __name__ == "__main__":
    main()
