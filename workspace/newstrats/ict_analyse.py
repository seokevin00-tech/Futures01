"""Per-cell paired ablation.  No T.ab anywhere (defect D28)."""
import json, math, os, statistics as st, sys
os.chdir('/home/user/Futures01')
sys.path[:0] = ['/home/user/Futures01', 'workspace/studies', 'workspace/newstrats']
import toolkit as T

D = json.load(open('/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad/perf.json'))["db"]
ARMS = ["sweep_only", "mss_only", "sweep_mss", "mss_fvg_retrace", "full", "wrong_order"]
CELLS = sorted(D)


def welch(a, b):
    """Two-sample z on two R-series.  The arms hold DIFFERENT trades, so this is unpaired -
    but it is computed inside one cell only, never pooled across cells."""
    if len(a) < 5 or len(b) < 5:
        return None
    ma, mb = st.mean(a), st.mean(b)
    va, vb = st.pvariance(a), st.pvariance(b)
    se = math.sqrt(va / len(a) + vb / len(b))
    return round((ma - mb) / se, 3) if se > 0 else None


def stouffer(zs):
    zs = [z for z in zs if z is not None]
    return round(sum(zs) / math.sqrt(len(zs)), 3) if zs else None


def sign_test(deltas):
    d = [x for x in deltas if x is not None and x != 0]
    k, n = sum(1 for x in d if x > 0), len(d)
    if n == 0:
        return {"pos": 0, "n": 0, "z": None}
    z = (k - n / 2) / math.sqrt(n / 4)
    return {"pos": k, "n": n, "z": round(z, 3),
            "p": round(2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2)))), 4)}


def table(window, exit_="anchored"):
    print(f"\n=== {window} / exit={exit_}  (expectancy in R, n trades) ===")
    print(f"{'cell':11s}" + "".join(f"{a[:13]:>16s}" for a in ARMS))
    for c in CELLS:
        cells = []
        for a in ARMS:
            r = D[c].get(f"{a}|{exit_}|{window}")
            cells.append(f"{r['exp']:>+9.3f}/{r['n']:<6d}" if r and r['n'] else f"{'-':>16s}")
        print(f"{c:11s}" + "".join(cells))


def compare(x, y, window, exit_="anchored"):
    zs, deltas, rows = [], [], {}
    for c in CELLS:
        A, B = D[c].get(f"{x}|{exit_}|{window}"), D[c].get(f"{y}|{exit_}|{window}")
        if not A or not B or A['n'] < 5 or B['n'] < 5:
            continue
        z = welch(A['rs'], B['rs'])
        d = round(A['exp'] - B['exp'], 4)
        zs.append(z); deltas.append(d)
        rows[c] = {"n_a": A['n'], "n_b": B['n'], "exp_a": A['exp'], "exp_b": B['exp'],
                   "delta": d, "z": z}
    return {"a": x, "b": y, "window": window, "exit": exit_, "cells": len(deltas),
            "median_delta_exp": round(st.median(deltas), 4) if deltas else None,
            "sign_test": sign_test(deltas), "stouffer_z": stouffer(zs), "per_cell": rows}


if __name__ == "__main__":
    for w in ("full", "IS", "OOS"):
        table(w)
    PAIRS = [("full", "sweep_mss"), ("full", "mss_fvg_retrace"), ("full", "mss_only"),
             ("full", "sweep_only"), ("full", "wrong_order"),
             ("sweep_mss", "mss_only"), ("mss_fvg_retrace", "mss_only"),
             ("sweep_only", "mss_only")]
    out = {}
    print(f"\n{'comparison':38s}{'win':>5s}{'cells':>7s}{'medΔexp':>9s}{'sign z':>8s}{'stouf z':>9s}")
    for w in ("full", "IS", "OOS"):
        for ex in ("anchored", "r2", "struct"):
            for x, y in PAIRS:
                r = compare(x, y, w, ex)
                out[f"{x}_vs_{y}|{w}|{ex}"] = r
                if ex == "anchored":
                    print(f"{x+' - '+y:38s}{w:>5s}{r['cells']:>7d}"
                          f"{(r['median_delta_exp'] if r['median_delta_exp'] is not None else 0):>9.3f}"
                          f"{str(r['sign_test']['z']):>8s}{str(r['stouffer_z']):>9s}")
    json.dump(out, open('/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad/ablation.json', 'w'), indent=1, default=str)
