"""Does multi-timeframe structure actually improve outcomes? Paired, per cell.

The combinator emits every sampled rule set twice: once with the entry on the
anchor timeframe (``execution_tf=None``) and once with the entry located on a
finer timeframe (60m -> 5m, 240m -> 15m). That is a matched pair by
construction - identical signals, identical filters, identical exit geometry,
identical bars - so the multi-timeframe question can be asked WITHOUT a rank
sum across arms (D28). The unit of independence is the rule set; each
contributes one difference.

Timeframe groups actually under test here:
  60m anchor  -> {5m entry, 60m, 240m, 1440m read}  vs {60m, 240m, 1440m}
  240m anchor -> {15m entry, 240m, 1440m read}      vs {240m, 1440m}
"""
from __future__ import annotations

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

CELLS = "/home/user/Futures01/workspace/strategy_research/w2/cells"


def run(symbol: str, tf: int, window: int = 274, budget: int = 8000,
        floors=(5, 10, 20)) -> dict:
    ser = T._series(symbol, tf, window)
    frame = build_symbol_frame(ser, FRAMES[tf])
    S = W.population(symbol, tf, budget, seed=1)
    res = run_portfolio(frame, S)
    fam = defaultdict(dict)
    for s in S:
        key = (s.group, tuple(sorted(c.name for c in s.conditions)),
               s.exit.identity, s.filters.label())
        fam[key][s.execution_tf] = res[s.strategy_id].trades
    out = {"symbol": symbol, "tf": tf, "window": window, "n_real": len(S),
           "exec_tf_offered": sorted({s.execution_tf for s in S if s.execution_tf}),
           "by_floor": {}}
    for floor in floors:
        diffs, dn, anchor_e, exec_e = [], [], [], []
        for k, byx in fam.items():
            if None not in byx:
                continue
            others = [x for x in byx if x is not None]
            if not others:
                continue
            a = byx[None]
            b = byx[others[0]]
            if len(a) < floor or len(b) < floor:
                continue
            ea = compute_metrics(a).expectancy_r
            eb = compute_metrics(b).expectancy_r
            anchor_e.append(ea)
            exec_e.append(eb)
            diffs.append(eb - ea)          # finer entry minus anchor entry
            dn.append(len(b) - len(a))
        if len(diffs) >= 5:
            m = st.mean(diffs)
            sd = st.pstdev(diffs) or 1e-9
            tt = m / (sd / math.sqrt(len(diffs)))
        else:
            m, tt = (st.mean(diffs) if diffs else 0.0), 0.0
        out["by_floor"][floor] = {
            "pairs": len(diffs),
            "mean_exp_finer_minus_anchor": round(m, 4),
            "median_exp_finer_minus_anchor": round(st.median(diffs), 4) if diffs else None,
            "paired_t": round(tt, 3), "sign": W.sign_test(diffs),
            "median_exp_anchor": round(st.median(anchor_e), 4) if anchor_e else None,
            "median_exp_finer": round(st.median(exec_e), 4) if exec_e else None,
            "median_trade_delta": round(st.median(dn), 1) if dn else None,
        }
    with open(f"{CELLS}/mtf_{symbol}_{tf}.json", "w") as fh:
        json.dump(out, fh, default=str)
    return out


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1], int(sys.argv[2])), default=str))
