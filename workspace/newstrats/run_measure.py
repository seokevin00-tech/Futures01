import sys, json
sys.path.insert(0, '/home/user/Futures01')
sys.path.insert(0, '/home/user/Futures01/workspace/studies'); sys.path.insert(0, '/home/user/Futures01/workspace/newstrats')
import toolkit as T
import leadlag_measure as M
from futures_agents.data.bars import BarSeries
from futures_agents.features import build_symbol_frame

SYMS = ["MGC", "MES", "NQ", "MNQ", "MCL"]
OUT = {}

def frames_for(sym, base_tf, tfs, lo=None, hi=None):
    s = T._series(sym, base_tf, None)
    bars = s.bars
    if lo is not None:
        n = len(bars)
        bars = bars[int(n*lo):int(n*hi)]
    return build_symbol_frame(BarSeries(sym, base_tf, bars), tfs)

for sym in SYMS:
    OUT[sym] = {}
    for base, (l, h) in [(60, (60, 240))]:
        f = frames_for(sym, base, [l, h])
        for defn in ("break", "swings"):
            r = M.lead_lag(f.frames[l], f.frames[h], l, h, defn=defn)
            r.pop("events")
            OUT[sym][f"{l}->{h}_{defn}"] = r
        # temporal stability of the lead itself: first 60% vs last 40%
        for tag, (a, b) in [("IS_first60", (0.0, 0.6)), ("OOS_last40", (0.6, 1.0))]:
            fs = frames_for(sym, base, [l, h], a, b)
            r = M.lead_lag(fs.frames[l], fs.frames[h], l, h, defn="break")
            r.pop("events")
            OUT[sym][f"{l}->{h}_break_{tag}"] = r
    # secondary pair on the 15m base (58 days only - measurement, not strategy)
    try:
        f15 = frames_for(sym, 15, [15, 60])
        r = M.lead_lag(f15.frames[15], f15.frames[60], 15, 60, defn="break")
        r.pop("events")
        OUT[sym]["15->60_break"] = r
    except Exception as e:
        OUT[sym]["15->60_break"] = {"error": f"{type(e).__name__}: {e}"}
    print(sym, "done", flush=True)

json.dump(OUT, open("/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad/measure.json", "w"), indent=1, default=str)
print("saved")
