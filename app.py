"""Bunon: Streamlit UI walking through the scripted story (PLAN.md §8).

Run:  streamlit run app.py
LLM answers are replayed from data/llm_cache (offline). Set BUNON_LLM_MODE=live and GEMINI_API_KEY
to read new emails live.
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
             "optimizer", "agent", "decisions"]
    src = Path(bunon.__file__).parent
    digest = hashlib.sha1(b"".join((src / f"{m}.py").read_bytes() for m in order)).hexdigest()
    if getattr(bunon, "_source_digest", None) != digest:
        for m in order:
            importlib.reload(importlib.import_module(f"bunon.{m}"))
        st.cache_data.clear()
        st.cache_resource.clear()
        bunon._source_digest = digest


_reload_bunon_if_changed()

from bunon import agent as ag  # noqa: E402  (after the reload check, so names bind to current code)
from bunon import config as C  # noqa: E402
from bunon import decisions as dl
from bunon import optimizer as op
from bunon import reliability as rl
from bunon.data_gen import load_public
from bunon.llm import LLMClient, current_mode
from bunon.schemas import OrderConstraints
from bunon.textnum import literal_numbers

st.set_page_config(page_title="Bunon: subcontract allocation", page_icon="🧵", layout="wide")

SYNTH = "🧪 synthetic"
SOURCE_BADGE = {"cache": "🤖 LLM (cached answer)", "live": "🤖 LLM (live)",
                "template": "📄 Template (no valid LLM answer; numbers still from the tools)"}


# =============================================================================
# Cached computations (keyed on plain values so Streamlit can hash them)
# =============================================================================

@st.cache_resource(show_spinner="Loading factories and fitting the reliability model…")
def world():
    return op.load_world()


@st.cache_data(show_spinner=False)
def report() -> dict:
    meta = load_public()["meta"]
    return json.loads(meta.loc[meta["key"] == "report", "value"].iloc[0])


@st.cache_data(show_spinner="Reading the email…")
def read_email(email: str):
    return ag.parse_order(email, C.DEMO_OWN_CAPACITY, LLMClient())


@st.cache_data(show_spinner="Solving the allocation and simulating 20,000 outcomes per plan…")
def run_plans(order_json: str, slack, target, unproven, district_cap, partial, shocks, max_f=C.MAX_FACTORIES,
              excluded: tuple = ()):
    w = world()
    order = OrderConstraints.model_validate_json(order_json)
    p = op.default_params(order, planned_slack_days=slack, unproven_cap_pct=unproven, max_district_share=district_cap,
                          max_factories=max_f, excluded_ids=list(excluded))
    cands = op.build_candidates(w, order, p)
    comp = op.compare_three(w, order, p, target_p=target, shocks=shocks, partial_lots=partial)
    rec = comp["ours"].recommended
    match = (op.baseline_to_match(w, order, p, rec.risk.p_meet_qty, shocks=shocks, partial_lots=partial)
             if rec.risk else None)
    frontier = []
    for pt in comp["ours"].frontier:
        bb = op.solve_baseline(cands, order.subcontract_qty, p, pt.buffer_pct)
        bp = op.evaluate(bb, cands, w, shocks, partial_lots=partial) if bb.status == "optimal" else None
        frontier.append({"buffer": pt.buffer_pct, "ours": pt.risk.p_meet_qty if pt.risk else None,
                         "baseline": bp.p_meet_qty if bp else None})
    facts = (ag.factsheet_for_plan(order, comp, match, cands, slack, target, report()["compliant_tee"])
             if rec.risk else None)
    return dict(order=order, params=p, cands=cands, comp=comp, match=match, frontier=frontier, facts=facts)


@st.cache_data(show_spinner="Writing the decision memo…")
def memo_for(*args):
    out = run_plans(*args)
    return ag.write_memo(out["facts"], "initial", LLMClient()) if out["facts"] else None


@st.cache_data(show_spinner="Re-planning: confirmed factories locked, new ones start late…")
def run_replan(args: tuple, days: int):
    out = run_plans(*args)
    target, shocks, partial = args[2], args[6], args[5]
    rec = out["comp"]["ours"].recommended
    rp = op.replan_locked(world(), out["order"], out["params"], rec, op.largest_line(rec), days_elapsed=days,
                          target_p=target, shocks=shocks, partial_lots=partial)
    memo = ag.write_memo(ag.factsheet_for_replan(out["facts"], rp), "replan", LLMClient())
    p_late = out["params"].model_copy(update={"excluded_ids": [rp.excluded_id]})
    from datetime import timedelta
    cands_late = op.build_candidates(world(), out["order"], p_late, as_of=C.AS_OF + timedelta(days=days))
    return rp, memo, cands_late


@st.cache_data(show_spinner="Reading the reply…")
def read_reply(text: str, planned_qty: int):
    return ag.parse_reply(text, planned_qty, LLMClient())


@st.cache_data(show_spinner="Re-planning with the factory's reply…")
def run_reply_replan(args: tuple, factory_id: str, decision: str, qty, delay, days: int):
    from datetime import timedelta
    out = run_plans(*args)
    target, shocks, partial = args[2], args[6], args[5]
    rec = out["comp"]["ours"].recommended
    rp = op.replan_from_reply(world(), out["order"], out["params"], rec, factory_id, decision, qty, delay,
                              days_elapsed=days, target_p=target, shocks=shocks, partial_lots=partial)
    memo = ag.write_memo(ag.factsheet_for_replan(out["facts"], rp), "replan", LLMClient())
    p_late = out["params"].model_copy(update={"excluded_ids": [rp.excluded_id]})
    cands_late = op.build_candidates(world(), out["order"], p_late, as_of=C.AS_OF + timedelta(days=days))
    return rp, memo, cands_late


@st.cache_data(show_spinner="Reading the news…")
def read_news(text: str):
    return ag.parse_news(text, LLMClient())


@st.cache_data(show_spinner="Simulating the disruption and re-planning around it…")
def run_news(args: tuple, area: str, severity: str, duration):
    out = run_plans(*args)
    target, shocks, partial = args[2], args[6], args[5]
    rec = out["comp"]["ours"].recommended
    ev = op.event_from_severity(area, severity, duration)
    return op.assess_news(world(), out["order"], out["params"], rec, ev, target_p=target, shocks=shocks,
                          partial_lots=partial)


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
            "P(on-time full delivery)": None if pt.risk is None else round(pt.risk.p_meet_qty, 3),
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


def approval_buttons(step: str, plan, summary: str, order, cands, key: str):
    """Approve / reject, logged with the acting role. Approval unlocks draft POs (download only)."""
    role = st.session_state.get("role", dl.ROLES[-1])
    state_key = f"decision_{key}_{plan.plan_id}"
    c1, c2, _ = st.columns([1, 1, 1])
    if c1.button("✅ Approve", key=f"approve_{key}", type="primary"):
        dl.log_decision(step, "approved", role, plan.plan_id, summary)
        st.session_state[state_key] = "approved"
    if c2.button("✖ Reject", key=f"reject_{key}"):
        dl.log_decision(step, "rejected", role, plan.plan_id, summary)
        st.session_state[state_key] = "rejected"
    status = st.session_state.get(state_key)
    if status == "approved":
        st.success(f"Approved by **{role}** and logged. Nothing has been sent: download the drafts, "
                   "review them and send them yourself.")
        st.download_button("⬇ Draft purchase-order requests (.txt)", dl.draft_po_text(order, plan, cands, role),
                           file_name=f"DRAFT_{plan.plan_id}.txt", key=f"po_{key}")
    elif status == "rejected":
        st.warning("Rejected and logged. Change the planning controls in the sidebar to get a different plan.")


# =============================================================================
# Page
# =============================================================================

st.title("Bunon: subcontract allocation for garment exporters")
st.warning(
    "**Prototype.** The factory list is real (Open Supply Hub), shown only as pseudonymous IDs. "
    "**All capacity, price and delivery-history figures are synthetic** (seeded, see DATA_PROVENANCE.md) and "
    "fill/lateness are placeholders pending research. The buyer email is fictional. Nothing is ever sent to "
    "a factory: every decision needs a human.", icon="⚠️")

with st.sidebar:
    st.selectbox("Acting as (recorded in the decision log)", dl.ROLES, key="role")
    st.header("Planning controls")
    for k, v in (("slack", C.PLANNED_SLACK_DAYS), ("target", C.TARGET_P_MEET), ("unproven", C.UNPROVEN_CAP_PCT),
                 ("dcap", C.DISTRICT_CAP_SHARE), ("max_f", C.MAX_FACTORIES), ("excluded", [])):
        st.session_state.setdefault(k, v)
    slack = st.slider("Planned slack before latest finish (days)", 0, 10, key="slack")
    target = st.slider("Target P(on-time full delivery)", 0.50, 0.99, step=0.01, key="target")
    max_f = st.slider("Max number of factories", 1, 5, key="max_f")
    unproven = st.slider("Cap on unproven factories (share of pieces)", 0.0, 0.30, step=0.05, key="unproven")
    dcap = st.slider("Max share of pieces per district (100% = no cap)", 0.30, 1.0, step=0.05, key="dcap")
    if st.session_state["excluded"]:
        st.caption("Excluded factories: " + ", ".join(st.session_state["excluded"]))
        if st.button("Clear exclusions"):
            st.session_state["excluded"] = []
            st.rerun()
    partial = st.toggle("Partial lots count (work finished by the ship date ships)", C.PARTIAL_LOTS_DEFAULT,
                        help="Off = strict view: a factory that runs past its slack delivers nothing on time.")
    shocks = st.toggle("Correlated district/national shocks", True)
    st.caption(f"LLM mode: **{current_mode()}** · model `{C.LLM_MODEL}`. As-of date {C.AS_OF:%d %b %Y} (fixed). "
               f"Delivery point: {C.DELIVERY_POINT_NAME}. Python {sys.version.split()[0]}.")
    if st.button("↺ Restart the story"):
        for k in list(st.session_state):
            if k != "role":
                del st.session_state[k]
        st.rerun()

# --- 1 · intake -------------------------------------------------------------------------------
st.header("1 · Buyer email → order")
st.caption("The LLM reads the email into order terms. Each value must come with a word-for-word quote from "
           "the email that states it; anything that fails the check is left for you to enter.")
email = st.text_area("Buyer email", C.DEMO_EMAIL_PATH.read_text(encoding="utf-8"), height=240)
if st.button("Read the email", type="primary"):
    st.session_state["email_read"] = email

intake = read_email(st.session_state["email_read"]) if "email_read" in st.session_state else None
if intake is not None:
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
            else:
                order = OrderConstraints(
                    total_qty=int(total), own_capacity_qty=int(own), deadline=deadline,
                    delivery_point=pre("delivery_terms"), fabric=pre("fabric"), gsm=pre("gsm"),
                    quality_aql=aql or None, fob_usd_per_piece=fob or None,
                    late_penalty_pct_per_week=penalty or None, penalty_text=pre("penalty_text"),
                    compliance_required=pre("compliance_required") or ["RSC", "BSCI"],
                    source_spans={k: v.source_span for k, v in intake.fields.items()
                                  if v.status == "ok" and v.source_span})
                st.session_state["order_json"] = order.model_dump_json()
                dl.log_decision("order", "confirmed", st.session_state["role"], None,
                                f"{order.total_qty:,} pcs, own {order.own_capacity_qty:,}, "
                                f"subcontract {order.subcontract_qty:,}, deadline {order.deadline}")
                for k in [k for k in st.session_state if k.startswith(("decision_", "replan"))]:
                    del st.session_state[k]

if "order_json" not in st.session_state:
    st.info("Read the email and confirm the order to continue.")
    st.stop()

args = (st.session_state["order_json"], int(slack), round(float(target), 2), round(float(unproven), 2),
        round(float(dcap), 2) if dcap < 1.0 else None, bool(partial), bool(shocks), int(max_f),
        tuple(sorted(st.session_state["excluded"])))
out = run_plans(*args)
order, cands, comp, match = out["order"], out["cands"], out["comp"], out["match"]
ours, rec = comp["ours"], comp["ours"].recommended
w = world()
st.success(f"Order confirmed: **{order.total_qty:,}** pieces, own capacity {order.own_capacity_qty:,} → "
           f"**subcontract {order.subcontract_qty:,}** by {order.deadline:%d %b %Y} (fixed).")

# --- 2 · funnel ---------------------------------------------------------------------------------
st.header("2 · Who can make it")
rep = report()
r1 = st.columns(3)
r1[0].metric("Factories in export", f"{rep['raw_rows']:,}")
r1[1].metric("Open", f"{rep['open_rows']:,}")
r1[2].metric("Make T-shirts", f"{rep['tee_capable']:,}")
r2 = st.columns(3)
r2[0].metric("RSC or valid BSCI", f"{rep['compliant_tee']:,}")
r2[1].metric("Spare capacity + deadline OK", f"{len(cands):,}", help=SYNTH)
r2[2].metric("Proven (≥ 3 past jobs)", f"{sum(c.proven for c in cands):,}", help=SYNTH)
st.caption("First four counts: real Open Supply Hub data. Last two depend on synthetic capacity and history.")

# --- 3 · reliability ------------------------------------------------------------------------------
st.header(f"3 · Reliability forecast {SYNTH}")
st.dataframe(pd.DataFrame([{
    "Factory": c.factory_id, "District": c.district,
    "Declared capacity in window": c.window_capacity_declared, "Forecast deliverable": c.window_capacity_risk_adj,
    "P(delivered in full)": round(w.forecasts[c.factory_id].p_full, 2),
    f"P(on time within {slack} d)": round(rl.p_on_time(w.forecasts[c.factory_id], slack), 2),
    "Past jobs": w.forecasts[c.factory_id].n_jobs,
    "Thin history": "yes" if w.forecasts[c.factory_id].thin_history else "",
    "CM price (BDT/pc)": round(c.unit_price_bdt, 1),
} for c in sorted(cands, key=lambda c: -c.window_capacity_risk_adj)[:15]]), hide_index=True, width="stretch")
st.caption("Top 15 candidates by forecast deliverable quantity. Thin-history factories get the population "
           "prior, so their forecasts are wider.")

# --- 4 · plans --------------------------------------------------------------------------------------
st.header("4 · Plans compared")
bl, bb = comp["baseline"], comp["baseline_buffered"]
h = st.columns(3)
h[0].metric("Naive: declared capacity + lowest price", f"{bl.risk.p_meet_qty:.0%}" if bl.risk else "—",
            help="P(on-time full delivery of the subcontracted quantity)")
h[1].metric(f"Naive + same {rec.buffer_pct:.0%} buffer", f"{bb.risk.p_meet_qty:.0%}" if bb.risk else "—")
h[2].metric("Bunon recommendation", f"{rec.risk.p_meet_qty:.0%}" if rec.risk else "—")
if not ours.target_met:
    st.error(f"No plan with a buffer ≤ 25% reaches the {target:.0%} target. Showing the most likely plan. "
             "The deadline is fixed: the options are quantity, target or more slack.")
rows = [plan_row("Naive baseline", bl, cands), plan_row("Naive + same buffer", bb, cands),
        plan_row("Bunon (ours)", rec, cands)]
if match:
    rows.append(plan_row("Naive, buffer needed to match ours", match, cands))
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
if match and rec.risk:
    st.info(f"To match our {rec.risk.p_meet_qty:.1%}, the naive rule needs a **{match.buffer_pct:.1%}** buffer "
            f"and costs **{bdt(match.plan.cost_total - rec.plan.cost_total)}** more.")
fr = pd.DataFrame(out["frontier"])
fig = go.Figure()
fig.add_trace(go.Scatter(x=fr["buffer"], y=fr["ours"], mode="lines+markers", name="Bunon"))
fig.add_trace(go.Scatter(x=fr["buffer"], y=fr["baseline"], mode="lines+markers", name="Naive + same buffer"))
fig.add_hline(y=target, line_dash="dot", annotation_text=f"target {target:.0%}")
fig.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10), xaxis_tickformat=".0%", yaxis_tickformat=".0%",
                  xaxis_title="Over-allocation buffer", yaxis_title="P(on-time full delivery)",
                  title="What the buffer buys", legend=dict(orientation="h", y=1.12))
st.plotly_chart(fig, width="stretch")

# --- 5 · recommended plan ----------------------------------------------------------------------------
st.header("5 · Recommended plan")
m = st.columns(4)
m[0].metric("Pieces allocated", f"{rec.plan.total_allocated:,}", f"+{rec.plan.buffer_qty:,} buffer")
m[1].metric("Total cost", bdt_m(rec.plan.cost_total), help=bdt(rec.plan.cost_total))
m[2].metric("Buffer price", bdt_m(rec.plan.cost_buffer), help=bdt(rec.plan.cost_buffer))
m[3].metric("P(on-time full delivery)", f"{rec.risk.p_meet_qty:.1%}" if rec.risk else "—",
            help=f"±{rec.risk.p_meet_se:.1%} simulation error" if rec.risk else None)
by_id = {c.factory_id: c for c in cands}
st.dataframe(pd.DataFrame([{
    "Factory": ln.factory_id, "District": by_id[ln.factory_id].district, "Pieces": ln.qty, "Share": f"{ln.share:.0%}",
    "CM (BDT/pc)": round(ln.unit_price_bdt, 1), "Transit (days)": by_id[ln.factory_id].transit_days,
    "Production days": by_id[ln.factory_id].window_days,
    "Short/late in failed simulations": f"{rec.risk.fail_contribution.get(ln.factory_id, 0):.0%}" if rec.risk else "",
} for ln in rec.plan.lines]), hide_index=True, width="stretch")
st.caption(f"Costs: CM price + material ({C.MATERIAL_COST_BDT_PER_PIECE:.0f} BDT/pc, placeholder) + transport + "
           f"{bdt(C.FIXED_COST_PER_FACTORY_BDT)} per factory. Binding constraints: "
           f"{', '.join(rec.plan.binding_constraints) or 'none'}.")

# --- steering ---------------------------------------------------------------------------------------------
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
    dl.log_decision("steer", "confirmed", st.session_state["role"], None,
                    f"{instruction} -> " + "; ".join(f"{a['field']}={a['value']}" for a in accepted))
    st.session_state.pop("steer", None)


st.subheader("Ask for a change")
st.caption("Describe a change in plain language. The LLM may only change the planning settings in the sidebar "
           "(never the deadline, quantity or compliance), every value must be quoted from your words, and "
           "nothing changes until you apply it.")
ex = st.columns(len(STEER_EXAMPLES))
for i, e in enumerate(STEER_EXAMPLES):
    if ex[i].button(e, key=f"steer_ex_{i}"):
        st.session_state["steer_text"] = e
instruction = st.text_input("Instruction", key="steer_text", placeholder="e.g. at most four factories")
if st.button("Interpret") and instruction.strip():
    st.session_state["steer"] = (instruction, ag.steer(instruction, {c.factory_id for c in cands}, LLMClient()))
if "steer" in st.session_state:
    instr, sr = st.session_state["steer"]
    if sr.meta:
        st.caption(SOURCE_BADGE[sr.meta.source])
    current = {"target_p": target, "planned_slack_days": slack, "max_factories": max_f,
               "unproven_cap_pct": unproven, "max_district_share": dcap,
               "excluded_ids": st.session_state["excluded"] or "none"}
    if sr.accepted:
        st.dataframe(pd.DataFrame([{"Setting": a["label"], "Now": str(current[a["field"]]), "Proposed": str(a["value"]),
                                    "Your words": a["source_span"]} for a in sr.accepted]),
                     hide_index=True, width="stretch")
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

# --- 6 · memo + approval --------------------------------------------------------------------------------
st.header("6 · Decision memo")
memo = memo_for(*args)
if memo is None:
    st.error("No feasible plan to explain.")
    st.stop()
show_memo(memo)
approval_buttons("plan", rec.plan, f"{len(rec.plan.lines)} factories, {rec.plan.total_allocated:,} pcs, "
                 f"P {rec.risk.p_meet_qty:.3f}, {bdt(rec.plan.cost_total)}", order, cands, "plan")

# --- 7 · disruption ----------------------------------------------------------------------------------------
st.header("7 · Disruption: a factory declines or answers")
DAYS = 3
REPLIES = json.loads((C.ROOT / "data" / "demo" / "factory_replies.json").read_text(encoding="utf-8"))


def show_replan(rp, rmemo, cands_late, key: str, what: str):
    n = rp.new
    d = st.columns(3)
    d[0].metric("New P(on-time full delivery)", f"{n.risk.p_meet_qty:.1%}" if n.risk else "—",
                delta=f"{(n.risk.p_meet_qty - rec.risk.p_meet_qty) * 100:+.1f} pts" if n.risk else None)
    d[1].metric("New cost", bdt_m(n.plan.cost_total), delta=bdt(n.plan.cost_total - rec.plan.cost_total),
                delta_color="inverse")
    d[2].metric("New buffer", f"{max(0, n.plan.total_allocated - n.plan.target_qty) / n.plan.target_qty:.1%}")
    if n.risk and n.risk.p_meet_qty < target:
        st.error(f"The re-plan does not reach the {target:.0%} target (best found: {n.risk.p_meet_qty:.1%}). "
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
    approval_buttons(key, n.plan, f"{what}; {len(n.plan.lines)} factories, P {n.risk.p_meet_qty:.3f}, "
                     f"{bdt(n.plan.cost_total)}", order, all_cands, key)


def _run_safely(fn, *a):
    try:
        return fn(*a)
    except Exception:
        import traceback
        st.error("The re-plan failed. Nothing was changed or sent. Technical details below.")
        with st.expander("Technical details"):
            st.code(traceback.format_exc())
        st.stop()


tab_decline, tab_reply, tab_news = st.tabs(["Scripted decline", "Factory reply (English / Bangla / Banglish)", "News (English / Bangla)"])

with tab_decline:
    gone = op.largest_line(rec)
    st.write(f"On day {DAYS}, **{gone}**, the factory with the largest allocation, declines. Factories that "
             "already confirmed keep their quantities (they may be asked for more, never less); new factories can "
             f"only start now, so they have {DAYS} fewer production days. The deadline does not move.")
    if st.button(f"Simulate: {gone} declines on day {DAYS}", type="primary"):
        st.session_state["replan_for"] = args
    if st.session_state.get("replan_for") == args:
        rp, rmemo, cands_late = _run_safely(run_replan, args, DAYS)
        show_replan(rp, rmemo, cands_late, "replan", f"{gone} declined")

with tab_reply:
    st.caption("Paste what a factory wrote back. The LLM classifies it as accept / reject / counter and pulls out "
               "quantity, delay and conditions, each with a word-for-word quote that the code checks. The code, "
               "not the LLM, settles the final category, and a counter-offer is locked into the re-plan.")
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
                        placeholder="e.g. Bhai, 14,300 pcs pura parbo na, max 9 hajar dite parbo.")
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
                qty = res.quantity.value if res.quantity.status == "ok" else None
                delay = res.delay_days.value if res.delay_days.status == "ok" else None
                rp, rmemo, cands_late = _run_safely(run_reply_replan, args, rfid, res.decision, qty, delay, DAYS)
                show_replan(rp, rmemo, cands_late, "reply_replan", f"{rfid} replied ({res.decision})")

with tab_news:
    NEWS = json.loads((C.ROOT / "data" / "demo" / "news.json").read_text(encoding="utf-8"))
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
        ntext_r = st.session_state["news_read"][0]
        nres = read_news(ntext_r)
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
            m3.metric("Cost of re-planning", bdt(new.plan.cost_total - rec.plan.cost_total))
            st.write("Factories in the current plan that are hit: "
                     + (", ".join(f"**{f}**" for f in r["affected_in_plan"]) or "none"))
            if not r["new_target_met"]:
                st.error(f"Even a plan made knowing about this cannot reach the {target:.0%} target. "
                         "Escalate to the buyer early.")
            ncands = r["cands"]
            st.dataframe(pd.DataFrame([plan_row("Current plan (event applied)",
                                                 rec.model_copy(update={"risk": r["current_with_event"]}), cands),
                                        plan_row("Plan made knowing about it", new, ncands)]),
                         hide_index=True, width="stretch")
            approval_buttons("news_replan", new.plan, f"news: {sev} {area}; P {new.risk.p_meet_qty:.3f}, "
                             f"{bdt(new.plan.cost_total)}", order, ncands, "news_replan")

# --- log ------------------------------------------------------------------------------------------------------
st.divider()
with st.expander("Decision log (this server)"):
    st.dataframe(dl.read_log(), hide_index=True, width="stretch")
    st.caption("Every confirmation, approval and rejection, with the acting role. No names are collected. "
               "On the hosted demo this log resets when the server restarts.")
st.caption("Data: Open Supply Hub (pseudonymised) + synthetic history, seed 42. LLM: Gemini, answers cached; "
           "every number in a memo is filled in from the optimiser and simulation results.")
