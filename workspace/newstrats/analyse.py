import sys, json, statistics as st
from collections import defaultdict
sys.path.insert(0, '/home/user/Futures01')
sys.path.insert(0, '/home/user/Futures01/workspace/studies')
import toolkit as T

rows = json.load(open('/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad/bt.json'))
FLOOR = int(sys.argv[1]) if len(sys.argv) > 1 else 0

def live(r):
    return r["n"] is not None and r["n"] >= max(1, FLOOR) and r["exp"] is not None

arms = sorted({r["arm"] for r in rows})
cells = sorted({(r["symbol"], r["cell"], r["tf"]) for r in rows})

print(f"=== per-arm census, floor={FLOOR} ===")
print(f"{'arm':28} {'nstrat':>6} {'trades':>7} {'medExp':>8} {'medWin':>7} {'medPF':>6} {'medT':>7} {'%prof':>6} {'medN':>5} {'medDD':>7} {'hold_m':>7}")
summ = {}
for a in arms:
    v = [r for r in rows if r["arm"] == a and live(r)]
    if not v:
        print(f"{a:28} EMPTY"); continue
    summ[a] = dict(
        n_strategies=len(v), total_trades=sum(r["n"] for r in v),
        median_exp=round(st.median([r["exp"] for r in v]), 4),
        median_win=round(st.median([r["win"] for r in v]), 4),
        median_pf=round(st.median([r["pf"] for r in v]), 3),
        median_t=round(st.median([r["t"] for r in v]), 3),
        pct_profitable=round(sum(1 for r in v if r["exp"] > 0) / len(v), 3),
        median_n=round(st.median([r["n"] for r in v]), 1),
        median_maxdd=round(st.median([r["maxdd"] for r in v]), 2),
        median_hold_min=round(st.median([r["hold_min"] for r in v if r["hold_min"]]), 1),
        median_rr=round(st.median([r["rr"] for r in v]), 3))
    s = summ[a]
    print(f"{a:28} {s['n_strategies']:6} {s['total_trades']:7} {s['median_exp']:8.4f} "
          f"{s['median_win']:7.3f} {s['median_pf']:6.2f} {s['median_t']:7.3f} "
          f"{s['pct_profitable']:6.2f} {s['median_n']:5.0f} {s['median_maxdd']:7.2f} {s['median_hold_min']:7.0f}")

def cell_compare(arm_a, arm_b, subset=None):
    """Per-cell paired comparison; combine with a sign test + Stouffer."""
    zs, signs, det = [], [], []
    for c in cells:
        if subset and c[1] not in subset:
            continue
        A = [r["exp"] for r in rows if r["arm"] == arm_a and live(r) and (r["symbol"], r["cell"], r["tf"]) == c]
        B = [r["exp"] for r in rows if r["arm"] == arm_b and live(r) and (r["symbol"], r["cell"], r["tf"]) == c]
        if len(A) < 5 or len(B) < 5:
            continue
        u = T.mann_whitney_u(A, B)
        zs.append(u["z"]); signs.append(1 if st.median(A) > st.median(B) else -1)
        det.append(dict(cell=f"{c[0]}/{c[1]}", z=u["z"], n_a=len(A), n_b=len(B),
                        med_a=round(st.median(A), 4), med_b=round(st.median(B), 4)))
    if not zs:
        return None
    stouffer = sum(zs) / (len(zs) ** 0.5)
    return dict(arm_a=arm_a, arm_b=arm_b, cells=len(zs),
                stouffer_z=round(stouffer, 3),
                mean_cell_z=round(st.mean(zs), 3),
                wins_a=sum(1 for s in signs if s > 0), wins_b=sum(1 for s in signs if s < 0),
                detail=det)

print(f"\n=== paired arm comparisons (per-cell z, Stouffer combined), floor={FLOOR} ===")
PAIRS = [("EARLY_ltf_break_first", "CONFIRM_late0"),
         ("EARLY_ltf_break_first", "INCUMBENT_bos_ltf"),
         ("EARLY_ltf_break_first", "CONFIRM_htf_broken_now"),
         ("EARLY_ltf_break_first", "ALIGNED_both_broken"),
         ("EARLY_fresh1", "CONFIRM_late0"),
         ("EARLY_fresh3", "CONFIRM_late0"),
         ("CONFIRM_late0", "INCUMBENT_bos_ltf"),
         ("CONFIRM_late4", "CONFIRM_late0"),
         ("CONFIRM_late8", "CONFIRM_late0"),
         ("ALIGNED_both_broken", "INCUMBENT_bos_ltf"),
         ("INCUMBENT_bos_htf_bound", "INCUMBENT_bos_ltf")]
out_pairs = {}
for a, b in PAIRS:
    for tag, sub in [("ALL", None), ("IS_slice0", {"slice0"}), ("OOS_slice1_2", {"slice1", "slice2"})]:
        r = cell_compare(a, b, sub)
        if not r: continue
        out_pairs[f"{a}__vs__{b}__{tag}"] = r
        print(f"{a:26} vs {b:24} {tag:13} cells={r['cells']:2} stouffer_z={r['stouffer_z']:+7.3f} "
              f"mean_z={r['mean_cell_z']:+6.3f} wins {r['wins_a']}-{r['wins_b']}")

print(f"\n=== 240m incumbent, per slice (the untested thread) ===")
for sym in ["MGC", "MES", "NQ", "MNQ", "MCL"]:
    line = [sym]
    for sl in ["slice0", "slice1", "slice2"]:
        v = [r for r in rows if r["arm"] == "INCUMBENT240_bos" and r["cell"] == sl
             and r["symbol"] == sym and live(r)]
        if not v:
            line.append(f"{sl}: none"); continue
        line.append(f"{sl}: n={len(v)} medExp={st.median([r['exp'] for r in v]):+.4f} "
                    f"medN={st.median([r['n'] for r in v]):.0f} %prof={sum(1 for r in v if r['exp']>0)/len(v):.2f}")
    print("  " + " | ".join(line))

json.dump(dict(floor=FLOOR, arm_summary=summ, pairs=out_pairs),
          open(f'/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad/analysis_f{FLOOR}.json', 'w'),
          indent=1, default=str)
