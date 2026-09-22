# Cross-symbol and timeframe-group backtesting

**Seat:** every symbol except plain single-timeframe MNQ (so: **MES, MGC, MCL**),
plus **timeframe groups** on all symbols.
**Repo:** `claude/intelligent-feynman-ongyjw`, baseline `5b3ae0c`. Every run in
this file executed against the tree as it stood 18:16-18:53 UTC on 2026-09-22;
see `### Test suite and provenance` for why that sentence needs the timestamps.
**Data:** synthetic only, from `futures_agents.data.loader.synthetic_series`.
**Compute:** 11 independent runs, 60 trading days each, 82,800 one-minute bars
each, ~1,215 unique strategies per run backtested in a single pass over the bars.
76,433 trades recorded in total.

Nothing in this file is a live edge. The generator is a near-martingale by
construction and every symbol's pooled result is negative after costs, which is
the sanity check the loader's own docstring asks for. What is measurable here is
*structure*: how contracts differ in cost economics and session shape, how stable
a family ranking is, whether higher-timeframe confirmation changes anything, and
how far the news filter can physically reach on each contract. Those are
properties of the system, not of the market, and they transfer to real data even
though the P&L does not.

---

## Verdict

1. **Symbol independence is real and it is not mainly about strategy
   preference - it is about cost and session geometry.** The all-in round turn
   is 5.4% of the median trade's risk on MCL and 1.7% on MGC, a 3.2x spread.
   MGC's session is 08:20-13:30 and MCL's is 09:00-14:30; neither is the equity
   session, and every "opening range", "lunch" and "power hour" assumption
   written for 09:30-16:00 is measuring a different hour of a different day on
   those two contracts. Those differences are deterministic and survive any
   amount of resampling.

2. **Family rankings do not transfer between contracts - but they do not
   transfer *within* a contract either, so "MES prefers different families from
   MGC" is not yet a claim I can make.** Cross-contract Spearman on family
   expectancy ranges -0.71 to +0.57. The control I ran for it - the same test
   between two independent price paths of *the same* contract - ranges -0.70 to
   +0.36. The ranking is not stable enough to transfer anything. At 60 days and
   ~400 strategies per run the correct statement is "no evidence either way",
   and any file claiming "MES is a mean-reversion contract" from a run this size
   is reporting noise. See `## Does a strategy transfer between contracts?`.

3. **Higher-timeframe confirmation buys fewer trades, and no measurable
   improvement in the ones that remain.** Pooled over 126 matched rule sets on
   all four contracts and all three named groups: one higher-timeframe
   confirmation keeps **44% / 42% / 28%** of the trades (1m+5m+15m, 5m+15m+1h,
   15m+1h+4h) for a paired expectancy change of **-0.033R, t = -1.81, p = 0.072,
   95% CI [-0.069, +0.003]**. Two confirmations keep 10-21% of the trades and
   still show nothing. The specification's question has an answer on this data
   and the answer is "mostly just fewer of them".

4. **Two defects make part of the timeframe question unanswerable as the code
   stands** (next section). `ExitModel.time_stop_bars` is counted in *base*
   (1-minute) bars, not the strategy's own bars, so every 1h and 4h strategy in
   the default exit catalogue is force-closed inside 30-120 minutes; and the
   combinator's `confirm_tfs` binding is a no-op for exactly the condition group
   called `multitimeframe`. I did not edit source - four sibling seats are
   running against this tree - but both are proved below with failing
   measurements.

5. **The MCL news question is answerable for the first time, and the answer on
   this data is "no detectable effect".** The symbol-scoped calendar works: the
   filter's mechanical reach is **0.92% of MCL trades and 0.97% of MGC trades
   against 0.14% on MES and 0.46% on MNQ**, and the vetoed MCL trades cluster at
   10:30-10:45 (EIA) while MES's cluster at 14:00-14:16 (FOMC). But the paired
   expectancy difference on MCL is **+0.0037R, t = +0.89, p = 0.38**, and the
   trades the filter removes are statistically indistinguishable from the ones it
   keeps. That is the expected result: the generator places its one daily shock
   at a uniformly random minute, so there is nothing at 10:30 to stand aside for.

6. **Nothing is published as live-eligible.** After deflating for the number of
   strategies searched (504-1,018 per symbol, `sqrt(2 ln n)` = 3.53-3.72), every
   symbol's walk-forward out-of-sample t-statistic goes negative and the deflated
   expectancy is 0.000R. Three of eleven (symbol, seed) walk-forwards came out
   "credible" by `WalkForwardResult.is_credible`; none replicated on the same
   contract's other seeds.

---

## Two defects found in the engine and combinator

Both were proved before I looked at any performance number, and both change how
the timeframe results must be read. Script:
`/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad/xsym/probes.py`.
**I did not edit source.** Four other seats are running backtests against this
working tree and changing exit semantics mid-flight would silently invalidate
their in-progress results. These are reported for the owner to fix.

### Defect 1 - `time_stop_bars` is counted in 1-minute bars on every timeframe

`BacktestEngine._manage` sets `pos.bars_held = i - pos.entry_index + 1`, where
`i` indexes `frame.base.bars` (1-minute), and `ExitModel.time_stop_bars` is
compared directly against it. The field is named *bars* and the combinator's exit
catalogue sets 30-120 of them alongside comments describing intraday holds.

Measured on MES, 6 days, one strategy per primary timeframe, `time_stop_bars=10`,
session-close exit disabled:

| primary | median bars_held | median minutes_held | exit reasons |
|---|---|---|---|
| 5m | 10 | 9 | TIME 182, STOP 28 |
| 60m | 10 | 9 | **TIME 234 (100%)** |
| 240m | 10 | 9 | **TIME 142 (100%)** |

A 4-hour strategy is closed after ten minutes - before 5% of one primary bar has
formed. The footprint is visible in the production runs: on the full universe,
TIME accounts for 912/1,665 of MNQ's 60m exits, 1,567/2,425 of MES's,
900/1,635 of MGC's, and **73/73 (100%) of MGC's 240m exits** and 516/835 of
MCL's. Every number in this file at 60m and 240m is a measurement of a
time-stopped strategy, not of that timeframe.

### Defect 2 - `Condition.bind()` is inert for the `multitimeframe` group

`combinator._build_strategy` binds conditions whose group is `multitimeframe` to
the confirmation timeframe, so a "multi-timeframe strategy really reads a
different timeframe". It does not. Both conditions in that group ignore their
`tf` argument: `mtf_aligned` calls `snap.alignment()` (which averages over every
timeframe present in the frame) and `mtf_not_conflicted` reads
`snap.tfs.values()`.

Measured on 213 MES snapshots, comparing evaluation at tf=5 against `bind(240)`:

| condition | disagreements |
|---|---|
| `mtf_aligned` | **0 / 213** |
| `mtf_not_conflicted` | **0 / 213** |
| `structure_trend` (control) | 129 / 213 |

The binding does change `Condition.label`, which changes `Strategy.strategy_id`,
so the generator emits two distinct strategy ids that trade identically. That
wastes sampling budget and inflates the trial count the deflation term is
computed from.

**Consequence, measured on the real sweep:** across 4,114 rule keys generated
both with and without the confirmation map, **58.6% are structurally identical**
(the same condition labels, hence provably the same trades). Of the 1,704 that do
differ structurally, 770 were backtested in both arms and **613 (80%) produced a
bit-identical R series anyway**. Turning the confirmation group on or off changes
nothing at all for roughly **91%** of what the combinator generates. That is why
I built the paired timeframe test by hand rather than relying on `confirm_tfs`.

---

## How I kept the price paths independent

`synthetic_series` seeds a single `random.Random(seed)`. The only symbol-specific
inputs are the starting price and a volatility scale, so **the same seed produces
the same return path for every contract**. Measured on 20 days (27,599 one-minute
log returns), `seed=5` for all four symbols:

| pair | correlation |
|---|---|
| MNQ / MES | **+0.9995** |
| MNQ / MGC | +0.9991 |
| MNQ / MCL | +0.9943 |
| MES / MGC | +0.9994 |
| MES / MCL | +0.9956 |
| MGC / MCL | +0.9970 |

Running MES and MGC on "the same data" and finding they behave alike would have
been a tautology about the generator.

**What I did instead.** Every run uses `seed = base_seed + sum(ord(c) for c in
symbol)`, the convention `SyntheticProvider` already uses, with base seeds spaced
1,000 apart so the ~21-point spread of symbol offsets can never collide. The
eleven runs therefore use **eleven distinct seed integers**: 1215, 1220, 1229,
1236, 2215, 2220, 2229, 2236, 3215, 3220, 3229.

Verification, same 20-day window:

| comparison | correlation |
|---|---|
| MNQ/MES, MNQ/MGC, MNQ/MCL at base 1000 | -0.019, +0.007, +0.011 |
| MES/MGC, MES/MCL, MGC/MCL at base 1000 | -0.001, +0.006, +0.006 |
| MCL at base 1000 vs 2000 vs 3000 vs 4000 (6 pairs) | -0.012 to +0.007 |

**Counting rule I applied throughout: independent observations = distinct seed
integers = 11.** A (symbol, seed) pair is *not* an independent observation unless
the seed integer itself is distinct, which under this offset scheme it is. Where
I pool three MES runs I say "3 paths", not "3 symbols x 3 seeds".

**One thing this does not buy.** Distinct seeds give distinct *price paths*; they
do not give distinct *contracts*. The generator's dynamics - regime transition
matrix, intraday volatility smile, tail mixture, delta model - are identical for
every symbol. So any genuine per-symbol difference I find can only come from the
deterministic inputs: tick size, point value, cost model, RTH window, and the
symbol-scoped economic calendar. It cannot come from the price process, because
there is only one price process. Every "MES prefers X" statement below is
therefore either (a) a cost/session-geometry effect, which is real, or (b) noise,
which is most of them.

**A control the sibling seats' work needs.** A seat that runs
`generate_strategies` without an explicit `groups=` argument now gets a
*different family universe per symbol*: an uncommitted `futures_agents/strategies/profiles.py`
appeared in the working tree during this run and `generate_combinations` was
patched to consult it. MNQ, MES and MGC each get 6-7 named families; MCL has no
profile and gets all 13. Comparing family rankings across contracts tested on
different family sets is not a comparison. **Every generation call in my runs
passes the full 13-template list explicitly**, so all four contracts were tested
on an identical strategy universe.

---

## Per-symbol results

Universe: all 13 templates, primary timeframes 1m/5m/15m/1h/4h, `max_total=450`
per run, exits from the combinator's own catalogue, costs from
`futures_agents/config.py` (commission + exchange fee charged as dollars,
slippage applied to fill prices: 0.5 ticks base on entries, 1.5 on stops, plus
volatility and thin-book adders).

### Contract economics - the part that is genuinely per-symbol

| | tick | point value | tick value | round turn (spec) | typical ATR | RT / ATR | RTH (ET) |
|---|---|---|---|---|---|---|---|
| MNQ | 0.25 | $2 | $0.50 | $1.94 | 120 pt | 0.81% | 09:30-16:00 |
| MES | 0.25 | $5 | $1.25 | $2.69 | 45 pt | 1.20% | 09:30-16:00 |
| MGC | 0.10 | $10 | $1.00 | $2.44 | 28 pt | 0.87% | **08:20-13:30** |
| MCL | 0.01 | $100 | $1.00 | $2.44 | 1.85 pt | 1.32% | **09:00-14:30** |

Measured against what the strategies actually risked:

| | trades | median risk | median risk $ | round turn / risk | fee drag (measured) | gross exp | net exp |
|---|---|---|---|---|---|---|---|
| MNQ | 6,252 | 42.0 pt | $84.00 | 2.3% | 0.0261R | -0.0135R | -0.0397R |
| MES | 9,312 | 11.63 pt | $58.12 | 4.6% | 0.0445R | -0.0721R | -0.1166R |
| MGC | 5,328 | 14.20 pt | $142.00 | **1.7%** | 0.0178R | -0.0024R | -0.0202R |
| MCL | 4,473 | 0.45 pt | $45.00 | **5.4%** | 0.0371R | -0.0009R | -0.0380R |

"Fee drag" is the commission+exchange component the engine charges as dollars;
the slippage component is already inside `gross_r`, and at the model's realised
~2 ticks round turn it adds a further ~0.012R on MNQ, ~0.043R on MES, ~0.014R on
MGC and ~0.044R on MCL. **All-in, an MCL strategy must clear roughly 7.6% of its
own risk unit before it has any edge at all; an MGC strategy must clear 2.4%.**
That is the single most transferable number in this file and it owes nothing to
the synthetic generator - it is arithmetic on the contract specs.

The practical rule: the sampled strategies stop MCL a median of 0.45 points, which
is 45 ticks, and the round turn is ~2.4 of those ticks. Any MCL idea whose stop is
tighter than ~20 ticks is spending more than 10% of its risk on the round turn
before the market moves.

### Pooled performance per symbol (all families, all timeframes, net)

| | n | win | avg win | avg loss | payoff | PF | expectancy | t | max DD | avg DD | Sharpe | Sortino | max cons W/L | avg duration | avg MAE | avg MFE |
|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|---|
| MNQ | 6,252 | 44.6% | +0.799R | -0.715R | 1.12 | 0.90 | **-0.0397R** | -3.28 | 259.4R | 112.1R | -0.041 | -0.063 | 26 / 13 | 45.4 min | 0.64R | 0.87R |
| MES | 9,312 | 45.5% | +0.790R | -0.874R | 0.90 | 0.76 | **-0.1166R** | -11.33 | 1099.1R | 519.5R | -0.117 | -0.161 | 18 / 14 | 31.7 min | 0.74R | 0.94R |
| MGC | 5,328 | 47.7% | +0.693R | -0.672R | 1.03 | 0.94 | **-0.0202R** | -1.78 | 252.3R | 162.6R | -0.024 | -0.035 | 12 / 14 | 36.9 min | 0.58R | 0.70R |
| MCL | 4,473 | 47.6% | +0.782R | -0.783R | 1.00 | 0.91 | **-0.0380R** | -2.65 | 188.4R | 103.5R | -0.040 | -0.058 | 24 / 24 | 31.0 min | 0.65R | 0.91R |

Every contract is negative, as a near-martingale minus costs must be. The
ordering tracks cost, not skill: MES is worst and also pays the highest fee drag
relative to its median risk among the index micros; MGC is least negative and
pays the least. MES's gross expectancy is genuinely worse than the others
(-0.0721R against ~-0.001R for MGC and MCL), which is a stop-geometry artefact:
its median risk of 11.6 points on a 45-point ATR product puts stops well inside
the noise, and it takes 1,735 of its 2,885 one-minute trades straight to STOP.

### Per-timeframe (each timeframe on its own)

Expectancy / t / n, and the exit mix that exposes Defect 1:

| | 1m | 5m | 15m | 1h | 4h |
|---|---|---|---|---|---|
| MNQ | -0.077 / -2.05 / 1137 | +0.002 / +0.06 / 836 | -0.030 / -1.38 / 2372 | -0.053 / -3.55 / 1665 | -0.015 / -0.70 / 242 |
| MES | -0.298 / -12.90 / 2885 | -0.084 / -4.98 / 3462 | +0.036 / +0.66 / 170 | +0.035 / +2.79 / 2425 | -0.076 / -2.22 / 370 |
| MGC | -0.085 / -3.13 / 1483 | -0.066 / -2.02 / 889 | +0.035 / +1.68 / 1248 | +0.015 / +1.11 / 1635 | +0.113 / +2.36 / 73 |
| MCL | -0.150 / -5.44 / 1636 | +0.029 / +0.79 / 772 | +0.023 / +0.55 / 771 | +0.049 / +1.88 / 459 | +0.015 / +1.12 / 835 |

TIME exits as a share of that timeframe's trades: 1m 1-6%, 5m 20-34%, 15m
26-62%, **1h 55-71%**, **4h 62-100%**. The apparent improvement from 1m to 1h is
substantially the time stop truncating losers before they reach a stop that sits
1.0-2.5 ATR away on a 60-minute chart. **I am not ranking timeframes off this
table**, and neither should anything downstream until Defect 1 is fixed.

The 1-minute row is the one result I would defend: it is negative on all four
contracts with t from -2.05 to -12.90, and the mechanism is not subtle - a
1-minute bar's range on MES is a small multiple of the 4.6% of risk the round
turn consumes, and 60% of 1m trades exit at STOP.

### Session, regime and volatility slices (pooled per symbol)

Session - note MGC and MCL have no `RTH_CLOSE` bucket because their sessions end
at 13:30 and 14:30, and MGC's largest bucket is `PRE_MARKET` because
`classify_session` is written around the equity open while MGC's RTH starts at
08:20:

| | best bucket | worst bucket |
|---|---|---|
| MNQ | RTH_AFTERNOON +0.013 (n=1148) | RTH_CLOSE -0.079, t=-3.90 (n=1220) |
| MES | RTH_MORNING -0.021 (n=1624) | LUNCH -0.258, t=-11.67 (n=1900) |
| MGC | **RTH_OPEN +0.073, t=+2.53** (n=984) | RTH_MORNING -0.078, t=-3.56 (n=1169) |
| MCL | PRE_MARKET +0.028 (n=931) | RTH_AFTERNOON -0.115, t=-4.87 (n=1033) |

Regime:

| | RANGE | TREND_UP | TREND_DOWN |
|---|---|---|---|
| MNQ | -0.076 (n=3863) | +0.056, t=+2.09 (n=1305) | -0.031 (n=989) |
| MES | -0.086 (n=5936) | -0.141 (n=1950) | -0.213 (n=1415) |
| MGC | -0.050 (n=3062) | +0.066, t=+2.63 (n=949) | -0.011 (n=1298) |
| MCL | -0.085 (n=3095) | **+0.164, t=+4.03** (n=503) | +0.011 (n=855) |

Volatility: MES is monotonically worse in HIGH (-0.141, t=-10.12) than NORMAL
(-0.079); MGC is the reverse sign (NORMAL +0.041, t=+2.42; HIGH -0.097,
t=-6.24); MCL is flat in HIGH (+0.010) and negative in NORMAL (-0.082). These
are three different answers to the same question on three contracts, which is
the strongest per-symbol signal in the file - and still unreplicated across
seeds, so I am not promoting any of it.

### Family tables (MIN_TRADES_FOR_RANK = 30 enforced)

Top three and bottom two per contract, by `walkforward.robust_score`. Families
below 30 trades are shown struck out of the ranking, not deleted.

**MES** (3 paths, 9,312 trades)

| rank | family | n | expectancy | t | PF | max DD |
|---|---|---|---|---|---|---|
| 1 | MEAN_REVERSION | 86 | +0.0133R | +0.31 | 1.09 | 4.8R |
| 2 | REVERSAL | 62 | -0.0342R | -0.37 | 0.89 | 8.9R |
| 3 | MULTI_TIMEFRAME | 986 | -0.0526R | -1.66 | 0.88 | 70.0R |
| 12 | TREND | 1,033 | -0.2755R | -6.59 | 0.63 | 290.9R |
| 13 | BREAKOUT | 140 | -0.2950R | -4.50 | 0.37 | 42.4R |
| - | *LIQUIDITY* | *3* | - | - | - | *below rank floor* |

**MGC** (3 paths, 5,328 trades)

| rank | family | n | expectancy | t | PF | max DD |
|---|---|---|---|---|---|---|
| 1 | LIQUIDITY | 339 | +0.0334R | +1.37 | 1.24 | 12.5R |
| 2 | OPENING_RANGE | 122 | +0.0360R | +0.54 | 1.13 | 12.7R |
| 3 | SUPPLY_DEMAND | 156 | +0.0245R | +0.29 | 1.06 | 24.6R |
| 11 | REVERSAL | 57 | -0.2892R | -3.29 | 0.36 | 17.2R |
| - | *FIBONACCI* | *20* | *-0.6175R* | *-4.27* | *0.16* | *below rank floor* |
| - | *TREND, PULLBACK* | *21, 10* | - | - | - | *below rank floor* |

**MCL** (3 paths, 4,473 trades)

| rank | family | n | expectancy | t | PF | max DD |
|---|---|---|---|---|---|---|
| 1 | BREAKOUT | 190 | +0.1229R | +2.83 | 1.79 | 2.6R |
| 2 | MOMENTUM | 137 | +0.1248R | +2.14 | 1.58 | 3.7R |
| 3 | TREND | 69 | +0.2206R | +1.23 | 1.39 | 9.2R |
| 12 | VOLUME_PROFILE | 37 | -0.1751R | -4.10 | 0.17 | 6.5R |
| 13 | REVERSAL | 112 | -0.3006R | -3.57 | 0.48 | 39.6R |
| - | *FIBONACCI, PULLBACK* | *23, 12* | - | - | - | *below rank floor* |

MCL's BREAKOUT (t=+2.83 on 190 trades) and MOMENTUM (t=+2.14 on 137) are the
only cells in the whole sweep that clear both the trade floor and t>2 in a
positive direction. They do not survive deflation - 897 strategies were searched
on MCL, `sqrt(2 ln 897)` = 3.69, so 3.69 t-units are free - and they do not
replicate: BREAKOUT ranks 2nd, 1st, 3rd across MCL's three seeds with
expectancies +0.185, +0.053, +0.045, while MOMENTUM ranks 3rd, outside the top
three, and 1st. Reported, not promoted.

---

## Does a strategy transfer between contracts?

### The naive answer

Spearman rank correlation of family expectancy between contracts, restricted to
families with >=30 trades on both sides:

| pair | families compared | rho | p |
|---|---|---|---|
| MNQ -> MES | 8 | -0.071 | 0.87 |
| MNQ -> MGC | 7 | +0.571 | 0.18 |
| MNQ -> MCL | 9 | +0.050 | 0.90 |
| MES -> MGC | 8 | **-0.714** | 0.047 |
| MES -> MCL | 10 | -0.576 | 0.082 |
| MGC -> MCL | 9 | +0.283 | 0.46 |

Read on its own this looks like a finding: MES and MGC rank families almost in
opposite order, p=0.047. The specification would be vindicated and the seat could
stop here.

### The control that kills it

The same test between two independent price paths of the **same** contract - the
family ranking's own reproducibility ceiling:

| pair | families | rho | p |
|---|---|---|---|
| MNQ seed 1236 vs 2236 | 8 | -0.429 | 0.29 |
| MES 1229 vs 2229 | 10 | -0.345 | 0.33 |
| MES 1229 vs 3229 | 8 | -0.071 | 0.87 |
| MES 2229 vs 3229 | 8 | +0.357 | 0.39 |
| MGC 1215 vs 2215 | 8 | -0.214 | 0.61 |
| MGC 1215 vs 3215 | 5 | -0.700 | 0.19 |
| MGC 2215 vs 3215 | 5 | -0.500 | 0.39 |
| MCL 1220 vs 2220 | 7 | +0.357 | 0.43 |
| MCL 1220 vs 3220 | 7 | +0.179 | 0.70 |
| MCL 2220 vs 3220 | 6 | -0.200 | 0.70 |

Mean within-symbol rho is **-0.16**; mean cross-symbol rho is **-0.08**. The
cross-contract disagreement is *not larger* than the same contract's disagreement
with itself. `MES -> MGC = -0.714` sits inside the range `MGC -> MGC` spans on
its own data (-0.70 to -0.50).

And the headline family flips every single time:

| contract | best family, path 1 | path 2 | path 3 |
|---|---|---|---|
| MNQ | VOLUME_PROFILE (+0.177, n=96) | TREND (+0.124, n=70) | - |
| MES | FIBONACCI (+0.158, n=79) | VOLUME_PROFILE (+0.066, n=131) | OPENING_RANGE (+0.069, n=81) |
| MGC | OPENING_RANGE (+0.144, n=58) | SUPPLY_DEMAND (+0.189, n=74) | LIQUIDITY (+0.073, n=132) |
| MCL | TREND (+0.393, n=44) | BREAKOUT (+0.053, n=56) | MOMENTUM (+0.208, n=34) |

Eleven runs, eleven different winners, and no contract agrees with itself twice.

### What I will and will not claim

**Will:** the specification's rule is correct and the system must keep obeying
it, because the *deterministic* per-symbol inputs differ enormously - a 3.2x
spread in round-turn cost as a share of risk, and two of four contracts whose RTH
is not the equity session. A strategy calibrated on MNQ's 09:30 opening range is
not merely untested on MGC, it is pointed at the wrong hour.

**Will not:** that MES prefers mean reversion, that MGC prefers liquidity, or
that MCL prefers breakouts. Those orderings are indistinguishable from resampling
noise at this sample size, and I measured the noise floor rather than assuming it
was small. To make a per-contract family claim stick you would need the
within-symbol rho to be comfortably positive first; mine is negative. My estimate
is that this needs roughly 10x the trades per family cell - order 300+ per family
per path, against the 37-340 most cells have now.

---

## Timeframe groups vs single timeframes

### Why I built the pairs by hand

The combinator's `confirm_tfs` mechanism cannot answer this question: 58.6% of
rule sets are structurally unchanged by it and 80% of the remainder still produce
identical trades (see Defect 2). So I constructed matched arms directly, all
evaluated on **one** `SymbolFrame` containing 1m/5m/15m/1h/4h so that every
feature, every regime label and every session level is byte-identical between
arms. Rule sets containing `multitimeframe` conditions were excluded, because
those read every timeframe in the frame regardless of which arm they are in.

- **single** - every condition evaluated on the primary timeframe, `confirm_tfs=()`.
- **gate** - identical, plus `structure_trend` bound to the first confirmation
  timeframe as a SIGNAL. `Strategy.evaluate` rejects the bar if any signal
  disagrees on direction, so this is exactly "only trade with the higher
  timeframe".
- **gate2** - plus `structure_trend` on the second confirmation timeframe too.

Groups are the three the specification names: **1m+5m+15m**, **5m+15m+1h**,
**15m+1h+4h**, with the primary being the lowest member. 105 rule sets per run
(35 rule sets x 3 groups) x 3 arms, 4 contracts, 11 paths.

### Pooled trades per arm

| contract | group | single | gate | gate2 |
|---|---|---|---|---|
| MNQ | 1m+5m+15m | n=575, -0.126R, t=-2.45 | n=239, -0.176R, t=-2.28 | n=108, -0.150R |
| MES | 1m+5m+15m | n=3829, -0.127R, t=-6.92 | n=1624, -0.115R, t=-4.19 | n=761, -0.066R |
| MGC | 1m+5m+15m | n=3297, -0.094R, t=-4.86 | n=1475, -0.107R, t=-3.74 | n=619, -0.091R |
| MCL | 1m+5m+15m | n=1374, -0.112R, t=-3.38 | n=598, -0.176R, t=-3.58 | n=264, -0.083R |
| MES | 5m+15m+1h | n=788, -0.032R | n=323, -0.037R | n=80, -0.121R |
| MGC | 5m+15m+1h | n=1238, -0.053R | n=493, -0.018R | n=140, -0.002R |
| MCL | 5m+15m+1h | n=181, -0.154R, t=-2.56 | n=78, -0.093R | n=23, -0.211R |
| MES | 15m+1h+4h | n=625, +0.023R | n=144, +0.052R | n=60, -0.001R |
| MGC | 15m+1h+4h | n=1080, -0.005R | n=314, +0.034R | n=92, -0.101R |
| MCL | 15m+1h+4h | n=75, +0.153R, t=+1.77 | n=23, -0.136R | n=10, +0.058R |

### The paired test

Difference in per-rule-set expectancy, gate minus single, over rule sets where
both arms produced >=5 trades. This is the number the specification asks for:

| group | arm | matched rule sets | mean change in trades | trades kept | paired dExpectancy | t | p |
|---|---|---|---|---|---|---|---|
| 1m+5m+15m | gate | **76** | -63.6 | **44.1%** | **-0.0587R** | **-2.43** | **0.018** |
| 1m+5m+15m | gate2 | 57 | -113.9 | 20.5% | +0.0080R | +0.26 | 0.80 |
| 5m+15m+1h | gate | 32 | -37.9 | 41.8% | +0.0264R | +0.75 | 0.46 |
| 5m+15m+1h | gate2 | 12 | -117.8 | 12.8% | -0.0240R | -0.34 | 0.74 |
| 15m+1h+4h | gate | 18 | -60.9 | 28.3% | -0.0296R | -0.75 | 0.46 |
| 15m+1h+4h | gate2 | 9 | -120.0 | 9.9% | -0.1490R | -2.00 | 0.080 |
| **all groups** | **gate** | **126** | -56.7 | **41.8%** | **-0.0330R** | **-1.81** | **0.072** |

95% CI on the all-groups paired difference: **[-0.0685, +0.0026] R per trade**.
In raw counts: the 126 matched rule sets produced **12,265 trades** in the single
arm and **5,121** in the gate arm.

Per contract (gate, 1m+5m+15m): MNQ -0.069R (8 pairs, p=0.18), MES -0.037R (31
pairs, p=0.35), MGC -0.097R (16 pairs, p=0.16), MCL -0.058R (21 pairs, p=0.18).
Every contract's point estimate is negative; none is individually significant;
pooling them is what produces the p=0.018.

### Answer

**Higher-timeframe confirmation removes 56-72% of the trades and does not improve
the survivors.** The best-powered cell (1m+5m+15m, 76 matched rule sets) shows
them getting *worse* by 0.059R per trade, p=0.018 - one of six cells tested, so
treat the significance with the Bonferroni it deserves (0.018 x 6 = 0.11), but
the sign is consistent across all four contracts. The all-groups estimate is
-0.033R with a CI that only just touches zero on the positive side.

Two caveats that bound this hard:
1. **15m+1h+4h is contaminated by Defect 1.** Its confirmation timeframes are
   exactly the ones whose trades are all time-stopped.
2. **Fewer trades is not automatically worse.** The gate arms have materially
   smaller drawdowns in absolute R simply by trading less. If the paired
   expectancy difference were zero rather than negative, "half the trades for the
   same edge" would be a real improvement in capital efficiency. It is not zero
   in the direction that would make that argument.

I therefore report: **on this data, multi-timeframe alignment does not improve
outcomes; it reduces exposure.** The system should keep testing groups (they are
cheap and the question is contract-specific) but should not hard-code
higher-timeframe confirmation as a quality gate on the strength of theory.

---

## MCL news filter: the paired comparison

### The calendar reach, measured first

Projected from `econ_calendar.project_events(..., symbol=...)` over the same 90
trading days, HIGH impact only, counting events that land inside each contract's
own RTH:

| symbol | RTH | HIGH events projected | **in-session** | which ones | blackout share of session minutes |
|---|---|---|---|---|---|
| MNQ | 09:30-16:00 | 18 | **6** | FOMC x6 | 0.44% |
| MES | 09:30-16:00 | 18 | **6** | FOMC x6 | 0.44% |
| MGC | **08:20-13:30** | 18 | **12** | CPI x4, NFP x4, PCE x4 | 1.12% |
| MCL | **09:00-14:30** | **36** | **21** | **EIA x18**, FOMC x3 | 1.84% |

MCL sees 3.5x MNQ's in-session high-impact events, which is the premise this task
was given. **The premise is also true of MGC and that appears not to have been
noticed**: gold's pit session opens at 08:20, ten minutes before every 08:30 BLS
and BEA print, so CPI, payrolls and core PCE all land inside MGC's RTH. MGC is
the second contract on this desk where the news dimension has any reach at all.

### The paired design

Built by hand, not mined out of the sweep, so pairing is 100% by construction:
150 rule sets per run at primary 5m and 15m, bare base filters, each instantiated
twice - **control** and **control + `outside_news_blackout`** - identical signals,
identical exits, identical everything else. 450 pairs per contract for MES/MGC/MCL,
300 for MNQ.

### Results

| symbol | pairs | control trades | filtered trades | net vetoed | veto rate | control exp | filtered exp | paired dExp | t | p |
|---|---|---|---|---|---|---|---|---|---|---|
| MNQ | 300 | 1,736 | 1,728 | 8 | 0.46% | -0.0324R | -0.0284R | +0.0019R | +2.07 | 0.045 |
| MES | 450 | 4,180 | 4,174 | 6 | **0.14%** | -0.0418R | -0.0422R | +0.0004R | +0.55 | 0.58 |
| MGC | 450 | 2,154 | 2,133 | 21 | 0.97% | -0.0228R | -0.0227R | +0.0019R | +1.15 | 0.26 |
| **MCL** | **450** | **3,586** | **3,553** | **33** | **0.92%** | -0.0033R | +0.0013R | **+0.0037R** | **+0.89** | **0.38** |

The filter's reach on MCL is **6.6x** MES's, which is the whole point of the
symbol-scoped calendar and it works. Where the vetoes land confirms the
mechanism exactly:

| symbol | entry times of vetoed trades (top buckets) |
|---|---|
| MES | 14:00 x2, 14:15, 14:16, 15:35 - **FOMC** |
| MGC | 08:21 x6, 08:30 x3, 08:45 x3, 08:40, 08:35, 08:26 - **the 08:30 prints** |
| MCL | **10:30 x5, 10:36 x3, 10:40 x2, 10:45 x4**, 14:30 x3 - **EIA**, plus FOMC presser |

The population the filter actually removes (control trades with no counterpart in
the filtered arm - note this exceeds the net veto count, because skipping one
entry frees the strategy to take a different one later):

| symbol | removed | expectancy of removed | win rate | t | p |
|---|---|---|---|---|---|
| MNQ | 16 | -0.4917R | 31.2% | -2.21 | 0.043 |
| MES | 10 | +0.3467R | 60.0% | +0.75 | 0.47 |
| MGC | 74 | **+0.3628R** | 62.2% | **+2.53** | **0.014** |
| MCL | 81 | -0.0831R | 46.9% | -0.89 | 0.37 |

### Answer

**On MCL: no detectable effect.** +0.0037R per trade in the filter's favour with
t=+0.89, p=0.38 over 450 matched pairs; the 81 trades it removes have an
expectancy of -0.083R against the kept population's +0.001R, t=-0.89, p=0.37.
Removing 0.92% of trades cannot move a per-trade expectancy much whatever it does
to those trades, and it does not.

**This is a measurement of the generator, not of crude oil.** `synthetic_series`
places its one daily shock at a uniformly random minute of the session with
probability 0.06; 10:30 has no special status in it. The honest conclusion is
that the *plumbing* is now correct and measurable on MCL for the first time, and
the *effect* cannot be estimated until there are real bars around real EIA
prints. Four of the eight signed results above point the filter's way and four
against - exactly what noise looks like.

Two results worth flagging because they point the other way and I am not hiding
them:
- **MGC's removed trades were significantly better than average** (+0.363R,
  t=+2.53, p=0.014, n=74). Taken at face value the blackout is discarding good
  trades on gold. At n=74, one of eight comparisons, on a generator with no news
  effect, I read it as noise - but it is the opposite of the assumption the
  filter encodes.
- **MNQ's paired difference is nominally significant** (+0.0019R, p=0.045) on a
  veto rate of 0.46%. An effect of 0.002R is 2% of an average loss. This is what
  a 1-in-20 test looks like when you run four of them.

---

## What I withheld and why

Nothing in this file is published as live-eligible. Concretely withheld:

1. **Every per-contract family preference.** The within-symbol reproducibility
   control (mean rho = -0.15) says the ranking does not survive a change of price
   path on the same contract. Publishing "MCL is a breakout contract" would be
   publishing an eleven-way coin flip.

2. **MCL BREAKOUT (+0.123R, t=+2.83, n=190) and MCL MOMENTUM (+0.125R, t=+2.14,
   n=137)**, the only positive cells clearing both the 30-trade floor and t>2.
   Deflation: 897 strategies searched on MCL, `sqrt(2 ln 897)` = 3.69, so
   deflated t = 2.83 - 3.69 = **-0.86** and 2.14 - 3.69 = **-1.55**. Both are
   below zero before any out-of-sample test is run.

3. **Every 1h and 4h result**, pending Defect 1. MGC 4h shows +0.113R at t=+2.36
   on 73 trades with a 2.04 profit factor and a 1.22R max drawdown; 100% of those
   73 trades exited on a ten-minute time stop. That is not a 4-hour strategy.

4. **The three "credible" walk-forwards.** MES seed 2229 (OOS +0.234R, t=+2.75,
   efficiency 0.89, stability 0.53), MGC seed 3215 (OOS +0.081R, efficiency 0.36,
   stability 0.35) and MCL seed 1220 (OOS +0.109R, efficiency 0.61, stability
   0.65) all pass `WalkForwardResult.is_credible`. None replicates on the same
   contract's other two paths - MES 1229 and 3229 give OOS -0.045 and -0.117;
   MGC 1215 gives -0.314; MCL 2220 gives -0.286. Three of eleven passing is
   roughly what chance produces with a five-fold top-5 selection over ~400
   candidates, and `is_credible` has no deflation term in it.

5. **Any claim that the news filter helps or hurts**, on any contract. The reach
   measurement is solid and reportable; the effect measurement is not estimable
   on data with no news effect in it.

6. **Any cross-symbol parameter transfer.** I never fitted a parameter on one
   contract and applied it to another, and the generation seed, the exit
   catalogue and the filter sets were held identical across contracts precisely
   so that a difference could only come from the contract.

### Anti-overfitting checks: what I looked for and what I found

| check | how | finding |
|---|---|---|
| Look-ahead bias | Structural: entries fill at the *next* bar's open (`FillModel.entry_on_next_open`); signals read `SymbolFrame.snapshot(i)` whose higher-timeframe pointers reference the last *completed* bar. Behavioural: all four contracts pooled negative after costs (-0.020R to -0.117R, t up to -11.33). | **Clean.** A look-ahead leak on a near-martingale shows up as a positive pooled result; there isn't one. |
| Future-data leakage via the calendar | `project_events` is rule-based and projects identically backwards and forwards; I confirmed the vetoed entry times land on the rules' own clock (10:30 EIA, 08:30 BLS, 14:00 FOMC). | **Clean.** |
| Repainting indicators | Used `SymbolFrame`'s precomputed columns only; the cross-timeframe alignment array is built once, forward, and returns -1 before a timeframe's first close. | **Clean for the columns used.** I did not re-audit `support_resistance`; a prior seat's commit message references a look-ahead leak fixed in that cache. |
| Data-mining bias | 504-1,018 strategies per symbol; deflation applied with `sqrt(2 ln n)` = 3.53-3.72. | **Fatal to everything.** Best observed OOS t is +0.28 (MES); deflated expectancy 0.000R on all four contracts. |
| Insufficient sample | `MIN_TRADES_FOR_RANK = 30` enforced; 7 of the 48 populated family cells fell below it and are excluded from all rankings. | Enforced. Several cells I would have liked to report (MES LIQUIDITY n=3, MGC TREND n=21, MCL PULLBACK n=12) are excluded. |
| Parameter sensitivity | Not swept directly - the combinator's exit catalogue is four to six *shapes*, not a grid. But the across-seed instability above is the same disease with a bigger lever: the *rule set* is more sensitive than any parameter. | **Failed in the sense that matters:** the selection is unstable under a change of price path. |
| Unrealistic fills | Stop-before-target on same-bar ambiguity; gaps fill at the open, worse than the stop; entry slippage 0.5 ticks base, stop exits 1.5, plus a volatility term and a thin-book term. | Pessimistic side taken throughout. |
| Understated costs | Commission + exchange fee charged as dollars per round turn ($1.44 on micros); measured fee drag 0.018-0.045R per trade; all-in with slippage 2.4-7.6% of R depending on contract. | Reported per contract; MCL's is the binding one. |
| Survivorship bias | Not applicable - one synthetic continuous series per contract, no universe selection, no delisting. | N/A, and stated rather than silently assumed. |
| Selection stability | Walk-forward Jaccard overlap between consecutive folds: 0.27-0.73, mean 0.50. | Superficially fine, but the selected strategies' OOS expectancy is negative in 7 of 11 runs, so stable selection of nothing. |
| Same-path confound | Explicit: distinct seed per (symbol, base) with 1,000-spacing; verified \|rho\| <= 0.019 between all symbol pairs and <= 0.012 between base seeds. | **Caught before it was made.** See `## How I kept the price paths independent`. |

---

## Measurements

### Walk-forward (anchored, 5 folds, expanding train from 30% of bars)

Selection by `walkforward.robust_score(min_trades=20)` on the training segment
only, evaluation on the untouched next segment, top 5 per fold, with a floor of 8
training trades before a strategy is scored at all.

| symbol | seed | IS exp | OOS exp | OOS n | efficiency | stability | OOS t | `is_credible` |
|---|---|---|---|---|---|---|---|---|
| MNQ | 1236 | +0.247 | -0.047 | 327 | -0.19 | 0.73 | -0.86 | no |
| MNQ | 2236 | +0.061 | -0.132 | 316 | -2.18 | 0.45 | -3.03 | no |
| MES | 1229 | +0.154 | -0.045 | 124 | -0.29 | 0.43 | -0.67 | no |
| MES | 2229 | +0.262 | +0.234 | 114 | 0.89 | 0.53 | +2.75 | **yes** |
| MES | 3229 | +0.093 | -0.117 | 140 | -1.26 | 0.39 | -1.70 | no |
| MGC | 1215 | +0.102 | -0.314 | 85 | -3.09 | 0.27 | -3.00 | no |
| MGC | 2215 | +0.180 | +0.028 | 193 | 0.16 | 0.51 | +0.66 | no |
| MGC | 3215 | +0.222 | +0.081 | 175 | 0.36 | 0.35 | +1.64 | **yes** |
| MCL | 1220 | +0.179 | +0.109 | 98 | 0.61 | 0.65 | +1.13 | **yes** |
| MCL | 2220 | +0.246 | -0.286 | 76 | -1.16 | 0.45 | -3.03 | no |
| MCL | 3220 | +0.282 | +0.084 | 142 | 0.30 | 0.68 | +0.98 | no |

In-sample expectancy is positive in 11 of 11 runs (+0.061 to +0.282R) and
out-of-sample in 5 of 11. That gap *is* the data-mining bias, made visible.

Combined out-of-sample per contract:

| | n | win | expectancy | t | p | PF | max DD | avg DD | max cons L | avg duration | MAE | MFE |
|---|---|---|---|---|---|---|---|---|---|---|---|---|
| MNQ | 643 | 41.4% | -0.0887R | -2.53 | 0.012 | 0.77 | 76.8R | 25.3R | 9 | 49.6 min | 0.64R | 0.79R |
| MES | 378 | 48.9% | +0.0122R | +0.28 | 0.78 | 1.04 | 18.8R | 7.3R | 7 | 52.7 min | 0.57R | 0.77R |
| MGC | 453 | 46.1% | -0.0156R | -0.47 | 0.64 | 0.95 | 27.5R | 15.7R | 8 | 44.1 min | 0.51R | 0.53R |
| MCL | 316 | 47.5% | +0.0027R | +0.05 | 0.96 | 1.01 | 31.6R | 13.8R | 13 | 36.1 min | 0.65R | 0.93R |

**Methodological note, and its validation.** These folds are computed by
partitioning the trades of one continuous backtest by entry-bar index, using the
repo's own `anchored_windows(n_bars, 5, 0.3)` and `robust_score`, rather than by
re-running `BacktestEngine` inside each window. The two can differ at window
boundaries: the engine restarts position state at each window edge, the partition
assigns a boundary-spanning trade to the window its entry falls in.

I cross-checked by running the repo's `walk_forward()` directly against my
partition implementation on the same frame (MCL seed 1220, 60 days, 161
strategies, 5 anchored folds, top 5, floor 5 training trades):

| | folds | IS expectancy | OOS expectancy | OOS n | efficiency | selection stability |
|---|---|---|---|---|---|---|
| `walk_forward()` | 5 | +0.4105 | -0.0487 | 6 | -0.119 | 0.714 |
| trade partition | 5 | +0.4105 | -0.0487 | 6 | -0.119 | 0.476 |

Per-fold selection overlap: **1.00 in all five folds** - the two methods choose
the identical strategies and produce identical in-sample and out-of-sample
statistics. The one field that differs is `selection_stability`, and the cause is
a definable choice rather than an error: `walk_forward()` only records a fold in
its stability series when that fold selected something, whereas my version
records an empty selection as zero overlap. Mine is the more pessimistic reading.
Everything else is bit-identical, so the fold table above is a faithful
reproduction of `walk_forward()` at a fraction of the compute.

### Monte Carlo risk of ruin, $50,000 account

`AccountConfig` defaults: $50,000 start, $5,000 trailing failure threshold.
Resampled from each contract's combined out-of-sample R series, 250-trade
horizon, 4,000 paths, both i.i.d. and 10-trade block bootstrap.

| symbol | $/trade | mode | P(ruin) | p05 equity | median equity | 95th-pct DD | P(profit) |
|---|---|---|---|---|---|---|---|
| MNQ | 150 | iid / block | 0.336 / 0.373 | $44,950 | $46,621 | 48.5R | 0.057 |
| MNQ | 250 | iid / block | **0.789 / 0.813** | $44,842 | $45,703 | 48.5R | 0.057 |
| MES | 150 | iid / block | 0.012 / 0.011 | $47,243 | $50,476 | 26.1R | 0.594 |
| MES | 250 | iid / block | 0.165 / 0.170 | $45,432 | $50,740 | 26.1R | 0.594 |
| MGC | 150 | iid / block | 0.010 / 0.035 | $46,578 | $49,407 | 27.3R | 0.364 |
| MGC | 250 | iid / block | 0.204 / 0.278 | $45,083 | $48,990 | 27.3R | 0.364 |
| MCL | 150 | iid / block | 0.041 / 0.118 | $46,377 | $50,066 | 31.9R | 0.508 |
| MCL | 250 | iid / block | 0.327 / **0.453** | $45,106 | $49,892 | 31.9R | 0.469 |

Two things to take from this. First, **the block bootstrap is uniformly worse
than i.i.d.** - MCL at $250 goes from 33% to 45% risk of ruin - because losing
trades cluster, which is precisely the risk a trailing $5,000 threshold is
exposed to. Any ruin number quoted from i.i.d. resampling alone is optimistic.
Second, at `AccountConfig`'s own ceiling (`max_dollar_risk = $500`, and a
0.75%-of-equity cap of $375) the ruin probabilities above would roughly double
again. **$250 per trade against a $5,000 trailing threshold is not survivable on
any of these four out-of-sample series**, and the two that look tolerable at
$150 (MES 1.2%, MGC 1.0%) are the ones whose OOS expectancy is statistically
zero.

### Deflation arithmetic, shown

`deflated_expectancy` subtracts `sqrt(2 ln trials)` from the observed
t-statistic, the expected maximum of *n* standard normals:

| symbol | trials searched | `sqrt(2 ln n)` | observed OOS t | deflated t | deflated expectancy |
|---|---|---|---|---|---|
| MNQ | 504 | 3.53 | -2.53 | -6.05 | 0.000R |
| MES | 1,018 | 3.72 | +0.28 | -3.44 | 0.000R |
| MGC | 742 | 3.64 | -0.47 | -4.10 | 0.000R |
| MCL | 897 | 3.69 | +0.05 | -3.64 | 0.000R |

Worked example, MCL: 897 distinct strategy ids were evaluated.
`sqrt(2 * ln 897) = sqrt(2 * 6.799) = 3.688`. The combined out-of-sample series
has expectancy +0.00275R with std 0.9693R over 316 trades, so t = +0.050.
Deflated t = 0.050 - 3.688 = **-3.638**, which is <= 0, so `deflated_expectancy`
returns 0.000R by construction. To clear the bar MCL's OOS series would have
needed t > 3.688, i.e. an expectancy above
`3.688 x 0.9693 / sqrt(316)` = **+0.2011R per trade**. It measured +0.0028R -
short by a factor of 73.

The same arithmetic for the others: MNQ needed +0.1239R and measured -0.0887R;
MES needed +0.1597R and measured +0.0122R; MGC needed +0.1215R and measured
-0.0156R.

### Run inventory and reproduction

| symbol | paths | seeds | bars/path | strategies/path | trades recorded |
|---|---|---|---|---|---|
| MNQ (reference arm only) | 2 | 1236, 2236 | 82,800 | ~1,215 | 10,904 |
| MES | 3 | 1229, 2229, 3229 | 82,800 | ~1,215 | 28,891 |
| MGC | 3 | 1215, 2215, 3215 | 82,800 | ~1,215 | 18,515 |
| MCL | 3 | 1220, 2220, 3220 | 82,800 | ~1,215 | 18,123 |

Per path: 406 universe strategies (13 templates x 5 primary timeframes x the
combinator's own filter variants), 315 timeframe-group arms, 300 news-pair arms,
140 confirm-map arms; all four sets backtested in a single pass over the bars.

Scripts (scratch, not part of the repo):
`/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad/xsym/`
- `worker.py` - one independent path end to end
- `probes.py` - the two defect proofs
- `seedcheck.py` - the path-correlation measurements
- `newscal.py` - the per-symbol in-session event counts
- `wfval.py` - `walk_forward()` vs trade-partition cross-check
- `analyze1.py` / `analyze2.py` / `analyze3.py` - per-symbol, transfer+timeframe+news, walk-forward+MC+deflation

### Test suite and provenance

**No source file was modified by this seat.** The only file I wrote in the
repository is this one; everything else lives in the scratch directory above.

The tree moved underneath this work, so the provenance matters and is recorded by
file mtime rather than asserted:

| event | time (UTC, 2026-09-22) |
|---|---|
| baseline given to this seat | `5b3ae0c` |
| `futures_agents/strategies/profiles.py` appears, `generate_combinations` gains its `groups_for` hook | 18:16 |
| my 11 worker processes launch (wave 1 / wave 2) | 18:24 / ~18:40 |
| **all 11 run outputs written** | **18:44 - 18:53** |
| `python -m pytest tests/ -q` | 18:38 -> **687 passed, 0 failed** |
| sibling edits to `combinator.py`, `metrics.py`, `library.py` | 19:03, 19:04, 19:08 |
| sibling commits `8be8755`, `0df691f` land on the branch | after my runs |
| `python -m pytest tests/ -q` (re-run at hand-off) | 19:10 -> **695 passed, 2 failed** |

Every measurement in this file was produced by a process that imported the tree
as it stood between 18:16 and 18:40 - that is, `5b3ae0c` plus `profiles.py` and
its `groups_for` hook, which I neutralised by passing the full 13-template
`groups=` list on every generation call. None of the 19:03-19:08 edits to
`combinator.py`, `metrics.py` or `library.py` were in any of my worker processes.

**The two failures at hand-off are not mine and post-date my runs:**

- `tests/test_symbol_profiles.py::test_annualisation_uses_calendar_days_not_active_days`
  fails with `TypeError: Trade.__init__() got an unexpected keyword argument
  'r_multiple'` - a new test constructing `Trade` with a field the dataclass does
  not have. It arrived with the uncommitted `metrics.py` edit at 19:04.
- `tests/test_strategy_library.py::test_no_signal_condition_fires_on_almost_every_bar`
  fails on `price_above_ema200` (0.9204) and `price_above_ema50` (0.9457)
  exceeding a new 90% ceiling. It arrived with the uncommitted `library.py`
  deadband edit at 19:08.

That `library.py` edit adds a 0.1-ATR deadband to `ema_fast_above_slow`,
`price_above_ema50`, `price_above_ema200`, `macd_directional` and two others.
Those conditions appear in a large share of the TREND, PULLBACK and MOMENTUM rule
sets I tested, so **re-running my scripts against the current tree will not
reproduce my numbers**, and should not be expected to. Whoever repeats this work
should re-run the whole sweep rather than splice new numbers into the old tables.

### MNQ's place in this file

MNQ single-timeframe work belongs to four sibling seats and I did not duplicate
it. MNQ appears here on two paths only, as the reference arm the transfer
question needs - you cannot ask "does an MNQ strategy work on MES" without
measuring MNQ on the same universe, the same seeds' spacing and the same
generation call. Its single-timeframe numbers should be taken from whichever seat
owns that lane, not from here.
