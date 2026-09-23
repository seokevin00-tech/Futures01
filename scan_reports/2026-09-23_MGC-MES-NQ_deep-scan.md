# Scan report — MGC, MES, NQ

**Date:** 2026-09-23
**Scope:** 3 symbols × 6 timeframes × 4 window lengths = 39 cells
**Screened:** 554,441 strategies · **Qualifying (≥15 trades, clones collapsed):** 3,564 · **At ≥20 trades:** 2,452
**Data:** `csv/raw/` (user-supplied CME micro futures, read-only, `chattr +i`)
**Reproduce:** `SCAN_OUT=workspace/focus/cells PYTHONPATH=. xargs -a workspace/focus/cells.txt -L1 -P 10 sh -c 'PYTHONPATH=. python workspace/bigscan/cell.py $0 $1 $2 6000'`
**Commit:** `ef10c64`

---

## Verdict first

**Nothing in this scan is statistically tradeable.** 0 of 2,452 strategies clear the
multiple-testing threshold, and 0 clear even the most generous denominator defensible.

What the scan *does* establish, with reasonable confidence:

1. **MES is materially better than MGC on this data.** MES TREND is profitable in 96.4% of
   its 137 strategies; MGC has no group with positive median expectancy at all.
2. **MES 1h TREND is the single most durable result** — positive across all three window
   lengths, strengthening as the window shortens.
3. **Sub-hourly timeframes are structurally unprofitable** for all three symbols. At 5
   minutes, 11–16% of strategies make money.

---

## Correction to a previously reported figure

Earlier work on this desk reported **correlation(win rate, expectancy) = +0.037** and
concluded that win rate does not predict profit. That figure came from a 32-strategy
sample and **does not hold**. On this scan's 2,452 strategies:

```
correlation(win rate, expectancy)      = +0.745
correlation(payoff ratio, expectancy)  = +0.329

of   138 strategies with win rate >= 60%:  95.7% profitable
of 1,311 strategies with win rate <= 45%:  24.1% profitable
```

The broad 11-symbol sweep run the same day gave **+0.718** on 3,453 strategies, pooled and
median-within-cell. Win rate *does* predict profit on this data. The mechanism: payoff
ratios cluster tightly (median 1.22, IQR 0.95–1.58), so with reward:risk roughly fixed,
expectancy is close to a linear function of win rate.

**The +0.037 figure should not be cited.**

---

## Method, and the three guards that change the answer

**Ranked by reward-for-risk, not win rate.** Win rate is reported because it was asked
for, with a 95% Wilson interval attached — a 75% win rate on 20 trades is statistically
compatible with 53%.

**Clones collapsed on realised trades before counting.** Filter variants that veto nothing
produce an identical trade list. Uncollapsed, a "top five" is routinely the top one printed
five times. In this scan 1,112 of the qualifying strategies were duplicates of another.

**Deflation charged against the full screened population.** Searching *n* strategies buys
roughly `sqrt(2·ln n)` free t-units. At 13,000–19,700 screened per cell that is **4.36
t-units** — anything below it is indistinguishable from noise. Deepening the search from
2,400 to 6,000 per template *raised* this bar from 4.13 to 4.36. That is the honest cost of
looking harder.

**Look-ahead discipline.** Higher timeframes are visible only when closed. The 4-hour
series is resampled from hourly with `keep_partial=False`; a half-formed final 4h bar has
not closed, and reading it would be look-ahead on the newest bar — precisely where a scan
is most tempted to find an edge.

---

## Results by symbol and timeframe

Median expectancy in R, with the share of strategies that are profitable.

| symbol | 1d | 4h | 1h | 30m | 15m | 5m |
|---|---|---|---|---|---|---|
| **MES** | +0.008 (65%) | −0.052 (23%) | +0.015 (56%) | **+0.110 (84%)** | +0.072 (66%) | −0.218 (11%) |
| **MGC** | −0.003 (47%) | +0.023 (61%) | −0.017 (40%) | −0.137 (9%) | **−0.232 (9%)** | −0.185 (15%) |
| **NQ** | — | −0.020 (39%) | **+0.045 (70%)** | — | — | −0.260 (16%) |

MGC at 30m and 15m is the worst cell in the scan: fewer than one strategy in ten makes
money. MES 30m is the best, but see the caveat under *Limitations* — it rests on a single
58-day window.

## Best strategy group per symbol

| symbol | group | strategies | median exp | median win | % profitable |
|---|---|---|---|---|---|
| **MES** | TREND | 137 | **+0.252** | 53.3% | **96.4%** |
| MES | MOMENTUM | 376 | +0.014 | 46.0% | 54.5% |
| MES | MULTI_TIMEFRAME | 65 | +0.007 | 46.4% | 55.4% |
| **NQ** | TREND | 26 | +0.081 | 43.0% | 92.3% |
| NQ | OPENING_RANGE | 18 | +0.070 | 52.4% | 66.7% |
| NQ | VOLUME_PROFILE | 108 | +0.046 | 42.2% | 68.5% |
| **MGC** | MULTI_TIMEFRAME | 96 | **−0.040** | 42.9% | 37.5% |
| MGC | LIQUIDITY | 31 | −0.053 | 45.5% | 25.8% |
| MGC | TREND | 52 | −0.056 | 39.7% | 30.8% |

**MGC's best group loses money on median.** Every one of its groups does.

---

## Table 1 — Highest win rate (min 20 trades)

| sym | tf | window | group | n | win | 95% CI | exp | R:R | t |
|---|---|---|---|---|---|---|---|---|---|
| MES | 1h | 6mo | TREND | 21 | 76.2% | 55–89 | +0.342 | 1.72 | 2.73 |
| MES | 15m | ~2mo | TREND | 20 | 75.0% | 53–89 | +0.356 | 0.84 | 1.84 |
| MES | 1d | 9mo | MULTI_TIMEFRAME | 27 | 74.1% | 55–87 | +0.034 | 0.47 | 0.54 |
| MES | 15m | ~2mo | TREND | 27 | 74.1% | 55–87 | +0.849 | 1.40 | 2.99 |
| NQ | 1h | 6mo | MOMENTUM | 23 | 73.9% | 54–87 | +0.167 | 0.73 | 1.26 |
| MES | 1d | 9mo | MULTI_TIMEFRAME | 30 | 73.3% | 56–86 | +0.007 | 0.38 | 0.10 |
| MES | 15m | ~2mo | MOMENTUM | 26 | 73.1% | 54–86 | +0.422 | 0.91 | 2.03 |
| MGC | 1h | 9mo | VOLUME_PROFILE | 28 | 71.4% | 53–85 | +0.295 | 0.82 | 1.53 |
| MGC | 1h | 9mo | VWAP | 21 | 71.4% | 50–86 | +0.019 | 0.43 | 0.14 |
| MES | 15m | ~2mo | VOLUME_PROFILE | 24 | 70.8% | 51–85 | +0.312 | 1.20 | 2.00 |
| MES | 1h | 3mo | VOLUME_PROFILE | 20 | 70.0% | 48–85 | +0.448 | 3.19 | 3.04 |
| MES | 30m | ~2mo | MOMENTUM | 20 | 70.0% | 48–85 | +0.287 | 1.29 | 1.84 |
| MES | 1h | 6mo | TREND | 23 | 69.6% | 49–84 | +0.452 | 2.25 | 2.95 |
| MES | 15m | ~2mo | MOMENTUM | 49 | 69.4% | 55–80 | +0.338 | 1.06 | 2.54 |
| NQ | 1h | 6mo | BREAKOUT | 22 | 68.2% | 47–84 | +0.219 | 1.74 | 2.06 |

**Read row 3 before trusting this table.** MES 1d MULTI_TIMEFRAME wins 74% of the time and
earns **+0.034R** on a 0.47:1 payoff. It is right three times in four and makes almost
nothing. That shape is exactly what a win-rate ranking promotes.

## Table 2 — Highest reward:risk (min 20 trades)

| sym | tf | window | group | n | R:R | win | exp | maxDD | t |
|---|---|---|---|---|---|---|---|---|---|
| MES | 1h | 6mo | MOMENTUM | 24 | 4.00 | 58.3% | +0.235 | 0.5R | 2.36 |
| MES | 30m | ~2mo | TREND | 20 | 3.70 | 60.0% | +0.794 | 3.4R | 3.18 |
| MES | 1h | 3mo | VOLUME_PROFILE | 20 | 3.19 | 70.0% | +0.448 | 0.7R | 3.04 |
| MES | 30m | ~2mo | TREND | 20 | 3.14 | 40.0% | +0.452 | 5.6R | 1.37 |
| MES | 1h | 6mo | MOMENTUM | 25 | 3.13 | 64.0% | +0.212 | 0.7R | 2.22 |
| MES | 4h | 9mo | VWAP | 21 | 3.10 | 42.9% | +0.479 | 2.1R | 1.57 |
| NQ | 1h | 3mo | MOMENTUM | 22 | 2.93 | 18.2% | **−0.208** | 7.1R | −0.71 |
| MES | 4h | 3mo | VWAP | 22 | 2.83 | 40.9% | +0.050 | 0.5R | 0.80 |
| MES | 30m | ~2mo | TREND | 23 | 2.80 | 34.8% | +0.249 | 8.9R | 0.81 |
| MES | 1h | 6mo | MOMENTUM | 20 | 2.80 | 65.0% | +0.204 | 0.5R | 2.24 |
| MGC | 4h | 9mo | MOMENTUM | 22 | 2.75 | 40.9% | +0.377 | 3.7R | 1.26 |
| NQ | 1h | 6mo | MOMENTUM | 23 | 2.67 | 52.2% | +0.364 | 1.4R | 2.02 |
| MES | 1h | 9mo | MOMENTUM | 20 | 2.66 | 40.0% | +0.202 | 2.4R | 0.80 |
| NQ | 1h | 9mo | MOMENTUM | 20 | 2.62 | 35.0% | +0.172 | 2.2R | 0.62 |
| MES | 15m | 1mo | LIQUIDITY | 20 | 2.55 | 25.0% | **−0.076** | 5.4R | −0.29 |

**Row 7 is the mirror trap.** A 2.93:1 payoff that loses 0.21R per trade at an 18% win
rate, with a 7.1R drawdown. Sorting by ratio promotes lottery tickets.

## Best per timeframe × window

| tf | window | best win rate | best R:R |
|---|---|---|---|
| 1d | 9mo | MES MULTI_TIMEFRAME 74% n=27 e=+0.03 | MGC VWAP 2.1:1 w=33% e=+0.00 |
| 1d | 6mo | MES VWAP 52% n=33 e=+0.04 | MGC MOMENTUM 1.8:1 w=43% e=+0.02 |
| 4h | 9mo | MGC MOMENTUM 64% n=22 e=+0.11 | MES VWAP 3.1:1 w=43% e=+0.48 |
| 4h | 6mo | MES VWAP 60% n=52 e=+0.01 | MES VWAP 2.5:1 w=30% e=+0.04 |
| 4h | 3mo | NQ MOMENTUM 50% n=20 e=−0.05 | MES VWAP 2.8:1 w=41% e=+0.05 |
| 1h | 9mo | MGC VOLUME_PROFILE 71% n=28 e=+0.29 | MES MOMENTUM 2.7:1 w=40% e=+0.20 |
| 1h | 6mo | MES TREND 76% n=21 e=+0.34 | MES MOMENTUM 4.0:1 w=58% e=+0.24 |
| 1h | 3mo | MES VOLUME_PROFILE 70% n=20 e=+0.45 | MES VOLUME_PROFILE 3.2:1 w=70% e=+0.45 |
| 1h | 1mo | MES VOLUME_PROFILE 55% n=20 e=+0.18 | MES VOLUME_PROFILE 1.5:1 w=55% e=+0.18 |
| 30m | ~2mo | MES MOMENTUM 70% n=20 e=+0.29 | MES TREND 3.7:1 w=60% e=+0.79 |
| 30m | 1mo | MES MOMENTUM 65% n=20 e=+0.26 | MES VWAP 1.8:1 w=23% e=−0.32 |
| 15m | ~2mo | MES TREND 75% n=20 e=+0.36 | MES LIQUIDITY 2.5:1 w=30% e=+0.04 |
| 15m | 1mo | MES MOMENTUM 62% n=21 e=+0.10 | MES LIQUIDITY 2.6:1 w=25% e=−0.08 |
| 5m | 1mo | MES VOLUME_PROFILE 67% n=24 e=+0.20 | NQ MEAN_REVERSION 2.3:1 w=30% e=−0.02 |

---

## The finding that matters most: cross-window stability

A single top row is a draw from the right tail. The same combination staying positive
across *different window lengths* is the closest thing here to evidence. Four qualify:

```
MES  1h  TREND            274d: +0.21   180d: +0.26   90d: +0.30
NQ   1h  OPENING_RANGE    274d: +0.07   180d: +0.09   90d: +0.26
NQ   1h  VOLUME_PROFILE   274d: +0.04   180d: +0.09   90d: +0.07
NQ   1h  VWAP             274d: +0.02   180d: +0.07   90d: +0.04
```

**MES 1h TREND** is the strongest single result in this scan: 137 strategies, 96.4%
profitable, positive in all three windows, strengthening as the window shortens. Nothing on
MGC replicates like this.

---

## Deflation: what did not survive

| | strict (all screened) | generous (distinct survivors per cell) |
|---|---|---|
| threshold | ~4.36 t-units | ~2.9–3.4 t-units |
| **clearing it** | **0 of 2,452** | **0 of 2,452** |

Closest candidates:

```
MES  30m   58d  TREND           n=20  t=3.18  vs generous 3.24   gap -0.05
MES   1h   90d  VOLUME_PROFILE  n=20  t=3.04  vs generous 3.04   gap -0.00
MES  15m   58d  TREND           n=27  t=2.99  vs generous 3.33   gap -0.33
MES   1h  180d  TREND           n=23  t=2.95  vs generous 3.35   gap -0.40
```

The top two sit essentially *on* the line — and both rest on 20 trades, which is the
reporting floor and exactly where a marginal t-statistic is least trustworthy.

---

## Limitations

1. **Data span caps what "9 / 6 / 3 / 1 month" can mean.** Only 1h, 4h and 1d support all
   four windows. 30m and 15m have 58 days total; 5m has 25–27 days. Cells are labelled by
   real length (`~2mo`, `1mo`) rather than borrowing a label the data cannot support.
2. **MES 30m and 15m results rest on one window.** They cannot be cross-window validated
   and should be treated as a single regime, not a replicated edge.
3. **Daily bars cannot answer the short windows.** 30 calendar days is ~20 daily bars,
   below the 120-bar minimum, so 1d only reports 9mo and 6mo.
4. **NQ is a full-size contract**, not a micro. On a $50,000 account it is a far larger
   risk unit than MNQ. The strategy statistics transfer; the position sizing does not.
5. **No walk-forward or period replication was run here.** This is a screen. The tests that
   have killed every prior candidate on this desk have not yet been applied to these.
6. **No live data.** The scan ends where the CSVs end (2026-09-21/22).

---

## Recommended next step

Take **MES 1h TREND** through disjoint-period replication and anchored walk-forward. It is
the only candidate with cross-window support, and those are the tests that decide whether
it is an edge or a survivor of search. Leave MGC alone on this evidence — not because it
cannot be traded, but because 554,441 tested strategies found no way to.

---

## Cell inventory

| cell | window span | bars | screened | cleared | distinct |
|---|---|---|---|---|---|
| MES_1440m_274d | 2025-12-22..2026-09-21 | 187 | 19,215 | 167 | 56 |
| MES_1440m_180d | 2026-03-25..2026-09-21 | 124 | 19,215 | 5 | 2 |
| MES_240m_274d | 2025-12-22..2026-09-22 | 1,148 | 19,215 | 133 | 46 |
| MES_240m_180d | 2026-03-26..2026-09-22 | 762 | 19,215 | 79 | 25 |
| MES_240m_90d | 2026-06-24..2026-09-22 | 385 | 19,215 | 21 | 7 |
| MES_240m_30d | 2026-08-23..2026-09-22 | 128 | 19,215 | 0 | 0 |
| MES_60m_274d | 2025-12-22..2026-09-22 | 4,259 | 13,235 | 717 | 372 |
| MES_60m_180d | 2026-03-26..2026-09-22 | 2,832 | 13,235 | 561 | 273 |
| MES_60m_90d | 2026-06-24..2026-09-22 | 1,429 | 13,235 | 191 | 101 |
| MES_60m_30d | 2026-08-23..2026-09-22 | 478 | 13,235 | 15 | 8 |
| MES_30m_58d | 2026-07-26..2026-09-22 | 1,876 | 13,235 | 433 | 188 |
| MES_30m_30d | 2026-08-23..2026-09-22 | 955 | 13,235 | 80 | 36 |
| MES_15m_58d | 2026-07-26..2026-09-22 | 3,750 | 13,235 | 506 | 252 |
| MES_15m_30d | 2026-08-23..2026-09-22 | 1,909 | 13,235 | 164 | 80 |
| MES_5m_27d | 2026-08-26..2026-09-22 | 4,877 | 13,235 | 352 | 191 |
| MGC_1440m_274d | 2025-12-22..2026-09-21 | 187 | 19,514 | 76 | 30 |
| MGC_1440m_180d | 2026-03-25..2026-09-21 | 124 | 19,514 | 14 | 7 |
| MGC_240m_274d | 2025-12-22..2026-09-22 | 1,148 | 19,514 | 130 | 51 |
| MGC_240m_180d | 2026-03-26..2026-09-22 | 762 | 19,514 | 52 | 21 |
| MGC_240m_90d | 2026-06-24..2026-09-22 | 385 | 19,514 | 12 | 5 |
| MGC_240m_30d | 2026-08-23..2026-09-22 | 128 | 19,514 | 0 | 0 |
| MGC_60m_274d | 2025-12-22..2026-09-22 | 4,256 | 13,073 | 490 | 216 |
| MGC_60m_180d | 2026-03-26..2026-09-22 | 2,827 | 13,073 | 375 | 164 |
| MGC_60m_90d | 2026-06-24..2026-09-22 | 1,430 | 13,073 | 189 | 82 |
| MGC_60m_30d | 2026-08-23..2026-09-22 | 479 | 13,073 | 8 | 3 |
| MGC_30m_58d | 2026-07-26..2026-09-22 | 1,878 | 13,073 | 272 | 175 |
| MGC_30m_30d | 2026-08-23..2026-09-22 | 956 | 13,073 | 67 | 32 |
| MGC_15m_58d | 2026-07-26..2026-09-22 | 3,752 | 13,073 | 280 | 185 |
| MGC_15m_30d | 2026-08-23..2026-09-22 | 1,910 | 13,073 | 80 | 39 |
| MGC_5m_27d | 2026-08-26..2026-09-22 | 4,892 | 13,073 | 253 | 136 |
| NQ_240m_274d | 2025-12-18..2026-09-18 | 1,148 | 19,738 | 112 | 46 |
| NQ_240m_180d | 2026-03-22..2026-09-18 | 772 | 19,738 | 55 | 24 |
| NQ_240m_90d | 2026-06-21..2026-09-18 | 389 | 19,738 | 12 | 5 |
| NQ_240m_30d | 2026-08-19..2026-09-18 | 129 | 19,738 | 0 | 0 |
| NQ_60m_274d | 2025-12-18..2026-09-18 | 4,256 | 12,962 | 569 | 294 |
| NQ_60m_180d | 2026-03-22..2026-09-18 | 2,873 | 12,962 | 404 | 193 |
| NQ_60m_90d | 2026-06-21..2026-09-18 | 1,449 | 12,962 | 128 | 57 |
| NQ_60m_30d | 2026-08-19..2026-09-18 | 478 | 12,962 | 6 | 4 |
| NQ_5m_25d | 2026-08-24..2026-09-18 | 4,877 | 12,962 | 265 | 158 |

_Row-level results for all 3,564 qualifying strategies: `workspace/focus/all_rows.json` (gitignored run artefact; regenerate with the command at the top)._
