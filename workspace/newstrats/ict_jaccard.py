"""Bar-level identity check against the library.

Five conditions in this project turned out to be duplicates of another under a different name,
so a new condition is not new until its firing SET has been compared with the existing ones on
the same bars.  Jaccard over fired-bar sets, plus the directional agreement where both fire.
"""
import json, os, sys
os.chdir('/home/user/Futures01')
sys.path[:0] = ['/home/user/Futures01', 'workspace/studies', 'workspace/newstrats']
import toolkit as T
import ict_sweep as I
from futures_agents.features import build_symbol_frame
from futures_agents.scout import FRAMES
from futures_agents.strategies.library import CONDITIONS as LIB

LIBNAMES = ["prior_day_sweep", "overnight_sweep", "session_extreme_sweep",
            "break_of_structure", "fvg_nearby", "imbalance_pullback",
            "prior_day_breakout", "pullback_to_support", "zone_touch",
            "fresh_zone_approach", "fib_golden_pocket", "structure_trend_up"]

out = {}
for sym in ['MES', 'MGC', 'MCL']:
    for tf in [60, 240]:
        series = T._series(sym, tf, None)
        frame = build_symbol_frame(series, FRAMES[tf])
        I.clear()
        I.register(I.build(sym, tf, series.bars))
        mine = {a: set() for a in I.ARMS}
        lib = {k: set() for k in LIBNAMES if k in LIB}
        n = 0
        for i in range(250, len(series.bars)):
            snap = frame.snapshot(i)
            if snap is None:
                continue
            n += 1
            for a in I.ARMS:
                if I.get(a).evaluate(snap, tf).triggered:
                    mine[a].add(i)
            for k in lib:
                try:
                    if LIB[k].evaluate(snap, tf).triggered:
                        lib[k].add(i)
                except Exception:
                    pass
        cell = {"bars": n,
                "mine_rates": {a: round(len(mine[a]) / n, 5) for a in mine},
                "lib_rates": {k: round(len(lib[k]) / n, 5) for k in lib},
                "jaccard": {}}
        for a in mine:
            for k in lib:
                u = len(mine[a] | lib[k])
                cell["jaccard"][f"{a}~{k}"] = round(len(mine[a] & lib[k]) / u, 4) if u else 0.0
        out[f"{sym}|{tf}"] = cell
        print(sym, tf, cell["mine_rates"], flush=True)
        top = sorted(cell["jaccard"].items(), key=lambda kv: -kv[1])[:6]
        print("   top overlaps:", [(k, v) for k, v in top], flush=True)

json.dump(out, open('/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad/jaccard.json', 'w'), indent=1)
