"""Per-symbol research profiles: which strategies each contract is tested with.

The specification is blunt about this - *"Every futures symbol must be treated
independently. The system should not assume that a strategy that works for MNQ
will work equally well for MES, MGC, CL."* Until now the generator ignored it:
every symbol got the same thirteen templates over the same timeframes, and
"independent" meant only that the backtests ran separately. Running the same
hypotheses on three contracts and calling the results independent is a
different claim from testing three contracts on their own terms.

These contracts are not variations on each other. Their own specs say so:

===== ============ ============ =========== ==================================
symbol round-turn   typical ATR  RTH         character
===== ============ ============ =========== ==================================
MNQ    0.97 pt      120 pt       09:30-16:00 widest range of the index micros;
                                             the opening drive is the day
MES    0.54 pt       45 pt       09:30-16:00 tightest, deepest, most rotational
MGC    0.24 pt       28 pt       08:20-13:30 a 23-hour product; its RTH is not
                                             the equity session at all
===== ============ ============ =========== ==================================

MGC is the one that exposes the old approach. Its pit session opens at 08:20
and closes at 13:30, so every equity-session assumption - the 09:30 opening
range, the 15:00 power hour, the 12:00 lunch lull - was being applied to a
contract whose day is shaped differently, and `OPENING_RANGE` with
`max_minutes_since_open=150` was measuring a window that ends before gold's
session does anything interesting.

A profile is a *prior about what to test*, not a claim about what works. It
narrows the hypothesis space per contract, and narrowing it buys back a little
statistical power: at a fixed per-family budget, MNQ's seven families are 622
hypotheses against the full set's 1,154, and the deflation term ``sqrt(2 ln n)``
falls from 3.755 to 3.587 - **0.168 t-units** handed back to the families that
were plausible. MGC's six give 534 and 3.544, or 0.211.

Those are measured, and they are smaller than they sound: a tenth of a t-unit
is not what makes this worth doing. The real argument is that testing gold's
opening range against an equity-session template was never a hypothesis, so
the budget spent on it was not a test that failed - it was a test that could
not have succeeded. Every profile here is falsifiable by the backtester, and
``groups`` can always be overridden to test a family a profile excludes; that
is how a prior gets checked rather than trusted.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, Optional, Sequence, Tuple

__all__ = ["SymbolProfile", "SYMBOL_PROFILES", "profile_for", "groups_for",
           "timeframes_for"]


@dataclass(frozen=True)
class SymbolProfile:
    """What this contract gets tested with, and why."""

    symbol: str
    #: Strategy template groups, most-favoured first.
    groups: Tuple[str, ...]
    #: Timeframes to generate over. Chosen from the contract's own noise floor:
    #: a timeframe whose typical bar range is near the round-turn cost is a
    #: timeframe where the edge has to beat the spread before it beats the
    #: market.
    timeframes: Tuple[int, ...]
    #: Timeframe groups to test confirmation against, from TIMEFRAME_GROUPS.
    timeframe_groups: Tuple[Tuple[int, ...], ...]
    #: Whether this contract's strategies should be confined to its RTH.
    rth_only: bool
    rationale: str
    #: Families deliberately excluded, with the reason. Recorded rather than
    #: silently omitted, so the exclusion is auditable and reversible.
    excluded: Dict[str, str] = field(default_factory=dict)


SYMBOL_PROFILES: Dict[str, SymbolProfile] = {

    # ------------------------------------------------------------------
    "MNQ": SymbolProfile(
        symbol="MNQ",
        groups=("MOMENTUM", "TREND", "BREAKOUT", "OPENING_RANGE",
                "MULTI_TIMEFRAME", "LIQUIDITY", "PULLBACK"),
        timeframes=(1, 5, 15, 60),
        timeframe_groups=((1, 5, 15), (5, 15, 60)),
        rth_only=True,
        rationale=(
            "120 points of typical ATR against a 0.97-point round turn is the "
            "widest signal-to-cost ratio on the desk, which is what makes fast "
            "timeframes viable here and nowhere else - a 1-minute MNQ bar clears "
            "its own transaction cost, a 1-minute MGC bar does not. The Nasdaq "
            "micro also carries the most directional persistence of the three, "
            "so continuation and expansion families get the budget."),
        excluded={
            "MEAN_REVERSION": "fading the most persistent of the three index "
                              "micros is the trade with the worst prior here; "
                              "MES is where rotation is tested",
            "FIBONACCI": "retracement depth needs a stable leg, and MNQ's legs "
                         "are the shortest-lived - tested on MGC instead",
        },
    ),

    # ------------------------------------------------------------------
    "MES": SymbolProfile(
        symbol="MES",
        groups=("VWAP", "VOLUME_PROFILE", "MEAN_REVERSION", "PULLBACK",
                "REVERSAL", "LIQUIDITY", "MULTI_TIMEFRAME"),
        timeframes=(5, 15, 60),
        timeframe_groups=((5, 15, 60), (15, 60, 240)),
        rth_only=True,
        rationale=(
            "The tightest and deepest of the three, and the one that spends most "
            "of its day rotating around a reference price rather than leaving it. "
            "That makes VWAP and the prior session's value area the natural "
            "anchors, and mean reversion a real hypothesis rather than a way to "
            "stand in front of a trend. Its cost ratio is the worst of the three "
            "- a 0.54-point round turn against 45 points of ATR is 1.20%, half "
            "again MNQ's 0.81% - so 1-minute generation is dropped: at that "
            "horizon the cost is a larger share of the move than the edge is."),
        excluded={
            "MOMENTUM": "ignition is MNQ's trade; on MES the same rules fire "
                        "into rotation",
            "OPENING_RANGE": "the opening drive is tested on MNQ, which has the "
                             "range to make the break mean something",
        },
    ),

    # ------------------------------------------------------------------
    "MGC": SymbolProfile(
        symbol="MGC",
        groups=("TREND", "SUPPLY_DEMAND", "FIBONACCI", "MULTI_TIMEFRAME",
                "MEAN_REVERSION", "VOLUME_PROFILE"),
        timeframes=(15, 60, 240),
        timeframe_groups=((15, 60, 240),),
        rth_only=False,
        rationale=(
            "Not an equity product and it should stop being tested like one. "
            "Gold's pit session is 08:20-13:30, so an 09:30 opening range and a "
            "15:00 power hour are windows borrowed from a different contract; "
            "and because the real drivers - the dollar, real yields, the London "
            "fix - move it around the clock, confining it to any RTH throws away "
            "most of its information. Its legs run over days rather than hours, "
            "which is the one place on this desk where a Fibonacci retracement "
            "has a stable anchor to be measured from, and where a supply/demand "
            "zone survives long enough to be retested."),
        excluded={
            "OPENING_RANGE": "gold's session opens at 08:20 and the template's "
                             "150-minute window is calibrated to the equity open",
            "MOMENTUM": "the 1m/5m ignition rules need the intraday range MNQ "
                        "has and gold does not",
            "BREAKOUT": "compression-to-expansion at intraday scale; on a "
                        "23-hour product this mostly fires on session handoffs",
        },
    ),
}


def unknown_groups() -> Dict[str, Tuple[str, ...]]:
    """Profile group names that match no template.

    ``groups_for`` filters them out, which is the dangerous behaviour: a typo
    or a renamed template would narrow a contract's research silently and the
    run would look deliberate. This function exists so a test can fail loudly
    instead. It already caught one - MNQ carried an ``IMBALANCE_ANY`` that was
    never a template.
    """
    from .combinator import TEMPLATES

    known = {t.group for t in TEMPLATES}
    out: Dict[str, Tuple[str, ...]] = {}
    for sym, prof in SYMBOL_PROFILES.items():
        bad = tuple(g for g in prof.groups if g not in known)
        bad += tuple(g for g in prof.excluded if g not in known)
        if bad:
            out[sym] = bad
    return out


def profile_for(symbol: str) -> Optional[SymbolProfile]:
    return SYMBOL_PROFILES.get(symbol.upper())


def groups_for(symbol: str, available: Optional[Sequence[str]] = None) -> Optional[Tuple[str, ...]]:
    """Template groups to test for ``symbol``, or None for "no opinion".

    Returning None rather than every group matters: a caller that gets None
    falls back to the full set, so an unprofiled contract is tested broadly
    instead of being silently narrowed by a profile written for something else.
    """
    prof = profile_for(symbol)
    if prof is None:
        return None
    if available is None:
        return prof.groups
    known = set(available)
    return tuple(g for g in prof.groups if g in known) or None


def timeframes_for(symbol: str) -> Optional[Tuple[int, ...]]:
    prof = profile_for(symbol)
    return prof.timeframes if prof else None
