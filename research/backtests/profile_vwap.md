# VOLUME_PROFILE and VWAP - backtest seat

Seat: volume profile & VWAP. Families owned: `VOLUME_PROFILE`, `VWAP`. Symbol: **MNQ only**.
Nothing here transfers to MES, MGC or CL; I did not test them and I make no claim about them.

**Measurement basis.** Everything below was run against **commit `5b3ae0c`**. Five independent
synthetic price paths, `synthetic_series("MNQ", days=120, seed=N, end_date=date(2026,3,17))`
for `N = 1..5`, 165,600 one-minute bars each, frames at 5/15/60/240m, default `CostModel`
(commission + exchange fees per round turn, volatility-scaled slippage, stop orders charged
more). Strategy universe per path: `generate_strategies("MNQ", [5,15,60],
groups=["VOLUME_PROFILE","VWAP"], max_total=400)` - 200 VOLUME_PROFILE + 200 VWAP.

**This is synthetic data. Nothing in this document is a live edge.** The generator's own
docstring says results should hover around break-even before costs and be negative after them;
they do. What synthetic data *can* settle is firing rates, sample sizes, geometry, paired
before/after comparisons on identical bars, and whether a measured ranking survives being
re-measured on a different path. Those are the questions I answered.

**Reproducibility note.** Since I ran, HEAD moved to `0df691f`, which added `execution_tf` and
`trigger_conditions` to the `Strategy.strategy_id` hash. I verified the **rule-set multiset is
unchanged** (400 rule sets, 0 added, 0 removed, `(group, tf, name)` multiset identical), but
every `strategy_id` string in this document is from `5b3ae0c` and will not resolve at HEAD.
Match my rows by `(group, primary_tf, name)`.

---

## Verdict

**Nothing is published. Zero live-eligible strategies in either of my families.** Both fail
walk-forward, and the whole ranking fails to replicate across price paths.

- **VOLUME_PROFILE fails walk-forward badly.** Anchored, 5 folds, seed 1: three of the five
  folds selected *nothing at all* (no strategy had 20 in-sample trades and a positive
  `robust_score`). The two folds that did select produced 24 out-of-sample trades at
  **-0.3558R**, against +0.0116R in sample - efficiency **-30.6**, selection stability
  **0.000**. 24 OOS trades is below the 30-trade floor in any case, so even the failure is
  under-powered.
- **VWAP fails walk-forward less dramatically and just as decisively.** Same setup: 2,219 IS
  trades at +0.1320R became 466 OOS trades at **-0.0294R**. Efficiency **-0.223** (negative -
  the sign flips out of sample, which is worse than mere decay). Selection stability 0.950: it
  picks essentially the same five strategies every fold, and they lose out of sample every time
  after fold 2.
- **The ranking does not replicate across price paths.** The 22 strategies that cleared 30
  trades on seed 1 were positive on only **30 of 88** re-tests on the other four paths
  (34.1%; a coin flip is 50%). Pairwise Spearman correlation of expectancy across paths, over
  the 25 rule sets with >=20 trades on all five: **rho = -0.031** (range -0.760 to +0.598).
  Rank a strategy on one path and you have learned nothing about the next one.
- **One rule set replicated directionally and still fails deflation.** VWAP 60m
  `above_vwap + break_of_structure + ema_stack + rsi_directional` was positive on 4 of 5 paths,
  pooled n=1,098, **+0.0263R, t=+2.142**, P(ruin) 0.02% at $240/trade, parameter sensitivity
  `worst_relative` 0.779. It is the best thing I found and I am still withholding it: the
  deflation charge at 400 ranked hypotheses is 3.462 t-units and at the 25 that survived the
  floor on all five paths it is 2.537 - both larger than the 2.142 observed. Deflated
  expectancy **+0.000R**. See [What I withheld](#what-i-withheld-and-why).
- **The interesting large samples are mechanical, and VOLUME_PROFILE is not even the worst
  offender.** `above_vwap` fires on **100.0%** of bars at 5m, 15m and 60m. It is not an event,
  it is an arithmetic tautology (price is above VWAP or below it). `value_area_breakout` fires
  on 66.8% of 15m bars. In a controlled minimal-pair test those two anchors produce a median of
  643 and 351 trades over 120 days, against 21 for `value_area_edge` and 11 for
  `lvn_rejection` - from an identical partner set, exit and filter. See
  [Is the large sample real?](#is-the-large-sample-real).
- **Of the two proposed fixes, one is a measurement no-op and the other is a correctness fix
  with no performance payoff.** The inverted `strength` produces a **bit-identical R-series in
  69 of 69** rule sets, because nothing in the backtest, metrics, risk or decision path reads
  `ConditionResult.strength`. The RTH-only prior profile reproduces the reversion seat's
  geometry on an independent path and moves pooled expectancy by +0.0014R. See
  [The two proposed fixes](#the-two-proposed-fixes-tested).

---

## Seed replication

Five independent price paths. Per the brief, MNQ/MES/MGC at one seed are one observation, so
all five observations here are MNQ at five different seeds.

### The whole sweep, path by path

| seed | strategies | trades | traded | cleared 30 | of those positive |
|---|---|---|---|---|---|
| 1 | 400 | 3,918 | 165 | 22 | 19 |
| 2 | 400 | 4,288 | 180 | 26 | 7 |
| 3 | 400 | 4,225 | 178 | 25 | 6 |
| 4 | 400 | 4,302 | 178 | 27 | 14 |
| 5 | 400 | 3,928 | 175 | 22 | 6 |

Seed 1 - the path I would have ranked on if I had run one seed - has 19 of 22 floor-clearers
positive. Seeds 3 and 5 have 6 of 22-25. That spread is the finding: **which path you draw
decides whether the family looks like an edge or like nothing.**

### Mean expectancy of the floor-clearers, by family and path

| seed | VOLUME_PROFILE traded / >=30 / mean exp / best t | VWAP traded / >=30 / mean exp / best t |
|---|---|---|
| 1 | 65 / 5 / **+0.0837R** / +0.575 | 100 / 17 / +0.0108R / +1.232 |
| 2 | 79 / 9 / **-0.1825R** / -0.752 | 101 / 17 / +0.0027R / +0.895 |
| 3 | 82 / 5 / **-0.1939R** / -1.132 | 96 / 20 / -0.0919R / +0.773 |
| 4 | 69 / 5 / **+0.0244R** / +0.268 | 109 / 22 / -0.0171R / +0.819 |
| 5 | 77 / 5 / **-0.0637R** / -0.305 | 98 / 17 / -0.0532R / +2.661 |

VOLUME_PROFILE's mean expectancy over floor-clearers swings from +0.0837R to -0.1939R across
paths - a 0.28R range on the same 200 rule sets. That is not a family with an edge whose size
is uncertain; that is a family with no edge being resampled.

### Every-trade pooled expectancy, no selection at all

| family | trades (5 paths) | total R | mean R |
|---|---|---|---|
| VOLUME_PROFILE | 3,463 | -144.6R | **-0.0418R** |
| VWAP | 17,198 | -272.8R | **-0.0159R** |

Both families lose money per trade once you stop selecting, VOLUME_PROFILE at more than twice
the rate. That is the honest baseline, and it is roughly the cost model.

### The one rule set that replicated

`VWAP 60m: above_vwap + break_of_structure + ema_stack + rsi_directional`
(three id clones differing only by the news filter; the filter changes nothing - see
[Measurements](#measurements)).

| seed | n | exp | PF | win | t | maxDD | avgDD | consec W/L | Sharpe | Sortino | avgW | avgL | R/R | MFE | MAE | hold | L/S |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| 1 | 202 | +0.0341R | 1.276 | 48.0% | +1.232 | 3.48R | 1.14R | 6/8 | +0.087 | +0.138 | +0.328 | -0.237 | 1.38 | 0.296 | 0.245 | 72m | 84/118 |
| 2 | 212 | -0.0014R | 0.991 | 49.5% | -0.048 | 8.48R | 3.55R | 9/8 | -0.003 | -0.005 | +0.324 | -0.321 | 1.01 | 0.297 | 0.278 | 72m | 85/127 |
| 3 | 232 | +0.0210R | 1.152 | 52.2% | +0.770 | 8.62R | 4.33R | 9/7 | +0.051 | +0.075 | +0.306 | -0.289 | 1.06 | 0.284 | 0.247 | 67m | 93/139 |
| 4 | 207 | +0.0049R | 1.035 | 41.1% | +0.181 | 5.38R | 1.83R | 5/11 | +0.013 | +0.019 | +0.355 | -0.239 | 1.49 | 0.285 | 0.263 | 73m | 141/66 |
| 5 | 245 | +0.0668R | 1.617 | 54.7% | +2.661 | 3.29R | 1.02R | 8/5 | +0.170 | +0.290 | +0.320 | -0.239 | 1.34 | 0.315 | 0.218 | 70m | 103/142 |
| **pooled** | **1,098** | **+0.0263R** | - | - | **+2.142** | - | - | - | - | - | - | - | - | - | - | 71m | 506/592 |

Note the win rate range: 41.1% to 54.7%, and the long/short split flipping from 141/66 on
seed 4 to 103/142 on seed 5. Five draws of the same rule set look like five different
strategies. Seed 5 alone would have been reported as "t = +2.66, PF 1.62"; that is exactly the
row a one-path sweep publishes and a second path deletes.

Slices for this rule set (seed 5, where it looked best - so read them as the optimistic case):

- session: RTH_OPEN 46tr **+0.192R**, LUNCH 59tr +0.086R, RTH_AFTERNOON 51tr +0.050R,
  RTH_MORNING 41tr +0.003R, RTH_CLOSE 48tr -0.005R
- regime: TREND_UP 54tr +0.106R, TREND_DOWN 67tr +0.096R, RANGE 124tr +0.034R
- volatility: HIGH 154tr +0.077R, NORMAL 90tr +0.048R

Every slice clears `MIN_TRADES_PER_SLICE = 5` comfortably and the three volatility/regime
slices clear 30 as well - but the **parent rule set fails deflation**, so a slice of it cannot
be published either. These are descriptive only. Note also that the two most flattering slices
(RTH_OPEN +0.192R, TREND_UP +0.106R) are the smallest, which is what slicing usually produces.

---

## Sweep results

### By timeframe, pooled over the five paths

| family | tf | strategy-paths | traded | cleared 30 | total trades | mean exp (floor) | best t |
|---|---|---|---|---|---|---|---|
| VOLUME_PROFILE | 5m | 325 | 76 | 25 | 1,965 | -0.0560R | +0.575 |
| VOLUME_PROFILE | 15m | 425 | 178 | **4** | 969 | **-0.2474R** | -1.154 |
| VOLUME_PROFILE | 60m | 250 | 118 | **0** | 529 | - | - |
| VWAP | 5m | 420 | 230 | 72 | 12,384 | -0.0475R | +1.189 |
| VWAP | 15m | 340 | 165 | 6 | 1,114 | +0.0261R | +0.788 |
| VWAP | 60m | 240 | 109 | 15 | 3,700 | +0.0248R | +2.661 |

Read this carefully against the brief's premise. **VOLUME_PROFILE at 15m clears the 30-trade
floor 4 times out of 425 strategy-paths, and those four have mean expectancy -0.2474R.**
VOLUME_PROFILE at 60m never clears the floor on any path, despite 118 of 250 strategy-paths
producing at least one trade. The family's trade production at 15m in my sampled universe tops
out at **30 trades**, not 230 or 315 - see
[Is the large sample real?](#is-the-large-sample-real) for why I believe those parent-sweep
rows are real rows that my sampler did not draw, and what mechanism produces them.

### Timeframes tested individually and in groups

Individually: 5m, 15m, 60m, each as a primary. In groups: the generator's `confirm_map`
attaches confirmation timeframes (5m -> 15m+60m, 15m -> 60m), and `_build_strategy` binds a
`structure` SIGNAL condition to the higher timeframe whenever a strategy has >=3 signal legs.
So "does higher-timeframe alignment help" is testable inside my universe as a contrast between
strategies that do and do not carry an HTF-bound leg:

| HTF-bound structure leg | floor-clearing strategy-paths | mean expectancy | median | positive |
|---|---|---|---|---|
| yes | 16 | **+0.0052R** | +0.0136R | 9 / 16 |
| no | 106 | **-0.0506R** | -0.0390R | 43 / 106 |

That looks like a win for higher-timeframe alignment, and it is almost entirely a confound:
binding only happens when a strategy has four signal legs, and four-leg strategies did better
anyway. Controlling for it:

| signal legs | HTF-bound | floor-clearing strategy-paths | mean expectancy | median | positive |
|---|---|---|---|---|---|
| 2 | no | 24 | -0.1009R | -0.0968R | 5 / 24 |
| 3 | no | 36 | -0.0575R | -0.0432R | 14 / 36 |
| 4 | no | 46 | -0.0189R | +0.0049R | 24 / 46 |
| 4 | **yes** | 16 | **+0.0052R** | +0.0136R | 9 / 16 |

Within the four-leg cell the bound arm is better by **+0.024R on 16 observations**, which is
not evidence of anything. **The honest answer to "does multi-timeframe alignment improve
outcomes" on this data is: not measurably.** The apparent signal is a leg-count effect.

The leg-count gradient itself is monotone and holds inside VWAP alone (2 legs -0.0716R on 20,
3 legs -0.0575R on 36, 4 legs **+0.0165R** on 37). I would not publish that either: this table
is conditioned on clearing 30 trades, and a four-leg strategy only clears 30 trades if it drew
a high-firing anchor, so leg count here is partly a proxy for which anchor was sampled. See
[Is the large sample real?](#is-the-large-sample-real).

1m and daily were not tested - runtime budget - so no claim is made about them.

### Walk-forward, anchored, 5 folds, top_k=5, `min_trades_is=20` (seed 1)

**VOLUME_PROFILE** - IS 151tr +0.0116R, OOS 24tr **-0.3558R**, efficiency **-30.621**,
selection stability **0.000**, `is_credible = False`.

| fold | test window | selected | IS exp | OOS exp | OOS trades |
|---|---|---|---|---|---|
| 0 | 2025-11-12 .. 2025-12-08 | **0** | - | - | 0 |
| 1 | 2025-12-08 .. 2026-01-02 | **0** | - | - | 0 |
| 2 | 2026-01-02 .. 2026-01-28 | 2 | +0.0275R | **-0.5672R** | 21 |
| 3 | 2026-01-28 .. 2026-02-22 | **0** | - | - | 0 |
| 4 | 2026-02-22 .. 2026-03-17 | 4 | +0.0009R | +0.8297R | 3 |

Three folds in five could not find a single VOLUME_PROFILE strategy worth selecting. The two
that could produced 24 trades between them. Fold 4's +0.8297R is three trades and means
nothing.

**VWAP** - IS 2,219tr +0.1320R, OOS 466tr **-0.0294R**, efficiency **-0.223**, selection
stability **0.950**, `is_credible = False`.

| fold | test window | selected | IS exp | OOS exp | OOS trades |
|---|---|---|---|---|---|
| 0 | 2025-11-12 .. 2025-12-08 | 4 | +0.2000R | +0.0995R | 88 |
| 1 | 2025-12-08 .. 2026-01-02 | 5 | +0.1657R | +0.0258R | 103 |
| 2 | 2026-01-02 .. 2026-01-28 | 5 | +0.1338R | +0.1066R | 97 |
| 3 | 2026-01-28 .. 2026-02-22 | 5 | +0.1348R | **-0.2060R** | 135 |
| 4 | 2026-02-22 .. 2026-03-17 | 5 | +0.0711R | **-0.3905R** | 43 |

The shape here is instructive and worth the desk's attention: stability is 0.950, meaning the
selector is *consistent* - it keeps choosing the same strategies. Consistency of selection is
not evidence of edge. It picked the same losers three folds running, and the in-sample
expectancy it selected on (+0.13R to +0.20R) is 5-7x anything that survived out of sample.

### Monte Carlo and risk of ruin, $50,000 account

Risk per trade is **$240**, which is what `AccountConfig` actually produces:
`max_total_drawdown` $5,000 x `(1 - protected_buffer_pct 0.20)` = $4,000 usable buffer x
`base_risk_pct_of_buffer` 0.06 = $240. The $375 equity ceiling
(`max_risk_pct_of_equity` 0.0075) and the $500 hard cap are looser. Failure threshold $5,000
trailing. 250-trade horizon, 3,000-5,000 resamples, iid.

Walk-forward OOS series (the only honest input), seed 1:

| family | OOS trades | P(ruin) | median final equity | p05 | maxDD p95 | longest losing streak p95 | P(profit) |
|---|---|---|---|---|---|---|---|
| VOLUME_PROFILE | 24 | **100.0%** | $44,980 | $44,776 | 114.8R | 18 | 0.0% |
| VWAP | 466 | **46.8%** | $47,888 | $44,974 | 38.8R | 11 | 31.8% |

A 100% probability of ruin on a 24-trade OOS series is a statement about the series, not a
forecast; resampling a set of trades whose mean is -0.36R will destroy the account every time.
It is still the number the risk layer would receive.

For contrast, the best-looking in-sample candidates per path (these are the rows a naive sweep
publishes):

| seed | rule set | n | exp | t | deflated | P(ruin) @ $240 | median eq | maxDD p95 | streak p95 |
|---|---|---|---|---|---|---|---|---|---|
| 1 | VOLUME_PROFILE 5m `above_vwap+delta_divergence+stoch_extreme+value_area_breakout` | 62 | +0.1087R | +0.575 | **+0.000R** | **30.0%** | $55,773 | 32.3R | 13 |
| 1 | same family, sibling exit | 74 | +0.0888R | +0.512 | +0.000R | **36.7%** | $54,403 | 34.6R | 13 |
| 2 | VWAP 5m `ema_stack+stoch_directional+vwap_reclaim` | 45 | +0.1423R | +0.895 | +0.000R | 2.4% | $58,643 | 18.1R | 9 |
| 3 | VWAP 60m `above_vwap+break_of_structure+ema_stack+rsi_directional` | 232 | +0.0211R | +0.773 | +0.000R | 0.1% | $51,290 | 10.5R | 10 |
| 4 | VWAP 15m `above_vwap+cvd_directional+price_above_ema200+stoch_extreme` | 30 | +0.0629R | +0.788 | +0.000R | 0.0% | $53,780 | 6.6R | 9 |
| 5 | VWAP 60m `above_vwap+break_of_structure+ema_stack+rsi_directional` | 245 | +0.0668R | +2.661 | +0.000R | 0.0% | $53,993 | 5.7R | 9 |

Note the seed-1 VOLUME_PROFILE rows: **+0.109R expectancy and a 30% probability of account
failure.** High expectancy at 62 trades with a 32R 95th-percentile drawdown is not a tradeable
edge; it is a small sample with fat tails. `robust_score` already discounts it to 0.0200, and
the ruin gate closes on it regardless.

### Deflation, arithmetic shown

`deflated_expectancy` charges `sqrt(2 ln trials)` t-units - the expected maximum of `trials`
standard normals - against the observed t, then converts the remainder back to R via
`deflated_t * std_r / sqrt(trades)`.

What I actually searched, counted honestly:

```
per-path sweep, my two families, tf 5/15/60            400 rule sets
POC-fix experiment, three non-baseline arms x 69        207
RTH-profile experiment, one new arm x 200              200
minimal-pair anchor experiment at 15m                  188
                                                     -----
distinct strategy configurations I backtested and
looked at inside my own families                       995
cross-family reference sweep (13 families, 30 each)    374  (not used to rank my families)
                                                     -----
total configurations touched                         1,369
```

And for scale, the space the generator could have drawn from at 5/15/60m, enumerated exactly:

```
VOLUME_PROFILE  5,228 signal sets x 6 exits x 3 tfs x 5 filter sets = 470,520
VWAP            5,228 signal sets x 6 exits x 3 tfs x 4 filter sets = 376,416
                                                            TOTAL    846,936
```

The charge at each of those framings:

| trials | `sqrt(2 ln n)` | best observed t in my families | deflated expectancy |
|---|---|---|---|
| 3 (the news-filter clones only - indefensible) | 1.482 | +2.142 pooled | +0.008R |
| 25 (rule sets with >=20 trades on all 5 paths) | 2.537 | +2.142 pooled | **+0.000R** |
| 400 (one path's ranked sweep) | 3.462 | +2.661 single path | **+0.000R** |
| 995 (everything I ranked in my families) | 3.716 | +2.142 pooled | **+0.000R** |
| 1,369 (everything I ran) | 3.800 | +2.142 pooled | **+0.000R** |
| 846,936 (the enumerable space) | 5.225 | +2.142 pooled | **+0.000R** |

Only the indefensible framing - pretending I tested three hypotheses when I ranked 400 -
produces a positive number, and it produces +0.008R, which is a fifth of the round-turn cost.
**Nothing in my families survives deflation at any trial count I can defend.**

### Parameter sensitivity

`parameter_sensitivity` on the one replicating rule set (VWAP 60m, seed 5, baseline
+0.0668R / 245 trades):

| perturbation | trades | expectancy | relative |
|---|---|---|---|
| stop x0.75 | 247 | +0.0814R | 1.219 |
| stop x1.25 | 243 | +0.0520R | 0.779 |
| targets x0.8 | 245 | +0.0620R | 0.929 |
| targets x1.25 | 245 | +0.0641R | 0.960 |
| **double slippage** | 245 | +0.0657R | **0.984** |

`worst_relative` 0.779, `mean_relative` 0.974 - comfortably past the 0.4 threshold. This is the
one check the candidate passes cleanly, and it is worth saying that it passes: the rule set is
not a knife-edge parameter coincidence, it is a real if tiny drift that happens not to be
distinguishable from zero. Double slippage costs 1.6% of the edge because the 60m risk unit is
large relative to a tick; a 5m version of the same test would be far less forgiving.

I could not run the free version of this check (same rule set across all six exit models)
because the generator samples one exit index per rule set, so no rule set appeared with three
or more exit variants clearing 30 trades in any path.

---

## The two proposed fixes, tested

Both were tested as experiments with paired arms on identical bars and identical rule sets.
I did not edit any source file for either; the variants were built by swapping a condition
function and by rebinding `TimeframeFrame.prior_session_profile` on a second frame.

### (a) POC distance ceiling and inverted strength

Proposal from the reversion seat: clamp `poc_reversion` to `0.5 <= |gap|/ATR <= 2.5`, and
invert `strength` so the condition is most confident when the POC is nearest.

Four arms x 69 rule sets in my universe that use `poc_reversion`, seed 1, one pass:

| arm | rule sets | traded | cleared 30 | total trades | pooled n | pooled exp | pooled t |
|---|---|---|---|---|---|---|---|
| base (as shipped) | 69 | 25 | **0** | 145 | 145 | +0.1485R | +2.341 |
| invert strength only | 69 | 25 | **0** | 145 | 145 | +0.1485R | +2.341 |
| ceiling only | 69 | 25 | **0** | 105 | 105 | +0.1362R | +1.637 |
| ceiling + invert | 69 | 25 | **0** | 105 | 105 | +0.1362R | +1.637 |

**Result 1 - the strength inversion is a measurement no-op, and I can prove it exactly.**
69 of 69 rule sets produce a **bit-identical R-series** between `base` and `invert`, and 69 of
69 between `ceiling` and `ceiling+invert`. `ConditionResult.strength` flows into
`StrategySignal.strength` and `Evidence.weight` and stops there: grepping `futures_agents/` for
consumers finds none in `backtest/engine.py`, `backtest/metrics.py`, `risk/`, `agents/decision.py`
or `orchestrator.py`. The only `strength` arithmetic in the decision path
(`agents/analysts.py:1911`) is a different quantity - the posture strength from `_price_posture`.

This is not an argument against inverting it. The current formula
`min(1.0, abs(gap)/atr_v/2.0)` really is monotonically wrong in distance and a reader of a
signal card would be misled by it. But the desk should know that **no backtest can ever
falsify or support the change as the code stands**, so it should be judged as a
reporting-correctness fix, not as a performance proposal, and it should not be credited with
any expectancy.

**Result 2 - the ceiling does what it claims geometrically and costs sample without improving
outcome.** One evaluation per completed 15m RTH bar, 3,213 evaluable bars:

| arm | fires | % of bars | median dist | mean dist |
|---|---|---|---|---|
| as shipped | 741 | 23.06% | 1.70 ATR | 2.16 ATR |
| with 2.5 ATR ceiling | 510 | **15.87%** | **1.22 ATR** | **1.31 ATR** |

Trade-level, paired on the 25 rule sets that traded: mean delta **-1.6 trades** (t = -4.62),
**15 rule sets lose trades, 0 gain any**. Pooled expectancy +0.1485R (t +2.34) -> +0.1362R
(t +1.64). **No arm has a single rule set at 30 trades**, so nothing here is publishable; the
pooled figures are directional reads over 69 heavily overlapping rule sets and 145 trades that
are not independent.

**Result 3 - a refinement of the prior seat's geometry that changes how much the ceiling
matters.** The reversion seat measured `poc_reversion` at a median **3.95 ATR** from the POC
with 61.6% of fires beyond 3 ATR. I measure a median of **1.70 ATR** with roughly 31% beyond
2.5 ATR. The difference is the denominator: they evaluated once per completed 5m bar across
the whole 24h trading day; I evaluated only on bars the strategy layer can trade.
`StrategyFilters.rth_only` defaults to `True`, so **every strategy in every template only ever
sees RTH bars**. The extreme distances live in the overnight session, where ATR is small and
price has wandered away from a 24h POC - and no strategy in this codebase can trade them.
Both numbers are correct; mine is the one that describes the population that produces trades.
The practical consequence is that the proposed ceiling bites less than the 24h figure implies,
which makes the case for it weaker, not stronger.

### (b) RTH-only prior-session profile

Rebound `prior_session_profile` on every `TimeframeFrame` of a second, otherwise identical
frame, filtering the prior day's bars with `is_rth(ts, spec.rth_open, spec.rth_close)`.

**Geometry - replicates on an independent path.** 119 comparable days, seed 1, 15m frame:

| measure | reversion seat (seed 5, 14 days) | me (seed 1, 119 days) |
|---|---|---|
| 24h value area width / RTH width | 1.42x | **1.524x** (170.54 vs 111.87 pts) |
| median \|POC_24h - POC_RTH\| | 9.80 pts | **8.98 pts** |
| RTH closes getting a different in-value answer | 11.0% | **16.1%** (498 of 3,094) |

Independent path, longer window, same conclusion, slightly larger effect. The internal
inconsistency the reversion seat identified is real and it is not a small one: the two
definitions of "yesterday's value" disagree about where price is on roughly one bar in six.

**Outcome - paired, 200 identical VOLUME_PROFILE strategies, same bars:**

| arm | traded | cleared 30 | total trades | mean exp (floor) | best t | pooled n | pooled exp | pooled t |
|---|---|---|---|---|---|---|---|---|
| 24h (shipped) | 65 | 5 | 644 | +0.0837R | +0.575 | 644 | +0.0502R | +1.000 |
| RTH-only | 54 | 5 | 616 | +0.0998R | +0.638 | 616 | +0.0516R | +0.974 |

Paired delta on the five pairs where either arm cleared 30 trades: **+0.0161R, 5 better /
0 worse, t = +3.87** - and **n = 5 pairs**, which is a sixth of the floor. I am reporting that
number only to show what it would look like if I published it, which I am not. Paired delta on
all 73 pairs that traded at all: -0.38 trades, t = -0.90, 38 better / 28 worse / 7 identical.
Pooled expectancy moves by **+0.0014R**, which is 3% of one tick of cost.

**Conclusion: this confirms the reversion seat exactly, on an independent price path and with
an independent metric.** They measured barrier races and POC-touch rates; I measured actual
backtested R after costs. Both say: every geometric measure improves, the outcome does not.
The change should be made because a codebase should not answer "is price in yesterday's value"
two different ways on the same bar, not because it will make money.

---

## Is the large sample real?

**No. The large samples are a direct function of how often the anchor condition fires, and the
anchors that fire most are the ones with the worst expectancy.** Three independent measurements
agree.

### 1. Firing rates, one evaluation per completed timeframe bar, RTH only (seed 1)

| condition | group | 5m (n=9,480) | 15m (n=3,240) | 60m (n=960) |
|---|---|---|---|---|
| `above_vwap` | vwap SIGNAL | **100.0%** | **100.0%** | **100.0%** |
| `away_from_hvn` | profile FILTER | 86.6% | 83.1% | 80.2% |
| `value_area_breakout` | profile SIGNAL | **68.2%** | **66.8%** | **63.3%** |
| `open_outside_value` | profile FILTER | 67.5% | 67.5% | 65.8% |
| `prior_day_breakout` | liquidity SIGNAL | 56.1% | 55.7% | 55.4% |
| `poc_reversion` | profile SIGNAL | 25.3% | 22.9% | 18.5% |
| `vwap_band_extension` | vwap SIGNAL | 12.7% | 13.2% | 14.6% |
| `vwap_band1_bounce` | vwap SIGNAL | 9.6% | 16.3% | 27.7% |
| `vwap_proximity` | vwap FILTER | 8.9% | 13.9% | 24.5% |
| `vwap_reclaim` | vwap SIGNAL | 4.5% | 8.2% | 20.8% |
| `value_area_edge` | profile SIGNAL | 2.4% | 4.0% | 6.4% |
| `lvn_rejection` | profile SIGNAL | 1.9% | 1.8% | 1.8% |

Reference conditions from families I do not own, for scale: `rsi_directional` 81.9/83.3/84.3%,
`ema_stack` 78.9/83.3/77.1%, `delta_confirms_bar` 79.7/79.1/76.2%, `break_of_structure`
39.2/40.9/52.8%, `bollinger_mean_pull` 35.0/34.5/55.0%.

`value_area_breakout` at 63-68% confirms the prior seats' 57-70% on an independent path. But
the headline in my own families is worse: **`above_vwap` fires on 100.0% of evaluable bars at
every timeframe.** It returns `no()` only when the close is exactly equal to VWAP to the tick,
which happened zero times in 13,680 evaluations. It is registered as a SIGNAL, so it never
gates a setup on non-occurrence - it only gates on *direction*. Functionally it is a
directional filter ("only go long above VWAP") wearing a trigger's clothes, and it is the
required-group condition for a quarter of the VWAP template's rule sets.

Two of VOLUME_PROFILE's optional filters are nearly as weak: `away_from_hvn` passes 80-87% of
the time and `open_outside_value` passes 66-68% of the time. A filter that admits five bars in
six is a rounding error on the trade count.

### 2. Minimal-pair test: swap only the anchor, hold everything else fixed (15m, seed 1)

For each of the 8 profile/VWAP SIGNAL conditions, paired with each of the same 24 partner
conditions drawn from the VOLUME_PROFILE template's optional groups, same exit
(`ATRx1.5->1/2/3R`), same base filters, same bars. 188 strategies. Whatever differs is the
anchor.

| anchor | pairs | traded | >=30 | total trades | median n | **median trades / calendar day** | max n | mean exp (>=30) |
|---|---|---|---|---|---|---|---|---|
| `above_vwap` | 20 | 19 | 18 | 10,137 | 643.0 | **5.358** | 900 | +0.0043R |
| `value_area_breakout` | 24 | 24 | 21 | 7,276 | 351.5 | **2.929** | 607 | **-0.0242R** |
| `poc_reversion` | 24 | 24 | 20 | 2,119 | 94.0 | 0.783 | 155 | -0.0613R |
| `vwap_reclaim` | 23 | 20 | 16 | 1,596 | 53.0 | 0.442 | 163 | -0.0313R |
| `vwap_band1_bounce` | 22 | 20 | 11 | 1,204 | 28.0 | 0.233 | 210 | +0.0140R |
| `value_area_edge` | 24 | 24 | 7 | 502 | 21.5 | 0.179 | 41 | **+0.0686R** |
| `lvn_rejection` | 24 | 22 | 0 | 283 | 11.0 | 0.092 | 23 | - |
| `vwap_band_extension` | 21 | 15 | 5 | 465 | 1.0 | 0.008 | 142 | -0.1462R |

Spearman correlation between per-bar firing rate (table 1, 15m) and median trade production:
**rho = +0.81**. Trade count is very largely a restatement of firing rate.

Three things follow.

1. **A 230- or 315-trade VOLUME_PROFILE row at 15m is entirely achievable and it is
   `value_area_breakout` that achieves it.** The median `value_area_breakout` minimal pair
   produces 351 trades over 120 days; the maximum produces 607. Nothing else in the profile
   group gets near it - `value_area_edge`'s *maximum* is 41 and `lvn_rejection`'s is 23.
2. **The big-sample anchors are the losing ones.** `value_area_breakout` mean expectancy over
   its 21 floor-clearing pairs is **-0.0242R**; `poc_reversion` is -0.0613R. The only anchor
   with a decently positive mean is `value_area_edge` at +0.0686R - on 7 floor-clearers out of
   24 pairs, which is exactly the low-n regime the brief warns about.
3. **A sample-size floor set at 30 does not protect against this.** 18 of 20 `above_vwap`
   pairs and 21 of 24 `value_area_breakout` pairs clear 30 trades. The floor screens out rare
   conditions and waves through conditions that describe the modal state. A condition firing on
   two thirds of bars will clear any trade-count floor on any data, edge or no edge.

### 3. Cross-family trade production, same bars, same budget (seed 1, 30 strategies per family)

| family | traded | >=30 | total trades | median n | max n | **max trades / calendar day** | mean hold |
|---|---|---|---|---|---|---|---|
| MOMENTUM | 8 | 3 | 848 | 0.0 | 329 | 2.742 | 72m |
| LIQUIDITY | 16 | 4 | 524 | 2.0 | 117 | 0.975 | 39m |
| VWAP | 11 | 3 | 375 | 0.0 | 99 | 0.825 | 51m |
| OPENING_RANGE | 14 | 3 | 433 | 0.5 | 98 | 0.817 | 64m |
| MULTI_TIMEFRAME | 19 | 4 | 295 | 1.0 | 61 | 0.508 | 61m |
| **VOLUME_PROFILE** | 18 | 4 | 367 | 1.0 | 57 | **0.475** | 37m |
| SUPPLY_DEMAND | 15 | 4 | 182 | 1.0 | 38 | 0.317 | 43m |
| TREND | 6 | 5 | 194 | 0.0 | 34 | 0.283 | 57m |
| MEAN_REVERSION | 4 | 0 | 7 | 0.0 | 2 | 0.017 | 42m |
| REVERSAL | 4 | 0 | 8 | 0.0 | 2 | 0.017 | 15m |
| FIBONACCI | 3 | 0 | 3 | 0.0 | 1 | 0.008 | 29m |
| BREAKOUT | 0 | 0 | 0 | - | 0 | 0.000 | - |
| PULLBACK | 0 | 0 | 0 | - | 0 | 0.000 | - |

This is the one place my answer is more nuanced than "yes it trades constantly". **At an equal
30-strategy budget VOLUME_PROFILE is mid-pack, sixth of thirteen**; MOMENTUM produces 5.8x its
maximum trade count. VOLUME_PROFILE is a high-firing family whose *sampled* strategies mostly
did not draw the high-firing anchor. The mechanism is real and measured (section 2); whether it
shows up in any given sweep depends on which rule sets the sampler drew.

In my own 400-strategy universe the largest VOLUME_PROFILE sample across all five paths is
**83 trades** (0.69/calendar day), and the largest samples overall are all VWAP
`above_vwap` rule sets at 313-346 trades (2.6-2.9/calendar day, 37% of RTH session time held
in a position). My sampler simply did not draw the 230/315-trade 15m rows the parent sweep
found. Distribution across both families, traded strategies, all five paths:

| family | traded strategy-paths | p50 trades/calendar day | p90 | max |
|---|---|---|---|---|
| VOLUME_PROFILE | 372 | 0.033 | 0.133 | 0.692 |
| VWAP | 504 | 0.050 | 0.950 | 2.883 |

The medians are low because most strategies that trade at all trade a handful of times; the
tail is what matters, and VWAP's tail reaches 2.883/day where VOLUME_PROFILE's stops at 0.692.

**A caution on the `trades_per_day` field.** `Metrics.trades_per_day` is
`trades / days-on-which-it-traded`, not `trades / calendar day`. The seed-5 VWAP 60m rule set
reports `trades_per_day = 3.025` on 245 trades over 120 days, because it traded on 81 of them.
Both are legitimate numbers answering different questions; anyone asking "does this trade
constantly" wants the calendar version, which is 2.04. I have used the calendar version
everywhere above and labelled it.

---

## What I withheld and why

**I published nothing as live-eligible.** Zero strategies in VOLUME_PROFILE or VWAP on MNQ
reach `RobustnessReport.live_eligible` or `WalkForwardResult.is_credible`.

Floors applied: `MIN_TRADES_FOR_RANK = 30`, `MIN_TRADES_PER_SLICE = 5`,
`walk_forward(min_trades_is=20)`, `live_eligible` (score >= 0.45, positive expectancy, positive
**deflated** expectancy, WF efficiency >= 0.35, P(ruin) <= 0.02).

Withheld specifically:

- **The best thing I found: VWAP 60m `above_vwap+break_of_structure+ema_stack+rsi_directional`.**
  Positive on 4 of 5 paths, pooled n=1,098 at +0.0263R, t=+2.142, P(ruin) 0.02% at $240/trade,
  parameter sensitivity `worst_relative` 0.779, double slippage costs 1.6% of the edge. It
  clears every gate except deflation, and it fails deflation at every trial count I can defend
  (needs t > 2.537 at the most generous defensible framing of 25 trials; it has 2.142). Also
  worth saying: its single best path (seed 5, t=+2.661) is the *only* row in my entire run with
  t > 2.0 on a single path, and four other paths of the same rule set say +0.034, -0.001,
  +0.021, +0.005. Publishing seed 5 alone would have been the mistake this seat exists to
  prevent.
- **Every POC-fix arm.** All four arms produced zero rule sets at 30 trades. The pooled
  +0.1485R / t +2.34 for the baseline arm is 145 trades drawn from 69 rule sets that overlap
  heavily; treating it as 145 independent observations would be wrong and I have not ranked on
  it.
- **The RTH-profile paired delta of +0.0161R at t=+3.87.** Five pairs. Below any floor. Quoted
  above only to show what the temptation looks like.
- **Every slice of every strategy whose parent failed deflation** - which is all of them. The
  session/regime tables for the VWAP 60m rule set are printed as description, with no claim.
- **VOLUME_PROFILE at 60m entirely.** 250 strategy-paths, 118 of them traded, 529 trades in
  total, and **zero** cleared the 30-trade floor on any path. No claim of any kind about volume
  profile on MNQ 60m.
- **VOLUME_PROFILE at 15m.** 4 of 425 strategy-paths cleared the floor, mean expectancy
  -0.2474R. The 15m timeframe the brief flagged as VOLUME_PROFILE's strength is, in my sampled
  universe, the timeframe where it can barely assemble a sample.
- **1m and daily timeframes.** Not tested - runtime budget. No claim.
- **MES, MGC, CL and every other symbol.** Not tested. Every number here is MNQ's.
- **`value_area_edge` at +0.0686R mean over its floor-clearing minimal pairs.** It is the only
  profile anchor with a decently positive mean and the only one the reversion seat endorsed,
  and it clears the floor on 7 of 24 pairs. That is precisely the shape of result - good-looking
  number, thin support, from the condition a prior seat already praised - that I would be
  confirming rather than testing if I published it. Flagging it as the most promising thing in
  the profile group for someone to test properly on real data, not publishing it.

### Three structural findings I did not act on

I found no bug that produces wrong numbers, so I edited no source file. I did find three
places where declared constraints do not do what they say. All three are in
`strategies/combinator.py`, which four sibling seats are sweeping against right now; changing
it mid-run would invalidate their results, so I am reporting and not touching.

1. **The VWAP template's `exclusive=(("above_vwap", "vwap_proximity"),)` is not enforced.**
   Measured: **18 of the 400 strategies** in my universe hold both, e.g.
   `vwap_above_vwap_delta_confirms_bar_price_above_ema50_structure_trend__f_vwap_proximity`.
   Cause: `generate_combinations` calls `_violates_exclusive(names, template.exclusive)` on the
   **signal** names, then appends filter sets afterwards with no exclusivity test. Any declared
   exclusion involving a FILTER is silently inert. `vwap_proximity` is exactly such a filter, so
   the template says "not both" and the generator produces both. (The pairing is also
   measurably consequential: adding `vwap_proximity` removes 109-134 trades per pair and moves
   expectancy by -0.056R to +0.028R with no consistent sign across the five paths.)
2. **VOLUME_PROFILE's own `exclusive` pairs are dead code.** `profile` is the template's sole
   *required* group and is not among its optional groups, so exactly one profile SIGNAL appears
   per strategy - measured, 200 of 200. The enumerated combination count is 5,228 before
   exclusions and 5,228 after: **zero combinations removed.** `("poc_reversion",
   "value_area_breakout")` and `("value_area_edge", "value_area_breakout")` cannot arise and
   never did.
3. **`GLOBAL_EXCLUSIVE ("value_area_breakout", "prior_day_breakout")` does not bind in either
   of my families.** `prior_day_breakout` lives in the `liquidity` group, which is not in
   VOLUME_PROFILE's or VWAP's optional groups; **0 of 400** of my strategies can hold it. The
   three-seat finding and its fix are correct and I am not disputing them - but the fix protects
   OPENING_RANGE and BREAKOUT, which are the only templates that can reach both conditions. It
   does **not** protect the family where `value_area_breakout` does most of its work, because in
   VOLUME_PROFILE that condition is never accompanied by its duplicate; it is simply the
   required leg. The duplication problem in VOLUME_PROFILE is not "two names for one statement",
   it is "one statement that is true two thirds of the time".

---

## Measurements

### Sweep parameters

```
commit                5b3ae0c  (HEAD has since moved to 0df691f; rule sets identical,
                               strategy_id hash changed - match rows by group+tf+name)
symbol                MNQ only
paths                 5 x synthetic_series("MNQ", days=120, seed=1..5,
                                           end_date=date(2026,3,17))
bars                  165,600 one-minute bars per path, overnight included
frames                5m, 15m, 60m, 240m  (regime tf auto-selected = 15m)
primary timeframes    5, 15, 60            [1m and daily NOT tested - runtime budget]
timeframe groups      5m -> confirm (15m, 60m) | 15m -> confirm (60m) | 60m -> none
families              VOLUME_PROFILE, VWAP  (exclusive to this seat)
universe              400 strategies/path: VOLUME_PROFILE 65/85/50 at 5/15/60m,
                                           VWAP 84/68/48 at 5/15/60m
costs                 default CostModel
walk-forward          anchored, folds=5, top_k=5, min_trades_is=20, min_train_fraction=0.3
                      (5 folds > the 4-fold minimum in the brief)
monte carlo           iid resampling, 250-trade horizon, 3,000-5,000 runs,
                      $50,000 start, $240/trade, $5,000 trailing failure threshold
runtime               ~55 min CPU total (the container runs at roughly one core)
```

### Optional filters, paired against their own controls

The generator emits each rule set with and without each optional filter, so every filtered arm
has a like-for-like control. Paired on rule sets whose control cleared 30 trades:

| filter | seed 1 | seed 2 | seed 3 | seed 4 | seed 5 |
|---|---|---|---|---|---|
| `no_imminent_release` dExp / dTrades | +0.0013R / -0.5 | -0.0018R / -0.4 | -0.0011R / -0.6 | +0.0002R / -1.0 | -0.0004R / 0.0 |
| `outside_news_blackout` dExp / dTrades | +0.0036R / -0.3 | -0.0002R / 0.0 | +0.0000R / 0.0 | -0.0001R / -0.1 | -0.0003R / 0.0 |
| `vwap_proximity` dExp / dTrades | -0.0561R / **-129.2** | -0.0317R / **-126.2** | +0.0284R / **-116.2** | -0.0329R / **-109.0** | -0.0026R / **-134.2** |

(5-7 pairs per cell.) The two news filters are inert in my families: they remove a fraction of
a trade per strategy and move expectancy by a thousandth of an R in an inconsistent direction.
That is consistent with the prior desk finding that the news dimension is measuring about four
days in a hundred.

`vwap_proximity` is the opposite: it removes roughly **110-135 trades per rule set** - the
single largest filter effect I measured - and the sign of its expectancy delta is negative on
four paths and positive on one. It is a large intervention with no established direction, and
it is also the filter the VWAP template declares mutually exclusive with `above_vwap` without
the generator enforcing it (see [What I withheld](#what-i-withheld-and-why), finding 1).

### Anti-overfitting checks - what I checked, and what I found

| check | how I checked it | result |
|---|---|---|
| **look-ahead bias** | truncated a 30-day series at 60% and re-evaluated all 9 profile/VWAP conditions at 3 timeframes on 666 overlapping bars in both frames | **0 mismatches**. Clean. |
| **repainting** | same test, plus `prior_session_profile` (poc/vah/val) compared at 313 indices between the truncated and full frames | **0 differ**. Clean. |
| **future-data leakage** | the profile is built from `_day_order[position-1]`, strictly the prior completed day; verified by the truncation test above | Clean for my families. |
| **data-mining bias** | `deflated_expectancy` at six trial counts, arithmetic shown above | **Fails.** Nothing survives at any defensible trial count. |
| **overfitting** | anchored walk-forward, 5 folds, both families | **Fails.** Efficiency -30.6 (VOLUME_PROFILE) and -0.223 (VWAP). |
| **insufficient sample** | 30-trade floor applied to every published figure; calendar trades/day reported separately from `Metrics.trades_per_day` | **Found the floor is not protective here** - a 67%-firing condition clears 30 trades trivially. |
| **parameter sensitivity** | `parameter_sensitivity` on the one replicating rule set | **Passes**: worst 0.779, mean 0.974. |
| **unrealistic fills** | structural: entry at next bar's open, stop wins ties, gaps fill at the open. Not re-derived. | Enforced by `BacktestEngine`. |
| **understated costs / slippage** | default CostModel throughout, plus a double-slippage variant on the lead candidate | Double slippage costs 1.6% of the 60m edge. A 5m equivalent would cost far more; not measured. |
| **survivorship bias** | every generated strategy retained and counted, including the 235 per path that produced zero trades | No strategy dropped from any denominator. |
| **regime dependence** | session / regime / volatility / time-bucket / day-of-week slices on the eight candidates that mattered | Reported above; all attached to parents that failed deflation. |
| **replication across paths** | the whole sweep re-run on 5 independent seeds | **Fails.** 34.1% off-path positive rate, cross-path Spearman -0.031. |

### Files

Scripts and raw results (scratchpad, not committed):
`/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad/pvw/`
- `run_seed.py`, `out/seed{1..5}.json` - per-path sweep + seed-1 walk-forward
- `e1_poc.py`, `out/e1_poc_seed1.json`, `out/e1_geo_seed1.json` - POC ceiling / strength arms
- `e2_rth.py`, `out/e2_rth_seed1.json` - RTH-only prior profile, paired
- `e3_rates.py`, `out/e3_rates_seed1.json`, `out/e3_family_seed1.json` - firing rates, families
- `e4_anchor.py`, `out/e4_anchor_seed1_15m.json` - minimal-pair anchor swap
- `e5_slices.py`, `out/e5_slices_seed5.json` - slices + `parameter_sensitivity`
- `e6_lookahead.py` - truncation test for look-ahead and repainting
- `hcount.py`, `hcount2.py`, `excl_check.py` - hypothesis-space and exclusivity counts
- `analyze.py`, `analyze2.py`, `analyze3.py` - aggregation

### Test suite

`python -m pytest tests/ -q` -> **687 passed in 20.40s**, run at HEAD after all my work. The
count is above the 675 in the brief because sibling seats added tests while I ran; nothing
failed. I edited no source file.
