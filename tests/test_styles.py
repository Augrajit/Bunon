"""Whole-style engine (SPEC.md D30): a style always goes whole to one factory; buffer = over-cut per style;
success = every style has good pieces >= (1 - tolerance) x its quantity."""
from __future__ import annotations

import pytest

from bunon import optimizer as op
from bunon import programme as pg
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


def test_a_short_factory_leaves_its_last_style_short(setup):
    """Pieces fill a factory's styles in sequence, and one style's over-cut cannot cover another style."""
    world, order, params, cands = setup
    plan = op.solve_mip(cands, order.subcontract_qty, params.model_copy(update={"buffer_pct": 0.1}))
    risk = op.evaluate(plan, cands, world, True)
    # The same plan read as one undivided product (extra pieces anywhere count) looks far safer.
    pooled = op.evaluate(plan.model_copy(update={"style_min_good": {}}), cands, world, True)
    assert risk.p_meet_qty < pooled.p_meet_qty
    # One factory with two styles: delivering exactly the first style's pieces plus the second's threshold passes,
    # one piece fewer fails, whatever the first style's over-cut.
    from bunon.simulation import _styles_ok
    import numpy as np
    ln = AllocationLine(factory_id="F0001", qty=2_200, unit_price_bdt=1, transport_bdt=0, risk_penalty_bdt=0, share=1,
                        styles=[("A", 1_200), ("B", 1_000)])
    p = AllocationPlan(plan_id="t", method="optimised", status="optimal", lines=[ln], target_qty=2_000,
                       total_allocated=2_200, buffer_qty=200, cost_production=0, cost_material=0, cost_transport=0,
                       cost_fixed=0, cost_buffer=0, cost_risk_penalty=0, cost_total=0,
                       style_min_good={"A": 980, "B": 980})
    d = np.array([[1_200 + 980], [1_200 + 979], [980 + 1_000]], float)
    z = np.zeros_like(d)
    assert _styles_ok(p, d, z, z.astype(bool)).tolist() == [True, False, False]


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
    assert mv.p_after > fc.p_after
    assert not opts["split_running"].allowed and C.RESCUE_SPLIT_RUNNING_STYLE is False


def test_accept_shortfall_is_judged_per_style_and_renegotiation_names_the_days(loop_case):
    from bunon import loop as lp
    world, b, reqs, pt, split, fc = loop_case
    opts = {o.kind: o for o in lp.rescue_options(world, b, pt, split, fc, [], lp.UPDATE_DAY)}
    rows = lp.projected_by_style(pt, fc.projections)
    assert opts["accept_shortfall"].allowed == all(got >= need for _, _, got, need in rows)
    days = lp.renegotiate_days(fc)
    assert days == -(-fc.shortfall // int(fc.rate_seen)) or days == max(1, -(-fc.shortfall // fc.rate_seen))
    assert f"{days} more days" in opts["renegotiate_date"].note
    assert opts["renegotiate_date"].p_after > fc.p_after


def test_published_demo_numbers():
    """The programme demo's numbers (SPEC.md D30) as published in README.md, SPEC.md and the video notes. If this
    fails, the engine or the fixture changed: update those documents (and re-warm the cache), never the other way
    round."""
    from bunon import config as C
    from bunon import loop as lp
    from bunon import personas as ps
    from bunon import service as sv
    assert C.DEMO_PROGRAMME
    world = op.load_world()
    b = sv.compute_plans(world, op.demo_order(), sv.PlanSettings(), n_compliant=442)
    assert b.order.subcontract_qty == 16_800 and b.rec.buffer_pct == 0.2
    assert b.rec.risk.p_meet_qty == pytest.approx(0.320, abs=0.0005)
    assert round(b.rec.plan.cost_total) == 2_653_954
    pf = ps.load()
    offers = []
    for r in lp.sourcing_list(b):
        m = ps.messages_for(pf, r.factory_id, "offer_reply")
        e = m[0].expected if m else None
        offers.append(lp.OfferTerms(r.factory_id, e.decision if e else "none", e.qty if e else None,
                                    e.delay_days if e else None, r.qty, e.price_bdt_per_piece if e else None))
    res, cands = lp.split_from_offers(world, b, offers)
    pt = res.recommended
    assert pt.risk.p_meet_qty == pytest.approx(0.320, abs=0.0005)
    assert {ln.factory_id: (ln.qty, [s for s, _ in ln.styles]) for ln in pt.plan.lines} == {
        "F0288": (8_424, ["S01", "S02", "S04", "S10"]), "F2427": (8_280, ["S05", "S06", "S07", "S08"]),
        "F2786": (3_456, ["S03", "S09"])}
    fc = lp.forecast_from_updates(world, b, pt, cands, [lp.Update("F2427", 2_000, 1, lp.UPDATE_DAY)])[0]
    assert (fc.projected, fc.shortfall) == (6_000, 2_280) and fc.p_after == 0.0
    assert lp.warning_level(fc, b.settings.target) == "below target"
    excl = {ln.factory_id for ln in pt.plan.lines}
    assert lp.last_style_rescue_day(world, b, pt, fc, excl, today=lp.UPDATE_DAY) == (13, "F2829")
    opts = {o.kind: o for o in lp.rescue_options(world, b, pt, cands, fc, [], lp.UPDATE_DAY)}
    assert opts["move"].note == "S07 to F2829 (+2,304 pcs)"
    assert opts["move"].p_after == pytest.approx(0.431, abs=0.0005) and round(opts["move"].cost_delta_bdt) == 40_563
    assert opts["surge"].note.startswith("F2427 +600") and opts["surge"].p_after == 0.0
    assert not opts["accept_shortfall"].allowed
    assert "6 more days" in opts["renegotiate_date"].note
    assert opts["renegotiate_date"].p_after == pytest.approx(0.549, abs=0.0005)
    # Day 14: F0288's late fabric; by then no outside factory can take a whole unstarted style any more.
    fcs = lp.forecast_from_updates(world, b, pt, cands, [lp.Update("F2427", 2_000, 1, lp.UPDATE_DAY),
                                                         lp.Update("F0288", 2_600, 2, lp.UPDATE_DAY2)])
    assert {f.factory_id: (f.projected, f.shortfall) for f in fcs} == {"F2427": (6_000, 2_280), "F0288": (6_240, 2_184)}
    assert lp.last_style_rescue_day(world, b, pt, fcs[0], excl, today=lp.UPDATE_DAY2) == (None, None)
    day14 = {o.kind: o for o in lp.rescue_options(world, b, pt, cands, fcs[0], [], lp.UPDATE_DAY2)}
    assert not day14["move"].allowed
