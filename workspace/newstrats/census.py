"""Firing-rate + geometry census. No strategies, no exits - just how often each
condition is true and what the underlying geometry distributions look like."""
from __future__ import annotations

import json
import os
import statistics as st
import sys

sys.path.insert(0, "workspace/studies")
sys.path.insert(0, "workspace/newstrats")

import geometry as G                                     # noqa: E402
import toolkit as T                                      # noqa: E402
from futures_agents.features import build_symbol_frame    # noqa: E402
from futures_agents.scout import FRAMES                   # noqa: E402
from futures_agents.strategies.library import get_condition  # noqa: E402

SYMBOLS = ["MGC", "MES", "MNQ", "MCL"]
TFS = [240, 60]
FIB = ["fib_golden_pocket", "fib_shallow_retrace"]


def census(symbol, tf):
    series = T._series(symbol, tf, None)
    frame = build_symbol_frame(series, FRAMES[tf])
    G.clear()
    G.reset_stats()
    G.register_frame(frame)
    names = G.GEOMETRY_NAMES + G.CONTROL_NAMES
    conds = {n: G.get(n) for n in names}
    fibs = {n: get_condition(n) for n in FIB}

    fired = {n: [] for n in list(conds) + FIB}   # bar indices
    n_bars = 0
    leg_atr, ratios, syms = [], [], []
    for snap in frame.iter_snapshots():
        s = snap.tf(tf)
        if s is None:
            continue
        n_bars += 1
        for n, c in conds.items():
            if c.fn(snap, tf).triggered:
                fired[n].append(snap.base_index)
        for n, c in fibs.items():
            if c.evaluate(snap, tf).triggered:
                fired[n].append(snap.base_index)
        # raw geometry distributions, ATR-normalised
        g = G._geo(snap, tf)
        atr = s.get("atr")
        if g and atr and s.structure_trend in ("UPTREND", "DOWNTREND"):
            up = s.structure_trend == "UPTREND"
            imp = g.impulses(up)
            if imp:
                leg_atr.append(imp[-1].size / atr)
            rr = [r for r in g.retrace_ratios(up) if 0 < r < 2]
            if rr:
                ratios.append(rr[-1])
            v = g.symmetry(up)
            if v is not None:
                syms.append(v)

    def q(v):
        if not v:
            return None
        v = sorted(v)
        return {"n": len(v), "p10": round(v[len(v) // 10], 3),
                "median": round(st.median(v), 3),
                "p90": round(v[min(len(v) - 1, 9 * len(v) // 10)], 3),
                "mean": round(st.fmean(v), 3)}

    rates = {n: round(len(f) / n_bars, 5) for n, f in fired.items()}
    sets = {n: set(f) for n, f in fired.items()}
    # overlap of the geometry conditions with the fib conditions
    ov = {}
    for a in ["pullbacks_shallowing", "pullbacks_deepening", "legs_expanding"]:
        for b in FIB:
            A, B = sets[a], sets[b]
            if not A or not B:
                continue
            ov[f"{a}|{b}"] = {
                "jaccard": round(len(A & B) / len(A | B), 4),
                "pct_of_A_also_B": round(len(A & B) / len(A), 4),
                "pct_of_B_also_A": round(len(A & B) / len(B), 4),
                "n_A": len(A), "n_B": len(B), "n_both": len(A & B)}
    # geometry vs its own control: what fraction of structure_trend bars survive
    ctl = sets["structure_trend_ctl"] | sets["structure_trend_fade_ctl"]
    sel = {n: (round(len(sets[n] & ctl) / len(ctl), 4) if ctl else None)
           for n in G.GEOMETRY_NAMES}
    # geometry vs geometry
    gg = {}
    for i, a in enumerate(G.GEOMETRY_NAMES):
        for b in G.GEOMETRY_NAMES[i + 1:]:
            A, B = sets[a], sets[b]
            if A and B:
                j = len(A & B) / len(A | B)
                if j > 0.02:
                    gg[f"{a}|{b}"] = round(j, 4)
    return {"symbol": symbol, "tf": tf, "bars": n_bars,
            "first_ts": str(series.bars[0].ts), "last_ts": str(series.bars[-1].ts),
            "firing_rates": rates, "selectivity_within_structure_trend": sel,
            "fib_overlap": ov, "geometry_overlap": gg,
            "dist_last_impulse_atr": q(leg_atr),
            "dist_last_retrace_ratio": q(ratios),
            "dist_symmetry": q(syms)}


if __name__ == "__main__":
    out = []
    for s in SYMBOLS:
        for tf in TFS:
            r = census(s, tf)
            out.append(r)
            print(f"{s} {tf}m bars={r['bars']}", flush=True)
            for n, v in sorted(r["firing_rates"].items()):
                print(f"   {n:32s} {v:8.4%}")
    os.makedirs("workspace/strategy_research/scratch", exist_ok=True)
    json.dump(out, open("workspace/strategy_research/scratch/census.json", "w"),
              indent=1, default=str)
    print("saved")
