# SUPPLY_DEMAND, LIQUIDITY and OPENING_RANGE — post-fix re-measurement and sweep

**Seat:** supply/demand, liquidity and opening-range backtesting.
**Symbol: MNQ only.** Every number below is MNQ's. I ran nothing on MES, MGC or
CL and claim nothing about them.
**Data: synthetic.** `synthetic_series("MNQ", days=120, seed=N,
end_date=date(2026,3,17))`, six independent seeds. The generator's own docstring
says it contains no repeating pattern a strategy could exploit and that results
should hover around break-even before costs and be clearly negative after them.
**Nothing here is a live edge. No number in this file may inform a live
decision.** What synthetic data can settle is mechanical — how often a detector
fires, how large a sample a family can produce, whether a stop lands where the
thesis says it should, whether two rule sets are the same trade wearing two
names, and whether anything repaints. Those are the questions I answer.

**Code state.** Measurements were taken against the working tree at `5b3ae0c`
and later. While I ran, two commits landed underneath me: `8be8755` (per-symbol
profiles) at 18:18 and `0df691f` (anchored targets) at 18:48. Neither touches
anything I measured: `indicators/structure.py`, `features.py` and
`strategies/library.py` are byte-identical between `5b3ae0c` and `0df691f`, the
`combinator.py` change only affects callers that do **not** name groups (I
always name mine), and the `base.py` change is additive behind
`TargetKind.R_MULTIPLE`, which is the default and the behaviour I measured.
`python -m pytest tests/ -q` against the committed tree → **687 passed, 0
failed** (the brief cited 675; the two new commits added
`test_anchored_targets.py` and `test_symbol_profiles.py`).

Re-run at the moment I finished, against the **live working tree**, it is
**695 passed, 2 failed**:
`test_strategy_library.py::test_no_signal_condition_fires_on_almost_every_bar`
and `test_symbol_profiles.py::test_annualisation_uses_calendar_days_not_active_days`.
**Neither is mine and neither is caused by anything in this file.** Both tests
are new and uncommitted — `git show HEAD:<file>` finds neither of them — and
they are red against uncommitted edits to `strategies/library.py`,
`strategies/combinator.py` and `backtest/metrics.py` that landed in the working
tree while I was writing. The offending conditions the first test names are
`cvd_directional`, `ema_fast_above_slow`, `macd_directional`,
`macd_hist_direction`, `price_above_ema200`, `price_above_ema50` — all trend
and momentum, none in my three families, and `zone_touch`,
`fresh_zone_approach`, `away_from_zone` and the sweep and opening-range
conditions do not appear. Flagging it so whichever seat owns it sees it.
**I edited no source file.**

**Engine granularity.** The backtests run on a **5-minute base series**
(`resample(synthetic_series(...), 5)`), not the 1-minute source, because four
sibling seats were saturating the box and a 1-minute base costs 5x. This is a
disclosure, not a hidden choice. It does not change price: resampling is
deterministic, and the 5/15/60m zone counts off the 5-minute base are
identical to those off the 1-minute base (604 / 179 / 29 either way). It does
change two things, both in the conservative direction — entries still fill at
the next bar's open at the identical price, but stop/target sequencing inside
a bar is resolved on 5-minute bars under the engine's "stop wins" rule rather
than on 1-minute bars, and a strategy cannot re-enter inside the same 5-minute
bar. Detector-level measurements (zone counts, firing rates, prefix stability)
are unaffected by this choice entirely.

---

## Verdict

**Nothing is published as live-eligible. Zero strategies in any of my three
families cleared `MIN_TRADES_FOR_RANK = 30` with a surviving edge.** Seven
findings carry the weight.

**1. The fix is real and roughly doubles the family's raw material — and it is
still nowhere near enough.** Zone counts on 120 days of MNQ went `301 -> 604`
(5m), `81 -> 179` (15m), `12 -> 29` (60m). `zone_touch` went from firing on
1.69% of 5-minute bars to 5.89%. But on the identical 192 generated
SUPPLY_DEMAND rule sets over the identical price path, the total trade count
went `105 -> 207` and the number of rule sets clearing 30 trades went
**`0 -> 0`**. The busiest single SUPPLY_DEMAND strategy in the whole generation
took **11 trades in 120 trading days**. At that rate the 30-trade floor arrives
in about 15 months. The family is not disproven; it is **unmeasurable at this
horizon**, and doubling the detector's output did not change that conclusion,
it only moved the shortfall from ~5x to ~3x.

**2. `base_vs_departure` — the test the fix's own docstring says "is what
carries the idea" — is arithmetically unreachable at the shipped defaults, and
the entire doubling came from the other half of the change.** The base ceiling
is `min(base_max_mult * avg_range, base_vs_departure * departure_range)`. A bar
only reaches that line after passing `rng >= departure_mult * avg_range`, and
`departure_mult = 2.0`, so `base_vs_departure * rng = 0.5 * rng >= 1.0 * avg =
base_max_mult * avg` **always**. The `min()` selects the norm term
unconditionally; the departure term can only bind at exact equality. Measured
across 812 accepted bases on three timeframes: bound by the norm term 811,
bound by the departure term **0**, exact tie 1. Re-running the accept loop with
the old `0.8 * avg` ceiling reproduces the old zone counts exactly
(301 / 81 / 12). **The change that did the work was `base_max_mult: 0.8 -> 1.0`,
full stop.** This is not a behavioural bug — the new detector is better than the
old one — so I have not touched the source. It is a claim in a docstring that
the arithmetic does not support, and anyone who tunes `departure_mult` upward
expecting the departure test to keep the base honest will get nothing.

**3. The stop-geometry finding survives the fix and got worse, and it is a
defect in the stop, not in the signal.** At a 1.0xATR stop the stop lands
**inside** the zone on 88.0% of `fresh_zone_approach` firings (5m), 82.6%
(15m), 93.3% (60m). At 1.2x: 72.4 / 60.5 / 80.0. The trend seat measured 71%
and 47% on the broken detector; post-fix both are higher, because the surviving
bases are wider (median zone height 0.83 ATR on 5m) while the trigger distance
did not move at all (median gap 0.550 ATR post-fix vs 0.557 pre-fix). The
premise that the stop sits beyond the level is violated in the large majority
of cases at every stop the combinator actually emits below 1.5x. **But the
obvious repair does not work**: placing the stop beyond `SDZone.distal` plus a
4-tick pad took pooled expectancy from `-0.088R` to `-0.267R` over 139 and 136
trades. Both samples are below the floor, so that is a diagnostic, not a
ranked result — but it is evidence against "fix the stop and the edge appears".

**4. Trigger-bar disjointness badly understates trade overlap with the sweep
family, exactly as the liquidity seat suspected — and the size of the
understatement is timeframe-dependent.** Entry-bar Jaccard between a
`zone_touch` probe and the pooled sweep probes is 0.024 / 0.023 / 0.032 on
5/15/60m, consistent with "not a relabelling". Measured on realised fills, the
fraction of `zone_touch` **time in market** that coincides with a same-direction
sweep position is **15.0% (5m), 25.4% (15m), 48.6% (60m)**, and the fraction of
`zone_touch` trades with a same-direction sweep entry within ±3 primary-tf bars
is **34.2% / 61.3% / 82.4%**. The liquidity seat's conclusion holds at 5m, is
marginal at 15m, and does not hold at 60m: on the hourly chart four zone-touch
trades in five are the same trade a sweep rule would have taken.

**5. LIQUIDITY is the only one of my three families that produces a rankable
sample, and its apparent edge does not replicate across price paths — the
desk's own gate cleared it on one path in three.** 80 rule sets per path, 25-28
of them clearing 30 trades, 2,378-2,584 pooled trades. Anchored walk-forward
out-of-sample expectancy on three independent paths: **-0.121R (t -1.34,
efficiency -0.50, P(ruin) 95.6%)**, **+0.261R (t +4.06, efficiency 0.97,
P(ruin) 0.0%, `is_credible = True`, deflated +0.071R)**, **+0.082R (t +1.68,
efficiency 1.67, P(ruin) 9.5%)**. Mean +0.074R, sd 0.191R, between-path
**t = 0.67**. On synthetic data built to contain no exploitable pattern, the
full walk-forward and robustness apparatus returned a credible, deflation-
surviving, zero-ruin verdict on **one path in three**. Nothing inside a single
path caught it; seed replication did. That is the most transferable result in
this file: **a strategy cleared by walk-forward on one price series is
unverified until it is re-run on several.** No LIQUIDITY strategy is published.

**6. OPENING_RANGE fires one to three times in 120 trading days, and widening
its clock does not fix it.** 80 rule sets, 13-19 of which trade at all,
13-29 trades in total, best rule set 3 trades; `signals == trades`, so nothing
is being blocked, the rules simply never fire. Dropping the 90-minute
`opening_drive_window` filter buys 21% more signals; also dropping the
150-minute scope cap buys 4x more - and **still leaves zero rule sets above 9
trades**. The constraint is the template's conjunction, not its clock. As that
sample grew from 29 to 118 trades the apparent expectancy fell from +0.983R to
+0.192R and the profit factor from 10.3 to 1.65, which is the cleanest
illustration in this file of what a 29-trade number is worth.

**7. No look-ahead, no repainting.** 558 raw prefix-vs-masked zone comparisons
and 139 `active_zones`-vs-prefix comparisons across three timeframes,
**zero mismatches**. The post-fix detector reports the same zone geometry,
touch count and invalidation state at bar *i* whether or not the series
continues past *i*.

---

## Post-fix re-measurement

Every figure the brief listed as stale, re-measured. The "old" column is the
pre-fix detector reconstructed exactly — `supply_demand_zones(bars,
base_max_mult=0.8, base_vs_departure=inf)` collapses `min(0.8*avg, inf)` to
`0.8*avg`, which is the old line verbatim — run over the **same** price path so
the comparison isolates the detector and not the sample.

### Zones per timeframe, 120 trading days (MNQ, seed 11)

| timeframe | zones OLD | zones NEW | change | never touched | invalidated | median height |
|---|---|---|---|---|---|---|
| 5m  | 301 | **604** | +100.7% | 25 -> 59 | 257 -> 503 | 8.00 -> 10.75 pts |
| 15m | 81  | **179** | +121.0% | 4 -> 12  | 69 -> 148  | 15.25 -> 19.50 pts |
| 60m | 12  | **29**  | +141.7% | 1 -> 3   | 11 -> 24   | 39.75 -> 44.75 pts |

*Stale claim "60m had only 8 zones in 120 days" — my pre-fix reconstruction
gives 12 on seed 11 and 10 / 11 / 8 / 11 / 10 on seeds 2 / 3 / 5 / 7 / 13. The
stale 8 is inside the seed-to-seed spread, i.e. it was one draw, not a
constant. Post-fix the same six paths give 29 / 21 / 30 / 28 / 26 / 35.*

### Firing rates

One evaluation per **closed bar of that timeframe** (not per 1-minute bar), so
the denominator is the number of decisions the strategy actually gets to make.
Both an all-session and an RTH-only column, because every template in my
families sets `rth_only=True`.

| condition | tf | OLD all bars | NEW all bars | OLD RTH | NEW RTH |
|---|---|---|---|---|---|
| `zone_touch` | 5m | 1.691% | **5.885%** | 1.592% | **4.765%** |
| `zone_touch` | 15m | 1.123% | **3.034%** | 0.385% | **2.179%** |
| `zone_touch` | 60m | 0.399% | **1.594%** | 0.714% | **2.857%** |
| `fresh_zone_approach` | 5m | 1.011% | **2.089%** | 0.844% | **1.667%** |
| `fresh_zone_approach` | 15m | 0.797% | **1.721%** | 0.609% | **1.186%** |
| `fresh_zone_approach` | 60m | 0.181% | **0.543%** | 0.357% | **0.714%** |
| `away_from_zone` (filter) | 5m | 97.594% | **92.500%** | 97.650% | **93.825%** |
| `away_from_zone` (filter) | 15m | 98.234% | **95.788%** | 99.135% | **96.859%** |
| `away_from_zone` (filter) | 60m | 98.841% | **97.246%** | 99.048% | **96.190%** |

The reconstruction validates itself: the stale figures were *"`zone_touch`
fired on 1.5% of bars, `fresh_zone_approach` on 0.8%"*, and my pre-fix
reconstruction gives **1.59%** and **0.844%** on 5m RTH. Within 0.1pp. So the
new column is a like-for-like replacement, not a different measurement.

`away_from_zone` is still a filter that passes almost everything — it went from
rejecting 2.4% of 5-minute bars to rejecting 7.5%. A filter that passes 93.8%
of bars cannot be doing much work, and the sweep confirms it: it never changed
a fold selection.

### Does SUPPLY_DEMAND trade yet?

192 generated SUPPLY_DEMAND rule sets (four timeframe cells — 5m alone, 15m
alone, 60m alone, and 5m+15m+60m with higher-timeframe confirmation — 50 per
cell before dedupe), MNQ seed 11, 120 days, run twice over the **same** price
path with the two detectors:

| | OLD detector | NEW detector |
|---|---|---|
| rule sets generated | 192 | 192 |
| rule sets that take ≥1 trade | 45 (23.4%) | **62 (32.3%)** |
| rule sets with ≥10 trades | 0 | **6** |
| rule sets with **≥30 trades (the floor)** | **0** | **0** |
| total trades | 105 | **207** |
| busiest single rule set | 6 trades | **11 trades** |
| pooled expectancy | -0.343R | -0.168R |
| pooled t | -4.16 | -2.40 |
| pooled win rate / PF | 27.6% / 0.40 | 35.8% / 0.68 |

By timeframe cell (trading rule sets / trades, NEW): 5m **23 / 120**,
15m **15 / 50**, 60m **6 / 9**, 5m+15m+60m **18 / 28**. The multi-timeframe
cell produces *fewer* trades than 5m alone by a factor of four — adding
higher-timeframe confirmation to a family that is already sample-starved makes
the sample problem worse, which answers "does multi-timeframe alignment improve
outcomes" for this family with a flat no: there is no outcome to improve.

The stale claim was *"1 rankable strategy out of 400 generated, 16 trades across
72 strategies"*. My generation is a different draw so the counts are not
directly comparable, but the direction is unambiguous and measured on identical
inputs: the fix roughly doubled sample production and produced **zero**
rankable strategies either way.

### Follow-through, re-measured

The stale claim was *"88% of `fresh_zone_approach` firings were moving toward
the zone and 71% reached it within 20 bars"*. I could not reproduce either
number under a literal reading, with **either** detector, which makes them
definitional rather than detector-dependent:

| measure (5m / 15m / 60m) | OLD | NEW |
|---|---|---|
| next bar closes nearer the proximal edge | 43.0 / 33.0 / 40.0% | 41.9 / 35.3 / 46.7% |
| price reaches the zone within 20 bars | 89.3 / 84.1 / 100% | 89.3 / 89.0 / 100% |
| reaches it, then runs 1 ATR in the signalled direction | 63.6 / 65.9 / 80.0% | 66.0 / 71.6 / 93.3% |

The reach rate did not move at all on 5m (89.25% -> 89.31%), which is the point:
**the condition fires at a median 0.55 ATR from the zone, so "price reaches the
zone" is close to a tautology of the trigger, not evidence about zones.** It is
not a finding that 89% of approaches arrive; it would be a finding if they
didn't. The third row is the one with content, and I have no matched baseline
for it, so I am not claiming it means anything.

### Which base test actually binds

Re-running the accept loop with both ceilings instrumented, seed 11:

| tf | qualifying departures | zones accepted | bound by `base_max_mult*avg` | bound by `base_vs_departure*rng` | exact tie | old `0.8*avg` would accept |
|---|---|---|---|---|---|---|
| 5m  | 1357 | 604 | 603 | **0** | 1 | 301 |
| 15m | 574  | 179 | 179 | **0** | 0 | 81 |
| 60m | 160  | 29  | 29  | **0** | 0 | 12 |

Every accepted base survives **either** ceiling alone (604/179/29 under each in
isolation). The arithmetic, in full: the departure gate is
`rng >= departure_mult * avg` with `departure_mult = 2.0`; therefore
`base_vs_departure * rng = 0.5 * rng >= 1.0 * avg = base_max_mult * avg`; so
`min(...)` returns `base_max_mult * avg` for every bar that can reach the line,
with equality only when `rng` is exactly `2 * avg`. **The departure test is
dead code at the defaults.** The right reading of the fix is that the
self-referential ceiling was not removed, it was loosened from `0.8x` to `1.0x`
of a trailing average that still contains the base bars — which is enough to
admit the textbook uniform-consolidation case (`range > 1.0 * avg` is false
when every range equals the average) but leaves a base one tick wider than its
own trailing average still rejected.

---

## Seed replication

**MNQ, MES and MGC at the same seed are the same price path.** Five symbols at
one seed is one observation. So: six independent seeds, MNQ only.

| seed | zones 5m old→new | zones 15m old→new | zones 60m old→new | `zone_touch` 5m | `fresh_zone_approach` 5m | fza gap (ATR, median) |
|---|---|---|---|---|---|---|
| 2  | 315 → 604 | 56 → 146 | 10 → 21 | 5.356% | 2.089% | 0.552 |
| 3  | 308 → 624 | 69 → 168 | 11 → 30 | 5.685% | 2.177% | 0.534 |
| 5  | 315 → 609 | 69 → 158 | 8 → 28  | 6.138% | 1.908% | 0.523 |
| 7  | 300 → 581 | 60 → 144 | 11 → 26 | 5.311% | 2.141% | 0.523 |
| 11 | 301 → 604 | 81 → 179 | 12 → 29 | 5.885% | 2.089% | 0.550 |
| 13 | 307 → 595 | 62 → 156 | 10 → 35 | 5.634% | 2.089% | 0.545 |
| **mean** | **308 → 603** | **66 → 159** | **10.3 → 28.2** | **5.67%** | **2.08%** | **0.538** |
| **spread** | ±15 (2.5%) | ±13 (8%) | ±4.6 (16%) | ±0.31pp | ±0.09pp | ±0.014 |

What replicates, on all six paths:
* the 5m zone count roughly **doubles** (ratio 1.92–2.03, mean 1.96);
* the 15m count more than doubles (2.21–2.61, mean 2.42);
* the 60m count more than doubles but with wide dispersion (2.1–3.5) — at 8–12
  zones pre-fix and 21–35 post-fix, the 60m chart is small-numbers territory on
  both sides and no 60m zone statistic should be quoted to two significant
  figures;
* `zone_touch` on 5m lands in a tight band, 5.31–6.14%;
* the `fresh_zone_approach` trigger distance is essentially a constant of the
  condition, 0.52–0.55 ATR, on every path and both detectors. That is the
  number the stop-geometry finding rests on, and it is the most stable number
  in this file.

What does **not** replicate cleanly: the 60m zone-count ratio, and anything
downstream of a SUPPLY_DEMAND trade count, because those samples are single
digits per strategy.

**Replication depth differs by measurement type, and I am explicit about it.**
Detector-level findings — zone counts, firing rates, trigger distance, the
old-vs-new ratio — are measured on **six** independent paths (seeds 2, 3, 5, 7,
11, 13). Strategy-level findings — the full sweep and the anchored walk-forward
— are measured on **three** (seeds 11, 2, 5), because four sibling seats were
saturating a four-core box for most of my window and a 240-rule-set sweep plus
walk-forward is roughly two orders of magnitude more compute than a firing-rate
pass. Three paths is enough to show that the LIQUIDITY walk-forward result does
not replicate (it disagrees in *sign* between paths 1 and 2, which one
additional path settles); it is **not** enough to estimate how often the gate
false-positives. I would put the rate at "roughly one path in three" and treat
that as an order of magnitude, not a number. Stop-geometry and trade-overlap
measurements are single-path (seed 11) and should be read as such — though the
quantity they turn on, the 0.52-0.55 ATR trigger distance, is measured on all
six.

---

## Sweep results

**Scope.** 240 generated rule sets per price path — 80 per family, 20 in each
of four timeframe cells (5m alone, 15m alone, 60m alone, and 5m+15m+60m with
higher-timeframe confirmation). Three independent price paths (seeds 11, 2, 5).
Full-data pass first, then **anchored walk-forward, 4 folds,
`min_train_fraction = 0.4`, `top_k = 5`**, run **per family** so selection never
mixes families. Monte Carlo ruin on the pooled out-of-sample R series against a
$50,000 account risking $300 per trade with a $5,000 trailing failure
threshold, 3,000 paths of 250 trades.

**One procedural note that is itself a result.** My first pass used the
library's default `min_trades_is = 20`. At that setting the walk-forward
selected **nothing at all** in any family on any path — no strategy accumulated
20 trades inside a training window. I lowered it to 10 to get any
out-of-sample estimate at all, and say so because the lowered floor makes every
walk-forward number below *more* generous than the system's own default, not
less.

### Full-data pass, by family

| seed | family | rule sets | take ≥1 trade | distinct trade sets | **clear 30 trades** | pooled trades | expectancy | t | win rate | PF | max DD |
|---|---|---|---|---|---|---|---|---|---|---|---|
| 11 | SUPPLY_DEMAND | 80 | 37 | 14 | **0** | 110 | -0.256R | -3.07 | 34.6% | 0.51 | 28.1R |
| 11 | LIQUIDITY | 80 | 32 | 23 | **25** | 2441 | +0.096R | +5.28 | 53.5% | 1.29 | 15.1R |
| 11 | OPENING_RANGE | 80 | 19 | 7 | **0** | 29 | +0.983R | +6.52 | 89.7% | 10.35 | 3.0R |
| 2 | SUPPLY_DEMAND | 80 | 42 | 16 | **0** | 165 | -0.276R | -3.93 | 40.0% | 0.52 | 55.4R |
| 2 | LIQUIDITY | 80 | 32 | 18 | **28** | 2584 | +0.128R | +6.86 | 54.3% | 1.37 | 15.2R |
| 2 | OPENING_RANGE | 80 | 16 | 5 | **0** | 16 | -0.798R | -6.49 | 18.8% | 0.04 | 13.4R |
| 5 | SUPPLY_DEMAND | 80 | 41 | 16 | **0** | 131 | +0.281R | +3.25 | 61.1% | 1.89 | 5.3R |
| 5 | LIQUIDITY | 80 | 32 | 17 | **25** | 2378 | +0.013R | +0.70 | 47.1% | 1.03 | 31.8R |
| 5 | OPENING_RANGE | 80 | 13 | 4 | **0** | 13 | -0.787R | -6.11 | 23.1% | 0.01 | 10.2R |

"Distinct trade sets" counts unique `(trades, expectancy, total R)` triples.
32 LIQUIDITY rule sets that trade collapse to 17–23 distinct trade sets: the
news filters the generator switches on and off produce byte-identical results,
which is consistent with what an earlier seat measured and which I therefore
use as the *effective* hypothesis count in the deflation below rather than the
nominal 80.

**Only LIQUIDITY produces a rankable sample.** SUPPLY_DEMAND's best rule set
takes 21 trades on its best path; OPENING_RANGE's takes 3. Note also that
SUPPLY_DEMAND's pooled expectancy is `-0.256 / -0.276 / +0.281`R on three
paths — the sign flips — and OPENING_RANGE's is `+0.983 / -0.798 / -0.787`R on
13–29 trades. Those are not results, they are the standard deviation of a
handful of trades.

### Timeframe cells — does multi-timeframe alignment help?

Pooled expectancy per cell (LIQUIDITY, the only family with a sample):

| cell | seed 11 | seed 2 | seed 5 | ≥30-trade rule sets (11/2/5) |
|---|---|---|---|---|
| 5m alone | +0.102R | +0.143R | +0.012R | 7 / 7 / 7 |
| 15m alone | +0.056R | +0.205R | +0.041R | 7 / 7 / 7 |
| 60m alone | +0.121R | +0.104R | -0.007R | 4 / 7 / 4 |
| **5m+15m+60m (HTF confirmation)** | **+0.120R** | **+0.015R** | **-0.007R** | 7 / 7 / 7 |

**No.** The multi-timeframe cell is best on one path, worst on one and tied-last
on the third. Its mean across the three paths is +0.043R against +0.086R for 5m
alone and +0.101R for 15m alone. On this family, on this data, higher-timeframe
confirmation does not improve outcomes; it costs trades in SUPPLY_DEMAND
(where the same cell produced 25 / 39 / 44 trades against 59 / 101 / 61 for 5m
alone) and buys nothing in LIQUIDITY. This is a measurement on three synthetic
paths, not a law, and the right conclusion is that the assumption should not be
made for free.

### Anchored walk-forward, per family

| seed | family | IS trades | IS expectancy | **OOS trades** | **OOS expectancy** | OOS t | efficiency | selection stability | `is_credible` | P(ruin) |
|---|---|---|---|---|---|---|---|---|---|---|
| 11 | SUPPLY_DEMAND | 0 | — | **0** | — | — | 0.00 | 0.00 | False | — |
| 11 | LIQUIDITY | 398 | +0.241R | **89** | **-0.121R** | -1.34 | **-0.50** | 0.167 | **False** | **95.6%** |
| 11 | OPENING_RANGE | 0 | — | **0** | — | — | 0.00 | 0.00 | False | — |
| 2 | SUPPLY_DEMAND | 0 | — | **0** | — | — | 0.00 | 0.00 | False | — |
| 2 | LIQUIDITY | 719 | +0.268R | **190** | **+0.261R** | +4.06 | **0.97** | 0.560 | **True** | **0.0%** |
| 2 | OPENING_RANGE | 0 | — | **0** | — | — | 0.00 | 0.00 | False | — |
| 5 | SUPPLY_DEMAND | 55 | +0.106R | **6** | -0.148R | -0.35 | -1.39 | 0.333 | False | 98.3% |
| 5 | LIQUIDITY | 1480 | +0.049R | **350** | **+0.082R** | +1.68 | **1.67** | 0.286 | **True** | **9.5%** |
| 5 | OPENING_RANGE | 0 | — | **0** | — | — | 0.00 | 0.00 | False | — |

**SUPPLY_DEMAND and OPENING_RANGE produce no out-of-sample estimate at all** on
two of three paths and six trades on the third. There is nothing to evaluate.
That is the finding, and it is a clean one: at 120 days on MNQ, neither family
generates enough trades for the walk-forward machinery to have an opinion.

**LIQUIDITY is where the interesting failure lives.** Fold-by-fold OOS
expectancy tells the story better than the totals:

* seed 11: `-0.029, -0.570, -0.297, +0.164` — four folds, three negative;
* seed 2: `+0.267, +0.197, -0.146, +0.732` — three of four positive;
* seed 5: `-0.088, -0.050, +0.146, +0.226` — an apparent regime change mid-series.

On **seed 2 the family passes every automated gate**: efficiency 0.97
(walk-forward carries 97% of the in-sample edge out of sample), selection
stability 0.56, `is_credible = True`, probability of ruin 0.0%, and a deflated
expectancy of +0.071R against the 80 rule sets searched. On **seed 11 the same
procedure over the same rule sets produces -0.121R, efficiency -0.50 and a
95.6% probability of account failure**. On seed 5 it produces +0.082R and a
9.5% ruin probability, which still fails the 2% gate.

Across the three paths the OOS expectancies are `-0.121, +0.261, +0.082`R:
mean **+0.074R**, standard deviation **0.191R**, and a between-path
**t of 0.67**. There is no edge here. This is synthetic data built to contain
none.

**So the single most useful thing this sweep measured is the false-positive
rate of the desk's own gate.** On a family that produces a real sample, on data
with no exploitable structure, the full walk-forward + robustness apparatus
returned `is_credible = True` with a positive deflated expectancy on **one of
three** independent price paths. Seed replication is what caught it; nothing
inside a single path's walk-forward did. **Any strategy cleared by walk-forward
on one price series should be treated as unverified until it is re-run on
several.**

### Risk of ruin, $50,000 account

`risk_of_ruin(oos_r, starting_equity=50_000, dollar_risk_per_trade=300,
failure_drawdown=5_000, trailing=True, trades=250, runs=3000)`:

| seed | family | P(ruin) | median final equity | 5th pct | worst | 95th-pct max DD | 95th-pct losing streak | P(profit) |
|---|---|---|---|---|---|---|---|---|
| 11 | LIQUIDITY | **95.6%** | $45,393 | $44,797 | $44,675 | 55.0R | 12 | 1.4% |
| 2 | LIQUIDITY | **0.0%** | $69,596 | $62,759 | $55,269 | 8.9R | 7 | 100% |
| 5 | LIQUIDITY | **9.5%** | $56,269 | $47,702 | $44,705 | 18.8R | 9 | 92.9% |
| 5 | SUPPLY_DEMAND | 98.3% | $45,349 | $44,797 | $44,689 | 63.5R | 11 | 0.5% |

Two of three LIQUIDITY paths fail the 2% ruin gate. The $300-per-trade sizing
is itself the constraint: at 0.6% of equity per trade against a 10% trailing
failure threshold, a 17R drawdown ends the account, and the 95th-percentile
drawdown is 8.9R on the best path and 55.0R on the worst.

### Where LIQUIDITY's apparent edge actually comes from

Slices of the seed-11 pooled full-data trades (2,441 trades), with seeds 2 and
5 in brackets where the sign differs:

* **By exit reason** — `TARGET` +1.675R (190 trades), `BREAKEVEN` +0.560R (338),
  `SESSION_CLOSE` **+0.303R on 1,207 trades**, `STOP` -1.037R (664),
  `TIME` +1.152R (42). Roughly half of all trades exit at the session close,
  and that bucket carries most of the positive expectancy. That is not a
  liquidity edge; it is the arithmetic of a scale-out ladder plus a
  breakeven stop plus a forced flat at 16:00 — a trade that never reached its
  stop and never reached its first target books a small positive number
  because the breakeven move already removed its downside.
* **By session** — RTH_MORNING +0.184R [+0.177, **-0.106**],
  RTH_AFTERNOON +0.149R [+0.127, +0.086], LUNCH +0.055R [**+0.420**, +0.249],
  RTH_CLOSE +0.027R [+0.071, **-0.126**], RTH_OPEN +0.099R [**-0.047**, +0.143].
  No session ordering survives all three paths.
* **By regime** — RANGE +0.093R [+0.190, **-0.049**], TREND_DOWN +0.188R
  [+0.096, +0.097], TREND_UP +0.002R [**-0.160**, +0.196]. Sign flips on two of
  four labels.
* **By volatility** — HIGH +0.107R [+0.111, +0.062], NORMAL +0.104R [+0.168,
  **-0.068**], LOW **-0.387R** [-0.373, +0.060] on 28–47 trades. The only
  consistent reading is that the LOW-volatility bucket is too small to read.
* **By direction** — LONG +0.096R [+0.054, +0.055], SHORT +0.095R [+0.182,
  **-0.027**]. Symmetric, as it should be on symmetric synthetic data.
* **Duration, MAE, MFE** — best individual rule sets hold 105–181 minutes with
  average MAE 0.36–0.65R and average MFE 0.62–1.06R. MFE exceeding MAE is the
  only structurally encouraging number in the whole sweep, and it is present on
  losing paths too.

**Best individual rule set on each path, and why none is published:**

| seed | rule set | tf | trades | expectancy | t | PF | max DD | max consecutive losses |
|---|---|---|---|---|---|---|---|---|
| 11 | `opening_range_breakout + range_position_extreme + rsi_…` | 60m | 89 | +0.133R | **1.80** | 1.67 | 3.2R | 5 |
| 2 | `opening_range_breakout + rsi_directional + vwap_reclaim` | 15m | 36 | +0.331R | **2.71** | 2.97 | 2.0R | 2 |
| 5 | `opening_range_breakout + range_position_extreme + rsi_…` | 15m | 155 | +0.105R | **1.38** | 1.29 | 6.4R | 5 |

Searching 80 rule sets buys `sqrt(2 ln 80) = 2.960` t-units for free, and 23
effective (distinct) trade sets buys `sqrt(2 ln 23) = 2.504`. **Every one of
these t-statistics is below both thresholds.** Not one best-on-path strategy
clears deflation even against its own family's search, before any of the wider
counting below.

### OPENING_RANGE: what is actually starving it

`OPENING_RANGE` rule sets generate **1 to 3 signals in 120 trading days**, and
`signals == trades` in every case — nothing is being blocked by an open
position, the rules simply never fire. I tested the obvious explanation
directly, same rule sets, seed 11, three time scopes:

| scope | signals | trades | rule sets trading | ≥30 trades | busiest | expectancy | t | PF |
|---|---|---|---|---|---|---|---|---|
| as generated (`opening_drive_window` + `max_minutes_since_open=150`) | 29 | 29 | 19 | **0** | 3 | +0.983R | 6.52 | 10.35 |
| `opening_drive_window` dropped | 35 | 35 | 19 | **0** | 4 | +0.850R | 5.30 | 5.84 |
| window **and** the 150-minute scope cap dropped | 118 | 118 | 29 | **0** | 9 | **+0.192R** | 2.30 | 1.65 |

The 90-minute window filter costs only 21% of the signals; the 150-minute
scope cap costs a further four-fold. But **removing all time scoping still
leaves zero rule sets above 9 trades**, so the binding constraint is the
template's conjunction — one liquidity signal plus up to three optional signals
from other groups, all required to agree on direction, at a bar where a
*completed* opening range has been broken. Widening the clock will not fix
this family; only a shorter conjunction or far more history will.

Note the third row as well: as the sample grows from 29 to 118 trades, the
apparent expectancy falls from +0.983R to +0.192R and the profit factor from
10.3 to 1.65. That is the clearest single illustration in this file of why a
29-trade result is not a result.

---

## Stop geometry

The trend seat's finding, re-measured post-fix on every `fresh_zone_approach`
firing that resolves to a specific zone (692 firings on 5m, 190 on 15m, 15 on
60m; seed 11, 120 days). The stop is the one the strategy would actually place:
`entry = round_to_tick(close)`, `stop = entry - sign * max(mult * ATR,
min_stop_ticks * tick)`.

| | 5m | 15m | 60m |
|---|---|---|---|
| firings with a matched zone | 692 | 190 | 15 |
| median gap to the proximal edge | **0.550 ATR** | 0.538 ATR | 0.510 ATR |
| median zone height | **0.825 ATR** | 0.770 ATR | 1.151 ATR |
| stop lands **inside** the zone @ 1.0xATR | **88.0%** | 82.6% | 93.3% |
| stop lands inside the zone @ 1.2xATR | **72.4%** | 60.5% | 80.0% |
| stop lands inside the zone @ 1.5xATR | 34.3% | 23.7% | 60.0% |
| stop lands inside the zone @ 2.5xATR | 0.3% | 1.1% | 0.0% |
| stop stops **short of** the zone | **0.0%** | 0.0% | 0.0% |

Three things follow.

**The finding holds and is worse post-fix.** 71% at 1.0x and 47% at 1.2x
pre-fix (trend seat) become 88.0% and 72.4%. The trigger distance is unchanged
(0.550 vs 0.557 ATR), so the whole movement is the zones getting taller: the
fix admits bases up to `1.0x` rather than `0.8x` of the trailing average range,
and a taller zone swallows more stops. **Relaxing the base ceiling made the
stop-geometry defect strictly worse.**

**It is a stop defect, not a signal defect.** The stop is *never* short of the
zone — 0.0% at every multiple on every timeframe. The geometry is entirely
"inside" versus "through". Since the condition fires at ~0.55 ATR of gap and
the zone is ~0.83 ATR tall, arriving beyond the far edge needs ~1.38 ATR of
stop; the combinator's four ATR exits are 1.0, 1.2, 1.5 and 2.5, so exactly two
of them can clear a median zone and only one clears it comfortably. This is
predictable from the two medians without running a backtest at all.

**No `StopKind` reads a zone level, and that is the blocker — but it is a
one-member blocker, not an architectural one.** `StopKind` is
`{ATR, STRUCTURE, FIXED_TICKS, VWAP_BAND, RANGE}`. None consults `SDZone`.
`ExitModel.stop_price()` however already receives `snap` and computes
`s = snap.tf(tf)`, and `s.sd_zones` is right there — the same list the
condition matched on. So a zone-aware stop is a new enum member and an
`elif` branch, not a plumbing change. I did not add it (my brief is not to edit
source). I simulated it in the harness with an `ExitModel` subclass placing the
stop at `SDZone.distal ∓ 4 ticks` on the zone the condition matched, falling
back to the strategy's own ATR stop when no zone matched.

### Does the zone-aware stop help? No.

72 SUPPLY_DEMAND rule sets containing `fresh_zone_approach` with an ATR exit,
seed 11, 120 days, identical entries, only the stop changed:

| | ATR stop (as generated) | zone-aware stop |
|---|---|---|
| trades | 139 | 136 |
| win rate | 43.9% | **37.5%** |
| expectancy | **-0.088R** | **-0.267R** |
| t-statistic | -1.06 | -3.49 |
| profit factor | 0.82 | 0.52 |
| max drawdown | 33.9R | 48.3R |
| avg win / avg loss | +0.918R / -0.875R | +0.771R / -0.890R |
| avg MAE / avg MFE | 0.794R / 0.851R | 0.841R / 0.749R |
| mean risk | 39.1 pts | 37.1 pts |
| rule sets ≥30 trades | **0** | **0** |

The zone-aware stop is not materially wider (37.1 vs 39.1 points) — placing it
at the distal edge plus a pad gives a median risk of 1.46 ATR (5m), which sits
between the 1.2x and 1.5x ATR exits. It is *differently placed*, not wider, and
it is placed worse: MFE falls and MAE rises, meaning the same trades now risk
more to capture less. **Neither leg clears the 30-trade floor, so this is
published as a diagnostic and nothing is ranked on it.** The honest reading is
that the stop-geometry defect is real and the obvious repair is not the answer;
what the geometry actually implies is that a 1.0x or 1.2x ATR stop should not
be *offered* to a zone-reaction strategy in the first place.

---

## Trade-level overlap with sweeps

The liquidity seat measured `zone_touch` against the sweep conditions at
trigger level: Jaccard 0.020, P(sweep | zone_touch) 5.7%, "not a relabelling" —
but flagged that 18.6% (5m) and 40.3% (15m) of zone touches had a sweep within
±3 bars, so trigger-bar disjointness understates trade overlap. Measured on
realised fills, that flag was right.

Matched probes: one signal condition each, **identical** exits
(`ATR x1.5 -> 1/2/3R`, 50/30/20 scale-out, breakeven at 1R, 60-bar time stop),
identical filters (`volatility_normal`, `volume_not_thin`, `rth_only`),
identical timeframe. Sweeps pooled = `prior_day_sweep` + `overnight_sweep` +
`session_extreme_sweep`. Seed 11, 120 days.

| | 5m | 15m | 60m |
|---|---|---|---|
| `zone_touch` trades | 120 | 31 | 17 |
| sweep trades (pooled) | 301 | 297 | 268 |
| — of which `prior_day` / `overnight` / `session_extreme` | 118 / 183 / 0 | 90 / 118 / 89 | 75 / 95 / 98 |
| **entry-bar Jaccard** | 0.0244 | 0.0229 | 0.0319 |
| identical entry bar | 10 | 7 | 8 |
| zone_touch trades with **any** sweep entry within ±3 bars | 65.0% | 83.9% | 94.1% |
| …with a **same-direction** sweep entry within ±3 bars | **34.2%** | **61.3%** | **82.4%** |
| **same-direction time-in-market overlap** | **15.0%** | **25.4%** | **48.6%** |
| (overlapping bars / zone_touch bars in market) | 185 / 1232 | 161 / 633 | 193 / 397 |

**Conclusion.** Trigger-bar Jaccard understates trade overlap by roughly an
order of magnitude, and the understatement grows with timeframe. On 5m the two
families are genuinely mostly different trades — 15% of position time shared —
and the liquidity seat's "not a relabelling" verdict stands. On 15m a quarter
of position time and three fifths of entries are shared. **On 60m, 82% of
`zone_touch` entries have a same-direction sweep entry within ±3 hourly bars and
48.6% of position time is shared; at that timeframe treating them as
independent evidence in a confluence would count one observation twice.** That
is a per-timeframe conclusion, not a per-condition one, and it is the kind of
thing only a realised-fill measurement shows.

For completeness, and **below the floor in two of three cases so not ranked**:

| | `zone_touch` | sweeps pooled |
|---|---|---|
| 5m | 120 trades, -0.012R, PF 0.97, t -0.13 | 301 trades, +0.019R, PF 1.04, t +0.31 |
| 15m | 31 trades, +0.150R, PF 1.43, t +0.86 | 297 trades, +0.022R, PF 1.05, t +0.37 |
| 60m | 17 trades, -0.172R, PF 0.56, t -0.97 | 268 trades, -0.002R, PF 1.00, t -0.03 |

The 15m `zone_touch` probe at +0.150R over 31 trades is exactly the kind of
number this desk exists to refuse. It clears the trade floor by one trade. Its
t-statistic is 0.86. The probe set alone is 12 hypotheses
(4 conditions x 3 timeframes), and `sqrt(2 * ln 12) = 2.229` t-units of
apparent significance are free from searching that alone — so the deflated
t-statistic is `0.86 - 2.23 = -1.37` and the deflated expectancy is **0**. It is
noise, and it is reported here only because burying it would be worse.

---

## What I withheld and why

**Everything.** Nothing from these three families is published as live-eligible
or entered into any ranking. Specifically:

| withheld | why |
|---|---|
| **The entire SUPPLY_DEMAND family** | 0 of 192 rule sets (count run) and 0 of 80 (sweep, x3 paths) reach 30 trades in 120 days; the busiest takes 11 and 21 respectively. Pooled expectancy flips sign across paths (-0.256 / -0.276 / +0.281R). Walk-forward produced **0 out-of-sample trades** on two of three paths and 6 on the third. `MIN_TRADES_FOR_RANK = 30` and I do not publish below it. **Untested, not disproven** — the same verdict the previous seat reached, now for a quantified reason rather than an unmeasured one. |
| **The entire OPENING_RANGE family** | 0 of 80 rule sets reach 30 trades on any path; 13–29 trades in total per path; best rule set 3 trades. Walk-forward produced **0 out-of-sample trades on all three paths**. Removing every time filter still leaves the busiest rule set at 9 trades. |
| **The entire LIQUIDITY family, including the one path that passed every gate** | It is the only family with a real sample (2,378–2,584 trades per path, 25–28 rule sets over the floor), and seed 2 returned `is_credible = True`, efficiency 0.97, P(ruin) 0.0% and deflated expectancy +0.071R. It does not replicate: the same procedure gives -0.121R on seed 11 (P(ruin) **95.6%**) and +0.082R on seed 5 (P(ruin) 9.5%). Between-path t = 0.67. Two of three paths fail the 2% ruin gate outright. |
| **Every best-on-path LIQUIDITY rule set** | Best t-statistics were 1.80, 2.71 and 1.38 against `sqrt(2 ln 80) = 2.960` free t-units from that family's own search alone — and 2.504 even at the generous 23-distinct-trade-set count. None clears deflation at any counting. |
| **The 15m `zone_touch` probe (+0.150R, PF 1.43, n=31)** | Clears the floor by one trade; deflated expectancy 0 against 12 hypotheses, never mind the ~392 I actually searched. |
| **The zone-aware-stop comparison** | 139 and 136 trades pooled across 72 rule sets — no individual rule set clears 30. Published as a diagnostic, ranked on nothing. |
| **Any 60m zone statistic quoted precisely** | 21–35 zones per 120 days across six paths; ±16% dispersion on the count itself. |
| **Anything about MES, MGC or CL** | Not tested. MNQ's numbers are MNQ's. |
| **Any claim that this is a live edge** | Synthetic data. The generator is explicitly built to contain no exploitable pattern. |

### Anti-overfitting checks: what I looked for and what I found

| check | result |
|---|---|
| **Look-ahead bias** | **Clean.** 558 prefix-vs-`as_of` zone comparisons (5m/15m/60m) and 139 `active_zones`-vs-prefix comparisons, **0 mismatches**. Entries fill at the next bar's open with adverse slippage; the signal bar's close is never traded. |
| **Repainting indicators** | **Clean.** A zone's top, bottom, touch count and invalidation state at bar *i* are identical whether the series ends at *i* or continues. `SDZone.as_of()` masking verified against a truncated-series recomputation, not just asserted. |
| **Future-data leakage** | **Clean**, same evidence. The post-fix detector's forward touch/invalidation scan is masked at every consumer I exercised. |
| **Data-mining bias** | **Found and priced.** Arithmetic below. One result survives deflation at every counting (seed 2 LIQUIDITY OOS, +0.014R even against 1,644 runs) — and fails replication, which is the point: deflation is necessary and not sufficient. |
| **Insufficient sample size** | **Found — this is the dominant failure mode of two of my three families.** SUPPLY_DEMAND cannot reach 30 trades at 120 days on any timeframe I tested (best: 21). OPENING_RANGE fires 1-3 times in 120 days and still cannot reach 10 with every time filter removed. |
| **Selection / walk-forward false positive** | **Found.** On synthetic data with no exploitable structure, the walk-forward plus robustness gate returned `is_credible = True`, efficiency 0.97, P(ruin) 0.0% and positive deflated expectancy for LIQUIDITY on 1 of 3 independent price paths. Only cross-path replication caught it. |
| **Unstable slice conclusions** | **Found.** Session, regime and volatility slices of the LIQUIDITY sample flip sign between price paths on 2 of 5 session buckets, 2 of 4 regime labels and 1 of 3 volatility labels. No slice-level claim is made. |
| **Parameter sensitivity** | **Partially tested.** Stop multiplier swept 1.0 / 1.2 / 1.5 / 2.5 ATR plus a zone-aware variant; detector parameter swept 0.8 / 1.0. I did **not** run `robustness.parameter_sensitivity()` per strategy, because it is defined relative to a baseline expectancy and no strategy cleared the floor to be a baseline. |
| **Unrealistic fills** | **Checked, and I made them slightly worse.** Engine fills entries at the next bar's open, applies adverse slippage to entry and stop, resolves stop-before-target within a bar, and gaps fill at the open. Running on a 5-minute base coarsens intrabar resolution, which under "stop wins" is conservative. |
| **Understated costs and slippage** | **Checked.** `CostModel` default is 0.5 tick entry slippage, +1.0 tick on stops, a volatility term above the median ATR percentile, and $0.72/side all-in on MNQ — $2.44 round turn. At the 39.1-point mean risk of my zone strategies that is **0.031R per trade**; at a 10-point stop it would be 0.122R. My families take wide stops, so cost is not what killed them — the sample size is. |
| **Survivorship bias** | **Not applicable.** One synthetic contract, no universe selection, no delisting. |
| **Seed / regime dependence** | **Tested on six independent price paths.** Detector-level findings replicate; trade-level findings are too small to test for replication, which is itself the finding. |

### Deflation arithmetic, shown

Expected maximum t-statistic from searching *n* independent hypotheses is
`sqrt(2 ln n)`; that much apparent significance is free.

| what I searched | n | `sqrt(2 ln n)` free t-units |
|---|---|---|
| the matched overlap probes | 12 | 2.229 |
| one family's effective (distinct) trade sets in the sweep | 23 | 2.504 |
| one family's nominal walk-forward pool | 80 | 2.960 |
| one seed's full generation (3 families x 4 timeframe cells) | 240 | 3.311 |
| the SUPPLY_DEMAND count run | 192 | 3.243 |
| three seeds' generations | 720 | 3.627 |
| **every distinct rule set this seat evaluated** | **≈392** | **3.456** |
| …counting detector variants, exit variants, scope variants and seeds as separate runs | ≈1,644 | 3.849 |

The ≈392: 220 distinct SUPPLY_DEMAND rule sets (192 from the count run plus the
stop-geometry and sweep generations, which overlap it) + 80 LIQUIDITY + 80
OPENING_RANGE + 12 overlap probes. The ≈1,644 runs: 384 (192 x 2 detectors) +
144 (72 x 2 exits) + 12 probes + 240 (80 x 3 time scopes) + 720 (240 x 3 seeds)
+ 144 (48 x 3 seeds, first pass).

**Applied to the only result that got anywhere.** Seed 2's LIQUIDITY
out-of-sample series: n = 190, expectancy +0.2608R, t = 4.060, so
`std_r = 0.2608 x sqrt(190) / 4.060 = 0.885` and
`std_r / sqrt(n) = 0.0642`. Deflated expectancy is
`(t - sqrt(2 ln trials)) x 0.0642`:

| trials counted | free t | deflated expectancy |
|---|---|---|
| 23 (that family's effective trade sets) | 2.504 | +0.100R |
| 80 (that family's nominal pool — what the tooling reported) | 2.960 | +0.071R |
| 240 (one seed's whole generation) | 3.311 | +0.048R |
| 720 (three seeds) | 3.627 | +0.028R |
| 392 (this seat's distinct rule sets) | 3.456 | +0.039R |
| **1,644 (every run this seat made)** | **3.849** | **+0.014R** |

So it survives deflation at every counting — which is exactly why deflation is
not sufficient on its own. **What kills it is replication**: -0.121R and
+0.082R on the two other paths, a between-path t of 0.67, and a 95.6%
probability of ruin on the worst of them. Every *other* floor-clearing result
in this file has a t-statistic below its own family's free-t threshold, so
every other `deflated_expectancy_r` I report is 0.

---

## Measurements

All scripts under
`/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad/sdliq/`.
Synthetic data only; MNQ only; seeds 2, 3, 5, 7, 11, 13 (detector level) and
11, 2, 5 (strategy level); `end_date=2026-03-17`; 120 trading days each.

| script | what it measures | output |
|---|---|---|
| `m1.py` | zones per timeframe and condition firing rates, old vs new detector, same path | `m1_seed11.json` |
| `m5_count.py` | 192 SUPPLY_DEMAND rule sets, old vs new detector, trade counts and floor clearance | `m5_seed11.json` |
| `m6_bias.py` | which base ceiling binds; re-run of the accept loop with both instrumented | `m6_seed11.json` |
| `m6b_prefix.py` | prefix stability / repaint / look-ahead, raw detector and `active_zones` | `m6b_seed11.json` |
| `m7_follow.py` | follow-through after `fresh_zone_approach`, old vs new | `m7_seed11.json` |
| `m8_rep.py` | six-seed replication of zone counts, firing rates, trigger distance | `m8_rep.json` |
| `m3_stop.py` | stop-inside-zone geometry; zone-aware `ExitModel` subclass A/B | `m3_seed11.json` |
| `m4_overlap.py` | realised-fill overlap, `zone_touch` vs the three sweep conditions | `m4_seed11.json` |
| `sweep.py` | family sweep across 5 / 15 / 60 and 5+15+60, anchored walk-forward per family, Monte Carlo ruin | `big_s{11,2,5}.json` (240 rule sets/seed), `out_s{11,2,3}.json` (first pass, 48) |
| `m9_orwindow.py` | is OPENING_RANGE starved by its clock or its conjunction | `m9_seed11.json` |

Machinery reused unmodified: `strategies/combinator.py` (`generate_strategies`),
`backtest/engine.py` (`BacktestEngine.run_many`), `backtest/metrics.py`
(`compute_metrics`, `slice_metrics`), `backtest/walkforward.py`
(`walk_forward`, anchored), `backtest/robustness.py` (`deflated_expectancy`),
`backtest/montecarlo.py` (`risk_of_ruin`), `indicators/structure.py`
(`supply_demand_zones`). Floors from `agents/research.py`
(`MIN_TRADES_FOR_RANK = 30`).
