"""Bunon: thin Streamlit UI (Day 2).

Run:  streamlit run app.py
Order intake by LLM, the decision memo and the approval log arrive on Days 3-4.
"""
from __future__ import annotations

import json
from datetime import date

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from bunon import config as C
from bunon import optimizer as op
from bunon import reliability as rl
from bunon.data_gen import load_public
from bunon.schemas import OrderConstraints

st.set_page_config(page_title="Bunon: subcontract allocation", page_icon="🧵", layout="wide")

SYNTH = "🧪 synthetic"


@st.cache_resource(show_spinner="Loading factories and fitting the reliability model…")
def world():
    return op.load_world()


@st.cache_data(show_spinner=False)
def report() -> dict:
    meta = load_public()["meta"]
    return json.loads(meta.loc[meta["key"] == "report", "value"].iloc[0])


def make_order(total: int, own: int, deadline: date, fob: float, penalty: float) -> OrderConstraints:
    return op.demo_order().model_copy(update=dict(
        total_qty=total, own_capacity_qty=own, deadline=deadline,
        fob_usd_per_piece=fob, late_penalty_pct_per_week=penalty))


@st.cache_data(show_spinner="Solving the allocation and simulating 20,000 outcomes per plan…")
def run_comparison(total, own, deadline, fob, penalty, slack, target, unproven, district_cap, partial, shocks):
    w = world()
    order = make_order(total, own, deadline, fob, penalty)
    p = op.default_params(order, planned_slack_days=slack, unproven_cap_pct=unproven,
                          max_district_share=district_cap)
    cands = op.build_candidates(w, order, p)
    r = op.compare_three(w, order, p, target_p=target, shocks=shocks, partial_lots=partial)
    rec = r["ours"].recommended
    match = (op.baseline_to_match(w, order, p, rec.risk.p_meet_qty, shocks=shocks, partial_lots=partial)
             if rec.risk else None)
    frontier = []
    for pt in r["ours"].frontier:
        bb = op.solve_baseline(cands, order.subcontract_qty, p, pt.buffer_pct)
        bp = op.evaluate(bb, cands, w, shocks, partial_lots=partial) if bb.status == "optimal" else None
        frontier.append({"buffer": pt.buffer_pct, "ours": pt.risk.p_meet_qty if pt.risk else None,
                         "ours_cost": pt.plan.cost_total, "baseline": bp.p_meet_qty if bp else None,
                         "baseline_cost": bb.cost_total})
    return dict(order=order, params=p, cands=cands, res=r, match=match, frontier=frontier)


@st.cache_data(show_spinner="Re-planning without the declining factory…")
def run_replan(total, own, deadline, fob, penalty, slack, target, unproven, district_cap, partial, shocks):
    out = run_comparison(total, own, deadline, fob, penalty, slack, target, unproven, district_cap, partial, shocks)
    rec = out["res"]["ours"].recommended
    gone = op.largest_line(rec)
    return op.replan_exclude(world(), out["order"], out["params"], rec, gone, reason="declined on day 3",
                             target_p=target, shocks=shocks, partial_lots=partial)


def bdt(x: float) -> str:
    return f"{x:,.0f} BDT"


def bdt_m(x: float) -> str:
    """Short form for metric tiles."""
    return f"{x / 1e6:,.2f}M BDT" if abs(x) >= 1e6 else f"{x / 1e3:,.0f}k BDT"


def plan_row(name: str, pt, cands) -> dict:
    shares = op.district_shares(pt.plan, cands) if pt.plan.lines else {}
    return {
        "Plan": name,
        "P(on-time full delivery)": None if pt.risk is None else round(pt.risk.p_meet_qty, 3),
        "Buffer": f"{pt.buffer_pct:.1%}",
        "Factories": len(pt.plan.lines),
        "Pieces": pt.plan.total_allocated,
        "Cost (BDT)": round(pt.plan.cost_total),
        "Buffer price (BDT)": round(pt.plan.cost_buffer),
        "Districts": ", ".join(f"{k} {v:.0%}" for k, v in shares.items()),
        "Status": pt.plan.status,
    }


# --- header -----------------------------------------------------------------------
st.title("Bunon: subcontract allocation for garment exporters")
st.warning(
    "**Prototype.** The factory list is real (Open Supply Hub), shown only as pseudonymous IDs. "
    "**All capacity, price and delivery-history figures are synthetic** (seeded, documented in "
    "DATA_PROVENANCE.md), and fill/lateness are placeholders pending research. "
    "Nothing is sent to any factory: every decision needs human approval.", icon="⚠️")

# --- sidebar: order + controls ------------------------------------------------------
with st.sidebar:
    st.header("1 · Order")
    st.caption("Fictional buyer order. LLM intake from an email arrives on Day 3.")
    total = st.number_input("Buyer order (pieces, basic T-shirts)", 1_000, 2_000_000, C.DEMO_TOTAL_QTY, 1_000)
    own = st.number_input("Your own capacity in the window (pieces)", 0, 2_000_000, C.DEMO_OWN_CAPACITY, 1_000)
    deadline = st.date_input("Delivery deadline (fixed)", C.DEMO_DEADLINE, min_value=C.AS_OF)
    fob = st.number_input("FOB price (USD/piece)", 0.5, 20.0, C.DEMO_FOB_USD, 0.05)
    penalty = st.number_input("Late penalty (% of FOB per week)", 0.0, 50.0, C.DEMO_PENALTY_PCT_PER_WEEK, 0.5)
    st.metric("To subcontract", f"{max(0, total - own):,} pieces")

    st.header("Planning controls")
    slack = st.slider("Planned slack before latest finish (days)", 0, 10, C.PLANNED_SLACK_DAYS)
    target = st.slider("Target P(on-time full delivery)", 0.50, 0.99, C.TARGET_P_MEET, 0.01)
    unproven = st.slider("Cap on unproven factories (share of pieces)", 0.0, 0.30, C.UNPROVEN_CAP_PCT, 0.05)
    use_cap = st.toggle(f"Max {C.DISTRICT_CAP_SHARE:.0%} of pieces per district", True)
    partial = st.toggle("Partial lots count (work finished by the ship date ships)", C.PARTIAL_LOTS_DEFAULT,
                        help="Off = strict view: a factory that runs past its slack delivers nothing on time.")
    shocks = st.toggle("Correlated district/national shocks", True)
    st.caption(f"As-of date {C.AS_OF:%d %b %Y} (fixed for a reproducible demo). "
               f"Delivery point: {C.DELIVERY_POINT_NAME}.")

if own >= total:
    st.success("Your own capacity covers the order: nothing to subcontract.")
    st.stop()

args = (int(total), int(own), deadline, float(fob), float(penalty), int(slack), float(target), float(unproven),
        C.DISTRICT_CAP_SHARE if use_cap else None, bool(partial), bool(shocks))
out = run_comparison(*args)
order, cands, res, match = out["order"], out["cands"], out["res"], out["match"]
ours = res["ours"]
rec = ours.recommended
w = world()

# --- 2 · funnel ---------------------------------------------------------------------
st.header("2 · Who can make it")
rep = report()
cols = st.columns(6)
cols[0].metric("Factories in export", f"{rep['raw_rows']:,}")
cols[1].metric("Open", f"{rep['open_rows']:,}")
cols[2].metric("Make T-shirts", f"{rep['tee_capable']:,}")
cols[3].metric("RSC or valid BSCI", f"{rep['compliant_tee']:,}")
cols[4].metric("Spare capacity + deadline OK", f"{len(cands):,}", help=SYNTH)
cols[5].metric("Proven (≥ 3 past jobs)", f"{sum(c.proven for c in cands):,}", help=SYNTH)
st.caption("First four counts: real Open Supply Hub data. Last two depend on synthetic capacity and history.")

# --- 3 · reliability ------------------------------------------------------------------
st.header(f"3 · Reliability forecast {SYNTH}")
fc_rows = []
for c in sorted(cands, key=lambda c: -c.window_capacity_risk_adj)[:15]:
    f = w.forecasts[c.factory_id]
    fc_rows.append({
        "Factory": c.factory_id, "District": c.district,
        "Declared capacity in window": c.window_capacity_declared,
        "Forecast deliverable": c.window_capacity_risk_adj,
        "P(delivered in full)": round(f.p_full, 2), "Fill p10": round(f.fill_p10, 3),
        f"P(on time within {slack} d)": round(rl.p_on_time(f, slack), 2),
        "Past jobs": f.n_jobs, "Thin history": "yes" if f.thin_history else "",
        "CM price (BDT/pc)": round(c.unit_price_bdt, 1),
    })
st.dataframe(pd.DataFrame(fc_rows), hide_index=True, width="stretch")
st.caption("Top 15 candidates by forecast deliverable quantity. Thin-history factories get the population "
           "prior, so their forecasts are wider.")

# --- 4 · three plans ----------------------------------------------------------------
st.header("4 · Plans compared")
headline = st.columns(3)
bl, bb = res["baseline"], res["baseline_buffered"]
headline[0].metric("Naive: declared capacity + lowest price",
                   f"{bl.risk.p_meet_qty:.0%}" if bl.risk else "—", help="P(on-time full delivery)")
headline[1].metric(f"Naive + same {rec.buffer_pct:.0%} buffer", f"{bb.risk.p_meet_qty:.0%}" if bb.risk else "—")
headline[2].metric("Bunon recommendation", f"{rec.risk.p_meet_qty:.0%}" if rec.risk else "—",
                   delta=f"{(rec.risk.p_meet_qty - bl.risk.p_meet_qty) * 100:+.0f} pts vs naive"
                   if rec.risk and bl.risk else None)
if not ours.target_met:
    st.error(f"No plan with a buffer ≤ 25% reaches the {target:.0%} target. Showing the most likely plan. "
             "The deadline is fixed, so the options are quantity, target or more slack.")

rows = [plan_row("Naive baseline", bl, cands), plan_row("Naive + same buffer", bb, cands),
        plan_row("Bunon (ours)", rec, cands)]
if match:
    rows.append(plan_row("Naive, buffer needed to match ours", match, cands))
st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
if match and rec.risk:
    st.info(f"To match our {rec.risk.p_meet_qty:.1%}, the naive rule needs a **{match.buffer_pct:.1%}** buffer "
            f"and costs **{bdt(match.plan.cost_total - rec.plan.cost_total)}** more.")
elif rec.risk:
    st.info("The naive rule cannot match our P(meet) with any buffer up to 60%.")

fr = pd.DataFrame(out["frontier"])
fig = go.Figure()
fig.add_trace(go.Scatter(x=fr["buffer"], y=fr["ours"], mode="lines+markers", name="Bunon"))
fig.add_trace(go.Scatter(x=fr["buffer"], y=fr["baseline"], mode="lines+markers", name="Naive + same buffer"))
fig.add_hline(y=target, line_dash="dot", annotation_text=f"target {target:.0%}")
fig.update_layout(height=320, margin=dict(l=10, r=10, t=30, b=10), xaxis_tickformat=".0%", yaxis_tickformat=".0%",
                  xaxis_title="Over-allocation buffer", yaxis_title="P(on-time full delivery)",
                  title="What the buffer buys", legend=dict(orientation="h", y=1.12))
st.plotly_chart(fig, width="stretch")

# --- 5 · recommended plan ---------------------------------------------------------------
st.header("5 · Recommended plan")
c1, c2, c3, c4 = st.columns(4)
c1.metric("Pieces allocated", f"{rec.plan.total_allocated:,}", f"+{rec.plan.buffer_qty:,} buffer")
c2.metric("Total cost", bdt_m(rec.plan.cost_total), help=bdt(rec.plan.cost_total))
c3.metric("Buffer price", bdt_m(rec.plan.cost_buffer), help=bdt(rec.plan.cost_buffer))
c4.metric("P(on-time full delivery)", f"{rec.risk.p_meet_qty:.1%}" if rec.risk else "—",
          help=f"±{rec.risk.p_meet_se:.1%} simulation error" if rec.risk else None)
by_id = {c.factory_id: c for c in cands}
st.dataframe(pd.DataFrame([{
    "Factory": ln.factory_id, "District": by_id[ln.factory_id].district, "Pieces": ln.qty, "Share": f"{ln.share:.0%}",
    "CM (BDT/pc)": round(ln.unit_price_bdt, 1), "Transport (BDT)": round(ln.transport_bdt),
    "Transit (days)": by_id[ln.factory_id].transit_days, "Production days": by_id[ln.factory_id].window_days,
    "Share of failures it contributes to": f"{rec.risk.fail_contribution.get(ln.factory_id, 0):.0%}" if rec.risk else "",
} for ln in rec.plan.lines]), hide_index=True, width="stretch")
st.caption(f"Costs: CM price + material ({C.MATERIAL_COST_BDT_PER_PIECE:.0f} BDT/pc, placeholder) + transport + "
           f"{bdt(C.FIXED_COST_PER_FACTORY_BDT)} per factory. Binding constraints: "
           f"{', '.join(rec.plan.binding_constraints) or 'none'}.")

# --- 6 · disruption -------------------------------------------------------------------------
st.header("6 · Disruption: a factory declines")
if rec.plan.lines:
    gone = op.largest_line(rec)
    st.write(f"Scripted scenario: **{gone}**, the factory with the largest allocation, declines on day 3.")
    if st.button(f"Simulate: {gone} declines", type="primary"):
        st.session_state["replan_for"] = args
    if st.session_state.get("replan_for") == args:
        rp = run_replan(*args)
        n = rp.new
        d1, d2, d3 = st.columns(3)
        d1.metric("New P(on-time full delivery)", f"{n.risk.p_meet_qty:.1%}" if n.risk else "—",
                  delta=f"{(n.risk.p_meet_qty - rec.risk.p_meet_qty) * 100:+.1f} pts" if n.risk else None)
        d2.metric("New cost", bdt_m(n.plan.cost_total), delta=bdt(n.plan.cost_total - rec.plan.cost_total),
                  delta_color="inverse")
        d3.metric("New buffer", f"{n.buffer_pct:.1%}")
        st.dataframe(pd.DataFrame([{
            "Factory": d["factory_id"], "Before": d["old_qty"], "After": d["new_qty"],
            "Change": d["new_qty"] - d["old_qty"],
            "Note": "declined" if d["factory_id"] == rp.excluded_id else ("new" if d["old_qty"] == 0 else ""),
        } for d in rp.diff]), hide_index=True, width="stretch")
        st.caption("Day 2 version: re-solves from scratch without the factory. From Day 4, confirmed "
                   "quantities at other factories are locked and the window shrinks by the days elapsed.")

st.divider()
st.caption("Decision memo (LLM, citing these numbers only) and the approval log arrive on Days 3–4. "
           "Data: Open Supply Hub (pseudonymised) + synthetic history, seed 42.")
