"""The order store: SQLite tables for one order's life, plus an append-only event log (PLAN_V2 §4).

One database per visitor session (`data/runtime/sessions/<visitor>.db`, gitignored), so demo visitors on the
shared host never see each other's orders. `events` is append-only and is the order's timeline; the other
tables hold current state. Track records (later milestone) are computed from events with source='platform'
only; synthetic history is never written here as if it were platform evidence.

Every row carries the STORY CLOCK: `story_day` (0 = the day the order arrived) and the matching calendar
date, so the demo can jump between days. No personal data: roles, never names.
"""
from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator, Literal

from bunon import config as C

SESSIONS_DIR = C.ROOT / "data" / "runtime" / "sessions"
SESSIONS_DIR_ENV = "BUNON_STORE_DIR"  # tests point the per-session stores elsewhere

EXPORTER_ROLES = ("Owner / director", "Merchandiser", "Production manager", "Quality inspector")
FACTORY_ROLES = ("Factory owner", "Floor supervisor")
ROLES = EXPORTER_ROLES + FACTORY_ROLES

EventSource = Literal["platform", "synthetic_history", "simulated_persona"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    order_id TEXT PRIMARY KEY, created_utc TEXT NOT NULL, buyer TEXT, product TEXT NOT NULL,
    total_qty INTEGER NOT NULL, own_capacity_qty INTEGER NOT NULL, subcontract_qty INTEGER NOT NULL,
    order_date TEXT NOT NULL, deadline TEXT NOT NULL, tolerance_pct REAL, safety_margin_pct REAL,
    loss_on_failure_bdt REAL, quality_terms TEXT, source_spans TEXT, order_json TEXT NOT NULL,
    status TEXT NOT NULL, story_day INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS sourcing_rounds (
    round_id TEXT PRIMARY KEY, order_id TEXT NOT NULL, story_day INTEGER NOT NULL,
    factory_ids TEXT NOT NULL, requested_qty TEXT NOT NULL, requested_date TEXT);
CREATE TABLE IF NOT EXISTS messages (
    msg_id TEXT PRIMARY KEY, order_id TEXT NOT NULL, factory_id TEXT NOT NULL,
    direction TEXT NOT NULL CHECK (direction IN ('out', 'in')), channel TEXT NOT NULL DEFAULT 'in_app',
    author_role TEXT, text TEXT NOT NULL, language TEXT, draft_of TEXT, approved_by_role TEXT,
    approved_day INTEGER, sent_day INTEGER, story_day INTEGER NOT NULL, is_simulated INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS offers (
    offer_id TEXT PRIMARY KEY, order_id TEXT NOT NULL, msg_id TEXT, factory_id TEXT NOT NULL,
    decision TEXT, qty INTEGER, price_bdt_per_piece REAL, ship_date TEXT, conditions TEXT, quotes TEXT,
    parse_status TEXT, story_day INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS allocations (
    order_id TEXT NOT NULL, version INTEGER NOT NULL, plan_id TEXT NOT NULL, label TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('proposed', 'approved', 'rejected', 'superseded')),
    lines TEXT NOT NULL, p_meet REAL, cost_total_bdt REAL, decided_by_role TEXT, story_day INTEGER NOT NULL,
    PRIMARY KEY (order_id, version));
CREATE TABLE IF NOT EXISTS progress_updates (
    update_id TEXT PRIMARY KEY, order_id TEXT NOT NULL, factory_id TEXT NOT NULL, story_day INTEGER NOT NULL,
    text TEXT NOT NULL, pieces_done_cum INTEGER, delay_days INTEGER, cause_text TEXT, cause_category TEXT,
    quotes TEXT, parse_status TEXT, is_simulated INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS forecasts (
    order_id TEXT NOT NULL, story_day INTEGER NOT NULL, factory_id TEXT NOT NULL,
    expected_good_by_deadline REAL, p10 REAL, p90 REAL, p_meet_order REAL, last_rescue_day INTEGER,
    PRIMARY KEY (order_id, story_day, factory_id));
CREATE TABLE IF NOT EXISTS rescue_options (
    option_id TEXT PRIMARY KEY, order_id TEXT NOT NULL, story_day INTEGER NOT NULL, kind TEXT NOT NULL,
    details TEXT, p_meet_after REAL, cost_delta_bdt REAL, chosen INTEGER NOT NULL DEFAULT 0, approved_by_role TEXT);
CREATE TABLE IF NOT EXISTS qc_records (
    qc_id TEXT PRIMARY KEY, order_id TEXT NOT NULL, factory_id TEXT NOT NULL, story_day INTEGER NOT NULL,
    received INTEGER NOT NULL, good INTEGER NOT NULL, rejected INTEGER NOT NULL, lot_rejected INTEGER NOT NULL,
    inspector_role TEXT NOT NULL, CHECK (good + rejected = received));
CREATE TABLE IF NOT EXISTS payments (
    payment_id TEXT PRIMARY KEY, order_id TEXT NOT NULL, factory_id TEXT NOT NULL, amount_bdt REAL NOT NULL,
    due_day INTEGER, paid_day INTEGER);
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT, ts_utc TEXT NOT NULL, order_id TEXT, story_day INTEGER NOT NULL,
    story_date TEXT NOT NULL, kind TEXT NOT NULL, subject TEXT, actor_role TEXT, summary TEXT NOT NULL,
    payload TEXT, source TEXT NOT NULL DEFAULT 'platform');
CREATE TABLE IF NOT EXISTS attributions (
    delay_id TEXT PRIMARY KEY, order_id TEXT NOT NULL, factory_id TEXT NOT NULL, category TEXT,
    reason_quote TEXT, evidence_event_ids TEXT, ai_suggestion TEXT, human_decision TEXT,
    disputed_by_factory INTEGER NOT NULL DEFAULT 0);
CREATE TABLE IF NOT EXISTS share_settings (
    factory_id TEXT NOT NULL, field TEXT NOT NULL,
    visibility TEXT NOT NULL CHECK (visibility IN ('private', 'exporters_worked_with', 'all_exporters')),
    PRIMARY KEY (factory_id, field));
"""


def session_db_path(visitor: str) -> Path:
    import re
    if not re.fullmatch(r"[0-9a-f]{32}", visitor or ""):
        raise ValueError("visitor id must be a 32-character hex id")
    import os
    return Path(os.environ.get(SESSIONS_DIR_ENV) or SESSIONS_DIR) / f"{visitor}.db"


class Store:
    """Thin wrapper over one SQLite file. Every write is a short transaction."""

    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._con() as con:
            con.executescript(SCHEMA)

    @contextmanager
    def _con(self) -> Iterator[sqlite3.Connection]:
        con = sqlite3.connect(self.path)
        con.row_factory = sqlite3.Row
        try:
            with con:
                yield con
        finally:
            con.close()

    # --- clock ---------------------------------------------------------------------------------
    @staticmethod
    def story_date(order_date: date, day: int) -> date:
        return order_date + timedelta(days=day)

    # --- events ----------------------------------------------------------------------------------
    def add_event(self, order_id: str | None, story_day: int, story_date: date, kind: str, summary: str,
                  actor_role: str | None = None, subject: str | None = None, payload: dict | None = None,
                  source: EventSource = "platform") -> int:
        if actor_role is not None and actor_role not in ROLES:
            raise ValueError(f"unknown role {actor_role!r}")
        with self._con() as con:
            cur = con.execute(
                "INSERT INTO events (ts_utc, order_id, story_day, story_date, kind, subject, actor_role, summary,"
                " payload, source) VALUES (?,?,?,?,?,?,?,?,?,?)",
                (datetime.now(timezone.utc).isoformat(timespec="seconds"), order_id, story_day,
                 story_date.isoformat(), kind, subject, actor_role, summary,
                 json.dumps(payload or {}, default=str), source))
            return int(cur.lastrowid)

    def events(self, order_id: str) -> list[dict]:
        with self._con() as con:
            rows = con.execute("SELECT * FROM events WHERE order_id = ? ORDER BY story_day, seq", (order_id,))
            return [dict(r) for r in rows]

    # --- orders ----------------------------------------------------------------------------------
    ORDER_COLUMNS = frozenset(("order_id", "created_utc", "buyer", "product", "total_qty", "own_capacity_qty",
                               "subcontract_qty", "order_date", "deadline", "tolerance_pct", "safety_margin_pct",
                               "loss_on_failure_bdt", "quality_terms", "source_spans", "order_json", "status",
                               "story_day"))

    def _check_columns(self, cols) -> None:
        bad = set(cols) - self.ORDER_COLUMNS
        if bad:
            raise ValueError(f"unknown order columns {sorted(bad)}")

    def insert_order(self, row: dict) -> None:
        self._check_columns(row)
        cols = ", ".join(row)
        with self._con() as con:
            con.execute(f"INSERT INTO orders ({cols}) VALUES ({', '.join('?' * len(row))})", tuple(row.values()))

    def order(self, order_id: str) -> dict | None:
        with self._con() as con:
            r = con.execute("SELECT * FROM orders WHERE order_id = ?", (order_id,)).fetchone()
            return dict(r) if r else None

    def order_ids(self) -> list[str]:
        with self._con() as con:
            return [r["order_id"] for r in con.execute("SELECT order_id FROM orders ORDER BY rowid")]

    def latest_order_id(self) -> str | None:
        with self._con() as con:
            r = con.execute("SELECT order_id FROM orders ORDER BY created_utc DESC, rowid DESC LIMIT 1").fetchone()
            return r["order_id"] if r else None

    def set_order(self, order_id: str, **fields) -> None:
        self._check_columns(fields)
        sets = ", ".join(f"{k} = ?" for k in fields)
        with self._con() as con:
            con.execute(f"UPDATE orders SET {sets} WHERE order_id = ?", (*fields.values(), order_id))

    # --- allocations -------------------------------------------------------------------------------
    def add_allocation(self, order_id: str, plan_id: str, label: str, lines: list[dict], p_meet: float | None,
                       cost_total_bdt: float | None, story_day: int) -> int:
        with self._con() as con:
            v = con.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM allocations WHERE order_id = ?",
                            (order_id,)).fetchone()[0]
            con.execute("INSERT INTO allocations VALUES (?,?,?,?,?,?,?,?,?,?)",
                        (order_id, v, plan_id, label, "proposed", json.dumps(lines), p_meet, cost_total_bdt,
                         None, story_day))
            return int(v)

    def allocations(self, order_id: str) -> list[dict]:
        with self._con() as con:
            rows = con.execute("SELECT * FROM allocations WHERE order_id = ? ORDER BY version", (order_id,))
            return [dict(r, lines=json.loads(r["lines"])) for r in rows]

    def find_allocation(self, order_id: str, plan_id: str) -> dict | None:
        rows = [a for a in self.allocations(order_id) if a["plan_id"] == plan_id]
        return rows[-1] if rows else None

    def set_allocation_status(self, order_id: str, version: int, status: str, role: str | None) -> None:
        with self._con() as con:
            if status == "approved":  # an approved plan replaces any earlier approved one
                con.execute("UPDATE allocations SET status = 'superseded' WHERE order_id = ? AND status = "
                            "'approved' AND version != ?", (order_id, version))
            con.execute("UPDATE allocations SET status = ?, decided_by_role = ? WHERE order_id = ? AND version = ?",
                        (status, role, order_id, version))

    # --- messages (used from the sourcing milestone on) -----------------------------------------------
    def messages(self, order_id: str | None = None, factory_id: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM messages WHERE 1=1", []
        if order_id:
            q, args = q + " AND order_id = ?", [*args, order_id]
        if factory_id:
            q, args = q + " AND factory_id = ?", [*args, factory_id]
        with self._con() as con:
            return [dict(r) for r in con.execute(q + " ORDER BY story_day, rowid", args)]
