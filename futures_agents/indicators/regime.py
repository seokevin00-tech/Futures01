"""Market regime classification.

Regime is the single most important conditioning variable in this system: the
same strategy that prints money in a trend bleeds out in a range, and a
strategy's historical statistics are only meaningful when sliced by the regime
they were earned in. Classification is deliberately boring and mechanical -
ADX for directional strength, Bollinger width and ATR percentile for
volatility state, normalised regression slope for direction - so that the
label is reproducible rather than a matter of interpretation.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Sequence, Tuple

from ..data.bars import Bar, BarSeries
from .core import (adx, atr, bollinger, ema, linreg_slope, percent_rank,
                   rolling_max, rolling_min, sma, stdev)

Num = Optional[float]

__all__ = ["RegimeSnapshot", "classify_regime", "volatility_regime", "trend_strength"]


@dataclass
class RegimeSnapshot:
    """A complete regime reading for one bar of one timeframe."""

    regime: str = "UNKNOWN"              # TREND_UP|TREND_DOWN|RANGE|VOLATILE_EXPANSION|COMPRESSION
    volatility: str = "NORMAL"           # DEAD|LOW|NORMAL|HIGH|EXTREME
    volume: str = "AVERAGE"
    adx: Optional[float] = None
    plus_di: Optional[float] = None
    minus_di: Optional[float] = None
    atr: Optional[float] = None
    atr_percentile: Optional[float] = None
    atr_median: Optional[float] = None
    bb_width_pct: Optional[float] = None
    bb_width_percentile: Optional[float] = None
    slope_atr: Optional[float] = None    # regression slope in ATR units per bar
    ema_stack: str = "MIXED"             # BULLISH | BEARISH | MIXED
    efficiency_ratio: Optional[float] = None
    confidence: float = 0.0

    @property
    def is_trending(self) -> bool:
        return self.regime in ("TREND_UP", "TREND_DOWN")

    @property
    def is_tradeable_volatility(self) -> bool:
        return self.volatility in ("LOW", "NORMAL", "HIGH")

    def to_dict(self) -> dict:
        return {
            "regime": self.regime, "volatility": self.volatility, "volume": self.volume,
            "adx": _r(self.adx), "plus_di": _r(self.plus_di), "minus_di": _r(self.minus_di),
            "atr": _r(self.atr, 4), "atr_percentile": _r(self.atr_percentile, 3),
            "bb_width_pct": _r(self.bb_width_pct, 4),
            "bb_width_percentile": _r(self.bb_width_percentile, 3),
            "slope_atr": _r(self.slope_atr, 4), "ema_stack": self.ema_stack,
            "efficiency_ratio": _r(self.efficiency_ratio, 3),
            "confidence": round(self.confidence, 3),
        }

    def summary(self) -> str:
        parts = [self.regime, f"vol {self.volatility}"]
        if self.adx is not None:
            parts.append(f"ADX {self.adx:.1f}")
        if self.slope_atr is not None:
            parts.append(f"slope {self.slope_atr:+.2f} ATR/bar")
        if self.efficiency_ratio is not None:
            parts.append(f"ER {self.efficiency_ratio:.2f}")
        return f"{parts[0]} ({', '.join(parts[1:])})"


def _r(v: Optional[float], nd: int = 2) -> Optional[float]:
    return None if v is None else round(v, nd)


def efficiency_ratio(closes: Sequence[float], period: int = 20) -> List[Num]:
    """Kaufman efficiency ratio: net change / total path length, in [0, 1].

    Near 1 means price moved in a straight line (trend); near 0 means it
    travelled a long way to get nowhere (chop). It is the cleanest single
    trend/range discriminator that does not depend on a threshold.
    """
    out: List[Num] = [None] * len(closes)
    for i in range(period, len(closes)):
        net = abs(closes[i] - closes[i - period])
        path = sum(abs(closes[j] - closes[j - 1]) for j in range(i - period + 1, i + 1))
        out[i] = (net / path) if path > 0 else 0.0
    return out


def trend_strength(bars: Sequence[Bar], period: int = 14) -> Optional[float]:
    """Signed trend strength in [-1, 1]: ADX scaled, signed by +DI/-DI."""
    if len(bars) < period * 3:
        return None
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    closes = [b.close for b in bars]
    a, p, m = adx(highs, lows, closes, period)
    if a[-1] is None or p[-1] is None or m[-1] is None:
        return None
    magnitude = min(1.0, a[-1] / 50.0)
    return magnitude if p[-1] >= m[-1] else -magnitude


def volatility_regime(bars: Sequence[Bar], *, atr_period: int = 14,
                      lookback: int = 250) -> Tuple[str, Optional[float], Optional[float]]:
    """Classify volatility against the series' own trailing ATR distribution.

    Returns ``(label, atr_value, percentile)``. Percentile - not an absolute
    threshold - because 40 points of ATR is calm for MNQ and impossible for MES.
    """
    if len(bars) < atr_period + 5:
        return "NORMAL", None, None
    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    closes = [b.close for b in bars]
    a = atr(highs, lows, closes, atr_period)
    valid = [v for v in a if v is not None]
    if not valid:
        return "NORMAL", None, None
    cur = valid[-1]
    window = valid[-lookback:] if len(valid) > lookback else valid
    if len(window) < 10:
        return "NORMAL", cur, None
    pct = sum(1 for v in window if v < cur) / len(window)
    if pct >= 0.95:
        label = "EXTREME"
    elif pct >= 0.75:
        label = "HIGH"
    elif pct >= 0.25:
        label = "NORMAL"
    elif pct >= 0.08:
        label = "LOW"
    else:
        label = "DEAD"
    return label, cur, pct


def classify_regime(bars: Sequence[Bar], *, adx_period: int = 14,
                    bb_period: int = 20, slope_period: int = 20,
                    lookback: int = 250) -> RegimeSnapshot:
    """Full regime classification for the final bar of ``bars``.

    Decision order:

    1. Volatility state first (an EXTREME reading overrides everything - it is
       a risk signal before it is an opportunity signal).
    2. Compression when Bollinger width sits in the bottom decile of its own
       history: the market is coiled, and breakout strategies outperform while
       mean-reversion strategies get run over.
    3. Trend when ADX and efficiency ratio agree, with direction from the
       normalised slope and the DI spread.
    4. Range otherwise.
    """
    snap = RegimeSnapshot()
    n = len(bars)
    if n < max(adx_period * 3, bb_period + 5, slope_period + 5):
        return snap

    highs = [b.high for b in bars]
    lows = [b.low for b in bars]
    closes = [b.close for b in bars]

    a, pdi, mdi = adx(highs, lows, closes, adx_period)
    snap.adx, snap.plus_di, snap.minus_di = a[-1], pdi[-1], mdi[-1]

    vol_label, atr_val, atr_pct = volatility_regime(bars, atr_period=adx_period,
                                                    lookback=lookback)
    snap.volatility, snap.atr, snap.atr_percentile = vol_label, atr_val, atr_pct

    atr_series = atr(highs, lows, closes, adx_period)
    atr_valid = sorted(v for v in atr_series if v is not None)
    if atr_valid:
        snap.atr_median = atr_valid[len(atr_valid) // 2]

    up, mid, lo = bollinger(closes, bb_period)
    if up[-1] is not None and lo[-1] is not None and mid[-1]:
        widths = [(u - l) / m * 100.0
                  for u, m, l in zip(up, mid, lo)
                  if u is not None and l is not None and m]
        snap.bb_width_pct = widths[-1] if widths else None
        if len(widths) >= 20:
            window = widths[-lookback:]
            snap.bb_width_percentile = (
                sum(1 for w in window if w < widths[-1]) / len(window))

    slope = linreg_slope(closes, slope_period)[-1]
    if slope is not None and atr_val:
        snap.slope_atr = slope / atr_val

    er = efficiency_ratio(closes, min(20, n - 1))
    snap.efficiency_ratio = er[-1]

    e_fast, e_mid, e_slow = ema(closes, 9)[-1], ema(closes, 21)[-1], ema(closes, 50)[-1]
    if None not in (e_fast, e_mid, e_slow):
        if e_fast > e_mid > e_slow:
            snap.ema_stack = "BULLISH"
        elif e_fast < e_mid < e_slow:
            snap.ema_stack = "BEARISH"

    # ---- decision ----
    adx_v = snap.adx or 0.0
    er_v = snap.efficiency_ratio or 0.0
    slope_v = snap.slope_atr or 0.0
    bbp = snap.bb_width_percentile

    if snap.volatility == "EXTREME":
        snap.regime = "VOLATILE_EXPANSION"
        snap.confidence = 0.80
    elif bbp is not None and bbp <= 0.10 and adx_v < 22:
        snap.regime = "COMPRESSION"
        snap.confidence = 0.70
    elif adx_v >= 25 and er_v >= 0.30:
        up_trend = (slope_v > 0) if slope_v else ((snap.plus_di or 0) >= (snap.minus_di or 0))
        snap.regime = "TREND_UP" if up_trend else "TREND_DOWN"
        snap.confidence = min(0.95, 0.45 + adx_v / 100.0 + er_v * 0.4)
        # A trend label needs the EMA stack to agree; if it fights the slope,
        # this is a pullback inside a range dressed up as a trend.
        if ((snap.regime == "TREND_UP" and snap.ema_stack == "BEARISH")
                or (snap.regime == "TREND_DOWN" and snap.ema_stack == "BULLISH")):
            snap.regime = "RANGE"
            snap.confidence = 0.45
    else:
        snap.regime = "RANGE"
        snap.confidence = min(0.85, 0.40 + (25.0 - min(adx_v, 25.0)) / 50.0)

    return snap
