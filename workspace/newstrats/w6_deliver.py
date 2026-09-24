"""Publish the three deliverables, ranked on durability rather than on profit."""
from __future__ import annotations
import json, math, os, statistics as st, sys
from collections import defaultdict

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")
sys.path.insert(0, "/home/user/Futures01/workspace/newstrats")
import toolkit as T, w6_analyse as W, w6_arms as A   # noqa: E402

OUT = "/home/user/Futures01/workspace/strategy_research"
SC = f"{OUT}/scratch/ict"
PREFIX = "ict_killzones_ote"
db = json.load(open(f"{SC}/walkforward_db.json"))
rows = db["rows"]

# ---------------------------------------------------------------- walk-forward
# One (base, variant) rule set has 6 folds x 4 symbols. Folds are consecutive,
# so folds 1..k-1 are the information available before fold k.
by_rs = defaultdict(list)
for r in rows:
    by_rs[(r["base"], r["variant"])].append(r)

FREE_T = T.free_t(len(by_rs))          # t-units bought by searching this many rule sets


def wf(rs_rows):
    """Anchored walk-forward on the R-series: IS = folds before, OOS = this fold."""
    per_sym = defaultdict(dict)
    for r in rs_rows:
        per_sym[r["symbol"]][r["fold"]] = r["metrics"]
    is_exp, oos_exp, oos_sign = [], [], []
    for sym, folds in per_sym.items():
        for k in range(1, max(folds) + 1):
            prior = [folds[j] for j in range(k) if folds.get(j, {}).get("trades", 0) >= 10]
            cur = folds.get(k, {})
            if not prior or cur.get("trades", 0) < 10:
                continue
            e_is = st.mean(p["expectancy_r"] for p in prior)
            is_exp.append(e_is); oos_exp.append(cur["expectancy_r"])
            if e_is > 0:
                oos_sign.append(1 if cur["expectancy_r"] > 0 else 0)
    if not oos_exp:
        return {"wf_pairs": 0}
    eff = (st.mean(oos_exp) / st.mean(is_exp)) if st.mean(is_exp) else 0.0
    return {"wf_pairs": len(oos_exp), "mean_IS_exp": round(st.mean(is_exp), 4),
            "mean_OOS_exp": round(st.mean(oos_exp), 4),
            "walk_forward_efficiency": round(eff, 3),
            "oos_profitable_after_positive_IS": (
                f"{sum(oos_sign)}/{len(oos_sign)}" if oos_sign else "n/a")}


def agg(rs_rows):
    live = [r for r in rs_rows if r["metrics"].get("trades", 0) >= 10]
    if not live:
        return None
    g = lambda f: [r["metrics"][f] for r in live]                       # noqa: E731
    tot = sum(g("trades"))
    exps = g("expectancy_r")
    # t of the pooled R-series approximated from per-cell expectancy and std
    ts = g("t_statistic")
    out = {"n_cells": len(live), "total_trades": tot,
           "median_expectancy_r": round(st.median(exps), 4),
           "mean_expectancy_r": round(st.mean(exps), 4),
           "pct_cells_profitable": round(sum(1 for e in exps if e > 0) / len(exps), 3),
           "median_win_rate": round(st.median(g("win_rate")), 4),
           "median_avg_win_r": round(st.median(g("avg_win_r")), 4),
           "median_avg_loss_r": round(st.median(g("avg_loss_r")), 4),
           "median_payoff_ratio": round(st.median(g("payoff_ratio")), 3),
           "median_profit_factor": round(st.median(g("profit_factor")), 3),
           "median_max_drawdown_r": round(st.median(g("max_drawdown_r")), 3),
           "median_avg_drawdown_r": round(st.median(g("avg_drawdown_r")), 3),
           "median_sharpe": round(st.median(g("sharpe")), 3),
           "median_sortino": round(st.median(g("sortino")), 3),
           "median_t_statistic": round(st.median(ts), 3),
           "max_consecutive_wins": max(g("max_consecutive_wins")),
           "max_consecutive_losses": max(g("max_consecutive_losses")),
           "median_avg_minutes_held": round(st.median(g("avg_minutes_held")), 1),
           "median_avg_mfe_r": round(st.median(g("avg_mfe_r")), 4),
           "median_avg_mae_r": round(st.median(g("avg_mae_r")), 4),
           "median_edge_ratio": round(st.median(g("edge_ratio")), 3),
           "median_n_per_cell": round(st.median(g("trades")), 1)}
    # cross-cell t on the per-cell expectancies: the honest sample size is CELLS
    sd = st.stdev(exps) if len(exps) > 1 else 0.0
    out["t_across_cells"] = round(st.mean(exps) / (sd / math.sqrt(len(exps))), 3) if sd else 0.0
    out["deflated_t"] = round(out["t_across_cells"] - FREE_T, 3)
    out["free_t_for_search"] = round(FREE_T, 3)
    return out


ranked = []
for (base, variant), rs in by_rs.items():
    a = agg(rs)
    if a is None:
        continue
    w = wf(rs)
    gates = {
        "sample_>=100_trades": a["total_trades"] >= 100,
        "median_expectancy_positive": a["median_expectancy_r"] > 0,
        "majority_of_cells_profitable": a["pct_cells_profitable"] > 0.5,
        "t_across_cells_clears_free_t": a["deflated_t"] > 0,
        "walk_forward_efficiency_>=0.5": (w.get("walk_forward_efficiency") or 0) >= 0.5,
        "max_consecutive_losses_<=12": a["max_consecutive_losses"] <= 12,
    }
    ranked.append({"rule_set": f"{base} + {variant}", "base": base, "variant": variant,
                   "timeframe": 60, "symbols": sorted({r["symbol"] for r in rs}),
                   **a, "walk_forward": w, "gates": gates,
                   "gates_passed": sum(gates.values()), "n_gates": len(gates),
                   "live_eligible": all(gates.values()),
                   "durability_score": round(a["deflated_t"], 3)})
ranked.sort(key=lambda r: -r["durability_score"])

rank_doc = {
    "study": PREFIX,
    "ranking_basis": ("Durability, never historical profit. Primary key is the t of the "
                      "per-cell expectancy series across the 24 walk-forward cells, "
                      f"deflated by free_t({len(by_rs)}) = {FREE_T:.3f} - the t-units that "
                      "searching this many rule sets buys for nothing. Six hard gates on "
                      "sample size, cell-level consistency, walk-forward efficiency and "
                      "consecutive losses must ALL pass for live eligibility."),
    "n_rule_sets_ranked": len(ranked),
    "n_live_eligible": sum(1 for r in ranked if r["live_eligible"]),
    "free_t": round(FREE_T, 3),
    "rankings": ranked}

os.makedirs(OUT, exist_ok=True)
json.dump(rank_doc, open(f"{OUT}/{PREFIX}_strategy_rankings.json", "w"), indent=1, default=str)
json.dump(db, open(f"{OUT}/{PREFIX}_performance_db.json", "w"), indent=1, default=str)
print("rule sets ranked:", len(ranked), "live eligible:", rank_doc["n_live_eligible"])
print("free_t =", round(FREE_T, 3))
for r in ranked[:8]:
    print("  %-46s dur=%+.2f t=%+.2f medexp=%+.4f n=%5d wfe=%s gates=%d/%d" % (
        r["rule_set"], r["durability_score"], r["t_across_cells"],
        r["median_expectancy_r"], r["total_trades"],
        r["walk_forward"].get("walk_forward_efficiency"), r["gates_passed"], r["n_gates"]))
print("  ...")
for r in ranked[-3:]:
    print("  %-46s dur=%+.2f t=%+.2f medexp=%+.4f n=%5d" % (
        r["rule_set"], r["durability_score"], r["t_across_cells"],
        r["median_expectancy_r"], r["total_trades"]))

# variant-level walk-forward, which is the thing the study is actually about
by_var = defaultdict(list)
for r in ranked:
    by_var[r["variant"]].append(r)
print()
print("%-18s %6s %8s %9s %8s %8s" % ("variant", "n_rs", "med_dur", "med_exp", "wfe", "eligible"))
for v, rs in sorted(by_var.items(), key=lambda kv: -st.median(x["durability_score"] for x in kv[1])):
    print("%-18s %6d %+8.2f %+9.4f %8.2f %8d" % (
        v, len(rs), st.median(x["durability_score"] for x in rs),
        st.median(x["median_expectancy_r"] for x in rs),
        st.median(x["walk_forward"].get("walk_forward_efficiency") or 0 for x in rs),
        sum(1 for x in rs if x["live_eligible"])))
json.dump({v: {"n_rule_sets": len(rs),
               "median_durability": round(st.median(x["durability_score"] for x in rs), 3),
               "median_expectancy_r": round(st.median(x["median_expectancy_r"] for x in rs), 4),
               "median_wf_efficiency": round(st.median(
                   x["walk_forward"].get("walk_forward_efficiency") or 0 for x in rs), 3),
               "live_eligible": sum(1 for x in rs if x["live_eligible"])}
           for v, rs in by_var.items()},
          open(f"{SC}/variant_summary.json", "w"), indent=1)
