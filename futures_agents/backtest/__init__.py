"""Backtesting: costs, the engine, metrics, walk-forward and robustness."""

from .costs import CostModel, FillModel, SlippageModel
from .engine import (BacktestEngine, BacktestResult, Trade, TradeLeg,
                     ExitReason, run_backtest, run_portfolio)
from .metrics import Metrics, compute_metrics, summarise, slice_metrics
from .walkforward import (WalkForwardResult, Fold, walk_forward, split_in_out,
                          rolling_windows)
from .montecarlo import MonteCarloResult, monte_carlo, risk_of_ruin
from .robustness import (RobustnessReport, assess_robustness, parameter_sensitivity,
                         BIAS_CHECKS, check_biases)

__all__ = [
    "CostModel", "FillModel", "SlippageModel",
    "BacktestEngine", "BacktestResult", "Trade", "TradeLeg", "ExitReason",
    "run_backtest", "run_portfolio",
    "Metrics", "compute_metrics", "summarise", "slice_metrics",
    "WalkForwardResult", "Fold", "walk_forward", "split_in_out", "rolling_windows",
    "MonteCarloResult", "monte_carlo", "risk_of_ruin",
    "RobustnessReport", "assess_robustness", "parameter_sensitivity",
    "BIAS_CHECKS", "check_biases",
]
