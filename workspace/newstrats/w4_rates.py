"""Firing rates, bar-level Jaccard against the library, and parameter sensitivity.

Three jobs, in the order they have to be done in:

1. **Firing rate of every condition**, before any performance number. Under 1%
   cannot support a strategy; near 50% is barely a condition at all.
2. **Jaccard against the library.** Five conditions in this project turned out
   to be duplicates of another under a different name. ``ict_fvg_libclone`` is
   a deliberate re-implementation of ``fvg_nearby``'s exact bookkeeping inside
   this module, so the overlap number can separate "different idea" from
   "different fill rule".
3. **Parameter sensitivity.** The order-block detector has four knobs. If the
   result only exists at one corner of that grid it is a fitted artefact, and
   the grid is the cheapest way to see it.
"""
from __future__ import annotations

import json
import sys
from typing import Dict, List

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")

import toolkit as T                                    # noqa: E402
from futures_agents.features import build_symbol_frame  # noqa: E402
from futures_agents.scout import FRAMES                 # noqa: E402
from futures_agents.strategies.library import CONDITIONS  # noqa: E402

from workspace.newstrats import ict_blocks as I        # noqa: E402
from workspace.newstrats.w4_barlevel import (CELLS, H, K, N_PLACEBO, SCRATCH,
                                             score_zone, sign_test, stouffer,
                                             two_prop_z)

EXISTING = ["fvg_nearby", "zone_touch", "fresh_zone_approach", "away_from_zone",
            "imbalance_bar", "imbalance_pullback", "break_of_structure",
            "structure_trend", "range_position_extreme", "pullback_to_support"]


def jaccard(a, b):
    inter = sum(1 for x, y in zip(a, b) if x and y)
    union = sum(1 for x, y in zip(a, b) if x or y)
    return round(inter / union, 4) if union else 0.0


def rates_cell(sym, tf):
    series = T._series(sym, tf, None)
    frame = build_symbol_frame(series, FRAMES[tf])
    I.register_frame(frame, sym)
    I.register_placebo(sym, tf)
    names = I.ALL + ["ict_placebo_ob", "ict_placebo_fvg"] + EXISTING
    conds = {n: CONDITIONS[n] for n in names if n in CONDITIONS}
    masks = {n: [] for n in conds}
    dirs = {n: [] for n in conds}
    nb = 0
    for snap in frame.iter_snapshots():
        nb += 1
        cache = {}
        for n, c in conds.items():
            r = c.evaluate(snap, tf, cache)
            masks[n].append(1 if r.triggered else 0)
            dirs[n].append(r.direction.value if r.triggered else "")
    rates = {n: round(sum(m) / nb, 5) for n, m in masks.items()}
    jac = {m: {e: jaccard(masks[m], masks[e]) for e in EXISTING if e in masks}
           for m in I.ALL}
    dag = {}
    for m in ("ict_fvg_libclone", "ict_fvg_return", "ict_fvg_close_in",
              "ict_ob_return"):
        row = {}
        for e in ("fvg_nearby", "zone_touch", "fresh_zone_approach"):
            both = [(dirs[m][i], dirs[e][i]) for i in range(nb)
                    if masks[m][i] and masks[e][i]]
            row[e] = dict(n_both=len(both),
                          pct_same_dir=(round(sum(1 for x, y in both if x == y)
                                              / len(both), 3) if both else None))
        dag[m] = row
    bars, atr, obs, fvgs, states = I.CACHE[(sym.upper(), tf)]
    return dict(n_bars=nb, firing_rate=rates, jaccard_vs_existing=jac,
                direction_agreement=dag, n_order_blocks=len(obs),
                n_fvgs=len(fvgs),
                misses={k[0]: v for k, v in I.MISSES.items()
                        if k[1] == sym.upper() and k[2] == tf}), masks, nb


GRID = [dict(disp_atr=d, disp_bars=b, require_fvg=f, body_only=o)
        for d in (0.5, 1.0, 1.5, 2.0) for b in (2, 3, 5)
        for f in (True, False) for o in (False, True)]


def sensitivity_cell(sym, tf):
    """Every corner of the order-block parameter grid, scored identically."""
    bars = list(T._series(sym, tf, None).bars)
    n = len(bars)
    cut = int(n * 0.6)
    import random
    out = []
    for g in GRID:
        atr, obs, fvgs, states = I.build(bars, **g)
        rng = random.Random(5)
        valid = [i for i in range(30, n - H - K - 1) if atr[i]]
        rec = dict(**g, n_obs=len(obs))
        for per, lo, hi in (("IS", 0, cut), ("OOS", cut, n)):
            real, sham = [], []
            vv = [i for i in valid if lo <= i < hi - H - K - 1]
            if len(vv) < 200:
                continue
            for z in obs:
                v = z.confirmed_index
                if not (lo <= v < hi - H - K - 1) or not atr[v]:
                    continue
                bull = z.direction == "BULL"
                r = score_zone(bars, atr, v, z.proximal, z.distal, bull)
                if r is None:
                    continue
                real.append(r)
                da = abs(bars[v].close - z.proximal) / atr[v]
                wa = abs(z.top - z.bottom) / atr[v]
                for _ in range(4):
                    q = rng.choice(vv); aq, cq = atr[q], bars[q].close
                    p2 = cq - da * aq if bull else cq + da * aq
                    d2 = p2 - wa * aq if bull else p2 + wa * aq
                    s = score_zone(bars, atr, q, p2, d2, bull)
                    if s is not None:
                        sham.append(s)
            ba = [r for r in real if r.get("barrier") is not None]
            bb = [r for r in sham if r.get("barrier") is not None]
            if len(ba) < 40 or len(bb) < 40:
                continue
            rec[f"{per}_n"] = len(ba)
            rec[f"{per}_win"] = round(sum(1 for r in ba if r["barrier"]) / len(ba), 4)
            rec[f"{per}_sham"] = round(sum(1 for r in bb if r["barrier"]) / len(bb), 4)
            rec[f"{per}_z"] = round(two_prop_z(sum(1 for r in ba if r["barrier"]),
                                               len(ba),
                                               sum(1 for r in bb if r["barrier"]),
                                               len(bb)), 3)
            rec[f"{per}_touch"] = round(sum(r["touch"] for r in real) / len(real), 4)
            rec[f"{per}_touch_sham"] = round(sum(r["touch"] for r in sham) / len(sham), 4)
        out.append(rec)
    return out


def main():
    out = {"cells": {}, "sensitivity": {}}
    for sym, tf in CELLS:
        cell, masks, nb = rates_cell(sym, tf)
        out["cells"][f"{sym}-{tf}m"] = cell
        r = cell["firing_rate"]
        print(f"{sym}-{tf}m n={nb} obs={cell['n_order_blocks']} "
              f"fvgs={cell['n_fvgs']}")
        for n in I.ALL:
            print(f"   {n:22s} {r[n]*100:6.2f}%  J(fvg_nearby)="
                  f"{cell['jaccard_vs_existing'][n].get('fvg_nearby',0):.3f} "
                  f"J(zone_touch)={cell['jaccard_vs_existing'][n].get('zone_touch',0):.3f} "
                  f"J(fresh_zone)={cell['jaccard_vs_existing'][n].get('fresh_zone_approach',0):.3f}")
        print("   [lib] " + "  ".join(f"{e}={r.get(e,0)*100:.2f}%" for e in
                                      ("fvg_nearby", "zone_touch",
                                       "fresh_zone_approach")))
        sys.stdout.flush()
    for sym, tf in CELLS[:5]:
        out["sensitivity"][f"{sym}-{tf}m"] = sensitivity_cell(sym, tf)
        print(f"sensitivity {sym}-{tf}m done")
        sys.stdout.flush()
    # how many of the 48 parameter corners give a positive OOS z, per cell
    summ = {}
    for c, rows in out["sensitivity"].items():
        zs = [r["OOS_z"] for r in rows if "OOS_z" in r]
        zi = [r["IS_z"] for r in rows if "IS_z" in r]
        summ[c] = dict(n_corners=len(zs),
                       pos_OOS=sum(1 for z in zs if z > 0),
                       best_OOS=max(zs) if zs else None,
                       worst_OOS=min(zs) if zs else None,
                       pos_IS=sum(1 for z in zi if z > 0),
                       best_IS=max(zi) if zi else None)
        print(f"GRID {c}: {summ[c]}")
    out["sensitivity_summary"] = summ
    with open(f"{SCRATCH}/w4_rates.json", "w") as fh:
        json.dump(out, fh, indent=1)
    return out


if __name__ == "__main__":
    main()
