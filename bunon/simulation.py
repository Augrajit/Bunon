"""Monte Carlo bundle risk: P(good pieces delivered on time >= (1 - tolerance) x Q) (PLAN.md §6, SPEC.md D12).

Per draw:
  - full delivery:     w.p. p_full_i ~ Beta(p_full_a, p_full_b), fill_i = 1
  - else short:        theta_i ~ N(fill_mu, fill_sd_param^2); fill_i = sigmoid(theta_i + N(0, fill_sd_noise^2))
  - catastrophe:       with p_catastrophe_i, fill_i ~ U(0, 0.4)
  - lateness:          late with p_late_i, days ~ Gamma(shape, scale_i)
  - shocks (mode "shocks"): one national shock and per-district shocks, shared by
    every factory they hit -> correlated failures
  - on time if delay <= slack_i; otherwise partial lots (default) or 0 ("late = 0" toggle)
  - QC (when the forecasts carry a QC model): a job defect rate ~ Beta, and a whole lot fails QC
    with p_lot_reject (itself drawn from its Beta posterior); only good pieces count.
  - success: total good on-time pieces >= plan.min_good_qty (= ceil((1 - tolerance) x Q)).
The delivered_p5 / delivered_p50 / histogram / shortfall figures are in GOOD pieces.
"""
from __future__ import annotations

import numpy as np

from bunon import config as C
from bunon.schemas import AllocationPlan, BundleRisk, ReliabilityForecast, ShockEvent, ShockParams


def _styles_ok(plan: AllocationPlan, delivered_i: np.ndarray, rate: np.ndarray, lot_rej: np.ndarray) -> np.ndarray:
    """Whole-style mode (SPEC.md D30): a factory makes its styles in sequence, so the pieces it delivers fill its
    first style first; the programme succeeds only if EVERY style has good pieces >= its own threshold
    (an over-cut of one style cannot cover another). A style in no line fails."""
    ok = np.ones(delivered_i.shape[0], bool)
    placed = set()
    for j, ln in enumerate(plan.lines):
        cum = 0.0
        for sid, cut in ln.styles:
            d = np.clip(delivered_i[:, j] - cum, 0.0, cut)
            cum += cut
            good = np.where(lot_rej[:, j], 0.0, d * (1 - rate[:, j]))
            ok &= good >= plan.style_min_good.get(sid, 0) - 1e-9
            placed.add(sid)
    if set(plan.style_min_good) - placed:
        ok[:] = False
    return ok


def simulate(plan: AllocationPlan, forecasts: dict[str, ReliabilityForecast], slack: dict[str, int],
             districts: dict[str, str], shocks: ShockParams | None = None,
             n_sims: int = C.N_SIMS, seed: int = C.SIM_SEED,
             window_days: dict[str, int] | None = None, events: list[ShockEvent] | None = None) -> BundleRisk:
    """window_days given -> "partial lots" (the DEFAULT since SPEC.md D2): a factory running d days
    late on a W-day job has finished (W + slack) / (W + d) of it by the latest ship date, and that
    part counts. window_days=None -> the "late = 0" comparison toggle."""
    rng = np.random.default_rng(seed)
    ids = [ln.factory_id for ln in plan.lines]
    k = len(ids)
    mode = "shocks" if shocks is not None else "independent"
    need = plan.min_good_qty if plan.min_good_qty is not None else plan.target_qty
    if k == 0:
        return BundleRisk(plan_id=plan.plan_id, n_sims=n_sims, seed=seed, p_meet_qty=0.0, p_meet_se=0.0,
                          expected_delivered=0.0, delivered_p5=0, delivered_p50=0, shortfall_p95=need,
                          histogram=[], fail_contribution={}, correlation_mode=mode, min_good_qty=need)

    f = [forecasts[i] for i in ids]
    qty = np.array([ln.qty for ln in plan.lines], float)
    mu = np.array([x.fill_mu for x in f])
    sd_p = np.array([x.fill_sd_param for x in f])
    sd_n = np.array([x.fill_sd_noise for x in f])
    p_cat = np.array([x.p_catastrophe for x in f])
    p_late = np.array([x.p_late for x in f])
    shape = np.array([x.late_days_shape for x in f])
    scale = np.array([x.late_days_scale for x in f])
    sl = np.array([slack[i] for i in ids], float)

    # Two-part fill: full delivery w.p. p_full (drawn from its Beta posterior = model uncertainty),
    # otherwise a short delivery from the logit-normal.
    p_full = rng.beta(np.array([x.p_full_a for x in f]), np.array([x.p_full_b for x in f]), (n_sims, k))
    full = rng.random((n_sims, k)) < p_full
    theta = mu + sd_p * rng.standard_normal((n_sims, k))
    short = 1.0 / (1.0 + np.exp(-(theta + sd_n * rng.standard_normal((n_sims, k)))))
    fill = np.where(full, 1.0, short)
    cat = rng.random((n_sims, k)) < p_cat
    fill = np.where(cat, rng.uniform(*C.CATASTROPHE_FILL, (n_sims, k)), fill)
    late = rng.random((n_sims, k)) < p_late
    delay = np.where(late, np.ceil(rng.gamma(shape, scale, (n_sims, k))), 0.0)

    if shocks is not None:
        nat = rng.random(n_sims) < shocks.p_national
        fill = np.where(nat[:, None], fill * shocks.national_fill_mult, fill)
        delay = delay + np.where(nat[:, None], rng.poisson(max(shocks.national_extra_delay, 0), (n_sims, k)), 0)
        for d in sorted({districts[i] for i in ids}):
            p = shocks.p_district.get(d, 0.0)
            if p <= 0:
                continue
            hit = rng.random(n_sims) < p
            cols = np.array([districts[i] == d for i in ids])
            mask = hit[:, None] & cols[None, :]
            fill = np.where(mask, fill * shocks.district_fill_mult, fill)
            delay = delay + np.where(mask, rng.poisson(max(shocks.district_extra_delay, 0), (n_sims, k)), 0)

    for ev in events or []:  # known disruptions (from news): applied to every draw, deterministically
        hit = np.array([ev.area == "national" or districts[i] == ev.area for i in ids])
        fill = np.where(hit[None, :], fill * ev.fill_mult, fill)
        delay = delay + np.where(hit[None, :], ev.extra_delay_days, 0)

    on_time = delay <= sl
    if window_days is None:
        frac = np.where(on_time, 1.0, 0.0)
    else:
        W = np.array([window_days[i] for i in ids], float)
        frac = np.where(on_time, 1.0, (W + sl) / (W + np.maximum(delay, 1)))
    delivered_i = qty * np.minimum(fill, 1.0) * frac
    lot_rej = np.zeros((n_sims, k), bool)
    rate = np.zeros((n_sims, k))
    good_i = delivered_i
    has_qc = [x.qc_defect_a is not None for x in f]
    if any(has_qc) and not all(has_qc):
        raise ValueError("either every factory in the plan has a QC model or none does")
    if all(has_qc):  # QC model present (SPEC.md D12); drawn last
        rate = rng.beta(np.array([x.qc_defect_a for x in f]), np.array([x.qc_defect_b for x in f]), (n_sims, k))
        p_lot = rng.beta(np.array([x.p_lot_reject_a for x in f]), np.array([x.p_lot_reject_b for x in f]), (n_sims, k))
        lot_rej = rng.random((n_sims, k)) < p_lot
        good_i = np.where(lot_rej, 0.0, delivered_i * (1 - rate))
    total = good_i.sum(axis=1)
    if plan.style_min_good:
        ok = _styles_ok(plan, delivered_i, rate, lot_rej)
    else:
        ok = total >= need
    p = float(ok.mean())

    failed = ~ok
    short = (~on_time) | cat | lot_rej
    contrib = {i: (float(short[failed, j].mean()) if failed.any() else 0.0) for j, i in enumerate(ids)}
    counts, edges = np.histogram(total, bins=30)
    return BundleRisk(
        plan_id=plan.plan_id, n_sims=n_sims, seed=seed, p_meet_qty=p,
        p_meet_se=float(np.sqrt(p * (1 - p) / n_sims)),
        expected_delivered=float(delivered_i.sum(axis=1).mean()),
        expected_good=float(total.mean()), min_good_qty=need,
        delivered_p5=int(np.percentile(total, 5)), delivered_p50=int(np.percentile(total, 50)),
        shortfall_p95=int(max(0.0, need - np.percentile(total, 5))),
        histogram=[(int(e), int(c)) for e, c in zip(edges[:-1], counts)],
        fail_contribution=contrib, correlation_mode=mode,
    )
