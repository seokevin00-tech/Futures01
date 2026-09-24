"""Analysis of the worker-2 ORB census: joint distribution, paired arms, OOS."""
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
from futures_agents.config import get_contract  # noqa: E402

D = "/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad"
ROWS = json.load(open(f"{D}/orb_census.json"))


def self_resolvable(r):
    """Worker-1's rule: can the SIGNAL timeframe alone tile [0, L) from the open?"""
    tf, L, sym = r["tf"], r["orlen"], r["symbol"]
    if tf > L or L % tf:
        return False
    h, m = get_contract(sym).rth_open.split(":")
    return (int(h) * 60 + int(m)) % tf == 0


for r in ROWS:
    r["selfres"] = self_resolvable(r)


def q(v, p):
    v = sorted(v)
    if not v:
        return float("nan")
    k = (len(v) - 1) * p
    lo, hi = int(math.floor(k)), int(math.ceil(k))
    return v[lo] if lo == hi else v[lo] + (v[hi] - v[lo]) * (k - lo)


def dist(rows, label):
    if not rows:
        return None
    w = [r["win"] for r in rows]
    p = [r["payoff"] for r in rows if r["payoff"] != float("inf")]
    e = [r["exp"] for r in rows]
    dd = [r["maxdd"] for r in rows]
    n = [r["n"] for r in rows]
    return dict(
        label=label, configs=len(rows), median_n=round(st.median(n), 1),
        win_p10=round(q(w, .1), 3), win_med=round(st.median(w), 4), win_p90=round(q(w, .9), 3),
        payoff_p10=round(q(p, .1), 3), payoff_med=round(st.median(p), 4), payoff_p90=round(q(p, .9), 3),
        exp_p10=round(q(e, .1), 4), exp_med=round(st.median(e), 4), exp_p90=round(q(e, .9), 4),
        maxdd_med=round(st.median(dd), 2), maxdd_p90=round(q(dd, .9), 2),
        pct_exp_pos=round(sum(1 for x in e if x > 0) / len(e), 3),
        pct_t_gt_2=round(sum(1 for r in rows if r["t"] > 2) / len(rows), 3),
        best_t=round(max(r["t"] for r in rows), 3),
        corr_win_payoff=round(O.__dict__.get("_c", lambda *a: 0)(w, p) if False else
                              _corr(w, p), 3),
    )


def _corr(a, b):
    if len(a) < 3:
        return 0.0
    ma, mb = st.mean(a), st.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** .5
    return num / den if den else 0.0


# ------------------------------------------------- paired arms (defect D28)
def paired(rows, dim, a, b, cellkey=("symbol", "tf")):
    """Per-cell paired sign test on delta-expectancy, combined by Stouffer.

    Arms are matched on EVERY other dimension, so each pair is the same rule
    set on the same bars with one thing swapped. This is deliberately NOT
    T.ab: a rank-sum over variants of the same rule sets inflates |z| ~3.3x
    (defect D28).
    """
    idx = {}
    for r in rows:
        k = (r["symbol"], r["source"], r["tf"], r["orlen"], r["entry"], r["stop"], r["target"])
        idx[k] = r
    cells = defaultdict(list)
    for r in rows:
        if r[dim] != a:
            continue
        k = list((r["symbol"], r["source"], r["tf"], r["orlen"], r["entry"], r["stop"], r["target"]))
        pos = ["symbol", "source", "tf", "orlen", "entry", "stop", "target"].index(dim)
        k[pos] = b
        other = idx.get(tuple(k))
        if other is None:
            continue
        cells[tuple(r[c] for c in cellkey)].append(
            (r["exp"] - other["exp"], r["win"] - other["win"],
             r["payoff"] - other["payoff"] if float("inf") not in
             (r["payoff"], other["payoff"]) else 0.0))
    per_cell, zs = [], []
    for c, ds in sorted(cells.items(), key=str):
        z, pos, neg = O.sign_test_z([d[0] for d in ds])
        per_cell.append(dict(cell=list(c), pairs=len(ds), pos=pos, neg=neg, z=round(z, 3),
                             med_dexp=round(st.median([d[0] for d in ds]), 4),
                             med_dwin=round(st.median([d[1] for d in ds]), 4),
                             med_dpayoff=round(st.median([d[2] for d in ds]), 4)))
        zs.append(z)
    Z = O.stouffer(zs)
    allds = [d[0] for ds in cells.values() for d in ds]
    return dict(dim=dim, arm_a=a, arm_b=b, cells=len(zs), pairs=len(allds),
                stouffer_z=round(Z, 3), p=round(O.norm_p(Z), 5),
                median_dexp=round(st.median(allds), 4) if allds else None,
                median_dwin=round(st.median([d[1] for ds in cells.values() for d in ds]), 4),
                median_dpayoff=round(st.median([d[2] for ds in cells.values() for d in ds]), 4),
                per_cell=per_cell)


if __name__ == "__main__":
    deep = [r for r in ROWS if r["source"] == "deep"]
    raw = [r for r in ROWS if r["source"] == "raw"]
    out = {}

    print("=" * 100)
    print("JOINT DISTRIBUTION of (win rate, payoff, expectancy, maxDD) over ORB configurations")
    print("=" * 100)
    hdr = ("scope", "cfg", "medN", "win10", "winMED", "win90", "pay10", "payMED",
           "pay90", "exp10", "expMED", "exp90", "ddMED", "%exp>0", "%t>2", "bestT", "r(win,pay)")
    print(("{:<26}" + "{:>8}" * 16).format(*hdr))

    def line(d):
        print(("{:<26}" + "{:>8}" * 16).format(
            d["label"], d["configs"], d["median_n"], d["win_p10"], d["win_med"], d["win_p90"],
            d["payoff_p10"], d["payoff_med"], d["payoff_p90"], d["exp_p10"], d["exp_med"],
            d["exp_p90"], d["maxdd_med"], d["pct_exp_pos"], d["pct_t_gt_2"], d["best_t"],
            d["corr_win_payoff"]))

    scopes = []
    for sym in ["MGC", "MES", "MNQ"]:
        scopes.append((f"deep {sym}", [r for r in deep if r["symbol"] == sym]))
    scopes.append(("deep ALL", deep))
    scopes.append(("deep ALL self-resolvable", [r for r in deep if r["selfres"]]))
    for sym in ["MGC", "MES", "NQ", "MNQ", "MCL"]:
        scopes.append((f"raw  {sym}", [r for r in raw if r["symbol"] == sym]))
    scopes.append(("raw  ALL", raw))
    ds = []
    for lab, rr in scopes:
        d = dist(rr, lab)
        if d:
            line(d)
            ds.append(d)
    out["joint_distribution"] = ds

    print()
    print("=" * 100)
    print("BY EXIT DIMENSION (deep, 346-352 sessions/symbol) - median over configs")
    print("=" * 100)
    for dim in ["stop", "target", "entry", "orlen", "tf"]:
        print(f"-- {dim}")
        vals = sorted({r[dim] for r in deep}, key=str)
        for v in vals:
            rr = [r for r in deep if r[dim] == v]
            d = dist(rr, f"  {dim}={v}")
            line(d)
            out.setdefault("by_dimension", {}).setdefault(dim, []).append(d)

    print()
    print("=" * 100)
    print("PAIRED PER-CELL SIGN TESTS ON dEXPECTANCY, STOUFFER-COMBINED (defect D28: never T.ab)")
    print("  cells = (symbol, tf); every other dimension matched exactly")
    print("=" * 100)
    comps = [("stop", "opp", "mid"), ("stop", "opp", "atr"), ("stop", "mid", "atr"),
             ("entry", "break", "retest"),
             ("target", "rw1", "r1"), ("target", "rw2", "r2"), ("target", "r1", "r3"),
             ("target", "rw1", "rw2"), ("tf", 5, 15),
             ("orlen", 5, 60), ("orlen", 15, 30), ("orlen", 30, 60)]
    res = []
    print("{:<28}{:>7}{:>8}{:>11}{:>10}{:>11}{:>12}{:>13}".format(
        "comparison", "cells", "pairs", "stouffZ", "p", "med dExp", "med dWin", "med dPayoff"))
    for dim, a, b in comps:
        r = paired(deep, dim, a, b)
        res.append(r)
        print("{:<28}{:>7}{:>8}{:>11}{:>10}{:>11}{:>12}{:>13}".format(
            f"{dim}: {a} - {b}", r["cells"], r["pairs"], r["stouffer_z"], r["p"],
            r["median_dexp"], r["median_dwin"], r["median_dpayoff"]))
    out["paired_tests_deep"] = res

    print()
    print("  same comparisons on RAW (18-19 sessions/symbol, 5 symbols) - power check")
    res_raw = []
    for dim, a, b in comps:
        r = paired(raw, dim, a, b)
        res_raw.append(r)
        print("{:<28}{:>7}{:>8}{:>11}{:>10}{:>11}{:>12}{:>13}".format(
            f"{dim}: {a} - {b}", r["cells"], r["pairs"], r["stouffer_z"], r["p"],
            r["median_dexp"], r["median_dwin"], r["median_dpayoff"]))
    out["paired_tests_raw"] = res_raw

    json.dump(out, open(f"{D}/orb_analysis.json", "w"), indent=1, default=str)
    print("\nwrote orb_analysis.json")
