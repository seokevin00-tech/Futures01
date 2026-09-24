"""The lift, with the mechanical confound removed.

The first control ("ran the level and closed beyond it") is location-matched but not
*distance*-matched, and that difference is close to tautological: a sweep bar closes back
INSIDE the level, so it is already nearer the swing low that the MSS has to break, while the
breakout bar closed the other way and has further to travel.  Part of any lift measured that
way is arithmetic, not order flow.

So stratify on exactly that: ``d = (close - reference swing) / ATR``, the distance the MSS
still has to cover, in volatility units.  Inside a stratum both arms are the same distance
from the break, and a residual difference is attributable to the sweep rather than to
geometry.  Two controls:

* ``ran_thru``  - ran the same level, closed beyond it;
* ``no_level``  - ran no level at all, same distance bucket.  If the lift survives against
  this one, resting liquidity is doing something; if it does not, "sweep" is a proxy for
  "price is close to the swing".

Combined across strata by Mantel-Haenszel, which is the standard way to pool 2x2 tables
without letting an imbalanced covariate manufacture an association.
"""
import json, math, os, sys
os.chdir('/home/user/Futures01')
sys.path[:0] = ['/home/user/Futures01', 'workspace/studies', 'workspace/newstrats']
import toolkit as T
import ict_sweep as I

EDGES = [0.0, 0.5, 1.0, 1.5, 2.5, 4.0, 1e9]


def _bucket(d):
    for k in range(len(EDGES) - 1):
        if EDGES[k] <= d < EDGES[k + 1]:
            return k
    return None


def analyse(sym, tf, N=5, cfg=I.Cfg()):
    bars = list(T._series(sym, tf, None).bars)
    n = len(bars)
    atr, lv = I._atr(bars), I._levels(bars)
    highs, lows = I._confirmed_swings(bars, cfg)
    HI, LO = I._latest_by_index(highs, n), I._latest_by_index(lows, n)
    right = cfg.swing_right

    def ref_swing(tbl, at, before):
        k = min(at - right, before - 1)
        return tbl[k] if 0 <= k < n else None

    def mss_within(i, short):
        for j in range(i + 1, min(i + 1 + N, n)):
            r = ref_swing(LO if short else HI, j, i)
            if r is None:
                continue
            if (bars[j].close < r.price) if short else (bars[j].close > r.price):
                return True
        return False

    # arm -> bucket -> [hit, total]
    tab = {a: {k: [0, 0] for k in range(len(EDGES) - 1)}
           for a in ("swept", "ran_thru", "no_level")}
    start = max(cfg.swing_left + right + 1, 20)
    for i in range(start, n - 1):
        b, a = bars[i], (atr[i] or 0.0)
        if a <= 0:
            continue
        hit_h = hit_l = None
        for name in cfg.pools:
            if name == "SWH":
                s = ref_swing(HI, i, i); L = s.price if s else None
            elif name == "SWL":
                s = ref_swing(LO, i, i); L = s.price if s else None
            else:
                L = lv[i].get(name)
            if L is None:
                continue
            if name.endswith("H") and b.high > L:
                hit_h = L if hit_h is None else max(hit_h, L)
            if name.endswith("L") and b.low < L:
                hit_l = L if hit_l is None else min(hit_l, L)
        for short in (True, False):
            r = ref_swing(LO if short else HI, i, i)
            if r is None:
                continue
            d = (b.close - r.price) / a if short else (r.price - b.close) / a
            k = _bucket(d)
            if k is None:
                continue                      # already beyond the reference: not a setup
            ran = hit_h if short else hit_l
            if ran is None:
                arm = "no_level"
            else:
                arm = "swept" if ((b.close < ran) if short else (b.close > ran)) else "ran_thru"
            cell = tab[arm][k]
            cell[1] += 1
            cell[0] += int(mss_within(i, short))
    return tab


def mantel_haenszel(t_a, t_b):
    """Pooled 2x2 across strata: a = treatment, b = control."""
    num = den = 0.0
    obs = exp = var = 0.0
    used = 0
    for k in t_a:
        a, n1 = t_a[k]
        c, n2 = t_b[k]
        b, dd = n1 - a, n2 - c
        nt = n1 + n2
        if nt < 10 or n1 == 0 or n2 == 0:
            continue
        used += 1
        num += a * dd / nt
        den += b * c / nt
        obs += a
        exp += n1 * (a + c) / nt
        var += n1 * n2 * (a + c) * (b + dd) / (nt * nt * (nt - 1)) if nt > 1 else 0
    if var <= 0:
        return {"or": None, "z": None, "strata": used}
    z = (obs - exp) / math.sqrt(var)
    return {"or": round(num / den, 3) if den else None, "z": round(z, 3),
            "strata": used, "n_treat": sum(t_a[k][1] for k in t_a),
            "n_ctrl": sum(t_b[k][1] for k in t_b)}


if __name__ == "__main__":
    res = {}
    for sym in ['MES', 'MNQ', 'MGC', 'MCL']:
        for tf in [5, 15, 30, 60, 240]:
            for N in (3, 5, 12):
                t = analyse(sym, tf, N)
                key = f"{sym}|{tf}|N{N}"
                res[key] = {"raw": {a: {str(k): v for k, v in t[a].items()} for a in t},
                            "vs_ran_thru": mantel_haenszel(t["swept"], t["ran_thru"]),
                            "vs_no_level": mantel_haenszel(t["swept"], t["no_level"])}
                if N == 5:
                    print(f"{sym:4s} {tf:4d} N={N}  vs_ranthru OR={res[key]['vs_ran_thru']['or']}"
                          f" z={res[key]['vs_ran_thru']['z']}   vs_nolevel"
                          f" OR={res[key]['vs_no_level']['or']} z={res[key]['vs_no_level']['z']}",
                          flush=True)
    json.dump(res, open('/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad/lift2.json', 'w'), indent=1)
