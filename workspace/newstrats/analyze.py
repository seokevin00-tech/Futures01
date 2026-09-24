"""Per-cell analysis of the geometry experiment.

Two views of the same runs:

* **trade level** - within one cell, the R series of one arm against the R
  series of another arm on the same bars. This is where the power is.
* **strategy level** - the toolkit's house method: the distribution of
  expectancies across the 13 partner filters, with and without the geometry
  condition, rank-sum tested.

Nothing is pooled across cells. Cells are combined with Stouffer and with a
sign test, because pooling trades across symbols and periods inflates z by
roughly 3x - that is the single most reproducible defect in this project.
"""
from __future__ import annotations

import json
import math
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, "workspace/studies")
sys.path.insert(0, "workspace/newstrats")
import toolkit as T   # noqa: E402

ROWS = json.load(open("workspace/strategy_research/scratch/geo_rows.json"))
TRADES = json.load(open("workspace/strategy_research/scratch/geo_trades.json"))

IS_SLICES = {"S1", "S2"}
OOS_SLICES = {"S3"}
INDEP = {"MGC", "MCL"}          # not part of the index complex


def norm_cdf(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def stouffer(zs):
    zs = [z for z in zs if z is not None]
    if not zs:
        return None
    Z = sum(zs) / math.sqrt(len(zs))
    return {"k": len(zs), "z": round(Z, 3),
            "p": round(2 * (1 - norm_cdf(abs(Z))), 5)}


def sign_test(diffs):
    d = [x for x in diffs if x is not None and x != 0]
    if not d:
        return None
    pos = sum(1 for x in d if x > 0)
    n = len(d)
    # exact two-sided binomial
    p = 2 * sum(math.comb(n, k) for k in range(min(pos, n - pos) + 1)) / 2 ** n
    return {"n": n, "pos": pos, "neg": n - pos, "p": round(min(1.0, p), 5)}


def tstat(v):
    if len(v) < 3:
        return None
    sd = st.pstdev(v)
    return round(st.fmean(v) / (sd / math.sqrt(len(v))), 3) if sd else None


def arm_trades(exitm, cell, arm):
    return [t for t in TRADES
            if t["cell"] == cell and t["arm"] == arm and t["exitm"] == exitm]


def rr(v):
    return [x["r"] for x in v]


def describe(v):
    r = rr(v)
    if not r:
        return {"n": 0}
    wins = [x for x in r if x > 0]
    losses = [x for x in r if x <= 0]
    return {"n": len(r), "exp": round(st.fmean(r), 4),
            "median_r": round(st.median(r), 4),
            "win": round(len(wins) / len(r), 4),
            "avg_win": round(st.fmean(wins), 4) if wins else 0.0,
            "avg_loss": round(st.fmean(losses), 4) if losses else 0.0,
            "t": tstat(r),
            "mae": round(st.fmean([x["mae"] for x in v]), 3),
            "mfe": round(st.fmean([x["mfe"] for x in v]), 3),
            "mins": round(st.fmean([x["mins"] for x in v]), 1)}


CELLS = sorted({t["cell"] for t in TRADES})


def contest(arm_a, arm_b, exitm="atr1.0", subtract=False, min_n=8):
    """Matched per-cell comparison of two arms' R series.

    ``subtract`` removes arm A's trades from arm B first, which turns
    "geometry versus control" into "the bars geometry picked versus the bars it
    left behind" rather than "a subset versus its own superset".
    """
    per = []
    for cell in CELLS:
        A = arm_trades(exitm, cell, arm_a)
        B = arm_trades(exitm, cell, arm_b)
        if subtract:
            keys = {(x["ts"], x["dir"]) for x in A}
            B = [x for x in B if (x["ts"], x["dir"]) not in keys]
        if len(A) < min_n or len(B) < min_n:
            per.append({"cell": cell, "skipped": True,
                        "n_a": len(A), "n_b": len(B)})
            continue
        u = T.mann_whitney_u(rr(A), rr(B))
        sym, tf, sl = cell.split("_")
        per.append({"cell": cell, "symbol": sym, "tf": int(tf), "slice": sl,
                    "a": describe(A), "b": describe(B), "z": u["z"],
                    "delta_exp": round(st.fmean(rr(A)) - st.fmean(rr(B)), 4)})
    return per


def summarise(per, tag):
    def grp(f):
        g = [p for p in per if not p.get("skipped") and f(p)]
        return {"cells": len(g),
                "stouffer": stouffer([p["z"] for p in g]),
                "sign_test_on_delta_exp": sign_test([p["delta_exp"] for p in g]),
                "median_delta_exp": (round(st.median([p["delta_exp"] for p in g]), 4)
                                     if g else None),
                "trades_a": sum(p["a"]["n"] for p in g),
                "trades_b": sum(p["b"]["n"] for p in g),
                "cells_positive": sum(1 for p in g if p["delta_exp"] > 0)}
    return {
        "comparison": tag,
        "skipped_cells": [p["cell"] for p in per if p.get("skipped")],
        "IN_SAMPLE_S1_S2": grp(lambda p: p["slice"] in IS_SLICES),
        "OUT_OF_SAMPLE_S3": grp(lambda p: p["slice"] in OOS_SLICES),
        "ALL": grp(lambda p: True),
        "INDEPENDENT_SYMBOLS_ONLY_MGC_MCL": grp(lambda p: p["symbol"] in INDEP),
        "BY_TF_240": grp(lambda p: p["tf"] == 240),
        "BY_TF_60": grp(lambda p: p["tf"] == 60),
        "per_cell": per,
    }


# ---------------------------------------------------------------- strategy level
def strategy_level(arm_a, arm_b):
    """Rank-sum on expectancy across the 13 partner filters, per cell."""
    out = []
    for cell in CELLS:
        for ex in ("atr1.0", "atr1.5"):
            A = [r for r in ROWS if r["cell"] == cell and r["arm"] == arm_a
                 and r["exitm"] == ex and r["n"] >= 5]
            B = [r for r in ROWS if r["cell"] == cell and r["arm"] == arm_b
                 and r["exitm"] == ex and r["n"] >= 5]
            if len(A) < 5 or len(B) < 5:
                continue
            u = T.mann_whitney_u([r["exp"] for r in A], [r["exp"] for r in B])
            sym, tf, sl = cell.split("_")
            out.append({"cell": cell, "symbol": sym, "tf": int(tf), "slice": sl,
                        "exit": ex, "n_a": len(A), "n_b": len(B), "z": u["z"],
                        "median_exp_a": round(st.median([r["exp"] for r in A]), 4),
                        "median_exp_b": round(st.median([r["exp"] for r in B]), 4)})
    def grp(f):
        g = [p for p in out if f(p)]
        d = [p["median_exp_a"] - p["median_exp_b"] for p in g]
        return {"cells": len(g), "stouffer": stouffer([p["z"] for p in g]),
                "sign_test": sign_test(d),
                "median_delta": round(st.median(d), 4) if d else None}
    return {"IN_SAMPLE_S1_S2": grp(lambda p: p["slice"] in IS_SLICES),
            "OUT_OF_SAMPLE_S3": grp(lambda p: p["slice"] in OOS_SLICES),
            "ALL": grp(lambda p: True), "per_cell": out}
