"""Parameter sensitivity: how many bars may elapse between the sweep and the MSS?

An effect that exists at one setting of ``max_gap`` and nowhere else is an artefact of that
setting, so the only honest way to report a timing tolerance is to sweep it and show the whole
curve - including the settings that look worse.  Same for the retrace window and the minimum
penetration.  The frame is built once per cell and reused across every variant, so all
variants see identical bars, identical indicators and identical costs.
"""
import json, math, os, statistics as st, sys
os.chdir('/home/user/Futures01')
sys.path[:0] = ['/home/user/Futures01', 'workspace/studies', 'workspace/newstrats']
import toolkit as T
import ict_sweep as I
from ict_perf import EXITS, row
from futures_agents.backtest.engine import run_portfolio
from futures_agents.features import build_symbol_frame
from futures_agents.scout import FRAMES

ALL8 = ("PDH", "PDL", "ONH", "ONL", "SESH", "SESL", "SWH", "SWL")
VAR = ([(f"gap{g}", I.Cfg(max_gap=g, pools=ALL8)) for g in (1, 2, 3, 5, 8, 12, 20)]
       + [(f"retr{r}", I.Cfg(retrace_window=r, pools=ALL8)) for r in (3, 5, 20, 40)]
       + [(f"pen{p}", I.Cfg(min_pen_atr=p, pools=ALL8)) for p in (0.1, 0.25, 0.5)]
       + [("obvious", I.Cfg(pools=("PDH", "PDL", "ONH", "ONL"))),
          ("swingonly", I.Cfg(pools=("SWH", "SWL"))),
          ("reclaim2", I.Cfg(reclaim_bars=2, pools=ALL8)),
          ("swing2", I.Cfg(swing_left=2, swing_right=2, pools=ALL8)),
          ("swing5", I.Cfg(swing_left=5, swing_right=5, pools=ALL8))])

out = {}
for sym in ['MES', 'MNQ', 'MGC', 'MCL']:
    for tf in [60, 240]:
        series = T._series(sym, tf, None)
        frame = build_symbol_frame(series, FRAMES[tf])
        n = len(series.bars)
        I.clear()
        strats, meta = [], {}
        for lab, cfg in VAR:
            b = I.build(sym, tf, series.bars, cfg)
            c = I.register_variant(b, "@" + lab)
            for a in ("full", "wrong_order", "sweep_mss"):
                s = T.make_strategy(sym, tf, [I.get(a + "@" + lab)], group="ICT",
                                    name=f"{a}@{lab}", exit_model=EXITS["anchored"])
                strats.append(s)
                meta[s.strategy_id] = (a, lab, c[a])
        for w, (s0, s1) in {"full": (250, n), "IS": (250, int(n * .6)),
                            "OOS": (int(n * .6), n)}.items():
            res = run_portfolio(frame, strats, start=s0, end=s1)
            for sid, (a, lab, nsig) in meta.items():
                r = row(res[sid].trades)
                r.pop('rs')
                r.update(arm=a, variant=lab, window=w, symbol=sym, tf=tf, signals=nsig)
                out[f"{sym}|{tf}|{a}|{lab}|{w}"] = r
        print(sym, tf, 'done', flush=True)

json.dump(out, open('/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad/sens.json', 'w'), indent=1, default=str)

CELLS = [(s, t) for s in ['MES', 'MNQ', 'MGC', 'MCL'] for t in [60, 240]]
def sgn(v):
    v = [x for x in v if x != 0]; k = sum(1 for x in v if x > 0); n = len(v)
    return round((k - n / 2) / math.sqrt(n / 4), 2) if n else None
print(f"\n{'variant':10s}" + "".join(f"{w:>22s}" for w in ('full', 'IS', 'OOS')))
print(f"{'':10s}" + "".join(f"{'medExp':>8s}{'signz':>7s}{'trd':>7s}" for _ in range(3)))
for lab, _ in VAR:
    line = f"{lab:10s}"
    for w in ('full', 'IS', 'OOS'):
        rs = [out[f"{s}|{t}|full|{lab}|{w}"] for s, t in CELLS
              if out[f"{s}|{t}|full|{lab}|{w}"]['n'] >= 10]
        if not rs:
            line += f"{'-':>22s}"; continue
        e = [r['exp'] for r in rs]
        line += f"{st.median(e):>+8.3f}{str(sgn(e)):>7s}{sum(r['n'] for r in rs):>7d}"
    print(line)
