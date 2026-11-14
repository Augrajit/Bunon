"""Day 2 checkpoint (PLAN.md v2 change 2): is P(meet) >= 0.90 reachable for the demo order?

Run:  python -m bunon.checkpoint   -> writes CHECKPOINT_DAY2.md
"""
from __future__ import annotations

from bunon import config as C
from bunon import optimizer as op
from bunon import reliability as rl

SLACKS = (0, 2, 4, 6, 8)


def _fmt(p):
    return "—" if p is None else f"{p:.3f}"


def run() -> str:
    w = op.load_world()
    order = op.demo_order()
    Q = order.subcontract_qty
    out = [
        "# Day 2 checkpoint: is P(meet) ≥ 0.90 reachable?",
        "",
        f"Demo order: {order.total_qty:,} tees, own capacity {order.own_capacity_qty:,}, **subcontract Q = {Q:,}**, "
        f"deadline {order.deadline}, as-of {C.AS_OF}. Max {C.MAX_FACTORIES} factories, unproven cap "
        f"{C.UNPROVEN_CAP_PCT:.0%}, {C.N_SIMS:,} simulations per plan, seed {C.SIM_SEED}. **All performance data is synthetic.**",
        "",
        "P(meet) = probability that on-time delivered pieces ≥ Q. Buffer b* = cheapest buffer on the grid "
        f"{list(C.BUFFER_GRID)} reaching 0.90, else the buffer with the highest P.",
        "",
    ]
    for partial in (False, True):
        title = ("A. As planned: late beyond slack counts as 0" if not partial
                 else "B. Variant (not in plan): partial lots: work finished by the latest ship date counts")
        out += [f"## {title}", "",
                "| Slack S (days) | Candidates | Ours: best P (shocks) | b* | Ours cost (BDT) | Ours P (independent) "
                "| Baseline P | Baseline + b* P | Baseline cost (BDT) |",
                "|---|---|---|---|---|---|---|---|---|"]
        for S in SLACKS:
            p = op.default_params(order, planned_slack_days=S)
            cands = op.build_candidates(w, order, p)
            r = op.compare_three(w, order, p, shocks=True, partial_lots=partial)
            rec = r["ours"].recommended
            ind = op.evaluate(rec.plan, cands, w, shocks=False, partial_lots=partial) if rec.risk else None
            bl, bb = r["baseline"], r["baseline_buffered"]
            out.append(
                f"| {S} | {len(cands)} | **{_fmt(rec.risk.p_meet_qty if rec.risk else None)}** | {rec.buffer_pct:.1%} "
                f"| {rec.plan.cost_total:,.0f} | {_fmt(ind.p_meet_qty if ind else None)} "
                f"| {_fmt(bl.risk.p_meet_qty if bl.risk else None)} | {_fmt(bb.risk.p_meet_qty if bb.risk else None)} "
                f"| {bl.plan.cost_total:,.0f} |")
        out.append("")

    # Extended buffer grid beyond the plan's 20% ceiling
    ext = (0.2, 0.3, 0.4, 0.5, 0.6)
    out += ["## C. Beyond the plan's 20% buffer ceiling", "",
            "Ours / baseline + same buffer, with shocks on. \"infeasible\" = more than 5 factories' risk-adjusted capacity.", "",
            "| Lot model | S | " + " | ".join(f"b = {b:.0%}" for b in ext) + " |",
            "|---|---|" + "---|" * len(ext)]
    for partial in (False, True):
        for S in (2, 4, 6):
            p = op.default_params(order, planned_slack_days=S)
            cands = op.build_candidates(w, order, p)
            res = op.search_buffer(w, order, p, grid=ext, partial_lots=partial)
            cells = []
            for pt in res.frontier:
                if not pt.risk:
                    cells.append("infeasible")
                    continue
                bb = op.solve_baseline(cands, Q, p, pt.buffer_pct)
                bp = op.evaluate(bb, cands, w, True, partial_lots=partial).p_meet_qty if bb.status == "optimal" else None
                cells.append(f"{pt.risk.p_meet_qty:.3f} / {_fmt(bp)} ({pt.plan.cost_total / 1e6:.2f}M BDT)")
            out.append(f"| {'partial lots' if partial else 'as planned'} | {S} | " + " | ".join(cells) + " |")
    out.append("")

    # Diagnostics for the as-planned case at the default slack
    S = C.PLANNED_SLACK_DAYS
    p = op.default_params(order, planned_slack_days=S)
    cands = {c.factory_id: c for c in op.build_candidates(w, order, p)}
    res = op.search_buffer(w, order, p)
    rec = res.recommended
    out += [f"## Why: the recommended plan at S = {S} (as planned)", "",
            f"Buffer {rec.buffer_pct:.1%}, {len(rec.plan.lines)} factories, {rec.plan.total_allocated:,} pieces, "
            f"P(meet) {rec.risk.p_meet_qty:.3f}.", "",
            "| Factory | Qty | Share | Mean fill | P(late) | P(on time within S) | Thin history | Share of failures where it was short |",
            "|---|---|---|---|---|---|---|---|"]
    for ln in rec.plan.lines:
        f = w.forecasts[ln.factory_id]
        out.append(f"| {ln.factory_id} | {ln.qty:,} | {ln.share:.0%} | {f.fill_mean:.2f} | {f.p_late:.2f} "
                   f"| {rl.p_on_time(f, S):.2f} | {'yes' if f.thin_history else 'no'} "
                   f"| {rec.risk.fail_contribution.get(ln.factory_id, 0):.0%} |")
    pop = [rl.p_on_time(f, S) for f in w.forecasts.values()]
    out += ["", f"Across all {len(pop):,} T-shirt factories, the median P(on time within {S} days) is "
            f"{sorted(pop)[len(pop) // 2]:.2f}. Late jobs in history run {w.forecasts[next(iter(w.forecasts))].late_days_shape:.2f}"
            f"-shape Gamma, about 7 days late on average.", ""]

    # Re-allocation smoke test (Day 2 version: exclude + re-solve)
    rp = op.replan_exclude(w, order, p, rec, op.largest_line(rec))
    out += ["## Re-allocation smoke test (Day 2: exclude + re-solve)", "",
            f"{rp.excluded_id} (largest line) declines → new plan: {len(rp.new.plan.lines)} factories, "
            f"{rp.new.plan.total_allocated:,} pieces, cost {rp.new.plan.cost_total:,.0f} BDT "
            f"(Δ {rp.new.plan.cost_total - rec.plan.cost_total:+,.0f}), "
            f"P(meet) {rp.new.risk.p_meet_qty if rp.new.risk else float('nan'):.3f} "
            f"(was {rec.risk.p_meet_qty:.3f}).", ""]
    return "\n".join(out)


if __name__ == "__main__":
    text = run()
    (C.ROOT / "CHECKPOINT_DAY2.md").write_text(text, encoding="utf-8")
    print(text)
