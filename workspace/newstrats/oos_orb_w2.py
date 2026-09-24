"""Out-of-sample for every headline claim: 60/40 temporal split + 3 disjoint slices."""
from __future__ import annotations

import json
import math
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/newstrats")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")

import orb_w2 as O  # noqa: E402
import toolkit as T  # noqa: E402

D = "/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad"
ROWS = json.load(open(f"{D}/orb_census.json"))
deep = [r for r in ROWS if r["source"] == "deep"]
raw = [r for r in ROWS if r["source"] == "raw"]
out = {}

# --------------------------------------------------------- 1. top configs
print("=" * 118)
print("TOP 12 DEEP CONFIGURATIONS BY IN-SAMPLE t, AND WHAT THEY DO OUT OF SAMPLE")
print("  free_t(1920 configs screened) = %.3f t-units are bought by search alone" % T.free_t(1920))
print("=" * 118)
hdr = ("sym tf OR entry stop tgt", "n", "win", "payoff", "exp", "maxdd", "t",
       "IS n", "IS exp", "OOS n", "OOS exp", "OOS win", "OOS pay", "slice exps")
print("{:<28}{:>5}{:>7}{:>8}{:>8}{:>8}{:>7}{:>6}{:>8}{:>6}{:>9}{:>8}{:>8}  {}".format(*hdr))
top = sorted(deep, key=lambda r: -r["t"])[:12]
for r in top:
    print("{:<28}{:>5}{:>7}{:>8}{:>8}{:>8}{:>7}{:>6}{:>8}{:>6}{:>9}{:>8}{:>8}  {}".format(
        f"{r['symbol']} {r['tf']}m OR{r['orlen']} {r['entry']} {r['stop']} {r['target']}",
        r["n"], r["win"], r["payoff"], r["exp"], r["maxdd"], r["t"],
        r["is_n"], r["is_exp"], r["oos_n"], r["oos_exp"], r["oos_win"], r["oos_payoff"],
        [x for x in r["sl_exp"]]))
out["top_deep_by_t"] = top

print()
print("IS->OOS DEGRADATION over all 720 deep configurations (60% train / 40% holdout, temporal)")
is_e = [r["is_exp"] for r in deep if r["is_exp"] is not None and r["is_n"] >= 20]
oo_e = [r["oos_exp"] for r in deep if r["is_exp"] is not None and r["is_n"] >= 20
        and r["oos_exp"] is not None]
pairs = [(r["is_exp"], r["oos_exp"]) for r in deep
         if r["is_exp"] is not None and r["oos_exp"] is not None
         and r["is_n"] >= 20 and r["oos_n"] >= 20]
pos_is = [p for p in pairs if p[0] > 0]
print(f"  configs with >=20 trades in both halves: {len(pairs)}")
print(f"  median IS expectancy  {st.median([p[0] for p in pairs]):+.4f}R")
print(f"  median OOS expectancy {st.median([p[1] for p in pairs]):+.4f}R")
print(f"  configs positive IS: {len(pos_is)}/{len(pairs)} = {len(pos_is)/len(pairs):.1%}")
print(f"  of those, still positive OOS: {sum(1 for p in pos_is if p[1] > 0)}/{len(pos_is)} = "
      f"{sum(1 for p in pos_is if p[1] > 0)/len(pos_is):.1%}  (chance = 50%)")


def _corr(a, b):
    ma, mb = st.mean(a), st.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** .5
    return num / den if den else 0.0


rho = _corr([p[0] for p in pairs], [p[1] for p in pairs])
print(f"  corr(IS exp, OOS exp) across configs = {rho:+.3f}   "
      f"(a real edge would be strongly positive; 0 means the ranking is noise)")
out["is_oos"] = dict(n_pairs=len(pairs),
                     median_is=round(st.median([p[0] for p in pairs]), 4),
                     median_oos=round(st.median([p[1] for p in pairs]), 4),
                     pct_pos_is=round(len(pos_is) / len(pairs), 4),
                     pct_pos_is_still_pos_oos=round(
                         sum(1 for p in pos_is if p[1] > 0) / len(pos_is), 4),
                     corr_is_oos=round(rho, 4))

# ------------------------------------------- 2. disjoint slices, floor-free
print()
print("=" * 118)
print("THREE DISJOINT PERIODS (deep, ~115 sessions each) - median expectancy over all 240 configs/symbol")
print("=" * 118)
print("{:<10}{:>12}{:>12}{:>12}{:>14}".format("symbol", "slice1 exp", "slice2 exp",
                                              "slice3 exp", "signs agree?"))
sl = {}
for sym in ["MGC", "MES", "MNQ"]:
    rr = [r for r in deep if r["symbol"] == sym]
    meds = []
    for k in range(3):
        v = [r["sl_exp"][k] for r in rr if r["sl_exp"][k] is not None and r["sl_n"][k] >= 20]
        meds.append(round(st.median(v), 4) if v else None)
    agree = len({(m > 0) for m in meds if m is not None}) == 1
    sl[sym] = meds
    print("{:<10}{:>12}{:>12}{:>12}{:>14}".format(sym, *[str(m) for m in meds],
                                                  "yes" if agree else "NO"))
out["disjoint_slices_deep"] = sl

# ------------------------------------- 3. do the paired results replicate OOS?
print()
print("=" * 118)
print("DO THE PAIRED ARM RESULTS REPLICATE OUT OF SAMPLE? (same test, computed on the 40% holdout)")
print("=" * 118)


def paired_oos(rows, dim, a, b, field_exp="oos_exp", field_n="oos_n", minn=15):
    idx = {}
    for r in rows:
        idx[(r["symbol"], r["tf"], r["orlen"], r["entry"], r["stop"], r["target"])] = r
    keys = ["symbol", "tf", "orlen", "entry", "stop", "target"]
    cells = defaultdict(list)
    for r in rows:
        if r[dim] != a or r[field_exp] is None or r[field_n] < minn:
            continue
        k = list((r["symbol"], r["tf"], r["orlen"], r["entry"], r["stop"], r["target"]))
        k[keys.index(dim)] = b
        o = idx.get(tuple(k))
        if o is None or o[field_exp] is None or o[field_n] < minn:
            continue
        cells[(r["symbol"], r["tf"])].append(r[field_exp] - o[field_exp])
    zs = [O.sign_test_z(v)[0] for v in cells.values() if v]
    allv = [x for v in cells.values() for x in v]
    return (O.stouffer(zs), len(zs), len(allv),
            round(st.median(allv), 4) if allv else None)


comps = [("stop", "opp", "mid"), ("stop", "opp", "atr"), ("stop", "mid", "atr"),
         ("entry", "break", "retest"), ("target", "r1", "r3"),
         ("target", "rw1", "rw2"), ("orlen", 15, 30), ("orlen", 30, 60)]
print("{:<26}{:>12}{:>12}{:>12}{:>12}{:>14}".format(
    "comparison", "IS Z", "IS medD", "OOS Z", "OOS medD", "sign holds?"))
rep = []
for dim, a, b in comps:
    zi, ci, pi, mi = paired_oos(deep, dim, a, b, "is_exp", "is_n")
    zo, co, po, mo = paired_oos(deep, dim, a, b, "oos_exp", "oos_n")
    holds = "yes" if (zi * zo > 0 and abs(zi) > 1 and abs(zo) > 1) else (
        "NO - flips" if zi * zo < 0 and min(abs(zi), abs(zo)) > 1 else "not resolved")
    rep.append(dict(dim=dim, a=a, b=b, is_z=round(zi, 3), is_med=mi,
                    oos_z=round(zo, 3), oos_med=mo, verdict=holds))
    print("{:<26}{:>12}{:>12}{:>12}{:>12}{:>14}".format(
        f"{dim}: {a} - {b}", round(zi, 2), mi, round(zo, 2), mo, holds))
out["paired_is_vs_oos"] = rep

json.dump(out, open(f"{D}/orb_oos.json", "w"), indent=1, default=str)
print("\nwrote orb_oos.json")
