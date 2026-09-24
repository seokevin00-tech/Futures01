"""The bar-level test again, split 60/40 in time. In sample, then out.

The one positive in the in-sample pass - fair value gaps winning a symmetric
1-ATR barrier race about two points more often than a matched sham - is exactly
the kind of number this programme has watched evaporate out of sample, so it
gets a temporal split before it gets quoted anywhere.

Both halves are self-contained: a zone is only counted when its whole
horizon+scoring window fits inside its own half, and the sham bars are drawn
from the same half. Nothing from the second period reaches the first.
"""
from __future__ import annotations

import json
import random
import sys

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")

import toolkit as T                                    # noqa: E402
from futures_agents.features import build_symbol_frame  # noqa: E402
from futures_agents.scout import FRAMES                 # noqa: E402

from workspace.newstrats import ict_blocks as I        # noqa: E402
from workspace.newstrats.w4_barlevel import (CELLS, H, K, N_PLACEBO, SCRATCH,
                                              score_zone, sign_test, stouffer,
                                              two_prop_z, welch_z)


def half(bars, atr, zones, lo, hi, rng, label):
    valid = [i for i in range(max(lo, 30), hi - H - K - 1) if atr[i]]
    if len(valid) < 200:
        return None
    real, sham = [], []
    for z in zones:
        v = z.confirmed_index
        if not (lo <= v < hi - H - K - 1) or not atr[v]:
            continue
        bull = z.direction == "BULL"
        r = score_zone(bars, atr, v, z.proximal, z.distal, bull)
        if r is None:
            continue
        real.append(r)
        d_atr = abs(bars[v].close - z.proximal) / atr[v]
        w_atr = abs(z.top - z.bottom) / atr[v]
        for _ in range(N_PLACEBO):
            q = rng.choice(valid)
            aq, cq = atr[q], bars[q].close
            p2 = cq - d_atr * aq if bull else cq + d_atr * aq
            d2 = p2 - w_atr * aq if bull else p2 + w_atr * aq
            s = score_zone(bars, atr, q, p2, d2, bull)
            if s is not None:
                sham.append(s)
    if len(real) < 30:
        return None
    ba = [r for r in real if r.get("barrier") is not None]
    bb = [r for r in sham if r.get("barrier") is not None]
    fa = [r["fwd"] for r in real if "fwd" in r]
    fb = [r["fwd"] for r in sham if "fwd" in r]
    return dict(
        n_real=len(real), n_sham=len(sham),
        touch_real=round(sum(r["touch"] for r in real) / len(real), 4),
        touch_sham=round(sum(r["touch"] for r in sham) / len(sham), 4),
        z_touch=round(two_prop_z(sum(r["touch"] for r in real), len(real),
                                 sum(r["touch"] for r in sham), len(sham)), 3),
        barrier_real=round(sum(1 for r in ba if r["barrier"]) / len(ba), 4) if ba else None,
        barrier_sham=round(sum(1 for r in bb if r["barrier"]) / len(bb), 4) if bb else None,
        n_barrier=len(ba),
        z_barrier=round(two_prop_z(sum(1 for r in ba if r["barrier"]), len(ba),
                                   sum(1 for r in bb if r["barrier"]), len(bb)), 3),
        fwd_real=round(sum(fa) / len(fa), 4) if fa else None,
        fwd_sham=round(sum(fb) / len(fb), 4) if fb else None,
        z_fwd=round(welch_z(fa, fb), 3))


def main():
    out = {"split": "60/40 temporal, self-contained halves", "cells": {}}
    for sym, tf in CELLS:
        series = T._series(sym, tf, None)
        frame = build_symbol_frame(series, FRAMES[tf])
        I.register_frame(frame, sym)
        bars, atr, obs, fvgs, states = I.CACHE[(sym.upper(), tf)]
        n = len(bars)
        cut = int(n * 0.6)
        rng = random.Random(23)
        row = {}
        for lab, zs in (("order_block", obs), ("fvg", fvgs)):
            row[lab] = {"IS": half(bars, atr, zs, 0, cut, rng, lab),
                        "OOS": half(bars, atr, zs, cut, n, rng, lab)}
        out["cells"][f"{sym}-{tf}m"] = row
        for lab in ("order_block", "fvg"):
            a, b = row[lab]["IS"], row[lab]["OOS"]
            if a and b:
                print(f"{sym}-{tf}m {lab:12s} IS barrier {a['barrier_real']} vs "
                      f"{a['barrier_sham']} z={a['z_barrier']:+.2f} (n={a['n_barrier']}) "
                      f"|| OOS barrier {b['barrier_real']} vs {b['barrier_sham']} "
                      f"z={b['z_barrier']:+.2f} (n={b['n_barrier']})  "
                      f"IS touch z={a['z_touch']:+.2f} OOS touch z={b['z_touch']:+.2f}")
        sys.stdout.flush()
    for lab in ("order_block", "fvg"):
        for per in ("IS", "OOS"):
            for key in ("z_touch", "z_barrier", "z_fwd"):
                zs = [out["cells"][c][lab][per][key] for c in out["cells"]
                      if out["cells"][c][lab][per]]
                out.setdefault("combined", {}).setdefault(lab, {}).setdefault(per, {})[key] = \
                    dict(stouffer=round(stouffer(zs), 3), per_cell=zs, sign=sign_test(zs))
                print(f"COMBINED {lab:12s} {per:4s} {key:10s} "
                      f"stouffer={stouffer(zs):+.2f} sign={sign_test(zs)}")
    with open(f"{SCRATCH}/ict_barlevel_oos.json", "w") as fh:
        json.dump(out, fh, indent=1)
    return out


if __name__ == "__main__":
    main()
