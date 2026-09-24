# ORB and ICT — what they are, and whether they pay

**Date:** 2026-09-24
**Scope:** 6 studies, 5 symbols (MGC, MES, NQ, MNQ, MCL), 60m/15m/5m plus a 352-session
1-minute archive
**Detail:** `workspace/studies/ORB_ICT_FINDINGS.md`; raw studies in `workspace/studies/out/`
**Defects found:** D30–D39 in `workspace/studies/DEFECTS.md`

---

## Answer

**Neither gives good reward for risk on this data. No algorithm was written, and I recommend
against writing one.**

ORB: **median expectancy −0.092R per trade against a median 32R max drawdown.** 1,920
configurations, 194,005 trades, 18.8% profitable, best t = 2.137 against a 3.888 free-search
threshold. Negative at **zero transaction cost**, so it is an absent edge rather than a cost
problem.

ICT: **0 live-eligible across every concept tested.** Order blocks and fair value gaps fail at
bar level before an exit is chosen. The central sweep→shift→retrace sequence is real and common
and adds nothing. Kill zones are trade thinning. OTE is an existing condition under another name,
already refuted.

---

## What they are

**ORB — Opening Range Breakout.** Define a range over the first N minutes after the session open;
trade a break of its high or low. Toby Crabel's original places a resting stop about 0.8 × the
10-day range beyond the edge, conditioned on NR4/NR7 range compression. The serious published
evidence — Zarattini, Barbon & Aziz 2024 (SSRN 4729284), claiming Sharpe ≈2.4 with 5 minutes the
best range length — is **on roughly 7,000 equities, not futures**, and is not independently
verified here.

**ICT — Inner Circle Trader / Institutional Trading Concepts**, Michael Huddleston's methodology.
(You wrote "ITC"; this is a two-character transposition and everything else fits. Say so if you
meant something else.) Its components: order blocks, fair value gaps, liquidity sweeps, market
structure shifts, kill zones, Optimal Trade Entry, premium/discount arrays.

**Four ICT concepts are not falsifiable as stated** and were not tested: Power of Three labels
every day post-hoc; the Judas swing requires the day's true direction, knowable only ex post; MSS
has two incompatible published detectors; the mitigation block is not separable from the order
block in any source retrieved.

---

## The evidence that settles it: five placebos, and each one wins

Every concept was tested against a control designed so that passing it would be impossible if
there were a real edge. **All five placebos matched or beat the real signal.**

| concept | placebo | result |
|---|---|---|
| ORB | **yesterday's** opening range | **beats today's** on all three deep symbols (profitable-config share 38→51% MGC, 18→37% MNQ) |
| FVG / order block | random zones matched on ATR-distance, width, side, horizon | 87.3–90.1% real vs **87.0–89.8% sham** (z = −0.47); order blocks touched **less** than sham (z = −2.05) |
| sweep sequence | the same entries **displaced 5 bars** | **+0.08R, beats the real entries** |
| sweep sequence | the deliberately **wrong order** (shift, then sweep) | supplies the **top two rows** of its own durability ranking |
| kill zones | every 24th bar **by index**, zero clock content | **mean +2.25, max +4.77** — any hour-shaped filter scores positive |

Separately, of the **5 most durable of 387** ranked order-block rule sets, **three contain a
placebo condition**, and `placebo + ema_stack` ranks **2nd overall**.

The lesson generalises past these two methodologies: **a league table of the best rule sets
containing a condition measures the search, not the condition.**

---

## The three most interesting individual findings

**1. The famous 80–90% fill rate is true and carries no information.** Price returns to fill an
FVG 87–90% of the time — and returns to a random zone 87–90% of the time. It is price wandering
about one ATR.

**2. ICT's central claim fails on its merits, not for lack of data.** The sweep→shift→retrace
sequence completes 77–106 times per symbol in 10.5 months (7–9 a month) — a healthy population,
unlike every earlier failure in this project, which starved. Unadjusted, a sweep raises the odds
of an opposing structure shift by **×1.46 (z = +17.6)**. That is tautological: a sweep bar closes
back *inside* the level, so it is already nearer the swing the shift must break. Stratify on that
distance and the odds ratio collapses to **1.08**; against bars that touched **no** level it is
**0.86**. At matched distance, sweeping liquidity predicts the shift **slightly worse than never
touching a level at all.**

**3. Kill-zone hours have more range and no more direction.** Directional efficiency is flat
across the entire clock (0.37–0.50 every hour). Since the exit is ATR-scaled, more range buys no
R. And ICT's own labels come out inverted: **21:00 ET — its designated non-entry window — is
+4.52**, while **08:00 ET, inside its New York kill zone, is the worst hour of the day at −3.41**.

---

## Why nobody could have found this before: both were unmeasurable here

Neither methodology had ever actually been tested in this library. Not because nobody tried —
because the machinery could not express them.

- **The opening range was never constructed** at 60m or 240m. The rule, now stated exactly: an
  L-minute range resolves on a T-minute frame **iff `T | L` and `T | RTH-open-in-minutes`.**
  09:30 = 570 is not divisible by 60; 08:20 = 500 is not divisible by 15, 30 or 60. **4-hour ORB
  is arithmetically impossible.** `snap.opening_range` was None on 4,990 of 5,000 MES 60m bars.
- **The shipped ORB condition was not an ORB.** No first-break gate, no session gate — it fired on
  **18.5% of MES 15m bars, ~7 per day**, including overnight bars hours after the close. On MCL it
  silently built a 30-minute range **60 minutes wide** and was a **Jaccard 0.993 duplicate of
  `initial_balance_break`**.
- **The combinator cannot express a sequence at all.** `min_signals=2` requires two conditions on
  the *same bar*, and a sweep and its consequence never co-occur. Every "A then B" idea is
  inexpressible — which is why the sweep family showed "0 rule sets ever reached 20 trades" and
  was misread as the idea failing.
- **`session_extreme_sweep` is self-referential** — it compares each bar against a session high
  that includes that bar, so exceeding it is arithmetically impossible. Its 0.61% rate was
  degeneracy, not rarity.
- **SUPPLY_DEMAND's emptiness was its detector.** The library's zone needs a base, a departure and
  an ATR gate and fires on 0.56–1.38% of bars; ICT's base-free order block fires on **40.5–51.5%**.

---

## The methodological finding worth more than either strategy

One worker found a look-ahead bug **in its own code**: a retest limit fills mid-bar at the bar's
extreme, and crediting that *same bar's* opposite extreme as a target hit cannot be symmetric —
because the extreme **is** the entry, so the stop cannot fire. It manufactured a **+0.354R cluster
at t = 5.19** that survived the 60/40 split **and all three disjoint slices**. 51% of its winners
"hit target" on the entry bar.

> **Resampling cannot detect a bias whose sign is always favourable. Only auditing the fill model
> can.**

Out-of-sample testing, disjoint periods and walk-forward all pass a bias present in every period.
This is the one failure mode the entire programme's methodology is blind to, and it is now a
required check.

---

## Also closed: the last outstanding lead

`fib_golden_pocket` vs `fib_shallow_retrace` was the only untested lead surviving the 22-study
programme. **Refuted — and the original claim was wrong in both directions.** Neither band
predicts anything at bar level (best |t| = 1.80); at 240m **zero matched pairs reach 20 trades in
any disjoint slice**; and the reported 60m *loss* actually has the **opposite sign** (+4.01),
carried by MES alone while MNQ contradicts its own index twin.

OTE is that same condition: **Jaccard 0.938–1.000, direction-aware identical** — the seventh
duplicate condition found in this project.

---

## Recommendation

Do not trade either on this evidence, and do not spend effort on an implementation. Both fail at
bar level, before any exit choice, so no exit tuning rescues them; five placebos beat them; and
nothing anywhere clears its deflation threshold.

What *is* worth keeping from the effort: the repaired ORB module (`workspace/newstrats/orb.py`)
now counts unresolvable cells in `orb.INERT`, so **"unmeasurable" can never again be read as "no
signal"** — the mistake that hid all of this. And ten defects (D30–D39) are now recorded, three of
which silently corrupted or zeroed results rather than merely wasting search budget.
