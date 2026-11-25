"""Persona fixture format (data/demo/personas.json): loads, validates, and catches answer-key mistakes."""
from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from bunon import personas as ps


def test_fixture_loads_and_passes_checks():
    pf = ps.load(include_examples=True)
    assert pf.format_version == ps.FORMAT_VERSION
    assert "simulated" in pf.simulated_notice.lower()
    assert ps.validate(pf) == []


def test_examples_are_excluded_from_the_demo():
    assert all(not p.example for p in ps.load().personas)
    assert any(p.example for p in ps.load(include_examples=True).personas)


def _file(text, expected):
    return ps.PersonaFile.model_validate({
        "format_version": 1, "simulated_notice": "simulated",
        "personas": [{"persona_id": "p", "factory_id": "TBD", "label": "x", "language": "Banglish",
                      "profile": "other", "messages": [{"msg_id": "m", "story_day": 2, "kind": "offer_reply",
                                                        "text": text, "expected": expected}]}]})


def test_answer_key_numbers_must_be_in_the_text():
    ok = _file("9 hajar pcs nibo, rate 62 taka, 25/01/2027", {"decision": "counter", "qty": 9000,
                                                              "price_bdt_per_piece": 62, "ship_date": "2027-01-25"})
    assert ps.validate(ok) == []
    bad = _file("dui din late hobe", {"decision": "counter", "delay_days": 2})
    assert any("delay_days" in p for p in ps.validate(bad))
    no_decision = _file("thik ache", {})
    assert any("decision" in p for p in ps.validate(no_decision))


def test_duplicate_ids_and_unknown_fields_are_rejected(tmp_path):
    base = json.loads(ps.PERSONAS_PATH.read_text(encoding="utf-8"))
    dup = dict(base, personas=base["personas"] + [base["personas"][1]])
    with pytest.raises(ValidationError):
        ps.PersonaFile.model_validate(dup)
    extra = dict(base, personas=[dict(base["personas"][1], real_name="x")])
    with pytest.raises(ValidationError):
        ps.PersonaFile.model_validate(extra)
