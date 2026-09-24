"""Matched-arm harness: same bars, same base rule set, same exit, one thing swapped.

Defect D28 forbids ``T.ab`` here.  ``T.ab`` runs an unpaired rank-sum over a
population of strategies, and variants of the same base rule set share most of
their trades; treating them as independent inflated |z| by about 3.3x on real
data and reached |z| = 8.7 on null data.  So every comparison in this study is
built as explicit matched arms:

    control  = Strategy(base)
    treated  = Strategy(base + [one extra condition])

evaluated on the same bars, and the test is a per-cell paired sign test on
Delta-expectancy across base rule sets, combined across cells with Stouffer.
A cell is one (symbol, timeframe, period).  Nothing is pooled across cells.

``rth_only`` is OFF in every arm.  It defaults True in ``StrategyFilters`` and
RTH on the index contracts is 09:30-16:00, which would delete the London-open
and Asian windows entirely and most of the NY-open window as well.  This is
stated in the findings rather than left implicit.
"""
from __future__ import annotations

import math
import statistics as st
import sys
from typing import Dict, List, Sequence

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")
sys.path.insert(0, "/home/user/Futures01/workspace/newstrats")

import toolkit as T                                    # noqa: E402
from futures_agents.backtest.engine import run_portfolio  # noqa: E402
from futures_agents.backtest.metrics import compute_metrics  # noqa: E402
from futures_agents.data.bars import BarSeries         # noqa: E402
from futures_agents.features import build_symbol_frame  # noqa: E402
from futures_agents.scout import FRAMES                # noqa: E402
from futures_agents.strategies.library import CONDITIONS as LIB  # noqa: E402

#: Base rule sets: one signal condition each, drawn from distinct groups so the
#: population is not thirty restatements of "momentum".  Singles rather than
#: confluences because a single signal fires often enough that the treated arm
#: still has trades after a 4.4%-of-bars filter is applied to it.
BASE_SIGNALS = [
    "ema_stack", "ema_fast_above_slow", "price_above_ema50", "di_direction",
    "slope_directional", "rsi_directional", "macd_directional",
    "macd_hist_direction", "stoch_directional", "rsi_extreme_reversal",
    "above_vwap", "vwap_band_extension", "vwap_reclaim", "cvd_directional",
    "delta_confirms_bar", "structure_trend", "break_of_structure",
    "pullback_to_support", "bollinger_extreme", "bollinger_mean_pull",
    "keltner_outside", "candle_reversal", "candle_engulfing",
    "candle_decisive_close", "poc_reversion", "value_area_edge",
    "imbalance_bar", "imbalance_pullback", "range_position_extreme",
    "regime_matches_direction",
]


def series_slice(symbol: str, tf: int, frac_lo: float, frac_hi: float):
    """A contiguous fraction of the series, by bar index - genuinely disjoint."""
    full = T._series(symbol, tf, None)
    bars = full.bars
    lo, hi = int(len(bars) * frac_lo), int(len(bars) * frac_hi)
    return BarSeries(symbol, tf, bars[lo:hi])


def run_arms(symbol: str, tf: int, series, base_names: Sequence[str],
             variants: Dict[str, Sequence], *, extra_registry=None,
             floor: int = 1, cost_model=None) -> List[dict]:
    """Every (base, variant) combination on one slice of bars.

    ``cost_model`` exists for one specific control. The engine sets
    ``thin = not is_rth(fill_ts)`` and the slippage model adds a full extra
    tick for a thin book, so an ET-clock comparison is partly a comparison of
    cost assumptions: a 10:00 entry fills in RTH at 0.5 ticks and a 21:00
    entry fills overnight at 1.5. Passing a model with
    ``thin_book_extra_ticks=0`` removes that asymmetry and says how much of a
    time-of-day result was the cost model.
    """
    reg = dict(LIB)
    if extra_registry:
        reg.update(extra_registry)
    frame = build_symbol_frame(series, FRAMES[tf])
    strats, meta = [], {}
    for b in base_names:
        if b not in reg:
            continue
        for vname, extra in variants.items():
            conds = [reg[b]] + [reg[e] if isinstance(e, str) else e for e in extra]
            s = T.make_strategy(symbol, tf, conds, group="ICT", name=f"{b}__{vname}")
            strats.append(s)
            meta[s.strategy_id] = (b, vname)
    res = run_portfolio(frame, strats, cost_model=cost_model)
    out = []
    for s in strats:
        trades = res[s.strategy_id].trades
        b, vname = meta[s.strategy_id]
        row = {"base": b, "variant": vname, "symbol": symbol, "tf": tf, "n": len(trades)}
        if len(trades) >= max(1, floor):
            m = compute_metrics(trades)
            row.update(exp=round(m.expectancy_r, 4), win=round(m.win_rate, 4),
                       pf=round(m.profit_factor, 4), rr=round(m.payoff_ratio, 4),
                       maxdd=round(m.max_drawdown_r, 3), t=round(m.t_statistic, 3),
                       sortino=round(m.sortino, 3),
                       wins=m.wins, losses=m.trades - m.wins)
        out.append(row)
    return out


def paired_sign(rows: Sequence[dict], treated: str, control: str,
                min_n: int = 10) -> dict:
    """Paired sign test on Delta-expectancy across base rule sets, within one cell."""
    by = {}
    for r in rows:
        by.setdefault(r["base"], {})[r["variant"]] = r
    pairs = []
    for b, v in by.items():
        a, c = v.get(treated), v.get(control)
        if not a or not c or a.get("n", 0) < min_n or c.get("n", 0) < min_n:
            continue
        pairs.append((b, a["exp"] - c["exp"], a["exp"], c["exp"], a["n"], c["n"]))
    if not pairs:
        return {"n_pairs": 0}
    pos = sum(1 for p in pairs if p[1] > 0)
    neg = sum(1 for p in pairs if p[1] < 0)
    d = [p[1] for p in pairs]
    return {"n_pairs": len(pairs), "better": pos, "worse": neg,
            "identical": len(pairs) - pos - neg,
            "sign_z": round((pos - neg) / math.sqrt(pos + neg), 3) if pos + neg else 0.0,
            "median_delta_exp": round(st.median(d), 4),
            "mean_delta_exp": round(st.mean(d), 4),
            "median_exp_treated": round(st.median([p[2] for p in pairs]), 4),
            "median_exp_control": round(st.median([p[3] for p in pairs]), 4),
            "median_n_treated": st.median([p[4] for p in pairs]),
            "median_n_control": st.median([p[5] for p in pairs]),
            "total_trades_treated": sum(p[4] for p in pairs),
            "total_trades_control": sum(p[5] for p in pairs)}


def stouffer(cells: Sequence[dict], key: str = "sign_z") -> dict:
    zs = [c[key] for c in cells if c.get("n_pairs", 0) > 0 and c.get(key) is not None]
    if not zs:
        return {"k_cells": 0}
    return {"k_cells": len(zs), "stouffer_z": round(sum(zs) / math.sqrt(len(zs)), 3),
            "cell_z": [round(z, 2) for z in zs],
            "cells_positive": sum(1 for z in zs if z > 0),
            "cells_negative": sum(1 for z in zs if z < 0)}
