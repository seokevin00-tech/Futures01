import sys, json
sys.path.insert(0, '/home/user/Futures01')
sys.path.insert(0, '/home/user/Futures01/workspace/studies')
sys.path.insert(0, '/home/user/Futures01/workspace/newstrats')
import toolkit as T, leadlag as L
from futures_agents.features import build_symbol_frame
from futures_agents.scout import FRAMES
from futures_agents.strategies.library import CONDITIONS

SYMS = ["MGC", "MES", "NQ", "MNQ", "MCL"]
NAMES = ["ltf_break_first", "ltf_break_first_fresh0", "ltf_break_first_fresh1",
         "ltf_break_first_fresh3", "ltf_break_first_fresh6",
         "htf_confirms_late0", "htf_confirms_late2", "htf_confirms_late4",
         "htf_confirms_late8", "htf_confirms_late16",
         "htf_broken_now", "ltf_and_htf_both_broken",
         "break_of_structure", "structure_trend", "mtf_aligned"]
TF = 60
res = {}
audit = {}
for sym in SYMS:
    audit[sym] = {tf: L._audit_causality(sym, tf, n_probe=10) for tf in (60, 240)}
    series = T._series(sym, TF, None)
    frame = build_symbol_frame(series, FRAMES[TF])
    n = len(frame)
    fires = {k: [] for k in NAMES}
    for i in range(200, n):
        snap = frame.snapshot(i)
        if snap is None:
            continue
        for k in NAMES:
            r = CONDITIONS[k].evaluate(snap, TF)
            fires[k].append(r.direction.value if r.triggered else "")
    m = len(fires[NAMES[0]])
    res[sym] = dict(bars=m, rate={k: round(sum(1 for x in v if x) / m, 4) for k, v in fires.items()})
    # identity check against the incumbent and against each other
    ident = {}
    for k in NAMES:
        for j in NAMES:
            if k >= j:
                continue
            same = sum(1 for a, b in zip(fires[k], fires[j]) if a == b) / m
            if same > 0.98:
                ident[f"{k}~{j}"] = round(same, 4)
    res[sym]["near_identical_pairs_gt98pct"] = ident
    res[sym]["vs_break_of_structure_agree"] = {
        k: round(sum(1 for a, b in zip(fires[k], fires["break_of_structure"]) if a == b) / m, 4)
        for k in NAMES if k != "break_of_structure"}
    print(sym, res[sym]["rate"], flush=True)

json.dump(dict(rates=res, causality_audit=audit),
          open('/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad/rates.json', 'w'),
          indent=1, default=str)
print("saved")
