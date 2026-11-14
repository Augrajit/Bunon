"""Allocation: MIP (PuLP + CBC), the naive baselines, buffer search and re-allocation (PLAN.md §5).

Hard rules that are never relaxed here: the deadline (inside each window) and
compliance (RSC or valid BSCI, in the pre-filter).
"""
from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass
from datetime import date
from pathlib import Path

import numpy as np
import pandas as pd
import pulp

from bunon import config as C
from bunon import reliability as rl
from bunon.data_gen import load_public
from bunon.schemas import (AllocationLine, AllocationPlan, BufferPoint, BundleRisk, Candidate,
                           OptimizerParams, OrderConstraints, PlanResult, ReliabilityForecast,
                           ReplanResult, ShockParams)
from bunon.simulation import simulate


# --- world: data + fitted model, loaded once ------------------------------------

@dataclass
class World:
    factories: pd.DataFrame                    # T-shirt factories joined with synthetic attributes
    forecasts: dict[str, ReliabilityForecast]
    shocks: ShockParams


def load_world(db: Path = C.PUBLIC_DB) -> World:
    d = load_public(db)
    f = d["factories"].merge(d["factory_synth"], on="factory_id")
    for col in ("tee_capable", "compliant", "rsc", "bsci_valid"):
        f[col] = f[col].astype(bool)
    ids = f["factory_id"].tolist()
    return World(factories=f, forecasts=rl.fit(d["history"], ids), shocks=rl.estimate_shocks(d["history"], f))


def demo_order() -> OrderConstraints:
    return OrderConstraints(
        total_qty=C.DEMO_TOTAL_QTY, own_capacity_qty=C.DEMO_OWN_CAPACITY, deadline=C.DEMO_DEADLINE,
        delivery_point=C.DELIVERY_POINT_NAME, fabric="100% cotton single jersey", gsm=160, quality_aql=2.5,
        fob_usd_per_piece=C.DEMO_FOB_USD, late_penalty_pct_per_week=C.DEMO_PENALTY_PCT_PER_WEEK,
    )


def missing_piece_cost_bdt(order: OrderConstraints) -> float:
    """Whole-order penalty spread over the subcontracted pieces (all-or-nothing heuristic, PLAN.md §5)."""
    if not order.fob_usd_per_piece or not order.late_penalty_pct_per_week or not order.subcontract_qty:
        return 0.0
    order_value_bdt = order.fob_usd_per_piece * C.USD_BDT * order.total_qty
    penalty = order_value_bdt * order.late_penalty_pct_per_week / 100 * C.WEEKS_AT_RISK
    return penalty / order.subcontract_qty


def default_params(order: OrderConstraints, **kw) -> OptimizerParams:
    base = dict(planned_slack_days=C.PLANNED_SLACK_DAYS, max_factories=C.MAX_FACTORIES,
                unproven_cap_pct=C.UNPROVEN_CAP_PCT, lot_size=C.LOT_SIZE,
                missing_piece_cost_bdt=missing_piece_cost_bdt(order),
                fixed_cost_per_factory_bdt=C.FIXED_COST_PER_FACTORY_BDT)
    base.update(kw)
    return OptimizerParams(**base)


# --- candidates -------------------------------------------------------------------

def haversine_km(lat, lng, lat0, lng0):
    lat, lng, lat0, lng0 = map(np.radians, (lat, lng, lat0, lng0))
    a = np.sin((lat - lat0) / 2) ** 2 + np.cos(lat) * np.cos(lat0) * np.sin((lng - lng0) / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(a))


def build_candidates(world: World, order: OrderConstraints, params: OptimizerParams,
                     as_of: date = C.AS_OF) -> list[Candidate]:
    f = world.factories
    f = f[f["tee_capable"] & f["compliant"] & (f["declared_spare_daily"] > 0)]
    f = f[~f["factory_id"].isin(params.excluded_ids)]
    road_km = haversine_km(f["lat"], f["lng"], *C.DELIVERY_POINT_LATLNG) * C.ROAD_CIRCUITY
    S = params.planned_slack_days
    out = []
    for row, km in zip(f.itertuples(index=False), road_km):
        transit = int(math.ceil(km / C.ROAD_KM_PER_DAY)) + 1
        latest_finish = (order.deadline - as_of).days - C.QC_DAYS - transit
        window = latest_finish - S - int(row.setup_days)
        if window <= 0:
            continue
        cap_declared = int(row.declared_spare_daily) * window
        if cap_declared < row.min_batch:
            continue
        fc = world.forecasts[row.factory_id]
        cap_risk = int(cap_declared * fc.fill_mean // params.lot_size * params.lot_size)
        out.append(Candidate(
            factory_id=row.factory_id, district=row.district,
            unit_price_bdt=float(row.unit_price_bdt),
            transport_bdt_per_piece=float(C.TRUCK_BDT_PER_KM * km / C.PCS_PER_TRUCK),
            transit_days=transit, slack_days=S, window_days=window,
            window_capacity_declared=cap_declared, window_capacity_risk_adj=cap_risk,
            min_batch=int(row.min_batch), proven=not fc.thin_history,
            q_effective=fc.fill_mean * rl.p_on_time(fc, S),
        ))
    return out


# --- plan construction ------------------------------------------------------------

def _plan_id(method: str, b: float, params: OptimizerParams, qty: dict[str, int]) -> str:
    h = hashlib.sha1(repr(sorted(qty.items())).encode()).hexdigest()[:6]
    return f"{method}-b{round(b * 1000)}-S{params.planned_slack_days}-{h}"


def make_plan(method: str, qty: dict[str, int], cands: dict[str, Candidate], Q: int, b: float,
              params: OptimizerParams, status: str = "optimal", **extra) -> AllocationPlan:
    qty = {k: v for k, v in qty.items() if v > 0}
    total = sum(qty.values())
    L = params.missing_piece_cost_bdt
    lines = [AllocationLine(
        factory_id=i, qty=q, unit_price_bdt=cands[i].unit_price_bdt,
        transport_bdt=cands[i].transport_bdt_per_piece * q,
        risk_penalty_bdt=L * (1 - cands[i].q_effective) * q,
        share=q / total if total else 0.0,
    ) for i, q in sorted(qty.items(), key=lambda kv: -kv[1])]
    prod = sum(ln.unit_price_bdt * ln.qty for ln in lines)
    trans = sum(ln.transport_bdt for ln in lines)
    fixed = params.fixed_cost_per_factory_bdt * len(lines)
    return AllocationPlan(
        plan_id=_plan_id(method, b, params, qty), method=method, status=status, lines=lines,
        target_qty=Q, total_allocated=total, buffer_qty=max(0, total - Q),
        cost_production=prod, cost_transport=trans, cost_fixed=fixed,
        cost_risk_penalty=sum(ln.risk_penalty_bdt for ln in lines),
        cost_total=prod + trans + fixed, **extra)


def solve_mip(cands: list[Candidate], Q: int, params: OptimizerParams, time_limit: int = 30,
              naive: bool = False) -> AllocationPlan:
    """min sum (price + transport + risk) x + F sum y, subject to PLAN.md §5 constraints 1-8.

    naive=True is the baseline: DECLARED capacity, no risk penalty, no unproven cap.
    It is the cheapest plan that covers the order on paper.
    """
    t0 = time.perf_counter()
    if naive:
        cands = [c.model_copy(update={"window_capacity_risk_adj": c.window_capacity_declared // params.lot_size
                                      * params.lot_size, "q_effective": 1.0, "proven": True}) for c in cands]
    by_id = {c.factory_id: c for c in cands}
    method = ("baseline_buffered" if params.buffer_pct > 0 else "baseline") if naive else "optimised"
    b, lot, L = params.buffer_pct, params.lot_size, params.missing_piece_cost_bdt
    target = (1 + b) * Q
    for fid in params.locked_min_qty:
        if fid not in by_id:
            raise ValueError(f"locked factory {fid} is not an eligible candidate")

    prob = pulp.LpProblem("allocation", pulp.LpMinimize)
    z = {c.factory_id: pulp.LpVariable(f"z_{c.factory_id}", 0, c.window_capacity_risk_adj // lot, cat="Integer")
         for c in cands}
    y = {c.factory_id: pulp.LpVariable(f"y_{c.factory_id}", cat="Binary") for c in cands}
    x = {i: lot * z[i] for i in z}
    unit = {c.factory_id: c.unit_price_bdt + c.transport_bdt_per_piece + L * (1 - c.q_effective) for c in cands}
    prob += pulp.lpSum(unit[i] * x[i] for i in x) + params.fixed_cost_per_factory_bdt * pulp.lpSum(y.values())

    for c in cands:
        i = c.factory_id
        prob += x[i] <= c.window_capacity_risk_adj * y[i], f"cap_{i}"            # 2 risk-adjusted capacity
        prob += x[i] >= c.min_batch * y[i], f"minbatch_{i}"                      # 3 minimum batch
        if i in params.locked_min_qty:                                            # 8 re-plan locks
            prob += x[i] >= params.locked_min_qty[i], f"lock_{i}"
    prob += pulp.lpSum(x.values()) >= target, "demand"                            # 1 demand + buffer
    prob += pulp.lpSum(y.values()) <= params.max_factories, "max_factories"        # 4
    prob += pulp.lpSum(y.values()) >= params.min_factories, "min_factories"
    unproven = [c.factory_id for c in cands if not c.proven]
    prob += pulp.lpSum(x[i] for i in unproven) <= params.unproven_cap_pct * target, "unproven_cap"  # 5
    # 6 deadline and 7 compliance are enforced by build_candidates (never relaxed).

    prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=time_limit))
    status = pulp.LpStatus[prob.status]
    secs = time.perf_counter() - t0
    if status != "Optimal":
        return make_plan(method, {}, by_id, Q, b, params,
                         status="infeasible" if status == "Infeasible" else "not_solved",
                         max_feasible_qty=max_feasible_qty(cands, params), solver_seconds=secs)

    qty = {i: int(round(pulp.value(x[i]))) for i in x}
    binding = []
    total = sum(qty.values())
    if total <= target + lot:
        binding.append("demand")
    if sum(1 for q in qty.values() if q > 0) == params.max_factories:
        binding.append("max_factories")
    if params.unproven_cap_pct * target - sum(qty[i] for i in unproven) < lot:
        binding.append("unproven_cap")
    binding += [f"capacity:{i}" for i, q in qty.items() if q > 0 and q >= by_id[i].window_capacity_risk_adj - lot]
    return make_plan(method, qty, by_id, Q, b, params, binding_constraints=binding, solver_seconds=secs)


def max_feasible_qty(cands: list[Candidate], params: OptimizerParams) -> int:
    """Largest quantity the hard constraints allow (shown when the order cannot be covered)."""
    lot = params.lot_size
    prob = pulp.LpProblem("max_qty", pulp.LpMaximize)
    z = {c.factory_id: pulp.LpVariable(f"z_{c.factory_id}", 0, c.window_capacity_risk_adj // lot, cat="Integer")
         for c in cands}
    y = {c.factory_id: pulp.LpVariable(f"y_{c.factory_id}", cat="Binary") for c in cands}
    x = {i: lot * z[i] for i in z}
    prob += pulp.lpSum(x.values())
    for c in cands:
        prob += x[c.factory_id] <= c.window_capacity_risk_adj * y[c.factory_id]
        prob += x[c.factory_id] >= c.min_batch * y[c.factory_id]
    prob += pulp.lpSum(y.values()) <= params.max_factories
    prob += pulp.lpSum(x[c.factory_id] for c in cands if not c.proven) <= params.unproven_cap_pct * pulp.lpSum(x.values())
    prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=30))
    return int(pulp.value(prob.objective) or 0)


def solve_baseline(cands: list[Candidate], Q: int, params: OptimizerParams, buffer_pct: float = 0.0) -> AllocationPlan:
    """Naive "declared capacity + lowest price": the cheapest plan that covers Q on paper.

    Same hard rules (compliance, deadline window, min batch, max factories) but trusts declared
    capacity and ignores reliability. Costs are reported with the real candidates' figures.
    """
    p = params.model_copy(update={"buffer_pct": buffer_pct, "missing_piece_cost_bdt": 0.0,
                                  "unproven_cap_pct": 1.0, "locked_min_qty": {}})
    plan = solve_mip(cands, Q, p, naive=True)
    by_id = {c.factory_id: c for c in cands}
    qty = {ln.factory_id: ln.qty for ln in plan.lines}
    return make_plan(plan.method, qty, by_id, Q, buffer_pct, params, status=plan.status,
                     max_feasible_qty=plan.max_feasible_qty, solver_seconds=plan.solver_seconds)


# --- buffer search, comparison, re-allocation --------------------------------------

def evaluate(plan: AllocationPlan, cands: list[Candidate], world: World, shocks: bool,
             n_sims: int = C.N_SIMS, seed: int = C.SIM_SEED, partial_lots: bool = False) -> BundleRisk:
    slack = {c.factory_id: c.slack_days for c in cands}
    dist = {c.factory_id: c.district for c in cands}
    win = {c.factory_id: c.window_days for c in cands} if partial_lots else None
    return simulate(plan, world.forecasts, slack, dist, world.shocks if shocks else None, n_sims, seed, win)


def search_buffer(world: World, order: OrderConstraints, params: OptimizerParams,
                  target_p: float = C.TARGET_P_MEET, grid=C.BUFFER_GRID, shocks: bool = True,
                  n_sims: int = C.N_SIMS, seed: int = C.SIM_SEED, partial_lots: bool = False) -> PlanResult:
    """Solve for each buffer; recommend the cheapest plan with P(meet) >= target, else the most likely one."""
    cands = build_candidates(world, order, params)
    Q = order.subcontract_qty
    frontier = []
    for b in grid:
        plan = solve_mip(cands, Q, params.model_copy(update={"buffer_pct": b}))
        risk = evaluate(plan, cands, world, shocks, n_sims, seed, partial_lots) if plan.status == "optimal" else None
        frontier.append(BufferPoint(buffer_pct=b, plan=plan, risk=risk))
    ok = [p for p in frontier if p.risk and p.risk.p_meet_qty >= target_p]
    if ok:
        rec, met = min(ok, key=lambda p: p.plan.cost_total), True
    else:
        solved = [p for p in frontier if p.risk]
        if not solved:
            return PlanResult(recommended=frontier[0], frontier=frontier, target_p=target_p, target_met=False)
        rec, met = max(solved, key=lambda p: (p.risk.p_meet_qty, -p.plan.cost_total)), False
    return PlanResult(recommended=rec, frontier=frontier, target_p=target_p, target_met=met)


def compare_three(world: World, order: OrderConstraints, params: OptimizerParams,
                  target_p: float = C.TARGET_P_MEET, shocks: bool = True,
                  partial_lots: bool = False) -> dict[str, BufferPoint | PlanResult]:
    """Baseline, baseline + our buffer, and ours, all scored by the same simulation (v2 change 1)."""
    cands = build_candidates(world, order, params)
    Q = order.subcontract_qty
    ours = search_buffer(world, order, params, target_p, shocks=shocks, partial_lots=partial_lots)
    b_star = ours.recommended.buffer_pct
    out: dict[str, BufferPoint | PlanResult] = {"ours": ours}
    for key, b in (("baseline", 0.0), ("baseline_buffered", b_star)):
        plan = solve_baseline(cands, Q, params, b)
        risk = evaluate(plan, cands, world, shocks, partial_lots=partial_lots) if plan.status == "optimal" else None
        out[key] = BufferPoint(buffer_pct=b, plan=plan, risk=risk)
    return out


def replan_exclude(world: World, order: OrderConstraints, params: OptimizerParams, old: BufferPoint,
                   excluded_id: str, reason: str = "declined", target_p: float = C.TARGET_P_MEET,
                   shocks: bool = True, partial_lots: bool = False) -> ReplanResult:
    """Day 2 re-allocation: drop one factory and re-solve from scratch (locks come on Day 4)."""
    p2 = params.model_copy(update={"excluded_ids": [*params.excluded_ids, excluded_id]})
    new = search_buffer(world, order, p2, target_p, shocks=shocks, partial_lots=partial_lots).recommended
    old_q = {ln.factory_id: ln.qty for ln in old.plan.lines}
    new_q = {ln.factory_id: ln.qty for ln in new.plan.lines}
    diff = [{"factory_id": i, "old_qty": old_q.get(i, 0), "new_qty": new_q.get(i, 0)}
            for i in sorted(set(old_q) | set(new_q), key=lambda i: -max(old_q.get(i, 0), new_q.get(i, 0)))]
    return ReplanResult(excluded_id=excluded_id, reason=reason, old=old, new=new, diff=diff)


def largest_line(point: BufferPoint) -> str:
    """Disruption rule (PLAN.md §8): the factory with the largest allocation declines."""
    return max(point.plan.lines, key=lambda ln: (ln.qty, ln.factory_id)).factory_id
