"""Parameter sensitivity + cost stress + a search-gap demonstration."""
import sys, json, statistics as st
sys.path.insert(0, '/home/user/Futures01')
sys.path.insert(0, '/home/user/Futures01/workspace/studies')
sys.path.insert(0, '/home/user/Futures01/workspace/newstrats')
import toolkit as T, leadlag as L
from run_bt import ARMS, PARTNERS, build
from futures_agents.backtest.costs import CostModel, SlippageModel
from futures_agents.backtest.engine import run_portfolio
from futures_agents.backtest.metrics import compute_metrics
from futures_agents.config import get_contract
from futures_agents.features import build_symbol_frame
from futures_agents.scout import FRAMES
from futures_agents.strategies.library import get_condition

SC = '/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad'
SYMS = ["MGC", "MES", "NQ", "MNQ", "MCL"]
SENS = ["EARLY_fresh0", "EARLY_fresh1", "EARLY_fresh3", "EARLY_ltf_break_first",
        "CONFIRM_late0", "CONFIRM_late4", "CONFIRM_late8"]

def run(sym, tf, series, strats, cost):
    frame = build_symbol_frame(series, FRAMES[tf])
    res = run_portfolio(frame, strats, cost_model=cost)
    out = []
    for s in strats:
        tr = res[s.strategy_id].trades
        if len(tr) < 20: continue
        m = compute_metrics(tr)
        out.append(dict(arm=s.group, name=s.name, n=m.trades, exp=round(m.expectancy_r,4),
                        win=round(m.win_rate,4), pf=round(m.profit_factor,4),
                        maxcl=m.max_consecutive_losses, maxcw=m.max_consecutive_wins,
                        t=round(m.t_statistic,3)))
    return out

base, stress = {}, {}
for sym in SYMS:
    spec = get_contract(sym)
    hard = CostModel(spec, slippage=SlippageModel(base_ticks=1.5, stop_order_extra_ticks=2.0,
                                                  volatility_coefficient=1.2,
                                                  thin_book_extra_ticks=2.0))
    for si,(a,b) in enumerate(T.disjoint_slices(sym,60,n=3)):
        series = T.slice_series(sym,60,a,b)
        strats = [s for arm in SENS for s in build(sym,60,arm)]
        for tag, cm, store in [("base", None, base), ("2x_slippage", hard, stress)]:
            for r in run(sym,60,series,strats,cm):
                r.update(symbol=sym, cell=f"slice{si}")
                store.setdefault(r["arm"], []).append(r)
    print(sym, flush=True)

def summ(store):
    return {a: dict(n_strategies=len(v),
                    median_exp=round(st.median([r["exp"] for r in v]),4),
                    median_win=round(st.median([r["win"] for r in v]),4),
                    median_pf=round(st.median([r["pf"] for r in v]),3),
                    pct_profitable=round(sum(1 for r in v if r["exp"]>0)/len(v),3),
                    median_max_consec_losses=st.median([r["maxcl"] for r in v]),
                    median_max_consec_wins=st.median([r["maxcw"] for r in v]),
                    median_trades=st.median([r["n"] for r in v]))
            for a,v in sorted(store.items())}

out = dict(base=summ(base), stress_2x_slippage=summ(stress))
print("\nPARAMETER SENSITIVITY + COST STRESS (floor 20, 15 cells pooled for display only)")
print(f"{'arm':26} {'base_exp':>9} {'base_%prof':>10} {'2x_exp':>9} {'2x_%prof':>9} {'delta':>8} {'maxCL':>6}")
for a in out["base"]:
    b, s = out["base"][a], out["stress_2x_slippage"].get(a)
    if not s: print(f"{a:26} {b['median_exp']:9.4f} {b['pct_profitable']:10.2f}   (stress empty)"); continue
    print(f"{a:26} {b['median_exp']:9.4f} {b['pct_profitable']:10.2f} {s['median_exp']:9.4f} {s['pct_profitable']:9.2f} "
          f"{s['median_exp']-b['median_exp']:+8.4f} {b['median_max_consec_losses']:6.0f}")
json.dump(out, open(f'{SC}/stress.json','w'), indent=1, default=str)
