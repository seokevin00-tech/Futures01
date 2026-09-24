"""Part A, step 1: BAR-LEVEL hour-of-day census, before any strategy exists.

If ICT's kill zones are where "the real moves happen", that has to be visible
in the raw bars: more range, more volume, more follow-through, more of the
day's extremes.  If it is not visible here, no strategy built on the windows
can work, and this is a cleaner finding than any backtest.

Normalisation: every price and volume quantity is divided by that TRADING
DAY's own mean bar range (or mean bar volume), so the profile is the shape of
the day and not the level of the vol regime.  A day therefore contributes the
same total weight whether it was a 300-point day or a 60-point day.

Trading day uses the CME 18:00 ET boundary, which is also ICT's convention -
the 20:00 Asian range belongs to the session it precedes.
"""
from __future__ import annotations

import json
import math
import os
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")
sys.path.insert(0, "/home/user/Futures01/workspace/newstrats")

import toolkit as T                                    # noqa: E402
import w6_ict_time as K                                   # noqa: E402
from futures_agents.timeutil import to_et, trading_day  # noqa: E402

OUT = "/home/user/Futures01/workspace/strategy_research/scratch/ict"
os.makedirs(OUT, exist_ok=True)


def bar_rows(symbol: str, tf: int):
    """Per-bar record with everything the hour profile needs."""
    series = T._series(symbol, tf, None)
    bars = series.bars
    rows = []
    for i, b in enumerate(bars):
        et = to_et(b.ts)
        rng = b.high - b.low
        nxt1 = bars[i + 1].close - b.close if i + 1 < len(bars) else None
        nxt3 = bars[i + 3].close - b.close if i + 3 < len(bars) else None
        rows.append(dict(
            i=i, ts=b.ts, day=trading_day(b.ts), hour=et.hour, dow=et.weekday(),
            o=b.open, h=b.high, l=b.low, c=b.close, v=float(b.volume or 0.0),
            rng=rng, body=abs(b.close - b.open), ret=b.close - b.open,
            eff=(abs(b.close - b.open) / rng) if rng > 0 else 0.0,
            lnhl=math.log(b.high / b.low) if b.low > 0 else 0.0,
            f1=nxt1, f3=nxt3,
            cont1=(1.0 if b.close >= b.open else -1.0) * nxt1 if nxt1 is not None else None,
            cont3=(1.0 if b.close >= b.open else -1.0) * nxt3 if nxt3 is not None else None,
        ))
    return rows


def normalise(rows):
    """Divide price/volume quantities by that trading day's own mean."""
    by_day = defaultdict(list)
    for r in rows:
        by_day[r["day"]].append(r)
    out = []
    for day, rs in by_day.items():
        if len(rs) < 6:                 # a half day tells us nothing about shape
            continue
        srng = st.mean(x["rng"] for x in rs) or 1e-9
        svol = st.mean(x["v"] for x in rs) or 1e-9
        hi = max(x["h"] for x in rs)
        lo = min(x["l"] for x in rs)
        hi_h = [x["hour"] for x in rs if x["h"] == hi][0]
        lo_h = [x["hour"] for x in rs if x["l"] == lo][0]
        for r in rs:
            q = dict(r)
            q["n_rng"] = r["rng"] / srng
            q["n_body"] = r["body"] / srng
            q["n_vol"] = (r["v"] / svol) if svol > 0 else 0.0
            q["n_f1"] = (r["f1"] / srng) if r["f1"] is not None else None
            q["n_af1"] = (abs(r["f1"]) / srng) if r["f1"] is not None else None
            q["n_f3"] = (r["f3"] / srng) if r["f3"] is not None else None
            q["n_af3"] = (abs(r["f3"]) / srng) if r["f3"] is not None else None
            q["n_cont1"] = (r["cont1"] / srng) if r["cont1"] is not None else None
            q["n_cont3"] = (r["cont3"] / srng) if r["cont3"] is not None else None
            q["is_day_high"] = 1.0 if r["hour"] == hi_h else 0.0
            q["is_day_low"] = 1.0 if r["hour"] == lo_h else 0.0
            q["is_day_ext"] = 1.0 if r["hour"] in (hi_h, lo_h) else 0.0
            out.append(q)
    return out


METRICS = ["n_rng", "n_body", "n_vol", "eff", "n_af1", "n_af3", "n_f1",
           "n_cont1", "n_cont3", "is_day_high", "is_day_low", "is_day_ext"]


def hour_profile(rows):
    by_h = defaultdict(list)
    for r in rows:
        by_h[r["hour"]].append(r)
    prof = {}
    for h in sorted(by_h):
        rs = by_h[h]
        d = {"n_bars": len(rs), "n_days": len({r["day"] for r in rs})}
        for m in METRICS:
            vals = [r[m] for r in rs if r.get(m) is not None]
            d[m] = round(st.mean(vals), 4) if vals else None
            if m in ("n_cont1", "n_f1") and len(vals) > 5:
                sd = st.pstdev(vals) or 1e-9
                d[m + "_t"] = round(st.mean(vals) / (sd / math.sqrt(len(vals))), 2)
        prof[h] = d
    return prof


def paired_day_test(rows, hours_a, metric):
    """Per trading DAY: mean(metric | hour in A) - mean(metric | hour not in A).

    Days are the unit of observation, which keeps intraday autocorrelation out
    of the standard error.  Reports the paired t and a sign test.
    """
    by_day = defaultdict(lambda: ([], []))
    for r in rows:
        v = r.get(metric)
        if v is None:
            continue
        (by_day[r["day"]][0] if r["hour"] in hours_a else by_day[r["day"]][1]).append(v)
    diffs = [st.mean(a) - st.mean(b) for a, b in by_day.values() if a and b]
    if len(diffs) < 10:
        return {"n_days": len(diffs), "skipped": True}
    m = st.mean(diffs)
    sd = st.stdev(diffs) or 1e-9
    t = m / (sd / math.sqrt(len(diffs)))
    pos = sum(1 for d in diffs if d > 0)
    neg = sum(1 for d in diffs if d < 0)
    z = (pos - neg) / math.sqrt(pos + neg) if pos + neg else 0.0
    return {"n_days": len(diffs), "mean_diff": round(m, 4), "t": round(t, 2),
            "median_diff": round(st.median(diffs), 4),
            "sign_pos": pos, "sign_neg": neg, "sign_z": round(z, 2),
            "ratio": round((st.mean([st.mean(a) for a, b in by_day.values() if a and b])
                            / (st.mean([st.mean(b) for a, b in by_day.values() if a and b]) or 1e-9)), 3)}


def stouffer(zs):
    zs = [z for z in zs if z is not None]
    return round(sum(zs) / math.sqrt(len(zs)), 3) if zs else None


def run(symbols, tfs, n_slices=3):
    report = {"design": {
        "normaliser": "each metric divided by that trading day's own mean bar range/volume",
        "trading_day": "CME 18:00 ET boundary (timeutil.trading_day)",
        "hour": "ET hour of the bar's START stamp, DST-correct via zoneinfo",
        "kill_zones": {k: list(v) for k, v in K.KILL_ZONES.items()},
        "unit_of_observation": "trading day (not bar), for the paired tests"}}
    for symbol in symbols:
        for tf in tfs:
            try:
                raw = bar_rows(symbol, tf)
            except FileNotFoundError:
                continue
            if len(raw) < 200:
                continue
            rows = normalise(raw)
            if not rows:
                continue
            span = (to_et(rows[0]["ts"]).isoformat(), to_et(rows[-1]["ts"]).isoformat())
            days = sorted({r["day"] for r in rows})
            cell = {"span": span, "n_bars": len(rows), "n_days": len(days),
                    "full": {"hour_profile": hour_profile(rows)}}
            # disjoint slices by DAY, so a slice never shares a bar with another
            edges = [days[int(len(days) * k / n_slices)] for k in range(n_slices)] + [None]
            slices = []
            for k in range(n_slices):
                lo, hi = edges[k], edges[k + 1]
                sl = [r for r in rows if r["day"] >= lo and (hi is None or r["day"] < hi)]
                slices.append(sl)
            cell["slices"] = [{"from": str(s[0]["day"]), "to": str(s[-1]["day"]),
                               "n_bars": len(s)} for s in slices if s]
            zone_tests = {}
            for zone in list(K.KILL_ZONES) + ["union"]:
                hrs = (set(K.union_hours(K.KZ_TRADE_UNION)) if zone == "union"
                       else set(K.zone_hours(zone)))
                per_metric = {}
                for metric in ["n_rng", "n_vol", "n_af1", "n_cont1", "is_day_ext", "eff"]:
                    full = paired_day_test(rows, hrs, metric)
                    per_slice = [paired_day_test(s, hrs, metric) for s in slices if s]
                    per_metric[metric] = {
                        "full": full,
                        "slices": per_slice,
                        "slice_t": [p.get("t") for p in per_slice],
                        "stouffer_slice_sign_z": stouffer([p.get("sign_z") for p in per_slice]),
                        "replicated_sign": all(
                            (p.get("mean_diff") or 0) > 0 for p in per_slice) or all(
                            (p.get("mean_diff") or 0) < 0 for p in per_slice)}
                zone_tests[zone] = per_metric
            cell["zone_tests"] = zone_tests
            report.setdefault(symbol, {})[str(tf)] = cell
            print(f"{symbol} {tf}m  bars={len(rows)} days={len(days)} {span[0][:10]}..{span[1][:10]}",
                  flush=True)
    return report


if __name__ == "__main__":
    syms = ["MNQ", "MES", "MGC", "MCL"]
    rep = run(syms, [60, 15, 5])
    with open(f"{OUT}/bar_hour_census.json", "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print("written", f"{OUT}/bar_hour_census.json")
