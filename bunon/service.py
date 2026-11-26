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
from bunon import loop as lp
from bunon import personas as ps
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

    # --- the loop: sourcing round -------------------------------------------------------------------
    def draft_round(self, order_id: str, b: PlanBundle, pf: ps.PersonaFile) -> list[dict]:
        """Create the sourcing round's request drafts once (template text, numbers from the plan). Not sent."""
        if not self.store.rows("sourcing_rounds", order_id):
            reqs = lp.sourcing_list(b)
            lang = {p.factory_id: p.language for p in pf.personas}
            day = self.day(order_id)
            self.store.insert("sourcing_rounds", {
                "round_id": f"{order_id}-R1", "order_id": order_id, "story_day": day,
                "factory_ids": json.dumps([r.factory_id for r in reqs]),
                "requested_qty": json.dumps({r.factory_id: r.qty for r in reqs}),
                "requested_date": json.dumps({r.factory_id: r.finish_by.isoformat() for r in reqs})})
            for r in reqs:
                language = lang.get(r.factory_id, "English")
                self.store.insert("messages", {
                    "msg_id": f"{order_id}:req-{r.factory_id}", "order_id": order_id, "factory_id": r.factory_id,
                    "direction": "out", "kind": "request", "text": lp.draft_request(r, language, b.order),
                    "language": language, "draft_of": "template", "story_day": day, "is_simulated": 0})
            self._event(order_id, "requests_drafted", f"{len(reqs)} request drafts ready for approval", None)
        return [m for m in self.store.messages(order_id) if m["kind"] == "request"]

    def requests(self, order_id: str) -> dict[str, dict]:
        r = self.store.rows("sourcing_rounds", order_id)
        if not r:
            return {}
        qty, when = json.loads(r[0]["requested_qty"]), json.loads(r[0]["requested_date"])
        return {f: {"qty": qty[f], "finish_by": when[f]} for f in json.loads(r[0]["factory_ids"])}

    def approve_round(self, order_id: str, role: str) -> None:
        """A human approves the drafts; only then are they 'sent' (shown in the factories' in-app inbox)."""
        _require_exporter(role, "approve and send requests")
        drafts = [m["msg_id"] for m in self.store.messages(order_id)
                  if m["kind"] == "request" and m["sent_day"] is None]
        if not drafts:
            return
        d = self.day(order_id)
        self.store.update_messages(order_id, drafts, approved_by_role=role, approved_day=d, sent_day=d)
        self._event(order_id, "requests_sent", f"{len(drafts)} requests approved and sent in the app", role)

    def round_sent(self, order_id: str) -> bool:
        msgs = [m for m in self.store.messages(order_id) if m["kind"] == "request"]
        return bool(msgs) and all(m["sent_day"] is not None for m in msgs)

    def receive(self, order_id: str, pf: ps.PersonaFile, kind: str, factory_ids: list[str]) -> list[dict]:
        """Simulated factories answer: persona messages of this kind up to today's story day become inbound
        messages (labelled simulated). Returns the new ones."""
        d, have = self.day(order_id), {m["msg_id"] for m in self.store.messages(order_id)}
        lang = {p.factory_id: p.language for p in pf.personas}
        new = []
        for fid in factory_ids:
            for m in ps.messages_for(pf, fid, kind):
                mid = f"{order_id}:{m.msg_id}"
                if m.story_day <= d and mid not in have:
                    row = {"msg_id": mid, "order_id": order_id, "factory_id": fid, "direction": "in", "kind": kind,
                           "text": m.text, "language": lang.get(fid), "story_day": m.story_day, "is_simulated": 1,
                           "draft_of": "placeholder" if m.placeholder else None}
                    self.store.insert("messages", row)
                    new.append(row)
        if new:
            self._event(order_id, f"{kind}_received", f"{len(new)} message(s) from simulated factories", None,
                        source="simulated_persona", payload={"msg_ids": [r["msg_id"] for r in new]})
        return new

    def record_offer(self, order_id: str, msg: dict, res) -> None:
        qty, delay = reply_terms(res)
        self.store.insert("offers", {
            "offer_id": f"{order_id}:offer-{msg['factory_id']}", "order_id": order_id, "msg_id": msg["msg_id"],
            "factory_id": msg["factory_id"], "decision": res.decision or "none", "qty": qty,
            "conditions": json.dumps([{"text": c.value, "quote": c.source_span, "status": c.status}
                                      for c in res.conditions], ensure_ascii=False),
            "quotes": json.dumps({"decision": res.decision_span, "qty": res.quantity.source_span,
                                  "delay": res.delay_days.source_span}, ensure_ascii=False),
            "parse_status": res.meta.source if res.meta else "unavailable", "story_day": msg["story_day"]})
        if delay is not None:
            self.store.insert("progress_updates", {
                "update_id": f"{order_id}:offer-delay-{msg['factory_id']}", "order_id": order_id,
                "factory_id": msg["factory_id"], "story_day": msg["story_day"], "text": msg["text"],
                "delay_days": delay, "is_simulated": 1, "parse_status": "from offer"})

    def offer_terms(self, order_id: str) -> list[lp.OfferTerms]:
        offers = {o["factory_id"]: o for o in self.store.rows("offers", order_id)}
        delays = {u["factory_id"]: u["delay_days"] for u in self.store.rows("progress_updates", order_id)
                  if u["parse_status"] == "from offer"}
        return [lp.OfferTerms(f, offers[f]["decision"] if f in offers else "none",
                              offers[f]["qty"] if f in offers else None, delays.get(f), r["qty"])
                for f, r in self.requests(order_id).items()]

    # --- the loop: follow-up, rescue, QC, record ------------------------------------------------------
    def record_update(self, order_id: str, msg: dict, res) -> None:
        self.store.insert("progress_updates", {
            "update_id": msg["msg_id"], "order_id": order_id, "factory_id": msg["factory_id"],
            "story_day": msg["story_day"], "text": msg["text"],
            "pieces_done_cum": res.pieces_done.value if res.pieces_done.status == "ok" else None,
            "delay_days": res.delay_days.value if res.delay_days.status == "ok" else None,
            "cause_text": res.cause.source_span if res.cause.status == "ok" else None,
            "cause_category": res.cause.value if res.cause.status == "ok" else None,
            "quotes": json.dumps({"pieces_done": res.pieces_done.source_span, "delay": res.delay_days.source_span},
                                 ensure_ascii=False),
            "parse_status": res.meta.source if res.meta else "unavailable", "is_simulated": 1})

    def record_forecast(self, order_id: str, fc: lp.Forecast, last_day: int | None) -> None:
        if any(f["story_day"] == fc.day and f["factory_id"] == fc.factory_id
               for f in self.store.rows("forecasts", order_id)):
            return
        self.store.insert("forecasts", {
            "order_id": order_id, "story_day": fc.day, "factory_id": fc.factory_id,
            "expected_good_by_deadline": fc.expected_good_after, "p_meet_order": fc.p_after,
            "last_rescue_day": last_day, "projected": fc.projected})
        self._event(order_id, "warning", f"{fc.factory_id} projected short by {fc.shortfall:,} pcs; chance of "
                    f"success {fc.p_before:.1%} -> {fc.p_after:.1%}"
                    + (f"; last day to rescue: day {last_day}" if last_day is not None else ""), None,
                    subject=fc.factory_id)

    def choose_rescue(self, order_id: str, opt: lp.RescueOption, role: str) -> None:
        """A human picks a rescue option (the 'move' plan itself is approved with `decide`)."""
        _require_exporter(role, "choose a rescue option")
        d = self.day(order_id)
        if any(r["chosen"] and r["kind"] == opt.kind and r["story_day"] == d
               for r in self.store.rows("rescue_options", order_id)):
            return
        self.store.insert("rescue_options", {
            "option_id": f"{order_id}:rescue-{d}-{opt.kind}", "order_id": order_id, "story_day": d, "kind": opt.kind,
            "details": opt.note, "p_meet_after": opt.p_after, "cost_delta_bdt": opt.cost_delta_bdt, "chosen": 1,
            "approved_by_role": role})
        self._event(order_id, "rescue_chosen", f"Rescue chosen: {opt.title} ({opt.note})", role)

    def chosen_rescue(self, order_id: str) -> str | None:
        rows = [r for r in self.store.rows("rescue_options", order_id) if r["chosen"]]
        return rows[-1]["kind"] if rows else None

    def approved_allocation(self, order_id: str) -> dict | None:
        rows = [a for a in self.store.allocations(order_id) if a["status"] == "approved"]
        return rows[-1] if rows else None

    def send_message(self, order_id: str, factory_id: str, text: str, role: str, msg_id: str) -> None:
        """A human-approved message to one factory (shown in its in-app inbox). Never sent automatically."""
        _require_exporter(role, "send a message")
        d = self.day(order_id)
        msg_id = f"{order_id}:{msg_id}"
        if any(m["msg_id"] == msg_id for m in self.store.messages(order_id)):
            return
        self.store.insert("messages", {"msg_id": msg_id, "order_id": order_id, "factory_id": factory_id,
                                       "direction": "out", "kind": "rescue_request", "text": text,
                                       "draft_of": "template",
                                       "approved_by_role": role, "approved_day": d, "sent_day": d,
                                       "story_day": d, "is_simulated": 0})
        self._event(order_id, "message_sent", f"Message to {factory_id} approved and sent in the app", role,
                    subject=factory_id)

    def record_qc(self, order_id: str, factory_id: str, received: int, rejected: int, lot_rejected: bool,
                  role: str, day: int) -> None:
        if role != "Quality inspector":
            raise PermissionError(f"only the quality inspector records QC (got {role!r})")
        if not 0 <= rejected <= received:
            raise ValueError("rejected pieces must be between 0 and the pieces received")
        if any(q["factory_id"] == factory_id for q in self.store.rows("qc_records", order_id)):
            return
        good = 0 if lot_rejected else received - rejected
        self.store.insert("qc_records", {
            "qc_id": f"{order_id}:qc-{factory_id}", "order_id": order_id, "factory_id": factory_id, "story_day": day,
            "received": received, "good": good, "rejected": received - good, "lot_rejected": int(lot_rejected),
            "inspector_role": role})
        # The QC row keeps the delivery day (for on-time); the event sits on today's story clock.
        self._event(order_id, "qc_recorded", f"QC {factory_id} (delivered day {day}): {received:,} received, "
                    f"{good:,} good", role, subject=factory_id)

    def track_record(self, order_id: str) -> list[dict]:
        """Each factory's record on THIS order, from platform events only (requests, replies, updates, QC).
        Delivered on time = QC on or before the day goods are due at the exporter (deadline minus QC days)."""
        o = self.order(order_id)
        due = lp.deadline_day(o) - C.QC_DAYS
        offers = {x["factory_id"]: x for x in self.store.rows("offers", order_id)}
        qc = {x["factory_id"]: x for x in self.store.rows("qc_records", order_id)}
        delays: dict[str, int] = {}
        for u in self.store.rows("progress_updates", order_id):
            if u["delay_days"]:
                delays[u["factory_id"]] = max(delays.get(u["factory_id"], 0), u["delay_days"])
        out = []
        for f in self.requests(order_id):
            off, q = offers.get(f), qc.get(f)
            out.append({"factory_id": f, "answered": off is not None,
                        "decision": ("no reply" if off is None else
                                     "unreadable (read by hand)" if off["decision"] == "none" else off["decision"]),
                        "delay_reported_days": delays.get(f, 0),
                        "delivered_on_time": None if q is None else q["story_day"] <= due,
                        "qc_pass_rate": None if q is None or not q["received"] else q["good"] / q["received"],
                        "lot_rejected": None if q is None else bool(q["lot_rejected"])})
        return out

    def _event(self, order_id: str, kind: str, summary: str, role: str | None, subject: str | None = None,
               payload: dict | None = None, day: int | None = None, source: str = "platform") -> int:
        d = self.day(order_id) if day is None else day
        return self.store.add_event(order_id, d, self.date_of(d), kind, summary, actor_role=role,
                                    subject=subject, payload=payload, source=source)
