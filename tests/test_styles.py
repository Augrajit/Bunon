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
