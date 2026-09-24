"""Anchored walk-forward: does picking the best geometry arm in-sample help next period?

Each symbol/timeframe series is cut into five consecutive, non-overlapping
blocks. In block k the best-performing arm is selected on realised expectancy
and then traded, unseen, in block k+1. The honest measure of geometry is what
that selection earns over the plain control in the same forward block - not
what the winner earned in the block where it was chosen.
"""
from __future__ import annotations

import json
import math
import statistics as st
import sys

sys.path.insert(0, "workspace/studies")
sys.path.insert(0, "workspace/newstrats")

import geometry as G                                        # noqa: E402
import toolkit as T                                         # noqa: E402
from futures_agents.backtest.engine import run_portfolio    # noqa: E402
from futures_agents.features import build_symbol_frame      # noqa: E402
from futures_agents.scout import FRAMES                     # noqa: E402
from futures_agents.strategies.base import StrategyFilters  # noqa: E402
from run_geometry import EXITS, SYMBOLS, TFS                # noqa: E402

FOLLOW = ["legs_expanding", "legs_contracting", "pullbacks_shallowing",
          "pullbacks_deepening", "swing_symmetry_impulse",
          "swing_symmetry_retrace"]
NFOLD = 5
MIN_TRADES = 8


def block(symbol, tf, lo, hi):
    series = T.slice_series(symbol, tf, lo, hi)
    if len(series) < 120:
        return {}
    frame = build_symbol_frame(series, FRAMES[tf])
    G.clear()
    G.register_frame(frame)
    tagged, strategies = [], []
    for arm in ["CONTROL"] + FOLLOW:
        cs = [G.get("structure_trend_ctl")]
        if arm != "CONTROL":
            cs.append(G.get(arm))
        s = T.make_strategy(symbol, tf, cs, group="WF", name=arm,
                            exit_model=EXITS["atr1.0"],
                            filters=StrategyFilters(rth_only=False))
        tagged.append((arm, s))
        strategies.append(s)
    res = run_portfolio(frame, strategies)
    return {arm: [t.net_r for t in res[s.strategy_id].trades] for arm, s in tagged}


if __name__ == "__main__":
    folds, rows = [], []
    for sym in SYMBOLS:
        for tf in TFS:
            span = T.disjoint_slices(sym, tf, 1)[0][0]
            edge = span / NFOLD
            blocks = [block(sym, tf, round(span - k * edge),
                            round(span - (k + 1) * edge)) for k in range(NFOLD)]
            for k in range(NFOLD - 1):
                tr, te = blocks[k], blocks[k + 1]
                if not tr or not te:
                    continue
                cands = [(st.fmean(v), a) for a, v in tr.items()
                         if a != "CONTROL" and len(v) >= MIN_TRADES]
                if not cands:
                    continue
                pick = max(cands)[1]
                fwd = te.get(pick, [])
                ctl = te.get("CONTROL", [])
                if len(fwd) < MIN_TRADES or len(ctl) < MIN_TRADES:
                    continue
                rows.append({
                    "symbol": sym, "tf": tf, "fold": k + 1, "picked": pick,
                    "is_exp_of_pick": round(max(cands)[0], 4),
                    "is_exp_of_control": round(st.fmean(tr["CONTROL"]), 4),
                    "oos_exp_of_pick": round(st.fmean(fwd), 4),
                    "oos_exp_of_control": round(st.fmean(ctl), 4),
                    "oos_delta": round(st.fmean(fwd) - st.fmean(ctl), 4),
                    "oos_n_pick": len(fwd), "oos_n_control": len(ctl),
                    "z": T.mann_whitney_u(fwd, ctl)["z"]})
    d = [r["oos_delta"] for r in rows]
    zs = [r["z"] for r in rows]
    decay = [r["oos_exp_of_pick"] - r["is_exp_of_pick"] for r in rows]
    summ = {
        "folds": len(rows),
        "folds_where_pick_beat_control_out_of_sample": sum(1 for x in d if x > 0),
        "median_oos_delta_vs_control_R": round(st.median(d), 4) if d else None,
        "mean_oos_delta_vs_control_R": round(st.fmean(d), 4) if d else None,
        "stouffer_z": (round(sum(zs) / math.sqrt(len(zs)), 3) if zs else None),
        "median_in_sample_expectancy_of_pick_R":
            round(st.median([r["is_exp_of_pick"] for r in rows]), 4) if rows else None,
        "median_out_of_sample_expectancy_of_pick_R":
            round(st.median([r["oos_exp_of_pick"] for r in rows]), 4) if rows else None,
        "median_selection_decay_R": round(st.median(decay), 4) if decay else None,
        "pick_frequency": {a: sum(1 for r in rows if r["picked"] == a)
                           for a in FOLLOW},
    }
    json.dump({"summary": summ, "folds": rows},
              open("workspace/strategy_research/scratch/walkforward.json", "w"),
              default=str)
    print(json.dumps(summ, indent=1))
    for r in rows:
        print(f"{r['symbol']} {r['tf']:4d} f{r['fold']} pick={r['picked']:24s} "
              f"IS={r['is_exp_of_pick']:+.3f} -> OOS={r['oos_exp_of_pick']:+.3f} "
              f"(ctl {r['oos_exp_of_control']:+.3f}) d={r['oos_delta']:+.3f} "
              f"n={r['oos_n_pick']}")
