"""Worker 1's cells: MGC and MCL, 60m and 240m, four nested windows + three
disjoint thirds.

MGC and MCL are the two genuinely independent contracts in this programme -
MNQ/NQ/MES/ES are one index complex, and agreement among those four is not four
pieces of evidence. Nothing here is pooled across the two symbols and nothing
measured on one is claimed for the other.

The four windows are **nested by construction**: 30d is a subset of 90d is a
subset of 180d is a subset of 274d, because all four end on the last bar in the
data. A strategy appearing in all four top tens is therefore one observation
seen four times, not four confirmations. The only genuinely independent periods
here are the three disjoint thirds, and the replication column is built from
those and from nothing else.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from typing import Dict, List, Optional

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")
sys.path.insert(0, "/home/user/Futures01/workspace/newstrats")
os.chdir("/home/user/Futures01")

import toolkit as T          # noqa: E402
import rank as R             # noqa: E402

SYMBOLS = ("MGC", "MCL")
TFS = (60, 240)
WINDOWS = (30, 90, 180, 274)
BUDGET = 2400
SEED = 1
OUTDIR = "/home/user/Futures01/workspace/strategy_research"
DB = f"{OUTDIR}/rank_mgc_mcl_performance_db.json"

CAVEATS = [
    "The four windows are nested (30d subset of 90d subset of 180d subset of "
    "274d). Agreement across them is one observation seen four times, not "
    "replication. Only the three disjoint thirds carry replication weight.",
    "Per symbol, never pooled. MGC and MCL are the two independent contracts "
    "here; no result from one is evidence about the other.",
    "rth_only forced False on the whole generated population (D24): left True "
    "it reduces the 4h population to one bar per day and cost this cell a "
    "measured 102 vs 377 trades and 0 vs 6 floor-clearers on MGC 60m/300 rule "
    "sets. Applied identically to reals and placebos.",
    "OPENING_RANGE dropped at 60m and 240m: an L-minute opening range is "
    "resolvable on a T-minute frame only if T divides L and T divides the RTH "
    "open in minutes from midnight (D39); 240m divides no OR length at all, "
    "and MGC's 08:20 open is not divisible by 60. Measured before dropping: 76 "
    "rule sets, 10 trades between them.",
    "exit_at_session_close=True exits dropped at 240m only: it closes a 4-hour "
    "trade on its entry bar, median hold 0.0 minutes, so those rows are a "
    "different instrument rather than a slow strategy with a bad exit.",
    "Clone collapse uses toolkit.fingerprint, which keys on (entry timestamp, "
    "direction) and ignores the exit. groups_spanning_different_exits counts "
    "how often that merged rule sets with different stop geometry; it was 0 in "
    "every cell run here.",
    "A placebo rank must be read against null_rank. With k placebos among N "
    "rows and no edge anywhere, the best placebo's expected rank is "
    "(N+1)/(k+1) - about 11 at a 10% share. 'A placebo in the top 10' is only "
    "news relative to that number, which is why it is reported beside it.",
]


def _store() -> dict:
    if os.path.exists(DB):
        try:
            return json.load(open(DB))
        except Exception:
            pass
    return {"cells": {}, "caveats": CAVEATS}


def _flush(store: dict) -> None:
    with open(DB, "w") as fh:
        json.dump(store, fh, default=str)


def _compact(row: dict) -> dict:
    keep = ("rank", "id", "name", "group", "n", "win", "win_lo", "win_hi", "exp",
            "payoff", "pf", "maxdd", "avgdd", "t", "free_t", "clears_free_t",
            "sharpe", "sortino", "avg_win", "avg_loss", "max_cons_win",
            "max_cons_loss", "avg_minutes", "mae", "mfe", "longs", "shorts",
            "clones", "stop_kind", "stop_mult", "target_kind",
            "session_close_exit", "is_placebo", "placebo_kind", "base_name",
            "signals", "filters")
    return {k: row[k] for k in keep if k in row}


def _payload(cell: dict) -> dict:
    """What goes into the study file: the head of the table, every control, and
    the diagnostics needed to read them - not 400 rows of tail."""
    rows = cell.get("rows", [])
    return {k: cell[k] for k in cell if k not in ("rows", "watch")} | {
        "top25": [_compact(r) for r in rows[:25]],
        "all_placebo_rows": [_compact(r) for r in rows if r["is_placebo"]],
        "tail_summary": {
            "n_rows_beyond_25": max(0, len(rows) - 25),
            "median_exp_all_rows": (sorted(r["exp"] for r in rows)[len(rows) // 2]
                                    if rows else None),
            "pct_positive": (round(sum(1 for r in rows if r["exp"] > 0) / len(rows), 3)
                             if rows else None)},
    }


def main(symbols=SYMBOLS, tfs=TFS, windows=WINDOWS) -> dict:
    store = _store()
    t_start = time.time()

    # ---------------- phase 1: the four nested windows ------------------
    for tf in tfs:
        for w in windows:
            study: Dict[str, dict] = {}
            for sym in symbols:
                key = f"{sym}_{tf}_{w}"
                cell = R.rank_cell(sym, tf, w, budget=BUDGET, seed=SEED)
                store["cells"][key] = cell
                _flush(store)                       # the moment it finishes
                study[sym] = _payload(cell)
                T.save(f"rank_mgc_mcl_{tf}_{w}",
                       f"Recurring profitability ranking, {tf}m, last {w} days",
                       "Which rule sets rank highest by expectancy in R net of "
                       "costs - and where does the best placebo land beside "
                       "them?",
                       {"design": {"windows_are_nested": True, "budget": BUDGET,
                                   "seed": SEED, "floor": R.FLOOR,
                                   "placebo_share_target": 0.10,
                                   "placebo_kinds": list(R.P.KINDS)},
                        "cells": study},
                       _headline(study), CAVEATS)
                print(f"saved rank_mgc_mcl_{tf}_{w} ({sym} done, "
                      f"{time.time()-t_start:.0f}s total)", flush=True)

    # ---------------- phase 2: the three disjoint thirds ----------------
    longest = max(windows)
    for tf in tfs:
        slices_by_symbol = {s: T.disjoint_slices(s, tf, 3) for s in symbols}
        watch = {}
        for sym in symbols:
            full = store["cells"].get(f"{sym}_{tf}_{max(windows)}", {})
            watch[sym] = [r["id"] for r in full.get("rows", [])
                          if not r["is_placebo"]][:10]
        for si in range(3):
            study = {}
            for sym in symbols:
                lo, hi = slices_by_symbol[sym][si]
                key = f"{sym}_{tf}_slice{si}"
                cell = R.rank_cell(sym, tf, None, budget=BUDGET, seed=SEED,
                                   slice_bounds=(lo, hi), watch_ids=watch[sym])
                store["cells"][key] = cell
                _flush(store)
                study[sym] = _payload(cell) | {"watch": cell.get("watch", {})}
                T.save(f"rank_mgc_mcl_{tf}_slice{si}",
                       f"Disjoint third {si + 1} of 3, {tf}m",
                       "Do the 9-month top ten hold up in a period that does "
                       "not overlap the one they were found in?",
                       {"design": {"disjoint": True, "bounds_days_ago":
                                   {s: list(slices_by_symbol[s][si]) for s in symbols},
                                   "watchlist_from": "274d top 10 real rows",
                                   "budget": BUDGET, "seed": SEED},
                        "cells": study},
                       _headline(study), CAVEATS)
                print(f"saved rank_mgc_mcl_{tf}_slice{si} ({sym} done, "
                      f"{time.time()-t_start:.0f}s total)", flush=True)

    # ---------------- phase 3: replication + pooled placebo audit -------
    summary = _summarise(store, symbols, tfs, windows)
    store["summary"] = summary
    _flush(store)
    with open(f"{OUTDIR}/rank_mgc_mcl_strategy_rankings.json", "w") as fh:
        json.dump(summary, fh, indent=1, default=str)
    T.save("rank_mgc_mcl_summary",
           "MGC and MCL: rankings, controls and replication",
           "Across every cell, where did the best placebo land, what replicated "
           "in disjoint periods, and did anything clear deflation?",
           summary, summary["headline"], CAVEATS)
    print(f"DONE {time.time()-t_start:.0f}s", flush=True)
    return summary


def _headline(study: Dict[str, dict]) -> str:
    bits = []
    for sym, c in study.items():
        if c.get("skipped"):
            bits.append(f"{sym}: skipped ({c['skipped']})")
            continue
        nr = c.get("null_rank", {}) or {}
        bits.append(
            f"{sym}: best placebo rank {c.get('best_placebo_rank')} of "
            f"{c.get('n_rows')} (null E={nr.get('expected_best_rank')}), "
            f"{c.get('n_clear_free_t')} of {c.get('n_rows')} clear free_t="
            f"{c.get('free_t')}")
    return "; ".join(bits)


def _summarise(store: dict, symbols, tfs, windows) -> dict:
    cells = store["cells"]
    placebo_ranks, pooled_norm = [], []
    rowsum = []
    for key, c in sorted(cells.items()):
        if c.get("skipped"):
            rowsum.append({"cell": key, "skipped": c["skipped"]})
            continue
        N = c["n_rows"]
        for r in c["rows"]:
            if r["is_placebo"] and N:
                pooled_norm.append((r["rank"] - 0.5) / N)
        placebo_ranks.append({
            "cell": key, "n_rows": N, "n_placebo_rows": c["n_rows_placebo"],
            "best_placebo_rank": c["best_placebo_rank"],
            "by_kind": c["best_placebo_rank_by_kind"],
            "null_expected": (c.get("null_rank") or {}).get("expected_best_rank"),
            "p_top10_under_null": (c.get("null_rank") or {}).get(
                "p_best_placebo_in_top10"),
            "placebo_in_top10": (c["best_placebo_rank"] is not None
                                 and c["best_placebo_rank"] <= 10)})
        rowsum.append({
            "cell": key, "bars": c["bars"], "screened": c["n_screened"],
            "rows": N, "free_t": c["free_t"], "clear_free_t": c["n_clear_free_t"],
            "positive": c["n_positive"],
            "best_placebo_rank": c["best_placebo_rank"],
            "top10": [{"rank": r["rank"], "name": r["name"][:60],
                       "group": r["group"], "n": r["n"], "exp": r["exp"],
                       "t": r["t"], "win": r["win"],
                       "wilson": [r["win_lo"], r["win_hi"]],
                       "payoff": r["payoff"], "maxdd": r["maxdd"],
                       "is_placebo": r["is_placebo"],
                       "placebo_kind": r["placebo_kind"]}
                      for r in c["rows"][:10]]})

    # replication: 274d top 10 vs the three disjoint thirds
    repl = {}
    for sym in symbols:
        for tf in tfs:
            full = cells.get(f"{sym}_{tf}_{max(windows)}", {})
            top = [r for r in full.get("rows", []) if not r["is_placebo"]][:10]
            slices = [cells.get(f"{sym}_{tf}_slice{i}", {}) for i in range(3)]
            items = []
            for r in top:
                w = [(s.get("watch") or {}).get(r["id"]) for s in slices]
                exps = [(x or {}).get("exp") for x in w]
                ns = [(x or {}).get("n") for x in w]
                pos = sum(1 for e in exps if e is not None and e > 0)
                traded = sum(1 for n in ns if n)
                items.append({
                    "rank": r["rank"], "name": r["name"][:60], "id": r["id"],
                    "n_full": r["n"], "exp_full": r["exp"],
                    "slice_exp": exps, "slice_n": ns,
                    "positive_in_all_three": pos == 3 and traded == 3,
                    "n_slices_positive": pos, "n_slices_traded": traded})
            repl[f"{sym}_{tf}"] = {
                "n_top10": len(items),
                "positive_in_all_three": sum(1 for x in items
                                             if x["positive_in_all_three"]),
                "items": items}

    ks = R._ks_uniform(pooled_norm) if len(pooled_norm) >= 5 else {}
    n_in_top10 = sum(1 for p in placebo_ranks if p["placebo_in_top10"])
    n_cells = len(placebo_ranks)
    exp_top10 = sum((p["p_top10_under_null"] or 0) for p in placebo_ranks)
    total_clear = sum(r.get("clear_free_t", 0) for r in rowsum)
    total_repl = sum(v["positive_in_all_three"] for v in repl.values())
    headline = (
        f"A placebo landed inside the top 10 in {n_in_top10} of {n_cells} cells "
        f"(expected {exp_top10:.1f} under the no-edge null). Best placebo rank "
        f"by cell: "
        + ", ".join(f"{p['cell']}={p['best_placebo_rank']}/{p['n_rows']}"
                    for p in placebo_ranks)
        + f". {total_clear} rows cleared free_t anywhere; "
        f"{total_repl} of the 9-month top-10 rows were positive in all three "
        f"disjoint thirds.")
    return {"headline": headline,
            "placebo_ranks": placebo_ranks,
            "pooled_placebo_rank_uniformity": {
                "n": len(pooled_norm), "ks_vs_uniform": ks,
                "mean_normalised_rank": (round(sum(pooled_norm) / len(pooled_norm), 4)
                                         if pooled_norm else None),
                "reading": "Under the project's standing null - no rule set here "
                           "has an edge - the controls' normalised ranks should be "
                           "uniform. Systematically high (near 1.0) would mean the "
                           "matching handicaps them and every real row above them "
                           "is flattered; systematically low would mean the reals "
                           "are worse than noise."},
            "replication_disjoint_thirds": repl,
            "cells": rowsum,
            "caveats": CAVEATS}


if __name__ == "__main__":
    main()
