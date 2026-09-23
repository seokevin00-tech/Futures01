"""Execute one chunk of the research plan. Small output by design.

A chunk writes a compact JSON summary - counts, medians, a handful of finalist
rows - and never a trade list. The reason is the usage budget rather than disk:
compute runs in a subprocess and costs nothing against a Claude session limit,
while READING results is what consumes it. A chunk that returned its trades
would blow the budget in a dozen runs.
"""

from __future__ import annotations

import json
import os
import sys
import time
from collections import defaultdict
from typing import Any, Dict, List

from ..backtest.engine import run_portfolio
from ..backtest.metrics import compute_metrics
from ..backtest.objectives import reward_for_risk_score, risk_profile
from ..backtest.robustness import deflated_expectancy
from ..backtest.walkforward import walk_forward
from ..data.bars import BarSeries
from ..data.loader import load_csv
from ..features import build_symbol_frame
from ..strategies.combinator import TEMPLATES, generate_strategies

ALL_GROUPS = [t.group for t in TEMPLATES]
FLOOR = 30
PERIODS = 3
PER_PERIOD_FLOOR = 8

HORIZONS = {
    "position": ("1d", 1440, [1440]),
    "swing":    ("1h", 60, [60, 240]),
    "intraday": ("15m", 15, [15, 60]),
    "fast":     ("5m", 5, [5, 15, 60]),
}


def trade_fingerprint(trades) -> str:
    """Identity of a strategy's realised behaviour, not of its definition.

    Two strategies that took the same trades are one observation however
    differently they are written. This matters because the optional-filter
    dimension generates a control and one variant per filter, and a filter
    that vetoes nothing produces a clone: measured on MGC daily, five
    "independent" strategies reported an identical [0.0106, 0.1401, 0.154]
    across three periods because four of them were the same rule set with a
    filter attached that removed no trades. Counted raw that reads as five
    of five against a chance expectation of 0.62; counted properly it is one
    of one, which is unremarkable.
    """
    import hashlib
    key = "|".join(f"{t.entry_ts.isoformat()}:{t.direction.value}:{t.exit_ts.isoformat()}"
                   for t in trades)
    return hashlib.sha1(key.encode()).hexdigest()[:16] if key else "empty"


def _dedupe(pairs):
    """Keep one representative per distinct realised trade set."""
    seen, out = set(), []
    for s, m, fp in pairs:
        if fp in seen:
            continue
        seen.add(fp)
        out.append((s, m, fp))
    return out


def _frame(symbol: str, horizon: str):
    suffix, base_min, tfs = HORIZONS[horizon]
    series = load_csv(f"csv/raw/{symbol}_{suffix}.csv", symbol, base_min)
    return series, build_symbol_frame(series, tfs), tfs


def screen(symbol: str, horizon: str, budget: int, out_dir: str) -> dict:
    """Wide, cheap pass. Rejects what never trades or loses outright."""
    t0 = time.time()
    series, frame, tfs = _frame(symbol, horizon)
    strategies = generate_strategies(symbol, tfs, groups=ALL_GROUPS,
                                     max_total=budget, seed=1)
    results = run_portfolio(frame, strategies)

    survivors, by_group = [], defaultdict(lambda: [0, 0])
    for s in strategies:
        trades = results[s.strategy_id].trades
        m = compute_metrics(trades)
        by_group[s.group][0] += 1
        if m.trades >= FLOOR:
            by_group[s.group][1] += 1
            if m.expectancy_r > 0:
                survivors.append((s, m, trade_fingerprint(trades)))

    survivors.sort(key=lambda x: -reward_for_risk_score(x[1]).score)
    raw_positive = len(survivors)
    survivors = _dedupe(survivors)
    keep = survivors[:40]
    payload = dict(
        stage="screen", symbol=symbol, horizon=horizon, bars=len(series),
        # The whole screened population, which is what deflation is charged
        # against later. Selecting 40 from 20,000 and deflating against 40
        # would hide the search entirely.
        screened=len(strategies),
        cleared_floor=sum(v[1] for v in by_group.values()),
        positive=len(survivors), positive_raw=raw_positive,
        clones_collapsed=raw_positive - len(survivors),
        by_group={g: {"generated": v[0], "cleared": v[1]}
                  for g, v in sorted(by_group.items())},
        finalists=[dict(strategy_id=s.strategy_id, group=s.group,
                        tf=s.primary_tf, execution_tf=s.execution_tf,
                        n=m.trades, exp=round(m.expectancy_r, 4),
                        rr=round(m.payoff_ratio, 3),
                        defl=round(deflated_expectancy(m, len(strategies)), 5))
                   for s, m, _ in keep],
        seconds=round(time.time() - t0, 1))
    path = os.path.join(out_dir, f"screen_{symbol}_{horizon}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    return payload


def validate(symbol: str, horizon: str, out_dir: str) -> dict:
    """The expensive suite, on survivors only."""
    t0 = time.time()
    screen_path = os.path.join(out_dir, f"screen_{symbol}_{horizon}.json")
    with open(screen_path, encoding="utf-8") as fh:
        prev = json.load(fh)
    wanted = {r["strategy_id"] for r in prev["finalists"]}
    if not wanted:
        return dict(stage="validate", symbol=symbol, horizon=horizon,
                    survivors=0, note="screen produced no survivors")

    series, frame, tfs = _frame(symbol, horizon)
    strategies = [s for s in generate_strategies(symbol, tfs, groups=ALL_GROUPS,
                                                 max_total=prev["screened"], seed=1)
                  if s.strategy_id in wanted]

    bars = list(series.bars)
    edge = len(bars) // PERIODS
    per_period: List[Dict[str, Any]] = []
    for k in range(PERIODS):
        chunk = bars[k * edge:(k + 1) * edge]
        f = build_symbol_frame(BarSeries(symbol, series.minutes, chunk), tfs)
        r = run_portfolio(f, strategies)
        per_period.append({s.strategy_id: compute_metrics(r[s.strategy_id].trades)
                           for s in strategies})

    # Fingerprint on the FULL-history trades so clones collapse before any
    # count is taken. Counting first and deduplicating afterwards would let
    # the inflated number reach a p-value.
    full = run_portfolio(frame, strategies)
    fps, seen = {}, set()
    unique_strategies = []
    for s in strategies:
        fp = trade_fingerprint(full[s.strategy_id].trades)
        fps[s.strategy_id] = fp
        if fp not in seen:
            seen.add(fp)
            unique_strategies.append(s)

    consistent = []
    for s in unique_strategies:
        ms = [p[s.strategy_id] for p in per_period]
        if any(m.trades < PER_PERIOD_FLOOR for m in ms):
            continue
        if all(m.expectancy_r > 0 for m in ms):
            consistent.append(dict(
                strategy_id=s.strategy_id, group=s.group, tf=s.primary_tf,
                exp=[round(m.expectancy_r, 4) for m in ms],
                trades=[m.trades for m in ms]))

    wf = None
    if strategies:
        w = walk_forward(frame, strategies, folds=4, top_k=5)
        wf = dict(efficiency=round(w.efficiency, 3),
                  credible=bool(w.is_credible),
                  stability=round(w.selection_stability, 3))

    # Eligible-for-replication count, needed for the binomial null.
    eligible = sum(1 for s in unique_strategies
                   if all(per_period[k][s.strategy_id].trades >= PER_PERIOD_FLOOR
                          for k in range(PERIODS)))
    payload = dict(
        stage="validate", symbol=symbol, horizon=horizon,
        screened=prev["screened"], candidates=len(strategies),
        distinct_candidates=len(unique_strategies),
        clones_collapsed=len(strategies) - len(unique_strategies),
        replication_eligible=eligible,
        all_periods_positive=len(consistent),
        expected_by_chance=round(eligible / (2 ** PERIODS), 2),
        consistent=consistent[:10], walk_forward=wf,
        seconds=round(time.time() - t0, 1))
    path = os.path.join(out_dir, f"validate_{symbol}_{horizon}.json")
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=1)
    return payload


def main() -> int:
    stage, symbol, horizon = sys.argv[1], sys.argv[2], sys.argv[3]
    budget = int(sys.argv[4]) if len(sys.argv) > 4 else 6000
    out_dir = os.environ.get("RESEARCH_OUT", "workspace/research_plan")
    os.makedirs(out_dir, exist_ok=True)
    p = (screen(symbol, horizon, budget, out_dir) if stage == "screen"
         else validate(symbol, horizon, out_dir))
    if stage == "screen":
        print(f"{symbol:4s} {horizon:9s} screen   {p['screened']:>6,} tested  "
              f"{p['cleared_floor']:>4d} cleared  {p['positive']:>4d} positive  "
              f"[{p['seconds']:.0f}s]", flush=True)
    else:
        wf = p.get("walk_forward") or {}
        print(f"{symbol:4s} {horizon:9s} validate {p.get('candidates',0):>4d} cand  "
              f"{p.get('all_periods_positive',0):>3d}/{p.get('replication_eligible',0):>3d} "
              f"all-periods-positive (chance {p.get('expected_by_chance',0)})  "
              f"eff {wf.get('efficiency','-')}  [{p.get('seconds',0):.0f}s]", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
