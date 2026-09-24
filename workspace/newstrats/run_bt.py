"""Matched arm comparison: early entry vs confirmation entry vs the incumbent.

Every arm is the SAME 14-strategy population (bare entry + 13 partner
conditions drawn from 13 different diversity groups) with only the entry rule
swapped. Same bars, same exit, same costs, same search size - so a difference
between arms is the entry rule and nothing else.
"""
import sys, json, os
sys.path.insert(0, '/home/user/Futures01')
sys.path.insert(0, '/home/user/Futures01/workspace/studies')
sys.path.insert(0, '/home/user/Futures01/workspace/newstrats')
import toolkit as T
import leadlag as L
from futures_agents.backtest.engine import run_portfolio
from futures_agents.backtest.metrics import compute_metrics
from futures_agents.features import build_symbol_frame
from futures_agents.scout import FRAMES
from futures_agents.strategies.library import get_condition

SYMS = ["MGC", "MES", "NQ", "MNQ", "MCL"]

ARMS = {
    "EARLY_ltf_break_first":      ["ltf_break_first"],
    "EARLY_fresh0":               ["ltf_break_first_fresh0"],
    "EARLY_fresh1":               ["ltf_break_first_fresh1"],
    "EARLY_fresh3":               ["ltf_break_first_fresh3"],
    "CONFIRM_late0":              ["htf_confirms_late0"],
    "CONFIRM_late4":              ["htf_confirms_late4"],
    "CONFIRM_late8":              ["htf_confirms_late8"],
    "CONFIRM_htf_broken_now":     ["htf_broken_now"],
    "ALIGNED_both_broken":        ["ltf_and_htf_both_broken"],
    "INCUMBENT_bos_ltf":          ["break_of_structure"],
    "INCUMBENT_bos_htf_bound":    [("break_of_structure", 240)],
}

PARTNERS = ["adx_trending", "rsi_directional", "above_vwap", "volume_not_thin",
            "cvd_directional", "volatility_normal", "regime_trending",
            "avoid_lunch", "candle_decisive_close", "bollinger_mean_pull",
            "fvg_nearby", "efficiency_high", "outside_news_blackout"]


def build(sym, tf, arm):
    conds = []
    for c in ARMS[arm]:
        conds.append(get_condition(c[0]).bind(c[1]) if isinstance(c, tuple)
                     else get_condition(c))
    out = [T.make_strategy(sym, tf, conds, group=arm, name=f"{arm}__bare")]
    for p in PARTNERS:
        out.append(T.make_strategy(sym, tf, conds + [get_condition(p)],
                                   group=arm, name=f"{arm}__{p}"))
    return out


def rows_for(sym, tf, series, strategies, cell):
    frame = build_symbol_frame(series, FRAMES[tf])
    res = run_portfolio(frame, list(strategies))
    seen, out = {}, []
    for s in strategies:
        tr = res[s.strategy_id].trades
        if not tr:
            out.append(dict(name=s.name, arm=s.group, symbol=sym, tf=tf, cell=cell,
                            n=0, exp=None, win=None, pf=None, t=None, maxdd=None,
                            rr=None, clone_of=None))
            continue
        fp = T.fingerprint(tr)
        m = compute_metrics(tr)
        mae = [t.mae_r for t in tr if getattr(t, "mae_r", None) is not None]
        mfe = [t.mfe_r for t in tr if getattr(t, "mfe_r", None) is not None]
        hold = [(t.exit_ts - t.entry_ts).total_seconds() / 60.0 for t in tr
                if t.exit_ts and t.entry_ts]
        out.append(dict(
            name=s.name, arm=s.group, symbol=sym, tf=tf, cell=cell,
            n=m.trades, win=round(m.win_rate, 4), exp=round(m.expectancy_r, 4),
            rr=round(m.payoff_ratio, 4), pf=round(m.profit_factor, 4),
            maxdd=round(m.max_drawdown_r, 3), t=round(m.t_statistic, 3),
            sortino=round(m.sortino, 3),
            avg_win=round(m.avg_win_r, 4) if hasattr(m, "avg_win_r") else None,
            avg_loss=round(m.avg_loss_r, 4) if hasattr(m, "avg_loss_r") else None,
            mae=round(sum(mae) / len(mae), 4) if mae else None,
            mfe=round(sum(mfe) / len(mfe), 4) if mfe else None,
            hold_min=round(sum(hold) / len(hold), 1) if hold else None,
            clone_of=seen.get(fp)))
        seen.setdefault(fp, s.name)
    return out


def main():
    allrows = []
    for sym in SYMS:
        slices = T.disjoint_slices(sym, 60, n=3)
        for si, (a, b) in enumerate(slices):
            series = T.slice_series(sym, 60, a, b)
            if len(series) < 400:
                print("skip", sym, si, len(series)); continue
            strats = [s for arm in ARMS for s in build(sym, 60, arm)]
            r = rows_for(sym, 60, series, strats, f"slice{si}")
            for x in r:
                x["slice_days"] = [a, b]
            allrows += r
            print(sym, "60m", f"slice{si}", len(series), "bars", len(r), "rows", flush=True)
        # the untested thread: break_of_structure at 240m on its own bars
        for si, (a, b) in enumerate(T.disjoint_slices(sym, 240, n=3)):
            s240 = T.slice_series(sym, 240, a, b)
            if len(s240) < 150:
                print("skip240", sym, si, len(s240)); continue
            st = T.make_strategy(sym, 240, [get_condition("break_of_structure")],
                                 group="INCUMBENT240_bos", name="INCUMBENT240_bos__bare")
            more = [T.make_strategy(sym, 240,
                                    [get_condition("break_of_structure"), get_condition(p)],
                                    group="INCUMBENT240_bos", name=f"INCUMBENT240_bos__{p}")
                    for p in PARTNERS]
            r = rows_for(sym, 240, s240, [st] + more, f"slice{si}")
            for x in r:
                x["slice_days"] = [a, b]
            allrows += r
            print(sym, "240m", f"slice{si}", len(s240), "bars", flush=True)
    json.dump(allrows, open('/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad/bt.json', 'w'),
              indent=0, default=str)
    print("saved", len(allrows))


if __name__ == "__main__":
    main()
