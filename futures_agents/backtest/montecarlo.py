"""Monte Carlo resampling and risk of ruin.

A backtest reports one path. The market could just as easily have dealt the
same trades in a different order, and that order decides whether a $50,000
account survives. A strategy with genuinely positive expectancy can still
destroy an account if an unlucky ordering of its losses arrives before the
drawdown allowance is rebuilt.

So the question this module answers is not "what did it make" but **"across
thousands of plausible reorderings, how often does the account fail?"** That is
the number the risk layer actually cares about.

Resampling with replacement deliberately breaks any serial dependence in the
trade sequence. Where a strategy's edge genuinely depends on streaks, the
``block`` mode preserves local ordering so the two can be compared.
"""

from __future__ import annotations

import math
import random
import statistics
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from .metrics import Metrics, compute_metrics, max_drawdown

__all__ = ["MonteCarloResult", "monte_carlo", "risk_of_ruin", "bootstrap_paths"]


def _percentile(sorted_values: Sequence[float], q: float) -> float:
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    pos = q * (len(sorted_values) - 1)
    lo = int(math.floor(pos))
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = pos - lo
    return sorted_values[lo] * (1 - frac) + sorted_values[hi] * frac


@dataclass
class MonteCarloResult:
    """Distribution of outcomes across resampled trade orderings."""

    runs: int = 0
    trades_per_run: int = 0
    mode: str = "iid"

    final_r_mean: float = 0.0
    final_r_median: float = 0.0
    final_r_p05: float = 0.0
    final_r_p25: float = 0.0
    final_r_p75: float = 0.0
    final_r_p95: float = 0.0
    prob_profitable: float = 0.0

    max_dd_mean: float = 0.0
    max_dd_median: float = 0.0
    max_dd_p95: float = 0.0
    max_dd_worst: float = 0.0

    longest_losing_streak_p95: int = 0
    longest_losing_streak_worst: int = 0

    #: Probability the account hits its failure threshold, when dollar risk is
    #: supplied. This is the headline number for account survival.
    probability_of_ruin: float = 0.0
    median_final_equity: float = 0.0
    p05_final_equity: float = 0.0
    worst_final_equity: float = 0.0

    def to_dict(self) -> dict:
        return {k: (round(v, 5) if isinstance(v, float) else v)
                for k, v in self.__dict__.items()}

    def summary(self) -> str:
        return (f"MC({self.runs}x{self.trades_per_run}): median {self.final_r_median:+.2f}R, "
                f"p05 {self.final_r_p05:+.2f}R, P(profit) {self.prob_profitable:.0%}, "
                f"maxDD p95 {self.max_dd_p95:.2f}R, P(ruin) {self.probability_of_ruin:.2%}")


def bootstrap_paths(r_values: Sequence[float], runs: int, length: Optional[int],
                    *, mode: str = "iid", block: int = 10,
                    rng: Optional[random.Random] = None) -> List[List[float]]:
    """Generate resampled trade sequences.

    ``mode="iid"`` samples trades independently with replacement - the standard
    approach, and the right one when trades are assumed independent.
    ``mode="block"`` samples contiguous blocks, preserving short-range
    dependence such as a strategy's tendency to lose several times in a row in
    an unfavourable regime.
    """
    rng = rng or random.Random(20260922)
    n = len(r_values)
    if n == 0:
        return []
    length = length or n
    paths: List[List[float]] = []
    for _ in range(runs):
        if mode == "block":
            path: List[float] = []
            while len(path) < length:
                start = rng.randrange(n)
                path.extend(r_values[start:start + block] or [r_values[start]])
            paths.append(path[:length])
        else:
            paths.append([r_values[rng.randrange(n)] for _ in range(length)])
    return paths


def monte_carlo(
    r_values: Sequence[float],
    *,
    runs: int = 2_000,
    length: Optional[int] = None,
    mode: str = "iid",
    block: int = 10,
    seed: int = 20260922,
    # Account-aware ruin simulation (optional but strongly recommended)
    starting_equity: Optional[float] = None,
    dollar_risk_per_trade: Optional[float] = None,
    failure_drawdown: Optional[float] = None,
    trailing_drawdown: bool = True,
) -> MonteCarloResult:
    """Resample the trade sequence and summarise the distribution of outcomes.

    When ``starting_equity``, ``dollar_risk_per_trade`` and ``failure_drawdown``
    are supplied, each path is also walked in dollar space against the account's
    real failure threshold, producing :attr:`MonteCarloResult.probability_of_ruin`.
    """
    res = MonteCarloResult(runs=runs, mode=mode)
    values = [float(v) for v in r_values]
    if not values:
        return res
    res.trades_per_run = length or len(values)

    rng = random.Random(seed)
    paths = bootstrap_paths(values, runs, length, mode=mode, block=block, rng=rng)
    if not paths:
        return res

    finals: List[float] = []
    dds: List[float] = []
    streaks: List[int] = []
    ruins = 0
    final_equities: List[float] = []

    for path in paths:
        finals.append(sum(path))
        dd, _ = max_drawdown(path)
        dds.append(dd)

        streak = worst = 0
        for r in path:
            if r < 0:
                streak += 1
                worst = max(worst, streak)
            else:
                streak = 0
        streaks.append(worst)

        if (starting_equity is not None and dollar_risk_per_trade
                and failure_drawdown):
            equity = starting_equity
            peak = starting_equity
            failed = False
            for r in path:
                equity += r * dollar_risk_per_trade
                peak = max(peak, equity)
                anchor = peak if trailing_drawdown else starting_equity
                if equity <= anchor - failure_drawdown:
                    failed = True
                    break
            final_equities.append(equity)
            if failed:
                ruins += 1

    finals.sort()
    dds.sort()
    streaks.sort()

    res.final_r_mean = statistics.fmean(finals)
    res.final_r_median = _percentile(finals, 0.50)
    res.final_r_p05 = _percentile(finals, 0.05)
    res.final_r_p25 = _percentile(finals, 0.25)
    res.final_r_p75 = _percentile(finals, 0.75)
    res.final_r_p95 = _percentile(finals, 0.95)
    res.prob_profitable = sum(1 for f in finals if f > 0) / len(finals)

    res.max_dd_mean = statistics.fmean(dds)
    res.max_dd_median = _percentile(dds, 0.50)
    res.max_dd_p95 = _percentile(dds, 0.95)
    res.max_dd_worst = dds[-1]

    res.longest_losing_streak_p95 = int(_percentile([float(s) for s in streaks], 0.95))
    res.longest_losing_streak_worst = streaks[-1]

    if final_equities:
        res.probability_of_ruin = ruins / len(paths)
        eq = sorted(final_equities)
        res.median_final_equity = _percentile(eq, 0.50)
        res.p05_final_equity = _percentile(eq, 0.05)
        res.worst_final_equity = eq[0]
    return res


def risk_of_ruin(
    r_values: Sequence[float],
    *,
    starting_equity: float = 50_000.0,
    dollar_risk_per_trade: float = 300.0,
    failure_drawdown: float = 5_000.0,
    trades: int = 250,
    runs: int = 5_000,
    trailing_drawdown: bool = True,
    seed: int = 20260922,
) -> Dict[str, float]:
    """Probability that an account fails over the next ``trades`` trades.

    The default horizon is roughly a year of trading at a few trades a week.
    The output is deliberately blunt: a strategy whose probability of ruin is
    8% is not a strategy to trade a $50,000 account with, whatever its
    expectancy looks like.
    """
    mc = monte_carlo(r_values, runs=runs, length=trades, seed=seed,
                     starting_equity=starting_equity,
                     dollar_risk_per_trade=dollar_risk_per_trade,
                     failure_drawdown=failure_drawdown,
                     trailing_drawdown=trailing_drawdown)
    return {
        "probability_of_ruin": round(mc.probability_of_ruin, 5),
        "median_final_equity": round(mc.median_final_equity, 2),
        "p05_final_equity": round(mc.p05_final_equity, 2),
        "worst_final_equity": round(mc.worst_final_equity, 2),
        "max_dd_p95_r": round(mc.max_dd_p95, 3),
        "longest_losing_streak_p95": mc.longest_losing_streak_p95,
        "prob_profitable": round(mc.prob_profitable, 4),
        "trades_simulated": trades,
        "runs": runs,
    }
