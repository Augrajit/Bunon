"""Tests for llm.py, textnum.py and agent.py (PLAN.md §7). No network: a fake provider stands in."""
from __future__ import annotations

import json
import re
from datetime import date

import pandas as pd
import pytest

from bunon import agent as ag
from bunon import config as C
from bunon import textnum as tn
from bunon.llm import CacheMiss, LLMClient

EMAIL = C.DEMO_EMAIL_PATH.read_text(encoding="utf-8")


class FakeProvider:
    name, model = "fake", "fake-1"

    def __init__(self, *responses):
        self.responses = list(responses)
        self.prompts: list[str] = []

    def generate_json(self, prompt, json_schema):
        self.prompts.append(prompt)
        r = self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]
        return r if isinstance(r, str) else json.dumps(r)


def client(tmp_path, *responses, mode="live"):
    return LLMClient(provider=FakeProvider(*responses), mode=mode, cache_dir=tmp_path)


# --- textnum ------------------------------------------------------------------------

@pytest.mark.parametrize("text, value", [
    ("150,000 pcs", 150000), ("1,50,000 pieces", 150000), ("150k", 150000), ("1.5 lakh", 150000),
    ("২০,০০০ পিস", 20000), ("USD 2.40 per piece", 2.4), ("a penalty of 5% of FOB", 5), ("AQL 2.5", 2.5),
    ("2 crore", 2e7),
])
def test_states_number(text, value):
    assert tn.states_number(text, value)


def test_states_number_rejects_absent_value():
    assert not tn.states_number("150,000 pcs", 15000)
    assert not tn.states_number("4 colours", 150000)


@pytest.mark.parametrize("text", ["no later than 20 December 2026", "20th Dec 2026", "December 20, 2026",
                                  "2026-12-20", "20/12/2026", "20.12.2026"])
def test_states_date(text):
    assert tn.states_date(text, date(2026, 12, 20))


def test_day_first_dates():
    assert tn.dates_in("05/12/2026") == [date(2026, 12, 5)]


@pytest.mark.parametrize("text, bad", [("about 91% likely", True), ("five factories", True),
                                       ("costs double", True), ("one of the factories", False),
                                       ("the plan is likely", False)])
def test_literal_numbers(text, bad):
    assert bool(tn.literal_numbers(text)) == bad


# --- llm cache ----------------------------------------------------------------------------

def test_cache_roundtrip_and_offline(tmp_path):
    out = {"summary": "s", "recommendation": "r", "rejected_alternatives": [], "risks": [], "human_actions": []}
    live = client(tmp_path, out)
    a, meta, _ = live.complete_json("t", "v1", "prompt", ag.MemoLLM)
    assert meta.source == "live" and live.calls == 1
    b, meta2, _ = live.complete_json("t", "v1", "prompt", ag.MemoLLM)
    assert meta2.source == "cache" and live.calls == 1 and a == b
    off = LLMClient(mode="offline", cache_dir=tmp_path, provider_name="fake", model="fake-1")
    c, meta3, _ = off.complete_json("t", "v1", "prompt", ag.MemoLLM)
    assert meta3.source == "cache" and c == a
    with pytest.raises(CacheMiss):
        off.complete_json("t", "v1", "a different prompt", ag.MemoLLM)


# --- intake ------------------------------------------------------------------------------------

GOOD_INTAKE = {
    "total_qty": {"value": 150000, "source_span": "Quantity: 150,000 pcs in total"},
    "deadline": {"value": "2026-12-20", "source_span": "no later than 20 December 2026"},
    "delivery_terms": {"value": "FOB Chattogram", "source_span": "FOB Chattogram"},
    "fabric": {"value": "100% cotton single jersey, 160 GSM", "source_span": "100% cotton single jersey, 160 GSM, reactive dyed"},
    "gsm": {"value": 160, "source_span": "160 GSM"},
    "quality_aql": {"value": 2.5, "source_span": "AQL 2.5"},
    "fob_usd_per_piece": {"value": 2.4, "source_span": "USD 2.40 per piece"},
    "late_penalty_pct_per_week": {"value": 5, "source_span": "a penalty of 5% of FOB value per week of delay"},
    "penalty_text": {"value": "5% of FOB value per week of delay", "source_span": "a penalty of 5% of FOB value per week of delay applies"},
    "compliance_required": ["RSC", "BSCI"],
    "compliance_span": "must be RSC-covered or hold a valid amfori BSCI audit",
}


def test_intake_happy_path(tmp_path):
    res = ag.parse_order(EMAIL, 100_000, client(tmp_path, GOOD_INTAKE))
    assert res.order is not None and not res.issues
    assert res.order.total_qty == 150_000 and res.order.subcontract_qty == 50_000
    assert res.order.deadline == date(2026, 12, 20)
    assert res.order.fob_usd_per_piece == 2.4 and res.order.late_penalty_pct_per_week == 5


def test_intake_rejects_number_not_in_quote(tmp_path):
    bad = json.loads(json.dumps(GOOD_INTAKE))
    bad["total_qty"] = {"value": 160000, "source_span": "Quantity: 150,000 pcs in total"}  # invented
    res = ag.parse_order(EMAIL, 100_000, client(tmp_path, bad))
    assert res.fields["total_qty"].status == "rejected"
    assert res.order is None and "total_qty" in res.missing_required


def test_intake_rejects_quote_not_in_email(tmp_path):
    bad = json.loads(json.dumps(GOOD_INTAKE))
    bad["fob_usd_per_piece"] = {"value": 2.6, "source_span": "USD 2.60 per piece"}
    res = ag.parse_order(EMAIL, 100_000, client(tmp_path, bad))
    assert res.fields["fob_usd_per_piece"].status == "rejected"
    assert res.order is not None and res.order.fob_usd_per_piece is None  # optional field: order still builds


def test_intake_rejects_wrong_date(tmp_path):
    bad = json.loads(json.dumps(GOOD_INTAKE))
    bad["deadline"]["value"] = "2026-12-27"
    res = ag.parse_order(EMAIL, 100_000, client(tmp_path, bad))
    assert res.fields["deadline"].status == "rejected" and res.order is None


def test_intake_offline_miss_asks_human(tmp_path):
    res = ag.parse_order(EMAIL, 100_000, LLMClient(mode="offline", cache_dir=tmp_path))
    assert res.order is None and res.issues and res.meta is None


# --- memo -------------------------------------------------------------------------------------

@pytest.fixture(scope="module")
def facts():
    from bunon import optimizer as op
    w = op.load_world()
    order = op.demo_order()
    p = op.default_params(order)
    cands = op.build_candidates(w, order, p)
    comp = op.compare_three(w, order, p)
    match = op.baseline_to_match(w, order, p, comp["ours"].recommended.risk.p_meet_qty)
    fs = ag.factsheet_for_plan(order, comp, match, cands, p.planned_slack_days, C.TARGET_P_MEET, 442)
    rp = op.replan_exclude(w, order, p, comp["ours"].recommended, op.largest_line(comp["ours"].recommended))
    return fs, ag.factsheet_for_replan(fs, rp)


def _memo(**kw):
    base = {"summary": "We recommend {{plan.n_factories}} factories at {{plan.p_meet}}.",
            "recommendation": "Approve the plan.", "rejected_alternatives": ["Naive: {{base.p_meet}}."],
            "risks": ["Concentration."], "human_actions": ["A person approves and sends every order."]}
    base.update(kw)
    return base


def test_validator_rejects_literal_number(facts):
    fs, _ = facts
    errs = ag.validate_memo(ag.MemoLLM(**_memo(summary="We reach 91% with {{plan.n_factories}} factories.")), fs)
    assert any("literal number" in e for e in errs)


def test_validator_rejects_number_word_and_unknown_key(facts):
    fs, _ = facts
    assert ag.validate_memo(ag.MemoLLM(**_memo(recommendation="Use five factories.")), fs)
    assert any("unknown fact key" in e for e in ag.validate_memo(ag.MemoLLM(**_memo(summary="{{plan.made_up}}")), fs))
    assert any("unknown factory" in e for e in ag.validate_memo(ag.MemoLLM(**_memo(summary="{{fac:F9999}}")), fs))


def _numbers(text: str) -> list[str]:
    return re.findall(r"[+-]?\d[\d,]*(?:\.\d+)?", text)


def test_every_number_in_rendered_memo_comes_from_facts(facts, tmp_path):
    fs, _ = facts
    fids = sorted(ag.factory_ids(fs))
    memo = _memo(recommendation="Approve: " + "; ".join(f"{{{{fac:{f}}}}} {{{{line.{f}.qty}}}}" for f in fids) + ".")
    out = ag.write_memo(fs, "initial", client(tmp_path, memo))
    assert out.source == "live" and out.attempts == 1
    allowed = {n for f in fs.values() for n in _numbers(f.render())} | {n for f in fids for n in _numbers(f)}
    found = _numbers(out.rendered_md)
    assert found and set(found) <= allowed, set(found) - allowed


def test_retry_then_template_fallback(facts, tmp_path):
    fs, _ = facts
    bad = _memo(summary="We are 91% sure.")
    c = client(tmp_path, bad, bad)
    out = ag.write_memo(fs, "initial", c)
    assert out.source == "template" and c.calls == 2
    assert "REJECTED" in c._provider.prompts[1]  # the retry carries the validator's errors
    assert not ag.validate_memo(out.sections, fs)


def test_retry_succeeds_second_time(facts, tmp_path):
    fs, _ = facts
    out = ag.write_memo(fs, "initial", client(tmp_path, _memo(summary="Two factories."), _memo()))
    assert out.source == "live" and out.attempts == 2


@pytest.mark.parametrize("kind", ["initial", "replan"])
def test_templates_are_valid_and_render(facts, kind):
    fs = facts[0] if kind == "initial" else facts[1]
    m = ag.template_memo(kind, fs)
    assert not ag.validate_memo(m, fs)
    assert "{{" not in ag._render_md(m, fs)


def test_offline_without_cache_uses_template(facts, tmp_path):
    fs, _ = facts
    out = ag.write_memo(fs, "initial", LLMClient(mode="offline", cache_dir=tmp_path))
    assert out.source == "template"


# --- pseudonymisation -----------------------------------------------------------------------------

@pytest.mark.skipif(not C.PRIVATE_MAPPING.exists(), reason="mapping is local-only")
def test_prompts_contain_no_real_names_or_os_ids(facts, tmp_path):
    fs, fs2 = facts
    c = client(tmp_path, _memo())
    ag.write_memo(fs, "initial", c)
    ag.write_memo(fs2, "replan", c)
    ag.parse_order(EMAIL, 100_000, c)
    prompts = "\n".join(c._provider.prompts).lower()
    mapping = pd.read_csv(C.PRIVATE_MAPPING, dtype=str, encoding="utf-8")
    assert not [o for o in mapping["os_id"] if o.lower() in prompts]
    names = [n.strip().lower() for n in mapping["name"] if len(n.strip()) >= 12]
    assert not [n for n in names if n in prompts]
    assert re.search(r"\bf\d{4}\b", prompts)  # factories appear, as pseudonyms (prompts lower-cased above)
