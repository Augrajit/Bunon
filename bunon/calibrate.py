"""Calibration to an observed base rate (SPEC.md D31). Not tuning.

The interviewee says failures are rare. Until a figure is given, the anchor is a PLACEHOLDER: the naive plan
(declared capacity + lowest price, no over-cut) should ship the demo programme on time in about 90% of runs. This
command moves only the LEAST-EVIDENCED synthetic inputs, in this order, until the naive plan reaches the anchor:

1. the short-delivery rate of the synthetic history. Since D31 short pieces are a cost, not a failure, so it has
   no effect on the chance of success and is left as it is (reported);
2. the lateness of the synthetic history: every factory's chance of being late is scaled by `late_mult`;
3. only if lateness alone cannot get there, the frequency of correlated shocks (`shock_mult`).

It never changes Bunon's own logic (optimiser, simulation, success rule, the interview values or the placeholders
in config.py) and it never looks at Bunon's plan while choosing. Bunon's chance is reported afterwards.

    python -m bunon.calibrate --target 0.90          # writes data/calibration.json, which load_world applies
    python -m bunon.calibrate --target 0.80 --dry-run
"""
from __future__ import annotations

import argparse
import dataclasses
import json
from datetime import date

from bunon import config as C

PATH = C.ROOT / "data" / "calibration.json"
IDENTITY = {"late_mult": 1.0, "shock_mult": 1.0}


def load() -> dict:
    """The calibration load_world applies (identity if the file is missing or BUNON_CALIBRATION=0)."""
    import os
    if os.environ.get("BUNON_CALIBRATION", "1") == "0" or not PATH.exists():
        return dict(IDENTITY)
    d = json.loads(PATH.read_text(encoding="utf-8"))
    return {k: float(d.get(k, v)) for k, v in IDENTITY.items()}


def apply(world, late_mult: float = 1.0, shock_mult: float = 1.0):
    """The world with lateness and shock frequency scaled (the history-fitted values stay as they were fitted)."""
    fs = {f: fc.model_copy(update={"p_late": min(1.0, fc.p_late * late_mult)}) for f, fc in world.forecasts.items()}
    sh = world.shocks.model_copy(update={
        "p_national": min(1.0, world.shocks.p_national * shock_mult),
        "p_district": {d: min(1.0, p * shock_mult) for d, p in world.shocks.p_district.items()}})
    return dataclasses.replace(world, forecasts=fs, shocks=sh)


def _naive_p(raw_world, order, params, cands, late_mult, shock_mult) -> float:
    from bunon import optimizer as op
    plan = op.solve_baseline(cands, order.subcontract_qty, params, 0.0)
    return op.evaluate(plan, cands, apply(raw_world, late_mult, shock_mult), True).p_meet_qty


def solve(target: float, raw_world=None) -> dict:
    """Choose late_mult (then shock_mult) so the naive plan's chance is the target. Bisection; P falls as either
    multiplier rises. Returns the values chosen and the naive plan's chance at them."""
    from bunon import optimizer as op
    raw_world = raw_world or op.load_world(calibrated=False)
    order = op.demo_order()
    params = op.default_params(order)
    cands = op.build_candidates(raw_world, order, params)
    p = lambda lm, sm: _naive_p(raw_world, order, params, cands, lm, sm)  # noqa: E731
    out = {"target": target, "short_delivery": "unchanged (a cost since D31, no effect on the chance of success)",
           "naive_p_uncalibrated": p(1.0, 1.0)}
    shock_mult = 1.0
    if p(0.0, 1.0) < target:  # lateness alone cannot reach it: scale the shock frequency, lateness off
        lo, hi = 0.0, 1.0
        for _ in range(30):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if p(0.0, mid) >= target else (lo, mid)
        shock_mult, late_mult = lo, 0.0
    else:
        lo, hi = 0.0, 1.0
        while p(hi, 1.0) >= target and hi < 64:  # the anchor may need MORE lateness than the history shows
            lo, hi = hi, hi * 2
        for _ in range(30):
            mid = (lo + hi) / 2
            lo, hi = (mid, hi) if p(mid, 1.0) >= target else (lo, mid)
        late_mult = lo
    out.update(late_mult=round(late_mult, 4), shock_mult=round(shock_mult, 4),
               naive_p=p(round(late_mult, 4), round(shock_mult, 4)))
    return out


def bunon_p(cal: dict) -> tuple[float, float]:
    """Bunon's recommended plan under a calibration: (chance, over-cut)."""
    from bunon import optimizer as op
    from bunon import service as sv
    w = apply(op.load_world(calibrated=False), cal["late_mult"], cal["shock_mult"])
    b = sv.compute_plans(w, op.demo_order(), sv.PlanSettings(), n_compliant=0)
    return b.rec.risk.p_meet_qty, b.rec.buffer_pct


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--target", type=float, default=0.90, help="naive plan's chance of shipping on time (anchor)")
    ap.add_argument("--dry-run", action="store_true", help="report only; do not write data/calibration.json")
    a = ap.parse_args()
    cal = solve(a.target)
    p, b = bunon_p(cal)
    cal.update(bunon_p=p, bunon_overcut=b, date=str(date.today()),
               note="Calibration to an observed base rate (interview: failures are rare); the 0.90 anchor is a "
                    "PLACEHOLDER until a figure is given. SPEC.md D31.")
    print(json.dumps(cal, indent=1))
    if not a.dry_run:
        PATH.write_text(json.dumps(cal, indent=1) + "\n", encoding="utf-8")
        print(f"wrote {PATH.relative_to(C.ROOT)}")


if __name__ == "__main__":
    main()
