"""Publish the three deliverables.

Ranked on durability, never on historical profit: expectancy in R, a sample-size penalty, the
t-statistic of the R series, drawdown and consecutive losses.  Nothing here is live-eligible,
and the file says so in the field that matters rather than in a footnote.
"""
import json, math, os, statistics as st, sys
os.chdir('/home/user/Futures01')
sys.path[:0] = ['/home/user/Futures01', 'workspace/studies']
import toolkit as T

SCR = '/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad'
OUT = 'workspace/strategy_research'
L = lambda f: json.load(open(f'{SCR}/{f}.json'))
CEN, PERF, ARM = L('census'), L('perf'), L('armsumm')
ABL, SENS, WF, BIAS, JAC, REG = L('ablation'), L('sens'), L('wf'), L('bias'), L('jaccard'), L('regime')
LIFT2 = L('lift2')
DB = PERF['db']
ARMS = ["sweep_only", "mss_only", "sweep_mss", "mss_fvg_retrace", "full", "wrong_order"]
CELLS = sorted(DB)


def durability(r):
    """Rank score. Expectancy carries the sign; everything else can only take away.

    A profit factor of 1.8 over 18 trades is noise, so the sample-size term is a hard
    multiplier rather than a tiebreak, and drawdown and the worst losing run are charged
    against the score rather than reported beside it.
    """
    if not r or r['n'] < 5:
        return None
    n_pen = min(1.0, math.sqrt(r['n'] / 100.0))          # full credit only at 100+ trades
    dd_pen = 1.0 / (1.0 + max(0.0, r['maxdd']) / 10.0)   # 10R of drawdown halves the score
    cl_pen = 1.0 / (1.0 + max(0, r['max_cons_l'] - 5) / 10.0)
    return round(r['exp'] * n_pen * dd_pen * cl_pen + 0.05 * max(-3, min(3, r['t'])), 4)


rank = []
for c in CELLS:
    sym, tf = c.split('|')
    for a in ARMS:
        for ex in ("anchored", "r2", "struct"):
            f, isw, oos = (DB[c].get(f"{a}|{ex}|{w}") for w in ("full", "IS", "OOS"))
            if not f or f['n'] < 5:
                continue
            rank.append(dict(
                strategy=f"ICT_{a}", symbol=sym, timeframe=int(tf), exit=ex,
                durability_score=durability(f), n=f['n'], expectancy_r=f['exp'],
                t_stat=f['t'], win_rate=f['win'], avg_win_r=f['avg_win'],
                avg_loss_r=f['avg_loss'], profit_factor=f['pf'], rr=f['rr'],
                max_dd_r=f['maxdd'], avg_dd_r=f['avgdd'], sharpe=f['sharpe'],
                sortino=f['sortino'], sqn=f['sqn'],
                max_consec_wins=f['max_cons_w'], max_consec_losses=f['max_cons_l'],
                avg_mae_r=f['mae'], avg_mfe_r=f['mfe'], avg_bars_held=f['bars'],
                avg_minutes_held=f['mins'],
                is_expectancy=isw['exp'] if isw else None, is_n=isw['n'] if isw else None,
                oos_expectancy=oos['exp'] if oos else None, oos_n=oos['n'] if oos else None,
                oos_sign_agrees=(None if not (isw and oos) else
                                 (isw['exp'] > 0) == (oos['exp'] > 0)),
                live_eligible=False))
rank.sort(key=lambda r: (-(r['durability_score'] or -9)))

n_screened = len(rank) + len(SENS)
free_t = T.free_t(n_screened)
best_t = max(r['t_stat'] for r in rank)

rankings = {
  "generated_by": "worker 5 of 6 - ICT sequence (sweep -> MSS -> retrace into the imbalance)",
  "study_id": "ict_sweep_mss",
  "ranked_on": "durability = expectancy_R x sqrt(min(1, n/100)) x drawdown penalty x "
               "consecutive-loss penalty + 0.05 x clipped t. Never on historical profit.",
  "live_eligible": [],
  "live_eligible_reason":
      "Nothing qualifies. Live eligibility required surviving out-of-sample evaluation AND "
      "walk-forward, and the sequence survived neither: the IS/OOS sign of the key ablation "
      "deltas flips in 6 of 8 comparisons, walk-forward tuning of the timing tolerance returns "
      "a median -0.11R per fold, and the full chain loses to a copy of itself displaced 5 bars "
      "(+0.08R placebo vs -0.02R real).",
  "deflation_arithmetic": {
      "rows_ranked": len(rank), "parameter_variants_run": len(SENS),
      "n_screened": n_screened, "free_t_units": round(free_t, 3),
      "best_observed_t": round(best_t, 3),
      "verdict": f"best t = {best_t:.2f} against {free_t:.2f} t-units bought by search alone. "
                 "Nothing clears deflation; nothing comes close."},
  "cells": CELLS,
  "arms": {a: {"description": d} for a, d in [
      ("sweep_only", "ran an obvious high/low and closed back inside it"),
      ("mss_only", "first close beyond the last confirmed swing"),
      ("sweep_mss", "sweep then opposing structure shift within N bars, entry at the break"),
      ("mss_fvg_retrace", "structure shift then retrace into its imbalance, NO sweep"),
      ("full", "sweep -> structure shift -> retrace into the imbalance (the ICT sequence)"),
      ("wrong_order", "structure shift FIRST, then the sweep, then the retrace (control)")]},
  "ranking": rank,
  "caveats": [
      "Every row is one strategy on one cell with one exit. There is no search inside a row, "
      "so a high rank here is a high draw, not a discovery.",
      "Positive rows exist (MCL 240m full chain, +0.15R over 28 trades) and are noise: the "
      "same arm is -0.33R in sample on the same symbol.",
      "MES and MNQ are one index complex. Agreement between them is not replication.",
      "15m cells span 58 days; 60m and 240m span 321-343 days."]}
json.dump(rankings, open(f'{OUT}/ict_sweep_mss_strategy_rankings.json', 'w'), indent=1, default=str)

perf = {
  "schema": "arm x symbol x timeframe x exit x window",
  "study_id": "ict_sweep_mss",
  "floor": "NONE - every cell with 5+ trades is reported; censuses are floor-free entirely",
  "symbols": ["MES", "MNQ", "MGC", "MCL", "NQ*", "ES*"],
  "symbol_note": "NQ and ES appear in the census only, as replicates of the MNQ/MES index "
                 "complex. Every performance number is MES, MNQ, MGC, MCL measured separately; "
                 "nothing measured on one symbol is claimed for another.",
  "timeframes_tested_individually": [5, 15, 30, 60, 240, 1440],
  "timeframes_backtested": [15, 60, 240],
  "timeframe_note": "5m spans 27 days and 1m spans 3.5 days - censused, not backtested. Daily "
                    "is inapplicable: the chain occurs 12-20 times in 7-10 years because "
                    "overnight and session liquidity pools do not exist at that bar size.",
  "multi_timeframe": "NOT claimed. This study tests each timeframe on its own. The lead-lag "
                     "worker already measured a 78-81% false-positive rate for lower-timeframe "
                     "structure breaks leading higher ones; nothing here contradicts or "
                     "confirms that and no MTF group was tested.",
  "census_floor_free": CEN['chain'], "single_pool_firing_rates": CEN['pools'],
  "signal_counts_per_cell": PERF['signals'],
  "per_cell_rows": {c: {k: {kk: vv for kk, vv in r.items() if kk != 'rs'}
                        for k, r in DB[c].items()} for c in DB},
  "arm_summary": ARM,
  "by_session_regime_timeframe_symbol": REG,
  "cost_sensitivity": BIAS['cost'],
  "parameter_sensitivity": SENS,
  "mechanism_test_distance_stratified": {k: {"vs_ran_thru": v["vs_ran_thru"],
                                             "vs_no_level": v["vs_no_level"]}
                                         for k, v in LIFT2.items()}}
json.dump(perf, open(f'{OUT}/ict_sweep_mss_performance_db.json', 'w'), indent=1, default=str)

rob = {
  "scope": "The ICT sequence: liquidity sweep -> market structure shift -> retrace into the "
           "resulting imbalance. Built explicitly as an ordered chain, not as a conjunction of "
           "independently sampled signals.",
  "study_id": "ict_sweep_mss",
  "bottom_line":
      "The sequence is real, common and useless. It occurs 77-106 times per 10.5 months per "
      "symbol at 60m - so the earlier sweep-family starvation was the min_signals=2 "
      "conjunction, not the phenomenon - but the first link does not cause the second, the "
      "chain is not more than its parts, the order does not matter, and the whole thing loses "
      "money net of realistic costs. NOT live-eligible.",
  "checks_performed": BIAS.get('checked', {}),
  "look_ahead_and_repainting": BIAS['repaint'],
  "cost_and_fill_realism": {a: {lab: round(st.median(
      [BIAS['cost'][k]['exp'] for k in BIAS['cost'] if k.endswith(f"|{a}|{lab}")]), 4)
      for lab in ('zero', 'x1', 'x2', 'x4')} for a in ('full', 'sweep_mss', 'mss_only')},
  "bar_level_identity_vs_library": JAC,
  "out_of_sample": {
      "design": "60/40 temporal split plus three contiguous slices per cell; never the nested "
                "274/180/90 windows.",
      "ablation_is_vs_oos": {k: {"median_delta": v["median_delta_exp"],
                                 "sign_z": v["sign_test"]["z"], "stouffer_z": v["stouffer_z"]}
                             for k, v in ABL.items() if k.endswith('|anchored')},
      "sign_flips": "6 of 8 ablation comparisons change sign between IS and OOS."},
  "walk_forward": WF['wf'],
  "statistical_method": {
      "T_ab_used": False,
      "why": "T.ab runs an unpaired rank-sum over strategies and these arms share 50-90% of "
             "their bars. Defect D28 measures that inflating |z| by ~3.3x, to |z| = 8.7 on "
             "null data. Every comparison here is a per-cell paired sign test on "
             "delta-expectancy plus a Stouffer combination of per-cell two-sample z.",
      "independent_unit": "the cell (symbol x timeframe). Trades are never pooled across "
                          "cells for a significance test; the pooled slice tables in the "
                          "performance db are descriptive only.",
      "D27_avoided": "market_structure().events is never read. All structure comes from "
                     "find_swings filtered on confirmed_index, and each chain resets its own "
                     "reference - which is the bug D27 describes."},
  "findings_that_are_negative_and_are_still_findings": [
      "The 2x sweep->MSS lift is geometry. Unadjusted a sweep nearly doubles the odds of an "
      "opposing MSS within 5 bars (Stouffer z = +17.6). Stratify on distance from the close to "
      "the swing the MSS must break and the Mantel-Haenszel OR falls to 1.08; against bars "
      "that ran no level at all the OR is 0.86 with Stouffer z = -4.03.",
      "The wrong order performs the same as the right order (median delta +0.11R, sign z "
      "+1.16, n.s.; OOS +0.08R, z +0.30).",
      "Entries displaced 5 bars later outperform the real entries (+0.08R vs -0.02R).",
      "Walk-forward tuning of the timing tolerance is worse than not tuning (-0.11R vs "
      "-0.00R); the hindsight oracle is +0.18R, so the data-mining bias is ~0.29R per fold.",
      "ICT kill zones are among the worst slices: LONDON -0.149R (n=88), RTH_OPEN -0.096R "
      "(n=34). Consistent with the library's earlier finding that no hours filter helps.",
      "sweep_only is significantly NEGATIVE on its own: median -0.076R over 12 cells, sign "
      "z = -2.89, Stouffer t = -4.29."],
  "defects_touched": {
      "D27": "avoided by construction; the per-chain reference reset is the fix.",
      "D28": "T.ab not used anywhere in this study.",
      "D29": "the 3-bar confirmation lag is why the MSS reference is pinned to swings formed "
             "before the sweep - without that pin the reference drifts to a low made during "
             "the drop and the break becomes unfalsifiable.",
      "NEW - library session_extreme_sweep is self-referential": {
          "detail": "session_levels() builds session_high from bars where b.ts <= cutoff, "
                    "which INCLUDES the bar being tested, so _level_sweep's b.high > "
                    "session_high is arithmetically impossible. Its measured 0.61% firing "
                    "rate is not a rare event, it is a near-degenerate condition.",
          "same_for": "overnight_sweep while price is still in the overnight session; the "
                      "library's 4.7% is an RTH-only rate for that reason.",
          "verified": "my causal reimplementation, using only bars strictly before the test "
                      "bar, fires at 2.7-3.9% (SESH/SESL) and 9.3-12.3% (ONH/ONL) at 60m. "
                      "The PDH/PDL union reproduces the library's 12.4% exactly (12.2%), "
                      "which is the control showing the difference is real and not mine."},
      "NEW - the combinator cannot express a sequence": {
          "detail": "min_signals=2 demands two conditions fire on ONE bar. A sweep and its "
                    "consequence never do. That, not rarity, is why 0 sweep rule sets ever "
                    "reached 20 trades: built as an explicit ordered chain the same primitives "
                    "yield 57-68 trades per cell at 60m.",
          "status": "the fix works and the strategy still fails. Worth recording so nobody "
                    "spends the effort again expecting the population to be the problem."}},
  "sample_size_bound": "20-68 full-chain trades per cell; per-cell expectancy SE near 0.2R. "
                       "This study can exclude effects larger than about 0.4R and cannot "
                       "resolve anything smaller. Stated as a limit, not a result.",
  "live_eligible": False}
json.dump(rob, open(f'{OUT}/ict_sweep_mss_robustness_report.json', 'w'), indent=1, default=str)

# additive, non-destructive merge into the shared trio - other workers own those files
for name, block in (("strategy_rankings", rankings), ("performance_db", perf),
                    ("robustness_report", rob)):
    path = f'{OUT}/{name}.json'
    cur = json.load(open(path)) if os.path.exists(path) else {}
    cur.setdefault("ict_sweep_mss", block)
    json.dump(cur, open(path, 'w'), indent=1, default=str)
    print("merged into", path)
print("published 3 prefixed deliverables; top 5 by durability:")
for r in rank[:5]:
    print(f"  {r['strategy']:20s} {r['symbol']}|{r['timeframe']}|{r['exit']:8s} "
          f"score {r['durability_score']:+.3f} n={r['n']} exp={r['expectancy_r']:+.3f} "
          f"IS={r['is_expectancy']:+.3f} OOS={r['oos_expectancy']:+.3f} t={r['t_stat']:+.2f}")
