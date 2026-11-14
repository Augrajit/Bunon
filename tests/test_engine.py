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
    return op.demo_order()


# --- reliability --------------------------------------------------------------

def test_gamma_cdf_matches_exponential():
    for x in (0.5, 2.0, 7.0):
        assert math.isclose(rl.gamma_cdf(x, 1.0, 3.0), 1 - math.exp(-x / 3.0), rel_tol=1e-9)
    assert rl.gamma_cdf(0, 1.5, 2) == 0.0


def test_thin_history_has_wider_intervals(world):
    fc = list(world.forecasts.values())
    w_thin = np.mean([f.fill_p90 - f.fill_p10 for f in fc if f.thin_history])
    w_thick = np.mean([f.fill_p90 - f.fill_p10 for f in fc if f.n_jobs >= 15])
    assert w_thin > w_thick


def test_no_history_gets_the_prior(world):
    from bunon.data_gen import load_public
    h = load_public()["history"]
    fc = rl.fit(h, ["F9999"])["F9999"]
    assert fc.n_jobs == 0 and fc.thin_history
    assert 0.5 < fc.fill_mean < 0.95


def test_holdout_calibration(world):
    from bunon.data_gen import load_public
    d = load_public()
    e = rl.evaluate(d["history"], d["factory_synth"]["factory_id"].tolist())
    assert 0.72 <= e.coverage_p10_p90 <= 0.88      # target 80%
    assert e.mae_model < e.mae_naive
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
        factory_id=fid, n_jobs=50, thin_history=False, fill_mu=mu, fill_sd_param=0.0, fill_sd_noise=1e-6,
        fill_mean=0.99, fill_p10=0.99, fill_p50=0.99, fill_p90=0.99, p_late=p_late,
        late_days_shape=1.0, late_days_scale=100.0, p_catastrophe=p_cat, accept_prob=1.0, model_version="t")


def _plan(qty: dict[str, int], target: int) -> AllocationPlan:
    tot = sum(qty.values())
    lines = [AllocationLine(factory_id=i, qty=q, unit_price_bdt=1, transport_bdt=0, risk_penalty_bdt=0, share=q / tot)
             for i, q in qty.items()]
    return AllocationPlan(plan_id="t", method="optimised", status="optimal", lines=lines, target_qty=target,
                          total_allocated=tot, buffer_qty=tot - target, cost_production=0, cost_transport=0,
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
