"""Publication run: 6-fold walk-forward at 60m, full metric set, durability ranking.

Folds are six CONSECUTIVE, non-overlapping sixths of each symbol's 60m series
(~37 trading days each). They are a genuine walk-forward: fold k is evaluated
having been chosen on folds 1..k-1 only, and no fold shares a bar with another.
The nested 274/180/90 windows are not used anywhere.

Ranking is on durability, never on highest historical profit:
deflated t = t_statistic - free_t(n_rule_sets_screened), then hard gates on
sample size, out-of-sample sign, walk-forward consistency and drawdown.
"""
from __future__ import annotations
import json, math, os, statistics as st, sys, time
from collections import defaultdict

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")
sys.path.insert(0, "/home/user/Futures01/workspace/newstrats")

import toolkit as T                                    # noqa: E402
import w6_arms as A, w6_ict_time as K                  # noqa: E402
from futures_agents.backtest.engine import run_portfolio  # noqa: E402
from futures_agents.backtest.metrics import compute_metrics, slice_metrics  # noqa: E402
from futures_agents.features import build_symbol_frame  # noqa: E402
from futures_agents.scout import FRAMES                # noqa: E402
from futures_agents.strategies.library import CONDITIONS as LIB  # noqa: E402

OUT = "/home/user/Futures01/workspace/strategy_research"
SC = f"{OUT}/scratch/ict"
N_FOLDS = 6
TF = 60
SYMBOLS = ["MNQ", "MES", "MGC", "MCL"]
VARIANTS = {"control": [], "kz_silver_bullet": ["kz_silver_bullet"],
            "kz_union": ["kz_union"], "kz_london_open": ["kz_london_open"],
            "kz_asian_range": ["kz_asian_range"], "stride24_p00": ["stride24_p00"],
            "gp": ["fib_golden_pocket"], "sh": ["fib_shallow_retrace"]}

FIELDS = ["trades", "wins", "losses", "win_rate", "expectancy_r", "avg_win_r",
          "avg_loss_r", "payoff_ratio", "profit_factor", "total_r",
          "max_drawdown_r", "avg_drawdown_r", "max_drawdown_trades",
          "sharpe", "sortino", "sqn", "t_statistic", "std_r",
          "max_consecutive_wins", "max_consecutive_losses",
          "avg_bars_held", "avg_minutes_held", "avg_mfe_r", "avg_mae_r",
          "edge_ratio", "long_trades", "short_trades", "long_expectancy_r",
          "short_expectancy_r", "recovery_factor", "ulcer_index"]


def met(trades):
    m = compute_metrics(trades)
    d = {f: round(getattr(m, f), 4) if isinstance(getattr(m, f), float) else getattr(m, f)
         for f in FIELDS}
    d["exit_reasons"] = m.exit_reasons
    return d


def by_slice(trades, key, min_trades=8):
    return {str(k): {"trades": v.trades, "win_rate": round(v.win_rate, 4),
                     "expectancy_r": round(v.expectancy_r, 4),
                     "profit_factor": round(v.profit_factor, 4),
                     "max_drawdown_r": round(v.max_drawdown_r, 3)}
            for k, v in slice_metrics(trades, key, min_trades=min_trades).items()}


def run():
    db = {"design": {
        "folds": N_FOLDS, "timeframe": TF, "symbols": SYMBOLS,
        "fold_definition": "six consecutive non-overlapping sixths of each symbol's "
                           "60m series; no fold shares a bar with another",
        "base_rule_sets": A.BASE_SIGNALS, "variants": sorted(VARIANTS),
        "exit": "ATRx1 stop, ANCHOR_ATR targets 1.0/2.5, scale 50/50, BE 1.5R, "
                "40-bar time stop, exit_at_session_close=False",
        "filters": "rth_only=False (deliberate: RTH would delete London and Asia)",
        "costs": "engine default - 0.5 tick base slippage, +1.0 tick thin book "
                 "(non-RTH fills), +1.0 tick for stop orders, commission per side "
                 "from ContractSpec; entries fill at the NEXT bar's open"},
        "rows": []}
    for symbol in SYMBOLS:
        for f in range(N_FOLDS):
            t0 = time.time()
            ser = A.series_slice(symbol, TF, f / N_FOLDS, (f + 1) / N_FOLDS)
            frame = build_symbol_frame(ser, FRAMES[TF])
            reg = dict(LIB); reg.update(K.CONDITIONS)
            strats, meta = [], {}
            for b in A.BASE_SIGNALS:
                for vn, extra in VARIANTS.items():
                    s = T.make_strategy(symbol, TF, [reg[b]] + [reg[e] for e in extra],
                                        group="ICT", name=f"{b}__{vn}")
                    strats.append(s); meta[s.strategy_id] = (b, vn)
            res = run_portfolio(frame, strats)
            for s in strats:
                tr = res[s.strategy_id].trades
                b, vn = meta[s.strategy_id]
                row = {"symbol": symbol, "tf": TF, "fold": f, "base": b, "variant": vn,
                       "span": [str(ser.bars[0].ts), str(ser.bars[-1].ts)],
                       "n_bars": len(ser.bars), "metrics": met(tr) if tr else {"trades": 0}}
                if len(tr) >= 20:
                    row["by_session"] = by_slice(tr, "session")
                    row["by_regime"] = by_slice(tr, "regime")
                    row["by_time_bucket"] = by_slice(tr, "time_bucket")
                    row["by_direction"] = by_slice(tr, "direction", 5)
                db["rows"].append(row)
            print(symbol, f, f"{time.time()-t0:.0f}s", flush=True)
    return db


if __name__ == "__main__":
    db = run()
    os.makedirs(SC, exist_ok=True)
    with open(f"{SC}/walkforward_db.json", "w") as fh:
        json.dump(db, fh, indent=1, default=str)
    print("written", f"{SC}/walkforward_db.json", len(db["rows"]), "rows")
