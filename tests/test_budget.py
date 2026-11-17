"""Live-call caps for the public demo (bunon/llm.py). A fake provider stands in for Gemini."""
from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from bunon import agent as ag
from bunon import llm
from bunon.llm import BudgetExceeded, CacheMiss, LLMClient


class Out(BaseModel):
    x: str


class FakeProvider:
    name, model = "fake", "fake-1"

    def __init__(self):
        self.n = 0

    def generate_json(self, prompt, json_schema):
        self.n += 1
        return json.dumps({"x": prompt})


@pytest.fixture
def caps(monkeypatch, tmp_path):
    monkeypatch.setenv("BUNON_LIVE_CALLS_PER_VISITOR", "2")
    monkeypatch.setenv("BUNON_LIVE_CALLS_PER_DAY", "3")
    llm._visitor_calls.clear()
    yield tmp_path / "budget.json"
    llm.set_visitor(None)
    llm._visitor_calls.clear()


def _client(tmp_path, budget):
    return LLMClient(provider=FakeProvider(), mode="live", cache_dir=tmp_path / "cache", budget_path=budget)


def test_visitor_cap_then_cache_still_free(caps, tmp_path):
    llm.set_visitor("v1")
    c = _client(tmp_path, caps)
    c.complete_json("t", "v1", "a", Out)
    c.complete_json("t", "v1", "b", Out)
    with pytest.raises(BudgetExceeded):
        c.complete_json("t", "v1", "c", Out)
    # already-cached answers never count against the cap
    _, meta, _ = c.complete_json("t", "v1", "a", Out)
    assert meta.source == "cache" and c._provider.n == 2
    assert llm.budget_status(caps)["visitor_used"] == 2


def test_daily_cap_spans_visitors(caps, tmp_path):
    c = _client(tmp_path, caps)
    for v, p in (("v1", "a"), ("v1", "b"), ("v2", "c")):
        llm.set_visitor(v)
        c.complete_json("t", "v1", p, Out)
    llm.set_visitor("v3")  # fresh visitor, but the day is used up
    with pytest.raises(BudgetExceeded, match="daily"):
        c.complete_json("t", "v1", "d", Out)
    assert llm.budget_status(caps)["day_used"] == 3


def test_budget_exceeded_is_a_cache_miss_for_callers(caps, tmp_path):
    assert issubclass(BudgetExceeded, CacheMiss)
    assert "limit" in ag.why_no_llm(BudgetExceeded("live-call limit for this visitor reached"))
    assert "offline" in ag.why_no_llm(CacheMiss("x"))


def test_fake_provider_without_budget_path_is_uncapped(caps, tmp_path):
    c = LLMClient(provider=FakeProvider(), mode="live", cache_dir=tmp_path / "cache")
    llm.set_visitor("v1")
    for p in "abcdef":
        c.complete_json("t", "v1", p, Out)
    assert not caps.exists()
