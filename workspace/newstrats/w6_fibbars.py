"""The untested lead, tested the cleanest way first: golden pocket vs shallow
retrace at BAR level, with no exit model at all.

The surviving suggestion from the fib work was that ``fib_golden_pocket`` beats
``fib_shallow_retrace`` at 240m on all three symbols and loses at 60m.  Those
z-scores came from ``T.ab`` over a generated population, which defect D28 says
inflates |z| by about 3.3x on variants of the same rule sets.

Before rebuilding it with matched strategy arms, ask the question with nothing
in the way: on the bars where each condition fires, what is the DIRECTION-
MATCHED forward return?  No stop, no target, no time stop, no slippage, no
20-trade floor - just "did the band point the right way".  If the 240m effect
is real it has to be visible here; if it is not visible here it was exit
geometry or search, not the band.

Baseline: a direction-matched random entry.  For every bar where the last
confirmed leg exists, take the direction the retracement conditions WOULD give
(long under an up leg, short under a down leg) regardless of where price sits.
That is the control the previous fib work said neither condition beat.
"""
from __future__ import annotations

import json
import math
import os
import statistics as st
import sys

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")
sys.path.insert(0, "/home/user/Futures01/workspace/newstrats")

import toolkit as T                                    # noqa: E402
from futures_agents.features import build_symbol_frame  # noqa: E402
from futures_agents.scout import FRAMES                # noqa: E402
from futures_agents.strategies.library import CONDITIONS as LIB  # noqa: E402

OUT = "/home/user/Futures01/workspace/strategy_research/scratch/ict"
os.makedirs(OUT, exist_ok=True)

HORIZONS = (1, 3, 6, 12)
COND = {"gp": "fib_golden_pocket", "sh": "fib_shallow_retrace"}


def collect(symbol: str, tf: int):
    """One record per bar on which anything fired, plus the baseline population."""
    series = T._series(symbol, tf, None)
    frame = build_symbol_frame(series, FRAMES[tf])
    tfr = frame.frames[tf]
    bars = tfr.series.bars
    closes = [b.close for b in bars]
    recs = []
    # T._series returns the tf's own series, so the frame's BASE minutes equal
    # tf and the base index is the tf index. Asserted rather than assumed: an
    # off-by-one between the two would silently shift every forward return.
    assert frame.base.minutes == tf, (frame.base.minutes, tf)
    assert len(frame.base.bars) == len(bars)
    for i in range(len(bars)):
        snap = frame.snapshot(i)
        if snap is None:
            continue
        s = snap.tf(tf)
        if s is None:
            continue
        atr = s.get("atr")
        leg = s.swing_leg()
        if not atr or leg is None:
            continue
        _, _, legdir = leg
        base_sign = 1.0 if legdir == "UP" else -1.0
        fwd = {}
        for h in HORIZONS:
            j = i + h
            fwd[h] = (closes[j] - closes[i]) / atr if j < len(closes) else None
        rec = {"i": i, "ts": bars[i].ts, "base_sign": base_sign, "fwd": fwd}
        for key, name in COND.items():
            r = LIB[name].fn(snap, tf)
            rec[key] = bool(r and r.triggered)
            if rec[key]:
                rec[key + "_sign"] = 1.0 if r.direction.value == "LONG" else -1.0
        recs.append(rec)
    return recs


def summarise(recs, key, h):
    """Mean direction-matched forward return in ATR units, with a t-stat."""
    if key == "base":
        vals = [r["base_sign"] * r["fwd"][h] for r in recs if r["fwd"][h] is not None]
    else:
        vals = [r[key + "_sign"] * r["fwd"][h] for r in recs
                if r.get(key) and r["fwd"][h] is not None]
    if len(vals) < 8:
        return {"n": len(vals)}
    m = st.mean(vals)
    sd = st.stdev(vals) or 1e-9
    return {"n": len(vals), "mean_atr": round(m, 4), "median": round(st.median(vals), 4),
            "t": round(m / (sd / math.sqrt(len(vals))), 2),
            "pct_up": round(sum(1 for v in vals if v > 0) / len(vals), 3)}


def welch(a, b):
    if len(a) < 8 or len(b) < 8:
        return None
    ma, mb = st.mean(a), st.mean(b)
    va, vb = st.variance(a) / len(a), st.variance(b) / len(b)
    se = math.sqrt(va + vb) or 1e-9
    return round((ma - mb) / se, 2)


def gp_vs_sh(recs, h):
    a = [r["gp_sign"] * r["fwd"][h] for r in recs if r.get("gp") and r["fwd"][h] is not None]
    b = [r["sh_sign"] * r["fwd"][h] for r in recs if r.get("sh") and r["fwd"][h] is not None]
    return {"n_gp": len(a), "n_sh": len(b),
            "mean_gp": round(st.mean(a), 4) if a else None,
            "mean_sh": round(st.mean(b), 4) if b else None,
            "welch_z": welch(a, b)}


def run(symbols, tfs, n_slices=3):
    rep = {"design": {
        "metric": "direction-matched forward return in ATR units, close to close",
        "horizons": list(HORIZONS),
        "baseline": "every bar with a confirmed leg, signed by the leg direction "
                    "(the direction-matched random entry the fib work used)",
        "no_exit_model": "no stop, target, time stop, slippage or trade floor"}}
    for symbol in symbols:
        for tf in tfs:
            recs = collect(symbol, tf)
            if len(recs) < 200:
                continue
            k = len(recs) // n_slices
            slices = [recs[j * k:(j + 1) * k if j < n_slices - 1 else len(recs)]
                      for j in range(n_slices)]
            cut = int(len(recs) * 0.6)
            cell = {"n_bars_with_leg": len(recs),
                    "span": [str(recs[0]["ts"]), str(recs[-1]["ts"])],
                    "full": {}, "IS_first60": {}, "OOS_last40": {}, "slices": []}
            for h in HORIZONS:
                cell["full"][f"h{h}"] = {
                    "gp": summarise(recs, "gp", h), "sh": summarise(recs, "sh", h),
                    "baseline": summarise(recs, "base", h),
                    "gp_vs_sh": gp_vs_sh(recs, h)}
                cell["IS_first60"][f"h{h}"] = {
                    "gp": summarise(recs[:cut], "gp", h),
                    "sh": summarise(recs[:cut], "sh", h),
                    "gp_vs_sh": gp_vs_sh(recs[:cut], h)}
                cell["OOS_last40"][f"h{h}"] = {
                    "gp": summarise(recs[cut:], "gp", h),
                    "sh": summarise(recs[cut:], "sh", h),
                    "gp_vs_sh": gp_vs_sh(recs[cut:], h)}
            for s in slices:
                cell["slices"].append({
                    "span": [str(s[0]["ts"]), str(s[-1]["ts"])],
                    **{f"h{h}": gp_vs_sh(s, h) for h in HORIZONS}})
            rep[f"{symbol}_{tf}"] = cell
            g = cell["full"]["h3"]["gp_vs_sh"]
            print(f"{symbol} {tf}m n={len(recs)} h3 gp={g['mean_gp']} sh={g['mean_sh']} "
                  f"welch_z={g['welch_z']}", flush=True)
    return rep


if __name__ == "__main__":
    rep = run(["MNQ", "MES", "MGC", "MCL"], [240, 60])
    with open(f"{OUT}/fib_bar_level.json", "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print("written", f"{OUT}/fib_bar_level.json")
