import sys, json, statistics as st
sys.path.insert(0, '/home/user/Futures01'); sys.path.insert(0, '/home/user/Futures01/workspace/studies')
import toolkit as T
SC = '/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad'
wf = json.load(open(f'{SC}/wf.json')); s240 = json.load(open(f'{SC}/s240.json'))
SYMS = ["MGC", "MES", "NQ", "MNQ", "MCL"]
F = 20
def ok(r): return r["n"] and r["n"] >= F and r["exp"] is not None

def cmp_blocks(rows, a, b, cells):
    res = {}
    for c in cells:
        A = [r["exp"] for r in rows if r["arm"] == a and ok(r) and (r["symbol"], r["cell"]) == c]
        B = [r["exp"] for r in rows if r["arm"] == b and ok(r) and (r["symbol"], r["cell"]) == c]
        if len(A) < 5 or len(B) < 5: continue
        u = T.mann_whitney_u(A, B)
        res[f"{c[0]}/{c[1]}"] = dict(z=u["z"], med_a=round(st.median(A),4), med_b=round(st.median(B),4), n_a=len(A), n_b=len(B))
    zs = [v["z"] for v in res.values()]
    return dict(cells=len(zs), stouffer=round(sum(zs)/len(zs)**0.5,3) if zs else None,
                pos=sum(1 for z in zs if z>0), neg=sum(1 for z in zs if z<0), detail=res)

blocks = sorted({(r["symbol"], r["cell"]) for r in wf})
print("=== 6-block walk-forward, 60m, floor 20 (blk0 oldest -> blk5 newest) ===")
WF = {}
for a,b in [("EARLY_ltf_break_first","INCUMBENT_bos_ltf"),
            ("EARLY_ltf_break_first","ALIGNED_both_broken"),
            ("EARLY_ltf_break_first","CONFIRM_htf_broken_now"),
            ("EARLY_fresh3","CONFIRM_late0"),
            ("EARLY_fresh3","INCUMBENT_bos_ltf"),
            ("CONFIRM_late0","INCUMBENT_bos_ltf")]:
    r = cmp_blocks(wf, a, b, blocks); WF[f"{a}__vs__{b}"] = r
    perblk = {}
    for k,v in r["detail"].items():
        perblk.setdefault(k.split("/")[1], []).append(v["z"])
    line = "  ".join(f"{bl}:{sum(1 for z in zz if z>0)}+/{sum(1 for z in zz if z<0)}-" for bl,zz in sorted(perblk.items()))
    print(f"{a:24} vs {b:24} cells={r['cells']:2} stouffer={r['stouffer']:+7.3f} {r['pos']}+/{r['neg']}-   {line}")
    WF[f"{a}__vs__{b}"]["per_block_sign"] = {bl:[sum(1 for z in zz if z>0), sum(1 for z in zz if z<0)] for bl,zz in sorted(perblk.items())}

print("\n=== 240m structure-signal shootout, disjoint slices, floor 20 ===")
cells240 = sorted({(r["symbol"], r["cell"]) for r in s240})
SH = {}
for other in ["structure_trend","pullback_to_support","fvg_nearby","range_position_extreme"]:
    for tag, sub in [("ALL",None),("IS_slice0",{"slice0"}),("OOS_slice1_2",{"slice1","slice2"})]:
        cs = [c for c in cells240 if sub is None or c[1] in sub]
        r = cmp_blocks(s240, "S240_break_of_structure", f"S240_{other}", cs)
        if not r["cells"]: continue
        SH[f"bos_vs_{other}_{tag}"] = r
        print(f"  bos vs {other:22} {tag:13} cells={r['cells']:2} stouffer={r['stouffer']:+7.3f} {r['pos']}+/{r['neg']}-")

print("\n=== 240m per-arm census, floor 20 ===")
C240 = {}
for a in sorted({r["arm"] for r in s240}):
    v = [r for r in s240 if r["arm"]==a and ok(r)]
    if not v: print(f"  {a:34} EMPTY"); continue
    C240[a] = dict(n_strategies=len(v), trades=sum(r["n"] for r in v),
                   median_exp=round(st.median([r["exp"] for r in v]),4),
                   median_win=round(st.median([r["win"] for r in v]),4),
                   median_pf=round(st.median([r["pf"] for r in v]),3),
                   median_t=round(st.median([r["t"] for r in v]),3),
                   pct_profitable=round(sum(1 for r in v if r["exp"]>0)/len(v),3),
                   median_n=round(st.median([r["n"] for r in v]),1),
                   median_maxdd=round(st.median([r["maxdd"] for r in v]),2))
    s=C240[a]; print(f"  {a:34} nstrat={s['n_strategies']:3} medExp={s['median_exp']:+.4f} medWin={s['median_win']:.3f} medPF={s['median_pf']:.2f} %prof={s['pct_profitable']:.2f} medN={s['median_n']:.0f}")

print("\n=== 240m bos per slice per symbol (IS vs OOS) ===")
BS = {}
for sym in SYMS:
    row = {}
    for sl in ["slice0","slice1","slice2"]:
        v=[r for r in s240 if r["arm"]=="S240_break_of_structure" and r["symbol"]==sym and r["cell"]==sl and ok(r)]
        row[sl] = dict(n=len(v), med_exp=round(st.median([r["exp"] for r in v]),4) if v else None,
                       pct_prof=round(sum(1 for r in v if r["exp"]>0)/len(v),2) if v else None,
                       med_trades=round(st.median([r["n"] for r in v]),0) if v else None)
    BS[sym]=row
    print(f"  {sym:5}", {k:(v['med_exp'],v['pct_prof']) for k,v in row.items()})

json.dump(dict(walk_forward=WF, shootout240=SH, census240=C240, bos240_per_slice=BS),
          open(f'{SC}/wf_analysis.json','w'), indent=1, default=str)
