"""Bunon: Streamlit UI walking through the scripted story (PLAN.md §8).

Run:  streamlit run app.py
LLM answers are replayed from data/llm_cache first. With BUNON_LLM_MODE=live and GEMINI_API_KEY (in .env
locally, or in the host's secrets) new text goes to the LLM, within the live-call caps in bunon/config.py.
"""
from __future__ import annotations

import json
import sys
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st


def _reload_bunon_if_changed() -> None:
    """Streamlit reruns this script on every change, but keeps already-imported `bunon` modules in
    memory. After a push, the host then runs new app code against old modules (seen 15 Nov 2026:
    AttributeError on newly added functions). Fix: hash the sources; if they differ from what this
    process loaded, reload the package in dependency order and drop caches built from old classes."""
    import hashlib
    import importlib
    from pathlib import Path

    import bunon
    order = ["config", "schemas", "textnum", "llm", "data_gen", "reliability", "simulation",
             "optimizer", "agent", "analyst", "store", "decisions", "personas", "loop", "service"]
    src = Path(bunon.__file__).parent
    digest = hashlib.sha1(b"".join((src / f"{m}.py").read_bytes() for m in order)).hexdigest()
    if getattr(bunon, "_source_digest", None) != digest:
        for m in order:
            importlib.reload(importlib.import_module(f"bunon.{m}"))
        st.cache_data.clear()
        st.cache_resource.clear()
        bunon._source_digest = digest


_reload_bunon_if_changed()

from bunon import agent as ag  # noqa: E402
from bunon import analyst  # noqa: E402  (after the reload check, so names bind to current code)
from bunon import config as C  # noqa: E402
from bunon import decisions as dl
from bunon import loop as lp
from bunon import optimizer as op
from bunon import personas
from bunon import reliability as rl
from bunon import service as sv
from bunon.data_gen import load_public
from bunon.llm import LLMClient, budget_status, current_mode, set_visitor
from bunon.schemas import OrderConstraints, ReplanResult
from bunon.store import EXPORTER_ROLES, FACTORY_ROLES, ROLES, Store, cleanup_sessions, session_db_path
from bunon.textnum import literal_numbers

st.set_page_config(page_title="Bunon: subcontracting you can trust", page_icon="🧵", layout="wide")


def _secrets_to_env() -> None:
    """Hosted: settings live in Streamlit secrets. Copy the few we use into the environment (never printed)."""
    import os
    try:
        sec = dict(st.secrets)
    except Exception:  # no secrets file (local run): .env is used instead
        return
    for k in ("GEMINI_API_KEY", C.LLM_MODE_ENV, "BUNON_LIVE_CALLS_PER_VISITOR", "BUNON_LIVE_CALLS_PER_DAY"):
        if k in sec and not os.environ.get(k):
            os.environ[k] = str(sec[k])


_secrets_to_env()
if "visitor" not in st.session_state:  # anonymous per-browser-session id, used only for the live-call cap
    import uuid
    st.session_state["visitor"] = uuid.uuid4().hex
set_visitor(st.session_state["visitor"])

# =============================================================================
# Look and feel: one CSS block (base theme and font are in .streamlit/config.toml)
# =============================================================================

THEME_CSS = """
<style>
:root { --bn-accent:#1E3A5F; --bn-accent-hover:#16304F; --bn-accent-soft:#EEF2F7; --bn-text:#0F172A;
        --bn-muted:#475569; --bn-border:#DDE2EA; --bn-canvas:#F6F7F9; }
[data-testid="stMainBlockContainer"] { padding-top:3rem; padding-bottom:4rem; max-width:1240px; }
h1 { font-size:2rem !important; line-height:1.2 !important; font-weight:700 !important; letter-spacing:-0.02em; color:var(--bn-text); }
h2 { font-size:1.3rem !important; font-weight:600 !important; letter-spacing:-0.01em; color:var(--bn-text);
     border-bottom:1px solid var(--bn-border); padding-bottom:.45rem !important; margin-top:2rem !important; }
h3 { font-size:1.08rem !important; font-weight:600 !important; color:var(--bn-text); }
[data-testid="stCaptionContainer"], [data-testid="stCaptionContainer"] * { color:var(--bn-muted) !important; }
/* surfaces: metrics and bordered containers are flat white panels with a hairline border */
[data-testid="stMetric"] { background:#FFFFFF; border:1px solid var(--bn-border); border-radius:6px; padding:14px 16px; }
[data-testid="stMetricLabel"] p { font-size:.8rem !important; text-transform:uppercase; letter-spacing:.04em; color:var(--bn-muted); }
[data-testid="stMetric"], .bn-step { transition:border-color .15s ease; }
[data-testid="stMetric"]:hover, .bn-step:hover { border-color:#AEB8C7; }
.stButton > button, [data-testid="stFormSubmitButton"] > button, [data-testid="stDownloadButton"] > button {
  font-weight:600; transition:background-color .15s ease, border-color .15s ease; }
/* a short fade when a page or panel appears */
@keyframes bnFade { from { opacity:0; } to { opacity:1; } }
[data-testid="stMainBlockContainer"] > div, [data-testid="stMetric"] { animation:bnFade .22s ease-out both; }
/* sidebar */
[data-testid="stSidebar"] { background:var(--bn-canvas); }
.bn-side-label { font-size:.72rem; font-weight:600; letter-spacing:.08em; text-transform:uppercase; color:#64748B; margin:.6rem 0 .3rem; }
.bn-nav a { display:block; padding:6px 10px; margin:1px 0; border-radius:4px; border-left:2px solid transparent;
            color:#334155 !important; text-decoration:none !important; font-size:.92rem; }
.bn-nav a:hover { background:#E8ECF2; border-left-color:var(--bn-accent); color:var(--bn-text) !important; }
/* brand and labels */
.bn-brand { display:flex; align-items:center; gap:10px; font-size:1.25rem !important; font-weight:700; color:var(--bn-text);
            letter-spacing:-0.01em; margin:0 0 .4rem 0; }
.bn-mark { display:inline-flex; align-items:center; justify-content:center; width:30px; height:30px; border-radius:6px;
           background:var(--bn-accent); color:#FFFFFF; font-size:.95rem; font-weight:700; }
.bn-badge { display:inline-block; padding:3px 10px; border-radius:4px; background:#FFF7EB; color:#7A4B0C;
            border:1px solid #E7C58C; font-size:.74rem; font-weight:600; letter-spacing:.06em; text-transform:uppercase; }
/* login page: the photo fills the window behind a dark overlay */
.bn-login-eyebrow { color:#C9D6E8 !important; font-size:.78rem; font-weight:600; letter-spacing:.12em; text-transform:uppercase; margin:0 0 1rem; }
.bn-login-title { color:#FFFFFF !important; font-size:2.6rem !important; line-height:1.12; font-weight:700; letter-spacing:-0.025em; margin:0 0 1rem; }
.bn-login-text { color:#E2E8F0 !important; font-size:1.08rem !important; line-height:1.6; max-width:34rem; margin:0 0 1.4rem; }
.bn-login-brand { display:flex; align-items:center; gap:10px; color:#FFFFFF; font-size:1.3rem; font-weight:700; margin:0 0 3.2rem; }
.bn-login-brand .bn-mark { background:#FFFFFF; color:var(--bn-accent); }
.bn-credit { font-size:.78rem; color:var(--bn-muted); margin-top:.4rem; }
.bn-credit a { color:inherit !important; }
.bn-credit-fixed { position:fixed; left:18px; bottom:12px; z-index:5; font-size:.74rem; color:rgba(255,255,255,.72); }
.bn-credit-fixed a { color:rgba(255,255,255,.85) !important; }
/* welcome page */
.bn-banner { border-radius:8px; padding:56px 52px; margin-bottom:1.2rem; background-size:cover; background-position:center 62%; }
.bn-banner .bn-login-title { font-size:2.3rem !important; margin-bottom:.8rem; }
.bn-banner .bn-login-text { margin:0; }
.bn-step { background:#FFFFFF; border:1px solid var(--bn-border); border-top:3px solid var(--bn-accent); border-radius:6px;
           padding:18px 18px 20px; min-height:270px; }
.bn-step-num { font-size:.72rem; font-weight:700; letter-spacing:.1em; color:var(--bn-accent); margin-bottom:.5rem; }
.bn-step h4 { margin:0 0 .45rem 0; padding:0; font-size:1rem; line-height:1.3; font-weight:600; color:var(--bn-text); }
.bn-step p { margin:0; color:var(--bn-muted); font-size:.9rem; line-height:1.5; }
/* factory app: a phone frame on the laptop */
.st-key-phone { max-width:400px; margin:0 auto; background:#FFFFFF; border:10px solid #0F172A !important;
                border-radius:32px !important; padding:18px 16px !important; }
@media (prefers-reduced-motion: reduce) {
  *, *::before, *::after { animation:none !important; transition:none !important; }
}
</style>
"""
st.markdown(THEME_CSS, unsafe_allow_html=True)


# =============================================================================
# Demo login and welcome (no real authentication: nothing is checked or stored)
# =============================================================================

TAGLINE = ("Pick the subcontract factories that will actually deliver, price the buffer, "
           "and re-plan when things go wrong.")
BADGE = '<span class="bn-badge">Synthetic data prototype</span>'
BRAND = '<span class="bn-mark">B</span>Bunon'
PHOTO = C.ROOT / "assets" / "factory_floor.jpg"
PHOTO_CREDIT = ('Photo: <a href="https://commons.wikimedia.org/wiki/File:Working_conditions_of_Garment_workers_in_Bangladesh.jpg" '
                'target="_blank">Fahad Faisal</a>, '
                '<a href="https://creativecommons.org/licenses/by-sa/4.0/" target="_blank">CC BY-SA 4.0</a>, '
                'via Wikimedia Commons (resized)')
SVG_CREDIT = "Illustration: original SVG made for Bunon (2026)"


@st.cache_resource(show_spinner=False)
def _hero() -> tuple[str, str]:
    """(data URI, credit HTML): the licensed factory photo if present, else the original SVG."""
    import base64
    if PHOTO.exists():
        return "data:image/jpeg;base64," + base64.b64encode(PHOTO.read_bytes()).decode("ascii"), PHOTO_CREDIT
    svg = (C.ROOT / "assets" / "hero_sewing.svg").read_bytes()
    return "data:image/svg+xml;base64," + base64.b64encode(svg).decode("ascii"), SVG_CREDIT


def _enter(page: str) -> None:
    st.session_state["authed"] = True
    st.session_state["page"] = page


def login_page() -> None:
    uri, credit = _hero()
    st.markdown(f"""<style>
[data-testid="stApp"] {{ background:
   linear-gradient(90deg, rgba(9,20,36,.92) 0%, rgba(9,20,36,.80) 42%, rgba(9,20,36,.45) 100%),
   url("{uri}") center / cover no-repeat fixed; }}
[data-testid="stHeader"] {{ background:transparent; }}
[data-testid="stMainBlockContainer"] {{ padding-top:9vh; }}
.st-key-login_card {{ background:#FFFFFF; border-radius:8px; border:none !important; padding:28px 28px 22px !important;
   box-shadow:0 20px 50px rgba(0,0,0,.35); }}
</style>
<div class="bn-credit-fixed">{credit}</div>""", unsafe_allow_html=True)
    left, gap, right = st.columns([1.25, 0.15, 0.9])
    with left:
        st.markdown(f'<div class="bn-login-brand">{BRAND}</div>'
                    '<p class="bn-login-eyebrow">Subcontract allocation for garment exporters</p>'
                    '<p class="bn-login-title">Deliver the order, even when a factory doesn\'t.</p>'
                    f'<p class="bn-login-text">{TAGLINE}</p>{BADGE}', unsafe_allow_html=True)
    with right:
        with st.container(border=True, key="login_card"):
            st.subheader("Sign in")
            st.caption("Demo access. No account needed. Nothing you type here is checked or stored.")
            st.button("Continue as demo exporter", type="primary", width="stretch",
                      on_click=_enter, args=("welcome",), key="demo_login")
            with st.form("demo_login_form", border=False):
                st.text_input("Email", value="demo.exporter@example.com")
                st.text_input("Password", value="demo-password", type="password")
                st.form_submit_button("Sign in", width="stretch", on_click=_enter, args=("welcome",))
            st.caption("A human approves every decision. Nothing is ever sent to a factory automatically.")


STEPS = (
    ("Paste the buyer email", "Bunon reads quantity, deadline and compliance, quoting the email for each. "
     "You confirm the order."),
    ("Review the reliability forecast", "How likely each compliant factory is to deliver good pieces on time, "
     "from its (synthetic) history."),
    ("Compare plans", "The naive plan, the naive plan with the same buffer, and Bunon's plan: on-time "
     "probability and cost."),
    ("Handle a reply or disruption", "A factory declines or counters, or the news reports a flood or strike. "
     "Bunon re-plans."),
    ("Approve the decision memo", "A short memo built only from computed numbers. You approve; you send "
     "the draft orders."),
)


def welcome_page() -> None:
    uri, credit = _hero()
    st.markdown(
        f'<div class="bn-banner" style="background-image:linear-gradient(90deg, rgba(9,20,36,.90) 0%, '
        f'rgba(9,20,36,.72) 50%, rgba(9,20,36,.30) 100%), url(&quot;{uri}&quot;)">'
        '<p class="bn-login-eyebrow">Demo workspace</p>'
        '<p class="bn-login-title">Welcome, demo exporter</p>'
        '<p class="bn-login-text">You can make 100,000 of a 150,000-piece T-shirt order in-house. Bunon finds the '
        'subcontract factories for the other 50,000 that are most likely to deliver by the deadline.</p></div>',
        unsafe_allow_html=True)
    b1, b2 = st.columns([1, 3], vertical_alignment="center")
    b1.button("Start a new order", type="primary", on_click=_enter, args=("app",), key="start_order", width="stretch")
    b2.markdown(f'{BADGE} <span class="bn-credit">&nbsp; {credit}</span>', unsafe_allow_html=True)
    st.header("How to use Bunon")
    cols = st.columns(len(STEPS))
    for i, (col, (title, text)) in enumerate(zip(cols, STEPS), start=1):
        col.markdown(f'<div class="bn-step"><div class="bn-step-num">STEP {i:02d}</div>'
                     f'<h4>{title}</h4><p>{text}</p></div>', unsafe_allow_html=True)
    st.caption("The factory list is real (Open Supply Hub), shown only as pseudonymous IDs. Capacity, prices and "
               "delivery history are synthetic. The buyer email is fictional.")


def _logout() -> None:
    """Forget the session, including its order store (a new login starts a new story on day 0)."""
    old = st.session_state.get("_book")
    keep = {"visitor"}  # the anonymous live-call counter id survives a logout
    for k in list(st.session_state):
        if k not in keep:
            del st.session_state[k]
    if old is not None:
        old.store.path.unlink(missing_ok=True)


if not st.session_state.get("authed"):
    login_page()
    st.stop()
if st.session_state.get("page", "welcome") == "welcome":
    with st.sidebar:
        st.markdown(f'<div class="bn-brand">{BRAND}</div>', unsafe_allow_html=True)
        st.button("Start a new order", key="nav_start_side", on_click=_enter, args=("app",), width="stretch")
        st.button("Log out", key="logout_welcome", on_click=_logout, width="stretch")
    welcome_page()
    st.stop()


SYNTH = "🧪 synthetic"
SOURCE_BADGE = {"cache": "🤖 LLM (cached answer)", "live": "🤖 LLM (live)",
                "template": "📄 Template (no valid LLM answer; numbers still from the tools)"}


# =============================================================================
# Cached computations (keyed on plain values so Streamlit can hash them). The engine calls themselves
# live in bunon/service.py, which has no Streamlit import (PLAN_V2 decision C).
# =============================================================================

@st.cache_resource(show_spinner="Loading factories and fitting the reliability model…")
def world():
    return op.load_world()


@st.cache_data(show_spinner=False)
def report() -> dict:
    meta = load_public()["meta"]
    return json.loads(meta.loc[meta["key"] == "report", "value"].iloc[0])


@st.cache_data(show_spinner=False)
def history() -> pd.DataFrame:
    return load_public()["history"]


def llm_once(spinner: str, fn, *a):
    """Run an LLM step once per input for this visitor. Not st.cache_data on purpose: a failed or
    capped attempt must not be served to every later visitor. Repeats across visitors are cheap
    anyway, because successful answers sit in the disk cache (data/llm_cache)."""
    store = st.session_state.setdefault("_llm_results", {})
    k = (fn.__name__, *a)
    if k not in store:
        with st.spinner(spinner):
            store[k] = fn(*a)
    return store[k]


def _read_email(email: str):
    return ag.parse_order(email, C.DEMO_OWN_CAPACITY, LLMClient())


def read_email(email: str):
    return llm_once("Reading the email…", _read_email, email)


@st.cache_data(show_spinner="Solving the allocation and simulating 20,000 outcomes per plan…")
def run_plans(order_json: str, s: sv.PlanSettings) -> sv.PlanBundle:
    return sv.compute_plans(world(), OrderConstraints.model_validate_json(order_json), s, report()["compliant_tee"])


def _memo_for(args: tuple):
    b = run_plans(*args)
    return ag.write_memo(b.facts, "initial", LLMClient()) if b.facts else None


def memo_for(*args):
    return llm_once("Writing the decision memo…", _memo_for, args)


def _replan_memo(args: tuple, rp_json: str):
    rp = ReplanResult.model_validate_json(rp_json)
    return ag.write_memo(ag.factsheet_for_replan(run_plans(*args).facts, rp), "replan", LLMClient())


@st.cache_data(show_spinner="Re-planning: confirmed factories locked, new ones start late…")
def _replan_numbers(args: tuple, days: int):
    return sv.replan_after_decline(world(), run_plans(*args), days)


def run_replan(args: tuple, days: int):
    rp, cands_late = _replan_numbers(args, days)
    if rp.new.risk is None:
        return rp, None, cands_late
    return rp, llm_once("Writing the re-plan memo…", _replan_memo, args, rp.model_dump_json()), cands_late


def _read_reply(text: str, planned_qty: int):
    return ag.parse_reply(text, planned_qty, LLMClient())


def read_reply(text: str, planned_qty: int):
    return llm_once("Reading the reply…", _read_reply, text, planned_qty)


@st.cache_data(show_spinner="Re-planning with the factory's reply…")
def _reply_replan_numbers(args: tuple, factory_id: str, decision: str, qty, delay, days: int):
    return sv.replan_after_reply(world(), run_plans(*args), factory_id, decision, qty, delay, days)


def run_reply_replan(args: tuple, factory_id: str, decision: str, qty, delay, days: int):
    rp, cands_late = _reply_replan_numbers(args, factory_id, decision, qty, delay, days)
    if rp.new.risk is None:
        return rp, None, cands_late
    return rp, llm_once("Writing the re-plan memo…", _replan_memo, args, rp.model_dump_json()), cands_late


def _read_news(text: str):
    return ag.parse_news(text, LLMClient())


def read_news(text: str):
    return llm_once("Reading the news…", _read_news, text)


@st.cache_data(show_spinner="Simulating the disruption and re-planning around it…")
def run_news(args: tuple, area: str, severity: str, duration):
    return sv.assess_news(world(), run_plans(*args), area, severity, duration)


def _run_question(args: tuple, question: str):
    return analyst.answer(question, sv.analyst_toolbox(world(), run_plans(*args)), LLMClient())


def run_question(args: tuple, question: str):
    return llm_once("The analyst is choosing and running tools…", _run_question, args, question)


# =============================================================================
# Session state: the order book (one SQLite store per visitor session) and the planning settings
# =============================================================================

@st.cache_resource(show_spinner=False)
def _cleanup_old_sessions() -> int:
    """Once per server process: delete visitor stores older than 24 hours."""
    return cleanup_sessions(24.0)


_cleanup_old_sessions()


def book() -> sv.OrderBook:
    # isinstance check: after a deploy the package is reloaded (hosting quirk) and an object built from the
    # old class must not be reused.
    if not isinstance(st.session_state.get("_book"), sv.OrderBook):
        st.session_state["_book"] = sv.OrderBook(Store(session_db_path(st.session_state["visitor"])))
    return st.session_state["_book"]


SETTING_DEFAULTS = (("slack", C.PLANNED_SLACK_DAYS), ("target", C.TARGET_P_MEET), ("unproven", C.UNPROVEN_CAP_PCT),
                    ("dcap", C.DISTRICT_CAP_SHARE), ("max_f", C.MAX_FACTORIES), ("excluded", []),
                    ("tol", C.DELIVERY_TOLERANCE_PCT), ("margin", C.SAFETY_MARGIN_DEFAULT_PCT),
                    ("loss", C.DEFAULT_LOSS_ON_FAILURE_BDT), ("partial", C.PARTIAL_LOTS_DEFAULT), ("shocks", True))


def settings() -> sv.PlanSettings:
    g = st.session_state
    return sv.PlanSettings(
        slack=int(g["slack"]), target=round(float(g["target"]), 2), unproven=round(float(g["unproven"]), 2),
        dcap=round(float(g["dcap"]), 2) if g["dcap"] < 1.0 else None, partial=bool(g["partial"]),
        shocks=bool(g["shocks"]), max_f=int(g["max_f"]), excluded=tuple(sorted(g["excluded"])),
        tol=round(float(g["tol"]), 3), margin=round(float(g["margin"]), 3), loss=float(g["loss"]))


def current_args() -> tuple | None:
    return (st.session_state["order_json"], settings()) if "order_json" in st.session_state else None


# =============================================================================
# Helpers
# =============================================================================

def bdt(x: float) -> str:
    return f"{x:,.0f} BDT"


def bdt_m(x: float) -> str:
    return f"{x / 1e6:,.2f}M BDT" if abs(x) >= 1e6 else f"{x / 1e3:,.0f}k BDT"


def plan_row(name, pt, cands) -> dict:
    shares = op.district_shares(pt.plan, cands) if pt.plan.lines else {}
    return {"Plan": name,
            "P(on time, within tolerance)": None if pt.risk is None else round(pt.risk.p_meet_qty, 3),
            "Buffer": f"{pt.buffer_pct:.1%}", "Factories": len(pt.plan.lines), "Pieces": pt.plan.total_allocated,
            "Cost (BDT)": round(pt.plan.cost_total), "Buffer price (BDT)": round(pt.plan.cost_buffer),
            "Districts": ", ".join(f"{k} {v:.0%}" for k, v in shares.items()), "Status": pt.plan.status}


def show_memo(memo):
    st.caption(SOURCE_BADGE[memo.source]
               + ("" if memo.source != "template" or not memo.errors else f" · {len(memo.errors)} issue(s) logged"))
    st.markdown(memo.rendered_md)
    if memo.errors:
        with st.expander("Why the LLM answer was not used"):
            st.write(memo.errors)


def approval_buttons(step: str, point, label: str, summary: str, order, cands, key: str, po_download: bool = True):
    """Approve / reject, recorded on the order's timeline with the acting role. Approval unlocks draft
    purchase-order text (download only): nothing is ever sent by the system."""
    role = st.session_state["role"]
    oid = st.session_state["order_id"]
    plan = point.plan
    state_key = f"decision_{key}_{plan.plan_id}"
    if role not in EXPORTER_ROLES:
        st.info("Switch to an exporter role in the sidebar to approve or reject.")
        return
    c1, c2, _ = st.columns([1, 1, 1])
    if c1.button("✅ Approve", key=f"approve_{key}", type="primary"):
        book().decide(oid, step, point, label, "approved", role, summary)
        st.session_state[state_key] = "approved"
    if c2.button("✖ Reject", key=f"reject_{key}"):
        book().decide(oid, step, point, label, "rejected", role, summary)
        st.session_state[state_key] = "rejected"
    status = st.session_state.get(state_key)
    if status == "approved" and po_download:
        st.success(f"Approved by **{role}** and recorded on the timeline. Nothing has been sent: download the "
                   "drafts, review them and send them yourself.")
        st.download_button("⬇ Draft purchase-order requests (.txt)", dl.draft_po_text(order, plan, cands, role),
                           file_name=f"DRAFT_{plan.plan_id}.txt", key=f"po_{key}")
    elif status == "approved":
        st.success(f"Approved by **{role}** and recorded on the timeline. Any message it needs is drafted below "
                   "and goes out only when you approve it.")
    elif status == "rejected":
        st.warning("Rejected and recorded. Change the planning settings in the sidebar to get a different plan.")


def no_feasible_replan(what: str) -> None:
    """The optimiser found no plan that covers the order under the current limits. Say so plainly;
    there is nothing to approve and no memo to write."""
    st.error(f"**No feasible re-plan.** After {what}, no combination of factories covers the order under the "
             "current limits: confirmed factories cannot be cut, new ones start late, and the sidebar caps "
             "(number of factories, unproven share, district share) leave too little capacity. Nothing was "
             "changed or sent.")
    st.info("Try loosening a limit in the sidebar (for example more factories or a higher unproven cap), or "
            "escalate to the buyer early: the deadline and quantity are fixed.")


def _run_safely(fn, *a):
    try:
        return fn(*a)
    except Exception:
        import traceback
        st.error("The re-plan failed. Nothing was changed or sent. Technical details below.")
        with st.expander("Technical details"):
            st.code(traceback.format_exc())
        st.stop()


def prototype_notice() -> None:
    st.markdown(BADGE, unsafe_allow_html=True)
    st.warning(
        "**Prototype.** The factory list is real (Open Supply Hub), shown only as pseudonymous IDs. "
        "**All capacity, price and delivery-history figures are synthetic** (seeded, see DATA_PROVENANCE.md) and "
        "the interview-based values are placeholders. The buyer email is fictional. Nothing is ever sent to "
        "a factory: every decision needs a human.", icon="⚠️")


# =============================================================================
# Sidebar: who is acting (demo role switcher), which view, the story clock, planning settings
# =============================================================================

VIEWS = ("Order timeline", "Show the maths")
TIMELINE_NAV = (("order", "Day 0 · Order"), ("plans", "Day 0 · Plan"), ("sourcing", "Day 0 · Sourcing"),
                ("offers", "Day 2 · Offers"), ("split", "Day 2 · Split"), ("update", "Day 12 · Update"),
                ("rescue", "Day 12 · Rescue"), ("qc", "Day 26 · QC"), ("record", "Track record"),
                ("payments", "Payments"),
                ("whatif", "Ask Bunon"), ("timeline", "Timeline"))

for _k, _v in SETTING_DEFAULTS:
    st.session_state.setdefault(_k, _v)
st.session_state.setdefault("role", "Merchandiser")

with st.sidebar:
    st.markdown(f'<div class="bn-brand">{BRAND}</div>', unsafe_allow_html=True)
    role = st.selectbox("Acting as (demo role switcher)", ROLES, key="role",
                        format_func=lambda r: f"{'Exporter' if r in EXPORTER_ROLES else 'Factory'} · {r}",
                        help="No accounts: pick a role to see that person's view. Decisions are recorded by role, "
                             "never by name.")
    if role in FACTORY_ROLES:
        view = "Factory app"
    else:
        if st.session_state.get("view") not in VIEWS:
            st.session_state["view"] = VIEWS[0]
        view = st.radio("View", VIEWS, key="view", horizontal=True)
        if view == VIEWS[0]:
            st.markdown('<div class="bn-side-label">On this page</div><div class="bn-nav">'
                        + "".join(f'<a href="#{a}" target="_self">{t}</a>' for a, t in TIMELINE_NAV) + "</div>",
                        unsafe_allow_html=True)
    if "order_id" in st.session_state:
        d = book().day(st.session_state["order_id"])
        st.caption(f"🕒 Story clock: **day {d}** · {book().date_of(d):%a %d %b %Y} (order received on day 0)")
    nb1, nb2 = st.columns(2)
    nb1.button("Welcome", key="nav_welcome", on_click=_enter, args=("welcome",), width="stretch")
    nb2.button("Log out", key="logout", on_click=_logout, width="stretch")
    with st.expander("Planning settings", expanded=False):
        st.slider("Planned slack before latest finish (days)", 0, 10, key="slack")
        st.slider("Target P(on time, within tolerance)", 0.50, 0.99, step=0.01, key="target",
                  help="Probability that the good pieces delivered by the deadline reach (1 − tolerance) "
                       "of the subcontracted quantity.")
        st.slider("Accepted shortfall (tolerance)", 0.0, 0.05, step=0.005, key="tol", format="%.3f",
                  help="Success = good pieces after QC ≥ (1 − tolerance) × subcontracted quantity. "
                       "Placeholder 2% from the expert interview.")
        st.slider("Safety margin: minimum over-allocation", 0.0, 0.25, step=0.025, key="margin",
                  help="Optional; default 0%. Bunon may still recommend a larger buffer to reach the target.")
        st.number_input("Expected loss if the subcontracted part fails (BDT)", 0.0, 1e9, step=100_000.0,
                        key="loss", format="%.0f",
                        help="Your estimate: penalties, air freight, lost margin. Not read from the email. "
                             "Default is a placeholder.")
        st.slider("Max number of factories", 1, 5, key="max_f")
        st.slider("Cap on unproven factories (share of pieces)", 0.0, 0.30, step=0.05, key="unproven")
        st.slider("Max share of pieces per district (100% = no cap)", 0.30, 1.0, step=0.05, key="dcap")
        if st.session_state["excluded"]:
            st.caption("Excluded factories: " + ", ".join(st.session_state["excluded"]))
            if st.button("Clear exclusions"):
                st.session_state["excluded"] = []
                st.rerun()
        st.toggle("Partial lots count (work finished by the ship date ships)", key="partial",
                  help="Off = strict view: a factory that runs past its slack delivers nothing on time.")
        st.toggle("Correlated district/national shocks", key="shocks")
    if current_mode() == "live":
        b = budget_status()
        st.caption(f"Live LLM calls: {b['visitor_used']}/{b['visitor_cap']} for you, {b['day_used']}/{b['day_cap']} "
                   "today for the whole demo. Cached answers are free and unlimited.")
    st.caption(f"LLM mode: **{current_mode()}** · model `{C.LLM_MODEL}`. Order date (day 0) {C.AS_OF:%d %b %Y}. "
               f"Delivery point: {C.DELIVERY_POINT_NAME}. Python {sys.version.split()[0]}.")
    if st.button("↺ Restart the story"):
        old = st.session_state.get("_book")
        for k in list(st.session_state):
            if k not in ("role", "authed", "page", "visitor"):
                del st.session_state[k]
        if old is not None:
            old.store.path.unlink(missing_ok=True)
        st.rerun()


# =============================================================================
# View: factory app (phone frame). Factories are simulated personas in the demo.
# =============================================================================

def view_factory() -> None:
    st.title("Factory app")
    st.markdown(BADGE + ' <span class="bn-badge">Simulated factories</span>', unsafe_allow_html=True)
    st.caption("What a subcontract factory sees on its phone: requests from exporters, a short daily update, and "
               "its own track record. Free for factories. In this demo every factory is a simulated persona.")
    ids: list[str] = []
    if "order_id" in st.session_state:
        allocs = book().store.allocations(st.session_state["order_id"])
        if allocs:
            ids = [ln["factory_id"] for ln in allocs[-1]["lines"]]
    if not ids:
        try:
            ids = [p.factory_id for p in personas.load().personas if p.factory_id != "TBD"]
        except Exception:
            ids = []
    if not ids:
        st.info("No factory to show yet. Confirm an order and look at the plan first (switch to an exporter role).")
        return
    fid = st.selectbox("Factory (pseudonymous ID)", ids, key="factory_view_id")
    w = world()
    row = w.factories.loc[w.factories["factory_id"] == fid].iloc[0]
    with st.container(border=True, key="phone"):
        st.markdown(f"**{fid}** · {row['district']} · <span class='bn-badge'>Simulated</span>",
                    unsafe_allow_html=True)
        st.markdown("**Inbox**")
        msgs = [m for m in book().store.messages(st.session_state.get("order_id"), factory_id=fid)
                if m["direction"] == "in" or m["sent_day"] is not None]  # drafts stay with the exporter
        if not msgs:
            st.caption("No requests yet. A request appears here only after the exporter approves it. "
                       "Nothing is ever sent automatically.")
        for m in msgs:
            st.markdown(f"Day {m['story_day']} · {'↘ from exporter' if m['direction'] == 'out' else '↗ you'}: "
                        f"{m['text']}")
        st.markdown("**My record** " + SYNTH)
        r = sv.factory_record(history(), fid)
        if r["jobs"]:
            c1, c2 = st.columns(2)
            c1.metric("Jobs done", f"{r['jobs']}")
            c2.metric("On time", f"{r['on_time_rate']:.0%}")
            c3, c4 = st.columns(2)
            c3.metric("Delivered in full", f"{r['full_rate']:.0%}")
            c4.metric("QC pass", f"{r['qc_pass_rate']:.1%}" if r["qc_pass_rate"] is not None else "—")
        else:
            st.caption("No past jobs on record.")
        st.caption("From synthetic history. On the platform, only verified events (offers, deliveries, QC results) "
                   "will count, and you choose what other exporters can see.")


# =============================================================================
# View: show the maths (engine detail; not part of the scripted demo)
# =============================================================================

STEER_EXAMPLES = (
    "Use no more than three factories and keep new factories under 10%.",
    "Don't use F2184 and plan 6 days of slack. Also move the deadline to 27 December.",  # cached; F2184 is a demo candidate
)
_KEY = {"target_p": "target", "planned_slack_days": "slack", "max_factories": "max_f",
        "unproven_cap_pct": "unproven", "max_district_share": "dcap"}


def _apply_steer(accepted: list[dict], instruction: str):
    for a in accepted:
        if a["field"] == "excluded_ids":
            st.session_state["excluded"] = sorted(set(st.session_state["excluded"]) | set(a["value"]))
        else:
            st.session_state[_KEY[a["field"]]] = a["value"]
    book().record_steer(st.session_state.get("order_id"), instruction, accepted, st.session_state["role"])
    st.session_state.pop("steer", None)


def view_maths() -> None:
    st.title("Show the maths")
    prototype_notice()
    args = current_args()
    if args is None:
        st.info("Confirm an order on the order timeline first.")
        return
    s = args[1]
    b = run_plans(*args)
    order, cands, comp, match, rec, ours = b.order, b.cands, b.comp, b.match, b.rec, b.comp["ours"]
    w = world()

    st.header("Who can make it", anchor="funnel")
    rep = report()
    r1 = st.columns(3)
    r1[0].metric("Factories in export", f"{rep['raw_rows']:,}")
    r1[1].metric("Open", f"{rep['open_rows']:,}")
    r1[2].metric("Make T-shirts", f"{rep['tee_capable']:,}")
    r2 = st.columns(3)
    r2[0].metric("RSC or valid BSCI", f"{rep['compliant_tee']:,}")
    r2[1].metric("Spare capacity + deadline OK", f"{len(cands):,}", help=SYNTH)
    r2[2].metric("Proven (≥ 3 past jobs)", f"{sum(c.proven for c in cands):,}", help=SYNTH)
    st.caption("First four counts: real Open Supply Hub data. Last two depend on synthetic capacity and history, "
               "and on compliance still being valid on the deadline.")

    st.header(f"Reliability forecast {SYNTH}", anchor="reliability")
    st.dataframe(pd.DataFrame([{
        "Factory": c.factory_id, "District": c.district,
        "Declared capacity in window": c.window_capacity_declared, "Forecast deliverable": c.window_capacity_risk_adj,
        "P(delivered in full)": round(w.forecasts[c.factory_id].p_full, 2),
        f"P(on time within {s.slack} d)": round(rl.p_on_time(w.forecasts[c.factory_id], s.slack), 2),
        "QC defect rate": f"{w.forecasts[c.factory_id].qc_defect_mean:.1%}",
        "Past jobs": w.forecasts[c.factory_id].n_jobs,
        "Thin history": "yes" if w.forecasts[c.factory_id].thin_history else "",
        "CM price (BDT/pc)": round(c.unit_price_bdt, 1),
    } for c in sorted(cands, key=lambda c: -c.window_capacity_risk_adj)[:15]]), hide_index=True, width="stretch")
    st.caption("Top 15 candidates by forecast deliverable quantity. Thin-history factories get the population "
               "prior, so their forecasts are wider.")

    st.header("Plans compared", anchor="plans_compared")
    if not b.feasible:
        st.error("**No feasible plan** under the current settings (see the order timeline).")
        return
    bl, bb = comp["baseline"], comp["baseline_buffered"]
    h = st.columns(3)
    h[0].metric("Naive: declared capacity + lowest price", f"{bl.risk.p_meet_qty:.0%}" if bl.risk else "—",
                help="P(good pieces delivered by the deadline ≥ (1 − tolerance) × subcontracted quantity)")
    h[1].metric(f"Naive + same {rec.buffer_pct:.0%} buffer", f"{bb.risk.p_meet_qty:.0%}" if bb.risk else "—")
    h[2].metric("Bunon recommendation", f"{rec.risk.p_meet_qty:.0%}")
    rows = [plan_row("Naive baseline", bl, cands), plan_row("Naive + same buffer", bb, cands),
            plan_row("Bunon (ours)", rec, cands)]
    if match:
        rows.append(plan_row("Naive, buffer needed to match ours", match, cands))
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    if match:
        st.info(f"To match our {rec.risk.p_meet_qty:.1%}, the naive rule needs a **{match.buffer_pct:.1%}** buffer "
                f"and costs **{bdt(match.plan.cost_total - rec.plan.cost_total)}** more.")
    fr = pd.DataFrame(b.frontier)
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=fr["buffer"], y=fr["ours"], mode="lines+markers", name="Bunon"))
    fig.add_trace(go.Scatter(x=fr["buffer"], y=fr["baseline"], mode="lines+markers", name="Naive + same buffer"))
    fig.add_hline(y=s.target, line_dash="dot", annotation_text=f"target {s.target:.0%}")
    fig.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10), xaxis_tickformat=".0%", yaxis_tickformat=".0%",
                      xaxis_title="Over-allocation buffer", yaxis_title="P(on time, within tolerance)",
                      title="What the buffer buys", legend=dict(orientation="h", y=1.12),
                      colorway=["#1E3A5F", "#B7791F"])
    st.plotly_chart(fig, width="stretch")
    st.caption(f"Costs: CM price + exporter-supplied material ({C.MATERIAL_COST_BDT_PER_PIECE:.1f} BDT/pc, "
               f"placeholder) + transport + {bdt(C.FIXED_COST_PER_FACTORY_BDT)} per factory. Binding constraints: "
               f"{', '.join(rec.plan.binding_constraints) or 'none'}.")

    st.header("Ask for a change", anchor="steer")
    st.caption("Describe a change in plain language. The LLM may only change the planning settings in the sidebar "
               "(never the deadline, quantity or compliance), every value must be quoted from your words, and "
               "nothing changes until you apply it.")
    ex = st.columns(len(STEER_EXAMPLES))
    for i, e in enumerate(STEER_EXAMPLES):
        if ex[i].button(e, key=f"steer_ex_{i}"):
            st.session_state["steer_text"] = e
    instruction = st.text_input("Instruction", key="steer_text", placeholder="e.g. at most four factories")
    if st.button("Interpret") and instruction.strip():
        with st.spinner("Interpreting…"):
            st.session_state["steer"] = (instruction, ag.steer(instruction, {c.factory_id for c in cands}, LLMClient()))
    if "steer" in st.session_state:
        instr, sr = st.session_state["steer"]
        if sr.meta:
            st.caption(SOURCE_BADGE[sr.meta.source])
        current = {"target_p": s.target, "planned_slack_days": s.slack, "max_factories": s.max_f,
                   "unproven_cap_pct": s.unproven, "max_district_share": s.dcap,
                   "excluded_ids": st.session_state["excluded"] or "none"}
        if sr.accepted:
            st.dataframe(pd.DataFrame([{"Setting": a["label"], "Now": str(current[a["field"]]),
                                        "Proposed": str(a["value"]), "Your words": a["source_span"]}
                                       for a in sr.accepted]), hide_index=True, width="stretch")
            st.button("Apply these changes", type="primary", on_click=_apply_steer, args=(sr.accepted, instr))
        for u in sr.unsupported:
            st.warning(f"Not changed: “{u}”. Only planning settings can be changed here; the deadline, "
                       "quantity and compliance rules are fixed.")
        for r in sr.rejected:
            st.error(f"Not applied: {r}")
        if not (sr.accepted or sr.unsupported or sr.rejected):
            st.info("No change found in the instruction.")
    if current_mode() == "offline":
        st.caption("On the public demo, answers are replayed from a cache: the two example instructions work; other "
                   "wording falls back to the sidebar controls.")
    section_followup(args)


# =============================================================================
# View: the order timeline (the demo)
# =============================================================================

DAYS = sv.DECLINE_DAY
REPLIES = json.loads((C.ROOT / "data" / "demo" / "factory_replies.json").read_text(encoding="utf-8"))
NEWS = json.loads((C.ROOT / "data" / "demo" / "news.json").read_text(encoding="utf-8"))
WHATIF_EXAMPLES = (
    "If the Ashulia stoppage turns severe, what happens to our plan, and what should we do?",
    "Should we stop using F2184 and keep any district under 50%? Compare that with the current plan.",
    "If a nationwide hartal lasts 2 days, can any plan still reach our target?",
)


def section_intake() -> None:
    st.header("Day 0 · Order received", anchor="order")
    st.caption("Paste the buyer's email. The LLM reads it into order terms; each value must come with a "
               "word-for-word quote from the email that states it, and anything that fails the check is left for "
               "you to enter.")
    email = st.text_area("Buyer email", C.DEMO_EMAIL_PATH.read_text(encoding="utf-8"), height=220)
    if st.button("Read the email", type="primary"):
        st.session_state["email_read"] = email
    intake = read_email(st.session_state["email_read"]) if "email_read" in st.session_state else None
    if intake is None:
        return
    if intake.meta:
        st.caption(SOURCE_BADGE[intake.meta.source])
    for issue in intake.issues:
        st.warning(issue)
    if intake.fields:
        icon = {"ok": "✅ found in email", "missing": "— not stated", "rejected": "⚠️ check failed"}
        st.dataframe(pd.DataFrame([{"Field": k, "Value": "" if f.value is None else str(f.value),
                                    "Quote from the email": f.source_span or "", "Check": icon[f.status],
                                    "Note": f.note} for k, f in intake.fields.items()]),
                     hide_index=True, width="stretch")

    def pre(name, default=None):
        f = intake.fields.get(name)
        return f.value if f is not None and f.status == "ok" else default

    with st.form("confirm_order"):
        st.markdown("**Check and confirm the order.** Blank fields were not verified in the email.")
        a, b, c = st.columns(3)
        total = a.number_input("Order quantity (pieces)", min_value=1, value=pre("total_qty"), step=1000)
        own = b.number_input("Your own capacity in the window (pieces)", min_value=0,
                             value=C.DEMO_OWN_CAPACITY, step=1000, help="Typed by you; never read from the email.")
        deadline = c.date_input("Deadline (fixed)", value=pre("deadline"), min_value=C.AS_OF)
        d, e, f = st.columns(3)
        fob = d.number_input("FOB (USD/piece)", min_value=0.0, value=pre("fob_usd_per_piece"), step=0.05)
        penalty = e.number_input("Late penalty (% of FOB per week)", min_value=0.0,
                                 value=pre("late_penalty_pct_per_week"), step=0.5)
        aql = f.number_input("AQL", min_value=0.0, value=pre("quality_aql"), step=0.5)
        if st.form_submit_button("Confirm order", type="primary"):
            if not total or not deadline:
                st.error("Quantity and deadline are required.")
            elif own >= total:
                st.error("Your own capacity covers the order: nothing to subcontract.")
            elif st.session_state["role"] not in EXPORTER_ROLES:
                st.error("Switch to an exporter role to confirm an order.")
            else:
                order = sv.order_from_intake(intake, total, own, deadline, fob, penalty, aql)
                st.session_state["order_json"] = order.model_dump_json()
                st.session_state["order_id"] = book().confirm_order(
                    order, st.session_state["role"], settings(), intake.meta.source if intake.meta else None,
                    buyer="fictional demo buyer")
                for k in [k for k in st.session_state if k.startswith(("decision_", "replan"))]:
                    del st.session_state[k]
                st.rerun()  # redraw the sidebar with the story clock


def show_replan(args, rp, rmemo, cands_late, key: str, what: str, label: str):
    b = run_plans(*args)
    rec, cands, order = b.rec, b.cands, b.order
    n = rp.new
    if n.risk is None:
        no_feasible_replan(what)
        return
    d = st.columns(3)
    d[0].metric("New P(on time, within tolerance)", f"{n.risk.p_meet_qty:.1%}",
                delta=f"{(n.risk.p_meet_qty - rec.risk.p_meet_qty) * 100:+.1f} pts")
    d[1].metric("New cost", bdt_m(n.plan.cost_total), delta=bdt(n.plan.cost_total - rec.plan.cost_total),
                delta_color="inverse")
    d[2].metric("New buffer", f"{max(0, n.plan.total_allocated - n.plan.target_qty) / n.plan.target_qty:.1%}")
    if n.risk.p_meet_qty < b.settings.target:
        st.error(f"The re-plan does not reach the {b.settings.target:.0%} target (best found: {n.risk.p_meet_qty:.1%}). "
                 "The deadline is fixed: escalate to the buyer early, or add capacity outside this plan.")
    declined = rp.reason.startswith(("declined", "rejected", "can supply nothing"))
    st.dataframe(pd.DataFrame([{
        "Factory": x["factory_id"], "Before": x["old_qty"], "After": x["new_qty"], "Change": x["new_qty"] - x["old_qty"],
        "Note": (("declined" if declined else "reply terms locked") if x["factory_id"] == rp.excluded_id else
                 f"new: starts on day {DAYS}" if x["old_qty"] == 0 else "confirmed: locked ≥ before"),
    } for x in rp.diff]), hide_index=True, width="stretch")
    st.subheader("Re-plan memo")
    show_memo(rmemo)
    all_cands = list({c.factory_id: c for c in [*cands, *cands_late]}.values())
    book().propose(st.session_state["order_id"], n, label)
    approval_buttons(key, n, label, f"{what}; {len(n.plan.lines)} factories, P {n.risk.p_meet_qty:.3f}, "
                     f"{bdt(n.plan.cost_total)}", order, all_cands, key)


def section_followup(args) -> None:
    b = run_plans(*args)
    rec, cands, order = b.rec, b.cands, b.order
    oid = st.session_state["order_id"]
    role = st.session_state["role"] if st.session_state["role"] in EXPORTER_ROLES else None
    st.header("What-ifs on the first plan: decline, reply, news", anchor="disruption")
    st.caption("Engine detail, not the order's story: what the first plan would do if its largest factory "
               "declined, if a factory replied with other terms, or if a news event hit. These do not move the "
               "order's story clock.")
    tab_decline, tab_reply, tab_news = st.tabs(["Scripted decline", "Factory reply (English / Bangla / Banglish)",
                                                "News (English / Bangla)"])
    with tab_decline:
        gone = op.largest_line(rec)
        st.write(f"On day {DAYS}, **{gone}**, the factory with the largest allocation, declines. Factories that "
                 "already confirmed keep their quantities (they may be asked for more, never less); new factories "
                 f"can only start now, so they have {DAYS} fewer production days. The deadline does not move.")
        if st.button(f"Simulate: {gone} declines on day {DAYS}", type="primary"):
            st.session_state["replan_for"] = args
        if st.session_state.get("replan_for") == args:
            rp, rmemo, cands_late = _run_safely(run_replan, args, DAYS)
            show_replan(args, rp, rmemo, cands_late, "replan", f"{gone} declined", "Re-plan after decline")

    with tab_reply:
        st.caption("Paste what a factory wrote back. The LLM classifies it as accept / reject / counter and pulls "
                   "out quantity, delay and conditions, each with a word-for-word quote that the code checks. The "
                   "code, not the LLM, settles the final category, and a counter-offer is locked into the re-plan.")
        plan_ids = [ln.factory_id for ln in rec.plan.lines]
        planned = {ln.factory_id: ln.qty for ln in rec.plan.lines}
        ex = st.columns(len(REPLIES))
        for i, r in enumerate(REPLIES):
            if ex[i].button(f"{r['factory_id']} · {r['language']}", key=f"reply_ex_{i}",
                            disabled=r["factory_id"] not in planned):
                st.session_state["reply_text"] = r["text"]
                st.session_state["reply_factory"] = r["factory_id"]
        if st.session_state.get("reply_factory") not in plan_ids:
            st.session_state["reply_factory"] = plan_ids[0]
        fid = st.selectbox("Reply from", plan_ids, key="reply_factory",
                           format_func=lambda f: f"{f} (planned {planned[f]:,} pieces)")
        text = st.text_area("Factory reply", key="reply_text", height=110,
                            placeholder="e.g. Bhai, 12,400 pcs pura parbo na, max 9 hajar dite parbo.")
        if st.button("Read the reply") and text.strip():
            st.session_state["reply_read"] = (text, fid, args)
        if st.session_state.get("reply_read", (None, None, None))[2] == args:
            rtext, rfid, _ = st.session_state["reply_read"]
            res = read_reply(rtext, planned[rfid])
            if res.meta:
                st.caption(SOURCE_BADGE[res.meta.source])
            for note in res.notes:
                st.warning(note)
            if res.decision:
                label = {"accept": "✅ Accept", "reject": "✖ Reject", "counter": "↔ Counter-offer"}[res.decision]
                changed = f" (the LLM said *{res.llm_decision}*)" if res.llm_decision != res.decision else ""
                st.markdown(f"**{rfid}: {label}**{changed}. Quote: “{res.decision_span}”")
                icon = {"ok": "✅ found in reply", "missing": "— not stated", "rejected": "⚠️ check failed"}
                rows = [{"Item": "Quantity (pieces)",
                         "Value": "" if res.quantity.value is None else f"{res.quantity.value:,}",
                         "Quote": res.quantity.source_span or "", "Check": icon[res.quantity.status]},
                        {"Item": "Delay (days)", "Value": "" if res.delay_days.value is None else str(res.delay_days.value),
                         "Quote": res.delay_days.source_span or "", "Check": icon[res.delay_days.status]}]
                # Conditions: show the LLM's English gloss only if it contains no numbers; the quote is the evidence.
                rows += [{"Item": "Condition", "Value": "(see quote)" if literal_numbers(str(c.value)) else c.value,
                          "Quote": c.source_span or "", "Check": icon[c.status]} for c in res.conditions]
                st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
                if res.decision == "accept":
                    st.success("No change to the plan: the factory confirmed the planned quantity.")
                elif st.button("Re-plan with this reply", type="primary"):
                    st.session_state["reply_replan"] = (rtext, rfid, args)
                if st.session_state.get("reply_replan") == (rtext, rfid, args) and res.decision != "accept":
                    qty, delay = sv.reply_terms(res)
                    rp, rmemo, cands_late = _run_safely(run_reply_replan, args, rfid, res.decision, qty, delay, DAYS)
                    show_replan(args, rp, rmemo, cands_late, "reply_replan", f"{rfid} replied ({res.decision})",
                                f"Re-plan after {rfid}'s reply")

    with tab_news:
        st.caption("Paste a headline or short news item (English or Bangla). The LLM names the area, the kind of "
                   "disruption, the duration and a severity label, each with a quote the code checks. **The LLM "
                   "chooses no numbers:** severity is turned into simulation parameters by a fixed table.")
        nx = st.columns(len(NEWS))
        for i, n in enumerate(NEWS):
            if nx[i].button(f"{n['id'].replace('_', ' ')} · {n['language']}", key=f"news_ex_{i}"):
                st.session_state["news_text"] = n["text"]
        ntext = st.text_area("News text", key="news_text", height=90, placeholder="e.g. Workers in Ashulia stopped work…")
        if st.button("Read the news") and ntext.strip():
            st.session_state["news_read"] = (ntext, args)
        if st.session_state.get("news_read", (None, None))[1] == args:
            nres = read_news(st.session_state["news_read"][0])
            if nres.meta:
                st.caption(SOURCE_BADGE[nres.meta.source])
            for note in nres.notes:
                st.warning(note)
            icon = {"ok": "✅ found in text", "missing": "— not stated", "rejected": "⚠️ check failed"}
            st.dataframe(pd.DataFrame([
                {"Item": "Area", "Value": nres.area or "(choose by hand)", "Quote": nres.area_span or ""},
                {"Item": "What happened", "Value": nres.disruption_type or "", "Quote": nres.type_span or ""},
                {"Item": "Duration (days)", "Value": "" if nres.duration.value is None else str(nres.duration.value),
                 "Quote": (nres.duration.source_span or "") + ("" if nres.duration.status == "ok" else f"  [{icon[nres.duration.status]}]")},
                {"Item": "Severity", "Value": nres.severity or "", "Quote": nres.severity_span or ""},
                {"Item": "LLM confidence", "Value": nres.confidence or "", "Quote": ""},
            ]), hide_index=True, width="stretch")
            districts = ["national", "ashulia_savar", "gazipur", "narayanganj", "chattogram", "dhaka_city", "mymensingh"]
            a1, a2 = st.columns(2)
            area = a1.selectbox("Area (check or correct)", districts,
                                index=districts.index(nres.area) if nres.area in districts else 0, key="news_area")
            sev = a2.selectbox("Severity (check or correct)", ["low", "medium", "high"],
                               index=["low", "medium", "high"].index(nres.severity) if nres.severity else 1, key="news_sev")
            dur = nres.duration.value if nres.duration.status == "ok" else None
            ev = op.event_from_severity(area, sev, dur)
            row = C.SEVERITY_TABLE[sev]
            st.caption(f"Fixed table, severity **{sev}**: affected factories deliver {row['fill_mult']:.0%} of planned "
                       f"output, and {row['lost_day_share']:.0%} of the event's "
                       f"{dur if dur is not None else row['default_days']} days"
                       f"{'' if dur is not None else ' (default: no number in the text)'} become delay → "
                       f"**{ev.extra_delay_days} days** late. Placeholder values pending research.")
            if st.button("Assess the impact", type="primary"):
                st.session_state["news_assess"] = (area, sev, dur, args)
            if st.session_state.get("news_assess") == (area, sev, dur, args):
                r = _run_safely(run_news, args, area, sev, dur)
                m1, m2, m3 = st.columns(3)
                m1.metric("Current plan, if this happens", f"{r['current_with_event'].p_meet_qty:.1%}",
                          delta=f"{(r['current_with_event'].p_meet_qty - rec.risk.p_meet_qty) * 100:+.1f} pts")
                new = r["new"]
                m2.metric("Plan made knowing about it", f"{new.risk.p_meet_qty:.1%}" if new.risk else "—")
                if new.risk is not None:
                    m3.metric("Cost of re-planning", bdt(new.plan.cost_total - rec.plan.cost_total))
                st.write("Factories in the current plan that are hit: "
                         + (", ".join(f"**{f}**" for f in r["affected_in_plan"]) or "none"))
                if new.risk is None:
                    no_feasible_replan(f"a {sev} disruption in {area}")
                else:
                    if r["keep_current_is_better"]:
                        st.warning("Keeping the current plan is at least as likely to succeed under this event as any "
                                   "new plan Bunon found. Do not re-plan for this event alone.")
                    if not r["new_target_met"]:
                        st.error(f"Even a plan made knowing about this cannot reach the {b.settings.target:.0%} "
                                 "target. Escalate to the buyer early.")
                    ncands = r["cands"]
                    st.dataframe(pd.DataFrame([plan_row("Current plan (event applied)",
                                                         rec.model_copy(update={"risk": r["current_with_event"]}), cands),
                                                plan_row("Plan made knowing about it", new, ncands)]),
                                 hide_index=True, width="stretch")
                    label = f"Plan for {sev} {area} disruption"
                    book().propose(oid, new, label)
                    approval_buttons("news_replan", new, label, f"news: {sev} {area}; P {new.risk.p_meet_qty:.3f}, "
                                     f"{bdt(new.plan.cost_total)}", order, ncands, "news_replan")


def section_ask(args) -> None:
    with st.container(border=True):
        st.subheader("Ask Bunon", anchor="whatif")
        st.caption("A what-if question about this order. The LLM decides which tools to run (simulate a disruption, "
                   f"solve a new plan with exclusions or caps, compare results), at most {analyst.MAX_TOOL_CALLS} "
                   "calls. The tools do all the numbers; every call is logged below.")
        qx = st.columns(len(WHATIF_EXAMPLES))
        for i, q in enumerate(WHATIF_EXAMPLES):
            if qx[i].button(q, key=f"whatif_ex_{i}"):
                st.session_state["whatif_text"] = q
        question = st.text_input("Question", key="whatif_text", placeholder="e.g. What if Gazipur floods for a week?")
        if st.button("Ask", type="primary") and question.strip():
            st.session_state["whatif"] = (question.strip(), args)
        if st.session_state.get("whatif", (None, None))[1] == args:
            run = _run_safely(run_question, args, st.session_state["whatif"][0])
            badge = {**SOURCE_BADGE, "unavailable": "⚠️ LLM not available for this question"}
            st.caption(badge[run.source] + (f" · {len(run.calls)} tool call(s)" if run.calls else ""))
            for c in run.calls:
                head = (f"Step {c.step} · `{c.call_id}` · **{c.name}**("
                        + ", ".join(f"{k}={json.dumps(v, ensure_ascii=False)}" for k, v in c.args.items()) + ")")
                with st.expander(head + ("" if c.ok else " · ⚠️ rejected"), expanded=False):
                    if c.ok:
                        st.dataframe(pd.DataFrame([{"Fact key": k, "Value": v, "Meaning": run.facts[k].label}
                                                   for k, v in c.facts.items()]), hide_index=True, width="stretch")
                    else:
                        st.error(c.error)
            if run.answer_md:
                st.markdown(run.answer_md)
            if run.errors:
                with st.expander("Checker messages"):
                    st.write(run.errors)
            if run.source == "unavailable":
                st.info(("The live-call limit is used up. " if current_mode() == "live" else
                         "The app is offline and this question is not in the cache. ")
                        + "The three example questions (with default settings) always work.")


def section_timeline() -> None:
    st.header("Timeline", anchor="timeline")
    ev = book().timeline(st.session_state["order_id"])
    st.dataframe(pd.DataFrame([{"Day": e["story_day"], "Date": e["story_date"], "What": e["summary"],
                                "Who (role)": e["actor_role"] or "Bunon", "Kind": e["kind"]} for e in ev]),
                 hide_index=True, width="stretch")
    st.caption("Every step of this order, on the story clock. Recorded by role, never by name; kept for this "
               "browser session only.")
    with st.expander("Decision log (this server)"):
        st.dataframe(dl.read_log(), hide_index=True, width="stretch")
        st.caption("Every confirmation, approval and rejection, with the acting role. On the hosted demo this log "
                   "resets when the server restarts.")


@st.cache_data(show_spinner=False)
def persona_file():
    return personas.load()


def _parse_offer(text: str, qty: int):
    return ag.parse_reply(text, qty, LLMClient())


def _parse_update(text: str, qty: int):
    return ag.parse_update(text, qty, LLMClient())


@st.cache_data(show_spinner="Splitting the order over the offers…")
def _split(args: tuple, offers: tuple):
    return lp.split_from_offers(world(), run_plans(*args), list(offers))


@st.cache_data(show_spinner="Re-forecasting the order from the update…")
def _forecast(args: tuple, offers: tuple, fid: str, done: int, delay, day: int):
    b = run_plans(*args)
    res, cands = _split(args, offers)
    declined = [o.factory_id for o in offers if o.decision == "reject"]
    fc = lp.forecast_after_update(world(), b, res.recommended, cands, fid, done, delay, day)
    last = lp.last_rescue_day(world(), b, fc.shortfall, {ln.factory_id for ln in res.recommended.plan.lines}
                              | set(declined), today=day)  # (day, factory that could still make the shortfall)
    opts = lp.rescue_options(world(), b, res.recommended, cands, fc, declined, day)
    return fc, last, opts


def _was_approved(oid: str, plan_id: str) -> bool:
    """Approved at some point (a later approval supersedes it, which still counts as approved here)."""
    return any(a["plan_id"] == plan_id and a["status"] in ("approved", "superseded")
               for a in book().store.allocations(oid))


def _role_or_none() -> str | None:
    r = st.session_state["role"]
    return r if r in EXPORTER_ROLES else None


def _simulated_tag(msg: dict) -> str:
    return "simulated · placeholder text" if msg.get("draft_of") == "placeholder" else "simulated"


def section_sourcing(args, oid: str) -> bool:
    """Day 0: request drafts for the plan's factories plus a standby; a human approves before anything goes."""
    b = run_plans(*args)
    st.header("Day 0 · Sourcing round", anchor="sourcing")
    drafts = book().draft_round(oid, b, persona_file())
    reqs = book().requests(oid)
    st.caption("Bunon asks the factories in the plan for their planned pieces, plus the best proven factory "
               "outside the plan as a standby. Drafts use a fixed template whose numbers come from the plan; "
               "nothing is sent until you approve. (LLM-written drafts come next, under the same no-numbers rule.)")
    st.dataframe(pd.DataFrame([{"Factory": m["factory_id"], "Asked for (pcs)": reqs[m["factory_id"]]["qty"],
                                "Finish by": reqs[m["factory_id"]]["finish_by"], "Language": m["language"],
                                "Draft": m["text"], "Status": "sent" if m["sent_day"] is not None else "draft"}
                               for m in drafts]), hide_index=True, width="stretch")
    if book().round_sent(oid):
        st.success("Requests approved and sent in the app (each factory sees it in its inbox).")
        return True
    if _role_or_none() is None:
        st.info("Switch to an exporter role to approve the requests.")
    elif st.button("Approve and send the requests", type="primary", key="send_requests"):
        book().approve_round(oid, st.session_state["role"])
        st.rerun()
    return False


def section_offers(args, oid: str) -> tuple | None:
    """Day 2: simulated factories reply; the LLM reads each reply; the code checks every quote."""
    st.header(f"Day {lp.REPLY_DAY} · Replies and offers", anchor="offers")
    if book().day(oid) < lp.REPLY_DAY:
        if st.button(f"Next: day {lp.REPLY_DAY}, the factories reply", type="primary", key="go_day_replies"):
            book().advance_to(oid, lp.REPLY_DAY, "factories reply to the sourcing round", _role_or_none())
            st.rerun()
        return None
    reqs = book().requests(oid)
    book().receive(oid, persona_file(), "offer_reply", list(reqs))
    have = {o["factory_id"] for o in book().store.rows("offers", oid)}
    rows = []
    for m in [m for m in book().store.messages(oid) if m["direction"] == "in" and m["kind"] == "offer_reply"]:
        res = llm_once("Reading the replies…", _parse_offer, m["text"], reqs[m["factory_id"]]["qty"])
        if m["factory_id"] not in have:
            book().record_offer(oid, m, res)
        qty, delay = sv.reply_terms(res)
        rows.append({"Factory": m["factory_id"], "Reply": m["text"], "Decision": res.decision or "read by hand",
                     "Pieces": "" if qty is None else f"{qty:,}", "Days late": "" if delay is None else str(delay),
                     "Conditions (quoted)": "; ".join(f"“{c.source_span}”" for c in res.conditions if c.status == "ok"),
                     "Read by": SOURCE_BADGE.get(res.meta.source, res.meta.source) if res.meta else "unavailable",
                     "Source": _simulated_tag(m)})
    st.caption("The LLM classifies each reply and pulls out pieces and delay, each with a quote the code checks; "
               "the code settles the decision. Prices in replies are shown as quoted conditions; reading them as "
               "numbers comes with the offer parser (next milestone).")
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    missing = [f for f in reqs if f not in {r["Factory"] for r in rows}]
    if missing:
        st.caption("No reply yet from: " + ", ".join(missing))
    return tuple(book().offer_terms(oid))


def section_split(args, oid: str, offers: tuple) -> object | None:
    """Day 2: the best split over the factories that said yes; the owner approves it."""
    st.header(f"Day {lp.REPLY_DAY} · The split", anchor="split")
    res, cands = _split(args, offers)
    pt = res.recommended
    if pt.risk is None:
        st.error("**No feasible split.** The factories that said yes cannot cover the order under the current "
                 "limits. Ask more factories or loosen a limit; nothing was sent.")
        return None
    s = run_plans(*args).settings
    m = st.columns(3)
    m[0].metric("Chance of success", f"{pt.risk.p_meet_qty:.1%}")
    m[1].metric("Pieces allocated", f"{pt.plan.total_allocated:,}", f"+{pt.plan.buffer_qty:,} buffer")
    m[2].metric("Total cost", bdt_m(pt.plan.cost_total), help=bdt(pt.plan.cost_total))
    if not res.target_met:
        st.error(f"The factories that said yes cannot reach the {s.target:.0%} target; this is the most likely split.")
    off = {o.factory_id: o for o in offers}
    st.dataframe(pd.DataFrame([{"Factory": ln.factory_id, "Pieces": ln.qty, "Offered / asked": off[ln.factory_id].qty
                                or off[ln.factory_id].requested, "Share": f"{ln.share:.0%}"}
                               for ln in pt.plan.lines]), hide_index=True, width="stretch")
    book().propose(oid, pt, "Split from offers")
    approval_buttons("split", pt, "Split from offers", f"{len(pt.plan.lines)} factories, P {pt.risk.p_meet_qty:.3f}, "
                     f"{bdt(pt.plan.cost_total)}", run_plans(*args).order, cands, "split")
    return pt if _was_approved(oid, pt.plan.plan_id) else None


def section_update(args, oid: str, offers: tuple, split_pt) -> tuple | None:
    """Day 12: a daily update arrives; the code projects that factory and re-simulates the order."""
    st.header(f"Day {lp.UPDATE_DAY} · Daily update and warning", anchor="update")
    if book().day(oid) < lp.UPDATE_DAY:
        if st.button(f"Next: day {lp.UPDATE_DAY}, a daily update arrives", type="primary", key="go_day_update"):
            book().advance_to(oid, lp.UPDATE_DAY, "daily updates from the factories", _role_or_none())
            st.rerun()
        return None
    qty = {ln.factory_id: ln.qty for ln in split_pt.plan.lines}
    book().receive(oid, persona_file(), "progress_update", list(qty))
    msgs = [m for m in book().store.messages(oid) if m["direction"] == "in" and m["kind"] == "progress_update"
            and m["factory_id"] in qty]  # only factories in the agreed split
    if not msgs:
        st.info("No updates today.")
        return None
    m = msgs[-1]
    res = llm_once("Reading the update…", _parse_update, m["text"], qty[m["factory_id"]])
    if not any(u["update_id"] == m["msg_id"] for u in book().store.rows("progress_updates", oid)):
        book().record_update(oid, m, res)
    st.markdown(f"**{m['factory_id']}** ({_simulated_tag(m)}): “{m['text']}”")
    if res.meta:
        st.caption(SOURCE_BADGE[res.meta.source])
    for note in res.notes:
        st.warning(note)
    icon = {"ok": "✅ found in update", "missing": "— not stated", "rejected": "⚠️ check failed"}
    st.dataframe(pd.DataFrame([
        {"Item": "Pieces finished so far", "Value": "" if res.pieces_done.value is None else f"{res.pieces_done.value:,}",
         "Quote": res.pieces_done.source_span or "", "Check": icon[res.pieces_done.status]},
        {"Item": "Days late", "Value": "" if res.delay_days.value is None else str(res.delay_days.value),
         "Quote": res.delay_days.source_span or "", "Check": icon[res.delay_days.status]},
        {"Item": "Reason given", "Value": res.cause.value or "", "Quote": res.cause.source_span or "",
         "Check": icon[res.cause.status]}]), hide_index=True, width="stretch")
    if res.pieces_done.status != "ok":
        st.info("The update states no verified number of finished pieces: ask the factory before re-forecasting.")
        return None
    delay = res.delay_days.value if res.delay_days.status == "ok" else None
    # Forecast as of the day the update was sent, so later days do not change an approved rescue.
    fc, (last, last_by), opts = _forecast(args, offers, m["factory_id"], res.pieces_done.value, delay,
                                          m["story_day"])
    book().record_forecast(oid, fc, last)
    k = st.columns(4)
    k[0].metric(f"{fc.factory_id}: pieces by its ship-by day", f"{fc.projected:,}", f"{-fc.shortfall:,} vs plan",
                delta_color="normal")
    k[1].metric("Chance of success", f"{fc.p_after:.1%}", f"{(fc.p_after - fc.p_before) * 100:+.1f} pts")
    k[2].metric("Expected good pieces", f"{fc.expected_good_after:,.0f}", help=f"Needed: {fc.min_good:,}")
    k[3].metric("Last day to rescue", f"day {last}" if last is not None else "—",
                help=f"{book().date_of(last):%d %b %Y}" if last is not None else "No factory outside the plan can "
                     "still make the shortfall in time.")
    st.warning(f"**{fc.factory_id} is behind.** It has finished {fc.pieces_done:,} of {fc.planned:,} pieces "
               f"({fc.rate_seen:,.0f} a day so far against {fc.rate_plan:,.0f} planned)"
               + (f" and reports {delay} days lost" if delay else "") + f". At this rate it makes about "
               f"{fc.projected:,} pieces by its ship-by day, {fc.shortfall:,} short. "
               + (f"A rescue is still possible until day {last} ({book().date_of(last):%d %b}), e.g. at {last_by}."
                  if last is not None else "No factory outside the plan can still make the shortfall in time."))
    st.caption("Every number above comes from the update's quoted figures and the simulation; the explanation is "
               "a fixed template (an LLM-written explanation with the same placeholder rule comes later).")
    return fc, last, opts


def section_rescue(args, oid: str, fc, opts) -> bool:
    """Day 12: rescue options with their chance and cost; a production manager decides."""
    st.header(f"Day {lp.UPDATE_DAY} · Rescue", anchor="rescue")
    b = run_plans(*args)
    st.dataframe(pd.DataFrame([{"Option": o.title, "Chance of success": "" if o.p_after is None else f"{o.p_after:.1%}",
                                "Extra cost": "" if o.cost_delta_bdt is None else bdt(o.cost_delta_bdt),
                                "Details": o.note, "Possible": "yes" if o.allowed else "no"} for o in opts]),
                 hide_index=True, width="stretch")
    chosen = book().chosen_rescue(oid)
    move = next((o for o in opts if o.kind == "move"), None)
    if chosen == "move" and move is not None and move.replan is not None and any(
            a["plan_id"] == move.replan.new.plan.plan_id and a["status"] == "rejected"
            for a in book().store.allocations(oid)):
        st.warning("The move plan was rejected. Choose again.")
        chosen = None
    if chosen is None:
        if _role_or_none() is None:
            st.info("Switch to an exporter role (production manager) to choose.")
            return False
        c = st.columns(len(opts))
        for i, o in enumerate(opts):
            if c[i].button(f"Choose: {o.title}", key=f"rescue_{o.kind}", disabled=not o.allowed):
                book().choose_rescue(oid, o, st.session_state["role"])
                st.rerun()
        return False
    opt = next(o for o in opts if o.kind == chosen)
    st.success(f"Chosen: **{opt.title}**.")
    if chosen != "move":
        return True
    rp = opt.replan
    approval_buttons("rescue", rp.new, "Rescue: move the shortfall", f"{opt.note}; P {rp.new.risk.p_meet_qty:.3f}",
                     b.order, _split(args, tuple(book().offer_terms(oid)))[1] + b.cands, "rescue", po_download=False)
    if not _was_approved(oid, rp.new.plan.plan_id):
        return False
    extra = [(d["factory_id"], d["new_qty"] - d["old_qty"]) for d in rp.diff
             if d["new_qty"] > d["old_qty"] and d["factory_id"] != fc.factory_id]
    lang = {p.factory_id: p.language for p in persona_file().personas}
    sent = {m["factory_id"] for m in book().store.messages(oid) if m["kind"] == "rescue_request"}
    drafts = [(f, n, lp.draft_rescue_request(f, n, b.order, lang.get(f, "English"))) for f, n in extra]
    st.dataframe(pd.DataFrame([{"To": f, "Extra pieces": n, "Draft": t,
                                "Status": "sent" if f in sent else "draft"} for f, n, t in drafts]),
                 hide_index=True, width="stretch")
    if all(f in sent for f, _, _ in drafts):
        book().receive(oid, persona_file(), "rescue_reply", [f for f, _, _ in drafts])
        extra_by = dict(extra)
        for m in [m for m in book().store.messages(oid) if m["direction"] == "in" and m["kind"] == "rescue_reply"]:
            res = llm_once("Reading the reply…", _parse_offer, m["text"], extra_by.get(m["factory_id"], 0))
            verdict = {"accept": "✅ accepts", "reject": "✖ declines", "counter": "↔ counters"}.get(res.decision,
                                                                                               "read by hand")
            st.markdown(f"**{m['factory_id']}** {verdict} ({_simulated_tag(m)}): “{m['text']}”")
            if res.decision != "accept":
                st.warning(f"{m['factory_id']} did not simply accept the extra pieces: re-plan before relying on it.")
        return True
    if st.button("Approve and send the rescue requests", type="primary", key="send_rescue"):
        for f, _, t in drafts:
            book().send_message(oid, f, t, st.session_state["role"], f"rescue-{f}-day{book().day(oid)}")
        book().advance_to(oid, lp.UPDATE_DAY + 1, "rescue requests answered", _role_or_none())
        st.rerun()
    return False


def section_qc(args, oid: str) -> bool:
    """Day 26: goods arrive; the quality inspector records good and rejected pieces."""
    st.header(f"Day {lp.QC_DAY} · Receive and QC", anchor="qc")
    if book().day(oid) < lp.QC_DAY:
        if st.button(f"Next: day {lp.QC_DAY}, goods arrive for inspection", type="primary", key="go_day_qc"):
            book().advance_to(oid, lp.QC_DAY, "goods arrived (days 24-26) and are inspected", _role_or_none())
            st.rerun()
        return False
    alloc = book().approved_allocation(oid)
    done = {q["factory_id"]: q for q in book().store.rows("qc_records", oid)}
    script = {q.factory_id: q for q in persona_file().qc_script}
    projected = {f["factory_id"]: f for f in book().store.rows("forecasts", oid)}
    rows = []
    for ln in alloc["lines"]:
        sc = script.get(ln["factory_id"])
        can_make = ln["qty"]
        if ln["factory_id"] in projected:  # what the update said it can make (the move plan already locks it there)
            fcr = projected[ln["factory_id"]]
            can_make = min(ln["qty"], int(fcr["projected"])) if fcr.get("projected") is not None else ln["qty"]
        received = round(can_make * (sc.delivered_share if sc else 1.0))
        rejected = round(received * (sc.reject_share if sc else 0.0))
        rows.append({"factory_id": ln["factory_id"], "received": received, "rejected": rejected,
                     "lot_rejected": bool(sc.lot_rejected) if sc else False,
                     "day": sc.story_day if sc else lp.QC_DAY, "placeholder": bool(sc and sc.placeholder)})
    st.caption("Pre-filled from the scripted delivery and QC outcome (simulated, placeholder shares); the "
               "inspector checks and records. Only good pieces count.")
    edited = st.data_editor(pd.DataFrame(rows), hide_index=True, width="stretch", key="qc_editor",
                            disabled=["factory_id", "day", "placeholder"])
    if done:
        st.success(f"QC recorded for {len(done)} factories: {sum(q['good'] for q in done.values()):,} good pieces.")
        return True
    if st.session_state["role"] != "Quality inspector":
        st.info("Switch the role to **Exporter · Quality inspector** to record QC.")
        return False
    if st.button("Record QC", type="primary", key="record_qc"):
        recs = edited.to_dict("records")
        bad = [x["factory_id"] for x in recs if pd.isna(x["received"]) or pd.isna(x["rejected"])
               or not 0 <= int(x["rejected"]) <= int(x["received"])]
        if bad:
            st.error("Check the rows for " + ", ".join(bad) + ": received and rejected are required, and rejected "
                     "must be between 0 and received. Nothing was recorded.")
            return False
        for x in recs:
            book().record_qc(oid, x["factory_id"], int(x["received"]), int(x["rejected"]), bool(x["lot_rejected"]),
                             st.session_state["role"], int(x["day"]))
        st.rerun()
    return False


def section_record(oid: str) -> None:
    st.header("Track record from this order", anchor="record")
    tr = book().track_record(oid)
    st.dataframe(pd.DataFrame([{"Factory": r["factory_id"], "Answered": "yes" if r["answered"] else "no",
                                "Decision": r["decision"], "Delay reported (days)": r["delay_reported_days"],
                                "Delivered on time": "" if r["delivered_on_time"] is None
                                else ("yes" if r["delivered_on_time"] else "no"),
                                "QC pass": "" if r["qc_pass_rate"] is None else f"{r['qc_pass_rate']:.1%}"}
                               for r in tr]), hide_index=True, width="stretch")
    st.caption("Counted only from what happened on the platform in this order (requests, replies, updates, QC), "
               "never from rumours. In this demo the factories are simulated. Delay causes come in a later "
               "milestone.")


def section_payments(oid: str) -> None:
    """The exporter's side of the record: what it owes each factory and whether it paid on time."""
    st.header("Payments: the exporter's record", anchor="payments")
    due = book().amounts_due(oid)
    if not due:
        st.caption("Nothing is due yet: payments follow the recorded QC.")
        return
    st.dataframe(pd.DataFrame([{"Factory": d["factory_id"], "Good pieces": d["good"],
                                "CM (BDT/pc)": round(d["unit_price_bdt"], 1), "Amount (BDT)": round(d["amount_bdt"]),
                                "Due (day)": d["due_day"],
                                "Paid (day)": "" if d["paid_day"] is None else d["paid_day"]} for d in due]),
                 hide_index=True, width="stretch")
    st.caption(f"Amount = good pieces × the agreed CM rate, due {C.PAYMENT_TERM_DAYS} days after delivery "
               "(placeholder term). Factories see whether an exporter pays on time, from these records only.")
    unpaid = [d for d in due if d["paid_day"] is None]
    if unpaid:
        if st.session_state["role"] not in EXPORTER_ROLES:
            st.info("Switch to an exporter role to record a payment.")
        elif st.button(f"Record payment today (day {book().day(oid)}) to {len(unpaid)} factor"
                       f"{'y' if len(unpaid) == 1 else 'ies'}", key="record_payments"):
            for d in unpaid:
                book().record_payment(oid, d["factory_id"], st.session_state["role"])
            st.rerun()
    rec = book().exporter_record(oid)
    if rec["payments"]:
        st.metric("Paid on time", f"{rec['on_time']} of {rec['payments']}")


def view_timeline() -> None:
    st.title("Order timeline")
    prototype_notice()
    section_intake()
    args = current_args()
    if args is None:
        st.info("Read the email and confirm the order to continue.")
        return
    b = run_plans(*args)
    order, cands, rec, s = b.order, b.cands, b.rec, b.settings
    oid = st.session_state["order_id"]
    st.success(f"Order confirmed: **{order.total_qty:,}** pieces, own capacity {order.own_capacity_qty:,} → "
               f"**subcontract {order.subcontract_qty:,}** by {order.deadline:%d %b %Y} (fixed).")

    st.header("Day 0 · Plan", anchor="plans")
    if not b.feasible:  # the optimiser found no allocation at all under these limits
        st.error(f"**No feasible plan.** No combination of at most {s.max_f} factor{'y' if s.max_f == 1 else 'ies'} "
                 f"can cover {order.subcontract_qty:,} pieces by the deadline under the current limits (unproven "
                 f"share {s.unproven:.0%}, district share {(s.dcap or 1.0):.0%}, {s.slack} days of slack). Nothing "
                 "was planned or sent.")
        st.info("Loosen a limit in the planning settings (most often: allow more factories or a higher district "
                "share), or escalate to the buyer: the deadline and quantity are fixed.")
        section_timeline()
        return
    book().propose(oid, rec, "Initial plan")
    bl = b.comp["baseline"]
    m = st.columns(3)
    m[0].metric("Chance of success", f"{rec.risk.p_meet_qty:.1%}",
                help=f"P(good pieces by the deadline ≥ {1 - s.tol:.0%} of {order.subcontract_qty:,}). "
                     f"±{rec.risk.p_meet_se:.1%} simulation error.")
    m[1].metric("Pieces allocated", f"{rec.plan.total_allocated:,}", f"+{rec.plan.buffer_qty:,} buffer")
    m[2].metric("Total cost", bdt_m(rec.plan.cost_total), help=bdt(rec.plan.cost_total))
    st.caption(f"For comparison, the naive plan (declared capacity + lowest price, no buffer) has a "
               f"{bl.risk.p_meet_qty:.0%} chance." if bl.risk else "The naive plan found no allocation.")
    if not b.comp["ours"].target_met:
        st.error(f"No plan with a buffer ≤ 25% reaches the {s.target:.0%} target. Showing the most likely plan. "
                 "The deadline is fixed: the options are quantity, target or more slack.")
    by_id = {c.factory_id: c for c in cands}
    st.dataframe(pd.DataFrame([{
        "Factory": ln.factory_id, "District": by_id[ln.factory_id].district, "Pieces": ln.qty,
        "Share": f"{ln.share:.0%}", "CM (BDT/pc)": round(ln.unit_price_bdt, 1),
        "Transit (days)": by_id[ln.factory_id].transit_days, "Production days": by_id[ln.factory_id].window_days,
        "Short/late in failed simulations": f"{rec.risk.fail_contribution.get(ln.factory_id, 0):.0%}",
    } for ln in rec.plan.lines]), hide_index=True, width="stretch")
    st.caption("This plan decides whom to ask. The full comparison, reliability forecasts and the buffer curve "
               "are under **Show the maths**.")
    st.subheader("Decision memo")
    memo = memo_for(*args)
    if memo is None:
        st.error("No feasible plan to explain.")
        return
    show_memo(memo)
    approval_buttons("plan", rec, "Initial plan", f"{len(rec.plan.lines)} factories, {rec.plan.total_allocated:,} "
                     f"pcs, P {rec.risk.p_meet_qty:.3f}, {bdt(rec.plan.cost_total)}", order, cands, "plan")

    if not _was_approved(oid, rec.plan.plan_id) and not book().store.rows("sourcing_rounds", oid):
        st.info("Approve the plan to start the sourcing round.")
    else:
        if section_sourcing(args, oid):
            offers = section_offers(args, oid)
            if offers is not None:
                split_pt = section_split(args, oid, offers)
                if split_pt is not None:
                    upd = section_update(args, oid, offers, split_pt)
                    if upd is not None:
                        fc, _, opts = upd
                        if section_rescue(args, oid, fc, opts) and section_qc(args, oid):
                            section_record(oid)
                            section_payments(oid)
    section_ask(args)
    section_timeline()


# =============================================================================
# Dispatch
# =============================================================================

if view == "Factory app":
    view_factory()
elif view == "Show the maths":
    view_maths()
else:
    view_timeline()
st.caption("Data: Open Supply Hub (pseudonymised) + synthetic history, seed 42. LLM: Gemini, answers cached; "
           "every number in a memo is filled in from the optimiser and simulation results.")
