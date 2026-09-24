"""Worker 2 anti-overfitting / mechanics audit.

Each check answers one of the failure modes the brief names, on MY symbols and
timeframes only. Nothing here is inferred from another worker's contract.
"""
from __future__ import annotations

import json
import sys

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")
sys.path.insert(0, "/home/user/Futures01/workspace/strategy_research/w2")

import toolkit as T  # noqa: E402
import w2rank as W  # noqa: E402
from futures_agents.backtest.engine import BacktestEngine, run_portfolio  # noqa: E402
from futures_agents.backtest.costs import CostModel  # noqa: E402
from futures_agents.config import get_contract  # noqa: E402
from futures_agents.data.bars import BarSeries  # noqa: E402
from futures_agents.features import build_symbol_frame  # noqa: E402
from futures_agents.scout import FRAMES  # noqa: E402

OUT = {}


def lookahead(symbol: str, tf: int, budget: int = 2000):
    """Truncation test. Rebuild the frame from a PREFIX of the bars and check
    that every trade whose life is entirely inside the prefix is identical.

    This is the direct test for repainting indicators and future-data leakage:
    a repainting feature computes a different value at bar i when bars after i
    exist, so the prefix run and the full run disagree on trades that closed
    long before the cut.
    """
    full = T._series(symbol, tf, 274)
    n = len(full.bars)
    cut = int(n * 0.7)
    pre = BarSeries(symbol, tf, full.bars[:cut])
    S = W.population(symbol, tf, budget, seed=1)
    fa = build_symbol_frame(full, FRAMES[tf])
    fb = build_symbol_frame(pre, FRAMES[tf])
    ra = run_portfolio(fa, S)
    rb = run_portfolio(fb, S)
    guard_ts = pre.bars[-1].ts
    same = diff = 0
    examples = []
    for s in S:
        A = [t for t in ra[s.strategy_id].trades if t.exit_ts and t.exit_ts < guard_ts]
        B = [t for t in rb[s.strategy_id].trades if t.exit_ts and t.exit_ts < guard_ts]
        ka = [(t.entry_ts, t.direction.value, round(t.net_r, 6)) for t in A]
        kb = [(t.entry_ts, t.direction.value, round(t.net_r, 6)) for t in B]
        if ka == kb:
            same += 1
        else:
            diff += 1
            if len(examples) < 3:
                examples.append({"id": s.strategy_id, "full": len(ka), "prefix": len(kb)})
    return {"symbol": symbol, "tf": tf, "strategies": len(S),
            "identical": same, "differing": diff, "examples": examples,
            "cut_bar": str(guard_ts)}


def costs(symbol: str, tf: int, budget: int = 2000):
    """How much of each R the cost model is actually taking, and what a
    doubled-slippage stress does to the ranking head."""
    ser = T._series(symbol, tf, 274)
    frame = build_symbol_frame(ser, FRAMES[tf])
    S = W.population(symbol, tf, budget, seed=1)
    spec = get_contract(symbol)
    base = run_portfolio(frame, S)
    from futures_agents.backtest.costs import SlippageModel
    harsh = CostModel(spec=spec, slippage=SlippageModel(
        base_ticks=1.0, stop_order_extra_ticks=2.0, volatility_coefficient=1.2,
        thin_book_extra_ticks=2.0, news_extra_ticks=4.0))
    stressed = BacktestEngine(frame, harsh).run_many(S)
    rows = []
    for s in S:
        tb = base[s.strategy_id].trades
        ts_ = stressed[s.strategy_id].trades
        if len(tb) < 20:
            continue
        gb = sum(t.gross_r for t in tb) / len(tb)
        nb = sum(t.net_r for t in tb) / len(tb)
        ns = (sum(t.net_r for t in ts_) / len(ts_)) if ts_ else 0.0
        rows.append(dict(id=s.strategy_id, n=len(tb), gross=round(gb, 4),
                         net=round(nb, 4), cost=round(gb - nb, 4),
                         net_2x=round(ns, 4), n_2x=len(ts_)))
    rows.sort(key=lambda r: -r["net"])
    import statistics as st
    return {"symbol": symbol, "tf": tf, "qualifying": len(rows),
            "median_cost_r_per_trade": round(st.median([r["cost"] for r in rows]), 4) if rows else None,
            "max_cost_r_per_trade": round(max(r["cost"] for r in rows), 4) if rows else None,
            "top10_base_net": [r["net"] for r in rows[:10]],
            "top10_under_2x_slippage": [r["net_2x"] for r in rows[:10]],
            "top10_still_positive_at_2x": sum(1 for r in rows[:10] if r["net_2x"] > 0)}


def parameter_sensitivity(symbol: str, tf: int, budget: int = 12000,
                          sib_floor: int = 5, min_siblings: int = 2):
    """Neighbourhood stability. For each qualifying rule set, how do its
    SIBLINGS - the same signal set under every other exit geometry the
    combinator offers - perform? A rule set whose edge lives in one exit cell
    and nowhere else is an exit-parameter artefact."""
    import statistics as st
    ser = T._series(symbol, tf, 274)
    frame = build_symbol_frame(ser, FRAMES[tf])
    S = W.population(symbol, tf, budget, seed=1)
    res = run_portfolio(frame, S)
    from collections import defaultdict
    fam = defaultdict(list)
    for s in S:
        trades = res[s.strategy_id].trades
        if len(trades) < sib_floor:
            continue
        key = (s.group, tuple(sorted(c.name for c in s.conditions)), s.execution_tf)
        from futures_agents.backtest.metrics import compute_metrics
        fam[key].append((s.exit.identity, compute_metrics(trades).expectancy_r, len(trades)))
    out = []
    for k, v in fam.items():
        if len(v) < min_siblings:
            continue
        exps = [x[1] for x in v]
        out.append(dict(group=k[0], conds=list(k[1]), siblings=len(v),
                        best=round(max(exps), 4), median=round(st.median(exps), 4),
                        worst=round(min(exps), 4),
                        frac_positive=round(sum(1 for e in exps if e > 0) / len(exps), 3)))
    out.sort(key=lambda r: -r["best"])
    if not out:
        return {"symbol": symbol, "tf": tf, "families": 0}
    return {"symbol": symbol, "tf": tf, "families": len(out),
            "sibling_floor": sib_floor, "min_siblings": min_siblings,
            "median_spread_best_minus_worst": round(st.median(
                [r["best"] - r["worst"] for r in out]), 4),
            "median_frac_positive_across_siblings":
                round(st.median([r["frac_positive"] for r in out]), 3),
            "top10_by_best_exit": out[:10],
            "top10_median_of_their_siblings":
                [r["median"] for r in out[:10]]}


if __name__ == "__main__":
    what = sys.argv[1]
    sym, tf = sys.argv[2], int(sys.argv[3])
    fn = {"lookahead": lookahead, "costs": costs, "sens": parameter_sensitivity}[what]
    r = fn(sym, tf)
    print(json.dumps(r, default=str))
    with open(f"/home/user/Futures01/workspace/strategy_research/w2/cells/audit_{what}_{sym}_{tf}.json", "w") as fh:
        json.dump(r, fh, default=str, indent=1)
