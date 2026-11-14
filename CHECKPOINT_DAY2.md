# Day 2 checkpoint (re-run after SPEC.md D3)

Demo order: 150,000 tees, own capacity 100,000, **subcontract Q = 50,000**, deadline 2026-12-20, as-of 2026-11-20. Max 5 factories, unproven cap 15%, **district cap 60% (ours only)**, buffer grid ceiling 25%, target P ≥ 0.9, 20,000 simulations per plan, seed 7, shocks on. Costs in BDT: CM + material (180/piece) + transport + fixed per factory. **All performance data is synthetic; fill and lateness are placeholders pending research.**

b* = cheapest buffer on our grid reaching the target, else the one with the highest P. "Baseline to match" = smallest buffer (2.5% steps, up to 60%) at which the naive baseline reaches our P.

## A. Partial lots (default)

| S | Baseline P | cost | Baseline + b* P | cost | **Ours P** | b* | cost | buffer price | Baseline to match ours: buffer | its cost | Ours saves |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 0.046 | 10.24M | 0.853 | 12.82M | **0.912** | 25.0% | 12.99M | 2.58M | 32.5% | 13.61M | +0.62M |
| 2 | 0.057 | 10.25M | 0.847 | 12.32M | **0.905** | 20.0% | 12.48M | 2.06M | 27.5% | 13.10M | +0.62M |
| 4 | 0.046 | 10.27M | 0.895 | 12.33M | **0.914** | 20.0% | 12.42M | 2.05M | 22.5% | 12.61M | +0.19M |
| 6 | 0.059 | 10.29M | 0.913 | 12.36M | **0.921** | 20.0% | 12.45M | 2.06M | 22.5% | 12.63M | +0.18M |
| 8 | 0.030 | 10.32M | 0.917 | 12.45M | **0.932** | 20.0% | 12.56M | 2.08M | 22.5% | 12.74M | +0.18M |

## B. "Late = 0" toggle (comparison)

| S | Baseline P | cost | Baseline + b* P | cost | **Ours P** | b* | cost | buffer price | Baseline to match ours: buffer | its cost | Ours saves |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 0 | 0.046 | 10.24M | 0.370 | 12.82M | **0.559** | 25.0% | 12.99M | 2.58M | 45.0% | 14.88M | +1.89M |
| 2 | 0.057 | 10.25M | 0.416 | 12.84M | **0.586** | 25.0% | 13.01M | 2.58M | 40.0% | 14.39M | +1.38M |
| 4 | 0.046 | 10.27M | 0.547 | 11.82M | **0.665** | 15.0% | 11.98M | 1.55M | 35.0% | 13.89M | +1.91M |
| 6 | 0.059 | 10.29M | 0.704 | 12.87M | **0.784** | 25.0% | 12.99M | 2.58M | 32.5% | 13.70M | +0.71M |
| 8 | 0.030 | 10.32M | 0.807 | 12.99M | **0.865** | 25.0% | 13.22M | 2.62M | 30.0% | 13.57M | +0.36M |

## District shares at S = 4 (partial lots)

| Plan | P(meet) | Buffer | Factories | District shares |
|---|---|---|---|---|
| Baseline | 0.046 | 0.0% | 4 | ashulia_savar 62%, gazipur 38% |
| Baseline + b* | 0.895 | 20.0% | 4 | ashulia_savar 77%, gazipur 23% |
| Ours | 0.914 | 20.0% | 5 | ashulia_savar 60%, gazipur 40% |
| Baseline to match ours | 0.918 | 22.5% | 5 | ashulia_savar 70%, gazipur 30% |

## Buffer frontier at S = 4 (partial lots)

| Buffer | Ours P | Ours cost | Baseline + same buffer P | its cost |
|---|---|---|---|---|
| 0.0% | 0.095 | 10.37M | 0.046 | 10.27M |
| 2.5% | 0.273 | 10.64M | 0.131 | 10.55M |
| 5.0% | 0.438 | 10.88M | 0.293 | 10.79M |
| 7.5% | 0.630 | 11.14M | 0.479 | 11.06M |
| 10.0% | 0.751 | 11.39M | 0.628 | 11.30M |
| 12.5% | 0.860 | 11.73M | 0.744 | 11.57M |
| 15.0% | 0.892 | 11.98M | 0.818 | 11.82M |
| 20.0% | 0.914 | 12.42M | 0.895 | 12.33M |
| 25.0% | 0.937 | 12.93M | 0.932 | 12.86M |

## Recommended plan at S = 4

Buffer 20.0%, 5 factories, 60,000 pieces, P(meet) 0.914, cost 12,417,585 BDT (buffer price 2,052,931).

| Factory | District | Qty | Share | P(full) | Mean fill | P(late) | P(on time within S) | Thin history |
|---|---|---|---|---|---|---|---|---|
| F2184 | ashulia_savar | 14,300 | 24% | 0.69 | 0.955 | 0.15 | 0.93 | no |
| F0288 | gazipur | 13,300 | 22% | 0.69 | 0.957 | 0.15 | 0.93 | no |
| F1064 | ashulia_savar | 12,200 | 20% | 0.56 | 0.938 | 0.17 | 0.92 | no |
| F2214 | gazipur | 10,700 | 18% | 0.41 | 0.922 | 0.21 | 0.90 | no |
| F2427 | ashulia_savar | 9,500 | 16% | 0.63 | 0.949 | 0.14 | 0.94 | no |

## Re-allocation smoke test (Day 2: exclude + re-solve)

F2184 (largest line) declines → 5 factories, 60,000 pieces, buffer 20.0%, cost 12,445,134 BDT (Δ +27,548), P(meet) 0.911 (was 0.914).
