"""The specification's confluence list, mapped to the conditions that test it.

The brief names thirty-eight testable variables. Nothing stops a condition
library from quietly covering thirty-one of them and generating confluences
that look complete - the combinator has no idea what it was asked for, and a
missing variable shows up as an absence, which is exactly the kind of defect
that never announces itself. That happened here: volume profile, market
profile, Fibonacci levels, imbalances, supply and demand, open interest and
news conditions all had analytics in the tree and no tradeable condition
attached, so the generator had been drawing from 31 of 38.

This module makes the list an object the test suite can assert against, so
that gap cannot silently reopen. Each entry names the registered conditions
that make its variable *tradeable* - not merely computed somewhere.
"""

from __future__ import annotations

from typing import Dict, List, Tuple

from .library import CONDITIONS

__all__ = ["SPEC_CONFLUENCES", "coverage_report", "uncovered"]


#: Specification variable -> the conditions that let a strategy trade it.
#: Order follows the brief, so the two can be diffed by eye.
SPEC_CONFLUENCES: Dict[str, Tuple[str, ...]] = {
    "price action": ("range_position_extreme", "delta_confirms_bar", "imbalance_bar"),
    "market structure": ("structure_trend", "break_of_structure"),
    "support/resistance": ("pullback_to_support", "fib_sr_confluence"),
    "supply and demand": ("zone_touch", "fresh_zone_approach", "away_from_zone"),
    "vwap": ("above_vwap", "vwap_proximity", "vwap_band_extension",
             "vwap_band1_bounce", "vwap_reclaim"),
    "volume": ("relative_volume_high", "volume_surge", "volume_not_thin"),
    "volume profile": ("poc_reversion", "value_area_edge", "value_area_breakout",
                       "lvn_rejection", "away_from_hvn"),
    "delta": ("delta_confirms_bar", "delta_divergence"),
    "cumulative volume delta": ("cvd_directional",),
    "order flow": ("cvd_directional", "delta_confirms_bar", "delta_divergence"),
    "open interest": ("oi_price_confirmation", "oi_expanding"),
    "momentum": ("rsi_directional", "macd_directional", "stoch_directional",
                 "macd_hist_direction"),
    "volatility": ("volatility_normal", "volatility_expanding", "volatility_compressed"),
    "atr": ("volatility_normal", "volatility_expanding"),
    "moving averages": ("ema_stack", "ema_fast_above_slow", "price_above_ema50",
                        "price_above_ema200"),
    "rsi": ("rsi_directional", "rsi_extreme_reversal"),
    "macd": ("macd_directional", "macd_hist_direction"),
    "bollinger bands": ("bollinger_extreme", "bollinger_mean_pull"),
    "fibonacci levels": ("fib_golden_pocket", "fib_shallow_retrace",
                         "fib_extension_reached", "fib_sr_confluence"),
    "liquidity": ("prior_day_sweep", "overnight_sweep", "session_extreme_sweep"),
    "breakouts": ("prior_day_breakout", "opening_range_breakout",
                  "initial_balance_break", "value_area_breakout"),
    "breakdowns": ("prior_day_breakout", "opening_range_breakout",
                   "break_of_structure"),
    "reversals": ("rsi_extreme_reversal", "stoch_extreme", "opening_range_fade",
                  "fib_extension_reached"),
    "trend continuation": ("ema_stack", "adx_trending", "di_direction",
                           "slope_directional", "efficiency_high",
                           "fib_golden_pocket", "imbalance_pullback"),
    "mean reversion": ("bollinger_mean_pull", "keltner_outside", "poc_reversion"),
    "opening range": ("opening_range_breakout", "opening_range_fade"),
    "previous day high/low": ("prior_day_sweep", "prior_day_breakout"),
    "overnight high/low": ("overnight_sweep",),
    "session highs/lows": ("session_extreme_sweep",),
    "market profile": ("value_area_edge", "value_area_breakout",
                       "open_outside_value", "initial_balance_break"),
    "fair value gaps": ("fvg_nearby",),
    "imbalances": ("imbalance_bar", "imbalance_pullback", "no_recent_imbalance"),
    "divergences": ("delta_divergence",),
    "multi-timeframe structure": ("mtf_aligned", "mtf_not_conflicted"),
    "news conditions": ("outside_news_blackout", "no_imminent_release",
                        "post_news_window"),
    "time-of-day behavior": ("avoid_lunch", "opening_drive_window", "power_hour",
                             "after_opening_range"),
    "volatility regimes": ("regime_trending", "regime_ranging",
                           "regime_matches_direction"),
    "volume regimes": ("relative_volume_high", "volume_surge", "volume_not_thin"),
}


def uncovered() -> Dict[str, List[str]]:
    """Specification variables whose named conditions are not all registered.

    A variable mapped to a condition that does not exist is worse than an
    unmapped one: the map claims coverage the library cannot deliver.
    """
    out: Dict[str, List[str]] = {}
    for variable, names in SPEC_CONFLUENCES.items():
        missing = [n for n in names if n not in CONDITIONS]
        if missing or not names:
            out[variable] = missing
    return out


def coverage_report() -> str:
    gaps = uncovered()
    lines = [f"CONFLUENCE COVERAGE  ({len(CONDITIONS)} conditions registered)",
             f"  specification variables : {len(SPEC_CONFLUENCES)}",
             f"  covered                 : {len(SPEC_CONFLUENCES) - len(gaps)}"]
    if gaps:
        lines.append(f"  MISSING                 : {len(gaps)}")
        for variable, missing in sorted(gaps.items()):
            lines.append(f"      - {variable}: {', '.join(missing) or 'no conditions mapped'}")
    else:
        lines.append("  MISSING                 : none")
    return "\n".join(lines)
