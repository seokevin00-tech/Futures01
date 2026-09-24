"""Out-of-sample split and anchored walk-forward for worker 2's cells.

One population run over the full 274-day window per (symbol, timeframe); trades
are then partitioned by ENTRY timestamp into folds. Rule sets are fixed - there
is nothing to re-fit - so the only thing walk-forward tests here is the
SELECTION: does picking the top of an in-sample ranking buy anything out of
sample. The placebo cohort is carried through the identical procedure, so the
answer has a control.
"""
from __future__ import annotations

import json
import math
import statistics as st
import sys
from collections import defaultdict
from typing import Dict, List, Optional, Sequence

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")
sys.path.insert(0, "/home/user/Futures01/workspace/strategy_research/w2")

import toolkit as T  # noqa: E402
import w2rank as W  # noqa: E402
from futures_agents.backtest.engine import run_portfolio  # noqa: E402
from futures_agents.backtest.metrics import compute_metrics  # noqa: E402
from futures_agents.features import build_symbol_frame  # noqa: E402
from futures_agents.scout import FRAMES  # noqa: E402

CELLS = "/home/user/Futures01/workspace/strategy_research/w2/cells"


def _stats(trades) -> dict:
    if not trades:
        return {"n": 0, "exp": 0.0, "t": 0.0}
    m = compute_metrics(trades)
    return {"n": m.trades, "exp": round(m.expectancy_r, 4), "t": round(m.t_statistic, 3),
            "win": round(m.win_rate, 4), "pf": round(m.profit_factor, 3),
            "maxdd": round(m.max_drawdown_r, 3)}


def _score_from(trades) -> float:
    if len(trades) < 5:
        return -9.0
    m = compute_metrics(trades)
    n = m.trades
    return (m.expectancy_r * (n / (n + 30.0))
            * (1.0 / (1.0 + max(0.0, m.max_drawdown_r) / 8.0))
            + 0.05 * max(-2.0, min(2.0, m.t_statistic)) / 4.0)


def run(symbol: str, tf: int, budget: int = 8000, folds: int = 5,
        floor_is: int = 15, top_k: int = 10) -> dict:
    ser = T._series(symbol, tf, 274)
    frame = build_symbol_frame(ser, FRAMES[tf])
    S = W.population(symbol, tf, budget, seed=1)
    res = run_portfolio(frame, S)
    traded = [s for s in S if res[s.strategy_id].trades]
    want = max(1, int(math.ceil(0.10 * len(S) / len(W.FLAVOURS))))
    hosts = W.sample_hosts(traded, want, 1)
    sigs = W.signal_series(frame, hosts, tf)
    P = [p for p in W.build_placebos(hosts, sigs, len(ser.bars), 1)
         if p.strategy_id not in {x.strategy_id for x in S}]
    resp = run_portfolio(frame, P) if P else {}

    book: Dict[str, dict] = {}
    for s in S:
        book[s.strategy_id] = {"arm": "real", "group": s.group,
                               "trades": res[s.strategy_id].trades}
    for p in P:
        book[p.strategy_id] = {"arm": p.group.lower(), "group": p.group,
                               "trades": resp[p.strategy_id].trades}

    bars = ser.bars
    edges = [bars[int(len(bars) * k / folds)].ts for k in range(folds)] + [bars[-1].ts]

    def slice_trades(tr, lo, hi):
        return [t for t in tr if lo <= t.entry_ts < hi]

    # ---- 60/40 holdout -------------------------------------------------
    cut = bars[int(len(bars) * 0.6)].ts
    hold = []
    seen_fp = set()
    for sid, b in book.items():
        ins = slice_trades(b["trades"], bars[0].ts, cut)
        oos = slice_trades(b["trades"], cut, bars[-1].ts)
        if len(ins) < floor_is:
            continue
        # Clone collapse on the IN-SAMPLE trades - the selection is made there,
        # so that is where duplicates would fill the top 10 with one rule set.
        fp = T.fingerprint(ins)
        if fp in seen_fp:
            continue
        seen_fp.add(fp)
        hold.append({"id": sid, "arm": b["arm"], "group": b["group"],
                     "is": _stats(ins), "oos": _stats(oos),
                     "is_score": round(_score_from(ins), 5)})
    hold.sort(key=lambda r: -r["is_score"])
    sel = hold[:top_k]
    oos_ns = [r for r in sel if r["oos"]["n"] >= 5]
    # Base rate: the fraction of ALL eligible rows that are OOS-positive. The
    # selected top 10 has to beat THIS, not 50% - costs push the median rule
    # set's expectancy below zero, so a coin-flip benchmark would flatter the
    # selection.
    base_pool = [r for r in hold if r["oos"]["n"] >= 5]
    base_real = [r for r in base_pool if r["arm"] == "real"]
    holdout = {
        "cut": str(cut), "eligible": len(hold),
        "base_rate_oos_positive_all_eligible":
            round(sum(1 for r in base_pool if r["oos"]["exp"] > 0) / len(base_pool), 4)
            if base_pool else None,
        "base_rate_oos_positive_real_only":
            round(sum(1 for r in base_real if r["oos"]["exp"] > 0) / len(base_real), 4)
            if base_real else None,
        "base_pool": len(base_pool),
        "n_placebo_eligible": sum(1 for r in hold if r["arm"] != "real"),
        "selected_top10": [{k: r[k] for k in ("id", "arm", "group", "is", "oos", "is_score")}
                           for r in sel],
        "n_placebo_in_selected_top10": sum(1 for r in sel if r["arm"] != "real"),
        "mean_is_exp_selected": round(st.mean([r["is"]["exp"] for r in sel]), 4) if sel else None,
        "mean_oos_exp_selected": round(st.mean([r["oos"]["exp"] for r in oos_ns]), 4) if oos_ns else None,
        "n_selected_positive_oos": sum(1 for r in oos_ns if r["oos"]["exp"] > 0),
        "n_selected_with_oos_trades": len(oos_ns),
        "median_oos_exp_all_real": round(st.median(
            [r["oos"]["exp"] for r in hold if r["arm"] == "real" and r["oos"]["n"] >= 5]), 4)
            if any(r["arm"] == "real" and r["oos"]["n"] >= 5 for r in hold) else None,
        "is_oos_rank_corr": _spearman([r["is_score"] for r in hold if r["oos"]["n"] >= 5],
                                      [r["oos"]["exp"] for r in hold if r["oos"]["n"] >= 5]),
    }

    # ---- anchored walk-forward ----------------------------------------
    wf_rows = []
    for k in range(1, folds):
        lo_tr, hi_tr = bars[0].ts, edges[k]
        lo_te, hi_te = edges[k], edges[k + 1]
        cand = []
        fps = set()
        for sid, b in book.items():
            tr = slice_trades(b["trades"], lo_tr, hi_tr)
            if len(tr) < floor_is:
                continue
            fp = T.fingerprint(tr)
            if fp in fps:
                continue
            fps.add(fp)
            cand.append((sid, b["arm"], b["group"], _score_from(tr), tr))
        cand.sort(key=lambda x: -x[3])
        picked = cand[:top_k]
        oos = []
        for sid, arm, grp, sc, tr in picked:
            te = slice_trades(book[sid]["trades"], lo_te, hi_te)
            oos.append({"id": sid, "arm": arm, "group": grp,
                        "is_score": round(sc, 5), "oos": _stats(te)})
        with_tr = [r for r in oos if r["oos"]["n"] > 0]
        # Fold base rate over every candidate, not just the ten selected.
        pool = []
        for sid, arm, grp, sc, tr in cand:
            te = slice_trades(book[sid]["trades"], lo_te, hi_te)
            if len(te) >= 3:
                pool.append(compute_metrics(te).expectancy_r)
        wf_rows.append({
            "fold_base_rate_oos_positive": round(
                sum(1 for e in pool if e > 0) / len(pool), 4) if pool else None,
            "fold_base_pool": len(pool),
            "fold_base_median_oos_exp": round(st.median(pool), 4) if pool else None,
            "fold": k, "train_end": str(hi_tr), "test": [str(lo_te), str(hi_te)],
            "candidates": len(cand),
            "n_placebo_candidates": sum(1 for c in cand if c[1] != "real"),
            "n_placebo_selected": sum(1 for r in oos if r["arm"] != "real"),
            "selected": oos,
            "oos_trades": sum(r["oos"]["n"] for r in oos),
            "oos_mean_exp": round(st.mean([r["oos"]["exp"] for r in with_tr]), 4) if with_tr else None,
            "n_positive_oos": sum(1 for r in with_tr if r["oos"]["exp"] > 0),
            "n_with_oos_trades": len(with_tr),
        })
    allpos = sum(r["n_positive_oos"] for r in wf_rows)
    alln = sum(r["n_with_oos_trades"] for r in wf_rows)
    out = {"symbol": symbol, "tf": tf, "bars": len(bars), "n_real": len(S),
           "n_placebo": len(P), "folds": folds, "top_k": top_k,
           "holdout_60_40": holdout, "walk_forward": wf_rows,
           "wf_summary": {"selections": alln, "positive_oos": allpos,
                          "pct_positive": round(allpos / alln, 3) if alln else None,
                          "sign_test": W.sign_test(
                              [1.0 if r["oos"]["exp"] > 0 else -1.0
                               for f in wf_rows for r in f["selected"] if r["oos"]["n"] > 0])}}
    with open(f"{CELLS}/wf_{symbol}_{tf}.json", "w") as fh:
        json.dump(out, fh, default=str)
    return out


def _spearman(a: Sequence[float], b: Sequence[float]) -> Optional[float]:
    if len(a) < 8:
        return None
    def rank(v):
        order = sorted(range(len(v)), key=lambda i: v[i])
        r = [0.0] * len(v)
        for pos, i in enumerate(order):
            r[i] = pos + 1
        return r
    return round(T.corr(rank(list(a)), rank(list(b))), 4)


if __name__ == "__main__":
    r = run(sys.argv[1], int(sys.argv[2]))
    print(json.dumps({k: v for k, v in r.items() if k != "walk_forward"}, default=str)[:3000])
