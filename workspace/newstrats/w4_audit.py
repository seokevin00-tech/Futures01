"""Leakage audit. Every claim in the ICT studies rests on these being true.

Six things are checked mechanically rather than asserted in prose:

1. No zone is ever consulted at or before the bar it became knowable.
2. An FVG's visibility bar is the third bar of the pattern, not the second.
3. An order block's confirmation is never earlier than the displacement that
   defines it, and the "last opposite candle" rule actually binds.
4. ``BarState`` for bar t is a pure function of bars 0..t - verified by
   rebuilding every state from a truncated series and comparing.
5. Touch counts and invalidation never anticipate.
6. A deliberately-peeking variant (fire one bar EARLY) is measured, as a
   positive control: if the harness cannot tell a cheat from the honest
   version, it cannot detect leakage either.
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")

import toolkit as T                                    # noqa: E402

from workspace.newstrats import ict_blocks as I        # noqa: E402
from workspace.newstrats.w4_barlevel import (H, K, SCRATCH, score_zone,
                                             two_prop_z)

CELLS = [("MGC", 60), ("MES", 60), ("MCL", 60), ("MNQ", 15)]


def audit(sym, tf):
    bars = list(T._series(sym, tf, None).bars)
    n = len(bars)
    atr, obs, fvgs, states = I.build(bars)
    r = {"n_bars": n, "n_obs": len(obs), "n_fvgs": len(fvgs)}

    # 2. FVG visibility
    r["fvg_confirm_is_third_bar"] = all(z.confirmed_index == z.origin_index + 1
                                        for z in fvgs)
    # 3. OB confirmation after the displacement, and the last-candle rule binds
    r["ob_confirm_after_origin"] = all(z.confirmed_index > z.origin_index
                                       for z in obs)
    r["ob_confirm_within_window"] = all(
        z.confirmed_index <= z.origin_index + I.PARAMS["disp_bars"] for z in obs)
    bad_last = 0
    for z in obs:
        i = z.origin_index
        nxt = bars[i + 1]
        down_next = nxt.close < nxt.open
        if (z.direction == "BULL" and down_next) or \
           (z.direction == "BEAR" and not down_next):
            bad_last += 1
    r["ob_last_candle_rule_violations"] = bad_last

    # 1/5. no state references a zone before it is knowable: rebuild the states
    #      from a truncated series and require an exact match on the overlap.
    mism = 0
    for cutoff in (int(n * 0.35), int(n * 0.6), int(n * 0.85)):
        a2, o2, f2, s2 = I.build(bars[:cutoff])
        for t in range(30, cutoff):
            x, y = states[t], s2[t]
            if (x.ob, x.fvg, x.breaker, x.ifvg, x.ob_close_in, x.fvg_close_in,
                    x.breaker_fresh, x.ifvg_fresh, x.fvg_libclone) != \
               (y.ob, y.fvg, y.breaker, y.ifvg, y.ob_close_in, y.fvg_close_in,
                    y.breaker_fresh, y.ifvg_fresh, y.fvg_libclone):
                mism += 1
    r["truncation_mismatches"] = mism
    r["causal"] = mism == 0

    # 6. positive control: score each OB from one bar BEFORE it was knowable.
    #    An honest harness must show the cheat doing better.
    honest = cheat = None
    hb, cb = [], []
    for z in obs:
        v = z.confirmed_index
        if v < 31 or v + H + K >= n or not atr[v] or not atr[v - 2]:
            continue
        bull = z.direction == "BULL"
        a = score_zone(bars, atr, v, z.proximal, z.distal, bull)
        c = score_zone(bars, atr, v - 2, z.proximal, z.distal, bull)
        if a and a.get("barrier") is not None:
            hb.append(a["barrier"])
        if c and c.get("barrier") is not None:
            cb.append(c["barrier"])
    if hb and cb:
        honest = round(sum(hb) / len(hb), 4)
        cheat = round(sum(cb) / len(cb), 4)
    r["positive_control"] = dict(
        honest_win=honest, peeking_2bars_early_win=cheat, n_honest=len(hb),
        n_cheat=len(cb),
        z=round(two_prop_z(sum(cb), len(cb), sum(hb), len(hb)), 3)
        if hb and cb else None)
    return r


def main():
    out = {}
    for sym, tf in CELLS:
        out[f"{sym}-{tf}m"] = audit(sym, tf)
        print(f"{sym}-{tf}m {json.dumps(out[f'{sym}-{tf}m'])}")
        sys.stdout.flush()
    with open(f"{SCRATCH}/w4_audit.json", "w") as fh:
        json.dump(out, fh, indent=1)
    return out


if __name__ == "__main__":
    main()
