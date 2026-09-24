"""Compact console summary of every cell, for the written report."""
from __future__ import annotations

import glob
import json
import os
import statistics as st
import sys

sys.path.insert(0, "/home/user/Futures01")
os.chdir("/home/user/Futures01")

C = "workspace/strategy_research/cells"
SYMS = ["NQ", "ES", "MZC", "MZS", "MZW"]
GR = {"MZC", "MZS", "MZW"}


def cells():
    out = {}
    for f in sorted(glob.glob(f"{C}/rank_nq_es_grains_*.json")):
        if "replication" in f or "rth_arm" in f:
            continue
        c = json.load(open(f))
        out[(c["symbol"], c["tf"], c["window"])] = c
    return out


def main():
    cs = cells()
    print("== PLACEBO RANK PER CELL (rank / null expectation) ==")
    print(f"{'sym':5s}{'tf':5s}" + "".join(f"{w:>22d}" for w in (30, 90, 180, 274)))
    for sym in SYMS:
        for tf in (60, 240):
            row = f"{sym:5s}{tf:<5d}"
            for w in (30, 90, 180, 274):
                c = cs.get((sym, tf, w))
                if not c or c.get("skipped"):
                    row += f"{'  -- no data --':>22s}"
                    continue
                p, s = c["primary"], c["secondary"]
                row += (f"{str(p['best_placebo_rank']):>4s}/"
                        f"{str(p['null_rank'].get('expected_best_rank')):<5s}"
                        f"[{p['n_total']:>3d}]"
                        f" f10:{str(s['best_placebo_rank']):>3s}/"
                        f"{str(s['null_rank'].get('expected_best_rank')):<4s}")
            print(row)

    print("\n== CENSUS: floor-free vs floored ==")
    print(f"{'cell':18s}{'gen':>6s}{'f1':>6s}{'medE1':>8s}{'%pos1':>7s}"
          f"{'f20':>5s}{'medE20':>8s}{'%pos20':>7s}{'bestT':>7s}{'free_t':>7s}")
    for sym in SYMS:
        for tf in (60, 240):
            for w in (30, 90, 180, 274):
                c = cs.get((sym, tf, w))
                if not c or c.get("skipped"):
                    continue
                cen = c["census"]
                print(f"{sym+' '+str(tf)+'m '+str(w)+'d':18s}{c['generated']:>6d}"
                      f"{cen['1']['collapsed']:>6d}{str(cen['1']['median_exp']):>8s}"
                      f"{str(cen['1']['pct_pos']):>7s}{cen['20']['collapsed']:>5d}"
                      f"{str(cen['20']['median_exp']):>8s}{str(cen['20']['pct_pos']):>7s}"
                      f"{str(cen['20']['best_t']):>7s}{c['primary']['free_t']:>7.2f}")

    print("\n== TOP 10 BY WINDOW (floor 20; * = placebo) ==")
    for sym in SYMS:
        for w in (30, 90, 180, 274):
            for tf in (60, 240):
                c = cs.get((sym, tf, w))
                if not c or c.get("skipped"):
                    continue
                rows = c["primary"]["rows"][:10]
                if not rows:
                    print(f"{sym} {tf}m {w}d: EMPTY at floor 20 "
                          f"(floor 10 has {c['secondary']['n_real']} real rows)")
                    continue
                print(f"{sym} {tf}m {w}d  free_t={c['primary']['free_t']:.2f} "
                      f"N={c['primary']['n_total']}")
                for r in rows:
                    mark = "*" if r["arm"] != "real" else " "
                    print(f"  {mark}{r['rank']:>3d} {r['group'][:12]:12s} n={r['n']:>3d} "
                          f"exp={r['exp']:+.3f} win={r['win']:.2f}"
                          f"[{r['win_lo']:.2f}-{r['win_hi']:.2f}] rr={r['rr']:.2f} "
                          f"pf={r['pf']:.2f} dd={r['maxdd']:.1f} t={r['t']:+.2f} "
                          f"cl={r['maxcl']} {r['name'][:46]}")

    print("\n== REPLICATION (disjoint thirds) ==")
    tot20 = tot20n = tot10 = tot10n = 0
    for f in sorted(glob.glob(f"{C}/replication_*.json")):
        sym = os.path.basename(f).split("_")[1].split(".")[0]
        r = json.load(open(f))
        for tf, blk in r.items():
            if "summary" not in blk:
                continue
            s = blk["summary"]

            def tal(ids):
                g = [s[i] for i in ids if i in s]
                p3 = sum(1 for x in g if x["slices_run"] == 3 and x["slices_positive"] == 3)
                return p3, len(g)
            a, an = tal(blk.get("ranked_ids_f20", []))
            b, bn = tal(blk.get("ranked_ids_f10", []))
            tot20 += a; tot20n += an; tot10 += b; tot10n += bn
            print(f"{sym} {tf}m {blk['slices']}  f20 top10 pos-in-all-3: {a}/{an}   "
                  f"f10: {b}/{bn}")
    print(f"TOTAL f20 {tot20}/{tot20n}  f10 {tot10}/{tot10n}   "
          f"(chance if each slice were a coin flip: {0.125*tot20n:.1f} and "
          f"{0.125*tot10n:.1f})")

    print("\n== DEFLATION ==")
    tot = 0
    for k, c in sorted(cs.items()):
        if c.get("skipped"):
            continue
        n = c["primary"]["n_clearing_free_t"] + c["secondary"]["n_clearing_free_t"]
        tot += n
        if n:
            print(f"  {k} clears free_t: {n}")
    print(f"  rows clearing free_t across all cells and both floors: {tot}")
    best_t = max((c["census"]["20"]["best_t"] or -9) for c in cs.values()
                 if not c.get("skipped"))
    print(f"  highest t anywhere at floor 20: {best_t}; lowest free_t: "
          f"{min(c['primary']['free_t'] for c in cs.values() if not c.get('skipped')):.2f}")

    print("\n== GRAINS vs INDEX (per-cell census statistics, never pooled) ==")
    for lbl, sel in (("grains", lambda s: s in GR), ("index", lambda s: s not in GR)):
        rows = [c for (s, tf, w), c in cs.items() if sel(s) and not c.get("skipped")]
        m1 = [c["census"]["1"]["median_exp"] for c in rows if c["census"]["1"]["median_exp"] is not None]
        p20 = [c["census"]["20"]["pct_pos"] for c in rows if c["census"]["20"]["pct_pos"] is not None]
        dens = [1000 * c["census"]["20"]["collapsed"] / c["generated"] for c in rows]
        plb = [(c["primary"]["best_placebo_rank"], c["primary"]["null_rank"].get("expected_best_rank"))
               for c in rows if c["primary"]["best_placebo_rank"]]
        beat = sum(1 for a, b in plb if b and a < b)
        print(f"  {lbl}: cells={len(rows)} median(medianExp floor-free)="
              f"{st.median(m1):+.4f} median(%profitable at f20)={st.median(p20):.3f} "
              f"median(qualifying per 1000 generated)={st.median(dens):.1f} "
              f"placebo beat null in {beat}/{len(plb)} cells")


if __name__ == "__main__":
    main()
