"""Worker 3 — recurring profitability ranking for NQ, ES, MZC, MZS, MZW at 60m/240m.

Placebo cohort: **worker 1's** ``workspace/newstrats/placebo.py`` (it appeared
~20 min into this run and is strictly better than the fallback that was being
written — it matches on RAW signal count rather than realised trades and draws
random entries from each base strategy's own *eligible* bars).  Worker 1's
``rank.py`` never appeared, so the ranking, census, replication and deflation
arithmetic below are this worker's own.

Reading the output:

* Ranking population = the combinator's generated rule sets for the cell,
  clone-collapsed on **realised trades**.
* Placebos inherit the base's exit model, ``StrategyFilters``, FILTER
  conditions, execution tf and confirm tfs; only the SIGNAL layer is replaced.
  Ranked in the same table, collapsed and floored identically.
* ``best_placebo_rank`` must be read against ``null_rank`` — with k placebos in
  N rows and no edge anywhere, E[best placebo rank] = (N+1)/(k+1).
* Costs: engine default CostModel.  Adverse slippage on entry and stop exits;
  target / time / session exits carry none, which understates costs.
"""
from __future__ import annotations

import json
import math
import os
import random
import statistics as st
import sys
from collections import defaultdict
from datetime import timedelta

sys.path.insert(0, "/home/user/Futures01")
os.chdir("/home/user/Futures01")

import workspace.studies.toolkit as T  # noqa: E402
import workspace.newstrats.placebo as P  # noqa: E402
from futures_agents.backtest.engine import run_portfolio  # noqa: E402
from futures_agents.backtest.metrics import compute_metrics  # noqa: E402
from futures_agents.data.bars import BarSeries  # noqa: E402
from futures_agents.features import build_symbol_frame  # noqa: E402
from futures_agents.scout import FRAMES  # noqa: E402
from futures_agents.strategies.base import ConditionKind  # noqa: E402
from futures_agents.strategies.combinator import generate_strategies  # noqa: E402

FLOOR = 20
SOFT = 10          # secondary floor, reported beside the primary one
BUDGET = 2400
OUTDIR = "workspace/strategy_research/cells"


# ------------------------------------------------------------------- rows
def row_of(s, trades, *, arm, parent=None):
    m = compute_metrics(trades)
    lo, hi = T.wilson(m.wins, m.trades)
    return dict(
        id=s.strategy_id, arm=arm, parent=parent, group=s.group, name=s.name,
        signals=[c.name for c in s.conditions if c.kind is ConditionKind.SIGNAL],
        filters=[c.name for c in s.conditions if c.kind is ConditionKind.FILTER],
        exec_tf=s.execution_tf, rth_only=bool(s.filters.rth_only),
        stop_kind=str(s.exit.stop_kind), stop_mult=s.exit.stop_mult,
        target_kind=str(s.exit.target_kind), anchor=list(s.exit.anchor_mult or ()),
        session_close=bool(s.exit.exit_at_session_close),
        n=m.trades, win=round(m.win_rate, 4), win_lo=round(lo, 4), win_hi=round(hi, 4),
        exp=round(m.expectancy_r, 4), avg_win=round(m.avg_win_r, 4),
        avg_loss=round(m.avg_loss_r, 4), rr=round(m.payoff_ratio, 4),
        pf=round(m.profit_factor, 4), maxdd=round(m.max_drawdown_r, 3),
        avgdd=round(m.avg_drawdown_r, 3), t=round(m.t_statistic, 3),
        sharpe=round(m.sharpe, 3), sortino=round(m.sortino, 3), sqn=round(m.sqn, 3),
        maxcw=m.max_consecutive_wins, maxcl=m.max_consecutive_losses,
        avg_min=round(m.avg_minutes_held, 1), avg_bars=round(m.avg_bars_held, 2),
        mfe=round(m.avg_mfe_r, 3), mae=round(m.avg_mae_r, 3),
        longs=m.long_trades, shorts=m.short_trades,
        long_exp=round(m.long_expectancy_r, 4), short_exp=round(m.short_expectancy_r, 4),
        total_r=round(m.total_r, 3), total_usd=round(m.total_dollars, 2),
        clones=1)


def collapse(pairs, floor):
    """pairs: [(strategy, trades, arm, parent)] -> clone-collapsed rows at ``floor``."""
    seen, out = {}, []
    for s, trades, arm, parent in pairs:
        if len(trades) < floor:
            continue
        fp = T.fingerprint(trades)
        if fp in seen:
            out[seen[fp]]["clones"] += 1
            continue
        seen[fp] = len(out)
        out.append(row_of(s, trades, arm=arm, parent=parent))
    return out


def breakdown(trades):
    def agg(key):
        d = defaultdict(list)
        for t in trades:
            d[key(t)].append(t.net_r)
        return {str(k): dict(n=len(v), exp=round(sum(v) / len(v), 4))
                for k, v in sorted(d.items(), key=lambda kv: str(kv[0]))
                if len(v) >= 3}
    return dict(session=agg(lambda t: t.session or "?"),
                regime=agg(lambda t: t.regime),
                vol=agg(lambda t: t.volatility),
                hour_utc=agg(lambda t: f"{t.entry_ts.hour:02d}"),
                dow=agg(lambda t: t.day_of_week),
                exit_reason=agg(lambda t: t.exit_reason.value))


def rank_table(real_pairs, plc_pairs, floor, n_generated):
    real = collapse(real_pairs, floor)
    plc = collapse(plc_pairs, floor)
    comb = sorted(real + plc, key=lambda r: (-r["exp"], -r["t"]))
    for i, r in enumerate(comb, 1):
        r["rank"] = i
    ft = T.free_t(n_generated + len(plc))
    for r in comb:
        r["clears_free_t"] = bool(r["t"] >= ft)
    pr = [r["rank"] for r in comb if r["arm"] != "real"]
    best = min(pr) if pr else None
    brow = next((r for r in comb if r["rank"] == best), None)
    return dict(
        floor=floor, n_real=len(real), n_placebo=len(plc), n_total=len(comb),
        placebo_share=round(len(plc) / max(1, len(comb)), 3),
        free_t=round(ft, 3),
        n_clearing_free_t=sum(1 for r in comb if r["clears_free_t"]),
        best_placebo_rank=best,
        best_placebo=({k: brow[k] for k in ("arm", "parent", "group", "n", "exp",
                                            "win", "rr", "t", "maxdd", "name")}
                      if brow else None),
        placebo_in_top10=bool(best is not None and best <= 10),
        placebo_ranks=sorted(pr)[:15],
        null_rank=P.null_rank_distribution(len(comb), len(plc)),
        rows=comb)


# ------------------------------------------------------------------- cell
def run_cell(sym, tf, window, *, budget=BUDGET, seed=1, verbose=True):
    series = T._series(sym, tf, window)
    if len(series) < 120:
        return dict(symbol=sym, tf=tf, window=window, bars=len(series),
                    skipped=f"{len(series)} bars < 120 minimum")
    frame = build_symbol_frame(series, FRAMES[tf])
    strats = [s for s in generate_strategies(
        sym, FRAMES[tf], groups=T.ALL_GROUPS, max_total=99999,
        max_per_template=budget, seed=seed) if s.primary_tf == tf]
    res = run_portfolio(frame, strats)
    by_id = {s.strategy_id: s for s in strats}
    realised = {sid: len(r.trades) for sid, r in res.items()}
    real_pairs = [(s, res[s.strategy_id].trades, "real", None) for s in strats]

    # ---- floor-free census, from the same single run ----
    with_tr = [(s, res[s.strategy_id].trades) for s in strats if res[s.strategy_id].trades]
    census = {}
    for f in (1, 5, 10, 20, 30, 50):
        rr = collapse([(s, tr, "real", None) for s, tr in with_tr], f)
        e = [r["exp"] for r in rr]
        census[str(f)] = dict(
            raw=sum(1 for _, tr in with_tr if len(tr) >= f), collapsed=len(rr),
            median_exp=round(st.median(e), 4) if e else None,
            pct_pos=round(sum(1 for x in e if x > 0) / len(e), 3) if e else None,
            best_exp=round(max(e), 4) if e else None,
            best_t=round(max(r["t"] for r in rr), 3) if rr else None,
            median_n=round(st.median([r["n"] for r in rr]), 1) if rr else None)

    # ---- placebo bases: strategies that actually entered a ranking ----
    soft_rows = sorted(collapse([(s, tr, "real", None) for s, tr in with_tr], 8),
                       key=lambda r: (-r["exp"], -r["t"]))
    n_f20 = census["20"]["collapsed"]
    n_bases = max(6, min(24, int(math.ceil(0.12 * n_f20 / 3.0))))
    rng = random.Random(f"w3:{sym}:{tf}:{window}")
    pool = [by_id[r["id"]] for r in soft_rows]
    bases = []
    if pool:
        top = pool[:max(1, n_bases // 3)]                 # hardest control
        rest = [p for p in pool if p not in top]
        bases = top + (rng.sample(rest, min(len(rest), n_bases - len(top)))
                       if rest else [])

    plc_pairs, pdiag, pmeta = [], {}, {}
    if bases:
        plc, pmeta, pdiag = P.build_cohort(frame, bases, realised,
                                           seed=abs(hash((sym, tf, window))) % 99991,
                                           pool="eligible")
        if plc:
            pres = run_portfolio(frame, plc)
            for s in plc:
                pmeta[s.strategy_id].realised = len(pres[s.strategy_id].trades)
            plc_pairs = [(s, pres[s.strategy_id].trades,
                          pmeta[s.strategy_id].kind, pmeta[s.strategy_id].base_id)
                         for s in plc]
            ratios = [m.schedule_vs_realised for m in pmeta.values()
                      if m.schedule_vs_realised is not None]
            pdiag["count_match_median"] = (round(st.median(ratios), 3) if ratios else None)

    primary = rank_table(real_pairs, plc_pairs, FLOOR, len(strats))
    secondary = rank_table(real_pairs, plc_pairs, SOFT, len(strats))

    lookup = dict(res)
    if plc_pairs:
        lookup.update(pres)
    for tbl in (primary, secondary):
        for r in tbl["rows"][:10]:
            r["breakdown"] = breakdown(lookup[r["id"]].trades)

    out = dict(
        symbol=sym, tf=tf, window=window, bars=len(series),
        span_days=(series.bars[-1].ts - series.bars[0].ts).days,
        first=str(series.bars[0].ts), last=str(series.bars[-1].ts),
        generated=len(strats), budget=budget, census=census,
        placebo_source="workspace/newstrats/placebo.py (worker 1)",
        placebo_diag=pdiag, n_bases=len(bases),
        primary=dict(primary, rows=primary["rows"][:60]),
        secondary=dict(secondary, rows=secondary["rows"][:40]),
        top25_ids=[r["id"] for r in primary["rows"][:25] if r["arm"] == "real"],
        soft_top25_ids=[r["id"] for r in secondary["rows"][:25] if r["arm"] == "real"],
    )
    if verbose:
        print(f"  {sym} {tf}m {window}d  gen={len(strats)} "
              f"f20: real={primary['n_real']} plc={primary['n_placebo']} "
              f"bestplc={primary['best_placebo_rank']}"
              f"(null E={primary['null_rank'].get('expected_best_rank')}) "
              f"clears={primary['n_clearing_free_t']}/{primary['n_total']} | "
              f"f10: real={secondary['n_real']} bestplc={secondary['best_placebo_rank']}",
              flush=True)
    return out


# ------------------------------------------------- disjoint-slice replication
def replicate(sym, tf, ids, *, budget=BUDGET, seed=1, n=3):
    slices = T.disjoint_slices(sym, tf, n)
    strats = [s for s in generate_strategies(
        sym, FRAMES[tf], groups=T.ALL_GROUPS, max_total=99999,
        max_per_template=budget, seed=seed) if s.primary_tf == tf]
    want = [s for s in strats if s.strategy_id in set(ids)]
    full = T._series(sym, tf, None)
    last = full.bars[-1].ts
    per = {}
    for a, b in slices:
        lo, hi = last - timedelta(days=a), last - timedelta(days=b)
        bars = [x for x in full.bars if lo <= x.ts < hi]
        key = f"{a}-{b}"
        if len(bars) < 120:
            per[key] = {"skipped": f"{len(bars)} bars"}
            continue
        fr = build_symbol_frame(BarSeries(sym, tf, bars), FRAMES[tf])
        r = run_portfolio(fr, want)
        per[key] = {"_bars": len(bars)}
        for s in want:
            m = compute_metrics(r[s.strategy_id].trades)
            per[key][s.strategy_id] = dict(n=m.trades, exp=round(m.expectancy_r, 4),
                                           t=round(m.t_statistic, 3),
                                           win=round(m.win_rate, 4))
    summary = {}
    for s in want:
        vals = [per[k].get(s.strategy_id) for k in per if "skipped" not in per[k]]
        vals = [v for v in vals if v]
        summary[s.strategy_id] = dict(
            slices_positive=sum(1 for v in vals if v["exp"] > 0),
            slices_run=len(vals),
            slices_with_trades=sum(1 for v in vals if v["n"] > 0),
            min_n=min((v["n"] for v in vals), default=0),
            exps=[v["exp"] for v in vals], ns=[v["n"] for v in vals])
    return dict(slices=[f"{a}-{b}" for a, b in slices], per_slice=per,
                summary=summary, n_requested=len(ids), n_found=len(want))


CAVEATS = [
    "Windows 30/90/180/274 are NESTED: a strategy in all four top-10 lists is ONE "
    "observation seen four times, not four confirmations.",
    "Placebo cohort built with worker 1's workspace/newstrats/placebo.py "
    "(placebo_random / placebo_shift / placebo_shuffle), ranked in the same table, "
    "clone-collapsed and floored identically. best_placebo_rank must be read against "
    "null_rank.expected_best_rank = (N+1)/(k+1).",
    "Per-cell statistics only. No pooled trade-level z, no pooling of per-strategy "
    "rows across cells, no T.ab (D28).",
    "NQ and ES are FULL-SIZE contracts. The R statistics transfer to the micros; the "
    "position sizing does not - on a $50k account one NQ is 10x one MNQ and one ES is "
    "10x one MES. NQ shares its price series with MNQ from a second fetch snapshot, so "
    "NQ/MNQ agreement is a consistency check, not evidence.",
    "MZC/MZS/MZW: 500-bushel CBOT micro grains, cents/bushel, tick 0.125 = $0.625, "
    "point value $5.00. Verified trade-by-trade against realised dollar P&L.",
    "Engine costs: commission + exchange fee both sides, adverse slippage on entry and "
    "on stop exits. Target / time / session-close exits carry NO slippage - costs are "
    "understated for target-heavy rule sets.",
    "D24: T.make_strategy defaults rth_only=False, but the combinator population used "
    "here contains both rth_only arms; each row carries its own rth_only flag.",
    "D21: at 240m the session-scale guard (390-minute threshold) never fires, so the "
    "time-of-day conditions misbehave at 240m. Any 240m row whose signals include a "
    "clock condition is suspect.",
]


def save_cell(cell):
    os.makedirs(OUTDIR, exist_ok=True)
    sym, tf, w = cell["symbol"], cell["tf"], cell["window"]
    sid = f"rank_nq_es_grains_{sym}_{tf}_{w}"
    with open(f"{OUTDIR}/{sid}.json", "w") as fh:
        json.dump(cell, fh, indent=1, default=str)
    if cell.get("skipped"):
        head = f"{sym} {tf}m {w}d not evaluable: {cell['skipped']}"
    else:
        p = cell["primary"]
        head = (f"best placebo ranked {p['best_placebo_rank']} of {p['n_total']} "
                f"(null expectation {p['null_rank'].get('expected_best_rank')}); "
                f"{p['n_clearing_free_t']} rows clear free_t={p['free_t']}")
    T.save(sid, f"{sym} {tf}m {w}d recurring profitability ranking",
           "Top 10 by expectancy in R, net of costs, with a matched placebo cohort "
           "ranked alongside; floor-free census beside the floored ranking.",
           cell, head, caveats=CAVEATS)
    return sid
