# Mean reversion, reversal, breakout and Fibonacci — backtesting seat

Seat: `MEAN_REVERSION`, `REVERSAL`, `BREAKOUT`, `FIBONACCI`. Branch
`claude/intelligent-feynman-ongyjw`, HEAD `5b3ae0c`.

**Data is synthetic throughout** — `synthetic_series("MNQ", days=120,
end_date=date(2026,3,17))` at seeds 1-5, five independent price paths, 165,600
one-minute bars each. Nothing in this document is evidence about a market and
nothing in it is a live edge. What synthetic data *can* settle is the question
this seat was actually sent to answer: whether the machinery generates,
enforces and measures what it claims to, and whether a family has enough sample
to be judged at all. Those are properties of the code, and they transfer.

**I edited no source file.** I built both proposed fixes as runtime-registered
conditions in the scratchpad and measured them; one of the two I am
recommending *against* landing, on the evidence below. The exact diff I do
recommend is in "The vanishing volume requirement". Test suite at the end of my
run: **694 passed** (675 when I started; a sibling seat landed
`tests/test_anchored_targets.py` and `futures_agents/backtest/objectives.py`
mid-run — the source tree moved under me and I note it rather than claim my 694
is comparable to the 675 I was given).

---

## Verdict

**Nothing in my four families is publishable. Not one rule set clears the
30-trade floor with a deflated expectancy above zero, on any timeframe, on any
of five price paths.** That is the headline, and it is not close.

Underneath it, four separate findings, in descending order of how much they
matter:

1. **`BREAKOUT`'s problem is not the vanishing volume requirement. It is
   `volatility_compressed`, which passes on 3.1% of 15-minute RTH bars.** The
   shipped template trades 3.10 times per strategy per 120 days and produces
   **zero trades at all on the 5-minute timeframe across 18 rule sets and five
   price paths**. Replacing that one filter with `volatility_normal` takes the
   same 78 rule sets from 1,209 trades to 11,011 — a 9.1x sample. The missing
   volume requirement is real (I confirmed it) and it is the second-order
   problem; you cannot enforce a confluence in a family that has no sample to
   enforce it on.
2. **Both ways of fixing the volume requirement cost 65-78% of an already
   fatal sample, and the SIGNAL version duplicates evidence the library already
   has.** A directional volume condition must borrow its direction from the
   bar's own sign, and `delta_confirms_bar` already supplies exactly that:
   measured, my candidate co-fires with it on 87.3% of its own firings and
   **agrees on direction 100.0% of the time**. That is the failure the diversity
   rule exists to prevent. I recommend against the new SIGNAL and in favour of
   volume as a direction-agnostic gate — plus making the silent drop loud.
3. **`FIBONACCI`'s leg anchor is genuinely broken and fixing it buys nothing.**
   The shipped `swing_leg()` flips direction once every 6.6 bars and agrees with
   `structure_trend` **52.9%** of the time — a coin flip. Anchoring the leg to
   the swing that preceded a break of structure cuts flips 3.1x and lifts
   agreement to **96.8%**, stable across five paths and three timeframes. In a
   clean paired swap it changed expectancy from -0.093R to -0.095R. The defect
   is real; it was not the reason the family loses.
4. **`MEAN_REVERSION` and `REVERSAL` are not thin, they are hollow.** Of 19
   distinct `MEAN_REVERSION` rule sets, **13 produced zero signals in 600
   trading days**. Of 21 `REVERSAL` rule sets, 11 produced zero, and a single
   rule set accounts for 279 of the family's 322 trades (87%). "REVERSAL's
   statistics" are one rule set's statistics wearing a family's name.

The one thing that looked like a survivor — a `REVERSAL` walk-forward with OOS
expectancy +0.193R at t = +2.98 — is an artifact, and dismantling it is in
"What I withheld and why".

---

## Seed replication

Five independent price paths, `seed` ∈ {1,2,3,4,5}, same `end_date`. I took the
brief's warning seriously: MNQ/MES/MGC at one seed is one observation, so every
number below that says "five paths" means five *seeds*, one symbol. This is an
MNQ document. I make no claim about MES, MGC or CL; I did not test them.

Per-path expectancy of the experiment arms (pooled over all rule sets in the
arm, 120 days each):

| arm | seed 1 | seed 2 | seed 3 | seed 4 | seed 5 | paths positive |
|---|---|---|---|---|---|---|
| `BREAKOUT` shipped | n=219 +0.110 | n=193 +0.038 | n=296 -0.010 | n=274 -0.069 | n=227 +0.016 | 3/5 |
| `BREAKOUT` + volume SIGNAL | n=68 +0.037 | n=28 +0.103 | n=56 +0.059 | n=72 -0.113 | n=44 +0.015 | 4/5 |
| `BREAKOUT` + `volume_surge` | n=96 +0.092 | n=64 -0.073 | n=92 +0.139 | n=112 -0.097 | n=56 +0.017 | 3/5 |
| `BREAKOUT` compression→normal | n=1923 +0.038 | n=2294 -0.011 | n=2378 +0.007 | n=1934 +0.056 | n=2482 +0.081 | 4/5 |
| `FIBONACCI` shipped leg | n=60 -0.114 | n=36 +0.015 | n=16 +0.007 | n=24 -0.098 | n=17 -0.331 | 2/5 |
| `FIBONACCI` BOS-anchored leg | n=40 -0.033 | n=24 -0.288 | n=19 +0.518 | n=29 -0.022 | n=17 -0.778 | 1/5 |

Read the FIBONACCI rows: seed 3 says +0.518R and seed 5 says -0.778R for the
*same rule sets*. At n≈20 per path that is what noise looks like, and it is
exactly why one seed is not a result. Nothing in my families replicated 5/5 in
sign.

The only structural measurement that *did* replicate perfectly across all five
paths is the leg-anchor diagnostic (next-to-last table in "Measurements"):
shipped-leg agreement with `structure_trend` was 47.4-64.7% on every path and
timeframe; BOS-leg agreement was 94.7-98.8% on every path and timeframe. No
overlap between the two ranges anywhere. Structural facts replicate; edges did
not.

---

## Sweep results

Generation: `generate_strategies("MNQ", (5,15,60), groups=[g], max_total=N)`,
default generator seed. Costs: the repo `CostModel` — commission + exchange fee
both sides, 0.5-tick base slippage, +1.0 tick on stops, volatility- and
thin-book widening. Entries fill at the **next bar's open**; stop wins over
target inside one bar; gaps fill at the open.

### Yield: how much of a 400-per-family sweep is even rankable

One path (seed 1), 400 generated per family, stock library, exactly the
comparison the brief asked for against TREND's 32 and MOMENTUM's 40:

| family | generated | median trades/strategy | max trades | strategies ≥30 trades | strategies with 0 trades |
|---|---|---|---|---|---|
| `MEAN_REVERSION` | 400 | 0 | 85 | **19** | 322 |
| `REVERSAL` | 400 | 0 | 15 | **0** | 267 |
| `BREAKOUT` | 396 | 0 | 42 | **3** | 365 |
| `FIBONACCI` | 400 | 0 | 48 | **7** | 298 |

`REVERSAL` is worse than the two families the brief flagged: **zero** of 400
reach the floor on a single path. The median strategy in all four families
takes no trades at all.

### Five-path sweep, stock-library universe only

100 generated per family per path, restricted to strategies built entirely from
the shipped library (see the audit note in "What I withheld and why"), pooled
over five paths:

| family | strategies | tf | trades | exp R | PF | win% | t |
|---|---|---|---|---|---|---|---|
| `MEAN_REVERSION` | 40 | 5 | 87 | -0.453 | 0.36 | 23.0 | -4.35 |
| | 24 | 15 | 81 | -0.035 | 0.90 | 49.4 | -0.41 |
| | 12 | 60 | 8 | +0.254 | 999 | 100.0 | +3.51 |
| `REVERSAL` | 32 | 5 | 1148 | -0.030 | 0.93 | 46.8 | -1.07 |
| | 24 | 15 | 54 | -0.039 | 0.88 | 37.0 | -0.39 |
| | 28 | 60 | 12 | -0.283 | 0.00 | 0.0 | -2.58 |
| `FIBONACCI` | 30 | 5 | 60 | +0.112 | 1.30 | 56.7 | +0.92 |
| | 15 | 15 | 29 | -0.344 | 0.29 | 44.8 | -2.80 |
| | 20 | 60 | 220 | -0.070 | 0.64 | 31.4 | -2.16 |

The `MEAN_REVERSION` 60m row — **PF 999, 100% win rate, t = +3.51** — is eight
trades. It is in the table precisely so it can be pointed at: that is what
`MIN_TRADES_FOR_RANK = 30` is for, and it is the single most seductive number in
this document.

Full per-family profile, same universe, five paths pooled:

| | `MEAN_REVERSION` | `REVERSAL` | `FIBONACCI` |
|---|---|---|---|
| trades | 176 | 1,214 | 309 |
| expectancy (net) | -0.2282R | -0.0328R | -0.0604R |
| expectancy (gross) | -0.2053R | -0.0033R | -0.0458R |
| profit factor | 0.552 | 0.925 | 0.766 |
| win rate | 38.6% | 45.9% | 37.5% |
| avg win / avg loss | +0.727 / -0.830 | +0.886 / -0.812 | +0.527 / -0.413 |
| t-statistic | -3.42 | -1.22 | -1.70 |
| max drawdown | 44.1R | 43.0R | 29.6R |
| avg drawdown | 30.34R | 19.56R | 15.66R |
| Sharpe / Sortino (per trade) | -0.258 / -0.321 | -0.035 / -0.050 | -0.097 / -0.135 |
| max consec wins / losses | 9 / 9 | 7 / **20** | 8 / 13 |
| avg MFE / MAE | 0.75R / 0.76R | 0.80R / 0.77R | 0.47R / 0.46R |
| edge ratio (MFE/MAE) | 0.99 | 1.04 | 1.01 |
| avg duration | 33 min | 19 min | 32 min |
| long / short | 80 / 96 | 627 / 587 | 130 / 179 |

The edge ratios are the tell. All three sit at 1.0: the average trade goes as
far against you as it goes for you before it resolves. There is no asymmetry
for an exit model to harvest.

`BREAKOUT` has no row here because **every** strategy in its 100-strategy sweep
drew the contaminating condition (its required `volume` pool was non-empty in
that run). Its clean numbers are in the next section, from a dedicated re-run.

### Costs

Gross-to-net gap, five paths: `MEAN_REVERSION` 0.023R, `REVERSAL` 0.030R,
`FIBONACCI` 0.015R, `BREAKOUT` 0.009-0.017R depending on arm. On a family whose
gross expectancy is -0.003R (`REVERSAL`), costs are ten times the signal. I did
not need to stress-test costs to kill anything; nothing was alive at zero cost
either.

### Slices

By session, `REVERSAL` (n=1,298): RTH_MORNING +0.132 (n=248), RTH_OPEN -0.194
(n=379), RTH_AFTERNOON -0.025 (n=353), RTH_CLOSE -0.011 (n=318). By regime,
`BREAKOUT`: TREND_UP +0.755 (n=24), TREND_DOWN +0.308 (n=56), RANGE -0.045
(n=168), VOLATILE_EXPANSION -1.019 (n=20). By volatility, `BREAKOUT`: NORMAL
+0.398 (n=108), EXTREME -1.019 (n=20).

I am reporting these because the brief asks for them and **not** offering any of
them as a finding. Nine session buckets times four families times three
timeframes is well over a hundred slices; at n=20-380 each, a +0.755 and a
-1.019 are what you get for free. The one slice pattern I would actually defend
is `BREAKOUT` in EXTREME volatility (-1.019R), because it has a mechanism —
the stop is `1.5 x ATR` and slippage widens with the ATR percentile, so the
worst fills land exactly where the stop is widest.

### Walk-forward

Anchored, 4 folds, `min_train_fraction=0.3`, top-5 per family per fold selected
by `robust_score` on training only. Folds are cut from the trade stream: a
training trade must have **entered and exited** before the boundary, so
selection sees no outcome resolved after it; test trades are those entered
inside the test segment. (This is an analytic equivalent of re-running the
engine per fold. It does not reproduce the engine's position state at a window
edge — worth at most one trade per strategy per fold. I took it because the
exact version cost 2.25 extra full passes per path and the machine was shared
with four other seats.)

Deduplicated to one variant per rule set — because `strategy_A` and
`strategy_A__f_outside_news_blackout` are one hypothesis, not two:

| family | rule sets | candidates/fold | selections | IS n / exp | OOS n / exp | OOS t | efficiency |
|---|---|---|---|---|---|---|---|
| `MEAN_REVERSION` | 19 | 0.0 | 0 | — | — | — | — |
| `REVERSAL` | 21 | 1.0 | 7 | 255 / +0.2044 | 59 / +0.1980 | +1.61 | +0.97 |
| `BREAKOUT` | 0 (all contaminated) | — | — | — | — | — | — |
| `FIBONACCI` | 13 | 0.1 | 1 | — | 0 | — | — |

**Three of my four families cannot be walk-forward tested at all.** Not "failed
out of sample" — there was never a fold in which any rule set had ten trades
that both opened and closed inside the training window. That is a finding about
the families, and it is the one I would put in front of a desk first.

---

## The vanishing volume requirement

### Confirmed, and it is wider than reported

`_signal_pools()` keeps only `ConditionKind.SIGNAL`, and then
`return [p for p in required if p], [p for p in optional if p]` drops any pool
that came back empty. The `volume` group contains three conditions and **all
three are FILTERs** (`relative_volume_high`, `volume_surge`,
`volume_not_thin`). Measured over all 13 templates:

| template | `required_groups` declared | pools that survive | dropped |
|---|---|---|---|
| `BREAKOUT` | `("structure","volume")` | `("structure",)` | **`volume`** |
| `MOMENTUM` | `("momentum","volume")` | `("momentum",)` | **`volume`** |
| the other 11 | — | all | none |

No exception, no log line, no field on the returned object. `BREAKOUT` runs as a
single-required-group template and reports itself as a two-group confluence.
And it is worse than the brief said: `volume` is *also* an `optional_group` in
`TREND`, `VWAP`, `OPENING_RANGE`, `LIQUIDITY`, `VOLUME_PROFILE` and
`SUPPLY_DEMAND`, where it silently contributes nothing as well. **Eight of
thirteen templates name a condition group that cannot contribute a signal.**

### The paired experiment

78 `BREAKOUT` rule sets generated from the stock library, five price paths, five
arms sharing every rule set, exit and timeframe. (My first attempt at this
registered the candidate conditions *before* generating, whereupon the
combinator drew them into the required pool and arm 0 and arm 1 came out
byte-identical. I caught it, threw the arm data away and re-ran with generation
first. The contaminated run's sweep data is used elsewhere only after auditing
out every strategy that touched a new condition.)

| arm | signals | trades | trades / strategy-path | strategy-paths ≥30 trades (of 390) | exp R | PF | t |
|---|---|---|---|---|---|---|---|
| 0: shipped (`volume` silently dropped) | 1,209 | 1,209 | 3.10 | 24 | +0.011 | 1.07 | +0.83 |
| 1: + `volume_expansion_directional` (new SIGNAL) | 268 | 268 | 0.69 | 0 | +0.005 | 1.04 | +0.19 |
| 2: + `volume_surge` (FILTER) | 420 | 420 | 1.08 | 0 | +0.017 | 1.14 | +0.86 |
| 3: + `relative_volume_high` (FILTER) | **0** | **0** | 0.00 | 0 | — | — | — |
| 4: `volatility_compressed` → `volatility_normal` | 11,011 | 11,011 | 28.23 | 75 | +0.034 | 1.12 | +3.98 |

Paired, per rule set per path (390 pairs): the SIGNAL arm destroys 941 of 1,209
trades (-78%); the `volume_surge` arm destroys 789 (-65%); the
`relative_volume_high` arm destroys all 1,209. Arm 4 adds 9,802.

Three things fall out of that table.

**`relative_volume_high` is a total veto.** It requires volume ≥ 1.3x the
same-clock-minute average and passes on **0.7%** of completed 15-minute RTH
bars in this generator. Any strategy carrying it takes zero trades. It is
currently reachable as an optional filter on `MOMENTUM` and `OPENING_RANGE`;
whoever owns those should know that the "with the filter" arm of their paired
test is an empty set, not a control.

**The new SIGNAL duplicates evidence the library already has.** I built
`volume_expansion_directional` to the brief's spec — fires both ways, reachable
from `BREAKOUT`/`MOMENTUM` via the `volume` group, requires participation
expansion (`rel_volume ≥ 1.25` or regime HIGH/SURGE) *and* a decisive close
(top or bottom third of the bar's range). It fires on 16.34% of completed RTH
5m+15m bars, so it is not degenerate. But its direction has to come from
somewhere, and the only thing available is the bar's own sign. Measured over
24,960 bars on two paths, against every SIGNAL in the library:

| existing condition | co-fires on this % of the new condition's firings | agrees on direction |
|---|---|---|
| `delta_confirms_bar` | 87.3% | **100.0%** |
| `range_position_extreme` | 31.5% | 99.5% |
| `imbalance_bar` | 21.2% | 100.0% |
| `price_above_ema50` | 100.0% | 65.4% |
| `cvd_directional` | 100.0% | 57.6% |

`delta_confirms_bar` is in the `orderflow` group. A `BREAKOUT` confluence of
"structure + volume + order flow" holding both conditions would be counting the
bar's sign twice and calling it two kinds of evidence — the exact property
`GLOBAL_EXCLUSIVE` exists to name. Volume has no direction of its own. Any
SIGNAL you put in that group will borrow one, and every available lender is
already in the library.

**The compression filter is the real constraint.** `volatility_compressed`
passes 3.1-3.2% of 15-minute RTH bars. Arm 4 is the only configuration in any
of my four families with a sample worth the word: 11,011 trades, 19 of 78 rule
sets clearing 30 pooled trades, a 60/40 chronological holdout that holds up
(IS +0.0328R at t=+2.96; OOS +0.0360R at t=+2.67). And it still publishes
nothing: t = +3.98 against 3.97 free t-units gives a **deflated expectancy of
+0.00005R**, and the best individual rule set in it (+0.1045R over 74 trades,
t=+1.10, 3/5 paths positive) deflates to exactly zero.

### What I argue for

**Move volume confirmation to `base_filters`, and make the silent drop loud —
but land the loud part only once `MOMENTUM` has been fixed too.**

The behaviour fix, which I did not land:

```python
    StrategyTemplate(
        group="BREAKOUT",
        description="Expansion out of compression, gated on participation",
-       required_groups=("structure", "volume"),
+       required_groups=("structure",),
        ...
-       base_filters=("volatility_compressed", "volume_not_thin"),
+       base_filters=("volatility_compressed", "volume_not_thin", "volume_surge"),
-       optional_filters=(..., "volume_surge", ...),
+       optional_filters=(...),                      # volume_surge is now nailed in
    ),
```

and the guard, as a test rather than a runtime raise:

```python
def test_every_required_group_can_supply_a_signal():
    """A required group with no SIGNAL condition is silently dropped by
    _signal_pools, so the template runs with fewer required groups than it
    declares and nothing says so."""
    for t in TEMPLATES:
        for g in t.required_groups:
            assert any(CONDITIONS[n].kind is ConditionKind.SIGNAL
                       for n in CONDITION_GROUPS.get(g, ())), \
                f"{t.group} requires group {g!r}, which has no SIGNAL condition"
```

Why this shape and not the others:

- *Not the new SIGNAL*: it costs 78% of the sample and its direction is
  `delta_confirms_bar`'s at 100% agreement. Landing it would also change
  `MOMENTUM`'s generated universe under a seat that is mid-run, to give them a
  condition that duplicates one they already draw.
- *Not a runtime raise*: `_signal_pools` raising would break `MOMENTUM` on
  import today. The test above fails today for exactly two templates, which is
  the correct blast radius for a defect that belongs to two templates — but a
  failing test is a red suite, and `MOMENTUM` is not mine to fix. The test is
  ready; it goes in when `MOMENTUM`'s owner moves `volume` out of its required
  groups.
- *`volume_surge` and not `relative_volume_high`*: 30% pass rate versus 0.7%.
  One is a gate, the other is a wall.

And loudly: **fix `volatility_compressed` first.** Enforcing a confluence
requirement on a template that takes 3.1 trades per 120 days is arranging the
furniture in a house with no floor.

---

## Why FIBONACCI and MEAN_REVERSION produce nothing

`Strategy.evaluate` returns `None` the moment two SIGNAL conditions disagree on
direction. So a template's yield is governed by the *joint* probability that its
required conditions co-fire **and** agree. Measured over five paths, one
evaluation per completed RTH bar (9,360 bars at 5m, 3,120 at 15m, 840 at 60m
per path):

| template's required pair | best pairing, joint co-fire-and-agree | worst |
|---|---|---|
| `TREND` (`trend` x `structure`) — control | **46.48%** | 2.26% |
| `FIBONACCI` (`fibonacci` x `trend`) | **5.00%** | 0.07% |
| `REVERSAL` (`meanreversion` x `orderflow`) | 12.72%, but see below | 1.15% |
| `MEAN_REVERSION` (`meanreversion` x optional `momentum`) | 30.26%, but see below | 0.00% |
| `MEAN_REVERSION` (`meanreversion` x optional `structure`) | 9.10% | 0.00% |

### FIBONACCI: the template fights itself, and the leg anchor is broken

The best `fibonacci` x `trend` pair — `fib_shallow_retrace` +
`ema_fast_above_slow` — co-fires and agrees on 5.00% of bars against TREND's
46.48%, a 9.3x gap. The worst, `fib_extension_reached` + `di_direction`, is
0.07%: roughly six bars in ten thousand.

But the interesting number is the *ordering*. Agreement with `price_above_ema50`
at 5m:

| fib condition | retracement depth | agreement with `price_above_ema50` |
|---|---|---|
| `fib_shallow_retrace` | 0.382-0.5 | 57.9% |
| `fib_golden_pocket` | 0.618-0.786 | **34.7%** |
| `fib_extension_reached` | 1.272-1.618 (opposite side) | 9.8% |

Agreement falls monotonically with retracement depth, and the golden pocket is
*anti*-correlated with the trend read. That is mechanical, not accidental: a
0.618-0.786 retracement of an up leg is, by construction, a price near the
bottom of that leg — which is exactly where a moving-average trend filter
computed on the same prices has already rolled over. `FIBONACCI` requires a
deep pullback and simultaneously requires a trend condition that the pullback
has invalidated. The deeper the fib, the more self-contradictory the template.

The leg anchor is a second, independent defect, and it replicated 15/15
(5 paths x 3 timeframes):

| | direction defined on | direction flips | one flip every | agrees with `structure_trend` |
|---|---|---|---|---|
| shipped `swing_leg()` | 32,531-32,686 of 33,120 5m bars | 4,911-5,028 | **6.6 bars** | **47.4-64.7%** (mean 52.9%) |
| BOS-anchored leg | 33,075-33,101 | 1,563-1,601 | 20.8 bars | **94.7-98.8%** (mean 96.8%) |

That confirms the trend seat's diagnosis on my own data and extends it to three
timeframes and five paths. `swing_leg()` returns the bounding box of the last
confirmed high and the last confirmed low; whichever was confirmed more recently
sets the direction, so the "impulse leg" reverses every time a new fractal
prints on the other side. Its agreement with the structure read is 52.9% — the
leg direction carries essentially no information about the trend it is supposed
to be a retracement of.

**The fix works and does not pay.** I anchored the leg to the impulse that
actually broke structure: walk the swings visible at this bar (via
`TimeframeFrame.visible_swings`, which is masked by `confirmed_index`, so no
look-ahead), find the most recent swing high that exceeded the previous swing
high (or low below the previous low), and take the leg from the extreme swing
that preceded the break to the break itself. Paired swap, 35 rule sets, five
paths, nothing else changed:

| | trades | exp R | PF | win% | t | maxDD | avg MFE | avg MAE |
|---|---|---|---|---|---|---|---|---|
| shipped leg | 153 | -0.0927 | 0.78 | 54.2 | -1.36 | 18.6R | 0.85R | 0.63R |
| BOS-anchored leg | 129 | -0.0952 | 0.80 | 45.7 | -1.10 | 23.8R | 0.89R | 0.67R |

Every one of those 282 trades was on the 5-minute timeframe; the 15m and 60m
pairs took **zero trades in both arms**. The BOS leg trades slightly *less*, not
more, and there is a reason: it anchors to a larger impulse, so its 0.382-0.5
band is wider and further from price, and price sits inside it less often. It
buys direction agreement and spends location frequency.

One suggestive counter-observation I am explicitly **not** treating as a result:
in the contaminated run, where the combinator could draw the BOS conditions
itself, `fib_shallow_retrace_bos` rule sets took 858 trades at +0.0244R against
`fib_shallow_retrace`'s 88 at +0.0178R. That is a 10x sample difference in
favour of the fix — but the generator paired the two with *different* partner
conditions and different timeframes, so it is not a controlled comparison and I
will not quote it as one. The controlled comparison is the table above, and it
says: real defect, no payoff.

### MEAN_REVERSION: the only partners that agree are restatements

The three `meanreversion` conditions agree with each other **100.0% of the time
they co-fire**, on every path and timeframe. So the required group is not the
constraint — as the brief said, they are three spellings of "price is far from
its mean in volatility units", and `bollinger_mean_pull` fires on 35.8% of 5m
bars.

The constraint is what the template is allowed to add. `MEAN_REVERSION` needs at
least two signals, so every strategy pairs its fade with an optional condition
from `momentum`, `vwap`, `orderflow`, `structure`, `profile` or `fibonacci`.
Ranked by joint co-fire-and-agree, the `momentum` partners split cleanly in two:

| partner | co-fire x agreement | what it is |
|---|---|---|
| `bollinger_mean_pull` + `stoch_extreme` | **30.26%** at 100.0% agreement | another extremity oscillator — **banned by `GLOBAL_EXCLUSIVE`** |
| `keltner_outside` + `stoch_extreme` | **29.51%** at 100.0% | same — **banned** |
| `keltner_outside` + `rsi_extreme_reversal` | 16.78% at 100.0% | same idea, not banned |
| `bollinger_extreme` + `stoch_extreme` | 13.65% at 100.0% | same idea, not banned |
| `bollinger_mean_pull` + `stoch_directional` | 5.44% at 15-23% | genuinely different evidence |
| `keltner_outside` + `rsi_directional` | **0.00%** | genuinely different evidence |

Every partner that agrees ≥100% of the time it co-fires is a restatement of the
required condition. Every partner that is genuinely different evidence agrees
12-38% of the time, because a fade and a with-flow read are opposite statements
by construction. Mean joint over all 18 meanreversion x momentum pairs: 7.75%.
Over all 15 meanreversion x structure pairs: 2.04%.

So the template has exactly two ways to produce a strategy: pair with a
restatement (which the diversity rule is right to refuse) or pair with a
contradiction (which `evaluate` is right to refuse). And the two best
restatements are already in `GLOBAL_EXCLUSIVE`. The result at rule-set level, 19
distinct stock rule sets, five paths, 600 trading days:

```
tf15  signals=23  delta_divergence_keltner_outside_poc_reversion_structure_trend
tf5   signals=17  bollinger_mean_pull_stoch_directional_structure_trend_vwap_band_extension
tf5   signals= 3  bollinger_mean_pull_cvd_directional_lvn_rejection_rsi_extreme_reversal
tf5   signals= 2  keltner_outside_lvn_rejection_macd_directional
tf60  signals= 1  ... tf60 signals=1 ...
  and THIRTEEN rule sets with signals=0
```

**The template shape is wrong, and it is wrong in a way the diversity rule
causes.** The rule protects against counting one observation twice, which is
correct. But it is stated over condition *groups*, and `meanreversion`,
`momentum` and `profile` each contain both extremity conditions and directional
conditions. A fade needs corroboration that is *different evidence about the
same claim* — absorption, a location reference, a failed break — not a different
group. The concrete repair I would test next (I did not have budget to run it):
give `MEAN_REVERSION` a required pair of `meanreversion` x `{liquidity,
profile, supplydemand}` — location and failure evidence, which can agree with a
fade without restating it — instead of a required single group plus optionals
drawn from six groups that are mostly with-flow.

`REVERSAL` has the same disease in a sharper form. Its required
`meanreversion` x `orderflow` pair has exactly one member that agrees:
`delta_divergence`, at 99.5-99.8% agreement with `bollinger_mean_pull` — i.e. a
near-perfect duplicate, and a `GLOBAL_EXCLUSIVE` candidate on the published
criterion. The alternatives are anti-correlated: `cvd_directional` fires on
**100% of bars** (it is the sign of a session cumulative, so it is always
something) and agrees with `bollinger_mean_pull` on 34.1% / 21.4% / 12.6% of
co-fires at 5m / 15m / 60m. Agreement falls as the timeframe rises, because the
longer the session cumulative runs the more certainly it points the way price
has stretched. Result: of 21 distinct rule sets, 11 took zero signals and one —
`delta_divergence + keltner_outside + poc_reversion` — took 279 of the family's
322 trades.

---

## What I withheld and why

**Everything. Nothing from my four families is published as live-eligible.** The
specific things I could have published and did not:

**1. The `REVERSAL` walk-forward "survivor".** My first pass produced: OOS n=209,
expectancy +0.1929R, PF 1.58, t = +2.98, max drawdown 5.9R, in-sample/out-of-sample
efficiency 0.99. That is a publishable-looking line. It is three separate
artifacts stacked:

- *It is one hypothesis counted four times.* All four "selected strategies" are
  `reversal_delta_divergence_keltner_outside_poc_reversion` with different
  optional news filters. Their trades are near-identical. Deduplicating to one
  variant per rule set takes n=209 → **59** and t = +2.98 → **+1.61**.
- *The selection pool was 3.9 candidates per fold.* "Top 5 of 4" is not
  selection, it is "did this one rule set have a positive training expectancy in
  this fold". The OOS estimate is therefore conditional on that, which is
  exactly the selection it was supposed to be immune to.
- *Unconditionally, the same rule set loses.* Over all five paths and all 120
  days: n=279, expectancy **-0.0174R**, t = -0.30, **2 of 5 paths positive**
  (+0.22, -0.21, +0.13, -0.06, -0.15). Its Monte Carlo risk of ruin on the
  $50,000 account is 91.4% at $500 risk per trade and 41.9% at $250.

Deflated against the real hypothesis count, its expectancy is **+0.0000R**.

**2. The `BREAKOUT` compression-relaxed arm.** t = +3.98 over 11,011 trades with
a holdout that holds (IS +0.0328R, OOS +0.0360R) is the strongest thing I found.
It deflates to +0.00005R — five hundredths of a basis point of an R — because
3.97 of those 3.98 t-units are free. Its best individual rule set is +0.1045R
over 74 trades at t = +1.10 and 3/5 paths positive, which deflates to zero. I
also distrust its *sign*: `synthetic_series` gives trend days a drift of 0.020
volatility units per bar, and a breakout/continuation template is precisely the
shape that harvests that. It is very likely measuring the generator.

**3. `MEAN_REVERSION` at 60 minutes**: PF 999, 100% win rate, t = +3.51. Eight
trades.

**4. `FIBONACCI` at 5 minutes, stock library**: +0.112R, PF 1.30, 56.7% win
rate, n=60 over five paths. Below the floor.

### Deflation arithmetic, shown

Distinct rule sets I actually evaluated in the course of this work:

```
  seed-1 yield probe, 400 per family x 4 families, after dedup   1,596
  five-path sweep, 100 per family x 4 families, after dedup        396   (86 distinct rule sets x filter variants)
  run-1 experiment arms (50x3 BREAKOUT + 25x2 FIBONACCI)           200
  run-2 experiment arms (78x5 BREAKOUT + 35x2 FIBONACCI)           460
  -------------------------------------------------------------------
  total hypotheses looked at                                     2,652

  free t-units = sqrt(2 * ln 2652) = sqrt(2 * 7.8831) = sqrt(15.766) = 3.971
```

So an unadjusted t below 3.97 is, on this search, indistinguishable from having
searched. The largest t I observed anywhere was +3.98. I have not counted the
sibling seats' searches; if the desk deflates at portfolio level the threshold
is higher still, not lower.

### Monte Carlo, $50,000 account

5,000 bootstrap paths, 250 trades, $5,000 trailing failure drawdown:

| series | n | $500/trade | $250/trade | P(profitable) |
|---|---|---|---|---|
| `MEAN_REVERSION` family | 176 | **100.00%** ruin | 99.86% | 0.0% |
| `REVERSAL` family | 1,214 | 94.32% | 49.90% | 28.3% |
| `FIBONACCI` family | 309 | 92.08% | 48.48% | 6.3% |
| the `REVERSAL` "survivor", unconditional | 279 | 91.4% | 41.9% | 38.3% |
| `BREAKOUT` compression-relaxed arm | 11,011 | 67.64% | 12.28% | 71.7% |

`BREAKOUT` family as shipped was not simulated: n below 30.

### Bias checks I ran, and what I found

| check | finding |
|---|---|
| look-ahead / future data | Entries fill at the **next bar's open** (`FillModel.entry_on_next_open`); stop wins over target within a bar; gaps fill at the open. My BOS leg uses `visible_swings()`, which is masked by `confirmed_index`. Verified by reading, not assumed. **Clean.** |
| repainting indicators | `swing_leg()` uses `last_swing_high/low_index`, both from confirmed swings. It is *unstable* (a flip every 6.6 bars) but it does not repaint — a flip is new information arriving, not old information changing. **Clean, but the instability is the F-finding above.** |
| data-mining bias | 2,652 hypotheses, arithmetic above. Deflation kills everything. **Found and fatal.** |
| multiplicity hidden inside the count | Filter variants inflate the apparent hypothesis count 4-6x (`MEAN_REVERSION` 100 strategies = 25 rule sets; `BREAKOUT` 96 = 16). The `REVERSAL` walk-forward "survivor" was one rule set counted four times. **Found; corrected everywhere above.** |
| insufficient sample | 13 of 19 `MEAN_REVERSION` rule sets and 11 of 21 `REVERSAL` rule sets take **zero** signals in 600 trading days. Three of four families have no walk-forward at all. **Found; it is the main finding.** |
| unrealistic fills / understated costs | Costs are 0.009-0.030R per trade against gross expectancies of ±0.05R. Nothing survived at zero cost either, so costs are not what killed anything — but they would have been decisive if anything had been marginal. **Checked.** |
| parameter sensitivity | Not swept as a grid, deliberately. The one parameter I did perturb — `volatility_compressed` → `volatility_normal` — moved the sample 9.1x and the expectancy from +0.011R to +0.034R. A family whose trade count moves an order of magnitude on one filter has no stable parameterisation to sweep. **Found.** |
| survivorship | Not applicable: one symbol, one continuous synthetic series, no universe selection. |
| my own contamination | My first run registered the experimental conditions before generation, and the combinator drew them into `FIBONACCI`'s and `BREAKOUT`'s required pools. I audited every sweep strategy for them (`MEAN_REVERSION` 76/100 clean, `REVERSAL` 84/100, `FIBONACCI` 65/100, `BREAKOUT` **0/96**), report only the clean subset, and re-ran both experiments from scratch with generation first. **Found by me, disclosed, corrected.** |
| shared source moving under me | Sibling seats modified shared source during my run: `strategies/base.py` (anchored targets) plus `backtest/objectives.py` and `tests/test_anchored_targets.py`; then `backtest/metrics.py`, `strategies/combinator.py` (exclusions re-checked after filters are attached) and `strategies/library.py` (`above_vwap` tightened from "close != vwap", which fired on 99.99% of bars, to "beyond the first VWAP band"). None of those touch the `volume` group or the `BREAKOUT`/`FIBONACCI`/`MEAN_REVERSION` templates, so my four findings stand. Two of them do change what my families *generate*: `above_vwap` is an optional signal for `MEAN_REVERSION` and `REVERSAL`, and the combinator now drops rule sets whose filter set violates an exclusion. All five of my processes launched within one second of each other so they are mutually consistent, but **the sweep numbers here were measured against a tree that no longer exists and will not reproduce byte-for-byte.** **Disclosed.** |

---

## Measurements

Reproduction: scripts in
`/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad/rb/`
(`run.py` five-path sweep, `run2.py` clean paired experiments, `p4_cofire.py`
condition co-fire, `overlap.py` new-condition trigger overlap, `analyse*.py`).

**Condition trigger rates**, mean of five paths, one evaluation per completed
RTH bar:

| condition | 5m | 15m | 60m |
|---|---|---|---|
| `cvd_directional` | 100.00% | 100.00% | 100.00% |
| `ema_fast_above_slow` | 100.00% | 100.00% | 99.40% |
| `macd_directional` | 100.00% | 100.00% | 99.17% |
| `price_above_ema50` | 100.00% | 100.00% | 98.33% |
| `delta_confirms_bar` | 80.01% | 79.62% | 78.55% |
| `structure_trend` | 62.47% | 61.54% | 59.29% |
| `bollinger_mean_pull` | 35.79% | 36.03% | 54.40% |
| `keltner_outside` | 30.78% | 38.28% | 42.29% |
| `bollinger_extreme` | 12.46% | 13.67% | 28.40% |
| `delta_divergence` | 14.54% | 14.49% | 22.17% |
| `fib_golden_pocket` | 9.56% | 8.95% | 8.45% |
| `fib_shallow_retrace` | 8.36% | 8.42% | 6.24% |
| `fib_extension_reached` | 1.89% | 1.87% | 1.81% |

Four conditions fire on essentially every bar. `cvd_directional` at 100% is not
a signal, it is a coin whose face is read every bar; as a required
`REVERSAL` partner it halves the signal space for free.

**Filter pass rates**, completed 15m RTH bars, seeds 1 and 2 (n=3,120 each):

| filter | seed 1 | seed 2 |
|---|---|---|
| `volume_not_thin` | 100.0% | 100.0% |
| `volatility_normal` | 79.5% | 81.3% |
| `avoid_lunch` | 76.9% | 76.9% |
| `volatility_expanding` | 69.7% | 69.6% |
| `mtf_not_conflicted` | 64.3% | 62.1% |
| `regime_ranging` | 58.7% | 58.3% |
| `volume_surge` | 30.2% | 29.7% |
| `regime_trending` | 20.4% | 22.2% |
| **`volatility_compressed`** | **3.1%** | **3.2%** |
| **`relative_volume_high`** | **0.7%** | **0.6%** |
| (`volume_expansion_directional`, proposed) | 21.0% | 21.4% |

**Leg anchor**, all five paths, all three timeframes:

| seed | tf | shipped: defined / flips / agrees with `structure_trend` | BOS: defined / flips / agrees |
|---|---|---|---|
| 1 | 5 | 32,531 / 4,911 / 52.4% | 33,100 / 1,564 / 96.6% |
| 1 | 15 | 10,852 / 1,621 / 47.4% | 11,024 / 527 / 96.4% |
| 1 | 60 | 2,725 / 386 / 53.6% | 2,748 / 146 / 94.7% |
| 2 | 5 | 32,622 / 4,961 / 50.5% | 33,075 / 1,584 / 97.2% |
| 2 | 15 | 10,861 / 1,681 / 51.6% | 11,012 / 534 / 96.4% |
| 2 | 60 | 2,709 / 419 / 64.7% | 2,726 / 137 / 97.9% |
| 3 | 5 | 32,628 / 4,993 / 51.5% | 33,097 / 1,569 / 97.4% |
| 3 | 15 | 10,857 / 1,601 / 49.8% | 11,013 / 509 / 96.3% |
| 3 | 60 | 2,674 / 381 / 45.1% | 2,741 / 151 / 97.5% |
| 4 | 5 | 32,598 / 4,979 / 51.2% | 33,101 / 1,601 / 98.1% |
| 4 | 15 | 10,850 / 1,662 / 49.9% | 11,018 / 518 / 95.9% |
| 4 | 60 | 2,635 / 374 / 64.3% | 2,723 / 130 / 98.8% |
| 5 | 5 | 32,686 / 5,028 / 51.2% | 33,092 / 1,563 / 97.9% |
| 5 | 15 | 10,833 / 1,631 / 49.7% | 11,017 / 523 / 97.3% |
| 5 | 60 | 2,692 / 386 / 62.8% | 2,736 / 141 / 96.1% |

Fifteen of fifteen. The shipped range (45.1-64.7%) and the BOS range
(94.7-98.8%) do not touch.

**Timeframes.** I tested 5, 15 and 60 individually and with the generator's
default confirmation groups (5→(15,60), 15→(60)). I have **no evidence either
way** on whether multi-timeframe alignment helps my families, and I will not
pretend otherwise: three of four families had too few trades for the comparison
to mean anything, and the fourth (`BREAKOUT`, compression-relaxed) is a
configuration I do not trust the sign of. What I can report is where the trades
are: `BREAKOUT` as shipped takes 1,206 of its 1,209 trades at 60m and **zero**
at 5m; the `FIBONACCI` paired experiment took all 282 of its trades at 5m and
zero at 15m or 60m. Those are template-level blackouts, not preferences.

**Test suite**: when I finished my runs, `python -m pytest tests/ -q` →
**694 passed in 35.00s** (675 at the HEAD I was given; the difference is a
sibling seat's `tests/test_anchored_targets.py`). Re-run twenty minutes later,
after further sibling edits landed: **695 passed, 2 failed**. Neither failure is
mine — **I changed no source file and weakened no test**, and both failures come
from tests that do not exist at HEAD:

- `test_no_signal_condition_fires_on_almost_every_bar` (new, in another seat's
  working copy) fails on `cvd_directional` 99.95%, `ema_fast_above_slow` 99.24%,
  `macd_directional` 98.73%, `macd_hist_direction` 98.73%, `price_above_ema50`
  98.17%, `price_above_ema200` 92.75%. That is an independent confirmation of
  the trigger-rate table above, arrived at by a different seat on different
  fixtures. They have fixed `above_vwap` and the other six are still open.
- `test_annualisation_uses_calendar_days_not_active_days` (new) fails against
  that seat's in-flight `backtest/metrics.py` change.

A clean verification of this document needs a tree where those two seats have
finished.
