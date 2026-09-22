# Confluence audit: do the 21 new conditions earn their place?

**Symbol: MNQ only.** Nothing here transfers to MES, MGC or CL. I did not test them.
**Data: synthetic.** 120 trading days of 1-minute MNQ bars from
`futures_agents.data.synthetic_series` (seed 7 + symbol offset, i.e. the default
`SyntheticProvider` path), 165,600 bars, 09:30-16:00 ET trading only
(`StrategyFilters.rth_only=True` on every template). The generator's own docstring
says it contains *no repeating pattern a strategy could exploit* and that results
should hover around break-even before costs and be clearly negative after them.
**No number in this file is evidence of a live edge, and none of it may inform a
live decision.** What synthetic data *can* settle - and what this run is for - is
mechanical: how often a condition fires, whether two conditions fire on the same
bars, whether a filter changes any trade at all, whether anything repaints, and
how large the hypothesis space has become. Those are the questions I answer.

**Code state.** Every measurement below was taken against **HEAD `f82bd35`**,
which is the commit I was asked to audit. While I was running, another agent
landed uncommitted changes in the working tree to `econ_calendar.py`,
`features.py`, `indicators/structure.py`, `strategies/base.py`,
`strategies/library.py` and `strategies/combinator.py`. Three of them bear
directly on findings here and I re-checked each against the working tree; where a
finding is already superseded I say so in place. Nothing in this file was
re-measured against the working tree except where explicitly stated.

Machinery reused as-is: `strategies/combinator.py` (`_signal_pools`,
`_filter_sets`, `_violates_exclusive`, `_build_strategy`), `backtest/engine.py`
(`BacktestEngine.run_many`), `backtest/metrics.py`, `backtest/walkforward.py`
(`robust_score`, `walk_forward`), `backtest/robustness.py` (`deflated_expectancy`,
`assess_robustness`), `backtest/montecarlo.py`. Floors taken from
`agents/research.py` (`MIN_TRADES_FOR_RANK = 30`, `MIN_TRADES_PER_SLICE = 5`) and
`schema.HistoricalPerformance.is_live_eligible` (30 trades, 10 OOS trades,
robustness >= 0.5). I edited no source file.

---

## Verdict

**Live-eligible strategies produced: zero.** Combined out-of-sample expectancy
across the walk-forward was **-0.120R over 402 trades (t = -3.04)** against an
in-sample **+0.150R over 1,360 trades (t = +7.32)** - walk-forward efficiency
**-0.80**, `is_credible = False`. That is the expected outcome on data with no
exploitable structure, and it is also the best available evidence that the engine
is not leaking the future: a look-ahead bug makes out-of-sample look *good*.

On the seven newly-tradeable specification variables, one at a time:

| # | Variable | Sample it produced | Independent of the old library? | Verdict |
|---|---|---|---|---|
| 1 | **volume profile** | 1,841 trades; 16 of 72 strategies cleared the 30-trade floor - the most productive new group | **No.** `value_area_breakout` vs `prior_day_breakout`: phi **+0.743**, Jaccard **0.831**, direction agreement **1.00** on 1,867 co-fires | Earns a place on sample production, **not** on independence. Its headline condition is a relabelling |
| 2 | **market profile** | shares conditions with (1) | **No.** `open_outside_value` phi **+0.537** with `prior_day_breakout`, and fires on **70.8%** of bars | A day-type label, not a new edge. Does not earn a separate line in the coverage map |
| 3 | **fibonacci levels** | 904 trades; 12 of 72 cleared the floor; the best floor-clearing expectancy in the run (+0.128R, n=30) | **Yes.** Largest \|phi\| against *any* existing condition is 0.264, and `fib_golden_pocket` has **zero** co-fires with `break_of_structure` | The only new group whose information is genuinely new. Edge still unproven. **Keep** |
| 4 | **imbalances** | 0 of 72 strategies containing `imbalance_bar` cleared the floor; `imbalance_pullback` got 6 through | **Partly.** `imbalance_bar` phi **+0.376** with `volume_surge`; direction agreement **1.00** with `delta_confirms_bar`. `no_recent_imbalance` phi **-0.604** with `opening_drive_window` - it is largely a time-of-day filter wearing a different name | Mostly restatement. `imbalance_pullback` fires on 42% of bars, which is not a setup |
| 5 | **supply and demand** | **16 trades across all 72 strategies.** Zero cleared any floor | **Yes** (max \|phi\| 0.064) but it fires on 0.5% of bars and `away_from_zone` passes 99.4% of them | **Untested, not disproven.** Withheld entirely. The zone detector is too selective to produce a sample at this horizon |
| 6 | **open interest** | **0 fires, 0 trades.** `oi_price_confirmation` and `oi_expanding` never triggered once on 3,120 sampled bars | Unmeasurable | The synthetic generator never populates `Bar.open_interest`, so both conditions correctly return "no signal" forever. The library is honest; the **coverage map is not** - this variable is *mapped*, not *tested*, and should not be counted as closed until a feed with an OI column exists |
| 7 | **news conditions** | 124 of 126 filtered variants produced a **byte-identical** trade set to their control | The two avoidance filters are near-duplicates of each other: `no_imminent_release` vs `outside_news_blackout` phi **+0.749**, Jaccard **0.997** | **Does not earn its 4x multiplication of the search space.** See below |

### Six of the twenty-one new conditions could not be traded at all at HEAD

Registration is not reachability. The combinator draws SIGNAL conditions from a
template's `required_groups` / `optional_groups`, but it draws FILTER conditions
**only** from names listed literally in `base_filters` or `optional_filters`. At
HEAD `f82bd35` exactly twelve filter names appear anywhere in `TEMPLATES`:

```
after_opening_range  avoid_lunch  mtf_not_conflicted  no_imminent_release
opening_drive_window outside_news_blackout  post_news_window  regime_ranging
regime_trending  volatility_compressed  volatility_normal  volume_not_thin
```

Of the 25 registered FILTER conditions, 13 are therefore undrawable, and one
SIGNAL (`regime_matches_direction`, whose `regime` group no template names) as
well - 14 of 73 conditions unreachable. **Six of them are new:**

| Unreachable new condition | Group | Fires on | Consequence |
|---|---|---|---|
| `away_from_hvn` | profile | 84.6% of bars | never in any generated strategy |
| `open_outside_value` | profile | 70.8% | never in any generated strategy |
| `fib_sr_confluence` | fibonacci | 69.7% | never in any generated strategy |
| `no_recent_imbalance` | imbalance | 82.6% | never in any generated strategy |
| `away_from_zone` | supplydemand | 99.4% | never in any generated strategy |
| `oi_expanding` | openinterest | 0.0% | never in any generated strategy |

`coverage.SPEC_CONFLUENCES` at HEAD named all six as evidence that "volume
profile", "market profile", "fibonacci levels", "imbalances", "supply and demand"
and "open interest" were tradeable. They were registered, not tradeable. This is
the same class of silent gap the coverage module was written to close, one level
down. (**Superseded:** the working tree now carries a reachability check in
`coverage.py` and reports `unreachable_conditions() == []`. I verified that.)

**Net:** of the seven, one (fibonacci) adds genuinely new information; two
(volume profile, market profile) are largely relabellings of prior-day location;
one (imbalances) is a partial restatement of volume and delta; two (supply/demand,
open interest) could not be measured at all; and one (news) is near-inert as
currently wired. The count went from 31/38 to 38/38 tradeable variables. The count
of variables that produced a *measurable, independent* sample went from 31 to
**32**.

---

## Defect found: the like-for-like control does not survive generation

**This is the most consequential thing in the run and it is not a performance
number.** I did not fix it - it is outside my file.

> **Status update.** Measured at HEAD `f82bd35`, which is what the numbers below
> describe. While this audit was running, a fix landed in the working tree
> (uncommitted): `generate_combinations` now enumerates and samples *rule sets*
> and emits every filter variant of each sampled rule set. I re-ran the same
> measurement against the working tree: **883 of 883 families complete (100%)**,
> 3,960 specs, variant-count histogram `{4: 608, 5: 122, 6: 153}`. The defect
> described below is real, was real at the commit I was asked to audit, and is
> fixed on disk. The consequences for deflation and for shortlist crowding
> described further down are **not** fixed by it - they are properties of the
> filter dimension itself.

`combinator._filter_sets` is documented as: *"Index 0 is always the bare base set,
so 'with the news filter' always has a like-for-like control to be compared
against."* That is true of `_filter_sets` in isolation. It is **false of the
universe `generate_combinations` actually returns**, because the per-template
random subsample is taken *after* the filter dimension has been expanded:

```
if len(candidates) > per_template:
    candidates = rng.sample(candidates, per_template)
```

A family (same group, timeframe, signal set, exit index) has 4 filter variants
scattered among hundreds of thousands of candidates; sampling 307 of them draws
at most one. Measured on the desk's own default path
(`generate_combinations("MNQ", (1,5,15,60,240), max_total=4000, seed=20260922)`):

```
specs returned                                      3,991
distinct families                                   3,985
families carrying a control (bare base filters)       976
families carrying >= 1 news-filtered variant        3,012
families carrying BOTH  ->  COMPLETE PAIRS              3   (0.08%)
filtered variants with no control in the same run   3,009
```

So in a normal research run, 3,009 news-filtered strategies are scored with no
control to compare them against, and the question "does standing aside for a
release help?" is unanswerable from the artefacts - while the search count, and
therefore the deflation charge every *other* strategy pays, is still multiplied by
four. The minimum fix is to sample at the family level `(group, tf, signal_set,
exit_index)` and then expand all filter sets for the sampled families. That is
what my harness did, which is why this report can answer the question at all.

---

## News filter: the paired comparison

126 families, each built four ways with identical signals, identical exits,
identical timeframe, identical base filters: control / `+outside_news_blackout` /
`+no_imminent_release` / both. MOMENTUM families instead carry
`+post_news_window` and `+outside_news_blackout+post_news_window`. 504 strategies
in total, one portfolio pass.

### The headline number

| Variant | Pairs | Pairs where the filter changed **any** trade | Control trades pooled | Trades vetoed | Paired mean d(expectancy) | Paired t |
|---|---|---|---|---|---|---|
| `outside_news_blackout` | 126 | **2** (124 identical) | 1,633 | **2 (0.122%)** | **-0.00191R** | -1.000 |
| `no_imminent_release` | 108 | **4** (104 identical) | 1,289 | **4 (0.310%)** | **-0.00218R** | -0.981 |
| both together | 108 | **4** (104 identical) | 1,289 | 5 (0.388%) | -0.00189R | -0.839 |
| `post_news_window` | 18 | 10 (8 identical) | 344 | **339 (98.5%)** | -0.15504R | -1.782 |
| `outside_news_blackout+post_news_window` | 18 | 10 | 344 | 339 (98.5%) | -0.15504R | -1.782 |

Restricted to pairs whose **control cleared the 30-trade floor** (the only pairs
where the difference is worth a t at all):

- `outside_news_blackout`, n = 16 pairs: mean d(expectancy) = **exactly 0.00000R,
  sd = 0.00000**. Every one of the 16 filtered variants is the same strategy.
- `no_imminent_release`, n = 13 pairs: mean d(expectancy) = **+0.00035R**,
  sd 0.00131, t = +0.958. Thirteen pairs and a third of a basis point of R.
- `post_news_window`, n = 3 pairs: mean d = -0.25543R, t = -1.028.

The reported t-statistics for the two avoidance filters are **degenerate and
should not be quoted as significance**: 124 of 126 differences are identically
zero, so the t is computed from a two-point distribution and the value near
-1.000 is an artefact of that, not a test result.

### The trade-level partition (the cleanest form of the question)

Take every control strategy's own trades and split them into the ones the filter
*would have* vetoed and the ones it would have kept. Same strategy, same sample,
no confound:

| Filter | Control strategies | Vetoed n | Vetoed mean R | Kept n | Kept mean R | kept - vetoed | Welch t |
|---|---|---|---|---|---|---|---|
| `outside_news_blackout` | 126 | **2** | +0.0511 | 1,631 | +0.0211 | -0.0300R | -0.027 |
| `no_imminent_release` | 108 | **4** | -0.4892 | 1,285 | +0.0151 | +0.5043R | +0.910 |
| `post_news_window` | 18 | 339 | +0.0574 | **5** | -0.4811 | -0.5385R | -1.319 |

The 4 trades `no_imminent_release` vetoed lost 1.96R between them out of +17.44R
total. That is the entire evidential content of the avoidance filters in this run.

### Conclusion, and the structural reason

**Standing aside for scheduled releases does nothing measurable here, and the
reason is structural rather than statistical.** Every high-impact rule in
`econ_calendar.ECON_RULES` except FOMC lands at **08:30 ET** - CPI (10th business
day), Nonfarm Payrolls (first Friday), Core PCE (last business day). Every
template's `StrategyFilters` has `rth_only=True`, so the 09:30-16:00 window never
overlaps the blackout around an 08:30 print, and by 09:30 the *next* high-impact
release is weeks away. Only FOMC (14:00 statement, 14:30 press conference) sits
inside RTH. Measured on the 46,800 RTH bars in the sample:

```
RTH bars in blackout ([-5, +10] min)          128   (0.274%)
RTH bars with a release < 30 min ahead        240   (0.513%)
RTH bars 5-60 min after a release             341   (0.729%)
RTH days touched by any news filter             4  of 120
  -> 2026-04-29, 2026-06-17, 2026-07-29, 2026-09-16  (all FOMC)
```

Four days in six months. The desk cannot learn whether news avoidance helps
RTH-only strategies from this calendar, and it should stop paying a 4x search
penalty to keep asking.

> **Re-checked against the working tree.** An `EIA Crude Oil Inventories` rule at
> **10:30 ET, Impact.HIGH** has since been added to `ECON_RULES` - a weekly
> release that *is* inside RTH, which is exactly what this question needs. As of
> now it projects **zero** events over the sample window (the rule is present but
> `project_events` returns only CPI 7, NFP 8, PCE 8, FOMC 10 = 33 events), so the
> incidence is unchanged: still 128 blackout bars, 240 imminent bars, 4 RTH days.
> That work looks mid-flight. Once EIA projects, re-run this comparison first -
> it turns an unanswerable question into an answerable one at roughly 24 affected
> RTH days per 120 instead of 4.

Three things would make the question answerable:
(a) allow overnight/pre-market strategies into the sweep, where the 08:30 prints
actually land; (b) include MEDIUM-impact rules (PPI, Claims, Retail Sales, GDP,
ISM 10:00 - ISM is inside RTH) in the blackout, which is a parameter of
`event_proximity`, not a code change; (c) widen the blackout beyond
[-5, +10] minutes. Until one of those happens, the honest statement is **"not
measured"**, not "no effect".

`post_news_window` is a different animal: it is a *requirement*, not an avoidance,
and it vetoes 98.5% of trades, leaving 5 across 18 strategies. It is sample
destruction, and the direction of what little is left (vetoed trades +0.057R,
kept trades -0.481R) points the *wrong* way for the hypothesis. n = 5. Withheld.

Finally, the two avoidance filters are near-duplicates of each other
(phi +0.749, Jaccard 0.997 at 15m), so the four filter sets are really about two
distinct restrictions. The dimension costs 4x in search and delivers ~2x in
information, of which ~0 was measurable.

---

## Redundancy against existing conditions

Every registered condition evaluated on the same **3,120 15-minute RTH
snapshots**, then trigger vectors correlated. `phi` is the Matthews correlation of
the binary fired/not-fired indicators (chosen over Jaccard because base rates here
range from 0.005 to 0.995); `jacc` is literal bar overlap; `dir` is the fraction
of co-firing bars on which the two agree about direction.

### Nearest existing condition for each of the 21 new conditions

| New condition | Group | Kind | Base rate | Nearest existing condition | phi | jacc | co-fires | dir |
|---|---|---|---|---|---|---|---|---|
| `value_area_breakout` | profile | SIGNAL | 0.700 | **`prior_day_breakout`** | **+0.743** | **0.831** | 1,867 | **1.00** |
| `no_recent_imbalance` | imbalance | FILTER | 0.826 | `opening_drive_window` | -0.604 | 0.098 | 293 | - |
| `poc_reversion` | profile | SIGNAL | 0.205 | `prior_day_breakout` | -0.597 | 0.012 | 31 | 0.00 |
| `open_outside_value` | profile | FILTER | 0.708 | `prior_day_breakout` | +0.537 | 0.723 | 1,738 | - |
| `away_from_hvn` | profile | FILTER | 0.846 | `prior_day_breakout` | +0.492 | 0.713 | 1,903 | - |
| `imbalance_bar` | imbalance | SIGNAL | 0.066 | `after_opening_range` | -0.377 | 0.038 | 113 | - |
| `imbalance_pullback` | imbalance | SIGNAL | 0.423 | `power_hour` | -0.320 | 0.014 | 25 | - |
| `fib_golden_pocket` | fibonacci | SIGNAL | 0.103 | `break_of_structure` | -0.264 | 0.000 | **0** | - |
| `fib_shallow_retrace` | fibonacci | SIGNAL | 0.087 | `break_of_structure` | -0.240 | 0.000 | **0** | - |
| `fib_sr_confluence` | fibonacci | FILTER | 0.697 | `prior_day_breakout` | -0.218 | 0.410 | 1,195 | - |
| `value_area_edge` | profile | SIGNAL | 0.025 | `prior_day_breakout` | -0.163 | 0.005 | 10 | 0.00 |
| `fib_extension_reached` | fibonacci | SIGNAL | 0.015 | `break_of_structure` | +0.159 | 0.040 | 47 | 0.00 |
| `lvn_rejection` | profile | SIGNAL | 0.021 | `prior_day_breakout` | -0.119 | 0.007 | 14 | 0.43 |
| `zone_touch` | supplydemand | SIGNAL | 0.005 | `after_opening_range` | -0.064 | 0.004 | 11 | - |
| `away_from_zone` | supplydemand | FILTER | 0.994 | `ema_stack` | +0.062 | 0.855 | 2,662 | - |
| `no_imminent_release` | news | FILTER | 0.995 | `ema_stack` | +0.060 | 0.856 | 2,664 | - |
| `outside_news_blackout` | news | FILTER | 0.995 | `relative_volume_high` | -0.058 | 0.005 | 15 | - |
| `fresh_zone_approach` | supplydemand | SIGNAL | 0.006 | `slope_directional` | -0.057 | 0.004 | 9 | 0.56 |
| `post_news_window` | news | FILTER | 0.008 | `avoid_lunch` | +0.048 | 0.010 | 24 | - |
| `oi_expanding` | openinterest | FILTER | **0.000** | - | 0.000 | 0.000 | 0 | - |
| `oi_price_confirmation` | openinterest | SIGNAL | **0.000** | - | 0.000 | 0.000 | 0 | - |

### Specifically named pairs

| Pair | phi | jacc | co-fires | a fires | b fires | dir agree |
|---|---|---|---|---|---|---|
| `value_area_breakout` vs `prior_day_breakout` | **+0.743** | **0.831** | 1,867 | 2,183 | 1,931 | **1.00** |
| `no_imminent_release` vs `outside_news_blackout` | **+0.749** | **0.997** | 3,100 | 3,104 | 3,104 | - |
| `away_from_zone` vs `away_from_hvn` | +0.001 | 0.842 | 2,624 | 3,101 | 2,640 | - |
| `imbalance_bar` vs `volume_surge` | +0.376 | 0.202 | 202 | 207 | 995 | - |
| `imbalance_bar` vs `delta_confirms_bar` | +0.069 | 0.074 | 186 | 207 | 2,476 | **1.00** |
| `fib_sr_confluence` vs `pullback_to_support` | +0.190 | 0.222 | 498 | 2,176 | 564 | - |
| `open_outside_value` vs `regime_trending` | +0.097 | 0.232 | 547 | 2,210 | 692 | - |
| `poc_reversion` vs `vwap_proximity` | +0.061 | 0.121 | 116 | 641 | 434 | - |
| `fib_extension_reached` vs `range_position_extreme` | +0.058 | 0.028 | 19 | 47 | 661 | 0.00 |
| `fib_golden_pocket` vs `pullback_to_support` | +0.041 | 0.090 | 73 | 321 | 564 | 0.58 |
| `poc_reversion` vs `bollinger_mean_pull` | -0.041 | 0.129 | 197 | 641 | 1,079 | 0.46 |
| `zone_touch` vs `pullback_to_support` | -0.034 | 0.000 | **0** | 16 | 564 | - |
| `value_area_edge` vs `pullback_to_support` | +0.025 | 0.030 | 19 | 79 | 564 | 0.53 |
| `fib_shallow_retrace` vs `pullback_to_support` | +0.021 | 0.072 | 56 | 270 | 564 | 0.38 |
| `lvn_rejection` vs `fvg_nearby` | +0.016 | 0.022 | 5 | 64 | 166 | 0.40 |

### What this says, bluntly

1. **`value_area_breakout` is `prior_day_breakout` with a different name.** They
   fire on 83% of the same bars, correlate at phi +0.743, and on 1,867 co-firing
   bars they have **never once disagreed about direction**. A confluence
   containing both is claiming location twice. The combinator's `exclusive` rules
   for VOLUME_PROFILE block `poc_reversion` + `value_area_breakout` and
   `value_area_edge` + `value_area_breakout`, but nothing stops
   `value_area_breakout` from sitting next to `prior_day_breakout` in a BREAKOUT
   or LIQUIDITY confluence, where they are in different diversity groups
   (`profile` and `liquidity`) and therefore both eligible. That is precisely the
   failure the diversity rule exists to prevent, and the diversity rule does not
   catch it. **This pair belongs in an `exclusive` clause.**
2. **`value_area_breakout` is not a breakout.** It fires on 70% of bars. A
   condition with a 70% base rate is a location statement ("price is not inside
   yesterday's value"), not a setup. `open_outside_value` (70.8%),
   `away_from_hvn` (84.6%), `fib_sr_confluence` (69.7%), `no_recent_imbalance`
   (82.6%) and `away_from_zone` (99.4%) are in the same category: they are almost
   always true, so they add search dimensions and cost deflation without
   discriminating between bars.
3. **`poc_reversion` is the logical complement of `prior_day_breakout`**
   (phi -0.597, direction agreement 0.00 on the 31 bars where both fire). It is
   the negation of an existing condition, which is information, but not *new*
   information.
4. **Fibonacci is the one group that is genuinely orthogonal.** Its largest
   \|phi\| against any existing condition is 0.264, and `fib_golden_pocket` /
   `fib_shallow_retrace` have **zero** co-fires with `break_of_structure` - they
   are mutually exclusive states by construction. Whether that orthogonal
   information is *profitable* is a different question this data cannot answer.
5. **Supply/demand is orthogonal but starved.** Max \|phi\| 0.064, and
   `zone_touch` has zero co-fires with `pullback_to_support` - genuinely a
   different observation. It fires on 0.5% of bars.
6. `away_from_zone` and `away_from_hvn` have Jaccard 0.842 but phi +0.001: they
   overlap heavily only because both are almost always true. That is the exact
   case where Jaccard misleads and phi does not - worth remembering before anyone
   quotes an overlap number.

Caveat: these are synthetic price paths. The relationships that are *structural*
(value area vs prior-day range; golden pocket vs break of structure) will hold on
real data; the exact coefficients will not. `oi_*` at 0.000 is a property of the
data source, not of the conditions.

---

## What I withheld and why

Floors applied, all taken from the desk's existing screens:
`MIN_TRADES_FOR_RANK = 30`, `MIN_TRADES_PER_SLICE = 5`,
`walk_forward(min_trades_is=20)`, `HistoricalPerformance.is_live_eligible`
(>= 30 trades, >= 10 OOS trades, robustness >= 0.5),
`RobustnessReport.live_eligible` (score >= 0.45, positive expectancy, **positive
deflated** expectancy, WF efficiency >= 0.35, P(ruin) <= 0.02).

**Withheld on the 30-trade floor - the best-looking results in the entire run:**

| Strategy | Group | TF | n | PF | Expectancy | Win rate | Raw t | Why withheld |
|---|---|---|---|---|---|---|---|---|
| `MNQ-15m-816f656d29a4` | VOLUME_PROFILE | 15m | **16** | **4.97** | **+0.3456R** | 75.0% | **+2.527** | Highest t in the sweep. 16 trades. `ema_stack+macd_directional+poc_reversion+structure_trend` |
| `MNQ-5m-9e210037b248` | FIBONACCI | 5m | **18** | 2.38 | **+0.4803R** | 66.7% | +1.718 | 18 trades. `break_of_structure+delta_confirms_bar+ema_stack+fib_golden_pocket` |
| `MNQ-60m-cc44dd22d14c` | MOMENTUM | 60m | **6** | **13.90** | **+0.7680R** | - | +1.767 | Six trades and a profit factor of 13.9. This is what noise looks like |

Each of the three appears 2-4 times in the raw ranking because its news-filtered
clones are identical strategies; I report the family once.

**Everything else withheld:**

- **132 strategies traded but fell short of 30.** All withheld from ranking. They
  remain in the persisted results - dropping them would be survivorship bias.
- **314 strategies produced zero trades** and are retained and reported as such,
  by group: SUPPLY_DEMAND 56, MEAN_REVERSION 60, TREND 56, MOMENTUM 46,
  FIBONACCI 44, VOLUME_PROFILE 28, VWAP 24.
- **The entire SUPPLY_DEMAND group.** 16 trades across 72 strategies, 0 clearing
  any floor. No claim of any kind is made about supply/demand zones on MNQ.
- **The entire openinterest group.** Zero fires. Not a failure - an absent input
  column. Nothing was measured, so nothing is reported beyond that fact.
- **`post_news_window`.** 5 surviving trades across 18 strategies. The sign of the
  difference is against the hypothesis; n = 5, so I am not reporting a sign.
- **Session and regime slices below 5 trades.** Example, `MNQ-5m-3704c68dafd0`
  (FIBONACCI 5m, n=18): the only reportable session slice is RTH_OPEN
  (n=9, +0.703R); LUNCH (n=1), RTH_MORNING (n=2), RTH_AFTERNOON (n=2) and
  RTH_CLOSE (n=4) are all withheld, as is TREND_DOWN (n=1). Note that the parent
  strategy is itself below the 30-trade floor, so the RTH_OPEN figure is withheld
  too - it is shown here only to name what the floor removed.
- **Everything that *did* clear the 30-trade floor.** All 58 of them failed
  deflation regardless (see below), and the walk-forward collapsed. Zero
  live-eligible strategies were published.

---

## Measurements

### Sweep parameters

```
symbol                MNQ (only)
bars                  165,600  1-minute, 120 trading days, overnight included
timeframes (primary)  5m, 15m, 60m   [1m and daily NOT tested - runtime budget]
confirm groups        5m -> (15m, 60m) | 15m -> (60m, 240m) | 60m -> (240m)
templates             7 of 13: VOLUME_PROFILE, SUPPLY_DEMAND, FIBONACCI (new)
                      + TREND, VWAP, MEAN_REVERSION, MOMENTUM (old control set)
families              126  (group x tf x signal set x exit index)
strategies            504  = 126 families x 4 news-filter variants
portfolio sweep       181s, one pass, shared per-bar condition cache
costs                 default CostModel (commission + exchange fees per round
                      turn, volatility-scaled slippage, stop orders charged more)
```

### Per-group results

| Group | n | Produced trades | Cleared 30 | Total trades | Median trades | Mean exp (traders) | Best raw t | Best robust_score |
|---|---|---|---|---|---|---|---|---|
| VOLUME_PROFILE | 72 | 44 | 16 | 1,841 | 8 | +0.1968R | +2.53 | 0.1681 |
| SUPPLY_DEMAND | 72 | 16 | **0** | 16 | 0 | -0.2989R | 0.00 | 0.0045 |
| FIBONACCI | 72 | 28 | 12 | 904 | 0 | -0.0023R | +1.72 | 0.2184 |
| TREND | 72 | 16 | 8 | 704 | 0 | -0.2544R | +0.28 | 0.0058 |
| VWAP | 72 | 48 | 12 | 1,297 | 2 | -0.1216R | +0.75 | 0.0106 |
| MEAN_REVERSION | 72 | 12 | 4 | 388 | 0 | -0.1200R | 0.00 | 0.0000 |
| MOMENTUM | 72 | 26 | 6 | 698 | 0 | -0.0488R | +1.77 | 0.0568 |

New groups pooled (clearing the floor): 28 of 216, mean expectancy **+0.0349R**,
median 49 trades, mean t **+0.272**, max t +0.729, 20 of 28 positive.
Old groups pooled: 30 of 288, mean expectancy **+0.0008R**, median 82 trades,
mean t **+0.052**, max t +0.975, 18 of 30 positive.
The new groups are not worse than the old ones; neither is distinguishable from
zero, which is the correct result on data with no structure.

Per primary timeframe: 5m - 164 strategies, 2,570 trades, 30 cleared, max t
+1.718. 15m - 208 strategies, 2,434 trades, 20 cleared, max t +2.527. 60m - 132
strategies, 844 trades, 8 cleared, max t +1.767. **1m and daily were not tested**,
so no claim is made about them.

### Full metric profile of the best floor-clearing candidates

```
MNQ-5m-9567d6ad84f8  FIBONACCI 5m  above_vwap+di_direction+fib_golden_pocket+structure_trend
  n=30  win 46.7%  PF 1.32  exp +0.1277R  avgW +1.140R  avgL -0.758R  R/R 1.50
  maxDD 2.17R  avgDD 0.98R  consec L 3  consec W 2  Sharpe +0.126  Sortino +0.202
  avg MFE 0.87R  avg MAE 0.69R  avg hold 17 min   t +0.691   robust_score 0.0635

MNQ-5m-97c1ff9be279  FIBONACCI 5m  delta_confirms_bar+fib_shallow_retrace+price_above_ema200+structure_trend
  n=49  win 46.9%  PF 1.28  exp +0.0851R  avgW +0.820R  avgL -0.565R  R/R 1.45
  maxDD 3.28R  avgDD 1.27R  consec L 5  consec W 4  Sharpe +0.104  Sortino +0.169
  avg MFE 0.76R  avg MAE 0.57R  avg hold 61 min   t +0.729   robust_score 0.0317

MNQ-60m-2474ab2b349e  MOMENTUM 60m  break_of_structure+delta_confirms_bar+rsi_directional
  n=145 win 52.4%  PF 1.25  exp +0.0343R  avgW +0.327R  avgL -0.288R  R/R 1.14
  maxDD 4.17R  avgDD 1.71R  consec L 5  consec W 7  Sharpe +0.081  Sortino +0.121
  avg MFE 0.31R  avg MAE 0.25R  avg hold 67 min   t +0.975   robust_score 0.0124
  (largest sample in the run; highest raw t among floor-clearing strategies)

MNQ-5m-04a19c06d70c  VOLUME_PROFILE 5m  above_vwap+break_of_structure+slope_directional+value_area_breakout
  n=228 win 48.3%  PF 1.10  exp +0.0326R  maxDD 15.61R  consec L 7  t +0.597
```

### The deflation arithmetic, spelled out

`robustness.deflated_expectancy` charges the expected maximum of *n* standard
normals, `sqrt(2 ln n)`, against the observed t-statistic, then converts what is
left back to R via `deflated_t * std_r / sqrt(trades)`.

**How big did the hypothesis space actually get?** Counting
`signal_sets x exits x filter_sets x timeframes` per template, with the generator's
own `min_signals=2, max_signals=4`, diversity rule and `exclusive` constraints
applied, at the 3 timeframes I tested:

```
BEFORE  10 templates, 52 conditions, no optional-filter dimension      308,529
AFTER   13 templates, 73 conditions, x4 optional-filter dimension    3,560,652
                                                        growth factor  x11.54

  sqrt(2 ln 308,529)   = 5.0278
  sqrt(2 ln 3,560,652) = 5.4928
  ------------------------------
  extra free t-units the library growth charges every finding:  +0.4650
```

Decomposing that: without the optional-filter dimension the new library would be
890,163 combinations, `sqrt(2 ln 890,163) = 5.2343`. So **the news dimension alone
raises the bar by +0.2585 t-units** - more than half the total cost of the whole
expansion - in exchange for a paired effect that was exactly zero on 124 of 126
rule sets.

The four deflation bases, and what each charges:

```
n =       504   this run's actual trial count      sqrt(2 ln n) = 3.5278
n =       156   effective trials after dedup (*)   sqrt(2 ln n) = 3.1780
n =     4,000   desk default max_combinations      sqrt(2 ln n) = 4.0728
n = 1,600,128   space of the 7 templates I sampled sqrt(2 ln n) = 5.3452
n = 3,560,652   full 13-template library space     sqrt(2 ln n) = 5.4928

(*) 126 families + 30 variants that actually differed from their control
    (2 + 4 + 4 + 10 + 10) = at most 156 distinct trade sets among the 504
    strategies. This is the most generous accounting available and it is the
    one I would defend: the other 348 are literal duplicates. It moves the bar
    by 0.35 t-units and changes no conclusion.
```

Applied to the two headline results:

```
Best raw t in the whole sweep:  MNQ-15m-816f656d29a4, n=16, exp +0.34558R,
                                std_r 0.54696,  t = 0.34558 / (0.54696/sqrt(16))
                                                  = +2.5273

  deflated t @ 156   = 2.5273 - 3.1780 = -0.6507  ->  deflated expectancy 0.000R
  deflated t @ 504   = 2.5273 - 3.5278 = -1.0005  ->  deflated expectancy 0.000R
  deflated t @ 4,000 = 2.5273 - 4.0728 = -1.5455  ->  deflated expectancy 0.000R
  deflated t @ 1.6M  = 2.5273 - 5.3452 = -2.8179  ->  deflated expectancy 0.000R
  deflated t @ 3.56M = 2.5273 - 5.4928 = -2.9655  ->  deflated expectancy 0.000R
  (and it is withheld anyway: 16 trades, below the 30-trade floor)

Best raw t clearing the floor:  MNQ-60m-2474ab2b349e, n=145, exp +0.03433R,
                                std_r 0.42408,  t = +0.9748

  deflated t @ 504   = 0.9748 - 3.5278 = -2.5530  ->  deflated expectancy 0.000R
  deflated t @ 3.56M = 0.9748 - 5.4928 = -4.5180  ->  deflated expectancy 0.000R

  To clear the run-level bar at n=145 it would need
     expectancy > 3.5278 * 0.42408 / sqrt(145) = +0.1243R per trade.
     It has +0.0343R - short by a factor of 3.6.
  To clear it at its observed +0.0343R edge it would need
     n = (3.5278 * 0.42408 / 0.03433)^2 = 1,899 trades.   It has 145.
  Against the full library space, it would need 4,604 trades.
```

**Every one of the 504 strategies has a deflated expectancy of exactly 0.000R at
every trial count, including the most generous one.** Not one finding here
survives deflation, and a finding that only survives undeflated is not a finding.

### Out-of-sample and walk-forward

Candidate screen on **fold 0's training window only** (bars 0-49,680, the first
30% - no out-of-sample bar contributed to pool selection), top 16 by
`robust_score`, then 4-fold anchored walk-forward with `top_k=5`:

```
combined IN-SAMPLE   1,360 trades  win 56.5%  PF 1.64  exp +0.1496R  t +7.32
combined OUT-OF-SAMPLE 402 trades  win 38.1%  PF 0.69  exp -0.1202R  t -3.04
                                   maxDD 67.06R  max consecutive losses 9
walk-forward efficiency  -0.8035        selection stability  0.8889
is_credible              False          live-eligible strategies  0

fold 0  train[0, 49,680]    test[49,680, 78,660]    IS +0.3263  OOS +0.1237  n=76
fold 1  train[0, 78,660]    test[78,660, 107,640]   IS +0.2635  OOS -0.2145  n=102
fold 2  train[0, 107,640]   test[107,640, 136,620]  IS +0.1285  OOS -0.2262  n=106
fold 3  train[0, 136,620]   test[136,620, 165,600]  IS +0.0501  OOS -0.0813  n=118
```

Note the in-sample expectancy decaying monotonically (+0.326 -> +0.264 -> +0.129
-> +0.050) as the anchored window grows: the apparent edge was concentrated in the
first 30% of the data and diluted as more bars arrived. That is the signature of a
sample artefact, not of a decaying edge.

**The selection stability of 0.889 is inflated and should not be read as a good
sign.** Four of the five slots selected in folds 1-3 are the *same strategy* under
four different news-filter labels:

```
MNQ-15m-d81cb3652914  TREND 15m  control                                   } identical
MNQ-15m-fe4ad86e214c  TREND 15m  +outside_news_blackout                    } trade
MNQ-15m-c9cafb4a94d3  TREND 15m  +no_imminent_release                      } sets,
MNQ-15m-28cb02ff857a  TREND 15m  +outside_news_blackout+no_imminent_release } all four
  signals: ema_stack + range_position_extreme + rsi_directional + value_area_breakout
  full sample n=117  exp +0.0213R  t +0.277  PF 1.06  maxDD 16.31R
```

All four carry screen score 0.2599 to four decimals. The news dimension therefore
did not just cost deflation - it **crowded four clones into a five-slot
walk-forward shortlist**, displacing four genuinely distinct candidates. That is a
concrete, measurable cost of the optional-filter dimension as currently wired.

Finalist robustness (`assess_robustness`, 2,000 Monte Carlo runs, $240 risk/trade,
$50,000 starting equity):

```
MNQ-15m-28cb02ff857a  n=117  exp +0.0213R  t +0.277  PF 1.06  maxDD 16.31R
  deflated expectancy 0.000R @ 504 trials      robustness score 0.00
  own walk-forward efficiency -0.7495  (IS n=257 +0.1797R | OOS n=79 -0.1347R)
  Monte Carlo: P(profitable) 0.598  maxDD p95 17.52R  worst 27.30R
               longest losing streak p95 = 11, worst = 20
               P(ruin) 0.014  median final equity $50,539  p05 $47,026
  live_eligible: NO - "edge does not survive correction for searching 504
                 combinations"; "walk-forward efficiency -0.75"
  bias checks failed: data_mining_bias, overfitting
```

Parameter sensitivity (`parameter_sensitivity`) was **not** run on the finalists:
each call costs six full-history single-strategy passes (~4 minutes each) and the
budget did not allow it. `assess_robustness` therefore used its default
`sens_gate = 0.6` for those strategies. Nothing turns on it - both finalists were
already disqualified by deflation and by walk-forward efficiency - but the
parameter-spike check is genuinely **unperformed**, not passed.

### Anti-overfitting checks: what I ran and what I found

| Check | How I checked it | Result |
|---|---|---|
| **Look-ahead bias / repainting / future-data leakage** | Mechanical: built a frame from the first 80% of a 12-day series and the full series, then compared **all 73 conditions'** (triggered, direction) verdicts at the same 429 bar indices in the overlap | **0 mismatches across 73 conditions x 429 bars.** No condition changes its answer when later bars are appended. Construction backs this: `prior_session_profile` uses only completed prior sessions, `SDZone.as_of` masks future touches and invalidations, `swing_leg()` returns None until both swings are fractal-confirmed, `econ_calendar` projects from recurrence rules rather than headlines. **Limitation, stated rather than glossed:** my test walks both frames forward in the same order, so it cannot detect an *access-order-dependent* leak. That is not hypothetical - the working tree now carries a fix to `features.py` for exactly that, an S/R cache keyed on a 5-bar bucket but computed from the caller's index, worth up to four bars of look-ahead depending on which bar warmed the bucket first. **My sweep ran on the unfixed code.** I checked the exposure rather than assuming it: the engine walks strictly forward, and every window boundary I used (screen end 49,680 and fold ends 78,660 / 107,640 / 136,620) is a multiple of the 5-bar bucket, so every bucket was warmed by its own first bar and the numbers above are not contaminated. What I cannot claim is the general one - my test does not establish that the frame is order-independent, and it was not |
| **Data-mining bias** | `deflated_expectancy` at five trial counts, arithmetic above | **FAILS for all 504.** Zero deflated expectancy everywhere |
| **Overfitting** | 4-fold anchored walk-forward, leak-free screen on fold 0 training bars only | **FAILS.** Efficiency -0.80, OOS t -3.04 |
| **Insufficient sample** | 30-trade rank floor, 5-trade slice floor, 10-OOS-trade eligibility floor | **446 of 504 withheld.** Three good-looking candidates withheld explicitly |
| **Survivorship bias** | All 504 results kept, including 314 zero-trade and 132 sub-floor strategies | Pass by construction |
| **Unrealistic fills** | Read `engine.py`: entries fill at the **next** bar's open, gaps fill at the open, and when one bar contains both stop and target the **stop** is assumed first | Pass by construction; not independently stress-tested |
| **Transaction costs / slippage** | Default `CostModel`: commission per side charged twice per round turn, volatility-scaled slippage, extra ticks on stop orders. Net R is used everywhere in this report | Charged. Not stress-tested at double slippage (that lives in `parameter_sensitivity`, which I did not run) |
| **Parameter sensitivity** | **Not run** - runtime | **UNPERFORMED.** Stated, not assumed |
| **Regime dependence** | Session and regime slices computed; every slice under 5 trades withheld | Nothing survived to a reportable slice |
| **Paired-control integrity** | Counted complete control/variant families in `generate_combinations`' default output | **FAILS: 3 of 3,985 (0.08%).** See the defect section |

### Reproduction

Scripts (throwaway, under the scratchpad, not committed):
`/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad/conf/`
- `probe.py` - frame build, news-bar incidence, condition group census
- `sweep.py` - paired universe, portfolio sweep, paired news analysis, screen + walk-forward + robustness
- `redund.py` - 73x73 trigger-vector correlation on 3,120 15m RTH snapshots
- `leak.py` - truncated-vs-full frame repainting test
- `analyze.py` - the tables above

Outputs: `sweep_out.json`, `wf_out.json`, `redund_out.json`, `leak_out.json` in the
same directory. Seeds fixed (`20260922` for the universe sample, `7 + offset` for
the data, `20260922` for Monte Carlo), so the whole run is reproducible.
