# The recurring top-10 ranking — findings

**Question:** the top 10 profitable strategies over the past 1, 3, 6 and 9 months.

**Answer: the table is not meaningless, but it is worthless.** Out of sample it carries about
0.7 extra names out of ten — a real, measurable, economically useless amount. It cannot
distinguish a real rule set from a random entry bar, 85% of it turns over every period, nothing in
it clears deflation, and trading last period's list returns half a hundredth of an R per trade.

---

## 1. The placebo, pooled across 112 ranking cells

| | count |
|---|---|
| cells with a placebo **inside the top 10** | **90 of 112** |
| cells with a placebo **inside the top 3** | **66** |
| cells **topped** by a placebo | **43** |

The careful reading is the ratio, not the count: **296 placebo rows inside top 10s against 349.7
expected if the ranking carried no information — ratio 0.85.** Placebos clear the 20-trade floor
*more* often than real rule sets, so a high absolute rate is expected. At 0.85 the ranking is
indistinguishable from chance.

Worker 3's independent test agrees (mean normalised placebo rank 0.566). The injection machinery
is validated two ways: a shift-0 arm reproduced its host **trade-for-trade in 786 of 786 checks**,
and the self-test's `placebo_shuffle` is uniform in every cell (see D42 — `placebo_shift` leaks and
is a conservative control).

## 2. The four windows rank different populations, not one ranking four times

Method: one full-history backtest per (symbol, timeframe) into a trade ledger; windows are slices
of it; clones collapsed. **Uncollapsed, MES 60m/274d lists one TREND rule set four times inside its
own top 10** — 220 qualifiers become 116.

Against a null that permutes R within (exit geometry × 30-day block), holding trade counts,
timings and each block's pooled R fixed, 200 draws, 13 cells:

```
274d → 180d overlap    63 observed  vs  69.5 forced by nesting   (BELOW the null)
180d →  90d            21           vs  23.6
274d →  90d            14           vs  13.5
274d →  30d             0           vs   0
population Spearman  0.741          vs  0.795 null
```

**No excess anywhere.** And the tables are not even comparable: only 69 of 130 of the 9-month
top-10 rows clear the trade floor in the 6-month window, 15 of 120 in the 3-month, and **0 of 60 in
the 1-month**.

## 3. Disjoint thirds — a small real effect that buys nothing

This is the one column with evidential weight, and it is genuinely non-zero:

**149 shared names against 124.0 by chance (+20%, sign z = +2.67, Stouffer +4.19)** — in the 25
pair-cells where a top 10 is a real choice, 63 against 44.4. That is **2.5 names of ten instead of
1.8.**

It buys nothing. **25 of the slice-1 top-10 rows are positive in all three thirds against a null of
33.7 — below chance.** Mean top-10 Jaccard across thirds is **0.152** (MES 60m 0.076), and the
top-ranked strategy changes in **281 of 385** walk-forward folds. The entire excess lives in daily
(+14.1 of +25.0) and 60m (+7.9); **240m and 30m contribute exactly zero.**

## 4. Deflation, programme-wide

**2,975,629 strategy evaluations** across the whole project (bigscan 533k, focus 613k, other
studies 1.13M, rank cells 549k+152k) → **free_t = 5.46**.

Of **1,454 rows appearing in any top 10, zero clear it** — and zero clear even their own cell's
free_t of ~4.1–4.4. Largest t anywhere in the project: **3.923**. Even discounting the 82% of
generated strategies that never trade, the effective search is ~495k and free_t 5.15, still
uncleared.

## 5. What a trader would actually have earned

Strictly causal roll: rank on the trailing window ending the instant the period starts, trade the
top 10, roll forward.

```
32,219 out-of-sample trades:        +0.0048R per trade
no-edge permutation null:           -0.0100R
excess:                             +0.0148R
```

Best configuration (60m, 6-month lookback) is +0.0706R over 1,455 trades — **of which the null
already supplies +0.0459R**, and only 3 of 6 cells beat their own null. 240m is negative at every
lookback; daily −0.007 to −0.016R; 15m/30m −0.031R.

**The comparison that ends the discussion:** ranked in one league table and followed forward, 723
real selections earned **−0.0002R** and 542 placebo selections **−0.0110R**. In the disjoint
thirds, **20% of real top-10 rows survive all three periods against 24% of placebo rows.**

## Audit

Boundary look-ahead was **measured, not assumed**: 2.47% of trades straddle a period edge, and
requiring both entry and exit inside moves the pooled figure by −0.004R — so the loose rule was
mildly generous. Signals are closed-bar, entries fill next-bar-open, the stop wins when one bar
holds both. D35's one-sided fill lives in the ORB module and cannot reach this. Costs use the
library's volatility/liquidity/news-scaled model. Clone inflation collapsed; nested
double-counting is the central correction.
