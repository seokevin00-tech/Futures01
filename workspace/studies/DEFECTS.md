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
