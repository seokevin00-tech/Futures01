"""Parameter sensitivity of the geometry conditions.

If the headline comparison changes sign when the swing fractal moves from 3
bars to 2 or 5, or when "three consecutive legs" becomes "two", then whatever
was measured was the parameter, not the market. Slim arms only (no partner
filters) so the sweep is cheap.
"""
from __future__ import annotations

import json
import math
import statistics as st
import sys

sys.path.insert(0, "workspace/studies")
sys.path.insert(0, "workspace/newstrats")

import geometry as G                                        # noqa: E402
import toolkit as T                                         # noqa: E402
from futures_agents.backtest.engine import run_portfolio    # noqa: E402
from futures_agents.features import build_symbol_frame      # noqa: E402
from futures_agents.scout import FRAMES                     # noqa: E402
from futures_agents.strategies.base import StrategyFilters  # noqa: E402
from run_geometry import EXITS, SYMBOLS, TFS                # noqa: E402

ARMS = ["CONTROL", "legs_expanding", "legs_contracting", "pullbacks_shallowing",
        "pullbacks_deepening", "swing_symmetry_impulse", "swing_symmetry_retrace"]


def norm_cdf(z):
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def stouffer(zs):
    zs = [z for z in zs if z is not None]
    if not zs:
        return None
    Z = sum(zs) / math.sqrt(len(zs))
    return {"k": len(zs), "z": round(Z, 3), "p": round(2 * (1 - norm_cdf(abs(Z))), 5)}


def run(symbol, tf, lo, hi, fractal):
    series = T.slice_series(symbol, tf, lo, hi)
    if len(series) < 150:
        return {}
    frame = build_symbol_frame(series, FRAMES[tf])
    G.clear()
    for t, tff in frame.frames.items():
        G.register(frame.symbol, t, tff.series.bars, fractal, fractal)
    tagged, strategies = [], []
    for arm in ARMS:
        cs = [G.get("structure_trend_ctl")]
        if arm != "CONTROL":
            cs.append(G.get(arm))
        for ename, ex in EXITS.items():
            s = T.make_strategy(symbol, tf, cs, group="SENS",
                                name=f"{arm}|{ename}", exit_model=ex,
                                filters=StrategyFilters(rth_only=False))
            tagged.append((arm, ename, s))
            strategies.append(s)
    res = run_portfolio(frame, strategies)
    out = {}
    for arm, ename, s in tagged:
        out[(arm, ename)] = [t.net_r for t in res[s.strategy_id].trades]
    return out


def sweep(fractal, n_imp, min_atr, sym_hi, sym_lo, n_ratio, sym_legs=4):
    G.MIN_ATR, G.N_IMPULSE, G.N_RATIO = min_atr, n_imp, n_ratio
    G.SYM_HI, G.SYM_LO, G.SYM_LEGS = sym_hi, sym_lo, sym_legs
    per = {}
    for sym in SYMBOLS:
        for tf in TFS:
            sl = T.disjoint_slices(sym, tf, 3)
            for k, (lo, hi) in enumerate(sl, 1):
                per[f"{sym}_{tf}_S{k}"] = run(sym, tf, lo, hi, fractal)
    return per


def contest(per, a, b, slices, ex="atr1.0", min_n=8):
    zs, deltas, na, nb = [], [], 0, 0
    for cell, d in per.items():
        if cell.split("_")[2] not in slices or not d:
            continue
        A, B = d.get((a, ex), []), d.get((b, ex), [])
        if len(A) < min_n or len(B) < min_n:
            continue
        zs.append(T.mann_whitney_u(A, B)["z"])
        deltas.append(st.fmean(A) - st.fmean(B))
        na += len(A)
        nb += len(B)
    return {"stouffer": stouffer(zs), "median_delta_exp":
            round(st.median(deltas), 4) if deltas else None,
            "cells": len(zs), "trades_a": na, "trades_b": nb}


GRID = [
    ("baseline   f=3 n=3 atr=0.5", dict(fractal=3, n_imp=3, min_atr=0.5, sym_hi=0.55, sym_lo=0.45, n_ratio=3)),
    ("fractal 2  f=2 n=3 atr=0.5", dict(fractal=2, n_imp=3, min_atr=0.5, sym_hi=0.55, sym_lo=0.45, n_ratio=3)),
    ("fractal 5  f=5 n=3 atr=0.5", dict(fractal=5, n_imp=3, min_atr=0.5, sym_hi=0.55, sym_lo=0.45, n_ratio=3)),
    ("legs 2  f=3 n=2 atr=0.5", dict(fractal=3, n_imp=2, min_atr=0.5, sym_hi=0.55, sym_lo=0.45, n_ratio=2)),
    ("legs 4  f=3 n=4 atr=0.5", dict(fractal=3, n_imp=4, min_atr=0.5, sym_hi=0.55, sym_lo=0.45, n_ratio=4)),
    ("no size gate atr=0.0", dict(fractal=3, n_imp=3, min_atr=0.0, sym_hi=0.55, sym_lo=0.45, n_ratio=3)),
    ("size gate atr=1.5", dict(fractal=3, n_imp=3, min_atr=1.5, sym_hi=0.55, sym_lo=0.45, n_ratio=3)),
    ("symmetry 0.65/0.35", dict(fractal=3, n_imp=3, min_atr=0.5, sym_hi=0.65, sym_lo=0.35, n_ratio=3)),
    ("symmetry window 6 legs", dict(fractal=3, n_imp=3, min_atr=0.5, sym_hi=0.55, sym_lo=0.45, n_ratio=3, sym_legs=6)),
]

if __name__ == "__main__":
    out = {}
    for label, kw in GRID:
        per = sweep(**kw)
        res = {}
        for a, b in [("legs_expanding", "legs_contracting"),
                     ("pullbacks_shallowing", "pullbacks_deepening"),
                     ("swing_symmetry_impulse", "swing_symmetry_retrace"),
                     ("legs_expanding", "CONTROL")]:
            res[f"{a}_vs_{b}"] = {
                "IS_S1S2": contest(per, a, b, {"S1", "S2"}),
                "OOS_S3": contest(per, a, b, {"S3"})}
        out[label] = res
        print("==", label)
        for k, v in res.items():
            print(f"   {k:46s} IS {v['IS_S1S2']['stouffer']} d={v['IS_S1S2']['median_delta_exp']}"
                  f"  | OOS {v['OOS_S3']['stouffer']} d={v['OOS_S3']['median_delta_exp']}")
        print(flush=True)
    json.dump(out, open("workspace/strategy_research/scratch/sensitivity2.json", "w"),
              default=str)
