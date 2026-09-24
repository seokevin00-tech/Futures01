"""Is the sequence a mechanism, or two independent events that sometimes land near each other?

ICT's claim is causal: the stop run *creates* the conditions for the reversal, so the structure
shift that follows it is not the same animal as a structure shift anywhere else.  That claim is
testable without any exit model, any trade floor or any P&L, and it should be tested first -
a performance number for a chain whose links are statistically independent is a number about
the exit model.

Three arms, all evaluated with the SAME market-structure-shift code:

* **swept**    - price ran an obvious level and closed back inside it;
* **ran_thru** - price ran the SAME level on the same bar and closed BEYOND it.  This is the
  control that matters: identical location, identical proximity to resting liquidity,
  identical volatility context; the only difference is the reclaim;
* **anybar**   - the unconditional base rate, for scale.

If P(opposing MSS within N | swept) is not above P(... | ran_thru), the sweep is not doing the
work the theory says it does.
"""
import json, math, os, sys
os.chdir('/home/user/Futures01')
sys.path[:0] = ['/home/user/Futures01', 'workspace/studies', 'workspace/newstrats']
import toolkit as T
import ict_sweep as I


def analyse(sym, tf, cfg=I.Cfg(), n_gaps=(1, 2, 3, 5, 8, 12, 20)):
    bars = list(T._series(sym, tf, None).bars)
    n = len(bars)
    atr, lv = I._atr(bars), I._levels(bars)
    highs, lows = I._confirmed_swings(bars, cfg)
    HI, LO = I._latest_by_index(highs, n), I._latest_by_index(lows, n)
    right = cfg.swing_right

    def ref_swing(tbl, at, before):
        k = min(at - right, before - 1)
        return tbl[k] if 0 <= k < n else None

    def mss_within(i, short, N):
        """Does structure shift AGAINST the run within N bars, referenced to a swing that
        formed before bar i?  Exactly the rule the full chain uses."""
        for j in range(i + 1, min(i + 1 + N, n)):
            r = ref_swing(LO if short else HI, j, i)
            if r is None:
                continue
            if (bars[j].close < r.price) if short else (bars[j].close > r.price):
                return j
        return None

    start = max(cfg.swing_left + right + 1, 20)
    swept = {True: [], False: []}
    ranthru = {True: [], False: []}
    for i in range(start, n):
        b, a = bars[i], (atr[i - 1] or 0.0)
        hit_h = hit_l = None
        for name in cfg.pools:
            if name == "SWH":
                s = ref_swing(HI, i, i)
                L = s.price if s else None
            elif name == "SWL":
                s = ref_swing(LO, i, i)
                L = s.price if s else None
            else:
                L = lv[i].get(name)
            if L is None:
                continue
            if name.endswith("H") and b.high > L + cfg.min_pen_atr * a:
                hit_h = L if hit_h is None else max(hit_h, L)
            if name.endswith("L") and b.low < L - cfg.min_pen_atr * a:
                hit_l = L if hit_l is None else min(hit_l, L)
        if hit_h is not None:
            (swept if b.close < hit_h else ranthru)[True].append(i)
        if hit_l is not None:
            (swept if b.close > hit_l else ranthru)[False].append(i)

    anybar = list(range(start, n - 1))
    out = {}
    for N in n_gaps:
        row = {}
        for arm, sets in (("swept", swept), ("ran_thru", ranthru)):
            hit = tot = 0
            for short in (True, False):
                for i in sets[short]:
                    tot += 1
                    hit += mss_within(i, short, N) is not None
            row[arm] = {"n": tot, "hit": hit, "p": round(hit / tot, 4) if tot else None}
        hit = tot = 0
        for i in anybar:
            for short in (True, False):
                tot += 1
                hit += mss_within(i, short, N) is not None
        row["anybar"] = {"n": tot, "hit": hit, "p": round(hit / tot, 4)}
        a, b2 = row["swept"], row["ran_thru"]
        row["lift_vs_ranthru"] = round(a["p"] / b2["p"], 3) if b2["p"] else None
        row["lift_vs_anybar"] = round(a["p"] / row["anybar"]["p"], 3) if row["anybar"]["p"] else None
        row["z_swept_vs_ranthru"] = _z2(a["hit"], a["n"], b2["hit"], b2["n"])
        out[N] = row
    return out


def _z2(k1, n1, k2, n2):
    if not n1 or not n2:
        return None
    p = (k1 + k2) / (n1 + n2)
    se = math.sqrt(p * (1 - p) * (1 / n1 + 1 / n2))
    return round(((k1 / n1) - (k2 / n2)) / se, 3) if se else None


if __name__ == "__main__":
    res = {}
    for sym in ['MES', 'MNQ', 'MGC', 'MCL']:
        for tf in [5, 15, 30, 60, 240]:
            res[f"{sym}|{tf}"] = analyse(sym, tf)
            print(sym, tf, {N: (v['swept']['p'], v['ran_thru']['p'], v['lift_vs_ranthru'],
                                v['z_swept_vs_ranthru']) for N, v in res[f"{sym}|{tf}"].items()
                            if N in (3, 5, 12)}, flush=True)
    json.dump(res, open('/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad/lift.json', 'w'), indent=1)
