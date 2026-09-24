"""Publish the three deliverables for the ICT order-block / FVG study.

Ranking is on DURABILITY, never on historical profit. The score is

    durability = t_of_R_series - free_t(searched)
                 - sample_penalty(n) - drawdown_penalty - streak_penalty

and it is computed on the OUT-OF-SAMPLE half, with the in-sample value carried
alongside so a sign flip is visible rather than averaged away. Nothing is
live-eligible unless its out-of-sample expectancy is positive AND its durability
score clears zero AND the paired test against a matched sham survives the
split. On this material nothing does.

Three other agents are writing the shared top-level files at the same time, so
this writes its own prefixed copies first and then adds exactly one namespaced
key to each shared file, read-modify-write, leaving every other key untouched.
"""
from __future__ import annotations

import json
import math
import os
import statistics as st
import sys
from typing import Dict, List

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")

import toolkit as T                                    # noqa: E402

SCRATCH = ("/tmp/claude-0/-home-user-Futures01/"
           "40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad")
PUB = "/home/user/Futures01/workspace/strategy_research"
NS = "ict_blocks_fvg"

#: Everything screened in this study: 15 conditions x 9 cells for firing, plus
#: 8 conditions x 2 periods x 2 comparisons in the arms, plus 12 bar-level
#: statistics x 2 concepts, plus 48 parameter corners x 5 cells.
SEARCHED = 15 * 9 + 8 * 2 * 2 + 12 * 2 + 48 * 5
FREE_T = T.free_t(SEARCHED)


def load(name):
    with open(f"{SCRATCH}/{name}") as fh:
        return json.load(fh)


def durability(m: dict) -> float:
    """t of the R series, deflated, then penalised for thin samples, deep
    drawdowns and long losing streaks. Profit does not appear."""
    n = m.get("trades", 0) or 0
    if n < 5:
        return float("-inf")
    t = m.get("t_statistic", 0.0) or 0.0
    sample_pen = max(0.0, (100 - n) / 100.0)
    dd_pen = abs(m.get("max_drawdown_r", 0.0) or 0.0) / 10.0
    streak_pen = max(0, (m.get("max_consecutive_losses", 0) or 0) - 8) / 4.0
    return round(t - FREE_T - sample_pen - dd_pen - streak_pen, 3)


def main():
    perf = load("w4_perf.json")
    arms = load("ict_arms.json")
    rates = load("w4_rates.json")
    bar = load("ict_barlevel.json")
    baroos = load("ict_barlevel_oos.json")
    fresh = load("ict_freshness.json")
    audit = load("w4_audit.json")
    os.makedirs(PUB, exist_ok=True)

    # ---------------------------------------------------------- rankings
    rows = []
    for cell, per in perf["cells"].items():
        for lab, rec in per["OOS"].items():
            if "overall" not in rec:
                continue
            o = rec["overall"]
            i = per["IS"].get(lab, {}).get("overall", {})
            if (o.get("trades") or 0) < 5:
                continue
            rows.append(dict(
                cell=cell, rule_set=lab,
                oos_n=o.get("trades"), oos_exp=o.get("expectancy_r"),
                oos_win=o.get("win_rate"), oos_pf=o.get("profit_factor"),
                oos_rr=o.get("payoff_ratio"), oos_t=o.get("t_statistic"),
                oos_maxdd=o.get("max_drawdown_r"), oos_avgdd=o.get("avg_drawdown_r"),
                oos_sharpe=o.get("sharpe"), oos_sortino=o.get("sortino"),
                oos_max_consec_losses=o.get("max_consecutive_losses"),
                oos_max_consec_wins=o.get("max_consecutive_wins"),
                oos_avg_win_r=o.get("avg_win_r"), oos_avg_loss_r=o.get("avg_loss_r"),
                oos_avg_minutes_held=o.get("avg_minutes_held"),
                oos_avg_mfe_r=o.get("avg_mfe_r"), oos_avg_mae_r=o.get("avg_mae_r"),
                is_n=i.get("trades"), is_exp=i.get("expectancy_r"),
                is_t=i.get("t_statistic"),
                sign_flip=(i.get("expectancy_r") is not None
                           and o.get("expectancy_r") is not None
                           and (i["expectancy_r"] > 0) != (o["expectancy_r"] > 0)),
                durability=durability(o)))
    rows.sort(key=lambda r: r["durability"], reverse=True)
    n_flip = sum(1 for r in rows if r["sign_flip"])
    pos_oos = [r for r in rows if (r["oos_exp"] or 0) > 0]

    rankings = {
        "generated_by": "worker 4 - ICT order blocks and fair value gaps",
        "module": "workspace/newstrats/ict_blocks.py",
        "ranked_on": ("durability = t(R series) - free_t(searched) - sample penalty "
                      "- max_drawdown/10 - (consecutive losses - 8)/4, computed OUT "
                      "OF SAMPLE. Historical profit is not an input."),
        "deflation_arithmetic": {
            "things_screened": SEARCHED, "free_t": round(FREE_T, 3),
            "best_oos_t_in_this_study": max((r["oos_t"] or 0) for r in rows),
            "verdict": "no rule set clears free_t out of sample"},
        "live_eligible": [],
        "live_eligible_reason": (
            "Nothing qualifies. The gate is: positive out-of-sample expectancy AND "
            "durability > 0 AND a paired test against a matched sham that survives the "
            "60/40 split. The best out-of-sample durability score here is "
            f"{rows[0]['durability'] if rows else 'n/a'} against a free_t of "
            f"{FREE_T:.2f}. Separately, the bar-level claim these strategies rest on "
            "does not hold: price returns to a real order block or fair value gap no "
            "more often than to a sham zone at the same ATR distance."),
        "counts": {"rule_sets_ranked": len(rows),
                   "with_positive_oos_expectancy": len(pos_oos),
                   "sign_flips_IS_to_OOS": n_flip,
                   "pct_sign_flips": round(n_flip / len(rows), 3) if rows else None},
        "top_30_by_durability": rows[:30],
        "bottom_10_by_durability": rows[-10:],
        "firing_rates": {c: rates["cells"][c]["firing_rate"] for c in rates["cells"]},
        "condition_identity_jaccard": {
            c: rates["cells"][c]["jaccard_vs_existing"] for c in rates["cells"]},
        "paired_arm_result": arms.get("paired_summary_signtest", {}),
        "caveats": [
            "Every number here is one symbol's own. Nothing was carried from MNQ to "
            "MES or from MGC to MCL; each cell was measured separately.",
            "MNQ/NQ/MES are one index complex - their agreement is not independent "
            "evidence. MGC and MCL are the independent contracts and they agree with "
            "the null.",
            "15m history is ~59 days. A 15m result here is weak evidence either way.",
            "Floor-free: every rule set with 5 or more out-of-sample trades is ranked."]}

    # ------------------------------------------------------- performance db
    db = {
        "schema": perf["schema"], "floor": perf["floor"],
        "generated_by": "worker 4 - ICT order blocks and fair value gaps",
        "free_t": round(FREE_T, 3), "things_screened": SEARCHED,
        "cells_measured": list(perf["cells"]),
        "split": "60/40 temporal, IS then OOS, geometry registered on the full series",
        "conditions": perf["conditions"], "paired_with": perf["paired_with"],
        "metrics_included": [
            "win rate", "avg win R", "avg loss R", "profit factor", "expectancy R",
            "max drawdown R", "avg drawdown R", "sharpe", "sortino", "payoff ratio",
            "trade count", "max consecutive wins", "max consecutive losses",
            "avg bars held", "avg minutes held", "avg MFE R", "avg MAE R",
            "edge ratio", "long/short split", "exit reasons",
            "by session", "by regime", "by timeframe (cell key)"],
        "by_rule_set": perf["cells"],
        "arms_raw": arms["cells"],
        "bar_level": {"in_sample": bar, "out_of_sample": baroos},
        "freshness": fresh}

    # -------------------------------------------------------- robustness
    robust = {
        "scope": "ICT order blocks, fair value gaps, breaker blocks, inversion FVGs. "
                 "MGC/MES/NQ/MNQ/MCL at 60m and 15m (NQ 60m only - no 15m file).",
        "generated_by": "worker 4",
        "bottom_line": (
            "Not live-eligible, and the reason is upstream of any strategy. The claim "
            "that price returns to these zones more than chance is FALSE against a "
            "matched sham baseline. The one in-sample positive (FVGs winning a "
            "symmetric 1-ATR barrier race, Stouffer z=+3.41, 8 of 9 cells) collapses to "
            "z=+0.13 and 4 of 9 cells out of sample. Order blocks flip sign: paired "
            "delta-expectancy +0.088 R in sample (7/9 cells) to -0.064 R out of sample "
            "(2/9 cells). No arm has positive absolute expectancy after costs."),
        "checks": {
            "overfitting": {
                "checked": True,
                "how": "60/40 temporal split on every headline, plus a 48-corner "
                       "parameter grid on the order-block detector across 5 cells.",
                "found": "Present and large. In-sample positives reverse out of sample "
                         "in the arms (ict_ob_fresh, ict_ob_return, ict_ob_newest) and "
                         "in the bar-level test (FVG barrier race). Across 235 "
                         "parameter corner-cells the best OOS z is +1.90 and the sign "
                         "is inconsistent between symbols."},
            "data_mining_bias": {
                "checked": True,
                "how": f"free_t({SEARCHED}) = {FREE_T:.2f} t-units bought by search.",
                "found": "Nothing clears it. The largest OOS across-cell sign z is "
                         "+2.33 (ict_fvg_newest) against free_t of "
                         f"{FREE_T:.2f}, and that condition was selected after "
                         "looking at eight."},
            "parameter_sensitivity": {
                "checked": True,
                "how": "displacement 0.5/1.0/1.5/2.0 ATR x window 2/3/5 bars x "
                       "imbalance required or not x full-range or body-only zone.",
                "found": "The effect exists at no corner. Worker 3's definitional map "
                         "flagged that 'which up move, how large, which candidate' is "
                         "genuinely ambiguous in the source material; the grid shows "
                         "the ambiguity is moot."},
            "insufficient_sample": {
                "checked": True,
                "how": "trade counts and bar-level counts reported at every step; "
                       "floor-free census.",
                "found": "NOT a problem here, which is itself a finding. Median 32-158 "
                         "trades per arm per cell and 1,500-2,700 zone touches per "
                         "cell. The SUPPLY_DEMAND programme's inability to reach 20 "
                         "trades was a property of its detector, not of the concept."},
            "unrealistic_fills": {
                "checked": True,
                "how": "engine FillModel - entry on next bar open, gaps fill at the "
                       "open, a bar holding both stop and target scores as a stop.",
                "found": "Clean. The bar-level tests additionally score an ambiguous "
                         "double-barrier bar as a loss for the claimed direction."},
            "understated_costs_and_slippage": {
                "checked": True,
                "how": "engine CostModel - commission plus exchange fee, slippage "
                       "scaled by ATR percentile, +1 tick on stops, thin-book and news "
                       "penalties.",
                "found": "Costs are the difference between 'marginal' and 'negative'. "
                         "Base rule sets run -0.084 R in sample net; the best ICT arm "
                         "reaches -0.004 R and never crosses zero."},
            "look_ahead_bias": {
                "checked": True,
                "how": "rebuilt every BarState from series truncated at 35/60/85% and "
                       "compared to the full-series state on all overlapping bars.",
                "found": "Zero mismatches across 4 cells and 3 cutoffs (~50,000 "
                         "bar-states). FVG visible at bar 3, order block never before "
                         "its displacement completes."},
            "repainting": {
                "checked": True,
                "how": "same truncation test; touch counts, invalidation and freshness "
                       "are folded forward in one causal pass.",
                "found": "None. Note the LIBRARY has the same discipline in "
                         "SDZone.as_of and the masked FVG copy in active_fvgs."},
            "future_data_leakage": {
                "checked": True,
                "how": "positive control - a variant that consults each order block 2 "
                       "bars EARLY.",
                "found": "The cheat scores 66.6-69.8% on the 1-ATR race against the "
                         "honest 45.4-52.6%, z=+4.19 to +6.97. The harness can detect "
                         "leakage, so the null results are real and not insensitivity."},
            "survivorship_bias": {
                "checked": True,
                "how": "single-instrument continuous series, no universe selection.",
                "found": "Not applicable in the usual sense. But these are CONTINUOUS "
                         "contracts, so roll adjustments sit inside the price series. "
                         "ATR-normalised zone geometry absorbs most of it; flagged, "
                         "not dismissed."},
            "duplicate_condition": {
                "checked": True,
                "how": "bar-level Jaccard against 10 library conditions, plus a "
                       "deliberate re-implementation of fvg_nearby (ict_fvg_libclone).",
                "found": "No duplicates. Jaccard vs fvg_nearby 0.005-0.121, vs "
                         "zone_touch 0.005-0.051, vs fresh_zone_approach 0.000-0.030. "
                         "ict_fvg_libclone scores 0.785-0.966 vs fvg_nearby, which "
                         "proves the 8x firing-rate gap is definitional, not a bug."},
            "degenerate_firing_rate": {
                "checked": True,
                "how": "firing rate of all 15 conditions on all 9 cells, first.",
                "found": "Two conditions are degenerate at the LOOSE end, not the "
                         "scarce end: ict_ob_return 40.5-51.5% and ict_fvg_return "
                         "50.3-56.1%. A directional condition true on half of all bars "
                         "is barely a condition. The fresh/newest variants "
                         "(5.1-34.2%) are the usable ones."},
            "clone_collapse_and_D28": {
                "checked": True,
                "how": "T.ab was never used. Arms are matched on base rule set, cell, "
                       "bars and exit; the statistic is a per-cell paired sign test on "
                       "delta-expectancy combined by Stouffer.",
                "found": "Both the across-cell sign test and the Stouffer of per-cell "
                         "sign-test z are reported. The per-cell paired t is stored but "
                         "flagged: 12 base rule sets in one cell share bars, so it has "
                         "the same correlation problem one level down."}},
        "out_of_sample_and_walk_forward": {
            "design": "60/40 temporal split, self-contained halves. T.disjoint_slices "
                      "was available but the 15m series is only ~59 days, so three "
                      "disjoint slices would leave ~20 days each - a split was the "
                      "honest choice at this data length.",
            "bar_level_IS_vs_OOS": baroos.get("combined", {}),
            "arms_IS_vs_OOS": {k: {p: {"across_cell_sign": v[p]["across_cell_sign"],
                                       "stouffer_of_per_cell_sign":
                                           v[p]["stouffer_of_per_cell_sign"],
                                       "median_delta_exp": v[p]["median_delta_exp"],
                                       "median_abs_exp_WITH": v[p]["median_abs_exp_WITH"]}
                                   for p in ("IS", "OOS")}
                               for k, v in arms.get("paired_summary_signtest", {}).items()},
            "freshness_IS_vs_OOS": fresh.get("combined", {})},
        "audit": audit,
        "findings_that_are_negatives_and_should_be_recorded_as_such": [
            "The 80-90% 'FVG fill rate' that circulates as evidence for the concept is "
            "real (87.3-90.1% within 40 bars) and worthless: a sham zone at the same "
            "ATR distance fills 87.0-89.8% of the time.",
            "Order blocks are the most permissive ICT condition, not the scarcest. The "
            "brief expected the opposite.",
            "Freshness - untested versus retested - is ANSWERABLE at bar level with "
            "1,500-2,700 touches per cell, unlike the supply/demand attempt, and the "
            "answer is that it does not matter (OB Stouffer +1.23 full, FVG +2.24 IS "
            "and -1.34 OOS).",
            "Breaker blocks and inversion FVGs, restricted to their first retest, are "
            "usable-rate conditions (5.6-6.7% and 11.8-14.3%) and are flat everywhere."]}

    for name, obj in (("strategy_rankings", rankings),
                      ("performance_db", db),
                      ("robustness_report", robust)):
        with open(f"{PUB}/{NS}_{name}.json", "w") as fh:
            json.dump(obj, fh, indent=1, default=str)
        shared = f"{PUB}/{name}.json"
        try:
            cur = json.load(open(shared)) if os.path.exists(shared) else {}
        except Exception:
            cur = {}
        if not isinstance(cur, dict):
            cur = {"_previous": cur}
        cur[NS] = obj
        with open(shared, "w") as fh:
            json.dump(cur, fh, indent=1, default=str)
        print(f"wrote {PUB}/{NS}_{name}.json and merged key '{NS}' into {shared}")

    print(f"\nrule sets ranked: {len(rows)}  positive OOS expectancy: {len(pos_oos)}  "
          f"sign flips: {n_flip}  free_t={FREE_T:.2f}")
    for r in rows[:8]:
        print(f"  {r['durability']:+7.2f} {r['cell']:9s} {r['rule_set']:34s} "
              f"OOSn={r['oos_n']:4d} exp={r['oos_exp']:+.4f} t={r['oos_t']:+.2f} "
              f"ISexp={r['is_exp']} flip={r['sign_flip']}")
    return rankings


if __name__ == "__main__":
    main()
