# Day 2 checkpoint: is P(meet) ≥ 0.90 reachable?

Demo order: 150,000 tees, own capacity 100,000, **subcontract Q = 50,000**, deadline 2026-12-20, as-of 2026-11-20. Max 5 factories, unproven cap 15%, 20,000 simulations per plan, seed 7. **All performance data is synthetic.**

P(meet) = probability that on-time delivered pieces ≥ Q. Buffer b* = cheapest buffer on the grid [0.0, 0.025, 0.05, 0.075, 0.1, 0.15, 0.2] reaching 0.90, else the buffer with the highest P.

## A. As planned: late beyond slack counts as 0

| Slack S (days) | Candidates | Ours: best P (shocks) | b* | Ours cost (BDT) | Ours P (independent) | Baseline P | Baseline + b* P | Baseline cost (BDT) |
|---|---|---|---|---|---|---|---|---|
| 0 | 244 | **0.324** | 20.0% | 1,934,237 | 0.348 | 0.000 | 0.027 | 1,243,075 |
| 2 | 239 | **0.357** | 20.0% | 1,877,883 | 0.379 | 0.000 | 0.044 | 1,249,246 |
| 4 | 231 | **0.389** | 20.0% | 1,860,061 | 0.412 | 0.000 | 0.090 | 1,272,064 |
| 6 | 222 | **0.487** | 20.0% | 1,907,039 | 0.519 | 0.000 | 0.118 | 1,285,424 |
| 8 | 207 | **0.463** | 15.0% | 1,893,144 | 0.489 | 0.000 | 0.037 | 1,314,609 |

## B. Variant (not in plan): partial lots: work finished by the latest ship date counts

| Slack S (days) | Candidates | Ours: best P (shocks) | b* | Ours cost (BDT) | Ours P (independent) | Baseline P | Baseline + b* P | Baseline cost (BDT) |
|---|---|---|---|---|---|---|---|---|
| 0 | 244 | **0.655** | 20.0% | 1,934,237 | 0.700 | 0.000 | 0.047 | 1,243,075 |
| 2 | 239 | **0.707** | 20.0% | 1,877,883 | 0.745 | 0.000 | 0.082 | 1,249,246 |
| 4 | 231 | **0.763** | 20.0% | 1,860,061 | 0.801 | 0.000 | 0.153 | 1,272,064 |
| 6 | 222 | **0.798** | 20.0% | 1,907,039 | 0.845 | 0.000 | 0.189 | 1,285,424 |
| 8 | 207 | **0.592** | 15.0% | 1,893,144 | 0.626 | 0.000 | 0.045 | 1,314,609 |

## C. Beyond the plan's 20% buffer ceiling

Ours / baseline + same buffer, with shocks on. "infeasible" = more than 5 factories' risk-adjusted capacity.

| Lot model | S | b = 20% | b = 30% | b = 40% | b = 50% | b = 60% |
|---|---|---|---|---|---|---|
| as planned | 2 | 0.357 / 0.044 (1.88M BDT) | 0.421 / 0.102 (2.10M BDT) | 0.524 / 0.125 (2.20M BDT) | 0.667 / 0.181 (2.35M BDT) | 0.705 / 0.282 (2.64M BDT) |
| as planned | 4 | 0.389 / 0.090 (1.86M BDT) | 0.481 / 0.178 (2.05M BDT) | 0.632 / 0.202 (2.20M BDT) | 0.725 / 0.319 (2.40M BDT) | infeasible |
| as planned | 6 | 0.487 / 0.118 (1.91M BDT) | 0.552 / 0.246 (2.19M BDT) | infeasible | infeasible | infeasible |
| partial lots | 2 | 0.707 / 0.082 (1.88M BDT) | 0.894 / 0.343 (2.10M BDT) | 0.941 / 0.624 (2.20M BDT) | 0.967 / 0.799 (2.35M BDT) | 0.981 / 0.893 (2.64M BDT) |
| partial lots | 4 | 0.763 / 0.153 (1.86M BDT) | 0.896 / 0.500 (2.05M BDT) | 0.952 / 0.724 (2.20M BDT) | 0.975 / 0.859 (2.40M BDT) | infeasible |
| partial lots | 6 | 0.798 / 0.189 (1.91M BDT) | 0.925 / 0.553 (2.19M BDT) | infeasible | infeasible | infeasible |

## Why: the recommended plan at S = 4 (as planned)

Buffer 20.0%, 5 factories, 60,000 pieces, P(meet) 0.389.

| Factory | Qty | Share | Mean fill | P(late) | P(on time within S) | Thin history | Share of failures where it was short |
|---|---|---|---|---|---|---|---|
| F0657 | 14,500 | 24% | 0.90 | 0.21 | 0.86 | no | 27% |
| F0620 | 14,000 | 23% | 0.88 | 0.18 | 0.89 | no | 22% |
| F1658 | 13,700 | 23% | 0.92 | 0.28 | 0.84 | no | 31% |
| F0637 | 9,100 | 15% | 0.89 | 0.28 | 0.81 | no | 35% |
| F2427 | 8,700 | 14% | 0.86 | 0.23 | 0.85 | no | 30% |

Across all 1,748 T-shirt factories, the median P(on time within 4 days) is 0.78. Late jobs in history run 1.73-shape Gamma, about 7 days late on average.

## Re-allocation smoke test (Day 2: exclude + re-solve)

F0657 (largest line) declines → new plan: 5 factories, 60,000 pieces, cost 1,835,961 BDT (Δ -24,101), P(meet) 0.376 (was 0.389).
