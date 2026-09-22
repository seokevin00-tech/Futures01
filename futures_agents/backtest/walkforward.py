"""In-sample / out-of-sample splitting, walk-forward analysis and rolling windows.

The point of this module is to make it *hard* to fool yourself. Optimising a
strategy over the whole dataset and reporting the result is not a backtest, it
is a description of the past. Walk-forward analysis answers the only question
that matters: when a selection rule was applied using data available at the
time, how did the selected strategies then perform on data nobody had seen?

Every number in a :class:`WalkForwardResult` that the system acts on comes from
out-of-sample segments. The in-sample numbers exist only to make the
degradation visible.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import (Any, Callable, Dict, Iterable, List, Optional, Sequence,
                    Tuple)

from ..features import SymbolFrame
from ..strategies.base import Strategy
from .costs import CostModel
from .engine import BacktestEngine, BacktestResult, Trade
from .metrics import Metrics, compute_metrics

__all__ = ["Fold", "WalkForwardResult", "split_in_out", "rolling_windows",
           "walk_forward", "robust_score", "anchored_windows"]


# --------------------------------------------------------------------------
# Selection criterion
# --------------------------------------------------------------------------

def robust_score(m: Metrics, *, min_trades: int = 30) -> float:
    """Rank strategies by durability, not by profit.

    The components, in order of influence:

    * expectancy in R - the edge itself;
    * a sample-size penalty that goes to zero below ``min_trades``, so a
      six-trade wonder cannot outrank a four-hundred-trade grinder;
    * the t-statistic, which asks whether the edge is distinguishable from luck;
    * a drawdown penalty, because a $50,000 account cannot sit through a 20R
      drawdown whatever the long-run expectancy is;
    * a consecutive-loss penalty, for the same reason.

    Total profit is deliberately absent. Ranking on it is how systems end up
    selecting whichever strategy happened to catch the single largest move in
    the sample.
    """
    if m.trades <= 0:
        return 0.0
    sample = min(1.0, m.trades / float(min_trades))
    if m.trades < min_trades * 0.5:
        sample *= 0.4                      # severe penalty, not just a small one

    edge = m.expectancy_r
    if edge <= 0:
        return edge * sample               # negative edges stay negative

    significance = max(0.0, min(2.0, m.t_statistic)) / 2.0
    dd_penalty = 1.0 / (1.0 + max(0.0, m.max_drawdown_r) / 8.0)
    streak_penalty = 1.0 / (1.0 + max(0, m.max_consecutive_losses - 4) / 5.0)
    consistency = max(0.0, min(1.0, m.profit_factor - 1.0))

    return (edge * sample
            * (0.45 + 0.30 * significance + 0.25 * consistency)
            * dd_penalty * streak_penalty)


# --------------------------------------------------------------------------
# Window construction
# --------------------------------------------------------------------------

def split_in_out(n_bars: int, in_sample_fraction: float = 0.6
                 ) -> Tuple[Tuple[int, int], Tuple[int, int]]:
    """A single chronological IS/OOS split. Never random - shuffling time-series
    data leaks the future into the training set."""
    if not 0.0 < in_sample_fraction < 1.0:
        raise ValueError("in_sample_fraction must be strictly between 0 and 1")
    cut = int(n_bars * in_sample_fraction)
    return (0, cut), (cut, n_bars)


def rolling_windows(n_bars: int, train_bars: int, test_bars: int,
                    step: Optional[int] = None) -> List[Tuple[Tuple[int, int],
                                                              Tuple[int, int]]]:
    """Non-overlapping test segments with a rolling (fixed-length) train window."""
    if train_bars <= 0 or test_bars <= 0:
        raise ValueError("train_bars and test_bars must be positive")
    step = step or test_bars
    out = []
    start = 0
    while start + train_bars + test_bars <= n_bars:
        train = (start, start + train_bars)
        test = (start + train_bars, start + train_bars + test_bars)
        out.append((train, test))
        start += step
    return out


def anchored_windows(n_bars: int, folds: int, min_train_fraction: float = 0.3
                     ) -> List[Tuple[Tuple[int, int], Tuple[int, int]]]:
    """Expanding (anchored) train window, fixed-size test segments.

    Anchored windows mirror how the system actually accumulates knowledge: it
    never forgets older history, it just keeps adding to it.
    """
    if folds < 1:
        raise ValueError("folds must be >= 1")
    first_train = int(n_bars * min_train_fraction)
    remaining = n_bars - first_train
    if remaining <= 0:
        return []
    seg = remaining // folds
    if seg <= 0:
        return []
    out = []
    for k in range(folds):
        train_end = first_train + k * seg
        test_end = train_end + seg
        if test_end > n_bars:
            break
        out.append(((0, train_end), (train_end, test_end)))
    return out


# --------------------------------------------------------------------------
# Walk-forward
# --------------------------------------------------------------------------

@dataclass
class Fold:
    """One train/test pair and what happened in it."""

    index: int
    train_range: Tuple[int, int]
    test_range: Tuple[int, int]
    train_start_ts: Optional[datetime] = None
    test_start_ts: Optional[datetime] = None
    test_end_ts: Optional[datetime] = None
    selected: List[str] = field(default_factory=list)
    in_sample: Dict[str, Metrics] = field(default_factory=dict)
    out_of_sample: Dict[str, Metrics] = field(default_factory=dict)
    oos_trades: List[Trade] = field(default_factory=list)

    @property
    def is_expectancy(self) -> float:
        vals = [m.expectancy_r for sid, m in self.in_sample.items() if sid in self.selected]
        return sum(vals) / len(vals) if vals else 0.0

    @property
    def oos_expectancy(self) -> float:
        vals = [m.expectancy_r for sid, m in self.out_of_sample.items()
                if sid in self.selected]
        return sum(vals) / len(vals) if vals else 0.0

    def to_dict(self) -> dict:
        return {
            "index": self.index,
            "train_range": list(self.train_range), "test_range": list(self.test_range),
            "test_start": self.test_start_ts.isoformat() if self.test_start_ts else None,
            "test_end": self.test_end_ts.isoformat() if self.test_end_ts else None,
            "selected": self.selected,
            "is_expectancy_r": round(self.is_expectancy, 4),
            "oos_expectancy_r": round(self.oos_expectancy, 4),
            "oos_trades": len(self.oos_trades),
        }


@dataclass
class WalkForwardResult:
    """Aggregated walk-forward outcome for one symbol."""

    symbol: str
    folds: List[Fold] = field(default_factory=list)
    combined_oos: Metrics = field(default_factory=Metrics)
    combined_is: Metrics = field(default_factory=Metrics)
    per_strategy_oos: Dict[str, Metrics] = field(default_factory=dict)
    selection_stability: float = 0.0     # how often the same strategies are chosen
    efficiency: float = 0.0              # OOS expectancy / IS expectancy

    @property
    def is_credible(self) -> bool:
        """The bar a symbol's research must clear before it informs live trading."""
        return (self.combined_oos.trades >= 30
                and self.combined_oos.expectancy_r > 0
                and self.efficiency >= 0.35
                and self.selection_stability >= 0.2)

    def to_dict(self) -> dict:
        return {
            "symbol": self.symbol,
            "folds": [f.to_dict() for f in self.folds],
            "combined_oos": self.combined_oos.to_dict(),
            "combined_is": self.combined_is.to_dict(),
            "efficiency": round(self.efficiency, 4),
            "selection_stability": round(self.selection_stability, 4),
            "is_credible": self.is_credible,
        }

    def summary(self) -> str:
        return (f"{self.symbol}: OOS {self.combined_oos.summary()} | "
                f"WF efficiency {self.efficiency:.2f} | "
                f"stability {self.selection_stability:.2f} | "
                f"credible={'yes' if self.is_credible else 'NO'}")


def walk_forward(
    frame: SymbolFrame,
    strategies: Sequence[Strategy],
    *,
    folds: int = 6,
    top_k: int = 5,
    min_trades_is: int = 20,
    anchored: bool = True,
    cost_model: Optional[CostModel] = None,
    min_train_fraction: float = 0.3,
    progress: Optional[Callable[[int, int], None]] = None,
) -> WalkForwardResult:
    """Run walk-forward selection and evaluation over ``frame``.

    For each fold the top ``top_k`` strategies are chosen by
    :func:`robust_score` **using only the training segment**, then evaluated on
    the following unseen segment. The combined out-of-sample trades across all
    folds are the honest estimate of the system's edge.
    """
    bars = len(frame.base)
    windows = (anchored_windows(bars, folds, min_train_fraction) if anchored
               else rolling_windows(bars, int(bars * min_train_fraction),
                                    max(1, int(bars * (1 - min_train_fraction) / folds))))
    result = WalkForwardResult(symbol=frame.symbol)
    if not windows or not strategies:
        return result

    engine = BacktestEngine(frame, cost_model)
    base_bars = frame.base.bars
    all_oos: List[Trade] = []
    all_is: List[Trade] = []
    selections: List[set] = []

    for k, (train, test) in enumerate(windows):
        if progress:
            progress(k, len(windows))

        train_res = engine.run_many(strategies, start=train[0], end=train[1])
        scored: List[Tuple[float, str, Metrics]] = []
        for sid, r in train_res.items():
            m = compute_metrics(r.trades)
            if m.trades < min_trades_is:
                continue
            scored.append((robust_score(m, min_trades=min_trades_is), sid, m))
        scored.sort(key=lambda x: (-x[0], x[1]))
        chosen = [sid for score, sid, _ in scored[:top_k] if score > 0]

        fold = Fold(index=k, train_range=train, test_range=test,
                    train_start_ts=base_bars[train[0]].ts if train[0] < len(base_bars) else None,
                    test_start_ts=base_bars[test[0]].ts if test[0] < len(base_bars) else None,
                    test_end_ts=base_bars[min(test[1], len(base_bars)) - 1].ts,
                    selected=chosen)
        fold.in_sample = {sid: m for _, sid, m in scored}

        if chosen:
            chosen_objs = [s for s in strategies if s.strategy_id in chosen]
            test_res = engine.run_many(chosen_objs, start=test[0], end=test[1])
            for sid, r in test_res.items():
                fold.out_of_sample[sid] = compute_metrics(r.trades)
                fold.oos_trades.extend(r.trades)
                all_oos.extend(r.trades)
            for sid in chosen:
                all_is.extend(train_res[sid].trades)
            selections.append(set(chosen))

        result.folds.append(fold)

    result.combined_oos = compute_metrics(all_oos)
    result.combined_is = compute_metrics(all_is)

    by_strategy: Dict[str, List[Trade]] = {}
    for t in all_oos:
        by_strategy.setdefault(t.strategy_id, []).append(t)
    result.per_strategy_oos = {sid: compute_metrics(ts) for sid, ts in by_strategy.items()}

    if result.combined_is.expectancy_r > 0:
        result.efficiency = result.combined_oos.expectancy_r / result.combined_is.expectancy_r
    elif result.combined_oos.expectancy_r > 0:
        result.efficiency = 1.0

    # Selection stability: mean Jaccard overlap between consecutive folds. A
    # system whose "best" strategy changes completely every fold has not found
    # an edge, it has found noise.
    if len(selections) >= 2:
        overlaps = []
        for a, b in zip(selections, selections[1:]):
            union = a | b
            overlaps.append(len(a & b) / len(union) if union else 0.0)
        result.selection_stability = sum(overlaps) / len(overlaps)
    elif len(selections) == 1:
        result.selection_stability = 1.0

    return result
