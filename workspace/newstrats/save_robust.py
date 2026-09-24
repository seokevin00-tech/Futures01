import sys, json
sys.path.insert(0, '/home/user/Futures01'); sys.path.insert(0, '/home/user/Futures01/workspace/studies')
import toolkit as T
SC = '/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad'
stress = json.load(open(f'{SC}/stress.json'))
rates = json.load(open(f'{SC}/rates.json'))

checks = {
 "look_ahead_bias": dict(status="CHECKED - clean", how=(
   "Three independent guards. (1) leadlag._audit_causality re-derives the break-direction and "
   "bars-since-onset arrays from bars[:i+1] and compares with the full-series value at i, at 10-12 "
   "probe points per series: 0 mismatches on MGC/MES/NQ/MNQ/MCL at both 60m and 240m, verdict CAUSAL "
   "on 10 of 10. (2) All swing reads go through TimeframeFrame._last_high/_prior_high/_last_low/"
   "_prior_low, which _build_swing_pointers fills by walking swings sorted on Swing.confirmed_index "
   "and advancing only while confirmed_index <= i - a 3-bar fractal at i is invisible until i+3. "
   "(3) Cross-timeframe reads go through SymbolFrame._build_alignment, which maps a base bar to the "
   "last COMPLETED higher-timeframe bar; every event in the measurement is stamped with bar.end_ts, "
   "not bar.ts, so the 240m bar spanning 12:00-16:00 is compared at 16:00 and can never appear to "
   "know something before the 60m bar closing 15:00."),
   result=rates["causality_audit"]),
 "repainting_indicators": dict(status="CHECKED - clean", how=(
   "Swings are the only repainting-capable input here and they are consumed exclusively through "
   "confirmed_index-filtered arrays. Confirmed by the same causality audit: a repainting read would "
   "show up as a truncated-vs-full mismatch and there were none.")),
 "future_data_leakage": dict(status="CHECKED - one residual, quantified", how=(
   "leadlag._history builds its cache from the FULL series rather than per-slice. That cannot leak "
   "the future (every stored value is a function of bars[0..i] only, audited above), but it does give "
   "a sliced backtest MORE warm-up history than it would have live at the very start of a slice. "
   "Direction of the error is toward better-informed early bars, not toward knowing the future.")),
 "engine_fill_realism": dict(status="CHECKED - conservative", how=(
   "Entry fills at the NEXT bar's open, never the signal close. When a bar contains both stop and "
   "target the stop wins. Gaps fill at the open, not at the stop price. This is the engine's "
   "documented behaviour and was not modified.")),
 "understated_costs": dict(status="CHECKED - not the cause", how=(
   "Re-ran all 7 lead-lag arms over 15 disjoint cells with slippage tripled (base 0.5 -> 1.5 ticks, "
   "stop extra 1.0 -> 2.0, volatility coefficient 0.6 -> 1.2, thin-book 1.0 -> 2.0). Median expectancy "
   "moves by -0.011R to -0.030R and the percentage of profitable strategies falls 4-11 points. Every "
   "arm was already negative at base costs, so costs are not what makes these arms lose - but a "
   "1-bar-earlier entry is also a worse-liquidity entry in reality and that is NOT modelled, which "
   "means the early arms are if anything flattered here."),
   result=stress),
 "parameter_sensitivity": dict(status="CHECKED - FAILS for the one surviving result", how=(
   "Median expectancy across the freshness parameter K (early break at most K bars old) is "
   "K=0 -0.0357, K=1 -0.0456, K=3 -0.0122, K=unbounded -0.0434. Non-monotone and non-smooth: the "
   "K=3 arm - the only comparison in this study that survived both the 3-slice and 6-block designs - "
   "sits on an isolated bump, not on a ridge. A real effect varies smoothly in its parameter. The "
   "lateness parameter N behaves better (N=0 -0.0767, N=4 -0.1041, monotone worse) but N=8 never "
   "clears the 20-trade floor and N=16 fires on ~0.0% of bars, so the late end is untestable.")),
 "insufficient_sample": dict(status="CHECKED - reported at both floors", how=(
   "Every census is reported floor-free and at floor 20. The floor changes conclusions: "
   "EARLY_fresh3 vs CONFIRM_late0 goes from +3.93 to +5.98 all-cells and +2.50 to +4.20 OOS, and "
   "CONFIRM_late8 vanishes entirely (175 strategies at floor 0, zero at floor 20). Per-cell trade "
   "counts: median 40-143 for the 60m arms, 40 for bos@240, 7 for late8.")),
 "data_mining_bias": dict(status="CHECKED - nothing clears deflation", how=(
   "168 strategy specs screened per cell -> free_t(168) = 3.204. 84 in the walk-forward -> "
   "free_t(84) = 2.977. 33 arm comparisons -> free_t(33) = 2.644. The best out-of-sample result in "
   "the whole study is EARLY_fresh3 vs CONFIRM_late0 at Stouffer +4.202 over 10 OOS cells (3-slice "
   "design) and +2.678 over 22 cells (6-block design). The 6-block figure is BELOW free_t(84). "
   "Nothing is announced as clearing deflation.")),
 "survivorship_bias": dict(status="PARTIAL - noted, not eliminated", how=(
   "All five contracts are continuous front-month CSVs. Roll adjustment is whatever the loader "
   "inherited; a roll gap inside a slice would show as a spurious structure break on both "
   "timeframes at once, which would inflate the measured co-state rate rather than the lead. Not "
   "separately audited. No contracts were dropped for poor performance - MGC and MCL, the two "
   "independent units, are reported even where they contradict the index complex.")),
 "degeneracy": dict(status="CHECKED - two conditions dropped", how=(
   "Firing rates on 4,600-5,000 bars per symbol: ltf_break_first 17.8-20.9%, fresh0 5.6-6.9%, "
   "fresh1 9.1-11.1%, fresh3 13.8-16.1%, htf_confirms_late0 6.9-9.3%, late4 3.2-5.0%, late8 "
   "0.6-1.2%, late16 0.00-0.04%. late16 is DEGENERATE and was dropped; late8 never clears the trade "
   "floor. ltf_break_first_fresh6 is 98.6-99.0% identical to ltf_break_first and htf_confirms_late2 "
   "is 98.1-98.4% identical to late0 - both dropped as duplicates."),
   result=rates["rates"]),
 "duplicate_of_existing_condition": dict(status="CHECKED", how=(
   "No lead-lag condition exceeds 86.7% verdict agreement with break_of_structure@60. One "
   "deliberate identity: htf_broken_now IS break_of_structure bound to 240m - identical trade sets, "
   "15,698 trades each - which is the intended proof that leadlag.break_dir() reproduces the "
   "library predicate exactly, not an accidental clone.")),
 "pooling_inflation": dict(status="AVOIDED", how=(
   "Every significance figure is a per-cell rank-sum z combined by Stouffer over cells. No trade "
   "pools, no cross-cell strategy pools. Where the two designs disagree - EARLY vs "
   "CONFIRM_htf_broken_now was -4.61 over 10 OOS cells in the 3-slice design and -0.903 over 30 "
   "cells in the 6-block design - the finer design is taken as the answer and the coarser one is "
   "downgraded.")),
 "nested_windows": dict(status="AVOIDED", how=(
   "T.disjoint_slices only. No trailing windows, nothing nested. 3-slice and 6-block designs are "
   "different partitions of the same history, and that is stated rather than counted as replication.")),
}

T.save("s_leadlag_robustness",
  "Anti-overfitting audit for the lead-lag study",
  "Which of look-ahead, repainting, future leakage, unrealistic fills, understated costs, parameter sensitivity, small samples, data-mining bias, survivorship and degeneracy were checked, and what did each find?",
  dict(checks=checks,
       verdict="NOTHING from this study is live-eligible.",
       live_eligible=[],
       reason=("Two independent bars are failed. (1) Level: every arm's median expectancy is "
               "negative at both trade floors and under tripled slippage. (2) Deflation: the single "
               "surviving relative result sits below free_t for the finer design, and it fails the "
               "parameter-sensitivity check - non-monotone in K. The measurement study s_leadlag "
               "stands on its own as a finding; the strategies built on it do not.")),
  headline=("Ten anti-overfitting checks run, eight clean, two adverse - and the two adverse ones kill "
            "the only surviving result. Look-ahead: 10 of 10 causality probes CAUSAL across 5 symbols "
            "x 2 timeframes, 0 mismatches. Costs: tripling slippage moves median expectancy by only "
            "-0.011R to -0.030R, so costs are not why these arms lose. Parameter sensitivity: FAILS - "
            "median expectancy across the freshness parameter is -0.036, -0.046, -0.012, -0.043 for "
            "K = 0, 1, 3, unbounded, so the winning K=3 arm is an isolated bump, not a ridge. "
            "Deflation: free_t(84) = 2.977 against a best six-block out-of-sample Stouffer of +2.678. "
            "Nothing is live-eligible."),
  caveats=["Roll-adjustment quality of the continuous front-month CSVs was not separately audited.",
           "The early-entry arms are flattered by the fill model: entering a bar before the crowd is in reality a thinner-book entry and the slippage model does not know that.",
           "The parameter-sensitivity failure is reported for the freshness parameter K; the lateness parameter N is monotone but its interesting end (N>=8) is untestable for lack of trades."])
print("saved")
