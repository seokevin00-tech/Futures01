"""D24 arm: the same rule sets with the RTH gate flipped off.

Every strategy the combinator generates carries ``rth_only=True`` (verified:
1252/1252 at 60m and 1939/1939 at 240m). So the entire ranking in
``strategy_rankings.json`` is an RTH-only ranking, and at 240m an RTH-only 240m
bar stream is roughly one bar per trading day.

This is a PAIRED comparison - identical rule sets, identical bars, one flag
changed - so it is the right shape of test under D28 (no ``T.ab`` across
correlated variants) and the statistic is computed per cell and never pooled.
"""
from __future__ import annotations

import dataclasses
import json
import math
import os
import statistics as st
import sys

sys.path.insert(0, "/home/user/Futures01")
os.chdir("/home/user/Futures01")

import workspace.newstrats.placebo as P  # noqa: E402
import workspace.strategy_research.w3_rank as W  # noqa: E402
import workspace.studies.toolkit as T  # noqa: E402
from futures_agents.backtest.engine import run_portfolio  # noqa: E402
from futures_agents.backtest.metrics import compute_metrics  # noqa: E402
from futures_agents.features import build_symbol_frame  # noqa: E402
from futures_agents.scout import FRAMES  # noqa: E402
from futures_agents.strategies.base import StrategyFilters  # noqa: E402
from futures_agents.strategies.combinator import generate_strategies  # noqa: E402

SYMS = ["MZC", "MZS", "MZW", "NQ", "ES"]


def wilcoxon_sign(deltas):
    """Exact-ish two-sided sign test on paired deltas (ties dropped)."""
    d = [x for x in deltas if abs(x) > 1e-9]
    if not d:
        return dict(n=0, pos=0, p=None, z=None)
    pos = sum(1 for x in d if x > 0)
    n = len(d)
    z = (pos - n / 2) / math.sqrt(n / 4)
    tail = sum(math.comb(n, k) for k in range(0, min(pos, n - pos) + 1)) / 2 ** n
    return dict(n=n, pos=pos, p=round(min(1.0, 2 * tail), 6), z=round(z, 3))


def cell(sym, tf, window, budget=W.BUDGET, seed=1):
    series = T._series(sym, tf, window)
    if len(series) < 120:
        return {"symbol": sym, "tf": tf, "window": window,
                "skipped": f"{len(series)} bars"}
    frame = build_symbol_frame(series, FRAMES[tf])
    base = [s for s in generate_strategies(
        sym, FRAMES[tf], groups=T.ALL_GROUPS, max_total=99999,
        max_per_template=budget, seed=seed) if s.primary_tf == tf]
    off = [dataclasses.replace(
        s, filters=dataclasses.replace(s.filters, rth_only=False), _id=None)
        for s in base]
    ron = run_portfolio(frame, base)
    roff = run_portfolio(frame, off)

    pairs = []
    for a, b in zip(base, off):
        ta, tb = ron[a.strategy_id].trades, roff[b.strategy_id].trades
        pairs.append((len(ta), len(tb),
                      compute_metrics(ta).expectancy_r if ta else None,
                      compute_metrics(tb).expectancy_r if tb else None))
    n_on = sum(p[0] for p in pairs)
    n_off = sum(p[1] for p in pairs)
    both = [(p[2], p[3]) for p in pairs if p[0] >= 20 and p[1] >= 20]
    d_exp = [b - a for a, b in both]

    # a full ranking + placebo cohort in the rth_only=False population
    off_rows = W.collapse([(s, roff[s.strategy_id].trades, "real", None) for s in off], 20)
    off_rows.sort(key=lambda r: (-r["exp"], -r["t"]))
    by_id = {s.strategy_id: s for s in off}
    soft = sorted(W.collapse([(s, roff[s.strategy_id].trades, "real", None) for s in off], 8),
                  key=lambda r: (-r["exp"], -r["t"]))
    import random
    rng = random.Random(f"rth:{sym}:{tf}:{window}")
    pool = [by_id[r["id"]] for r in soft]
    n_bases = max(6, min(24, int(math.ceil(0.12 * len(off_rows) / 3.0))))
    bases = []
    if pool:
        top = pool[:max(1, n_bases // 3)]
        rest = [p for p in pool if p not in top]
        bases = top + (rng.sample(rest, min(len(rest), n_bases - len(top))) if rest else [])
    plc_pairs = []
    if bases:
        realised = {sid: len(r.trades) for sid, r in roff.items()}
        plc, meta, diag = P.build_cohort(frame, bases, realised,
                                         seed=abs(hash((sym, tf, window, "rth"))) % 99991,
                                         pool="eligible")
        if plc:
            pres = run_portfolio(frame, plc)
            plc_pairs = [(s, pres[s.strategy_id].trades, meta[s.strategy_id].kind,
                          meta[s.strategy_id].base_id) for s in plc]
    tbl = W.rank_table([(s, roff[s.strategy_id].trades, "real", None) for s in off],
                       plc_pairs, 20, len(off))

    out = dict(
        symbol=sym, tf=tf, window=window, bars=len(series), rule_sets=len(base),
        trades_rth_on=n_on, trades_rth_off=n_off,
        population_multiplier=round(n_off / max(1, n_on), 2),
        rule_sets_reaching_20_on=sum(1 for p in pairs if p[0] >= 20),
        rule_sets_reaching_20_off=sum(1 for p in pairs if p[1] >= 20),
        paired_both_qualify=len(both),
        median_exp_on=round(st.median([a for a, _ in both]), 4) if both else None,
        median_exp_off=round(st.median([b for _, b in both]), 4) if both else None,
        median_delta=round(st.median(d_exp), 4) if d_exp else None,
        sign_test=wilcoxon_sign(d_exp),
        rth_off_ranking=dict(
            n_real=tbl["n_real"], n_placebo=tbl["n_placebo"],
            best_placebo_rank=tbl["best_placebo_rank"],
            null_expected=tbl["null_rank"].get("expected_best_rank"),
            free_t=tbl["free_t"], n_clearing_free_t=tbl["n_clearing_free_t"],
            top10=[{k: r.get(k) for k in
                    ("rank", "arm", "group", "name", "n", "win", "exp", "rr", "pf",
                     "maxdd", "t", "sortino", "maxcl", "avg_min", "mfe", "mae")}
                   for r in tbl["rows"][:10]]),
    )
    print(f"  rth {sym} {tf}m {window}d: trades {n_on}->{n_off} ({out['population_multiplier']}x) "
          f"qualifying {out['rule_sets_reaching_20_on']}->{out['rule_sets_reaching_20_off']} "
          f"med exp {out['median_exp_on']}->{out['median_exp_off']} "
          f"sign p={out['sign_test']['p']} | bestplc={tbl['best_placebo_rank']} "
          f"(null {tbl['null_rank'].get('expected_best_rank')})", flush=True)
    return out


if __name__ == "__main__":
    syms = sys.argv[1:] or SYMS
    res = {}
    for sym in syms:
        for tf in (240, 60):
            for w in (274, 180):
                try:
                    res[f"{sym}_{tf}_{w}"] = cell(sym, tf, w)
                except Exception as exc:  # noqa: BLE001
                    import traceback
                    traceback.print_exc()
                    res[f"{sym}_{tf}_{w}"] = {"error": f"{type(exc).__name__}: {exc}"}
    os.makedirs(W.OUTDIR, exist_ok=True)
    tag = "_".join(syms)
    with open(f"{W.OUTDIR}/rth_arm_{tag}.json", "w") as fh:
        json.dump(res, fh, indent=1, default=str)
    T.save(f"rank_nq_es_grains_rth_arm_{tag}",
           "D24 paired arm: the same rule sets with rth_only flipped off",
           "Every combinator rule set carries rth_only=True. How much population, and "
           "how much measured edge, does that cost at 60m and 240m on NQ, ES and the "
           "micro grains?",
           res,
           "Paired per-cell sign test on identical rule sets over identical bars; "
           "no pooling across cells.",
           caveats=W.CAVEATS)
    print("saved", f"{W.OUTDIR}/rth_arm_{tag}.json")
