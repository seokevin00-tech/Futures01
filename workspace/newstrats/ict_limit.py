"""The strongest objection to this study's negative verdict, tested rather than argued.

Entries here are signalled at the close of the retrace bar and filled at the NEXT bar's open.
Measured, 41% of those fills land more than one gap-height away from the imbalance edge
(median 0.74 gap-heights).  ICT prescribes a resting limit AT the edge, so the study may be
losing a real edge to execution.

This simulates that limit directly: fill at the gap edge on the touch bar, stop one ATR the
other side of the FILL, anchored targets at 1.0 and 2.5 ATR, 50/50 scale-out, stop-before-
target inside the same bar, 40-bar time stop, and a round-turn commission still charged.  It
is deliberately generous - no queue risk, no partial fill, no slippage on the entry, and the
touch bar itself is allowed to fill even though the limit would have had to be resting before
the bar opened.

Note the first-order effect is small by construction: with an ATR stop and ATR-anchored
targets measured FROM the entry, a better fill translates the whole geometry rather than
handing over free R.  What changes is path dependence, and that is what this measures.
"""
import json, os, statistics as st, sys
os.chdir('/home/user/Futures01')
sys.path[:0] = ['/home/user/Futures01', 'workspace/studies', 'workspace/newstrats']
import toolkit as T
import ict_sweep as I
from futures_agents.config import get_contract
from futures_agents.schema import Direction


def simulate(bars, chains, atr, spec, stop_mult=1.0, tmul=(1.0, 2.5),
             time_stop=40, commission=True):
    out = []
    for c in chains:
        if c.entry_i is None or c.fvg_bot is None:
            continue
        m = c.entry_i
        a = atr[m]
        if not a or a <= 0:
            continue
        short = c.side is Direction.SHORT
        sign = -1 if short else 1
        entry = spec.round_to_tick(c.fvg_bot if short else c.fvg_top)
        # a limit resting at the edge only fills if the bar actually reached it
        if short and bars[m].high < entry:
            continue
        if (not short) and bars[m].low > entry:
            continue
        stop = spec.round_to_tick(entry - sign * stop_mult * a)
        risk = abs(entry - stop)
        if risk < spec.min_stop_ticks * spec.tick_size:
            continue
        tgts = [spec.round_to_tick(entry + sign * a * k) for k in tmul]
        filled = [False, False]
        realised = 0.0
        remaining = 1.0
        exit_r = None
        for j in range(m + 1, min(m + 1 + time_stop, len(bars))):
            b = bars[j]
            hit_stop = (b.high >= stop) if short else (b.low <= stop)
            if hit_stop:                                  # pessimistic: stop first
                realised += remaining * (-1.0)
                exit_r = realised
                break
            for k in (0, 1):
                if filled[k]:
                    continue
                if (b.low <= tgts[k]) if short else (b.high >= tgts[k]):
                    filled[k] = True
                    realised += 0.5 * (abs(tgts[k] - entry) / risk)
                    remaining -= 0.5
                    if k == 0:
                        stop = entry                      # breakeven after the first target
            if remaining <= 1e-9:
                exit_r = realised
                break
        if exit_r is None:
            j = min(m + time_stop, len(bars) - 1)
            realised += remaining * (sign * (bars[j].close - entry) / risk)
            exit_r = realised
        if commission:
            rd = risk * spec.point_value
            exit_r -= (2 * (spec.commission_per_side + spec.exchange_fee_per_side)) / max(rd, 1e-9)
        out.append(exit_r)
    return out


res = {}
for sym in ['MES', 'MNQ', 'MGC', 'MCL']:
    for tf in [15, 60, 240]:
        bars = list(T._series(sym, tf, None).bars)
        b = I.build(sym, tf, bars)
        rs = simulate(bars, b.chains, I._atr(bars), get_contract(sym))
        n = len(rs)
        if n < 5:
            continue
        exp = st.mean(rs)
        sd = st.pstdev(rs) or 1e-9
        res[f"{sym}|{tf}"] = {"n": n, "exp": round(exp, 4),
                              "win": round(sum(1 for x in rs if x > 0) / n, 4),
                              "t": round(exp / (sd / n ** 0.5), 3),
                              "total_r": round(sum(rs), 2)}
        print(sym, tf, res[f"{sym}|{tf}"], flush=True)

v = [r['exp'] for r in res.values()]
print(f"\nIDEALISED LIMIT AT THE IMBALANCE EDGE: {len(v)} cells, "
      f"median expectancy {st.median(v):+.4f}R, "
      f"{sum(1 for x in v if x > 0)}/{len(v)} cells positive, "
      f"{sum(r['n'] for r in res.values())} trades")
print("engine (market on next open, same chains): median -0.045R, 4/12 cells positive")
json.dump(res, open('/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad/limit.json', 'w'), indent=1)
