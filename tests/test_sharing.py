"""The factory controls who sees its record (SPEC.md D26, HANDOFF task 4.3)."""
from __future__ import annotations

import json

import pytest

from bunon import config as C
from bunon import decisions as dl
from bunon import loop as lp
from bunon import service as sv
from bunon.store import Store

pytestmark = pytest.mark.skipif(not C.PUBLIC_DB.exists(), reason="run `python -m bunon.data_gen` first")


@pytest.fixture()
def book(tmp_path, monkeypatch):
    monkeypatch.setenv(dl.LOG_DB_ENV, str(tmp_path / "decisions.db"))
    return sv.OrderBook(Store(tmp_path / "store.db"))


def _order_with(book, fid: str, late: bool) -> str:
    """An order on which `fid` was asked, accepted and delivered (late or on time), from platform rows."""
    from bunon import optimizer as op
    oid = book.confirm_order(op.demo_order(), "Merchandiser", sv.PlanSettings(), None)
    book.store.insert("sourcing_rounds", {"round_id": f"{oid}:r1", "order_id": oid, "story_day": 0,
                                          "factory_ids": json.dumps([fid]), "requested_qty": json.dumps({fid: 1_000}),
                                          "requested_date": json.dumps({fid: "2027-01-28"})})
    book.store.insert("messages", {"msg_id": f"{oid}:req-{fid}", "order_id": oid, "factory_id": fid, "direction": "out",
                                   "kind": "request", "text": "request", "story_day": 0, "sent_day": 0})
    book.advance_to(oid, lp.REPLY_DAY, "replies", "Merchandiser")
    book.factory_reply(oid, fid, "accept", 1_000, 0, "", "Factory owner")
    book.advance_to(oid, lp.QC_DAY, "qc", "Merchandiser")
    book.record_qc(oid, fid, 1_000, 10, False, "Quality inspector", lp.QC_DAY if late else 20)
    return oid


def test_a_hidden_record_shows_as_not_shared_to_new_exporters_only(book):
    mine = _order_with(book, "F1856", late=True)      # this exporter's order
    other = _order_with(book, "F1856", late=False)    # stands for another exporter's order in the same platform
    assert book.record_shared("F1856")                # shared by default
    seen = book.record_for_exporter("F1856", {mine})
    assert seen["own"]["deliveries"] == 1 and seen["own"]["on_time_rate"] == 0.0
    assert seen["others"]["deliveries"] == 1 and seen["others"]["on_time_rate"] == 1.0 and seen["label"] is None

    with pytest.raises(PermissionError):
        book.set_record_sharing("F1856", False, "Merchandiser")  # only the factory decides
    events_before = {o: len(book.timeline(o)) for o in (mine, other)}
    track_before = book.track_record(mine)
    book.set_record_sharing("F1856", False, "Factory owner")
    new = book.record_for_exporter("F1856", set())    # an exporter it has never worked with
    assert new == {"own": None, "shared": False, "others": None, "label": "record not shared"}
    worked = book.record_for_exporter("F1856", {mine})  # an exporter it has worked with keeps its own orders' events
    assert worked["own"] == seen["own"] and worked["others"] is None and worked["label"] == "record not shared"
    # hiding the record changes who sees it, never the events: nothing is edited, deleted or hidden one by one
    assert {o: len(book.timeline(o)) for o in (mine, other)} == events_before
    assert book.track_record(mine) == track_before    # the late delivery still counts on the exporter's own order
    book.set_record_sharing("F1856", True, "Floor supervisor")
    assert book.record_for_exporter("F1856", set())["others"]["deliveries"] == 2


def test_the_factory_cannot_edit_delete_or_hide_single_events(book):
    """There is no API for it, and the only setting is the whole record's visibility to new exporters."""
    for name in dir(sv.OrderBook):
        assert not any(w in name for w in ("delete", "edit_event", "hide_event", "remove")), name
    assert not any(n.startswith(("delete", "remove")) for n in dir(Store))
    with pytest.raises(Exception):
        book.store.set_share_visibility("F1856", "record", "hidden")  # only the three allowed visibilities
