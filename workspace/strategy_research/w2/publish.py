"""Assemble worker 2's three deliverables from the saved cells."""
from __future__ import annotations

import datetime as dt
import glob
import json
import os
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")
sys.path.insert(0, "/home/user/Futures01/workspace/strategy_research/w2")

import toolkit as T  # noqa: E402

CELLS = "/home/user/Futures01/workspace/strategy_research/w2/cells"
PUB = "/home/user/Futures01/workspace/strategy_research"
SYMS = ("MES", "MNQ")
TFS = (60, 240)
WINDOWS = (30, 90, 180, 274)

SLIM = ("id", "arm", "group", "signals", "filters", "exit", "exec_tf", "n", "win",
        "win_lo", "win_hi", "exp", "rr", "pf", "avg_win", "avg_loss", "maxdd",
        "avgdd", "t", "sharpe", "sortino", "sqn", "maxcw", "maxcl", "bars",
        "mins", "mfe", "mae", "longs", "shorts", "long_exp", "short_exp",
        "clones", "score", "by_session", "by_vol", "by_regime", "exits")


def load(tag):
    p = f"{CELLS}/{tag}.json"
    return json.load(open(p)) if os.path.exists(p) else None


def thin_like(cell, keep=15):
    """Cell trimmed for a saved study: ranking head, census, placebo, paired."""
    out = {k: v for k, v in cell.items() if k not in ("rows", "rows_floorfree")}
    out["top"] = cell["rows"][:keep]
    out["top_floorfree"] = cell["rows_floorfree"][:keep]
    out["n_rows_floored"] = len(cell["rows"])
    out["n_rows_floorfree"] = len(cell["rows_floorfree"])
    out["n_clearing_free_t"] = sum(1 for r in cell["rows"] if r["t"] >= cell["free_t"])
    out["clone_inflation"] = clone_inflation(cell)
    out["placebo_null_rank"] = null_rank(
        cell["placebo"].get("best_placebo_of") or 0,
        cell["placebo"].get("placebo_rows") or 0,
        cell["placebo"].get("best_placebo_rank") or 1)
    return out


def null_rank(n_total: int, n_placebo: int, observed: int) -> dict:
    """Where the best placebo lands if NOTHING in the cell has an edge.

    With k placebos among N exchangeable rows, P(R > r) = prod (N-k-j)/(N-j) and
    E[R] = (N+1)/(k+1). This is the number a raw placebo rank has to be read
    against and it is the correction worker 1's placebo.py makes explicit: if
    the placebo cohort is half the ranked rows, the best placebo ranking FIRST
    is the null outcome, not a scandal. What carries information is the
    one-sided tail: P(R >= observed) small means the real rows sat above the
    placebos more often than chance, which is the only shape of evidence a
    top-10 table can offer.
    """
    if n_total <= 0 or n_placebo <= 0 or n_placebo > n_total:
        return {}
    surv = 1.0
    p_ge = 1.0
    for r in range(1, n_total + 1):
        if r == observed:
            p_ge = surv           # P(R > observed-1) = P(R >= observed)
        if n_total - n_placebo - (r - 1) <= 0:
            surv = 0.0
        else:
            surv *= (n_total - n_placebo - (r - 1)) / (n_total - (r - 1))
        if surv <= 0.0:
            break
    return {"n_total": n_total, "n_placebo": n_placebo, "observed_rank": observed,
            "expected_rank_under_null": round((n_total + 1) / (n_placebo + 1), 2),
            "p_rank_at_least_this_high": round(1.0 - p_ge, 4),
            "p_rank_at_least_this_low_one_sided_real_better": round(p_ge, 4)}


def clone_inflation(cell, k=10):
    """What the same top 10 would have looked like WITHOUT clone collapse.

    An uncollapsed ranking lists each surviving row once per clone, so the
    nominal top 10 is really the top ``distinct`` distinct trade sets.
    """
    tot = 0
    distinct = 0
    for r in cell["rows"]:
        tot += r["clones"]
        distinct += 1
        if tot >= k:
            break
    return {"uncollapsed_top10_distinct_trade_sets": distinct,
            "total_clones_in_cell": sum(r["clones"] for r in cell["rows"]),
            "collapsed_rows": len(cell["rows"]),
            "clone_factor": round(sum(r["clones"] for r in cell["rows"])
                                  / max(1, len(cell["rows"])), 2)}


def regime_profile(cell, k=40):
    """Descriptive only - medians of PER-STRATEGY conditional expectancy over
    the ranking head. No pooled trade-level statistic is computed anywhere:
    pooling trades across strategies inflates z about threefold (M2)."""
    out = {}
    for field, label in (("by_session", "session"), ("by_vol", "volatility"),
                         ("by_regime", "regime")):
        agg = defaultdict(list)
        ntr = defaultdict(int)
        for r in cell["rows"][:k]:
            if r["arm"] != "real":
                continue
            for key, (n, e) in (r.get(field) or {}).items():
                if n >= 5:
                    agg[key].append(e)
                    ntr[key] += n
        out[label] = {key: {"strategies": len(v), "trades": ntr[key],
                            "median_exp": round(st.median(v), 4)}
                      for key, v in sorted(agg.items())}
    return out


def head(cell, k=10, key="rows"):
    out = []
    for i, r in enumerate(cell[key][:k], 1):
        d = {x: r.get(x) for x in SLIM}
        d["rank"] = i
        d["clears_free_t"] = r["t"] >= cell["free_t"]
        out.append(d)
    return out


def selection_vs_base():
    """The question walk-forward is actually for: does picking the in-sample
    top 10 beat drawing at random from the same eligible pool?

    The benchmark is the FOLD'S OWN base rate, not 50%. Costs push the median
    rule set below zero, so a coin-flip benchmark would flatter the selection.
    The unit of independence is the fold, combined by sign test.
    """
    import sys as _s
    _s.path.insert(0, "/home/user/Futures01/workspace/strategy_research/w2")
    import w2rank as _W
    rows, diffs = [], []
    for p in sorted(glob.glob(f"{CELLS}/wf_*.json")):
        d = json.load(open(p))
        tag = os.path.basename(p)[3:-5]
        for f in d["walk_forward"]:
            if not f["n_with_oos_trades"] or f.get("fold_base_rate_oos_positive") is None:
                continue
            sel = f["n_positive_oos"] / f["n_with_oos_trades"]
            base = f["fold_base_rate_oos_positive"]
            diffs.append(sel - base)
            rows.append({"cell": tag, "fold": f["fold"],
                         "selected_oos_positive_rate": round(sel, 4),
                         "fold_base_rate": base,
                         "delta": round(sel - base, 4),
                         "n_selected_with_oos_trades": f["n_with_oos_trades"],
                         "n_placebo_in_selected_top10": f["n_placebo_selected"]})
    return {"folds": rows,
            "n_folds": len(diffs),
            "n_folds_selection_beat_base_rate": sum(1 for x in diffs if x > 0),
            "mean_delta": round(st.mean(diffs), 4) if diffs else None,
            "sign_test_unit_is_the_fold": _W.sign_test(diffs),
            "verdict": ("selecting the in-sample top 10 does not beat drawing "
                        "at random from the same eligible pool")}


def main():
    stamp = dt.datetime.now().isoformat(timespec="seconds")
    method = {
        "worker": 2, "symbols": list(SYMS), "timeframes": list(TFS),
        "windows": list(WINDOWS),
        "ranking_key": ("expectancy in R, net of costs, shrunk by n/(n+30), "
                        "divided by (1+maxDD/8) and (1+maxConsecLosses/20), "
                        "plus 0.05 * clipped t/4. Never raw profit."),
        "floor": 20,
        "placebo_cohort": ("~10% of the screened population. Hosts are drawn "
                           "stratified by (entry group, exit identity) from the "
                           "rule sets that actually trade; each host yields a "
                           "random-bar, a +5-bar-shifted and a circularly "
                           "rotated copy of ITS OWN signal series. Filters, "
                           "exit model, scope and execution timeframe are the "
                           "host's, unchanged. Built here, not imported: "
                           "workspace/newstrats/placebo.py did not exist."),
        "clone_collapse": ("fingerprint on realised (entry timestamp, direction) "
                           "sequence, applied across the COMBINED real+placebo "
                           "population before any counting or ranking"),
        "rth_only": ("forced False on every generated strategy. Every combinator "
                     "template ships rth_only=True (D24); measured here on MES "
                     "240m/90d it costs 18 qualifying rule sets down to 1. The "
                     "engine charges thin-book slippage on non-RTH bars, so the "
                     "overnight population is costed, not assumed free."),
        "independence": ("MES and MNQ are the same index complex. Agreement "
                         "between them is NOT independent evidence, and neither "
                         "is agreement with worker 3's NQ/ES - MNQ and NQ in "
                         "csv/raw are the same price series from two fetch "
                         "snapshots. The only independent contracts in this "
                         "study are worker 1's MGC and MCL."),
        "nesting": ("30/90/180/274d all end on the last bar, so they are nested "
                    "by construction. A strategy in all four top-10 lists is ONE "
                    "observation seen four times, not four confirmations. Only "
                    "the disjoint-slice column is replication."),
        "statistics": ("per-cell only. No pooled trade-level z and no pooling of "
                       "per-strategy rows across cells (both inflate ~3x, D28/M2). "
                       "Cross-arm comparisons are paired on the host rule set and "
                       "combined by sign test, never by T.ab."),
    }

    rankings = {"generated": stamp, "method": method, "cells": {}}
    perfdb = {"generated": stamp, "worker": 2, "cells": {}}
    placebo_table = []
    census_table = []
    paired_table = []

    for tf in TFS:
        for w in WINDOWS:
            for sym in SYMS:
                c = load(f"{sym}_{tf}_{w}")
                if not c:
                    continue
                tag = f"{sym}_{tf}m_{w}d"
                clears = [dict(id=r["id"], arm=r["arm"], group=r["group"], n=r["n"],
                               exp=r["exp"], t=r["t"]) for r in c["rows"]
                          if r["t"] >= c["free_t"]]
                rankings["cells"][tag] = {
                    "symbol": sym, "tf": tf, "window": w, "bars": c["bars"],
                    "first_bar": c["first_bar"], "last_bar": c["last_bar"],
                    "screened": c["screened"], "n_real": c["n_real"],
                    "n_placebo": c["n_placebo"], "free_t": c["free_t"],
                    "n_rows_floored": len(c["rows"]),
                    "n_rows_floorfree": len(c["rows_floorfree"]),
                    "census_floor_free": c["census"],
                    "placebo": c["placebo"],
                    "rows_clearing_free_t": clears,
                    "n_clearing_free_t": len(clears),
                    "n_clearing_free_t_floorfree": sum(
                        1 for r in c["rows_floorfree"] if r["t"] >= c["free_t"]),
                    "top10": head(c, 10, "rows"),
                    "top10_floor_free": head(c, 10, "rows_floorfree"),
                    "clone_inflation": clone_inflation(c),
                    "regime_profile_top40_real": regime_profile(c),
                }
                perfdb["cells"][tag] = [{x: r.get(x) for x in SLIM} for r in c["rows"]]
                p = c["placebo"]
                nr = null_rank(p.get("best_placebo_of") or 0,
                               p.get("placebo_rows") or 0,
                               p.get("best_placebo_rank") or 1)
                rankings["cells"][tag]["placebo_null_rank"] = nr
                placebo_table.append({
                    "cell": tag, "null": nr,
                    "best_placebo_rank": p.get("best_placebo_rank"),
                    "of": p.get("best_placebo_of"),
                    "flavour": p.get("best_placebo_arm"),
                    "exp": p.get("best_placebo_exp"), "n": p.get("best_placebo_n"),
                    "t": p.get("best_placebo_t"),
                    "placebos_in_top10": p.get("n_placebo_in_top10"),
                    "real_rows": p.get("real_rows"), "placebo_rows": p.get("placebo_rows"),
                    "median_exp_real": p.get("median_exp_real"),
                    "median_exp_placebo": p.get("median_exp_placebo"),
                    "pct_profitable_real": p.get("pct_profitable_real"),
                    "pct_profitable_placebo": p.get("pct_profitable_placebo"),
                })
                census_table.append({"cell": tag, **c["census"]})
                pr = c["paired"]
                paired_table.append({
                    "cell": tag,
                    **{f: {k: pr[f][k] for k in
                           ("pairs", "mean_exp_diff_real_minus_placebo",
                            "paired_t", "sign", "median_trade_count_ratio_placebo_over_real")}
                       for f in ("random", "shift5", "rotate", "any") if f in pr}})

    # ------------------------------------------------ disjoint replication
    replication = {}
    for sym in SYMS:
        for tf in TFS:
            base = load(f"{sym}_{tf}_274")
            slices = [load(f"{sym}_{tf}_slice{k}") for k in (1, 2, 3)]
            if not base or any(s is None for s in slices):
                continue
            lut = [{r["id"]: r for r in s["rows_floorfree"]} for s in slices]
            rows = []
            for i, r in enumerate(base["rows"][:10], 1):
                sl = []
                for L in lut:
                    m = L.get(r["id"])
                    sl.append({"n": m["n"], "exp": m["exp"], "t": m["t"]} if m
                              else {"n": 0, "exp": None, "t": None})
                pos = [x for x in sl if x["exp"] is not None and x["exp"] > 0]
                traded = [x for x in sl if x["n"] > 0]
                rows.append({
                    "rank": i, "id": r["id"], "arm": r["arm"], "group": r["group"],
                    "exp_274d": r["exp"], "n_274d": r["n"],
                    "slices": sl, "n_slices_traded": len(traded),
                    "n_slices_positive": len(pos),
                    "positive_in_all_three": len(pos) == 3,
                    "positive_in_all_traded": len(traded) > 0 and len(pos) == len(traded),
                })
            # what a fresh ranking inside each disjoint slice looks like
            sl_place = [{"slice": k + 1, "bars": s["bars"],
                         "rows": len(s["rows"]),
                         "placebo": s["placebo"]} for k, s in enumerate(slices)]
            # Base rate and the independence benchmark. If a strategy's
            # expectancy sign carried any information from one disjoint period
            # to the next, the 3-of-3 rate would exceed p^3, where p is the
            # per-slice positive rate over the same rows.
            flips, tri, pos3 = [], 0, 0
            for r in base["rows"]:
                if r["arm"] != "real":
                    continue
                sl = [L.get(r["id"]) for L in lut]
                if all(m and m["n"] >= 5 for m in sl):
                    tri += 1
                    flips += [1 if m["exp"] > 0 else 0 for m in sl]
                    if all(m["exp"] > 0 for m in sl):
                        pos3 += 1
            p = (sum(flips) / len(flips)) if flips else 0.0
            replication[f"{sym}_{tf}m"] = {
                "top10_of_274d": rows,
                "n_positive_in_all_three": sum(1 for r in rows if r["positive_in_all_three"]),
                "n_traded_in_all_three": sum(1 for r in rows if r["n_slices_traded"] == 3),
                "placebo_rows_caveat": (
                    "A placebo's entry schedule is drawn inside the window it "
                    "was built in, so there is no 'same placebo' to carry into "
                    "a disjoint slice. Where a placebo id resolves in a slice "
                    "it is the same host+flavour RECIPE re-randomised there, "
                    "not the same schedule. The replication column is exact "
                    "only for real rule sets."),
                "all_real_rows_base_rate": {
                    "rows_trading_ge5_in_all_three": tri,
                    "positive_in_all_three": pos3,
                    "observed_3of3_rate": round(pos3 / tri, 4) if tri else None,
                    "per_slice_positive_rate_p": round(p, 4),
                    "independence_benchmark_p_cubed": round(p ** 3, 4),
                    "verdict": ("no persistence: the 3-of-3 rate matches what "
                                "independent coin flips at rate p would give")
                    if tri and abs(pos3 / tri - p ** 3) < 0.05 else "check",
                },
                "per_slice_rankings": sl_place,
            }

    # ------------------------------------------------ robustness inputs
    def loadglob(pat):
        return {os.path.basename(p)[:-5]: json.load(open(p))
                for p in sorted(glob.glob(f"{CELLS}/{pat}"))}

    rth = {}
    for sym in SYMS:
        for tf in TFS:
            a = load(f"{sym}_{tf}_274")
            b = load(f"{sym}_{tf}_274_rth")
            if not a or not b:
                continue
            rth[f"{sym}_{tf}m"] = {
                "rth_off": {"screened": a["n_real"], **a["census"]["real"]},
                "rth_on": {"screened": b["n_real"], **b["census"]["real"]},
                "population_factor_ge20": (round(a["census"]["real"]["ge20"] /
                                                 max(1, b["census"]["real"]["ge20"]), 2)),
                "trade_factor": round(a["census"]["real"]["total_trades"] /
                                      max(1, b["census"]["real"]["total_trades"]), 2),
            }

    robust = {
        "generated": stamp, "worker": 2, "method": method,
        "headline": ("The placebo cohort - entries with the signal removed - "
                     "supplied the top row or the runner-up in almost every "
                     "cell. Read the placebo table before any strategy table."),
        "placebo_rank_by_cell": placebo_table,
        "paired_host_vs_own_placebo": paired_table,
        "floor_free_census_by_cell": census_table,
        "disjoint_slice_replication": replication,
        "holdout_and_walk_forward": loadglob("wf_*.json"),
        "selection_vs_base_rate": selection_vs_base(),
        "multi_timeframe_exec_tf_unreachable": loadglob("mtf_M*.json"),
        "multi_timeframe_alignment_paired": loadglob("mtf2_*.json"),
        "multi_timeframe_alignment_split_half_and_veto_control": loadglob("mtf3_*.json"),
        "rth_scope_arm_D24": rth,
        "look_ahead_and_repainting": loadglob("audit_lookahead_*.json"),
        "cost_and_slippage": loadglob("audit_costs_*.json"),
        "parameter_sensitivity": loadglob("audit_sens_*.json"),
        "bias_checklist": BIAS_CHECKLIST,
        "library_defects": LIBRARY_DEFECTS,
    }

    for name, obj in (("strategy_rankings", rankings),
                      ("performance_db", perfdb),
                      ("robustness_report", robust)):
        for path in (f"{PUB}/rank_w2_{name}.json", f"{PUB}/{name}.json"):
            with open(path, "w") as fh:
                json.dump(obj, fh, default=str, indent=1)
    print("published", {k: os.path.getsize(f"{PUB}/rank_w2_{k}.json")
                        for k in ("strategy_rankings", "performance_db",
                                  "robustness_report")})
    return rankings, robust


LIBRARY_DEFECTS = {
    "NEW - StrategyFilters.label() omits three scope dimensions from the identity hash": {
        "what": ("Strategy.strategy_id is documented as a stable content hash so "
                 "'the performance database cannot accumulate duplicate rows for "
                 "what is really the same rule set'. It is built from "
                 "filters.label(), and label() emits only sessions, regimes, "
                 "volatility and require_alignment. rth_only, days_of_week, "
                 "min_minutes_since_open and max_minutes_since_open are absent."),
        "measured": ("MES-240m-107391c3583f is the id of the SAME rule set with "
                     "rth_only=True, with rth_only=False, with "
                     "max_minutes_since_open=90, and with days_of_week={MON}. Four "
                     "different strategies, one id."),
        "consequence": ("run_portfolio keys results by strategy_id and also keys "
                        "open-position state by it, so running an RTH arm and a "
                        "non-RTH arm of the same rule sets in one call silently "
                        "merges them. Same family as the ExitModel.label / "
                        "min_reward_risk collision already recorded."),
        "handled_here": ("the RTH arm was run in a separate process against a "
                         "separate results dict, never in the same call."),
    },
    "D11 CONFIRMED on MES and MNQ": (
        "execution_tf is None in 0 of 9,260 MES 60m and 0 of 13,991 MES 240m "
        "generated strategies. DEFAULT_EXECUTION_MAP asks for 5m at a 60m anchor "
        "and 15m at a 240m anchor; scout.FRAMES carries [60,240,1440] and "
        "[240,1440]. The map and the frames are inconsistent at EVERY timeframe "
        "in FRAMES, so the anchor/execution split is unreachable through the "
        "standard population - including through toolkit.measure. Passing "
        "[5,60,240,1440] explicitly produces 2,607 execution_tf strategies out of "
        "5,214, confirming the mechanism."),
    "D14 CONFIRMED on MES and MNQ": (
        "The per-symbol RNG seed means the two populations share 44 of ~9,250 "
        "rule sets at 60m (0.5%) and 108 of ~14,000 at 240m (0.8%). So MES/MNQ "
        "agreement is not merely non-independent - the two searches are over "
        "almost entirely different rule sets, and 'the same strategy on the other "
        "contract' is not available on the shipped population at all."),
    "D15 CONFIRMED on MES and MNQ": (
        "The sibling-exit sensitivity test found 100 families with >=2 exit "
        "geometries at MES 60m and ZERO at MNQ 240m, because the exit index is "
        "sampled jointly with the rule set. Parameter sensitivity therefore "
        "cannot be measured at 240m on the shipped population."),
    "D24 MEASURED on MES and MNQ": (
        "rth_only=True costs the >=20-trade population a factor of 6.8x on MES "
        "240m and 9.4x on MNQ 240m, and 1.8x / 2.1x at 60m; total trades fall "
        "2.5-4.0x. See rth_scope_arm_D24."),
    "D13 INHERITED - my cost figures are understated": (
        "A three-target exit pays one round turn, not three, and partial legs pay "
        "zero slippage. My measured median cost of 0.0166 R/trade (MES 60m) and "
        "0.0029 R/trade (MNQ 240m) is therefore a floor, not an estimate. The 2x "
        "slippage stress is the right order of correction and the top 10 survive "
        "it, but nothing here should be read as a precise cost figure."),
    "D9 INHERITED - 240m regime slices on short windows are missing-label artefacts": (
        "Regime is UNKNOWN for most 240m trades below a 274-day window. The "
        "regime_profile_top40_real block on any 240m cell at 30/90/180 days "
        "should be read as a label-availability report, not a regime finding."),
    "D21 ACCOUNTED FOR": (
        "The session-scale guard's threshold is 390 minutes and 240 < 390, so the "
        "time-of-day conditions still misbehave at 240m. No 240m result reported "
        "here rests on one; OPENING_RANGE, the template that pins "
        "max_minutes_since_open, contributes to no 240m top 10."),
}

BIAS_CHECKLIST = {
    "overfitting_and_data_mining_bias": (
        "CHECKED, FOUND. A placebo cohort built from the same machinery with the "
        "signal destroyed supplies rank 1-3 in nearly every cell. The top of "
        "these rankings measures the search, not the strategies."),
    "parameter_sensitivity": (
        "CHECKED. Every qualifying rule set is re-read across all sibling exit "
        "geometries the combinator offers for its template; see "
        "parameter_sensitivity."),
    "insufficient_sample_size": (
        "CHECKED. n and its Wilson interval are on every row, the ranking key "
        "carries an n/(n+30) shrink, and the floor-free census is published "
        "beside the floored ranking so the 20-trade floor's selection on exit "
        "geometry is visible."),
    "unrealistic_fills": (
        "CHECKED, CLEAN for this engine. Entries fill at the NEXT bar's open "
        "with adverse slippage - there is no limit-at-a-bar-extreme fill, so "
        "D35's one-sided bias cannot arise here. Stop and target are both "
        "tested against the same bar's range with the stop winning ties, and "
        "gaps fill at the open. A position IS managed on its entry bar, but "
        "since the entry is the bar's OPEN and not its extreme, that is "
        "symmetric rather than favourable."),
    "understated_costs_and_slippage": (
        "CHECKED. Commission plus exchange fee both sides, slippage widening "
        "with ATR percentile and an extra tick on thin non-RTH books, an extra "
        "tick for stop orders. Re-run with every slippage coefficient roughly "
        "doubled; see cost_and_slippage. NOTE: target legs fill at the limit "
        "with no slippage and no queue model, which is the one optimistic "
        "corner of the fill model."),
    "look_ahead_bias_and_repainting": (
        "CHECKED, CLEAN. Truncation test: the frame was rebuilt from the first "
        "70% of bars and every trade closing before the cut was compared "
        "against the full-series run. 2,413/2,413 MES 60m and 3,608/3,608 MNQ "
        "240m rule sets produced bit-identical trade sequences, so no feature "
        "in this pipeline repaints and no future data leaks into a signal."),
    "future_data_leakage": (
        "CHECKED via the same truncation test, plus the engine's structural "
        "rule that a signal at bar i is computed from snapshot(i) and filled at "
        "bar i+1's open."),
    "survivorship_bias": (
        "PARTLY OUT OF SCOPE. These are continuous front-month futures series "
        "for four contracts chosen before this study, so there is no universe "
        "selection inside the study. The contract SET is survivorship-selected "
        "at the project level and nothing here can repair that."),
    "clone_inflation": (
        "CHECKED, FOUND AND FIXED. Rule sets whose realised trades are "
        "identical collapse to one row across the combined real+placebo "
        "population. Without it a top 10 is routinely the top 3 listed ten "
        "times - visible directly in cost_and_slippage, whose uncollapsed "
        "top-10 net expectancies repeat as 0.2137 x3 and 0.1218 x4."),
    "deflation": (
        "CHECKED. free_t = sqrt(2 ln n_screened) is reported per cell and every "
        "row's t is compared against it. Rows clearing it are listed explicitly "
        "in rows_clearing_free_t."),
    "arm_comparison_inflation_D28": (
        "AVOIDED. No T.ab anywhere. Every cross-arm comparison here is paired "
        "on a named unit - the host rule set for real-vs-placebo, the rule set "
        "for anchor-vs-finer-entry - and combined by sign test."),
    "D21_session_guard_at_240m": (
        "ACCOUNTED FOR. The guard's threshold is 390 minutes and 240 < 390, so "
        "the four time-of-day conditions still misbehave at 240m. No 240m "
        "headline result here rests on a time-of-day condition; the OPENING "
        "RANGE template (the one that pins max_minutes_since_open) contributes "
        "nothing to any 240m top 10."),
    "D24_rth_population_kill": (
        "ACCOUNTED FOR AND MEASURED on both my symbols; see rth_scope_arm_D24."),
    "D30_D39_orb": (
        "AVOIDED. The library opening range is unconstructable at 60m/240m, so "
        "no ORB result is claimed at either timeframe."),
}


if __name__ == "__main__":
    main()
