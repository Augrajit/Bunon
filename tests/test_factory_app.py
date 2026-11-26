"""Click-through of the factory side (phone view): a factory answers a request and posts a daily update with
numbers it types itself, and the exporter's timeline uses them without an AI reading. Opt-in (pytest -m sweep)
because it drives the whole app (about a minute)."""
from __future__ import annotations

import pytest

from bunon import config as C
from tests.test_app_sweep import APP, DEFAULT, Runner

pytestmark = [
    pytest.mark.sweep,
    pytest.mark.skipif(not C.PUBLIC_DB.exists(), reason="run `python -m bunon.data_gen` first"),
]


def _as(at, role, view=None):
    at.session_state["role"] = role
    if view:
        at.session_state["view"] = view
    at.run()


def test_factory_answers_and_updates_in_the_app(monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv(C.LLM_MODE_ENV, "offline")
    monkeypatch.setenv("BUNON_DECISIONS_DB", str(tmp_path / "decisions.db"))
    monkeypatch.setenv("BUNON_STORE_DIR", str(tmp_path / "sessions"))
    at = AppTest.from_file(APP, default_timeout=1200)
    for k, v in DEFAULT.items():
        at.session_state[k] = v
    at.run()
    r = Runner(at)
    assert r.click(key="demo_login") and r.click(key="start_order"), r.problems
    assert r.click("Read the email") and r.click("Confirm order"), r.problems
    assert r.click(key="approve_plan") and r.click(key="send_requests"), r.problems

    # day 0, factory app: F0824 (scripted to decline) accepts instead, typed by the factory itself
    _as(at, "Factory owner")
    at.selectbox(key="factory_view_id").set_value("F0824").run()
    assert any("Answer the request" in m.value for m in at.markdown), r.problems
    at.radio[0].set_value("Accept")
    r.click("Send answer")
    assert any("Your answer: accept" in c.value for c in at.caption), [c.value for c in at.caption]

    # day 2, exporter: the typed answer is in the offers table without an AI reading; the rest are scripted
    _as(at, "Merchandiser", "Order timeline")
    assert r.click(key="go_day_replies"), r.problems
    offers = next(df.value for df in at.dataframe if "Read by" in df.value.columns)
    row = offers[offers["Factory"] == "F0824"].iloc[0]
    assert row["Read by"] == "typed by the factory" and row["Decision"] == "accept"
    assert len(offers) == 6
    assert r.click(key="approve_split"), r.problems
    assert r.click(key="go_day_update"), r.problems

    # day 12, factory app: F1856 posts an on-track update next to F1064's scripted late one; the exporter's
    # forecast uses both (D22): F1856 as typed, F1064 as read, simulated together
    _as(at, "Floor supervisor")
    at.selectbox(key="factory_view_id").set_value("F1856").run()
    at.number_input[0].set_value(13_600)
    r.click(prefix="Post update for day")
    _as(at, "Merchandiser", "Order timeline")
    assert any("typed in the factory app" in m.value for m in at.markdown), r.problems
    assert any("2 factories sent updates" in m.value for m in at.markdown), r.problems
    table = next(df.value for df in at.dataframe if "Pieces by its ship-by day" in df.value.columns)
    assert list(table["Factory"]) == ["F1064", "F1856"] and table["Short"].iloc[1] == "0"
    assert at.warning and not any("on track" in s.value for s in at.success)
    assert r.click(key="rescue_escalate_buyer") and r.click(key="go_day_qc"), r.problems
    assert not r.problems, "\n".join(r.problems)
    log = next(df.value for df in at.dataframe if "Who (role)" in df.value.columns)
    assert {"Factory owner", "Floor supervisor"} <= set(log["Who (role)"])


def test_overtime_rescue_is_drafted_and_sent_only_on_approval(monkeypatch, tmp_path):
    from streamlit.testing.v1 import AppTest

    monkeypatch.setenv(C.LLM_MODE_ENV, "offline")
    monkeypatch.setenv("BUNON_DECISIONS_DB", str(tmp_path / "decisions.db"))
    monkeypatch.setenv("BUNON_STORE_DIR", str(tmp_path / "sessions"))
    at = AppTest.from_file(APP, default_timeout=1200)
    for k, v in DEFAULT.items():
        at.session_state[k] = v
    at.run()
    r = Runner(at)
    assert r.click(key="demo_login") and r.click(key="start_order"), r.problems
    assert r.click("Read the email") and r.click("Confirm order"), r.problems
    for key in ("approve_plan", "send_requests", "go_day_replies", "approve_split", "go_day_update", "rescue_surge"):
        assert r.click(key=key), (key, r.problems)
    draft = next(df.value for df in at.dataframe if "Extra pieces" in df.value.columns)
    assert list(draft["Status"]) == ["draft"] and draft["To"].iloc[0] == "F1064"
    assert r.click(key="send_surge") and r.click(key="go_day_qc"), r.problems
    assert not r.problems, "\n".join(r.problems)
    log = next(df.value for df in at.dataframe if "Who (role)" in df.value.columns)
    assert {"rescue_chosen", "message_sent"} <= set(log["Kind"])
