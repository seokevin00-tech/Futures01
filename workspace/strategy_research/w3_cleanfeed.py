"""Does the micro-grain data defect drive the micro-grain rankings?

Found while auditing: the MZC/MZS/MZW 60m series carry 12-26% flat bars
(high == low, a single print), 9-11% zero-volume bars, and 98-367 close-to-open
gaps larger than 2% of price, of which ~half reverse on the very next bar. On
corn the excursion is ~16 cents, which is the size of a CBOT calendar spread -
consistent with the continuous series splicing quotes from more than one
contract month on hours when the front month did not trade. NQ and ES have zero
flat bars and zero 2% gaps.

This arm rebuilds the cell on a cleaned series - zero-volume and single-print
bars dropped - and re-ranks. The comparison is paired on the RULE SET (same
generated population, same seed, same window), so the only thing that differs is
the bar stream.
"""
from __future__ import annotations

import json
import os
import statistics as st
import sys

sys.path.insert(0, "/home/user/Futures01")
os.chdir("/home/user/Futures01")

import workspace.strategy_research.w3_rank as W  # noqa: E402
import workspace.studies.toolkit as T  # noqa: E402
from futures_agents.backtest.engine import run_portfolio  # noqa: E402
from futures_agents.data.bars import BarSeries  # noqa: E402
from futures_agents.features import build_symbol_frame  # noqa: E402
from futures_agents.scout import FRAMES  # noqa: E402
from futures_agents.strategies.combinator import generate_strategies  # noqa: E402


def clean_bars(bars):
    return [b for b in bars if b.volume > 0 and b.high > b.low]


def cell(sym, tf, window, budget=W.BUDGET, seed=1):
    raw = T._series(sym, tf, window)
    cln = BarSeries(sym, tf, clean_bars(raw.bars))
    strats = [s for s in generate_strategies(
        sym, FRAMES[tf], groups=T.ALL_GROUPS, max_total=99999,
        max_per_template=budget, seed=seed) if s.primary_tf == tf]
    out = {}
    tops = {}
    for lbl, ser in (("raw", raw), ("clean", cln)):
        if len(ser) < 120:
            out[lbl] = {"skipped": f"{len(ser)} bars"}
            continue
        res = run_portfolio(build_symbol_frame(ser, FRAMES[tf]), strats)
        rows = W.collapse([(s, res[s.strategy_id].trades, "real", None) for s in strats], 20)
        rows.sort(key=lambda r: (-r["exp"], -r["t"]))
        allr = W.collapse([(s, res[s.strategy_id].trades, "real", None) for s in strats], 1)
        e = [r["exp"] for r in allr]
        out[lbl] = dict(
            bars=len(ser), qualifying=len(rows),
            median_exp_floor_free=round(st.median(e), 4) if e else None,
            pct_positive_floor_free=round(sum(1 for x in e if x > 0) / len(e), 3) if e else None,
            median_exp_f20=round(st.median([r["exp"] for r in rows]), 4) if rows else None,
            pct_positive_f20=round(sum(1 for r in rows if r["exp"] > 0) / len(rows), 3) if rows else None,
            best_exp=rows[0]["exp"] if rows else None,
            best_t=max((r["t"] for r in rows), default=None),
            top10=[dict(rank=i + 1, group=r["group"], n=r["n"], exp=r["exp"],
                        t=r["t"], name=r["name"]) for i, r in enumerate(rows[:10])])
        tops[lbl] = [r["id"] for r in rows[:10]]
    if "raw" in tops and "clean" in tops:
        out["top10_overlap"] = len(set(tops["raw"]) & set(tops["clean"]))
    print(f"  cleanfeed {sym} {tf}m {window}d: bars {out['raw'].get('bars')}->"
          f"{out['clean'].get('bars')} qualifying {out['raw'].get('qualifying')}->"
          f"{out['clean'].get('qualifying')} medExp(f1) "
          f"{out['raw'].get('median_exp_floor_free')}->{out['clean'].get('median_exp_floor_free')} "
          f"bestExp {out['raw'].get('best_exp')}->{out['clean'].get('best_exp')} "
          f"top10 overlap {out.get('top10_overlap')}", flush=True)
    return out


if __name__ == "__main__":
    res = {}
    for sym in (sys.argv[1:] or ["MZC", "MZS", "MZW", "NQ", "ES"]):
        for tf in (60, 240):
            for w in (274,):
                try:
                    res[f"{sym}_{tf}_{w}"] = cell(sym, tf, w)
                except Exception as exc:  # noqa: BLE001
                    import traceback
                    traceback.print_exc()
                    res[f"{sym}_{tf}_{w}"] = {"error": str(exc)}
    tag = "_".join(sys.argv[1:]) or "all"
    with open(f"{W.OUTDIR}/audit_cleanfeed_{tag}.json", "w") as fh:
        json.dump({"cleanfeed": res}, fh, indent=1, default=str)
    T.save(f"rank_nq_es_grains_cleanfeed_{tag}",
           "Micro-grain series defect: does it drive the micro-grain ranking?",
           "Re-rank the 274-day cell on a series with zero-volume and single-print "
           "bars removed, paired on the identical generated rule sets.",
           {"cleanfeed": res},
           "The micro-grain bar stream contains single prints and ~2-5% reverting "
           "excursions the index complex does not have.",
           caveats=W.CAVEATS)
    print("saved")
