"""Reliability forecast per factory: empirical Bayes, no ML (PLAN.md §4, v2 change 4).

Model, per factory i (untagged accepted jobs):
  full_ij        ~ Bernoulli(p_full_i),     p_full_i ~ Beta prior (pooled)
  if short: logit(fill_ij) ~ N(theta_i, sigma^2)
  theta_i        ~ N(mu, tau^2)             population prior, method of moments
  late_ij        ~ Bernoulli(p_late_i),     p_late_i ~ Beta prior (pooled)
  late days      ~ Gamma(shape, scale_i),   scale shrunk to the pooled scale
  catastrophe    ~ Bernoulli(p_cat_i),      Beta prior (pooled)
  accept offer   ~ Bernoulli(accept_i),     Beta prior (pooled)

A factory with no history gets the prior, which is wide. Shock-tagged jobs are
excluded from the base fit; their frequency feeds ShockParams for the simulation.
"""
from __future__ import annotations

import math
from datetime import date
from statistics import NormalDist

import numpy as np
import pandas as pd

from bunon import config as C
from bunon.data_gen import flag_bad_history_rows
from bunon.schemas import BucketEval, ReliabilityEval, ReliabilityForecast, ShockParams

MODEL_VERSION = "eb-2"  # eb-2: two-part fill (full / short), SPEC.md D2
_Z90 = 1.2815515655446004
_EPS = 0.01


def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def _logit(p):
    p = np.clip(p, _EPS, 1 - _EPS)
    return np.log(p / (1 - p))


def logitnormal_mean(mu, sd):
    """Approximate E[sigmoid(N(mu, sd^2))] (probit approximation)."""
    return _sigmoid(mu / np.sqrt(1.0 + math.pi * sd ** 2 / 8.0))


def gamma_cdf(x: float, shape: float, scale: float) -> float:
    """Regularised lower incomplete gamma P(shape, x/scale), by series. No scipy needed."""
    if x <= 0:
        return 0.0
    z = x / scale
    term = 1.0 / shape
    total = term
    for n in range(1, 500):
        term *= z / (shape + n)
        total += term
        if term < total * 1e-12:
            break
    return min(1.0, total * math.exp(-z + shape * math.log(z) - math.lgamma(shape)))


def p_on_time(f: ReliabilityForecast, slack_days: int) -> float:
    """P(delay <= slack): on time, or late by no more than the slack."""
    return (1 - f.p_late) + f.p_late * gamma_cdf(slack_days, f.late_days_shape, f.late_days_scale)


def _beta_prior(k: pd.Series, n: pd.Series, min_n: int = 3, strength_cap: float = 50.0) -> tuple[float, float]:
    """Method-of-moments Beta prior from per-factory rates."""
    mask = n >= min_n
    rates = (k[mask] / n[mask]).to_numpy()
    m = float(k.sum() / max(n.sum(), 1))
    if len(rates) < 5:
        return m * 10, (1 - m) * 10
    v_obs = rates.var()
    v_binom = float(np.mean(m * (1 - m) / n[mask]))
    v_between = max(v_obs - v_binom, 1e-4)
    strength = min(max(m * (1 - m) / v_between - 1, 2.0), strength_cap)
    return m * strength, (1 - m) * strength


def clean_jobs(history: pd.DataFrame) -> pd.DataFrame:
    h = history.copy()
    h["accepted"] = h["accepted"].astype(bool)
    h = h[h["accepted"] & ~flag_bad_history_rows(h)].copy()
    h["fill"] = h["delivered_qty"].astype(float) / h["committed_qty"].astype(float)
    h["delay_days"] = h["delay_days"].astype(float)
    h["tag"] = h["shock_tag"].fillna("")
    h["promised_date"] = pd.to_datetime(h["promised_date"])
    return h


def fit(history: pd.DataFrame, factory_ids: list[str], until: date | None = None) -> dict[str, ReliabilityForecast]:
    """Fit on jobs promised before `until` (all jobs if None) and forecast every id in factory_ids."""
    offers = history.copy()
    offers["offered_date"] = pd.to_datetime(offers["offered_date"])
    jobs = clean_jobs(history)
    if until is not None:
        jobs = jobs[jobs["promised_date"] < pd.Timestamp(until)]
        offers = offers[offers["offered_date"] < pd.Timestamp(until)]

    base = jobs[jobs["tag"] == ""].copy()
    base["full"] = base["fill"] >= C.FULL_FILL_THRESHOLD

    # Part 1: P(delivered in full), Beta-Binomial.
    gf = base.groupby("factory_id")["full"]
    full_k, full_n = gf.sum(), gf.size()
    a_full, b_full = _beta_prior(full_k, full_n)

    # Part 2: fill of SHORT deliveries, normal-normal on the logit scale.
    short = base[~base["full"]].copy()
    short["y"] = _logit(short["fill"].to_numpy())
    g = short.groupby("factory_id")["y"]
    n_i, ybar, var_i = g.size(), g.mean(), g.var(ddof=1)

    sigma2 = float(np.average(var_i[n_i >= 2], weights=n_i[n_i >= 2] - 1))
    ok = n_i >= 3
    mu = float(ybar[ok].mean())
    tau2 = max(float(ybar[ok].var() - np.mean(sigma2 / n_i[ok])), 0.05)

    # Lateness, catastrophes and acceptance: Beta-Binomial.
    gb = base.groupby("factory_id")
    late_k, late_n = gb["delay_days"].apply(lambda s: (s > 0).sum()), gb.size()
    a_late, b_late = _beta_prior(late_k, late_n)

    late_days = base.loc[base["delay_days"] > 0, ["factory_id", "delay_days"]]
    pooled_mean = float(late_days["delay_days"].mean())
    pooled_var = float(late_days["delay_days"].var())
    shape = max(pooled_mean ** 2 / pooled_var, 0.5)
    ld = late_days.groupby("factory_id")["delay_days"].agg(["sum", "size"])

    cat = jobs.assign(c=jobs["tag"].str.contains("catastrophe")).groupby("factory_id")["c"].agg(["sum", "size"])
    a_cat, b_cat = _beta_prior(cat["sum"], cat["size"])

    acc = offers.assign(a=offers["accepted"].astype(bool)).groupby("factory_id")["a"].agg(["sum", "size"])
    a_acc, b_acc = _beta_prior(acc["sum"], acc["size"])

    n_all = jobs.groupby("factory_id").size()
    out: dict[str, ReliabilityForecast] = {}
    for fid in factory_ids:
        n = int(n_i.get(fid, 0))
        post_var = 1.0 / (1.0 / tau2 + n / sigma2)
        post_mean = post_var * (mu / tau2 + (n * ybar.get(fid, 0.0)) / sigma2) if n else mu
        sd_param = math.sqrt(post_var)
        sd_pred = math.sqrt(post_var + sigma2)

        k_l, n_l = int(late_k.get(fid, 0)), int(late_n.get(fid, 0))
        p_late = (a_late + k_l) / (a_late + b_late + n_l)
        s_sum, s_n = (float(ld.loc[fid, "sum"]), int(ld.loc[fid, "size"])) if fid in ld.index else (0.0, 0)
        k0 = 3.0  # pseudo late jobs at the pooled mean
        mean_late = (k0 * pooled_mean + s_sum) / (k0 + s_n)
        c_k, c_n = (int(cat.loc[fid, "sum"]), int(cat.loc[fid, "size"])) if fid in cat.index else (0, 0)
        p_cat = (a_cat + c_k) / (a_cat + b_cat + c_n)
        o_k, o_n = (int(acc.loc[fid, "sum"]), int(acc.loc[fid, "size"])) if fid in acc.index else (0, 0)

        pa = a_full + int(full_k.get(fid, 0))
        pb = b_full + int(full_n.get(fid, 0)) - int(full_k.get(fid, 0))
        p_full = pa / (pa + pb)
        short_mean = float(logitnormal_mean(post_mean, sd_pred))

        def q(level: float) -> float:
            """Quantile of the two-part mixture (catastrophes excluded)."""
            if level >= 1 - p_full:
                return 1.0
            return float(_sigmoid(post_mean + sd_pred * NormalDist().inv_cdf(level / (1 - p_full))))

        out[fid] = ReliabilityForecast(
            factory_id=fid,
            n_jobs=int(n_all.get(fid, 0)),
            thin_history=int(n_all.get(fid, 0)) < C.THIN_HISTORY_JOBS,
            p_full=p_full, p_full_a=pa, p_full_b=pb,
            fill_mu=post_mean,
            fill_sd_param=sd_param,
            fill_sd_noise=math.sqrt(sigma2),
            fill_mean=(1 - p_cat) * (p_full + (1 - p_full) * short_mean) + p_cat * 0.2,
            fill_p10=q(0.10),
            fill_p50=q(0.50),
            fill_p90=q(0.90),
            p_late=p_late,
            late_days_shape=shape,
            late_days_scale=mean_late / shape,
            p_catastrophe=p_cat,
            accept_prob=(a_acc + o_k) / (a_acc + b_acc + o_n),
            model_version=MODEL_VERSION,
        )
    return out


def estimate_shocks(history: pd.DataFrame, factories: pd.DataFrame) -> ShockParams:
    """Shock frequencies and sizes, estimated from the tagged history (not from the generator's settings)."""
    jobs = clean_jobs(history).merge(factories[["factory_id", "district"]], on="factory_id")
    jobs["month"] = jobs["promised_date"].dt.to_period("M")
    months = jobs["month"].nunique()
    base = jobs[jobs["tag"] == ""]

    nat = jobs[jobs["tag"].str.contains("national")]
    dist = jobs[jobs["tag"].str.contains("district") & ~jobs["tag"].str.contains("national")]
    base_fill, base_late = base["fill"].mean(), base["delay_days"].clip(lower=0).mean()

    # About one shock per district in three years: too sparse per district, so pool the rate.
    hits = active = 0
    known = sorted(d for d in jobs["district"].unique() if d != "unknown")
    for d in known:
        grp = jobs[jobs["district"] == d]
        hits += grp.loc[grp["tag"].str.contains("district"), "month"].nunique()
        active += grp["month"].nunique()
    pooled = hits / active if active else 0.0
    p_d = {d: pooled for d in known}
    return ShockParams(
        p_national=nat["month"].nunique() / months,
        national_fill_mult=float(nat["fill"].mean() / base_fill) if len(nat) else 1.0,
        national_extra_delay=float(nat["delay_days"].mean() - base_late) if len(nat) else 0.0,
        p_district=p_d,
        district_fill_mult=float(dist["fill"].mean() / base_fill) if len(dist) else 1.0,
        district_extra_delay=float(dist["delay_days"].mean() - base_late) if len(dist) else 0.0,
    )


def evaluate(history: pd.DataFrame, factory_ids: list[str], holdout_start: date = date(2026, 5, 1)) -> ReliabilityEval:
    """Time split: fit before holdout_start, score untagged jobs promised after it."""
    fc = fit(history, factory_ids, until=holdout_start)
    jobs = clean_jobs(history)
    train = jobs[jobs["promised_date"] < pd.Timestamp(holdout_start)]
    test = jobs[(jobs["promised_date"] >= pd.Timestamp(holdout_start)) & (jobs["tag"] == "")]
    test = test[test["factory_id"].isin(fc)]
    n_train = train.groupby("factory_id").size()

    def score(t: pd.DataFrame) -> tuple[float, float, float]:
        # RMSE, not MAE: with a point mass at "full", MAE rewards always predicting 1.0.
        mean = t["factory_id"].map(lambda f: fc[f].fill_mean)
        lo = t["factory_id"].map(lambda f: fc[f].fill_p10)
        hi = t["factory_id"].map(lambda f: fc[f].fill_p90)
        return (float(np.sqrt(((t["fill"] - mean) ** 2).mean())), float(np.sqrt(((t["fill"] - 1) ** 2).mean())),
                float(((t["fill"] >= lo - 1e-9) & (t["fill"] <= hi + 1e-9)).mean()))

    buckets = []
    hist_n = test["factory_id"].map(lambda f: int(n_train.get(f, 0)))
    for label, lo_n, hi_n in (("0-2", 0, 2), ("3-10", 3, 10), ("11+", 11, 10 ** 6)):
        t = test[(hist_n >= lo_n) & (hist_n <= hi_n)]
        if len(t):
            rmse, rmse_n, cov = score(t)
            buckets.append(BucketEval(bucket=label, n_jobs=len(t), rmse_model=rmse, rmse_naive=rmse_n,
                                      coverage_p10_p90=cov))
    rmse, rmse_n, cov = score(test)
    p10 = test["factory_id"].map(lambda f: fc[f].fill_p10)
    below = float((test["fill"] < p10 - 1e-9).mean())
    full_pred = float(test["factory_id"].map(lambda f: fc[f].p_full).mean())
    full_obs = float((test["fill"] >= C.FULL_FILL_THRESHOLD).mean())
    late = (test["delay_days"] > 0).astype(float)
    p = test["factory_id"].map(lambda f: fc[f].p_late)
    return ReliabilityEval(
        holdout_start=holdout_start, n_train_jobs=len(train), n_holdout_jobs=len(test),
        rmse_model=rmse, rmse_naive=rmse_n, coverage_p10_p90=cov,
        below_p10_rate=below, full_rate_pred=full_pred, full_rate_obs=full_obs,
        brier_late_model=float(((p - late) ** 2).mean()), brier_late_naive=float((late ** 2).mean()),
        by_bucket=buckets,
    )
