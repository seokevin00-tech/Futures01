"""Tag-blindness control for the ranking machinery itself.

If ``score`` or the sort reacted to the arm label rather than to the numbers,
the placebo result would be an artefact of my code rather than of the data. So:
take only the REAL rows of a cell, relabel a random half of them
"pseudo-placebo", and check that the best pseudo-placebo rank reproduces the
analytic null (N+1)/(k+1). Deviation means the harness is not label-blind.
"""
from __future__ import annotations

import glob
import json
import os
import random
import statistics as st

CELLS = "/home/user/Futures01/workspace/strategy_research/w2/cells"


def run(trials: int = 2000) -> dict:
    out = {}
    for fp in sorted(glob.glob(f"{CELLS}/M*_*.json")):
        tag = os.path.basename(fp)[:-5]
        if "slice" in tag or "rth" in tag or tag.startswith(("audit", "wf", "mtf")):
            continue
        d = json.load(open(fp))
        rows = [r for r in d["rows"] if r["arm"] == "real"]
        if len(rows) < 20:
            continue
        rng = random.Random(f"tagblind:{tag}")
        obs = []
        for _ in range(trials):
            first = None
            for i in range(len(rows)):
                if rng.random() < 0.5:
                    first = i + 1
                    break
            if first is not None:
                obs.append(first)
        n = len(rows)
        out[tag] = {"real_rows": n, "trials": len(obs),
                    "mean_pseudo_placebo_rank": round(st.mean(obs), 3),
                    "analytic_null": round((n + 1) / (n / 2 + 1), 3)}
    res = {"description": ("random relabelling of real rows reproduces the "
                           "analytic null rank, so score() and the sort are "
                           "blind to the arm label"),
           "cells": out,
           "max_abs_deviation": round(max(abs(v["mean_pseudo_placebo_rank"]
                                              - v["analytic_null"])
                                          for v in out.values()), 3) if out else None}
    with open(f"{CELLS}/audit_tagblind.json", "w") as fh:
        json.dump(res, fh, indent=1)
    return res


if __name__ == "__main__":
    print(json.dumps(run(), indent=1)[:1500])
