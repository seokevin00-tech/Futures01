"""Worker-2 ORB census: firing rates first, then the joint (win, payoff, exp) distribution."""
from __future__ import annotations

import json
import os
import statistics as st
import sys
from datetime import date

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/newstrats")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")

import orb_w2 as O  # noqa: E402

OUTDIR = "/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad"
os.makedirs(OUTDIR, exist_ok=True)

ENTRIES = ["break", "retest"]
STOPS = ["opp", "mid", "atr"]
TARGETS = ["rw1", "rw2", "r1", "r2", "r3"]
ORLENS = [5, 15, 30, 60]
TFS = [5, 15]

DEEP = ["MGC", "MES", "MNQ"]
RAW = ["MGC", "MES", "NQ", "MNQ", "MCL"]


def split_days(days, frac=0.6):
    days = sorted(days)
    k = int(len(days) * frac)
    return days[:k], days[k:]


def thirds(days):
    days = sorted(days)
    n = len(days) // 3
    return [days[:n], days[n:2 * n], days[2 * n:]]


def census(symbols, source):
    rows = []
    for sym in symbols:
        all_days = sorted(O.opening_ranges(sym, 15, source))
        is_days, oos_days = split_days(all_days, 0.6)
        sl = thirds(all_days)
        for tf in TFS:
            for orl in ORLENS:
                for en in ENTRIES:
                    for sp in STOPS:
                        for tg in TARGETS:
                            tr, dg = O.simulate(sym, tf, orl, en, sp, tg, source=source)
                            if not tr:
                                continue
                            m = O.metrics(tr)
                            days_set = {t.day for t in tr}
                            m_is = O.metrics([t for t in tr if t.day in set(is_days)])
                            m_oos = O.metrics([t for t in tr if t.day in set(oos_days)])
                            slices = [O.metrics([t for t in tr if t.day in set(s)])
                                      for s in sl]
                            rows.append(dict(
                                symbol=sym, source=source, tf=tf, orlen=orl,
                                entry=en, stop=sp, target=tg,
                                lag_min=max(0, tf - orl % tf if orl % tf else 0),
                                **dg, **m,
                                is_n=m_is.get("n", 0), is_exp=m_is.get("exp"),
                                is_win=m_is.get("win"), is_payoff=m_is.get("payoff"),
                                oos_n=m_oos.get("n", 0), oos_exp=m_oos.get("exp"),
                                oos_win=m_oos.get("win"), oos_payoff=m_oos.get("payoff"),
                                sl_exp=[s.get("exp") for s in slices],
                                sl_n=[s.get("n", 0) for s in slices],
                            ))
        print(f"  {sym}/{source} done ({len(rows)} rows so far)", flush=True)
    return rows


def firing_rates(symbols, source):
    out = []
    for sym in symbols:
        for tf in TFS:
            for orl in ORLENS:
                for en in ENTRIES:
                    _, dg = O.simulate(sym, tf, orl, en, "opp", "rw1", source=source)
                    out.append(dict(symbol=sym, source=source, tf=tf, orlen=orl,
                                    entry=en, **dg))
    return out


def mechanism(symbols, source):
    """Exit-free: after a break, how far does price run for vs against, in OR widths.

    No stop, no target, no floor. If a range break carried directional
    information, favourable excursion to the RTH close would exceed adverse
    excursion. This cannot be overfitted because there is nothing to fit.
    """
    out = []
    from futures_agents.config import get_contract
    from futures_agents.timeutil import to_et
    for sym in symbols:
        for orl in ORLENS:
            spec = get_contract(sym)
            ors = O.opening_ranges(sym, orl, source)
            tfb = 5
            bars = O.series(sym, tfb, source).bars
            dbars = O._rth_day_index(bars, spec.rth_open, spec.rth_close)
            mfes, maes, closes, widths = [], [], [], []
            nsess = nbreak = 0
            for d in sorted(set(ors) & set(dbars)):
                orr = ors[d]
                elig = [b for b in dbars[d] if to_et(b.ts) >= orr.end_ts]
                if len(elig) < 3:
                    continue
                nsess += 1
                sig = None
                for i, b in enumerate(elig[:-1]):
                    if b.close > orr.high:
                        sig, dr, edge = i, 1, orr.high
                        break
                    if b.close < orr.low:
                        sig, dr, edge = i, -1, orr.low
                        break
                if sig is None:
                    continue
                nbreak += 1
                fb = elig[sig + 1]
                e = fb.open
                rest = elig[sig + 1:]
                mfe = max((b.high - e) if dr > 0 else (e - b.low) for b in rest)
                mae = max((e - b.low) if dr > 0 else (b.high - e) for b in rest)
                w = orr.width
                mfes.append(mfe / w)
                maes.append(mae / w)
                closes.append(((rest[-1].close - e) * dr) / w)
                widths.append(w)
            if nbreak < 5:
                continue
            out.append(dict(
                symbol=sym, source=source, orlen=orl, sessions=nsess, breaks=nbreak,
                break_rate=round(nbreak / nsess, 4),
                med_mfe_w=round(st.median(mfes), 3), med_mae_w=round(st.median(maes), 3),
                mfe_mae=round(st.median(mfes) / st.median(maes), 3),
                med_close_w=round(st.median(closes), 4),
                mean_close_w=round(st.mean(closes), 4),
                pct_close_favourable=round(sum(1 for c in closes if c > 0) / len(closes), 3),
                t_close=round(st.mean(closes) / (st.pstdev(closes) / len(closes) ** 0.5), 3)
                if st.pstdev(closes) > 0 else 0.0,
            ))
    return out


if __name__ == "__main__":
    what = sys.argv[1] if len(sys.argv) > 1 else "all"
    if what in ("all", "fire"):
        fr = firing_rates(DEEP, "deep") + firing_rates(RAW, "raw")
        json.dump(fr, open(f"{OUTDIR}/orb_firing.json", "w"), indent=1, default=str)
        print("firing rates written", len(fr))
    if what in ("all", "mech"):
        mk = mechanism(DEEP, "deep") + mechanism(RAW, "raw")
        json.dump(mk, open(f"{OUTDIR}/orb_mech.json", "w"), indent=1, default=str)
        print("mechanism written", len(mk))
    if what in ("all", "census"):
        rows = census(DEEP, "deep") + census(RAW, "raw")
        json.dump(rows, open(f"{OUTDIR}/orb_census.json", "w"), indent=1, default=str)
        print("census written", len(rows))
