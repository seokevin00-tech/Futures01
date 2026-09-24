"""Combine matched-arm cells: per-cell sign z, Stouffer, and a declared OOS split."""
from __future__ import annotations
import json, math, sys
sys.path.insert(0, "/home/user/Futures01/workspace/newstrats")
import w6_arms as A  # noqa: E402

SC = "/home/user/Futures01/workspace/strategy_research/scratch/ict"


def combine(path, key="paired", variants=None, is_slices=(0, 1), oos_slices=(2,), tf=None):
    d = json.load(open(path))
    cells = d["cells"]
    variants = variants or sorted({v for c in cells.values() for v in c[key]})
    out = {}
    for v in variants:
        rows = []
        for ck, c in cells.items():
            if tf is not None and c["tf"] != tf:
                continue
            p = c[key].get(v)
            if not p or not p.get("n_pairs"):
                continue
            rows.append({"cell": ck, "symbol": c["symbol"], "tf": c["tf"],
                         "slice": c.get("slice", c.get("period")), **p})
        if not rows:
            continue
        def pick(sel):
            return [r for r in rows if str(r["slice"])[-1:].isdigit()
                    and int(str(r["slice"])[-1:]) in sel]
        out[v] = {
            "all_cells": A.stouffer(rows),
            "IS_cells": A.stouffer(pick(is_slices)),
            "OOS_cells": A.stouffer(pick(oos_slices)),
            "per_cell": [{"cell": r["cell"], "z": r["sign_z"], "pairs": r["n_pairs"],
                          "better": r["better"], "worse": r["worse"],
                          "med_dexp": r["median_delta_exp"],
                          "n_treated": r["median_n_treated"],
                          "n_control": r["median_n_control"]} for r in rows],
            "median_delta_exp_across_cells": round(
                sorted(r["median_delta_exp"] for r in rows)[len(rows) // 2], 4),
            "total_trades_treated": sum(r["total_trades_treated"] for r in rows),
            "total_trades_control": sum(r["total_trades_control"] for r in rows)}
    return out
