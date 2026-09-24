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

---

## Worker 1 (MGC, MCL) — a more careful reading of the placebo result

A placebo is inside the top 10 in **20 of 23 non-empty cells** (12.4 expected under the no-edge
null), and ranks **1st in 8 cells**. On **MGC 240m/180d the top four rows are all placebos.**

**But the raw count overstates it, and the correct split matters.** A top-10 placebo only means
something relative to the control's share of that table:

- In the **12 large cells** (N = 97–251, control share 8.7–12%): a placebo is inside the top 10 in
  **9 of 12 against 7.6 expected — Poisson-binomial p = 0.30. Not distinguishable from chance in
  either direction.**
- In the **11 thin cells**, the 12-placebo floor forces the control share to 23–73%, so the best
  placebo is expected at rank 1–4 *regardless*. Report, do not interpret.

Also: the 3 placebos derived from one base are **not 3 independent controls** — on MGC 240m/180d,
3 of the top 4 share a single base.

This reaches the same conclusion as worker 3's raw counts by a stricter route: **the ranking cannot
distinguish a real rule set from a random entry, and in properly-sized cells the placebo rate is
exactly what chance predicts.**

### The leak worker 1 found in its own matching — three iterations

This is the check earning its keep, and it ran in the direction that would have *hidden* the
result:

| version | behaviour | mean normalised placebo rank |
|---|---|---|
| v1: group × trade-count-quantile round-robin | handed the control arm each group's **least active** strategy | **0.57**, sign test 16/23 (p=0.047) — placebos systematically ranking low, the void condition |
| v2: draw before clone collapse | overcorrected — base median 96 vs population 56 | — |
| **v3: stratify on the ranked population's trade-count quartiles** | controls carry slightly **more** trades than reals (Stouffer +2.46) | **0.5104**, sign test 14/23 (p=0.20) |

v3's residual bias is in the **conservative** direction — more trades means lower variance, so if
anything **these placebo ranks are understated**.

**Warning that qualifies the pooled analysis:** do not pool placebo rows across cells. The pooled
KS reads p=0.014 purely from that trade-count inflation.

### The power control — what makes the nulls mean something

A deliberate look-ahead cheat — the same entries shifted 5 bars **backward** — ranks **1st in all
four 274-day cells**, median expectancy **+0.30 to +0.76R** against a real median of −0.05 to
+0.07R, beating its own base in **15 of 16** matched pairs.

**The harness sees a signal that is really there.** So the null results are real absence, not an
insensitive test.

Fill audit clean on **42,279 trades**: entry is the fill bar's *open* plus adverse slippage (max
2.0 ticks, the model ceiling), never a bar extreme; **0 favourable-slippage fills**; 321 trades hit
a target on their entry bar and in **0** of those was the stop also touched — **the D35 shape is
absent here**.

### Deflation and replication

**0 rows clear `free_t` in any of 28 cells** (free_t 4.03–4.13). Largest t anywhere: **2.97**
(MCL 60m 180d). Nothing is close.

Disjoint-slice replication 11 of 40 — but the median trade count *inside a third* is 7–15, so this
column is weak rather than strong.

### One economic difference worth keeping

**MCL is the cost-fragile contract; MGC is not.** Costs flip **8 of 183** MCL 60m rows and 3 of 37
MCL 240m rows from positive gross to negative net, against **1 of 259** for MGC.

---

## Worker 2 (MES, MNQ) — the most damning placebo result of the four

**Best placebo ranked 1st in 12 of 16 cells, ≤3rd in 15 of 16, and 5th in the last.
96 of 160 top-10 slots are placebos.**

Null-adjusted (placebos are 22–52% of floored rows, so E[best placebo rank] = 1.0–2.9): the
one-sided p that reals beat placebos at the top is **never below 0.109 in any of the 16 cells**.
Tag-blindness verified — relabelling random reals reproduces the analytic null to within 0.04, so
this is the data and not the code.

| cell | placebos in top 10 | best real |
|---|---|---|
| MES 30d 60m | **9 / 10** | VOLUME_PROFILE +0.08, n=26 |
| **MES 30d 240m** | **10 / 10 — no real strategy in the top 10 at all** | — |
| MNQ 30d 60m | 6 / 10 | MOMENTUM +0.55, n=24 |
| MES 90d 60m | 2 / 10 | VOLUME_PROFILE +0.63, n=20 |
| MES 90d 240m | 9 / 10 | VWAP +0.09, n=41 |
| MNQ 90d 60m | 3 / 10 | TREND +0.66, n=28 |
| MNQ 90d 240m | 9 / 10 | MOMENTUM +0.14, n=32 |
| MES 180d 60m | 4 / 10 | VOLUME_PROFILE +0.35, n=44 |
| MES 180d 240m | 7 / 10 | VWAP +0.67, n=27 |
| MNQ 180d 60m | 2 / 10 | TREND +0.70, n=27 |
| MES 274d 60m | 7 / 10 | VOLUME_PROFILE +0.35, n=56 |
| MNQ 274d 60m | 3 / 10 | TREND +0.44, n=44 |

**Deflation: 0 rows clear `free_t` (4.29 at 60m, 4.38 at 240m) in any of the 16 cells.** Max t in a
floored ranking is 3.03. The floor-free census shows 7–18 clearers per cell — every one has n=2–4
trades and most are placebos, so the honest count is zero.

**Replication:** 4 of 40 of the 274d top 10 are positive across all three disjoint thirds, and only
2–3 of each 10 even traded in all three. Against the coin-flip benchmark p³ the observed rates are
0.045/0.045/0.081/0.031 vs 0.041/0.040/0.099/0.080 — **no persistence in expectancy sign at all.**

**Walk-forward:** selecting the in-sample top 10 beat its own fold base rate in **4 of 16**
pre-registered folds (sign z = −2.00, p = 0.046, mean deficit −0.12). Placebos took 3–10 of every
fold's top 10.

**Parameter sensitivity:** 100 sibling families at MES 60m — **median fraction of siblings positive
is 0.0**.

### The one controlled positive, and the one candidate

`require_alignment ≥ 0.5`, paired per rule set, split-half, against a random veto removing the
identical number of trades: **MES 240m +0.047R in sample and +0.077R out of sample** (veto control
−0.004/−0.011). It is the only controlled positive in the study — and it **helps in 1 of 4 cells,
in a cell found by looking at 4** (MES 60m −0.087 OOS, MNQ 240m −0.025, MNQ 60m −0.072).

**MNQ 60m with `rth_only=True`** is the single cell of 20 where reals beat placebos at the top
(best placebo 17th of 322, null E = 3.05, one-sided p = 0.0015, paired host t = +2.55). But it was
a post-hoc find from a D24 control arm, its 60/40 holdout is **below** base rate (67% vs 72%), and
in the disjoint thirds the best placebo lands 1st, 3rd and 9th. **Not live-eligible.**

### D14 confirmed quantitatively

MES and MNQ populations share **44 of ~9,250 rule sets at 60m (0.5%)** and **108 of ~14,000 at 240m
(0.8%)**. "The same strategy on the other contract" is not merely unreliable — it is **not
available**.

---

# Re-scoped to MGC + MCL — the clean, independent contracts

The pooled analysis ran over a population containing the index complex (one instrument wearing
four names) and the grains (whose CSVs splice contract months, D40). Re-run on the only two
genuinely independent, clean contracts, 60m/240m plus MGC daily. Both `rth_only` arms built and
**never mixed in one `run_portfolio` call**; both agree on every verdict.

## 1. The placebo finding WEAKENS — and I overstated it

**Correction.** I reported that "a random entry outranks the real signal in a third of cells". That
was inflated by the control's share of each table: the median control share is **0.37**, not the
10% the headline implicitly assumed.

Share-matched to a 10% cohort inside each cell (500 draws), using only the two honest kinds
(`random` + `shuffle`): controls reach the top 10 **less often than chance** —

```
12.3 cells observed vs a null of 18.6 over 27 cells    z = -2.63   (rth_only=False)
13.6 vs 14.0                                            z = -0.21   (rth_only=True)
```

Uncorrected the cells read 26 of 28, which is the inflation worker 1 identified. **So the ranking
does separate signal from noise — a little.**

The `placebo_shift` leak (D42) replicates independently: mean normalised rank **0.466** (below 0.5
in 22 of 34 cells) against **0.542** for random and **0.550** for shuffle. The two honest kinds
rank *worse* than uniform; only the leaky one ranks better.

## 2. Nested-window overlap HOLDS

274d→180d: **13 observed vs 17.2 forced** by nesting (rth=False), 21 vs 23.5 (rth=True).
180d→90d: 10 vs 10.3 and 10 vs 10.1. Population Spearman below the null in both arms.
**0 of 40** rows of the 9-month top 10 clear the floor in the 1-month window, in each arm.

## 3. The disjoint-thirds persistence BREAKS

The pooled run's one encouraging number — **+20% name overlap, 149 vs 124.0, sign z = +2.67** —
**was a property of the contaminated population.**

On clean data: overlap **15 vs 16.9 by chance = −1.9 (−11%)**, above chance in 5 of 12 pair-cells,
sign z = −0.58. Triple-positive **8 against a null of 10.7**. Jaccard **0.081** overall and
**0.048** where a top 10 is a genuine choice — *worse* turnover than the pooled 0.152.

The rth=True arm shows +34%, but its 240m cells hold 1–9 qualifiers so the "top 10" is the entire
population; restricted to the 4 pair-cells with ≥20 qualifiers on both sides it is +3.3, Stouffer
+1.60, triple-positive 1 against a null of 2.0. MGC daily alone: 7 vs 5.8, one of three pairs
positive, survival **2 against a null of 5.2**.

## 4. Trading last period's top 10 — STRENGTHENS as a negative

```
rth_only=False, 13,171 OOS trades:   -0.0155R   vs null -0.0104R   excess -0.0050R
rth_only=True,   4,574 OOS trades:   +0.0019R   vs null +0.0019R   excess  0.0000R
```

**The pooled +0.0048R (excess +0.0148R) does not survive.** On clean contracts the excess is
**negative or exactly zero**.

**And the cleanest statement in the whole exercise:** at a 6-month lookback the selected top 10
**underperforms trading the entire qualifying universe**, in both arms — +0.022R against +0.057R,
and −0.024R against +0.064R. *Selecting is worse than not selecting.* MGC daily is negative at
every lookback. The top-ranked strategy changes in **58 of 70** folds.

Consistent with the corrected placebo read, reals do beat controls followed forward: +0.0492R vs
+0.0309R (rth=False), −0.0568R vs −0.0690R (rth=True) — a small, consistent edge to the reals.

## 5. Deflation HOLDS

516,651 evaluations on this population → **free_t = 5.13**. **0 of 692 top-10 rows clear it**, and
zero clear their own cell's free_t. Max t = **3.27** (this run) and 2.97 (worker 1's) — both below
the 3.92 the contaminated population produced.

## Net

The contaminated population was **flattering the one encouraging number and understating the
ranking's ability to beat a control**. Corrected on clean data: the ranking separates signal from
noise a little (controls below chance at z = −2.63; reals beat controls forward), but there is **no
out-of-sample name persistence**, **nothing clears deflation**, and **the realised expectancy of
acting on the list is negative and worse than trading the whole qualifying universe.**

Live eligibility: unchanged, and firmer.

**Footnote on D43:** checked rather than assumed. The combinator emits only `rth_only=True` rule
sets and their id set is fully distinct, so nothing had actually merged before the fix — the
collision was reachable but not reached by this population. The fix stands; the prior results were
not corrupted by it.
