"""Classical price-based indicators.

Contract for every function here:

* output length == input length,
* ``None`` where the value is undefined (warm-up),
* index *i* of the output uses only inputs at indices <= *i*.

The third rule is the one that matters. Indicators implemented with a
vectorised "centred" window - a common shortcut - leak future data and make a
backtest look brilliant and a live account look broken.
"""

from __future__ import annotations

import math
from typing import List, Optional, Sequence, Tuple

Num = Optional[float]

__all__ = [
    "sma", "ema", "wma", "rma", "stdev", "rsi", "macd", "bollinger",
    "true_range", "atr", "rolling_max", "rolling_min", "roc", "momentum",
    "linreg_slope", "zscore", "stochastic", "adx", "keltner", "fib_levels",
    "pivot_points", "crossed_above", "crossed_below", "slope_normalised",
    "percent_rank", "last_valid",
]


def last_valid(values: Sequence[Num]) -> Optional[float]:
    """Most recent non-None value, or None."""
    for v in reversed(values):
        if v is not None:
            return v
    return None


def _check(period: int, name: str = "period") -> int:
    p = int(period)
    if p < 1:
        raise ValueError(f"{name} must be >= 1, got {period}")
    return p


# --------------------------------------------------------------------------
# Moving averages
# --------------------------------------------------------------------------

def sma(values: Sequence[float], period: int) -> List[Num]:
    """Simple moving average."""
    p = _check(period)
    out: List[Num] = [None] * len(values)
    total = 0.0
    for i, v in enumerate(values):
        total += v
        if i >= p:
            total -= values[i - p]
        if i >= p - 1:
            out[i] = total / p
    return out


def ema(values: Sequence[float], period: int) -> List[Num]:
    """Exponential moving average, seeded with the first full SMA.

    Seeding with the SMA (rather than the first value) removes the long
    initialisation transient that otherwise biases early backtest bars.
    """
    p = _check(period)
    out: List[Num] = [None] * len(values)
    if len(values) < p:
        return out
    k = 2.0 / (p + 1.0)
    prev = sum(values[:p]) / p
    out[p - 1] = prev
    for i in range(p, len(values)):
        prev = values[i] * k + prev * (1.0 - k)
        out[i] = prev
    return out


def wma(values: Sequence[float], period: int) -> List[Num]:
    """Linearly weighted moving average."""
    p = _check(period)
    out: List[Num] = [None] * len(values)
    denom = p * (p + 1) / 2.0
    for i in range(p - 1, len(values)):
        window = values[i - p + 1: i + 1]
        out[i] = sum(v * (j + 1) for j, v in enumerate(window)) / denom
    return out


def rma(values: Sequence[float], period: int) -> List[Num]:
    """Wilder's smoothing (used by RSI, ATR and ADX)."""
    p = _check(period)
    out: List[Num] = [None] * len(values)
    if len(values) < p:
        return out
    prev = sum(values[:p]) / p
    out[p - 1] = prev
    for i in range(p, len(values)):
        prev = (prev * (p - 1) + values[i]) / p
        out[i] = prev
    return out


# --------------------------------------------------------------------------
# Dispersion
# --------------------------------------------------------------------------

def stdev(values: Sequence[float], period: int, *, sample: bool = False) -> List[Num]:
    """Rolling standard deviation via Welford-style running sums."""
    p = _check(period)
    out: List[Num] = [None] * len(values)
    s = s2 = 0.0
    denom = (p - 1) if (sample and p > 1) else p
    for i, v in enumerate(values):
        s += v
        s2 += v * v
        if i >= p:
            old = values[i - p]
            s -= old
            s2 -= old * old
        if i >= p - 1:
            mean = s / p
            var = max(0.0, (s2 - p * mean * mean) / denom)
            out[i] = math.sqrt(var)
    return out


def zscore(values: Sequence[float], period: int) -> List[Num]:
    """(x - mean) / sd over a trailing window."""
    m = sma(values, period)
    sd = stdev(values, period)
    out: List[Num] = [None] * len(values)
    for i in range(len(values)):
        if m[i] is None or sd[i] is None or sd[i] == 0:
            continue
        out[i] = (values[i] - m[i]) / sd[i]
    return out


def percent_rank(values: Sequence[float], period: int) -> List[Num]:
    """Fraction of the trailing window below the current value, in [0, 1].

    Used for regime work, where "is today's ATR high" only means anything
    relative to its own recent distribution.
    """
    p = _check(period)
    out: List[Num] = [None] * len(values)
    for i in range(p - 1, len(values)):
        window = values[i - p + 1: i + 1]
        cur = values[i]
        below = sum(1 for v in window if v < cur)
        out[i] = below / float(len(window))
    return out


# --------------------------------------------------------------------------
# Oscillators
# --------------------------------------------------------------------------

def rsi(values: Sequence[float], period: int = 14) -> List[Num]:
    """Wilder's RSI."""
    p = _check(period)
    out: List[Num] = [None] * len(values)
    if len(values) <= p:
        return out
    gains, losses = [0.0], [0.0]
    for i in range(1, len(values)):
        change = values[i] - values[i - 1]
        gains.append(max(0.0, change))
        losses.append(max(0.0, -change))
    avg_gain = sum(gains[1:p + 1]) / p
    avg_loss = sum(losses[1:p + 1]) / p
    out[p] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    for i in range(p + 1, len(values)):
        avg_gain = (avg_gain * (p - 1) + gains[i]) / p
        avg_loss = (avg_loss * (p - 1) + losses[i]) / p
        out[i] = 100.0 if avg_loss == 0 else 100.0 - (100.0 / (1.0 + avg_gain / avg_loss))
    return out


def macd(values: Sequence[float], fast: int = 12, slow: int = 26,
         signal: int = 9) -> Tuple[List[Num], List[Num], List[Num]]:
    """MACD line, signal line and histogram."""
    if fast >= slow:
        raise ValueError(f"fast ({fast}) must be shorter than slow ({slow})")
    ef, es = ema(values, fast), ema(values, slow)
    line: List[Num] = [None if (a is None or b is None) else a - b for a, b in zip(ef, es)]
    valid = [v for v in line if v is not None]
    sig: List[Num] = [None] * len(values)
    if len(valid) >= signal:
        start = next(i for i, v in enumerate(line) if v is not None)
        sig_valid = ema(valid, signal)
        for off, v in enumerate(sig_valid):
            sig[start + off] = v
    hist: List[Num] = [None if (a is None or b is None) else a - b
                       for a, b in zip(line, sig)]
    return line, sig, hist


def stochastic(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
               k_period: int = 14, d_period: int = 3,
               smooth: int = 3) -> Tuple[List[Num], List[Num]]:
    """Slow stochastic %K and %D."""
    n = len(closes)
    hh, ll = rolling_max(highs, k_period), rolling_min(lows, k_period)
    raw: List[Num] = [None] * n
    for i in range(n):
        if hh[i] is None or ll[i] is None:
            continue
        span = hh[i] - ll[i]
        raw[i] = 50.0 if span == 0 else (closes[i] - ll[i]) / span * 100.0
    valid = [v for v in raw if v is not None]
    k: List[Num] = [None] * n
    if len(valid) >= smooth:
        start = next(i for i, v in enumerate(raw) if v is not None)
        for off, v in enumerate(sma(valid, smooth)):
            k[start + off] = v
    kvalid = [v for v in k if v is not None]
    d: List[Num] = [None] * n
    if len(kvalid) >= d_period:
        start = next(i for i, v in enumerate(k) if v is not None)
        for off, v in enumerate(sma(kvalid, d_period)):
            d[start + off] = v
    return k, d


# --------------------------------------------------------------------------
# Volatility
# --------------------------------------------------------------------------

def true_range(highs: Sequence[float], lows: Sequence[float],
               closes: Sequence[float]) -> List[Num]:
    """True range. The first bar has no previous close, so it is ``None``."""
    out: List[Num] = [None] * len(closes)
    for i in range(1, len(closes)):
        pc = closes[i - 1]
        out[i] = max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc))
    return out


def atr(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        period: int = 14) -> List[Num]:
    """Average true range (Wilder)."""
    tr = true_range(highs, lows, closes)
    valid = [v for v in tr if v is not None]
    out: List[Num] = [None] * len(closes)
    if len(valid) < period:
        return out
    smoothed = rma(valid, period)
    for off, v in enumerate(smoothed):
        out[off + 1] = v
    return out


def bollinger(values: Sequence[float], period: int = 20,
              mult: float = 2.0) -> Tuple[List[Num], List[Num], List[Num]]:
    """Bollinger bands: (upper, middle, lower)."""
    mid = sma(values, period)
    sd = stdev(values, period)
    up: List[Num] = [None] * len(values)
    lo: List[Num] = [None] * len(values)
    for i in range(len(values)):
        if mid[i] is None or sd[i] is None:
            continue
        up[i] = mid[i] + mult * sd[i]
        lo[i] = mid[i] - mult * sd[i]
    return up, mid, lo


def keltner(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
            period: int = 20, mult: float = 1.5) -> Tuple[List[Num], List[Num], List[Num]]:
    """Keltner channels around an EMA, width set by ATR."""
    mid = ema(closes, period)
    a = atr(highs, lows, closes, period)
    up: List[Num] = [None] * len(closes)
    lo: List[Num] = [None] * len(closes)
    for i in range(len(closes)):
        if mid[i] is None or a[i] is None:
            continue
        up[i] = mid[i] + mult * a[i]
        lo[i] = mid[i] - mult * a[i]
    return up, mid, lo


def adx(highs: Sequence[float], lows: Sequence[float], closes: Sequence[float],
        period: int = 14) -> Tuple[List[Num], List[Num], List[Num]]:
    """ADX with +DI and -DI. The system's primary trend/range discriminator."""
    n = len(closes)
    out_adx: List[Num] = [None] * n
    out_pdi: List[Num] = [None] * n
    out_mdi: List[Num] = [None] * n
    if n < period * 2 + 1:
        return out_adx, out_pdi, out_mdi

    plus_dm, minus_dm, trs = [], [], []
    for i in range(1, n):
        up_move = highs[i] - highs[i - 1]
        down_move = lows[i - 1] - lows[i]
        plus_dm.append(up_move if (up_move > down_move and up_move > 0) else 0.0)
        minus_dm.append(down_move if (down_move > up_move and down_move > 0) else 0.0)
        pc = closes[i - 1]
        trs.append(max(highs[i] - lows[i], abs(highs[i] - pc), abs(lows[i] - pc)))

    str_ = rma(trs, period)
    spdm = rma(plus_dm, period)
    smdm = rma(minus_dm, period)

    dx: List[Num] = [None] * len(trs)
    for i in range(len(trs)):
        if str_[i] is None or spdm[i] is None or smdm[i] is None or str_[i] == 0:
            continue
        pdi = 100.0 * spdm[i] / str_[i]
        mdi = 100.0 * smdm[i] / str_[i]
        out_pdi[i + 1] = pdi
        out_mdi[i + 1] = mdi
        denom = pdi + mdi
        dx[i] = 0.0 if denom == 0 else 100.0 * abs(pdi - mdi) / denom

    dxv = [v for v in dx if v is not None]
    if len(dxv) >= period:
        start = next(i for i, v in enumerate(dx) if v is not None)
        for off, v in enumerate(rma(dxv, period)):
            out_adx[start + off + 1] = v
    return out_adx, out_pdi, out_mdi


# --------------------------------------------------------------------------
# Rolling extremes, momentum, regression
# --------------------------------------------------------------------------

def rolling_max(values: Sequence[float], period: int) -> List[Num]:
    p = _check(period)
    out: List[Num] = [None] * len(values)
    for i in range(p - 1, len(values)):
        out[i] = max(values[i - p + 1: i + 1])
    return out


def rolling_min(values: Sequence[float], period: int) -> List[Num]:
    p = _check(period)
    out: List[Num] = [None] * len(values)
    for i in range(p - 1, len(values)):
        out[i] = min(values[i - p + 1: i + 1])
    return out


def roc(values: Sequence[float], period: int) -> List[Num]:
    """Rate of change, in percent."""
    p = _check(period)
    out: List[Num] = [None] * len(values)
    for i in range(p, len(values)):
        prev = values[i - p]
        if prev:
            out[i] = (values[i] - prev) / abs(prev) * 100.0
    return out


def momentum(values: Sequence[float], period: int) -> List[Num]:
    """Absolute change over ``period`` bars."""
    p = _check(period)
    out: List[Num] = [None] * len(values)
    for i in range(p, len(values)):
        out[i] = values[i] - values[i - p]
    return out


def linreg_slope(values: Sequence[float], period: int) -> List[Num]:
    """Least-squares slope per bar over a trailing window."""
    p = _check(period)
    out: List[Num] = [None] * len(values)
    if p < 2:
        return out
    xs = list(range(p))
    sx = sum(xs)
    sxx = sum(x * x for x in xs)
    denom = p * sxx - sx * sx
    if denom == 0:
        return out
    for i in range(p - 1, len(values)):
        w = values[i - p + 1: i + 1]
        sy = sum(w)
        sxy = sum(x * y for x, y in zip(xs, w))
        out[i] = (p * sxy - sx * sy) / denom
    return out


def slope_normalised(values: Sequence[float], period: int,
                     scale: Sequence[Num]) -> List[Num]:
    """Regression slope expressed in units of ``scale`` (typically ATR).

    A raw slope is not comparable across symbols or volatility regimes;
    dividing by ATR makes "steep" mean the same thing on MNQ and MCL.
    """
    sl = linreg_slope(values, period)
    out: List[Num] = [None] * len(values)
    for i in range(len(values)):
        s = sl[i]
        sc = scale[i] if i < len(scale) else None
        if s is None or sc in (None, 0):
            continue
        out[i] = s / sc
    return out


def crossed_above(a: Sequence[Num], b: Sequence[Num], i: int) -> bool:
    """True when series ``a`` crossed above ``b`` on bar ``i``."""
    if i < 1 or i >= len(a) or i >= len(b):
        return False
    if a[i] is None or b[i] is None or a[i - 1] is None or b[i - 1] is None:
        return False
    return a[i - 1] <= b[i - 1] and a[i] > b[i]


def crossed_below(a: Sequence[Num], b: Sequence[Num], i: int) -> bool:
    if i < 1 or i >= len(a) or i >= len(b):
        return False
    if a[i] is None or b[i] is None or a[i - 1] is None or b[i - 1] is None:
        return False
    return a[i - 1] >= b[i - 1] and a[i] < b[i]


# --------------------------------------------------------------------------
# Levels
# --------------------------------------------------------------------------

def fib_levels(high: float, low: float, *, extend: bool = True) -> dict:
    """Retracement and (optionally) extension levels for a swing."""
    span = high - low
    levels = {
        "0.0": low, "0.236": low + 0.236 * span, "0.382": low + 0.382 * span,
        "0.5": low + 0.5 * span, "0.618": low + 0.618 * span,
        "0.705": low + 0.705 * span, "0.786": low + 0.786 * span, "1.0": high,
    }
    if extend:
        levels.update({
            "1.272": low + 1.272 * span, "1.414": low + 1.414 * span,
            "1.618": low + 1.618 * span, "2.0": low + 2.0 * span,
        })
    return levels


def pivot_points(high: float, low: float, close: float, *,
                 style: str = "classic") -> dict:
    """Floor-trader pivots from the previous session's H/L/C."""
    if style == "fibonacci":
        p = (high + low + close) / 3.0
        rng = high - low
        return {"P": p,
                "R1": p + 0.382 * rng, "R2": p + 0.618 * rng, "R3": p + 1.0 * rng,
                "S1": p - 0.382 * rng, "S2": p - 0.618 * rng, "S3": p - 1.0 * rng}
    if style == "camarilla":
        rng = high - low
        return {"P": (high + low + close) / 3.0,
                "R1": close + rng * 1.1 / 12, "R2": close + rng * 1.1 / 6,
                "R3": close + rng * 1.1 / 4, "R4": close + rng * 1.1 / 2,
                "S1": close - rng * 1.1 / 12, "S2": close - rng * 1.1 / 6,
                "S3": close - rng * 1.1 / 4, "S4": close - rng * 1.1 / 2}
    p = (high + low + close) / 3.0
    return {"P": p, "R1": 2 * p - low, "R2": p + (high - low),
            "R3": high + 2 * (p - low),
            "S1": 2 * p - high, "S2": p - (high - low),
            "S3": low - 2 * (high - p)}
