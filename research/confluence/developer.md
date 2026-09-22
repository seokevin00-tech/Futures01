# Developer: adversarial correctness audit of the confluence layer (f82bd35)

Audited at `f82bd35`; fixes measured again at `5583805`, which is where the
desk lead landed them. Every number below is reproducible from the scripts
named against it.

## Verdict

The new machinery holds. The specific thing the commit set out to get right -
that a zone's touch count and invalidation, a Fibonacci leg, a prior-session
profile, an open-interest delta and a news column all answer for the bar asking
rather than for the end of the series - survives the hardest test I could put
to it: 1,824 (prefix, index) snapshot pairs across six cut points, comparing a
total serialisation of every field on every timeframe, produced **zero**
differing leaves, and an exhaustive sweep of 16,560 bars handed 53,366 zones to
snapshots with **zero** masking violations. The exception guard is hiding
nothing today either: 633,056 raw condition calls across five data shapes and
thirteen poisoned snapshots, plus a real 3,960-strategy backtest over 16,560
bars, produced **zero** swallowed exceptions. What I did find was four real
defects, and the most serious of them is one level out from the new work rather
than in it: the S/R level cache made the frame's output a function of the order
bars were asked in, and leaked up to four bars of future swing confirmations to
anything that did not walk strictly forward - 3,803 of 4,000 snapshots differed
between an ascending and a descending walk of the same frame. The new
`fib_sr_confluence` reads that cache, which is how I got to it. Within the new
work itself the defects are smaller but real: the news blackout window was
inverted (it left the ten minutes into an 08:30 print open and blocked for ten
minutes after instead of five), and `fib_sr_confluence` mirrored its whole
retracement set on up legs. Cost regressed 13.2% end-to-end; I recovered 7.6
points of it in `active_zones` and the rest is genuine new analytics.

## Confirmed defects

### 1. The S/R level cache leaked the future and made results order-dependent

**Severity: look-ahead, plus a determinism violation.** Pre-existing (commit
`0655916`), but it reaches the new `fib_sr_confluence` along with
`pullback_to_support` and every agent that reads `snapshot.to_dict()`.

`TimeframeFrame.sr_levels` keyed its cache on `index // 5` but computed the
levels from `index`. Whichever bar in a bucket asked first won.

Reproduction (`srleak.py`): build one `SymbolFrame`, walk `sr_levels(i)` for
every 5-minute bar from 200 to the end, and count levels whose constituent
swing sits *after* the asking bar.

| query order | S/R levels built from a swing in the asking bar's future |
|---|---|
| ascending | 0 |
| descending | **102** |

Example from the run: bar 3305 was handed a level built from a swing pivot at
bar 3306.

Reproduction (`order.py` / `order_before.py`): two frames over the same
16,560-bar series, one walked ascending over indices 2000-5999 and one walked
descending; compare the full snapshot serialisation.

| | snapshots differing between the two walks (of 4,000) |
|---|---|
| before | **3,803** |
| after | 0 |

**Changed.** `futures_agents/features.py` - the bucket is evaluated at its own
first bar (`at = bucket * self.SR_BUCKET`), not at the caller's index. For a
step-1 forward walk this is byte-identical to the previous behaviour; for every
other access pattern it is now stale-but-safe rather than leaky, and it is a
function of the data alone. No throughput cost (0.11 -> 0.12 us/bar).

**Tests.** `tests/test_features.py::test_sr_levels_do_not_depend_on_the_order_bars_are_asked_in`
and `::test_sr_levels_never_include_a_swing_the_bar_has_not_seen`. Both fail
against the pre-fix code.

### 2. The news blackout window was inverted

**Severity: the filter blocked the wrong half of the event.**

`SymbolFrame._build_news_proximity` tested
`-after <= (ts - event) <= before`. `ts - event` is *negative* before the
event, so with `before=10, after=5` the window was 5 minutes ahead of the
release through 10 minutes past it - the mirror image of what
`AccountConfig.news_blackout_before_min/after_min` (10/5) asks for.
`econ_calendar.event_proximity` had the identical inversion.

Reproduction (`news_shift.py`): list the bar offsets flagged
`in_news_blackout` around each high-impact release in the series.

| | blackout offsets around NFP and CPI |
|---|---|
| before | **-5 .. +10 minutes** |
| after | -10 .. +5 minutes |

Both are 16 bars, which is why a bar count alone would not have caught it. The
practical consequence: a strategy could initiate 6-10 minutes ahead of the
08:30 print - exactly the window the filter exists to close.

**Changed.** `futures_agents/features.py` - window is now
`[-before, +after]`, and the bounds are read off `AccountConfig` through new
module constants `NEWS_BLACKOUT_BEFORE_MIN` / `NEWS_BLACKOUT_AFTER_MIN` rather
than restated. The old code read them through
`getattr(getattr(self, "_account", None), "news_blackout_before_min", 10)`;
`SymbolFrame` has no `_account` attribute, so that expression was permanently
`10` and only looked configurable.

**Tests.** `tests/test_strategy_library.py::test_blackout_is_the_window_the_risk_config_asks_for`
(fails pre-fix: 15 bars inside the configured window were not blacked out),
`::test_blackout_window_matches_the_account_config`,
`::test_event_proximity_blacks_out_before_the_release`.

### 3. `event_proximity` clipped its own blackout and reported a quiet calendar

Same function, two further defects, both the class the commit already fixed for
`_build_news_proximity`:

* its lookback was `after_min + 1 = 6` minutes, so the (wrongly `before_min`
  = 10 minute) post-event blackout was silently truncated at +6;
* its forward horizon was 72 hours. High-impact US releases are monthly, so
  between prints the function returned `minutes_to_next = inf`, which a caller
  reads as "nothing scheduled" rather than as a horizon artefact.

Reproduction (`news_rates.py`), probing around NFP 2026-03-06 08:30:

| offset | before: `(minutes_to, blackout)` | after |
|---|---|---|
| -11m | (11.0, False) | (11.0, False) |
| -10m | (10.0, **False**) | (10.0, True) |
| -5m | (5.0, True) | (5.0, True) |
| +5m | (**inf**, True) | (10075.0, True) |
| +6m | (inf, **True**) | (10074.0, False) |
| +20m | (inf, False) | (10060.0, False) |

**Changed.** `futures_agents/econ_calendar.py` - `PROJECTION_DAYS = 45`
replaces the 72-hour forward horizon; the lookback stays `after_min + 1`, which
is now correct because the post-event half of the window is `after_min`.

**Test.** `::test_event_proximity_sees_the_next_release_between_monthly_prints`.

### 4. `fib_sr_confluence` mirrored its whole retracement set on up legs

**Severity: the condition tested a level nobody draws and reported the wrong
one.** New in `f82bd35`.

```python
level = lo + ratio * span          # for every ratio, whichever way the leg ran
```

A retracement is measured from the *end* of the leg back towards its start -
which is what `TFSnapshot.fib_zone` does correctly two files away. For an UP
leg the code therefore tested `{lo+0.382s, lo+0.5s, lo+0.618s, lo+0.786s}` =
`{hi-0.618s, hi-0.5s, hi-0.382s, hi-0.214s}`. The 0.786 retracement was never
tested; the 0.214 level, which is not a Fibonacci retracement, was tested in
its place; and the reason string named the wrong ratio on three of four.

Reproduction (`logic.py`): 2,936 confirmed legs, 1,386 of them UP.

| | mismatched (level, labelled ratio) pairs on UP legs |
|---|---|
| before | **4,158 of 5,544 (75.0%)** |
| after | 0 |

**Changed.** `futures_agents/strategies/library.py` -
`level = hi - ratio * span if direction == "UP" else lo + ratio * span`.
Firing rate moved 32.952% -> 33.209%, so this is not a cosmetic relabel: it
tests a different set of prices.

**Test.** `::test_fib_sr_confluence_measures_retracements_from_the_end_of_the_leg`
(fails pre-fix: "reported the 0.786 retracement of a UP leg at 21800.343, but
that level is 21757.157").

### 5. A negative index meant "the end of the series" in `active_zones` / `active_fvgs`

**Severity: latent look-ahead.** Not reachable through `snapshot()`, which
guards `index < 0` - but both accessors are public and both are in the file
whose entire job is not showing the future.

`ptr[min(index, n - 1)]` with `index = -1` is Python's negative indexing: it
returns the *last* row of the pointer table. Reproduction, on a 390-bar frame:
`active_fvgs(-1)` returned 6 gaps, the newest at index 382.

**Changed.** `min(max(0, index), ...)` in both. An out-of-range bar now sees
nothing. **Test.** `::test_active_zones_and_fvgs_treat_a_negative_index_as_the_start`
(fails pre-fix).

### 6. `active_zones` re-walked the entire zone list on every bar

**Severity: cost.** `start = max(0, k - ZONE_SCAN_WINDOW)` used a constant
documented as a *bar* age as a *zone count*, so on 1-minute data it sliced all
201 zones every bar and threw ~195 of them away on the age check.

| | `active_zones` on 1m, 13,800 bars |
|---|---|
| before | **8.51 us/bar** |
| after | 2.19-2.41 us/bar (-72%) |

**Changed.** `_build_zone_pointers` now also builds a monotonically advancing
lower pointer `_zone_lo_ptr`, and `active_zones` skips `SDZone.as_of` entirely
when nothing about the zone postdates the asking bar. Handing out the stored
object is only safe if nobody can mutate it, so `SDZone` is now
`@dataclass(frozen=True)`.

Output equivalence was verified explicitly against the old implementation at
**21,260 (timeframe, index) points: 0 mismatches** (`zones_equiv.py`), and the
sharing is safe by measurement as well as by construction - two identical
sweeps on the same frame and one on a freshly built frame produced the same
digest `533134acc31e3bbc` (`determinism.py`).

### 7. The guard could not tell a degenerate bar from broken code

Not a defect in any condition - see the Measurements section, the guard is
currently swallowing nothing - but a defect in the instrumentation, and one
that has already cost this work once: fifteen conditions called
`fmt_price(price, snap.spec)`, raised on every evaluation, and were recorded as
confluences with a 0% trigger rate.

The narrowest change that keeps a 4,000-strategy sweep robust is not to stop
swallowing. It is to stop swallowing *silently*. One raise in a hundred
thousand is a degenerate bar; a raise on every evaluation is a defect, and the
difference is a count.

**Changed.** `futures_agents/strategies/base.py` - a module-level
`CONDITION_ERRORS: Dict[(condition, exception type), int]` with
`condition_errors()` and `reset_condition_errors()`. Nothing reads it to make a
decision, so it cannot change a result; it is observational only.

**Tests.** `::test_the_guard_counts_what_it_swallows` (a deliberately broken
condition: `evaluate` still returns "no signal", and the tally records
`{("deliberately_broken", "TypeError"): 2}` after two calls) and
`::test_no_condition_in_the_library_raises_through_the_guard`, which runs all
73 conditions through `evaluate` on all four timeframes and asserts the tally
is empty afterwards. That second test is the one that would have caught the
`fmt_price` bug *through* the guard rather than around it.

## Unconfirmed suspicions

Everything in this section is **unproven**. I could not demonstrate that any of
it changes a result.

**`oi_price_confirmation` pairs OI change with a proxy for direction, not with
direction.** The docstring is honest - it says it reads price direction from
the bar's position in its 20-bar range - but range position and the 20-bar
close-to-close change are not the same measurement, and the classical OI thesis
("rising OI with rising price is new longs") is about the change. Measured
(`logic.py`): when the condition makes a directional call, range position
agrees with `sign(close[i] - close[i-20])` 92.6% of the time on 1m (11,636 of
12,567) and 92.2% on 5m (2,320 of 2,515). On the remaining ~7.5% it emits the
opposite direction to what its own thesis implies. It also declines to call on
3,929 1m bars where price did move. `roc` is already imported in `features.py`,
so the alternative costs one column - but which reading of "the price move" is
right is a modelling choice, not a bug, and I did not want to change the
meaning of a variable the desk is about to measure. **Unproven that the 7.5%
matters.**

**The `last_base_end` dedupe in `supply_demand_zones` never fires.** The brief
asked whether it silently drops legitimate zones. Measured over 21,260 bar/
timeframe pairs: **0 candidates dropped**, on any timeframe. The reason looks
structural rather than accidental - the base walk breaks at any bar with
`range > 0.8 * avg_range`, and a previous departure bar has `range >= 2.0 *
avg_range`, so `base_start` is always greater than the previous `base_end` -
but I have only argued that, not proved it for every possible `avg_range`
trajectory. So: the guard drops nothing, the comment above it ("otherwise one
impulse paints five overlapping zones") describes a protection that never
engages, and I left it alone. **Unproven that it is unreachable in principle.**

**`TimeframeFrame.zones`, `.fvgs` and `.swings` are public, unmasked
attributes.** `active_zones` is the only masked accessor, and it is the only
thing `snapshot()` uses - I grepped, nothing else in the tree touches
`.zones`. But the invariant lives in a convention, not in the type. Making the
raw lists private (`_zones`) would cost nothing. **Unproven that anything
reaches them.**

**`active_fvgs` is now the largest per-bar cost in the snapshot path** at
8.0-9.4 us/bar (vs 2.2-2.4 for `active_zones` after the fix), and its
`FVG_SCAN_WINDOW = 60` is a count of *gaps* where `ZONE_SCAN_WINDOW` is an age
in *bars*. That asymmetry means a 61st-newest unfilled gap is dropped on
recency of discovery rather than on age. Pre-existing, and plausibly deliberate
- the docstring says "how many recently-visible gaps to consider". **Unproven
that it drops a gap anyone would trade.**

**`econ_calendar._rule_dates` has a dead bounds check.**
`pick = days[idx - 1] if idx > 0 else days[idx]` is evaluated *before*
`pick if 0 <= abs(idx) - 1 < len(days) else days[-1]`, so the guard can never
save an out-of-range index - `days[idx-1]` would already have raised. With the
current rules the largest index is 18 (GDP) and the fewest business days a
month can have is 19, so it is not reachable today. **Unproven that no rule
addition makes it reachable** - and note this file was being rewritten by the
news seat while I audited, so the rule set has since changed.

**The test suite's own determinism wobbles with the calendar date.**
`synthetic_series` defaults `end_date=date.today()`, and
`tests/test_strategy_library.py`'s module-scoped `oi_series` fixture uses that
default. The bar *values* are seeded and stable; the timestamps are not, so
which macro releases land inside the fixture changes day to day. That is the
suite's determinism, not the core's, and no test currently depends on a
specific release - but a news test that did would be a flake with a 30-day
period. I did not change it.

**Concurrent edits.** `futures_agents/econ_calendar.py` was being rewritten by
the news_macro seat while I was measuring - at one point it was transiently
unimportable (`is_market_holiday` removed from the import while
`_business_days` still called it). My edits to `event_proximity` survived
alongside theirs and the suite is green, but the event set my news numbers were
measured against has since changed (symbol-scoped rules, stored FOMC dates,
federal rather than exchange holidays). The blackout-window *shape* findings
above are independent of which events exist.

## Measurements

Tree state: `5583805`. All scripts in
`/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad/devaudit/`.

### Test suite

`python -m pytest tests/ -q` -> **672 passed**, 8.78s. (655 at `f82bd35`; I
added 10, other seats added the rest.) No test was weakened or removed.

### Prefix stability (`prefix.py`)

Series: 16,560 1-minute MNQ bars with synthetic open interest, timeframes
(1, 5, 15, 60). Six prefix cuts at 5796 / 8280 / 10267 / 12420 / 14572 / 16063
bars; every 37th index in each prefix compared against the same index on the
full series, using a total serialisation (every `values` key, structure trend,
swings and their indices, S/R levels, divergence, volume regime, active FVGs
including `filled_index`, prior profile POC/VAH/VAL/HVN/LVN, open interest,
imbalance and recency, every `sd_zone` field including `touch_indices`,
`invalidated_index`, `touches`, `fresh`, `proximal`, `distal`, `swing_leg()`,
both `fib_zone()` bands, regime, session levels, opening range, and all three
news columns).

```
compared 1824 (n, i) snapshot pairs
mismatched snapshots: 0
```

### Zone masking (`zone_sweep.py`)

```
snapshots swept:                              16,560
zones handed to a snapshot:                   53,366
masking violations (future touch / invalidation / zone index > bar):   0
stored objects handed out while carrying future state:                 0
```

### Query-order independence (`order.py`, `srleak.py`)

| probe | before fix | after fix |
|---|---|---|
| snapshots differing asc vs desc (of 4,000) | 3,803 | 0 |
| S/R levels citing a future swing, descending walk | 102 | 0 |
| S/R levels citing a future swing, ascending walk | 0 | 0 |

### Direct leak proofs (`deep.py`)

```
prior_session_profile: 21,252 bar/tf pairs; None on 1,771 (first session);
                       mismatches vs the prior day rebuilt from scratch: 0
fib legs:              20,833 legs; endpoints not fractal-confirmed at the
                       asking bar (needs swing_right=3 bars after the pivot): 0
oi_change:             21,172 values; any not equal to oi[i] - oi[i-20]: 0
```

### Exception-swallow audit

Raw calls to `cond.fn`, outside `Condition.evaluate`'s guard.

| harness | evaluations | exceptions |
|---|---|---|
| `rates.py` - 5 data shapes (10-day with OI, 10-day without OI, single session, zero-range/zero-volume bars, 60-bar stub) x 4 timeframes x 73 conditions | 510,416 | **0** |
| `fuzz.py` - 13 poisoned snapshots (atr=0, atr=None, hh20==ll20, oi=0, oi_change=None, NaN in every float column, inf close, flat profile, empty profile, zero-height zone, zero-width S/R, swing hi==lo, junk imbalance strings, tick_size=0) | 122,640 | **0** |
| `sweep_errors.py` - 3,960 generated strategies x 16,560 bars through `BacktestEngine.run_many`, tallied via `condition_errors()` | 7,345 signals / 7,345 trades | **0 swallowed** |

### Condition firing rates

73 conditions, evaluated on the five shapes above, 6,992 evaluations each.
Every condition fires at least once. Extremes:

| condition | group | rate |
|---|---|---|
| post_news_window | news | 0.343% (24/6992) |
| lvn_rejection | profile | 0.400% (28) |
| fresh_zone_approach | supplydemand | 0.658% (46) |
| value_area_edge | profile | 0.686% (48) |
| zone_touch | supplydemand | 1.230% (86) |
| fib_extension_reached | fibonacci | 1.273% (89) |
| imbalance_bar | imbalance | 3.733% (261) |
| fib_shallow_retrace | fibonacci | 5.478% (383) |
| fib_golden_pocket | fibonacci | 6.364% (445) |
| oi_price_confirmation | openinterest | 12.071% (844) |
| oi_expanding | openinterest | 19.122% (1337) |
| imbalance_pullback | imbalance | 23.012% (1609) |
| value_area_breakout | profile | 25.715% (1798) |
| fib_sr_confluence | fibonacci | 32.952% -> **33.209%** after fix 4 (2322) |
| away_from_hvn | profile | 36.070% (2522) |
| away_from_zone | supplydemand | 64.931% (4540) |
| no_recent_imbalance | imbalance | 90.232% (6309) |
| no_imminent_release | news | 99.771% (6976) |
| outside_news_blackout | news | 99.886% (6984) |

Over all 16,560 bars of the reference series rather than the sampled subset:
`in_news_blackout` 32 bars (0.193%), `minutes_to_high_impact < 30` 60 bars
(0.362%), post-news 5-60m window 112 bars (0.676%), `minutes_to_high_impact ==
inf` 0 bars. The news dimension is therefore very nearly a constant on a
10-day RTH-heavy sample - which is the same observation the sweep seat landed
in `5583805` from a different direction.

### Condition-group usage in a real sweep (`sweep_errors.py`)

3,960 strategies from `generate_strategies("MNQ", [1,5,15,60], max_total=4000,
seed=1)`. All six new groups are reachable:

```
volatility 4011  volume 3883  trend 2429  structure 2367  momentum 2194
news 1766  orderflow 1645  vwap 1589  profile 1239  time 988  liquidity 912
meanreversion 912  multitimeframe 742  fibonacci 718  regime 706
supplydemand 671  imbalance 640  openinterest 210
```

### Supply/demand zone detection (`zonelogic.py`, `logic.py`, `tf60.py`)

```
departure bar opens inside its own base:   1m 244/244, 5m 28/28, 15m 6/6  (100%)
last_base_end dedupe fired:                0 times on any timeframe
6-zone cap in active_zones bound:          0 bars (max live at once: 1m 6, 5m 5, 15m 2)
zone population (10 days):   1m 244 (106 SUPPLY / 138 DEMAND), 5m 28, 15m 6, 60m 0
departure-filter breakdown, 1m:  not_a_departure 15286, mid_range_close 641,
                                 no_base 340, zero_range 28, zone 244
zone density by timeframe:   10d  1m 201/13800   5m 24/2760   15m 4/920    60m 0/230
                             60d  1m 1175/82800  5m 145/16560 15m 43/5520  60m 5/1380
                            120d  1m 2292/165600 5m 315/33120 15m 69/11040 60m 8/2760
```

The last row is the one worth a second look: a 60-minute supply/demand
strategy has eight zones to work with over a 120-day run. That is a
measurement, not a defect.

### `oi_price_confirmation` pairing (`logic.py`)

| timeframe | bars where the condition made a call | range position agrees with `sign(close[i]-close[i-20])` | disagrees | no call although price moved |
|---|---|---|---|---|
| 1m | 12,567 | 11,636 (92.6%) | 931 (7.4%) | 3,929 |
| 5m | 2,515 | 2,320 (92.2%) | 195 (7.8%) | 775 |

### Throughput

13,800 base bars, timeframes (1, 5, 15, 60), best of 3, same process
conditions, `bench.py` run against three worktrees.

| tree | frame build | snapshot walk | snapshots/s | bars/s end-to-end |
|---|---|---|---|---|
| `f82bd35~1` (before the confluence work) | 0.722 s | 2.478 s | 5,569.2 | **4,313.2** |
| `f82bd35` (as committed) | 0.794 s | 2.894 s | 4,769.1 | **3,742.3** (-13.2%) |
| `5583805` (after fix 6) | 0.775 s | 2.651 s | 5,205.1 | **4,028.2** (-6.6%) |

Attribution of the remaining 6.6%, measured rather than guessed:

*Build (+0.053 s):* `supply_demand_zones` ~31 ms across four timeframes
(23.9 ms on 1m alone), `detect_imbalances` ~41 ms (31.8 ms on 1m - the function
is old, calling it per timeframe is new), `_build_news_proximity` 31.5 ms once,
plus the zone pointers and `_oi_change`. Both zone and imbalance detectors
recompute a 20-bar `sum(b.range ...)` per bar where a rolling sum would do;
that is worth roughly 20 ms of an 800 ms build and I judged it not worth the
churn.

*Snapshot walk (+0.173 s over 13,800 bars = 12.5 us/bar):* per-bar accessor
costs, measured on a warmed frame:

| accessor | 1m | 5m | 15m | 60m |
|---|---|---|---|---|
| `active_zones` (new) | 2.41 us | 1.34 | 0.68 | 0.09 |
| `prior_session_profile` (new) | 0.23 us | 0.22 | 0.22 | 0.23 |
| `sr_levels` (pre-existing) | 0.12 us | 0.11 | 0.09 | 0.10 |
| `active_fvgs` (pre-existing) | 8.98 us | 8.15 | 8.06 | 4.36 |

The rest is irreducible without restructuring the snapshot: `TFSnapshot` gained
six fields and `FeatureSnapshot` three, `FEATURE_NAMES` gained two columns, and
all of that is paid four times per base bar.

### Determinism (`determinism.py`)

```
generate_strategies(seed=7) reproducible:      780 ids identical across two calls
sweep pass 1 digest:                           533134acc31e3bbc
sweep pass 2 digest (same frame):              533134acc31e3bbc
sweep digest on a freshly built frame:         533134acc31e3bbc
153 strategies with trades, 2,444 trades
```

The digest is over `(strategy_id, trade count, signals generated, sum of
net_r)` for all 780 strategies. Two passes on the same frame agreeing is the
check that sharing the stored `SDZone` object out of `active_zones` is safe.

### Files changed

- `futures_agents/features.py` - S/R cache bucket anchor; news blackout window
  and `NEWS_BLACKOUT_*` constants; `_zone_lo_ptr`; `active_zones` fast path;
  negative-index clamp in `active_zones` and `active_fvgs`.
- `futures_agents/econ_calendar.py` - `event_proximity` window inversion and
  `PROJECTION_DAYS`.
- `futures_agents/indicators/structure.py` - `SDZone` frozen.
- `futures_agents/strategies/library.py` - `fib_sr_confluence` leg direction.
- `futures_agents/strategies/base.py` - `CONDITION_ERRORS` tally.
- `tests/test_features.py` - 3 tests.
- `tests/test_strategy_library.py` - 7 tests.
