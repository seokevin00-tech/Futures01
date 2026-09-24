"""Rate-matched placebo for the fib bands.

``gp_vs_control`` cannot be read on its own: the 24-hour census showed that
adding ANY sparse filter to a base rule set improves paired expectancy, purely
by thinning clustered re-entries. ``fib_golden_pocket`` fires on 8.0-9.9% of
bars - about one bar in eleven - so stride-11 placebos are its count-matched
null. If the placebos score as well as the golden pocket, ``gp_vs_control`` is
the thinning artefact and only ``gp_vs_sh`` (band against band, rates matched)
carries information.
"""
from __future__ import annotations
import json, os, sys, time
sys.path.insert(0, "/home/user/Futures01/workspace/newstrats")
import w6_arms as A, w6_ict_time as K   # noqa: E402

OUT = "/home/user/Futures01/workspace/strategy_research/scratch/ict"
VAR = {"control": [], "gp": ["fib_golden_pocket"], "sh": ["fib_shallow_retrace"]}
VAR.update({f"stride11_p{p:02d}": [f"stride11_p{p:02d}"] for p in (0, 3, 7)})
SLICES = [(0.0, 1 / 3), (1 / 3, 2 / 3), (2 / 3, 1.0)]


def main(symbols, tfs):
    rep = {"design": {"placebo": "every 11th bar by index; matched to "
                                 "fib_golden_pocket's 8.0-9.9% firing rate"},
           "cells": {}}
    for symbol in symbols:
        for tf in tfs:
            for si, (lo, hi) in enumerate(SLICES):
                t0 = time.time()
                ser = A.series_slice(symbol, tf, lo, hi)
                rows = A.run_arms(symbol, tf, ser, A.BASE_SIGNALS, VAR,
                                  extra_registry=K.CONDITIONS)
                key = f"{symbol}_{tf}_s{si}"
                rep["cells"][key] = {
                    "symbol": symbol, "tf": tf, "slice": si, "n_bars": len(ser.bars),
                    "rows": rows,
                    "paired": {v: A.paired_sign(rows, v, "control", min_n=5)
                               for v in VAR if v != "control"}}
                print(key, f"{time.time()-t0:.0f}s", flush=True)
                with open(f"{OUT}/fib_placebo.json", "w") as fh:
                    json.dump(rep, fh, indent=1, default=str)


if __name__ == "__main__":
    main(["MNQ", "MES", "MGC", "MCL"], [60, 240])
    print("written")
