"""Delay attribution (SPEC.md D23, HANDOFF task 4.2): the AI suggests, code rules cut what has no evidence, an
exporter role decides, and the factory can dispute."""
from __future__ import annotations

import json

import pytest

from bunon import agent as ag
from bunon import attribution as at
from bunon import config as C
from bunon import decisions as dl
from bunon import loop as lp
from bunon import service as sv
from bunon.llm import LLMClient
from bunon.store import Store

UPDATE = "Bhai update: aj porjonto 3,500 pcs ready hoise. Line e current nai, 2 din late hobe."
DELAY = at.Delay("O:delay-F1064-day12", "F1064", "ashulia_savar", 12, 2, UPDATE, "Line e current nai")


class FakeProvider:
    name, model = "fake", "fake-1"

    def __init__(self, response):
        self.response = response

    def generate_json(self, prompt, json_schema):
        return json.dumps(self.response)


def fake(tmp_path, response):
    return LLMClient(provider=FakeProvider(response), mode="live", cache_dir=tmp_path)


def ev(eid="O:news-1", area="ashulia_savar", start=10, end=12):
    return at.NewsEvent(eid, area, "power_outage", start, end, "power cut across Ashulia")


def test_matching_events_need_the_district_or_national_and_the_production_days():
    evs = [ev("a"), ev("b", "gazipur"), ev("c", "national"), ev("d", start=0, end=1), ev("e", start=13, end=15)]
    assert [e.event_id for e in at.matching_events(evs, "ashulia_savar", lp.REPLY_DAY, 12)] == ["a", "c"]


def test_outside_event_with_a_matching_recorded_event_is_kept(tmp_path):
    out = {"category": "outside_event", "reason_quote": "Line e current nai", "evidence_event_ids": ["O:news-1"],
           "explanation": "The factory reports a power cut, and a power cut in its area is on record."}
    r = ag.suggest_attribution(DELAY, [ev()], fake(tmp_path, out))
    assert r.category == "outside_event" and r.evidence_event_ids == ["O:news-1"]
    assert r.reason_quote == "Line e current nai" and r.explanation and r.meta.source == "live"


def test_outside_event_without_a_recorded_event_is_at_most_unclear(tmp_path):
    out = {"category": "outside_event", "reason_quote": "Line e current nai", "evidence_event_ids": ["O:news-9"],
           "explanation": "A power cut outside the factory."}
    r = ag.suggest_attribution(DELAY, [], fake(tmp_path, out))  # nothing recorded, and an invented id
    assert r.category == "unclear" and r.suggested == "outside_event" and r.evidence_event_ids == []
    assert any("cut to unclear" in n for n in r.notes)
    r = ag.suggest_attribution(DELAY, [ev()], fake(tmp_path / "b", dict(out, evidence_event_ids=[])))
    assert r.category == "unclear"  # a matching event exists but the AI cited none


def test_factory_side_needs_a_quote_from_the_update_and_no_numbers_in_the_explanation(tmp_path):
    out = {"category": "factory", "reason_quote": "the generator broke", "evidence_event_ids": [],
           "explanation": "It is two days late."}
    r = ag.suggest_attribution(DELAY, [], fake(tmp_path, out))
    assert r.category == "unclear" and r.reason_quote is None and r.explanation is None
    ok = dict(out, reason_quote="Line e current nai", explanation="The factory names no outside event.")
    r = ag.suggest_attribution(DELAY, [], fake(tmp_path / "b", ok))
    assert r.category == "factory" and r.explanation


def test_offline_miss_gives_no_suggestion(tmp_path):
    r = ag.suggest_attribution(DELAY, [], LLMClient(mode="offline", cache_dir=tmp_path))
    assert r.category is None and r.meta is None and r.notes


@pytest.fixture()
def book(tmp_path, monkeypatch):
    monkeypatch.setenv(dl.LOG_DB_ENV, str(tmp_path / "decisions.db"))
    return sv.OrderBook(Store(tmp_path / "store.db"))


@pytest.mark.skipif(not C.PUBLIC_DB.exists(), reason="run `python -m bunon.data_gen` first")
def test_people_decide_the_factory_disputes_and_the_record_counts_it(book):
    from bunon import optimizer as op
    oid = book.confirm_order(op.demo_order(), "Merchandiser", sv.PlanSettings(), None)
    book.store.insert("sourcing_rounds", {"round_id": f"{oid}:r1", "order_id": oid, "story_day": 0,
                                          "factory_ids": json.dumps(["F1064"]),
                                          "requested_qty": json.dumps({"F1064": 13_100}),
                                          "requested_date": json.dumps({"F1064": "2027-01-28"})})
    book.advance_to(oid, lp.UPDATE_DAY, "updates", "Merchandiser")
    book.store.insert("progress_updates", {"update_id": f"{oid}:u", "order_id": oid, "factory_id": "F1064",
                                           "story_day": 12, "text": UPDATE, "pieces_done_cum": 3_500, "delay_days": 2,
                                           "cause_text": "Line e current nai", "is_simulated": 1,
                                           "parse_status": "cache"})
    (d,) = book.delays(oid, {"F1064": "ashulia_savar"})
    assert d.delay_days == 2 and d.cause_quote == "Line e current nai"
    with pytest.raises(ValueError):  # the rule holds for people too
        book.decide_attribution(oid, d, "outside_event", "Production manager")
    with pytest.raises(PermissionError):
        book.decide_attribution(oid, d, "factory", "Factory owner")
    with pytest.raises(PermissionError):
        book.record_news(oid, "power cut across Ashulia", "ashulia_savar", "power_outage", "medium", None,
                         "Floor supervisor")
    eid = book.record_news(oid, "power cut across Ashulia", "ashulia_savar", "power_outage", "medium", None,
                           "Merchandiser")
    assert book.record_news(oid, "power cut across Ashulia", "ashulia_savar", "power_outage", "medium", None,
                            "Merchandiser") == eid  # the same item twice is one event
    other = book.record_news(oid, "flood in Gazipur", "gazipur", "flood_or_weather", "high", 2, "Merchandiser")
    assert [e.event_id for e in book.matching_events(oid, d)] == [eid] and other != eid
    (e,) = book.matching_events(oid, d)
    assert e.start_day == 12 and e.end_day == 12 + C.SEVERITY_TABLE["medium"]["default_days"] - 1
    with pytest.raises(ValueError):
        book.decide_attribution(oid, d, "outside_event", "Production manager", [other])  # not a matching event
    with pytest.raises(ValueError):
        book.dispute_attribution(oid, d.delay_id, "F1064", "no", "Factory owner")  # nothing decided yet
    book.decide_attribution(oid, d, "factory", "Production manager")
    row = book.attribution(oid, d.delay_id)
    assert row["category"] == "factory" and row["human_decision"] == "Production manager"
    with pytest.raises(PermissionError):
        book.dispute_attribution(oid, d.delay_id, "F1064", "The whole area had no power.", "Merchandiser")
    book.dispute_attribution(oid, d.delay_id, "F1064", "The whole area had no power.", "Factory owner")
    tr = next(r for r in book.track_record(oid) if r["factory_id"] == "F1064")
    assert tr["delay_causes"] == {"factory": 1} and tr["causes_disputed"] == 1
    book.decide_attribution(oid, d, "outside_event", "Owner / director", [eid])  # a second look, with evidence
    row = book.attribution(oid, d.delay_id)
    assert row["category"] == "outside_event" and json.loads(row["evidence_event_ids"]) == [eid]
    assert row["disputed_by_factory"] == 0  # a new decision clears the dispute; the timeline keeps both
    kinds = [x["kind"] for x in book.timeline(oid)]
    assert kinds.count("attribution_decided") == 2 and "attribution_disputed" in kinds and "news_recorded" in kinds
