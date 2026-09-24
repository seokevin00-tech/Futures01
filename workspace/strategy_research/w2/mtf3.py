"""Split-half control for the require_alignment effect found in mtf2.

mtf2 says a |alignment| >= 0.5 scope gate lifts paired expectancy sharply. Two
things have to be checked before that means anything:

1. **Is it out of sample?** The same paired difference is recomputed on the
   first 60% and the last 40% of the bars separately. An effect present in one
   half only is a period artefact.

2. **Is it just "fewer trades"?** A gate that removes trades raises expectancy
   whenever the removed trades were merely average, because expectancy is a
   mean over a shrinking sample with the same variance. So a RANDOM veto
   removing the SAME number of trades per rule set is run as a control arm, and
   the alignment gate has to beat it, not zero.

Caveat carried into the report: the unit here is the rule set, and rule sets in
a generated population share trades heavily. The sign test therefore overstates
significance (D28's mechanism). The effect SIZE and its consistency across
halves are the parts to read; the z is an upper bound.
"""
from __future__ import annotations

import dataclasses as dc
import json
import math
import random
import statistics as st
import sys

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


def _pairstat(pairs):
    if len(pairs) < 5:
        return {"pairs": len(pairs)}
    d = [b - a for a, b in pairs]
    m = st.mean(d)
    sd = st.pstdev(d) or 1e-9
    return {"pairs": len(d), "mean_diff": round(m, 4),
            "median_diff": round(st.median(d), 4),
            "paired_t_upper_bound": round(m / (sd / math.sqrt(len(d))), 3),
            "sign_upper_bound": W.sign_test(d)}


def run(symbol: str, tf: int, window: int = 274, budget: int = 4000,
        align: float = 0.5, floor: int = 10) -> dict:
    ser = T._series(symbol, tf, window)
    frame = build_symbol_frame(ser, FRAMES[tf])
    base = W.population(symbol, tf, budget, seed=1)
    armS = [dc.replace(s, filters=dc.replace(s.filters, require_alignment=align),
                       _id=None) for s in base]
    rb = run_portfolio(frame, base)
    ra = run_portfolio(frame, armS)
    bars = ser.bars
    cut = bars[int(len(bars) * 0.6)].ts

    def part(tr, lo, hi):
        return [t for t in tr if lo <= t.entry_ts < hi]

    rng = random.Random(f"veto:{symbol}:{tf}")
    out = {"symbol": symbol, "tf": tf, "window": window, "align": align,
           "n_base": len(base), "cut": str(cut)}
    for label, (lo, hi) in (("in_sample_first60", (bars[0].ts, cut)),
                            ("out_of_sample_last40", (cut, bars[-1].ts)),
                            ("full", (bars[0].ts, bars[-1].ts))):
        real_pairs, veto_pairs, kept = [], [], []
        for s, a in zip(base, armS):
            tb = part(rb[s.strategy_id].trades, lo, hi)
            ta = part(ra[a.strategy_id].trades, lo, hi)
            if len(tb) < floor or len(ta) < floor:
                continue
            eb = compute_metrics(tb).expectancy_r
            ea = compute_metrics(ta).expectancy_r
            real_pairs.append((eb, ea))
            kept.append(len(ta) / len(tb))
            # random veto control: keep len(ta) of tb, chosen at random
            sub = rng.sample(tb, min(len(ta), len(tb)))
            veto_pairs.append((eb, compute_metrics(sub).expectancy_r))
        out[label] = {
            "alignment_gate": _pairstat(real_pairs),
            "random_veto_control_same_size": _pairstat(veto_pairs),
            "median_trade_retention": round(st.median(kept), 3) if kept else None,
        }
    with open(f"{CELLS}/mtf3_{symbol}_{tf}.json", "w") as fh:
        json.dump(out, fh, default=str)
    return out


if __name__ == "__main__":
    print(json.dumps(run(sys.argv[1], int(sys.argv[2])), default=str, indent=1))
