"""One cell of the big scan: (symbol, timeframe, window).

Emits every qualifying strategy, not a pre-picked top slice, so the tables can
be built afterwards without re-running anything. Two guards matter more than
the scan itself:

* **Clone collapse.** Filter variants that veto nothing produce an identical
  trade list. Without collapsing them a "highest win rate" table is one
  strategy printed ten times. Collapsed on realised trades, not on rule text.

* **A win-rate confidence interval.** A 100% win rate on four trades is the
  single most attractive-looking and least meaningful row a scan can produce.
  Every row carries a Wilson interval so the table can show how wide the
  uncertainty really is at its sample size.
"""
import json
import math
import os
import sys
import time
from datetime import timedelta

from futures_agents.backtest.engine import run_portfolio
from futures_agents.backtest.metrics import compute_metrics
from futures_agents.data.bars import BarSeries
from futures_agents.data.loader import load_csv
from futures_agents.features import build_symbol_frame
from futures_agents.scout import FRAMES
from futures_agents.strategies.combinator import TEMPLATES, generate_strategies

SUFFIX = {1440: "1d", 240: "4h", 60: "1h", 30: "30m", 15: "15m", 5: "5m", 1: "1m"}
#: Timeframes with no file of their own, built by resampling a finer one.
#: 4-hour is the anchor several earlier results lived on and no vendor file
#: covers it, so it is aggregated from hourly bars.
DERIVED = {240: ("1h", 60)}
OUT_DIR = os.environ.get("SCAN_OUT", "workspace/bigscan/cells")
FLOOR = 15          # report floor; every row carries n so it can be raised later


def wilson(wins: int, n: int, z: float = 1.96):
    """95% interval for a win rate. Wide at small n - that is the point."""
    if n == 0:
        return (0.0, 0.0)
    p = wins / n
    d = 1 + z * z / n
    c = p + z * z / (2 * n)
    s = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return ((c - s) / d, (c + s) / d)


def fingerprint(trades) -> str:
    import hashlib
    key = "|".join(f"{t.entry_ts.isoformat()}:{t.direction.value}" for t in trades)
    return hashlib.sha1(key.encode()).hexdigest()[:16] if key else "empty"


def main() -> int:
    sym, tf, window = sys.argv[1], int(sys.argv[2]), int(sys.argv[3])
    budget = int(sys.argv[4]) if len(sys.argv) > 4 else 2400
    t0 = time.time()
    os.makedirs(OUT_DIR, exist_ok=True)
    tag = f"{sym}_{tf}m_{window}d"
    path = f"{OUT_DIR}/{tag}.json"

    if tf in DERIVED:
        suffix, src_min = DERIVED[tf]
        src = load_csv(f"csv/raw/{sym}_{suffix}.csv", sym, src_min)
        # keep_partial=False: a half-formed final 4h bar has not closed, and
        # letting a strategy read it would be look-ahead on the newest bar -
        # exactly where a scan is most tempted to find an edge.
        full = src.resample(tf, keep_partial=False)
    else:
        full = load_csv(f"csv/raw/{sym}_{SUFFIX[tf]}.csv", sym, tf)
    bars = list(full.bars)
    span_all = (bars[0].ts, bars[-1].ts)
    cutoff = bars[-1].ts - timedelta(days=window)
    bars = [b for b in bars if b.ts >= cutoff]
    if len(bars) < 120:
        json.dump({"cell": tag, "symbol": sym, "tf": tf, "window": window,
                   "skipped": f"only {len(bars)} bars in window"},
                  open(path, "w"))
        print(f"{tag}: SKIPPED, {len(bars)} bars", flush=True)
        return 0

    series = BarSeries(sym, tf, bars)
    tfs = FRAMES[tf]
    frame = build_symbol_frame(series, tfs)
    strategies = [s for s in generate_strategies(
        sym, tfs, groups=[t.group for t in TEMPLATES], max_total=99999,
        max_per_template=budget, seed=1) if s.primary_tf == tf]
    results = run_portfolio(frame, strategies)

    seen, rows = {}, []
    cleared = 0
    for s in strategies:
        trades = results[s.strategy_id].trades
        if len(trades) < FLOOR:
            continue
        cleared += 1
        fp = fingerprint(trades)
        if fp in seen:
            rows[seen[fp]]["clones"] += 1
            continue
        m = compute_metrics(trades)
        lo, hi = wilson(m.wins, m.trades)
        seen[fp] = len(rows)
        rows.append(dict(
            id=s.strategy_id, group=s.group, tf=tf, exec_tf=s.execution_tf,
            n=m.trades, win=round(m.win_rate, 4),
            win_lo=round(lo, 4), win_hi=round(hi, 4),
            exp=round(m.expectancy_r, 4), rr=round(m.payoff_ratio, 4),
            pf=round(m.profit_factor, 4), maxdd=round(m.max_drawdown_r, 3),
            t=round(m.t_statistic, 3), clones=1,
            avg_win=round(m.avg_win_r, 4), avg_loss=round(m.avg_loss_r, 4)))

    payload = dict(
        cell=tag, symbol=sym, tf=tf, window=window, frames=tfs,
        bars=len(series),
        data_span=f"{span_all[0]:%Y-%m-%d}..{span_all[1]:%Y-%m-%d}",
        window_span=f"{bars[0].ts:%Y-%m-%d}..{bars[-1].ts:%Y-%m-%d}",
        window_actual_days=round((bars[-1].ts - bars[0].ts).total_seconds() / 86400, 1),
        screened=len(strategies), cleared=cleared, distinct=len(rows),
        floor=FLOOR,
        free_t=round(math.sqrt(2 * math.log(max(2, len(strategies)))), 3),
        rows=rows, seconds=round(time.time() - t0, 1))
    json.dump(payload, open(path, "w"))
    print(f"{tag}: {len(strategies):,} screened, {cleared} cleared, "
          f"{len(rows)} distinct, {len(series):,} bars [{payload['seconds']:.0f}s]",
          flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
