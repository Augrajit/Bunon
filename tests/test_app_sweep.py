"""Click-through sweep of the Streamlit app (app.py) at extreme sidebar settings.

Every sidebar control is set to its ends, plus tight and loose combinations and each toggle off;
at each setting every action is clicked: plan, approve, scripted decline, the 4 demo factory replies,
the 3 demo news items, the what-if examples and steering. Runs offline (cached LLM answers only, no
network) and writes the decision log to a temporary file.

Slow (several minutes), so it is opt-in:   pytest -m sweep
Written after a live crash on 18 Nov 2026 (infeasible re-plan, see SPEC.md D9).
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bunon import config as C

pytestmark = [
    pytest.mark.sweep,
    pytest.mark.skipif(not C.PUBLIC_DB.exists(), reason="run `python -m bunon.data_gen` first"),
]

APP = str(Path(__file__).resolve().parents[1] / "app.py")
DEFAULT = dict(slack=4, target=0.90, max_f=5, unproven=0.15, dcap=0.60, tol=0.02, margin=0.0, loss=4_400_000.0,
               partial=True, shocks=True)
CASES = [("default", {})]
for _k, _vals in (("slack", [0, 10]), ("target", [0.50, 0.99]), ("max_f", [1, 2, 3, 4]),
                  ("unproven", [0.0, 0.30]), ("dcap", [0.30, 1.0]), ("tol", [0.0, 0.05]), ("margin", [0.25]),
                  ("loss", [0.0])):
    CASES += [(f"{_k}={_v}", {_k: _v}) for _v in _vals]
CASES += [
    ("max_f=3,unproven=0.1", {"max_f": 3, "unproven": 0.10}),  # the 18 Nov crash settings (no plan since D12)
    ("max_f=1,dcap=0.3", {"max_f": 1, "dcap": 0.30}),
    ("max_f=2,unproven=0,slack=10", {"max_f": 2, "unproven": 0.0, "slack": 10}),
    ("loose", {"unproven": 0.30, "dcap": 1.0, "slack": 0}),
    ("partial_off", {"partial": False}),
    ("shocks_off", {"shocks": False}),
    ("partial_off,max_f=2", {"partial": False, "max_f": 2}),
]


class Runner:
    def __init__(self, at):
        self.at = at
        self.problems: list[str] = []

    def check(self, action: str) -> bool:
        at = self.at
        bad = [f"exception: {str(e.value)[:300]}" for e in at.exception]
        bad += [f"error: {e.value[:200]}" for e in at.error if "failed" in e.value.lower()]
        bad += [f"traceback: {c.value[:300]}" for c in at.code if "Traceback" in c.value]
        self.problems += [f"{action}: {b}" for b in bad]
        return not bad

    def click(self, label: str | None = None, key: str | None = None, prefix: str | None = None) -> bool:
        try:
            if key:
                b = self.at.button(key=key)
            else:
                m = [b for b in self.at.button if (b.label.startswith(prefix) if prefix else b.label == label)]
                if not m:
                    return False
                b = m[0]
        except KeyError:
            return False  # button not on the page in this state (e.g. no plan, or an "accept" reply)
        if b.disabled:
            return False
        b.click().run()
        return self.check(key or label or prefix)


@pytest.mark.parametrize("name, over", CASES, ids=[c[0] for c in CASES])
def test_every_action_at_extreme_settings(name, over, monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv(C.LLM_MODE_ENV, "offline")
    monkeypatch.setenv("BUNON_DECISIONS_DB", str(tmp_path / "decisions.db"))
    monkeypatch.setenv("BUNON_STORE_DIR", str(tmp_path / "sessions"))
    at = AppTest.from_file(APP, default_timeout=1200)
    for k, v in {**DEFAULT, **over}.items():
        at.session_state[k] = v
    at.run()
    r = Runner(at)
    r.check("load")
    # demo login (one click) -> welcome -> start a new order -> the order timeline
    assert r.click(key="demo_login") and any("How to use Bunon" in h.value for h in at.header), r.problems
    assert r.click(key="start_order") and any("Order received" in h.value for h in at.header), r.problems

    assert r.click("Read the email") and r.click("Confirm order"), r.problems
    # Guard against a hollow pass: offline intake must come from the cache (re-run `python -m
    # bunon.warm_cache` after changing the demo email or the intake prompt), and the plan step must render.
    assert not any("LLM intake unavailable" in w.value for w in at.warning), "intake not cached: run warm_cache"
    assert any("Day 0 · Plan" in h.value for h in at.header), "the plan section did not render"
    feasible = not any("No feasible plan" in e.value for e in at.error)
    if over.get("max_f") in (1, 2, 3):  # no allocation exists: say so plainly, show no empty plan
        assert not feasible
    r.click(key="approve_plan")
    r.click(prefix="Simulate:")
    r.click(key="approve_replan")
    for i in range(4):
        if r.click(key=f"reply_ex_{i}"):
            r.click("Read the reply")
            r.click("Re-plan with this reply")
            r.click(key="approve_reply_replan")
    for i in range(3):
        if r.click(key=f"news_ex_{i}"):
            r.click("Read the news")
            r.click("Assess the impact")
            r.click(key="approve_news_replan")
    for i in range(3):
        if r.click(key=f"whatif_ex_{i}"):
            r.click("Ask")
    # the timeline records the order and (when a plan exists) the approval, on the story clock
    assert any("Timeline" in h.value for h in at.header), "timeline missing"
    log = next(df.value for df in at.dataframe if "Who (role)" in df.value.columns)
    assert "order_confirmed" in set(log["Kind"])
    if feasible:
        assert "plan_approved" in set(log["Kind"]) and log["Day"].max() >= 3

    # show the maths: the engine detail and steering live here now
    at.session_state["view"] = "Show the maths"
    at.run()
    r.check("maths view")
    assert any(h.value in ("Plans compared", "Who can make it") for h in at.header), "maths view missing"
    r.click(key="steer_ex_0")
    r.click("Interpret")

    # the factory side: a phone-frame view for a factory role
    at.session_state["role"] = "Floor supervisor"
    at.run()
    r.check("factory view")
    assert any(t.value == "Factory app" for t in at.title), "factory view missing"
    at.session_state["role"] = "Merchandiser"
    at.run()
    assert not r.problems, "\n".join(r.problems)
    # The infeasible re-plan itself (18 Nov crash) is covered by
    # tests/test_engine.py::test_replan_can_be_infeasible_and_says_so (SPEC.md D12).

    # logout returns to the login page and forgets the session
    assert r.click(key="logout"), r.problems
    assert any(b.key == "demo_login" for b in at.button) and "authed" not in at.session_state
