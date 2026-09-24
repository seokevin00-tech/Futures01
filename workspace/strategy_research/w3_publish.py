"""Assemble worker 3's three published artefacts from the saved cells + audits."""
from __future__ import annotations

import json
import math
import os
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, "/home/user/Futures01")
os.chdir("/home/user/Futures01")

import workspace.strategy_research.w3_rank as W  # noqa: E402
import workspace.studies.toolkit as T  # noqa: E402

PUB = "workspace/strategy_research"
CELLS = W.OUTDIR
GRAINS = ["MZC", "MZS", "MZW"]
INDEX = ["NQ", "ES"]
KEEP = ("rank", "arm", "parent", "group", "name", "signals", "filters", "rth_only",
        "stop_kind", "stop_mult", "target_kind", "anchor", "session_close",
        "n", "win", "win_lo", "win_hi", "exp", "avg_win", "avg_loss", "rr", "pf",
        "maxdd", "avgdd", "t", "sharpe", "sortino", "sqn", "maxcw", "maxcl",
        "avg_min", "avg_bars", "mfe", "mae", "longs", "shorts", "long_exp",
        "short_exp", "total_r", "total_usd", "clones", "clears_free_t", "id")


def load_cells():
    out = {}
    for f in sorted(os.listdir(CELLS)):
        if not f.startswith("rank_nq_es_grains_") or "replication" in f:
            continue
        c = json.load(open(os.path.join(CELLS, f)))
        out[(c["symbol"], c["tf"], c["window"])] = c
    return out


def load_reps():
    out = {}
    for f in sorted(os.listdir(CELLS)):
        if f.startswith("replication_"):
            out[f.split("_")[1].split(".")[0]] = json.load(open(os.path.join(CELLS, f)))
    return out


def load_audit(name):
    """Merge every audit_<name>*.json shard (the audits run split by symbol)."""
    merged = {}
    for f in sorted(os.listdir(CELLS)):
        if not (f.startswith(f"audit_{name}") and f.endswith(".json")):
            continue
        for k, v in json.load(open(os.path.join(CELLS, f))).items():
            if isinstance(v, dict):
                merged.setdefault(k, {}).update(v)
            else:
                merged[k] = v
    return merged


def sign_test(pos, n):
    """Two-sided exact binomial against p=0.5."""
    if n == 0:
        return None
    def c(n, k):
        return math.comb(n, k)
    tail = sum(c(n, k) for k in range(0, min(pos, n - pos) + 1)) / 2 ** n
    return round(min(1.0, 2 * tail), 4)


def stouffer(zs):
    zs = [z for z in zs if z is not None]
    if not zs:
        return None
    return round(sum(zs) / math.sqrt(len(zs)), 3)


def main():
    cells = load_cells()
    reps = load_reps()

    # ------------------------------------------------- strategy_rankings.json
    rankings = {"about": {
        "worker": "3 of 4",
        "symbols": GRAINS + INDEX, "timeframes": [60, 240],
        "windows": [30, 90, 180, 274],
        "ranked_on": "expectancy in R, net of costs (ties broken by t)",
        "placebo_module": "workspace/newstrats/placebo.py (worker 1)",
        "nesting_warning": (
            "The 30/90/180/274-day windows are NESTED. A strategy appearing in all "
            "four top-10 lists is ONE observation seen four times, not four "
            "confirmations. Only the disjoint-slice column in robustness_report.json "
            "carries replication weight."),
        "sizing_warning": (
            "NQ ($20/pt) and ES ($50/pt) are FULL-SIZE contracts. The R statistics "
            "here transfer to MNQ/MES; the position sizing does not - one NQ is ten "
            "MNQ and one ES is ten MES of risk on a $50,000 account."),
        "grain_specs_verified": (
            "MZC/MZS/MZW: 500 bu, cents/bushel, tick 0.125 = $0.625, $5.00/point. "
            "Checked trade-by-trade against realised dollar P&L - see "
            "robustness_report.json -> dollar_audit."),
        "caveats": W.CAVEATS,
    }, "cells": {}}

    for (sym, tf, w), c in sorted(cells.items()):
        key = f"{sym}_{tf}m_{w}d"
        if c.get("skipped"):
            rankings["cells"][key] = {"skipped": c["skipped"], "bars": c.get("bars")}
            continue
        entry = {"symbol": sym, "tf": tf, "window_days": w, "bars": c["bars"],
                 "span_days": c["span_days"], "last_bar": c["last"],
                 "generated_rule_sets": c["generated"],
                 "free_t": c["primary"]["free_t"],
                 "census_floor_free": c["census"],
                 "placebo_diag": {k: v for k, v in (c.get("placebo_diag") or {}).items()
                                  if k != "id_collisions"}}
        for tag, lbl in (("primary", "floor_20"), ("secondary", "floor_10")):
            t_ = c[tag]
            entry[lbl] = {
                "n_real_rows": t_["n_real"], "n_placebo_rows": t_["n_placebo"],
                "placebo_share": t_["placebo_share"],
                "best_placebo_rank": t_["best_placebo_rank"],
                "best_placebo_rank_expected_under_null":
                    t_["null_rank"].get("expected_best_rank"),
                "placebo_in_top10": t_["placebo_in_top10"],
                "all_placebo_ranks": t_["placebo_ranks"],
                "best_placebo": t_["best_placebo"],
                "rows_clearing_free_t": t_["n_clearing_free_t"],
                "top10": [{k: r.get(k) for k in KEEP} for r in t_["rows"][:10]],
                "top10_breakdown": {r["name"]: r.get("breakdown")
                                    for r in t_["rows"][:10] if r.get("breakdown")},
            }
        rankings["cells"][key] = entry

    for nm in ("rank_nq_es_grains_strategy_rankings.json", "strategy_rankings.json"):
        with open(f"{PUB}/{nm}", "w") as fh:
            json.dump(rankings, fh, indent=1, default=str)

    # ---------------------------------------------------- performance_db.json
    db = {"about": {
        "unit": "one row = one clone-collapsed rule set inside ONE cell",
        "no_pooling": ("Rows are NEVER pooled across cells. Pooling per-strategy rows "
                       "across cells is the M2 defect one level up and inflates z ~3x."),
        "arms": "real | placebo_random | placebo_shift | placebo_shuffle",
        "costs": ("commission + exchange fee both sides, adverse slippage on entry and "
                  "on stop exits; target/time/session exits carry no slippage"),
    }, "rows": []}
    for (sym, tf, w), c in sorted(cells.items()):
        if c.get("skipped"):
            continue
        for r in c["primary"]["rows"]:
            db["rows"].append(dict({k: r.get(k) for k in KEEP},
                                   symbol=sym, tf=tf, window=w, floor=20,
                                   cell_free_t=c["primary"]["free_t"],
                                   cell_screened=c["generated"]))
        for r in c["secondary"]["rows"][:25]:
            db["rows"].append(dict({k: r.get(k) for k in KEEP},
                                   symbol=sym, tf=tf, window=w, floor=10,
                                   cell_free_t=c["secondary"]["free_t"],
                                   cell_screened=c["generated"]))
    for nm in ("rank_nq_es_grains_performance_db.json", "performance_db.json"):
        with open(f"{PUB}/{nm}", "w") as fh:
            json.dump(db, fh, indent=1, default=str)

    # -------------------------------------------------- robustness_report.json
    # placebo verdict per cell
    plc = []
    for (sym, tf, w), c in sorted(cells.items()):
        if c.get("skipped"):
            continue
        for tag, lbl in (("primary", 20), ("secondary", 10)):
            t_ = c[tag]
            if t_["best_placebo_rank"] is None:
                continue
            exp = t_["null_rank"].get("expected_best_rank")
            plc.append(dict(cell=f"{sym}_{tf}m_{w}d", floor=lbl,
                            n_total=t_["n_total"], n_placebo=t_["n_placebo"],
                            best_rank=t_["best_placebo_rank"], null_expected=exp,
                            better_than_null=bool(exp and t_["best_placebo_rank"] < exp),
                            in_top10=t_["placebo_in_top10"],
                            kind=(t_["best_placebo"] or {}).get("arm"),
                            p_top10_under_null=t_["null_rank"].get("p_best_placebo_in_top10")))
    beat = sum(1 for p in plc if p["better_than_null"])
    top10 = sum(1 for p in plc if p["in_top10"])

    # replication
    rep_out = {}
    for sym, r in reps.items():
        for tf, blk in r.items():
            if not isinstance(blk, dict) or "summary" not in blk:
                continue
            f20 = blk.get("ranked_ids_f20", [])
            f10 = blk.get("ranked_ids_f10", [])
            def tally(ids):
                got = [blk["summary"][i] for i in ids if i in blk["summary"]]
                return dict(
                    n=len(got),
                    positive_in_all_3=sum(1 for g in got
                                          if g["slices_run"] == 3 and g["slices_positive"] == 3),
                    positive_in_2=sum(1 for g in got if g["slices_positive"] == 2),
                    traded_in_all_3=sum(1 for g in got if g["slices_with_trades"] == 3),
                    detail=[dict(id=i, exps=blk["summary"][i]["exps"],
                                 ns=blk["summary"][i]["ns"])
                            for i in ids if i in blk["summary"]])
            rep_out[f"{sym}_{tf}m"] = dict(slices=blk["slices"],
                                           floor20_top10=tally(f20),
                                           floor10_top10=tally(f10))

    # grains vs index complex, per-cell statistics combined by sign test
    comp = {}
    for group, syms in (("grains", GRAINS), ("index", INDEX)):
        rows = []
        for (sym, tf, w), c in sorted(cells.items()):
            if sym not in syms or c.get("skipped"):
                continue
            cen = c["census"]
            rows.append(dict(cell=f"{sym}_{tf}m_{w}d",
                             pop_f1=cen["1"]["collapsed"], pop_f20=cen["20"]["collapsed"],
                             med_exp_f1=cen["1"]["median_exp"],
                             pct_pos_f1=cen["1"]["pct_pos"],
                             med_exp_f20=cen["20"]["median_exp"],
                             pct_pos_f20=cen["20"]["pct_pos"],
                             best_t_f20=cen["20"]["best_t"],
                             median_n_f1=cen["1"]["median_n"],
                             best_plc_rank=c["primary"]["best_placebo_rank"],
                             null_exp=c["primary"]["null_rank"].get("expected_best_rank")))
        comp[group] = dict(cells=rows, n_cells=len(rows))
        for f in ("med_exp_f1", "pct_pos_f1", "med_exp_f20", "pct_pos_f20"):
            v = [r[f] for r in rows if r[f] is not None]
            comp[group]["median_" + f] = round(st.median(v), 4) if v else None
        pos = sum(1 for r in rows if (r["med_exp_f1"] or 0) > 0)
        comp[group]["cells_with_positive_median_exp_f1"] = pos
        comp[group]["sign_test_p_f1"] = sign_test(pos, len(rows))

    rob = {
        "about": {
            "checked": [
                "overfitting / data-mining bias (free_t deflation per cell, seed "
                "sensitivity, 60/40 out-of-sample, disjoint-slice replication)",
                "parameter sensitivity (three independent generation seeds)",
                "insufficient sample size (floor-free census at floors 1/5/10/20/30/50)",
                "unrealistic fills (fill model read directly: entry at next open with "
                "adverse slippage, stop wins ties, gaps honoured)",
                "understated costs and slippage (target/time/session legs charged "
                "0.5/1.0/2.0 extra ticks analytically)",
                "look-ahead bias and repainting (prefix-stability: truncate the series "
                "and require bit-identical trades before the cut)",
                "survivorship bias (single continuous front-month series per symbol; "
                "no cross-sectional selection is performed)",
                "future-data leakage (contract specs verified; dollar P&L reconstructed "
                "from leg prices x point value for every trade)",
                "placebo control (worker 1's placebo.py; three kinds, ranked in the "
                "same table)",
            ],
            "not_checked": [
                "roll/continuation methodology of the supplied CSVs - the series are "
                "taken as given and a bad splice would show up as a phantom gap trade",
                "intrabar order of stop and target beyond the engine's pessimistic rule",
                "live liquidity in the micro grains: MZC/MZS/MZW 60m bars routinely "
                "show single-digit volume, so real fills would be worse than modelled",
            ],
        },
        "placebo_verdict": {
            "cells_evaluated": len(plc),
            "cells_where_best_placebo_beat_its_null_expectation": beat,
            "cells_where_a_placebo_entered_the_top_10": top10,
            "per_cell": plc,
            "how_to_read": ("With k placebos among N rows and no edge anywhere, "
                            "E[best placebo rank] = (N+1)/(k+1). A placebo at rank 1 in "
                            "a 4-row table is not news; a placebo at rank 1 in a 100-row "
                            "table with 10 placebos is."),
        },
        "disjoint_slice_replication": rep_out,
        "grains_vs_index_complex": comp,
        "dollar_audit": load_audit("dollar").get("dollar_audit", {}),
        "exit_slippage_stress": load_audit("slip").get("exit_slippage_stress", {}),
        "prefix_stability": load_audit("prefix").get("prefix_stability", {}),
        "oos_split_60_40": load_audit("oos").get("oos_split", {}),
        "seed_sensitivity": load_audit("seed").get("seed_sensitivity", {}),
        "clock_conditions_at_240m": load_audit("clock").get("clock_audit", {}),
        "tradeability": load_audit("liquidity"),
        "series_quality": load_audit("series_quality"),
        "roll_gap_scan": load_audit("rollgap"),
        "fill_model_audit": load_audit("fill"),
        "stop_fill_quality": load_audit("stopfill"),
        "clean_feed_arm": load_audit("cleanfeed"),
        "rth_only_arm_D24": {k: v for f in sorted(os.listdir(CELLS))
                             if f.startswith("rth_arm_") and f.endswith(".json")
                             for k, v in json.load(open(os.path.join(CELLS, f))).items()},
        "deflation": {
            "rule": "free_t(screened) = sqrt(2 ln screened); nothing is evidence below it",
            "per_cell": [dict(cell=f"{s}_{tf}m_{w}d", screened=c["generated"],
                              free_t=c["primary"]["free_t"],
                              rows_clearing_floor20=c["primary"]["n_clearing_free_t"],
                              rows_clearing_floor10=c["secondary"]["n_clearing_free_t"],
                              best_t_floor20=(c["primary"]["rows"][0]["t"]
                                              if c["primary"]["rows"] else None))
                         for (s, tf, w), c in sorted(cells.items()) if not c.get("skipped")],
        },
    }
    rob["deflation"]["total_rows_clearing_free_t"] = sum(
        d["rows_clearing_floor20"] + d["rows_clearing_floor10"]
        for d in rob["deflation"]["per_cell"])
    for nm in ("rank_nq_es_grains_robustness_report.json", "robustness_report.json"):
        with open(f"{PUB}/{nm}", "w") as fh:
            json.dump(rob, fh, indent=1, default=str)

    print(json.dumps({
        "cells": len(cells),
        "placebo_cells": len(plc),
        "placebo_beat_null": beat,
        "placebo_in_top10": top10,
        "rows_clearing_free_t": rob["deflation"]["total_rows_clearing_free_t"],
    }, indent=1))


if __name__ == "__main__":
    main()
