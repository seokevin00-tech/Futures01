"""Does multi-timeframe alignment actually improve outcomes? Paired, per cell.

The combinator's own execution-timeframe pairing is unreachable through
scout.FRAMES (see the defect note in the findings), so the multi-timeframe
question is asked here with two pairings that ARE reachable, both exactly
matched on signals, filters, exit geometry and bars:

A. ``require_alignment`` - a direction-agnostic scope gate on |alignment|,
   the weighted agreement of the structural trend across the frame's
   timeframes. Arm B is arm A plus the gate at 0.3 and at 0.5. Nothing else
   changes, so the difference is the alignment requirement and nothing else.

B. ``mtf_aligned`` appended as an extra SIGNAL condition. Stricter than A:
   the higher timeframes must agree AND agree with the trade's direction.

Unit of independence: the rule set. Each contributes one paired difference.
No rank sum across arms (D28).
"""
from __future__ import annotations

import dataclasses as dc
import json
import math
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")
sys.path.insert(0, "/home/user/Futures01/workspace/strategy_research/w2")

import toolkit as T  # noqa: E402
import w2rank as W  # noqa: E402
from futures_agents.backtest.engine import run_portfolio  # noqa: E402
from futures_agents.backtest.metrics import compute_metrics  # noqa: E402
from futures_agents.features import build_symbol_frame  # noqa: E402
from futures_agents.scout import FRAMES  # noqa: E402
from futures_agents.strategies.library import get_condition  # noqa: E402

CELLS = "/home/user/Futures01/workspace/strategy_research/w2/cells"


def _paired(a_map, b_map, floor):
    diffs, dn, ea_, eb_ = [], [], [], []
    for k, a in a_map.items():
        b = b_map.get(k)
        if b is None or len(a) < floor or len(b) < floor:
            continue
        ea = compute_metrics(a).expectancy_r
        eb = compute_metrics(b).expectancy_r
        ea_.append(ea)
        eb_.append(eb)
        diffs.append(eb - ea)
        dn.append(len(b) - len(a))
    if len(diffs) >= 5:
        m = st.mean(diffs)
        sd = st.pstdev(diffs) or 1e-9
        tt = m / (sd / math.sqrt(len(diffs)))
    else:
        m, tt = (st.mean(diffs) if diffs else 0.0), 0.0
    return {"pairs": len(diffs), "mean_diff_arm_minus_base": round(m, 4),
            "median_diff": round(st.median(diffs), 4) if diffs else None,
            "paired_t": round(tt, 3), "sign": W.sign_test(diffs),
            "median_exp_base": round(st.median(ea_), 4) if ea_ else None,
            "median_exp_arm": round(st.median(eb_), 4) if eb_ else None,
            "median_trade_delta": round(st.median(dn), 1) if dn else None}


def run(symbol: str, tf: int, window: int = 274, budget: int = 4000) -> dict:
    ser = T._series(symbol, tf, window)
    frame = build_symbol_frame(ser, FRAMES[tf])
    base = W.population(symbol, tf, budget, seed=1)
    mtf = get_condition("mtf_aligned")
    strong = get_condition("mtf_strongly_aligned")
    # Every arm carries the index of the base rule set it came from, so the
    # pairing survives the strategy_id change that each modification causes.
    arms = {"base": [(i, s) for i, s in enumerate(base)]}
    for lab, al in (("align0.3", 0.3), ("align0.5", 0.5)):
        arms[lab] = [(i, dc.replace(s, filters=dc.replace(
            s.filters, require_alignment=al), _id=None))
            for i, s in enumerate(base)]
    for lab, cond in (("mtf_aligned", mtf), ("mtf_strongly_aligned", strong)):
        arms[lab] = [(i, dc.replace(s, conditions=s.conditions + (cond,), _id=None))
                     for i, s in enumerate(base)
                     if cond.name not in {c.name for c in s.conditions}]
    out = {"symbol": symbol, "tf": tf, "window": window, "bars": len(ser),
           "n_base": len(base), "arms": {}}
    res = {}
    for lab, pairs in arms.items():
        ids = {s.strategy_id for _, s in pairs}
        if len(ids) != len(pairs):
            out.setdefault("warnings", []).append(
                f"{lab}: {len(pairs)-len(ids)} strategy_id collisions within arm")
        r = run_portfolio(frame, [s for _, s in pairs])
        res[lab] = {i: r[s.strategy_id].trades for i, s in pairs}
    for lab in arms:
        if lab == "base":
            continue
        arm = res[lab]
        common = {i: res["base"][i] for i in arm}
        out["arms"][lab] = {f: _paired(common, arm, f) for f in (5, 10, 20)}
    with open(f"{CELLS}/mtf2_{symbol}_{tf}.json", "w") as fh:
        json.dump(out, fh, default=str)
    return out


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1], int(sys.argv[2])), default=str))
