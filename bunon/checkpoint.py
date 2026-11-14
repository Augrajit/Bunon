"""Day 2 checkpoint, re-run after SPEC.md D3: three plans, district shares, equal-reliability comparison.

Run:  python -m bunon.checkpoint   -> writes CHECKPOINT_DAY2.md
"""
from __future__ import annotations

from bunon import config as C
from bunon import optimizer as op
from bunon import reliability as rl

SLACKS = (0, 2, 4, 6, 8)


def _p(pt):
    return "—" if pt is None or pt.risk is None else f"{pt.risk.p_meet_qty:.3f}"


def _m(x):
    return f"{x / 1e6:.2f}M"


def _shares(d: dict[str, float]) -> str:
    return ", ".join(f"{k} {v:.0%}" for k, v in d.items())


def run() -> str:
    w = op.load_world()
    order = op.demo_order()
    Q = order.subcontract_qty
    out = [
        "# Day 2 checkpoint (re-run after SPEC.md D3)",
        "",
        f"Demo order: {order.total_qty:,} tees, own capacity {order.own_capacity_qty:,}, **subcontract Q = {Q:,}**, "
        f"deadline {order.deadline}, as-of {C.AS_OF}. Max {C.MAX_FACTORIES} factories, unproven cap "
        f"{C.UNPROVEN_CAP_PCT:.0%}, **district cap {C.DISTRICT_CAP_SHARE:.0%} (ours only)**, buffer grid ceiling 25%, "
        f"target P ≥ {C.TARGET_P_MEET}, {C.N_SIMS:,} simulations per plan, seed {C.SIM_SEED}, shocks on. "
        f"Costs in BDT: CM + material ({C.MATERIAL_COST_BDT_PER_PIECE:.0f}/piece) + transport + fixed per factory. "
        "**All performance data is synthetic; fill and lateness are placeholders pending research.**",
        "",
        "b* = cheapest buffer on our grid reaching the target, else the one with the highest P. "
        "\"Baseline to match\" = smallest buffer (2.5% steps, up to 60%) at which the naive baseline reaches our P.",
        "",
    ]
    for partial in (True, False):
        title = "A. Partial lots (default)" if partial else "B. \"Late = 0\" toggle (comparison)"
        out += [f"## {title}", "",
                "| S | Baseline P | cost | Baseline + b* P | cost | **Ours P** | b* | cost | buffer price "
                "| Baseline to match ours: buffer | its cost | Ours saves |",
                "|---|---|---|---|---|---|---|---|---|---|---|---|"]
        for S in SLACKS:
            p = op.default_params(order, planned_slack_days=S)
            r = op.compare_three(w, order, p, shocks=True, partial_lots=partial)
            ours, bl, bb = r["ours"], r["baseline"], r["baseline_buffered"]
            rec = ours.recommended
            match = op.baseline_to_match(w, order, p, rec.risk.p_meet_qty, partial_lots=partial) if rec.risk else None
            mb = "not reached ≤ 60%" if match is None else f"{match.buffer_pct:.1%}"
            mc = "—" if match is None else _m(match.plan.cost_total)
            sv = "—" if match is None else f"{(match.plan.cost_total - rec.plan.cost_total) / 1e6:+.2f}M"
            out.append(
                f"| {S} | {_p(bl)} | {_m(bl.plan.cost_total)} | {_p(bb)} | {_m(bb.plan.cost_total)} "
                f"| **{_p(rec)}** | {rec.buffer_pct:.1%} | {_m(rec.plan.cost_total)} | {_m(rec.plan.cost_buffer)} "
                f"| {mb} | {mc} | {sv} |")
        out.append("")

    S = C.PLANNED_SLACK_DAYS
    p = op.default_params(order, planned_slack_days=S)
    cands = op.build_candidates(w, order, p)
    by_id = {c.factory_id: c for c in cands}
    r = op.compare_three(w, order, p)
    rec = r["ours"].recommended
    match = op.baseline_to_match(w, order, p, rec.risk.p_meet_qty)
    out += [f"## District shares at S = {S} (partial lots)", "",
            "| Plan | P(meet) | Buffer | Factories | District shares |", "|---|---|---|---|---|"]
    rows = [("Baseline", r["baseline"]), ("Baseline + b*", r["baseline_buffered"]), ("Ours", rec)]
    if match:
        rows.append(("Baseline to match ours", match))
    for name, pt in rows:
        out.append(f"| {name} | {_p(pt)} | {pt.buffer_pct:.1%} | {len(pt.plan.lines)} "
                   f"| {_shares(op.district_shares(pt.plan, cands))} |")
    out.append("")

    res = op.search_buffer(w, order, p)
    out += [f"## Buffer frontier at S = {S} (partial lots)", "",
            "| Buffer | Ours P | Ours cost | Baseline + same buffer P | its cost |", "|---|---|---|---|---|"]
    for pt in res.frontier:
        bb = op.solve_baseline(cands, Q, p, pt.buffer_pct)
        bp = op.evaluate(bb, cands, w, True) if bb.status == "optimal" else None
        out.append(f"| {pt.buffer_pct:.1%} | {_p(pt)} | {_m(pt.plan.cost_total)} "
                   f"| {'—' if bp is None else f'{bp.p_meet_qty:.3f}'} | {_m(bb.cost_total)} |")
    out.append("")

    out += [f"## Recommended plan at S = {S}", "",
            f"Buffer {rec.buffer_pct:.1%}, {len(rec.plan.lines)} factories, {rec.plan.total_allocated:,} pieces, "
            f"P(meet) {rec.risk.p_meet_qty:.3f}, cost {rec.plan.cost_total:,.0f} BDT (buffer price "
            f"{rec.plan.cost_buffer:,.0f}).", "",
            "| Factory | District | Qty | Share | P(full) | Mean fill | P(late) | P(on time within S) | Thin history |",
            "|---|---|---|---|---|---|---|---|---|"]
    for ln in rec.plan.lines:
        f = w.forecasts[ln.factory_id]
        out.append(f"| {ln.factory_id} | {by_id[ln.factory_id].district} | {ln.qty:,} | {ln.share:.0%} | {f.p_full:.2f} "
                   f"| {f.fill_mean:.3f} | {f.p_late:.2f} | {rl.p_on_time(f, S):.2f} | {'yes' if f.thin_history else 'no'} |")
    out.append("")

    rp = op.replan_exclude(w, order, p, rec, op.largest_line(rec))
    out += ["## Re-allocation smoke test (Day 2: exclude + re-solve)", "",
            f"{rp.excluded_id} (largest line) declines → {len(rp.new.plan.lines)} factories, "
            f"{rp.new.plan.total_allocated:,} pieces, buffer {rp.new.buffer_pct:.1%}, cost {rp.new.plan.cost_total:,.0f} BDT "
            f"(Δ {rp.new.plan.cost_total - rec.plan.cost_total:+,.0f}), P(meet) "
            f"{rp.new.risk.p_meet_qty if rp.new.risk else float('nan'):.3f} (was {rec.risk.p_meet_qty:.3f}).", ""]
    return "\n".join(out)


if __name__ == "__main__":
    text = run()
    (C.ROOT / "CHECKPOINT_DAY2.md").write_text(text, encoding="utf-8")
    print(text)
