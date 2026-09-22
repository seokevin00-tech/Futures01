"""Data provider adapters and the multi-timeframe view handed to strategies.

``DataProvider`` is the seam between this system and the outside world. The
three built-in implementations (CSV, replay, synthetic) cover research and
simulation; a live feed is added by implementing the same three methods. See
``docs/DATA_PROVIDERS.md`` for wiring a real vendor.

``MultiTimeframeView`` is the object every strategy and analyst actually reads.
It owns the look-ahead guarantee: ask it for the 15-minute series at 10:07 and
you get the bars that had *closed* by 10:07, never the developing one, unless
you explicitly ask for the developing bar by name.
"""

from __future__ import annotations

import os
from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, Iterator, List, Optional, Sequence, Tuple

from ..config import TIMEFRAMES, get_contract
from ..timeutil import ET, to_et
from .bars import Bar, BarSeries, resample
from .loader import load_csv, load_symbol, synthetic_series

__all__ = [
    "DataProvider", "CsvProvider", "SyntheticProvider", "ReplayProvider",
    "MultiTimeframeView",
]


class DataProvider(ABC):
    """Minimal interface a data source must satisfy."""

    @abstractmethod
    def base_series(self, symbol: str) -> BarSeries:
        """The finest-granularity series available for ``symbol`` (usually 1m)."""

    @abstractmethod
    def symbols(self) -> Sequence[str]:
        """Symbols this provider can serve."""

    def latest_time(self, symbol: str) -> Optional[datetime]:
        last = self.base_series(symbol).last
        return last.ts if last else None

    def view(self, symbol: str, timeframes: Sequence[int],
             as_of: Optional[datetime] = None) -> "MultiTimeframeView":
        return MultiTimeframeView(self.base_series(symbol), timeframes, as_of=as_of)


class CsvProvider(DataProvider):
    """Reads ``{data_dir}/{SYMBOL}_{minutes}m.csv``; caches parsed series."""

    def __init__(self, data_dir: str = "data", minutes: int = 1,
                 symbols: Optional[Sequence[str]] = None):
        self.data_dir = data_dir
        self.minutes = minutes
        self._explicit = list(symbols) if symbols else None
        self._cache: Dict[str, BarSeries] = {}

    def symbols(self) -> Sequence[str]:
        if self._explicit:
            return self._explicit
        found = []
        if os.path.isdir(self.data_dir):
            for f in sorted(os.listdir(self.data_dir)):
                if f.lower().endswith(".csv"):
                    found.append(f.split("_")[0].split(".")[0].upper())
        return sorted(set(found))

    def base_series(self, symbol: str) -> BarSeries:
        key = symbol.upper()
        if key not in self._cache:
            self._cache[key] = load_symbol(key, data_dir=self.data_dir,
                                           minutes=self.minutes,
                                           allow_synthetic=False)
        return self._cache[key]


class SyntheticProvider(DataProvider):
    """Deterministic generated data - lets the whole system run with no feed."""

    def __init__(self, symbols: Sequence[str], days: int = 120, seed: int = 7,
                 minutes: int = 1):
        self._symbols = [s.upper() for s in symbols]
        self.days = days
        self.seed = seed
        self.minutes = minutes
        self._cache: Dict[str, BarSeries] = {}

    def symbols(self) -> Sequence[str]:
        return list(self._symbols)

    def base_series(self, symbol: str) -> BarSeries:
        key = symbol.upper()
        if key not in self._cache:
            # Distinct seed per symbol so symbols are not copies of each other -
            # correlated-exposure logic must be tested against independent paths.
            offset = sum(ord(c) for c in key)
            self._cache[key] = synthetic_series(
                key, days=self.days, minutes=self.minutes, seed=self.seed + offset)
        return self._cache[key]


class ReplayProvider(DataProvider):
    """Wraps another provider and exposes history only up to a moving cursor.

    This is how live operation is simulated honestly: the orchestrator advances
    the cursor bar by bar, and every agent sees exactly what it would have seen
    at that instant. Any component that can "see the future" through a replay
    provider has a bug, because the provider physically cannot serve it.
    """

    def __init__(self, inner: DataProvider, start: Optional[datetime] = None):
        self.inner = inner
        self._cursor: Optional[datetime] = to_et(start) if start else None
        self._truncated: Dict[Tuple[str, Optional[datetime]], BarSeries] = {}

    @property
    def cursor(self) -> Optional[datetime]:
        return self._cursor

    def symbols(self) -> Sequence[str]:
        return self.inner.symbols()

    def seek(self, ts: datetime) -> None:
        self._cursor = to_et(ts)

    def advance(self, minutes: int = 1) -> Optional[datetime]:
        if self._cursor is None:
            full = self.inner.base_series(self.symbols()[0])
            self._cursor = full[0].ts if len(full) else None
        else:
            self._cursor = self._cursor + timedelta(minutes=minutes)
        return self._cursor

    def base_series(self, symbol: str) -> BarSeries:
        full = self.inner.base_series(symbol)
        if self._cursor is None:
            return full
        key = (symbol.upper(), self._cursor)
        if key not in self._truncated:
            self._truncated.clear()      # only the current cursor is ever needed
            self._truncated[key] = full.before(self._cursor, inclusive=True)
        return self._truncated[key]

    def iter_bars(self, symbol: str, start: Optional[datetime] = None) -> Iterator[Bar]:
        """Walk the underlying series, advancing the cursor to each bar."""
        for bar in self.inner.base_series(symbol):
            if start and bar.ts < to_et(start):
                continue
            self._cursor = bar.ts
            yield bar


@dataclass
class _TFEntry:
    completed: BarSeries
    developing: Optional[Bar]


class MultiTimeframeView:
    """Aligned, look-ahead-safe multi-timeframe access to one symbol.

    Construction resamples the base series once per requested timeframe; from
    then on ``.series(tf)`` is a cheap lookup. ``as_of`` pins the view to an
    instant, which is what the backtester and the replay orchestrator use to
    guarantee that no component reads a bar that had not yet closed.
    """

    def __init__(self, base: BarSeries, timeframes: Sequence[int],
                 as_of: Optional[datetime] = None):
        self.symbol = base.symbol
        self.as_of = to_et(as_of) if as_of else (base.last.end_ts if base.last else None)
        self.timeframes = tuple(sorted({int(t) for t in timeframes}))
        for tf in self.timeframes:
            if tf not in TIMEFRAMES:
                raise ValueError(f"Unsupported timeframe {tf}m; allowed {TIMEFRAMES}")
            if tf < base.minutes:
                raise ValueError(
                    f"Requested {tf}m but the base series is {base.minutes}m - "
                    "cannot synthesise data finer than the source")

        src = base.before(self.as_of, inclusive=True) if as_of else base
        self._base = src
        self._tf: Dict[int, _TFEntry] = {}
        for tf in self.timeframes:
            full = resample(src, tf, keep_partial=True) if tf != src.minutes else src
            last = full.last
            if last is not None and not last.complete:
                self._tf[tf] = _TFEntry(full.completed, last)
            else:
                self._tf[tf] = _TFEntry(full, None)

    # ---- access -----------------------------------------------------
    @property
    def base(self) -> BarSeries:
        """The raw base-resolution series up to ``as_of``."""
        return self._base

    def series(self, timeframe: int) -> BarSeries:
        """Completed bars only - safe for any signal computation."""
        entry = self._tf.get(int(timeframe))
        if entry is None:
            raise KeyError(
                f"{self.symbol}: timeframe {timeframe}m was not requested for this view "
                f"(have {self.timeframes})")
        return entry.completed

    def developing(self, timeframe: int) -> Optional[Bar]:
        """The in-progress bar, explicitly requested. Never used for signals
        that claim to be confirmed."""
        entry = self._tf.get(int(timeframe))
        return entry.developing if entry else None

    def last_price(self) -> Optional[float]:
        """Most recent traded price available at ``as_of``."""
        b = self._base.last
        return b.close if b else None

    def last_bar(self, timeframe: Optional[int] = None) -> Optional[Bar]:
        if timeframe is None:
            return self._base.last
        return self.series(timeframe).last

    def has_history(self, timeframe: int, bars: int) -> bool:
        return len(self.series(timeframe)) >= bars

    def __repr__(self) -> str:
        tfs = ",".join(f"{t}m" for t in self.timeframes)
        return f"<MultiTimeframeView {self.symbol} [{tfs}] as_of={self.as_of}>"
