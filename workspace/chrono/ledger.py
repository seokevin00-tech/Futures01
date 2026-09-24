"""Build a per-(month, group) expectancy series for one symbol.

The question is whether the IDENTITY of the group that works rotates in a
detectable, symbol-specific way - VWAP, then TREND, then MOMENTUM, then VWAP.

That needs a long series of months, so the daily files are the right input:
MGC reaches back to 2016 (~120 months) and MES/MNQ to 2019 (~89), where the
hourly files give only ten or so. Ten monthly buckets cannot support a
transition matrix over thirteen groups.

Nothing here is a trading test. It is a description of what already happened,
so look-ahead is not the hazard - the hazard is finding a pattern in 169
transition cells fitted on 100 observations, which is what the analysis stage
guards against.
"""
import json
import os
import sys
from collections import defaultdict

from futures_agents.backtest.engine import run_portfolio
from futures_agents.data.loader import load_csv
from futures_agents.features import build_symbol_frame
from futures_agents.strategies.combinator import TEMPLATES, generate_strategies

OUT = "workspace/chrono/ledgers"
SUFFIX = {1440: "1d", 60: "1h"}
FRAMES = {1440: [1440, 7200], 60: [60, 240, 1440]}


def main() -> int:
    sym, tf = sys.argv[1], int(sys.argv[2])
    budget = int(sys.argv[3]) if len(sys.argv) > 3 else 2400
    os.makedirs(OUT, exist_ok=True)

    series = load_csv(f"csv/raw/{sym}_{SUFFIX[tf]}.csv", sym, tf)
    frame = build_symbol_frame(series, FRAMES[tf])
    strats = [s for s in generate_strategies(
        sym, FRAMES[tf], groups=[t.group for t in TEMPLATES], max_total=99999,
        max_per_template=budget, seed=1) if s.primary_tf == tf]
    res = run_portfolio(frame, strats)

    # month -> group -> list of realised R. Every trade counts once; no floor
    # and no ranking, because selecting here would bake in the very thing the
    # analysis is trying to detect.
    buckets = defaultdict(lambda: defaultdict(list))
    n_trades = 0
    for s in strats:
        for t in res[s.strategy_id].trades:
            buckets[f"{t.entry_ts:%Y-%m}"][s.group].append(t.net_r)
            n_trades += 1

    payload = dict(
        symbol=sym, tf=tf, bars=len(series),
        span=f"{series.bars[0].ts:%Y-%m-%d}..{series.bars[-1].ts:%Y-%m-%d}",
        strategies=len(strats), trades=n_trades, months=len(buckets),
        series={m: {g: [round(x, 5) for x in v] for g, v in gs.items()}
                for m, gs in buckets.items()})
    path = f"{OUT}/{sym}_{tf}.json"
    json.dump(payload, open(path, "w"))
    print(f"{sym} {tf}m: {len(strats):,} strategies, {n_trades:,} trades, "
          f"{len(buckets)} months [{payload['span']}]", flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
