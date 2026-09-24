# ORB and ICT — findings

Six studies. "ITC" resolves to **ICT** (Inner Circle Trader / Institutional Trading Concepts)
with high confidence — see `ict_define`.

---

## `ict_blocks_fvg` — order blocks and fair value gaps

The two most precisely codeable ICT concepts, and therefore the fairest possible test of the
methodology. **Verdict: a clean negative, and the concepts fail at the bar level before any exit
is chosen — so no exit tuning can rescue them.**

### Order blocks are the most *permissive* ICT condition, not the scarcest

The opposite of what my brief predicted, and it corrects an earlier finding:

| condition | firing rate |
|---|---|
| `ict_ob_return` | **40.5–51.5%** |
| `ict_fvg_return` | **50.3–56.1%** |
| `ict_ob_newest` / `ict_fvg_newest` | 18.3–24.6% / 30.2–34.2% |
| `ict_ob_fresh` / `ict_fvg_fresh` | 5.1–6.4% / 10.6–13.5% |
| library's `fresh_zone_approach` | **0.56–1.38%** |

**So SUPPLY_DEMAND's emptiness was its detector, not the concept.** The library's zone requires a
base, a departure, an ATR gate and freshness; ICT's order block requires none of that. The
earlier conclusion that "the freshness question is unanswerable for lack of sample" was an
artefact of that detector — with a base-free definition there are 191–634 first touches and
1,372–2,180 later touches per cell, 10–100× what the library could supply.

### Nothing is a duplicate — and the reason is definitional, not mechanical

Jaccard against existing conditions: `fvg_nearby` 0.005–0.121, `zone_touch` 0.005–0.051,
`fresh_zone_approach` 0.000–0.030. The worker then built `ict_fvg_libclone`, a deliberate
re-implementation of `fvg_nearby`'s bookkeeping, and got **Jaccard 0.785–0.966**.

So the machinery agrees and the 8× rate gap is purely definitional: **the library declares an FVG
filled at its midpoint and tests the close; the ICT reading keeps it to the far edge and tests
the bar range.** `fvg_nearby` is a far narrower statement than "an FVG is in play", and reading
it as the ICT concept is a misreading.

### The magnet claim is false as an edge

The headline ICT statistic — that price returns to fill these zones 80–90% of the time — is
**reproduced by random zones**. Sham zones matched on ATR-distance, width, side, symbol and
horizon, 12 draws per zone:

```
FVG touched within 40 bars:   87.3-90.1% real  vs  87.0-89.8% sham   Stouffer z = -0.47
Order blocks:                 78.2-82.3% real  vs  79.9-83.1% sham   Stouffer z = -2.05
                                                   (real touched LESS)
```

The famous fill rate is price wandering about 1 ATR. It is true and it is not information.

Reaction after the touch (symmetric 1-ATR barrier race): order blocks −0.93, nothing. FVG **+2.33
in sample, 7/9 cells** — and out of sample that evaporates: **IS Stouffer +3.41, 8/9 cells → OOS
+0.13, 4/9 cells**, with the two largest IS cells (NQ-60m +2.21, MNQ-60m +2.04) both flipping
negative.

### Paired arms: every positive is a loss made smaller

12 base rule sets × 9 cells × matched sham, per-cell sign test on Δexpectancy, Stouffer combined
(never `T.ab`, per D28):

- `ict_ob_fresh`: **IS +0.088R (7/9 cells, +2.89) → OOS −0.064R (2/9, −2.66)** — clean sign flip.
- `ict_fvg_fresh`: IS +0.078R with **9/9 cells** (p=0.0027) → OOS +0.026R, 6/9, +1.45.
- Best OOS survivor `ict_fvg_newest`: +0.022R, 8/9, z=+2.33 — below free_t = 3.48, **and chosen
  after looking at eight**.

**The decisive line: median absolute expectancy is negative in all 16 condition × period rows
after costs (best −0.004R). Every positive Δ is a loss made smaller, never a profit.** Base rule
sets run −0.084R IS and −0.026R OOS net.

### The sharpest artefact in the whole project

Of the **5 most durable of 387** ranked rule sets, **three contain `ict_placebo_ob`** — zones at
random locations, matched only on count and long/short mix. `ict_placebo_ob + ema_stack` on
NQ-60m ranks **2nd overall** (+0.433R OOS, 35 trades, t = +2.12).

This is the cleanest demonstration this project has produced of why a league table is not
evidence: **a ranking of the best rule sets containing a condition measures the search, not the
condition.**

### The audit had power, which is what makes the nulls mean something

- **Positive control for leakage:** peeking 2 bars early lifts the barrier win rate from 45–53%
  to 67–70%, z = +4.2 to +7.0. The harness *can* see leakage, so the null results are real
  absence rather than an insensitive test. This is the check most audits omit.
- Look-ahead and repainting: rebuilding every BarState from series truncated at 35/60/85% gives
  **0 mismatches over ~50,000 bar-states**.
- Parameter sensitivity: a 48-corner order-block grid × 5 cells — **no corner works**, best OOS
  z = +1.90, and sign inconsistent (MCL-60m is 2/48 positive IS but 31/48 OOS).
- Sample size is *not* the problem here: 32–158 trades per arm per cell.
- 141 of 387 rule sets flip expectancy sign IS→OOS; only 131 of 387 are positive OOS.

---

## `orb_test` — does ORB give good reward for risk?

**Verdict: no. Median expectancy −0.092R per trade against a median max drawdown of 32R.**

1,920 configurations, 194,005 trades, floor-free, net of costs, `rth_only` deliberately ON, each
contract's own open (MGC 08:20, MCL 09:00, MES/MNQ/NQ 09:30).

### An ORB entry is not a selective condition

**The opening range is broken in 74–100% of RTH sessions** (median 99.3%; ≥95% in 82 of 128
cells). By this library's own standard — over 95% firing is not a condition — "the range broke"
carries almost no information. Exit-free, median MFE/MAE after the break is **0.80–1.03
range-widths**: no directional information before an exit is even chosen.

### Sample, stated before any performance number

`csv/raw` gives **18–19 RTH sessions per symbol, median 16 trades per configuration** — an
anecdote, exactly as D30 predicted. The worker therefore added `data/*_1m.csv` (Oanda CFD
2019-01→2020-05: real market structure, **not** the futures price, and flagged as such) reaching
**346–352 sessions** for MGC/MES/MNQ. NQ and MCL have no deep archive. Note NQ and MNQ in
`csv/raw` are the same price series from two fetch snapshots.

### Reward for risk, per symbol, never pooled

| deep archive | win | payoff | expectancy | maxDD |
|---|---|---|---|---|
| MGC | 0.416 | 1.266 | **−0.064R** | 15.0R |
| MES | 0.358 | 1.413 | **−0.142R** | 55.2R |
| MNQ | 0.404 | 1.377 | **−0.040R** | 28.9R |

18.8% of configs profitable. **Best t anywhere = 2.137 against free_t(1,920) = 3.888 — zero
clear deflation.** MGC and MCL are the only independent contracts; MNQ/NQ/MES agree by
construction.

### The payoff/win-rate cancellation, reproduced exactly

| stop | win rate | payoff | expectancy |
|---|---|---|---|
| opposite range edge | 44.2% | 1.003 | −0.085R |
| mid-range | 38.6% | 1.397 | −0.087R |
| ATR multiple | 30.4% | **1.900** | −0.108R |

Payoff rises **89%**, win rate falls 13.8 points, expectancy does not improve.
**corr(win, payoff) = −0.745** across configs (−0.93 on MES and MNQ). Reporting payoff alone
would have called this a win — the fourth time this project has caught that trap.

### Out of sample

Paired per-cell sign tests, Stouffer-combined. In sample five of eight axes are significant
(orlen 30−60 at z=−7.16, stop opp−mid +4.00). **Out of sample: 3 of 8 flip sign, 5 unresolved,
zero replicate.** All three disjoint periods negative on all three deep symbols. Of 169 configs
profitable in sample, only **27.8% stay profitable out of sample — worse than a coin.**

### The decisive test: yesterday's range beats today's

Expectancy is negative at **zero transaction cost** (MGC −0.035R, MES −0.096R, MNQ −0.027R), so
this is an absent edge rather than a cost problem. And a placebo using **yesterday's** opening
range *beats* today's on all three symbols:

```
MGC  -0.041R → +0.002R     profitable-config share 38% → 51%
MES  -0.128R → -0.072R                              1% →  8%
MNQ  -0.033R → -0.020R                             18% → 37%
```

**Today's opening range carries nothing that a wrong-day range does not.** Same logic as the
sham-zone test that killed order blocks, and the same answer.

The best single config (MGC 15m, OR60, retest, mid stop, 1R target: n=108, +0.191R, t=2.14) has
20/72 positive neighbours and is negative on MES and MNQ — a spike, not a plateau.
