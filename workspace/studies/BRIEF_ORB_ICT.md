# Research brief — ORB and ICT

Six agents researching **Opening Range Breakout (ORB)** and **ICT / Institutional Trading
Concepts** (the user wrote "ITC"; treat ICT as the reading unless evidence says otherwise, and
flag the ambiguity in your findings).

The deliverable the user asked for: **what these are, whether they give good reward for risk,
and if so, an algorithm.** Answer the first two honestly before anyone writes the third.

## What is already known, so nobody re-discovers it

Several ICT and ORB primitives are **already in this library and have already been measured**.
Most failed — but several failed for reasons that were *bugs*, which is why a fresh test is
legitimate rather than redundant:

| existing condition | ICT/ORB equivalent | what was measured |
|---|---|---|
| `opening_range_breakout` | ORB entry | Fires on **4 of 4,256** NQ 60m bars. Of 47 floored OPENING_RANGE strategies, **one** was an actual OR breakout. Effectively untested. |
| `fvg_nearby` | Fair value gap / imbalance | Exists, never isolated |
| `prior_day_sweep`, `overnight_sweep`, `session_extreme_sweep` | Liquidity sweep / stop run | **0 rule sets ever reached 20 trades**, every cell, every symbol. Untested, not refuted. |
| `zone_touch`, `fresh_zone_approach` | Order block / mitigation block | SUPPLY_DEMAND: zero strategies reach 20 trades; `fresh_zone_approach` fires on 0.5–3.8% of bars |
| `break_of_structure` | Market structure shift (MSS/BOS) | +4.4 to +5.0 IS at 240m, **retracted** — fails OOS, 37% profitable |
| `fib_golden_pocket` (0.618–0.786) | Optimal Trade Entry (OTE) | Fib conditions are a **clean negative**; `fib_sr_confluence` fires on 48.6–81.0% of all bars |
| `opening_drive_window`, `power_hour`, `avoid_lunch` | Kill zones | No hours filter improves expectancy; all four neutral-to-harmful |

Also relevant: 240m OPENING_RANGE and 240m REVERSAL are **broken, not empty** — see
`workspace/studies/DEFECTS.md` D21/D22. Daily OPENING_RANGE is legitimately inapplicable.

**So the honest prior is that these will fail.** That is not a reason to test them sloppily — it
is a reason to test them *properly*, because the previous attempts did not.

## Method — non-negotiable, and one of these is new

Read `workspace/studies/BRIEF_STRUCTURE.md` for the full method. The additions:

1. **Use PAIRED tests, not `T.ab`, whenever your arms are variants of the same rule sets.**
   `T.ab` runs an unpaired rank-sum over strategies. Partner variants sharing 50–90% of their
   trades survive clone collapse and are still correlated, and the rank-sum treats them as
   independent: **measured inflation of |z| by ~3.3× (range 1.7–15.3), reaching |z| = 8.7 on
   null data**, with the sign flipping OOS in 7 of 11 comparisons. Build matched arms
   (same bars, same base rule set, same exit, one thing swapped) and use a **per-cell paired
   sign test on Δexpectancy, combined by Stouffer**. This is defect D28 and it is mine.
2. **Out-of-sample or it does not count.** Disjoint slices (`T.disjoint_slices`) or a 60/40
   temporal split. Never the nested 274/180/90 windows. The programme's cleanest result was a
   condition at **z = +12.20 in sample** and significantly negative OOS in 6 of 12 cells.
3. **Report the firing rate of every condition you build**, before any performance number.
   Under 1% cannot support a strategy; over 95% is not a condition.
4. **Check bar-level identity against existing conditions.** Five conditions in this project have
   turned out to be duplicates of another under a different name. Report Jaccard overlap.
5. **Floor-free censuses.** The 20-trade floor selects on exit geometry, not signal quality.
6. Defaults that matter: `T.make_strategy` already sets `exit_at_session_close=False` (its True
   default reduces every 4h and daily trade to one bar) and `rth_only=False` (that flag costs
   every group a 2–12× population at 240m). ORB work will need RTH — turn it on deliberately and
   say you did.

## On sourcing

Try `WebSearch`/`WebFetch` for the documented definitions and any published evidence. If network
egress is blocked (it has been for data vendors in this environment), fall back to your own
knowledge — both methodologies are extensively documented — and **state explicitly which you
used.** Do not present recalled material as if it were fetched.

**Treat promotional claims as claims, not evidence.** ICT material in particular circulates with
strong performance assertions and no auditable record. Your job is to test the mechanics, not to
repeat the marketing. If a concept is stated in a way that cannot be falsified, say so — that is
a finding.

## Saving

`T.save(study_id, ...)` the moment each piece of analysis finishes, not at the end. A previous
run lost 18 studies to agents who computed everything and saved nothing.

## Reply

Under 25 lines. What it is, the firing rates, the paired OOS result, and a plain verdict on
reward-for-risk. A clean negative is a real finding.
