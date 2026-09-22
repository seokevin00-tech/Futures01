"""Bar primitives and look-ahead-free resampling.

The single most destructive bug class in a backtest is future-data leakage, so
this module encodes two invariants:

* A higher-timeframe bar built from lower-timeframe bars carries a ``complete``
  flag. Strategies may only read *completed* higher-timeframe bars; the
  in-progress bar is visible only as the "developing" bar and is never used for
  a signal that would not have been available in real time.

* ``BarSeries`` is append-only in time order and validates monotonicity on
  construction, so an out-of-order file cannot silently reorder history.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from ..timeutil import ET, to_et, trading_day

__all__ = ["Bar", "BarSeries", "resample", "align_bucket"]


@dataclass(frozen=True)
class Bar:
    """One OHLCV bar.

    ``bid_volume``/``ask_volume`` carry true order-flow when the feed provides
    it. When it does not, :meth:`estimated_delta` derives a proxy and
    ``delta_is_estimated`` stays True - the distinction matters, because an
    order-flow strategy validated on estimated delta has not been validated.
    """

    ts: datetime                   # bar OPEN time, Eastern
    open: float
    high: float
    low: float
    close: float
    volume: float = 0.0
    minutes: int = 1               # bar duration
    bid_volume: Optional[float] = None    # traded at bid (seller-initiated)
    ask_volume: Optional[float] = None    # traded at ask (buyer-initiated)
    open_interest: Optional[float] = None
    complete: bool = True

    # ---- derived ----------------------------------------------------
    @property
    def end_ts(self) -> datetime:
        return self.ts + timedelta(minutes=self.minutes)

    @property
    def range(self) -> float:
        return self.high - self.low

    @property
    def body(self) -> float:
        return self.close - self.open

    @property
    def body_abs(self) -> float:
        return abs(self.close - self.open)

    @property
    def upper_wick(self) -> float:
        return self.high - max(self.open, self.close)

    @property
    def lower_wick(self) -> float:
        return min(self.open, self.close) - self.low

    @property
    def is_up(self) -> bool:
        return self.close > self.open

    @property
    def typical(self) -> float:
        return (self.high + self.low + self.close) / 3.0

    @property
    def hlc4(self) -> float:
        return (self.high + self.low + self.close + self.open) / 4.0

    @property
    def mid(self) -> float:
        return (self.high + self.low) / 2.0

    @property
    def delta_is_estimated(self) -> bool:
        return self.bid_volume is None or self.ask_volume is None

    @property
    def delta(self) -> float:
        """Volume delta: buyer-initiated minus seller-initiated volume.

        With a real bid/ask split this is exact. Without one it falls back to
        the close-location-value proxy, which correlates with true delta but is
        not a substitute for it.
        """
        if self.bid_volume is not None and self.ask_volume is not None:
            return self.ask_volume - self.bid_volume
        return self.estimated_delta()

    def estimated_delta(self) -> float:
        """Close-location-value delta proxy in [-volume, +volume].

        CLV = ((C - L) - (H - C)) / (H - L); a bar closing on its high implies
        buyers absorbed the range, a bar closing on its low implies sellers did.
        """
        rng = self.high - self.low
        if rng <= 0 or self.volume <= 0:
            return 0.0
        clv = ((self.close - self.low) - (self.high - self.close)) / rng
        return clv * self.volume

    def validate(self) -> None:
        if not (self.low <= self.open <= self.high):
            raise ValueError(f"Bar {self.ts}: open {self.open} outside [{self.low},{self.high}]")
        if not (self.low <= self.close <= self.high):
            raise ValueError(f"Bar {self.ts}: close {self.close} outside [{self.low},{self.high}]")
        if self.high < self.low:
            raise ValueError(f"Bar {self.ts}: high {self.high} < low {self.low}")
        if self.volume < 0:
            raise ValueError(f"Bar {self.ts}: negative volume")

    def to_dict(self) -> dict:
        return {
            "ts": to_et(self.ts).isoformat(), "open": self.open, "high": self.high,
            "low": self.low, "close": self.close, "volume": self.volume,
            "minutes": self.minutes, "bid_volume": self.bid_volume,
            "ask_volume": self.ask_volume, "open_interest": self.open_interest,
            "complete": self.complete,
        }


def align_bucket(ts: datetime, minutes: int) -> datetime:
    """Start time of the ``minutes``-bucket containing ``ts``.

    Intraday buckets align to the hour (so a 15m bar starts at :00/:15/:30/:45
    exactly as every charting package draws it). Daily buckets align to the
    CME trading day, which starts at 18:00 ET the previous evening.
    """
    d = to_et(ts)
    if minutes >= 1440:
        day = trading_day(d)
        return datetime.combine(day - timedelta(days=1), datetime.min.time(),
                                tzinfo=ET).replace(hour=18)
    if minutes >= 60:
        # Align multi-hour buckets to midnight so 4h bars fall on 00/04/08/12/16/20.
        total = d.hour * 60 + d.minute
        start = (total // minutes) * minutes
        return d.replace(hour=start // 60, minute=start % 60, second=0, microsecond=0)
    start_min = (d.minute // minutes) * minutes
    return d.replace(minute=start_min, second=0, microsecond=0)


class BarSeries:
    """An append-only, time-ordered series of same-duration bars."""

    __slots__ = ("symbol", "minutes", "_bars", "_index")

    def __init__(self, symbol: str, minutes: int, bars: Optional[Iterable[Bar]] = None):
        self.symbol = symbol.upper()
        self.minutes = int(minutes)
        self._bars: List[Bar] = []
        self._index: Dict[datetime, int] = {}
        for b in bars or ():
            self.append(b)

    # ---- container protocol ----------------------------------------
    def __len__(self) -> int:
        return len(self._bars)

    def __iter__(self) -> Iterator[Bar]:
        return iter(self._bars)

    def __getitem__(self, item):
        if isinstance(item, slice):
            return BarSeries(self.symbol, self.minutes, self._bars[item])
        return self._bars[item]

    def __repr__(self) -> str:
        if not self._bars:
            return f"<BarSeries {self.symbol} {self.minutes}m empty>"
        return (f"<BarSeries {self.symbol} {self.minutes}m n={len(self._bars)} "
                f"{self._bars[0].ts:%Y-%m-%d %H:%M}..{self._bars[-1].ts:%Y-%m-%d %H:%M}>")

    # ---- mutation ---------------------------------------------------
    def append(self, bar: Bar) -> None:
        """Append a bar, enforcing duration and strict time ordering."""
        if bar.minutes != self.minutes:
            raise ValueError(
                f"{self.symbol}: bar duration {bar.minutes}m != series {self.minutes}m")
        bar.validate()
        b = bar if bar.ts.tzinfo else replace(bar, ts=to_et(bar.ts))
        if self._bars:
            last = self._bars[-1]
            if b.ts < last.ts:
                raise ValueError(
                    f"{self.symbol}: bar {b.ts} precedes last bar {last.ts}; "
                    "input is not time-ordered")
            if b.ts == last.ts:
                # Same bucket: replace (a developing bar being finalised).
                self._bars[-1] = b
                return
        self._index[b.ts] = len(self._bars)
        self._bars.append(b)

    def extend(self, bars: Iterable[Bar]) -> None:
        for b in bars:
            self.append(b)

    # ---- access -----------------------------------------------------
    @property
    def bars(self) -> List[Bar]:
        return self._bars

    @property
    def last(self) -> Optional[Bar]:
        return self._bars[-1] if self._bars else None

    @property
    def completed(self) -> "BarSeries":
        """The series with any in-progress trailing bar removed.

        This is what strategies are handed. Reading the developing bar's close
        as if it were final is the most common form of look-ahead bias.
        """
        if self._bars and not self._bars[-1].complete:
            return BarSeries(self.symbol, self.minutes, self._bars[:-1])
        return self

    def at(self, ts: datetime) -> Optional[Bar]:
        idx = self._index.get(to_et(ts))
        return self._bars[idx] if idx is not None else None

    def before(self, ts: datetime, inclusive: bool = False) -> "BarSeries":
        """Every bar strictly before ``ts`` - the look-ahead-safe slice."""
        t = to_et(ts)
        out = [b for b in self._bars if (b.ts <= t if inclusive else b.ts < t)]
        return BarSeries(self.symbol, self.minutes, out)

    def tail(self, n: int) -> "BarSeries":
        return BarSeries(self.symbol, self.minutes, self._bars[-n:] if n > 0 else [])

    # ---- column views ----------------------------------------------
    def closes(self) -> List[float]:
        return [b.close for b in self._bars]

    def opens(self) -> List[float]:
        return [b.open for b in self._bars]

    def highs(self) -> List[float]:
        return [b.high for b in self._bars]

    def lows(self) -> List[float]:
        return [b.low for b in self._bars]

    def volumes(self) -> List[float]:
        return [b.volume for b in self._bars]

    def deltas(self) -> List[float]:
        return [b.delta for b in self._bars]

    def timestamps(self) -> List[datetime]:
        return [b.ts for b in self._bars]

    def typicals(self) -> List[float]:
        return [b.typical for b in self._bars]

    # ---- transforms -------------------------------------------------
    def resample(self, minutes: int, *, keep_partial: bool = True) -> "BarSeries":
        return resample(self, minutes, keep_partial=keep_partial)

    def session_slice(self, day, open_hhmm: str = "09:30",
                      close_hhmm: str = "16:00") -> "BarSeries":
        """Bars belonging to one RTH session."""
        from ..timeutil import rth_bounds
        o, c = rth_bounds(day, open_hhmm, close_hhmm)
        return BarSeries(self.symbol, self.minutes,
                         [b for b in self._bars if o <= b.ts < c])

    def trading_day_slice(self, day) -> "BarSeries":
        return BarSeries(self.symbol, self.minutes,
                         [b for b in self._bars if trading_day(b.ts) == day])

    def trading_days(self) -> List:
        seen, out = set(), []
        for b in self._bars:
            d = trading_day(b.ts)
            if d not in seen:
                seen.add(d)
                out.append(d)
        return out

    def to_dicts(self) -> List[dict]:
        return [b.to_dict() for b in self._bars]


def resample(series: BarSeries, minutes: int, *, keep_partial: bool = True) -> BarSeries:
    """Aggregate a series into a higher timeframe.

    The final bucket is emitted with ``complete=False`` when it has not yet
    received all of its constituent bars, which lets downstream code choose
    between the confirmed series (``.completed``) and the developing bar.
    """
    if minutes < series.minutes:
        raise ValueError(
            f"Cannot resample {series.minutes}m data up to a shorter {minutes}m "
            "timeframe - that would require data the series does not contain")
    if minutes == series.minutes:
        return BarSeries(series.symbol, minutes, series.bars)
    if minutes % series.minutes != 0 and minutes < 1440:
        raise ValueError(
            f"{minutes}m is not a whole multiple of the {series.minutes}m source")

    out = BarSeries(series.symbol, minutes)
    bucket_start: Optional[datetime] = None
    o = h = l = c = 0.0
    vol = bid = ask = 0.0
    has_flow = True
    oi: Optional[float] = None
    count = 0
    expected = max(1, minutes // series.minutes)

    def emit(complete: bool) -> None:
        nonlocal bucket_start
        if bucket_start is None:
            return
        out.append(Bar(
            ts=bucket_start, open=o, high=h, low=l, close=c, volume=vol,
            minutes=minutes,
            bid_volume=bid if has_flow else None,
            ask_volume=ask if has_flow else None,
            open_interest=oi, complete=complete,
        ))

    for b in series:
        start = align_bucket(b.ts, minutes)
        if bucket_start is None:
            bucket_start = start
            o, h, l, c = b.open, b.high, b.low, b.close
            vol, bid, ask = b.volume, 0.0, 0.0
            has_flow = not b.delta_is_estimated
            if has_flow:
                bid, ask = b.bid_volume or 0.0, b.ask_volume or 0.0
            oi, count = b.open_interest, 1
            continue
        if start != bucket_start:
            emit(complete=True)
            bucket_start = start
            o, h, l, c = b.open, b.high, b.low, b.close
            vol = b.volume
            has_flow = not b.delta_is_estimated
            bid = (b.bid_volume or 0.0) if has_flow else 0.0
            ask = (b.ask_volume or 0.0) if has_flow else 0.0
            oi, count = b.open_interest, 1
        else:
            h = max(h, b.high)
            l = min(l, b.low)
            c = b.close
            vol += b.volume
            if b.delta_is_estimated:
                has_flow = False
            else:
                bid += b.bid_volume or 0.0
                ask += b.ask_volume or 0.0
            if b.open_interest is not None:
                oi = b.open_interest
            count += 1

    if bucket_start is not None:
        full = count >= expected
        if full or keep_partial:
            emit(complete=full)
    return out
