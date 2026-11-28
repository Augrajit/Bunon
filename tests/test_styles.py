"""Whole-style engine (SPEC.md D30): a style always goes whole to one factory; buffer = over-cut per style;
success = every style has good pieces >= (1 - tolerance) x its quantity."""
from __future__ import annotations

import pytest

from bunon import optimizer as op
from bunon import programme as pg
from bunon import config as C
from bunon.schemas import AllocationLine, AllocationPlan


@pytest.fixture(scope="module")
def setup():
    world = op.load_world()
    order = pg.as_programme(op.demo_order())
    params = op.default_params(order)
    cands = op.build_candidates(world, order, params)
    return world, order, params, cands


def test_programme_is_the_ten_anonymised_styles():
    styles = pg.load_styles()
    assert [s.style_id for s in styles] == [f"S{i:02d}" for i in range(1, 11)]
    assert sum(s.qty for s in styles) == 16_800
    assert all(set(s.ranges) <= {"4/6X", "7/16"} for s in styles)


def test_every_style_goes_whole_to_one_factory_within_the_limits(setup):
    world, order, params, cands = setup
    by_id = {c.factory_id: c for c in cands}
    for b in (0.0, 0.1):
        plan = op.solve_mip(cands, order.subcontract_qty, params.model_copy(update={"buffer_pct": b}))
        assert plan.status == "optimal"
        placed = [s for ln in plan.lines for s, _ in ln.styles]
        assert sorted(placed) == [s.style_id for s in order.styles]  # each style exactly once
        for ln in plan.lines:
            assert ln.qty == sum(q for _, q in ln.styles)
            assert ln.qty <= by_id[ln.factory_id].window_capacity_risk_adj
            assert ln.qty >= by_id[ln.factory_id].min_batch
        cut = dict(s for ln in plan.lines for s in ln.styles)
        assert all(cut[s.style_id] == op.cut_qty(s.qty, b) for s in order.styles)  # over-cut within the style
        assert len(plan.lines) <= params.max_factories
        unproven = sum(ln.qty for ln in plan.lines if not by_id[ln.factory_id].proven)
        assert unproven <= params.unproven_cap_pct * plan.total_allocated + 1e-6
        assert plan.style_min_good == op.style_min_good(params)


def test_locked_style_stays_and_baseline_assigns_whole_styles(setup):
    world, order, params, cands = setup
    plan = op.solve_mip(cands, order.subcontract_qty, params)
    by_id = {c.factory_id: c for c in cands}
    s, q = max((st for ln in plan.lines for st in ln.styles), key=lambda st: st[1])
    home = next(ln.factory_id for ln in plan.lines if s in dict(ln.styles))
    f = next(c.factory_id for c in sorted(cands, key=lambda c: c.factory_id)  # a proven factory outside the plan
             if c.proven and c.factory_id not in {ln.factory_id for ln in plan.lines}
             and c.window_capacity_risk_adj >= q and c.min_batch <= q)
    moved = op.solve_mip(cands, order.subcontract_qty, params.model_copy(update={"locked_styles": {s: f}}))
    assert moved.status == "optimal"
    assert s in dict(next(ln for ln in moved.lines if ln.factory_id == f).styles)
    base = op.solve_baseline(cands, order.subcontract_qty, params)
    assert base.status == "optimal"
    assert sorted(s for ln in base.lines for s, _ in ln.styles) == [s.style_id for s in order.styles]


def test_success_is_shipping_on_time_and_short_pieces_are_a_cost(setup):
    """D31: the programme succeeds when every factory ships by its date; short pieces and rejects are a cost.
    Pieces still fill a factory's styles in sequence, and one style's over-cut cannot cover another style."""
    world, order, params, cands = setup
    plan = op.solve_mip(cands, order.subcontract_qty, params.model_copy(update={"buffer_pct": 0.1}))
    risk = op.evaluate(plan, cands, world, True)
    assert plan.style_qty == dict(params.styles)
    assert risk.expected_short_pcs > 0 and risk.expected_reject_pcs > 0
    assert op.quality_cost_bdt(risk, params) == pytest.approx(
        risk.expected_short_pcs * params.short_piece_value_bdt
        - risk.expected_reject_pcs * C.REJECT_SALVAGE_BDT_PER_PIECE)
    # tolerance is no longer a gate: changing it leaves the chance as it is
    loose = plan.model_copy(update={"style_min_good": {k: 0 for k in plan.style_min_good}})
    assert op.evaluate(loose, cands, world, True).p_meet_qty == risk.p_meet_qty
    from bunon.simulation import _style_pieces
    import numpy as np
    ln = AllocationLine(factory_id="F0001", qty=2_200, unit_price_bdt=1, transport_bdt=0, risk_penalty_bdt=0, share=1,
                        styles=[("A", 1_200), ("B", 1_000)])
    p = AllocationPlan(plan_id="t", method="optimised", status="optimal", lines=[ln], target_qty=2_000,
                       total_allocated=2_200, buffer_qty=200, cost_production=0, cost_material=0, cost_transport=0,
                       cost_fixed=0, cost_buffer=0, cost_risk_penalty=0, cost_total=0,
                       style_qty={"A": 1_000, "B": 1_000})
    d = np.array([[2_200.0], [1_900.0]])
    z = np.zeros_like(d)
    short, rej = _style_pieces(p, d, z, z.astype(bool))
    assert short.tolist() == [0.0, 300.0] and rej.tolist() == [0.0, 0.0]  # A's 200 extra cannot cover B


def test_material_comes_from_the_delivery_orders_fabric_rates():
    from bunon import programme as pg
    s = next(x for x in pg.load_styles() if x.style_id == "S05")  # 7/16 only
    assert s.qty_by_range == {"7/16": 600}
    assert pg.fabric_kg_per_piece(s) == pytest.approx(1.40 / 12 * 1.02)
    assert pg.material_bdt_per_piece(s) == pytest.approx(1.40 / 12 * 1.02 * C.FABRIC_BDT_PER_KG + C.TRIMS_BDT_PER_PIECE)


def test_running_style_split_is_disabled():
    from bunon import config as C
    assert C.RESCUE_SPLIT_RUNNING_STYLE is False


# --- the order loop with whole styles ------------------------------------------------------------------------

@pytest.fixture(scope="module")
def loop_case(setup):
    from bunon import loop as lp
    from bunon import service as sv
    world, order, params, cands = setup
    b = sv.compute_plans(world, order, sv.PlanSettings(), n_compliant=442)
    reqs = lp.sourcing_list(b)
    offers = [lp.OfferTerms(r.factory_id, "accept", None, None, r.qty) for r in reqs]
    res, split = lp.split_from_offers(world, b, offers)
    pt = res.recommended
    big = pt.plan.lines[0]
    fc = lp.forecast_after_update(world, b, pt, split, big.factory_id, big.qty // 8, 3, lp.UPDATE_DAY)
    return world, b, reqs, pt, split, fc


def test_requests_name_whole_styles_and_accepters_keep_them(loop_case):
    from bunon import loop as lp
    world, b, reqs, pt, split, fc = loop_case
    plan_reqs = [r for r in reqs if r.role == "plan"]
    assert sorted(s for r in plan_reqs for s in r.styles) == [f"S{i:02d}" for i in range(1, 11)]
    text = lp.draft_request(plan_reqs[0], "English", b.order)
    assert all(s in text for s in plan_reqs[0].styles) and f"{plan_reqs[0].qty:,}" in text
    # every factory accepted: each keeps exactly the styles it was asked for
    assert {ln.factory_id: tuple(s for s, _ in ln.styles) for ln in pt.plan.lines} == \
        {r.factory_id: r.styles for r in plan_reqs}


def test_rescue_moves_only_unstarted_whole_styles(loop_case):
    from bunon import config as C
    from bunon import loop as lp
    world, b, reqs, pt, split, fc = loop_case
    unstarted = {s for s, _ in lp.unstarted_styles(pt, fc.factory_id, fc.pieces_done)}
    started = {s for s, _ in lp.styles_at(pt, fc.factory_id)} - unstarted
    assert started and unstarted  # a quarter done: the first style is running
    opts = {o.kind: o for o in lp.rescue_options(world, b, pt, split, fc, [], lp.UPDATE_DAY)}
    assert list(opts) == ["move", "surge", "accept_shortfall", "renegotiate_date", "split_running"]
    mv = opts["move"]
    assert mv.allowed and mv.replan is not None
    moved = {s for d in mv.replan.diff for s in d["styles_in"]}
    assert moved and moved <= unstarted  # never a running style, never another factory's style
    new_at = {ln.factory_id: {s for s, _ in ln.styles} for ln in mv.replan.new.plan.lines}
    assert started <= new_at[fc.factory_id]
    assert sorted(s for v in new_at.values() for s in v) == [f"S{i:02d}" for i in range(1, 11)]  # still whole
    assert mv.quality_cost_bdt < opts["accept_shortfall"].quality_cost_bdt  # moving saves short pieces
    assert not opts["split_running"].allowed and C.RESCUE_SPLIT_RUNNING_STYLE is False


def test_shipping_short_is_always_possible_and_renegotiation_names_the_days(loop_case):
    """D31: tolerance is not a gate; shipping short on the date is always an option, priced."""
    from bunon import loop as lp
    world, b, reqs, pt, split, fc = loop_case
    opts = {o.kind: o for o in lp.rescue_options(world, b, pt, split, fc, [], lp.UPDATE_DAY)}
    assert opts["accept_shortfall"].allowed and opts["accept_shortfall"].quality_cost_bdt > 0
    days = lp.renegotiate_days(fc)
    assert days == -(-fc.shortfall // int(fc.rate_seen)) or days == max(1, -(-fc.shortfall // fc.rate_seen))
    assert f"{days} more days" in opts["renegotiate_date"].note
    assert opts["renegotiate_date"].p_after >= fc.p_after


def test_published_demo_numbers():
    """The programme demo's numbers (SPEC.md D30, D31) as published in README.md, SPEC.md and VIDEO_NOTES.md. If
    this fails, the engine, the calibration or the fixture changed: update those documents (and re-warm the cache),
    never the other way round."""
    from bunon import calibrate as cal
    from bunon import loop as lp
    from bunon import personas as ps
    from bunon import service as sv
    assert C.DEMO_PROGRAMME and cal.load() == {"late_mult": 0.1828, "shock_mult": 1.0}
    world = op.load_world()
    b = sv.compute_plans(world, op.demo_order(), sv.PlanSettings(), n_compliant=442)
    assert b.order.subcontract_qty == 16_800 and b.rec.buffer_pct == 0.0
    assert b.rec.risk.p_meet_qty == pytest.approx(0.9212, abs=0.0005)
    assert b.comp["baseline"].risk.p_meet_qty == pytest.approx(0.900, abs=0.0005)  # the calibration anchor
    assert round(b.rec.plan.cost_total) == 1_765_173
    assert round(op.quality_cost_bdt(b.rec.risk, b.params)) == 233_571
    pf = ps.load()
    offers = []
    for r in lp.sourcing_list(b):
        m = ps.messages_for(pf, r.factory_id, "offer_reply")
        e = m[0].expected if m else None
        offers.append(lp.OfferTerms(r.factory_id, e.decision if e else "none", e.qty if e else None,
                                    e.delay_days if e else None, r.qty, e.price_bdt_per_piece if e else None))
    assert [r.factory_id for r in lp.sourcing_list(b)] == ["F0288", "F1558", "F2314"]
    res, cands = lp.split_from_offers(world, b, offers)
    pt = res.recommended
    assert pt.risk.p_meet_qty == pytest.approx(0.9212, abs=0.0005)
    assert {ln.factory_id: (ln.qty, [s for s, _ in ln.styles]) for ln in pt.plan.lines} == {
        "F0288": (10_080, ["S02", "S04", "S05", "S07", "S08", "S10"]), "F1558": (6_720, ["S01", "S03", "S06", "S09"])}
    fc = lp.forecast_from_updates(world, b, pt, cands, [lp.Update("F1558", 1_800, 1, lp.UPDATE_DAY, 42, 60)])[0]
    assert (fc.projected, fc.shortfall) == (5_400, 1_320) and round(fc.output_ratio, 2) == 0.64
    assert fc.p_after == pytest.approx(0.9555, abs=0.0005) and lp.warning_level(fc, b.settings.target) == "watch"
    excl = {ln.factory_id for ln in pt.plan.lines}
    assert lp.last_style_rescue_day(world, b, pt, fc, excl, today=lp.UPDATE_DAY) == (12, "F2829")
    opts = {o.kind: o for o in lp.rescue_options(world, b, pt, cands, fc, [], lp.UPDATE_DAY)}
    assert opts["move"].note == "S03, S09 to F2829 (+2,880 pcs)"
    assert opts["move"].p_after == pytest.approx(0.9182, abs=0.0005) and round(opts["move"].cost_delta_bdt) == 29_928
    assert round(opts["move"].quality_cost_bdt) == 191_070
    assert round(opts["accept_shortfall"].quality_cost_bdt) == 541_303 and opts["accept_shortfall"].allowed
    assert opts["surge"].note.startswith("F1558 +540") and round(opts["surge"].quality_cost_bdt) == 385_453
    assert "4 more days" in opts["renegotiate_date"].note
    assert opts["renegotiate_date"].p_after == pytest.approx(0.9777, abs=0.0005)
    # Day 14: F0288's late fabric; by then no outside factory can take whole unstarted styles any more.
    fcs = lp.forecast_from_updates(world, b, pt, cands, [lp.Update("F1558", 1_800, 1, lp.UPDATE_DAY, 42, 60),
                                                         lp.Update("F0288", 2_600, 2, lp.UPDATE_DAY2)])
    assert {f.factory_id: (f.projected, f.shortfall) for f in fcs} == {"F0288": (6_240, 3_840), "F1558": (5_400, 1_320)}
    assert lp.last_style_rescue_day(world, b, pt, fcs[0], excl, today=lp.UPDATE_DAY2) == (None, None)
    # New entrant: one trial style, priced, not chosen automatically
    assert [c.factory_id for c in sv.new_entrants(b)] == ["F0882"]
    sid, bp = sv.trial_style(world, b, "F0882")
    assert sid == "S10" and bp.risk.p_meet_qty == pytest.approx(0.8884, abs=0.0005)
    assert round(bp.plan.cost_total - b.rec.plan.cost_total) == 33_840


def test_programme_memo_templates_pass_their_own_checks():
    """D33: the fallback memos (initial and re-plan) satisfy the whole-style memo rules, so an offline cache miss
    never breaks the app (the 28 Nov sweep caught a re-plan memo that could not)."""
    from bunon import agent as ag
    from bunon import service as sv
    world = op.load_world()
    b = sv.compute_plans(world, op.demo_order(), sv.PlanSettings(), n_compliant=442)
    assert not ag.validate_memo(ag.template_memo("initial", b.facts), b.facts)
    rp, _ = sv.replan_after_decline(world, b)
    fs = ag.factsheet_for_replan(b.facts, rp)
    assert ag.memo_required(fs) == ["new.short_cost"]
    assert not ag.validate_memo(ag.template_memo("replan", fs), fs)
