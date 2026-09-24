# Research brief — multi-timeframe swing structure

You are one of five agents researching **new** strategies built on swing structure — higher
highs / higher lows versus lower highs / lower lows — read across more than one timeframe.

## Why this is being rebuilt rather than extended

The library already has `structure_trend` (HH/HL vs LH/LL) and aggregates it across
timeframes as `mtf_aligned`. A 22-study programme just measured that machinery and it does
not work:

- Requiring **any** alignment signal is detectably **worse** than not requiring one:
  z = −4.09 over 14 cells, 366 vs 1,151 strategies.
- Demanding unanimity rather than a majority makes **no** detectable difference (z = +1.84).
- On any **two-member** frame, `mtf_aligned` and `mtf_strongly_aligned` are identical on
  100% of bars — with two voters, "majority" and "unanimous" are the same statement.
- `mtf_aligned` is the strongest negative condition in the whole library (z = −2.53).

So a weighted directional vote across timeframes is a dead end. Your job is to find out
whether a *better-specified* use of the same raw material works. The raw material is good;
the aggregation was naive.

**The central suspicion worth testing:** the library treats timeframe disagreement as a veto
(`mtf_not_conflicted`). That may be exactly backwards. The classic trade is a higher
timeframe in an uptrend while the lower timeframe is pulling back — making LH/LL temporarily.
Under the current design that setup is forbidden. Disagreement at the right nesting may be
the signal rather than the problem.

## What you have to work with

```python
import sys; sys.path.insert(0, 'workspace/studies')
import toolkit as T
```

Per timeframe, each snapshot exposes `structure_trend`, `last_swing_high`, `last_swing_low`,
`prior_swing_high`, `prior_swing_low`, and `swing_leg()`. Reach a timeframe with
`snap.tfs[tf]` or `snap.tf(tf)`. `futures_agents/indicators/structure.py` has `Swing` with
`confirmed_index` — **the bar at which the swing first became knowable**. Every consumer must
filter on it; a swing is not visible when it forms, only when it is confirmed.

New toolkit helpers for this work:
- `T.make_strategy(symbol, tf, conditions, ...)` — build a `Strategy` directly. Do **not**
  edit `library.py`; five agents editing one file will collide. Register your conditions in
  your own module under `workspace/newstrats/` with the `@condition` decorator and pass them
  in. It defaults to the one exit that survived the programme and to `rth_only=False`.
- `T.measure_custom(symbol, tf, window, strategies)` — run explicit strategies, same row shape.
- `T.disjoint_slices(symbol, tf, n=3)` and `T.slice_series(...)` — genuinely non-overlapping
  periods.
- `T.ab(rows, predicate)` — matched comparison. `T.wilson`, `T.free_t`, `T.mann_whitney_u`.

## Method — these are not optional

1. **Always build a control arm.** A new condition is measured against the same rule sets
   without it, on the same bars. A ranking of the best strategies containing your condition
   tells you what you searched, not whether it works.
2. **Out-of-sample or it does not count.** Use a 60/40 temporal split or `T.disjoint_slices`.
   The programme's single cleanest result was `volatility_compressed` scoring **z = +12.20
   in sample** and going **significantly negative out of sample in 6 of 12 cells**. In-sample
   significance is nearly worthless here.
3. **Never use nested windows as replication.** 274/180/90 all end on the same bar.
4. **Per-cell statistics only.** Pooling trades inflates z ~3× (−2.75 vs −0.83); pooling
   per-strategy rows across cells inflates the same way (−4.32 vs −1.17). Combine with
   Stouffer or a sign test.
5. **Report floor-free censuses.** The 20-trade floor selects on exit geometry: a 0.75-ATR
   stop yields ~88 trades where the same entry at 1.5-ATR yields ~11.
6. **Watch for degeneracy.** Before reporting any condition, print its firing rate. Under 1%
   cannot support a strategy; over 95% is not a condition. And check it is not identical to
   an existing one — that is exactly how `mtf_aligned` passed review for weeks while being a
   copy of `structure_trend`.
7. **Deflation.** `T.free_t(n_screened)` — roughly 4 t-units are free at these search sizes.
   Nothing in the library has ever cleared it. Do not announce one without the arithmetic.

## Coverage

Symbols **MGC, MES, NQ** minimum (add MNQ, MCL if useful — note MNQ/NQ/MES are one index
complex, so agreement between them is not independent evidence; MGC and MCL are the
independent contracts). Timeframes **240 and 60**. Periods: disjoint slices, not windows.

## Saving

`T.save(study_id, title, question, payload, headline, caveats)` — **the moment each piece of
analysis finishes**, not at the end. A previous run of this programme lost 18 studies to
agents who computed everything and saved nothing. Assume you can be stopped at any time.

## Reply

Under 25 lines. Headline, your two or three strongest numbers with both arm sizes and the
rank-sum z, your out-of-sample result for each headline claim, and anything that contradicts
this brief. A clean negative is a real finding — say so plainly rather than manufacturing a
positive.
