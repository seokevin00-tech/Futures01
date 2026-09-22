"""Market structure: swings, breaks of structure, S/R, FVGs, sweeps, sessions.

The central discipline of this module is the **confirmation lag**. A swing high
at bar *i* using a 3-bar fractal is not knowable until bar *i+3*. Every swing
therefore carries ``confirmed_index``, and every consumer filters on it. Code
that treats a swing as visible at the bar it occurred is the single most common
source of a backtest that cannot be reproduced live.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Sequence, Tuple

from ..timeutil import ET, is_rth, minutes_since_open, to_et, trading_day
from ..data.bars import Bar, BarSeries

__all__ = [
    "Swing", "find_swings", "market_structure", "SRLevel", "support_resistance",
    "OpeningRange", "opening_range", "SessionLevels", "session_levels",
    "FVG", "fair_value_gaps", "Sweep", "liquidity_sweeps", "detect_imbalances",
    "SDZone", "supply_demand_zones",
]


# --------------------------------------------------------------------------
# Swings
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Swing:
    index: int
    ts: datetime
    price: float
    kind: str                # "HIGH" | "LOW"
    confirmed_index: int     # first bar at which this swing was knowable
    strength: int = 1        # bars on each side that it dominates

    @property
    def is_high(self) -> bool:
        return self.kind == "HIGH"

    def visible_at(self, index: int) -> bool:
        return index >= self.confirmed_index


def find_swings(bars: Sequence[Bar], left: int = 3, right: int = 3) -> List[Swing]:
    """Fractal swing highs and lows.

    A swing high at *i* requires ``left`` bars to its left with lower highs and
    ``right`` bars to its right with lower highs; it becomes visible at
    ``i + right``.
    """
    out: List[Swing] = []
    n = len(bars)
    if n < left + right + 1:
        return out
    for i in range(left, n - right):
        h, l = bars[i].high, bars[i].low
        if (all(bars[j].high <= h for j in range(i - left, i))
                and all(bars[j].high < h for j in range(i + 1, i + right + 1))):
            out.append(Swing(i, bars[i].ts, h, "HIGH", i + right, min(left, right)))
        if (all(bars[j].low >= l for j in range(i - left, i))
                and all(bars[j].low > l for j in range(i + 1, i + right + 1))):
            out.append(Swing(i, bars[i].ts, l, "LOW", i + right, min(left, right)))
    out.sort(key=lambda s: (s.index, s.kind))
    return out


# --------------------------------------------------------------------------
# Market structure
# --------------------------------------------------------------------------

@dataclass
class StructureState:
    """Structural read of a series as of its final bar."""

    trend: str = "UNDEFINED"          # UPTREND | DOWNTREND | RANGE | UNDEFINED
    last_event: str = ""              # BOS_UP | BOS_DOWN | CHOCH_UP | CHOCH_DOWN
    last_event_index: Optional[int] = None
    swing_highs: List[Swing] = field(default_factory=list)
    swing_lows: List[Swing] = field(default_factory=list)
    last_high: Optional[Swing] = None
    last_low: Optional[Swing] = None
    higher_highs: int = 0
    higher_lows: int = 0
    lower_highs: int = 0
    lower_lows: int = 0
    events: List[Tuple[int, str, float]] = field(default_factory=list)

    @property
    def is_uptrend(self) -> bool:
        return self.trend == "UPTREND"

    @property
    def is_downtrend(self) -> bool:
        return self.trend == "DOWNTREND"

    def to_dict(self) -> dict:
        return {
            "trend": self.trend, "last_event": self.last_event,
            "last_event_index": self.last_event_index,
            "last_high": self.last_high.price if self.last_high else None,
            "last_low": self.last_low.price if self.last_low else None,
            "higher_highs": self.higher_highs, "higher_lows": self.higher_lows,
            "lower_highs": self.lower_highs, "lower_lows": self.lower_lows,
        }


def market_structure(bars: Sequence[Bar], left: int = 3, right: int = 3,
                     as_of: Optional[int] = None) -> StructureState:
    """Classify trend from swing sequence, and record BOS / CHoCH events.

    * **BOS** (break of structure): price takes out the prior swing in the
      direction of the existing trend - continuation.
    * **CHoCH** (change of character): price takes out the prior swing against
      the trend - the first structural warning of a reversal.

    ``as_of`` restricts the analysis to swings confirmed by that bar index.
    """
    end = len(bars) - 1 if as_of is None else min(as_of, len(bars) - 1)
    if end < 0:
        return StructureState()

    swings = [s for s in find_swings(bars[: end + 1], left, right)
              if s.confirmed_index <= end]
    highs = [s for s in swings if s.is_high]
    lows = [s for s in swings if not s.is_high]

    st = StructureState(swing_highs=highs, swing_lows=lows,
                        last_high=highs[-1] if highs else None,
                        last_low=lows[-1] if lows else None)

    for i in range(1, len(highs)):
        if highs[i].price > highs[i - 1].price:
            st.higher_highs += 1
        elif highs[i].price < highs[i - 1].price:
            st.lower_highs += 1
    for i in range(1, len(lows)):
        if lows[i].price > lows[i - 1].price:
            st.higher_lows += 1
        elif lows[i].price < lows[i - 1].price:
            st.lower_lows += 1

    bullish = st.higher_highs + st.higher_lows
    bearish = st.lower_highs + st.lower_lows
    if bullish >= bearish + 2:
        st.trend = "UPTREND"
    elif bearish >= bullish + 2:
        st.trend = "DOWNTREND"
    elif highs and lows:
        st.trend = "RANGE"

    # Walk the confirmed swings in order and emit BOS / CHoCH transitions.
    trend = "UNDEFINED"
    ref_high = ref_low = None
    for s in swings:
        if s.is_high:
            if ref_high is not None and s.price > ref_high:
                ev = "BOS_UP" if trend in ("UPTREND", "UNDEFINED") else "CHOCH_UP"
                st.events.append((s.confirmed_index, ev, s.price))
                trend = "UPTREND"
            ref_high = s.price if ref_high is None else max(ref_high, s.price)
        else:
            if ref_low is not None and s.price < ref_low:
                ev = "BOS_DOWN" if trend in ("DOWNTREND", "UNDEFINED") else "CHOCH_DOWN"
                st.events.append((s.confirmed_index, ev, s.price))
                trend = "DOWNTREND"
            ref_low = s.price if ref_low is None else min(ref_low, s.price)

    if st.events:
        st.last_event_index, st.last_event, _ = st.events[-1]
    return st


# --------------------------------------------------------------------------
# Support / resistance
# --------------------------------------------------------------------------

@dataclass
class SRLevel:
    price: float
    touches: int
    kind: str                 # "SUPPORT" | "RESISTANCE" | "PIVOT"
    first_index: int
    last_index: int
    strength: float = 0.0     # touches weighted by recency and swing strength

    def distance_pct(self, price: float) -> float:
        return abs(price - self.price) / price * 100.0 if price else 0.0

    def to_dict(self) -> dict:
        return {"price": self.price, "touches": self.touches, "kind": self.kind,
                "strength": round(self.strength, 3)}


def support_resistance(bars: Sequence[Bar], *, tolerance: float = 0.0015,
                       left: int = 3, right: int = 3,
                       min_touches: int = 2, max_levels: int = 12,
                       as_of: Optional[int] = None) -> List[SRLevel]:
    """Cluster confirmed swing points into support and resistance levels.

    ``tolerance`` is a fraction of price, so the same setting behaves sensibly
    on MNQ at 21,800 and MCL at 71.50.
    """
    end = len(bars) - 1 if as_of is None else min(as_of, len(bars) - 1)
    if end < left + right:
        return []
    swings = [s for s in find_swings(bars[: end + 1], left, right)
              if s.confirmed_index <= end]
    if not swings:
        return []

    ref = bars[end].close or 1.0
    band = max(1e-9, abs(ref) * tolerance)

    # Cluster against each group's ANCHOR price, not its last member. Comparing
    # against the last member lets a dense run of swings chain into one cluster
    # spanning hundreds of points ("single-linkage chaining"), which reports a
    # fictional level with an absurd touch count.
    clusters: List[List[Swing]] = []
    for s in sorted(swings, key=lambda x: x.price):
        if clusters and abs(s.price - clusters[-1][0].price) <= band:
            clusters[-1].append(s)
        else:
            clusters.append([s])

    levels: List[SRLevel] = []
    for cl in clusters:
        if len(cl) < min_touches:
            continue
        price = sum(s.price for s in cl) / len(cl)
        n_high = sum(1 for s in cl if s.is_high)
        kind = ("RESISTANCE" if n_high > len(cl) - n_high
                else "SUPPORT" if n_high < len(cl) - n_high else "PIVOT")
        # Recency weighting: a level defended last week matters less than one
        # defended this morning.
        strength = sum(s.strength * math.exp(-(end - s.confirmed_index) / max(1.0, end * 0.5))
                       for s in cl)
        levels.append(SRLevel(price, len(cl), kind,
                              min(s.index for s in cl), max(s.index for s in cl),
                              strength))

    levels.sort(key=lambda lv: lv.strength, reverse=True)
    return levels[:max_levels]


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------

@dataclass
class OpeningRange:
    day: date
    minutes: int
    high: float
    low: float
    complete: bool
    open_price: float
    volume: float = 0.0

    @property
    def mid(self) -> float:
        return (self.high + self.low) / 2.0

    @property
    def size(self) -> float:
        return self.high - self.low

    def to_dict(self) -> dict:
        return {"day": str(self.day), "minutes": self.minutes, "high": self.high,
                "low": self.low, "mid": self.mid, "size": self.size,
                "complete": self.complete}


def opening_range(bars: Sequence[Bar], minutes: int = 30, day: Optional[date] = None,
                  open_hhmm: str = "09:30") -> Optional[OpeningRange]:
    """Opening range for a session.

    ``complete`` is False until the full window has elapsed - an OR breakout
    signal taken off an incomplete range is a look-ahead artefact.
    """
    if not bars:
        return None
    target = day or to_et(bars[-1].ts).date()
    window = [b for b in bars
              if to_et(b.ts).date() == target
              and 0 <= minutes_since_open(b.ts, open_hhmm) < minutes]
    if not window:
        return None
    elapsed = minutes_since_open(bars[-1].ts, open_hhmm)
    covered = max(minutes_since_open(b.ts, open_hhmm) + b.minutes for b in window)
    return OpeningRange(
        day=target, minutes=minutes,
        high=max(b.high for b in window), low=min(b.low for b in window),
        complete=(covered >= minutes) and (elapsed >= minutes),
        open_price=window[0].open,
        volume=sum(b.volume for b in window),
    )


@dataclass
class SessionLevels:
    """Reference levels every futures desk watches."""

    prev_day_high: Optional[float] = None
    prev_day_low: Optional[float] = None
    prev_day_close: Optional[float] = None
    overnight_high: Optional[float] = None
    overnight_low: Optional[float] = None
    session_high: Optional[float] = None
    session_low: Optional[float] = None
    initial_balance_high: Optional[float] = None   # first hour of RTH
    initial_balance_low: Optional[float] = None
    day_open: Optional[float] = None

    def to_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}

    def nearest(self, price: float) -> Tuple[Optional[str], Optional[float]]:
        best_name, best_price, best_dist = None, None, float("inf")
        for name, level in self.__dict__.items():
            if level is None:
                continue
            d = abs(level - price)
            if d < best_dist:
                best_name, best_price, best_dist = name, level, d
        return best_name, best_price


def session_levels(bars: Sequence[Bar], *, as_of: Optional[datetime] = None,
                   open_hhmm: str = "09:30", close_hhmm: str = "16:00") -> SessionLevels:
    """Compute PDH/PDL/PDC, overnight H/L, session H/L and initial balance.

    Everything is computed from bars at or before ``as_of`` only.
    """
    lv = SessionLevels()
    if not bars:
        return lv
    cutoff = to_et(as_of) if as_of else bars[-1].ts
    visible = [b for b in bars if b.ts <= cutoff]
    if not visible:
        return lv

    today = trading_day(cutoff)
    by_day: Dict[date, List[Bar]] = {}
    for b in visible:
        by_day.setdefault(trading_day(b.ts), []).append(b)

    prior_days = sorted(d for d in by_day if d < today)
    if prior_days:
        prev = [b for b in by_day[prior_days[-1]]
                if is_rth(b.ts, open_hhmm, close_hhmm)] or by_day[prior_days[-1]]
        lv.prev_day_high = max(b.high for b in prev)
        lv.prev_day_low = min(b.low for b in prev)
        lv.prev_day_close = prev[-1].close

    today_bars = by_day.get(today, [])
    if today_bars:
        overnight = [b for b in today_bars if not is_rth(b.ts, open_hhmm, close_hhmm)]
        rth = [b for b in today_bars if is_rth(b.ts, open_hhmm, close_hhmm)]
        if overnight:
            lv.overnight_high = max(b.high for b in overnight)
            lv.overnight_low = min(b.low for b in overnight)
        if rth:
            lv.session_high = max(b.high for b in rth)
            lv.session_low = min(b.low for b in rth)
            lv.day_open = rth[0].open
            ib = [b for b in rth if minutes_since_open(b.ts, open_hhmm) < 60]
            if ib:
                lv.initial_balance_high = max(b.high for b in ib)
                lv.initial_balance_low = min(b.low for b in ib)
    return lv


# --------------------------------------------------------------------------
# Fair value gaps / imbalances
# --------------------------------------------------------------------------

@dataclass
class FVG:
    """A three-bar fair value gap (imbalance)."""

    index: int               # index of the middle (displacement) bar
    ts: datetime
    top: float
    bottom: float
    direction: str           # "BULLISH" | "BEARISH"
    filled_index: Optional[int] = None

    @property
    def size(self) -> float:
        return self.top - self.bottom

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2.0

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top

    def to_dict(self) -> dict:
        return {"index": self.index, "top": self.top, "bottom": self.bottom,
                "direction": self.direction, "filled": self.filled_index is not None}


def fair_value_gaps(bars: Sequence[Bar], *, min_size_pct: float = 0.0,
                    as_of: Optional[int] = None,
                    track_fills: bool = True) -> List[FVG]:
    """Find three-bar FVGs and mark which have been filled.

    A bullish FVG exists when bar *i-1*'s high is below bar *i+1*'s low: price
    displaced up so fast that no trade occurred in between. The gap is only
    knowable at bar *i+1*, so ``index`` is the middle bar and consumers should
    treat ``index + 1`` as the visibility point.
    """
    end = len(bars) - 1 if as_of is None else min(as_of, len(bars) - 1)
    out: List[FVG] = []
    for i in range(1, end):
        prev, nxt = bars[i - 1], bars[i + 1]
        ref = bars[i].close or 1.0
        if nxt.low > prev.high:
            size = nxt.low - prev.high
            if size / abs(ref) * 100.0 >= min_size_pct:
                out.append(FVG(i, bars[i].ts, nxt.low, prev.high, "BULLISH"))
        elif nxt.high < prev.low:
            size = prev.low - nxt.high
            if size / abs(ref) * 100.0 >= min_size_pct:
                out.append(FVG(i, bars[i].ts, prev.low, nxt.high, "BEARISH"))

    if track_fills:
        for g in out:
            for j in range(g.index + 2, end + 1):
                if bars[j].low <= g.mid <= bars[j].high:
                    g.filled_index = j
                    break
    return out


def detect_imbalances(bars: Sequence[Bar], *, threshold: float = 2.0,
                      as_of: Optional[int] = None) -> List[Tuple[int, str, float]]:
    """Bars whose range and delta both far exceed the recent norm.

    Returns ``(index, direction, magnitude)``. These mark aggressive one-sided
    participation - the footprint of a real initiative move rather than drift.
    """
    end = len(bars) - 1 if as_of is None else min(as_of, len(bars) - 1)
    out: List[Tuple[int, str, float]] = []
    window = 20
    for i in range(window, end + 1):
        prior = bars[i - window:i]
        avg_range = sum(b.range for b in prior) / window
        avg_vol = sum(b.volume for b in prior) / window
        if avg_range <= 0 or avg_vol <= 0:
            continue
        r_mult = bars[i].range / avg_range
        v_mult = bars[i].volume / avg_vol
        if r_mult >= threshold and v_mult >= threshold * 0.6:
            direction = "BULLISH" if bars[i].close > bars[i].open else "BEARISH"
            out.append((i, direction, r_mult))
    return out


# --------------------------------------------------------------------------
# Liquidity sweeps
# --------------------------------------------------------------------------

@dataclass
class Sweep:
    """A stop run: price traded through a level, then closed back inside it."""

    index: int
    ts: datetime
    level: float
    level_name: str
    direction: str           # "BULLISH" (swept lows) | "BEARISH" (swept highs)
    penetration: float       # how far beyond the level price traded
    reclaim_index: int       # bar at which the reclaim was confirmed

    def to_dict(self) -> dict:
        return {"index": self.index, "level": self.level, "level_name": self.level_name,
                "direction": self.direction, "penetration": self.penetration}


def liquidity_sweeps(bars: Sequence[Bar], levels: Dict[str, float], *,
                     reclaim_bars: int = 2, min_penetration: float = 0.0,
                     as_of: Optional[int] = None) -> List[Sweep]:
    """Find sweeps of the supplied levels.

    A sweep is only a sweep once price has closed back through the level, so
    ``reclaim_index`` is the bar at which the pattern became tradeable - always
    later than the bar that made the extreme.
    """
    end = len(bars) - 1 if as_of is None else min(as_of, len(bars) - 1)
    out: List[Sweep] = []
    for name, level in levels.items():
        if level is None:
            continue
        for i in range(end + 1):
            b = bars[i]
            if b.high > level >= b.close and (b.high - level) >= min_penetration:
                for j in range(i + 1, min(i + 1 + reclaim_bars, end + 1)):
                    if bars[j].close < level:
                        out.append(Sweep(i, b.ts, level, name, "BEARISH",
                                         b.high - level, j))
                        break
            elif b.low < level <= b.close and (level - b.low) >= min_penetration:
                for j in range(i + 1, min(i + 1 + reclaim_bars, end + 1)):
                    if bars[j].close > level:
                        out.append(Sweep(i, b.ts, level, name, "BULLISH",
                                         level - b.low, j))
                        break
    out.sort(key=lambda s: s.reclaim_index)
    return out


# --------------------------------------------------------------------------
# Supply and demand zones
# --------------------------------------------------------------------------

@dataclass
class SDZone:
    """A base of balance that a decisive move departed from.

    The idea a supply/demand trader is expressing is that unfilled orders were
    left behind where price last turned violently, so a return to that area
    meets resting interest. Whether that is true is exactly what the backtester
    is for; this structure only locates the area.

    A zone is defined by two parts, both required:

    * a **base** - one to a few bars of genuinely small range, i.e. balance;
    * a **departure** - the next bar leaving that base with a range several
      times the recent average and a body that dominates its own range.

    A large bar on its own is not a zone. Without the preceding balance it is
    just a large bar, and taking every one of them would paint the whole chart.

    ``touch_indices`` and ``invalidated_index`` are recorded over the full
    series, so consumers **must** mask them to the bar being evaluated -
    :meth:`as_of` does that. Handing the raw object to a strategy would tell it
    how many times a zone is going to be tested in the future.
    """

    index: int                    # departure bar; the zone is knowable at its close
    ts: datetime
    kind: str                     # "DEMAND" | "SUPPLY"
    top: float
    bottom: float
    base_start: int
    base_end: int
    departure_strength: float     # departure range / recent average range
    touch_indices: Tuple[int, ...] = ()
    invalidated_index: Optional[int] = None

    @property
    def height(self) -> float:
        return self.top - self.bottom

    @property
    def mid(self) -> float:
        return (self.top + self.bottom) / 2.0

    @property
    def proximal(self) -> float:
        """The edge price reaches first coming back to the zone."""
        return self.top if self.kind == "DEMAND" else self.bottom

    @property
    def distal(self) -> float:
        """The far edge - where the zone is wrong rather than merely tested."""
        return self.bottom if self.kind == "DEMAND" else self.top

    @property
    def touches(self) -> int:
        return len(self.touch_indices)

    @property
    def fresh(self) -> bool:
        """Untested. Freshness is the one property this idea insists on."""
        return not self.touch_indices and self.invalidated_index is None

    def contains(self, price: float) -> bool:
        return self.bottom <= price <= self.top

    def distance_atr(self, price: float, atr_value: Optional[float]) -> Optional[float]:
        """Distance from ``price`` to the proximal edge, in ATR units."""
        if not atr_value:
            return None
        return (self.proximal - price) / atr_value

    def as_of(self, index: int) -> "SDZone":
        """A copy carrying only what was knowable at ``index``.

        Future touches are dropped and a future invalidation is hidden, so
        ``fresh`` and ``touches`` answer for that bar rather than for the end of
        the series.
        """
        inv = (self.invalidated_index
               if self.invalidated_index is not None and self.invalidated_index <= index
               else None)
        return SDZone(self.index, self.ts, self.kind, self.top, self.bottom,
                      self.base_start, self.base_end, self.departure_strength,
                      tuple(t for t in self.touch_indices if t <= index), inv)

    def to_dict(self) -> dict:
        return {"index": self.index, "kind": self.kind,
                "top": round(self.top, 6), "bottom": round(self.bottom, 6),
                "proximal": round(self.proximal, 6),
                "strength": round(self.departure_strength, 2),
                "touches": self.touches, "fresh": self.fresh,
                "invalidated": self.invalidated_index is not None}


def supply_demand_zones(bars: Sequence[Bar], *, window: int = 20,
                        departure_mult: float = 2.0, base_max_mult: float = 0.8,
                        max_base_bars: int = 3, body_frac: float = 0.55,
                        max_age: int = 400,
                        as_of: Optional[int] = None) -> List[SDZone]:
    """Locate demand and supply zones and track how each one has been used.

    ``window`` sets the lookback the "average range" is measured over, so the
    same multipliers behave the same on a 1-minute MNQ chart and a daily MGC
    one. ``max_age`` bounds the forward scan: a zone nobody has traded for four
    hundred bars is not a level, and scanning to the end of the series for each
    one is quadratic.
    """
    end = len(bars) - 1 if as_of is None else min(as_of, len(bars) - 1)
    if end < window + 1:
        return []

    out: List[SDZone] = []
    last_base_end: Dict[str, int] = {"DEMAND": -1, "SUPPLY": -1}

    for i in range(window + 1, end + 1):
        bar = bars[i]
        rng = bar.range
        if rng <= 0:
            continue
        prior = bars[i - window:i]
        avg_range = sum(b.range for b in prior) / float(window)
        if avg_range <= 0 or rng < departure_mult * avg_range:
            continue
        if abs(bar.close - bar.open) < body_frac * rng:
            continue            # a wide bar that closed mid-range is indecision

        kind = "DEMAND" if bar.close > bar.open else "SUPPLY"

        # Walk back over the balance that the departure left.
        base: List[Bar] = []
        j = i - 1
        while j >= 0 and len(base) < max_base_bars:
            if bars[j].range > base_max_mult * avg_range:
                break
            base.append(bars[j])
            j -= 1
        if not base:
            continue            # no balance before the move: not a zone
        base_start, base_end = j + 1, i - 1

        # Consecutive departures share a base; keep the first and move on,
        # otherwise one impulse paints five overlapping zones.
        if base_start <= last_base_end[kind]:
            continue
        last_base_end[kind] = base_end

        top = max(b.high for b in base)
        bottom = min(b.low for b in base)
        if top <= bottom:
            continue

        touches: List[int] = []
        invalidated: Optional[int] = None
        stop = min(end, i + max_age)
        for k in range(i + 1, stop + 1):
            nb = bars[k]
            if nb.low <= top and nb.high >= bottom:
                touches.append(k)
            if kind == "DEMAND" and nb.close < bottom:
                invalidated = k
                break
            if kind == "SUPPLY" and nb.close > top:
                invalidated = k
                break

        out.append(SDZone(i, bar.ts, kind, top, bottom, base_start, base_end,
                          rng / avg_range, tuple(touches), invalidated))
    return out
