# Confluence layer review - mean-reversion & liquidity seat

Reviewer: research_reversion / research_liquidity (the fade desk and the stop-run desk).
Scope: the six new condition groups at `f82bd35`, judged from reversion and liquidity only.
Trend and quantitative angles are owned by the other two reviewers and are not covered here.

**Measurement basis.** All figures below are MNQ synthetic, `seed=5`, 15-day and 60-day
windows, evaluated once per completed timeframe bar unless stated. Forward tests use a
symmetric 1.0 ATR barrier over the next 60 one-minute bars, plus the 60-minute close-to-close
move in ATR units.

**The caveat that governs every win rate I quote.** `futures_agents/data/loader.py` states in
its module docstring that the generator is deliberately a near-martingale with "no mechanical
edge baked in". My baselines confirm it: always-long over 16,534 completed 5m bars wins
49.9% of barrier races at meanFwd +0.011 ATR; at 15m, 50.2% and +0.003 ATR. **No win rate in
this document is evidence about real markets, mine or anybody's.** What the data *can* settle,
and what I have therefore leaned on, is: firing rates, sample sizes, geometry, set overlap, and
whether a condition's own claim matches what it measures. Where I use a win rate at all I use
it against a matched control on the same data, never on its own.

---

## Verdict

**Partly. The layer serves coverage; it serves reversion and liquidity unevenly, and it did
not close the gap that actually constrains my two seats.**

What works: `value_area_edge` is a correctly specified responsive trade and its ATR band is
doing real work (F4). `zone_touch` is genuinely not a relabelling of the existing sweeps, and
there is a structural reason, not just a measured one (F7). The supply/demand detector's
invalidation logic is airtight (F8).

What does not: `poc_reversion` has a distance floor and no ceiling, so 61.6% of its signals
point at a magnet that is unreachable inside an hour, and its confidence score is monotonically
*wrong* in distance (F1, F2). `lvn_rejection` draws from a node set that is empty in 34 of 59
prior-day profiles because the LVN detector is sited inside the value area, and its direction
rule carries no information at n=629 (F5, F6). Six of the twenty-one new conditions - including
both of the "do not initiate here" filters in my territory - cannot be selected by any template,
so the coverage map asserts tradeability the generator cannot deliver (F10). `post_news_window`
removes 98.8% of a MOMENTUM strategy's trades and yields zero publishable strategies on any
window this desk currently runs (F11).

And the largest miss: the desk now has **six new location conditions and still zero
location-referenced exits** (F12). Every one of them publishes a reference price that the exit
machinery is structurally unable to trade to. For a reversion seat that is not a detail - a fade
is defined by its reference, and this layer added references without adding the ability to use
them.

---

## Findings

### F1. `poc_reversion` has a distance floor and no ceiling, and most of its firing mass sits where its own thesis cannot complete

**Claim.** The condition is specified as a rotation to the POC but does not bound how far the
POC may be. On the larger sample it fires on 43.54% of completed 5m bars, at a *median* 3.95
ATR from the POC, and the POC is reached within the next 60 minutes only 19.4% of the time.

**Evidence.** `futures_agents/strategies/library.py`, `_poc_revert`: the only distance test is
`if abs(gap) < atr_v * 0.5: return ConditionResult.no()`. There is no upper bound.
Measured, 60 days, one eval per completed 5m bar (n=16,534 evaluable):

| | fires | mean dist to POC | median | >3 ATR away | POC reached in 60m | barrier win |
|---|---|---|---|---|---|---|
| `poc_reversion` as shipped | 7,199 (43.54%) | 5.67 ATR | 3.95 ATR | 61.6% | 19.4% | 50.2% (3486/3458/255) |

Baseline always-long on the same bars is 50.8-49.9%. Direction split was 3,055 long / 4,144
short (58% short); at 15 days / 5m the skew was starker (469/1,289, 73% short). I checked
whether the value-area construction biases this - see F14 - and it does not; I therefore record
the skew as a property of this sample, not of the code.

**Confidence: high** on the geometry, which is a structural property of using a stale daily
reference. Medium on the direction skew, which is sample-dependent.

---

### F2. "Inside value" is the right precondition - but only inside about 2 ATR. The brief's proposed alternative measures worse, not better

This is the question I was asked to settle, and the answer went against my prior.

**Claim (a): the precondition earns its place, but only at short range.** Splitting POC-touch
rate by distance band, with "outside value" as the matched control at the same distance
(15 days, 5m):

| distance to POC | INSIDE value: POC reached in 60m | OUTSIDE value: same |
|---|---|---|
| 0.5 - 2.0 ATR | **56.5%** (n=322) | 37.7% (n=53) |
| 2.0 - 4.0 ATR | 19.7% (n=355) | 20.5% (n=161) |
| 4.0 - 7.0 ATR | 6.7% (n=210) | 1.8% (n=385) |
| 7.0 - 12.0 ATR | 0.0% (n=113) | 1.1% (n=623) |

Inside-value beats outside-value by 19 points in the near band and by nothing at all from 2 ATR
out. The precondition is correct; the missing ceiling is what makes it useless across most of
its range.

**Claim (b): the "starts just outside value and returns in" rotation is not higher probability.**
I implemented it and measured it (15 days, 5m): prior 5m close outside the prior value area,
this close back inside, direction toward the POC.

| alternative | n | barrier win | meanFwd |
|---|---|---|---|
| ALT `value_area_return` (out -> in, toward POC) | 34 | 48.5% | +0.003 ATR |
| ALT `value_area_reject` (in -> out, continue away) | 33 | 60.6% | +0.600 ATR |
| ALT outside value, fade toward value | 2,644 | **44.3%** | **-0.406 ATR** |
| excluded by the 0.5 ATR floor (already at the POC) | 151 | 51.4% | -0.171 ATR |

The re-entry trade is a coin flip on a sample too small to publish. The *opposite* read - poke
into value, close back out, continue away - was the better of the two, and the large-sample
version of my own thesis (fade from outside value back toward value, n=2,644) is decisively
negative at 44.3% / -0.406 ATR. **I cannot support changing the precondition. I asked the data
for my preferred answer and it said no.**

Note for the pooling: that n=2,644 result is the mirror image of `value_area_breakout`
(15 days, 5m: 55.7% win, +0.410 ATR, n=2,614). They are one fact stated twice - once price is
outside the prior value area, continuation beat reversion over the next hour on this sample.
It should be counted once.

**Confidence: high** that the brief's alternative is not supported here. **Medium** that it is
wrong in general - 34 signals in 15 days is exactly the sample size F6 warns about.

---

### F3. The prior *trading day* profile is the wrong reference by the library's own standards - but changing it to RTH-only does not fix the trade

**Claim.** `prior_session_profile` builds from every bar of the prior trading day, Globex
included, while the sweep conditions on the very same snapshot reference RTH-only levels. That
is an internal inconsistency. Switching to an RTH-only profile measurably improves the
*geometry* and does not improve the *outcome*.

**Evidence (code).** `futures_agents/features.py`, `_build_session_index` maps bars by
`trading_day(bar.ts)` with no RTH test, and `prior_session_profile` feeds
`self._day_bars[self._day_order[position - 1]]` straight into `volume_profile`. Meanwhile
`_build_session_state` computes `prev_day_high` / `prev_day_low` from `rth_high` / `rth_low`
under an explicit `is_rth(...)` guard, and keeps `on_high` / `on_low` separate. So
`prior_day_sweep` and `poc_reversion` on the same bar are quoting two different definitions of
"yesterday".

**Evidence (measured).** 14 comparable days, 15-day window:

- 24h value area is **1.42x wider** than RTH-only (216.70 vs 152.43 points).
- Median |POC_24h - POC_RTH| = 9.80 points; only 7 of 14 POCs agree within one 24h bin.
- **11.0%** of 5m closes get a different "inside value" answer from the two profiles
  (424 of 3,864).

Re-running the exact `poc_reversion` rule against an RTH-only profile, 60 days, 5m:

| profile | fires | mean dist | POC reached in 60m | >3 ATR | barrier win | meanFwd |
|---|---|---|---|---|---|---|
| 24h (shipped) | 43.54% | 5.67 ATR | 19.4% | 61.6% | 50.2% | -0.047 ATR |
| RTH-only | 28.02% | 4.37 ATR | **23.2%** | 53.6% | 47.7% | -0.150 ATR |

The RTH profile is the tighter, more faithful reference on every geometric measure. The trade
outcome is not better (and on martingale data the 2.5-point win-rate difference is not
evidence either way). **My position: fix the inconsistency because it is an inconsistency, not
because it will make money. The defect that costs something is the missing distance ceiling in
F1, not the session definition.**

**Confidence: high** on the geometry and the code inconsistency. **Low** that the change alters
profitability.

---

### F4. `value_area_edge` is correctly specified and its 0.35 ATR band is not decorative

**Claim.** The tag-and-close-back-inside event happens on 1.71% of completed 5m bars; the band
cuts that to 0.93% and removes the measurably worse half. It is the one new profile condition I
would leave alone.

**Evidence.** 15 days, 5m, n=3,852 evaluable bars:

| | n | % of bars | barrier win | meanFwd |
|---|---|---|---|---|
| tag VAL/VAH + close back inside, **no band** | 66 | 1.71% | 48.4% | -0.125 ATR |
| ... band kept these (the registered rule) | 36 | 0.93% | 51.4% | +0.125 ATR |
| ... band **rejected** these | 30 | 0.78% | 44.8% | -0.424 ATR |

The band is the difference between a bar that closed back just inside the edge and one that
closed 2 ATR inside it - the latter is a reversal bar that has already made its move, and the
band correctly refuses it. 36 signals per 15 days at 5m is ~2.4/day, so this condition clears
the 30-trade floor in roughly 13 trading days. It is one of the few new conditions with no
sample problem.

**Confidence: medium-high.** The direction of the band's effect is consistent and mechanically
sensible; the n=36/n=30 split is itself thin, so I claim the band is *doing something*, not
that it is optimally sized.

---

### F5. `lvn_rejection` is looking for low-volume nodes in the one place they structurally cannot be

**Claim.** The LVN detector restricts candidates to *inside* the value area. In profile trading
the tradeable low-volume node is the gap between distributions, which by definition sits outside
a single 70% value area. The consequence is measurable: 34 of 59 prior-day profiles contain
**zero** LVN nodes.

**Evidence (code).** `futures_agents/indicators/volume.py`, `volume_profile`:

```
lvn = [prices[k] for k in range(bins)
       if hist[k] <= mean_vol * 0.4 and low_i <= k <= high_i]
```

`low_i` and `high_i` are the value-area bounds. A bin that holds 40% of mean volume *and* sits
inside the region holding 70% of total volume is close to a contradiction; the `hvn` list next
to it carries no such restriction (mean 9.86 nodes/profile at 15 days).

**Evidence (measured).** 60 days: mean 1.78 LVN nodes per profile, zero-LVN profiles 34/59.
Share of 5m bars whose prior profile has any LVN at all: 41.7%; at 15m, 30.1%. The firing rate
is also wildly unstable across samples - 0.31% of 5m bars over 15 days versus 2.70% over
60 days, an 8x swing, because it depends on whether ~1.8 nodes per profile happen to land near
traded prices.

**Confidence: high.** This is a code-level siting error with a directly measured consequence.

---

### F6. The direction inference in `lvn_rejection` carries no information - and the 15-day sample said the opposite, convincingly

This is the question about whether the LVN is "a momentum bar with extra steps". **It is
neither.** A momentum bar is not a step, because the bar's own body direction predicts nothing
at all; and the LVN location adds nothing to it.

**Evidence.** 60 days, in each condition's own stated direction, against a matched control of
*every* bar traded in its own body direction:

| | n | barrier win | meanFwd |
|---|---|---|---|
| `lvn_rejection` 5m (body direction) | 446 | 47.9% | +0.067 ATR |
| same, direction **flipped** | 446 | 52.1% | -0.067 ATR |
| CONTROL: all 5m bars, body direction | 15,863 | 50.0% | +0.045 ATR |
| `lvn_rejection` 15m | 183 | 51.1% | +0.041 ATR |
| CONTROL: all 15m bars, body direction | 5,276 | 50.6% | +0.027 ATR |

Pooled across both timeframes that is 629 signals with no separation from a control of 21,139.
I also tested a second direction rule - traversal measured against the prior bar's close rather
than the bar's own open - and it returned *identical* numbers at both timeframes, meaning the
two rules agree on every trigger bar. There is no better direction available from the bar alone.

**The sample-floor lesson, concretely.** On the 15-day / 5m window this same condition returned
**10 wins from 13 at +2.088 ATR mean - a 76.9% win rate with a one-sided binomial p of 0.042
against the control rate.** It would have passed a naive significance test. At n=446 it is
47.9%. This is the cleanest demonstration I have that `MIN_TRADES_FOR_RANK = 30` is a floor and
not a target: 13 was enough to look significant, and 30 would not have been enough to be safe.

**Confidence: high.** The direction rule is the weakest in the new set, as the brief suspected,
but the reason is not that it is momentum - it is that nothing about the bar predicts anything,
so any rule built from the bar alone inherits that.

---

### F7. `zone_touch` is **not** `prior_day_sweep` by another name, and the reason is structural

**Claim.** The overlap on trigger bars is near zero, and it is near zero by construction: the
two conditions are testing mutually near-exclusive bar shapes.

**Evidence (code).** `_level_sweep` fires on `b.low < low_level <= b.close` - a wick *through*
a level and a close back *out*. `_zone_touch` fires on `z.contains(px)` - the **close inside**
the zone. A bar that closes inside a zone has by definition not closed back out of it.

**Evidence (measured).** 60 days, trigger-bar sets:

| | 5m | 15m |
|---|---|---|
| `zone_touch` triggers | 366 | 72 |
| any sweep (`prior_day` / `overnight` / `session_extreme`) | 680 | 401 |
| intersection | 21 | 5 |
| P(sweep \| zone_touch) | 5.7% | 6.9% |
| P(zone_touch \| sweep) | 3.1% | 1.2% |
| Jaccard | 0.020 | 0.011 |
| of the intersection, same direction | 14/21 | 2/5 |

**The "new confluence is a relabelling" charge does not stand.** I would have filed it if the
numbers supported it; they do not.

**Confidence: high.**

---

### F8. ...but trigger-bar disjointness understates *trade* overlap, and the pooling test must use realised fills

**Claim.** Zone touches and sweeps do not coincide on the bar, but they cluster in time closely
enough that positions opened by each will overlap.

**Evidence.** 60 days: zone touches with a sweep within +/-3 bars - **18.6%** at 5m, **40.3%**
at 15m (+/-45 minutes); within +/-5 bars, 22.4% and 48.6%. With `time_stop_bars` of 30-120 in
`expand_exit_models`, a 15m zone-touch trade and a 15m sweep trade opened three bars apart are
concurrently open most of the time.

**Consequence for the debate.** When `SUPPLY_DEMAND` findings are pooled against `LIQUIDITY`
findings, the correct instrument is `trade_overlap` from `agents/debate.py` on realised fills -
the same call I use in cross-examination - and **not** the trigger-bar disjointness in F7. I am
filing the caveat against my own favourable finding because it is the objection I would raise
against a rival.

**Confidence: high** on the clustering measurement; **medium** on how much realised overlap it
produces, which I have not measured directly.

---

### F9. `away_from_zone` is not a weak filter. It is an unreachable one - and if it were reachable it would be a null hypothesis

Two separate answers to "is a filter that passes 97.5% of the time worth the hypothesis it
costs".

**(a) It currently costs zero hypotheses, because nothing can select it.** See F10. Filters
reach a strategy only through `template.base_filters` or `template.optional_filters` in
`_filter_sets`; `away_from_zone` appears in neither. Empirically: 5,993 generated strategies use
59 of the 73 registered conditions, and `away_from_zone` is not among them. The
`SUPPLY_DEMAND` template's `exclusive=(("zone_touch", "away_from_zone"), ("fresh_zone_approach",
"away_from_zone"))` is dead code twice over - `_violates_exclusive` is only applied to signal
names, and a FILTER can never appear there.

**(b) If it were wired in, it would change almost nothing.** I hand-built it onto every
REVERSAL / LIQUIDITY / SUPPLY_DEMAND strategy that trades and re-ran 60 days:

| | without | with `away_from_zone` |
|---|---|---|
| total trades (24 strategies) | 888 | 877 |
| trades removed | - | 11 (**1.24%**) |
| strategies with an identical trade count | - | **18 of 24** |
| median \|delta\| / max \|delta\| | - | 0 / 4 trades |
| strategies clearing the 30-trade floor | 4 | 4 |
| expectancy delta over the 4 comparable pairs | - | mean **+0.0057R**, max \|delta\| 0.0194R |

**Verdict: not worth it.** Wiring it into `optional_filters` doubles the generated universe for
those templates - and therefore the deflation every *other* strategy in the pool pays - in order
to distinguish outcomes that differ by 0.006R on 1.2% of trades. Either delete it or demote it
to a reported diagnostic.

One caveat on reading any "retained fraction": trade counts are **not** monotone in filters,
because `max_concurrent_per_strategy=1` means blocking one entry can free the strategy to take a
different one later. I observed exactly this - adding `outside_news_blackout` moved one
strategy's count from 1,515 to 1,516.

**Confidence: high.**

---

### F10. Six of the twenty-one new conditions cannot be selected by any template, and `coverage.py` is structurally unable to notice

**Claim.** The coverage map checks registration, not reachability, so it certifies conditions
the generator can never use.

**Evidence (code).** `coverage.py::uncovered` tests only `n not in CONDITIONS`. But its own
docstring sets a higher bar: "Each entry names the registered conditions that make its variable
*tradeable* - not merely computed somewhere." By that standard six entries fail.

**Evidence (measured).** Of 5,993 strategies generated across all 13 templates and four
timeframes, only 59 of 73 conditions appear. Never selected:

`away_from_zone`, `away_from_hvn`, `open_outside_value`, `fib_sr_confluence`,
`no_recent_imbalance`, `oi_expanding`
(plus eight pre-existing ones: `adx_trending`, `efficiency_high`, `power_hour`,
`regime_matches_direction`, `relative_volume_high`, `volatility_expanding`, `volume_surge`,
`vwap_proximity`).

Three of the six are in my territory. `supply and demand` is mapped to three conditions of which
one is unreachable; `volume profile` to five of which one is; `market profile` to four of which
one is. The variables remain covered by their other conditions - this is a weakened claim, not a
false one - but "73 conditions" overstates the tradeable library by 14.

**Confidence: high.** Pure code fact, confirmed empirically.

---

### F11. `post_news_window` removes 98.8% of a MOMENTUM strategy's trades and yields zero publishable strategies on any window this desk runs

**My position, since the brief asks for one: it is a waste of a hypothesis *as wired*, and it is
not the only way to learn whether the post-release reaction is tradeable. Move it from a filter
to a slice.**

**Evidence.** 60 trading days, 50 MOMENTUM strategies that actually trade, each run bare and
then with each news filter appended - a genuine like-for-like control:

| variant | mean trades | median | max | strategies clearing 30 trades | retained |
|---|---|---|---|---|---|
| bare | 112.6 | 23 | 1,515 | **22 / 50** | - |
| `+ outside_news_blackout` | 112.6 | 23 | 1,516 | 22 / 50 | 99.99% |
| `+ post_news_window` | **1.5** | **0** | 16 | **0 / 50** | **1.19%** (max 6.25%) |

24 of 50 post-news variants took at least one trade. Extrapolating: the best variant runs 67.2
trades/year (0.4 years to reach the 30-trade floor); the median variant 8.4 trades/year
(**3.6 years**). The desk's own recent runs used a 20-day reduced sweep for rankings and
165,600 bars (~115 trading days) for robustness. **On both of those windows the answer is zero
publishable post-news strategies**, and every one of them will be withheld on the sample floor
exactly as the brief predicts.

**Why the "only way to learn" framing is false.** `backtest/metrics.py` already slices realised
trades by `regime`, `session`, `time_bucket`, `day_of_week` and more, each with a `min_trades`
floor, and `Trade` already carries the signal's `session` / `time_bucket` / `regime` context.
Adding a `news_phase` slice key (clear / pre-release / blackout / post-release), populated from
the `minutes_to_high_impact` and `minutes_since_high_impact` already on `FeatureSnapshot`,
answers "is the post-release reaction tradeable?" using all **112.6** trades per strategy
instead of 1.5, across the whole universe, at **zero** added hypotheses and zero added
deflation.

**The honest counter-argument, stated so it can be argued with.** A slice measures the
post-news expectancy of strategies that were not built for the post-news window; a filter
measures a strategy that only trades there. Those are different questions, and only the filter
answers the second. My answer: the second question cannot be answered at 1.5 trades per 60 days,
and the cost of asking it is paid by every other MOMENTUM strategy in the pool through
`trials_searched`. Ask the cheap question first; if the slice shows a post-release effect worth
chasing, *then* spend a dedicated template on it with its own sample expectations.

Separately: `outside_news_blackout` retains 99.99% of trades. It is equally null in the other
direction and costs the same doubling of the MOMENTUM universe.

**Confidence: high** on the measurement; **medium** on the recommendation, which trades one kind
of information for another.

---

### F12. Six new location conditions, and no exit that can reference a location

**This is the gap the release did not close, and it is the one that matters most to my seat.**

**Claim.** `poc_reversion`, `value_area_edge`, `lvn_rejection`, `zone_touch`,
`fresh_zone_approach` and the Fibonacci conditions all publish a reference price in
`ConditionResult.value`. Nothing in the exit machinery can use it.

**Evidence (code).** `strategies/base.py`:

```
def target_prices(self, entry, stop, direction, spec):
    risk = abs(entry - stop)
    sign = direction.sign
    return [spec.round_to_tick(entry + sign * risk * r) for r in self.targets_r]
```

Targets are pure R-multiples of the stop distance. `StopKind` offers ATR, STRUCTURE,
FIXED_TICKS, VWAP_BAND, RANGE - no profile or zone reference. `ConditionResult.value` reaches
only `Evidence(... value=res.value ...)`, which is reporting. Likewise `ConditionResult.strength`
is accumulated into `strength_sum / n_signals` and nothing in `backtest/` or `risk/` reads it.

**What that means for `poc_reversion` specifically.** It names the POC as its reason, at a median
3.95 ATR, and then trades a 1.5 ATR stop to 1R/2R/3R targets that have nothing to do with the
POC. **The condition never trades to the level it is named after.** Its economic content reduces
to "price is somewhere inside yesterday's value area; take the side that points at the POC."

**Related, and the sharpest single inconsistency I found.** `_poc_revert` sets
`strength = min(1.0, abs(gap) / atr_v / 2.0)` - confidence rises with distance, saturating at
2 ATR. The measured probability of completing the rotation *falls* with distance: 56.5% at
0.5-2 ATR, 19.7% at 2-4, 6.7% at 4-7, 0.0% beyond 7 (F2). The condition is most confident
exactly where it is least likely to be right. Because `strength` is reporting-only this misleads
a human reading a callout rather than the backtester - but a callout is what a human trades.

**Confidence: high.** Entirely code-verifiable.

---

### F13. The fade desk's own condition pool did not grow

**Claim.** Twenty-one new conditions and six new groups, and the `meanreversion` group is still
three conditions - all of them the same idea.

**Evidence.** `CONDITION_GROUPS["meanreversion"] = [bollinger_extreme, bollinger_mean_pull,
keltner_outside]`. Bollinger is SMA +/- k*stdev; Keltner is EMA +/- k*ATR. All three are "price
is far from its own moving average in volatility units". `MEAN_REVERSION`'s `required_groups` is
`("meanreversion",)`, so **every** mean-reversion strategy this desk generates is anchored on one
of those three. `bollinger_mean_pull` fires on 35.35% of 5m bars (15 days) - a third of all bars
- so the fade desk's anchor is close to always-on.

The combinator's diversity rule protects a *confluence* from being five restatements of one
idea. It cannot protect a *group* that is itself one idea, and the new layer put the only new
location-based reversion condition (`poc_reversion`) in `profile`, where the fade template can
draw it optionally but never anchor on it.

**Confidence: high** on the structure; **medium** on how much it costs, which I have not isolated.

---

### F14. A value-area asymmetry I measured at 15 days did not replicate at 60 - recorded because it is the same lesson as F6

I hypothesised that the value-area expansion loop in `volume_profile` biases the area upward
(`if above >= below: high_i += 1` - ties expand up), which would give `poc_reversion` a
structural short bias.

15 days, 14 profiles: mean (VAH-POC)/(VA width) = **0.588**, 8 of 14 wider above the POC.
60 days, 59 profiles: **0.506**, 30 of 59 wider above.

**It did not replicate. I withdraw the claim.** The short skew reported in F1 stands as an
observation about this sample and is not attributable to the tie-break.

**Confidence: high** that the tie-break bias is not real at this magnitude.

---

## What I would add

Four items, ordered by what I would spend the desk's time on first. Two of them I measured and
could not demonstrate value for; I have said so rather than dressing them up.

### A1. A distance ceiling on `poc_reversion`, and an inverted strength - *highest value, lowest cost*

**Rule.** Add an upper bound alongside the existing floor: fire only when
`0.5 <= abs(poc - close) / atr <= 2.5`. Change `strength` from
`min(1.0, abs(gap) / atr_v / 2.0)` to something decreasing in distance, e.g.
`max(0.2, 1.0 - abs(gap) / atr_v / 2.5)`.

**Why.** F2's table: POC completion within 60m is 56.5% inside 2 ATR and 6.7% at 4-7 ATR, while
61.6% of current firings are beyond 3 ATR. F12: the strength field currently ranks confidence in
exactly the wrong order.

**Pre-conceded weakness.** On a symmetric 1 ATR barrier the capped band measured *worse* than the
tail (60 days, 5m: 47.5% at n=1,958 versus 50.6% at n=5,237). That is because the barrier test
does not use the POC as the target - which is F12's point, and why A1 and A2 should be tested
together. The claim A1 stands on is that the condition should not advertise a reference the
market reaches 6.7% of the time.

**Cost.** Firing rate drops from ~43% of 5m bars to roughly 12-15%, which is a *benefit* for a
condition that is supposed to be a location filter.

### A2. Level-referenced exits

**Rule.** Add `StopKind.LEVEL` and an optional `target_level` to `ExitModel`, both fed from
`ConditionResult.value` of the anchoring signal. For a `poc_reversion` entry: target 1 = the POC,
stop = 1.0 ATR beyond the value-area edge on the entry side. For `zone_touch`: target = the zone's
`distal` edge, stop = beyond `distal` plus pad. `SDZone` already exposes `proximal` and `distal`
for exactly this and nothing consumes them.

**Why.** F12. Six location conditions publish reference prices into a field no exit can read;
`target_prices` is R-multiples only. Without this, every profile and zone condition is a direction
generator with an unrelated exit, and the desk cannot tell a bad *signal* from a bad *exit*.

**Testable immediately.** Run the existing `VOLUME_PROFILE` and `SUPPLY_DEMAND` universes twice -
once with the R-multiple exits, once with level-referenced ones - and compare. That is one
ablation, not a new search.

### A3. `sr_cluster_sweep` - the liquidity group's sample problem

**Rule.** The bar pierces an `SRLevel` whose `strength` is in the top quartile of the levels
visible on that bar and closes back through it; direction is away from the level. Pad and quartile
threshold are the tested parameters.

**Why.** The `liquidity` group sweeps only single named levels - PDH/PDL, ONH/ONL, session
extremes. There is no condition anywhere that sweeps a *cluster*, even though
`support_resistance` already builds recency-and-touch-weighted `SRLevel` objects and
`pullback_to_support` already consumes them. Stops rest at clusters of equal highs, not only at
named daily levels.

**Measured, 60 days:**

| | 5m n | 5m win | 15m n | 15m win |
|---|---|---|---|---|
| shipped sweeps (PDH/PDL/ONH/ONL/session), pooled | 680 | 47.4% | 401 | 44.0% |
| PROPOSED `sr_cluster_sweep`, any level | 4,900 | 51.0% | 2,008 | 50.6% |
| PROPOSED `sr_cluster_sweep`, top-quartile strength | 1,655 | 47.9% | 645 | 49.3% |

**The claim is sample, not edge.** On martingale data all of these sit on 50%. What matters is
that the top-quartile version produces **2.4x** the signals at 5m and **1.6x** at 15m. My own
seat's discipline (`MIN_SLICE_TRADES = 30` in `research_liquidity.py`) means a liquidity finding
must survive being cut by session *and* time bucket; with 401 raw 15m sweeps over 60 days, most
of those slices are unpublishable today. This is the condition that makes the liquidity seat's
own reporting standard achievable.

**Pre-conceded weakness.** "Any level" at a mean 5.3-6.9 visible levels per bar fires on ~30% of
bars and is too loose to be a level condition at all. The proposal is the top-quartile variant;
if the desk cannot justify the quartile threshold on out-of-sample data, this is threshold-shopping
and should be rejected.

### A4. Two expressiveness gaps I could not demonstrate value for

Filed because the library cannot express these shapes at all, not because I have evidence they
pay. Both measured and both came back flat. The desk should weigh them as completeness, and is
entitled to say no.

**(a) `zone_rejection`.** Rule: for a DEMAND zone, `bar.low <= z.top and close > z.top`
(mirror for SUPPLY), zone not invalidated, direction = zone kind. The library believes the
wick-through-and-reclaim shape matters at PDH/PDL/ONH/ONL/session extremes (`_level_sweep`) and
does not express it at supply/demand zones, where `zone_touch` requires the close *inside*
(F7). Measured 60 days: 423 signals at 5m / 106 at 15m versus `zone_touch`'s 366 / 72 -
+16% and +47% sample. Win 52.7% / 45.9% versus 53.5% / 50.0%. **No demonstrated improvement.**

**(b) Multi-bar acceptance failure.** `_level_sweep` is single-bar only: `b.low < level <= b.close`
on one bar. The market poking above PDH, holding there for twenty minutes and *then* failing is
the classic stop-run-then-reversion and is inexpressible. Rule: any of the last K closes beyond
the level, this close back inside. Measured 60 days, 5m: K=3 gives 574 signals at 51.2%, K=5 gives
815 at 49.7%, versus the shipped single-bar 446 at 50.7%; at 15m, 339 / 473 versus 262, all within
two points of each other. **More sample, no demonstrated improvement.**

### Also, cheaply: two housekeeping items that follow from F10

Either wire the six unreachable conditions into templates or delete them, and extend
`coverage.py::uncovered` to assert *reachability* - that some template can select each named
condition - rather than mere registration. As written, the module cannot detect the failure it
was created to prevent.

### What I am NOT proposing

- **Not** changing `poc_reversion`'s "inside value" precondition to an outside-in rotation.
  I tested my own preferred hypothesis and it measured 48.5% on n=34, while the large-sample
  version of it measured 44.3% on n=2,644 (F2).
- **Not** switching the profile to RTH-only on performance grounds. The geometry improves and the
  outcome does not (F3). Fix it as an internal consistency defect or leave it.
- **Not** a new mean-reversion band condition. The `meanreversion` group's problem (F13) is that
  its three members are one idea, and a fourth band would be a fourth restatement.

---

## Where I expect the other specialists to disagree with me

Named specifically so the debate has something to bite on.

**1. The trend specialist will claim my single biggest measurement as his.** F2's
outside-value-fade result - 44.3% win, -0.406 ATR, n=2,644 - is the strongest number in this
document and it says continuation beats reversion once price leaves the prior value area. He will
argue the whole `profile` group should be reclassified as a trend tool and that my seat has no
business owning it. **Where I would push back:** `value_area_breakout` fires on **63.4%** of 5m
bars (15 days). A condition that is true on two bars in three is describing the modal state, not
an event; its 55.7% win rate is closer to "the market had autocorrelation in this sample" than to
a location edge. But this is my most vulnerable claim and I expect to concede part of it.

**2. The quantitative specialist will say every win rate here is meaningless.** He is right, and
I have said so at the top - but he should apply it symmetrically. My load-bearing claims are
firing rates, sample sizes, set overlap and code facts (F1, F5, F9, F10, F11, F12), none of which
depend on the data having an edge. The claims of mine that **do** depend on it, and that he can
legitimately knock down, are: F4's band ablation (n=36 vs n=30), F2's near-band advantage, and
A3's "not worse" framing.

**3. He will also call F2's distance table a tautology.** Closer targets get hit more often -
of course they do. **My answer:** yes, and that is exactly why it bites. The tautology is
precisely what `poc_reversion`'s `strength` field contradicts (F12). A condition whose confidence
score runs opposite to an arithmetic certainty is a defect regardless of whether the market has
an edge.

**4. My sample-floor arguments assume the desk keeps running 20-60 day windows.** F11's
"0 of 50 publishable" and A3's slice argument both weaken if the desk moves to multi-year
history. On five years of data the best post-news MOMENTUM variant would clear 30 trades
comfortably. Anyone who intends to extend the history should discount F11 accordingly - though
the 1.19% retention and the `trials_searched` cost survive the change.

**5. The trend specialist will defend `post_news_window` as his filter on his template.**
MOMENTUM is not my family. My counter is that the deflation cost is shared across the pool, not
borne by MOMENTUM alone, which makes it the desk's decision and not his - and that the slice
alternative in F11 gets him the same information on 75x the sample.

**6. Someone should accuse A3 of manufacturing sample.** "Loosen the level definition until you
get 2.4x the signals" is how a search gets wider without getting better, and the top-quartile
threshold is a knob I chose after looking at the data. I have flagged it myself above; if the
desk cannot justify the quartile out of sample, reject A3 outright.

**7. My defence of `zone_touch` in F7 is the finding most likely to be right for the wrong
reason.** I measured trigger-bar overlap and found almost none. F8 says that understates trade
overlap, and at 15m 40.3% of zone touches sit within +/-3 bars of a sweep. If `trade_overlap` on
realised fills comes back high, F7's conclusion survives as a statement about bar shapes and
**fails** as a statement about whether the confluence is new. I would concede that.
