"""Shared toolkit for the strategy studies.

Twenty agents asking twenty different questions of the same data will, left to
themselves, each write their own loader, their own trade floor and their own
idea of what counts as significant - and the resulting twenty answers cannot be
compared with one another. This module fixes those choices in one place.

The one function that matters is :func:`ab`. Almost every interesting question
here is comparative - "do strategies containing X beat otherwise similar ones
that do not" - and the honest way to answer it is a matched comparison against a
control arm drawn from the SAME generated population, not a league table of the
best things containing X. A ranking of winners tells you what the search found;
a matched comparison tells you whether X did anything.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import statistics as st
from collections import defaultdict
from datetime import timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from futures_agents.backtest.engine import run_portfolio
from futures_agents.backtest.metrics import compute_metrics
from futures_agents.data.bars import BarSeries
from futures_agents.data.loader import load_csv
from futures_agents.features import build_symbol_frame
from futures_agents.scout import FRAMES
from futures_agents.strategies.base import ConditionKind
from futures_agents.strategies.combinator import TEMPLATES, generate_strategies

OUT = "workspace/studies/out"
SUFFIX = {1440: "1d", 60: "1h", 30: "30m", 15: "15m", 5: "5m"}
DERIVED = {240: ("1h", 60)}
ALL_GROUPS = [t.group for t in TEMPLATES]

#: Reporting floor. Below this a win rate is barely an estimate at all: the 95%
#: interval on 15 trades spans roughly 40 percentage points.
FLOOR = 20


# ---------------------------------------------------------------- statistics
def wilson(wins: int, n: int, z: float = 1.96):
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    s = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - s) / d, (c + s) / d)


def free_t(trials: int) -> float:
    """t-units bought by search alone. Anything under this is not evidence."""
    return math.sqrt(2.0 * math.log(max(2, trials)))


def corr(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) < 3:
        return 0.0
    ma, mb = st.mean(a), st.mean(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = (sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b)) ** 0.5
    return num / den if den else 0.0


def mann_whitney_u(a: Sequence[float], b: Sequence[float]) -> Dict[str, float]:
    """Rank-sum test with a normal approximation.

    Used rather than a t-test because expectancy distributions across a
    generated population are heavy-tailed and asymmetric: a handful of
    strategies with enormous payoff ratios drag a mean around, and the question
    being asked ("does this arm sit higher") is about location, not means.
    """
    na, nb = len(a), len(b)
    if na < 5 or nb < 5:
        return {"u": 0.0, "z": 0.0, "p": 1.0, "n_a": na, "n_b": nb}
    merged = sorted([(v, 0) for v in a] + [(v, 1) for v in b])
    ranks, i = [0.0] * len(merged), 0
    while i < len(merged):
        j = i
        while j + 1 < len(merged) and merged[j + 1][0] == merged[i][0]:
            j += 1
        avg = (i + j) / 2.0 + 1.0
        for k in range(i, j + 1):
            ranks[k] = avg
        i = j + 1
    ra = sum(r for r, (_, g) in zip(ranks, merged) if g == 0)
    u_a = ra - na * (na + 1) / 2.0
    mu = na * nb / 2.0
    sd = math.sqrt(na * nb * (na + nb + 1) / 12.0)
    z = (u_a - mu) / sd if sd else 0.0
    p = 2 * (1 - 0.5 * (1 + math.erf(abs(z) / math.sqrt(2))))
    return {"u": u_a, "z": round(z, 3), "p": round(p, 5), "n_a": na, "n_b": nb}


# ---------------------------------------------------------------- scan rows
def scan_rows(which: str = "both") -> List[dict]:
    """Every qualifying strategy from the completed scans, with its context."""
    dirs = {"broad": ["workspace/bigscan/cells"],
            "focus": ["workspace/focus/cells"],
            "both": ["workspace/bigscan/cells", "workspace/focus/cells"]}[which]
    out = []
    for d in dirs:
        if not os.path.isdir(d):
            continue
        for f in sorted(os.listdir(d)):
            c = json.load(open(os.path.join(d, f)))
            if c.get("skipped") or not c.get("rows"):
                continue
            for r in c["rows"]:
                r = dict(r)
                r.update(symbol=c["symbol"], tf=c["tf"], window=c["window"],
                         scan=os.path.basename(d), free_t=c["free_t"],
                         screened=c["screened"], span=c["window_span"])
                out.append(r)
    return out


# ---------------------------------------------------------------- backtests
def _series(symbol: str, tf: int, window_days: Optional[int]):
    if tf in DERIVED:
        suf, src_min = DERIVED[tf]
        full = load_csv(f"csv/raw/{symbol}_{suf}.csv", symbol, src_min).resample(
            tf, keep_partial=False)
    else:
        full = load_csv(f"csv/raw/{symbol}_{SUFFIX[tf]}.csv", symbol, tf)
    bars = list(full.bars)
    if window_days:
        bars = [b for b in bars if b.ts >= bars[-1].ts - timedelta(days=window_days)]
    return BarSeries(symbol, tf, bars)


def fingerprint(trades) -> str:
    key = "|".join(f"{t.entry_ts.isoformat()}:{t.direction.value}" for t in trades)
    return hashlib.sha1(key.encode()).hexdigest()[:16] if key else "empty"


def measure(symbol: str, tf: int, window_days: Optional[int] = None, *,
            groups: Optional[Sequence[str]] = None, budget: int = 2400,
            seed: int = 1, floor: int = FLOOR, collapse: bool = True
            ) -> List[dict]:
    """Generate, run and measure one (symbol, timeframe, window).

    Returns one dict per surviving strategy, carrying the condition names so a
    caller can slice on them without re-running anything. Clones - rule sets
    whose realised trades are identical - collapse to one observation by
    default, because counting them separately is the most common way a study
    turns one result into five.
    """
    series = _series(symbol, tf, window_days)
    if len(series) < 120:
        return []
    frame = build_symbol_frame(series, FRAMES[tf])
    strats = [s for s in generate_strategies(
        symbol, FRAMES[tf], groups=list(groups) if groups else ALL_GROUPS,
        max_total=99999, max_per_template=budget, seed=seed)
        if s.primary_tf == tf]
    res = run_portfolio(frame, strats)

    seen, out = {}, []
    for s in strats:
        trades = res[s.strategy_id].trades
        if len(trades) < floor:
            continue
        if collapse:
            fp = fingerprint(trades)
            if fp in seen:
                out[seen[fp]]["clones"] += 1
                continue
            seen[fp] = len(out)
        m = compute_metrics(trades)
        lo, hi = wilson(m.wins, m.trades)
        out.append(dict(
            id=s.strategy_id, group=s.group, symbol=symbol, tf=tf,
            window=window_days, exec_tf=s.execution_tf,
            signals=[c.name for c in s.conditions if c.kind is ConditionKind.SIGNAL],
            filters=[c.name for c in s.conditions if c.kind is ConditionKind.FILTER],
            conditions=[c.name for c in s.conditions],
            stop_kind=str(s.exit.stop_kind), stop_mult=s.exit.stop_mult,
            target_kind=str(s.exit.target_kind), targets=list(s.exit.anchor_mult),
            n=m.trades, win=round(m.win_rate, 4), win_lo=round(lo, 4),
            win_hi=round(hi, 4), exp=round(m.expectancy_r, 4),
            rr=round(m.payoff_ratio, 4), pf=round(m.profit_factor, 4),
            maxdd=round(m.max_drawdown_r, 3), t=round(m.t_statistic, 3),
            sortino=round(m.sortino, 3), clones=1))
    return out


def ab(rows: Iterable[dict], predicate, label_a="with", label_b="without") -> dict:
    """Matched comparison of the strategies satisfying ``predicate`` against the rest.

    Both arms come from the same generated population over the same bars, so
    they share symbol, timeframe, window, cost model and search size. What
    differs is the thing being tested. Reports the rank-sum test rather than a
    difference of means, and reports both arms' sizes so a lopsided split is
    visible rather than hidden inside a p-value.
    """
    rows = list(rows)
    A = [r for r in rows if predicate(r)]
    B = [r for r in rows if not predicate(r)]
    if not A or not B:
        return {"skipped": f"one arm empty ({label_a}={len(A)}, {label_b}={len(B)})"}
    def summ(v):
        return dict(n_strategies=len(v),
                    median_exp=round(st.median([x["exp"] for x in v]), 4),
                    median_win=round(st.median([x["win"] for x in v]), 4),
                    median_rr=round(st.median([x["rr"] for x in v]), 3),
                    pct_profitable=round(sum(1 for x in v if x["exp"] > 0) / len(v), 3),
                    total_trades=sum(x["n"] for x in v),
                    best_t=round(max(x["t"] for x in v), 3))
    u = mann_whitney_u([x["exp"] for x in A], [x["exp"] for x in B])
    return {label_a: summ(A), label_b: summ(B),
            "median_exp_delta": round(summ(A)["median_exp"] - summ(B)["median_exp"], 4),
            "rank_sum": u,
            "verdict": ("A higher" if u["z"] > 1.96 else
                        "B higher" if u["z"] < -1.96 else
                        "no detectable difference")}


def save(study_id: str, title: str, question: str, payload: dict,
         headline: str, caveats: Sequence[str] = ()) -> str:
    """Write one study's findings where the aggregator will find them."""
    os.makedirs(OUT, exist_ok=True)
    doc = dict(study_id=study_id, title=title, question=question,
               headline=headline, caveats=list(caveats), findings=payload)
    path = f"{OUT}/{study_id}.json"
    with open(path, "w") as fh:
        json.dump(doc, fh, indent=1, default=str)
    return path


# ==========================================================================
# CUSTOM STRATEGY RESEARCH
# ==========================================================================
#
# The combinator builds strategies from templates, and a template names groups
# that already exist. New research conditions have no template, and five agents
# editing library.py at once would collide. So custom work builds Strategy
# objects directly: full control over the rule set, no template plumbing, and
# no shared file to contend on.

def make_strategy(symbol: str, tf: int, conditions: Sequence, *,
                  group: str = "CUSTOM", name: Optional[str] = None,
                  exit_model=None, filters=None, execution_tf: Optional[int] = None,
                  confirm_tfs: Sequence[int] = ()):
    """One Strategy from an explicit condition list.

    ``exit_model`` defaults to the only exit that survived the study programme:
    ATRx1 with targets anchored to 1/2.5 ATR of the anchor timeframe, best or
    joint-best in 7 of 10 entry groups at every trade floor from 5 to 30. It
    also sets ``exit_at_session_close=False``, which was the single largest
    measured exit effect - left True it reduces every 4h and daily trade to one
    bar, median hold 0.0 minutes.
    """
    from futures_agents.strategies.base import (ExitModel, StopKind, Strategy,
                                                StrategyFilters, TargetKind)
    if exit_model is None:
        exit_model = ExitModel(
            StopKind.ATR, 1.0, targets_r=(2.0, 4.0), scale_out=(0.5, 0.5),
            breakeven_at_r=1.5, time_stop_bars=40,
            target_kind=TargetKind.ANCHOR_ATR, anchor_mult=(1.0, 2.5),
            min_reward_risk=1.5, exit_at_session_close=False)
    if filters is None:
        # rth_only defaults True and is the library-wide 4h population killer:
        # it reduces the 4h population to one bar per day and costs every group
        # a 2-12x factor. Custom research starts without it; turn it on
        # deliberately and report that you did.
        filters = StrategyFilters(rth_only=False)
    conds = tuple(conditions)
    return Strategy(
        name=name or f"{group.lower()}_{'_'.join(c.name for c in conds)}",
        symbol=symbol.upper(), group=group, primary_tf=tf, conditions=conds,
        exit=exit_model, filters=filters, execution_tf=execution_tf,
        confirm_tfs=tuple(confirm_tfs))


def measure_custom(symbol: str, tf: int, window_days: Optional[int],
                   strategies: Sequence, *, floor: int = FLOOR,
                   collapse: bool = True) -> List[dict]:
    """Run explicit Strategy objects and return the same row shape as ``measure``.

    Use this for a new condition, and always alongside a control arm built the
    same way, or the result is a league table rather than a comparison.
    """
    series = _series(symbol, tf, window_days)
    if len(series) < 120:
        return []
    frame = build_symbol_frame(series, FRAMES[tf])
    res = run_portfolio(frame, list(strategies))
    seen, out = {}, []
    for s in strategies:
        trades = res[s.strategy_id].trades
        if len(trades) < floor:
            continue
        if collapse:
            fp = fingerprint(trades)
            if fp in seen:
                out[seen[fp]]["clones"] += 1
                continue
            seen[fp] = len(out)
        m = compute_metrics(trades)
        lo, hi = wilson(m.wins, m.trades)
        out.append(dict(
            id=s.strategy_id, group=s.group, symbol=symbol, tf=tf,
            window=window_days, name=s.name,
            conditions=[c.name for c in s.conditions],
            n=m.trades, win=round(m.win_rate, 4), win_lo=round(lo, 4),
            win_hi=round(hi, 4), exp=round(m.expectancy_r, 4),
            rr=round(m.payoff_ratio, 4), pf=round(m.profit_factor, 4),
            maxdd=round(m.max_drawdown_r, 3), t=round(m.t_statistic, 3),
            sortino=round(m.sortino, 3), clones=1))
    return out


def disjoint_slices(symbol: str, tf: int, n: int = 3) -> List[Tuple[int, int]]:
    """``n`` non-overlapping (start_days_ago, end_days_ago) windows.

    The scan windows used earlier in this project all END on the same final bar,
    so 90d is a subset of 180d is a subset of 274d and agreement across them is
    one observation seen three times. Replication needs genuinely disjoint
    periods; this hands them out.
    """
    series = _series(symbol, tf, None)
    span = (series.bars[-1].ts - series.bars[0].ts).days
    edge = span // n
    return [((n - i) * edge, (n - i - 1) * edge) for i in range(n)]


def slice_series(symbol: str, tf: int, start_days_ago: int, end_days_ago: int):
    """Bars inside one disjoint window, for use with ``build_symbol_frame``."""
    full = _series(symbol, tf, None)
    last = full.bars[-1].ts
    lo = last - timedelta(days=start_days_ago)
    hi = last - timedelta(days=end_days_ago)
    return BarSeries(symbol, tf, [b for b in full.bars if lo <= b.ts < hi])
