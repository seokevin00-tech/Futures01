"""Volume, VWAP, delta and volume-profile analytics.

Order-flow measures are only as good as the data behind them. Where a feed
supplies a true bid/ask volume split these functions use it; where it does not,
:attr:`Bar.delta` falls back to a close-location proxy and every result derived
from it is flagged ``estimated=True``. A strategy validated on estimated delta
has been validated on a proxy, and the system says so rather than quietly
presenting it as order flow.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ..timeutil import is_rth, to_et, trading_day
from ..data.bars import Bar, BarSeries
from .core import percent_rank, sma

Num = Optional[float]

__all__ = [
    "AnchorVWAP", "build_anchor_vwap", "major_move_anchors", "swing_anchors",
    "vwap", "vwap_bands", "anchored_vwap", "delta_series", "cumulative_delta",
    "VolumeProfile", "volume_profile", "relative_volume", "volume_regime",
    "delta_divergence",
]


def _anchor_key(bar: Bar, anchor: str) -> object:
    """Group key that resets the running accumulation."""
    if anchor == "session":          # CME trading day (18:00 ET roll)
        return trading_day(bar.ts)
    if anchor == "rth":              # calendar day, RTH bars only
        return to_et(bar.ts).date()
    if anchor == "week":
        d = to_et(bar.ts).date()
        return (d.isocalendar().year, d.isocalendar().week)
    if anchor == "none":
        return 0
    raise ValueError(f"Unknown VWAP anchor {anchor!r}")


def vwap(bars: Sequence[Bar], anchor: str = "session",
         rth_only: bool = False) -> List[Num]:
    """Volume-weighted average price, reset at each anchor boundary.

    ``anchor="session"`` resets at the 18:00 ET Globex open, which is the VWAP
    every futures desk quotes. ``rth_only=True`` restarts at 09:30 and ignores
    overnight volume, which is the VWAP most retail platforms draw - they are
    different numbers and the system keeps them distinct.
    """
    out: List[Num] = [None] * len(bars)
    cur_key = None
    pv = vol = 0.0
    for i, b in enumerate(bars):
        if rth_only and not is_rth(b.ts):
            out[i] = out[i - 1] if i else None
            continue
        key = to_et(b.ts).date() if rth_only else _anchor_key(b, anchor)
        if key != cur_key:
            cur_key, pv, vol = key, 0.0, 0.0
        v = b.volume if b.volume > 0 else 1.0
        pv += b.typical * v
        vol += v
        out[i] = pv / vol if vol > 0 else None
    return out


def vwap_bands(bars: Sequence[Bar], anchor: str = "session",
               mults: Sequence[float] = (1.0, 2.0, 3.0),
               rth_only: bool = False) -> Dict[str, List[Num]]:
    """VWAP plus volume-weighted standard-deviation bands.

    Returns ``{"vwap": [...], "upper_1": [...], "lower_1": [...], ...}``.
    """
    n = len(bars)
    line: List[Num] = [None] * n
    bands: Dict[str, List[Num]] = {f"{side}_{m:g}": [None] * n
                                   for m in mults for side in ("upper", "lower")}
    cur_key = None
    pv = vol = pv2 = 0.0
    for i, b in enumerate(bars):
        if rth_only and not is_rth(b.ts):
            continue
        key = to_et(b.ts).date() if rth_only else _anchor_key(b, anchor)
        if key != cur_key:
            cur_key, pv, vol, pv2 = key, 0.0, 0.0, 0.0
        v = b.volume if b.volume > 0 else 1.0
        tp = b.typical
        pv += tp * v
        pv2 += tp * tp * v
        vol += v
        if vol <= 0:
            continue
        mean = pv / vol
        line[i] = mean
        var = max(0.0, pv2 / vol - mean * mean)
        sd = math.sqrt(var)
        for m in mults:
            bands[f"upper_{m:g}"][i] = mean + m * sd
            bands[f"lower_{m:g}"][i] = mean - m * sd
    return {"vwap": line, **bands}


def anchored_vwap(bars: Sequence[Bar], anchor_index: int) -> List[Num]:
    """VWAP anchored to a specific bar - a swing high/low, a news release, a gap."""
    out: List[Num] = [None] * len(bars)
    if not 0 <= anchor_index < len(bars):
        return out
    pv = vol = 0.0
    for i in range(anchor_index, len(bars)):
        b = bars[i]
        v = b.volume if b.volume > 0 else 1.0
        pv += b.typical * v
        vol += v
        out[i] = pv / vol if vol else None
    return out


# --------------------------------------------------------------------------
# Delta / CVD
# --------------------------------------------------------------------------

def delta_series(bars: Sequence[Bar]) -> List[float]:
    return [b.delta for b in bars]


def cumulative_delta(bars: Sequence[Bar], anchor: str = "session") -> List[float]:
    """Cumulative volume delta, reset at each anchor boundary."""
    out: List[float] = [0.0] * len(bars)
    cur_key, total = None, 0.0
    for i, b in enumerate(bars):
        key = _anchor_key(b, anchor)
        if key != cur_key:
            cur_key, total = key, 0.0
        total += b.delta
        out[i] = total
    return out


def delta_divergence(bars: Sequence[Bar], lookback: int = 20,
                     anchor: str = "session") -> List[Optional[str]]:
    """Detect price/CVD divergence at each bar.

    Returns ``"BEARISH"`` when price prints a higher high while CVD does not
    (buyers failing to follow through), ``"BULLISH"`` for the mirror case, and
    ``None`` otherwise. Only trailing data is used, so the value at bar *i* is
    what would have been visible at bar *i*.
    """
    cvd = cumulative_delta(bars, anchor)
    out: List[Optional[str]] = [None] * len(bars)
    for i in range(lookback, len(bars)):
        window = range(i - lookback, i)
        prior_high = max(bars[j].high for j in window)
        prior_low = min(bars[j].low for j in window)
        prior_cvd_high = max(cvd[j] for j in window)
        prior_cvd_low = min(cvd[j] for j in window)
        if bars[i].high > prior_high and cvd[i] <= prior_cvd_high:
            out[i] = "BEARISH"
        elif bars[i].low < prior_low and cvd[i] >= prior_cvd_low:
            out[i] = "BULLISH"
    return out


# --------------------------------------------------------------------------
# Volume profile
# --------------------------------------------------------------------------

@dataclass
class VolumeProfile:
    """Price-by-volume distribution for a set of bars."""

    poc: float                       # point of control - highest-volume price
    vah: float                       # value-area high (70% of volume)
    val: float                       # value-area low
    bin_size: float
    histogram: List[Tuple[float, float]] = field(default_factory=list)  # (price, volume)
    total_volume: float = 0.0
    hvn: List[float] = field(default_factory=list)   # high-volume nodes
    lvn: List[float] = field(default_factory=list)   # low-volume nodes
    estimated: bool = False

    def volume_at(self, price: float) -> float:
        if not self.histogram:
            return 0.0
        idx = min(range(len(self.histogram)),
                  key=lambda i: abs(self.histogram[i][0] - price))
        return self.histogram[idx][1]

    def in_value_area(self, price: float) -> bool:
        return self.val <= price <= self.vah

    def to_dict(self) -> dict:
        return {"poc": self.poc, "vah": self.vah, "val": self.val,
                "bin_size": self.bin_size, "total_volume": self.total_volume,
                "hvn": self.hvn, "lvn": self.lvn, "estimated": self.estimated}


def volume_profile(bars: Sequence[Bar], bins: int = 60,
                   value_area_pct: float = 0.70) -> Optional[VolumeProfile]:
    """Build a price-by-volume profile.

    Each bar's volume is spread uniformly across the price bins its range
    touches. That is an approximation of true tick-level distribution, but it
    is the standard one and it is stable; the alternative (assigning all volume
    to the close) produces a profile that jumps around with bar boundaries.
    """
    bars = [b for b in bars if b.high >= b.low]
    if not bars:
        return None
    hi = max(b.high for b in bars)
    lo = min(b.low for b in bars)
    if hi <= lo:
        return None
    bins = max(4, int(bins))
    bin_size = (hi - lo) / bins
    hist = [0.0] * bins
    for b in bars:
        first = min(bins - 1, max(0, int((b.low - lo) / bin_size)))
        last = min(bins - 1, max(0, int((b.high - lo) / bin_size)))
        span = last - first + 1
        share = (b.volume or 1.0) / span
        for k in range(first, last + 1):
            hist[k] += share

    total = sum(hist)
    if total <= 0:
        return None
    prices = [lo + (k + 0.5) * bin_size for k in range(bins)]
    poc_idx = max(range(bins), key=lambda k: hist[k])

    # Expand outward from the POC until the value area holds the target share.
    target = total * value_area_pct
    acc = hist[poc_idx]
    low_i = high_i = poc_idx
    while acc < target and (low_i > 0 or high_i < bins - 1):
        below = hist[low_i - 1] if low_i > 0 else -1.0
        above = hist[high_i + 1] if high_i < bins - 1 else -1.0
        if above >= below:
            high_i += 1
            acc += hist[high_i]
        else:
            low_i -= 1
            acc += hist[low_i]

    mean_vol = total / bins
    hvn = [prices[k] for k in range(bins) if hist[k] >= mean_vol * 1.5]
    lvn = [prices[k] for k in range(bins)
           if hist[k] <= mean_vol * 0.4 and low_i <= k <= high_i]

    return VolumeProfile(
        poc=prices[poc_idx], vah=prices[high_i], val=prices[low_i],
        bin_size=bin_size, histogram=list(zip(prices, hist)), total_volume=total,
        hvn=hvn, lvn=lvn,
        estimated=any(b.delta_is_estimated for b in bars),
    )


# --------------------------------------------------------------------------
# Relative volume / regime
# --------------------------------------------------------------------------

def relative_volume(bars: Sequence[Bar], lookback_days: int = 20) -> List[Num]:
    """Volume relative to the same time of day on prior sessions.

    Comparing a 09:31 bar's volume to a 20-bar average that spans lunch is
    meaningless. This compares like with like: the same clock minute across
    previous trading days.
    """
    out: List[Num] = [None] * len(bars)
    history: Dict[Tuple[int, int], List[float]] = {}
    for i, b in enumerate(bars):
        d = to_et(b.ts)
        key = (d.hour, d.minute)
        prior = history.get(key)
        if prior:
            avg = sum(prior) / len(prior)
            out[i] = (b.volume / avg) if avg > 0 else None
        history.setdefault(key, []).append(b.volume)
        if len(history[key]) > lookback_days:
            history[key].pop(0)
    return out


def volume_regime(bars: Sequence[Bar], lookback: int = 60) -> List[Optional[str]]:
    """Classify each bar's volume against its own trailing distribution."""
    vols = [b.volume for b in bars]
    pr = percent_rank(vols, min(lookback, max(2, len(vols))))
    out: List[Optional[str]] = [None] * len(bars)
    for i, p in enumerate(pr):
        if p is None:
            continue
        if p >= 0.90:
            out[i] = "SURGE"
        elif p >= 0.70:
            out[i] = "ABOVE_AVERAGE"
        elif p >= 0.30:
            out[i] = "AVERAGE"
        elif p >= 0.10:
            out[i] = "BELOW_AVERAGE"
        else:
            out[i] = "THIN"
    return out


# --------------------------------------------------------------------------
# Anchored VWAP
# --------------------------------------------------------------------------

@dataclass
class AnchorVWAP:
    """Volume-weighted average price measured from one chosen origin.

    Session VWAP answers "what is fair value today". Anchored VWAP answers
    "what has everyone who traded since *that* event paid on average", which is
    a different and often more useful question: it is the level at which the
    participants who entered on a move are collectively flat.

    **The look-ahead trap, and how it is handled.** The interesting anchors -
    the bar a major move departed from, a swing that later turned out to be
    significant - are only identifiable *after* the move. Anchoring there is
    legitimate; reading the line before the anchor was identifiable is not.
    So an anchor carries both ``index`` (where the calculation starts, which
    may be in the past) and ``knowable_index`` (the first bar at which a trader
    could have drawn it). :meth:`value_at` returns None before that bar, and
    the feature layer never exposes an anchor that is not yet knowable.

    That distinction is the whole reason this is not simply a VWAP with a
    different start: computing from a retrospective origin is standard
    practice, and *acting* on it before the origin was recognisable is
    look-ahead of the purest kind.
    """

    index: int                 # bar the calculation starts from
    knowable_index: int        # first bar at which this anchor was identifiable
    kind: str                  # "SWING_HIGH" | "SWING_LOW" | "DISPLACEMENT"
    ts: datetime
    values: List[Num] = field(default_factory=list)   # aligned to the series
    anchor_price: float = 0.0

    def value_at(self, i: int) -> Num:
        """The line at bar ``i``, or None where it must not be read."""
        if i < self.knowable_index or i >= len(self.values):
            return None
        return self.values[i]

    def distance_atr(self, i: int, price: float, atr_value: Optional[float]) -> Num:
        v = self.value_at(i)
        if v is None or not atr_value:
            return None
        return (price - v) / atr_value

    def to_dict(self) -> dict:
        return {"index": self.index, "knowable_index": self.knowable_index,
                "kind": self.kind, "anchor_price": round(self.anchor_price, 4)}


def build_anchor_vwap(bars: Sequence[Bar], anchor_index: int, *,
                      knowable_index: Optional[int] = None,
                      kind: str = "CUSTOM") -> Optional[AnchorVWAP]:
    """VWAP accumulated forward from ``anchor_index``, as a guarded object.

    Distinct from :func:`anchored_vwap`, which returns a bare list and has no
    notion of when the anchor became identifiable. Both exist because the list
    form is already part of this package's public surface; this form is the one
    to use when the anchor is chosen retrospectively, which is nearly always.
    """
    n = len(bars)
    if not 0 <= anchor_index < n:
        return None
    values: List[Num] = [None] * n
    pv = vol = 0.0
    for i in range(anchor_index, n):
        b = bars[i]
        v = b.volume if b.volume > 0 else 1.0
        pv += b.typical * v
        vol += v
        values[i] = pv / vol if vol > 0 else None
    return AnchorVWAP(index=anchor_index,
                      knowable_index=(anchor_index if knowable_index is None
                                      else max(anchor_index, knowable_index)),
                      kind=kind, ts=bars[anchor_index].ts, values=values,
                      anchor_price=bars[anchor_index].close)


def major_move_anchors(bars: Sequence[Bar], *, window: int = 20,
                       move_mult: float = 2.5, max_anchors: int = 40,
                       min_gap: int = 5) -> List[AnchorVWAP]:
    """Anchors at the origin of each major movement.

    A "major movement" is a bar whose range is ``move_mult`` times the recent
    average. The anchor is placed at the bar *before* it - the last bar of
    balance, which is where the participants who got run over were positioned -
    and becomes knowable only once the move has completed, because that is when
    a trader could have seen it.

    ``min_gap`` stops one impulse from producing a cluster of near-identical
    lines, which would look like confluence and be one observation.
    """
    n = len(bars)
    out: List[AnchorVWAP] = []
    last = -10 ** 9
    for i in range(window + 1, n):
        prior = bars[i - window:i]
        avg = sum(b.range for b in prior) / float(window)
        if avg <= 0 or bars[i].range < move_mult * avg:
            continue
        origin = max(0, i - 1)
        if origin - last < min_gap:
            continue
        last = origin
        av = build_anchor_vwap(bars, origin, knowable_index=i, kind="DISPLACEMENT")
        if av is not None:
            out.append(av)
    return out[-max_anchors:]


def swing_anchors(bars: Sequence[Bar], swings: Sequence[Any], *,
                  max_anchors: int = 20) -> List[AnchorVWAP]:
    """Anchors at confirmed swing highs and lows.

    ``swings`` are :class:`~futures_agents.indicators.structure.Swing` objects,
    whose ``confirmed_index`` is exactly the knowable point - the bar at which
    the fractal completed and the swing could be drawn.
    """
    out: List[AnchorVWAP] = []
    for s in swings:
        av = build_anchor_vwap(bars, s.index, knowable_index=s.confirmed_index,
                               kind="SWING_HIGH" if s.is_high else "SWING_LOW")
        if av is not None:
            out.append(av)
    return out[-max_anchors:]
