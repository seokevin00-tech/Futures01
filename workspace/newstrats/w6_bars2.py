"""Part A, step 2: the control that decides whether the ICT clock adds anything.

Step 1 showed the kill zones sit high on range and volume.  So does "the US
cash session", and every kill zone except london_open and asian_range overlaps
it.  The question that matters is therefore NOT "are kill-zone hours more
active than the average hour" - they are, trivially - but:

  * inside RTH, is the Silver Bullet / NY-open window distinguishable from the
    other RTH hours?
  * outside RTH, is the London-open window distinguishable from the rest of
    the overnight?

If both answers are no, then ICT's clock carries no information beyond "trade
the cash session", which is not an ICT idea.

RTH is taken from each contract's own ``ContractSpec`` - MGC closes 13:30 ET
and MCL 14:30, so the RTH hour set differs by symbol and the comparison has to
as well.
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

import w6_bars as B                                   # noqa: E402
import w6_ict_time as K                                   # noqa: E402
from futures_agents.config import get_contract         # noqa: E402

OUT = "/home/user/Futures01/workspace/strategy_research/scratch/ict"


def rth_hours(symbol: str):
    """Hours whose 60m bar overlaps this contract's own RTH."""
    spec = get_contract(symbol)
    o = int(spec.rth_open.split(":")[0])
    ch, cm = (int(x) for x in spec.rth_close.split(":"))
    last = ch if cm > 0 else ch - 1
    return set(range(o, last + 1))


def paired_within(rows, hours_a, hours_pool, metric):
    """Paired per-day test of A vs (pool minus A); both restricted to pool."""
    by_day = defaultdict(lambda: ([], []))
    for r in rows:
        if r["hour"] not in hours_pool:
            continue
        v = r.get(metric)
        if v is None:
            continue
        (by_day[r["day"]][0] if r["hour"] in hours_a else by_day[r["day"]][1]).append(v)
    pairs = [(st.mean(a), st.mean(b)) for a, b in by_day.values() if a and b]
    if len(pairs) < 10:
        return {"n_days": len(pairs), "skipped": True}
    diffs = [a - b for a, b in pairs]
    m, sd = st.mean(diffs), (st.stdev(diffs) or 1e-9)
    pos = sum(1 for d in diffs if d > 0)
    neg = sum(1 for d in diffs if d < 0)
    return {"n_days": len(pairs), "mean_diff": round(m, 4),
            "t": round(m / (sd / math.sqrt(len(diffs))), 2),
            "ratio": round(st.mean([a for a, _ in pairs])
                           / (st.mean([b for _, b in pairs]) or 1e-9), 3),
            "sign_pos": pos, "sign_neg": neg,
            "sign_z": round((pos - neg) / math.sqrt(pos + neg), 2) if pos + neg else 0.0}


METRICS = ["n_rng", "n_vol", "n_af1", "n_cont1", "is_day_ext", "eff"]


def run(symbols, tf=60, n_slices=3):
    rep = {"design": {
        "question": "does the ICT clock add anything beyond 'the US cash session'",
        "within_rth": "kill-zone hours vs the OTHER hours of the same contract's RTH",
        "within_overnight": "kill-zone hours vs the OTHER non-RTH hours",
        "rth_hours_per_symbol": {}}}
    for symbol in symbols:
        rows = B.normalise(B.bar_rows(symbol, tf))
        rth = rth_hours(symbol)
        oth = set(range(24)) - rth
        rep["design"]["rth_hours_per_symbol"][symbol] = sorted(rth)
        days = sorted({r["day"] for r in rows})
        edges = [days[int(len(days) * k / n_slices)] for k in range(n_slices)] + [None]
        slices = [[r for r in rows if r["day"] >= edges[k]
                   and (edges[k + 1] is None or r["day"] < edges[k + 1])]
                  for k in range(n_slices)]
        cell = {}
        tests = {
            "silver_bullet_vs_other_RTH": (set(K.zone_hours("silver_bullet")) & rth, rth),
            "ny_open_vs_other_RTH": (set(K.zone_hours("ny_open")) & rth, rth),
            "london_close_vs_other_RTH": (set(K.zone_hours("london_close")) & rth, rth),
            "london_open_vs_other_overnight": (set(K.zone_hours("london_open")) & oth, oth),
            "asian_vs_other_overnight": (set(K.zone_hours("asian_range")) & oth, oth),
            "ny_open_preRTH_vs_other_overnight": (set(K.zone_hours("ny_open")) & oth, oth),
        }
        for name, (a, pool) in tests.items():
            if not a or not (pool - a):
                cell[name] = {"skipped": "empty arm", "hours_a": sorted(a)}
                continue
            entry = {"hours_a": sorted(a), "hours_b": sorted(pool - a), "metrics": {}}
            for m in METRICS:
                full = paired_within(rows, a, pool, m)
                per = [paired_within(s, a, pool, m) for s in slices]
                zs = [p.get("sign_z") for p in per if not p.get("skipped")]
                entry["metrics"][m] = {
                    "full": full, "slice_t": [p.get("t") for p in per],
                    "stouffer_sign_z": round(sum(zs) / math.sqrt(len(zs)), 2) if zs else None,
                    "replicated": (all((p.get("mean_diff") or 0) > 0 for p in per)
                                   or all((p.get("mean_diff") or 0) < 0 for p in per))}
            cell[name] = entry
        rep[symbol] = cell
        print(symbol, "done", flush=True)
    return rep


if __name__ == "__main__":
    rep = run(["MNQ", "MES", "MGC", "MCL"])
    with open(f"{OUT}/bar_hour_control.json", "w") as fh:
        json.dump(rep, fh, indent=1, default=str)
    print("written", f"{OUT}/bar_hour_control.json")
