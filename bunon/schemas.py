"""Typed contracts between modules (PLAN.md §2).

Day 1 holds the data and planning contracts. LLM contracts (FactSheet,
DecisionMemo, ParamChange) are added on Day 3.
"""
from __future__ import annotations

from datetime import date
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


# --- data_gen ---------------------------------------------------------------

class Factory(_Strict):
    """One row of the public `factories` table. No name, no os_id, no address."""
    factory_id: str = Field(pattern=r"^F\d{4}$")
    district: str
    lat: float
    lng: float
    coord_shared: bool
    workers: int = Field(ge=0)
    workers_imputed: bool
    tee_capable: bool
    has_knitting: bool
    has_printing: bool
    has_dyeing: bool
    has_textile_prod: bool
    rsc: bool
    bsci_valid: bool
    bsci_expiry: date | None
    compliant: bool

    @model_validator(mode="after")
    def _rule(self) -> "Factory":
        if self.compliant != (self.rsc or self.bsci_valid):
            raise ValueError("compliant must equal rsc or bsci_valid")
        return self


class FactorySynth(_Strict):
    """Synthetic commercial attributes (table `factory_synth`)."""
    factory_id: str
    declared_daily_capacity: int = Field(ge=0)
    declared_spare_daily: int = Field(ge=0)
    unit_price_bdt: float = Field(gt=0)
    min_batch: int = Field(gt=0)
    setup_days: int = Field(ge=0)


class HistoryJob(_Strict):
    """One synthetic past job offer (table `history`). Raw: may contain data errors."""
    job_id: str
    factory_id: str
    offered_date: date
    accepted: bool
    committed_qty: int | None
    declared_capacity_at_time: int | None
    promised_date: date | None
    delivered_qty: int | None
    delay_days: int | None
    unit_price_bdt: float | None
    shock_tag: str | None


class DataGenReport(_Strict):
    raw_rows: int
    closed_dropped: int
    open_rows: int
    tee_capable: int
    rsc: int
    bsci_valid: int
    compliant_tee: int
    workers_imputed: int
    coord_shared: int
    district_counts: dict[str, int]
    history_jobs: int
    history_accepted: int
    thin_history_factories: int
    seed: int
    as_of: date


# --- order ------------------------------------------------------------------

class OrderConstraints(_Strict):
    product: Literal["basic_tshirt"] = "basic_tshirt"
    total_qty: int = Field(gt=0)
    own_capacity_qty: int = Field(ge=0)
    deadline: date
    delivery_point: str | None = None
    fabric: str | None = None
    gsm: int | None = None
    quality_aql: float | None = None
    compliance_required: list[str] = ["RSC", "BSCI"]   # rule: RSC or valid BSCI
    fob_usd_per_piece: float | None = None
    late_penalty_pct_per_week: float | None = None
    penalty_text: str | None = None
    source_spans: dict[str, str] = {}
    missing_fields: list[str] = []

    @property
    def subcontract_qty(self) -> int:
        """Computed in code, never by the LLM."""
        return max(0, self.total_qty - self.own_capacity_qty)


# --- reliability ------------------------------------------------------------

class ReliabilityForecast(_Strict):
    factory_id: str
    n_jobs: int
    thin_history: bool
    fill_mu: float            # posterior mean of logit fill
    fill_sd_param: float      # posterior sd of logit fill (model uncertainty)
    fill_sd_noise: float      # per-job sd on logit scale
    fill_mean: float
    fill_p10: float
    fill_p50: float
    fill_p90: float
    p_late: float
    late_days_shape: float
    late_days_scale: float
    p_catastrophe: float
    accept_prob: float
    model_version: str


class BucketEval(_Strict):
    bucket: str
    n_jobs: int
    mae_model: float
    mae_naive: float
    coverage_p10_p90: float


class ReliabilityEval(_Strict):
    holdout_start: date
    n_train_jobs: int
    n_holdout_jobs: int
    mae_model: float
    mae_naive: float           # naive = "declared is delivered" (fill = 1)
    coverage_p10_p90: float
    brier_late_model: float
    brier_late_naive: float    # naive = never late
    by_bucket: list[BucketEval]


class ShockParams(_Strict):
    """Correlated-shock model estimated from history (PLAN.md §6)."""
    p_national: float
    national_fill_mult: float
    national_extra_delay: float
    p_district: dict[str, float]
    district_fill_mult: float
    district_extra_delay: float


# --- optimizer --------------------------------------------------------------

class Candidate(_Strict):
    factory_id: str
    district: str
    unit_price_bdt: float
    transport_bdt_per_piece: float
    transit_days: int
    slack_days: int
    window_days: int
    window_capacity_declared: int
    window_capacity_risk_adj: int
    min_batch: int
    proven: bool
    q_effective: float


class OptimizerParams(_Strict):
    buffer_pct: float = Field(0.0, ge=0.0, le=0.5)
    planned_slack_days: int = Field(4, ge=0, le=15)
    max_factories: int = Field(5, ge=1, le=10)
    min_factories: int = Field(1, ge=1, le=10)
    unproven_cap_pct: float = Field(0.15, ge=0.0, le=0.5)
    lot_size: int = Field(100, gt=0)
    missing_piece_cost_bdt: float = Field(0.0, ge=0.0)
    fixed_cost_per_factory_bdt: float = Field(0.0, ge=0.0)
    excluded_ids: list[str] = []
    locked_min_qty: dict[str, int] = {}


class AllocationLine(_Strict):
    factory_id: str
    qty: int
    unit_price_bdt: float
    transport_bdt: float
    risk_penalty_bdt: float
    share: float


class AllocationPlan(_Strict):
    plan_id: str
    method: Literal["optimised", "baseline", "baseline_buffered"]
    status: Literal["optimal", "infeasible", "not_solved"]
    lines: list[AllocationLine]
    target_qty: int
    total_allocated: int
    buffer_qty: int
    cost_production: float
    cost_transport: float
    cost_fixed: float
    cost_risk_penalty: float   # model shadow cost used in the objective; not cash
    cost_total: float          # cash cost = production + transport + fixed
    binding_constraints: list[str] = []
    max_feasible_qty: int | None = None
    solver_seconds: float = 0.0


# --- simulation -------------------------------------------------------------

class BundleRisk(_Strict):
    plan_id: str
    n_sims: int
    seed: int
    p_meet_qty: float
    p_meet_se: float
    expected_delivered: float
    delivered_p5: int
    delivered_p50: int
    shortfall_p95: int
    histogram: list[tuple[int, int]]
    fail_contribution: dict[str, float]
    correlation_mode: Literal["independent", "shocks"]


class BufferPoint(_Strict):
    buffer_pct: float
    plan: AllocationPlan
    risk: BundleRisk | None


class PlanResult(_Strict):
    """Output of the buffer search: the recommended plan plus the whole frontier."""
    recommended: BufferPoint
    frontier: list[BufferPoint]
    target_p: float
    target_met: bool


class ReplanResult(_Strict):
    excluded_id: str
    reason: str
    old: BufferPoint
    new: BufferPoint
    diff: list[dict]           # factory_id, old_qty, new_qty
