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

---

## `ict_sweep_mss` — the sequence: sweep → structure shift → retrace into the imbalance

ICT's central claim, tested as an ordered chain rather than a conjunction. **Verdict: the
sequence is real, common, correctly detectable without look-ahead, and worthless.**

This is the strongest negative in the programme, because unlike the earlier retractions **the
population was healthy** — the idea failed on its merits rather than starving.

### The sequence is not rare — the old starvation was an architecture bug

Per symbol at 60m over 10.5 months: **1,893–2,014 sweeps** (37.9–40.3% of bars) → 264–370 reach
an opposing MSS within 5 bars → 136–184 leave a displacement imbalance → **77–106 retrace into
it** (1.2–2.1% of bars, 7–9 per month). Built as an ordered chain that is **57–68 trades per
cell**, against the sweep family's previous **zero rule sets ever reaching 20 trades**.

Cross-check that validates the detector: PDH+PDL sweep rate 12.2% against the library's measured
`prior_day_sweep` 12.4%.

### The first link does not cause the second

Unadjusted, a sweep raises P(opposing MSS within 5 bars) by a median **×1.46** (Stouffer
z = +17.6). That is **tautological**: a sweep bar closes back *inside* the level, so it is
already nearer the swing the MSS must break.

Stratify on that distance in ATR and the effect collapses:

```
Mantel-Haenszel OR vs bars that ran the same level:   1.08
vs bars that ran NO level:                            0.86   (Stouffer z = -4.03)
```

**At matched distance, sweeping liquidity predicts the structure shift slightly *worse* than
never touching a level at all.**

### Ablation: no stage adds anything

Six arms, matched bars/exit/costs, per-cell paired sign test (never `T.ab`, per D28). **Not one
of 8 comparisons × 3 windows reaches |z| = 1.96.** `full − sweep_mss`: −0.079R IS → +0.096R OOS.
`full − mss_only`: −0.091R IS → +0.064R OOS. **Sign flips IS→OOS in 6 of 8.** Absolute level is
negative: full chain −0.045R per cell, 8/12 cells losing.

### The wrong order trades the same — and ranks first

MSS-then-sweep (the deliberately wrong order): median Δ +0.11R full sample (n.s.), +0.08R OOS.
**The top two rows of the worker's own durability ranking are the wrong-order control**
(MNQ 60m, +0.39R, t = 2.10). Best t anywhere 2.10 against free_t = 3.84.

A **placebo** — the same entries displaced 5 bars — returns **+0.08R, beating the real entries.**

### Tuning the timing costs more than the effect is worth

19 variants of gap and retrace tolerance: none positive OOS, 18 of 19 negative IS. Entry counts
scale **~linearly in N** (28/41/56/81/118/138/174) — independent events, not a mechanism.
Walk-forward: tuning N on history returns **−0.11R per fold** against −0.00R for never tuning,
with a hindsight oracle at +0.18R — so **data-mining bias ≈ 0.29R per fold, larger than any
effect in the study.**

### Execution and cost reality

Gross +0.052R → net −0.017R → at 4× slippage −0.062R: **the entire gross edge sits inside the
cost envelope**, and D13 means even that understates it. 41% of entries land more than one
gap-height from the edge, so an idealised resting limit at the imbalance was tested — it is
**worse**: −0.158R over 741 trades, 1/12 cells positive, win rate falling 39.9% → 19.8%.

### Audit

**0 mismatches in 1,494 prefix-versus-full entry comparisons** across 8 cells × 5 truncations.
`.events` never read (D27); swings filtered on `confirmed_index`; FVGs dated to their third bar;
the MSS reference pinned to swings formed *before* the sweep so it cannot drift. Max Jaccard
against 12 library conditions: **0.055** — not a duplicate. Sample bound stated honestly: 20–68
trades per cell, SE ≈ 0.2R, so effects under ~0.4R are unresolvable here.

Multi-timeframe made it worse: 240m levels into a 60m chain gives −0.199R against −0.060R,
**8/8 cells negative**, starved to 9–17 trades per cell.

---

## `orb_define` — what ORB is, and why the library never measured it

**Definitions.** Fetched (WebSearch snippets; several primary sources egress-blocked): Crabel's
ORB places a resting stop a "stretch" (≈0.8 × the 10-day range) beyond the opening range high or
low, with NR4/NR7 compression as the conditioning filter; Zarattini/Barbon/Aziz 2024 (SSRN
4729284) test a 5-minute ORB on ~7,000 stocks 2016–23 and claim Sharpe ≈2.4, finding 5m the best
OR length; Holmberg/Lönnbark/Lundström find some ORB returns above zero. **Recalled, not
fetched:** the axis taxonomy itself (close vs touch entry, retest vs immediate, stop at opposite
edge / mid / ATR, targets in range-heights vs R vs session extremes, the fade variant).
**No performance claim above is independently verified**, and all are on equities, not futures.

**The repair.** See D30 and D39. The headline is that the library's ORB was never an ORB: no
first-break gate and no session gate, so it fires on 18.5% of MES 15m bars — about 7 per day,
including overnight bars hours after the close — and on MCL it is a **Jaccard 0.993 duplicate of
`initial_balance_break`** off a range that is silently built 60 minutes wide instead of 30.

**The way out of the sample trap.** `data/{MES,MGC,MNQ}_1m.csv` holds 404k–470k genuine 1-minute
bars covering **352 RTH trading days** (2019-01-01 → 2020-05-14) and resamples to any grid,
including MGC's 08:20 open — about 350 ORB signals per symbol, enough for a 60/40 split and
disjoint slices. Exposed as `orb.long_series(sym, tf)`. It contains the Feb–Mar 2020 crash and is
a different era from `csv/raw`, so **the two cannot be pooled**. Worker 2 found and used the same
archive independently.

**Corrected conditions** are in `workspace/newstrats/orb.py`: `orb_break_{5,15,30,60}m`,
`orb_touch_*` (Crabel's stop-order entry), `orb_retest_*`, `orb_fade_*`. Unresolvable cells return
`no()` and are counted in `orb.INERT`, so **"unmeasurable" can never again be read as "no
signal"** — which is precisely the mistake that hid this for weeks.
