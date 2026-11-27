"""Story-clock jumps (SPEC.md D25, HANDOFF task 5.2): one button reaches a checkpoint by pressing the same buttons a
presenter would, with every approval recorded under the role. Opt-in (pytest -m sweep): it drives the whole app."""
from __future__ import annotations

import pytest

from bunon import config as C
from tests.test_app_sweep import APP, DEFAULT, Runner

pytestmark = [
    pytest.mark.sweep,
    pytest.mark.skipif(not C.PUBLIC_DB.exists(), reason="run `python -m bunon.data_gen` first"),
]


def _start(monkeypatch, tmp_path):
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
    return at, r


def _log(at):
    return next(df.value for df in at.dataframe if "Who (role)" in df.value.columns)


def test_jump_to_the_second_update_shows_the_combined_forecast(monkeypatch, tmp_path):
    at, r = _start(monkeypatch, tmp_path)
    assert r.click(key="jump_14"), r.problems
    assert not r.problems, "\n".join(r.problems)
    assert any("Day 14 · Daily updates" in h.value for h in at.header)
    table = next(df.value for df in at.dataframe if "Pieces by its ship-by day" in df.value.columns)
    assert list(table["Factory"]) == ["F2427", "F0288"], table  # both late; the order's chance is simulated once
    assert list(table["Short"]) == ["2,280", "2,184"]
    assert any("2 factories sent updates" in m.value for m in at.markdown)
    assert any(h.value.startswith("Day 14 · Rescue") for h in at.header)
    log = _log(at)
    approved = log[log["Kind"] == "plan_approved"]
    assert len(approved) == 2 and set(approved["Who (role)"]) == {"Merchandiser"}  # plan + split, under the role
    assert "requests_sent" in set(log["Kind"]) and "rescue_chosen" not in set(log["Kind"])  # the rescue is a choice
    assert [b.key for b in at.button if b.key and b.key.startswith("jump_")] == ["jump_26"]


def test_jump_to_qc_after_choosing_on_day_12_keeps_the_day_12_forecast(monkeypatch, tmp_path):
    """A rescue chosen on day 12 fixes that forecast: the day-14 update arrives in the inbox but does not change
    an approved rescue."""
    at, r = _start(monkeypatch, tmp_path)
    assert r.click(key="jump_12"), r.problems
    assert any("Wait: day 14" in b.label for b in at.button)
    for key in ("rescue_move", "approve_rescue", "send_rescue", "go_day_qc"):
        assert r.click(key=key), (key, r.problems)
    assert any("Day 12 · Daily updates" in h.value for h in at.header)
    assert not any("factories sent updates" in m.value for m in at.markdown)
    assert any("Receive and QC" in h.value for h in at.header)
    assert not r.problems, "\n".join(r.problems)


def test_jump_to_qc_from_the_start(monkeypatch, tmp_path):
    at, r = _start(monkeypatch, tmp_path)
    assert r.click(key="jump_26"), r.problems
    assert not r.problems, "\n".join(r.problems)
    assert any("Receive and QC" in h.value for h in at.header)
    assert not any(b.key and b.key.startswith("jump_") for b in at.button)  # nothing left to jump to
    kinds = set(_log(at)["Kind"])
    assert {"rescue_chosen", "message_sent"} <= kinds, sorted(kinds)
