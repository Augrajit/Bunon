"""Monte Carlo bundle risk: P(total on-time delivery >= Q) (PLAN.md §6).

Per draw:
  - model uncertainty: theta_i ~ N(fill_mu, fill_sd_param^2)
  - job noise:         fill_i = sigmoid(theta_i + N(0, fill_sd_noise^2))
  - catastrophe:       with p_catastrophe_i, fill_i ~ U(0, 0.4)
  - lateness:          late with p_late_i, days ~ Gamma(shape, scale_i)
  - shocks (mode "shocks"): one national shock and per-district shocks, shared by
    every factory they hit -> correlated failures
  - on time if delay <= slack_i; late goods count as 0 (conservative)
"""
from __future__ import annotations

import numpy as np

from bunon import config as C
from bunon.schemas import AllocationPlan, BundleRisk, ReliabilityForecast, ShockParams


def simulate(plan: AllocationPlan, forecasts: dict[str, ReliabilityForecast], slack: dict[str, int],
             districts: dict[str, str], shocks: ShockParams | None = None,
             n_sims: int = C.N_SIMS, seed: int = C.SIM_SEED,
             window_days: dict[str, int] | None = None) -> BundleRisk:
    """window_days given -> "partial lots" variant (NOT in the approved plan; checkpoint option only):
    a factory running d days late on a W-day job has finished (W + slack) / (W + d) of it by the
    latest ship date, and that part counts. Without it, any lateness beyond slack counts as 0."""
    rng = np.random.default_rng(seed)
    ids = [ln.factory_id for ln in plan.lines]
    k = len(ids)
    mode = "shocks" if shocks is not None else "independent"
    if k == 0:
        return BundleRisk(plan_id=plan.plan_id, n_sims=n_sims, seed=seed, p_meet_qty=0.0, p_meet_se=0.0,
                          expected_delivered=0.0, delivered_p5=0, delivered_p50=0, shortfall_p95=plan.target_qty,
                          histogram=[], fail_contribution={}, correlation_mode=mode)

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

    theta = mu + sd_p * rng.standard_normal((n_sims, k))
    fill = 1.0 / (1.0 + np.exp(-(theta + sd_n * rng.standard_normal((n_sims, k)))))
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

    on_time = delay <= sl
    if window_days is None:
        frac = np.where(on_time, 1.0, 0.0)
    else:
        W = np.array([window_days[i] for i in ids], float)
        frac = np.where(on_time, 1.0, (W + sl) / (W + np.maximum(delay, 1)))
    delivered_i = qty * np.minimum(fill, 1.0) * frac
    total = delivered_i.sum(axis=1)
    ok = total >= plan.target_qty
    p = float(ok.mean())

    failed = ~ok
    short = (~on_time) | cat
    contrib = {i: (float(short[failed, j].mean()) if failed.any() else 0.0) for j, i in enumerate(ids)}
    counts, edges = np.histogram(total, bins=30)
    return BundleRisk(
        plan_id=plan.plan_id, n_sims=n_sims, seed=seed, p_meet_qty=p,
        p_meet_se=float(np.sqrt(p * (1 - p) / n_sims)),
        expected_delivered=float(total.mean()),
        delivered_p5=int(np.percentile(total, 5)), delivered_p50=int(np.percentile(total, 50)),
        shortfall_p95=int(max(0.0, plan.target_qty - np.percentile(total, 5))),
        histogram=[(int(e), int(c)) for e, c in zip(edges[:-1], counts)],
        fail_contribution=contrib, correlation_mode=mode,
    )
