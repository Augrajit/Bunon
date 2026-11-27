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
from bunon import attribution as at
from bunon import config as C
from bunon import decisions as dl
from bunon import loop as lp
from bunon import personas as ps
from bunon import optimizer as op
from bunon import reliability as rl
from bunon.schemas import BufferPoint, Candidate, OptimizerParams, OrderConstraints, ReplanResult
from bunon.store import EXPORTER_ROLES, FACTORY_ROLES, Store

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


def _require_factory(role: str, what: str) -> None:
    if role not in FACTORY_ROLES:
        raise PermissionError(f"only a factory role can {what} (got {role!r})")


FACTORY_FORM = "factory_form"  # messages.draft_of for text a factory typed into the app's form (no LLM involved)


def settle_decision(decision: str, qty: int | None, delay: int | None, requested: int) -> str:
    """The code settles the decision (same rule as agent.validate_reply): a yes with fewer pieces or a delay is a
    counter-offer; zero pieces is a rejection."""
    if decision == "reject" or qty == 0:
        return "reject"
    if decision == "accept" and ((qty is not None and qty < requested) or (delay or 0) > 0):
        return "counter"
    return decision


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
    def draft_round(self, order_id: str, b: PlanBundle, pf: ps.PersonaFile, client=None) -> list[dict]:
        """Create the sourcing round's request drafts once. Not sent. With an LLM client, each draft is written by
        the LLM under the placeholder rule (`agent.write_message`; numbers from the plan); otherwise, or if the
        LLM's draft fails the checks, the deterministic template."""
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
                text, source = lp.draft_request(r, language, b.order), "template"
                if client is not None:
                    fs = ag.message_facts("request", b.order, qty=r.qty, finish_by=r.finish_by)
                    d = ag.write_message("request", fs, language, text, client)
                    text, source = d.text, ("llm" if d.source != "template" else "template")
                self.store.insert("messages", {
                    "msg_id": f"{order_id}:req-{r.factory_id}", "order_id": order_id, "factory_id": r.factory_id,
                    "direction": "out", "kind": "request", "text": text,
                    "language": language, "draft_of": source, "story_day": day, "is_simulated": 0})
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
        msgs = self.store.messages(order_id)
        d, have = self.day(order_id), {m["msg_id"] for m in msgs}
        own = {m["factory_id"] for m in msgs if m["kind"] == kind and m["draft_of"] == FACTORY_FORM}
        lang = {p.factory_id: p.language for p in pf.personas}
        new = []
        for fid in factory_ids:
            if fid in own:  # the factory answered for itself in the app: its scripted message is not used
                continue
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
            "price_bdt_per_piece": res.price.value if res.price.status == "ok" else None,
            "ship_date": res.ship_date.value.isoformat() if res.ship_date.status == "ok" else None,
            "conditions": json.dumps([{"text": c.value, "quote": c.source_span, "status": c.status}
                                      for c in res.conditions], ensure_ascii=False),
            "quotes": json.dumps({"decision": res.decision_span, "qty": res.quantity.source_span,
                                  "delay": res.delay_days.source_span, "price": res.price.source_span,
                                  "ship_date": res.ship_date.source_span}, ensure_ascii=False),
            "parse_status": res.meta.source if res.meta else "unavailable", "story_day": msg["story_day"]})
        if delay is not None:
            self.store.insert("progress_updates", {
                "update_id": f"{order_id}:offer-delay-{msg['factory_id']}", "order_id": order_id,
                "factory_id": msg["factory_id"], "story_day": msg["story_day"], "text": msg["text"],
                "delay_days": delay, "is_simulated": 1, "parse_status": "from offer"})

    # --- the factory side (phone view): numbers the factory types itself, so no LLM reads them -------------
    def factory_reply(self, order_id: str, factory_id: str, decision: str, qty: int | None, delay_days: int | None,
                      text: str, role: str) -> str:
        """A factory answers a request it was sent. Returns the decision the code settled."""
        _require_factory(role, "answer a request")
        req = self.requests(order_id).get(factory_id)
        sent = any(m["kind"] == "request" and m["factory_id"] == factory_id and m["sent_day"] is not None
                   for m in self.store.messages(order_id))
        if req is None or not sent:
            raise ValueError(f"{factory_id} has no request to answer")
        if decision not in ("accept", "reject", "counter"):
            raise ValueError(f"unknown decision {decision!r}")
        if (qty is not None and qty < 0) or (delay_days is not None and delay_days < 0):
            raise ValueError("pieces and days late cannot be negative")
        done = next((o for o in self.store.rows("offers", order_id) if o["factory_id"] == factory_id), None)
        if done is not None:
            return done["decision"]
        decision = settle_decision(decision, qty, delay_days, req["qty"])
        d = self.day(order_id)
        mid = f"{order_id}:form-offer-{factory_id}"
        self.store.insert("messages", {"msg_id": mid, "order_id": order_id, "factory_id": factory_id,
                                       "direction": "in", "kind": "offer_reply", "author_role": role,
                                       "text": text.strip() or f"{decision}", "draft_of": FACTORY_FORM,
                                       "story_day": d, "is_simulated": 0})
        self.store.insert("offers", {
            "offer_id": f"{order_id}:offer-{factory_id}", "order_id": order_id, "msg_id": mid,
            "factory_id": factory_id, "decision": decision, "qty": None if decision == "reject" else qty,
            "conditions": "[]", "quotes": "{}", "parse_status": "typed by factory", "story_day": d})
        if delay_days and decision != "reject":
            self.store.insert("progress_updates", {
                "update_id": f"{order_id}:offer-delay-{factory_id}", "order_id": order_id, "factory_id": factory_id,
                "story_day": d, "text": text, "delay_days": delay_days, "is_simulated": 0,
                "parse_status": "from offer"})
        self._event(order_id, "offer_reply_received", f"{factory_id} answered in the app: {decision}", role,
                    subject=factory_id)
        return decision

    def factory_update(self, order_id: str, factory_id: str, pieces_done: int, delay_days: int, cause_text: str,
                       role: str) -> None:
        """A factory posts its daily update in the app (once per factory per story day)."""
        _require_factory(role, "post a daily update")
        if factory_id not in self.requests(order_id):
            raise ValueError(f"{factory_id} has no work on this order")
        if pieces_done < 0 or delay_days < 0:
            raise ValueError("pieces and days late cannot be negative")
        d = self.day(order_id)
        mid = f"{order_id}:form-update-{factory_id}-day{d}"
        if any(m["msg_id"] == mid for m in self.store.messages(order_id)):
            return
        cause = cause_text.strip() or None
        text = f"{pieces_done:,} pieces finished" + (f", {delay_days} days late" if delay_days else ", on time")             + (f": {cause}" if cause else "")
        self.store.insert("messages", {"msg_id": mid, "order_id": order_id, "factory_id": factory_id,
                                       "direction": "in", "kind": "progress_update", "author_role": role,
                                       "text": text, "draft_of": FACTORY_FORM, "story_day": d, "is_simulated": 0})
        self.store.insert("progress_updates", {
            "update_id": mid, "order_id": order_id, "factory_id": factory_id, "story_day": d, "text": text,
            "pieces_done_cum": pieces_done, "delay_days": delay_days, "cause_text": cause, "cause_category": cause,
            "quotes": "{}", "parse_status": "typed by factory", "is_simulated": 0})
        self._event(order_id, "progress_update_received", f"{factory_id} posted an update in the app", role,
                    subject=factory_id)

    def offer_terms(self, order_id: str) -> list[lp.OfferTerms]:
        offers = {o["factory_id"]: o for o in self.store.rows("offers", order_id)}
        delays = {u["factory_id"]: u["delay_days"] for u in self.store.rows("progress_updates", order_id)
                  if u["parse_status"] == "from offer"}
        out = []
        for f, r in self.requests(order_id).items():
            o = offers.get(f)
            delay = delays.get(f)
            if o and o["ship_date"] and o["decision"] != "reject":
                # a stated ship date after the asked finish date is a delay, counted in days by code
                late = (date.fromisoformat(o["ship_date"]) - date.fromisoformat(r["finish_by"])).days
                if late > 0 and (delay or 0) < late:
                    delay = late
            out.append(lp.OfferTerms(f, o["decision"] if o else "none", o["qty"] if o else None, delay, r["qty"],
                                     o["price_bdt_per_piece"] if o else None))
        return out

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

    def updates(self, order_id: str) -> list[lp.Update]:
        """Every daily update with a verified (or typed) piece count, oldest first; delays stated with an offer
        are not updates. `loop.latest_updates` keeps the latest per factory."""
        rows = [u for u in self.store.rows("progress_updates", order_id)
                if u["parse_status"] != "from offer" and u["pieces_done_cum"] is not None]
        return [lp.Update(u["factory_id"], u["pieces_done_cum"], u["delay_days"] or None, u["story_day"])
                for u in sorted(rows, key=lambda u: u["story_day"])]

    def previous_p(self, order_id: str, day: int) -> float | None:
        """The order's chance at the latest forecast from updates made before `day` (None if there was none)."""
        rows = [f for f in self.store.rows("forecasts", order_id) if f["story_day"] < day]
        return max(rows, key=lambda f: f["story_day"])["p_meet_order"] if rows else None

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

    def rescue_day(self, order_id: str) -> int | None:
        """The story day a rescue was first chosen (None if none yet). Updates after it do not change that
        forecast, so an approved rescue stays the one that was approved (D25)."""
        days = [r["story_day"] for r in self.store.rows("rescue_options", order_id) if r["chosen"]]
        return min(days) if days else None

    def chosen_rescue(self, order_id: str) -> str | None:
        rows = [r for r in self.store.rows("rescue_options", order_id) if r["chosen"]]
        return rows[-1]["kind"] if rows else None

    def approved_allocation(self, order_id: str) -> dict | None:
        rows = [a for a in self.store.allocations(order_id) if a["status"] == "approved"]
        return rows[-1] if rows else None

    def send_message(self, order_id: str, factory_id: str, text: str, role: str, msg_id: str,
                     kind: str = "rescue_request", draft_of: str = "template") -> None:
        """A human-approved message to one factory (shown in its in-app inbox). Never sent automatically."""
        _require_exporter(role, "send a message")
        d = self.day(order_id)
        msg_id = f"{order_id}:{msg_id}"
        if any(m["msg_id"] == msg_id for m in self.store.messages(order_id)):
            return
        self.store.insert("messages", {"msg_id": msg_id, "order_id": order_id, "factory_id": factory_id,
                                       "direction": "out", "kind": kind, "text": text,
                                       "draft_of": draft_of,
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
        n_delays: dict[str, int] = {}
        no_cause: dict[str, int] = {}
        causes: dict[str, list[str]] = {}
        for u in self.store.rows("progress_updates", order_id):
            if u["delay_days"]:
                f = u["factory_id"]
                delays[f] = max(delays.get(f, 0), u["delay_days"])
                n_delays[f] = n_delays.get(f, 0) + 1
                if u["cause_category"]:
                    causes.setdefault(f, []).append(u["cause_category"].strip().lower())
                else:
                    no_cause[f] = no_cause.get(f, 0) + 1
        # causes decided by a person (D23), and how many the factory disputes
        decided: dict[str, dict[str, int]] = {}
        disputed: dict[str, int] = {}
        for a in self.store.rows("attributions", order_id):
            if a["human_decision"] and a["category"]:
                c = decided.setdefault(a["factory_id"], {})
                c[a["category"]] = c.get(a["category"], 0) + 1
                disputed[a["factory_id"]] = disputed.get(a["factory_id"], 0) + a["disputed_by_factory"]
        out = []
        for f in self.requests(order_id):
            off, q = offers.get(f), qc.get(f)
            out.append({"factory_id": f, "answered": off is not None,
                        "decision": ("no reply" if off is None else
                                     "unreadable (read by hand)" if off["decision"] == "none" else off["decision"]),
                        "delay_reported_days": delays.get(f, 0),
                        # counted by code, never judged by the LLM: how many delays, how many without a stated
                        # cause, and how many repeat a cause already given on this order
                        "delays_reported": n_delays.get(f, 0),
                        "delays_without_cause": no_cause.get(f, 0),
                        "cause_repeats": len(causes.get(f, [])) - len(set(causes.get(f, []))),
                        "delay_causes": decided.get(f, {}),
                        "causes_disputed": disputed.get(f, 0),
                        "delivered_on_time": None if q is None else q["story_day"] <= due,
                        "qc_pass_rate": None if q is None or not q["received"] else q["good"] / q["received"],
                        "lot_rejected": None if q is None else bool(q["lot_rejected"])})
        return out

    def factory_platform_record(self, factory_id: str) -> dict:
        """One factory's record across every order in this store, from platform rows only (requests it was sent,
        its answers, updates and QC). Simulated personas count here too, so the app labels the result simulated."""
        asked = answered = took = qc_n = on_time = delays = 0
        good = received = 0
        for oid in self.store.order_ids():
            tr = {r["factory_id"]: r for r in self.track_record(oid)}
            r = tr.get(factory_id)
            if r is None:
                continue
            asked += 1
            answered += r["answered"]
            took += r["decision"] in ("accept", "counter")
            delays += r["delays_reported"]
            if r["delivered_on_time"] is not None:
                qc_n += 1
                on_time += r["delivered_on_time"]
            for q in self.store.rows("qc_records", oid):
                if q["factory_id"] == factory_id:
                    good, received = good + q["good"], received + q["received"]
        return {"orders_asked": asked, "answered": answered, "took_work": took,
                "acceptance_rate": took / answered if answered else None,
                "deliveries": qc_n, "on_time_rate": on_time / qc_n if qc_n else None,
                "qc_pass_rate": good / received if received else None, "delays_reported": delays}

    # --- the exporter's side of the record: payments (PLAN_V2 cut b) ----------------------------------------
    def amounts_due(self, order_id: str) -> list[dict]:
        """What the exporter owes each factory once QC is recorded: good pieces x the agreed unit price (code),
        due PAYMENT_TERM_DAYS after delivery. Nothing is typed by hand and nothing comes from an LLM."""
        alloc = self.approved_allocation(order_id)
        if alloc is None:
            return []
        price = {ln["factory_id"]: ln.get("unit_price_bdt") for ln in alloc["lines"]}
        paid = {p["factory_id"]: p for p in self.store.rows("payments", order_id)}
        out = []
        for q in sorted(self.store.rows("qc_records", order_id), key=lambda r: r["factory_id"]):
            f = q["factory_id"]
            if price.get(f) is None:
                continue
            p = paid.get(f)
            out.append({"factory_id": f, "good": q["good"], "unit_price_bdt": price[f],
                        "amount_bdt": round(q["good"] * price[f], 2),
                        "due_day": q["story_day"] + C.PAYMENT_TERM_DAYS,
                        "paid_day": p["paid_day"] if p else None})
        return out

    def record_payment(self, order_id: str, factory_id: str, role: str) -> None:
        """The exporter records that it paid a factory today (story clock). Exporter roles only; once per factory."""
        _require_exporter(role, "record a payment")
        due = next((d for d in self.amounts_due(order_id) if d["factory_id"] == factory_id), None)
        if due is None:
            raise ValueError(f"nothing is due to {factory_id} yet (record QC first)")
        if due["paid_day"] is not None:
            return
        d = self.day(order_id)
        self.store.insert("payments", {"payment_id": f"{order_id}:pay-{factory_id}", "order_id": order_id,
                                       "factory_id": factory_id, "amount_bdt": due["amount_bdt"],
                                       "due_day": due["due_day"], "paid_day": d})
        self._event(order_id, "payment_recorded", f"Paid {factory_id} {due['amount_bdt']:,.0f} BDT "
                    f"(due day {due['due_day']})", role, subject=factory_id)

    def exporter_record(self, order_id: str) -> dict:
        """The exporter's payment record on this order, computed only from the payments table."""
        rows = self.store.rows("payments", order_id)
        on_time = [r for r in rows if r["paid_day"] <= r["due_day"]]
        late = [r["paid_day"] - r["due_day"] for r in rows if r["paid_day"] > r["due_day"]]
        return {"payments": len(rows), "on_time": len(on_time),
                "on_time_rate": len(on_time) / len(rows) if rows else None,
                "avg_days_late": sum(late) / len(late) if late else None}

    # --- delay attribution (SPEC.md D23): AI suggests, code checks, an exporter role decides, the factory may dispute
    def record_news(self, order_id: str, quote: str, area: str, kind: str, severity: str, duration_days: int | None,
                    role: str, start_day: int | None = None) -> str:
        """Record a news item on this order (read with `agent.parse_news`, confirmed by a person). Its length is
        the stated days or the severity table's default (code). Returns its event id; the same item twice is one."""
        _require_exporter(role, "record a news event")
        if severity not in C.SEVERITY_TABLE:
            raise ValueError(f"unknown severity {severity!r}")
        if area not in dict(C.DISTRICT_KEYWORDS) and area != "national":
            raise ValueError(f"unknown area {area!r}")
        d = self.day(order_id)
        start = d if start_day is None else start_day
        if not 0 <= start <= d:
            raise ValueError("a news event cannot start before the order or after today")
        for e in self.news_events(order_id):
            if e.quote == quote and e.area == area:
                return e.event_id
        days = duration_days if duration_days else C.SEVERITY_TABLE[severity]["default_days"]
        eid = f"{order_id}:news-{len(self.news_events(order_id)) + 1}"
        self._event(order_id, "news_recorded", f"News recorded: {kind.replace('_', ' ')} in {area}", role,
                    subject=area, payload={"event_id": eid, "area": area, "kind": kind, "severity": severity,
                                           "start_day": start, "end_day": start + days - 1, "quote": quote})
        return eid

    def news_events(self, order_id: str) -> list[at.NewsEvent]:
        out = []
        for e in self.store.events(order_id):
            if e["kind"] == "news_recorded":
                p = json.loads(e["payload"] or "{}")
                out.append(at.NewsEvent(p["event_id"], p["area"], p["kind"], p["start_day"], p["end_day"], p["quote"]))
        return out

    def delays(self, order_id: str, districts: dict[str, str]) -> list[at.Delay]:
        """Delays to attribute: each factory's latest daily update that states days late (delays stated with an
        offer are not attributed here)."""
        latest: dict[str, dict] = {}
        for u in sorted(self.store.rows("progress_updates", order_id), key=lambda u: u["story_day"]):
            if u["parse_status"] != "from offer":
                latest[u["factory_id"]] = u
        return [at.Delay(f"{order_id}:delay-{f}-day{u['story_day']}", f, districts.get(f, "unknown"), u["story_day"],
                         u["delay_days"], u["text"], u["cause_text"])
                for f, u in sorted(latest.items()) if u["delay_days"]]

    def matching_events(self, order_id: str, delay: at.Delay) -> list[at.NewsEvent]:
        return at.matching_events(self.news_events(order_id), delay.district, lp.REPLY_DAY, delay.day)

    def attribution(self, order_id: str, delay_id: str) -> dict | None:
        return next((a for a in self.store.rows("attributions", order_id) if a["delay_id"] == delay_id), None)

    def record_suggestion(self, order_id: str, delay: at.Delay, res) -> None:
        """Keep the AI's checked suggestion next to the delay (never a decision)."""
        row = self.attribution(order_id, delay.delay_id) or {
            "delay_id": delay.delay_id, "order_id": order_id, "factory_id": delay.factory_id}
        sugg = json.dumps({"category": res.category, "suggested": res.suggested, "reason_quote": res.reason_quote,
                           "evidence_event_ids": res.evidence_event_ids, "notes": res.notes,
                           "source": res.meta.source if res.meta else "unavailable"}, ensure_ascii=False)
        if row.get("ai_suggestion") == sugg:
            return
        self.store.insert("attributions", {**row, "ai_suggestion": sugg})

    def decide_attribution(self, order_id: str, delay: at.Delay, category: str, role: str,
                           evidence_event_ids: list[str] | None = None) -> None:
        """An exporter role decides the cause. The code rule holds for people too: `outside_event` needs a
        matching recorded event (cited). The factory's own words are kept as the reason."""
        _require_exporter(role, "decide a delay's cause")
        if category not in at.CATEGORIES:
            raise ValueError(f"unknown category {category!r}")
        match = {e.event_id for e in self.matching_events(order_id, delay)}
        ev = [i for i in (evidence_event_ids or []) if i in match]
        if category == "outside_event" and not ev:
            raise ValueError("an outside event needs a recorded event in the factory's district during production")
        row = self.attribution(order_id, delay.delay_id) or {
            "delay_id": delay.delay_id, "order_id": order_id, "factory_id": delay.factory_id}
        self.store.insert("attributions", {**row, "category": category, "human_decision": role,
                                           "reason_quote": delay.cause_quote or delay.update_text,
                                           "evidence_event_ids": json.dumps(ev if category == "outside_event" else []),
                                           "disputed_by_factory": 0})
        self._event(order_id, "attribution_decided", f"Delay at {delay.factory_id}: {at.LABELS[category]}", role,
                    subject=delay.factory_id, payload={"delay_id": delay.delay_id, "category": category,
                                                       "evidence_event_ids": ev})

    def dispute_attribution(self, order_id: str, delay_id: str, factory_id: str, text: str, role: str) -> None:
        """The factory disputes a decided cause; the dispute is kept on its record and in the timeline."""
        _require_factory(role, "dispute a delay's cause")
        row = self.attribution(order_id, delay_id)
        if row is None or row["factory_id"] != factory_id or not row["human_decision"]:
            raise ValueError("only a decided cause of this factory's own delay can be disputed")
        if row["disputed_by_factory"]:
            return
        self.store.insert("attributions", {**row, "disputed_by_factory": 1})
        self._event(order_id, "attribution_disputed", f"{factory_id} disputes the cause recorded for its delay", role,
                    subject=factory_id, payload={"delay_id": delay_id, "text": text.strip()})

    def _event(self, order_id: str, kind: str, summary: str, role: str | None, subject: str | None = None,
               payload: dict | None = None, day: int | None = None, source: str = "platform") -> int:
        d = self.day(order_id) if day is None else day
        return self.store.add_event(order_id, d, self.date_of(d), kind, summary, actor_role=role,
                                    subject=subject, payload=payload, source=source)
