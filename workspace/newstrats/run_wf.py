"""Walk-forward (6 sequential disjoint blocks) + the 240m structure-signal shootout."""
import sys, json
sys.path.insert(0, '/home/user/Futures01')
sys.path.insert(0, '/home/user/Futures01/workspace/studies')
sys.path.insert(0, '/home/user/Futures01/workspace/newstrats')
import toolkit as T, leadlag as L
from run_bt import ARMS, PARTNERS, build, rows_for
from futures_agents.strategies.library import get_condition

SYMS = ["MGC", "MES", "NQ", "MNQ", "MCL"]
WF_ARMS = ["EARLY_ltf_break_first", "EARLY_fresh3", "CONFIRM_late0",
           "CONFIRM_htf_broken_now", "ALIGNED_both_broken", "INCUMBENT_bos_ltf"]
SC = '/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad'

# ---- A. 6-block walk-forward on 60m ------------------------------------
wf = []
for sym in SYMS:
    for bi, (a, b) in enumerate(T.disjoint_slices(sym, 60, n=6)):
        series = T.slice_series(sym, 60, a, b)
        if len(series) < 350:
            print("skip", sym, bi, len(series)); continue
        strats = [s for arm in WF_ARMS for s in build(sym, 60, arm)]
        r = rows_for(sym, 60, series, strats, f"blk{bi}")
        wf += r
        print("WF", sym, bi, len(series), flush=True)
json.dump(wf, open(f'{SC}/wf.json', 'w'), indent=0, default=str)
print("wf saved", len(wf))

# ---- B. 240m structure-signal shootout, disjoint slices ----------------
S240 = ["break_of_structure", "structure_trend", "pullback_to_support",
        "fvg_nearby", "range_position_extreme"]
sh = []
for sym in SYMS:
    for si, (a, b) in enumerate(T.disjoint_slices(sym, 240, n=3)):
        s240 = T.slice_series(sym, 240, a, b)
        if len(s240) < 150:
            continue
        strats = []
        for sig in S240:
            strats.append(T.make_strategy(sym, 240, [get_condition(sig)],
                                          group=f"S240_{sig}", name=f"S240_{sig}__bare"))
            for p in PARTNERS:
                strats.append(T.make_strategy(sym, 240,
                                              [get_condition(sig), get_condition(p)],
                                              group=f"S240_{sig}", name=f"S240_{sig}__{p}"))
        sh += rows_for(sym, 240, s240, strats, f"slice{si}")
        print("S240", sym, si, len(s240), flush=True)
json.dump(sh, open(f'{SC}/s240.json', 'w'), indent=0, default=str)
print("s240 saved", len(sh))
