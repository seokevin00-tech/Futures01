# Brief — the recurring profitability ranking

Four workers produce **the top 10 profitable strategies over the past 1, 3, 6 and 9 months**,
per symbol, repeatably.

## The one thing that makes this honest

**Every cell must contain a placebo cohort, ranked alongside the real strategies.**

This is not optional garnish. In this project so far:
- A placebo order block (random locations, matched only on count and direction mix) ranked
  **2nd of 387** rule sets.
- The deliberately **wrong-order** sequence supplied the **top two rows** of its own durability
  ranking.
- An every-24th-bar filter with **zero clock content** outscored the ICT kill zones.
- Trading **yesterday's** opening range beat today's.

A top-10 table without a control measures the search, not the strategies. So build, in every
cell, roughly 10% placebo strategies drawn from the same generation machinery — same exits, same
filters, same trade counts — but with their entry signal replaced by one of:
- a **random-bar** entry matched on trade count and long/short mix;
- a **shifted** entry: the real signal displaced by 5 bars;
- a **shuffled** entry: the real signal's timestamps permuted within the window.

Then report **where the best placebo ranks in each cell**. If a placebo lands inside the top 10,
say so at the top of your findings — that is the single most useful number you can produce, and
it outranks any strategy you find.

## Windows

`--window` in days: **30, 90, 180, 274**, all ending at the last bar in the data.

**These are nested by construction** — the 30-day window is a subset of the 90-day, which is a
subset of the 180-day. That is what "past 1/3/6/9 months" means and it is what the user asked
for, so use it. But it means **a strategy appearing in all four top-10 lists is one observation
seen four times, not four confirmations.** State that wherever you present the four tables
together, and do not describe cross-window agreement as replication.

To get genuine replication, additionally run `T.disjoint_slices(sym, tf, 3)` and report which of
the 9-month top 10 are positive in all three **non-overlapping** thirds. That column is the only
one in the whole report with evidential weight.

## Ranking rules

- Rank by **expectancy in R**, net of costs. Report alongside it: `n`, win rate with its Wilson
  interval, payoff ratio, max drawdown in R, `t`, and the cell's `free_t`.
- **Report the floor-free census next to the floored ranking.** The 20-trade floor selects on
  exit geometry, not signal quality: a 0.75-ATR stop yields ~88 trades where the same entry at
  1.5-ATR yields ~11. A ranking at a floor is a ranking of tight stops.
- **Collapse clones** on realised trades before counting, or the top 10 is the top 1 listed ten
  times. 80 of MGC's 139 qualifying strategies were duplicates in an earlier scan.
- **Per symbol, never pooled.** MNQ/NQ/MES/ES are one index complex; agreement among them is not
  independent evidence. MGC and MCL are the independent contracts. Say so.
- **Deflation.** Report `T.free_t(n_screened)` per cell and mark every row that clears it. Nothing
  in this project ever has; if something does, show the arithmetic before believing it.

## Known library state

Fixed and usable: the `strategy_id` collision between the two STRUCTURE exits; the R-multiple exit
that was statically deleted; the session-scale guard on the time-of-day conditions; contract-aware
`power_hour`.

Still broken — avoid or account for: **D21** (the session guard uses a 390-minute threshold that
240m never reaches); **D24** (`rth_only=True` costs every group a 2–12× population at 240m — note
`T.make_strategy` defaults it off); **D30/D39** (the opening range is unconstructable at 60m/240m;
use `workspace/newstrats/orb.py` if you touch ORB); **D36** (`session_extreme_sweep` is
self-referential); **D37** (the combinator cannot express any "A then B" sequence).

## Method

Paired per-cell tests where you compare arms, **never `T.ab` across variants of the same rule
sets** (D28 — it inflates |z| ~3.3×, reaching 8.7 on null data). Per-cell statistics combined by
Stouffer or sign test; **no pooled trade-level z** (inflates ~3×) and **no pooling of per-strategy
rows across cells** (same defect, one level up).

**D35, the one thing resampling cannot catch:** a bias whose sign is always favourable passes
every out-of-sample test. Audit your fill model directly — a limit that fills at a bar's extreme
must not also be credited with that same bar's opposite extreme as a target hit.

## Saving

`T.save(...)` the moment each cell finishes, not at the end. A previous run lost 18 studies to
agents who computed everything and saved nothing.

## Reply

Under 25 lines: where the best placebo ranked in each window, your top 10 for each window with
expectancy and n, how many survive disjoint-slice replication, and how many clear deflation.
