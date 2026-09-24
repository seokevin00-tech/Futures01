"""Part A, step 3: the kill zones as matched strategy filters.

Design, per the brief:

* one cell = (symbol, timeframe, disjoint slice).  Slices are contiguous,
  non-overlapping thirds of the series by bar index - not the nested
  274/180/90 windows, which all end on the same bar.
* 30 base rule sets, one signal condition each, drawn from distinct condition
  groups.  Every arm is that base rule set plus exactly one kill-zone FILTER;
  the control is the same base rule set with no time filter at all.
* per-cell paired sign test on Delta-expectancy across the 30 base rule sets,
  combined across cells with Stouffer.  Never ``T.ab`` (D28).
* ``rth_only=False`` everywhere - otherwise London and Asia are invisible.
* floor-free: a base rule set enters a pair if BOTH arms have >= min_n trades,
  and min_n is reported and varied, rather than the 20-trade library floor
  which selects on exit geometry.

Timeframes: 60m is the only series with enough history for three disjoint
slices (222-224 trading days).  15m has 41 days and 5m 20, so they are run as
corroboration with their sample sizes stated.  240m is NOT run: 240m bars
start at 00/04/08/12/16/20 ET only, so the Silver Bullet window contains
0.00% of them and the other windows are half-covered by construction.
"""
from __future__ import annotations

import json
import os
import sys
import time

sys.path.insert(0, "/home/user/Futures01/workspace/newstrats")
import w6_arms as A                                    # noqa: E402
import w6_ict_time as K                                # noqa: E402

OUT = "/home/user/Futures01/workspace/strategy_research/scratch/ict"
os.makedirs(OUT, exist_ok=True)

KZ_VARIANTS = {
    "control": [],
    "kz_london_open": ["kz_london_open"],
    "kz_ny_open": ["kz_ny_open"],
    "kz_silver_bullet": ["kz_silver_bullet"],
    "kz_london_close": ["kz_london_close"],
    "kz_asian_range": ["kz_asian_range"],
    "kz_union": ["kz_union"],
    "kz_outside_all": ["kz_outside_all"],
    "avoid_1500_1600": ["avoid_1500_1600"],
}
HOUR_VARIANTS = {"control": []}
HOUR_VARIANTS.update({f"hour_{h:02d}": [f"hour_{h:02d}"] for h in range(24)})

SLICES = {60: [(0.0, 1 / 3), (1 / 3, 2 / 3), (2 / 3, 1.0)],
          15: [(0.0, 1 / 3), (1 / 3, 2 / 3), (2 / 3, 1.0)],
          5: [(0.0, 0.5), (0.5, 1.0)]}


def main(mode: str, tfs, symbols):
    variants = KZ_VARIANTS if mode == "kz" else HOUR_VARIANTS
    rep = {"design": {"mode": mode, "variants": sorted(variants),
                      "n_base_rule_sets": len(A.BASE_SIGNALS),
                      "base_rule_sets": A.BASE_SIGNALS,
                      "rth_only": False,
                      "exit": "T.make_strategy default: ATRx1 stop, ANCHOR_ATR targets "
                              "1.0/2.5, scale 50/50, BE at 1.5R, 40-bar time stop, "
                              "exit_at_session_close=False",
                      "slices": {str(k): v for k, v in SLICES.items()},
                      "statistic": "per-cell paired sign test on Delta-expectancy, "
                                   "Stouffer across cells; never T.ab (D28)"},
           "cells": {}}
    for symbol in symbols:
        for tf in tfs:
            for si, (lo, hi) in enumerate(SLICES[tf]):
                t0 = time.time()
                ser = A.series_slice(symbol, tf, lo, hi)
                if len(ser.bars) < 300:
                    continue
                rows = A.run_arms(symbol, tf, ser, A.BASE_SIGNALS, variants,
                                  extra_registry=K.CONDITIONS)
                key = f"{symbol}_{tf}_s{si}"
                rep["cells"][key] = {
                    "symbol": symbol, "tf": tf, "slice": si,
                    "n_bars": len(ser.bars),
                    "span": [str(ser.bars[0].ts), str(ser.bars[-1].ts)],
                    "rows": rows,
                    "paired": {v: A.paired_sign(rows, v, "control", min_n=mn)
                               for v in variants if v != "control"
                               for mn in [10]},
                    "paired_min20": {v: A.paired_sign(rows, v, "control", min_n=20)
                                     for v in variants if v != "control"},
                }
                print(f"{key} bars={len(ser.bars)} {time.time() - t0:.0f}s", flush=True)
                with open(f"{OUT}/{mode}_arms.json", "w") as fh:
                    json.dump(rep, fh, indent=1, default=str)
    return rep


if __name__ == "__main__":
    mode = sys.argv[1] if len(sys.argv) > 1 else "kz"
    tfs = [int(x) for x in (sys.argv[2].split(",") if len(sys.argv) > 2 else ["60"])]
    syms = (sys.argv[3].split(",") if len(sys.argv) > 3 else ["MNQ", "MES", "MGC", "MCL"])
    main(mode, tfs, syms)
    print("written", f"{OUT}/{mode}_arms.json")
