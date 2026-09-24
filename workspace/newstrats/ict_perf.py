"""Ablation backtest of the ICT sequence, out of sample by construction.

Six arms, matched on everything except the entry rule: the same bars, the same symbol, the
same exit model, the same costs, one strategy per arm.  There is no search here - no parameter
is chosen by looking at the result - so there is nothing for a deflation to charge against
except the six arms and the handful of exits, which is stated rather than hidden.

The comparison is per cell and paired.  ``T.ab`` is deliberately NOT used anywhere: it runs an
unpaired rank-sum over strategies, and these arms share 50-90% of their bars, which is exactly
the correlation structure defect D28 measures inflating |z| by ~3.3x.
"""
import json, os, sys
os.chdir('/home/user/Futures01')
sys.path[:0] = ['/home/user/Futures01', 'workspace/studies', 'workspace/newstrats']
import toolkit as T
import ict_sweep as I
from futures_agents.backtest.engine import run_portfolio
from futures_agents.backtest.metrics import compute_metrics
from futures_agents.features import build_symbol_frame
from futures_agents.scout import FRAMES
from futures_agents.strategies.base import ExitModel, StopKind, TargetKind

EXITS = {
    # the exit that survived the study programme (T.make_strategy's default)
    "anchored": None,
    # pure R-multiples: immune to min_reward_risk, so a veto cannot masquerade as a signal
    "r2": ExitModel(StopKind.ATR, 1.0, targets_r=(1.0, 2.0), scale_out=(0.5, 0.5),
                    breakeven_at_r=1.0, time_stop_bars=40,
                    target_kind=TargetKind.R_MULTIPLE, exit_at_session_close=False),
    # ICT's own risk model: stop beyond the structure, target the prior swing
    "struct": ExitModel(StopKind.STRUCTURE, 1.0, targets_r=(1.0, 2.0), scale_out=(0.5, 0.5),
                        breakeven_at_r=1.0, time_stop_bars=40,
                        target_kind=TargetKind.R_MULTIPLE, exit_at_session_close=False),
}


def row(trades):
    m = compute_metrics(trades)
    return dict(n=m.trades, exp=round(m.expectancy_r, 4), win=round(m.win_rate, 4),
                pf=round(m.profit_factor, 4), rr=round(m.payoff_ratio, 4),
                t=round(m.t_statistic, 3), maxdd=round(m.max_drawdown_r, 3),
                avgdd=round(m.avg_drawdown_r, 3), sharpe=round(m.sharpe, 3),
                sortino=round(m.sortino, 3), sqn=round(m.sqn, 3),
                avg_win=round(m.avg_win_r, 4), avg_loss=round(m.avg_loss_r, 4),
                max_cons_w=m.max_consecutive_wins, max_cons_l=m.max_consecutive_losses,
                mfe=round(m.avg_mfe_r, 3), mae=round(m.avg_mae_r, 3),
                bars=round(m.avg_bars_held, 2), mins=round(m.avg_minutes_held, 1),
                total_r=round(m.total_r, 3),
                rs=[round(t.net_r, 5) for t in trades])


def run(sym, tf, cfg=I.Cfg(), arms=I.ARMS, suffix=""):
    series = T._series(sym, tf, None)
    frame = build_symbol_frame(series, FRAMES[tf])
    built = I.build(sym, tf, series.bars, cfg)
    counts = I.register(built, suffix)
    n = len(series.bars)
    strats, meta = [], {}
    for a in arms:
        for ex, em in EXITS.items():
            s = T.make_strategy(sym, tf, [I.get(a + suffix)], group="ICT",
                                name=f"{a}|{ex}", exit_model=em)
            strats.append(s)
            meta[s.strategy_id] = (a, ex)
    windows = {"full": (250, n), "IS": (250, int(n * 0.6)), "OOS": (int(n * 0.6), n)}
    third = (n - 250) // 3
    for k in range(3):
        windows[f"slice{k+1}"] = (250 + k * third, 250 + (k + 1) * third)
    out = {}
    for w, (s0, s1) in windows.items():
        res = run_portfolio(frame, strats, start=s0, end=s1)
        for sid, (a, ex) in meta.items():
            out[f"{a}|{ex}|{w}"] = row(res[sid].trades)
            out[f"{a}|{ex}|{w}"].update(arm=a, exit=ex, window=w, symbol=sym, tf=tf,
                                        bars=s1 - s0)
    return out, counts, built.census


if __name__ == "__main__":
    db, cens, sig = {}, {}, {}
    for sym in ['MES', 'MNQ', 'MGC', 'MCL']:
        for tf in [15, 60, 240]:
            I.clear()
            o, c, ce = run(sym, tf)
            db[f"{sym}|{tf}"] = o
            cens[f"{sym}|{tf}"] = ce
            sig[f"{sym}|{tf}"] = c
            print(sym, tf, {a: c[a] for a in I.ARMS},
                  {a: o[f"{a}|anchored|full"]["n"] for a in I.ARMS}, flush=True)
    json.dump({"db": db, "signals": sig, "census": cens},
              open('/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad/perf.json', 'w'), indent=1, default=str)
