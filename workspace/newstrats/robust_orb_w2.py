"""Robustness: parameter sensitivity, cost sensitivity, regime split, and a
bar-level identity check against the shipped ``opening_range_breakout``."""
from __future__ import annotations

import json
import statistics as st
import sys
from collections import defaultdict

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/newstrats")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")

import orb_w2 as O  # noqa: E402
from futures_agents.config import get_contract  # noqa: E402
from futures_agents.timeutil import to_et  # noqa: E402

D = "/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad"
out = {}

# ------------------------------------------------- 1. parameter sensitivity
CAND = ("MGC", 15, 60, "retest", "mid", "r1")
print("=" * 104)
print(f"PARAMETER SENSITIVITY around the single best configuration in the census: {CAND}")
print("  A real effect has a neighbourhood. A data-mining artefact is a spike.")
print("=" * 104)
print("{:<38}{:>6}{:>8}{:>9}{:>9}{:>8}{:>8}".format(
    "config", "n", "win", "payoff", "exp", "maxdd", "t"))
neigh = []
for sym in ["MGC", "MES", "MNQ"]:
    for orl in [30, 60]:
        for sp in ["opp", "mid", "atr"]:
            for tg in ["r1", "rw1"]:
                for tf in [5, 15]:
                    tr, dg = O.simulate(sym, tf, orl, "retest", sp, tg, source="deep")
                    if len(tr) < 20:
                        continue
                    m = O.metrics(tr)
                    star = "  <== candidate" if (sym, tf, orl, "retest", sp, tg) == CAND else ""
                    row = dict(symbol=sym, tf=tf, orlen=orl, stop=sp, target=tg, **m)
                    neigh.append(row)
                    print("{:<38}{:>6}{:>8}{:>9}{:>9}{:>8}{:>8}{}".format(
                        f"{sym} {tf}m OR{orl} retest {sp} {tg}", m["n"], m["win"],
                        m["payoff"], m["exp"], m["maxdd"], m["t"], star))
out["parameter_neighbourhood"] = neigh
pos = sum(1 for r in neigh if r["exp"] > 0)
print(f"\n  neighbourhood: {pos}/{len(neigh)} positive; median exp "
      f"{st.median([r['exp'] for r in neigh]):+.4f}R; best t {max(r['t'] for r in neigh):.2f}")

# -------------------------------------------------------- 2. cost sensitivity
print()
print("=" * 104)
print("COST SENSITIVITY: gross, modelled, and double-slippage, deep, median over all 240 configs/symbol")
print("=" * 104)
import orb_w2 as _O
costs = {}
for label, se, ss in [("zero cost", 0.0, 0.0), ("modelled (0.5/1.5 ticks)", 0.5, 1.5),
                      ("double slippage (1.0/3.0)", 1.0, 3.0)]:
    _O.SLIP_ENTRY_TICKS, _O.SLIP_STOP_TICKS = se, ss
    _O.SLIP_EOD_TICKS = ss
    meds = {}
    for sym in ["MGC", "MES", "MNQ"]:
        es = []
        for orl in [5, 15, 30, 60]:
            for en in ["break", "retest"]:
                for sp in ["opp", "mid", "atr"]:
                    for tg in ["r1", "r2", "rw1"]:
                        tr, _ = O.simulate(sym, 15, orl, en, sp, tg, source="deep")
                        if len(tr) >= 20:
                            es.append(O.metrics(tr)["exp"])
        meds[sym] = round(st.median(es), 4)
    costs[label] = meds
    print(f"  {label:<28} " + "  ".join(f"{k} {v:+.4f}R" for k, v in meds.items()))
_O.SLIP_ENTRY_TICKS, _O.SLIP_STOP_TICKS, _O.SLIP_EOD_TICKS = 0.5, 1.5, 1.5
out["cost_sensitivity"] = costs

# ------------------------------------------------- 3. regime / session split
print()
print("=" * 104)
print("PERFORMANCE BY VOLATILITY REGIME AND TIME OF DAY (deep, 15m, OR30, break, opp, rw1)")
print("  regime = tercile of the session's opening-range width / 14-day median OR width")
print("=" * 104)
reg = {}
for sym in ["MGC", "MES", "MNQ"]:
    ors = O.opening_ranges(sym, 30, "deep")
    widths = {d: o.width for d, o in ors.items()}
    med = st.median(widths.values())
    rows = defaultdict(list)
    hourly = defaultdict(list)
    for en in ["break", "retest"]:
        for sp in ["opp", "mid", "atr"]:
            for tg in ["rw1", "r2"]:
                tr, _ = O.simulate(sym, 15, 30, en, sp, tg, source="deep")
                for t in tr:
                    w = widths.get(t.day)
                    if w is None:
                        continue
                    b = "wide OR" if w > 1.25 * med else ("narrow OR" if w < 0.75 * med else "mid OR")
                    rows[b].append(t.r)
                    hourly[to_et(t.entry_ts).hour].append(t.r)
    reg[sym] = {k: dict(n=len(v), exp=round(st.mean(v), 4),
                        win=round(sum(1 for x in v if x > 0) / len(v), 3))
                for k, v in sorted(rows.items())}
    reg[sym]["by_entry_hour_ET"] = {str(h): dict(n=len(v), exp=round(st.mean(v), 4))
                                    for h, v in sorted(hourly.items())}
    print(f"  {sym}: " + "  ".join(
        f"{k} n={v['n']} exp={v['exp']:+.3f} win={v['win']}"
        for k, v in reg[sym].items() if k != "by_entry_hour_ET"))
    print(f"        by entry hour ET: " + "  ".join(
        f"{h}h n={v['n']} {v['exp']:+.3f}" for h, v in reg[sym]["by_entry_hour_ET"].items()))
out["regime_and_time_of_day"] = reg

# ------------------------------- 4. bar-level identity vs the shipped condition
print()
print("=" * 104)
print("BAR-LEVEL IDENTITY: my OR break vs the library's shipped opening_range_breakout")
print("=" * 104)
from futures_agents.features import build_symbol_frame  # noqa: E402
from futures_agents.scout import FRAMES  # noqa: E402
from futures_agents.strategies.library import get_condition as lib_get  # noqa: E402
import toolkit as T  # noqa: E402

jac = []
lib = lib_get("opening_range_breakout")
for sym in ["MGC", "MES", "MNQ", "MCL"]:
    for tf in [5, 15, 60]:
        try:
            s = T._series(sym, tf, None)
            fr = build_symbol_frame(s, FRAMES[tf])
        except Exception as e:
            print(f"  {sym} {tf}m: {e}")
            continue
        mine, theirs = set(), set()
        spec = get_contract(sym)
        ors = O.opening_ranges(sym, 30, "raw")       # 30m to match the library's hard-coded 30
        for i in range(len(fr)):
            sn = fr.snapshot(i)
            r = lib.evaluate(sn, tf)
            if r.triggered:
                theirs.add(sn.ts)
            o = ors.get(to_et(sn.ts).date())
            if o and to_et(sn.ts) >= o.end_ts:
                b = s.bars[i]
                if b.close > o.high or b.close < o.low:
                    mine.add(sn.ts)
        inter = len(mine & theirs)
        uni = len(mine | theirs)
        jac.append(dict(symbol=sym, tf=tf, bars=len(fr), mine=len(mine),
                        library=len(theirs), intersection=inter,
                        jaccard=round(inter / uni, 4) if uni else None,
                        library_fire_rate=round(len(theirs) / len(fr), 5),
                        mine_fire_rate=round(len(mine) / len(fr), 5)))
        print(f"  {sym:4} {tf:>3}m bars={len(fr):>5}  mine={len(mine):>5} "
              f"({len(mine)/len(fr):.2%})  library={len(theirs):>5} "
              f"({len(theirs)/len(fr):.2%})  Jaccard={jac[-1]['jaccard']}")
out["jaccard_vs_library"] = jac

json.dump(out, open(f"{D}/orb_robust.json", "w"), indent=1, default=str)
print("\nwrote orb_robust.json")
