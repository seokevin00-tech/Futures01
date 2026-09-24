"""Does freshness matter - an untested block versus one price has already used?

The supply/demand programme could not answer this: ``fresh_zone_approach``
fires on 0.5-1.4% of bars and no rule set built on it ever reached 20 trades.
At bar level the question is answerable, because it does not need a strategy -
it needs touches, and there are thousands of them.

Every touch of a live zone is scored by the same exit-free rule as the
bar-level study: a symmetric 1-ATR barrier race from the touch bar's close, in
the direction the concept claims. First touch versus later touch, per cell,
split 60/40 in time.

Touches of the same zone are not independent, so the headline is repeated on a
decimated set - at most one touch per zone - and both are reported.
"""
from __future__ import annotations

import json
import sys
from typing import List, Optional, Sequence

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")

import toolkit as T                                    # noqa: E402
from futures_agents.features import build_symbol_frame  # noqa: E402
from futures_agents.scout import FRAMES                 # noqa: E402

from workspace.newstrats import ict_blocks as I        # noqa: E402
from workspace.newstrats.w4_barlevel import (CELLS, K, SCRATCH, sign_test,
                                              stouffer, two_prop_z)


def race(bars, atr, t: int, bull: bool, k: int = K) -> Optional[bool]:
    """1 ATR in the claimed direction before 1 ATR against it. A bar holding
    both barriers is scored as a loss - the engine makes the same assumption."""
    a = atr[t]
    if not a or a <= 0 or t + 1 >= len(bars):
        return None
    c0 = bars[t].close
    up, dn = c0 + a, c0 - a
    for j in range(t + 1, min(len(bars), t + 1 + k)):
        b = bars[j]
        hi, lo = b.high >= up, b.low <= dn
        if hi and lo:
            return False
        if hi:
            return bull
        if lo:
            return not bull
    return None


def collect(bars, atr, states, which: str, lo: int, hi: int):
    """(fresh_results, retested_results, decimated_fresh, decimated_retested)."""
    fresh, ret, dfresh, dret = [], [], [], []
    seen_first = set()
    for t in range(lo, min(hi, len(bars))):
        slot = states[t].ob if which == "ob" else states[t].fvg
        if slot is None:
            continue
        d, is_fresh, _ = slot
        r = race(bars, atr, t, d == "BULL")
        if r is None:
            continue
        (fresh if is_fresh else ret).append(r)
        # decimate: one observation per 20-bar block, so a zone price sits
        # inside for six bars contributes once rather than six times
        blk = t // 20
        if blk not in seen_first:
            seen_first.add(blk)
            (dfresh if is_fresh else dret).append(r)
    return fresh, ret, dfresh, dret


def summ(a: Sequence[bool], b: Sequence[bool]) -> dict:
    ka, kb = sum(a), sum(b)
    return dict(n_fresh=len(a), n_retested=len(b),
                win_fresh=round(ka / len(a), 4) if a else None,
                win_retested=round(kb / len(b), 4) if b else None,
                z=round(two_prop_z(ka, len(a), kb, len(b)), 3))


def main():
    out = {"score_bars": K, "cells": {}}
    for sym, tf in CELLS:
        series = T._series(sym, tf, None)
        frame = build_symbol_frame(series, FRAMES[tf])
        I.register_frame(frame, sym)
        bars, atr, obs, fvgs, states = I.CACHE[(sym.upper(), tf)]
        n = len(bars); cut = int(n * 0.6)
        row = {}
        for which in ("ob", "fvg"):
            rec = {}
            for per, (a, b) in (("IS", (0, cut)), ("OOS", (cut, n)),
                                ("FULL", (0, n))):
                f, r, df, dr = collect(bars, atr, states, which, a, b)
                rec[per] = summ(f, r)
                rec[per]["decimated"] = summ(df, dr)
            row[which] = rec
        out["cells"][f"{sym}-{tf}m"] = row
        for which in ("ob", "fvg"):
            r = row[which]
            print(f"{sym}-{tf}m {which:4s} FULL fresh {r['FULL']['win_fresh']} "
                  f"(n={r['FULL']['n_fresh']}) vs retested {r['FULL']['win_retested']} "
                  f"(n={r['FULL']['n_retested']}) z={r['FULL']['z']:+.2f} | "
                  f"IS z={r['IS']['z']:+.2f} OOS z={r['OOS']['z']:+.2f} | "
                  f"decim z={r['FULL']['decimated']['z']:+.2f}")
        sys.stdout.flush()
    for which in ("ob", "fvg"):
        for per in ("FULL", "IS", "OOS"):
            zs = [out["cells"][c][which][per]["z"] for c in out["cells"]]
            out.setdefault("combined", {}).setdefault(which, {})[per] = dict(
                stouffer=round(stouffer(zs), 3), per_cell=zs, sign=sign_test(zs))
            print(f"COMBINED {which:4s} {per:5s} stouffer={stouffer(zs):+.2f} "
                  f"sign={sign_test(zs)}")
    with open(f"{SCRATCH}/ict_freshness.json", "w") as fh:
        json.dump(out, fh, indent=1)
    return out


if __name__ == "__main__":
    main()
