"""Allocation: MIP (PuLP + CBC), the naive baselines, buffer search and re-allocation (PLAN.md §5).

Hard rules that are never relaxed here: the deadline (inside each window) and
compliance (RSC or valid BSCI, in the pre-filter).
"""
from __future__ import annotations

import hashlib
import math
import time
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd
import pulp

from bunon import config as C
from bunon import reliability as rl
from bunon.data_gen import load_public
from bunon.schemas import (AllocationLine, AllocationPlan, BufferPoint, BundleRisk, Candidate,
                           OptimizerParams, OrderConstraints, PlanResult, ReliabilityForecast,
                           ReplanResult, ShockEvent, ShockParams)
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
                unproven_cap_pct=C.UNPROVEN_CAP_PCT, max_district_share=C.DISTRICT_CAP_SHARE, lot_size=C.LOT_SIZE,
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
    mat = C.MATERIAL_COST_BDT_PER_PIECE * total
    fixed = params.fixed_cost_per_factory_bdt * len(lines)
    buffer_qty = max(0, total - Q)
    avg_piece = (prod + trans + mat) / total if total else 0.0
    return AllocationPlan(
        plan_id=_plan_id(method, b, params, qty), method=method, status=status, lines=lines,
        target_qty=Q, total_allocated=total, buffer_qty=buffer_qty,
        cost_production=prod, cost_material=mat, cost_transport=trans, cost_fixed=fixed,
        cost_buffer=buffer_qty * (avg_piece - C.SURPLUS_SALVAGE_BDT_PER_PIECE),
        cost_risk_penalty=sum(ln.risk_penalty_bdt for ln in lines),
        cost_total=prod + mat + trans + fixed, **extra)


def solve_mip(cands: list[Candidate], Q: int, params: OptimizerParams, time_limit: int = 30,
              naive: bool = False) -> AllocationPlan:
    """min sum (CM price + material + transport + risk) x + F sum y, subject to PLAN.md §5 constraints 1-8.

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
    unit = {c.factory_id: c.unit_price_bdt + C.MATERIAL_COST_BDT_PER_PIECE + c.transport_bdt_per_piece
            + L * (1 - c.q_effective) for c in cands}
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
    if params.max_district_share is not None and not naive:                     # 9 district cap (SPEC.md D3)
        for d in sorted({c.district for c in cands}):
            members = [c.factory_id for c in cands if c.district == d]
            prob += (pulp.lpSum(x[i] for i in members)
                     <= params.max_district_share * pulp.lpSum(x.values())), f"district_{d}"
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
    if params.max_district_share is not None and not naive and total:
        per_d: dict[str, int] = {}
        for i, q in qty.items():
            per_d[by_id[i].district] = per_d.get(by_id[i].district, 0) + q
        binding += [f"district:{d}" for d, q in per_d.items() if params.max_district_share * total - q < lot]
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
    if params.max_district_share is not None:
        for d in sorted({c.district for c in cands}):
            prob += (pulp.lpSum(x[c.factory_id] for c in cands if c.district == d)
                     <= params.max_district_share * pulp.lpSum(x.values()))
    prob.solve(pulp.PULP_CBC_CMD(msg=0, timeLimit=30))
    return int(pulp.value(prob.objective) or 0)


def solve_baseline(cands: list[Candidate], Q: int, params: OptimizerParams, buffer_pct: float = 0.0) -> AllocationPlan:
    """Naive "declared capacity + lowest price": the cheapest plan that covers Q on paper.

    Same hard rules (compliance, deadline window, min batch, max factories) but trusts declared
    capacity and ignores reliability. Costs are reported with the real candidates' figures.
    """
    p = params.model_copy(update={"buffer_pct": buffer_pct, "missing_piece_cost_bdt": 0.0,
                                  "unproven_cap_pct": 1.0, "max_district_share": None, "locked_min_qty": {}})
    plan = solve_mip(cands, Q, p, naive=True)
    by_id = {c.factory_id: c for c in cands}
    qty = {ln.factory_id: ln.qty for ln in plan.lines}
    return make_plan(plan.method, qty, by_id, Q, buffer_pct, params, status=plan.status,
                     max_feasible_qty=plan.max_feasible_qty, solver_seconds=plan.solver_seconds)


# --- buffer search, comparison, re-allocation --------------------------------------

def evaluate(plan: AllocationPlan, cands: list[Candidate], world: World, shocks: bool,
             n_sims: int = C.N_SIMS, seed: int = C.SIM_SEED, partial_lots: bool = C.PARTIAL_LOTS_DEFAULT,
             events: list[ShockEvent] | None = None) -> BundleRisk:
    slack = {c.factory_id: c.slack_days for c in cands}
    dist = {c.factory_id: c.district for c in cands}
    win = {c.factory_id: c.window_days for c in cands} if partial_lots else None
    return simulate(plan, world.forecasts, slack, dist, world.shocks if shocks else None, n_sims, seed, win, events)


def search_buffer(world: World, order: OrderConstraints, params: OptimizerParams,
                  target_p: float = C.TARGET_P_MEET, grid=C.BUFFER_GRID, shocks: bool = True,
                  n_sims: int = C.N_SIMS, seed: int = C.SIM_SEED, partial_lots: bool = C.PARTIAL_LOTS_DEFAULT,
                  cands: list[Candidate] | None = None, events: list[ShockEvent] | None = None) -> PlanResult:
    """Solve for each buffer; recommend the cheapest plan with P(meet) >= target, else the most likely one."""
    cands = cands if cands is not None else build_candidates(world, order, params)
    Q = order.subcontract_qty
    frontier = []
    for b in grid:
        plan = solve_mip(cands, Q, params.model_copy(update={"buffer_pct": b}))
        risk = (evaluate(plan, cands, world, shocks, n_sims, seed, partial_lots, events)
                if plan.status == "optimal" else None)
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
                  partial_lots: bool = C.PARTIAL_LOTS_DEFAULT) -> dict[str, BufferPoint | PlanResult]:
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


def baseline_to_match(world: World, order: OrderConstraints, params: OptimizerParams, p_target: float,
                      step: float = 0.025, max_buffer: float = 0.60, shocks: bool = True,
                      partial_lots: bool = C.PARTIAL_LOTS_DEFAULT) -> BufferPoint | None:
    """Equal-reliability comparison: the smallest buffer at which the naive baseline reaches p_target.

    The search may go past our 25% ceiling, because the point is to price what the naive rule needs.
    Returns None if no buffer up to max_buffer gets there.
    """
    cands = build_candidates(world, order, params)
    Q = order.subcontract_qty
    for k in range(int(round(max_buffer / step)) + 1):
        b = round(k * step, 4)
        plan = solve_baseline(cands, Q, params, b)
        if plan.status != "optimal":
            return None
        risk = evaluate(plan, cands, world, shocks, partial_lots=partial_lots)
        if risk.p_meet_qty >= p_target:
            return BufferPoint(buffer_pct=b, plan=plan, risk=risk)
    return None


def district_shares(plan: AllocationPlan, cands: list[Candidate]) -> dict[str, float]:
    dist = {c.factory_id: c.district for c in cands}
    out: dict[str, float] = {}
    for ln in plan.lines:
        out[dist[ln.factory_id]] = out.get(dist[ln.factory_id], 0.0) + ln.share
    return dict(sorted(out.items(), key=lambda kv: -kv[1]))


def replan_exclude(world: World, order: OrderConstraints, params: OptimizerParams, old: BufferPoint,
                   excluded_id: str, reason: str = "declined", target_p: float = C.TARGET_P_MEET,
                   shocks: bool = True, partial_lots: bool = C.PARTIAL_LOTS_DEFAULT) -> ReplanResult:
    """Day 2 re-allocation: drop one factory and re-solve from scratch (locks come on Day 4)."""
    p2 = params.model_copy(update={"excluded_ids": [*params.excluded_ids, excluded_id]})
    new = search_buffer(world, order, p2, target_p, shocks=shocks, partial_lots=partial_lots).recommended
    old_q = {ln.factory_id: ln.qty for ln in old.plan.lines}
    new_q = {ln.factory_id: ln.qty for ln in new.plan.lines}
    diff = [{"factory_id": i, "old_qty": old_q.get(i, 0), "new_qty": new_q.get(i, 0)}
            for i in sorted(set(old_q) | set(new_q), key=lambda i: -max(old_q.get(i, 0), new_q.get(i, 0)))]
    return ReplanResult(excluded_id=excluded_id, reason=reason, old=old, new=new, diff=diff)


def replan_locked(world: World, order: OrderConstraints, params: OptimizerParams, old: BufferPoint,
                  excluded_id: str, days_elapsed: int = 3, reason: str | None = None,
                  target_p: float = C.TARGET_P_MEET, shocks: bool = True,
                  partial_lots: bool = C.PARTIAL_LOTS_DEFAULT, as_of: date = C.AS_OF) -> ReplanResult:
    """Day 4 re-allocation (PLAN.md §5): a factory declines `days_elapsed` days after the plan.

    - The decliner is excluded.
    - Every other factory in the old plan has confirmed: its quantity is locked as a lower bound
      (it may be asked for more, never less), and it keeps its original production window.
    - New factories can only start now: their window shrinks by `days_elapsed`.
    - The deadline does not move. Compliance is unchanged.
    """
    old_q = {ln.factory_id: ln.qty for ln in old.plan.lines}
    if excluded_id not in old_q:
        raise ValueError(f"{excluded_id} is not in the plan")
    locked = {f: q for f, q in old_q.items() if f != excluded_id}
    p2 = params.model_copy(update={"excluded_ids": [*params.excluded_ids, excluded_id], "locked_min_qty": locked})
    original = {c.factory_id: c for c in build_candidates(world, order, p2, as_of=as_of)}
    later = {c.factory_id: c for c in build_candidates(world, order, p2, as_of=as_of + timedelta(days=days_elapsed))}
    cands = [original[f] for f in locked if f in original] + [c for f, c in later.items() if f not in locked]
    new = search_buffer(world, order, p2, target_p, shocks=shocks, partial_lots=partial_lots, cands=cands).recommended
    new_q = {ln.factory_id: ln.qty for ln in new.plan.lines}
    diff = [{"factory_id": i, "old_qty": old_q.get(i, 0), "new_qty": new_q.get(i, 0)}
            for i in sorted(set(old_q) | set(new_q), key=lambda i: -max(old_q.get(i, 0), new_q.get(i, 0)))]
    return ReplanResult(excluded_id=excluded_id, reason=reason or f"declined on day {days_elapsed}",
                        old=old, new=new, diff=diff)


def replan_from_reply(world: World, order: OrderConstraints, params: OptimizerParams, old: BufferPoint,
                      factory_id: str, decision: str, qty: int | None = None, delay_days: int | None = None,
                      days_elapsed: int = 3, target_p: float = C.TARGET_P_MEET, shocks: bool = True,
                      partial_lots: bool = C.PARTIAL_LOTS_DEFAULT, as_of: date = C.AS_OF) -> ReplanResult | None:
    """Re-plan after a factory's reply (validated by agent.parse_reply).

    - reject  -> same as a decline: locked re-plan without the factory.
    - accept  -> nothing to change (returns None).
    - counter -> the factory stays in the plan with its counter terms LOCKED:
        * a quantity becomes its exact quantity (lower bound and cap),
        * a delay of d days reduces its slack by d (negative = later than the latest finish;
          only partial lots then count, via the simulation).
      Other confirmed factories keep their quantities as lower bounds; new factories start late.
    """
    if decision == "accept":
        return None
    if decision == "reject":
        return replan_locked(world, order, params, old, factory_id, days_elapsed,
                             f"rejected the order on day {days_elapsed}", target_p, shocks, partial_lots, as_of)
    old_q = {ln.factory_id: ln.qty for ln in old.plan.lines}
    if factory_id not in old_q:
        raise ValueError(f"{factory_id} is not in the plan")
    lot = params.lot_size
    agreed = (qty // lot * lot) if qty is not None else old_q[factory_id]
    if agreed <= 0:
        return replan_locked(world, order, params, old, factory_id, days_elapsed,
                             f"can supply nothing (day {days_elapsed})", target_p, shocks, partial_lots, as_of)
    locked = dict(old_q)
    locked[factory_id] = agreed
    p2 = params.model_copy(update={"locked_min_qty": locked})
    original = {c.factory_id: c for c in build_candidates(world, order, p2, as_of=as_of)}
    later = {c.factory_id: c for c in build_candidates(world, order, p2, as_of=as_of + timedelta(days=days_elapsed))}
    c = original[factory_id]
    fc = world.forecasts[factory_id]
    slack = c.slack_days - (delay_days or 0)
    q_eff = (fc.fill_mean * rl.p_on_time(fc, slack) if slack >= 0
             else fc.fill_mean * max(0.0, (c.window_days + slack) / c.window_days))
    changed = c.model_copy(update={"slack_days": slack, "window_capacity_risk_adj": agreed,
                                   "window_capacity_declared": agreed, "min_batch": min(c.min_batch, agreed),
                                   "q_effective": q_eff})
    cands = ([changed] + [original[f] for f in locked if f != factory_id and f in original]
             + [x for f, x in later.items() if f not in locked])
    new = search_buffer(world, order, p2, target_p, shocks=shocks, partial_lots=partial_lots, cands=cands).recommended
    new_q = {ln.factory_id: ln.qty for ln in new.plan.lines}
    diff = [{"factory_id": i, "old_qty": old_q.get(i, 0), "new_qty": new_q.get(i, 0)}
            for i in sorted(set(old_q) | set(new_q), key=lambda i: -max(old_q.get(i, 0), new_q.get(i, 0)))]
    terms = []
    if qty is not None and agreed != old_q[factory_id]:
        terms.append(f"can supply only {agreed:,} pieces")
    if delay_days:
        terms.append(f"will deliver {delay_days} days late")
    reason = "countered: " + (" and ".join(terms) if terms else "with conditions") + f" (day {days_elapsed})"
    return ReplanResult(excluded_id=factory_id, reason=reason, old=old, new=new, diff=diff)


def largest_line(point: BufferPoint) -> str:
    """Disruption rule (PLAN.md §8): the factory with the largest allocation declines."""
    return max(point.plan.lines, key=lambda ln: (ln.qty, ln.factory_id)).factory_id


# --- news-driven disruptions (SPEC.md D7) -------------------------------------------------------------

def event_from_severity(area: str, severity: str, duration_days: int | None, label: str = "") -> ShockEvent:
    """Severity label -> simulation parameters through the FIXED config table (the LLM never sets numbers)."""
    row = C.SEVERITY_TABLE[severity]
    days = duration_days if duration_days is not None else row["default_days"]
    delay = min(C.MAX_EVENT_DELAY_DAYS, round(days * row["lost_day_share"]))
    return ShockEvent(area=area, fill_mult=row["fill_mult"], extra_delay_days=int(delay), label=label)


def adjust_for_events(cands: list[Candidate], world: World, events: list[ShockEvent]) -> list[Candidate]:
    """What the optimiser should believe about affected factories: less output, less slack."""
    out = []
    for c in cands:
        hit = [e for e in events if e.area == "national" or e.area == c.district]
        if not hit:
            out.append(c)
            continue
        mult = 1.0
        delay = 0
        for e in hit:
            mult *= e.fill_mult
            delay += e.extra_delay_days
        fc = world.forecasts[c.factory_id]
        slack = c.slack_days - delay
        q = (fc.fill_mean * mult * rl.p_on_time(fc, slack) if slack >= 0
             else fc.fill_mean * mult * max(0.0, (c.window_days + slack) / c.window_days))
        cap = int(c.window_capacity_risk_adj * mult) // 100 * 100
        if cap < c.min_batch:
            continue  # cannot produce a minimum batch any more
        out.append(c.model_copy(update={"slack_days": slack, "window_capacity_risk_adj": cap, "q_effective": q}))
    return out


def assess_news(world: World, order: OrderConstraints, params: OptimizerParams, current: BufferPoint,
                event: ShockEvent, target_p: float = C.TARGET_P_MEET, shocks: bool = True,
                partial_lots: bool = C.PARTIAL_LOTS_DEFAULT) -> dict:
    """(1) the current plan's P(meet) if the event happens; (2) a plan made knowing about it
    (news arrives before purchase orders are sent, so no quantities are locked)."""
    cands = build_candidates(world, order, params)
    hit_now = evaluate(current.plan, cands, world, shocks, partial_lots=partial_lots, events=[event])
    adj = adjust_for_events(cands, world, [event])
    res = search_buffer(world, order, params, target_p, shocks=shocks, partial_lots=partial_lots,
                        cands=adj, events=[event])
    affected = [ln.factory_id for ln in current.plan.lines
                if event.area == "national" or next(c.district for c in cands if c.factory_id == ln.factory_id) == event.area]
    return {"event": event, "current_with_event": hit_now, "affected_in_plan": affected,
            "new": res.recommended, "new_target_met": res.target_met, "cands": adj}
