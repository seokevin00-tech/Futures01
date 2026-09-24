# Strategy study programme — findings

**Date:** 2026-09-24
**Scope:** 22 studies (13 strategy groups, 8 cross-cutting, 1 diagnostic), run by 24 agents
**Method:** matched A/B against a control arm from the same generated population over the same
bars; rank-sum tests; 912 comparisons, of which 9 rejected as trade-level pools
**Commit:** see `workspace/studies/out/*.json` for every study's full numbers
**Companion:** `workspace/studies/DEFECTS.md` — 26 defects and 3 merge hazards

---

## Bottom line

**Nothing in this library is tradeable, and the reason is not that we searched badly.**

Cut into genuinely disjoint periods, **72 strategies are positive in all three against a
permutation null of 70.1** — exactly chance. Excluding one cell it is 44 against 60.2, *below*
chance. Walk-forward agrees: out-of-sample expectancy is negative in 5 of 6 cells, and at MES
60m the top-ranked strategy changes in **6 of 6 folds**.

Best t-statistic anywhere in the programme: **3.92** against a free-search threshold of
**4.15**. Nothing clears it.

Three findings that looked strongest earlier in this project have been retracted by this
programme. Each was a measurement artefact, not a market fact:

| earlier claim | what it actually was |
|---|---|
| MES 1h TREND, +0.252R, 96.4% profitable | A **trade-floor artefact**. Real inside the 20–40 trade band only, and only on MES. Inverts at n≥40 (z=−1.93) and n≥60 (z=−4.78). corr(expectancy, n) = −0.272. |
| NQ 1h OPENING_RANGE positive in all three windows | **One observation, three times.** Windows are nested; both 90d ids sit inside the 274d set. And it is not about opening ranges: `opening_range_breakout` fires on **4 of 4,256** bars; of 47 floored OR strategies, **one** is an OR breakout. |
| Anchor/execution split raises reward for risk | **Never ran.** `execution_tf` is None in **8,521 of 8,521** scanned strategies. Rebuilt properly: pooled z = −0.00. |

---

## What survived programme-wide correction

912 comparisons, Bonferroni threshold |z| ≥ 4.03. **56 claims survive.** The ones worth
acting on:

### 1. Turn `exit_at_session_close` off — the largest single effect measured
Paired sign z = **+3.52**. At MES daily it is the difference between a 0-minute trade and a
15-day one. Median hold at 240m and daily is currently **0.0 minutes** — every 4h and daily
trade opens and force-closes inside one bar. The much larger "anchored targets beat R-multiple"
result (z = −5.87) **is this flag wearing another name**: hold it constant and target-kind falls
to z = −1.80 / +1.24, neither detectable.

### 2. One exit model, stable at every floor from 5 to 30 trades
`ATRx1 → 2/4R anchored to 1/2.5 ATR`. Mean within-entry rank 0.447 against a 0.556 null over
352 blocks; best or joint-best in **7 of 10 entry groups**. Three targets are worse than two
(z = −2.87, 453 pairs). No stable best stop width exists — the ordering reverses by timeframe.

### 3. Use the anchor/execution split ONLY at a 4-hour anchor with anchored targets
Pooled it does nothing (z = −0.00). It pays with anchored targets (z = +3.20, 328 pairs) and at
a 240m anchor (z = +4.07, 3 of 3 cells). It is **negative** with R-multiple targets (z = −2.85)
and at a daily anchor (z = −5.80). The mechanism is real and exactly self-cancelling: payoff
rises (z = +9.59) while win rate falls (z = −6.79).

### 4. Do not open intraday positions 15:00–16:00 ET
z = −4.43, median −0.617R, replicated across symbols. Note `power_hour` targets exactly that
window.

### 5. Do not fade extension on the bar spanning the cash open, at 60m
z = −10.64 pooled per-strategy (259 vs 451), replicating on NQ (−7.84), MNQ (−7.78), MES
(−5.60) and MGC (−5.39). It reverses at 15m, so it is a property of the hourly bar.

### 6. `volatility_normal` earns its place — but is symbol-specific
Paired within 2,571 rule sets: sign z = **+9.64**, costing 15–27% of trades. But it helps in 4
cells and **detectably hurts MGC 60m** (z = −6.30). Not a universal rule.

### Suggestive, not established
`fib_golden_pocket` beats `fib_shallow_retrace` **at 240m on all three symbols** (z = +7.14 /
+5.65 / +4.83) and loses at 60m on all three. Survives programme-wide Bonferroni; partly
OOS-robust; contradicted between shipped and counterfactual 60m populations. Deserves its own
study before anyone builds on it.

---

## Premises this programme refuted

**Confluence does not help.** Going from 2 signals to 4 cuts median trade count 35% (65 → 42,
matched z = +8.48) and does not move expectancy — the sign favours **two**. One filter beats
three or four on every statistic. If a number is wanted: **2 signals, 1 filter.**

**Multi-timeframe agreement does not help.** Requiring any alignment signal is detectably
*worse* than not requiring one (z = −4.09, 366 vs 1,151). Unanimity versus majority: no
detectable difference. `mtf_aligned` is the strongest negative condition in the library
(z = −2.53).

**Trend-following does not need a trending regime.** `regime_trending`: 3 cells positive, 3
negative, while halving trade count.

**Compression does not precede tradeable expansion.** Requiring `volatility_compressed` scores
up to z = **+12.20 in sample** and goes **significantly negative out of sample in 6 of 12
cells**. It also costs ~2/3 of all opportunity. The cleanest overfit in the programme.

**Fibonacci levels carry no information here.** `fib_sr_confluence` fires on **48.6–81.0% of all
bars** — it is near-independent of the thing it is supposed to be confluent with. Fib-level bars
are indistinguishable from plain support/resistance bars, and neither beats a direction-matched
random entry. **Four conditions can leave the search space with no measured loss.**

**The lunch-hour folk claim is refuted.** Turning `avoid_lunch` on hurts (z = −3.25).

---

## Groups that cannot be evaluated as built

| group | status |
|---|---|
| SUPPLY_DEMAND | **Unusable.** Zero strategies reach 20 trades in six cells; quadrupling budget moved 0 → 0. `fresh_zone_approach` fires on 0.5–3.8% of bars, scarcest in the library. Needs `min_signals=1` or demotion to a FILTER. |
| LIQUIDITY (sweep side) | **Never testable.** 156 rule sets with ≥1 trade, **0 with ≥20**, every cell every symbol. Every published LIQUIDITY number is 100% the breakout side. |
| VOLUME_PROFILE | One condition wearing six names — 100% of floor-clearing strategies carry `value_area_breakout`, which fires on ~51% of bars. Treat it as a trend proxy. Cannot trade at 4h at all. |
| MEAN_REVERSION | The +0.070 was a floor artefact; floor-free it is **−0.141R, worst of 12 groups**. |
| PULLBACK | `adx_trending` is **not offered to this template at all** — the library has no strength gate on the trend precondition. |
| REVERSAL | `range_position_extreme` is a **guaranteed zero** inside it (returns LONG at the range top — a continuation read — so it can never agree with the required mean-reversion signal). 262–440 rule sets per cell, 0 trades, 12 of 12 cells. |

---

## Hard constraints for anyone building on this

1. **The 20-trade floor selects on exit geometry, not signal quality.** A 0.75-ATR stop yields
   ~88 trades where the same entry at 1.5-ATR yields ~11. Every ranking is biased toward tight
   stops. Report floor-free censuses alongside.
2. **Scan windows of 274/180/90 days are nested**, all ending on the same bar. Agreement across
   them is one observation at three scales. Use disjoint slices.
3. **Seven filter names are exact aliases for group membership** — `avoid_lunch`≡REVERSAL,
   `opening_drive_window`≡OPENING_RANGE, `after_opening_range`≡LIQUIDITY,
   `mtf_not_conflicted`≡PULLBACK, `regime_trending`≡TREND, `regime_ranging`≡MEAN_REVERSION,
   `volatility_compressed`≡BREAKOUT. A/B on them compares groups.
4. **Pooling inflates at two levels.** Trade-level z inflates ~3× over paired per-strategy
   (−2.75 vs −0.83). Pooling per-strategy rows *across cells* inflates the same way
   (`di_direction` −4.32 pooled → −1.17 per cell). Only per-cell statistics combined by Stouffer
   or sign test are admissible.
5. **Cross-symbol comparison is currently impossible.** `generate_combinations` seeds RNG per
   symbol, so MES and MGC 60m populations share **0 of 204** rule sets.
6. **`max_per_template` is not a strategy count** — it is divided by `max(2, 2·len(filter_sets))`
   and split across the frame's timeframes.
7. **Costs are under-charged on multi-target exits**: a three-target scale-out pays **one** round
   turn, and the partials pay zero slippage. 62–92% of 4h/daily exits pay no exit slippage.

---

## What should be built next

Not a trading algorithm. Nothing here has an edge to implement, and building one now would be
dressing noise in code.

**Priority 1 — fix the defect register.** 26 defects, several of which corrupt results rather
than merely wasting budget. The `strategy_id` collision, the mis-scoped reward/risk floor, and
the three instances of the `rth_only` trap are already fixed; D21–D26 are not. Until
`rth_only=True` at 240m is addressed, every 4h population is 2–12× smaller than it should be.

**Priority 2 — a measurement harness that cannot produce these artefacts.** Floor-free censuses
by default; disjoint periods by default; matched controls by default; per-cell statistics with
Stouffer combination; and a guard that refuses to report a condition as an effect when it is a
group alias.

**Priority 3 — the live leads, tested properly.**

> **RETRACTED (2026-09-24, same day).** This section named `break_of_structure` at 240m as
> "the most promising untested thread in the programme", on +4.4 to +5.0 versus other
> structure signals, strengthening with the trade floor. The `s_leadlag` study has now run
> the out-of-sample test that was missing and **it does not reproduce**: median expectancy
> **−0.0364R** over 142 strategies, 37% profitable. Against `structure_trend` at 240m it is
> +1.061 over 15 cells (8+/7−), but that is **IS −0.876 / OOS +1.919**, and per disjoint
> slice it is negative on 5/5 symbols in the oldest, positive on 4/5 in the middle and
> negative on 4/5 in the newest — **5+/5− across out-of-sample cells, 3 of 6 on independent
> units**. A comparator artefact plus one favourable period. Do not build on it.

- ~~`break_of_structure` versus other structure signals at 240m~~ — retracted above.
- `fib_golden_pocket` versus `fib_shallow_retrace` at 240m. Still untested out of sample; it
  is now the only surviving lead, and it should be treated with the scepticism this
  retraction earns.

Both need the same treatment that killed everything else: disjoint periods, out-of-sample
splits, and deflation against the real search size. If they die there, they die.
