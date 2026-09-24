"""Cost control: rerun the hour census with the thin-book slippage tick removed.

The engine charges ``thin = not is_rth(fill_ts)`` an extra tick on entry and
on stop exits.  Every overnight-hour arm therefore pays 1.5 ticks where every
RTH-hour arm pays 0.5, and any ET-clock ranking is partly a ranking of cost
assumptions.  This run is identical in every other respect.
"""
from __future__ import annotations
import json, os, sys, time
sys.path.insert(0, "/home/user/Futures01/workspace/newstrats")
import w6_arms as A, w6_ict_time as K, w6_run_kz as R   # noqa: E402
from futures_agents.backtest.costs import CostModel, SlippageModel  # noqa: E402
from futures_agents.config import get_contract          # noqa: E402

OUT = "/home/user/Futures01/workspace/strategy_research/scratch/ict"


def main(symbols, tf=60):
    rep = {"design": {"change": "SlippageModel(thin_book_extra_ticks=0.0); "
                                "everything else identical to hour_arms",
                      "why": "removes the RTH-vs-overnight cost asymmetry"},
           "cells": {}}
    for symbol in symbols:
        cm = CostModel(spec=get_contract(symbol),
                       slippage=SlippageModel(thin_book_extra_ticks=0.0))
        for si, (lo, hi) in enumerate(R.SLICES[tf]):
            t0 = time.time()
            ser = A.series_slice(symbol, tf, lo, hi)
            rows = A.run_arms(symbol, tf, ser, A.BASE_SIGNALS, R.HOUR_VARIANTS,
                              extra_registry=K.CONDITIONS, cost_model=cm)
            key = f"{symbol}_{tf}_s{si}"
            rep["cells"][key] = {"symbol": symbol, "tf": tf, "slice": si,
                                 "n_bars": len(ser.bars), "rows": rows,
                                 "paired": {v: A.paired_sign(rows, v, "control", min_n=10)
                                            for v in R.HOUR_VARIANTS if v != "control"}}
            print(key, f"{time.time()-t0:.0f}s", flush=True)
            with open(f"{OUT}/hour_arms_nocostasym.json", "w") as fh:
                json.dump(rep, fh, indent=1, default=str)
    return rep


if __name__ == "__main__":
    main(["MNQ", "MES", "MGC", "MCL"])
    print("written")
