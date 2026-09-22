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

**Registration is not reachability.** The first version of this module checked
only that each named condition was a registered key, and three reviewers
independently found the same hole: fourteen of seventy-three conditions could
not appear in any generated strategy, because the combinator draws SIGNAL
conditions from a template's condition groups and FILTER conditions only from
its filter lists - so a filter nobody listed was unreachable by construction,
and a SIGNAL whose group no template names was too. Half of the specification
variables this module printed as covered were leaning on at least one of them.
A variable no generated strategy can express is not covered, whatever the
registry says, so :func:`uncovered` now answers the reachability question.
"""

from __future__ import annotations

from typing import Dict, List, Set, Tuple

from .base import ConditionKind
from .library import CONDITIONS, CONDITION_GROUPS

__all__ = ["SPEC_CONFLUENCES", "coverage_report", "uncovered",
           "reachable_conditions", "unreachable_conditions"]


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


#: Conditions deliberately kept out of the generated universe, with the
#: reason. An exemption has to be written down: the whole point of the
#: reachability check is that a condition nothing can draw is invisible, and
#: an unexplained exemption recreates exactly that blind spot.
NOT_GENERATED: Dict[str, str] = {
    "post_news_window": (
        "passes 0.66% of bars, so as an optional filter its treatment arm "
        "takes almost no trades - 0 of 50 MOMENTUM strategies cleared the "
        "30-trade floor with it against 22 of 50 without. An arm that cannot "
        "produce a sample is not a control. It stays registered because the "
        "question it asks - is the post-release reaction tradeable - is a "
        "real one, answered as a slice over realised trades where every "
        "trade contributes, rather than as a filter that leaves five."),
}


def reachable_conditions() -> Set[str]:
    """Conditions a generated strategy can actually contain.

    Mirrors what the combinator does rather than restating it: a SIGNAL is
    drawable when some template names its group in ``required_groups`` or
    ``optional_groups``; a FILTER is drawable only when some template names it
    directly in ``base_filters`` or ``optional_filters``. Imported lazily
    because the combinator imports this module's sibling.
    """
    from .combinator import TEMPLATES

    out: Set[str] = set()
    for template in TEMPLATES:
        for group in tuple(template.required_groups) + tuple(template.optional_groups):
            for name in CONDITION_GROUPS.get(group, ()):
                if CONDITIONS[name].kind is ConditionKind.SIGNAL:
                    out.add(name)
        for name in tuple(template.base_filters) + tuple(template.optional_filters):
            if name in CONDITIONS:
                out.add(name)
    # Documented exemptions count as reachable for coverage purposes; they are
    # usable, just not generated into strategies.
    out.update(n for n in NOT_GENERATED if n in CONDITIONS)
    return out


def unreachable_conditions() -> List[str]:
    """Registered conditions no template can draw. Should always be empty."""
    return sorted(set(CONDITIONS) - reachable_conditions())


def uncovered() -> Dict[str, List[str]]:
    """Specification variables with no reachable condition behind them.

    Two ways to fail, and the second is the one that actually happened:
    a named condition that is not registered at all (the map claims coverage
    the library cannot deliver), or one that is registered and unreachable
    (the library claims coverage the *generator* cannot deliver). The value is
    the list of named conditions that failed, so the report can say which.
    """
    reachable = reachable_conditions()
    out: Dict[str, List[str]] = {}
    for variable, names in SPEC_CONFLUENCES.items():
        bad = [n for n in names if n not in CONDITIONS or n not in reachable]
        if not names or len(bad) == len(names):
            out[variable] = bad
    return out


def weakly_covered() -> Dict[str, List[str]]:
    """Variables that are covered, but with at least one dead condition behind
    them. Not a failure - the variable is still tradeable - but it means the
    map is quoting something the generator will never build."""
    reachable = reachable_conditions()
    out: Dict[str, List[str]] = {}
    for variable, names in SPEC_CONFLUENCES.items():
        bad = [n for n in names if n not in CONDITIONS or n not in reachable]
        if bad and len(bad) < len(names):
            out[variable] = bad
    return out


def coverage_report() -> str:
    gaps = uncovered()
    weak = weakly_covered()
    dead = unreachable_conditions()
    lines = [f"CONFLUENCE COVERAGE  ({len(CONDITIONS)} conditions registered, "
             f"{len(reachable_conditions())} reachable)",
             f"  specification variables : {len(SPEC_CONFLUENCES)}",
             f"  covered                 : {len(SPEC_CONFLUENCES) - len(gaps)}"]
    if dead:
        lines.append(f"  UNREACHABLE conditions  : {len(dead)} -> {', '.join(dead)}")
    if weak:
        lines.append(f"  covered but quoting a dead condition : {len(weak)}")
        for variable, bad in sorted(weak.items()):
            lines.append(f"      - {variable}: {', '.join(bad)}")
    if gaps:
        lines.append(f"  MISSING                 : {len(gaps)}")
        for variable, missing in sorted(gaps.items()):
            lines.append(f"      - {variable}: {', '.join(missing) or 'no conditions mapped'}")
    else:
        lines.append("  MISSING                 : none")
    return "\n".join(lines)
