"""Bar-level test of the ICT claim itself, before any strategy is built.

Two claims are separable and both are testable without choosing an exit:

**The magnet claim** - price returns to an order block / fair value gap. Tested
as a fill rate against a *matched random-bar baseline*: for every real zone,
sham zones are planted at the same signed distance from price in ATR units,
with the same width, on the same side, at randomly chosen bars of the same
series. Everything is held equal except the assertion that the location is
special. Without that control, "price returns to 78% of order blocks" is a
statement about price wandering, not about order blocks.

**The reaction claim** - the level then holds. Tested from the touch bar as a
symmetric 1-ATR barrier race (does the claimed direction pay 1 ATR before the
other side takes 1 ATR) plus forward return and MFE/MAE in ATR units. A
symmetric barrier is the most exit-neutral scoring rule available: it does not
favour trend or mean reversion and it has no parameter to tune.

Dependence is handled explicitly. Zones overlap heavily in time, so the raw
count overstates the sample. Every headline is recomputed on an *episode*
subsample - at most one zone per horizon-length block - and both are reported.
"""
from __future__ import annotations

import json
import math
import os
import random
import sys
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")

import toolkit as T                                    # noqa: E402
from futures_agents.features import build_symbol_frame  # noqa: E402
from futures_agents.scout import FRAMES                 # noqa: E402

from workspace.newstrats import ict_blocks as I        # noqa: E402

CELLS = [("MGC", 60), ("MES", 60), ("NQ", 60), ("MNQ", 60), ("MCL", 60),
         ("MGC", 15), ("MES", 15), ("MNQ", 15), ("MCL", 15)]

H = 40          # bars allowed for the return
K = 20          # bars scored after the touch
N_PLACEBO = 12  # sham zones per real zone
SCRATCH = ("/tmp/claude-0/-home-user-Futures01/"
           "40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad")


def phi(z: float) -> float:
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


def two_prop_z(k1, n1, k2, n2) -> float:
    if n1 == 0 or n2 == 0:
        return 0.0
    p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return (k1 / n1 - k2 / n2) / se if se else 0.0


def welch_z(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) < 5 or len(b) < 5:
        return 0.0
    ma = sum(a) / len(a); mb = sum(b) / len(b)
    va = sum((x - ma) ** 2 for x in a) / (len(a) - 1)
    vb = sum((x - mb) ** 2 for x in b) / (len(b) - 1)
    se = math.sqrt(va / len(a) + vb / len(b))
    return (ma - mb) / se if se else 0.0


def stouffer(zs: Sequence[float]) -> float:
    zs = [z for z in zs if z == z]
    return sum(zs) / math.sqrt(len(zs)) if zs else 0.0


def sign_test(deltas: Sequence[float]) -> dict:
    pos = sum(1 for d in deltas if d > 0)
    neg = sum(1 for d in deltas if d < 0)
    n = pos + neg
    if n == 0:
        return {"pos": 0, "neg": 0, "z": 0.0, "p": 1.0}
    z = (pos - n / 2) / math.sqrt(n / 4)
    return {"pos": pos, "neg": neg, "n": n, "z": round(z, 3),
            "p": round(2 * (1 - phi(abs(z))), 5)}


# ------------------------------------------------------------------ scoring
def score_zone(bars, atr, v: int, prox: float, dist: float, bull: bool,
               h: int = H, k: int = K) -> Optional[dict]:
    """One zone (real or sham) scored identically. ``v`` is the birth bar."""
    n = len(bars)
    if v + h + k >= n:
        return None
    mid = (prox + dist) / 2.0
    touch = None
    hit_mid = hit_dist = False
    for j in range(v + 1, v + 1 + h):
        b = bars[j]
        if bull:
            if b.low <= prox:
                if touch is None:
                    touch = j
                if b.low <= mid:
                    hit_mid = True
                if b.low <= dist:
                    hit_dist = True
        else:
            if b.high >= prox:
                if touch is None:
                    touch = j
                if b.high >= mid:
                    hit_mid = True
                if b.high >= dist:
                    hit_dist = True
        if hit_dist:
            break
    out = dict(touch=touch is not None, mid=hit_mid, full=hit_dist,
               bars_to_touch=(touch - v) if touch else None)
    if touch is None:
        return out
    a = atr[touch]
    if not a or a <= 0:
        return out
    c0 = bars[touch].close
    # symmetric 1-ATR barrier race from the touch bar's close
    up, dn = c0 + a, c0 - a
    win = None
    mfe = mae = 0.0
    end = min(n - 1, touch + k)
    for j in range(touch + 1, end + 1):
        b = bars[j]
        mfe = max(mfe, (b.high - c0) / a if bull else (c0 - b.low) / a)
        mae = max(mae, (c0 - b.low) / a if bull else (b.high - c0) / a)
        hi_hit, lo_hit = b.high >= up, b.low <= dn
        if hi_hit and lo_hit:
            win = False if bull else False   # ambiguous bar: score against
            break
        if hi_hit:
            win = bull
            break
        if lo_hit:
            win = not bull
            break
    fwd = (bars[end].close - c0) / a
    out.update(barrier=win, fwd=fwd if bull else -fwd, mfe=mfe, mae=mae,
               edge=(mfe - mae))
    return out


def run_cell(symbol: str, tf: int, seed: int = 11) -> dict:
    series = T._series(symbol, tf, None)
    frame = build_symbol_frame(series, FRAMES[tf])
    I.register_frame(frame, symbol)
    bars, atr, obs, fvgs, states = I.CACHE[(symbol.upper(), tf)]
    n = len(bars)
    rng = random.Random(seed)
    valid = [i for i in range(30, n - H - K - 1) if atr[i]]
    res = {}
    for label, zones in (("order_block", obs), ("fvg", fvgs)):
        real, sham = [], []
        real_ep = []
        last_ep = -10 ** 9
        for z in zones:
            v = z.confirmed_index
            if not atr[v]:
                continue
            bull = z.direction == "BULL"
            r = score_zone(bars, atr, v, z.proximal, z.distal, bull)
            if r is None:
                continue
            r["_v"] = v
            real.append(r)
            if v - last_ep >= H:
                real_ep.append(r); last_ep = v
            # matched shams: same distance, same width, same side, random bar
            d_atr = abs(bars[v].close - z.proximal) / atr[v]
            w_atr = abs(z.top - z.bottom) / atr[v]
            for _ in range(N_PLACEBO):
                q = rng.choice(valid)
                aq = atr[q]
                cq = bars[q].close
                if bull:
                    p2 = cq - d_atr * aq; d2 = p2 - w_atr * aq
                else:
                    p2 = cq + d_atr * aq; d2 = p2 + w_atr * aq
                s = score_zone(bars, atr, q, p2, d2, bull)
                if s is not None:
                    sham.append(s)

        def agg(rows):
            t = [r for r in rows if r["touch"]]
            bt = [r for r in t if r.get("barrier") is not None]
            fw = [r["fwd"] for r in t if "fwd" in r]
            eg = [r["edge"] for r in t if "edge" in r]
            return dict(
                n=len(rows),
                touch_rate=round(sum(r["touch"] for r in rows) / len(rows), 4)
                if rows else None,
                mid_rate=round(sum(r["mid"] for r in rows) / len(rows), 4)
                if rows else None,
                full_rate=round(sum(r["full"] for r in rows) / len(rows), 4)
                if rows else None,
                n_touched=len(t),
                barrier_win=round(sum(1 for r in bt if r["barrier"]) / len(bt), 4)
                if bt else None,
                n_barrier=len(bt),
                mean_fwd=round(sum(fw) / len(fw), 4) if fw else None,
                mean_edge=round(sum(eg) / len(eg), 4) if eg else None,
                med_bars_to_touch=(sorted(r["bars_to_touch"] for r in t)[len(t) // 2]
                                   if t else None))

        A, B, E = agg(real), agg(sham), agg(real_ep)
        bt_a = [r for r in real if r.get("barrier") is not None]
        bt_b = [r for r in sham if r.get("barrier") is not None]
        res[label] = dict(
            real=A, placebo=B, real_episodes=E,
            z_touch=round(two_prop_z(sum(r["touch"] for r in real), len(real),
                                     sum(r["touch"] for r in sham), len(sham)), 3),
            z_full=round(two_prop_z(sum(r["full"] for r in real), len(real),
                                    sum(r["full"] for r in sham), len(sham)), 3),
            z_touch_episodes=round(
                two_prop_z(sum(r["touch"] for r in real_ep), len(real_ep),
                           sum(r["touch"] for r in sham), len(sham)), 3),
            z_barrier=round(two_prop_z(sum(1 for r in bt_a if r["barrier"]), len(bt_a),
                                       sum(1 for r in bt_b if r["barrier"]),
                                       len(bt_b)), 3),
            z_fwd=round(welch_z([r["fwd"] for r in real if "fwd" in r],
                                [r["fwd"] for r in sham if "fwd" in r]), 3),
            z_edge=round(welch_z([r["edge"] for r in real if "edge" in r],
                                 [r["edge"] for r in sham if "edge" in r]), 3),
        )
    return res


def main():
    out = {"horizon_bars": H, "score_bars": K, "placebos_per_zone": N_PLACEBO,
           "cells": {}}
    for sym, tf in CELLS:
        r = run_cell(sym, tf)
        out["cells"][f"{sym}-{tf}m"] = r
        for lab in ("order_block", "fvg"):
            d = r[lab]
            print(f"{sym}-{tf}m {lab:12s} "
                  f"touch {d['real']['touch_rate']} vs sham {d['placebo']['touch_rate']} "
                  f"z={d['z_touch']:+.2f} (episodes z={d['z_touch_episodes']:+.2f}) | "
                  f"full {d['real']['full_rate']} vs {d['placebo']['full_rate']} "
                  f"z={d['z_full']:+.2f} | barrier {d['real']['barrier_win']} vs "
                  f"{d['placebo']['barrier_win']} z={d['z_barrier']:+.2f} | "
                  f"fwd {d['real']['mean_fwd']} vs {d['placebo']['mean_fwd']} "
                  f"z={d['z_fwd']:+.2f}")
        sys.stdout.flush()
    # combine
    for lab in ("order_block", "fvg"):
        for key in ("z_touch", "z_touch_episodes", "z_full", "z_barrier",
                    "z_fwd", "z_edge"):
            zs = [out["cells"][c][lab][key] for c in out["cells"]]
            out.setdefault("combined", {}).setdefault(lab, {})[key] = dict(
                stouffer=round(stouffer(zs), 3), per_cell=zs,
                sign=sign_test(zs))
            print(f"COMBINED {lab:12s} {key:18s} stouffer={stouffer(zs):+.2f} "
                  f"sign={sign_test(zs)}")
    with open(f"{SCRATCH}/ict_barlevel.json", "w") as fh:
        json.dump(out, fh, indent=1)
    return out


if __name__ == "__main__":
    main()
