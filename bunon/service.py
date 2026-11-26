"""Order logic for the app, with NO Streamlit import (PLAN_V2 decision C).

The UI (app.py) only renders and collects clicks; every order action and every engine call it makes goes
through here, so a web or WhatsApp front end could later drive the same functions.

- `PlanSettings` + `compute_plans`: the planning settings and the three-plan comparison (pure, cacheable).
- `replan_after_decline`, `replan_after_reply`, `assess_news`, `analyst_toolbox`: engine calls for the
  follow-up steps, keyed on a computed `PlanBundle`.
- `OrderBook`: the order's state and timeline in the per-session `Store` (story clock, allocations,
  approvals). Every approval or rejection is also written to the decision log (`decisions.py`).
- `factory_record`: a factory's own track record from the SYNTHETIC history (labelled so).
Nothing here sends anything to a factory.
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Literal

import pandas as pd
from pydantic import BaseModel, ConfigDict

from bunon import agent as ag
from bunon import analyst
from bunon import config as C
from bunon import decisions as dl
from bunon import optimizer as op
from bunon import reliability as rl
from bunon.schemas import BufferPoint, Candidate, OptimizerParams, OrderConstraints, ReplanResult
from bunon.store import EXPORTER_ROLES, Store

DECLINE_DAY = 3  # the scripted decline and the demo replies arrive on story day 3


# --- planning settings and the three-plan comparison ---------------------------------------------------

class PlanSettings(BaseModel):
    """The manager's planning settings (sidebar). Frozen and hashable, so the UI can cache on them."""
    model_config = ConfigDict(frozen=True, extra="forbid")
    slack: int = C.PLANNED_SLACK_DAYS
    target: float = C.TARGET_P_MEET
    unproven: float = C.UNPROVEN_CAP_PCT
    dcap: float | None = C.DISTRICT_CAP_SHARE          # None = no district cap
    partial: bool = C.PARTIAL_LOTS_DEFAULT
    shocks: bool = True
    max_f: int = C.MAX_FACTORIES
    excluded: tuple[str, ...] = ()
    tol: float = C.DELIVERY_TOLERANCE_PCT
    margin: float = C.SAFETY_MARGIN_DEFAULT_PCT
    loss: float = C.DEFAULT_LOSS_ON_FAILURE_BDT

    def apply(self, order: OrderConstraints) -> tuple[OrderConstraints, OptimizerParams]:
        order = order.model_copy(update={"loss_on_failure_bdt": self.loss})
        p = op.default_params(order, planned_slack_days=self.slack, unproven_cap_pct=self.unproven,
                              max_district_share=self.dcap, max_factories=self.max_f,
                              excluded_ids=list(self.excluded), tolerance_pct=self.tol, min_buffer_pct=self.margin)
        return order, p


@dataclass
class PlanBundle:
    order: OrderConstraints
    settings: PlanSettings
    params: OptimizerParams
    cands: list[Candidate]
    comp: dict
    match: BufferPoint | None
    frontier: list[dict]
    facts: dict | None

    @property
    def rec(self) -> BufferPoint:
        return self.comp["ours"].recommended

    @property
    def feasible(self) -> bool:
        return self.rec.risk is not None


def compute_plans(world: op.World, order: OrderConstraints, s: PlanSettings, n_compliant: int) -> PlanBundle:
    """Naive, naive + same buffer, and Bunon's plan, the buffer frontier, and the memo's fact sheet."""
    order, p = s.apply(order)
    cands = op.build_candidates(world, order, p)
    comp = op.compare_three(world, order, p, target_p=s.target, shocks=s.shocks, partial_lots=s.partial)
    rec = comp["ours"].recommended
    match = (op.baseline_to_match(world, order, p, rec.risk.p_meet_qty, shocks=s.shocks, partial_lots=s.partial)
             if rec.risk else None)
    frontier = []
    for pt in comp["ours"].frontier:
        bb = op.solve_baseline(cands, order.subcontract_qty, p, pt.buffer_pct)
        bp = op.evaluate(bb, cands, world, s.shocks, partial_lots=s.partial) if bb.status == "optimal" else None
        frontier.append({"buffer": pt.buffer_pct, "ours": pt.risk.p_meet_qty if pt.risk else None,
                         "baseline": bp.p_meet_qty if bp else None})
    facts = ag.factsheet_for_plan(order, comp, match, cands, s.slack, s.target, n_compliant) if rec.risk else None
    return PlanBundle(order, s, p, cands, comp, match, frontier, facts)


# --- follow-up steps (engine only) --------------------------------------------------------------------

def _late_candidates(world: op.World, b: PlanBundle, excluded_id: str, days: int) -> list[Candidate]:
    p_late = b.params.model_copy(update={"excluded_ids": [excluded_id]})
    return op.build_candidates(world, b.order, p_late, as_of=C.AS_OF + timedelta(days=days))


def replan_after_decline(world: op.World, b: PlanBundle, days: int = DECLINE_DAY) -> tuple[ReplanResult, list]:
    """The factory with the largest allocation declines on `days`; confirmed factories are locked."""
    rp = op.replan_locked(world, b.order, b.params, b.rec, op.largest_line(b.rec), days_elapsed=days,
                          target_p=b.settings.target, shocks=b.settings.shocks, partial_lots=b.settings.partial)
    return rp, _late_candidates(world, b, rp.excluded_id, days)


def replan_after_reply(world: op.World, b: PlanBundle, factory_id: str, decision: str, qty: int | None,
                       delay: int | None, days: int = DECLINE_DAY) -> tuple[ReplanResult, list]:
    rp = op.replan_from_reply(world, b.order, b.params, b.rec, factory_id, decision, qty, delay,
                              days_elapsed=days, target_p=b.settings.target, shocks=b.settings.shocks,
                              partial_lots=b.settings.partial)
    return rp, _late_candidates(world, b, rp.excluded_id, days)


def assess_news(world: op.World, b: PlanBundle, area: str, severity: str, duration: int | None) -> dict:
    ev = op.event_from_severity(area, severity, duration)
    return op.assess_news(world, b.order, b.params, b.rec, ev, target_p=b.settings.target,
                          shocks=b.settings.shocks, partial_lots=b.settings.partial)


def analyst_toolbox(world: op.World, b: PlanBundle) -> analyst.Toolbox:
    return analyst.Toolbox(world, b.order, b.params, b.comp, b.facts, b.settings.target, b.settings.shocks,
                           b.settings.partial)


# --- a factory's own record (synthetic history) ---------------------------------------------------------

def factory_record(history: pd.DataFrame, factory_id: str) -> dict:
    """What a factory sees about itself. Source: the SYNTHETIC history only (labelled so in the UI);
    platform-verified records start with the track-record milestone."""
    h = history[history["factory_id"] == factory_id]
    offers = len(h)
    jobs = rl.clean_jobs(h) if offers else h
    n = len(jobs)
    if not n:
        return {"offers": offers, "jobs": 0, "accept_rate": None, "on_time_rate": None, "full_rate": None,
                "qc_pass_rate": None, "source": "synthetic_history"}
    rej = pd.to_numeric(jobs.get("qc_rejected_qty"), errors="coerce").fillna(0)
    deliv = jobs["delivered_qty"].astype(float)
    return {"offers": offers, "jobs": n, "accept_rate": n / offers if offers else None,
            "on_time_rate": float((jobs["delay_days"] <= 0).mean()),
            "full_rate": float((jobs["fill"] >= C.FULL_FILL_THRESHOLD).mean()),
            "qc_pass_rate": float(1 - rej.sum() / deliv.sum()) if deliv.sum() else None,
            "source": "synthetic_history"}


def order_from_intake(intake, total: int, own: int, deadline: date, fob: float | None, penalty: float | None,
                      aql: float | None) -> OrderConstraints:
    """The order the human confirmed: their form values, plus the text fields and quotes that passed the
    intake checks (unverified fields stay empty)."""
    def ok(name):
        f = intake.fields.get(name)
        return f.value if f is not None and f.status == "ok" else None
    return OrderConstraints(
        total_qty=int(total), own_capacity_qty=int(own), deadline=deadline, delivery_point=ok("delivery_terms"),
        fabric=ok("fabric"), gsm=ok("gsm"), quality_aql=aql or None, fob_usd_per_piece=fob or None,
        late_penalty_pct_per_week=penalty or None, penalty_text=ok("penalty_text"),
        compliance_required=ok("compliance_required") or ["RSC", "BSCI"],
        source_spans={k: v.source_span for k, v in intake.fields.items() if v.status == "ok" and v.source_span})


def reply_terms(res) -> tuple[int | None, int | None]:
    """Quantity and delay from a parsed reply, only where the quote check passed."""
    return (res.quantity.value if res.quantity.status == "ok" else None,
            res.delay_days.value if res.delay_days.status == "ok" else None)


def _require_exporter(role: str, what: str) -> None:
    if role not in EXPORTER_ROLES:
        raise PermissionError(f"only an exporter role can {what} (got {role!r})")


# --- the order's state and timeline ------------------------------------------------------------------------

class OrderBook:
    """One visitor's orders, approvals and timeline. Roles only, never names."""

    def __init__(self, store: Store):
        self.store = store

    # orders and the story clock
    def confirm_order(self, order: OrderConstraints, role: str, s: PlanSettings, intake_source: str | None,
                      buyer: str | None = None) -> str:
        _require_exporter(role, "confirm an order")
        digest = hashlib.sha1(order.model_dump_json().encode()).hexdigest()[:6]
        n = sum(1 for _ in self.store.order_ids())
        order_id = f"ORD-{C.AS_OF:%y%m%d}-{digest}-{n + 1}"  # a re-confirm is a new order on day 0
        self.store.insert_order({
            "order_id": order_id, "created_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "buyer": buyer, "product": order.product, "total_qty": order.total_qty,
            "own_capacity_qty": order.own_capacity_qty, "subcontract_qty": order.subcontract_qty,
            "order_date": C.AS_OF.isoformat(), "deadline": order.deadline.isoformat(), "tolerance_pct": s.tol,
            "safety_margin_pct": s.margin, "loss_on_failure_bdt": s.loss,
            "quality_terms": f"AQL {order.quality_aql}" if order.quality_aql else None,
            "source_spans": json.dumps(order.source_spans), "order_json": order.model_dump_json(),
            "status": "confirmed", "story_day": 0})
        summary = (f"{order.total_qty:,} pcs, own {order.own_capacity_qty:,}, subcontract {order.subcontract_qty:,}, "
                   f"deadline {order.deadline:%d %b %Y}")
        self._event(order_id, "order_confirmed", summary, role,
                    payload={"intake": intake_source, "quoted_fields": sorted(order.source_spans)})
        dl.log_decision("order", "confirmed", role, None, summary)
        return order_id

    def order(self, order_id: str) -> OrderConstraints:
        return OrderConstraints.model_validate_json(self.store.order(order_id)["order_json"])

    def day(self, order_id: str) -> int:
        return int(self.store.order(order_id)["story_day"])

    @staticmethod
    def date_of(day: int) -> date:
        return Store.story_date(C.AS_OF, day)

    def advance_to(self, order_id: str, day: int, reason: str, role: str | None = None) -> int:
        """Move the story clock forward (never back) and log why."""
        now = self.day(order_id)
        if day > now:
            self.store.set_order(order_id, story_day=day)
            self._event(order_id, "clock", f"Day {day}: {reason}", role, day=day)
        return max(day, now)

    # plans and approvals
    def propose(self, order_id: str, point: BufferPoint, label: str) -> int:
        """Record a proposed allocation once per plan id (re-renders do not duplicate it)."""
        found = self.store.find_allocation(order_id, point.plan.plan_id)
        if found:
            return found["version"]
        lines = [{"factory_id": ln.factory_id, "qty": ln.qty, "unit_price_bdt": ln.unit_price_bdt}
                 for ln in point.plan.lines]
        v = self.store.add_allocation(order_id, point.plan.plan_id, label, lines,
                                      point.risk.p_meet_qty if point.risk else None, point.plan.cost_total,
                                      self.day(order_id))
        self._event(order_id, "plan_proposed", f"{label}: {len(lines)} factories, "
                    f"{point.plan.total_allocated:,} pcs", None, subject=point.plan.plan_id)
        return v

    def decide(self, order_id: str, step: str, point: BufferPoint, label: str,
               decision: Literal["approved", "rejected"], role: str, summary: str) -> None:
        """A human approves or rejects a plan. Nothing is sent: approval only unlocks the draft text."""
        _require_exporter(role, f"{decision[:-1]}e a plan")
        v = self.propose(order_id, point, label)
        self.store.set_allocation_status(order_id, v, decision, role)
        self._event(order_id, f"plan_{decision}", f"{label} {decision} ({summary})", role,
                    subject=point.plan.plan_id)
        dl.log_decision(step, decision, role, point.plan.plan_id, summary)

    def record_steer(self, order_id: str | None, instruction: str, accepted: list[dict], role: str) -> None:
        """A manager applied plain-language changes to the planning settings (the values were quoted from
        their own words and checked in agent.steer)."""
        _require_exporter(role, "change the planning settings")
        summary = f"{instruction} -> " + "; ".join(f"{a['field']}={a['value']}" for a in accepted)
        if order_id is not None:
            self._event(order_id, "settings_changed", summary, role, payload={"accepted": accepted})
        dl.log_decision("steer", "confirmed", role, None, summary)

    def timeline(self, order_id: str) -> list[dict]:
        return self.store.events(order_id)

    def _event(self, order_id: str, kind: str, summary: str, role: str | None, subject: str | None = None,
               payload: dict | None = None, day: int | None = None) -> int:
        d = self.day(order_id) if day is None else day
        return self.store.add_event(order_id, d, self.date_of(d), kind, summary, actor_role=role,
                                    subject=subject, payload=payload)
