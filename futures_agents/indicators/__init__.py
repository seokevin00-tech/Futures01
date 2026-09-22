"""Deterministic, look-ahead-free indicator primitives.

Every function in this package returns a list the same length as its input,
with ``None`` in the warm-up positions. That convention is what makes
alignment errors impossible: index *i* of any output always corresponds to bar
*i* of the input, and a value that could not have been computed in real time is
``None`` rather than a silently back-filled number.

No function ever reads an index greater than the one it is computing.
"""

from .core import (
    sma, ema, wma, rma, stdev, rsi, macd, bollinger, true_range, atr,
    rolling_max, rolling_min, roc, momentum, linreg_slope, zscore,
    stochastic, adx, keltner, fib_levels, pivot_points, crossed_above,
    crossed_below, slope_normalised, percent_rank,
)
from .volume import (
    vwap, vwap_bands, anchored_vwap, cumulative_delta, delta_series,
    volume_profile, VolumeProfile, relative_volume, volume_regime,
    delta_divergence,
)
from .structure import (
    Swing, find_swings, market_structure, support_resistance, SRLevel,
    opening_range, OpeningRange, session_levels, SessionLevels,
    fair_value_gaps, FVG, liquidity_sweeps, Sweep, detect_imbalances,
)
from .regime import (
    classify_regime, volatility_regime, RegimeSnapshot, trend_strength,
)

__all__ = [
    # core
    "sma", "ema", "wma", "rma", "stdev", "rsi", "macd", "bollinger",
    "true_range", "atr", "rolling_max", "rolling_min", "roc", "momentum",
    "linreg_slope", "zscore", "stochastic", "adx", "keltner", "fib_levels",
    "pivot_points", "crossed_above", "crossed_below", "slope_normalised",
    "percent_rank",
    # volume
    "vwap", "vwap_bands", "anchored_vwap", "cumulative_delta", "delta_series",
    "volume_profile", "VolumeProfile", "relative_volume", "volume_regime",
    "delta_divergence",
    # structure
    "Swing", "find_swings", "market_structure", "support_resistance", "SRLevel",
    "opening_range", "OpeningRange", "session_levels", "SessionLevels",
    "fair_value_gaps", "FVG", "liquidity_sweeps", "Sweep", "detect_imbalances",
    # regime
    "classify_regime", "volatility_regime", "RegimeSnapshot", "trend_strength",
]
