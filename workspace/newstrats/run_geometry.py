"""Matched-arm experiment for swing geometry.

Design
------
Every geometry strategy is the CONTROL strategy plus one geometry condition, on
the same bars, with the same partner filter and the same exit. So the two arms
differ in exactly one thing and the comparison is nested rather than a league
table:

    control :  [structure_trend_ctl]                 + partner + exit
    test    :  [structure_trend_ctl, <geometry>]      + partner + exit

Fade arms use ``structure_trend_fade_ctl`` as their own control, because a fade
that beat a follow-the-trend control would be measuring direction, not shape.

Partners are FILTER-kind conditions: they veto, they never propose a direction,
so they cannot change which way an arm trades.

Periods are three genuinely disjoint slices. Slices 1-2 are in sample, slice 3
(the most recent third) is untouched out of sample.
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, "workspace/studies")
sys.path.insert(0, "workspace/newstrats")

import geometry as G                                        # noqa: E402
import toolkit as T                                         # noqa: E402
from futures_agents.backtest.engine import run_portfolio    # noqa: E402
from futures_agents.backtest.metrics import compute_metrics  # noqa: E402
from futures_agents.features import build_symbol_frame      # noqa: E402
from futures_agents.scout import FRAMES                     # noqa: E402
from futures_agents.strategies.base import (ExitModel, StopKind,  # noqa: E402
                                            StrategyFilters, TargetKind)
from futures_agents.strategies.library import get_condition  # noqa: E402

SYMBOLS = ["MGC", "MES", "MNQ", "MCL"]
TFS = [240, 60]

#: Partner filters. Diverse, none of them structural or fibonacci, so they
#: cannot smuggle the thing being tested into the control arm.
PARTNERS = ["none", "adx_trending", "efficiency_high", "volatility_normal",
            "volatility_expanding", "regime_trending", "regime_ranging",
            "volume_not_thin", "relative_volume_high", "avoid_lunch",
            "away_from_zone", "outside_news_blackout", "no_recent_imbalance"]

#: Two exits. The trade floor selects on exit geometry, so a single exit would
#: confound "this condition works" with "this condition survives this stop".
EXITS = {
    "atr1.0": ExitModel(StopKind.ATR, 1.0, targets_r=(2.0, 4.0),
                        scale_out=(0.5, 0.5), breakeven_at_r=1.5,
                        time_stop_bars=40, target_kind=TargetKind.ANCHOR_ATR,
                        anchor_mult=(1.0, 2.5), min_reward_risk=1.5,
                        exit_at_session_close=False),
    "atr1.5": ExitModel(StopKind.ATR, 1.5, targets_r=(2.0, 4.0),
                        scale_out=(0.5, 0.5), breakeven_at_r=1.5,
                        time_stop_bars=40, target_kind=TargetKind.ANCHOR_ATR,
                        anchor_mult=(1.0, 2.5), min_reward_risk=1.5,
                        exit_at_session_close=False),
}

FOLLOW = ["legs_expanding", "legs_contracting", "pullbacks_shallowing",
          "pullbacks_deepening", "swing_symmetry_impulse",
          "swing_symmetry_retrace"]
FADE = ["legs_contracting_fade", "pullbacks_deepening_fade",
        "swing_symmetry_retrace_fade"]


def build(symbol, tf):
    """Every strategy in the experiment for one (symbol, tf), tagged."""
    out = []
    for base, arms in (("structure_trend_ctl", ["CONTROL"] + FOLLOW),
                       ("structure_trend_fade_ctl", ["CONTROL_FADE"] + FADE)):
        for arm in arms:
            conds = [G.get(base)]
            if not arm.startswith("CONTROL"):
                conds.append(G.get(arm))
            for pname in PARTNERS:
                cs = list(conds)
                if pname != "none":
                    cs.append(get_condition(pname))
                for ename, ex in EXITS.items():
                    s = T.make_strategy(
                        symbol, tf, cs, group="GEOMETRY",
                        name=f"{arm}|{pname}|{ename}", exit_model=ex,
                        filters=StrategyFilters(rth_only=False))
                    out.append((arm, base, pname, ename, s))
    return out


def run_cell(symbol, tf, lo, hi, label, floor=5):
    series = T.slice_series(symbol, tf, lo, hi)
    if len(series) < 150:
        return [], []
    frame = build_symbol_frame(series, FRAMES[tf])
    G.clear()
    G.reset_stats()
    G.register_frame(frame)
    tagged = build(symbol, tf)
    strategies = [t[-1] for t in tagged]
    res = run_portfolio(frame, strategies)
    rows, tdump = [], []
    for arm, base, pname, ename, s in tagged:
        trades = res[s.strategy_id].trades
        if pname == "none":
            for t in trades:
                tdump.append(dict(
                    cell=f"{symbol}_{tf}_{label}", symbol=symbol, tf=tf,
                    slice=label, arm=arm, base=base, exitm=ename,
                    ts=t.entry_ts.isoformat(), dir=t.direction.value,
                    r=round(t.net_r, 5), mae=round(t.mae_r, 4),
                    mfe=round(t.mfe_r, 4), mins=round(t.minutes_held, 1),
                    session=t.session, regime=t.regime, vol=t.volatility,
                    reason=t.exit_reason.value))
        m = compute_metrics(trades)
        lo_w, hi_w = T.wilson(m.wins, m.trades) if m.trades else (0.0, 0.0)
        rows.append(dict(
            cell=f"{symbol}_{tf}_{label}", symbol=symbol, tf=tf, slice=label,
            arm=arm, base=base, partner=pname, exitm=ename,
            fp=T.fingerprint(trades), bars=len(series),
            n=m.trades, win=round(m.win_rate, 4),
            win_lo=round(lo_w, 4), win_hi=round(hi_w, 4),
            exp=round(m.expectancy_r, 4), rr=round(m.payoff_ratio, 4),
            pf=round(m.profit_factor, 4), t=round(m.t_statistic, 3),
            maxdd=round(m.max_drawdown_r, 3), avgdd=round(m.avg_drawdown_r, 3),
            sharpe=round(m.sharpe, 3), sortino=round(m.sortino, 3),
            sqn=round(m.sqn, 3), avg_win=round(m.avg_win_r, 4),
            avg_loss=round(m.avg_loss_r, 4),
            maxcw=m.max_consecutive_wins, maxcl=m.max_consecutive_losses,
            mfe=round(m.avg_mfe_r, 3), mae=round(m.avg_mae_r, 3),
            edge_ratio=round(m.edge_ratio, 3),
            mins=round(m.avg_minutes_held, 1),
            long_n=m.long_trades, short_n=m.short_trades,
            long_exp=round(m.long_expectancy_r, 4),
            short_exp=round(m.short_expectancy_r, 4),
            floor_ok=m.trades >= floor))
    return rows, tdump


if __name__ == "__main__":
    allrows, alltrades = [], []
    for sym in SYMBOLS:
        for tf in TFS:
            sl = T.disjoint_slices(sym, tf, 3)
            for k, (lo, hi) in enumerate(sl, 1):
                lab = f"S{k}"
                r, td = run_cell(sym, tf, lo, hi, lab)
                allrows += r
                alltrades += td
                nz = [x for x in r if x["n"] >= 5]
                print(f"{sym} {tf}m {lab} days[{lo}->{hi}] rows={len(r)} "
                      f"with>=5 trades={len(nz)} "
                      f"median_n={st.median([x['n'] for x in r]) if r else 0}",
                      flush=True)
    os.makedirs("workspace/strategy_research/scratch", exist_ok=True)
    json.dump(allrows,
              open("workspace/strategy_research/scratch/geo_rows.json", "w"),
              default=str)
    json.dump(alltrades,
              open("workspace/strategy_research/scratch/geo_trades.json", "w"),
              default=str)
    print("rows", len(allrows), "trades", len(alltrades))
