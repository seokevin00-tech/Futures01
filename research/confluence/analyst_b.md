# Analyst B — quantitative audit of the "38 of 38" confluence-coverage claim

**Scope.** Commit `f82bd35` ("Close the confluence gap: 31 of 38 spec variables were
tradeable, now 38"), compared against its parent `dd8d171`. All library/combinator
measurements were taken against the working tree pinned to `f82bd35` for
`strategies/`, `features.py`, `indicators/`, `econ_calendar.py`. (A concurrent
edit by another seat later landed a direction fix inside `fib_sr_confluence`;
it does not move any number here, because that condition appears in zero
generated strategies either way.)

**Data.** Every empirical figure below is measured on *synthetic* MNQ series
(`synthetic_series("MNQ", days=15|45, seed=5|11|23)`) with open interest
synthesised the way `tests/test_strategy_library.py` does it — that is the only
data this repo has. Trigger-overlap numbers are therefore properties of the
synthetic generator as much as of the market, and I say so wherever it matters.
The structural findings (Sections 2, 3 and the mapping audit) are **not**
data-dependent: they are properties of the code and hold on any data.

---

## Verdict

**"38 of 38" is literally true and analytically empty.**

The literal claim the test enforces is: *every condition named in
`SPEC_CONFLUENCES` is a registered key of `CONDITIONS`*. That is true —
`uncovered()` returns `{}`, all 73 named conditions resolve, 38/38 entries pass.
No one is misreporting anything. But that assertion is the only thing
`test_spec_confluence_coverage_is_complete` checks, and it is satisfied by
*naming* a condition, not by making the variable tradeable. The module's own
docstring promises more than the test enforces: "Each entry names the registered
conditions that make its variable **tradeable** — not merely computed somewhere."
Measured against that sentence, the number is not 38.

My numbers, in increasing order of strictness:

| measure | count | what it means |
|---|---|---|
| named conditions all registered | **38 / 38** | what the test checks; true |
| entry is not a subset of another entry | **30 / 38** | 8 entries are wholly contained in another |
| entry has ≥1 condition the generator actually varies | **32 / 38** | 6 are covered only by never-varied base filters |
| entry has ≥1 condition shared with **no** other entry | **22 / 38** | the answer to the question as posed |
| entry has ≥1 condition that is both exclusive **and** varied | **17 / 38** | coverage that survives contact with the generator |

**The number I will defend is 22 of 38 for distinctness, and 17 of 38 for
distinctness that the research actually exercises.** 22 is the count the brief
asked for. 17 is the one I would put in front of the desk, because a variable
whose only non-shared condition is one the combinator never emits (`away_from_hvn`,
`oi_expanding`, `power_hour`, `regime_matches_direction`, `open_outside_value`,
`no_recent_imbalance`, `adx_trending`, `efficiency_high`, `vwap_proximity`,
`away_from_zone`) is covered on paper and untested in fact.

Two further things are worth stating plainly, because they are larger than the
counting argument:

1. **14 of the 73 registered conditions appear in zero generated strategies** at
   every realistic budget (`max_total` = 150, 1 500 and 4 000, MNQ, tf 1/5/15/60).
   Nine more appear but are *constant* inside every template that uses them, so
   nothing can ablate them. Only **50 of 73** conditions are genuinely varied
   hypotheses.
2. **70 % of generated strategies never fire at all** (545 of a stratified
   780-strategy sample, over 20 700 snapshots / 45 synthetic days). The desk's
   own published artefact agrees: `robustness_report` records 117 of 150
   strategies with zero trades (78 %). Coverage that cannot produce a signal is
   not coverage.

The commit closed a real gap — seven variables that had analytics and no
condition now have conditions, and `econ_calendar.py` genuinely turns "news
conditions" into a backtestable variable with a with/without control in the
enumeration. That part is sound work. The defect is the headline: "38 of 38"
counts map entries, and the map double-counts.

---

## Mapping audit

Legend: `~~strikethrough~~` = the generator produces **zero** strategies
containing it; `_italic_` = it appears, but is constant within every template
that uses it (never ablatable); `*` = not shared with any other of the 38 rows.
`excl` = exclusive conditions / total. `varied` = genuinely-varied conditions /
total. Counts from `generate_strategies("MNQ", [1,5,15,60], max_total=4000)` →
3 991 strategies.

| # | spec variable | conditions | excl | varied | judgement |
|---|---|---|---|---|---|
| 1 | price action | range_position_extreme\*, delta_confirms_bar, imbalance_bar | 1/3 | 3/3 | **Not price action.** Three unrelated things wearing the name: a 20-bar range-position indicator (structure), an order-flow confirmation, and an imbalance detector. Two of the three are borrowed from rows 8/10 and 32. There is **no bar-form primitive anywhere in the library** — `Bar.upper_wick`/`lower_wick` exist in `data/bars.py` and no condition reads them; no engulfing, pin, inside bar, close-location-value or body-ratio condition is registered. Price action is the one variable on this list whose defining content is absent. |
| 2 | market structure | structure_trend\*, break_of_structure | 1/2 | 2/2 | Honest. HH/HL classification plus a break of it. |
| 3 | support/resistance | pullback_to_support\*, ~~fib_sr_confluence~~ | 1/2 | 1/2 | Half honest. `pullback_to_support` is real and varied; the second condition is a FILTER no template can reach, so S/R rests on one condition. |
| 4 | supply and demand | zone_touch\*, fresh_zone_approach\*, ~~away_from_zone~~\* | 3/3 | 2/3 | Honest mapping, dead in practice. Fully distinct, but zone_touch fires on 1.5 % of bars and fresh_zone_approach on 0.8 %, and **88 % of SUPPLY_DEMAND strategies never fire** (53 of 60 sampled, 45 days). |
| 5 | vwap | above_vwap\*, ~~vwap_proximity~~\*, vwap_band_extension\*, vwap_band1_bounce\*, vwap_reclaim\* | 5/5 | 4/5 | **Best-covered row on the sheet.** Five exclusive conditions, four reachable. |
| 6 | volume | ~~relative_volume_high~~, ~~volume_surge~~, _volume_not_thin_ | 0/3 | 0/3 | **Not covered.** All three are FILTERs; the `volume` group holds **zero SIGNAL conditions**, so `_signal_pools` silently drops it from MOMENTUM's and BREAKOUT's `required_groups`. Two of the three are unreachable; the third is a base filter present in 92.3 % of strategies and absent from none within its templates. Volume is an assumption, not a variable. |
| 7 | volume profile | poc_reversion, value_area_edge, value_area_breakout, lvn_rejection\*, ~~away_from_hvn~~\* | 2/5 | 4/5 | Honest. Real new content; three conditions shared with row 30, which is defensible (market profile and volume profile are the same distribution read two ways) but means rows 7 and 30 are not two variables. |
| 8 | delta | delta_confirms_bar, delta_divergence | 0/2 | 2/2 | **Subset of row 10.** No exclusive condition. |
| 9 | cumulative volume delta | cvd_directional | 0/1 | 1/1 | **Subset of row 10.** One condition, also row 10's. |
| 10 | order flow | cvd_directional, delta_confirms_bar, delta_divergence | 0/3 | 3/3 | Rows 8, 9, 10 and 33 are **one** set of three conditions counted four times. `order flow` = `delta` ∪ `cvd`; `divergences` ⊂ `delta` ⊂ `order flow`. Net: 3 conditions, 4 spec rows. |
| 11 | open interest | oi_price_confirmation\*, ~~oi_expanding~~\* | 2/2 | 1/2 | Honest but thin. Fully exclusive; only `oi_price_confirmation` is reachable, and the `openinterest` group reaches **2.5 %** of generated strategies (101 of 3 991) — the lowest of any group. Synthetic OI only; there is no real OI in the repo. |
| 12 | momentum | rsi_directional, macd_directional, stoch_directional\*, macd_hist_direction | 1/4 | 4/4 | Honest as a row, but rows 12, 16 and 17 share everything but `stoch_directional` and `rsi_extreme_reversal`. |
| 13 | volatility | _volatility_normal_, ~~volatility_expanding~~, _volatility_compressed_\* | 1/3 | 0/3 | **Not a tested variable.** All three are FILTERs; one is unreachable, the other two are nailed into `base_filters` and constant within every template that carries them. Nothing measures whether the volatility gate helps. |
| 14 | atr | _volatility_normal_, ~~volatility_expanding~~ | 0/2 | 0/2 | **Double-counting, confirmed.** Strict subset of row 13, and the two conditions are two thresholds on the *same* number: `volatility_regime()` buckets ATR against its own trailing distribution, and `volatility_expanding` reads `atr_percentile ≥ 0.70` directly. "ATR" and "volatility" are one measurement at two cut-points, and neither is varied. The one place ATR is genuinely a tested variable is `ExitModel(StopKind.ATR, stop_mult ∈ {0.75, 1.0, 1.2, 1.5, 2.5})` — which the map does not cite. |
| 15 | moving averages | ema_stack, ema_fast_above_slow\*, price_above_ema50\*, price_above_ema200\* | 3/4 | 4/4 | Honest. |
| 16 | rsi | rsi_directional, rsi_extreme_reversal | 0/2 | 2/2 | No exclusive condition; split between rows 12 and 23. |
| 17 | macd | macd_directional, macd_hist_direction | 0/2 | 2/2 | **Strict subset of row 12.** Zero exclusive content. |
| 18 | bollinger bands | bollinger_extreme\*, bollinger_mean_pull | 1/2 | 2/2 | Honest. |
| 19 | fibonacci levels | fib_golden_pocket, fib_shallow_retrace\*, fib_extension_reached, ~~fib_sr_confluence~~ | 1/4 | 3/4 | Honest. Real new content; three of four reachable. The three are mutually exclusive by template construction, which is correct. |
| 20 | liquidity | prior_day_sweep, overnight_sweep, session_extreme_sweep | 0/3 | 3/3 | **Fully decomposed into rows 27, 28, 29** — one condition each. Rows 20/28/29 are three spec entries over three conditions that rows 27–29 already claim. Defensible reading of the spec (it lists both the concept and the levels) but it is not four variables' worth of evidence. |
| 21 | breakouts | prior_day_breakout, opening_range_breakout, initial_balance_break, value_area_breakout | 0/4 | 4/4 | Honest content, zero exclusive conditions — every one is shared with rows 22, 27 or 30. |
| 22 | breakdowns | prior_day_breakout, opening_range_breakout, break_of_structure | 0/3 | 3/3 | **Not a separate variable.** All three conditions are bidirectional: `_pd_break` returns LONG above PDH and SHORT below PDL from the same call; same for `_orb` and `_bos`. The short branch *is* the breakdown, and `allowed_directions` already covers both. Rows 21 and 22 are one condition family read twice. There is no condition that exists only for breakdowns. |
| 23 | reversals | rsi_extreme_reversal, stoch_extreme\*, opening_range_fade, fib_extension_reached | 1/4 | 4/4 | Honest content, mostly borrowed from rows 16, 19, 26. |
| 24 | trend continuation | ema_stack, ~~adx_trending~~\*, di_direction\*, slope_directional\*, ~~efficiency_high~~\*, fib_golden_pocket, imbalance_pullback | 4/7 | 5/7 | Honest — the largest genuinely-exclusive block after VWAP. But two of its four exclusive conditions (`adx_trending`, `efficiency_high`) are FILTERs no template reaches. |
| 25 | mean reversion | bollinger_mean_pull, keltner_outside\*, poc_reversion | 1/3 | 3/3 | Honest. |
| 26 | opening range | opening_range_breakout, opening_range_fade | 0/2 | 2/2 | No exclusive condition (shared with rows 21, 22, 23), but the pair is coherent and both are varied. |
| 27 | previous day high/low | prior_day_sweep, prior_day_breakout | 0/2 | 2/2 | Overlaps rows 20 and 21 completely. |
| 28 | overnight high/low | overnight_sweep | 0/1 | 1/1 | **Strict subset of row 20.** |
| 29 | session highs/lows | session_extreme_sweep | 0/1 | 1/1 | **Strict subset of row 20.** |
| 30 | market profile | value_area_edge, value_area_breakout, ~~open_outside_value~~\*, initial_balance_break | 1/4 | 3/4 | Near-duplicate of row 7 plus one liquidity condition; its only exclusive condition is unreachable. |
| 31 | fair value gaps | fvg_nearby\* | 1/1 | 1/1 | Honest, exclusive, varied — thin (one condition) but real. |
| 32 | imbalances | imbalance_bar, imbalance_pullback, ~~no_recent_imbalance~~\* | 1/3 | 2/3 | Honest. Two reachable signals; the exclusive one is an unreachable filter. |
| 33 | divergences | delta_divergence | 0/1 | 1/1 | **Strict subset of rows 8 and 10.** |
| 34 | multi-timeframe structure | mtf_aligned\*, _mtf_not_conflicted_\* | 2/2 | 1/2 | Honest. Fully exclusive; one signal varied, the filter constant inside PULLBACK. |
| 35 | news conditions | outside_news_blackout\*, no_imminent_release\*, post_news_window\* | 3/3 | 3/3 | **The one row that is unambiguously new, exclusive and varied.** See the caveat in the next section: the control it was built to provide is never sampled. |
| 36 | time-of-day behavior | _avoid_lunch_\*, _opening_drive_window_\*, ~~power_hour~~\*, _after_opening_range_\* | 4/4 | 0/4 | **Fully exclusive, entirely untested.** `power_hour` is unreachable; the other three are base filters pinned to exactly one template each (REVERSAL, OPENING_RANGE, LIQUIDITY) at 307/307 strategies. There is no REVERSAL strategy *without* `avoid_lunch`, so "does avoiding lunch help" is unanswerable from this universe. |
| 37 | volatility regimes | _regime_trending_\*, _regime_ranging_\*, ~~regime_matches_direction~~\* | 3/3 | 0/3 | **Fully exclusive, entirely untested.** The `regime` group is named in **no** template's `required_groups` or `optional_groups`, so its one SIGNAL condition (`regime_matches_direction`) is structurally unreachable; the two filters are constant inside TREND and MEAN_REVERSION respectively. |
| 38 | volume regimes | ~~relative_volume_high~~, ~~volume_surge~~, _volume_not_thin_ | 0/3 | 0/3 | **Identical set to row 6.** Not a near-duplicate — the same three names, Jaccard 1.00. Two rows, one mapping, zero varied conditions. |

---

## Distinct coverage

### Shared-condition arithmetic

30 of the 73 conditions are cited by more than one spec row. The five conditions
cited by three rows each: `delta_confirms_bar`, `delta_divergence`,
`value_area_breakout`, `prior_day_breakout`, `opening_range_breakout`.

**22 of 38 rows have at least one condition not shared with any other row.**
The 16 that have none:

```
volume, delta, cumulative volume delta, order flow, atr, rsi, macd, liquidity,
breakouts, breakdowns, opening range, previous day high/low,
overnight high/low, session highs/lows, divergences, volume regimes
```

### Containment

Eight rows are a subset or an equal of another row, i.e. they add no condition
the containing row does not already have:

```
volume                  == volume regimes        (Jaccard 1.00)
atr                     ⊂  volatility            (Jaccard 0.67)
delta                   ⊂  order flow            (Jaccard 0.67)
cumulative volume delta ⊂  order flow
divergences             ⊂  delta ⊂ order flow    (Jaccard 0.50 vs delta)
macd                    ⊂  momentum              (Jaccard 0.50)
overnight high/low      ⊂  liquidity
session highs/lows      ⊂  liquidity
```

That leaves **30 rows that are not contained in another row**, and 22 that carry
something of their own.

### Distinctness that survives the generator

Cross the exclusivity test with the reachability test from the next section:

- **17 of 38** rows have ≥1 condition that is both exclusive and genuinely
  varied in a 3 991-strategy universe.
- **9 rows** have an exclusive condition the generator *never* produces:
  supply and demand, vwap, volume profile, open interest, trend continuation,
  market profile, imbalances, time-of-day behavior, volatility regimes.
- **6 rows** have no genuinely-varied condition at all: volume, volume regimes,
  volatility, atr, time-of-day behavior, volatility regimes.

**The honest headline is "22 of 38 distinct, 17 of 38 distinct and exercised" —
not 38 of 38.** The claim that the library went from 31 to 38 is a claim about
the map, and the map is the wrong object to count.

---

## Generated-strategy coverage

`generate_strategies("MNQ", [1,5,15,60], max_total=N)`, N ∈ {150, 1 500, 4 000},
default seed. The dead list is **identical at all three budgets** — this is
structural, not a sampling artefact.

### Conditions in zero generated strategies: 14 of 73 (19 %)

| condition | group | kind | why it is dead |
|---|---|---|---|
| adx_trending | trend | FILTER | FILTER-kind; not in any `base_filters` or `optional_filters` |
| efficiency_high | trend | FILTER | same |
| vwap_proximity | vwap | FILTER | same — and VWAP's `exclusive=(("above_vwap","vwap_proximity"),)` is dead code, since a FILTER can never enter a signal pool |
| relative_volume_high | volume | FILTER | same |
| volume_surge | volume | FILTER | same |
| volatility_expanding | volatility | FILTER | same |
| power_hour | time | FILTER | same |
| away_from_hvn | profile | FILTER | same |
| open_outside_value | profile | FILTER | same |
| fib_sr_confluence | fibonacci | FILTER | same |
| no_recent_imbalance | imbalance | FILTER | same |
| away_from_zone | supplydemand | FILTER | same — and SUPPLY_DEMAND's two `exclusive` pairs referencing it are dead code |
| oi_expanding | openinterest | FILTER | same |
| **regime_matches_direction** | regime | **SIGNAL** | the `regime` group is named in no template's `required_groups` or `optional_groups` |

The mechanism is one line: `_signal_pools()` filters to
`CONDITIONS[n].kind is ConditionKind.SIGNAL`, so a FILTER can only enter a
strategy through `base_filters` or `optional_filters`. Only nine filters are in
any `base_filters` and three in any `optional_filters`; **13 of the 25 registered
FILTER conditions are unreachable by construction**. `regime_matches_direction`
is the one SIGNAL casualty, and it is the only SIGNAL condition in its group.

### A second silent drop: the `volume` group has no SIGNAL conditions

`MOMENTUM` declares `required_groups=("momentum","volume")` and `BREAKOUT`
declares `("structure","volume")`. `_signal_pools` computes
`[p for p in required if p]`, which **silently discards** the empty `volume`
pool. Both templates therefore run as single-required-group templates, and the
"confirmed by participation" / "expansion out of compression **with volume**"
descriptions are not enforced by anything. The same applies to `volume` as an
`optional_groups` entry in six further templates, where it contributes nothing.

### Conditions present but never varied: 9 more

`volatility_normal` (12 of 13 templates, 307/307 each), `volume_not_thin`
(12 templates, 307/307 each), `regime_trending` (TREND 307/307),
`regime_ranging` (MEAN_REVERSION 307/307), `volatility_compressed` (BREAKOUT
307/307), `avoid_lunch` (REVERSAL 307/307), `opening_drive_window`
(OPENING_RANGE 307/307), `after_opening_range` (LIQUIDITY 307/307),
`mtf_not_conflicted` (PULLBACK 307/307).

Each is present in 100 % of the strategies of every template that carries it, so
there is no like-for-like control anywhere in the universe. They cannot be
ablated and their contribution cannot be measured. **Testability tiers: 14 dead,
9 constant, 50 genuinely varied — 68 % of the library.**

### Group reach (3 991 strategies)

```
volatility 100.0%   volume 92.3%   news 76.1%   structure 60.4%   trend 55.9%
momentum 53.8%      orderflow 42.6%   vwap 37.2%   profile 28.1%   meanreversion 23.1%
time 23.1%          liquidity 22.6%   multitimeframe 18.3%   fibonacci 16.2%
supplydemand 16.1%  regime 15.4%   imbalance 12.3%   openinterest 2.5%
```

`test_combinator_draws_from_every_condition_group` passes — but it tests
*groups*, and all 18 groups are reached via filters. It cannot see that 14
conditions inside those groups are dead. The equivalent condition-level assertion
would fail today.

### The optional-filter control is enumerated but never sampled

`_filter_sets()` does put the bare base set at index 0, and
`test_optional_filters_always_include_a_control` verifies that. But the sampler
then draws `per_template` specs uniformly from a candidate list in which every
rule set appears as four filter variants, so the variants almost never travel
together:

| max_total | specs | carry an optional (news) filter | of those, control twin also sampled |
|---|---|---|---|
| 150 | 143 | 107 (74.8 %) | **0 (0.0 %)** |
| 4 000 | 3 991 | 3 038 (76.1 %) | **5 (0.2 %)** |

At 4 000, 3 965 of 3 991 rule-set families are represented by exactly **one**
filter variant. The commit message says news filters are "enumerated as optional
rather than nailed into base_filters, so every filtered variant keeps a
like-for-like control and standing aside for a release is measured instead of
assumed." The enumeration does that; the sample does not. In practice the news
filter is assumed on 76 % of the searched universe and measured on 0.2 % of it.

### Do the generated strategies fire at all?

500 strategies × 4 140 snapshots (15 days): **83 % never fired**.
780 strategies stratified 60-per-template × 20 700 snapshots (45 days):

| template | never fires | fires < 10 times |
|---|---|---|
| SUPPLY_DEMAND *(new)* | **88 %** | 98 % |
| MEAN_REVERSION | 82 % | 92 % |
| PULLBACK | 78 % | 92 % |
| LIQUIDITY | 77 % | 87 % |
| REVERSAL | 75 % | 80 % |
| FIBONACCI *(new)* | 73 % | 87 % |
| OPENING_RANGE | 72 % | 83 % |
| BREAKOUT | 68 % | 75 % |
| TREND | 67 % | 78 % |
| VOLUME_PROFILE *(new)* | 60 % | 78 % |
| VWAP | 60 % | 67 % |
| MULTI_TIMEFRAME | 55 % | 68 % |
| MOMENTUM | 53 % | 75 % |
| **total** | **70 % (545/780)** | **82 % (636/780)** |

This is corroborated out of sample by the desk's own artefact: the last published
MNQ sweep recorded 117 of 150 strategies with zero trades (78 %) over 20 trading
days. The three templates this commit added are at or near the bottom of the
table. **Adding coverage added mostly non-firing hypotheses.**

A large share of that is mechanical. `Strategy.evaluate` returns `None` the
moment two signal conditions disagree on direction. Classifying all 3 991
generated confluences by the worst pairwise directional agreement among their
signal conditions (agreement = P(same direction | both fired), measured over
6 898 snapshots, pairs with ≥50 co-fires):

| bucket | strategies | share |
|---|---|---|
| contradictory (worst pair agrees < 5 % of the time) | 1 130 | **28.3 %** |
| mostly contradictory (< 30 %) | 1 644 | 41.2 % |
| mixed | 1 115 | 27.9 % |
| near-redundant (every pair agrees > 90 %) | 49 | 1.2 % |
| unmeasured (all pairs too sparse) | 53 | 1.3 % |

**63 distinct cross-group signal pairs with < 5 % directional agreement are
available to the combinator, and 28 % of the searched universe contains at
least one of them.** Those strategies consume trial budget and can never fire.
Worst offenders by frequency: `break_of_structure | stoch_extreme` (agreement
0.000, r = −0.746, in 37 strategies), `break_of_structure | rsi_extreme_reversal`
(0.000, r = −0.499, 45), `range_position_extreme | stoch_extreme` (0.000,
r = −0.624, 46), `delta_divergence | range_position_extreme` (0.002, r = −0.367,
51). Templates carry `exclusive` pairs for semantically opposed conditions
*within* a template; nothing does it across the group-diversity axis.

---

## Overlap matrix

Method: all 73 conditions evaluated raw (outside `Condition.evaluate`'s
exception guard) on every third bar of `synthetic_series("MNQ", days=15, seed=5)`
at the 5-minute timeframe, n = 6 898 snapshots; repeated on seeds 11 and 23 for
stability. 2 491 cross-group pairs, 137 same-group pairs.

**A caveat that changes the reading.** Raw Jaccard on trigger-bar sets is
misleading here, because several "directional" conditions fire on essentially
every bar and carry their information in the *direction*, not the trigger:
`above_vwap` 100.0 %, `cvd_directional` 100.0 %, `ema_fast_above_slow` 99.5 %,
`macd_directional` 99.2 %, `price_above_ema50` 98.8 %. Any two of those show
J ≈ 1.00 and mean nothing by it. So I report, for SIGNAL pairs, both Jaccard and
two direction-aware statistics: Pearson r on the per-bar −1/0/+1 verdict, and
agreement = P(same direction | both fired). Agreement is the one that matters for
the combinator, because that is the gate `Strategy.evaluate` applies.

### Cross-group pairs that break the diversity assumption

Filtered to pairs the combinator can actually place together (both groups appear
in the same template) with ≥50 co-fires and agreement ≥ 0.90. `#strats` = how many
of the 3 991 generated strategies contain the pair. `J range` is across seeds
5/11/23.

| J (s5) | J range | r | agree | P(b\|a) | P(a\|b) | #strats | pair |
|---|---|---|---|---|---|---|---|
| 0.831 | – | +0.803 | 0.941 | 0.83 | 1.00 | 25 | `ema_fast_above_slow` (trend) \| `rsi_directional` (momentum) |
| 0.824 | – | +0.835 | 0.960 | 0.83 | 0.99 | 38 | `price_above_ema50` (trend) \| `rsi_directional` (momentum) |
| 0.818 | 0.788–0.818 | +0.888 | 0.993 | 0.92 | 0.88 | 25 | `di_direction` (trend) \| `rsi_directional` (momentum) |
| 0.776 | 0.768–0.806 | +0.752 | 0.928 | 0.78 | 1.00 | 28 | `above_vwap` (vwap) \| `ema_stack` (trend) |
| 0.715 | – | +0.752 | 0.951 | 0.86 | 0.81 | 32 | `ema_stack` (trend) \| `rsi_directional` (momentum) |
| **0.713** | **0.594–0.713** | **+0.839** | **1.000** | **0.94** | **0.74** | **9** | **`prior_day_breakout` (liquidity) \| `value_area_breakout` (profile)** |
| 0.707 | – | +0.705 | 0.926 | 0.79 | 0.87 | 28 | `rsi_directional` (momentum) \| `slope_directional` (trend) |
| 0.666 | 0.610–0.666 | +0.760 | 0.965 | 0.67 | 1.00 | 22 | `price_above_ema200` (trend) \| `value_area_breakout` (profile) |
| 0.526 | 0.401–0.526 | +0.708 | 0.989 | 0.53 | 1.00 | 5 | `price_above_ema200` (trend) \| `prior_day_breakout` (liquidity) |
| 0.514 | 0.484–0.519 | +0.723 | 0.986 | 0.90 | 0.55 | 13 | `oi_price_confirmation` (openinterest) \| `rsi_directional` (momentum) |
| 0.511 | 0.483–0.543 | +0.688 | 1.000 | 0.83 | 0.57 | 17 | `keltner_outside` (meanrev) \| `stoch_extreme` (momentum) |
| 0.510 | 0.510–0.531 | +0.680 | 1.000 | 0.77 | 0.60 | 13 | `bollinger_mean_pull` (meanrev) \| `stoch_extreme` (momentum) |
| 0.501 | 0.382–0.501 | +0.596 | 0.922 | 0.50 | 1.00 | 11 | `above_vwap` (vwap) \| `prior_day_breakout` (liquidity) |
| 0.469 | 0.453–0.469 | +0.666 | 0.992 | 0.96 | 0.48 | 29 | `break_of_structure` (structure) \| `rsi_directional` (momentum) |
| 0.464 | – | +0.646 | 0.985 | 0.92 | 0.48 | 43 | `break_of_structure` (structure) \| `di_direction` (trend) |
| 0.444 | 0.306–0.444 | +0.614 | 0.995 | 0.68 | 0.56 | 23 | `rsi_extreme_reversal` (momentum) \| `vwap_band_extension` (vwap) |
| 0.472 | – | +0.652 | 0.963 | 0.53 | 0.82 | 10 | `di_direction` (trend) \| `oi_price_confirmation` (openinterest) |
| 0.428 | – | +0.568 | 0.953 | 0.85 | 0.46 | 54 | `break_of_structure` (structure) \| `slope_directional` (trend) |
| 0.416 | – | +0.552 | 0.928 | 1.00 | 0.42 | 47 | `break_of_structure` (structure) \| `ema_fast_above_slow` (trend) |

26 cross-group pairs clear agreement ≥ 0.90 with J ≥ 0.40; 9 clear agreement
≥ 0.95 with J ≥ 0.50.

**The brief's hypothesis is confirmed, and slightly understated.**
`value_area_breakout` (profile) and `prior_day_breakout` (liquidity) fire on
71 % of the same bars (J = 0.713, stable 0.594–0.713 across three seeds),
correlate at r = +0.839, and **when they both fire they agree on direction
100.0 % of the time** (0 disagreements over 3 240 co-fires). Conditionally,
P(`value_area_breakout` | `prior_day_breakout`) = 0.94. They are the same
statement — "the close is beyond a prior-session reference level" — computed
against two different reference levels that mostly coincide. A TREND or BREAKOUT
confluence containing both counts one piece of evidence twice and calls itself
one factor more diverse than it is. `price_above_ema200 | value_area_breakout`
(J = 0.666, agree 0.965) and `price_above_ema200 | prior_day_breakout`
(J = 0.526, agree 0.989) close the triangle: on synthetic data, "above the
200 EMA", "above the prior day's range" and "above the prior session's value
area" are one variable in three costumes.

The trend↔momentum block (`rsi_directional` against `di_direction`,
`price_above_ema50`, `ema_fast_above_slow`, `ema_stack`, `slope_directional`;
r = +0.70 to +0.89, agreement 0.93–0.99) is the largest and most frequently
sampled violation — 148 of 3 991 strategies contain at least one of those five
pairs. `rsi_directional` is "RSI above/below 50", which on trending synthetic
data is a slow moving-average crossover under another name. Whether it is
equally redundant on real MNQ is not measurable here.

### What the diversity rule gets right

I should not overstate this. Across all 3 991 generated confluences, the mean
absolute correlation between the signal conditions inside a confluence is
**0.183** (median 0.164, p90 0.335), and only **1.2 %** of confluences have a
mean |r| above 0.5. The group-diversity rule is doing most of its job. The
failure is specific, not systemic: a short list of named pairs — the
breakout-family triangle and the trend/momentum block above — where two groups
encode the same measurement. Those pairs should be added to a cross-group
`exclusive` list (which does not currently exist; `exclusive` is per-template
and cannot express "never pair these two groups' members").

### The other half: anti-correlated pairs

Symmetrically, 63 cross-group pairs have agreement < 5 %, i.e. a confluence
containing both can essentially never fire (see previous section). The diversity
rule is blind in both directions: it assumes different groups means
*independent*, and the data says different groups can mean *identical* or
*mutually exclusive*, and 69.5 % of the generated universe sits in one of those
two failure modes.

---

## The price of coverage

### How much the hypothesis space grew

Counting the enumerable candidates per template *before* sampling
(rule sets × filter sets × timeframes × exits), for MNQ over tf 1/5/15/60:

| | `dd8d171` | `f82bd35` | ratio |
|---|---|---|---|
| conditions (SIGNAL) | 52 (36) | 73 (48) | ×1.40 (×1.33) |
| condition groups | 12 | 18 | ×1.50 |
| templates | 10 | 13 | ×1.30 |
| filter sets per template | 1 | 4 | ×4.00 |
| **enumerable candidates** | **411 372** | **4 747 536** | **×11.54** |
| per-template sample slots at `max_total`=4 000 | 400 | 307 | ×0.77 |
| sampling density (sampled / enumerable) | 0.97 % | **0.084 %** | ×0.087 |

Per-template growth: BREAKOUT ×12.5, MEAN_REVERSION ×14.0, TREND ×8.8,
MULTI_TIMEFRAME ×6.8, plus three wholly new templates contributing 891 072
candidates. The optional-filter dimension alone accounts for ×4.0 of the ×11.54;
the extra conditions and groups account for ×2.9.

### What that does to the significance bar

The gate is `deflated_expectancy(m, trials) > 0`, i.e.
`t > sqrt(2·ln(trials))`, and expectancy scales linearly in t
(`expectancy = t · std_r / sqrt(n)`). So the percentage increase in required
expectancy is exactly the percentage increase in `sqrt(2·ln(trials))`.

| basis for `trials` | before | after | required expectancy |
|---|---|---|---|
| **as coded** — `trials` = strategies backtested, `max_combinations` unchanged at 4 000 | 4.073 | 4.073 | **+0.00 %** |
| hypothesis space actually searchable | 5.085 | 5.545 | **+9.05 %** |
| `max_total` needed to hold the old sampling density (4 000 → 46 163) | 4.073 | 4.635 | **+13.79 %** |

**This is the finding on this axis, and it cuts the opposite way from what the
brief anticipated.** `_trials_searched()` reads `trials_searched` from the
published sweep, which is `len(sweep.strategies)` — the number of strategies
*backtested*, capped by `max_combinations`. Because `max_combinations` did not
change, **the deflation term did not move at all.** The desk searched an
11.5× larger space and the multiple-testing correction is numerically identical.

So the bill was not paid in deflation. It was paid in two other currencies:

1. **Dilution.** Any given hypothesis is now 11.5× less likely to be tested at
   all (0.97 % → 0.084 %), and each of the ten original templates gets 23 %
   fewer slots (400 → 307) drawn from a 6.8–14.0× larger pool. The commit did
   not make the research more thorough; at a fixed budget it made it thinner
   everywhere while making the menu longer.
2. **Budget spent on hypotheses that cannot produce a statistic.** 76.1 % of the
   sampled universe carries a news filter whose control is absent (0.2 % paired);
   28.3 % contains a self-contradicting signal pair; 70 % never fires. Of 4 000
   nominal trials, roughly 1 200 can generate a trade.

Concretely, in the units the brief asked for: **a strategy that was borderline
before needs 0 % more expectancy under the correction as it is currently coded,
9.0 % more if `trials` is honestly set to the space that was searched, and
13.8 % more if the desk raises `max_combinations` to 46 163 to search the new
space as densely as it searched the old one.** The third is the number that will
bite, because searching 4.7 M candidates at 4 000 samples is not a search.

### And a correction that runs the other way

`trials` counts strategies *backtested*, including the ones with zero trades. In
the last published MNQ run, 33 of 150 produced any trade. The maximum of a set
is taken over the 33 that produced a statistic, not over all 150:
`sqrt(2·ln 33)` = 2.644 versus `sqrt(2·ln 150)` = 3.166 — **the applied
deflation is 19.7 % stronger than the live-trial count justifies**. So the
present gate is simultaneously too harsh on the axis it measures (non-firing
strategies inflate `trials`) and too lax on the axis it does not (the sampled
space is 4.7 M, not 4 000). These do not cancel; they are errors in different
quantities, and both are unstated.

Worked example on the desk's own published finalist,
`MNQ-15m-e7ead0b85ce4` (TREND, n = 99, expectancy +0.1974 R, t = +1.58, so
`std_r/sqrt(n)` = 0.1249 R per t-unit):

| `trials` basis | required expectancy | shortfall |
|---|---|---|
| 33 (strategies that traded) | 0.330 R | ×1.67 |
| 150 (as actually run) | 0.396 R | ×2.00 |
| 4 000 (config default) | 0.509 R | ×2.58 |
| 411 372 (old space) | 0.635 R | ×3.22 |
| 4 747 536 (new space) | 0.693 R | ×3.51 |

The best thing the last sweep found needs to double its expectancy to clear the
bar it was actually measured against, and to treble it to clear the bar the
searched space implies. That was true before this commit and it is slightly more
true after it. It is not an argument against widening coverage; it is an argument
that widening coverage without widening the sample, fixing the 14 dead
conditions, the 63 contradictory pairs and the unsampled controls, buys menu
length rather than evidence.

---

## What I could not measure

- **Real-market overlap.** All trigger statistics are on synthetic bars. The
  structural findings (dead conditions, empty pools, unreachable groups, unpaired
  controls, hypothesis-space arithmetic) are code properties and hold regardless;
  the Jaccard/agreement figures are not, and the trend↔momentum block in
  particular may be a property of the synthetic generator's trend persistence.
  The breakout-family triangle is mechanically over-determined (three conditions
  that all test "close beyond a prior reference level") and I expect it to
  survive on real data, but I have not shown that.
- **Whether any of the 38 variables has an edge.** Nothing here is a statement
  about profitability. The whole audit is about whether the system is in a
  position to find out.
- **Open interest.** Synthesised from close−open in the test fixture and in my
  harness; the repo has no real OI series, so `oi_price_confirmation`'s 50.3 %
  trigger rate and r = +0.723 against `rsi_directional` are artefacts of how the
  fixture manufactures OI, not measurements of open interest.
- **Per-condition contribution.** The library docstring promises ablation
  ("the system can ablate any one of them and measure what it actually
  contributed"). I found no ablation routine in `backtest/`, and 9 of the 73
  conditions are constant within their templates, so ablation is not possible for
  them even in principle at present.
