"""Floor-free census of the ICT sequence.  Counts first, performance later.

Nothing here applies a trade floor, an exit model or a profitability test.  The only question
is how often each stage of the chain actually happens - because if the full sequence occurs
eight times in ten months, no backtest of it can mean anything, and that is the finding.
"""
import json, os, sys
os.chdir('/home/user/Futures01')
sys.path[:0] = ['/home/user/Futures01', 'workspace/studies', 'workspace/newstrats']
import toolkit as T, ict_sweep as I

SYMS = ['MES', 'MNQ', 'MGC', 'MCL']
DUP = ['NQ', 'ES']                       # same index complex as MNQ/MES - NOT independent
TFS = [5, 15, 30, 60, 240, 1440]

ALL8 = ("PDH", "PDL", "ONH", "ONL", "SESH", "SESL", "SWH", "SWL")
OBVIOUS = ("PDH", "PDL", "ONH", "ONL")
SWINGS = ("SWH", "SWL")

CFGS = [I.Cfg(label='base', pools=ALL8),
        I.Cfg(label='obvious', pools=OBVIOUS),
        I.Cfg(label='swingonly', pools=SWINGS),
        I.Cfg(label='nofvg', pools=ALL8, require_fvg=False)]
CFGS += [I.Cfg(label=f'gap{g}', pools=ALL8, max_gap=g) for g in (1, 2, 3, 8, 12, 20)]
CFGS += [I.Cfg(label='pen0.25', pools=ALL8, min_pen_atr=0.25),
         I.Cfg(label='reclaim2', pools=ALL8, reclaim_bars=2),
         I.Cfg(label='retr5', pools=ALL8, retrace_window=5),
         I.Cfg(label='retr20', pools=ALL8, retrace_window=20)]

out, pools = {}, {}
for sym in SYMS + DUP:
    for tf in TFS:
        try:
            series = T._series(sym, tf, None)
        except Exception:
            continue
        if len(series) < 200:
            continue
        span = (series.bars[-1].ts - series.bars[0].ts).days
        for cfg in CFGS:
            b = I.build(sym, tf, series.bars, cfg)
            c = dict(b.census)
            c.update(symbol=sym, tf=tf, cfg=cfg.label, span_days=span,
                     dup=sym in DUP)
            out[f"{sym}|{tf}|{cfg.label}"] = c
        # single-pool firing rates, for comparison with the library's measured numbers
        for p in ALL8:
            b = I.build(sym, tf, series.bars, I.Cfg(label=p, pools=(p,)))
            pools[f"{sym}|{tf}|{p}"] = {
                "sweeps": b.census['s1_sweeps'], "bars": b.census['bars'],
                "rate": b.census['rate_s1'], "mss": b.census['s2_sweep_then_mss'],
                "entries": b.census['s4_retraced_entry']}
        print(sym, tf, 'done', flush=True)

json.dump({"chain": out, "pools": pools},
          open('/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad/census.json', 'w'), indent=1, default=str)
print('cells', len(out))
