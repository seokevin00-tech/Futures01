"""Where, when and in what regime the full chain's trades happen - and whether it matters."""
import json, math, os, statistics as st, sys
os.chdir('/home/user/Futures01')
sys.path[:0] = ['/home/user/Futures01', 'workspace/studies', 'workspace/newstrats']
import toolkit as T
import ict_sweep as I
from ict_perf import EXITS
from futures_agents.backtest.engine import run_portfolio
from futures_agents.backtest.metrics import compute_metrics
from futures_agents.features import build_symbol_frame
from futures_agents.scout import FRAMES

ARMS = ("full", "sweep_mss", "mss_fvg_retrace", "mss_only", "wrong_order")
trades = {a: [] for a in ARMS}
for sym in ['MES', 'MNQ', 'MGC', 'MCL']:
    for tf in [15, 60, 240]:
        series = T._series(sym, tf, None)
        frame = build_symbol_frame(series, FRAMES[tf])
        I.clear(); I.register(I.build(sym, tf, series.bars))
        ss = [T.make_strategy(sym, tf, [I.get(a)], group="ICT", name=a,
                              exit_model=EXITS["anchored"]) for a in ARMS]
        res = run_portfolio(frame, ss, start=250, end=len(series.bars))
        for s, a in zip(ss, ARMS):
            for t in res[s.strategy_id].trades:
                trades[a].append(dict(r=t.net_r, session=t.session or "?",
                                      bucket=t.time_bucket or "?", regime=str(t.regime),
                                      vol=str(t.volatility), dow=t.day_of_week,
                                      dirn=t.direction.value, sym=sym, tf=tf,
                                      mae=t.mae_r, mfe=t.mfe_r, bars=t.bars_held,
                                      exit=str(t.exit_reason)))


def brk(rows, key):
    out = {}
    for g in sorted({r[key] for r in rows}):
        v = [r['r'] for r in rows if r[key] == g]
        if len(v) < 8:
            continue
        m, sd = st.mean(v), (st.pstdev(v) or 1e-9)
        out[str(g)] = {"n": len(v), "exp": round(m, 4),
                       "win": round(sum(1 for x in v if x > 0) / len(v), 3),
                       "t": round(m / (sd / math.sqrt(len(v))), 3),
                       "total_r": round(sum(v), 2)}
    return out


out = {}
for a in ARMS:
    rows = trades[a]
    v = [r['r'] for r in rows]
    out[a] = {"n": len(rows), "expectancy": round(st.mean(v), 4),
              "win_rate": round(sum(1 for x in v if x > 0) / len(v), 4),
              "avg_win": round(st.mean([x for x in v if x > 0]), 4),
              "avg_loss": round(st.mean([x for x in v if x <= 0]), 4),
              "avg_mae_r": round(st.mean([r['mae'] for r in rows]), 3),
              "avg_mfe_r": round(st.mean([r['mfe'] for r in rows]), 3),
              "avg_bars_held": round(st.mean([r['bars'] for r in rows]), 2),
              "by_session": brk(rows, 'session'), "by_time_bucket": brk(rows, 'bucket'),
              "by_regime": brk(rows, 'regime'), "by_volatility": brk(rows, 'vol'),
              "by_day": brk(rows, 'dow'), "by_direction": brk(rows, 'dirn'),
              "by_timeframe": brk(rows, 'tf'), "by_symbol": brk(rows, 'sym'),
              "by_exit_reason": brk(rows, 'exit')}
json.dump(out, open('/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad/regime.json', 'w'), indent=1, default=str)
f = out['full']
print("full chain, all cells pooled:", f['n'], "trades  exp", f['expectancy'],
      " win", f['win_rate'], " avgW", f['avg_win'], " avgL", f['avg_loss'])
for k in ('by_session', 'by_volatility', 'by_regime', 'by_direction', 'by_timeframe', 'by_symbol'):
    print(f"  {k}:", {g: (v['n'], v['exp'], v['t']) for g, v in f[k].items()})
