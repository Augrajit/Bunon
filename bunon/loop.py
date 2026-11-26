"""The order loop's engine steps (PLAN_V2 §5): sourcing list and request drafts, the split from offers,
the forecast from a daily update, the last day a rescue is possible, and rescue options.

Everything here is code: no LLM call and no number from an LLM. The texts drafted here are deterministic
templates whose numbers come from the engine; a human approves them before they are "sent" (in-app only).
LLM-written drafts come in a later milestone and will use the same placeholder rule as the memos.
"""
from __future__ import annotations

import dataclasses
import math
from dataclasses import dataclass
from datetime import date, timedelta

from bunon import config as C
from bunon import optimizer as op
from bunon import reliability as rl
from bunon.schemas import AllocationPlan, BufferPoint, Candidate, PlanResult, ReplanResult
from bunon.simulation import simulate

REPLY_DAY = 2      # factories answer the sourcing round
UPDATE_DAY = 12    # the scripted daily update that triggers the warning (production started on day 2 + setup)
QC_DAY = 26        # goods received and inspected at the exporter


def deadline_day(order) -> int:
    return (order.deadline - C.AS_OF).days


def ship_by_day(order, c: Candidate) -> int:
    """Last day a factory can finish and still reach the exporter's QC before the deadline."""
    return deadline_day(order) - C.QC_DAYS - c.transit_days


# --- sourcing ---------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class Request:
    factory_id: str
    qty: int
    finish_by: date
    role: str          # "plan" (in the recommended plan) or "standby" (best proven factory outside it)


def sourcing_list(b, n_standby: int = 1) -> list[Request]:
    """Ask every factory in the recommended plan for its planned quantity, plus the best proven factories
    outside the plan (by forecast deliverable pieces) as standbys for their forecast capacity."""
    by_id = {c.factory_id: c for c in b.cands}
    finish = lambda c: C.AS_OF + timedelta(days=ship_by_day(b.order, c) - c.slack_days)  # noqa: E731
    reqs = [Request(ln.factory_id, ln.qty, finish(by_id[ln.factory_id]), "plan")
            for ln in sorted(b.rec.plan.lines, key=lambda ln: (-ln.qty, ln.factory_id))]
    planned = {r.factory_id for r in reqs}
    spare = sorted((c for c in b.cands if c.factory_id not in planned and c.proven),
                   key=lambda c: (-(c.q_effective * c.window_capacity_risk_adj), c.factory_id))
    reqs += [Request(c.factory_id, c.window_capacity_risk_adj, finish(c), "standby") for c in spare[:n_standby]]
    return reqs


def draft_request(req: Request, language: str, order) -> str:
    """Deterministic request text; every number comes from the plan (code)."""
    qty, when = f"{req.qty:,}", f"{req.finish_by:%d %b %Y}"
    fabric = f"{order.gsm} GSM " if order.gsm else ""
    if language == "Banglish":
        return (f"Assalamu alaikum bhai. Amader ekta order ache: basic crew-neck T-shirt, {fabric}fabric amra dibo. "
                f"{qty} pcs, production shesh {when} er moddhe. Parben kina, ar rate koto, janaben.")
    if language == "Bangla":
        return (f"আসসালামু আলাইকুম। বেসিক ক্রু-নেক টি-শার্টের একটি অর্ডার: {qty} পিস, {fabric}কাপড় আমরা দেব, "
                f"উৎপাদন শেষ {when} এর মধ্যে। নিতে পারবেন কি না এবং রেট কত, জানাবেন।")
    return (f"Dear Sir, we would like to place a subcontract order: {qty} pcs basic crew-neck T-shirt, {fabric}fabric "
            f"supplied by us, production complete by {when}. Please confirm the quantity you can take and your rate "
            "per piece.")


def draft_rescue_request(factory_id: str, extra: int, order, language: str = "English") -> str:
    when = f"{order.deadline:%d %b %Y}"
    if language == "Banglish":
        return (f"Bhai, ekta urgent request: aro {extra:,} pcs basic T-shirt nite parben? Buyer deadline {when}, "
                "fabric amra dibo. Janaben please.")
    return (f"Dear Sir, an urgent request: could you take {extra:,} more pcs of the basic crew-neck T-shirt? The buyer's "
            f"deadline is {when}; fabric supplied by us. Please confirm.")


def draft_surge_request(factory_id: str, extra: int, total: int, order, language: str = "English") -> str:
    """Overtime request to the late factory itself; the numbers come from the forecast (code)."""
    when = f"{order.deadline:%d %b %Y}"
    if language == "Banglish":
        return (f"Bhai, line e overtime kore aro {extra:,} pcs ber korte parben? Tahole mot {total:,} pcs hobe, "
                f"buyer deadline {when}. Overtime er extra rate amra dibo. Janaben please.")
    if language == "Bangla":
        return (f"ওভারটাইম করে আরও {extra:,} পিস বের করতে পারবেন? তাহলে মোট {total:,} পিস হবে, বায়ারের শেষ তারিখ "
                f"{when}। ওভারটাইমের বাড়তি রেট আমরা দেব। জানাবেন।")
    return (f"Dear Sir, could your line add overtime to make {extra:,} more pcs, {total:,} pcs in all, before the "
            f"buyer's deadline of {when}? We will pay the overtime premium. Please confirm.")


# --- split from offers ---------------------------------------------------------------------------------------

@dataclass(frozen=True)
class OfferTerms:
    factory_id: str
    decision: str            # accept / counter / reject / none (no reply or unreadable)
    qty: int | None          # verified quantity from the reply, if any
    delay_days: int | None   # verified delay from the reply, if any (or from a ship date after the asked date)
    requested: int
    price: float | None = None  # verified rate per piece from the reply, if any (BDT)


def split_from_offers(world: op.World, b, offers: list[OfferTerms]) -> tuple[PlanResult, list[Candidate]]:
    """The best split over the factories that said yes: each capped at the pieces it offered (or the pieces we
    asked for, if it named no number), never above our forecast capacity; a stated delay cuts its slack."""
    by_id = {c.factory_id: c for c in b.cands}
    lot = b.params.lot_size
    cands = []
    for o in offers:
        if o.decision not in ("accept", "counter") or o.factory_id not in by_id:
            continue
        c = by_id[o.factory_id]
        offered = (o.qty if o.qty is not None else o.requested) // lot * lot
        cap = min(offered, c.window_capacity_risk_adj)
        if cap < lot:
            continue
        fc = world.forecasts[o.factory_id]
        slack = c.slack_days - (o.delay_days or 0)
        q_eff = (fc.fill_mean * rl.p_on_time(fc, slack) if slack >= 0
                 else fc.fill_mean * max(0.0, (c.window_days + slack) / c.window_days)) * fc.good_share
        upd = {"window_capacity_risk_adj": cap, "window_capacity_declared": cap,
               "min_batch": min(c.min_batch, cap), "slack_days": slack, "q_effective": q_eff}
        if o.price is not None:  # the factory's quoted rate replaces our synthetic rate for it
            upd["unit_price_bdt"] = float(o.price)
        cands.append(c.model_copy(update=upd))
    declined = [o.factory_id for o in offers if o.decision == "reject"]
    p = b.params.model_copy(update={"excluded_ids": sorted(set(b.params.excluded_ids) | set(declined))})
    res = op.search_buffer(world, b.order, p, b.settings.target, shocks=b.settings.shocks,
                           partial_lots=b.settings.partial, cands=cands)
    return res, cands


# --- forecast from a daily update ----------------------------------------------------------------------------

@dataclass(frozen=True)
class Forecast:
    factory_id: str
    planned: int
    pieces_done: int
    day: int
    rate_plan: float
    rate_seen: float
    projected: int            # pieces this factory finishes by its ship-by day at the rate seen, after the delay
    shortfall: int            # planned - projected (>= 0)
    p_before: float
    p_after: float            # P(order succeeds) with this factory's projection, others as forecast
    expected_good_after: float
    min_good: int
    days_left: int = 0        # production days left at the rate seen (after the stated delay)
    # other factories with an update, taken as certain at their projection too: (factory_id, projected, shortfall)
    others: tuple[tuple[str, int, int], ...] = ()

    @property
    def projections(self) -> dict[str, int]:
        """Every updated factory's projection (this one and the others), all taken as certain together."""
        return {self.factory_id: self.projected, **{f: q for f, q, _ in self.others}}

    @property
    def order_shortfall(self) -> int:
        """Pieces short across every updated factory."""
        return self.shortfall + sum(x for _, _, x in self.others)


def _certain(world: op.World, factory_ids) -> op.World:
    """The world with some factories' deliveries made certain (the projection from an update is what each makes);
    their QC model is kept, so only good pieces count. `factory_ids`: one id or several."""
    ids = [factory_ids] if isinstance(factory_ids, str) else list(factory_ids)
    upd = {f: world.forecasts[f].model_copy(update={
        "p_full": 1.0, "p_full_a": 1e9, "p_full_b": 1e-9, "p_late": 0.0, "p_catastrophe": 0.0}) for f in ids}
    return dataclasses.replace(world, forecasts={**world.forecasts, **upd})


@dataclass(frozen=True)
class Update:
    """One daily update as numbers (read from the text under the quote rule, or typed in the factory app)."""
    factory_id: str
    pieces_done: int
    delay_days: int | None
    day: int


def _project(world: op.World, b, point: BufferPoint, cands: list[Candidate], u: Update, start_day: int):
    c = next(x for x in cands if x.factory_id == u.factory_id)
    setup = int(world.factories.loc[world.factories["factory_id"] == u.factory_id, "setup_days"].iloc[0])
    planned = next(ln.qty for ln in point.plan.lines if ln.factory_id == u.factory_id)
    ship_by = ship_by_day(b.order, c)
    start = start_day + setup
    rate_seen = u.pieces_done / max(1, u.day - start)
    rate_plan = planned / max(1, ship_by - c.slack_days - start)
    days_left = max(0, ship_by - u.day - (u.delay_days or 0))
    projected = int(min(planned, u.pieces_done + rate_seen * days_left))
    return planned, rate_plan, rate_seen, projected, max(0, planned - projected), days_left


def latest_updates(updates: list[Update]) -> list[Update]:
    """The latest update per factory (a later day replaces an earlier one; on the same day the last one given)."""
    out: dict[str, Update] = {}
    for u in updates:
        if u.factory_id not in out or u.day >= out[u.factory_id].day:
            out[u.factory_id] = u
    return list(out.values())


def forecast_from_updates(world: op.World, b, point: BufferPoint, cands: list[Candidate], updates: list[Update],
                          start_day: int = REPLY_DAY) -> list[Forecast]:
    """Several factories' updates at once: each updated factory is projected from its latest update (see
    `forecast_after_update`), all those projections are taken as certain together and the order is re-simulated
    once. Returns one Forecast per updated factory in the plan, largest shortfall first; each carries the joint
    chance and the others' projections (`others`), so the rescue options are scored on the same footing."""
    in_plan = {ln.factory_id for ln in point.plan.lines}
    ups = [u for u in latest_updates(updates) if u.factory_id in in_plan]
    if not ups:
        return []
    proj = {u.factory_id: (u, _project(world, b, point, cands, u, start_day)) for u in ups}
    risk = _risk_with(world, b, point, cands, {f: v[1][3] for f, v in proj.items()})
    day = max(u.day for u in ups)
    out = []
    for f, (u, (planned, rate_plan, rate_seen, projected, shortfall, days_left)) in proj.items():
        others = tuple((g, v[1][3], v[1][4]) for g, v in sorted(proj.items()) if g != f)
        out.append(Forecast(f, planned, u.pieces_done, day, rate_plan, rate_seen, projected, shortfall,
                            point.risk.p_meet_qty, risk.p_meet_qty, risk.expected_good, risk.min_good_qty,
                            days_left, others))
    return sorted(out, key=lambda fc: (-fc.shortfall, fc.factory_id))


def forecast_after_update(world: op.World, b, point: BufferPoint, cands: list[Candidate], factory_id: str,
                          pieces_done: int, delay_days: int | None, day: int,
                          start_day: int = REPLY_DAY) -> Forecast:
    """Project the updated factory from what it has done so far. Production starts on the day the split was agreed
    (`start_day`) plus the factory's setup days; rate seen = pieces done / production days so far; it then loses
    the stated delay and keeps that rate until its ship-by day. Every other factory keeps its reliability
    forecast. The order's chance is re-simulated with this factory's projection taken as certain."""
    return forecast_from_updates(world, b, point, cands, [Update(factory_id, pieces_done, delay_days, day)],
                                 start_day)[0]


def _risk_with(world: op.World, b, point: BufferPoint, cands: list[Candidate], qtys: dict[str, int]):
    """Re-simulate the order with the given factories making exactly their `qtys` (taken as certain), the rest
    as forecast."""
    lines = [ln.model_copy(update={"qty": qtys[ln.factory_id]}) if ln.factory_id in qtys else ln
             for ln in point.plan.lines]
    plan: AllocationPlan = point.plan.model_copy(update={"lines": [ln for ln in lines if ln.qty > 0]})
    slack = {x.factory_id: x.slack_days for x in cands}
    slack.update({f: 10_000 for f in qtys})
    dist = {x.factory_id: x.district for x in cands}
    win = {x.factory_id: x.window_days for x in cands} if b.settings.partial else None
    w2 = _certain(world, qtys)
    return simulate(plan, w2.forecasts, slack, dist, world.shocks if b.settings.shocks else None, window_days=win)


def surge_pieces(fc: Forecast) -> int:
    """Extra pieces the late factory could make with overtime: SURGE_SHARE more a day at the rate seen, over the
    days it has left, never above the shortfall. A placeholder model (research ask 18)."""
    return int(min(fc.shortfall, fc.rate_seen * C.SURGE_SHARE * fc.days_left))


def last_rescue_day(world: op.World, b, shortfall: int, exclude: set[str],
                    today: int = 0) -> tuple[int | None, str | None]:
    """Latest day on which a factory outside the plan could still be asked to make the shortfall: its ship-by
    day minus its setup days minus the days its spare capacity needs. (None, None) if nobody can any more
    (that day is already past)."""
    if shortfall <= 0:
        return None, None
    best: tuple[int, str] | None = None
    for c in b.cands:
        if c.factory_id in exclude or c.window_days <= 0:
            continue
        daily = c.window_capacity_risk_adj / c.window_days
        if daily <= 0:
            continue
        setup = int(world.factories.loc[world.factories["factory_id"] == c.factory_id, "setup_days"].iloc[0])
        day = ship_by_day(b.order, c) - setup - math.ceil(shortfall / daily)
        if day >= today and (best is None or (day, c.factory_id) > (best[0], best[1])):
            best = (day, c.factory_id)
    return best if best else (None, None)


def chance_dropped(fc: Forecast, prev_p: float | None) -> bool:
    """The order's chance fell by at least WARN_DROP_PP (PLACEHOLDER) since the previous forecast from updates."""
    return prev_p is not None and prev_p - fc.p_after >= C.WARN_DROP_PP - 1e-12


def warning_level(fc: Forecast, target: float, prev_p: float | None = None) -> str:
    """The warning's level is set by code, never by the LLM: on track / watch / below target. `fc` may be one
    of several updated factories: the shortfall counted is the order's (all updated factories). `prev_p`: the
    order's chance at the previous forecast from updates (None on the first), so a drop between two updates
    is a warning even when nobody is short yet."""
    if fc.order_shortfall <= 0 and not chance_dropped(fc, prev_p):
        return "on track"
    return "below target" if fc.p_after < target else "watch"


# --- rescue options ------------------------------------------------------------------------------------------

@dataclass(frozen=True)
class RescueOption:
    kind: str                 # move / surge / accept_shortfall / escalate_buyer
    title: str
    p_after: float | None
    cost_delta_bdt: float | None
    replan: ReplanResult | None
    allowed: bool
    note: str


def rescue_move(world: op.World, b, point: BufferPoint, split_cands: list[Candidate], fc: Forecast,
                excluded: list[str], day: int) -> ReplanResult | None:
    """Move the shortfall, scored on the same footing as the forecast: the late factory is locked at what it can
    make (taken as certain); every other confirmed factory keeps at least its pieces and stays capped at what it
    OFFERED; factories outside the round can join but start on `day`. None if no plan covers the order."""
    lot = b.params.lot_size
    old_q = {ln.factory_id: ln.qty for ln in point.plan.lines}
    proj = fc.projections
    keep = fc.projected // lot * lot
    by_id = {c.factory_id: c for c in split_cands}
    cands, locked = [], {}
    for f, q in old_q.items():
        c = by_id[f]
        if f in proj:  # every updated factory is locked at what it can make (taken as certain)
            k = proj[f] // lot * lot
            if k < lot:
                continue
            cands.append(c.model_copy(update={"window_capacity_risk_adj": k, "window_capacity_declared": k,
                                              "min_batch": min(c.min_batch, k), "slack_days": 10_000,
                                              "q_effective": world.forecasts[f].good_share}))
            locked[f] = k
        else:
            cands.append(c)
            locked[f] = q
    excl = sorted(set(b.params.excluded_ids) | set(excluded))
    later = op.build_candidates(world, b.order, b.params.model_copy(update={"excluded_ids": excl}),
                                as_of=C.AS_OF + timedelta(days=day))
    cands += [c for c in later if c.factory_id not in old_q]
    p = b.params.model_copy(update={"locked_min_qty": locked, "excluded_ids": excl})
    new = op.search_buffer(_certain(world, proj), b.order, p, b.settings.target, shocks=b.settings.shocks,
                           partial_lots=b.settings.partial, cands=cands).recommended
    if new.risk is None:
        return None
    new_q = {ln.factory_id: ln.qty for ln in new.plan.lines}
    diff = [{"factory_id": i, "old_qty": old_q.get(i, 0), "new_qty": new_q.get(i, 0)}
            for i in sorted(set(old_q) | set(new_q), key=lambda i: (-max(old_q.get(i, 0), new_q.get(i, 0)), i))]
    return ReplanResult(excluded_id=fc.factory_id, reason=f"can make about {keep:,} pieces (update on day {day})",
                        old=point, new=new, diff=diff)


def rescue_options(world: op.World, b, point: BufferPoint, split_cands: list[Candidate], fc: Forecast,
                   excluded: list[str], day: int) -> list[RescueOption]:
    """(1) move the shortfall (`rescue_move`); (1b) overtime at the late factory (`surge_pieces`, placeholder
    model); (2) accept the shortfall, only if the expected good pieces still
    meet the tolerance; (3) tell the buyer early. All chances on the same footing: the late factory's projection
    taken as certain, everyone else as forecast. Numbers from the optimiser and simulation only."""
    out: list[RescueOption] = []
    rp = rescue_move(world, b, point, split_cands, fc, excluded, day)
    if rp is not None:
        moved = [d for d in rp.diff if d["new_qty"] > d["old_qty"] and d["factory_id"] not in fc.projections]
        out.append(RescueOption(
            "move", "Move the shortfall to other factories", rp.new.risk.p_meet_qty,
            rp.new.plan.cost_total - point.plan.cost_total, rp, True,
            ", ".join(f"{d['factory_id']} +{d['new_qty'] - d['old_qty']:,}" for d in moved) or "no change"))
    else:
        out.append(RescueOption("move", "Move the shortfall to other factories", None, None, None, False,
                                "no feasible re-plan under the current limits"))
    extra = surge_pieces(fc)
    if extra > 0:
        price = next(ln.unit_price_bdt for ln in point.plan.lines if ln.factory_id == fc.factory_id)
        risk = _risk_with(world, b, point, split_cands, {**fc.projections, fc.factory_id: fc.projected + extra})
        out.append(RescueOption("surge", "Overtime at the same factory", risk.p_meet_qty,
                                extra * price * (C.SURGE_PRICE_FACTOR - 1), None, True,
                                f"{fc.factory_id} +{extra:,} with overtime ({extra:,} of {fc.shortfall:,} short)"))
    else:
        out.append(RescueOption("surge", "Overtime at the same factory", None, None, None, False,
                                "no production days left for overtime"))
    ok = fc.expected_good_after >= fc.min_good
    out.append(RescueOption("accept_shortfall", "Accept the shortfall", fc.p_after, 0.0, None, ok,
                            "expected good pieces still meet the tolerance" if ok
                            else "expected good pieces fall below the tolerance"))
    out.append(RescueOption("escalate_buyer", "Tell the buyer now", None, None, None, True,
                            "always possible; the deadline and quantity are the buyer's to change"))
    return out
