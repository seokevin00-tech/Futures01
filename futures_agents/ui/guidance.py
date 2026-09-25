"""What the measurement actually established, as data the UI can render.

``CALLOUT.md`` is the operating brief a human reads. This module is the same
content in a form the screens can lay out, so the honesty travels with the
numbers instead of living in a file nobody opens while trading.

Two things are deliberate here:

* **The per-symbol verdicts include the negative ones.** MES and MNQ have no
  surviving framework, and the UI says so on the symbol selector rather than
  leaving a blank that reads as "fine". A tool that is silent about the symbols
  it cannot help with is worse than one that has no opinion at all, because
  silence looks like approval.
* **No number here is presented as an edge.** Roughly three million strategy
  evaluations produced nothing that cleared its own multiple-testing threshold -
  the highest t-statistic anywhere is 3.92 against a required 5.13. The
  framework is structure for a discretionary read. :data:`HEADLINE_CAVEAT` is
  rendered on every screen that offers a direction.
"""

from __future__ import annotations

from dataclasses import dataclass, asdict
from typing import Dict, List, Optional, Tuple

__all__ = ["SymbolFramework", "FRAMEWORKS", "framework_for", "MEASURED_RULES",
           "HEADLINE_CAVEAT", "as_dict"]


HEADLINE_CAVEAT = (
    "Nothing in this library is live-eligible. ~3M strategy evaluations; not one "
    "cleared its multiple-testing threshold (best t = 3.92 vs 5.13 required). "
    "Placebo entries rank alongside real signals, and trading last period's top-10 "
    "underperforms trading the whole qualifying universe. Use this as structure for "
    "a discretionary read, not as a system with a measured edge."
)


@dataclass(frozen=True)
class SymbolFramework:
    """The measured verdict for one symbol."""

    symbol: str
    status: str            # "measured" | "none"
    signals: Tuple[str, ...]
    timeframes: Tuple[int, ...]
    note: str
    independent: bool      # False for members of the one index complex

    def to_dict(self) -> dict:
        d = asdict(self)
        d["signals"] = list(self.signals)
        d["timeframes"] = list(self.timeframes)
        return d


FRAMEWORKS: Dict[str, SymbolFramework] = {
    "MCL": SymbolFramework(
        symbol="MCL", status="measured", signals=("MOMENTUM",),
        timeframes=(60, 240),
        note="MOMENTUM on 66 observations, 97% positive, beat control on both "
             "timeframes. The strongest thing in the library - which still did not "
             "clear its own significance threshold.",
        independent=True),
    "MGC": SymbolFramework(
        symbol="MGC", status="measured",
        signals=("VWAP", "TREND", "MOMENTUM", "MULTI_TIMEFRAME"),
        timeframes=(60,),
        note="All four modestly above control at 60m only. MGC at 240m is worse "
             "than its own placebo, so 240m is not offered.",
        independent=True),
    "MES": SymbolFramework(
        symbol="MES", status="none", signals=(), timeframes=(),
        note="Nothing beat its own placebo - including TREND at 154 observations "
             "and 100% positive. Default to NO TRADE unless the picture is "
             "compelling on its own terms.",
        independent=False),
    "MNQ": SymbolFramework(
        symbol="MNQ", status="none", signals=(), timeframes=(),
        note="Nothing beat its own placebo. Default to NO TRADE unless the picture "
             "is compelling on its own terms.",
        independent=False),
    "ES": SymbolFramework(
        symbol="ES", status="none", signals=(), timeframes=(),
        note="Full-size contract in the index complex - no measured framework, and "
             "a median trade risks roughly ten times its micro.",
        independent=False),
    "NQ": SymbolFramework(
        symbol="NQ", status="none", signals=(), timeframes=(),
        note="Full-size contract in the index complex - no measured framework, and "
             "a median trade risks roughly ten times its micro.",
        independent=False),
}


def framework_for(symbol: str) -> SymbolFramework:
    """The verdict for ``symbol``; an explicit 'not measured' for the rest.

    Returning a real object for an unmeasured symbol rather than ``None`` keeps
    the caller from having to decide what a missing entry means - there is no
    reading of a blank that is safer than the sentence below.
    """
    key = symbol.upper().strip()
    found = FRAMEWORKS.get(key)
    if found is not None:
        return found
    return SymbolFramework(
        symbol=key, status="none", signals=(), timeframes=(),
        note="This symbol was not part of the measurement programme, so there is "
             "no tested framework for it here at all.",
        independent=True)


#: Rules that were measured and held. Each carries the evidence, because a rule
#: quoted without its basis is indistinguishable from a preference.
MEASURED_RULES: Tuple[Dict[str, str], ...] = (
    {"rule": "Two signals plus one filter is the ceiling",
     "basis": "Going from 2 signals to 4 cut trade count 35% with no expectancy "
              "gain; the sign favours two. More confluence is a worse trade."},
    {"rule": "Multi-timeframe agreement is not a virtue",
     "basis": "Requiring any alignment measured detectably worse than requiring "
              "none (z = -4.09). It wins months often and loses on average."},
    {"rule": "Win rate and payoff cancel out",
     "basis": "Widening a stop from structure to ATR raised payoff ~89% and dropped "
              "win rate ~14 points for no expectancy gain. Never quote one alone."},
    {"rule": "Structural stops are never tighter than 0.5 ATR",
     "basis": "Below that they are noise - the tighter stop is hit more often than "
              "the better ratio is worth."},
    {"rule": "No intraday entries between 15:00 and 16:00 ET",
     "basis": "z = -4.43, median -0.617R, replicated."},
    {"rule": "No hours filter improves expectancy",
     "basis": "The lunch-avoidance folk claim is refuted."},
    {"rule": "Sub-hourly is a graveyard",
     "basis": "At 5 minutes, only 11-16% of strategies make money."},
    {"rule": "ORB and ICT did not pay here",
     "basis": "Yesterday's opening range beats today's; FVG and order-block fill "
              "rates are reproduced by random zones; the ICT sweep/shift/retrace "
              "sequence is real, common, and adds nothing over its parts."},
)


def as_dict() -> dict:
    """The whole guidance payload, for the bootstrap response."""
    return {
        "caveat": HEADLINE_CAVEAT,
        "frameworks": {k: v.to_dict() for k, v in FRAMEWORKS.items()},
        "rules": [dict(r) for r in MEASURED_RULES],
    }
