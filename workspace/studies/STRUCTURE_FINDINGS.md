# Multi-timeframe structure research — findings

Five studies on swing structure (HH/HL vs LH/LL) read across timeframes, run after the
22-study programme showed the library's existing `mtf_aligned` machinery fails.

---

## `s_freshness` — structure age and distance to invalidation

**Scope:** MGC/MES/NQ/MNQ/MCL × {60m, 240m} × 3 disjoint slices = 30 cells, newest slice held
out. Two independent simulators (a bespoke non-overlapping event study and production
`run_portfolio`), both net of costs, next-bar-open entry, stop-before-target.

### The hypothesis is refuted, and the refutation is clean

Entering near the structural invalidation does **not** improve reward for risk. It is the
same self-cancellation the programme has now measured **three times**:

```
near <= 0.5 ATR   payoff 3.00   win 0.194   expectancy -0.1101
ungated           payoff 1.75   win 0.323   expectancy -0.1061
                                            (30 cells, 1,018 trades)
```

A monotone dose-response confirms it: N0.25 z = −7.69 (OOS −7.65, **OOS sign 0/8**), N0.4
−6.24, N0.5 −3.88, N0.75 −1.12, N1.0 −0.77. Payoff climbs 1.76 → 3.42 exactly as win rate
falls 0.333 → 0.100.

**And the gradient is mechanical, not informational.** Holding geometry fixed at a 1-ATR stop
and 1.5-ATR target and gating on the same distance buckets, win rate is **flat at 0.370–0.423
across every bucket**. Proximity to invalidation carries no directional information at all;
the entire payoff/win-rate trade-off is stop distance and nothing else.

### What survives is a prohibition, not an edge

`distance_to_invalidation > 0.5 ATR` combined with `StopKind.STRUCTURE`:

```
30 cells, 1,600 trades   exp +0.0136R vs -0.1061R   win 0.411   payoff 1.44
sign 22/8   Stouffer +2.325
OOS: 10 cells, 516 trades   +0.0008 vs -0.2050   sign 7/3   z = +2.024
```

Flat across 0.25–1.25 ATR (z = +2.79 … +1.66, all OOS-positive), so it is not a knife edge.
The same gate with an ATR stop does nothing (z = +0.21), which confirms it is a **stop-placement
rule** rather than an entry filter.

**The caveat that decides how to read it:** it only restores parity. An ungated structural stop
is *worse* than a plain 1-ATR stop out of sample (z = −2.24, sign 2/8); this floor repairs that
damage. Measured against the ATR stop the floors are only +0.58 to −0.06 OOS. So the honest
statement is **"if you use structural stops, never let them be tighter than ~0.5 ATR"** — not
"structural stops are better". Nothing is live-eligible: free_t ≈ 3.00 and no positive arm
reaches it.

### Freshness does nothing

Six trend-age buckets with geometry held constant: median expectancy −0.12R to +0.21R, **no
monotonicity**, all |z| < 1.70. The freshest bucket (age 0–1, 13.1% firing, 1,371 trades) is
z = +0.459; it looked positive in sample (+0.1046R) and died out of sample (−0.0244R). The
**best** bucket is the *stalest* (age 31+), pointing opposite to the thesis.

### A suggestion in my brief was the worst arm in the study

I wrote that "a fresh 4-hour structure with price near its invalidation is the strongest form
of this idea" and told the worker to test that specific conjunction. Measured: it is the
**worst** arm, fires on **1.8%** of eligible bars (111 trades / 9 cells), expectancy −0.2851R.
The robustly-sized version is z = **−3.312**, OOS **−2.568**.

### Independent reproduction of the `mtf_aligned` negative

Built from scratch, not reusing the library's implementation: mere 4h/60m structural agreement
gives −0.0990R vs +0.0020R, sign **2/13**, z = **−2.684**. The programme's finding that
timeframe agreement *hurts* now has a second, independent confirmation.

### The census that undercuts the whole premise

Across 30 cells: structure trends on 50–66% of bars; trend age median 4–10 bars. Distance to
invalidation median **1.57–2.68 ATR**. And the implied reward:risk of the natural structural
trade — stop just past invalidation, target at the prior swing extreme — has a median of
**0.60–1.10, below 1:1 in 26 of 30 cells**. Only 21–45% of bars offer RR ≥ 1, and the prior
extreme is **already behind price** on 13–38% of bars.

That is a structural fact about these instruments, not a strategy result: the textbook
structural trade is usually a sub-1:1 proposition before any edge is considered.

### Bias checks

Look-ahead and repainting clean (a truncation probe reproduced 120/120 rows). The tape's levels
match the frame's `last_swing_low/high` and `structure_trend` on **3,684/3,684** bars, so the
filter and `StopKind.STRUCTURE` refer to the same price. Costs flagged: a 0.5-ATR structural
stop costs 0.0633 R/trade against 0.0149 R ungated (4.2×), but gross-of-cost runs are still
significantly negative (z = −3.85), so costs amplify rather than create the effect. Per-cell
statistics only, non-overlapping slices, no nested windows. Data limit: one ~10.5-month window;
240m is resampled from 1h (~450 bars per slice).

---

## `s_swing_depth` — how many consecutive HH/HL is enough

**Scope:** 19 new conditions in `workspace/newstrats/depth.py`; MGC/MES/NQ/MNQ/MCL × {60m, 240m}
× disjoint halves. Depth defined as `min(trailing run of HH, trailing run of HL)`.

### `structure_trend` was already the depth-1 condition

`sd_count_ge1` is **identical to `structure_trend` on 100.000% of bars in 10 of 10 cells**, with
bit-identical trade fingerprints on 140/140 (cell, partner) pairs. `features.py:639-649` sets
UPTREND iff `last_high > prior_high AND last_low > prior_low` — which *is* depth ≥ 1.

This corrects the framing in my own brief, which treated `structure_trend` and the
multi-timeframe aggregation as separate machinery. They are not: **`mtf_aligned` was a weighted
vote over depth-1 reads.** Any future design that adds a "swing count" on top of
`structure_trend` is double-counting, and this is the fourth condition in this project found to
be a duplicate of another under a different name.

### There is no optimal count above 1, and the reason is sample

Firing rates: ge1 56–65%, ge2 15–24%, ge3 4.9–8.9%, ge4 1.2–3.7%, **ge5 0.0–1.6% — below the
1% floor in 6 of 10 cells**. ge4 and ge5 are *untestable*, not negative.

**The trade-count cost is four times worse than the confluence cost the programme already
measured.** Fraction of control trades retained: ge1 0.520, ge2 0.199, ge3 0.065, ge4 0.023. In
independent structural episodes, depth 1→2 loses 63–74% and 2→3 another ~65%. Over 10.5 months
every 240m symbol has **≤10 depth-3 and ≤4 depth-4 episodes**.

### Decay is real in magnitude but does not replicate

Paired, Stouffer over per-cell sign z: ge3 vs ge1 **z = −4.26 IS / −1.36 OOS**; ge3 vs ge2
−3.96 IS / −1.17 OOS; ge2 vs ge1 −2.70 IS / +0.35 OOS. Median IS expectancy −0.055 / −0.080 /
−0.127R at ge1/ge2/ge3. Under the strictest one-vote-per-cell sign test **nothing clears**;
5-fold walk-forward is null (ge1 20/50 folds positive, ge3 7/18).

### The decisive test: depth is worse than randomly discarding the same trades

Trade-count-matched control (1,926 tests, 2,000 draws of the same *m* trades from each arm's own
control): ge3 sits at the **28.7th percentile**, z = **−6.57 IS / −3.49 OOS**. Depth performs
worse than a random equal-sized sample of the same entries — so the filter is not selecting
better trades, it is selecting worse ones while also selecting fewer.

### Adding a depth filter destroys out-of-sample transfer

Spearman(IS expectancy, OOS expectancy) across 966 depth rows = **+0.004**. The same controls
*without* a depth filter score **+0.287** Pearson. Whatever weak IS→OOS signal exists in this
library, requiring swing depth removes it.

### Count versus duration

Median structure age in bars is near-identical at 240m and 60m (25–27 bars at depth 1, 63–85 at
depth 5), so the count is scale-free in bars and a 240m depth-5 is 4× longer in wall-clock. The
count × timeframe interaction is a **sign flip, not an optimum**: ge3 vs ge2 is −4.87 OOS on
240m and +2.29 OOS on 60m.

The only arm positive in both halves is **duration, not count**: `sd_age_ge50` on 60m,
z = +3.75 IS / +2.06 OOS versus depth-1, OOS median +0.238R at a 50% win rate. But it rests on
4 cells and 4.1% of control trades, reverses to −3.16 on 240m, and sits below free_t = 2.61.
Treat as overfit.

**Nothing live-eligible:** best single-half t across 2,800 rows is 2.566 against free_t = 3.984.

Bias checks: look-ahead PASS (240/240 truncation matches, lag exactly 3 bars), repainting PASS,
leakage PASS, fills PASS, costs PASS (3× slippage changes no conclusion), parameter sensitivity
stable in shape across fractal width 2–5 though not in sign. Failed by the worker's own audit:
sample size, data-mining bias, OOS transfer, and duplication.

---

## `s_leadlag` — does the lower timeframe break structure first

**Scope:** 5 symbols, ~321 days of 60m bars, 60m→240m; matched arms share a 14-strategy
population with only the entry swapped; disjoint slices, per-cell rank-sum + Stouffer.

### The lead is real, large and stable — and not tradeable

The 60m breaks structure a median **6–17 bars** ahead of the 240m (MGC 17, MNQ 8, MCL 7, MES
6.5, NQ 6; IQR ~2–27; zero-lead 0%). **79–87%** of 240m breaks had a live 60m break of the same
direction already in place, and only **0–6%** had no precursor at all. Availability is not the
problem.

The price is a **78–81% false-positive rate** at a 24-hour horizon (69–77% at 48h) — near
identical on all five symbols, on the independent 15m→60m pair, and between the first 60% and
last 40% of the sample. **Buying a 1.5–4.5 higher-timeframe-bar head start costs four wrong
signals in five.**

The mechanism confirms it: early entries genuinely are earlier (405–431 min held vs 570–633),
but their MFE is *lower* (1.37–1.41R vs 1.43R) and MAE *higher*, edge ratio 1.169 vs 1.231.
Being early buys worse excursion — exactly what an 80% false-positive rate predicts.

### Every strategy form of it is null or worse

- `ltf_break_first` vs `break_of_structure`@60: z = −0.60 over 15 cells, **−1.17 OOS**;
  six-block walk-forward +0.112 with 15+/15−. The "not yet confirmed" gate is worth nothing.
- `htf_confirms_late`: monotone worse with N (N=0 −0.077R, N=4 −0.104R), N=8 never clears 20
  trades, N=16 fires on 0.0%. Entering late is strictly worse.
- The one apparent survivor fails parameter sensitivity: K = 0/1/3/∞ gives
  −0.036/−0.046/−0.012/−0.043, an isolated bump.

### `break_of_structure`@240 fails its first out-of-sample test — lead RETRACTED

The previous programme flagged this as its most promising untested thread (+4.4 to +5.0 versus
other structure signals, strengthening with the floor). Tested: median expectancy **−0.0364R**
over 142 strategies, **37% profitable**. Against `structure_trend`@240 it is +1.061 over 15
cells but that decomposes to **IS −0.876 / OOS +1.919**, and per disjoint slice it is negative
on 5/5 symbols in the oldest, positive on 4/5 in the middle, negative on 4/5 in the newest —
**5+/5− across OOS cells**. A comparator artefact plus one favourable period.

### Look-ahead guard, stated explicitly because the whole study is about timing

All swing reads advance a pointer only while `confirmed_index <= i`; every event is stamped
`bar.end_ts` rather than `bar.ts`; all cross-timeframe comparison is on wall-clock closes, never
indices. `_audit_causality` re-derives every array from `bars[:i+1]` and compares against the
full-series value: **0 mismatches, causal on 10/10** (5 symbols × 2 timeframes). Entries fill at
the next bar's open.
