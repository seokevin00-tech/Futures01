"""Publish the geometry results into the three workspace files.

Other agents write these files too, so this MERGES a namespaced
``swing_geometry`` section rather than replacing the document.

Ranking is on durability, never on historical profit: expectancy in R shrunk
towards zero by sample size, the t-statistic of the R series deflated by the
search size, recovery factor against max drawdown, and a penalty for the worst
losing streak.
"""
from __future__ import annotations

import json
import math
import os
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, "workspace/studies")
sys.path.insert(0, "workspace/newstrats")
import toolkit as T   # noqa: E402

ROOT = "workspace/strategy_research"
SCRATCH = f"{ROOT}/scratch"

#: Every distinct arm x contrast x period x parameter-variant that was looked
#: at. Used for the deflation arithmetic - understating it is how a search
#: announces an edge it bought with trials.
N_SCREENED = 9 * 4 * 2 * 9 + 24          # arms x contrasts x periods x variants + WF folds


def metrics(rs, mae, mfe, mins):
    n = len(rs)
    w = [x for x in rs if x > 0]
    l = [x for x in rs if x <= 0]
    eq = peak = 0.0
    dd, cw, cl, mcw, mcl = [], 0, 0, 0, 0
    for x in rs:
        eq += x
        peak = max(peak, eq)
        dd.append(peak - eq)
        if x > 0:
            cw, cl = cw + 1, 0
        else:
            cl, cw = cl + 1, 0
        mcw, mcl = max(mcw, cw), max(mcl, cl)
    sd = st.pstdev(rs) if n > 2 else 0.0
    neg = [x for x in rs if x < 0]
    dsd = st.pstdev(neg) if len(neg) > 2 else 0.0
    return dict(
        trades=n, win_rate=round(len(w) / n, 4),
        avg_win_r=round(st.fmean(w), 4) if w else 0.0,
        avg_loss_r=round(st.fmean(l), 4) if l else 0.0,
        reward_risk=round(abs(st.fmean(w) / st.fmean(l)), 3) if w and l and st.fmean(l) else None,
        profit_factor=round(sum(w) / abs(sum(l)), 3) if l and sum(l) else None,
        expectancy_r=round(st.fmean(rs), 4), total_r=round(sum(rs), 3),
        max_drawdown_r=round(max(dd), 3), avg_drawdown_r=round(st.fmean(dd), 3),
        recovery_factor=round(sum(rs) / max(dd), 3) if max(dd) else None,
        sharpe_per_trade=round(st.fmean(rs) / sd, 3) if sd else None,
        sortino=round(st.fmean(rs) / dsd, 3) if dsd else None,
        t_statistic=round(st.fmean(rs) / (sd / math.sqrt(n)), 3) if sd else None,
        max_consecutive_wins=mcw, max_consecutive_losses=mcl,
        avg_duration_minutes=round(st.fmean(mins), 1),
        avg_mae_r=round(st.fmean(mae), 3), avg_mfe_r=round(st.fmean(mfe), 3),
        edge_ratio=round(st.fmean(mfe) / st.fmean(mae), 3) if st.fmean(mae) else None)


def durability(m):
    """Rank key. Profit does not appear in it except through shrunk expectancy."""
    n = m["trades"]
    shrink = n / (n + 40.0)                       # sample-size penalty
    exp_s = m["expectancy_r"] * shrink
    t_def = (m["t_statistic"] or 0.0) - T.free_t(N_SCREENED)
    rec = m["recovery_factor"] or 0.0
    streak = m["max_consecutive_losses"] / 10.0
    return round(10 * exp_s + t_def + min(rec, 2.0) - streak, 3)


def main():
    trades = json.load(open(f"{SCRATCH}/geo_trades.json"))
    trades = [t for t in trades if t["exitm"] == "atr1.0"]
    wf = json.load(open(f"{SCRATCH}/walkforward.json"))
    census = {f"{r['symbol']}_{r['tf']}": r
              for r in json.load(open(f"{SCRATCH}/census.json"))}
    costs = json.load(open(f"{SCRATCH}/costs.json"))

    # ---- performance db: per arm, per symbol, per tf, per slice, per regime
    def agg(sel):
        rs = [t["r"] for t in sel]
        if len(rs) < 10:
            return None
        return metrics(rs, [t["mae"] for t in sel], [t["mfe"] for t in sel],
                       [t["mins"] for t in sel])

    arms = sorted({t["arm"] for t in trades})
    perf = {"by_arm": {}, "by_arm_symbol": {}, "by_arm_timeframe": {},
            "by_arm_slice": {}, "by_arm_session": {}, "by_arm_regime": {},
            "by_arm_volatility_regime": {}}
    for a in arms:
        A = [t for t in trades if t["arm"] == a]
        perf["by_arm"][a] = agg(A)
        for key, field in (("by_arm_symbol", "symbol"), ("by_arm_timeframe", "tf"),
                           ("by_arm_slice", "slice"), ("by_arm_session", "session"),
                           ("by_arm_regime", "regime"),
                           ("by_arm_volatility_regime", "vol")):
            d = defaultdict(list)
            for t in A:
                d[str(t[field])].append(t)
            perf[key][a] = {k: agg(v) for k, v in sorted(d.items()) if agg(v)}

    # ---- rankings
    ranked = []
    for a in arms:
        m = perf["by_arm"][a]
        if not m:
            continue
        oos = agg([t for t in trades if t["arm"] == a and t["slice"] == "S3"])
        ins = agg([t for t in trades if t["arm"] == a and t["slice"] in ("S1", "S2")])
        ranked.append({
            "arm": a, "durability_score": durability(m),
            "full_sample": m, "in_sample_S1_S2": ins, "out_of_sample_S3": oos,
            "oos_expectancy_sign_matches_is":
                (None if not (ins and oos) else
                 (ins["expectancy_r"] > 0) == (oos["expectancy_r"] > 0)),
            "firing_rate_by_cell": {c: v["firing_rates"].get(a)
                                    for c, v in census.items()
                                    if a in v["firing_rates"]},
            "live_eligible": False,
            "live_eligible_reason": (
                "Negative expectancy after costs on the full sample, no "
                "out-of-sample confirmation, and a t-statistic far below the "
                f"deflation floor free_t({N_SCREENED})={T.free_t(N_SCREENED):.2f}.")})
    ranked.sort(key=lambda r: -r["durability_score"])

    # ---- robustness
    doc = json.load(open("workspace/studies/out/s_geometry.json"))
    f = doc["findings"]
    rob = {
        "scope": "workspace/newstrats/geometry.py - 9 swing-geometry conditions "
                 "plus 2 controls, MGC/MES/MNQ/MCL at 240m and 60m, three "
                 "disjoint periods plus a 60/40 temporal split plus a 5-block "
                 "anchored walk-forward.",
        "bottom_line": "No geometry condition is live-eligible. Nothing survived "
                       "out of sample.",
        "checks": {
            "repainting_and_future_data_leakage": {
                "method": "Rebuilt the whole zigzag from a series truncated at "
                          "bar i and compared it with the state recorded at bar i "
                          "when the full series was processed.",
                "result": "960 probes across 8 symbol/timeframe cells, 0 "
                          "mismatches. The geometry at bar i does not change "
                          "when later bars arrive.",
                "detail": f["robustness_repaint_lag_and_parameter_sensitivity"]
                          ["repaint_and_future_leakage"]},
            "look_ahead_via_swing_confirmation": {
                "method": "Every swing filtered on Swing.confirmed_index; the "
                          "zigzag is built forward in confirmation order and the "
                          "same-kind collapse can only see already-confirmed swings.",
                "result": "Confirmation costs exactly 3 bars on a 3-bar fractal "
                          "while the MEDIAN swing leg is 5-6 bars, so 50-60% of a "
                          "typical leg has already run before the swing that "
                          "starts it is knowable. This is a structural reason leg "
                          "geometry is hard to trade, not a bug.",
                "detail": f["robustness_repaint_lag_and_parameter_sensitivity"]
                          ["confirmation_lag_vs_leg_length"]},
            "degeneracy_and_duplication": {
                "method": "Firing-rate census on every bar plus pairwise Jaccard "
                          "against the existing fibonacci conditions.",
                "result": "Firing rates 1.4%-40% - none under 1%, none over 95%. "
                          "Max Jaccard against fib_golden_pocket / "
                          "fib_shallow_retrace is 0.09; at most 23% of a geometry "
                          "condition's bars also carry a fib condition. These are "
                          "not the mtf_aligned situation."},
            "parameter_sensitivity": {
                "result": "Signs flip across parameter settings. "
                          "swing_symmetry_impulse vs retrace runs IS z=-1.34 to "
                          "+1.28 across variants; pullbacks_shallowing vs "
                          "deepening runs IS z=-1.38 to +1.57. No setting reaches "
                          "|z|=2.4 in sample.",
                "detail": f["parameter_sensitivity_corrected_and_cost_sensitivity"]
                          ["headline_statistic_across_variants"]},
            "costs_slippage_and_fills": {
                "result": "Engine defaults already assume entry on the NEXT bar "
                          "open, stop filled before target within a bar, gaps "
                          "filled at the open, and slippage widening with the ATR "
                          "percentile. Doubling every slippage parameter moves "
                          "expectancy by a median of 0.012 R. The results are flat, "
                          "not cost-marginal.",
                "detail": costs},
            "sample_size": {
                "result": "11 months of data (2025-10 to 2026-09): 1347-1384 bars "
                          "at 240m and 5000 at 60m per symbol. At 240m the "
                          "contracting and deepening arms clear 8 trades in only "
                          "2-3 of 8 cells. Several headline cells rest on 11-26 "
                          "trades and are reported but not believed."},
            "data_mining_bias_and_deflation": {
                "n_screened": N_SCREENED,
                "free_t": round(T.free_t(N_SCREENED), 3),
                "largest_trade_level_stouffer_z_observed": 2.359,
                "verdict": "The largest statistic anywhere in the programme is "
                           "below the free-t floor, and it appears only out of "
                           "sample with an in-sample z of -0.16 - which is the "
                           "wrong order for a confirmation."},
            "statistical_inflation_found": {
                "result": "Rank-summing per-strategy expectancies across 13 "
                          "correlated partner filters within one cell inflates |z| "
                          "by a median of about 3.3x (range 1.7-15.3) relative to "
                          "the trade-level test on the SAME trades, and 7 of 11 of "
                          "those inflated statistics flip sign out of sample. This "
                          "is a third instance of the project's known z-inflation "
                          "defect, in a new place.",
                "detail": f["robustness_repaint_lag_and_parameter_sensitivity"]
                          ["z_inflation_strategy_level_vs_trade_level"]},
            "survivorship_bias": {
                "result": "Not applicable in the usual sense - four continuously "
                          "listed front-month futures series, no universe "
                          "selection. But the symbol set is itself a survivor "
                          "choice: MNQ stands in for NQ because no NQ csv exists."},
            "floor_free_reporting": {
                "result": "Primary statistics are trade-level per cell with a "
                          "minimum of 8 trades per arm, and the firing-rate census "
                          "has no floor at all. The strategy-level view uses a "
                          "5-trade floor and is reported alongside, not instead."},
        },
        "out_of_sample_and_walk_forward": {
            "temporal_design": "Three disjoint periods (S1/S2 in sample, S3 out of "
                               "sample), re-cut as a 60/40 split, plus a 5-block "
                               "anchored walk-forward. No nested windows were used "
                               "as replication.",
            "walk_forward": wf["summary"],
            "headline_claims_and_their_oos_result": [
                {"claim": "expansion beats contraction",
                 "in_sample": "Stouffer z=+1.47 over 9 cells (398 vs 195 trades)",
                 "out_of_sample": "z=-0.81 over 5 cells (246 vs 96) - REVERSED",
                 "verdict": "not supported"},
                {"claim": "shallowing pullbacks beat deepening pullbacks",
                 "in_sample": "z=-1.37 over 11 cells (456 vs 166)",
                 "out_of_sample": "z=+1.96 over 4 cells (226 vs 76) - REVERSED",
                 "verdict": "not supported; the two periods disagree in sign"},
                {"claim": "impulse-dominant swing symmetry beats retrace-dominant",
                 "in_sample": "z=-0.79 over 15 cells (1252 vs 554)",
                 "out_of_sample": "z=-0.67 over 8 cells (706 vs 319)",
                 "verdict": "consistently negative - the stated prior is wrong in "
                            "sign, though not significantly"},
                {"claim": "geometry adds to the plain structure_trend label",
                 "in_sample": "best arm z=+1.16 (legs_expanding, 491 vs 1482)",
                 "out_of_sample": "z=-0.83 - REVERSED",
                 "verdict": "not supported; the information is not there"},
                {"claim": "a contracting structure is a better fade than an "
                          "expanding one is a follow",
                 "in_sample": "z=-1.39 over 11 cells",
                 "out_of_sample": "z=-1.63 over 5 cells; all cells z=-2.06",
                 "verdict": "consistently NEGATIVE - the fade is worse, not "
                            "better, but below the deflation floor"},
            ]},
        "new_defects_found_by_this_worker": [
            "Default-argument capture of a module global in this worker's own "
            "sensitivity sweep silently re-ran the baseline three times and "
            "reported perfect parameter stability. Found and fixed; a sweep that "
            "shows no sensitivity should be treated as broken until proven "
            "otherwise.",
            "Strategy-level rank-sum across correlated partner variants inflates "
            "|z| by ~3.3x versus the trade-level test on the same trades.",
            "The 3-bar fractal confirmation lag consumes 50-60% of a median swing "
            "leg on every symbol and both timeframes. Any condition that needs "
            "three confirmed legs is reading a structure that is largely over.",
        ],
    }

    ns = "swing_geometry"
    for path, section in (("strategy_rankings.json",
                           {"ranked_on": "durability: expectancy in R shrunk by "
                                         "n/(n+40), plus t-statistic deflated by "
                                         f"free_t({N_SCREENED}), plus recovery "
                                         "factor capped at 2, minus "
                                         "max_consecutive_losses/10. Historical "
                                         "profit is not a ranking input.",
                            "live_eligible": [],
                            "live_eligible_reason":
                                "Nothing qualifies. Every arm is negative after "
                                "costs on the full sample and no headline claim "
                                "survived out of sample.",
                            "n_screened": N_SCREENED,
                            "free_t": round(T.free_t(N_SCREENED), 3),
                            "rankings": ranked}),
                          ("performance_db.json", perf),
                          ("robustness_report.json", rob)):
        p = f"{ROOT}/{path}"
        doc2 = json.load(open(p)) if os.path.exists(p) else {}
        doc2[ns] = section
        doc2.setdefault("generated_by", [])
        if isinstance(doc2["generated_by"], list) and ns not in doc2["generated_by"]:
            doc2["generated_by"].append(ns)
        json.dump(doc2, open(p, "w"), indent=1, default=str)
        print("wrote", p)

    for r in ranked:
        m = r["full_sample"]
        print(f"{r['arm']:28s} score={r['durability_score']:+7.3f} n={m['trades']:5d} "
              f"exp={m['expectancy_r']:+.4f} pf={m['profit_factor']} "
              f"t={m['t_statistic']} maxdd={m['max_drawdown_r']} "
              f"mcl={m['max_consecutive_losses']} oos_sign_ok={r['oos_expectancy_sign_matches_is']}")


if __name__ == "__main__":
    main()
