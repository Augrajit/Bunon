"""Bunon: Streamlit UI walking through the scripted story (PLAN.md §8).

Run:  streamlit run app.py
LLM answers are replayed from data/llm_cache (offline). Set BUNON_LLM_MODE=live and GEMINI_API_KEY
to read new emails live.
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from bunon import agent as ag
from bunon import config as C
from bunon import decisions as dl
from bunon import optimizer as op
from bunon import reliability as rl
from bunon.data_gen import load_public
from bunon.llm import LLMClient, current_mode
from bunon.schemas import OrderConstraints

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
def run_plans(order_json: str, slack, target, unproven, district_cap, partial, shocks):
    w = world()
    order = OrderConstraints.model_validate_json(order_json)
    p = op.default_params(order, planned_slack_days=slack, unproven_cap_pct=unproven, max_district_share=district_cap)
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
def memo_for(order_json: str, slack, target, unproven, district_cap, partial, shocks):
    out = run_plans(order_json, slack, target, unproven, district_cap, partial, shocks)
    return ag.write_memo(out["facts"], "initial", LLMClient()) if out["facts"] else None


@st.cache_data(show_spinner="Re-planning: confirmed factories locked, new ones start late…")
def run_replan(order_json: str, slack, target, unproven, district_cap, partial, shocks, days: int):
    out = run_plans(order_json, slack, target, unproven, district_cap, partial, shocks)
    rec = out["comp"]["ours"].recommended
    rp = op.replan_locked(world(), out["order"], out["params"], rec, op.largest_line(rec), days_elapsed=days,
                          target_p=target, shocks=shocks, partial_lots=partial)
    memo = ag.write_memo(ag.factsheet_for_replan(out["facts"], rp), "replan", LLMClient())
    p_late = out["params"].model_copy(update={"excluded_ids": [rp.excluded_id]})
    from datetime import timedelta
    cands_late = op.build_candidates(world(), out["order"], p_late, as_of=C.AS_OF + timedelta(days=days))
    return rp, memo, cands_late


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
    slack = st.slider("Planned slack before latest finish (days)", 0, 10, C.PLANNED_SLACK_DAYS)
    target = st.slider("Target P(on-time full delivery)", 0.50, 0.99, C.TARGET_P_MEET, 0.01)
    unproven = st.slider("Cap on unproven factories (share of pieces)", 0.0, 0.30, C.UNPROVEN_CAP_PCT, 0.05)
    use_cap = st.toggle(f"Max {C.DISTRICT_CAP_SHARE:.0%} of pieces per district", True)
    partial = st.toggle("Partial lots count (work finished by the ship date ships)", C.PARTIAL_LOTS_DEFAULT,
                        help="Off = strict view: a factory that runs past its slack delivers nothing on time.")
    shocks = st.toggle("Correlated district/national shocks", True)
    st.caption(f"LLM mode: **{current_mode()}** · model `{C.LLM_MODEL}`. As-of date {C.AS_OF:%d %b %Y} (fixed). "
               f"Delivery point: {C.DELIVERY_POINT_NAME}.")
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

args = (st.session_state["order_json"], int(slack), float(target), float(unproven),
        C.DISTRICT_CAP_SHARE if use_cap else None, bool(partial), bool(shocks))
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
st.header("7 · Disruption: a factory declines")
gone = op.largest_line(rec)
DAYS = 3
st.write(f"Scripted scenario: on day {DAYS}, **{gone}**, the factory with the largest allocation, declines. "
         "Factories that already confirmed keep their quantities (they may be asked for more, never less); "
         f"new factories can only start now, so they have {DAYS} fewer production days. The deadline does not move.")
if st.button(f"Simulate: {gone} declines on day {DAYS}", type="primary"):
    st.session_state["replan_for"] = args
if st.session_state.get("replan_for") == args:
    rp, rmemo, cands_late = run_replan(*args, DAYS)
    n = rp.new
    d = st.columns(3)
    d[0].metric("New P(on-time full delivery)", f"{n.risk.p_meet_qty:.1%}" if n.risk else "—",
                delta=f"{(n.risk.p_meet_qty - rec.risk.p_meet_qty) * 100:+.1f} pts" if n.risk else None)
    d[1].metric("New cost", bdt_m(n.plan.cost_total), delta=bdt(n.plan.cost_total - rec.plan.cost_total),
                delta_color="inverse")
    d[2].metric("New buffer", f"{n.buffer_pct:.1%}")
    st.dataframe(pd.DataFrame([{
        "Factory": x["factory_id"], "Before": x["old_qty"], "After": x["new_qty"], "Change": x["new_qty"] - x["old_qty"],
        "Note": ("declined" if x["factory_id"] == rp.excluded_id else
                 "new: starts on day 3" if x["old_qty"] == 0 else "confirmed: locked ≥ before"),
    } for x in rp.diff]), hide_index=True, width="stretch")
    st.subheader("Re-plan memo")
    show_memo(rmemo)
    all_cands = list({c.factory_id: c for c in [*cands, *cands_late]}.values())
    approval_buttons("replan", n.plan, f"{gone} declined; {len(n.plan.lines)} factories, P "
                     f"{n.risk.p_meet_qty:.3f}, {bdt(n.plan.cost_total)}", order, all_cands, "replan")

# --- log ------------------------------------------------------------------------------------------------------
st.divider()
with st.expander("Decision log (this server)"):
    st.dataframe(dl.read_log(), hide_index=True, width="stretch")
    st.caption("Every confirmation, approval and rejection, with the acting role. No names are collected. "
               "On the hosted demo this log resets when the server restarts.")
st.caption("Data: Open Supply Hub (pseudonymised) + synthetic history, seed 42. LLM: Gemini, answers cached; "
           "every number in a memo is filled in from the optimiser and simulation results.")
