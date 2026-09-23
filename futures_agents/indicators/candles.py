"""Candlestick patterns - the bar-form evidence the library never read.

``Bar`` has carried ``body``, ``upper_wick``, ``lower_wick`` and ``is_up``
since the start, and until now not one condition used them. The coverage map
listed "price action" as covered by a 20-bar range-position indicator, an
order-flow confirmation and an imbalance detector - three unrelated things
wearing the name, none of which look at the shape of a bar. This module is
the missing primitive.

Two rules run through all of it:

**Everything is normalised.** A three-point wick means one thing on MGC and
nothing on MNQ, so every threshold is expressed as a fraction of the bar's own
range or of a trailing average, never in points. The same pattern definition
then behaves identically on a 0.25-tick index future and a 0.01-tick crude
contract.

**Nothing reads forward.** A pattern is judged on the bar it completes and the
bars before it. There is no "confirmation bar" that peeks at what happened
next - where a classical definition demands one (morning star, for instance),
the pattern is simply reported one bar later, which is when a trader could
have acted on it anyway.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import List, Optional, Sequence

from ..data.bars import Bar

__all__ = ["CandlePattern", "classify_candle", "detect_patterns",
           "close_location_value", "body_fraction"]


@dataclass(frozen=True)
class CandlePattern:
    """One recognised bar form."""

    index: int
    name: str
    direction: str          # "BULLISH" | "BEARISH" | "NEUTRAL"
    strength: float         # 0-1, how emphatic the form is
    detail: str = ""

    def to_dict(self) -> dict:
        return {"index": self.index, "name": self.name,
                "direction": self.direction, "strength": round(self.strength, 3)}


def close_location_value(bar: Bar) -> Optional[float]:
    """Where the close sits in the bar's range, in [-1, +1].

    +1 is a close on the high, -1 on the low, 0 at the midpoint. This single
    number carries most of what a candlestick name encodes, and unlike a name
    it is continuous - which makes it far easier to test than a taxonomy.
    """
    rng = bar.range
    if rng <= 0:
        return None
    return ((bar.close - bar.low) - (bar.high - bar.close)) / rng


def body_fraction(bar: Bar) -> Optional[float]:
    """Body as a fraction of range. Near 1 is decisive, near 0 is indecision."""
    rng = bar.range
    return (bar.body_abs / rng) if rng > 0 else None


def _avg_range(bars: Sequence[Bar], i: int, window: int) -> Optional[float]:
    lo = max(0, i - window)
    if i - lo < 5:
        return None
    prior = bars[lo:i]
    return sum(b.range for b in prior) / len(prior)


def classify_candle(bars: Sequence[Bar], i: int, *,
                    window: int = 20) -> List[CandlePattern]:
    """Every pattern completing at bar ``i``.

    Returns a list because forms genuinely overlap - a bar can be both an
    engulfing and a marubozu, and collapsing that to one label would throw
    away the part that might matter.
    """
    if not 0 <= i < len(bars):
        return []
    bar = bars[i]
    rng = bar.range
    if rng <= 0:
        return []
    avg = _avg_range(bars, i, window)
    out: List[CandlePattern] = []

    bf = body_fraction(bar) or 0.0
    clv = close_location_value(bar) or 0.0
    up_w, lo_w = bar.upper_wick / rng, bar.lower_wick / rng

    # ---- single-bar forms ---------------------------------------------
    if bf <= 0.10:
        out.append(CandlePattern(i, "doji", "NEUTRAL", 1.0 - bf * 10,
                                 f"body {bf:.0%} of range"))
    if bf >= 0.85:
        out.append(CandlePattern(i, "marubozu",
                                 "BULLISH" if bar.is_up else "BEARISH", bf,
                                 f"body {bf:.0%} of range"))
    # A pin bar is a rejection: one wick dominates and the body sits opposite.
    if lo_w >= 0.60 and bf <= 0.35:
        out.append(CandlePattern(i, "hammer", "BULLISH", lo_w,
                                 f"lower wick {lo_w:.0%} of range"))
    if up_w >= 0.60 and bf <= 0.35:
        out.append(CandlePattern(i, "shooting_star", "BEARISH", up_w,
                                 f"upper wick {up_w:.0%} of range"))

    if i == 0:
        return out
    prev = bars[i - 1]

    # ---- two-bar forms -------------------------------------------------
    # Engulfing: this body swallows the previous one and reverses its sign.
    if (bar.body_abs > prev.body_abs and prev.body_abs > 0
            and bar.is_up != prev.is_up):
        if bar.is_up and bar.close >= prev.open and bar.open <= prev.close:
            out.append(CandlePattern(i, "bullish_engulfing", "BULLISH",
                                     min(1.0, bar.body_abs / max(prev.body_abs, 1e-9) / 2),
                                     f"engulfs a {prev.body_abs:.2f} body"))
        elif not bar.is_up and bar.close <= prev.open and bar.open >= prev.close:
            out.append(CandlePattern(i, "bearish_engulfing", "BEARISH",
                                     min(1.0, bar.body_abs / max(prev.body_abs, 1e-9) / 2),
                                     f"engulfs a {prev.body_abs:.2f} body"))

    # Inside bar: compression, and the only pattern here that is a FILTER on
    # the next move rather than a directional claim.
    if bar.high <= prev.high and bar.low >= prev.low:
        out.append(CandlePattern(i, "inside_bar", "NEUTRAL",
                                 1.0 - (rng / prev.range if prev.range > 0 else 1.0),
                                 "range inside the prior bar"))

    # Outside bar: expansion that took both sides of the prior bar.
    if bar.high > prev.high and bar.low < prev.low and avg and rng > avg:
        out.append(CandlePattern(i, "outside_bar",
                                 "BULLISH" if clv > 0.3 else
                                 "BEARISH" if clv < -0.3 else "NEUTRAL",
                                 min(1.0, rng / avg / 2.0),
                                 f"took both sides, closed {clv:+.2f}"))
    return out


def detect_patterns(bars: Sequence[Bar], *, window: int = 20,
                    as_of: Optional[int] = None) -> List[CandlePattern]:
    """Every pattern in the series up to ``as_of``."""
    end = len(bars) - 1 if as_of is None else min(as_of, len(bars) - 1)
    out: List[CandlePattern] = []
    for i in range(end + 1):
        out.extend(classify_candle(bars, i, window=window))
    return out
