# MOMENTUM / TREND / PULLBACK / MULTI_TIMEFRAME — backtest seat

Symbol: **MNQ only**. Nothing here is claimed for MES, MGC or CL; those are
separate universes and were not tested. Data: `synthetic_series("MNQ",
days=120, seed=N, end_date=date(2026,3,17))` for N in {5, 11, 23, 41, 67} —
five independent price paths, not five symbols on one path.

Code pinned to **5b3ae0c** (a `git archive` export to
`/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad/mt/pinned`).
See *Reproducibility hazard* at the bottom: the working tree moved twice while
this ran and the newer HEAD does not generate the same strategy set.

---

## Verdict

**You are right to disbelieve it. I could not replicate it, and I am
publishing nothing as live-eligible.**

The 1,186-strategy sweep on seed 5 reproduces exactly — I got the same 1,186
strategies, the same `MNQ-60m-69a9cbce1cce` at +0.2371R / PF 1.99 / n=39, the
same two n=120 rows at +0.2172R. Then I ran the identical protocol on four more
price paths. The walk-forward came back:

| seed | 5 | 11 | 23 | 41 | 67 |
|---|---|---|---|---|---|
| OOS expectancy | **+0.1436R** | −0.0551R | −0.0294R | −0.0963R | **+0.0544R** |
| efficiency | 0.89 | −0.35 | −0.51 | −0.96 | 1.08 |
| `is_credible` | yes | NO | NO | NO | yes |

Two of five. Your own rule: a result that appears on 2 of 5 paths is noise.
Pooled over all five paths the out-of-sample record is 1,555 trades at
**+0.0149R, t = +0.77, PF 1.051**. Across paths the OOS expectancy is
+0.0034R ± 0.0857 (t = +0.08 on n = 5) and efficiency is +0.030 ± 0.809
(t = +0.07). The seed-5 result is one draw from a distribution centred on zero.

Three further things I found that you should know before the next sweep:

1. **The "three MOMENTUM rows at the top" are one hypothesis, not three.** All
   three are the same rule set — `break_of_structure + price_above_ema200 +
   regime_matches_direction + rsi_directional` on 60m with the ATRx1 → 1.5/3/5R
   exit. They differ only by optional filter. The bare row and the
   `outside_news_blackout` row produce a **bit-identical trade list** (verified
   timestamp by timestamp: the news filter removed 0 of 120 trades). The
   n=39 row is the same rule set with `volume_surge` bolted on.

2. **n=39 is really n=25.** Fourteen of the 39 "trades" (36%) are entered at the
   16:00 open on a signal from the last RTH bar and closed at
   `SESSION_CLOSE` on that same one-minute bar. Mean net −0.0224R — they are
   commission, not trades. Pooled over five paths: 70 of 189 (37%) are these
   one-minute session-boundary round trips. The real sample is 25, which is
   below `MIN_TRADES_FOR_RANK` = 30 and below the desk's own
   `AccountConfig.min_backtest_trades` = 40.

3. **What varies across paths is the regime drift, not the strategy.** The
   generator's trending regimes carry a ±0.020-vol-unit drift. Measured on the
   classifier's own labels, the forward-2h return of "trade with the regime
   label" is +0.158, +0.201, −0.058, −0.078, +0.079 ATR on seeds 5/11/23/41/67 —
   pooled +0.0649 ATR at t = +1.72 over 786 observations. That per-path number
   correlates +0.50 with the per-path walk-forward OOS expectancy. The MOMENTUM
   family is riding whatever the regime term happened to do on that particular
   120-day draw. It is not an edge; it is a property of one sample of one
   generator, and it has no counterpart in a real market.

Deflation kills everything regardless. Nothing in my families reaches a
deflated expectancy above zero on any path, against any honest trial count.

---

## Seed replication

364 strategies (my four families, generated identically to the parent sweep and
filtered by group), 120 days, MNQ, walk-forward anchored, 4 folds, top_k = 5,
`min_trades_is` = 20.

| seed | WF OOS trades | OOS exp R | OOS t | IS exp R | efficiency | selection stability | `is_credible` | best full-period row (n≥30) |
|---:|---:|---:|---:|---:|---:|---:|:--|:--|
| 5  | 340 | **+0.1436** | +3.03 | +0.1613 | **0.89** | 0.51 | **yes** | `MNQ-60m-69a9cbce1cce` +0.2371R n=39 |
| 11 | 367 | −0.0551 | −1.32 | +0.1582 | −0.35 | 0.27 | NO | `MNQ-60m-0d18e3f39ab2` +0.1328R n=104 |
| 23 | 263 | −0.0294 | −0.63 | +0.0579 | −0.51 | 0.53 | NO | `MNQ-5m-04daefecc6d1` +0.1991R n=44 |
| 41 | 196 | −0.0963 | −2.19 | +0.0998 | −0.96 | 0.31 | NO | `MNQ-15m-0d9750668f5d` +0.0549R n=50 |
| 67 | 389 | **+0.0544** | +1.55 | +0.0502 | **1.08** | 0.67 | **yes** | `MNQ-60m-0d18e3f39ab2` +0.0892R n=115 |

Pooled across the five paths: **1,555 OOS trades, +0.0149R, t = +0.77,
win 47.1%, avg win +0.650R, avg loss −0.551R, PF 1.051, payoff 1.18.**

In-sample expectancy is positive on 5/5 paths (+0.1613, +0.1582, +0.0579,
+0.0998, +0.0502). Out-of-sample it is positive on 2/5. That gap *is* the
overfitting, measured.

Fold detail — the seed-5 story does not repeat even within seeds:

```
seed  5: f0 IS +0.1356 OOS +0.0836 | f1 IS +0.1468 OOS +0.2600 | f2 IS +0.1701 OOS +0.0439 | f3 IS +0.1657 OOS +0.2998
seed 11: f0 IS +0.1109 OOS +0.2288 | f1 IS +0.1516 OOS -0.1121 | f2 IS +0.3807 OOS -0.7528 | f3 IS +0.0997 OOS +0.0287
seed 23: f0 IS +0.0918 OOS -0.2372 | f1 IS +0.0354 OOS +0.1992 | f2 IS +0.0637 OOS -0.2716 | f3 IS +0.0671 OOS +0.1538
seed 41: f0 IS +0.0912 OOS -0.0596 | f1 IS +0.0598 OOS +0.0908 | f2 IS +0.0922 OOS -0.2860 | f3 IS +0.1982 OOS -0.2472
seed 67: f0 IS +0.0754 OOS -0.0507 | f1 IS +0.0503 OOS +0.0359 | f2 IS +0.0466 OOS +0.0474 | f3 IS +0.0551 OOS +0.0902
```

Seed 11 fold 2 is the shape to remember: the training window looked like the
best in the whole study at +0.3807R, and the very next unseen segment paid
**−0.7528R**. Selection stability on that seed was 0.27 — folds 1→2 and 2→3
shared *zero* strategies out of ten.

---

## The 60m MOMENTUM result, examined

`MNQ-60m-69a9cbce1cce` — MOMENTUM, 60m, no confirmation timeframes.
Signals: `break_of_structure`, `price_above_ema200`, `regime_matches_direction`,
`rsi_directional`. Filters: `volatility_normal`, `volume_surge`.
Exit: `ATRx1 → 1.5/3/5R`, scale (0.4, 0.3, 0.3), breakeven at 1.5R,
`time_stop_bars` 120, RTH only.

### Is it one lucky run of trades? No — it is three fat trades.

Seed 5, n = 39, total +9.249R:

| drop the best | n | expectancy | total R | t | % of all profit removed |
|---:|---:|---:|---:|---:|---:|
| 0 | 39 | **+0.2371R** | +9.25R | 1.56 | — |
| 1 | 38 | +0.1887R | +7.17R | 1.28 | 22% |
| 2 | 37 | +0.1386R | +5.13R | 0.97 | 45% |
| **3** | **36** | **+0.0910R** | +3.27R | **0.66** | **65%** |
| 5 | 34 | +0.0042R | +0.14R | 0.03 | 98% |

Top five R: 2.08, 2.04, 1.85, 1.57, 1.56. Worst five: −1.03, −1.03, −1.02,
−1.02, −1.02. Wins 19 at +0.978R, losses 20 at −0.466R. Drop the best three and
expectancy falls below the desk's `min_expectancy_r` = 0.08 gate; drop five and
it is zero.

### Longest winning streak versus a coin

Longest win streak **3**, longest loss streak 5. A fair coin with p = 0.487 over
39 trials produces a mean longest win run of **4.50** (p95 = 8), and
P(longest run ≥ 3) = **0.945**. So the sequence is *less* streaky than chance.
The "lucky run" hypothesis is wrong and I am recording that as a finding: the
result is not a run, it is concentration in trade *size*.

### Which bars it fires on

Entry hour (ET), seed 5: **10:00 → 22 trades, 11:00 → 3, 16:00 → 14.** That is
the whole distribution. On a 60m grid with `rth_only`, the strategy has three
live moments a day and uses two of them.

The 16:00 bucket is not trading. Those 14 fills come from a signal on the final
RTH bar, fill at the 16:00 open, and hit `exit_at_session_close` on that same
one-minute bar: median holding time 0 minutes, mean net **−0.0224R**. Pooled
over five paths: 70 of 189 trades, all `SESSION_CLOSE`, all entry hour 16,
mean −0.0161R, while the 119 genuine trades average +0.0732R.

Seed 5 split: 14 padding trades at −0.0224R, **25 real trades at +0.3825R**.
So the headline is a 25-trade sample dressed as 39. The sample-size penalty in
`robust_score` is being paid against an inflated count.

Other slices, pooled over five paths (n = 189):

- session: RTH_OPEN +0.045R (n=93), RTH_MORNING +0.173R (n=26), RTH_CLOSE −0.016R (n=70)
- regime: TREND_DOWN +0.114R (n=103), TREND_UP −0.049R (n=86)
- weekday: TUE +0.226R (46), THU +0.039R (32), WED +0.037R (45), MON +0.014R (37), FRI −0.216R (29)
- direction (seed 5): SHORT +0.312R (n=24), LONG +0.118R (n=15); on seed 11 both are negative

Seed 5 monthly expectancy: 2025-10 +0.083 (6), 11 +0.449 (6), 12 +0.265 (7),
2026-01 +0.166 (7), 02 +0.048 (7), 03 +0.452 (6). Six to seven trades a month —
no month carries a rankable sample.

### The three top rows are one hypothesis

- `MNQ-60m-0d18e3f39ab2` (bare) and `MNQ-60m-05b65efba451` (+`outside_news_blackout`)
  produce an **identical trade list** on seed 5 — 120 trades, same timestamps,
  same +0.2172R, same t = 2.59. The news filter removed nothing. Two rows,
  one observation.
- `MNQ-60m-69a9cbce1cce` is the same rule set + `volume_surge`. 25 of its 39
  entries occur at timestamps that also appear in the 120-trade parent. Within
  the 120, the `volume_surge` subset averages **+0.1945R** and the remainder
  **+0.2231R** — the filter selected the *worse* half. A permutation test
  (20,000 random 25-subsamples of the 120) gives **p = 0.558** that a random
  subsample does at least as well. The +0.2371 versus +0.2172 "improvement" is
  a re-shuffled trade path, nothing more.

### Does it replicate?

| seed | n | exp R | PF | t | win% | maxDD R | maxCL |
|---:|---:|---:|---:|---:|---:|---:|---:|
| 5 | 39 | **+0.2371** | 1.99 | +1.56 | 48.7 | 3.13 | 5 |
| 11 | 41 | **−0.1240** | 0.64 | −0.99 | 29.3 | 8.45 | 4 |
| 23 | 33 | +0.1037 | 1.53 | +0.80 | 57.6 | 3.44 | 3 |
| 41 | 36 | +0.0017 | 1.01 | +0.01 | 47.2 | 3.48 | 6 |
| 67 | 40 | −0.0017 | 0.99 | −0.01 | 57.5 | 3.36 | 3 |

Pooled n = 189, **+0.0401R, t = +0.67**. Positive on 3/5 paths, and one of
those three is +0.0017R. Its in-sample t on the path it was found on was 1.56 —
it never had the significance to survive a 1,186-strategy search in the first
place.

---

## Sweep results

364 strategies × 5 price paths = **1,820 strategy-path evaluations, 9,573
trades**, plus a paired no-confirmation arm (11,790 trades). Timeframes 5 / 15 /
60 with the default confirmation map (5m → 15m+60m, 15m → 60m, 60m → none).

### Family × timeframe, rows clearing n ≥ 30, pooled over 5 paths

| family | tf | rows | trades | trade-weighted exp | rows with exp > 0 |
|:--|---:|---:|---:|---:|---:|
| MOMENTUM | 5m | 15 | 1,174 | +0.0449R | 60.0% |
| MOMENTUM | 15m | 15 | 1,193 | +0.0452R | 93.3% |
| MOMENTUM | 60m | 30 | 2,996 | +0.0555R | 70.0% |
| TREND | 15m | 30 | 1,717 | −0.0364R | 53.3% |
| **all** | | **90** | **7,080** | **+0.0297R** | |

### Two of my four families are not rankable at all

| family | strategy-paths | zero-trade | max n | median n | rows reaching n ≥ 30 |
|:--|---:|---:|---:|---:|---:|
| MOMENTUM | 450 | 326 (72%) | 171 | 0 | 60 |
| TREND | 450 | 291 (65%) | 73 | 0 | 30 |
| PULLBACK | 460 | 408 (89%) | **5** | 0 | **0** |
| MULTI_TIMEFRAME | 460 | 222 (48%) | **25** | 1 | **0** |

**PULLBACK produced 94 trades in total across 460 strategy-path evaluations.**
Its most active member fired five times in 120 days. MULTI_TIMEFRAME never got
a single member to 30 trades on any path. I am not ranking either family, and I
am not reporting a "best PULLBACK" — there is nothing to rank. The
`trend + meanreversion` required-group pair is close to mutually exclusive in
practice, and that is the finding.

### Does multi-timeframe alignment actually improve outcomes?

Paired test, same rule set with and without confirmation timeframes, 5m and 15m
primaries only (60m has no confirmation in either arm so it cannot be paired):

- 67 paired rule-set/path cells with n ≥ 20 in both arms
- with confirmation: mean expectancy **+0.0200R**
- without: **+0.0114R**
- paired difference **+0.0086R, t = +0.61**, and **34 of 67** pairs favour
  confirmation — a coin flip

Per family (n ≥ 10 both arms): MULTI_TIMEFRAME +0.0226R (t = +0.34, 33/44),
TREND −0.0307R (t = −1.28, 34/60), MOMENTUM +0.0433R (t = **+3.71**, but only
**15/30** pairs favour confirmation — the mean is carried by a handful of large
differences while the sign test is exactly 50/50, so I am not claiming it).

**Answer: no measurable benefit.** Higher-timeframe confirmation did not
improve outcomes in my families on MNQ on this data. It is an assumption that
did not pay.

### Walk-forward selection: what the folds actually picked

```
seed  5: MOMENTUM-5m 9, MOMENTUM-60m 8, MOMENTUM-15m 3   overlaps 3/7, 3/7, 4/6
seed 11: MOMENTUM-60m 12, TREND-15m 5, MOMENTUM-5m 1, MOMENTUM-15m 1   overlaps 4/5, 0/10, 0/10
seed 23: MOMENTUM-15m 11, MOMENTUM-60m 5, MOMENTUM-5m 2   overlaps 2/6, 3/5, 4/6
seed 41: MOMENTUM-60m 7, MOMENTUM-15m 4, MULTI_TIMEFRAME-5m 3, MOMENTUM-5m 1   overlaps 2/3, 1/7, 1/9
seed 67: MOMENTUM-60m 17, MOMENTUM-15m 2, MOMENTUM-5m 1   overlaps 4/6, 4/6, 4/6
```

Selection stability 0.51 / 0.27 / 0.53 / 0.31 / 0.67. On seeds 11 and 41 the
selector changed its mind completely between consecutive folds. A selector that
disagrees with itself has not found an edge.

### Monte Carlo against the real $50,000 account

Trailing $5,000 failure threshold, 250-trade horizon, 4,000–5,000 bootstrap
paths.

| series | n | risk/trade | P(ruin) | P(profit) | p05 equity | maxDD p95 |
|:--|---:|---:|---:|---:|---:|---:|
| 60m MOMENTUM n=39, seed 5 | 39 | $300 | 0.0% | 100.0% | $60,477 | 9.8R |
| 60m MOMENTUM n=39, seed 5 | 39 | $375 | 0.6% | 100.0% | $63,013 | 9.8R |
| 60m MOMENTUM n=120, seed 5 | 120 | $300 | 0.1% | 100.0% | $59,061 | 10.1R |
| WF OOS, seed 5 only | 340 | $300 | 0.9% | 99.6% | $54,058 | 12.9R |
| WF OOS, seed 5 only | 340 | $375 | 4.1% | 99.6% | $54,097 | 12.9R |
| **WF OOS, all 5 paths** | **1,555** | **$300** | **20.1%** | **62.6%** | **$45,400** | **23.6R** |
| **WF OOS, all 5 paths** | **1,555** | **$375** | **36.3%** | **62.6%** | **$45,114** | **23.6R** |

This is the single most important row in the study. Bootstrap the seed-5 path
alone and the account is safe (0.9% ruin). Bootstrap the honest five-path
out-of-sample record and it fails **one time in five** at $300 a trade and
**more than one time in three** at the $375 ceiling. The comfort in the first
row is entirely an artefact of resampling the one path that worked.

---

## What I withheld and why

Eighteen distinct strategy ids cleared n ≥ 30 with positive expectancy on at
least one price path. **I published none of them.** Pooled statistics across all
five paths, with deflation against 1,820 hypotheses (364 rule sets × 5 paths;
√(2 ln 1820) = 3.875):

| sid | family | tf | paths +ve | pooled n | pooled exp | pooled t | deflated t |
|:--|:--|---:|---:|---:|---:|---:|---:|
| `MNQ-60m-0d18e3f39ab2` | MOMENTUM | 60 | 4/5 | 553 | +0.0910R | +2.36 | −1.52 |
| `MNQ-60m-05b65efba451` | MOMENTUM | 60 | 4/5 | 553 | +0.0902R | +2.34 | −1.53 |
| `MNQ-15m-0d9750668f5d` | MOMENTUM | 15 | 4/5 | 251 | +0.0613R | +1.11 | −2.77 |
| `MNQ-5m-04daefecc6d1` | MOMENTUM | 5 | 3/5 | 217 | +0.0460R | +0.85 | −3.03 |
| `MNQ-5m-1a052d02097b` | MOMENTUM | 5 | 3/5 | 479 | +0.0452R | +1.18 | −2.70 |
| `MNQ-5m-368ed3faea53` | MOMENTUM | 5 | 3/5 | 478 | +0.0441R | +1.15 | −2.73 |
| `MNQ-60m-12505ae32188` | MOMENTUM | 60 | 4/5 | 756 | +0.0435R | +2.32 | −1.55 |
| `MNQ-60m-1958d476f6e2` | MOMENTUM | 60 | 4/5 | 756 | +0.0434R | +2.32 | −1.55 |
| `MNQ-15m-2d9fd7873214` | MOMENTUM | 15 | **5/5** | 471 | +0.0409R | +1.18 | −2.70 |
| `MNQ-15m-d8843c569072` | MOMENTUM | 15 | **5/5** | 471 | +0.0409R | +1.18 | −2.70 |
| `MNQ-60m-69a9cbce1cce` | MOMENTUM | 60 | 3/5 | 189 | +0.0401R | +0.67 | −3.21 |
| `MNQ-15m-6f5a4b6fe2f6` | TREND | 15 | 3/5 | 290 | −0.0267R | −0.37 | −4.25 |
| `MNQ-15m-514c9b0435b7` | TREND | 15 | 3/5 | 290 | −0.0267R | −0.37 | −4.25 |
| `MNQ-15m-e45a9435118d` | TREND | 15 | 3/5 | 290 | −0.0268R | −0.37 | −4.25 |
| `MNQ-15m-4385a8b242c2` | TREND | 15 | 3/5 | 290 | −0.0275R | −0.38 | −4.26 |
| `MNQ-60m-ec36dd3f0820` | MOMENTUM | 60 | 2/5 | 189 | −0.0385R | −1.15 | −5.03 |
| `MNQ-15m-9cd51dc08257` | TREND | 15 | 2/5 | 286 | −0.0414R | −0.57 | −4.45 |
| `MNQ-15m-65d42a556417` | TREND | 15 | 2/5 | 271 | −0.0718R | −1.00 | −4.88 |

Reasons, floor by floor:

**Withheld — failed replication across price paths.** The whole seed-5 top of
table. `MNQ-60m-69a9cbce1cce` is −0.1240R on seed 11. `MNQ-5m-1a052d02097b`
(+0.1501R, n=100 on seed 5) is −0.011R on seed 11 and −0.057R on seed 41.
Every TREND row that looked publishable on seed 5 (+0.116R, n=73) is
−0.284R on seed 41 and pooled negative.

**Withheld — n below the floor once padding is removed.**
`MNQ-60m-69a9cbce1cce` at n=39 is 25 real trades plus 14 one-minute
session-boundary round trips. Below `MIN_TRADES_FOR_RANK` = 30 and below the
desk's `min_backtest_trades` = 40 even at face value.

**Withheld — duplicate hypotheses masquerading as corroboration.** Four pairs
in the table above are the same rule set with a filter that removed nothing:
`0d18e3f39ab2`/`05b65efba451`, `2d9fd7873214`/`d8843c569072`,
`1a052d02097b`/`368ed3faea53`, `12505ae32188`/`1958d476f6e2`. Counting them as
separate confirmations would be double counting. The effective candidate count
is 14, not 18.

**Withheld — the one 5/5 candidate still fails deflation.**
`MNQ-15m-2d9fd7873214` (`cvd_directional + imbalance_pullback +
macd_hist_direction + regime_matches_direction`, ATRx1.5 → 1/2/3R, confirmed
on 60m) is the only strategy positive on all five paths at n ≥ 30 each:
+0.0749 / +0.0288 / +0.0490 / +0.0371 / +0.0171R. Pooled n = 471,
**+0.0409R, t = +1.18**. Deflated it is −2.26 t-units against my own 364 and
−2.70 against 1,820. Its Monte Carlo risk of ruin is **9.6%** over 250 trades at
$300 risk, p05 final equity $46,358, p95 max drawdown 19.2R, p95 losing streak
10. A 9.6% chance of blowing the account is not a candidate. It is the best
thing I found and it is still not good enough.

**Withheld — whole families, for want of a sample.** PULLBACK (94 trades across
460 strategy-paths, max 5 per strategy) and MULTI_TIMEFRAME (max 25 per
strategy, never 30). No ranking is possible and none is offered.

**Withheld — regime and session slices.** RTH_MORNING shows +0.173R on the
headline row, entry hour 11:00 shows +0.608R, Tuesday shows +0.226R. Every one
of those is n ≤ 46 pooled across five paths, and the 11:00 bucket is n = 10.
This is exactly the LVN shape you flagged (76.9% at n=13 → 47.9% at n=446) and
I am not reporting any of them as conditions.

---

## Measurements

### Deflation arithmetic, shown

The expected maximum of *n* standard normals is ≈ √(2 ln n); that much apparent
t-statistic is bought by searching, not earned by edge.

```
√(2 ln   364) = 3.4343      my four families, one path
√(2 ln 1,186) = 3.7625      the parent sweep's own strategy count
√(2 ln 1,820) = 3.8747      364 rule sets x 5 price paths (what I searched)
√(2 ln 5,930) = 4.1684      1,186 strategies x 5 price paths (the full honest count)
```

Applied to the pooled walk-forward OOS record:

```
pooled OOS, 5 paths, n = 1,555:   t = +0.77
  t - √(2 ln 1,820) = 0.77 - 3.875 = -3.11     dead
  t - √(2 ln 5,930) = 0.77 - 4.168 = -3.40     dead
```

Applied per path (seat-level, against my own 364 and against the parent's 1,186):

```
seed  5:  t = +3.03   -> -0.41 (vs 364)   -0.73 (vs 1,186)   dead
seed 11:  t = -1.32   -> -4.75            -5.08              dead
seed 23:  t = -0.63   -> -4.06            -4.39              dead
seed 41:  t = -2.19   -> -5.63            -5.95              dead
seed 67:  t = +1.55   -> -1.88            -2.21              dead
```

Note that even the single path that produced `is_credible=True` and the
headline result — seed 5, OOS t = +3.03 — is 0.41 t-units short against my own
364-strategy search and 0.73 short against the 1,186 the parent ran. Your
arithmetic on the original run (observed 2.77 versus 3.763, deficit 0.99) is
confirmed by an independent replication of the same protocol on the same path.

`deflated_expectancy(m, 1186)` returned **exactly 0.0000 for all 40 rows** that
cleared n ≥ 30 in the reproduced parent sweep, my families and everyone else's.
The highest in-sample t anywhere in that sweep was 2.59. The repo's own
deflation function had already rejected the entire table before I started.

### The generator's regime drift (the mechanism)

Forward 2-bar (2 hour) 60m return in ATR units, conditioned on the classifier's
own regime label (computed from trailing bars only, no look-ahead), RTH bars:

| | TREND_UP | TREND_DOWN | RANGE | VOLATILE_EXPANSION |
|:--|---:|---:|---:|---:|
| seed 5 | +0.1988 (81) | −0.1258 (102) | −0.0342 (396) | −0.0694 (121) |
| seed 11 | +0.0776 (64) | −0.2954 (84) | +0.0535 (430) | −0.0374 (126) |
| seed 23 | −0.0671 (63) | +0.0521 (87) | −0.1168 (425) | +0.0412 (128) |
| seed 41 | +0.0611 (80) | +0.2440 (67) | +0.1318 (434) | +0.0151 (125) |
| seed 67 | +0.0418 (95) | −0.1351 (63) | +0.0915 (429) | +0.1338 (116) |
| **pooled** | **+0.0671 (383)** | **−0.0627 (403)** | +0.0266 (2,114) | +0.0155 (616) |

"Trade with the regime label" composite, pooled: n = 786, **+0.0649 ATR,
t = +1.72**. Per path: +0.158, +0.201, −0.058, −0.078, +0.079.
**Correlation with per-path walk-forward OOS expectancy: +0.50** (n = 5,
descriptive only).

The drift is real in the data and weak (t = 1.72 pooled, i.e. not significant),
and its sign flips path to path. `regime_matches_direction` appears in the
headline rule set and in the 5/5 candidate. What the MOMENTUM family is
measuring is a generator parameter, sampled once per price path. This is a
data-generating-process artefact, and it is the reason the family tops a table
on one seed and is negative on the next.

### Full metric block — headline rows, every path

`MNQ-60m-69a9cbce1cce` (MOMENTUM 60m, n≈39):

| seed | n | exp | PF | t | win% | avgW | avgL | payoff | maxDD | avgDD | Sharpe | Sortino | SQN | maxCW | maxCL | MFE | MAE | avg min | L/S | Lexp | Sexp |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:--|---:|---:|
| 5 | 39 | +0.2371 | 1.99 | +1.56 | 48.7 | +0.978 | −0.466 | 2.10 | 3.13 | 0.71 | +0.250 | +0.502 | +1.56 | 3 | 5 | 0.79 | 0.38 | 62 | 15/24 | +0.118 | +0.312 |
| 11 | 41 | −0.1240 | 0.64 | −0.99 | 29.3 | +0.752 | −0.487 | 1.55 | 8.45 | 3.95 | −0.155 | −0.220 | −0.99 | 2 | 4 | 0.48 | 0.46 | 45 | 23/18 | −0.176 | −0.058 |
| 23 | 33 | +0.1037 | 1.53 | +0.80 | 57.6 | +0.521 | −0.463 | 1.13 | 3.44 | 1.07 | +0.140 | +0.244 | +0.80 | 6 | 3 | 0.67 | 0.40 | 61 | 7/26 | −0.003 | +0.132 |
| 41 | 36 | +0.0017 | 1.01 | +0.01 | 47.2 | +0.565 | −0.502 | 1.12 | 3.48 | 1.17 | +0.002 | +0.003 | +0.01 | 3 | 6 | 0.55 | 0.46 | 58 | 18/18 | −0.051 | +0.055 |
| 67 | 40 | −0.0017 | 0.99 | −0.01 | 57.5 | +0.527 | −0.717 | 0.74 | 3.36 | 1.54 | −0.002 | −0.003 | −0.01 | 4 | 3 | 0.71 | 0.45 | 63 | 23/17 | −0.042 | +0.052 |

`MNQ-60m-0d18e3f39ab2` (same rule set, no `volume_surge`, n≈110):

| seed | n | exp | PF | t | win% | avgW | avgL | payoff | maxDD | avgDD | Sharpe | Sortino | SQN | maxCW | maxCL | MFE | MAE | avg min | L/S |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:--|
| 5 | 120 | +0.2172 | 1.86 | +2.59 | 55.8 | +0.843 | −0.574 | 1.47 | 7.95 | 1.74 | +0.237 | +0.455 | +2.59 | 7 | 6 | 0.86 | 0.51 | 78 | 44/76 |
| 11 | 104 | +0.1328 | 1.42 | +1.40 | 47.1 | +0.957 | −0.602 | 1.59 | 5.53 | 2.48 | +0.138 | +0.246 | +1.40 | 4 | 4 | 0.80 | 0.50 | 67 | 58/46 |
| 23 | 103 | +0.0354 | 1.11 | +0.41 | 50.5 | +0.729 | −0.671 | 1.09 | 6.69 | 3.62 | +0.040 | +0.064 | +0.41 | 5 | 5 | 0.77 | 0.55 | 75 | 19/84 |
| 41 | 111 | −0.0313 | 0.92 | −0.35 | 43.2 | +0.851 | −0.703 | 1.21 | 8.43 | 3.99 | −0.033 | −0.051 | −0.35 | 4 | 5 | 0.77 | 0.62 | 74 | 64/47 |
| 67 | 115 | +0.0892 | 1.32 | +1.17 | 57.4 | +0.646 | −0.661 | 0.98 | 3.94 | 1.43 | +0.109 | +0.176 | +1.17 | 9 | 5 | 0.80 | 0.51 | 78 | 69/46 |

`MNQ-15m-2d9fd7873214` (the 5/5 candidate, MOMENTUM 15m, confirm 60m):

| seed | n | exp | PF | t | win% | avgW | avgL | maxDD | avgDD | Sharpe | Sortino | SQN | maxCW | maxCL | MFE | MAE | avg min | L/S | Lexp | Sexp |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:--|---:|---:|
| 5 | 91 | +0.0749 | 1.27 | +0.93 | 56.0 | +0.636 | −0.641 | 3.54 | 1.58 | +0.097 | +0.150 | +0.93 | 7 | 6 | 0.73 | 0.53 | 49 | 42/49 | +0.037 | +0.107 |
| 11 | 81 | +0.0288 | 1.09 | +0.33 | 48.1 | +0.721 | −0.614 | 6.22 | 1.70 | +0.036 | +0.055 | +0.33 | 6 | 9 | 0.70 | 0.53 | 48 | 41/40 | −0.033 | +0.092 |
| 23 | 96 | +0.0490 | 1.18 | +0.65 | 50.0 | +0.640 | −0.542 | 7.58 | 3.68 | +0.067 | +0.104 | +0.65 | 7 | 7 | 0.67 | 0.54 | 45 | 37/59 | −0.065 | +0.121 |
| 41 | 98 | +0.0371 | 1.14 | +0.52 | 51.0 | +0.599 | −0.548 | 6.10 | 2.80 | +0.053 | +0.079 | +0.52 | 6 | 4 | 0.64 | 0.53 | 47 | 51/47 | +0.032 | +0.043 |
| 67 | 105 | +0.0171 | 1.05 | +0.22 | 50.5 | +0.672 | −0.651 | 4.98 | 2.20 | +0.021 | +0.032 | +0.22 | 7 | 6 | 0.67 | 0.55 | 47 | 52/53 | +0.035 | −0.000 |

### Population statistics, unselected

| family | strategy-paths that traded | trades | mean net R | rows n≥30 | of which +ve |
|:--|---:|---:|---:|---:|---:|
| MOMENTUM | 124 | 5,526 | +0.0531 | 60 | 44 (73%) |
| TREND | 159 | 2,355 | −0.0289 | 30 | 16 (53%) |
| PULLBACK | 52 | 94 | +0.0279 | 0 | 0 |
| MULTI_TIMEFRAME | 238 | 1,598 | +0.0929 | 0 | 0 |

By timeframe, no floor: 5m 1,943 trades +0.1086R; 15m 3,918 trades +0.0001R;
60m 3,712 trades +0.0445R. Whole arm A: 9,573 trades, +0.0393R mean.

A whole *unselected* family averaging +0.05R is the tell. If the edge were in
particular rule sets, the population mean would sit near zero and the good ones
would stand out. It does not — the population drifts, which is the regime term
above, and every member inherits it. These trades overlap heavily on the same
bars so the effective sample is far below 9,573 and I attach no t-statistic
to it.

### Cost accounting actually charged

Seed 5, headline row: commission $1.44 per round turn; cost 0.0123R per trade
(range 0.0046–0.0165R); gross expectancy +0.2495R → net +0.2371R. Mean risk
distance 64.20 MNQ points, so one tick (0.25 pt) = 0.0039R.

### Anti-overfitting checklist — what I checked and what I found

| check | result |
|:--|:--|
| **Look-ahead bias** | **Clean.** Signals come from `SymbolFrame.snapshot(i)`; entries fill at bar *i+1*'s open (`FillModel.entry_on_next_open`). Verified by reading `BacktestEngine.run_many` step ordering. |
| **Repainting indicators** | **Clean.** `find_swings` stamps `confirmed_index = i + right`, and `_build_swing_pointers` only advances when `confirmed_index <= i`. `break_of_structure` — the headline's core signal — therefore cannot see a swing before it was confirmable. |
| **Future-data leakage in percentiles** | **Clean.** `TimeframeFrame._percentile_of` appends the current value *after* ranking against the trailing buffer; `percent_rank` uses a trailing window. `volatility_normal` (a filter in every one of my strategies) is not leaking. |
| **Stop/target ambiguity** | **Conservative.** `stop_before_target_in_same_bar = True`; gaps fill at the open (`honour_gaps`). The pessimistic reading is taken. |
| **Costs** | **Charged, slightly understated.** Commission is charged once per round turn per contract (correct). Entry slippage 0.5 tick, stop slippage 1.5 ticks + a volatility term. But (a) `TIME` and `SESSION_CLOSE` exits fill at `bar.close` with **zero** slippage, and they are 30 of 39 exits on the headline row; (b) `SlippageModel.news_extra_ticks = 2.0` exists but the engine never passes `news=True`, so release-window slippage is never charged; (c) target legs fill on a *touch* of the limit, which overstates fill probability. Magnitude of (a): charging one tick on those exits costs 0.0030R, moving the headline from +0.2371R to +0.2342R. Small — it is not the reason the result fails. |
| **Unrealistic fills** | **One artefact found.** 36% of the headline row's trades are a same-bar entry-and-exit at the 16:00 session boundary (14 of 39 on seed 5, 70 of 189 pooled). They cannot express the strategy's thesis and they inflate the trade count that `robust_score`'s sample-size term relies on. |
| **Sample size** | **Failed.** n=39 is 25 real trades. PULLBACK and MULTI_TIMEFRAME never reach 30 at all. |
| **Data-mining bias / deflation** | **Failed, at every honest trial count.** See the arithmetic above. `deflated_expectancy` returns 0.0000 for all 40 rows clearing n≥30 in the parent sweep. |
| **Parameter sensitivity** | **Failed.** Dropping the best 3 of 39 trades takes expectancy from +0.2371R to +0.0910R and t from 1.56 to 0.66. Adding `volume_surge` (the only parameter difference between the n=39 and n=120 rows) selects the *worse* half of the parent's trades, permutation p = 0.558. |
| **Out-of-sample / walk-forward** | **Failed on 3 of 5 paths.** Efficiency 0.89, −0.35, −0.51, −0.96, 1.08. |
| **Selection stability** | **Weak.** 0.51 / 0.27 / 0.53 / 0.31 / 0.67. Two paths had consecutive folds sharing zero selections. |
| **Survivorship bias** | **Not applicable.** One synthetic contract, one continuous series, no universe construction and no delisting. Noted so it is not silently assumed to have been checked. |
| **Data-generating-process artefact** | **Found, and it is the explanation.** Per-regime drift measured at +0.0649 ATR (t = 1.72) pooled, sign varying by path, correlating +0.50 with per-path OOS expectancy. |

### Reproducibility hazard (please read)

The working tree moved under this run. I started at the HEAD I was given,
`5b3ae0c`; partway through, `git log -1` reported `8be8755`, and by the end
`0df691f`. At `8be8755`, `generate_strategies("MNQ", [5,15,60], max_total=1200,
seed=1)` produces **1,176 strategies across 7 groups** (168 each) instead of
**1,186 across 13 groups** — a sibling seat added per-symbol research profiles
(`futures_agents/strategies/profiles.py`, "Treat MNQ, MES and MGC as three
contracts, not one tested three times"), and MNQ's profile enables seven
families. My four are all inside MNQ's seven, so the narrowing does not exclude
this seat's work, but it does change the strategy ids: `MNQ-60m-69a9cbce1cce`
does not exist at that HEAD, and the hypothesis count the deflation term is
charged against is no longer 1,186. Everything in this document was therefore
run against a pristine `git archive 5b3ae0c` export, which reproduces the
parent sweep exactly. I did not modify any source file. The deflation
arithmetic below is stated against 1,186 because that is the search the
headline actually came out of; anyone re-running on the current HEAD should
recount.

I did not publish `strategy_rankings.json`, `performance_db.json` or
`robustness_report.json` to `workspace/strategy_research/`: I was scoped to this
one file and four sibling seats are writing in parallel.

### Tests

```
pinned 5b3ae0c (the tree these results come from):  675 passed in 9.96s
live working tree at 0df691f:                       694 passed in 32.88s
```

No source file was edited. No bug was found that required one — the
session-boundary fills, the `time_stop_bars`-counted-in-base-bars semantics
(a "120-bar" time stop on a 60m strategy fires after 120 *minutes*, i.e. 1.4–1.7
primary bars; median holding is 81 minutes on the headline row) and the dormant
news-slippage term are all behaving as written. They are reported here as
characterisation, not as defects.

---

## Bottom line

**Nothing here is a live edge.** Not the 60m MOMENTUM row, not the 5/5
MNQ-15m candidate, not the family. The top of your table was one price path, one
rule set counted three times, 25 real trades, and three fat winners. Run it on
four more paths and it averages +0.0034R with a standard deviation twenty-five
times larger than its mean, and bootstrapping the honest out-of-sample record
against the real $50,000 account fails it one time in five.
