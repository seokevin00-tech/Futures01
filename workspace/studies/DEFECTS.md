# Defects found by the study programme

Fixes are deliberately **deferred until all twenty-one studies finish**. The studies run
as subprocesses that import the library; changing a condition mid-programme would mean
some studies measured the old behaviour and some the new, and the merged report could not
distinguish them. Consistency of the programme is worth more than an early fix.

Each entry names the study that found it and the evidence, so the fix can be verified
against the same measurement.

---

## D1 — Time-of-day conditions veto 100% of daily and most 4h bars (found by `x_session`)

`StrategyFilters.passes` has a `_SESSION_MINUTES = 390` guard, added when `rth_only` was
found vetoing every daily bar. **The four time-of-day conditions in `strategies/library.py`
have no equivalent guard.** On a daily bar `minutes_since_open` is −570 and `session` is
ASIA, so `opening_drive_window`, `after_opening_range` and `power_hour` each veto every
bar. Two of them are BASE filters on their templates, so whole groups die silently:

| timeframe | group | zero-trade strategies |
|---|---|---|
| daily | LIQUIDITY | 1068 / 1068 |
| daily | OPENING_RANGE | 972 / 972 |
| 4h | REVERSAL | 1012/1012 MES, 1104/1104 MGC, 1004/1004 NQ |
| 4h | OPENING_RANGE | 972 / 972 |

On 4h, `rth_only` leaves only the 12:00 ET bar, which `classify_session` labels LUNCH, so
`avoid_lunch` then vetoes it.

Zero-compute confirmation from the completed scans: of 189 daily rows, **0** are LIQUIDITY
or OPENING_RANGE; of 230 4h rows, **0** are OPENING_RANGE or REVERSAL.

**Fix:** give the time-of-day conditions the same session-length guard as
`StrategyFilters`, so a bar spanning whole sessions is not asked where it sits inside one.

## D2 — `power_hour` is unreachable on MGC and MCL (found by `x_session`)

It tests 15:00–16:00 ET, but MGC's RTH closes 13:30 and MCL's 14:30. 509/509 strategies
containing it take zero trades on those symbols. **Fix:** derive the window from the
contract's own `rth_close` rather than hardcoding equity-index hours.

## D3 — `Trade.session` records the signal bar's session, not the fill's (found by `x_session`)

Mismatch on **89.4% of 60m trades and 100% of 240m trades**. Any "performance by session"
analysis built on that field is mislabelled by one bar. **Fix:** stamp the session from the
fill timestamp, or rename the field so it cannot be misread.

## D4 — `exit_at_session_close` opens and closes 4h/60m trades in one bar (found by `x_session`)

Defaults True. A 16:00 ET 60m entry is opened and force-closed within the same bar: median
hold **0 minutes**, 68.5% exiting via SESSION_CLOSE. On 4h, **every** trade fills at 16:00
ET and is charged thin-book slippage. A matched split on this flag gives an hourly-shape
correlation of only +0.435 at 60m — the apparent mid-morning edge exists *only* in
force-closed strategies. **This is a confound in every 4h and 60m result produced so far.**

## D5 — `prior_session_profile` makes VOLUME_PROFILE impossible at 4h/daily (found by `g_volume_profile`)

Requires ≥10 bars in the prior session; a 23-hour CME session is 6 bars at 240m. Profile is
None on **0 of 1,148** 4h bars; all six profile conditions fire on 0.0%. Absence of the
group at 4h/1d is an implementation gate, not a market fact.

## D6 — The 20-trade floor selects on exit geometry, not signal (found by `g_volume_profile`)

NQ `value_area_breakout` at a 0.75-ATR stop: median 88 trades, 20/20 clear the floor. The
*same entry* at 1.5-ATR: median 11.5 trades, 0/10 clear. Every ranking in every report so
far is therefore biased toward tight stops. **Fix:** report a floor-free census alongside,
or stratify by stop width before ranking.

## D7 — Scan windows are nested, not disjoint (found by `g_volume_profile`, confirmed by `x_session`)

All windows end on the same final bar: 90d ⊂ 180d ⊂ 274d. "Stable across windows" is one
observation at three scales. Independently confirmed by `x_session`: real temporal splits
give IS-vs-OOS correlation of session mean R at −0.079 (MES 60m), −0.988 (MGC 60m), −0.392
(NQ 60m). Already corrected in `scan_reports/2026-09-23_MGC-MES-NQ_deep-scan.md`.

---

## M1 — MERGE HAZARD: three filter names are aliases for group membership

Raised by `x_session`, and it invalidates part of any study that sliced on these names.

In the shipped combinator:

- "contains `avoid_lunch`" ≡ "is REVERSAL"
- "contains `opening_drive_window`" ≡ "is OPENING_RANGE"
- "contains `after_opening_range`" ≡ "is LIQUIDITY"

A `T.ab` on filter presence in a normal population is therefore **a comparison of strategy
groups**, not of filters. Any study reporting one of these as a filter effect is reporting
a group effect under another name. The merge must catch this — `x_conditions` and
`x_regime` are the likely casualties.

## M2 — MERGE HAZARD: pooled trade-level statistics are badly inflated

NQ 60m lunch, pooled across trades: z = −2.75 on 330 trades. The same comparison done
per-strategy and paired: z = −0.83 on 13 strategies. Only paired, per-strategy numbers are
admissible. The merge must reject pooled trade-level z values.

---

## D8 — `ExitModel` index 3 can never trade, and two exits collide on one id (found by `g_mean_reversion`)

`Strategy.evaluate` rejects any setup whose reward/risk is below `exit.min_reward_risk`
(default 1.5). For an `R_MULTIPLE` exit that ratio **is** `targets_r[-1]`, and exit index 3
is `ATRx1.2 -> 1.2R`. 1.2 < 1.5, so it is rejected on every bar, always.

Verified: 0 trades as shipped, 85 trades with `min_reward_risk=1.0` on identical entries and
bars; 0 of 70,657 probe trades originated from it. It is one of seven exits offered to
MEAN_REVERSION, PULLBACK, REVERSAL, FIBONACCI, VWAP, OPENING_RANGE, LIQUIDITY,
VOLUME_PROFILE and SUPPLY_DEMAND — so roughly **1/7 of those templates' search budget is
spent on rule sets that structurally cannot trade**.

Separately and worse: `ExitModel.label` omits `min_reward_risk`, so two exits differing only
in that field **produce the same `strategy_id`**. `run_portfolio` keys results by
`strategy_id`, so one silently overwrites the other. This is a data-integrity bug, not just
wasted budget — it should be fixed before any re-run.

## D9 — 240m regime labels are UNKNOWN on short windows (found by `g_mean_reversion`)

`_default_regime_tf` falls through to 1440 at a 240m primary, and `classify_regime` needs 60
daily bars. Result: regime is UNKNOWN for **100% of trades at a 58-day window, 93% at 90
days, 16–35% at 274 days**. Any 240m regime-gated result on a window ≤180d is an artefact of
missing labels, not a regime finding.

## D10 — my own brief was wrong about 15m window support (found by `g_mean_reversion`)

`workspace/studies/BRIEF.md` told all 21 agents that "windows 274, 180, 90 are the ones all
timeframes support". **False at 15m**: `csv/raw/*_15m.csv` spans 58 days, so all three
windows resolve to the same 3,753 bars — one observation reported as three replications. Any
study treating 15m windows as independent has overcounted. `NQ_15m.csv` also does not exist;
MNQ is the substitute.

---

## Verdict log

| study | group/topic | verdict |
|---|---|---|
| `g_volume_profile` | VOLUME_PROFILE | no detectable edge; the group is one condition (`value_area_breakout`, fires on 51% of bars) wearing six names |
| `x_session` | time of day | no hours filter helps; `avoid_lunch` folk claim refuted; one replicated negative — do not open intraday 15:00–16:00 ET |
| `g_mean_reversion` | MEAN_REVERSION | nothing there. The +0.070 was a floor artefact — floor-free median is **−0.141R, worst of 12 groups**. Detectably *worse* than the population (z=−3.09). Regime gating does not rescue it (paired: RANGE better in exactly 50% of 382 identical rule sets). Negative **gross**, so costs are not the cause. |

---

# Round 2 defects (found by worker 3's cross-cutting studies)

## D11 — the anchor/execution split has never once been tested (found by `x_timeframes`)

`execution_tf` is `None` in **8,521 of 8,521** scanned strategies. `DEFAULT_EXECUTION_MAP`
points at timeframes that `FRAMES` never carries, so the split was unreachable by
construction. Every claim made about it in this project — including in the committed scan
report — rests on a feature that never activated.

Built properly (8 cells, 742 paired pairs): pooled expectancy sign z = **−0.00**. The
mechanism is real but self-cancelling — payoff z = +9.59, win rate z = −6.79. It pays only
where the theory says it should: **+3.20 with ANCHORED targets, −2.85 with R_MULTIPLE**,
and +4.07 at a 240m anchor versus −5.80 at 1440m.

## D12 — `exit_at_session_close` reduces every 4h and daily trade to one bar (found by `x_costs`, `x_exits`)

Median hold at 240m and 1440m is **0.0 minutes**. With the shipped flag every 4-hour and
daily trade opens and force-closes inside the same bar. Turning it off is the single
largest exit effect measured (paired sign z = +3.52) — and the headline "anchored beats
R_MULTIPLE" result (z = −5.87) **is this flag**: cross the two and target-kind falls to
z = −1.80 / +1.24, neither detectable. Every 4h and daily result in this project is
contaminated by it.

## D13 — scale-out legs are charged neither commission nor slippage (found by `x_costs`)

A three-target exit pays **one** round turn, not three, and the partials pay zero slippage.
Multi-target exits are under-costed roughly 2×, and 62–92% of 4h/daily exits pay no exit
slippage at all. This flatters exactly the multi-target exits the catalogue favours.

## D14 — per-symbol RNG seeding makes cross-symbol comparison meaningless (found by `x_robustness`)

`generate_combinations` seeds its RNG per symbol, so MES and MGC 60m populations share
**0 of 204** rule sets. Every cross-symbol comparison in this project has been comparing
*different strategies*, not the same strategy on different instruments. `strategies_for_symbols`
exists for this and was scoped to diagnostic-only.

## D15 — no paired exit control exists (found by `x_exits`)

The exit index is sampled jointly with the rule set, so only **93 of 8,317** shipped rule
sets exist with two different exits. Exit comparisons on the shipped population are
confounded with the entry; a paired test requires re-emitting entries across every exit.

## D16 — `RegimeSnapshot()` defaults volatility to NORMAL (found by `x_regime`)

So `volatility_normal` silently **passes** when the classifier has no history, rather than
reporting itself inert. Combined with: regime is UNKNOWN on 100% of 240m bars at 58 days,
77.9% at 90, 25.6% at 274; and daily falls through to a weekly classifier needing 5.7 years,
UNKNOWN on 32.6% of bars even at 274 days.

## M3 — regime and volatility base filters are group aliases too (found by `x_regime`)

Five of six regime/volatility conditions are template BASE filters present in 100% of their
group — `volatility_normal` in 1,361 of 1,377, with an **empty control arm in all 15 cells**.
None can be evaluated on the shipped population; they need re-emission with and without.

---

## Verdict log, round 2

| study | verdict |
|---|---|
| `x_robustness` | **Nothing replicates.** 72 strategies positive across three disjoint periods vs a permutation null of **70.1** — exactly chance. Excluding one cell: 44 vs 60.2, *below* chance. Walk-forward OOS negative in 5 of 6 cells; at MES 60m the top-ranked strategy differs in **6 of 6 folds** (stability 0.150). At 240m only one rule set clears a 20-trade floor in all three periods, so that timeframe structurally cannot be out-of-sample tested on this data. |
| `x_exits` | Turn `exit_at_session_close` **off** (z=+3.52). Best exit `ATRx1→2/4R[ANCHOR_ATR:1/2.5]`, best in 7 of 10 entry groups, stable at floors 5–30. Anchored targets raise payoff (z=−13.83) but **not** expectancy. Three targets worse than two (z=−2.87). No stable best stop width — the ordering reverses by timeframe. |
| `x_costs` | Costs decide **MES 5m and nothing else**. Zeroing them moves MES 5m from −0.161R to −0.013R and flips 30.7% of strategies to profit; NQ and MGC 5m stay ≈−0.2R. Every other cell is negative **gross**. All-in cost is 15.0% of one R at 5m, 4.6% at 60m, 1.2% at daily. |
| `x_conditions` | **One survivor** of 34 tests under Benjamini-Hochberg: `ema_stack` (+0.028R vs −0.006R, 96 vs 854, Stouffer z=+3.10 over 7 cells, 6 agreeing). No negative survives correction. `mtf_aligned` at z=−2.53 is the strongest negative and contradicts the multi-timeframe premise. `oi_expanding` fires on 0.0% of bars; both news filters pass 99.9%. |
| `x_timeframes` | The split was never active (D11). Rebuilt: pooled z=−0.00, but +3.20 with anchored targets and +4.07 at a 240m anchor. Best anchor timeframe is **symbol-specific and disagrees in both directions** — MGC prefers 240m, NQ prefers 60m. |
| `x_confluence` | **More confluence is a bad trade.** 2→4 signals cuts median trade count 65→42 (z=+8.48) and does not move expectancy (z=+1.89, sign favouring *two*). The pooled table saying 4 beats 2 is a cell-composition artefact. One filter beats three or four on every statistic. **Answer: 2 signals, 1 filter.** Candlesticks unevaluable — 4 of 5 appear in under 20 floor-clearing strategies. |
| `x_regime` | Not a usable knob. `volatility_normal` earns its place overall (z=+9.64 over 2,571 pairs) but is **symbol-specific** — MGC 60m detectably negative (z=−6.30). `regime_trending` does nothing: 3 cells positive, 3 negative. Trend-following does not need a trending regime. |

---

# Round 3 defects (worker 1's group studies). Renumbered D17+ — worker 1 and
# worker 3 both started at D11 independently.

## D17 — my `mtf_aligned` fix was incomplete: the degeneracy moved (found by `g_multi_timeframe`)

The guard requires two voting timeframes and `FRAMES` now puts something above the primary.
That fixed the *reported* case. It did not fix the general one: **on any TWO-member frame,
`mtf_aligned` ≡ `mtf_strongly_aligned` on 100% of bars** — 4259/4259 MES 60m at [60,240],
1148/1148 at the shipped 240m frame on all five contracts, 1859/1859 MES daily. With two
voters, "majority" and "unanimous" are the same statement. The shipped 240m frame is
`[240, 1440]`, so every 4h strategy is still affected. A frame needs **three** members before
the two conditions can differ.

## D18 — five templates carry the three anchored exits twice (found by `g_trend`)

The exit catalogue has 12 entries and 9 distinct models, so ~25% of those templates' search
budget is spent re-testing the same exit — and cross-group comparisons are not exit-matched
as a result.

## D19 — `exit_at_session_close=False` is an exact alias for anchored targets (found by `g_trend`)

Zero overlap: every anchored exit sets it False, every R-multiple exit leaves it True. So
D12 cannot be measured on the shipped catalogue at all — the flag and the target kind are
the same variable. Worker 3 separated them by re-emitting entries across both; on the
shipped population it is unidentifiable.

## D20 — daily LIQUIDITY and OPENING_RANGE are legitimately inapplicable (found by `g_opening_range`)

This is the diagnosis the fix commit deferred. **Every liquidity signal fires on 0 of 1,859
daily bars**, and the opening-range conditions are sub-hourly concepts. The groups are not
gated by a bug at daily scale; the ideas need intraday structure that a daily bar does not
contain. 4h LIQUIDITY *does* now work after the session-guard fix. Verdict: legitimately
inapplicable at daily, previously broken at 4h, now fixed at 4h.

## M1 UNDERCOUNTED — there are seven group aliases, not three

Add to the three already recorded: `mtf_not_conflicted` ≡ PULLBACK, `regime_trending` ≡
TREND, `regime_ranging` ≡ MEAN_REVERSION, `volatility_compressed` ≡ BREAKOUT. Any A/B on
these names compares groups, not conditions.

## M2 RECURS ONE LEVEL UP — pooling per-strategy rows across cells inflates too

Fixing trade-level pooling was not enough. Pooling per-strategy rows across cells inflates
the same way: `di_direction` −4.32 pooled → −1.17 per cell; `relative_volume_high` −2.92 →
−0.52; `above_vwap` −2.16 → −0.54. Only per-cell statistics, combined by Stouffer or sign
test, are admissible.

---

## Verdict log, round 3

| study | verdict |
|---|---|
| `g_trend` | **The headline was a floor artefact.** TREND beats a matched control (z=+4.93, 14 cells) but only in the **20–40 trade band** and only on MES (z=+6.86; MGC +0.36, NQ +0.42, MNQ −1.20). It **inverts** at n≥40 (z=−1.93) and n≥60 (z=−4.78); corr(expectancy, n) inside TREND is **−0.272**. It reverses on daily, where its arms are largest. "+0.252 / 96.4%" was the scans' surviving set, not the group. No within-TREND condition survives per-cell matching. |
| `g_multi_timeframe` | **The premise is refuted.** Having any mtf signal is detectably *worse* than none: z=−4.09 over 14 cells (366 vs 1,151). Unanimous vs majority: no detectable difference (z=+1.84, 38 vs 37). A paired experiment adding a weekly vote helps MES/NQ/MNQ — but those are one index complex; MGC, the only independent contract, disagrees (−0.022R). |
| `g_momentum` | The paradox dissolves: MOMENTUM **is** its trend-following half (2,107 rule sets to 127). Matched win rate is not higher (z=+1.22, 10/23 cells); it holds 38 of the top-100 win-rate rows only because it is 35% of the population. Group vs rest: z=−0.02. |
| `g_vwap` | Neither half works and neither beats the other (z=−0.54). The premise is wrong: VWAP's payoff ratio (1.218) **and** win rate (0.448) are both *below* the population's (1.268 / 0.4545). |
| `g_liquidity` | **The sweep side has never once reached a testable sample**: 156 rule sets with ≥1 trade, **0 with ≥20**, in every cell on every symbol. Every floored LIQUIDITY number ever published is 100% the breakout side. |
| `g_opening_range` | **Neither replicated nor about opening ranges.** The three "windows" are 11/6/2 strategies and both 90d ids sit inside the 274d set — one observation. `opening_range_breakout` fires on **4 of 4,256** NQ 60m bars; of 47 floored OR strategies, **1** is an actual OR breakout (28 are prior_day_breakout). |

---

# Round 4 (worker 2). D21+ — includes a defect in my own fix.

## D21 — MY SESSION GUARD NEVER REACHED 4h (found by `d_dead_groups`)

`_spans_sessions(tf)` tests `tf >= _SESSION_MINUTES` = 390, and **240 < 390**, so the guard
I added does nothing at 4h. `opening_drive_window` still fires on **0.0000 of 1,287** 4h
bars. Removing it alone: MES 0/300 → **36/300** trading (340 trades), NQ 0/328 → **44/328**
(640 trades).

The mechanism is not "the bar spans sessions" but "the bar is too long to sit inside the
window being asked about": `rth_only` keeps only the 12:00 ET bar at 4h, whose
`minutes_since_open` is 150, and `opening_drive_window` wants 0–90. A 240-minute bar cannot
be inside a 90-minute window. The guard needs to compare the bar length against the
*window* it is testing, not against a fixed session constant.

MGC has a second independent gate: `max_minutes_since_open=150` against its 08:20 RTH open
puts the single RTH 4h bar at mso=220, so MGC's scope pass is 0.0000 on its own.

## D22 — 240m REVERSAL is an exactly empty intersection (found by `d_dead_groups`)

`rth_only` keeps exactly the 209 12:00-ET bars (16.24% of 1,287). `classify_session` labels
**all 209** LUNCH. `avoid_lunch` therefore passes on exactly 1 − 209/1287 = 0.8376 — the
exact complement. The two filters are precise complements and their intersection is empty by
construction. The signal layer is healthy (bollinger_mean_pull 0.32–0.36, cvd_directional
0.84–0.86, agreeing pairs on 22–23% of bars). Dropping `avoid_lunch` → 16/324; turning
`rth_only` off → 41/312.

## D23 — the rth_only trap, third instance, one layer lower (found by `d_dead_groups`)

`prior_day_sweep` and `prior_day_breakout` are proper daily concepts and fail on daily bars
only because `_build_session_state` derives previous-day levels from bars flagged by
`is_rth(b.ts)` — and `is_rth` is False on **100% of daily bars**. Same bug family as
`rth_only` in StrategyFilters and the time-of-day conditions, now found in the session-state
builder. **Warning attached**: because the LIQUIDITY template's required group is merely
"liquidity", fixing this plumbing would revive daily OPENING_RANGE as a relabelled duplicate
of daily LIQUIDITY.

## D24 — `rth_only=True` is the library-wide 4h population killer (found by `d_dead_groups`)

It costs **every** group a 2–12× factor at 240m, because it reduces the 4h population to one
bar per day.

## D25 — `range_position_extreme` is a guaranteed zero inside REVERSAL (found by `g_reversal`)

It returns LONG at the **top** of the range — a continuation read — so it can never agree in
direction with REVERSAL's required mean-reversion signal. 262–440 rule sets per cell contain
it and **0 take a single trade in all twelve cells**, while the same condition trades freely
in BREAKOUT (up to 2,387 trades). ~5–6% of the template's budget. Same family as D8.

## D26 — BREAKOUT's volume requirement silently disappears (found by `g_breakout`)

`required_groups=('structure','volume')` reduces to `('structure',)` because the volume group
holds no SIGNAL conditions and `_signal_pools` drops empty pools. `relative_volume_high` is
never offered to this template. The template asks for volume confirmation and never gets it.

## T1 — toolkit caveat: `max_per_template` is not a strategy count

`generate_combinations` divides it by `max(2, 2*len(filter_sets))` and splits the result
across the frame's timeframes, so budget 8,000 yields ~1,100–2,100 rule sets per template per
timeframe. Every "budget" figure in these studies should be read that way.

---

## CONTRADICTION BETWEEN STUDIES — `ema_stack`

`x_conditions` (worker 3) reports `ema_stack` as **the single survivor** of 34 tests under
Benjamini-Hochberg: +0.028R vs −0.006R, 96 vs 854 strategies, Stouffer z=+3.10 over 7 cells,
6 agreeing.

`g_pullback` (worker 2) reports `ema_stack` as **the sharpest overfit it found**: in-sample
z=+4.27 → out-of-sample **0.00** on MES 60m, and IS +2.25 → OOS **−3.87** on MGC 240m.

These are not the same test — worker 3 measured it across the general population without an
out-of-sample split; worker 2 measured it inside PULLBACK with a 60/40 temporal split. But
the only condition to survive correction in the whole programme is also the one that most
clearly fails out-of-sample where anyone looked. **Treat `ema_stack` as unproven, not as the
one surviving edge**, until someone runs worker 3's exact test with worker 2's temporal split.

---

## Verdict log, round 4

| study | verdict |
|---|---|
| `d_dead_groups` | **Three of four dead cells are bugs, one is a market fact.** 240m OPENING_RANGE broken (D21), 240m REVERSAL broken (D22), daily LIQUIDITY mixed — 5 of 7 signals legitimately inapplicable, 2 of 7 broken (D23). Daily OPENING_RANGE legitimately inapplicable: `snap.opening_range` is None on 100% of daily bars. |
| `g_supply_demand` | **Unusable as built; the freshness question is unanswerable.** Zero strategies reach 20 trades in all six cells; quadrupling budget moved 0 → 0. Not the detector's fault — 72–93% of bars carry a zone, but `fresh_zone_approach` fires on 0.5–3.8%, the scarcest condition in the library. Needs `min_signals=1` or demotion to a FILTER, plus more history. |
| `g_pullback` | `adx_trending` **is not offered to PULLBACK at all** (0 of ~4,000–7,800 rule sets), so the library has no strength gate on the trend precondition. The "dip with a trend signal" effect is PULLBACK-vs-REVERSAL under another name (M1), and on a temporal split it **reverses significantly in 3 of 9 cells**. |
| `g_reversal` | No extension effect survives (z=+3.02 at floor 1 decaying to +0.82 at floor 20; OOS −2.52). 43 of 152 tests nominally significant against 7.6 expected, but heavily correlated. Plus D25. |
| `g_breakout` | **Requiring compression is an in-sample illusion.** Paired counterfactual: full-sample z up to **+12.20**; on a 60/40 split, IS-positive in 11 of 12 cells and **significantly negative OOS in six** (NQ 240m +6.89 → −7.56; MGC 60m +10.94 → −6.90). Survives only on MES 60m. It also costs ~2/3 of all opportunity. Volume confirmation is not general either. **Unfinished thread worth chasing**: `break_of_structure` vs other structure signals is +4.4 to +5.0 at 240m on all three symbols and *strengthens* with the floor — not yet OOS-tested. |
| `g_fibonacci` | **Clean negative.** `fib_sr_confluence` does not test confluence — it fires on **48.6–81.0% of all bars**, and P(fires given a fib level) exceeds P(fires without) by only 0.4–9.0 points. Fib-level bars and `pullback_to_support` bars are indistinguishable; against a direction-matched baseline neither beats a random entry. **Four conditions can be dropped from the search space with no measured loss.** |

## D27 — `market_structure().events` never resets its reference levels (found by `s_leadlag`)

`ref_high` / `ref_low` are running max/min that are never reset, so structure events are
emitted almost never: **5,000 MGC 60m bars yield 30 BOS_UP and 1 CHOCH_DOWN**. The event
stream is unusable; `s_leadlag` had to work from the confirmed-swing arrays instead. Anything
in the library reading `.events` is reading near-nothing.

---

## D28 — MY OWN `T.ab()` INFLATES z WHEN ARMS HOLD CORRELATED VARIANTS (found by `s_geometry`)

**This affects every study in the programme, because I told 24 agents to route every
comparative claim through it.**

`T.ab` runs a rank-sum over *strategies*. Clone collapse removes arms whose realised trades are
**identical**, but partner variants sharing 50–90% of their trades survive collapse and are
still heavily correlated. The rank-sum then treats them as independent observations.

Measured against the trade-level test on the same trades: **|z| inflated ~3.3×, range
1.7–15.3**, producing |z| up to **8.7 on null data**, and the sign flipped out of sample in 7
of 11 comparisons.

This is the **third distinct instance of the same underlying defect** in this project, and the
three point in different directions, which is why it kept being missed:

| instance | inflated unit | direction |
|---|---|---|
| M2 | pooling **trades** across strategies | trade-level z ≈ 3× the paired per-strategy z |
| M2 (recurrence) | pooling **per-strategy rows across cells** | pooled −4.32 → per-cell −1.17 |
| **D28** | pooling **correlated strategy variants** within a cell | strategy-level ≈ 3.3× the trade-level z |

The general rule, which should have been in the brief from the start: **whichever unit you
aggregate over, if those units share trades they are not independent and the test inflates.**
Correct practice is per-cell statistics on genuinely independent units, combined by Stouffer or
a sign test — and stating which unit was treated as independent and why.

**What this does and does not invalidate.** It does not touch the negative findings: an inflated
statistic that still fails to reject makes the negative *stronger*. It does threaten any
positive claim in the programme whose arms contained correlated variants. The positives that
matter — the `exit_at_session_close` effect, the anchored exit ranking, the ≤0.5 ATR structural
stop prohibition — were all measured **paired within entry** or by sign test across cells rather
than by a raw rank-sum across variants, so they are likely safe. That should be verified rather
than assumed before anyone builds on them.

## D29 — the fractal confirmation lag eats most of a swing leg (found by `s_geometry`)

The 3-bar fractal confirmation consumes **50–60% of a median 5–6 bar swing leg**, on every
symbol and both timeframes. This is a structural reason multi-leg geometry cannot be traded on
this data at these timeframes: by the time a leg is confirmed, most of it has happened. Not a
bug — a constraint that should be stated before anyone proposes another swing-geometry idea.

---

## D30 — the opening range is never constructed at 60m or 240m (found by `ict_define`)

**The blocking ORB defect, and distinct from D21.**

`futures_agents/features.py::_build_session_state` seeds the opening range with
`minutes_since_open(b.ts, spec.rth_open) < 30`. But 60m and 240m bars are clock-aligned to
`:00`, while RTH opens at **09:30** (MES/NQ/MNQ) or **08:20** (MGC). No bar ever falls inside
the seed window, so the range is never built: `snap.opening_range` is None on **4,990 of 5,000**
MES 60m bars.

Measured OR-seed bars:

| symbol | 5m | 15m | 30m | 60m | 240m |
|---|---|---|---|---|---|
| MES | 114 | 82 | 41 | **2** | **0** |
| MGC | — | — | — | **0** | — |
| MCL | — | — | — | works | — |

MCL works only because its RTH opens at 09:00, on the hour. So `opening_range_breakout` at
0.16%/0.00% and `opening_range_fade` at 0.0000 are **this bug, not a market fact** — which
retroactively explains the "4 of 4,256 bars" finding.

**The sting, which is worse than the bug.** ORB is computable only at 5m (~19 trading days of
data) and 15m/30m (~41 days), giving **at most ~41 signals per symbol**. The 60m series has ~201
days and structurally cannot form a range. **The timeframes with the sample have the bug; the
timeframes without the bug lack the sample.** ORB may not be testable on this data at a sample
worth believing, whatever the fix.

## D31 — kill-zone windows are arithmetically impossible at 240m (found by `ict_define`)

240m bars start at 00/04/08/12/16/20 ET, so the Silver Bullet window (10:00–11:00 ET) contains
**0.00%** of them. At 60m it is 4.38% — exactly one bar per day. Not a weak effect; an empty
intersection.

## D32 — `market_structure()`'s CHoCH is mis-specified, not just rare (found by `ict_define`)

Refines D27. CHoCH emits **1–6 events per 5,000 bars (0.02–0.30%)**, an order of magnitude below
the 1% usability floor, because it compares against a **running extreme** rather than the last
counter-trend swing as the methodology defines it. Re-specify or drop; do not report its rarity
as a market fact.

## Genuinely new and untested (from `ict_define`'s mapping)

Ranked, with measured firing rates where taken:

1. **BOS/CHoCH as EVENTS rather than STATE.** The indicator layer emits them with
   `confirmed_index`, but **no condition exposes them**. BOS events fire on 0.6–2.3% of bars
   against the existing *state* condition's 31–42%. These are different objects and only the
   state has ever been tested.
2. **Inversion FVG** — zero implementation; `active_fvgs()` discards filled gaps by construction,
   so the concept is unreachable.
3. **Breaker block** — zero implementation, though `SDZone.invalidated_index` and `as_of()`
   already exist to support it.
4. Base-free order block (the library's SDZone demands a *base*; ICT's order block does not).
5. Equal highs/lows (0.9–1.6% at 0.10× tolerance, 2.5–3.6% at 0.25×).
6. Swing-anchored premium/discount gate.
7. FVG size screen — `min_size_pct` is hard-coded to 0.0.

## Not falsifiable as stated — do not build on these

- **Power of Three** — labels every day post-hoc and refutes none.
- **Judas swing** — requires "the true daily direction", known only ex post. Its forward-testable
  residue is `overnight_sweep` AND a clock filter, joint rate likely <1%.
- **MSS** — sources give two incompatible detectors ("CHoCH + displacement" versus "the
  confirmation after a CHoCH").
- **Mitigation block** — not separable from order block in any source retrieved.

## Evidence quality warning

Every ICT source retrieved is a broker blog, indicator vendor or teaching site. **None
peer-reviewed, none with an auditable record.** Two circulating backtest claims flatly
contradict each other ("0 of 648 backtests beat the index" versus "FVG 64.8% mitigation vs
standalone OB 43.1% bounce"). Recorded as claims, not priors.

## D33 — SUPPLY_DEMAND's emptiness is its detector, not the concept (found by `ict_blocks_fvg`)

Corrects `g_supply_demand`, which concluded the group was "unusable as built and the freshness
question cannot be answered with this data". The first half stands; the second does not. The
library's SDZone requires a base, a departure, an ATR gate and freshness, and fires on
**0.56–1.38%** of bars. A base-free order block — the ICT definition — fires on **40.5–51.5%**.
With that definition there are 191–634 first touches and 1,372–2,180 later touches per cell,
10–100× what the library's detector could supply, and the freshness question becomes answerable.
(The answer is still no, but for a real reason rather than for lack of sample.)

## D34 — `fvg_nearby` is a much narrower statement than "an FVG is in play" (found by `ict_blocks_fvg`)

Not a bug, but a definitional trap worth recording. The library declares an FVG **filled at its
midpoint** and tests the **close**; the standard reading keeps the gap live to its **far edge**
and tests the **bar range**. A deliberate re-implementation of the library's bookkeeping matches
it at Jaccard 0.785–0.966, so the machinery is sound — but the rates differ ~8×. Anyone reading
`fvg_nearby` as "the ICT fair value gap" is measuring something much stricter.

## D35 — a one-sided fill bug that resampling cannot catch (found by `orb_test`)

The worker found this **in its own code**, and the lesson generalises past ORB.

A retest limit order fills mid-bar at the bar's own extreme. Crediting that *same bar's* opposite
extreme as a target hit is one-sided: the stop cannot fire, because the extreme **is** the entry
price. It manufactured a **+0.354R cluster at t = 5.19** — and that cluster survived the 60/40
split **and all three disjoint slices**. 51% of its winners "hit target" on the entry bar. Fixed
(a stop may fill on a limit-entry bar; a target may not), and the same config is now −0.057R.

**The lesson, which belongs in every future study's method section: resampling cannot detect a
bias whose sign is always favourable. Only auditing the fill model can.** Out-of-sample testing,
disjoint periods and walk-forward all pass a bias that is present in every period. This is the
one failure mode the programme's entire methodology is blind to.

## D36 — `session_extreme_sweep` is self-referential, so its rarity is degeneracy (found by `ict_sweep_mss`)

`session_levels()` builds `session_high` from bars with `b.ts <= cutoff` — **which includes the
bar being tested**. So `bar.high > session_high` is arithmetically impossible, and the condition's
0.61% firing rate is an artefact rather than a market fact. The same mechanism caps
`overnight_sweep` at an RTH-only 4.7%.

A causal reimplementation fires at **2.7–3.9%** and **9.3–12.3%** respectively. The control that
proves the difference belongs to the library and not to the reimplementation: PDH/PDL reproduces
the library's number **exactly**.

## D37 — the combinator cannot express a sequence at all (found by `ict_sweep_mss`)

`min_signals=2` requires two conditions to fire **on the same bar**. A sweep and its consequence
never co-occur by definition. So every ordered-chain idea — the whole of ICT's central claim, and
any "A then B" setup — is inexpressible in the template system, and attempting one produces the
starvation that was previously misread as the idea failing. This is an architectural limitation,
not a tuning problem, and it explains the sweep family's "0 rule sets ever reached 20 trades".

## D38 — `toolkit.measure_custom` silently zeroes custom conditions (found by `orb_define`)

**Mine, and the worst failure mode there is: it returns zero rather than raising.**
`measure_custom` builds its own frame and never calls `register_frame`, so any condition needing
frame registration returns `no()` on every bar and the strategy reports zero trades. Indistinguishable
from "the idea does not work". Workers who built their own runners were unaffected; anyone calling
`measure_custom` with custom conditions got silent nulls.

## D39 — library ORB is a state, not an event, and silently widens (found by `orb_define`)

Three distinct faults in `opening_range_breakout`, on top of D30:

1. **No first-break gate and no session gate.** It fires on **18.5% of MES 15m bars, ~7 per day**,
   including overnight bars hours after the close. It reports "price is beyond the range", not
   "the range broke". Even where the range exists, it was never measuring ORB.
2. **Silent widening.** MCL opens 09:00 and *is* on the hourly grid, so its "30-minute OR" is
   built **60 minutes wide**. The condition fires on 17.3% of MCL 60m bars off a mislabelled
   range — and is a **Jaccard 0.993 duplicate of `initial_balance_break`** there (0.841 MCL 15m,
   0.686 MES 15m). **Sixth alias for the M1 list.**
3. **The resolvability rule**, which generalises D30 and D31 to a single arithmetic statement:

   > An L-minute opening range is resolvable on a T-minute frame **iff `T | L` and
   > `T | (RTH open in minutes from midnight)`.**

   09:30 = 570 is not divisible by 60; 08:20 = 500 is not divisible by 15, 30 or 60. **240m
   divides no OR length at all, so 4-hour ORB is arithmetically impossible** — the same
   statement as D20 at daily.

| symbol | 5m | 15m | 30m | 60m | 240m |
|---|---|---|---|---|---|
| MES/MNQ/NQ | 5/15/30/60 | 15/30/60 | 30/60 | **none** | **none** |
| MGC | 5/15/30/60 | **none** | **none** | **none** | **none** |
| MCL | all | 15/30/60 | 30/60 | 60 only | **none** |

## D40 — the micro-grain CSVs splice more than one contract month (found by `rank_nq_es_grains`)

**A data-integrity problem in the supplied files, not in the code.** MZC 60m: **26.3% of bars are
flat** (high == low, a single print), **11.3% have zero volume**, and **367 close-to-open gaps
exceed 2%, of which 47% fully reverse on the next bar**. On corn that excursion is ~16 cents — the
size of a CBOT calendar spread — so the continuous series is splicing quotes from **more than one
contract month** on hours the front month did not trade. MZW 19.6%/9.0%/202; MZS 12.2%/10.8%/98.

**NQ and ES have zero flat bars and zero 2% gaps**, so this is specific to the grains.

The engine honours gaps at the open, so stops fill at prices that never traded — which is very
likely why grain stops realise **−1.215R (MZC) / −1.179R (MZW)** against −1.007R on NQ. Dropping
zero-volume and single-print bars removes **29% of MZC's 60m bars and changes 6 of the 10
top-ranked strategies** on both MZC and MZW.

**No micro-grain backtest from these files should be trusted**, and the economics rule them out
anyway: median 60m bar volume is **11–17 contracts** (10th percentile 1–2) against NQ's 9,643, and
round-turn fees are **9.2% of one R on MZC** versus 0.17% on NQ.

## D41 — NQ and MNQ are NOT the same series (corrects an earlier claim)

An earlier ORB study reported that NQ and MNQ in `csv/raw` were "the same price series from two
fetch snapshots". Measured: only **0.2% of 4,947 overlapping 1h bars share OHLC** (mean |Δclose|
2.6 points), and NQ's last bar is 2026-09-18 against MNQ's 2026-09-22 — so windows anchored to the
final bar **end four days apart**. Same market, different bars. Still not independent evidence,
but the earlier statement was wrong and any analysis that treated them as identical is suspect.

## D24 — correction to my own brief: `rth_only=False` buys sample and costs expectancy

I told the ranking workers to leave `rth_only` off, citing the 2–12× population cost. Measured
paired, with identical rule sets and the flag flipped: trades rise ×3.2–4.2 at 240m and ×2.1–2.5 at
60m — **and paired median expectancy falls** (MZC 240m +0.073 → −0.103, sign p=0.001; MZC 60m
−0.237 → −0.387, p≈0; ES 60m −0.022 → −0.054, p=0.0004). **The RTH gate was flattering the
measurement while starving the sample.** Both readings are true; report which one you used.
Notably, in the properly-populated cells the best placebo ranks **1–3 against nulls of 4.7–19.2 in
8 of 9**.

## D42 — `placebo_shift` is not a clean control; `placebo_random` and `placebo_shuffle` are

The self-test across all four MGC/MCL cells, run after the single-cell check passed:

| kind | mean normalised rank (4 cells) | verdict |
|---|---|---|
| `placebo_shuffle` | 0.501, 0.539, 0.511, 0.574 | **clean** — uniform everywhere (KS p 0.25–0.98) |
| `placebo_random` | 0.621, 0.546, 0.536, 0.511 | **clean at 60m**; MCL_240 ranks *worse* than uniform (p=0.029), small-sample |
| `placebo_shift` | **0.370, 0.376, 0.442, 0.369** | **leaks** — consistently better than the 0.5 uniform predicts, in all four cells |

A 5-bar-shifted copy of a real signal retains part of that signal, because adjacent bars are
correlated. So `placebo_shift` is a **degraded real strategy, not a placebo**, and it is a
*conservative* control: a shifted placebo beating a real strategy is a weaker result than a random
or shuffled one doing so.

**This does not weaken the headline finding — it sharpens it.** The strongest case in the ranking
study (MZC 60m 180d, placebo taking ranks 1 and 2 of 66 rows) was `placebo_random` and
`placebo_shuffle`, the two clean kinds. Any future ranking should report the three kinds
separately and lean on shuffle, which is uniform in every cell tested.
