"""SPEC.md D12: success counts good pieces against a tolerance; QC defects and whole-lot rejection;
compliance at the deadline; loss on failure is a manager input; optional safety margin."""
from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from bunon import config as C
from bunon import optimizer as op
from bunon import reliability as rl
from bunon.data_gen import load_public
from bunon.schemas import AllocationLine, AllocationPlan, ReliabilityForecast
from bunon.simulation import simulate

pytestmark = pytest.mark.skipif(not C.PUBLIC_DB.exists(), reason="run `python -m bunon.data_gen` first")


def _fc(fid, defect=None, lot=None):
    """A factory that always delivers in full and on time; QC as given (None = no QC model)."""
    qc = {}
    if defect is not None:
        conc = 1e6  # defect rate fixed at `defect`
        qc.update(qc_defect_mean=defect, qc_defect_a=max(defect * conc, 1e-9), qc_defect_b=(1 - defect) * conc)
    if lot is not None:
        qc.update(p_lot_reject=lot, p_lot_reject_a=max(lot * 1e6, 1e-9), p_lot_reject_b=(1 - lot) * 1e6)
    return ReliabilityForecast(
        factory_id=fid, n_jobs=50, thin_history=False, p_full=1.0, p_full_a=1e9, p_full_b=1e-9, fill_mu=8.0,
        fill_sd_param=0.0, fill_sd_noise=1e-6, fill_mean=1.0, fill_p10=1.0, fill_p50=1.0, fill_p90=1.0, p_late=0.0,
        late_days_shape=1.0, late_days_scale=1.0, p_catastrophe=0.0, accept_prob=1.0, model_version="t", **qc)


def _plan(qty: dict[str, int], target: int, min_good: int | None) -> AllocationPlan:
    tot = sum(qty.values())
    lines = [AllocationLine(factory_id=i, qty=q, unit_price_bdt=1, transport_bdt=0, risk_penalty_bdt=0,
                            share=q / tot) for i, q in qty.items()]
    return AllocationPlan(plan_id="t", method="optimised", status="optimal", lines=lines, target_qty=target,
                          min_good_qty=min_good, total_allocated=tot, buffer_qty=tot - target, cost_production=0,
                          cost_material=0, cost_transport=0, cost_buffer=0, cost_fixed=0, cost_risk_penalty=0,
                          cost_total=0)


def _sim(fc, plan):
    return simulate(plan, {"A": fc}, {"A": 0}, {"A": "x"}, n_sims=20_000, seed=5)


def test_only_good_pieces_count_against_the_tolerance():
    fc = _fc("A", defect=0.03, lot=0.0)          # 1,000 delivered -> 970 good, every time
    assert _sim(fc, _plan({"A": 1000}, 1000, 980)).p_meet_qty == 0.0   # 2% tolerance needs 980
    assert _sim(fc, _plan({"A": 1000}, 1000, 950)).p_meet_qty == 1.0   # 5% tolerance needs 950
    r = _sim(fc, _plan({"A": 1000}, 1000, 950))
    assert abs(r.expected_good - 970) < 1 and abs(r.expected_delivered - 1000) < 1 and r.min_good_qty == 950


def test_whole_lot_rejection_counts_as_zero_good_pieces():
    r = _sim(_fc("A", defect=0.0, lot=0.25), _plan({"A": 1000}, 1000, 900))
    assert abs(r.p_meet_qty - 0.75) < 0.02


def test_without_a_qc_model_every_delivered_piece_is_good():
    r = _sim(_fc("A"), _plan({"A": 1000}, 1000, None))
    assert r.p_meet_qty == 1.0 and r.min_good_qty == 1000


def test_tolerance_sets_the_plan_threshold():
    order = op.demo_order()
    p = op.default_params(order)
    assert p.tolerance_pct == C.DELIVERY_TOLERANCE_PCT
    cands = op.build_candidates(op.load_world(), order, p)
    plan = op.solve_mip(cands, order.subcontract_qty, p)
    assert plan.min_good_qty == int(np.ceil((1 - C.DELIVERY_TOLERANCE_PCT) * order.subcontract_qty))


def test_reliability_learns_qc_from_history():
    d = load_public()
    hist = d["history"]
    ids = sorted(d["factory_synth"]["factory_id"])
    fc = rl.fit(hist, ids)
    means = np.array([fc[i].qc_defect_mean for i in ids])
    lots = np.array([fc[i].p_lot_reject for i in ids])
    assert 0.005 < means.mean() < 0.05 and 0.005 < lots.mean() < 0.05
    assert all(fc[i].qc_defect_a and fc[i].p_lot_reject_a for i in ids)
    acc = hist[hist["accepted"].astype(bool)].copy()
    acc["rate"] = pd.to_numeric(acc["qc_rejected_qty"]) / pd.to_numeric(acc["delivered_qty"]).clip(lower=1)
    obs = acc.groupby("factory_id")["rate"].mean()
    many = acc.groupby("factory_id").size() >= 10
    sel = [i for i in obs.index if many.get(i, False)]
    corr = np.corrcoef([fc[i].qc_defect_mean for i in sel], obs[sel])[0, 1]
    assert corr > 0.5  # the model ranks factories' quality like their own record


def test_compliance_must_hold_at_the_deadline():
    w = op.load_world()
    f = w.factories
    bsci_only = f[f["tee_capable"].astype(bool) & ~f["rsc"].astype(bool) & f["bsci_valid"].astype(bool)
                  & (f["declared_spare_daily"] > 0)]
    expiry = pd.to_datetime(bsci_only["bsci_expiry"])
    order = op.demo_order()
    ids = {c.factory_id for c in op.build_candidates(w, order, op.default_params(order))}
    lapses = bsci_only.loc[expiry < pd.Timestamp(order.deadline), "factory_id"]
    assert not (set(lapses) & ids)  # a BSCI audit that lapses before the deadline does not count


def test_loss_on_failure_is_a_manager_input():
    order = op.demo_order()
    default = op.default_params(order).missing_piece_cost_bdt
    assert default == pytest.approx(C.DEFAULT_LOSS_ON_FAILURE_BDT / order.subcontract_qty)
    mine = order.model_copy(update={"loss_on_failure_bdt": 1_000_000.0})
    assert op.default_params(mine).missing_piece_cost_bdt == pytest.approx(1_000_000.0 / order.subcontract_qty)
    # the email's penalty clause no longer sets it
    other_penalty = order.model_copy(update={"late_penalty_pct_per_week": 50.0})
    assert op.default_params(other_penalty).missing_piece_cost_bdt == pytest.approx(default)


def test_safety_margin_is_a_floor_on_the_buffer():
    w, order = op.load_world(), op.demo_order()
    p = op.default_params(order, min_buffer_pct=0.10)
    res = op.search_buffer(w, order, p, n_sims=2_000)
    assert all(pt.buffer_pct >= 0.10 for pt in res.frontier)
    assert op.default_params(order).min_buffer_pct == C.SAFETY_MARGIN_DEFAULT_PCT == 0.0


def test_demo_order_is_in_january_2027():
    order = op.demo_order()
    assert C.AS_OF == date(2027, 1, 3) and order.deadline == date(2027, 1, 31)
    assert C.DATA_AS_OF < C.AS_OF


def test_event_replans_are_scored_like_the_current_plan():
    """SPEC.md D12 fix: a plan made knowing about an event is scored with the original slack and the event
    applied once (it used to count the delay twice), and the app is told when keeping the plan is better."""
    w, order = op.load_world(), op.demo_order()
    p = op.default_params(order)
    rec = op.compare_three(w, order, p)["ours"].recommended
    cands = op.build_candidates(w, order, p)
    ev = op.event_from_severity("gazipur", "high", None)
    r = op.assess_news(w, order, p, rec, ev)
    again = op.evaluate(r["new"].plan, cands, w, True, events=[ev])
    assert r["new"].risk.p_meet_qty == pytest.approx(again.p_meet_qty)
    assert r["keep_current_is_better"] == (r["current_with_event"].p_meet_qty >= r["new"].risk.p_meet_qty)


def test_event_cuts_capacity_only_for_days_lost_beyond_slack():
    w, order = op.load_world(), op.demo_order()
    p = op.default_params(order)
    cands = op.build_candidates(w, order, p)
    mild = op.adjust_for_events(cands, w, [op.event_from_severity("national", "low", 2)])  # 1 day < slack
    before = {c.factory_id: c for c in cands}
    assert mild and all(m.window_capacity_risk_adj == before[m.factory_id].window_capacity_risk_adj for m in mild)
    assert all(m.q_effective < before[m.factory_id].q_effective for m in mild)


def test_replan_diff_order_does_not_depend_on_the_hash_seed():
    """Reviewer finding (D12): two factories with equal quantities were ordered by set iteration, which
    changes with PYTHONHASHSEED, so the memo prompt (and its cache key) changed between server restarts."""
    import subprocess
    import sys
    code = ("from bunon import optimizer as op; w=op.load_world(); o=op.demo_order(); p=op.default_params(o); "
            "r=op.compare_three(w,o,p)['ours'].recommended; "
            "rp=op.replan_locked(w,o,p,r,op.largest_line(r),days_elapsed=3); print([d['factory_id'] for d in rp.diff])")
    outs = set()
    for seed in ("1", "2", "5"):
        env = dict(__import__("os").environ, PYTHONHASHSEED=seed)
        outs.add(subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, env=env,
                                cwd=str(C.ROOT), check=True).stdout)
    assert len(outs) == 1
