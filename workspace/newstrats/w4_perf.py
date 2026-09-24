"""Full statistics for the ICT block/gap conditions - the performance database.

The mandate asks for win rate, average win and loss, profit factor, expectancy,
max and average drawdown, Sharpe, Sortino, R/R, trade count, consecutive wins
and losses, average duration, MAE and MFE, and a breakdown by session,
timeframe and regime. ``compute_metrics`` and ``summarise`` produce all of it,
so this file is mostly a matter of choosing WHICH rule sets deserve the
reporting - and reporting them floor-free, because a 20-trade floor selects on
exit geometry rather than on signal quality.

Reported for every condition: solo, and paired with the two base rule sets that
gave it the largest and smallest in-sample delta, so nobody can read the table
as a league of winners.
"""
from __future__ import annotations

import json
import sys
from typing import Dict, List

sys.path.insert(0, "/home/user/Futures01")
sys.path.insert(0, "/home/user/Futures01/workspace/studies")

import toolkit as T                                    # noqa: E402
from futures_agents.backtest.engine import run_portfolio  # noqa: E402
from futures_agents.backtest.metrics import compute_metrics, summarise  # noqa: E402
from futures_agents.strategies.library import CONDITIONS  # noqa: E402

from workspace.newstrats import ict_blocks as I        # noqa: E402
from workspace.newstrats.w4_arms import (BASES, CELLS, SCRATCH, build,
                                         split_frames)

HEADLINE = ["ict_ob_fresh", "ict_fvg_fresh", "ict_ob_return", "ict_fvg_return",
            "ict_fvg_newest", "ict_ob_newest", "ict_breaker_fresh",
            "ict_ifvg_fresh", "ict_placebo_ob", "ict_placebo_fvg"]
PAIRED_WITH = ["structure_trend", "ema_stack", "above_vwap"]


def main():
    plans = [(f"SOLO::{c}", [c]) for c in HEADLINE]
    for c in HEADLINE:
        for b in PAIRED_WITH:
            plans.append((f"{c}+{b}", [b, c]))
    for b in PAIRED_WITH:
        plans.append((f"BASE::{b}", [b]))

    db = {"schema": "cell -> period -> rule set -> full metrics + session/regime slices",
          "floor": "NONE - floor-free census, every rule set reported at its own n",
          "conditions": HEADLINE, "paired_with": PAIRED_WITH,
          "cells": {}}
    for sym, tf in CELLS:
        isf, oosf = split_frames(sym, tf)
        labelled = [(lab, build(sym, tf, lab, names)) for lab, names in plans]
        uniq = {}
        for _, s in labelled:
            uniq.setdefault(s.strategy_id, s)
        row = {}
        for per, frame in (("IS", isf), ("OOS", oosf)):
            res = run_portfolio(frame, list(uniq.values()))
            rec = {}
            for lab, s in labelled:
                tr = res[s.strategy_id].trades
                if not tr:
                    rec[lab] = {"n": 0}
                    continue
                summ = summarise(tr, slices=("regime", "session"), min_trades=8)
                rec[lab] = summ
            row[per] = rec
        db["cells"][f"{sym}-{tf}m"] = row
        m = row["IS"].get("SOLO::ict_fvg_fresh", {}).get("overall", {})
        print(f"{sym}-{tf}m solo fvg_fresh IS n={m.get('trades')} "
              f"exp={m.get('expectancy_r')} win={m.get('win_rate')} "
              f"pf={m.get('profit_factor')}")
        sys.stdout.flush()
    with open(f"{SCRATCH}/w4_perf.json", "w") as fh:
        json.dump(db, fh, indent=1, default=str)
    return db


if __name__ == "__main__":
    main()
