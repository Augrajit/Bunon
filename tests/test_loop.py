"""The thin loop (PLAN_V2 §5, milestone 26 Nov – 2 Dec): sourcing, offers, split, update, forecast, rescue,
QC and the track record. Engine steps are code; the update parser follows the quote-check rule."""
from __future__ import annotations

import json
from datetime import date, timedelta

import pytest

from bunon import agent as ag
from bunon import config as C
from bunon import decisions as dl
from bunon import loop as lp
from bunon import optimizer as op
from bunon import personas as ps
from bunon import service as sv
from bunon.llm import LLMClient
from bunon.store import Store

pytestmark = pytest.mark.skipif(not C.PUBLIC_DB.exists(), reason="run `python -m bunon.data_gen` first")


class FakeProvider:
    name, model = "fake", "fake-1"

    def __init__(self, response):
        self.response = response

    def generate_json(self, prompt, json_schema):
        return json.dumps(self.response)


def fake(tmp_path, response):
    return LLMClient(provider=FakeProvider(response), mode="live", cache_dir=tmp_path)


@pytest.fixture(scope="module")
def world():
    return op.load_world()


@pytest.fixture(scope="module")
def bundle(world):
    return sv.compute_plans(world, op.demo_order(), sv.PlanSettings(), n_compliant=442)


def answer_key_offers(bundle) -> list[lp.OfferTerms]:
    pf = ps.load()
    out = []
    for r in lp.sourcing_list(bundle):
        m = ps.messages_for(pf, r.factory_id, "offer_reply")
        e = m[0].expected if m else None
        out.append(lp.OfferTerms(r.factory_id, e.decision if e else "none", e.qty if e else None,
                                 e.delay_days if e else None, r.qty, e.price_bdt_per_piece if e else None))
    return out


# --- engine steps --------------------------------------------------------------------------------------

def test_sourcing_asks_the_plan_plus_a_proven_standby(bundle):
    reqs = lp.sourcing_list(bundle)
    plan = {ln.factory_id: ln.qty for ln in bundle.rec.plan.lines}
    assert {r.factory_id: r.qty for r in reqs if r.role == "plan"} == plan
    standby = [r for r in reqs if r.role == "standby"]
    assert len(standby) == 1 and standby[0].factory_id not in plan
    assert next(c for c in bundle.cands if c.factory_id == standby[0].factory_id).proven
    assert all(r.finish_by < bundle.order.deadline for r in reqs)


def test_request_drafts_carry_only_plan_numbers(bundle):
    r = lp.sourcing_list(bundle)[0]
    for lang in ("English", "Banglish", "Bangla"):
        text = lp.draft_request(r, lang, bundle.order)
        assert f"{r.qty:,}" in text
        assert set(tn_numbers(text)) <= {float(r.qty), float(bundle.order.gsm), float(r.finish_by.day),
                                          float(r.finish_by.year)}


def tn_numbers(text):
    from bunon import textnum as tn
    return tn.numbers_in(text)


def test_split_never_exceeds_offers_and_drops_decliners(world, bundle):
    offers = answer_key_offers(bundle)
    res, cands = lp.split_from_offers(world, bundle, offers)
    lines = {ln.factory_id: ln.qty for ln in res.recommended.plan.lines}
    for o in offers:
        if o.decision == "reject":
            assert o.factory_id not in lines
        elif o.factory_id in lines:
            assert lines[o.factory_id] <= (o.qty or o.requested)
    assert res.recommended.risk is not None


def test_forecast_from_an_update_is_plain_arithmetic(world, bundle):
    res, cands = lp.split_from_offers(world, bundle, answer_key_offers(bundle))
    pt = res.recommended
    fid = pt.plan.lines[0].factory_id
    c = next(x for x in cands if x.factory_id == fid)
    setup = int(world.factories.loc[world.factories["factory_id"] == fid, "setup_days"].iloc[0])
    fc = lp.forecast_after_update(world, bundle, pt, cands, fid, 3_500, 2, 12)
    days_left = lp.ship_by_day(bundle.order, c) - 12 - 2
    start = lp.REPLY_DAY + setup  # production starts once the split is agreed, after the setup days
    assert fc.rate_seen == pytest.approx(3_500 / max(1, 12 - start))
    assert fc.projected == int(min(fc.planned, 3_500 + fc.rate_seen * days_left))
    assert fc.shortfall == fc.planned - fc.projected
    on_plan = lp.forecast_after_update(world, bundle, pt, cands, fid, fc.planned, 0, 12)
    assert on_plan.shortfall == 0 and on_plan.p_after >= fc.p_after


def test_last_rescue_day_moves_earlier_for_a_bigger_shortfall(world, bundle):
    exclude = {ln.factory_id for ln in bundle.rec.plan.lines}
    small, _ = lp.last_rescue_day(world, bundle, 1_000, exclude)
    big, _ = lp.last_rescue_day(world, bundle, 10_000, exclude)
    assert small is not None and big is not None and big <= small
    assert lp.last_rescue_day(world, bundle, 0, exclude) == (None, None)
    assert lp.last_rescue_day(world, bundle, 1_000, exclude, today=small + 1) == (None, None)  # never in the past


def test_accept_shortfall_only_within_tolerance(world, bundle):
    res, cands = lp.split_from_offers(world, bundle, answer_key_offers(bundle))
    pt = res.recommended
    fid = pt.plan.lines[0].factory_id
    for done in (3_500, 0):
        fc = lp.forecast_after_update(world, bundle, pt, cands, fid, done, 2, 12)
        opts = {o.kind: o for o in lp.rescue_options(world, bundle, pt, cands, fc, ["F0824"], 12)}
        assert opts["accept_shortfall"].allowed == (fc.expected_good_after >= fc.min_good)
        assert opts["escalate_buyer"].allowed
        if opts["move"].allowed:
            assert opts["move"].p_after is not None and opts["move"].replan.new.risk is not None


def test_move_respects_offers_and_locks_the_late_factory(world, bundle):
    """Reviewer finding (D14): the move is scored like the forecast and never asks a confirmed factory for more
    than it offered."""
    offers = answer_key_offers(bundle)
    res, cands = lp.split_from_offers(world, bundle, offers)
    pt = res.recommended
    fc = lp.forecast_after_update(world, bundle, pt, cands, "F1064", 3_500, 2, 12)
    rp = lp.rescue_move(world, bundle, pt, cands, fc, ["F0824"], 12)
    assert rp is not None
    new = {ln.factory_id: ln.qty for ln in rp.new.plan.lines}
    assert new["F1064"] == fc.projected // 100 * 100
    offered = {o.factory_id: (o.qty or o.requested) for o in offers}
    for ln in pt.plan.lines:
        if ln.factory_id != "F1064":
            assert ln.qty <= new[ln.factory_id] <= offered[ln.factory_id]
    assert "F0824" not in new


# --- the update parser keeps the quote rule -------------------------------------------------------------

UPDATE = "Bhai update: aj porjonto 2,500 pcs ready hoise. Line e current nai, 2 din late hobe."


def _upd(status="delayed", span="2 din late hobe", done=2500, dspan="2,500 pcs ready", delay=2,
         lspan="2 din late hobe", cause="power cut", cspan="Line e current nai"):
    return {"status": status, "status_span": span, "pieces_done": {"value": done, "source_span": dspan},
            "delay_days": {"value": delay, "source_span": lspan}, "cause": {"value": cause, "source_span": cspan}}


def test_update_parser_accepts_quoted_values(tmp_path):
    r = ag.parse_update(UPDATE, 13_100, fake(tmp_path, _upd()))
    assert r.status == "delayed" and r.pieces_done.value == 2500 and r.delay_days.value == 2
    assert r.cause.value == "power cut" and not r.notes


def test_update_parser_drops_invented_or_unquoted_numbers(tmp_path):
    r = ag.parse_update(UPDATE, 13_100, fake(tmp_path, _upd(done=3000)))
    assert r.pieces_done.status == "rejected" and r.pieces_done.value is None
    r = ag.parse_update(UPDATE, 13_100, fake(tmp_path / "b", _upd(dspan="3,000 pcs ready", done=3000)))
    assert r.pieces_done.status == "rejected"
    r = ag.parse_update(UPDATE, 13_100, fake(tmp_path / "c", _upd(status="on_track", span="update")))
    assert r.status == "delayed"  # a stated delay overrides "on track"


def test_update_parser_offline_miss_asks_a_human(tmp_path):
    r = ag.parse_update(UPDATE, 13_100, LLMClient(mode="offline", cache_dir=tmp_path))
    assert r.status is None and r.pieces_done.status == "missing" and r.notes


# --- the loop in the order book ---------------------------------------------------------------------------

@pytest.fixture()
def book(tmp_path, monkeypatch):
    monkeypatch.setenv(dl.LOG_DB_ENV, str(tmp_path / "decisions.db"))
    return sv.OrderBook(Store(tmp_path / "store.db"))


def test_loop_end_to_end_in_the_order_book(world, bundle, book):
    pf = ps.load()
    oid = book.confirm_order(bundle.order, "Merchandiser", sv.PlanSettings(), None)
    drafts = book.draft_round(oid, bundle, pf)
    assert len(drafts) == 6 and all(m["sent_day"] is None for m in drafts)
    assert book.receive(oid, pf, "offer_reply", list(book.requests(oid))) == []  # nothing before day 2
    with pytest.raises(PermissionError):
        book.approve_round(oid, "Factory owner")
    book.approve_round(oid, "Merchandiser")
    assert book.round_sent(oid)
    book.advance_to(oid, lp.REPLY_DAY, "replies", "Merchandiser")
    got = book.receive(oid, pf, "offer_reply", list(book.requests(oid)))
    assert len(got) == 6 and all(m["is_simulated"] == 1 for m in got)
    assert [e for e in book.timeline(oid) if e["kind"] == "offer_reply_received"][0]["source"] == "simulated_persona"
    with pytest.raises(PermissionError):
        book.send_message(oid, "F1856", "x", "Floor supervisor", "m1")
    with pytest.raises(PermissionError):
        book.record_qc(oid, "F1856", 100, 1, False, "Merchandiser", 24)
    book.record_qc(oid, "F1856", 13_600, 163, False, "Quality inspector", 24)
    book.record_qc(oid, "F1064", 10_000, 310, False, "Quality inspector", 26)
    tr = {r["factory_id"]: r for r in book.track_record(oid)}
    assert tr["F1856"]["delivered_on_time"] is True and tr["F1064"]["delivered_on_time"] is False
    assert tr["F1856"]["qc_pass_rate"] == pytest.approx(1 - 163 / 13_600)
    assert tr["F0824"]["decision"] == "no reply"  # no offer recorded in this test


def test_persona_placeholders_are_flagged_and_cover_the_sourcing_round(bundle):
    pf = ps.load()
    asked = {r.factory_id for r in lp.sourcing_list(bundle)}
    with_offer = {p.factory_id for p in pf.personas for m in p.messages if m.kind == "offer_reply"}
    assert asked <= with_offer
    assert ps.validate(pf) == []


def test_placeholder_messages_stay_tagged_when_received(world, bundle, book):
    """Placeholder text is flagged all the way into the app (the team swaps it for their own text)."""
    pf = ps.load()
    oid = book.confirm_order(bundle.order, "Merchandiser", sv.PlanSettings(), None)
    book.draft_round(oid, bundle, pf)
    book.approve_round(oid, "Merchandiser")
    book.advance_to(oid, lp.REPLY_DAY, "replies", "Merchandiser")
    got = book.receive(oid, pf, "offer_reply", list(book.requests(oid)))
    flagged = {m.msg_id for p in pf.personas for m in p.messages if m.placeholder}
    for row in got:
        assert (row["draft_of"] == "placeholder") == (row["msg_id"].split(":", 1)[1] in flagged)


def test_factory_inbox_rows_are_order_scoped(bundle, book):
    """Reviewer finding (D14): ids are scoped to the order, so a second order does not take over the first's rows."""
    pf = ps.load()
    a = book.confirm_order(bundle.order, "Merchandiser", sv.PlanSettings(), None)
    book.draft_round(a, bundle, pf)
    b = book.confirm_order(bundle.order, "Merchandiser", sv.PlanSettings(), None)
    book.draft_round(b, bundle, pf)
    assert len(book.store.messages(a)) == len(book.store.messages(b)) == 6


# --- the exporter's payment record (PLAN_V2 cut b) ---------------------------------------------------------

def _through_qc(bundle, book):
    oid = book.confirm_order(bundle.order, "Merchandiser", sv.PlanSettings(), None)
    book.decide(oid, "plan", bundle.rec, "Initial plan", "approved", "Merchandiser", "test")
    lines = bundle.rec.plan.lines
    book.record_qc(oid, lines[0].factory_id, 1_000, 20, False, "Quality inspector", 24)
    book.record_qc(oid, lines[1].factory_id, 500, 500, True, "Quality inspector", 26)
    return oid, lines


def test_amount_due_is_good_pieces_times_the_agreed_rate(bundle, book):
    oid, lines = _through_qc(bundle, book)
    due = {d["factory_id"]: d for d in book.amounts_due(oid)}
    assert set(due) == {lines[0].factory_id, lines[1].factory_id}
    a = due[lines[0].factory_id]
    assert a["good"] == 980 and a["amount_bdt"] == pytest.approx(980 * lines[0].unit_price_bdt, abs=0.01)
    assert a["due_day"] == 24 + C.PAYMENT_TERM_DAYS and a["paid_day"] is None
    assert due[lines[1].factory_id]["amount_bdt"] == 0  # a rejected lot has no good pieces


def test_payments_only_by_exporters_once_and_recomputed_from_rows(bundle, book):
    oid, lines = _through_qc(bundle, book)
    f0, f1 = lines[0].factory_id, lines[1].factory_id
    with pytest.raises(PermissionError):
        book.record_payment(oid, f0, "Factory owner")
    with pytest.raises(ValueError):
        book.record_payment(oid, lines[2].factory_id, "Merchandiser")  # no QC yet, nothing due
    assert book.exporter_record(oid) == {"payments": 0, "on_time": 0, "on_time_rate": None, "avg_days_late": None}
    book.advance_to(oid, 30, "pay", "Merchandiser")
    book.record_payment(oid, f0, "Owner / director")
    book.record_payment(oid, f0, "Owner / director")  # idempotent
    book.advance_to(oid, 26 + C.PAYMENT_TERM_DAYS + 4, "pay late", "Merchandiser")
    book.record_payment(oid, f1, "Merchandiser")
    rec = book.exporter_record(oid)
    assert rec == {"payments": 2, "on_time": 1, "on_time_rate": 0.5, "avg_days_late": 4}
    assert sum(e["kind"] == "payment_recorded" for e in book.timeline(oid)) == 2


def test_delays_and_causes_are_counted_by_code(bundle, book):
    """PLAN_V2 §0 item 2: code counts delays, missing causes and repeated causes; nothing calls them excuses."""
    oid = book.confirm_order(bundle.order, "Merchandiser", sv.PlanSettings(), None)
    book.draft_round(oid, bundle, ps.load())
    f = bundle.rec.plan.lines[0].factory_id
    for i, (delay, cause) in enumerate([(2, "power cut"), (1, "Power cut "), (3, None), (0, None)]):
        book.store.insert("progress_updates", {"update_id": f"{oid}:u{i}", "order_id": oid, "factory_id": f,
                                               "story_day": 10 + i, "text": "t", "delay_days": delay,
                                               "cause_category": cause, "is_simulated": 1})
    r = next(x for x in book.track_record(oid) if x["factory_id"] == f)
    assert (r["delays_reported"], r["delays_without_cause"], r["cause_repeats"]) == (3, 1, 1)
    assert r["delay_reported_days"] == 3


# --- the factory side: a factory answers and posts updates itself (numbers it typed, no LLM) ----------------

def _sent_round(bundle, book):
    oid = book.confirm_order(bundle.order, "Merchandiser", sv.PlanSettings(), None)
    book.draft_round(oid, bundle, ps.load())
    return oid


def test_factory_reply_needs_a_sent_request_and_a_factory_role(bundle, book):
    oid = _sent_round(bundle, book)
    f = "F1856"
    with pytest.raises(ValueError):
        book.factory_reply(oid, f, "accept", None, None, "", "Factory owner")  # drafted, not sent yet
    book.approve_round(oid, "Merchandiser")
    with pytest.raises(PermissionError):
        book.factory_reply(oid, f, "accept", None, None, "", "Merchandiser")
    with pytest.raises(ValueError):
        book.factory_reply(oid, "F9999", "accept", None, None, "", "Factory owner")
    asked = book.requests(oid)[f]["qty"]
    assert book.factory_reply(oid, f, "accept", asked - 1_000, None, "Can do less", "Factory owner") == "counter"
    assert book.factory_reply(oid, f, "reject", 0, None, "", "Factory owner") == "counter"  # first answer stands
    o = next(x for x in book.offer_terms(oid) if x.factory_id == f)
    assert (o.decision, o.qty) == ("counter", asked - 1_000)


def test_a_factory_answer_replaces_its_scripted_reply(bundle, book):
    oid = _sent_round(bundle, book)
    book.approve_round(oid, "Merchandiser")
    book.factory_reply(oid, "F0824", "accept", None, 3, "Yes, 3 days late", "Floor supervisor")
    book.advance_to(oid, lp.REPLY_DAY, "replies", "Merchandiser")
    got = book.receive(oid, ps.load(), "offer_reply", list(book.requests(oid)))
    assert "F0824" not in {m["factory_id"] for m in got} and len(got) == 5
    o = next(x for x in book.offer_terms(oid) if x.factory_id == "F0824")
    assert (o.decision, o.delay_days) == ("counter", 3)
    ev = [e for e in book.timeline(oid) if e["kind"] == "offer_reply_received"]
    assert ev[0]["source"] == "platform" and ev[0]["actor_role"] == "Floor supervisor"


def test_factory_update_is_stored_as_typed_once_a_day(bundle, book):
    oid = _sent_round(bundle, book)
    book.approve_round(oid, "Merchandiser")
    with pytest.raises(PermissionError):
        book.factory_update(oid, "F1064", 100, 0, "", "Production manager")
    with pytest.raises(ValueError):
        book.factory_update(oid, "F1064", -1, 0, "", "Factory owner")
    book.advance_to(oid, 8, "update", "Merchandiser")
    book.factory_update(oid, "F1064", 2_000, 1, "Fabric late", "Factory owner")
    book.factory_update(oid, "F1064", 2_500, 1, "Fabric late", "Factory owner")  # same day: first one stands
    ups = [u for u in book.store.rows("progress_updates", oid) if u["parse_status"] == "typed by factory"]
    assert len(ups) == 1 and ups[0]["pieces_done_cum"] == 2_000 and ups[0]["cause_category"] == "Fabric late"
    tr = next(r for r in book.track_record(oid) if r["factory_id"] == "F1064")
    assert tr["delays_reported"] == 1 and tr["delays_without_cause"] == 0


# --- the published demo numbers (README, SPEC D14, the video script) ---------------------------------------

def test_published_demo_numbers(world, bundle):
    """HANDOFF task 5.1: if this fails, the engine or the fixture changed: update README.md, SPEC.md and the
    video script with the new numbers (and re-warm the cache), never the other way round."""
    assert bundle.rec.risk.p_meet_qty == pytest.approx(0.875, abs=0.0005)
    offers = answer_key_offers(bundle)
    res, cands = lp.split_from_offers(world, bundle, offers)
    pt = res.recommended
    assert pt.risk.p_meet_qty == pytest.approx(0.841, abs=0.0005)
    assert {ln.factory_id: ln.qty for ln in pt.plan.lines}["F2829"] == 9_900
    fc = lp.forecast_after_update(world, bundle, pt, cands, "F1064", 3_500, 2, lp.UPDATE_DAY)
    assert (fc.projected, fc.shortfall) == (9_800, 3_300)
    assert fc.p_after == pytest.approx(0.818, abs=0.0005)
    assert round(fc.expected_good_after) == 51_719 and fc.min_good == 49_000
    excl = {ln.factory_id for ln in pt.plan.lines} | {"F0824"}
    assert lp.last_rescue_day(world, bundle, fc.shortfall, excl, today=lp.UPDATE_DAY)[0] == 16
    opts = {o.kind: o for o in lp.rescue_options(world, bundle, pt, cands, fc, ["F0824"], lp.UPDATE_DAY)}
    assert opts["move"].p_after == pytest.approx(0.856, abs=0.0005)
    assert round(opts["move"].cost_delta_bdt) == 34_813  # 33,757 before quoted rates (D20) and opts["move"].note == "F2829 +3,300"
    assert opts["accept_shortfall"].allowed


def test_factory_platform_record_recomputes_across_orders(bundle, book):
    """HANDOFF task 4.1: a factory's record over every order in the store, from platform rows only."""
    pf = ps.load()
    for received, rejected, day in ((1_000, 50, 24), (2_000, 0, 27)):
        oid = book.confirm_order(bundle.order, "Merchandiser", sv.PlanSettings(), None)
        book.draft_round(oid, bundle, pf)
        book.approve_round(oid, "Merchandiser")
        book.factory_reply(oid, "F1856", "accept", None, None, "", "Factory owner")
        book.record_qc(oid, "F1856", received, rejected, False, "Quality inspector", day)
    r = book.factory_platform_record("F1856")
    assert (r["orders_asked"], r["answered"], r["took_work"], r["deliveries"]) == (2, 2, 2, 2)
    assert r["acceptance_rate"] == 1.0 and r["on_time_rate"] == 0.5  # day 27 is after the due day
    assert r["qc_pass_rate"] == pytest.approx(2_950 / 3_000)
    assert book.factory_platform_record("F9999")["orders_asked"] == 0


def test_surge_option_stays_within_the_placeholder_model(world, bundle):
    """HANDOFF task 3.3: overtime never adds more than SURGE_SHARE a day over the days left, nor more than the
    shortfall; it costs only the overtime premium on those pieces."""
    res, cands = lp.split_from_offers(world, bundle, answer_key_offers(bundle))
    pt = res.recommended
    fc = lp.forecast_after_update(world, bundle, pt, cands, "F1064", 3_500, 2, lp.UPDATE_DAY)
    extra = lp.surge_pieces(fc)
    assert 0 < extra <= min(fc.shortfall, fc.rate_seen * C.SURGE_SHARE * fc.days_left)
    surge = {o.kind: o for o in lp.rescue_options(world, bundle, pt, cands, fc, ["F0824"], lp.UPDATE_DAY)}["surge"]
    price = next(ln.unit_price_bdt for ln in pt.plan.lines if ln.factory_id == "F1064")
    assert surge.allowed and surge.cost_delta_bdt == pytest.approx(extra * price * (C.SURGE_PRICE_FACTOR - 1))
    assert fc.p_after <= surge.p_after
    late = lp.forecast_after_update(world, bundle, pt, cands, "F1064", 3_500, 30, lp.UPDATE_DAY)  # no days left
    assert late.days_left == 0 and lp.surge_pieces(late) == 0


def test_surge_request_carries_only_forecast_numbers(bundle):
    for lang in ("English", "Banglish", "Bangla"):
        t = lp.draft_surge_request("F1064", 945, 10_745, bundle.order, lang)
        assert "945" in t and "10,745" in t
        assert set(tn_numbers(t)) <= {945.0, 10_745.0, float(bundle.order.deadline.day),
                                      float(bundle.order.deadline.year)}


# --- offer parser v2: price and ship date, each quoted; the code settles the decision (D20) ------------------

def _offer(decision, span, qty=None, qspan=None, price=None, pspan=None, ship=None, sspan=None, conds=()):
    return {"decision": decision, "decision_span": span, "quantity": {"value": qty, "source_span": qspan},
            "delay_days": {"value": None, "source_span": None},
            "price_bdt_per_piece": {"value": price, "source_span": pspan},
            "ship_date": {"value": ship, "source_span": sspan},
            "conditions": [{"text": t, "source_span": s} for t, s in conds]}


F2829 = "Hello, we can take up to 15,200 pcs basic tee for this order and deliver on time. Rate 60 BDT per piece."
F2184 = "12,400 pcs korte parbo bhai, but rate 50 e hobe na, 56 taka lagbe."
EXAMPLE = "Bhai, 8000 pcs nite parbo, rate 62 taka, delivery 25/01/2027."


def test_a_rate_near_our_planned_rate_is_not_a_counter(tmp_path):
    """Known issue 1 (D14): F2829 stating its rate, in answer to "your rate", was read as a counter-offer."""
    out = _offer("counter", "we can take up to 15,200 pcs", 15_200, "up to 15,200 pcs", 60, "Rate 60 BDT per piece",
                 conds=[("rate 60 BDT per piece", "Rate 60 BDT per piece")])
    r = ag.parse_reply(F2829, 15_200, fake(tmp_path, out), planned_price=59.68)
    assert r.price.status == "ok" and r.price.value == 60 and r.decision == "accept" and r.notes
    r = ag.parse_reply(F2829, 15_200, fake(tmp_path / "b", out), planned_price=50.0)  # well above our rate
    assert r.decision == "counter"


def test_a_higher_rate_makes_a_yes_a_counter_and_the_asked_price_is_kept(tmp_path):
    out = _offer("accept", "12,400 pcs korte parbo", 12_400, "12,400 pcs", 56, "56 taka lagbe")
    r = ag.parse_reply(F2184, 12_400, fake(tmp_path, out), planned_price=50.18)
    assert r.decision == "counter" and r.price.value == 56
    bad = _offer("counter", "56 taka lagbe", 12_400, "12,400 pcs", 50, "56 taka lagbe")  # 50 is not what is asked
    r = ag.parse_reply(F2184, 12_400, fake(tmp_path / "b", bad), planned_price=50.18)
    assert r.price.status == "rejected" and r.price.value is None


def test_ship_date_must_be_stated_day_first(tmp_path):
    out = _offer("counter", "8000 pcs nite parbo", 8_000, "8000 pcs", 62, "rate 62 taka", "2027-01-25",
                 "delivery 25/01/2027")
    r = ag.parse_reply(EXAMPLE, 10_000, fake(tmp_path, out), planned_price=55.0)
    assert r.ship_date.status == "ok" and str(r.ship_date.value) == "2027-01-25" and r.decision == "counter"
    wrong = _offer("counter", "8000 pcs nite parbo", 8_000, "8000 pcs", ship="2027-25-01", sspan="delivery 25/01/2027")
    assert ag.parse_reply(EXAMPLE, 10_000, fake(tmp_path / "b", wrong)).ship_date.status == "rejected"
    other = _offer("counter", "8000 pcs nite parbo", 8_000, "8000 pcs", ship="2027-01-26", sspan="delivery 25/01/2027")
    assert ag.parse_reply(EXAMPLE, 10_000, fake(tmp_path / "c", other)).ship_date.value is None


def test_a_price_outside_the_usual_range_is_flagged_not_dropped(tmp_path):
    text = "Ok 13,100 pcs, rate 250 taka."
    out = _offer("counter", "Ok 13,100 pcs", 13_100, "13,100 pcs", 250, "rate 250 taka")
    r = ag.parse_reply(text, 13_100, fake(tmp_path, out), planned_price=50.0)
    assert r.price.value == 250 and any("outside the usual range" in n for n in r.notes)


def test_quoted_rates_price_the_split_and_a_late_ship_date_is_a_delay(world, bundle, book):
    offers = answer_key_offers(bundle)
    res, cands = lp.split_from_offers(world, bundle, offers)
    price = {c.factory_id: c.unit_price_bdt for c in cands}
    assert price["F2829"] == 60.0 and price["F2184"] == 56.0 and price["F1856"] == 57.0
    assert price["F1064"] == next(c.unit_price_bdt for c in bundle.cands if c.factory_id == "F1064")  # no rate quoted
    oid = book.confirm_order(bundle.order, "Merchandiser", sv.PlanSettings(), None)
    book.draft_round(oid, bundle, ps.load())
    finish = date.fromisoformat(book.requests(oid)["F1856"]["finish_by"])
    book.store.insert("offers", {"offer_id": f"{oid}:offer-F1856", "order_id": oid, "factory_id": "F1856",
                                 "decision": "counter", "qty": 13_200, "price_bdt_per_piece": 58.0,
                                 "ship_date": (finish + timedelta(days=3)).isoformat(), "story_day": 2})
    o = next(x for x in book.offer_terms(oid) if x.factory_id == "F1856")
    assert (o.delay_days, o.price) == (3, 58.0)


# --- AI-written messages and warning under the placeholder rule (D21) --------------------------------------

def _req_facts(bundle):
    r = lp.sourcing_list(bundle)[0]
    return r, ag.message_facts("request", bundle.order, qty=r.qty, finish_by=r.finish_by)


def test_ai_request_draft_renders_only_plan_numbers(tmp_path, bundle):
    r, fs = _req_facts(bundle)
    text = "Dear Sir, can you make {{req.qty}} pcs ({{req.gsm}} GSM, fabric from us), finished by {{req.finish_by}}?"
    d = ag.write_message("request", fs, "English", "TEMPLATE", fake(tmp_path, {"text": text}))
    assert d.source == "live" and f"{r.qty:,}" in d.text and f"{r.finish_by:%d %b %Y}" in d.text
    assert set(tn_numbers(d.text)) <= {float(r.qty), float(bundle.order.gsm), float(r.finish_by.day),
                                       float(r.finish_by.year)}


@pytest.mark.parametrize("text, why", [
    ("Bhai, {{req.qty}} pcs, 5 din er moddhe, {{req.finish_by}}", "literal number"),
    ("Bhai, dui hajar pcs aro lagbe: {{req.qty}} pcs by {{req.finish_by}}", "number word"),
    ("Bhai, {{req.qty}} pcs parben?", "missing required"),
    ("Bhai {{fac:F1856}}, {{req.qty}} pcs by {{req.finish_by}}", "do not name"),
    ("Bhai, {{req.price}} taka rate e {{req.qty}} pcs by {{req.finish_by}}", "unknown fact key"),
])
def test_ai_draft_breaking_the_rule_falls_back_to_the_template(tmp_path, bundle, text, why):
    _, fs = _req_facts(bundle)
    d = ag.write_message("request", fs, "Banglish", "TEMPLATE", fake(tmp_path, {"text": text}))
    assert d.source == "template" and d.text == "TEMPLATE" and any(why in e for e in d.errors), d.errors


def test_bangla_number_words_are_caught():
    assert tn_bn("আরও দুই হাজার পিস") == ["দুই", "হাজার"]
    assert tn_bn("Bhai, aro panch hajar pcs") == ["panch", "hajar"]
    assert tn_bn("Bhai, at the factory, tin shed") == []  # ambiguous words are not flagged
    assert tn_bn("একটি অর্ডার, বিশেষ অনুরোধ, কাজ আটকে আছে") == []  # "a/an", "special", "stuck"
    assert tn_bn("আরও দুইটি লাইন") == ["দুইটি"]


def tn_bn(text):
    from bunon import textnum as tn
    return tn.bn_number_words(text)


def test_offline_miss_drafts_with_the_template_and_draft_round_marks_the_source(tmp_path, bundle, book):
    _, fs = _req_facts(bundle)
    d = ag.write_message("request", fs, "English", "TEMPLATE", LLMClient(mode="offline", cache_dir=tmp_path))
    assert d.source == "template" and d.text == "TEMPLATE"
    oid = book.confirm_order(bundle.order, "Merchandiser", sv.PlanSettings(), None)
    text = "Dear Sir, please confirm {{req.qty}} pcs by {{req.finish_by}} and your rate."
    drafts = book.draft_round(oid, bundle, ps.load(), fake(tmp_path / "f", {"text": text}))
    assert len(drafts) == 6 and all(m["draft_of"] == "llm" and m["sent_day"] is None for m in drafts)
    assert all(f"{book.requests(oid)[m['factory_id']]['qty']:,}" in m["text"] for m in drafts)


def test_ai_warning_cites_only_forecast_facts(tmp_path, world, bundle):
    res, cands = lp.split_from_offers(world, bundle, answer_key_offers(bundle))
    fc = lp.forecast_after_update(world, bundle, res.recommended, cands, "F1064", 3_500, 2, lp.UPDATE_DAY)
    level = lp.warning_level(fc, 0.90)
    assert level == "below target"
    fs = ag.warning_facts(fc, 2, 0.90, 16, C.AS_OF + __import__("datetime").timedelta(days=16), "F2314", level)
    ok = {"headline": "{{fac:F1064}} is falling behind.",
          "explanation": "It reports {{warn.pieces_done}} pieces done and {{warn.delay}} lost, so it will make "
                         "{{warn.projected}}, {{warn.shortfall}} short. The order's chance drops to {{warn.p_after}}. "
                         "A rescue is possible until {{warn.last_date}}."}
    w = ag.write_warning(fs, fake(tmp_path, ok))
    assert w.source == "live" and "3,300" in w.explanation and "81.8%" in w.explanation and "19 Jan 2027" in w.explanation
    bad = dict(ok, explanation="It will be about 3,300 short; chance {{warn.p_after}}, {{warn.shortfall}}.")
    w = ag.write_warning(fs, fake(tmp_path / "b", bad))
    assert w.source == "template" and "3,300" in w.explanation and w.errors
    vague = dict(ok, explanation=ok["explanation"].replace("{{warn.pieces_done}} pieces", "some pieces"))
    w = ag.write_warning(fs, fake(tmp_path / "c", vague))
    assert w.source == "template" and any("vague" in e or "warn.pieces_done" in e for e in w.errors)
    assert lp.warning_level(lp.forecast_after_update(world, bundle, res.recommended, cands, "F1064", 13_100, 0,
                                                     lp.UPDATE_DAY), 0.90) == "on track"


# --- several updates (HANDOFF task 3.1, SPEC.md D22) ------------------------------------------------------------

def _two_late(world, bundle):
    res, cands = lp.split_from_offers(world, bundle, answer_key_offers(bundle))
    pt = res.recommended
    a, b2 = [ln.factory_id for ln in sorted(pt.plan.lines, key=lambda ln: -ln.qty)][:2]
    qa = next(ln.qty for ln in pt.plan.lines if ln.factory_id == a)
    qb = next(ln.qty for ln in pt.plan.lines if ln.factory_id == b2)
    ups = [lp.Update(a, qa // 4, 2, lp.UPDATE_DAY), lp.Update(b2, qb // 4, 3, lp.UPDATE_DAY)]
    return pt, cands, a, b2, ups


def test_one_update_through_the_multi_path_is_unchanged(world, bundle):
    res, cands = lp.split_from_offers(world, bundle, answer_key_offers(bundle))
    one = lp.forecast_after_update(world, bundle, res.recommended, cands, "F1064", 3_500, 2, lp.UPDATE_DAY)
    many = lp.forecast_from_updates(world, bundle, res.recommended, cands, [lp.Update("F1064", 3_500, 2, lp.UPDATE_DAY)])
    assert many == [one] and one.others == () and one.order_shortfall == one.shortfall
    assert one.projected == 9_800 and f"{one.p_after:.1%}" == "81.8%"  # the published demo numbers stay


def test_two_late_factories_shortfalls_add_and_chance_is_joint(world, bundle):
    pt, cands, a, b2, ups = _two_late(world, bundle)
    alone = {u.factory_id: lp.forecast_from_updates(world, bundle, pt, cands, [u])[0] for u in ups}
    both = lp.forecast_from_updates(world, bundle, pt, cands, ups)
    assert {fc.factory_id for fc in both} == {a, b2}
    assert [fc.shortfall for fc in both] == sorted((fc.shortfall for fc in both), reverse=True)
    for fc in both:  # each projection is the factory's own arithmetic; the chance is one joint simulation
        assert fc.projected == alone[fc.factory_id].projected and fc.shortfall == alone[fc.factory_id].shortfall
        assert fc.order_shortfall == sum(x.shortfall for x in alone.values())
        assert fc.p_after == both[0].p_after <= min(x.p_after for x in alone.values())
        assert fc.projections == {x.factory_id: x.projected for x in alone.values()}


def test_latest_update_per_factory_wins_and_outsiders_are_ignored(world, bundle):
    res, cands = lp.split_from_offers(world, bundle, answer_key_offers(bundle))
    pt = res.recommended
    early = lp.Update("F1064", 1_000, 5, 8)
    late = lp.Update("F1064", 3_500, 2, lp.UPDATE_DAY)
    assert lp.latest_updates([late, early]) == [late] and lp.latest_updates([early, late]) == [late]
    outsider = next(c.factory_id for c in bundle.cands if c.factory_id not in {ln.factory_id for ln in pt.plan.lines})
    fcs = lp.forecast_from_updates(world, bundle, pt, cands, [early, late, lp.Update(outsider, 0, 9, lp.UPDATE_DAY)])
    assert fcs == [lp.forecast_after_update(world, bundle, pt, cands, "F1064", 3_500, 2, lp.UPDATE_DAY)]
    assert lp.forecast_from_updates(world, bundle, pt, cands, []) == []


def test_an_on_plan_update_elsewhere_keeps_the_shortfall_and_never_lowers_the_chance(world, bundle):
    res, cands = lp.split_from_offers(world, bundle, answer_key_offers(bundle))
    pt = res.recommended
    other = next(ln for ln in pt.plan.lines if ln.factory_id != "F1064")
    late = lp.Update("F1064", 3_500, 2, lp.UPDATE_DAY)
    alone = lp.forecast_from_updates(world, bundle, pt, cands, [late])[0]
    fcs = lp.forecast_from_updates(world, bundle, pt, cands, [late, lp.Update(other.factory_id, other.qty, 0,
                                                                                lp.UPDATE_DAY)])
    assert fcs[0].factory_id == "F1064" and fcs[0].order_shortfall == alone.shortfall
    assert fcs[0].p_after >= alone.p_after - 1e-9


def test_rescue_with_two_updates_locks_both_late_factories(world, bundle):
    pt, cands, a, b2, ups = _two_late(world, bundle)
    fc = lp.forecast_from_updates(world, bundle, pt, cands, ups)[0]
    rp = lp.rescue_move(world, bundle, pt, cands, fc, ["F0824"], lp.UPDATE_DAY)
    opts = {o.kind: o for o in lp.rescue_options(world, bundle, pt, cands, fc, ["F0824"], lp.UPDATE_DAY)}
    if rp is not None:
        new = {ln.factory_id: ln.qty for ln in rp.new.plan.lines}
        for f, q in fc.projections.items():  # neither updated factory is asked for more than it can make
            assert new.get(f, 0) <= q // 100 * 100
        assert not any(f in opts["move"].note for f in fc.projections)
    assert opts["accept_shortfall"].p_after == fc.p_after


def test_warning_on_a_drop_between_two_updates(world, bundle):
    res, cands = lp.split_from_offers(world, bundle, answer_key_offers(bundle))
    ln = next(x for x in res.recommended.plan.lines if x.factory_id == "F1064")
    fc = lp.forecast_after_update(world, bundle, res.recommended, cands, "F1064", ln.qty, 0, lp.UPDATE_DAY)
    assert fc.shortfall == 0
    assert lp.warning_level(fc, 0.0) == "on track"
    assert lp.warning_level(fc, 0.0, prev_p=fc.p_after + C.WARN_DROP_PP / 2) == "on track"
    assert lp.warning_level(fc, 0.0, prev_p=fc.p_after + C.WARN_DROP_PP) == "watch"
    assert lp.warning_level(fc, 1.0, prev_p=fc.p_after + C.WARN_DROP_PP) == "below target"
    assert lp.warning_level(fc, 0.0, prev_p=fc.p_after - 0.2) == "on track"  # a rise is never a warning


def test_service_lists_updates_and_the_previous_chance(book, world, bundle):
    oid = book.confirm_order(bundle.order, "Merchandiser", sv.PlanSettings(), None)
    assert book.updates(oid) == [] and book.previous_p(oid, lp.UPDATE_DAY) is None
    book.store.insert("progress_updates", {"update_id": "u1", "order_id": oid, "factory_id": "F1064", "story_day": 8,
                                           "text": "x", "pieces_done_cum": 1_000, "delay_days": 0, "is_simulated": 0,
                                           "parse_status": "typed by factory"})
    book.store.insert("progress_updates", {"update_id": "u0", "order_id": oid, "factory_id": "F1064", "story_day": 2,
                                           "text": "y", "delay_days": 3, "is_simulated": 0,
                                           "parse_status": "from offer"})  # a delay stated with an offer: not an update
    assert book.updates(oid) == [lp.Update("F1064", 1_000, None, 8)]
    res, cands = lp.split_from_offers(world, bundle, answer_key_offers(bundle))
    fc = lp.forecast_after_update(world, bundle, res.recommended, cands, "F1064", 1_000, None, 8)
    book.record_forecast(oid, fc, None)
    assert book.previous_p(oid, 8) is None and book.previous_p(oid, lp.UPDATE_DAY) == pytest.approx(fc.p_after)


def test_warning_with_several_updates_and_a_previous_chance(tmp_path, world, bundle):
    pt, cands, a, b2, ups = _two_late(world, bundle)
    fc = lp.forecast_from_updates(world, bundle, pt, cands, ups)[0]
    fs = ag.warning_facts(fc, 2, 0.90, None, None, None, lp.warning_level(fc, 0.90, fc.p_after + 0.1),
                          fc.p_after + 0.1)
    assert {"warn.order_shortfall", "warn.p_prev"} <= set(ag.warning_required(fs))
    w = ag.write_warning(fs, LLMClient(mode="offline", cache_dir=tmp_path))  # a miss: the template
    assert w.source == "template" and f"{fc.order_shortfall:,}" in w.explanation
    assert f"{fc.p_after + 0.1:.1%}" in w.explanation
    one = lp.forecast_after_update(world, bundle, pt, cands, "F1064", 3_500, 2, lp.UPDATE_DAY)
    assert not {"warn.order_shortfall", "warn.p_prev", "warn.others"} & set(
        ag.warning_facts(one, 2, 0.90, None, None, None, "below target"))  # the one-update prompt is unchanged


def test_qc_prefill_adds_the_overtime_pieces_once_the_request_is_sent(book, world, bundle):
    """D27: with overtime chosen and its request sent, the late factory is expected to deliver its projection plus
    the overtime pieces (never above its approved pieces); before the request is sent, only the projection."""
    pf = ps.load()
    oid = book.confirm_order(bundle.order, "Merchandiser", sv.PlanSettings(), None)
    book.draft_round(oid, bundle, pf)
    book.approve_round(oid, "Merchandiser")
    res, cands = lp.split_from_offers(world, bundle, answer_key_offers(bundle))
    pt = res.recommended
    book.decide(oid, "split", pt, "Split from offers", "approved", "Merchandiser", "test")
    book.advance_to(oid, lp.UPDATE_DAY, "update", "Merchandiser")
    fc = lp.forecast_after_update(world, bundle, pt, cands, "F1064", 3_500, 2, lp.UPDATE_DAY)
    book.record_forecast(oid, fc, None)
    planned = {ln.factory_id: ln.qty for ln in pt.plan.lines}
    assert book.expected_pieces(oid, fc) == {**planned, "F1064": fc.projected}
    opt = next(o for o in lp.rescue_options(world, bundle, pt, cands, fc, ["F0824"], lp.UPDATE_DAY) if o.kind == "surge")
    book.choose_rescue(oid, opt, "Production manager")
    assert book.expected_pieces(oid, fc)["F1064"] == fc.projected  # not sent yet
    book.send_message(oid, "F1064", "overtime please", "Production manager", "surge-F1064", kind="surge_request")
    exp = book.expected_pieces(oid, fc)
    assert exp["F1064"] == min(planned["F1064"], fc.projected + lp.surge_pieces(fc)) == 9_800 + 945
    assert {f: q for f, q in exp.items() if f != "F1064"} == {f: q for f, q in planned.items() if f != "F1064"}
