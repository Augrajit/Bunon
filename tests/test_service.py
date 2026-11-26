"""The order store and service layer (PLAN_V2 §4–5, milestone 29 Nov – 2 Dec)."""
from __future__ import annotations

import ast
import sqlite3

import pytest

from bunon import config as C
from bunon import decisions as dl
from bunon import optimizer as op
from bunon import service as sv
from bunon.data_gen import load_public
from bunon.store import EXPORTER_ROLES, FACTORY_ROLES, ROLES, Store, session_db_path

pytestmark = pytest.mark.skipif(not C.PUBLIC_DB.exists(), reason="run `python -m bunon.data_gen` first")


@pytest.fixture(scope="module")
def world():
    return op.load_world()


@pytest.fixture(scope="module")
def bundle(world):
    return sv.compute_plans(world, op.demo_order(), sv.PlanSettings(), n_compliant=442)


@pytest.fixture()
def book(tmp_path, monkeypatch):
    monkeypatch.setenv(dl.LOG_DB_ENV, str(tmp_path / "decisions.db"))
    return sv.OrderBook(Store(tmp_path / "store.db"))


def test_service_has_no_streamlit_import():
    for mod in ("service", "store"):
        tree = ast.parse((C.ROOT / "bunon" / f"{mod}.py").read_text(encoding="utf-8"))
        names = {a.name.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.Import) for a in n.names}
        names |= {n.module.split(".")[0] for n in ast.walk(tree) if isinstance(n, ast.ImportFrom) and n.module}
        assert "streamlit" not in names, mod


def test_schema_has_every_plan_v2_table(tmp_path):
    Store(tmp_path / "s.db")
    with sqlite3.connect(tmp_path / "s.db") as con:
        tables = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"orders", "sourcing_rounds", "messages", "offers", "allocations", "progress_updates", "forecasts",
            "rescue_options", "qc_records", "payments", "events", "attributions", "share_settings"} <= tables


def test_roles_and_session_paths():
    assert ROLES == EXPORTER_ROLES + FACTORY_ROLES and dl.ROLES == ROLES
    vid = "0123456789abcdef0123456789abcdef"
    assert session_db_path(vid).name == f"{vid}.db"
    for bad in ("../escape", "abc123", "١٢٣" * 11):
        with pytest.raises(ValueError):
            session_db_path(bad)


def test_compute_plans_matches_the_engine(world, bundle):
    order, p = sv.PlanSettings().apply(op.demo_order())
    direct = op.compare_three(world, order, p)["ours"].recommended
    assert bundle.feasible and bundle.rec.plan.plan_id == direct.plan.plan_id
    assert bundle.rec.risk.p_meet_qty == direct.risk.p_meet_qty
    assert bundle.facts["plan.p_meet"].value == direct.risk.p_meet_qty


def test_order_lifecycle_is_logged_on_the_story_clock(book, bundle):
    oid = book.confirm_order(op.demo_order(), "Merchandiser", sv.PlanSettings(), "cache", buyer="fictional")
    assert book.day(oid) == 0 and book.order(oid).subcontract_qty == 50_000
    v = book.propose(oid, bundle.rec, "Initial plan")
    assert book.propose(oid, bundle.rec, "Initial plan") == v  # re-rendering does not duplicate
    book.decide(oid, "plan", bundle.rec, "Initial plan", "approved", "Owner / director", "test")
    alloc = book.store.allocations(oid)
    assert [a["status"] for a in alloc] == ["approved"] and alloc[0]["decided_by_role"] == "Owner / director"
    assert book.advance_to(oid, sv.DECLINE_DAY, "F1856 declines") == sv.DECLINE_DAY
    assert book.advance_to(oid, 1, "going back is ignored") == sv.DECLINE_DAY
    ev = book.timeline(oid)
    assert [e["kind"] for e in ev] == ["order_confirmed", "plan_proposed", "plan_approved", "clock"]
    assert ev[-1]["story_date"] == "2027-01-06" and all(e["source"] == "platform" for e in ev)
    log = dl.read_log()
    assert set(log["step"]) == {"order", "plan"}


def test_approving_a_new_plan_supersedes_the_old_one(world, book, bundle):
    oid = book.confirm_order(op.demo_order(), "Merchandiser", sv.PlanSettings(), None)
    book.decide(oid, "plan", bundle.rec, "Initial plan", "approved", "Owner / director", "x")
    rp, _ = sv.replan_after_decline(world, bundle)
    book.decide(oid, "replan", rp.new, "Re-plan after decline", "approved", "Production manager", "y")
    assert [a["status"] for a in book.store.allocations(oid)] == ["superseded", "approved"]


def test_only_exporter_roles_confirm_approve_or_steer(book, bundle):
    """Reviewer finding (D13): the service itself refuses, not only the UI."""
    with pytest.raises(PermissionError):
        book.confirm_order(op.demo_order(), "Someone's name", sv.PlanSettings(), None)
    with pytest.raises(PermissionError):
        book.confirm_order(op.demo_order(), "Factory owner", sv.PlanSettings(), None)
    oid = book.confirm_order(op.demo_order(), "Merchandiser", sv.PlanSettings(), None)
    for role in FACTORY_ROLES:
        with pytest.raises(PermissionError):
            book.decide(oid, "plan", bundle.rec, "Initial plan", "approved", role, "x")
        with pytest.raises(PermissionError):
            book.record_steer(oid, "x", [], role)
    assert all(a["status"] == "proposed" for a in book.store.allocations(oid)) or not book.store.allocations(oid)


def test_a_reconfirmed_order_starts_again_on_day_zero(book):
    """Reviewer finding (D13): confirming again (or after a logout) must not inherit the old clock."""
    a = book.confirm_order(op.demo_order(), "Merchandiser", sv.PlanSettings(), None)
    book.advance_to(a, 3, "decline")
    b = book.confirm_order(op.demo_order(), "Merchandiser", sv.PlanSettings(), None)
    assert a != b and book.day(b) == 0 and [e["kind"] for e in book.timeline(b)] == ["order_confirmed"]


def test_steering_lands_on_the_timeline(book):
    oid = book.confirm_order(op.demo_order(), "Merchandiser", sv.PlanSettings(), None)
    book.record_steer(oid, "at most four factories", [{"field": "max_factories", "value": 4}], "Production manager")
    ev = book.timeline(oid)[-1]
    assert ev["kind"] == "settings_changed" and "max_factories=4" in ev["summary"]


def test_order_columns_are_whitelisted(tmp_path):
    st = Store(tmp_path / "s.db")
    with pytest.raises(ValueError):
        st.set_order("x", **{"story_day = 0; DROP TABLE orders; --": 1})


def test_factory_record_comes_from_synthetic_history():
    h = load_public()["history"]
    fid = h["factory_id"].value_counts().index[0]
    r = sv.factory_record(h, fid)
    assert r["source"] == "synthetic_history" and r["jobs"] > 0
    assert 0 <= r["on_time_rate"] <= 1 and 0 < r["qc_pass_rate"] <= 1
    assert sv.factory_record(h, "F9999")["jobs"] == 0


def test_old_session_stores_are_cleaned_up(tmp_path, monkeypatch):
    import os
    import time
    from bunon.store import cleanup_sessions
    monkeypatch.setenv("BUNON_STORE_DIR", str(tmp_path))
    old, new = tmp_path / ("a" * 32 + ".db"), tmp_path / ("b" * 32 + ".db")
    Store(old), Store(new)
    two_days_ago = time.time() - 48 * 3600
    os.utime(old, (two_days_ago, two_days_ago))
    assert cleanup_sessions(24.0) == 1 and not old.exists() and new.exists()


def test_app_reloads_every_bunon_module():
    """Reviewer-style regression (D14): app.py reloads bunon modules after a deploy; a module missing from the list
    keeps old classes (seen: loop.py's BufferPoint rejected by pydantic). Every module the app uses must be listed."""
    import re
    src = (C.ROOT / "app.py").read_text(encoding="utf-8")
    listed = set(re.findall(r'"(\w+)"', src[src.index("order = ["):src.index("]", src.index("order = ["))]))
    used = set(re.findall(r"from bunon(?:\.(\w+))? import (\w+)", src)) 
    modules = {m or n for m, n in used} - {"load_public"} 
    modules = {m for m in modules if (C.ROOT / "bunon" / f"{m}.py").exists()}
    deps = {"store", "loop", "personas"}  # imported by service / decisions
    assert (modules | deps) - {"warm_cache", "checkpoint"} <= listed, sorted((modules | deps) - listed)
