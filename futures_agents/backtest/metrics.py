"""Performance metrics.

Everything is computed in R (multiples of the initial stop) so results are
comparable across symbols, timeframes and account sizes.

Two metrics deserve a note because they are what stop the system from chasing
curve-fitted equity curves:

* **Expectancy in R** is the headline, not total profit. Total profit is a
  function of position size and sample length; expectancy is a property of the
  edge.
* **The t-statistic of the R series** answers the question that win rate and
  profit factor cannot: is this distinguishable from luck at this sample size?
  A profit factor of 1.8 over 18 trades is noise; the same figure over 400 is
  evidence.
"""

from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Sequence, Tuple

from .engine import ExitReason, Trade

__all__ = ["Metrics", "compute_metrics", "summarise", "slice_metrics",
           "drawdown_series", "max_drawdown"]

#: Bars per year used to annualise the Sharpe/Sortino of a trade series.
TRADING_DAYS_PER_YEAR = 252


@dataclass
class Metrics:
    """The full statistical profile of a set of trades."""

    trades: int = 0
    wins: int = 0
    losses: int = 0
    scratches: int = 0
    win_rate: float = 0.0
    loss_rate: float = 0.0

    total_r: float = 0.0
    expectancy_r: float = 0.0
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    median_r: float = 0.0
    largest_win_r: float = 0.0
    largest_loss_r: float = 0.0
    profit_factor: float = 0.0
    payoff_ratio: float = 0.0            # avg win / avg loss
    total_dollars: float = 0.0

    max_drawdown_r: float = 0.0
    avg_drawdown_r: float = 0.0
    max_drawdown_trades: int = 0         # longest drawdown, in trades
    recovery_factor: float = 0.0
    ulcer_index: float = 0.0

    sharpe: float = 0.0                  # per-trade
    sharpe_annualised: float = 0.0
    sortino: float = 0.0
    sqn: float = 0.0                     # System Quality Number
    t_statistic: float = 0.0
    std_r: float = 0.0
    downside_deviation: float = 0.0

    max_consecutive_wins: int = 0
    max_consecutive_losses: int = 0
    current_streak: int = 0

    avg_bars_held: float = 0.0
    avg_minutes_held: float = 0.0
    avg_mfe_r: float = 0.0
    avg_mae_r: float = 0.0
    avg_mae_r_winners: float = 0.0
    avg_mfe_r_losers: float = 0.0
    edge_ratio: float = 0.0              # avg MFE / avg MAE

    long_trades: int = 0
    short_trades: int = 0
    long_expectancy_r: float = 0.0
    short_expectancy_r: float = 0.0

    exit_reasons: Dict[str, int] = field(default_factory=dict)
    trades_per_day: float = 0.0
    trading_days: int = 0

    def to_dict(self) -> dict:
        d = asdict(self)
        return {k: (round(v, 5) if isinstance(v, float) else v) for k, v in d.items()}

    @property
    def is_profitable(self) -> bool:
        return self.expectancy_r > 0

    def summary(self) -> str:
        return (f"{self.trades} trades | win {self.win_rate * 100:.1f}% | "
                f"PF {self.profit_factor:.2f} | exp {self.expectancy_r:+.3f}R | "
                f"maxDD {self.max_drawdown_r:.2f}R | SQN {self.sqn:.2f} | "
                f"t {self.t_statistic:.2f}")


def drawdown_series(r_values: Sequence[float]) -> List[float]:
    """Drawdown (in R, non-negative) after each trade."""
    out, peak, eq = [], 0.0, 0.0
    for r in r_values:
        eq += r
        peak = max(peak, eq)
        out.append(peak - eq)
    return out


def max_drawdown(r_values: Sequence[float]) -> Tuple[float, int]:
    """Maximum drawdown in R and its longest duration in trades."""
    dd = drawdown_series(r_values)
    if not dd:
        return 0.0, 0
    longest = current = 0
    for d in dd:
        current = current + 1 if d > 1e-12 else 0
        longest = max(longest, current)
    return max(dd), longest


def _safe_mean(xs: Sequence[float]) -> float:
    return statistics.fmean(xs) if xs else 0.0


def compute_metrics(trades: Sequence[Trade], *,
                    scratch_threshold: float = 1e-9) -> Metrics:
    """Compute the full metric set for a list of trades."""
    m = Metrics()
    m.trades = len(trades)
    if not trades:
        return m

    r = [t.net_r for t in trades]
    m.total_r = sum(r)
    m.total_dollars = sum(t.net_dollars for t in trades)
    m.expectancy_r = m.total_r / m.trades
    m.median_r = statistics.median(r)

    wins = [x for x in r if x > scratch_threshold]
    losses = [x for x in r if x < -scratch_threshold]
    m.wins, m.losses = len(wins), len(losses)
    m.scratches = m.trades - m.wins - m.losses
    m.win_rate = m.wins / m.trades
    m.loss_rate = m.losses / m.trades
    m.avg_win_r = _safe_mean(wins)
    m.avg_loss_r = _safe_mean(losses)
    m.largest_win_r = max(wins) if wins else 0.0
    m.largest_loss_r = min(losses) if losses else 0.0

    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    # Profit factor is undefined with no losses; reporting "infinity" invites
    # ranking a 3-trade sample above a 400-trade one, so it is capped.
    m.profit_factor = (gross_win / gross_loss if gross_loss > 0
                       else (999.0 if gross_win > 0 else 0.0))
    m.payoff_ratio = (abs(m.avg_win_r / m.avg_loss_r) if m.avg_loss_r else 0.0)

    m.max_drawdown_r, m.max_drawdown_trades = max_drawdown(r)
    dd = drawdown_series(r)
    active_dd = [d for d in dd if d > 1e-12]
    m.avg_drawdown_r = _safe_mean(active_dd)
    m.recovery_factor = (m.total_r / m.max_drawdown_r) if m.max_drawdown_r > 0 else 0.0
    m.ulcer_index = math.sqrt(_safe_mean([d * d for d in dd])) if dd else 0.0

    if m.trades > 1:
        m.std_r = statistics.stdev(r)
        if m.std_r > 0:
            m.sharpe = m.expectancy_r / m.std_r
            m.t_statistic = m.expectancy_r / (m.std_r / math.sqrt(m.trades))
            m.sqn = math.sqrt(m.trades) * m.expectancy_r / m.std_r
        downside = [min(0.0, x) for x in r]
        m.downside_deviation = math.sqrt(_safe_mean([d * d for d in downside]))
        if m.downside_deviation > 0:
            m.sortino = m.expectancy_r / m.downside_deviation

    days = {t.trading_day for t in trades}
    m.trading_days = len(days)
    m.trades_per_day = m.trades / max(1, m.trading_days)
    if m.trading_days > 0 and m.std_r > 0:
        # Annualise using the observed trade frequency rather than assuming one
        # trade per day - an intraday system trading 4x a day has a very
        # different annualised profile from one trading weekly.
        per_year = m.trades_per_day * TRADING_DAYS_PER_YEAR
        m.sharpe_annualised = m.sharpe * math.sqrt(max(1.0, per_year))

    streak = best_w = best_l = 0
    for x in r:
        if x > scratch_threshold:
            streak = streak + 1 if streak > 0 else 1
            best_w = max(best_w, streak)
        elif x < -scratch_threshold:
            streak = streak - 1 if streak < 0 else -1
            best_l = max(best_l, -streak)
        else:
            streak = 0
    m.max_consecutive_wins, m.max_consecutive_losses = best_w, best_l
    m.current_streak = streak

    m.avg_bars_held = _safe_mean([t.bars_held for t in trades])
    m.avg_minutes_held = _safe_mean([t.minutes_held for t in trades])
    m.avg_mfe_r = _safe_mean([t.mfe_r for t in trades])
    m.avg_mae_r = _safe_mean([t.mae_r for t in trades])
    m.avg_mae_r_winners = _safe_mean([t.mae_r for t in trades if t.net_r > 0])
    m.avg_mfe_r_losers = _safe_mean([t.mfe_r for t in trades if t.net_r <= 0])
    m.edge_ratio = (m.avg_mfe_r / m.avg_mae_r) if m.avg_mae_r > 0 else 0.0

    longs = [t for t in trades if t.direction.sign > 0]
    shorts = [t for t in trades if t.direction.sign < 0]
    m.long_trades, m.short_trades = len(longs), len(shorts)
    m.long_expectancy_r = _safe_mean([t.net_r for t in longs])
    m.short_expectancy_r = _safe_mean([t.net_r for t in shorts])

    m.exit_reasons = dict(Counter(t.exit_reason.value for t in trades))
    return m


#: How the specification asks performance to be sliced.
SLICE_KEYS: Dict[str, Callable[[Trade], Any]] = {
    "symbol": lambda t: t.symbol,
    "group": lambda t: t.group,
    "timeframe": lambda t: t.primary_tf,
    "regime": lambda t: t.regime,
    "volatility": lambda t: t.volatility,
    "session": lambda t: t.session,
    "time_bucket": lambda t: t.time_bucket,
    "day_of_week": lambda t: t.day_of_week,
    "direction": lambda t: t.direction.value,
    "exit_reason": lambda t: t.exit_reason.value,
}


def slice_metrics(trades: Sequence[Trade], key: str,
                  *, min_trades: int = 1) -> Dict[Any, Metrics]:
    """Metrics grouped by one dimension.

    ``min_trades`` exists because a slice with four trades in it is not a
    finding, and presenting it next to a slice with four hundred invites
    exactly the wrong conclusion.
    """
    if key not in SLICE_KEYS:
        raise KeyError(f"Unknown slice {key!r}. Available: {', '.join(SLICE_KEYS)}")
    fn = SLICE_KEYS[key]
    buckets: Dict[Any, List[Trade]] = defaultdict(list)
    for t in trades:
        buckets[fn(t)].append(t)
    return {k: compute_metrics(v) for k, v in sorted(buckets.items(), key=lambda kv: str(kv[0]))
            if len(v) >= min_trades}


def summarise(trades: Sequence[Trade], *, slices: Sequence[str] = ("regime", "session"),
              min_trades: int = 10) -> dict:
    """Overall metrics plus the requested slices, ready for JSON or a report."""
    overall = compute_metrics(trades)
    out = {"overall": overall.to_dict(), "slices": {}}
    for key in slices:
        out["slices"][key] = {
            str(k): v.to_dict()
            for k, v in slice_metrics(trades, key, min_trades=min_trades).items()}
    return out
