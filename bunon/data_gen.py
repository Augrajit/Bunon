"""Clean the Open Supply Hub export and generate synthetic performance history.

Outputs (PLAN.md §2.1):
  data/processed/bunon.db   public: pseudonymous IDs, district, rounded coords. No names, no os_id.
  data/private/mapping.csv  local only (gitignored): factory_id -> os_id, name.

The synthetic generator reads only the public table, so anyone can rebuild the
synthetic data from the public DB and the seed.

Run:  python -m bunon.data_gen
"""
from __future__ import annotations

import json
import re
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from bunon import config as C
from bunon.schemas import DataGenReport

# Columns read from the raw export. Everything else, including all personal
# contact fields, is never loaded.
_RAW_COLS = [
    "os_id", "name", "address", "lat", "lng", "is_closed", "number_of_workers",
    "product_type", "processing_type", "processing_type_facility_type_raw",
    "claim_product_types", "facility_type",
    "accord_inspections_and_remediation_program.rsc_presence",
    "amfori_compliance_status.bsci_audit.expiration_date",
]
_PRODUCT_FIELDS = ["product_type", "processing_type", "processing_type_facility_type_raw", "claim_product_types"]
_TEE = re.compile(C.TEE_REGEX, re.IGNORECASE)
_RANGE = re.compile(r"^(\d+)\s*-\s*(\d+)$")


# --- parsing helpers ---------------------------------------------------------

def parse_workers(value: str) -> float | None:
    """Median of pipe-joined worker counts; ranges count as their midpoint; 0 means missing."""
    nums: list[float] = []
    for part in str(value).split("|"):
        part = part.strip().replace(",", "")
        if not part:
            continue
        m = _RANGE.match(part)
        if m:
            nums.append((int(m.group(1)) + int(m.group(2))) / 2)
        elif part.isdigit():
            nums.append(float(part))
    med = float(np.median(nums)) if nums else None
    return med if med else None  # None for empty and for 0


def assign_district(address: str, lat: float) -> str:
    a = str(address).lower()
    for label, keys in C.DISTRICT_KEYWORDS:
        if any(k in a for k in keys):
            if label == "chattogram" and lat >= C.CHATTOGRAM_MAX_LAT:
                continue  # "Dhaka-Chittagong Highway" etc.
            return label
    return "unknown"


def latest_date(value: str) -> date | None:
    ds = []
    for part in str(value).split("|"):
        part = part.strip()[:10]
        try:
            ds.append(date.fromisoformat(part))
        except ValueError:
            continue
    return max(ds) if ds else None


def _has(series: pd.Series, word: str) -> pd.Series:
    return series.str.contains(word, case=False, regex=False)


# --- step 1: clean the real export ------------------------------------------

def clean_osh(raw: pd.DataFrame, as_of: date = C.DATA_AS_OF) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Return (public factories table, private mapping, counts)."""
    df = raw[_RAW_COLS].copy()
    counts = {"raw_rows": len(df)}

    df["lat_f"] = df["lat"].astype(float)
    df["lng_f"] = df["lng"].astype(float)
    # Shared exact coordinates across the whole export: likely area centroids.
    df["coord_shared"] = df.duplicated(["lat", "lng"], keep=False)

    closed = df["is_closed"].str.strip().str.lower() == "true"
    counts["closed_dropped"] = int(closed.sum())
    df = df[~closed].copy()
    counts["open_rows"] = len(df)

    # Pseudonymous IDs: seeded shuffle, independent of SYNTH_SEED and of os_id order.
    df = df.sort_values("os_id").reset_index(drop=True)
    perm = np.random.default_rng(C.ID_SEED).permutation(len(df))
    df["factory_id"] = [f"F{i + 1:04d}" for i in perm]

    df["district"] = [assign_district(a, la) for a, la in zip(df["address"], df["lat_f"])]

    w = df["number_of_workers"].map(parse_workers)
    df["workers_imputed"] = w.isna()
    district_median = w.groupby(df["district"]).transform("median")
    w = w.fillna(district_median).fillna(w.median())
    step = C.WORKERS_ROUND_TO
    df["workers"] = (np.maximum(step, np.round(w / step) * step)).astype(int)

    prod_text = df[_PRODUCT_FIELDS].agg(" | ".join, axis=1)
    df["tee_capable"] = prod_text.map(lambda s: bool(_TEE.search(s)))
    proc = df["processing_type"] + " | " + df["processing_type_facility_type_raw"]
    df["has_knitting"] = _has(proc, "knit")
    df["has_printing"] = _has(proc, "print")
    df["has_dyeing"] = _has(proc, "dye")
    df["has_textile_prod"] = _has(df["facility_type"], "textile or material production")

    df["rsc"] = df["accord_inspections_and_remediation_program.rsc_presence"].str.contains("Yes", regex=False)
    df["bsci_expiry"] = df["amfori_compliance_status.bsci_audit.expiration_date"].map(latest_date)
    df["bsci_valid"] = df["bsci_expiry"].map(lambda d: d is not None and d >= as_of)
    df["compliant"] = df["rsc"] | df["bsci_valid"]

    df["lat"] = df["lat_f"].round(C.COORD_DECIMALS)
    df["lng"] = df["lng_f"].round(C.COORD_DECIMALS)

    public_cols = [
        "factory_id", "district", "lat", "lng", "coord_shared", "workers", "workers_imputed",
        "tee_capable", "has_knitting", "has_printing", "has_dyeing", "has_textile_prod",
        "rsc", "bsci_valid", "bsci_expiry", "compliant",
    ]
    public = df[public_cols].sort_values("factory_id").reset_index(drop=True)
    mapping = df[["factory_id", "os_id", "name"]].sort_values("factory_id").reset_index(drop=True)

    tee = public[public["tee_capable"]]
    counts.update(
        tee_capable=int(len(tee)),
        rsc=int(tee["rsc"].sum()),
        bsci_valid=int(tee["bsci_valid"].sum()),
        compliant_tee=int(tee["compliant"].sum()),
        workers_imputed=int(public["workers_imputed"].sum()),
        coord_shared=int(public["coord_shared"].sum()),
        district_counts={k: int(v) for k, v in public["district"].value_counts().items()},
    )
    return public, mapping, counts


# --- step 2: synthetic history ----------------------------------------------

def _sigmoid(x):
    return 1.0 / (1.0 + np.exp(-x))


def _months(start: date, end: date) -> list[tuple[int, int]]:
    out, y, m = [], start.year, start.month
    while (y, m) <= (end.year, end.month):
        out.append((y, m))
        y, m = (y + 1, 1) if m == 12 else (y, m + 1)
    return out


def generate_synthetic(factories: pd.DataFrame, seed: int = C.SYNTH_SEED
                       ) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Return (factory_synth, history, hidden). `hidden` holds latent traits for tests only; it is never stored."""
    rng = np.random.default_rng(seed)
    f = factories[factories["tee_capable"]].sort_values("factory_id").reset_index(drop=True)
    n = len(f)

    logw = np.log(f["workers"].to_numpy(float))
    z_size = (logw - logw.mean()) / logw.std()
    theta = (rng.normal(C.THETA_MEAN, C.THETA_SD, n)
             + C.THETA_COMPLIANT_BONUS * f["compliant"].to_numpy(float)
             + C.THETA_SIZE_COEF * z_size)
    z_theta = (theta - theta.mean()) / theta.std()

    r = C.PRICE_THETA_CORR
    price = C.BASE_CM_PRICE_BDT * np.exp(C.PRICE_LOGSD * (r * z_theta + np.sqrt(1 - r * r) * rng.normal(size=n)))
    p_late = _sigmoid(C.LATE_BASE_LOGIT + C.LATE_THETA_COEF * z_theta + rng.normal(0, C.LATE_NOISE_SD, n))
    late_scale = rng.uniform(*C.LATE_SCALE_RANGE, n)
    accept_prob = rng.beta(*C.ACCEPT_BETA, n)
    overdeclare = np.exp(rng.normal(C.OVERDECLARE_LOGMEAN, C.OVERDECLARE_LOGSD, n))

    true_daily = f["workers"].to_numpy(float) * rng.uniform(*C.SEWING_SHARE_RANGE, n) * C.PCS_PER_OPERATOR_DAY
    declared_daily = np.round(true_daily * overdeclare).astype(int)
    booked = rng.random(n) < C.FULLY_BOOKED_SHARE
    share = rng.beta(*C.SPARE_SHARE_BETA, n)
    declared_spare = np.where(booked, 0, np.minimum(np.round(share * declared_daily), C.SPARE_DAILY_CAP)).astype(int)
    hist_spare = np.maximum(np.minimum(rng.beta(*C.SPARE_SHARE_BETA, n) * declared_daily, C.SPARE_DAILY_CAP), 100)

    tercile = pd.qcut(f["workers"].rank(method="first"), 3, labels=False).to_numpy()
    mb = np.array(C.MIN_BATCH_CHOICES)
    min_batch = mb[np.clip(tercile + rng.integers(0, 2, n), 0, len(mb) - 1)]
    setup = rng.integers(C.SETUP_DAYS_RANGE[0], C.SETUP_DAYS_RANGE[1] + 1, n)

    synth = pd.DataFrame({
        "factory_id": f["factory_id"],
        "declared_daily_capacity": declared_daily,
        "declared_spare_daily": declared_spare,
        "unit_price_bdt": np.round(price, 2),
        "min_batch": min_batch,
        "setup_days": setup,
    })
    hidden = pd.DataFrame({
        "factory_id": f["factory_id"], "theta": theta, "p_late": p_late, "late_scale": late_scale,
        "accept_prob": accept_prob, "overdeclare": overdeclare, "true_daily": true_daily,
        "true_fill_mean": _sigmoid(theta) + (1 - _sigmoid(theta)) * _sigmoid(
            (C.SHORT_FILL_LOGIT + C.SHORT_FILL_THETA_COEF * z_theta)
            / np.sqrt(1 + np.pi * C.SHORT_FILL_NOISE_SD ** 2 / 8)),
        "booked": booked,
    })

    # Shock calendar
    months = _months(C.HISTORY_START, C.HISTORY_END + timedelta(days=60))
    districts = sorted(d for d in f["district"].unique() if d != "unknown")
    national = {m for m in months if rng.random() < C.NATIONAL_SHOCK_PROB}
    district_shock = {(d, m) for d in districts for m in months if rng.random() < C.DISTRICT_SHOCK_PROB}

    # Jobs per factory: mixture of Poissons, so some factories have almost no history.
    w = np.array([wt for wt, _ in C.HISTORY_MIX])
    lam = np.array([mu for _, mu in C.HISTORY_MIX])
    comp = rng.choice(len(w), size=n, p=w / w.sum())
    n_jobs = rng.poisson(lam[comp])
    idx = np.repeat(np.arange(n), n_jobs)
    m = len(idx)

    span = (C.HISTORY_END - C.HISTORY_START).days - 45
    offered = np.array([C.HISTORY_START + timedelta(days=int(d)) for d in rng.integers(0, span, m)])
    job_days = rng.integers(C.JOB_DAYS_RANGE[0], C.JOB_DAYS_RANGE[1] + 1, m)
    promised = np.array([o + timedelta(days=7 + int(j)) for o, j in zip(offered, job_days)])
    accepted = rng.random(m) < accept_prob[idx]

    cap_at_time = np.round(hist_spare[idx] * rng.uniform(*C.SEASONAL_CAPACITY_RANGE, m) * job_days).astype(int)
    util = rng.uniform(*C.UTILISATION_RANGE, m)
    committed = np.maximum(100, np.round(util * cap_at_time / 100) * 100).astype(int)

    # Two-part fill (SPEC.md D2): delivered in full with p_full = sigmoid(theta), else short.
    full = rng.random(m) < _sigmoid(theta[idx])
    short = _sigmoid(C.SHORT_FILL_LOGIT + C.SHORT_FILL_THETA_COEF * z_theta[idx]
                     + rng.normal(0, C.SHORT_FILL_NOISE_SD, m))
    fill = np.where(full, 1.0, short)
    stretched = committed / (cap_at_time / overdeclare[idx]) > C.OVERSTRETCH_THRESHOLD
    fill = np.where(stretched, fill * rng.uniform(*C.OVERSTRETCH_RANGE, m), fill)

    pm = [(p.year, p.month) for p in promised]
    eid = np.array([k in C.EID_MONTHS for k in pm])
    monsoon = np.array([k[1] in C.MONSOON_MONTHS for k in pm])

    dist = f["district"].to_numpy()[idx]
    is_nat = np.array([k in national for k in pm])
    is_dist = np.array([(d, k) in district_shock for d, k in zip(dist, pm)])
    fill = np.where(is_dist, fill * rng.uniform(*C.DISTRICT_SHOCK_FILL, m), fill)
    fill = np.where(is_nat, fill * rng.uniform(*C.NATIONAL_SHOCK_FILL, m), fill)
    catastrophe = rng.random(m) < C.CATASTROPHE_PROB
    fill = np.where(catastrophe, rng.uniform(*C.CATASTROPHE_FILL, m), fill)

    late = rng.random(m) < np.clip(p_late[idx] + C.MONSOON_LATE_BUMP * monsoon, 0, 1)
    delay = np.where(late, np.ceil(rng.gamma(C.LATE_GAMMA_SHAPE, late_scale[idx])), 0)
    delay = delay + np.where(late & monsoon, C.MONSOON_EXTRA_DAYS, 0)
    # Eid is planned around: it lengthens a slip by a few days, it doesn't create one (SPEC.md D3).
    delay = delay + np.where(late & eid, rng.integers(C.EID_DELAY_DAYS[0], C.EID_DELAY_DAYS[1] + 1, m), 0)
    delay = delay + np.where(is_dist, rng.integers(C.DISTRICT_SHOCK_DELAY[0], C.DISTRICT_SHOCK_DELAY[1] + 1, m), 0)
    delay = delay + np.where(is_nat, rng.integers(C.NATIONAL_SHOCK_DELAY[0], C.NATIONAL_SHOCK_DELAY[1] + 1, m), 0)
    delay = delay.astype(int)

    delivered = np.round(committed * fill).astype(int)

    # Data-entry errors the cleaner must catch: x10 quantity typo, or a negative delay.
    err = accepted & (rng.random(m) < C.DATA_ERROR_PROB)
    typo = err & (rng.random(m) < 0.5)
    neg = err & ~typo
    delivered = np.where(typo, delivered * 10, delivered)
    delay = np.where(neg, -rng.integers(1, 11, m), delay)

    tag = [
        "+".join(t for t, on in (("national", a), ("district", b), ("catastrophe", c)) if on) or None
        for a, b, c in zip(is_nat, is_dist, catastrophe)
    ]

    history = pd.DataFrame({
        "job_id": [f"J{i + 1:06d}" for i in range(m)],
        "factory_id": f["factory_id"].to_numpy()[idx],
        "offered_date": offered,
        "accepted": accepted,
        "committed_qty": np.where(accepted, committed, None),
        "declared_capacity_at_time": cap_at_time,
        "promised_date": np.where(accepted, promised, None),
        "delivered_qty": np.where(accepted, delivered, None),
        "delay_days": np.where(accepted, delay, None),
        "unit_price_bdt": np.where(accepted, np.round(price[idx] * rng.uniform(0.95, 1.05, m), 2), None),
        "shock_tag": np.where(accepted, tag, None),
    })
    # QC at the exporter (SPEC.md D12). A separate random stream, so adding QC leaves every
    # earlier synthetic value unchanged. Per factory: a true defect rate (slightly lower for more
    # reliable factories) and a small chance that a whole delivered lot fails final QC.
    qrng = np.random.default_rng(seed + 1_000)
    def _beta(mean, conc, size=None):
        return qrng.beta(mean * conc, (1 - mean) * conc, size)
    logit0 = np.log(C.QC_DEFECT_MEAN / (1 - C.QC_DEFECT_MEAN))
    defect_mean_i = _sigmoid(logit0 + C.QC_DEFECT_THETA_COEF * z_theta)
    defect_i = _beta(defect_mean_i, C.QC_DEFECT_FACTORY_CONC)
    p_lot_i = _beta(np.full(n, C.QC_LOT_REJECT_MEAN), C.QC_LOT_REJECT_CONC)
    rate_j = _beta(np.clip(defect_i[idx], 1e-4, 0.5), C.QC_DEFECT_JOB_CONC)
    lot_rej = qrng.random(m) < p_lot_i[idx]
    qc_rejected = np.where(lot_rej, delivered, np.round(delivered * rate_j)).astype(int)
    hidden["true_defect_rate"] = defect_i
    hidden["true_p_lot_reject"] = p_lot_i
    history["qc_rejected_qty"] = np.where(accepted, qc_rejected, None)
    history["lot_rejected"] = np.where(accepted, lot_rej, None)

    history = history.sort_values(["offered_date", "job_id"]).reset_index(drop=True)
    return synth, history, hidden


def flag_bad_history_rows(history: pd.DataFrame) -> pd.Series:
    """True for accepted rows that are data-entry errors (impossible quantity or negative delay)."""
    acc = history["accepted"].astype(bool)
    committed = pd.to_numeric(history["committed_qty"], errors="coerce")
    delivered = pd.to_numeric(history["delivered_qty"], errors="coerce")
    delay = pd.to_numeric(history["delay_days"], errors="coerce")
    return acc & ((delivered > 1.2 * committed) | (delay < 0))


# --- step 3: write ---------------------------------------------------------

def _to_sql_frame(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in out.columns:
        if out[col].dtype == bool:
            out[col] = out[col].astype(int)
        elif out[col].map(lambda v: isinstance(v, date)).any():
            out[col] = out[col].map(lambda v: v.isoformat() if isinstance(v, date) else None)
    return out


def write_db(path: Path, factories: pd.DataFrame, synth: pd.DataFrame,
             history: pd.DataFrame, report: DataGenReport) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        path.unlink()
    with sqlite3.connect(path) as con:
        _to_sql_frame(factories).to_sql("factories", con, index=False)
        s = _to_sql_frame(synth)
        s["is_synthetic"] = 1
        s.to_sql("factory_synth", con, index=False)
        h = _to_sql_frame(history)
        h["is_synthetic"] = 1
        h.to_sql("history", con, index=False)
        pd.DataFrame([
            {"key": "report", "value": report.model_dump_json()},
            {"key": "notice", "value": "factories: derived from Open Supply Hub (real, pseudonymised). "
                                       "factory_synth and history: SYNTHETIC."},
        ]).to_sql("meta", con, index=False)


def load_public(path: Path = C.PUBLIC_DB) -> dict[str, pd.DataFrame]:
    with sqlite3.connect(path) as con:
        return {t: pd.read_sql(f"SELECT * FROM {t}", con) for t in ("factories", "factory_synth", "history", "meta")}


def build(raw_csv: Path = C.RAW_CSV, db: Path = C.PUBLIC_DB, mapping_path: Path = C.PRIVATE_MAPPING,
          seed: int = C.SYNTH_SEED, as_of: date = C.DATA_AS_OF) -> DataGenReport:
    raw = pd.read_csv(raw_csv, dtype=str, keep_default_na=False, encoding="utf-8")
    public, mapping, counts = clean_osh(raw, as_of)
    synth, history, _ = generate_synthetic(public, seed)

    acc = history[history["accepted"]]
    jobs_per = acc.groupby("factory_id").size().reindex(synth["factory_id"], fill_value=0)
    report = DataGenReport(
        **counts,
        history_jobs=len(history),
        history_accepted=len(acc),
        thin_history_factories=int((jobs_per < C.THIN_HISTORY_JOBS).sum()),
        seed=seed,
        as_of=as_of,
    )
    mapping_path.parent.mkdir(parents=True, exist_ok=True)
    mapping.to_csv(mapping_path, index=False, encoding="utf-8")
    write_db(db, public, synth, history, report)
    return report


if __name__ == "__main__":
    rep = build()
    print(json.dumps(json.loads(rep.model_dump_json()), indent=2))
