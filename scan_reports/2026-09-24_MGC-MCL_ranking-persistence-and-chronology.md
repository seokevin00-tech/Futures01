# Ranking persistence and chronological rotation — MGC, MCL (clean contracts), with MES/MNQ as the contaminated comparison

**Date:** 2026-09-24
**Code at commit:** `d284e37` (chronology), `de23c12` (re-scoped ranking)
**Row-level artefacts:** `workspace/studies/RANKING_FINDINGS.md`, `workspace/chrono/FINDINGS.md`,
`workspace/studies/out/rank_persistence_audit.json`, `workspace/studies/out/rank_mgc_mcl_audit.json`
**Re-run:** `python workspace/chrono/ledger.py && python workspace/chrono/analyse.py`;
ranking cells via `workspace/newstrats/rank_audit.py`

This report answers three questions the user asked in sequence, and it is written so the
answers survive without the chat transcript:

1. *"Keep testing with 4 workers and give me the top 10 that came to be profitable the past
   month, 3 months, 6 months, and 9 months."*
2. *"Do the same study but for the symbols MGC and MCL."* → *"Do you have at least a top 5
   strategies or strategy groups with timeframes best suited uniquely for each symbol now?"*
3. *"Is there a trend to what futures strategies worked chronologically — a chain, unique to
   each symbol?"*

---

## The four mandatory statements

**1. How many strategies were screened.** 516,651 evaluations on the clean MGC+MCL population
(60m, 240m, MGC daily; both `rth_only` arms built and never mixed in one `run_portfolio` call).
Programme-wide across this repository: **2,975,629**.

**2. The deflation verdict.** Searching *n* strategies buys roughly `sqrt(2·ln n)` free t-units.
On this population `free_t = 5.13`; programme-wide `free_t = 5.46`. **Zero of 692 top-10 rows
clear it, and zero clear even their own cell's threshold (4.03–4.13).** Largest t on clean
contracts: **3.27**. Largest t anywhere in the project: **3.923**. Nothing is close, and
nothing here is live-eligible.

**3. Sample size on every row.** Stated inline throughout. Where a "top 10" is drawn from a cell
holding 1–9 qualifiers, the top 10 *is* the whole population and is labelled as such rather than
ranked. The median trade count inside a disjoint third is **7–15**, which is why the replication
column below is weak evidence rather than strong.

**4. What the data could not support.** The hourly files carry 11–12 months, which cannot support
a transition matrix over thirteen strategy groups — the chronology test is reported on **daily**
series only, and the hourly attempt is recorded as skipped rather than squeezed. MES/MNQ/NQ/ES are
one index complex (they share 0.5–0.8% of rule sets, D14/D41), so agreement between them is not
corroboration. The grain CSVs splice contract months (D40) and are excluded. Nested windows
(30 ⊂ 90 ⊂ 180 ⊂ 274 days, all ending on the same bar) make window agreement arithmetic, not
replication.

---

## Part A — the recurring top-10 list

**The table is not meaningless, but it is worthless.** Out of sample it carries about 0.7 extra
names out of ten on the contaminated population, and on clean contracts that excess disappears.

### A.1 What a trader would actually have earned

Strictly causal roll: rank on the trailing window ending the instant the period starts, trade the
top 10, roll forward.

| arm | OOS trades | realised | no-edge null | excess |
|---|---|---|---|---|
| `rth_only=False` | 13,171 | **−0.0155R** | −0.0104R | **−0.0050R** |
| `rth_only=True` | 4,574 | +0.0019R | +0.0019R | **0.0000R** |

The pooled population's encouraging +0.0048R (excess +0.0148R) **does not survive** re-scoping to
clean contracts.

**The single cleanest statement in the whole exercise:** at a 6-month lookback the selected top 10
**underperforms trading the entire qualifying universe**, in both arms — +0.022R against +0.057R,
and −0.024R against +0.064R. *Selecting is worse than not selecting.* MGC daily is negative at
every lookback. The top-ranked strategy changes in **58 of 70** walk-forward folds.

### A.2 Placebo cohorts — and a correction to what I first reported

Three control kinds run through each base strategy's own exits, filters and sizing, with only the
signal layer replaced: `placebo_random` (random bars, count-matched), `placebo_shift` (5-bar
displacement), `placebo_shuffle` (timestamp permutation).

**Correction, stated in place.** I first reported that "a random entry outranks the real signal in
a third of cells". That was inflated by the control cohort's share of each table — the median
control share is **0.37**, not the ~10% the headline implicitly assumed. Share-matched to a 10%
cohort inside each cell (500 draws, honest kinds only):

```
12.3 cells observed vs a null of 18.6 over 27 cells    z = -2.63   (rth_only=False)
13.6 vs 14.0                                           z = -0.21   (rth_only=True)
```

**Controls reach the top 10 less often than chance.** So the ranking does separate signal from
noise — a little. `placebo_shift` leaks (D42) and is the only kind that ranks better than uniform
(mean normalised rank 0.466 against 0.542 random and 0.550 shuffle); the two honest kinds rank
*worse* than uniform.

### A.3 Persistence

- **Nested windows: no excess anywhere.** 274d→180d overlap 13 observed against 17.2 *forced* by
  nesting; 180d→90d 10 against 10.3. Population Spearman below the null in both arms. **0 of 40**
  rows of the 9-month top 10 clear the trade floor in the 1-month window.
- **Disjoint thirds: breaks on clean data.** The pooled run's one encouraging number (+20% name
  overlap, 149 vs 124.0, sign z = +2.67) **was a property of the contaminated population.** Clean:
  overlap **15 against 16.9 by chance (−11%)**, above chance in 5 of 12 pair-cells, sign z = −0.58.
  Triple-positive 8 against a null of 10.7. Jaccard **0.081** overall, **0.048** where a top 10 is
  a genuine choice.

### A.4 Why the nulls mean something — the power control

A deliberate look-ahead cheat (the same entries shifted 5 bars *backward*) ranks **1st in all four
274-day cells**, median expectancy **+0.30 to +0.76R** against a real median of −0.05 to +0.07R,
beating its own base in **15 of 16** matched pairs. A second cheat (peeking 2 bars early) ranks
**1 of 147, 1 of 106, 1 of 21, 1 of 24** in the four MGC/MCL cells.

**The harness detects a signal that is really there.** That licenses reading the null results above
as real absence rather than an insensitive test.

### A.5 Fill and cost audit

Clean on **42,279 trades**: entry is the fill bar's *open* plus adverse slippage (2.0-tick model
ceiling), never a bar extreme; **0 favourable-slippage fills**; 321 trades hit a target on their
entry bar and in **0** of those was the stop also touched — the D35 shape is absent here. Boundary
look-ahead was measured rather than assumed: 2.47% of trades straddle a period edge and requiring
both entry and exit inside moves the pooled figure by **−0.004R**, so the loose rule was mildly
generous and no conclusion depends on it.

**One economic difference worth keeping: MCL is the cost-fragile contract, MGC is not.** Costs flip
**8 of 183** MCL 60m rows and 3 of 37 MCL 240m rows from positive gross to negative net, against
**1 of 259** for MGC.

### A.6 The contaminated comparison (MES/MNQ), kept for contrast

On the index complex the placebo result is far worse: best placebo ranked 1st in **12 of 16 cells**,
≤3rd in 15 of 16; **96 of 160 top-10 slots are placebos**; and on **MES 30d/240m no real strategy
appears in the top 10 at all**. Tag-blindness was verified — relabelling random reals reproduces
the analytic null to within 0.04, so this is the data and not the code. Selecting the in-sample top
10 beat its own fold base rate in **4 of 16** pre-registered folds (sign z = −2.00). At MES 60m,
across 100 sibling families, the **median fraction of siblings positive is 0.0**.

---

## Part B — the per-symbol answer

This is the durable form of the answer to *"top 5 strategies or strategy groups per symbol"*. It is
a **framework for a discretionary read**, not a set of edges: nothing below clears deflation.

| symbol | what measured best | timeframe | strength of claim |
|---|---|---|---|
| **MCL** | MOMENTUM (66 observations, 97% positive, beat its control on both arms) | 60m and 240m | strongest in the study, still not live-eligible; cost-fragile |
| **MGC** | VWAP, TREND, MOMENTUM, MULTI_TIMEFRAME — all modestly above control | **60m only** | MGC at 240m is worse than its own placebo |
| **MES / MNQ** | nothing beat its own placebo, including TREND at 154 observations and 100% positive | — | default to no trade |

MGC and MCL are the only **independent** contracts in this data set. The one controlled positive
anywhere in the index complex — `require_alignment ≥ 0.5` on MES 240m, +0.047R in sample and
+0.077R out of sample against a random-veto control — **helps in 1 of 4 cells, in a cell found by
looking at 4.** MNQ 60m with `rth_only=True` is the single cell of 20 where reals beat placebos at
the top (p = 0.0015), but it was a post-hoc find, its 60/40 holdout is *below* base rate (67% vs
72%), and in the disjoint thirds the best placebo lands 1st, 3rd and 9th. **Not live-eligible.**

The eight measured operating rules that came out of this programme (two signals and one filter;
multi-timeframe agreement is not a virtue; win rate and payoff cancel; structural stops never
tighter than ~0.5 ATR; no intraday entries 15:00–16:00 ET; no hours filter helps; sub-hourly is a
graveyard; ORB and ICT do not pay) are carried in `CALLOUT.md`, which is the operating brief for
the live-callout session.

---

## Part C — does strategy-group efficacy rotate chronologically?

**Question:** is there a sequence — MGC works on VWAP, then TREND, then MOMENTUM, then VWAP again —
and is that sequence unique per symbol?

**Answer: no detectable chaining. The one suggestive pattern is on MGC and it does not survive
correction.**

**Method.** Monthly buckets from the daily series (MGC 117 months, 56,670 trades; MES and MNQ 89
months each, 34k and 39k). Every trade from every generated strategy counts once — **no floor on
strategies and no ranking**, because selecting before measuring would bake in the very persistence
the test looks for. Three tests in increasing order of power: persistence of the monthly winner
(null = shuffle of month order, which destroys chronology while preserving each group's marginal
strength exactly), own-group autocorrelation, and **cross-lag**, which states the chaining
hypothesis directly.

**Persistence of the winner — nothing.**

| symbol | winner repeats | shuffled null | z |
|---|---|---|---|
| MGC daily | 20 / 95 | 18.2 | +0.49 |
| MNQ daily | 18 / 60 | 14.7 | +1.00 |
| MES daily | 17 / 56 | 18.3 | −0.37 |

Which groups win most often is only mildly symbol-specific, and **the same three groups top all
three symbols** (MULTI_TIMEFRAME, VWAP, MOMENTUM) — shared structure, not a per-symbol signature.

**Own-group autocorrelation — weak, positive, uncorrected.** Strongest are all on MGC (TREND
r=+0.383 on n=25, MULTI_TIMEFRAME r=+0.249, VWAP r=+0.165 on 106 months). None reaches |z|=1.96
individually and there are 18 of them, so **zero survive any correction.**

**Cross-lag — the direct test, and it fails.** 0 surviving pairs on every symbol (MGC 20 pairs,
Bonferroni |z| ≥ 3.02; MNQ 12 pairs, 2.87; MES 6 pairs, 2.64). The largest effects, all on MGC and
all short of threshold:

```
VWAP(t)            -> TREND(t+1)      r=+0.528  n=25  z=+2.75   (needs 3.02)
VWAP(t)            -> MOMENTUM(t+1)   r=+0.240  n=97  z=+2.37
MULTI_TIMEFRAME(t) -> TREND(t+1)      r=+0.495  n=20  z=+2.24
```

This is the closest the hypothesis comes to being true — and note what it is *not*. **It is not a
rotation:** all four leading effects are *positive*, so a good VWAP month predicts a good TREND
month rather than a handover. That is co-movement, groups rising and falling together, which is
what an underlying regime driving everything would produce. The two largest rest on n=25 and n=20
months, they were selected by scanning 20 pairs, and MES and MNQ show nothing at all with their
largest cross-lags flipping sign.

**Worth stating plainly:** even had a chain been found, it would describe what already happened.
Acting on it requires knowing which group leads *before* the month it leads in, which is a strictly
harder test than this one.

---

## Net

The contaminated population was flattering the one encouraging persistence number and understating
the ranking's ability to beat a control. Corrected on clean contracts: the ranking separates signal
from noise a little (controls below chance at z = −2.63; reals beat controls followed forward),
but there is **no out-of-sample name persistence**, **nothing clears deflation**, and **the
realised expectancy of acting on the list is negative and worse than trading the whole qualifying
universe.** There is no chronological chain on any symbol.

**Live eligibility: unchanged, and firmer.** Nothing in this library should be traded as a system.
Known-broken machinery is catalogued in `workspace/studies/DEFECTS.md` (D1–D43); D21, D24, D36,
D37, D38, D39 and D40 are open at the time of this report.
