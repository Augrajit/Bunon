"""Tests for bunon.data_gen (PLAN.md §1, §3, v2 change 6).

Tests that need the raw CSV or the private mapping skip on a clean clone,
because those files are deliberately not in the public repo.
"""
from __future__ import annotations

import math
import sqlite3
from datetime import date

import numpy as np
import pandas as pd
import pytest

from bunon import config as C
from bunon import data_gen as dg

needs_raw = pytest.mark.skipif(not C.RAW_CSV.exists(), reason="raw CSV is local-only")
needs_mapping = pytest.mark.skipif(not C.PRIVATE_MAPPING.exists(), reason="mapping is local-only")
needs_db = pytest.mark.skipif(not C.PUBLIC_DB.exists(), reason="run `python -m bunon.data_gen` first")


@pytest.fixture(scope="module")
def cleaned():
    raw = pd.read_csv(C.RAW_CSV, dtype=str, keep_default_na=False, encoding="utf-8")
    return dg.clean_osh(raw, C.AS_OF)


@pytest.fixture(scope="module")
def public_db():
    return dg.load_public()


@pytest.fixture(scope="module")
def synthetic(public_db):
    f = public_db["factories"].copy()
    for col in ("tee_capable", "compliant"):
        f[col] = f[col].astype(bool)
    return f, *dg.generate_synthetic(f, C.SYNTH_SEED)


# --- parsing --------------------------------------------------------------

@pytest.mark.parametrize("raw, expected", [
    ("1569|1485|1600", 1569.0),
    ("1001-5000", 3000.5),
    ("2001-5000|4920|4920", 4920.0),
    ("1,200", 1200.0),
    ("0", None),
    ("", None),
])
def test_parse_workers(raw, expected):
    assert dg.parse_workers(raw) == expected


@pytest.mark.parametrize("address, lat, expected", [
    ("Dhaka-Chittagong Highway, Fatullah, Narayanganj", 23.65, "narayanganj"),
    ("Dhaka-Chittagong Road, Kanchpur", 23.70, "dhaka_city"),
    ("Kalurghat, Chattogram", 22.40, "chattogram"),
    ("Tongi, Gazipur 1712, Dhaka", 23.92, "gazipur"),
    ("Baipayl, Ashulia, Savar, Dhaka", 23.90, "ashulia_savar"),
    ("Somewhere", 23.0, "unknown"),
])
def test_assign_district(address, lat, expected):
    assert dg.assign_district(address, lat) == expected


def test_latest_date_takes_max():
    assert dg.latest_date("2025-01-01|2027-03-04|bad") == date(2027, 3, 4)
    assert dg.latest_date("") is None


# --- real-data funnel (PLAN.md §1) ---------------------------------------------

@needs_raw
def test_funnel_counts_match_plan(cleaned):
    _, _, counts = cleaned
    assert counts["raw_rows"] == 3214
    assert counts["closed_dropped"] == 18
    assert counts["open_rows"] == 3196
    assert counts["tee_capable"] == 1748
    assert counts["compliant_tee"] == 442  # rule: RSC or valid BSCI at as_of 2026-11-20


@needs_raw
def test_compliance_rule(cleaned):
    public, _, _ = cleaned
    assert (public["compliant"] == (public["rsc"] | public["bsci_valid"])).all()


@needs_raw
def test_ids_are_unique_and_stable(cleaned):
    public, mapping, _ = cleaned
    assert public["factory_id"].is_unique
    raw = pd.read_csv(C.RAW_CSV, dtype=str, keep_default_na=False, encoding="utf-8")
    _, mapping2, _ = dg.clean_osh(raw, C.AS_OF)
    pd.testing.assert_frame_equal(mapping, mapping2)


# --- public DB privacy (v2 change 6) ---------------------------------------------

PUBLIC_FACTORY_COLS = {
    "factory_id", "district", "lat", "lng", "coord_shared", "workers", "workers_imputed",
    "tee_capable", "has_knitting", "has_printing", "has_dyeing", "has_textile_prod",
    "rsc", "bsci_valid", "bsci_expiry", "compliant",
}


@needs_db
def test_public_db_has_only_allowed_columns(public_db):
    assert set(public_db["factories"].columns) == PUBLIC_FACTORY_COLS
    for table in public_db.values():
        assert not {"name", "os_id", "address"} & set(table.columns)


@needs_db
def test_public_db_coordinates_rounded(public_db):
    f = public_db["factories"]
    for col in ("lat", "lng"):
        assert np.allclose(f[col], f[col].round(C.COORD_DECIMALS))
    assert (f["workers"] % C.WORKERS_ROUND_TO == 0).all()


@needs_db
@needs_mapping
def test_public_db_contains_no_names_or_os_ids():
    mapping = pd.read_csv(C.PRIVATE_MAPPING, dtype=str, encoding="utf-8")
    with sqlite3.connect(C.PUBLIC_DB) as con:
        blob = "\n".join(
            str(v) for (t,) in con.execute("SELECT name FROM sqlite_master WHERE type='table'")
            for row in con.execute(f"SELECT * FROM {t}") for v in row
        ).lower()
    leaked_ids = [o for o in mapping["os_id"] if o.lower() in blob]
    assert not leaked_ids
    # Names: only check distinctive ones (generic words like "ltd" would match by chance).
    names = [n.strip().lower() for n in mapping["name"] if len(n.strip()) >= 12]
    leaked = [n for n in names if n in blob]
    assert not leaked, leaked[:5]


# --- synthetic data (PLAN.md §3) ---------------------------------------------

@needs_db
def test_generation_is_deterministic(synthetic):
    f, synth, history, _ = synthetic
    s2, h2, _ = dg.generate_synthetic(f, C.SYNTH_SEED)
    pd.testing.assert_frame_equal(synth, s2)
    pd.testing.assert_frame_equal(history, h2)


@needs_db
def test_no_hero_price_reliability_correlation(synthetic):
    _, synth, _, hidden = synthetic
    rho = pd.Series(synth["unit_price_bdt"]).rank().corr(hidden["theta"].rank())
    assert 0.05 <= rho <= 0.35, rho  # cheaper -> slightly less reliable


@needs_db
def test_no_hero_not_more_good_and_cheap_than_chance(synthetic):
    _, synth, _, hidden = synthetic
    n = len(synth)
    cheap = synth["unit_price_bdt"] <= synth["unit_price_bdt"].quantile(0.05)
    reliable = hidden["theta"] >= hidden["theta"].quantile(0.95)
    expected_if_independent = n * 0.05 * 0.05
    assert (cheap & reliable).sum() <= expected_if_independent * 1.5


@needs_db
def test_no_single_factory_can_carry_demo_order(synthetic):
    f, synth, _, hidden = synthetic
    days = (C.DEMO_DEADLINE - C.AS_OF).days
    min_transit = 2  # ceil(km/200) + 1 with km > 0
    window = days - C.QC_DAYS - min_transit - C.PLANNED_SLACK_DAYS - synth["setup_days"]
    risk_adj = synth["declared_spare_daily"] * window * hidden["true_fill_mean"]
    assert risk_adj.max() <= C.MAX_SINGLE_FACTORY_SHARE_OF_Q * C.DEMO_SUBCONTRACT_QTY


def _haversine_km(lat, lng, lat0, lng0):
    lat, lng, lat0, lng0 = map(np.radians, (lat, lng, lat0, lng0))
    a = np.sin((lat - lat0) / 2) ** 2 + np.cos(lat) * np.cos(lat0) * np.sin((lng - lng0) / 2) ** 2
    return 2 * 6371.0 * np.arcsin(np.sqrt(a))


@needs_db
def test_calibration_rule_median_candidate_covers_8_to_15_pct(synthetic):
    """PLAN.md §3: median feasible candidate can make 8-15% of Q in the window."""
    f, synth, _, _ = synthetic
    tee = f[f["tee_capable"]].sort_values("factory_id").reset_index(drop=True)
    km = _haversine_km(tee["lat"], tee["lng"], *C.DELIVERY_POINT_LATLNG) * C.ROAD_CIRCUITY
    transit = np.ceil(km / C.ROAD_KM_PER_DAY) + 1
    window = ((C.DEMO_DEADLINE - C.AS_OF).days - C.QC_DAYS - transit
              - C.PLANNED_SLACK_DAYS - synth["setup_days"])
    cap = synth["declared_spare_daily"] * window
    feasible = tee["compliant"] & (cap >= synth["min_batch"]) & (window > 0)
    share = cap[feasible].median() / C.DEMO_SUBCONTRACT_QTY
    assert 0.08 <= share <= 0.15, f"median candidate covers {share:.1%} of Q (n={int(feasible.sum())})"


@needs_db
def test_thin_history_share(synthetic):
    _, synth, history, _ = synthetic
    acc = history[history["accepted"]].groupby("factory_id").size()
    jobs = acc.reindex(synth["factory_id"], fill_value=0)
    thin = (jobs < C.THIN_HISTORY_JOBS).mean()
    assert 0.15 <= thin <= 0.35, thin


@needs_db
def test_declared_vs_delivered_is_realistic(synthetic):
    _, _, history, _ = synthetic
    ok = history[history["accepted"] & ~dg.flag_bad_history_rows(history)]
    fill = ok["delivered_qty"].astype(float) / ok["committed_qty"].astype(float)
    late = ok["delay_days"].astype(float) > 0
    assert 0.78 <= fill.mean() <= 0.92, fill.mean()
    assert 0.15 <= late.mean() <= 0.45, late.mean()
    assert (fill < 0.5).mean() > 0.01          # outliers exist
    assert ok["shock_tag"].notna().mean() > 0.02  # correlated shocks exist


@needs_db
def test_outlier_catcher_flags_injected_errors(synthetic):
    _, _, history, _ = synthetic
    flagged = dg.flag_bad_history_rows(history)
    rate = flagged.sum() / history["accepted"].sum()
    assert 0.005 <= rate <= 0.015, rate


def test_outlier_catcher_on_known_rows():
    h = pd.DataFrame({
        "accepted": [True, True, True, False],
        "committed_qty": [1000, 1000, 1000, None],
        "delivered_qty": [950, 9500, 1000, None],
        "delay_days": [0, 0, -3, None],
    })
    assert dg.flag_bad_history_rows(h).tolist() == [False, True, True, False]
