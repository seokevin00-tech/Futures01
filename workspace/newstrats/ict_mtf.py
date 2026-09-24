"""Multi-timeframe: higher-timeframe liquidity, lower-timeframe entry.

This is the one multi-timeframe claim ICT actually makes, and it is not the one the library
already refuted.  ``mtf_aligned`` is a directional VOTE across timeframes and it is the
library's strongest negative condition.  What ICT says is different and more specific: the
liquidity pool worth running lives on the higher timeframe, and the structure shift and entry
happen on the lower one.  So the 240m chart's confirmed swing highs and lows are handed to the
60m chain as extra pools, and the 15m chain gets the 60m chart's.

Levels cross timeframes by TIMESTAMP, never by index, and a higher-timeframe swing enters the
lower chart at the first bar whose timestamp is at or after the swing's CONFIRMATION bar close
- not its formation.  Getting that wrong imports the higher timeframe's future.

Three arms, matched: lower-timeframe pools only (the base study), higher-timeframe pools only,
and both.  Plus the alignment question the structure brief flags - does requiring the higher
timeframe to AGREE help, and does requiring it to DISAGREE help more?
"""
import json, math, os, statistics as st, sys
os.chdir('/home/user/Futures01')
sys.path[:0] = ['/home/user/Futures01', 'workspace/studies', 'workspace/newstrats']
import toolkit as T
import ict_sweep as I
from ict_perf import EXITS, row
from futures_agents.backtest.engine import run_portfolio
from futures_agents.features import build_symbol_frame
from futures_agents.scout import FRAMES

ALL8 = ("PDH", "PDL", "ONH", "ONL", "SESH", "SESL", "SWH", "SWL")
PAIRS = [(60, 240), (15, 60)]


def htf_levels(sym, ltf, htf, ltf_bars):
    """Per-LTF-bar dict of the HTF's most recent CONFIRMED swing high and low."""
    hb = list(T._series(sym, htf, None).bars)
    sw = sorted(I.find_swings(hb, 3, 3), key=lambda s: s.confirmed_index)
    # a swing is knowable on the lower chart only once the HTF confirming bar has CLOSED
    ev = [(hb[s.confirmed_index].ts + (hb[1].ts - hb[0].ts), s) for s in sw
          if s.confirmed_index < len(hb)]
    ev.sort(key=lambda x: x[0])
    out, k, hi, lo = [], 0, None, None
    for b in ltf_bars:
        while k < len(ev) and ev[k][0] <= b.ts:
            s = ev[k][1]
            if s.is_high:
                hi = s.price
            else:
                lo = s.price
            k += 1
        out.append({"HTFH": hi, "HTFL": lo})
    return out


res = {}
for sym in ['MES', 'MNQ', 'MGC', 'MCL']:
    for ltf, htf in PAIRS:
        series = T._series(sym, ltf, None)
        frame = build_symbol_frame(series, FRAMES[ltf])
        n = len(series.bars)
        ex = htf_levels(sym, ltf, htf, series.bars)
        I.clear()
        variants = {
            "ltf_pools": (I.Cfg(pools=ALL8), None),
            "htf_pools": (I.Cfg(pools=("HTFH", "HTFL")), ex),
            "both_pools": (I.Cfg(pools=ALL8 + ("HTFH", "HTFL")), ex)}
        strats, meta = [], {}
        for lab, (cfg, xl) in variants.items():
            b = I.build(sym, ltf, series.bars, cfg, extra_levels=xl)
            c = I.register_variant(b, "@" + lab)
            s = T.make_strategy(sym, ltf, [I.get("full@" + lab)], group="ICT",
                                name=f"full@{lab}", exit_model=EXITS["anchored"])
            strats.append(s)
            meta[s.strategy_id] = (lab, c["full"], b.census["s4_retraced_entry"])
        for w, (s0, s1) in {"full": (250, n), "IS": (250, int(n * .6)),
                            "OOS": (int(n * .6), n)}.items():
            r = run_portfolio(frame, strats, start=s0, end=s1)
            for sid, (lab, nsig, ncen) in meta.items():
                d = row(r[sid].trades); d.pop('rs')
                d.update(variant=lab, window=w, symbol=sym, tf=ltf, htf=htf,
                         signal_bars=nsig, census_entries=ncen)
                res[f"{sym}|{ltf}<{htf}|{lab}|{w}"] = d
        print(sym, ltf, '<', htf,
              {l: (meta[s][2], res[f'{sym}|{ltf}<{htf}|{l}|full']['n'],
                   res[f'{sym}|{ltf}<{htf}|{l}|full']['exp'])
               for s, (l, _, _) in meta.items()}, flush=True)

json.dump(res, open('/tmp/claude-0/-home-user-Futures01/40939d92-faf6-5f2f-9d25-d7e403702dd3/scratchpad/mtf.json', 'w'), indent=1, default=str)

def sgn(v):
    v = [x for x in v if x != 0]; k = sum(1 for x in v if x > 0); n = len(v)
    return round((k - n / 2) / math.sqrt(n / 4), 3) if n else None
print()
for w in ('full', 'IS', 'OOS'):
    for lab in ('ltf_pools', 'htf_pools', 'both_pools'):
        v = [res[k]['exp'] for k in res if k.endswith(f"|{lab}|{w}") and res[k]['n'] >= 8]
        nt = sum(res[k]['n'] for k in res if k.endswith(f"|{lab}|{w}") and res[k]['n'] >= 8)
        print(f"{w:5s} {lab:11s} cells {len(v):2d}  trades {nt:4d}  median exp "
              f"{st.median(v):+.4f}  sign z {sgn(v)}")
    d = [res[f"{s}|{l}<{h}|htf_pools|{w}"]['exp'] - res[f"{s}|{l}<{h}|ltf_pools|{w}"]['exp']
         for s in ['MES', 'MNQ', 'MGC', 'MCL'] for l, h in PAIRS
         if res[f"{s}|{l}<{h}|htf_pools|{w}"]['n'] >= 8]
    print(f"      htf - ltf: median delta {st.median(d):+.4f}  sign z {sgn(d)}  n_cells {len(d)}")
