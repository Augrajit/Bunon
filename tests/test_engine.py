"""Tests for reliability, optimizer and simulation (PLAN.md §4-§6)."""
from __future__ import annotations

import math

import numpy as np
import pytest

from bunon import config as C
from bunon import optimizer as op
from bunon import reliability as rl
from bunon.schemas import AllocationLine, AllocationPlan, Candidate, ReliabilityForecast
from bunon.simulation import simulate

pytestmark = pytest.mark.skipif(not C.PUBLIC_DB.exists(), reason="run `python -m bunon.data_gen` first")


@pytest.fixture(scope="module")
def world():
    return op.load_world()


@pytest.fixture(scope="module")
def order():
    return op.piece_demo_order()


# --- reliability --------------------------------------------------------------

def test_gamma_cdf_matches_exponential():
    for x in (0.5, 2.0, 7.0):
        assert math.isclose(rl.gamma_cdf(x, 1.0, 3.0), 1 - math.exp(-x / 3.0), rel_tol=1e-9)
    assert rl.gamma_cdf(0, 1.5, 2) == 0.0


def test_thin_history_has_more_uncertainty(world):
    """Wider uncertainty for thin history = wider posteriors (p_full and short-fill mean)."""
    def beta_sd(f):
        a, b = f.p_full_a, f.p_full_b
        return np.sqrt(a * b / ((a + b) ** 2 * (a + b + 1)))
    fc = list(world.forecasts.values())
    thin = [f for f in fc if f.thin_history]
    thick = [f for f in fc if f.n_jobs >= 15]
    assert np.mean([beta_sd(f) for f in thin]) > np.mean([beta_sd(f) for f in thick])
    assert np.mean([f.fill_sd_param for f in thin]) > np.mean([f.fill_sd_param for f in thick])


def test_no_history_gets_the_prior(world):
    from bunon.data_gen import load_public
    h = load_public()["history"]
    fc = rl.fit(h, ["F9999"])["F9999"]
    assert fc.n_jobs == 0 and fc.thin_history
    assert 0.5 < fc.fill_mean < 0.99


def test_holdout_calibration(world):
    from bunon.data_gen import load_public
    d = load_public()
    e = rl.evaluate(d["history"], d["factory_synth"]["factory_id"].tolist())
    assert 0.05 <= e.below_p10_rate <= 0.15          # target 10% below p10
    assert abs(e.full_rate_pred - e.full_rate_obs) < 0.05
    assert e.rmse_model < e.rmse_naive
    assert e.brier_late_model < e.brier_late_naive


# --- optimizer: hard constraints --------------------------------------------------

def test_candidates_respect_compliance_and_deadline(world, order):
    p = op.default_params(order)
    cands = op.build_candidates(world, order, p)
    f = world.factories.set_index("factory_id")
    assert cands
    for c in cands:
        assert f.loc[c.factory_id, "rsc"] or f.loc[c.factory_id, "bsci_valid"]
        assert c.window_days > 0
        assert c.window_capacity_declared >= c.min_batch
        assert c.slack_days == p.planned_slack_days


@pytest.mark.parametrize("b", [0.0, 0.2])
def test_mip_respects_all_constraints(world, order, b):
    p = op.default_params(order, buffer_pct=b)
    cands = {c.factory_id: c for c in op.build_candidates(world, order, p)}
    plan = op.solve_mip(list(cands.values()), order.subcontract_qty, p)
    assert plan.status == "optimal"
    target = (1 + b) * order.subcontract_qty
    assert plan.total_allocated >= target
    assert 1 <= len(plan.lines) <= p.max_factories
    unproven = sum(ln.qty for ln in plan.lines if not cands[ln.factory_id].proven)
    assert unproven <= p.unproven_cap_pct * target + 1e-6
    for ln in plan.lines:
        c = cands[ln.factory_id]
        assert c.min_batch <= ln.qty <= c.window_capacity_risk_adj
        assert ln.qty % p.lot_size == 0
    for share in op.district_shares(plan, list(cands.values())).values():
        assert share <= p.max_district_share + 1e-9


def test_infeasible_order_reports_max_quantity(world, order):
    big = order.model_copy(update={"total_qty": order.own_capacity_qty + 500_000})
    p = op.default_params(big)
    plan = op.solve_mip(op.build_candidates(world, big, p), big.subcontract_qty, p)
    assert plan.status == "infeasible"
    assert plan.lines == []
    assert 0 < plan.max_feasible_qty < 500_000


def test_baseline_trusts_declared_capacity_and_is_cheaper(world, order):
    p = op.default_params(order)
    cands = op.build_candidates(world, order, p)
    by_id = {c.factory_id: c for c in cands}
    base = op.solve_baseline(cands, order.subcontract_qty, p)
    ours = op.solve_mip(cands, order.subcontract_qty, p)
    assert base.status == "optimal" and base.method == "baseline"
    assert base.total_allocated >= order.subcontract_qty
    assert all(ln.qty <= by_id[ln.factory_id].window_capacity_declared for ln in base.lines)
    assert base.cost_total <= ours.cost_total


def test_locked_replan_keeps_confirmed_quantities(world, order):
    p = op.default_params(order)
    rec = op.search_buffer(world, order, p, n_sims=2_000).recommended
    gone = op.largest_line(rec)
    rp = op.replan_locked(world, order, p, rec, gone, days_elapsed=3)
    old = {ln.factory_id: ln.qty for ln in rec.plan.lines}
    new = {ln.factory_id: ln.qty for ln in rp.new.plan.lines}
    assert gone not in new
    for f, q in old.items():
        if f != gone:
            assert new.get(f, 0) >= q            # confirmed factories never cut
    assert rp.new.plan.total_allocated >= order.subcontract_qty
    assert rp.reason == "declined on day 3"


def test_late_start_shrinks_new_factories_window(world, order):
    from datetime import timedelta
    p = op.default_params(order)
    now = {c.factory_id: c for c in op.build_candidates(world, order, p)}
    later = {c.factory_id: c for c in op.build_candidates(world, order, p, as_of=C.AS_OF + timedelta(days=3))}
    common = set(now) & set(later)
    assert common and all(later[f].window_days == now[f].window_days - 3 for f in common)


def test_replan_excludes_the_decliner(world, order):
    p = op.default_params(order)
    rec = op.search_buffer(world, order, p, n_sims=2_000).recommended
    gone = op.largest_line(rec)
    rp = op.replan_exclude(world, order, p, rec, gone)
    assert gone not in {ln.factory_id for ln in rp.new.plan.lines}
    assert rp.new.plan.total_allocated >= order.subcontract_qty
    assert any(d["factory_id"] == gone and d["new_qty"] == 0 for d in rp.diff)


# --- simulation -----------------------------------------------------------------------

def _fc(fid, mu=8.0, p_late=0.0, p_cat=0.0):
    return ReliabilityForecast(
        factory_id=fid, n_jobs=50, thin_history=False, p_full=0.999, p_full_a=1e4, p_full_b=1.0, fill_mu=mu, fill_sd_param=0.0, fill_sd_noise=1e-6,
        fill_mean=0.99, fill_p10=0.99, fill_p50=0.99, fill_p90=0.99, p_late=p_late,
        late_days_shape=1.0, late_days_scale=100.0, p_catastrophe=p_cat, accept_prob=1.0, model_version="t")


def _plan(qty: dict[str, int], target: int) -> AllocationPlan:
    tot = sum(qty.values())
    lines = [AllocationLine(factory_id=i, qty=q, unit_price_bdt=1, transport_bdt=0, risk_penalty_bdt=0, share=q / tot)
             for i, q in qty.items()]
    return AllocationPlan(plan_id="t", method="optimised", status="optimal", lines=lines, target_qty=target,
                          total_allocated=tot, buffer_qty=tot - target, cost_production=0, cost_material=0, cost_transport=0, cost_buffer=0,
                          cost_fixed=0, cost_risk_penalty=0, cost_total=0)


def test_perfect_factory_always_meets():
    r = simulate(_plan({"A": 1000}, 990), {"A": _fc("A")}, {"A": 0}, {"A": "x"}, n_sims=2000, seed=1)
    assert r.p_meet_qty == 1.0


def test_two_independent_factories_match_analytic():
    # Each needed (target > either alone); each late w.p. 0.3 with a huge delay -> on time 0.7 each.
    fc = {"A": _fc("A", p_late=0.3), "B": _fc("B", p_late=0.3)}
    r = simulate(_plan({"A": 600, "B": 600}, 1100), fc, {"A": 0, "B": 0}, {"A": "x", "B": "y"},
                 n_sims=40_000, seed=3)
    assert abs(r.p_meet_qty - 0.49) < 3 * r.p_meet_se + 0.005


def test_simulation_is_seeded():
    fc = {"A": _fc("A", p_late=0.3)}
    a = simulate(_plan({"A": 1000}, 900), fc, {"A": 0}, {"A": "x"}, n_sims=5000, seed=11)
    b = simulate(_plan({"A": 1000}, 900), fc, {"A": 0}, {"A": "x"}, n_sims=5000, seed=11)
    assert a == b


def test_partial_lots_never_lower_than_all_or_nothing(world, order):
    p = op.default_params(order, buffer_pct=0.2)
    cands = op.build_candidates(world, order, p)
    plan = op.solve_mip(cands, order.subcontract_qty, p)
    strict = op.evaluate(plan, cands, world, shocks=True, n_sims=5000)
    partial = op.evaluate(plan, cands, world, shocks=True, n_sims=5000, partial_lots=True)
    assert partial.p_meet_qty >= strict.p_meet_qty


def test_replan_can_be_infeasible_and_says_so():
    """Seen live 18 Nov (then: max 3 factories + 10% unproven cap, decline on day 3). Since SPEC.md D12 the
    January order has a shorter window, so the case is now: max 4 factories, the largest factory declines
    on day 12. No plan covers the order; the result must carry no risk (the app shows 'No feasible
    re-plan' instead of crashing)."""
    w = op.load_world()
    order = op.piece_demo_order()
    p = op.default_params(order, max_factories=4)
    rec = op.compare_three(w, order, p)["ours"].recommended
    assert rec.plan.lines, "the initial plan must exist for this test to mean anything"
    rp = op.replan_locked(w, order, p, rec, op.largest_line(rec), days_elapsed=12)
    assert rp.new.risk is None and rp.new.plan.status != "optimal" and not rp.new.plan.lines
