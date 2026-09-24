# Multi-timeframe structure research — findings

Five studies on swing structure (HH/HL vs LH/LL) read across timeframes, run after the
22-study programme showed the library's existing `mtf_aligned` machinery fails.

---

## `s_freshness` — structure age and distance to invalidation

**Scope:** MGC/MES/NQ/MNQ/MCL × {60m, 240m} × 3 disjoint slices = 30 cells, newest slice held
out. Two independent simulators (a bespoke non-overlapping event study and production
`run_portfolio`), both net of costs, next-bar-open entry, stop-before-target.

### The hypothesis is refuted, and the refutation is clean

Entering near the structural invalidation does **not** improve reward for risk. It is the
same self-cancellation the programme has now measured **three times**:

```
near <= 0.5 ATR   payoff 3.00   win 0.194   expectancy -0.1101
ungated           payoff 1.75   win 0.323   expectancy -0.1061
                                            (30 cells, 1,018 trades)
```

A monotone dose-response confirms it: N0.25 z = −7.69 (OOS −7.65, **OOS sign 0/8**), N0.4
−6.24, N0.5 −3.88, N0.75 −1.12, N1.0 −0.77. Payoff climbs 1.76 → 3.42 exactly as win rate
falls 0.333 → 0.100.

**And the gradient is mechanical, not informational.** Holding geometry fixed at a 1-ATR stop
and 1.5-ATR target and gating on the same distance buckets, win rate is **flat at 0.370–0.423
across every bucket**. Proximity to invalidation carries no directional information at all;
the entire payoff/win-rate trade-off is stop distance and nothing else.

### What survives is a prohibition, not an edge

`distance_to_invalidation > 0.5 ATR` combined with `StopKind.STRUCTURE`:

```
30 cells, 1,600 trades   exp +0.0136R vs -0.1061R   win 0.411   payoff 1.44
sign 22/8   Stouffer +2.325
OOS: 10 cells, 516 trades   +0.0008 vs -0.2050   sign 7/3   z = +2.024
```

Flat across 0.25–1.25 ATR (z = +2.79 … +1.66, all OOS-positive), so it is not a knife edge.
The same gate with an ATR stop does nothing (z = +0.21), which confirms it is a **stop-placement
rule** rather than an entry filter.

**The caveat that decides how to read it:** it only restores parity. An ungated structural stop
is *worse* than a plain 1-ATR stop out of sample (z = −2.24, sign 2/8); this floor repairs that
damage. Measured against the ATR stop the floors are only +0.58 to −0.06 OOS. So the honest
statement is **"if you use structural stops, never let them be tighter than ~0.5 ATR"** — not
"structural stops are better". Nothing is live-eligible: free_t ≈ 3.00 and no positive arm
reaches it.

### Freshness does nothing

Six trend-age buckets with geometry held constant: median expectancy −0.12R to +0.21R, **no
monotonicity**, all |z| < 1.70. The freshest bucket (age 0–1, 13.1% firing, 1,371 trades) is
z = +0.459; it looked positive in sample (+0.1046R) and died out of sample (−0.0244R). The
**best** bucket is the *stalest* (age 31+), pointing opposite to the thesis.

### A suggestion in my brief was the worst arm in the study

I wrote that "a fresh 4-hour structure with price near its invalidation is the strongest form
of this idea" and told the worker to test that specific conjunction. Measured: it is the
**worst** arm, fires on **1.8%** of eligible bars (111 trades / 9 cells), expectancy −0.2851R.
The robustly-sized version is z = **−3.312**, OOS **−2.568**.

### Independent reproduction of the `mtf_aligned` negative

Built from scratch, not reusing the library's implementation: mere 4h/60m structural agreement
gives −0.0990R vs +0.0020R, sign **2/13**, z = **−2.684**. The programme's finding that
timeframe agreement *hurts* now has a second, independent confirmation.

### The census that undercuts the whole premise

Across 30 cells: structure trends on 50–66% of bars; trend age median 4–10 bars. Distance to
invalidation median **1.57–2.68 ATR**. And the implied reward:risk of the natural structural
trade — stop just past invalidation, target at the prior swing extreme — has a median of
**0.60–1.10, below 1:1 in 26 of 30 cells**. Only 21–45% of bars offer RR ≥ 1, and the prior
extreme is **already behind price** on 13–38% of bars.

That is a structural fact about these instruments, not a strategy result: the textbook
structural trade is usually a sub-1:1 proposition before any edge is considered.

### Bias checks

Look-ahead and repainting clean (a truncation probe reproduced 120/120 rows). The tape's levels
match the frame's `last_swing_low/high` and `structure_trend` on **3,684/3,684** bars, so the
filter and `StopKind.STRUCTURE` refer to the same price. Costs flagged: a 0.5-ATR structural
stop costs 0.0633 R/trade against 0.0149 R ungated (4.2×), but gross-of-cost runs are still
significantly negative (z = −3.85), so costs amplify rather than create the effect. Per-cell
statistics only, non-overlapping slices, no nested windows. Data limit: one ~10.5-month window;
240m is resampled from 1h (~450 bars per slice).
