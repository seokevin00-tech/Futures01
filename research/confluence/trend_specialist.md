# Trend & Continuation review of the 52 -> 73 condition layer

Seat: `futures_agents/agents/research_trend.py` (TREND / PULLBACK / MOMENTUM /
MULTI_TIMEFRAME). Design review, not a sweep.

## Measurement basis

All numbers below come from `synthetic_series("MNQ", days=15, seed=S)` ->
`build_symbol_frame(s, [1,5,15,60])`, evaluated **once per 5-minute bar**
(~4,140 bars/seed), seeds `5, 11, 23, 42, 77`. Strategy counts use `days=30`.

Two honesty notes that constrain everything I claim:

* **`MNQ`/`MES`/`MGC` at the same seed are the same price path** - bar-to-bar
  return correlation 0.998 (MNQ/MES) and 0.999 (MNQ/MGC); different seeds are
  0.03. An earlier 3-symbol x 3-seed run of mine looked like 9 replications and
  was 3. Every pooled figure here is **5 independent paths from one generator**,
  not five markets.
* **The tree moved under me.** I measured at `HEAD f82bd35` with an already
  dirty working tree; during my session someone else's fixes landed in
  `features.py` (an `sr_levels` cache look-ahead fix), `library.py` (`_fib_sr`
  ratio direction) and `structure.py` (`SDZone` frozen). None touch
  `swing_leg`, `_fib_pullback`, `_imb_pull` or `supply_demand_zones`, so
  findings 1-8 and 11 are unaffected. I re-ran the two numbers that do depend
  on `sr_levels` (findings 9 and 13) on the post-fix tree and report the
  post-fix values; the reachability count (finding 10) re-verifies at 14/73
  after the change.
* **My effect metric is mean forward move in ATR, drift-adjusted** (the
  sample's unconditional mean forward move is subtracted, then signed by the
  condition's own direction). It is *not* R. It ignores stops, targets,
  scale-outs and cost. It is a diagnostic of whether a condition points the
  right way, not a claim of edge. See "Where I expect disagreement".

---

## Verdict

**Partially, and not where it matters most to this seat.**

The seven groups close a *registration* gap, not a *tradeability* gap.
`coverage_report()` prints `covered: 38, MISSING: none` while **14 of the 73
conditions can never be drawn by the combinator**, including 2 of the 7
conditions the map assigns to the variable literally named "trend continuation"
(finding 10).

Of the two new groups a continuation trader would actually reach for, both are
close to unusable at this desk's own 30-trade floor: the `FIBONACCI` template
yields **6 rankable strategies per 400 generated** and `SUPPLY_DEMAND` yields
**1 per 400**, against 32 for `TREND` and 45 for `MULTI_TIMEFRAME`
(finding 11).

The one genuinely valuable new idea for continuation - displacement, then
pullback, then resume - is implemented **without the pullback**, and the
pullback is where the entire measured effect lives: +0.219 ATR with a real
retrace test versus -0.016 ATR without it, inside the same bar window
(findings 4-5). That is the single highest-value fix on the board from my side.

---

## Findings

### 1. The Fibonacci leg is a bounding box of two swings, not an impulse
**Claim.** `swing_leg()` returns `(last_swing_low, last_swing_high)` with
direction decided purely by which index is newer
(`futures_agents/features.py:108-120`). That is not the leg a continuation
trader measures from, and it is not even stable.

**Evidence.** Over 4,043 bars with a leg available (seed 5, 5m):

| leg direction vs `structure_trend` | n | % |
|---|---|---|
| agrees (UP/UPTREND, DOWN/DOWNTREND) | 1,358 | 33.6% |
| opposes | 1,206 | 29.8% |
| structure RANGE/UNDEFINED | 1,479 | 36.6% |

Median leg length `|hi_i - lo_i|` = **5 tf bars** (25 minutes), p95 = 14. The
leg direction **flips 612 times in 4,140 bars**, median 5 bars between flips,
p25 = 3. A "confirmed swing leg" that reverses every five bars is a micro
oscillation, not the impulse that broke structure. Mechanically the flip is
forced: once the pullback low after an up impulse gets fractal-confirmed,
`lo_i > hi_i` and the same price action is relabelled a DOWN leg.

**Confidence: high** (code + measurement, one sample but structural).

### 2. The sign is defensible; the anchoring is the defect
**Claim.** LONG on a retracement of an UP leg is the right sign *in principle*.
It is untestable as shipped because the signal carries no information - but the
subset where the leg opposes structure is measurably harmful.

**Evidence.** `fib_golden_pocket`, drift-adjusted edge in its own direction,
5 independent seeds:

| variant | pooled n | H=6 (30m) | H=12 (60m) | seeds positive at H6 |
|---|---|---|---|---|
| as shipped | 1,964 | **-0.033 ATR** | -0.048 | 2/5 |
| + `structure_trend` agrees with the leg | 762 | **+0.030 ATR** | +0.059 | 2/5 |
| + `structure_trend` **opposes** the leg | 562 | **-0.211 ATR** | -0.407 | 1/5 |

The ~30% of firings where the leg opposes structure are where the damage is.
Anchoring to the structural impulse is the fix - but note what the numbers
actually support: **it removes harm, it does not create an edge** (+0.030 ATR
is nothing).

**Confidence: medium-high** that the opposing subset is harmful (4/5 seeds
negative, magnitude -0.21/-0.41); **medium** that structure-agreement is the
right gate.

### 3. `fib_shallow_retrace` points the wrong way more often than right - weakly
**Claim.** Pooled-negative in its own direction on both sides.

**Evidence.** Pooled over 5 seeds: **-0.091 ATR at H6, 1/5 seeds positive**
(n=1,749); with the structure gate, -0.052. On MNQ seed 5 alone the SHORT side
read -0.342 ATR (t=-2.01) across four horizons, which is what first drew my
attention - **it did not replicate at that magnitude**. I am reporting the
pooled number, not the one that made the better story.

**Confidence: low-medium.** -0.09 ATR is far inside the noise of any shipped
stop (1.0-2.5 x ATR). This is my most vulnerable finding and I would concede it.

### 4. `imbalance_pullback` contains no pullback
**Claim.** "A displacement happened recently" is the *whole* test. The only
gate is `1 <= since <= 10` (`library.py`, `_imb_pull`).

**Evidence.** Comparing close at the firing bar to close at the displacement
bar, over 1,185 firings (seed 5):

* pulled back at all: **601 (50.7%)** - a coin flip
* still extended: 584 (49.3%)
* retrace in ATR (>0 = pulled back): p10 **-1.66**, median **+0.03**, p90 +1.76

One firing in ten is more than 1.6 ATR *further* in the displacement direction
than the bar that caused it. It fires on **27-28.6% of all bars**, against
`imbalance_bar`'s 3.7%.

**Confidence: high.**

### 5. The fix is not ATR-relative or structure-relative. It is displacement-relative
**Claim.** The bar window does nothing; a real retrace test does all the work.

**Evidence.** Holding the window fixed at `since <= 3` and splitting on whether
price retraced >= 33% of the displacement bar's range:

| variant | pooled n | H=6 | H=12 | seeds positive at H6 |
|---|---|---|---|---|
| as shipped (1-10 bars, no retrace test) | 5,629 | -0.019 | -0.052 | 2/5 |
| `since<=3` **+ real retrace >= 33%** | 394 | **+0.219 ATR** | -0.063 | **4/5** |
| `since<=3`, **no** retrace | 1,629 | -0.016 | -0.031 | 2/5 |

Same window, same displacement, opposite results. The retrace test is the
active ingredient. It also cuts the firing rate from ~28% to ~1.9% of bars.

The effect is **short-horizon only** - gone by H=12 (-0.063). The shortest
shipped exit (`StopKind.ATR 1.2, targets_r=(1.2,), time_stop_bars=30`) holds
for 30 primary-tf bars, which on 5m is 150 minutes: five times longer than the
window in which this signal has any content. That mismatch is testable.

**Confidence: medium-high** (4/5 seeds, n=394 pooled, ~79/seed - thin, and H12
kills it).

### 6. The bars-since decay is inert, and floored where the edge is worst
**Claim.** `strength` changes no backtest result, and the formula's floor sits
exactly on the harmful tail.

**Evidence.** `ConditionResult.strength` is consumed only at
`strategies/base.py:449` (`strength_sum`) and `:454` (`Evidence.weight`),
surfacing as `StrategySignal.strength`. Nothing under `futures_agents/backtest/`
reads it - it gates nothing, sizes nothing. Separately,
`max(0.4, 1.0 - since/12)` floors at 0.4 from `since=8` onward, and measured
edge by bucket (H=6, seed 5): `since 1` +0.031, `since 2-3` +0.048,
`since 4-6` -0.008, **`since 7-10` -0.193**. The decay stops decaying precisely
where the signal turns harmful.

**Confidence: high.**

### 7. It is not a restatement of `imbalance_bar` - it is worse than one
**Claim.** Only its smallest slice is a lagged copy; the rest is a regime flag.

**Evidence.** `imbalance_bar` at bar *t* -> `imbalance_pullback` in the **same
direction** at *t+1*: **148/162 = 91.4%** (the 8.6% miss is a new displacement
resetting `since` to 0). But `since=1` is only **12.5%** of firings; the
distribution is near-flat out to `since=10` (12.5% -> 7.8%). So 87.5% of its
firings are "a big bar happened up to 50 minutes ago". Pooled edge:
`imbalance_bar` +0.065 ATR H6 vs `imbalance_pullback` -0.019.

**Confidence: high.**

### 8. `fresh_zone_approach` fires early enough that the shipped stops land inside the zone
**Claim.** The +-0.75 ATR band is too wide for the stop geometry a pullback
entry depends on.

**Evidence.** 101 firings across 3 seeds. Distance to the proximal edge at
firing: p10 0.26, **median 0.56 ATR**, p90 0.67; **76% fire more than 0.40 ATR
away**. Median zone height is **0.72 ATR** (p10 0.31, p90 1.74). Where a
1.0 x ATR stop from the entry close actually lands:

| shipped ATR stop | inside the zone | beyond the distal edge (correct) |
|---|---|---|
| 1.0 x ATR | **72 (71%)** | 29 (29%) |
| 1.2 x ATR | **47 (47%)** | 54 (53%) |
| 1.5 x ATR | 12 (12%) | 89 (88%) |
| 2.5 x ATR | 0 | 101 (100%) |

With the two tightest of the four shipped ATR exits the trade is stopped out by
the very level it is trading. That is the continuation-specific complaint: a
pullback entry's entire premise is that the stop sits beyond the level.

**Counter-evidence I found and am reporting.** "Too early" is a *geometry*
problem, not a signal problem: **88%** of firings are moving toward the zone at
the firing bar and **71%** reach it within 20 bars (65% of the >0.40 ATR
"early" ones). And the single strongest positive number anywhere in the new
layer was `fresh_zone_approach` SHORT at +1.03 ATR H6 (t=+2.24) - on **n=26,
one seed**, far below my own sample floor. I make no claim on it.

**Confidence: high** on the geometry; **none** on the direction result.

### 9. `away_from_zone` cannot be harmful to a continuation strategy, twice over
**Claim.** It is undrawable, and even if drawn it would block almost nothing.

**Evidence.** It is `ConditionKind.FILTER`. `_signal_pools()` admits only
`ConditionKind.SIGNAL` into required/optional groups, and filters reach a
strategy **only** through `base_filters`/`optional_filters` - `away_from_zone`
appears in neither, in any of the 13 templates. The two `exclusive` pairs
naming it (`combinator.py:261-262`) are dead code, and `_violates_exclusive` is
applied only to `spec.signal_conditions` anyway, so they could never have fired.

Measured as if it were wired in (post-fix tree, 12,420 bars, 3 seeds): it
passes on **97.6%** of all bars, and would block **2.8%** of
`fib_golden_pocket`, **1.7%** of `fib_shallow_retrace`, **2.0%** of
`pullback_to_support` and **2.9%** of `imbalance_pullback` firings.
(It blocks 100% of `zone_touch` by construction, which is what the dead
`exclusive` pair was for.)

The premise of the question - that it forbids initiating where a pullback entry
lives - is sound in principle and quantitatively negligible here, because the
zones are rare (`zone_touch` 1.5% of bars, `fresh_zone_approach` 0.8%).

**Confidence: high.**

### 10. `coverage.py` proves registration, not reachability - and "trend continuation" is 5/7
**Claim.** The module's stated purpose ("not merely computed somewhere") is
exactly the claim its check does not make.

**Evidence.** `uncovered()` tests `n not in CONDITIONS` - registration only. A
condition that is registered but that no template can draw passes. Measured
against the actual templates: **14 of 73 conditions are undrawable**, touching
**15 of the 38 spec variables** that `coverage_report()` prints as covered:

```
trend continuation    5/7 drawable   undrawable: adx_trending, efficiency_high
volume / volume regimes 1/3          undrawable: relative_volume_high, volume_surge
fibonacci levels      3/4            undrawable: fib_sr_confluence
imbalances            2/3            undrawable: no_recent_imbalance
supply and demand     2/3            undrawable: away_from_zone
open interest         1/2            undrawable: oi_expanding
market profile        3/4            undrawable: open_outside_value
volume profile        4/5            undrawable: away_from_hvn
volatility / atr      2/3, 1/2       undrawable: volatility_expanding
volatility regimes    2/3            undrawable: regime_matches_direction
vwap                  4/5            undrawable: vwap_proximity
support/resistance    1/2            undrawable: fib_sr_confluence
time-of-day behavior  3/4            undrawable: power_hour
```

4 of the 14 are new (`fib_sr_confluence`, `no_recent_imbalance`,
`away_from_zone`, `oi_expanding`); 10 predate this change. The ones I own:
**`adx_trending` and `efficiency_high` are FILTER-kind in the `trend` group and
appear in no template's filter list**, so the two conditions that most directly
express "is this actually trending" cannot enter any generated strategy. No
test asserts reachability (`tests/test_strategy_library.py`, 17 passing).

Also: `oi_price_confirmation` is drawable but fired **0 times in 10,348
snapshots** (no OI on this feed) - documented and deliberate, but it means any
`MOMENTUM`/`BREAKOUT` confluence that draws it produces zero trades.

**Confidence: high.**

### 11. `FIBONACCI` and `SUPPLY_DEMAND` do not produce rankable strategies
**Claim.** Both fall below this desk's own `MIN_TRADES_FOR_RANK = 30`.

**Evidence.** MNQ, 30 days, 400 strategies generated per group, full portfolio
backtest:

| group | generated | traded at all | **>= 30 trades** | median trades |
|---|---|---|---|---|
| FIBONACCI | 400 | 91 | **6** | 4 |
| SUPPLY_DEMAND | 400 | 70 | **1** | 4 |
| PULLBACK | 400 | 64 | 4 | 2 |
| TREND | 400 | 144 | 32 | 8 |
| BREAKOUT | 400 | 60 | 8 | 4 |
| VOLUME_PROFILE | 400 | 147 | 39 | 4 |
| MOMENTUM | 400 | 134 | 40 | 6 |
| MULTI_TIMEFRAME | 400 | 156 | 45 | 6 |

The cause is the required pair. Since `Strategy.evaluate` returns `None` the
moment two signals disagree on direction (`base.py:445-447`), `FIBONACCI`'s
`fibonacci + trend` pair survives on **1.1-4.0% of bars** depending on which
trend condition is drawn (`fib_golden_pocket + di_direction` = 1.14%;
`+ price_above_ema200` = 3.99%), against `TREND`'s `trend + structure` pair at
**35-40% of bars**. `FIBONACCI` is roughly ten times more selective than the
template it is meant to sit beside, before filters, RTH and exits.

**Confidence: high.**

### 12. Continuation templates should not draw `supplydemand`; `profile` is a different problem
**Claim.** Adding `supplydemand` as an optional to a direction-agreeing
confluence is close to multiplying by zero; adding `profile` adds a group label
without adding conditioning.

**Evidence.** `zone_touch` fires on 1.5% of bars, `fresh_zone_approach` on
0.8%. `PULLBACK` and `MULTI_TIMEFRAME` both list `supplydemand` as optional;
`PULLBACK` gets 4 rankable strategies per 400. At the other end,
`value_area_breakout` (in `TREND`'s optional `profile`) fires on **60.9%** of
bars - it satisfies the diversity rule's group counter while conditioning on
almost nothing.

**Confidence: high** on the rates; **medium** on the prescription.

### 13. I looked for diversity dilution across the new groups and mostly did not find it
**Claim.** The new groups are not restatements of the existing ones. Reporting
this because a challenge list that only ever shows hits is not evidence of rigour.

**Evidence.** Cross-group co-firing lift over 12,420 bars (3 seeds):

| pair | P(B\|A) | base rate of B | lift | same direction |
|---|---|---|---|---|
| `fib_golden_pocket` x `pullback_to_support` | 0.21 | 0.149 | 1.39 | 0.59 |
| `imbalance_pullback` x `break_of_structure` | 0.46 | 0.407 | 1.13 | 0.86 |
| `imbalance_pullback` x `ema_stack` | 0.82 | 0.783 | 1.05 | 0.73 |
| `fib_golden_pocket` x `zone_touch` | 0.02 | 0.015 | 1.33 | 0.95 |

Nothing here is a hidden restatement. The real threat to the diversity rule is
**selectivity, not correlation** - and the worst offenders (`cvd_directional`
100% of bars, `rsi_directional` 82%, `ema_stack` 78%) predate this change and
are not mine to claim.

**Confidence: high.**

---

## What I would add

Three conditions, plus one I tested and am not filing.

### A. `impulse_retrace` (group `fibonacci`) - replaces the anchor, not the sign
**Rule.** On `structure_event == "BOS_UP"` at bar *b*, fix the leg as
(the confirmed swing low immediately preceding the break, the highest high made
since *b*). Fire LONG while `close` is inside the 0.382-0.786 retracement of
**that** leg, while the leg's origin low is unviolated. Expire the leg on
`BOS_DOWN`. Mirror for shorts.

**Why, with evidence.** The shipped leg opposes structure on 29.8% of bars
(finding 1) and that subset measures **-0.211 ATR H6 / -0.407 H12**, 1/5 seeds
positive; the agreeing subset measures +0.030 (finding 2). Anchoring to the
break removes the harmful subset by construction rather than by a co-firing
filter, and it gives the leg a defined lifetime instead of flipping every five
bars.

**Test.** Head-to-head against `fib_golden_pocket` on identical exits; the bar
to clear is pooled edge > 0 on >= 4/5 seeds *and* >= 30 trades in a 30-day
window, which `FIBONACCI` currently misses 394 times out of 400.

### B. `displacement_retest` (group `imbalance`) - the pullback `imbalance_pullback` is missing
**Rule.** `recent_imbalance` is 1-3 bars old **AND** price has retraced >= 33%
of the displacement bar's range back toward its origin **AND** has not closed
beyond that origin. Direction = the displacement's direction.

**Why, with evidence.** Pooled **+0.219 ATR at H6, 4/5 seeds positive**
(n=394), against -0.016 for the same window without the retrace test and -0.019
for the shipped condition (finding 5).

**Test.** Pair it with an exit shorter than its own decay - the effect is gone
by H=12 (-0.063), while the shortest shipped exit runs 30 primary-tf bars. This
is the one place in the new layer where I would spend walk-forward budget.

### C. `zone_stop_room` (group `supplydemand`, `ConditionKind.FILTER`) - and wire it into a template
**Rule.** For a long, require the nearest demand zone's **distal** edge to sit
beyond the stop the strategy would actually use:
`entry - stop_mult * ATR <= zone.bottom`. Mirror for shorts. Pass when no zone
is within range.

**Why, with evidence.** 71% of `fresh_zone_approach` entries at 1.0 x ATR and
47% at 1.2 x ATR put the stop **inside** the zone (finding 8); median zone
height 0.72 ATR against a median entry distance of 0.56 ATR. This is the
condition that makes a zone tradeable for continuation rather than decorative.

**Caveat I must state:** filing it as a FILTER means it lands in the same dead
pool as the other 14 undrawable conditions unless it is added to a template's
`base_filters`/`optional_filters` (finding 9). The condition is worthless
without that wiring.

### Not filed: `zone_held_resumption` - I built it and it failed
The question posed to me was whether the interesting zone for a trend trader is
the one price returns to and holds. I implemented it - a zone with `touches >= 1`,
price back outside on the correct side within 0.5 ATR, direction = away from the
zone - and measured it: **pooled -0.200 ATR H6, 1/5 seeds positive, n=308**.
It does not work in this sample. `fresh_zone_approach`'s freshness requirement
is therefore not obviously the wrong selection, and I am not filing a
replacement for it. Recording this because "I looked and found nothing" is a
result.

Nothing else. I am not filing an ADX/efficiency condition - `adx_trending` and
`efficiency_high` already exist and need wiring, not writing (finding 10).

---

## Where I expect the other specialists to disagree with me

1. **My metric is not R, and the quant seat should say so.** Mean forward move
   in ATR ignores stops, targets, scale-outs and cost. A +0.219 ATR mean move
   at H=6 sits inside the noise of a 1.0-1.5 ATR stop and may not survive the
   round-turn cost. **I think this objection is correct**; I have run no
   trade-level backtest of proposal B and I state its effect as diagnostic, not
   as an edge. If someone re-runs it in R and it vanishes, the finding stands
   (the retrace test separates the population) but the recommendation weakens.

2. **Five seeds of one synthetic generator is not five regimes.** I caught
   myself claiming 9 replications when MNQ/MES/MGC at a seed are the same path
   (corr 0.998+). I have corrected that, but 5 paths from one generator is still
   not out-of-sample evidence, and every pooled number here should be read as
   one generator's opinion. This is exactly the objection I would file against a
   rival.

3. **Finding 3 (`fib_shallow_retrace` sign) is my weakest and I would concede
   it.** -0.091 ATR pooled, 1/5 seeds positive. The single-seed result that
   first suggested an inverted sign (-0.342 ATR, t=-2.01) did not replicate at
   that magnitude. If anyone shows it is noise, I withdraw it.

4. **Finding 8's geometry assumes a market entry at the close.** It does -
   `Strategy.evaluate` sets `entry = spec.round_to_tick(snap.price)`. The
   mean-reversion/liquidity seat may argue the 0.75 ATR band is a *feature*,
   because a real desk rests a limit at the proximal edge and the fill is at the
   zone, not 0.56 ATR above it. If the engine ever supports resting entries, my
   stop-inside-the-zone claim dissolves entirely. Against today's engine it holds.

5. **Finding 9 may be dismissed as a combinator bug, not a condition defect.**
   Fair. But the question asked was whether `away_from_zone` is actively harmful
   to a continuation strategy, and the answer is that it cannot be - undrawable,
   and blocking 1.7-2.9% of continuation entries even if drawn.

6. **I am handing the reversion seat a result that favours it.** `zone_touch`
   SHORT measured +0.517 ATR at H6 (t=+2.49, n=48) and `fresh_zone_approach`
   SHORT +1.03 ATR (t=+2.24, n=26) on single seeds. Both are below my own
   30-trade floor so I claim nothing, but if the reversion seat finds the same
   thing at size, the honest reading of the `supplydemand` group is that it is a
   **fade** group that continuation templates should not be drawing at all -
   which would strengthen my finding 12 and weaken my proposal C.

7. **Finding 11's trade counts are one symbol, one 30-day window.** A longer
   window will lift every count; what I claim is the *ratio* (FIBONACCI 6 vs
   MULTI_TIMEFRAME 45 from identical budgets), not the absolute floor failure.
