"""All constants and assumptions in one place.

Anything marked ASSUMPTION is a placeholder until the team's research comes in
(see RESEARCH_ASKS.md). The UI shows these as "assumed".
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

# --- Paths ---------------------------------------------------------------
ROOT = Path(__file__).resolve().parent.parent
RAW_CSV = ROOT / "data" / "raw" / "factories.csv"          # gitignored
PUBLIC_DB = ROOT / "data" / "processed" / "bunon.db"       # committed
PRIVATE_MAPPING = ROOT / "data" / "private" / "mapping.csv"  # gitignored
LLM_CACHE_DIR = ROOT / "data" / "llm_cache"

# --- Reproducibility -----------------------------------------------------
AS_OF = date(2026, 11, 20)      # fixed demo date, so the demo is reproducible later
SYNTH_SEED = 42                 # fixed before the first run (PLAN.md §3)
ID_SEED = 20261114              # separate seed: pseudonym IDs never change with SYNTH_SEED

# --- Cleaning ------------------------------------------------------------
COORD_DECIMALS = 2              # public DB coordinates rounded to ~1.1 km
WORKERS_ROUND_TO = 50           # public DB worker counts rounded (PLAN.md §12 open Q2, default yes)

# Area labels by address keyword, first match wins. Specific areas before "dhaka",
# because "Dhaka" also appears as the division name in many addresses.
DISTRICT_KEYWORDS: list[tuple[str, tuple[str, ...]]] = [
    ("ashulia_savar", ("ashulia", "savar")),
    ("gazipur", ("gazipur", "tongi", "kaliakoir", "sreepur", "konabari", "kashimpur")),
    ("narayanganj", ("narayanganj", "fatullah", "siddhirganj", "rupganj")),
    ("narsingdi", ("narsingdi",)),
    ("manikganj", ("manikganj",)),
    ("mymensingh", ("mymensingh", "valuka", "bhaluka")),
    ("cumilla", ("cumilla", "comilla")),
    ("chattogram", ("chattogram", "chittagong")),  # needs lat < 23.0 (see data_gen)
    ("dhaka_city", ("mirpur", "dhaka")),
]
CHATTOGRAM_MAX_LAT = 23.0       # "Dhaka-Chittagong Highway" appears in Narayanganj/Cumilla addresses

TEE_REGEX = r"t[\s-]?shirt|tee\b"

# --- Synthetic data (PLAN.md §3) -----------------------------------------
HISTORY_START = date(2023, 11, 1)
HISTORY_END = date(2026, 10, 31)

PCS_PER_OPERATOR_DAY = 40.0     # ASSUMPTION (research ask 1)
SEWING_SHARE_RANGE = (0.55, 0.70)  # ASSUMPTION (research ask 2)
OVERDECLARE_LOGMEAN, OVERDECLARE_LOGSD = 0.10, 0.12

FULLY_BOOKED_SHARE = 0.35       # ASSUMPTION (research ask 3)
# ASSUMPTION, calibrated once to the PLAN.md §3 rule (median feasible candidate = 8-15% of Q):
# Beta(2, 200) -> mean ~1% of declared capacity; median candidate 13.8% of Q; top 5 cover 1.67 Q.
SPARE_SHARE_BETA = (2.0, 200.0)
SPARE_DAILY_CAP = 1_000         # ASSUMPTION: at most about one line offered to outside work

BASE_CM_PRICE_BDT = 30.0        # ASSUMPTION (research ask 4)
MATERIAL_COST_BDT_PER_PIECE = 180.0  # PLACEHOLDER: fabric + trims, ~65% of FOB (SPEC.md D2); charged on every piece
SURPLUS_SALVAGE_BDT_PER_PIECE = 0.0  # PLACEHOLDER: conservative, buffer pieces carry full cost
PRICE_LOGSD = 0.12
PRICE_THETA_CORR = 0.20         # cheaper -> slightly less reliable

# --- Fill and lateness: PLACEHOLDER PENDING RESEARCH (SPEC.md D2, chosen before re-running) ---
# Two-part fill: a job is delivered in full with p_full = sigmoid(theta); otherwise
# fill = sigmoid(SHORT_FILL_LOGIT + SHORT_FILL_THETA_COEF * z(theta) + N(0, SHORT_FILL_NOISE_SD)).
THETA_MEAN, THETA_SD = 1.0, 0.8          # PLACEHOLDER: median p_full ~0.73
THETA_COMPLIANT_BONUS = 0.3
THETA_SIZE_COEF = 0.15
SHORT_FILL_LOGIT = 2.2                   # PLACEHOLDER: short deliveries miss ~5-15%
SHORT_FILL_THETA_COEF = 0.3
SHORT_FILL_NOISE_SD = 0.5
OVERSTRETCH_THRESHOLD = 1.0              # PLACEHOLDER: only beyond TRUE capacity
OVERSTRETCH_RANGE = (0.85, 0.97)         # PLACEHOLDER

LATE_BASE_LOGIT, LATE_THETA_COEF, LATE_NOISE_SD = -1.6, -0.6, 0.5  # PLACEHOLDER: median P(late) ~0.17
LATE_SCALE_RANGE = (1.0, 3.0)            # PLACEHOLDER: ~3.5 days late when late
LATE_GAMMA_SHAPE = 1.5
FULL_FILL_THRESHOLD = 0.995              # reliability model: fill >= this counts as "delivered in full"

ACCEPT_BETA = (6.0, 2.0)

# History length mixture: (weight, poisson mean)
HISTORY_MIX: list[tuple[float, float]] = [(0.20, 1.0), (0.30, 6.0), (0.50, 18.0)]
JOB_DAYS_RANGE = (10, 30)
UTILISATION_RANGE = (0.4, 1.0)           # PLACEHOLDER
SEASONAL_CAPACITY_RANGE = (0.7, 1.6)   # spare capacity varies through the year

# Eid months, approximate (year, month). Labelled approximate in DATA_PROVENANCE.
EID_MONTHS = {(2024, 4), (2024, 6), (2025, 3), (2025, 6), (2026, 3), (2026, 5)}
EID_DELAY_DAYS = (2, 4)                  # PLACEHOLDER: Eid costs a few days of delay, not quantity (SPEC.md D3)
MONSOON_MONTHS = {6, 7, 8, 9}
MONSOON_LATE_BUMP = 0.05                 # PLACEHOLDER
MONSOON_EXTRA_DAYS = 1                   # PLACEHOLDER

DISTRICT_SHOCK_PROB = 0.03      # per (district, month)
NATIONAL_SHOCK_PROB = 0.02      # per month
DISTRICT_SHOCK_FILL = (0.5, 0.8)
DISTRICT_SHOCK_DELAY = (5, 15)
NATIONAL_SHOCK_FILL = (0.85, 0.95)
NATIONAL_SHOCK_DELAY = (2, 5)

CATASTROPHE_PROB = 0.01                  # PLACEHOLDER
CATASTROPHE_FILL = (0.0, 0.4)
DATA_ERROR_PROB = 0.01

MIN_BATCH_CHOICES = (500, 1_000, 2_000, 3_000)  # ASSUMPTION (research ask 9)
SETUP_DAYS_RANGE = (3, 7)

# --- Demo order (fictional; PLAN.md §8) -----------------------------------
DEMO_TOTAL_QTY = 150_000
DEMO_OWN_CAPACITY = 100_000
DEMO_SUBCONTRACT_QTY = DEMO_TOTAL_QTY - DEMO_OWN_CAPACITY
DEMO_DEADLINE = date(2026, 12, 20)
DEMO_FOB_USD = 2.40             # ASSUMPTION (research ask 8)
DEMO_PENALTY_PCT_PER_WEEK = 5.0  # ASSUMPTION (research ask 5)
DELIVERY_POINT_NAME = "Exporter consolidation point, Gazipur (district point, not a real factory)"
DELIVERY_POINT_LATLNG = (24.00, 90.42)  # ASSUMPTION: approximate Gazipur point, to confirm

# --- LLM (PLAN.md §7) ------------------------------------------------------
# Verified 15 Nov 2026 at aistudio.google.com/docs: gemini-3.5-flash-lite is "Stable" and has a
# free tier (free-tier content may be used to improve Google products; we send only fictional
# emails, pseudonymous IDs and synthetic numbers). SDK: google-genai 3.3.0, generateContent.
LLM_PROVIDER = "gemini"
LLM_MODEL = "gemini-3.5-flash-lite"
LLM_TEMPERATURE = 0.0
LLM_SEED = 42
LLM_MODE_ENV = "BUNON_LLM_MODE"  # "offline" (default: cache only) or "live"
DEMO_EMAIL_PATH = ROOT / "data" / "demo" / "buyer_email_en.txt"

# --- Optimiser defaults (PLAN.md §5) --------------------------------------
QC_DAYS = 3
PLANNED_SLACK_DAYS = 4
ROAD_CIRCUITY = 1.35            # ASSUMPTION
ROAD_KM_PER_DAY = 200           # ASSUMPTION
TRUCK_BDT_PER_KM = 60.0         # ASSUMPTION (research ask 6)
PCS_PER_TRUCK = 12_000          # ASSUMPTION (research ask 6)
USD_BDT = 122.0                 # ASSUMPTION: exchange rate, to confirm on the day
WEEKS_AT_RISK = 2               # order cancelled after 2 weeks late (demo email)
FIXED_COST_PER_FACTORY_BDT = 20_000.0  # ASSUMPTION: onboarding, QC visits, coordination
N_SIMS = 20_000
SIM_SEED = 7
MAX_FACTORIES = 5
UNPROVEN_CAP_PCT = 0.15
THIN_HISTORY_JOBS = 3
LOT_SIZE = 100
TARGET_P_MEET = 0.90
BUFFER_GRID = (0.0, 0.025, 0.05, 0.075, 0.10, 0.125, 0.15, 0.20, 0.25)  # ceiling 25% (SPEC.md D2)
PARTIAL_LOTS_DEFAULT = True     # SPEC.md D2: work finished by the latest ship date counts
DISTRICT_CAP_SHARE = 0.60       # SPEC.md D3: max share of allocation in one district (correlated shocks)
MAX_SINGLE_FACTORY_SHARE_OF_Q = 0.40  # "no hero" check on the demo order (PLAN.md §3)
