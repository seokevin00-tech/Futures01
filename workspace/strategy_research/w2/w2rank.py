"""Worker 2 ranking harness: MES + MNQ, 60m and 240m.

Self-contained because workspace/newstrats/placebo.py and rank.py did not exist
when this run started. Everything here is built from the same library machinery
the rest of the programme uses (combinator population, library engine, library
cost model), so a placebo row and a real row differ ONLY in the entry signal.

Design notes that matter for reading the output:

* ``rth_only`` is forced OFF on every generated strategy. Every combinator
  template ships ``StrategyFilters(rth_only=True)``, which is D24 - the
  library-wide 4h population killer. Measured here on MES 240m/90d: 1 rule set
  reaching 20 trades with it on, 18 with it off. The engine charges thin-book
  slippage on non-RTH bars automatically, so the overnight population is costed
  rather than assumed free. A paired RTH arm is run separately to quantify it.

* Placebos keep the host's FILTER conditions, exit model, scope filters,
  execution timeframe and confirm timeframes. Only the SIGNAL layer is
  replaced. Three flavours:
    - ``random``: uniform random bars, signal count and long/short mix matched
      exactly to the host's own realised signal series;
    - ``shift5``: every host signal displaced +5 bars (same count, same mix,
      same spacing);
    - ``rotate``: the host signal sequence circularly rotated by a random
      offset (preserves clustering and time-of-day distribution exactly,
      destroys the alignment to price). This is the "shuffled timestamps"
      flavour; a literal direction permutation is degenerate for the many rule
      sets whose realised signals are all one direction.

* Clones are collapsed on realised trades ACROSS the combined real+placebo
  population, so a placebo that happens to reproduce a real trade set cannot be
  double counted, and the first-seen row keeps the clone tally.
"""
from __future__ import annotations

import dataclasses as dc
import hashlib
import json
import math
import os
import random
import statistics as st
import sys
from collections import Counter, defaultdict
from datetime import timedelta
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")

import toolkit as T  # noqa: E402
from futures_agents.backtest.engine import run_portfolio  # noqa: E402
from futures_agents.backtest.metrics import compute_metrics  # noqa: E402
from futures_agents.data.bars import BarSeries  # noqa: E402
from futures_agents.features import build_symbol_frame  # noqa: E402
from futures_agents.scout import FRAMES  # noqa: E402
from futures_agents.schema import Direction  # noqa: E402
from futures_agents.strategies.base import (Condition, ConditionKind,  # noqa: E402
                                            ConditionResult, Strategy)
from futures_agents.strategies.combinator import generate_strategies  # noqa: E402

OUTDIR = "/home/user/Futures01/workspace/strategy_research/w2/cells"
FLAVOURS = ("random", "shift5", "rotate")


# ---------------------------------------------------------------- population
def population(symbol: str, tf: int, budget: int, seed: int = 1,
               rth: bool = False) -> List[Strategy]:
    S = [x for x in generate_strategies(
        symbol, FRAMES[tf], max_total=999999, max_per_template=budget, seed=seed)
        if x.primary_tf == tf]
    if not rth:
        S = [dc.replace(x, filters=dc.replace(x.filters, rth_only=False), _id=None)
             for x in S]
    return S


def frame_for(symbol: str, tf: int, window: Optional[int] = None,
              series: Optional[BarSeries] = None):
    ser = series if series is not None else T._series(symbol, tf, window)
    return ser, build_symbol_frame(ser, FRAMES[tf])


# ------------------------------------------------------------ signal replay
def signal_series(frame, strategies: Sequence[Strategy], tf: int
                  ) -> Dict[str, Dict[int, Direction]]:
    """host strategy_id -> {base bar index: composite SIGNAL direction}.

    Replays only the SIGNAL layer (filters and exits are left to the engine, so
    the placebo inherits them unchanged). Distinct (condition, timeframe) pairs
    are evaluated once per bar and shared, exactly as the engine does.
    """
    conds: Dict[Tuple[str, int], Condition] = {}
    for s in strategies:
        for c in s.signal_conditions:
            conds[(c.name, c.timeframe or tf)] = c
    n = len(frame.base.bars)
    grid: Dict[Tuple[str, int], List[Optional[Direction]]] = {
        k: [None] * n for k in conds}
    for i in range(n):
        snap = frame.snapshot(i)
        if snap is None:
            continue
        cache: Dict = {}
        for k, c in conds.items():
            r = c.evaluate(snap, tf, cache)
            if r.triggered and r.direction is not Direction.NEUTRAL:
                grid[k][i] = r.direction
    out: Dict[str, Dict[int, Direction]] = {}
    for s in strategies:
        keys = [(c.name, c.timeframe or tf) for c in s.signal_conditions]
        m: Dict[int, Direction] = {}
        first = grid[keys[0]]
        rest = [grid[k] for k in keys[1:]]
        for i in range(n):
            d = first[i]
            if d is None:
                continue
            if all(g[i] is d for g in rest):
                m[i] = d
        out[s.strategy_id] = m
    return out


# ---------------------------------------------------------------- placebos
def _placebo_condition(tag: str, mapping: Dict[int, Direction]) -> Condition:
    def fn(snap, _tf, _m=mapping):
        d = _m.get(snap.base_index)
        if d is None:
            return ConditionResult.no()
        return ConditionResult.yes(d, detail="placebo")
    return Condition(name=f"placebo_{tag}", group="placebo", fn=fn,
                     kind=ConditionKind.SIGNAL, description="placebo entry",
                     timeframe=None, warmup_bars=0)


def _remap(mapping: Dict[int, Direction], flavour: str, n_bars: int,
           rng: random.Random) -> Dict[int, Direction]:
    if not mapping:
        return {}
    idx = sorted(mapping)
    dirs = [mapping[i] for i in idx]
    lo = 60  # keep placebos out of the indicator warmup zone, as the real ones are
    if flavour == "random":
        span = list(range(lo, n_bars))
        if len(span) <= len(idx):
            return {}
        pick = rng.sample(span, len(idx))
        shuffled = dirs[:]
        rng.shuffle(shuffled)
        return dict(zip(sorted(pick), shuffled))
    if flavour == "shift5":
        return {i + 5: d for i, d in mapping.items() if lo <= i + 5 < n_bars}
    if flavour == "rotate":
        off = rng.randrange(max(1, n_bars // 8), max(2, n_bars - n_bars // 8))
        out = {}
        for i, d in mapping.items():
            j = (i + off) % n_bars
            if j >= lo:
                out[j] = d
        return out
    raise ValueError(flavour)


def build_placebos(hosts: Sequence[Strategy], sigs: Dict[str, Dict[int, Direction]],
                   n_bars: int, seed: int) -> List[Strategy]:
    out: List[Strategy] = []
    seen = set()
    for h in hosts:
        m = sigs.get(h.strategy_id) or {}
        if len(m) < 3:
            continue
        for fl in FLAVOURS:
            rng = random.Random(f"{seed}:{h.strategy_id}:{fl}")
            pm = _remap(m, fl, n_bars, rng)
            if len(pm) < 3:
                continue
            tag = f"{fl}_{hashlib.sha1((h.strategy_id + fl).encode()).hexdigest()[:8]}"
            cond = _placebo_condition(tag, pm)
            conds = tuple(h.filter_conditions) + (cond,)
            p = Strategy(
                name=f"placebo_{fl}_{h.name}"[:180], symbol=h.symbol,
                group=f"PLACEBO_{fl.upper()}", primary_tf=h.primary_tf,
                conditions=conds, exit=h.exit, filters=h.filters,
                allowed_directions=h.allowed_directions,
                confirm_tfs=h.confirm_tfs, execution_tf=h.execution_tf,
                trigger_conditions=h.trigger_conditions,
                description=f"host:{h.strategy_id}:{fl}:{len(m)}")
            if p.strategy_id in seen:
                continue
            seen.add(p.strategy_id)
            out.append(p)
    return out


def sample_hosts(pool: Sequence[Strategy], want: int, seed: int) -> List[Strategy]:
    """Stratified by (group, exit identity) so placebos inherit the same
    exit-geometry and entry-group mix as the population they control for.

    ``pool`` is the set of rule sets that actually took a trade. Sampling hosts
    from the whole generated population instead would spend most of the placebo
    budget on rule sets that never fire, and the control cohort would end up an
    order of magnitude smaller than the thing it is controlling.
    """
    rng = random.Random(f"host:{seed}")
    buckets: Dict[Tuple[str, str], List[Strategy]] = defaultdict(list)
    for s in pool:
        buckets[(s.group, s.exit.identity)].append(s)
    if not buckets:
        return []
    frac = min(1.0, want / max(1, len(pool)))
    hosts: List[Strategy] = []
    for k in sorted(buckets):
        v = buckets[k]
        take = max(1, int(round(len(v) * frac)))
        hosts.extend(rng.sample(v, min(take, len(v))))
    return hosts


# ---------------------------------------------------------------- measuring
def row_from(s: Strategy, trades, arm: str) -> dict:
    m = compute_metrics(trades)
    lo, hi = T.wilson(m.wins, m.trades)
    sess = Counter(t.session for t in trades)
    reg = Counter(t.regime for t in trades)
    vol = Counter(t.volatility for t in trades)
    bucket = Counter(t.time_bucket for t in trades)

    def exp_by(key):
        d = defaultdict(list)
        for t in trades:
            d[key(t)].append(t.net_r)
        return {k: [len(v), round(sum(v) / len(v), 4)] for k, v in d.items()}

    return dict(
        id=s.strategy_id, arm=arm, group=s.group, name=s.name[:120],
        host=(s.description.split(":")[1] if s.description.startswith("host:") else None),
        signals=[c.name for c in s.signal_conditions],
        filters=[c.name for c in s.filter_conditions],
        exit=s.exit.identity, exec_tf=s.execution_tf,
        n=m.trades, win=round(m.win_rate, 4), win_lo=round(lo, 4), win_hi=round(hi, 4),
        exp=round(m.expectancy_r, 4), rr=round(m.payoff_ratio, 4),
        pf=round(m.profit_factor, 4), total_r=round(m.total_r, 3),
        avg_win=round(m.avg_win_r, 4), avg_loss=round(m.avg_loss_r, 4),
        maxdd=round(m.max_drawdown_r, 3), avgdd=round(m.avg_drawdown_r, 3),
        t=round(m.t_statistic, 3), sharpe=round(m.sharpe, 3),
        sortino=round(m.sortino, 3), sqn=round(m.sqn, 3), std=round(m.std_r, 4),
        maxcw=m.max_consecutive_wins, maxcl=m.max_consecutive_losses,
        bars=round(m.avg_bars_held, 2), mins=round(m.avg_minutes_held, 1),
        mfe=round(m.avg_mfe_r, 4), mae=round(m.avg_mae_r, 4),
        longs=m.long_trades, shorts=m.short_trades,
        long_exp=round(m.long_expectancy_r, 4), short_exp=round(m.short_expectancy_r, 4),
        exits=dict(m.exit_reasons), sessions=dict(sess), regimes=dict(reg),
        vols=dict(vol), buckets=dict(bucket),
        by_session=exp_by(lambda t: t.session),
        by_vol=exp_by(lambda t: t.volatility),
        by_regime=exp_by(lambda t: t.regime),
        clones=1, clone_arms=[arm])


def collapse(pairs: List[Tuple[Strategy, list, str]], floor: int) -> List[dict]:
    """pairs of (strategy, trades, arm) -> rows, clones collapsed on realised trades."""
    seen: Dict[str, int] = {}
    out: List[dict] = []
    for s, trades, arm in pairs:
        if len(trades) < floor:
            continue
        fp = T.fingerprint(trades)
        if fp in seen:
            r = out[seen[fp]]
            r["clones"] += 1
            if arm not in r["clone_arms"]:
                r["clone_arms"].append(arm)
            continue
        seen[fp] = len(out)
        out.append(row_from(s, trades, arm))
    return out


def census(pairs: List[Tuple[Strategy, list, str]]) -> dict:
    """Floor-free census: what the 20-trade floor is actually selecting on."""
    ns = [len(t) for _, t, a in pairs if a == "real"]
    nsp = [len(t) for _, t, a in pairs if a != "real"]

    def d(v):
        if not v:
            return {}
        v2 = sorted(v)
        return dict(n_strategies=len(v), traded=sum(1 for x in v if x > 0),
                    ge5=sum(1 for x in v if x >= 5),
                    ge10=sum(1 for x in v if x >= 10),
                    ge20=sum(1 for x in v if x >= 20),
                    ge30=sum(1 for x in v if x >= 30),
                    median=v2[len(v2) // 2], p90=v2[int(0.9 * (len(v2) - 1))],
                    max=v2[-1], total_trades=sum(v))
    return {"real": d(ns), "placebo": d(nsp)}


def sign_test(diffs: Sequence[float]) -> dict:
    d = [x for x in diffs if x != 0]
    n = len(d)
    if n < 5:
        return {"n": n, "pos": sum(1 for x in d if x > 0), "z": 0.0, "p": 1.0}
    pos = sum(1 for x in d if x > 0)
    z = (pos - n / 2.0) / math.sqrt(n / 4.0)
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return {"n": n, "pos": pos, "z": round(z, 3), "p": round(p, 5)}


def paired_host_test(pairs, floor: int) -> dict:
    """Host-vs-its-own-placebo, paired. The only cross-arm test run here.

    D28: a rank sum across two arms holding correlated variants inflates |z| by
    ~3.3x. Every placebo here is derived from one named host, so the comparison
    has a natural pairing and needs no cross-arm rank sum at all. The unit of
    independence is the HOST rule set; each host contributes one difference.
    """
    real = {}
    plac = defaultdict(dict)
    counts = {}
    for s, trades, arm in pairs:
        if arm == "real":
            real[s.strategy_id] = trades
        else:
            hid = s.description.split(":")[1]
            fl = s.description.split(":")[2]
            plac[hid][fl] = trades
    out = {}
    for fl in list(FLAVOURS) + ["any"]:
        diffs, ratios = [], []
        for hid, byfl in plac.items():
            rt = real.get(hid)
            if rt is None or len(rt) < floor:
                continue
            if fl == "any":
                cand = [v for v in byfl.values() if len(v) >= floor]
                if not cand:
                    continue
                pe = st.mean([compute_metrics(v).expectancy_r for v in cand])
                pn = st.mean([len(v) for v in cand])
            else:
                v = byfl.get(fl)
                if v is None or len(v) < floor:
                    continue
                pe = compute_metrics(v).expectancy_r
                pn = len(v)
            diffs.append(compute_metrics(rt).expectancy_r - pe)
            ratios.append(pn / max(1, len(rt)))
        if len(diffs) >= 5:
            m = st.mean(diffs)
            sd = st.pstdev(diffs) or 1e-9
            tt = m / (sd / math.sqrt(len(diffs)))
        else:
            m, tt = (st.mean(diffs) if diffs else 0.0), 0.0
        out[fl] = {"pairs": len(diffs), "mean_exp_diff_real_minus_placebo": round(m, 4),
                   "paired_t": round(tt, 3), "sign": sign_test(diffs),
                   "median_trade_count_ratio_placebo_over_real":
                       round(st.median(ratios), 3) if ratios else None}
    counts["real_traded"] = sum(1 for _, t, a in pairs if a == "real" and t)
    out["_counts"] = counts
    return out


def score(r: dict) -> float:
    """Durability score. Expectancy in R with a sample-size penalty, a t-stat
    term and a drawdown term - never raw profit. Deliberately dull: the ranking
    key must not be tuned, or the ranking is another fitted parameter."""
    n = max(1, r["n"])
    shrink = n / (n + 30.0)                      # sample-size penalty
    dd = 1.0 / (1.0 + max(0.0, r["maxdd"]) / 8.0)
    tterm = max(-2.0, min(2.0, r["t"])) / 4.0
    cl = 1.0 / (1.0 + max(0, r["maxcl"]) / 20.0)
    return round(r["exp"] * shrink * dd * cl + 0.05 * tterm, 5)


# ---------------------------------------------------------------- one cell
def run_cell(symbol: str, tf: int, *, window: Optional[int] = None,
             slice_days: Optional[Tuple[int, int]] = None, budget: int = 6000,
             seed: int = 1, placebo_frac: float = 0.10, floor: int = 20,
             rth: bool = False) -> dict:
    if slice_days is not None:
        ser = T.slice_series(symbol, tf, *slice_days)
    else:
        ser = T._series(symbol, tf, window)
    if len(ser) < 120:
        return {"skipped": f"{len(ser)} bars"}
    _, frame = frame_for(symbol, tf, series=ser)
    S = population(symbol, tf, budget, seed=seed, rth=rth)
    res = run_portfolio(frame, S)
    traded = [s for s in S if res[s.strategy_id].trades]
    want_hosts = max(1, int(math.ceil(placebo_frac * len(S) / len(FLAVOURS))))
    hosts = sample_hosts(traded, want_hosts, seed)
    sigs = signal_series(frame, hosts, tf)
    P = build_placebos(hosts, sigs, len(frame.base.bars), seed)
    real_ids = {s.strategy_id for s in S}
    P = [p for p in P if p.strategy_id not in real_ids]
    resp = run_portfolio(frame, P) if P else {}

    pairs = ([(s, res[s.strategy_id].trades, "real") for s in S]
             + [(p, resp[p.strategy_id].trades, p.group.lower()) for p in P])
    rows = collapse(pairs, floor)
    rows0 = collapse(pairs, 1)
    for r in rows:
        r["score"] = score(r)
    for r in rows0:
        r["score"] = score(r)
    rows.sort(key=lambda r: -r["score"])
    rows0.sort(key=lambda r: -r["score"])
    screened = len(S) + len(P)
    return dict(
        symbol=symbol, tf=tf, window=window, slice_days=slice_days, rth=rth,
        bars=len(ser), first_bar=str(ser.bars[0].ts), last_bar=str(ser.bars[-1].ts),
        n_real=len(S), n_placebo=len(P), n_hosts=len(hosts), screened=screened,
        free_t=round(T.free_t(screened), 3), floor=floor,
        census=census(pairs), paired=paired_host_test(pairs, floor),
        placebo=placebo_rank(rows), rows=rows, rows_floorfree=rows0)


def placebo_rank(rows: List[dict]) -> dict:
    """Where the best placebo landed, and how the two cohorts compare."""
    real = [r for r in rows if r["arm"] == "real"]
    plac = [r for r in rows if r["arm"] != "real"]
    if not plac:
        return {"placebo_rows": 0, "note": "no placebo cleared the floor"}
    best_i = min(i for i, r in enumerate(rows) if r["arm"] != "real")
    best = rows[best_i]
    rexp = sorted(r["exp"] for r in real)
    pexp = sorted(r["exp"] for r in plac)

    def pct(v, pool):
        return round(sum(1 for x in pool if x < v) / len(pool), 4) if pool else None
    return {
        "placebo_rows": len(plac), "real_rows": len(real),
        "best_placebo_rank": best_i + 1, "best_placebo_of": len(rows),
        "best_placebo_arm": best["arm"], "best_placebo_exp": best["exp"],
        "best_placebo_n": best["n"], "best_placebo_t": best["t"],
        "best_placebo_score": best["score"],
        "n_placebo_in_top10": sum(1 for r in rows[:10] if r["arm"] != "real"),
        "best_placebo_exp_pctile_vs_real": pct(best["exp"], rexp),
        "best_real_exp_pctile_vs_placebo": pct(max(rexp) if rexp else 0.0, pexp),
        "median_exp_real": round(st.median(rexp), 4) if rexp else None,
        "median_exp_placebo": round(st.median(pexp), 4) if pexp else None,
        "pct_profitable_real": round(sum(1 for x in rexp if x > 0) / len(rexp), 3) if rexp else None,
        "pct_profitable_placebo": round(sum(1 for x in pexp if x > 0) / len(pexp), 3) if pexp else None,
        "search_ratio_real_over_placebo": round(len(real) / len(plac), 2),
    }


def save_cell(cell: dict, path_tag: str) -> str:
    os.makedirs(OUTDIR, exist_ok=True)
    p = f"{OUTDIR}/{path_tag}.json"
    with open(p, "w") as fh:
        json.dump(cell, fh, default=str)
    return p
