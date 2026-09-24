"""Count-matched placebo: does ANY 1-in-24 entry filter beat no filter?"""
from __future__ import annotations
import json, os, sys, time
sys.path.insert(0, "/home/user/Futures01/workspace/newstrats")
import w6_arms as A, w6_ict_time as K, w6_run_kz as R   # noqa: E402

OUT = "/home/user/Futures01/workspace/strategy_research/scratch/ict"
VAR = {"control": []}
VAR.update({f"stride24_p{p:02d}": [f"stride24_p{p:02d}"] for p in (0, 5, 11, 17, 23)})
VAR.update({h: [h] for h in ("hour_10", "hour_21", "hour_08")})


def main(symbols, tf=60):
    rep = {"design": {"placebo": "every 24th bar by index, 5 phases; identical firing "
                                 "rate to a one-hour filter, zero clock content",
                      "reference_arms": ["hour_10 (Silver Bullet)", "hour_21 (ICT dead zone)",
                                         "hour_08 (worst hour vs control)"]},
           "cells": {}}
    for symbol in symbols:
        for si, (lo, hi) in enumerate(R.SLICES[tf]):
            t0 = time.time()
            ser = A.series_slice(symbol, tf, lo, hi)
            rows = A.run_arms(symbol, tf, ser, A.BASE_SIGNALS, VAR,
                              extra_registry=K.CONDITIONS)
            key = f"{symbol}_{tf}_s{si}"
            rep["cells"][key] = {"symbol": symbol, "tf": tf, "slice": si,
                                 "n_bars": len(ser.bars), "rows": rows,
                                 "paired": {v: A.paired_sign(rows, v, "control", min_n=10)
                                            for v in VAR if v != "control"}}
            print(key, f"{time.time()-t0:.0f}s", flush=True)
            with open(f"{OUT}/placebo_arms.json", "w") as fh:
                json.dump(rep, fh, indent=1, default=str)


if __name__ == "__main__":
    main(["MNQ", "MES", "MGC", "MCL"])
    print("written")
