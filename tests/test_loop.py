"""The thin loop (PLAN_V2 §5, milestone 26 Nov – 2 Dec): sourcing, offers, split, update, forecast, rescue,
QC and the track record. Engine steps are code; the update parser follows the quote-check rule."""
from __future__ import annotations

import json

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
                                 e.delay_days if e else None, r.qty))
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
