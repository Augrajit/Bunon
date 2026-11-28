"""Tests for the tool-calling analyst (SPEC.md D8). A scripted fake provider stands in for Gemini."""
from __future__ import annotations

import re

import pytest

from bunon import agent as ag
from bunon import analyst as an
from bunon import config as C
from bunon import optimizer as op
from bunon.llm import LLMClient

pytestmark = pytest.mark.skipif(not C.PUBLIC_DB.exists(), reason="run `python -m bunon.data_gen` first")


class FakeSession:
    def __init__(self, script, final="A human decides; nothing is sent automatically. Data is synthetic."):
        self.script, self.final, self.i = list(script), final, 0
        self.received: list = []

    def step(self, tool_results=None, user_text=None, force_text=False):
        self.received.append((tool_results, user_text, force_text))
        if force_text and (self.i >= len(self.script) or "calls" in self.script[min(self.i, len(self.script) - 1)]):
            self.i += 1
            return {"text": self.final}
        out = self.script[min(self.i, len(self.script) - 1)]
        self.i += 1
        return out


class FakeToolProvider:
    name, model = "fake", "fake-tools-1"

    def __init__(self, script, final=None):
        self.script, self.final = script, final
        self.sessions: list[FakeSession] = []

    def start_tools(self, system, question, declarations):
        s = FakeSession(self.script, self.final) if self.final else FakeSession(self.script)
        self.sessions.append(s)
        return s

    def generate_json(self, prompt, json_schema):  # not used here
        raise AssertionError


@pytest.fixture(scope="module")
def ctx():
    w = op.load_world()
    order = op.demo_order()
    p = op.default_params(order)
    comp = op.compare_three(w, order, p)
    rec = comp["ours"].recommended
    match = op.baseline_to_match(w, order, p, rec.risk.p_meet_qty)
    base = ag.factsheet_for_plan(order, comp, match, op.build_candidates(w, order, p), 4, C.TARGET_P_MEET, 442)
    return w, order, p, comp, base


def toolbox(ctx):
    w, order, p, comp, base = ctx
    return an.Toolbox(w, order, p, comp, base, C.TARGET_P_MEET)


GOOD = [
    {"calls": [{"id": "a", "name": "run_simulation", "args": {"area": "ashulia_savar", "severity": "high"}}]},
    {"calls": [{"id": "b", "name": "solve_plan", "args": {"shock_area": "ashulia_savar", "shock_severity": "high"}}]},
    {"text": "With a severe Ashulia stoppage the current plan falls to {{c1.p_meet}}; a plan made knowing about it "
             "reaches {{c2.p_meet}} for {{c2.cost_delta}}. A human decides and nothing is sent automatically; "
             "the data is synthetic."},
]


def _numbers(text):
    return re.findall(r"[+-]?\d[\d,]*(?:\.\d+)?", text)


def test_tool_loop_answer_uses_only_tool_facts(ctx, tmp_path):
    prov = FakeToolProvider(GOOD)
    run = an.answer("What if Ashulia stops?", toolbox(ctx), LLMClient(provider=prov, mode="live", cache_dir=tmp_path))
    assert run.source == "live" and [c.name for c in run.calls] == ["run_simulation", "solve_plan"]
    assert all(c.ok for c in run.calls) and "{{" not in run.answer_md
    allowed = {n for f in run.facts.values() for n in _numbers(f.render())}
    assert set(_numbers(run.answer_md)) <= allowed
    # the model received tool results carrying fact keys, not raw floats to copy
    sent = prov.sessions[0].received[1][0][0]["response"]
    assert sent["ok"] and "c1.p_meet" in sent["facts"]


def test_simulation_reports_event_length_separately_from_delay(ctx):
    tb = toolbox(ctx)
    _, ok, facts, _ = tb.call("run_simulation", {"area": "narayanganj", "severity": "medium", "duration_days": 3})
    assert ok and facts["c1.event_days"] == "3 days" and facts["c1.delay_days"] == "2 days"
    assert "c1.n_affected" in facts
    _, ok, facts, _ = tb.call("run_simulation", {"area": "gazipur", "severity": "high"})  # no duration: table default
    assert ok and facts["c2.event_days"] == f"{C.SEVERITY_TABLE['high']['default_days']} days"


def test_answer_may_name_factories_from_tool_results(ctx):
    tb = toolbox(ctx)
    _, ok, facts, _ = tb.call("solve_plan", {"shock_area": "ashulia_savar", "shock_severity": "high"})
    new_id = facts["c1.factories"].split(", ")[0]
    assert ok and not ag.check_text("answer", f"Move work to {{{{fac:{new_id}}}}}.", tb.facts)
    assert ag.check_text("answer", "Move work to {{fac:F9999}}.", tb.facts)


def test_capped_analyst_reports_unavailable(ctx, tmp_path, monkeypatch):
    from bunon import llm
    monkeypatch.setenv("BUNON_LIVE_CALLS_PER_DAY", "0")
    prov = FakeToolProvider(GOOD)
    c = LLMClient(provider=prov, mode="live", cache_dir=tmp_path / "c", budget_path=tmp_path / "b.json")
    run = an.answer("What if Ashulia stops?", toolbox(ctx), c)
    assert run.source == "unavailable" and not run.calls and "daily" in run.errors[0]
    assert not prov.sessions or not prov.sessions[0].received  # no request reached the provider
    llm.set_visitor(None)


def test_cached_question_replays_offline(ctx, tmp_path):
    an.answer("What if Ashulia stops?", toolbox(ctx), LLMClient(provider=FakeToolProvider(GOOD), mode="live", cache_dir=tmp_path))
    off = LLMClient(mode="offline", cache_dir=tmp_path, provider_name="fake", model="fake-tools-1")
    run = an.answer("What if Ashulia stops?", toolbox(ctx), off)
    assert run.source == "cache" and len(run.calls) == 2 and "{{" not in run.answer_md
    other = an.answer("A different question", toolbox(ctx), off)
    assert other.source == "unavailable"


def test_tool_call_limit(ctx, tmp_path):
    two = {"calls": [{"id": "x", "name": "compare_plans", "args": {"first": "current", "second": "current"}},
                     {"id": "y", "name": "compare_plans", "args": {"first": "current", "second": "current"}}]}
    prov = FakeToolProvider([two] * 10, final="The difference is {{c1.p_diff}}; a human decides and nothing is sent automatically.")
    run = an.answer("Loop forever", toolbox(ctx), LLMClient(provider=prov, mode="live", cache_dir=tmp_path))
    assert len(run.calls) == an.MAX_TOOL_CALLS
    assert any(force for _, _, force in prov.sessions[0].received)  # tools were switched off
    assert run.source == "live"


def test_digits_in_answer_retry_then_template(ctx, tmp_path):
    bad = {"text": "The plan falls to 66% so act now."}
    prov = FakeToolProvider([GOOD[0], bad, bad], final="Still 66% bad.")
    run = an.answer("What if?", toolbox(ctx), LLMClient(provider=prov, mode="live", cache_dir=tmp_path))
    assert run.source == "template" and run.errors
    assert any(u for _, u, _ in prov.sessions[0].received if u)  # retry message was sent
    assert not ag.check_text("t", an._template(run.calls), run.facts)


def test_bad_arguments_reported_to_model(ctx, tmp_path):
    script = [{"calls": [{"id": "a", "name": "solve_plan", "args": {"exclude_factories": ["F9999"]}}]},
              {"text": "That factory is unknown. A human decides; nothing is sent automatically; data is synthetic."}]
    prov = FakeToolProvider(script)
    run = an.answer("Drop F9999?", toolbox(ctx), LLMClient(provider=prov, mode="live", cache_dir=tmp_path))
    assert not run.calls[0].ok and "unknown factory" in run.calls[0].error
    assert prov.sessions[0].received[1][0][0]["response"]["ok"] is False


def test_solve_plan_respects_exclusion_and_caps(ctx):
    tb = toolbox(ctx)
    cid, ok, facts, err = tb.call("solve_plan", {"exclude_factories": ["F2184"], "max_district_share": 0.5})
    assert ok, err
    assert "F2184" not in facts[f"{cid}.factories"]


def test_an_answer_in_another_script_without_facts_is_retried_then_template(ctx, tmp_path):
    """Seen live on 27 Nov 2026 (D30): the model answered with two characters of another script."""
    junk = {"text": "သမار"}
    prov = FakeToolProvider([GOOD[0], junk, junk], final="သမ")
    run = an.answer("What if?", toolbox(ctx), LLMClient(provider=prov, mode="live", cache_dir=tmp_path))
    assert run.source == "template" and any("cites at least one tool fact" in e for e in run.errors)


def test_junk_before_a_good_answer_is_still_rejected():
    assert not an._is_answer("သမft\nWe checked {{c1.p_meet}} for the plan and a human decides.")
    assert an._is_answer("We checked {{c1.p_meet}} for the plan and a human decides.")
