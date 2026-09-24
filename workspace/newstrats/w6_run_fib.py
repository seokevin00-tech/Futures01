"""The only untested lead in the programme, given the out-of-sample test nobody ran.

Claim on the table: ``fib_golden_pocket`` beats ``fib_shallow_retrace`` at 240m
on all three symbols (z = +7.14 / +5.65 / +4.83) and loses at 60m on all three.
Those z-scores came from ``T.ab`` over a generated population, which D28 says
inflates |z| by about 3.3x on variants of the same rule sets - and the two fib
conditions ARE variants of one rule ("price is in a retracement band"), so this
is exactly the case D28 describes.

Rebuilt as matched arms: same base rule set, same bars, same exit, the only
difference being which band.  Per-cell paired sign test on Delta-expectancy,
Stouffer across cells, and a declared out-of-sample slice.
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

VARIANTS = {"control": [],
            "gp": ["fib_golden_pocket"],
            "sh": ["fib_shallow_retrace"],
            "ote": ["ote_zone"],
            "ote_strict": ["ote_strict"],
            "ote_0705": ["ote_0705"]}

SLICES = [(0.0, 1 / 3), (1 / 3, 2 / 3), (2 / 3, 1.0)]
SPLIT = [(0.0, 0.6), (0.6, 1.0)]


def main(symbols, tfs):
    rep = {"design": {
        "claim_under_test": "fib_golden_pocket beats fib_shallow_retrace at 240m and "
                            "loses at 60m (originally z=+7.14/+5.65/+4.83 via T.ab)",
        "why_retested": "D28: T.ab is unpaired and the two bands are variants of one "
                        "rule, the exact case where |z| inflates ~3.3x",
        "variants": sorted(VARIANTS), "n_base_rule_sets": len(A.BASE_SIGNALS),
        "statistic": "per-cell paired sign test on Delta-expectancy (gp minus sh), "
                     "Stouffer across cells",
        "periods": {"disjoint_thirds": SLICES, "temporal_60_40": SPLIT}},
        "cells": {}}
    for symbol in symbols:
        for tf in tfs:
            for tag, wins in (("s", SLICES), ("split", SPLIT)):
                for si, (lo, hi) in enumerate(wins):
                    t0 = time.time()
                    ser = A.series_slice(symbol, tf, lo, hi)
                    if len(ser.bars) < 250:
                        continue
                    rows = A.run_arms(symbol, tf, ser, A.BASE_SIGNALS, VARIANTS,
                                      extra_registry=K.CONDITIONS)
                    key = f"{symbol}_{tf}_{tag}{si}"
                    cell = {"symbol": symbol, "tf": tf, "period": f"{tag}{si}",
                            "n_bars": len(ser.bars),
                            "span": [str(ser.bars[0].ts), str(ser.bars[-1].ts)],
                            "rows": rows, "paired": {}}
                    for mn in (5, 10, 20):
                        cell["paired"][f"min_n{mn}"] = {
                            "gp_vs_sh": A.paired_sign(rows, "gp", "sh", min_n=mn),
                            "gp_vs_control": A.paired_sign(rows, "gp", "control", min_n=mn),
                            "sh_vs_control": A.paired_sign(rows, "sh", "control", min_n=mn),
                            "ote_vs_control": A.paired_sign(rows, "ote", "control", min_n=mn),
                            "ote_vs_gp": A.paired_sign(rows, "ote", "gp", min_n=mn),
                            "ote_strict_vs_gp": A.paired_sign(rows, "ote_strict", "gp", min_n=mn),
                            "ote_0705_vs_gp": A.paired_sign(rows, "ote_0705", "gp", min_n=mn)}
                    rep["cells"][key] = cell
                    print(f"{key} bars={len(ser.bars)} "
                          f"gp_vs_sh_z={cell['paired']['min_n5']['gp_vs_sh'].get('sign_z')} "
                          f"pairs={cell['paired']['min_n5']['gp_vs_sh'].get('n_pairs')} "
                          f"{time.time() - t0:.0f}s", flush=True)
                    with open(f"{OUT}/fib_arms.json", "w") as fh:
                        json.dump(rep, fh, indent=1, default=str)
    return rep


if __name__ == "__main__":
    main(["MNQ", "MES", "MGC", "MCL"], [240, 60])
    print("written", f"{OUT}/fib_arms.json")
