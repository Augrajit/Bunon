"""D31: calibration to the interview's base rate, attendance in the daily update, the floor representative,
the factory ranking and the new-entrant path."""
from __future__ import annotations

import json
from datetime import timedelta

import pytest

from bunon import agent as ag
from bunon import calibrate as cal
from bunon import config as C
from bunon import decisions as dl
from bunon import loop as lp
from bunon import optimizer as op
from bunon import profiles as pf
from bunon import service as sv
from bunon.store import FLOOR_REP_ROLE, Store

pytestmark = pytest.mark.skipif(not C.PUBLIC_DB.exists(), reason="run `python -m bunon.data_gen` first")


# --- calibration -------------------------------------------------------------------------------------------

def test_calibration_scales_only_lateness_and_shocks():
    raw = op.load_world(calibrated=False)
    w = cal.apply(raw, 0.5, 2.0)
    f = next(iter(raw.forecasts))
    assert w.forecasts[f].p_late == pytest.approx(raw.forecasts[f].p_late * 0.5)
    same = {k: v for k, v in w.forecasts[f].model_dump().items() if k != "p_late"}
    assert same == {k: v for k, v in raw.forecasts[f].model_dump().items() if k != "p_late"}
    assert w.shocks.p_national == pytest.approx(min(1.0, raw.shocks.p_national * 2.0))
    assert w.factories is raw.factories


def test_load_world_applies_the_committed_calibration_and_it_can_be_switched_off(monkeypatch):
    c = json.loads(cal.PATH.read_text(encoding="utf-8"))
    assert c["target"] == 0.9 and c["naive_p"] == pytest.approx(0.9, abs=0.001)
    f = "F0288"
    raw = op.load_world(calibrated=False).forecasts[f].p_late
    assert op.load_world().forecasts[f].p_late == pytest.approx(raw * c["late_mult"])
    monkeypatch.setenv("BUNON_CALIBRATION", "0")
    assert cal.load() == cal.IDENTITY


def test_calibration_hits_the_anchor_and_never_looks_at_bunons_plan():
    out = cal.solve(0.9)
    assert out["naive_p"] == pytest.approx(0.9, abs=0.002)
    assert out["late_mult"] == pytest.approx(json.loads(cal.PATH.read_text(encoding="utf-8"))["late_mult"])
    import inspect
    src = inspect.getsource(cal.solve)
    assert "search_buffer" not in src and "compute_plans" not in src  # only the naive plan is used to choose


# --- attendance, floor representative, mismatch --------------------------------------------------------------

def test_update_parser_reads_attendance_under_the_quote_rule():
    text = "Bhai update: aj porjonto 1,800 pcs ready hoise. 60 jon operator er moddhe 42 jon aisilo, 1 din late hobe."
    out = ag.UpdateLLM.model_validate({
        "status": "delayed", "status_span": "1 din late hobe",
        "pieces_done": {"value": 1800, "source_span": "1,800 pcs"}, "delay_days": {"value": 1, "source_span": "1 din"},
        "workers_present": {"value": 42, "source_span": "42 jon aisilo"},
        "workers_planned": {"value": 61, "source_span": "60 jon operator"},  # wrong number: dropped
        "cause": {"value": "worker absence", "source_span": "42 jon aisilo"}})
    r = ag.validate_update(text, out)
    assert r.workers_present.value == 42 and r.workers_present.status == "ok"
    assert r.workers_planned.value is None and r.workers_planned.status == "rejected"
    old = ag.UpdateLLM.model_validate({"status": "on_track", "status_span": "ready", "pieces_done": {},
                                       "delay_days": {}, "cause": {}})
    assert old.workers_present.value is None  # an answer without attendance still parses


@pytest.fixture()
def book(tmp_path, monkeypatch):
    monkeypatch.setenv(dl.LOG_DB_ENV, str(tmp_path / "decisions.db"))
    return sv.OrderBook(Store(tmp_path / "store.db"))


def _order(book, fid="F1558"):
    oid = book.confirm_order(op.demo_order(), "Merchandiser", sv.PlanSettings(), None)
    book.store.insert("sourcing_rounds", {"round_id": f"{oid}:r1", "order_id": oid, "story_day": 0,
                                          "factory_ids": json.dumps([fid]), "requested_qty": json.dumps({fid: 6_720}),
                                          "requested_date": json.dumps({fid: "2027-01-22"})})
    book.store.insert("messages", {"msg_id": f"{oid}:req-{fid}", "order_id": oid, "factory_id": fid,
                                   "direction": "out", "kind": "request", "text": "request", "story_day": 0,
                                   "sent_day": 0})
    book.advance_to(oid, lp.UPDATE_DAY, "updates", "Merchandiser")
    return oid


def test_floor_report_is_used_and_a_difference_is_flagged(book):
    oid = _order(book)
    book.factory_update(oid, "F1558", 1_800, 1, "workers absent", "Floor supervisor", 42, 60)
    with pytest.raises(PermissionError):
        book.floor_report(oid, "F1558", 1_600, 42, 60, "", "Merchandiser")  # only the floor representative
    book.floor_report(oid, "F1558", 1_600, 42, 60, "two lines short", FLOOR_REP_ROLE)
    book.floor_report(oid, "F1558", 1_600, 42, 60, "again", FLOOR_REP_ROLE)  # once a day
    ups = lp.latest_updates(book.updates(oid))
    assert len(ups) == 1 and ups[0].pieces_done == 1_600 and ups[0].reporter == "floor_rep"
    assert ups[0].delay_days == 1 and (ups[0].workers_present, ups[0].workers_planned) == (42, 60)
    mm = book.report_mismatches(oid)
    assert len(mm) == 1 and mm[0]["flagged"] and mm[0]["diff_pct"] == pytest.approx(200 / 1_800)
    kinds = [e["kind"] for e in book.store.events(oid)]
    assert kinds.count("floor_report_posted") == 1


def test_a_small_difference_is_not_flagged(book):
    oid = _order(book)
    book.factory_update(oid, "F1558", 1_800, 0, "", "Floor supervisor")
    book.floor_report(oid, "F1558", 1_750, None, None, "", FLOOR_REP_ROLE)
    assert not book.report_mismatches(oid)[0]["flagged"]  # 2.8% < 5% (placeholder)


def test_the_floor_representative_cannot_approve():
    from bunon.store import EXPORTER_ROLES
    assert FLOOR_REP_ROLE not in EXPORTER_ROLES


# --- warning signal, ranking, new entrant ---------------------------------------------------------------------

def test_warning_signal_is_output_against_the_daily_target():
    base = dict(planned=6_720, pieces_done=1_800, day=12, projected=6_720, shortfall=0, p_before=0.92, p_after=0.95,
                expected_good_after=0.0, min_good=0, whole_styles=True)
    ok = lp.Forecast("F1558", rate_plan=500, rate_seen=460, output_ratios=(("F1558", 0.92),), **base)
    low = lp.Forecast("F1558", rate_plan=500, rate_seen=400, output_ratios=(("F1558", 0.80),), **base)
    assert lp.warning_level(ok, 0.9) == "on track" and lp.warning_level(low, 0.9) == "watch"
    assert lp.warning_level(low.__class__(**{**low.__dict__, "p_after": 0.5}), 0.9) == "below target"


def test_ranking_is_capacity_then_workers_then_capability_then_quality():
    c = op.build_candidates(op.load_world(), op.demo_order(), op.default_params(op.demo_order()))[0]
    a = c.model_copy(update={"factory_id": "A", "window_capacity_risk_adj": 5_000, "workers": 100, "capability": 0})
    b = c.model_copy(update={"factory_id": "B", "window_capacity_risk_adj": 4_000, "workers": 900, "capability": 3})
    d = c.model_copy(update={"factory_id": "D", "window_capacity_risk_adj": 4_000, "workers": 900, "capability": 1})
    e = c.model_copy(update={"factory_id": "E", "window_capacity_risk_adj": 4_000, "workers": 900, "capability": 1,
                             "qc_defect": 0.0})
    d = d.model_copy(update={"qc_defect": 0.05})
    assert [x.factory_id for x in sorted([d, b, e, a], key=lp.rank_key)] == ["A", "B", "E", "D"]


def test_new_entrant_needs_a_vouch_and_a_recent_site_visit():
    p = pf.load()["F0882"]
    assert pf.eligible_new_entrant(p)
    assert not pf.eligible_new_entrant(p, C.AS_OF + timedelta(days=C.NEW_ENTRANT_VISIT_DAYS + 30))
    assert not pf.eligible_new_entrant(pf.Profile("F0882", "", (), p.site_visits))
    w = op.load_world()
    cands = {c.factory_id for c in op.build_candidates(w, op.demo_order(), op.default_params(op.demo_order()))}
    zero = [f for f, fc in w.forecasts.items() if fc.n_jobs == 0]
    assert "F0882" in cands and not (set(zero) - {"F0882"}) & cands  # other no-history factories are out
    c = next(x for x in op.build_candidates(w, op.demo_order(), op.default_params(op.demo_order()))
             if x.factory_id == "F0882")
    assert not c.proven  # so the unproven-factory cap limits it
