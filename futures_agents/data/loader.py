"""Bar loading, saving and a realistic synthetic generator.

The synthetic generator exists so the entire system - research, backtests, risk,
agents, callouts - is runnable end to end with no data vendor and no network.
It is deliberately built as a near-martingale: regimes persist, volatility has
the real intraday smile, and volume/delta are realistic, but there is no
mechanical edge baked in. A strategy that "works" on this data because of a
generator artefact would be worse than useless, so the drift term is tiny
relative to the noise term and delta carries substantial independent noise.
"""

from __future__ import annotations

import csv
import math
import os
import random
from datetime import date, datetime, timedelta
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from ..config import ContractSpec, get_contract
from ..timeutil import ET, is_market_holiday, to_et, trading_day
from .bars import Bar, BarSeries

__all__ = ["load_csv", "save_csv", "synthetic_series", "load_symbol", "CSV_FIELDS"]

CSV_FIELDS = ("timestamp", "open", "high", "low", "close", "volume",
              "bid_volume", "ask_volume", "open_interest")

#: Column aliases accepted by the loader, so exports from common platforms
#: (Sierra Chart, NinjaTrader, TradingView, Databento, IQFeed) load unmodified.
_ALIASES: Dict[str, Tuple[str, ...]] = {
    "timestamp": ("timestamp", "time", "datetime", "date_time", "ts", "date",
                  "bar_time", "opentime", "open_time", "ts_event"),
    "open": ("open", "o", "openprice", "open_price", "first"),
    "high": ("high", "h", "highprice", "high_price", "max"),
    "low": ("low", "l", "lowprice", "low_price", "min"),
    "close": ("close", "c", "closeprice", "close_price", "last", "settle"),
    "volume": ("volume", "v", "vol", "totalvolume", "total_volume", "size"),
    "bid_volume": ("bid_volume", "bidvolume", "bidvol", "sell_volume", "askvolume_sell",
                   "bid_size", "downvolume", "down_volume"),
    "ask_volume": ("ask_volume", "askvolume", "askvol", "buy_volume", "bidvolume_buy",
                   "ask_size", "upvolume", "up_volume"),
    "open_interest": ("open_interest", "openinterest", "oi"),
}

_TS_FORMATS = (
    "%Y-%m-%d %H:%M:%S%z", "%Y-%m-%dT%H:%M:%S%z", "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M",
    "%m/%d/%Y %H:%M:%S", "%m/%d/%Y %H:%M", "%Y%m%d %H%M%S", "%Y-%m-%d",
)


def _parse_ts(value: str) -> datetime:
    v = value.strip()
    if not v:
        raise ValueError("empty timestamp")
    if v.isdigit():                       # epoch seconds / millis / nanos
        n = int(v)
        if n > 10**17:
            n //= 10**9
        elif n > 10**12:
            n //= 1000
        return datetime.fromtimestamp(n, tz=ET)
    try:
        return to_et(datetime.fromisoformat(v))
    except ValueError:
        pass
    for fmt in _TS_FORMATS:
        try:
            return to_et(datetime.strptime(v, fmt))
        except ValueError:
            continue
    raise ValueError(f"Unrecognised timestamp format: {value!r}")


def _resolve_columns(header: Sequence[str]) -> Dict[str, int]:
    norm = {h.strip().lower().replace(" ", "_").lstrip("﻿"): i
            for i, h in enumerate(header)}
    out: Dict[str, int] = {}
    for field, aliases in _ALIASES.items():
        for a in aliases:
            if a in norm:
                out[field] = norm[a]
                break
    missing = [f for f in ("timestamp", "open", "high", "low", "close") if f not in out]
    if missing:
        raise ValueError(
            f"CSV is missing required column(s) {missing}. Header was: {list(header)}")
    return out


def load_csv(path: str, symbol: str, minutes: int = 1, *,
             max_bars: Optional[int] = None) -> BarSeries:
    """Load bars from a CSV file.

    Column names are matched case-insensitively against a table of aliases used
    by common charting and data platforms. Timestamps without an offset are
    read as Eastern Time, matching the convention of every US futures export.
    """
    series = BarSeries(symbol, minutes)
    with open(path, "r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.reader(fh)
        try:
            header = next(reader)
        except StopIteration:
            return series
        cols = _resolve_columns(header)

        def opt(row: Sequence[str], key: str) -> Optional[float]:
            i = cols.get(key)
            if i is None or i >= len(row):
                return None
            raw = row[i].strip()
            if not raw:
                return None
            try:
                return float(raw)
            except ValueError:
                return None

        rows = 0
        for row in reader:
            if not row or all(not c.strip() for c in row):
                continue
            try:
                bar = Bar(
                    ts=_parse_ts(row[cols["timestamp"]]),
                    open=float(row[cols["open"]]),
                    high=float(row[cols["high"]]),
                    low=float(row[cols["low"]]),
                    close=float(row[cols["close"]]),
                    volume=opt(row, "volume") or 0.0,
                    minutes=minutes,
                    bid_volume=opt(row, "bid_volume"),
                    ask_volume=opt(row, "ask_volume"),
                    open_interest=opt(row, "open_interest"),
                )
            except (ValueError, IndexError) as exc:
                raise ValueError(f"{path}: malformed row {rows + 2}: {exc}") from exc
            series.append(bar)
            rows += 1
            if max_bars and rows >= max_bars:
                break
    return series


def save_csv(series: BarSeries, path: str) -> str:
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(CSV_FIELDS)
        for b in series:
            w.writerow([
                to_et(b.ts).isoformat(), b.open, b.high, b.low, b.close, b.volume,
                "" if b.bid_volume is None else b.bid_volume,
                "" if b.ask_volume is None else b.ask_volume,
                "" if b.open_interest is None else b.open_interest,
            ])
    return path


# --------------------------------------------------------------------------
# Synthetic market generator
# --------------------------------------------------------------------------

#: Intraday volatility multipliers by ET hour. The shape is the well documented
#: U: violent open, dead lunch, active close, quiet overnight.
_VOL_BY_HOUR = {
    0: 0.45, 1: 0.40, 2: 0.45, 3: 0.70, 4: 0.75, 5: 0.75, 6: 0.80, 7: 0.90,
    8: 1.15, 9: 1.85, 10: 1.50, 11: 1.05, 12: 0.72, 13: 0.85, 14: 1.10,
    15: 1.45, 16: 0.55, 17: 0.30, 18: 0.55, 19: 0.55, 20: 0.50, 21: 0.48,
    22: 0.45, 23: 0.45,
}
_VOLUME_BY_HOUR = {
    0: 0.20, 1: 0.16, 2: 0.18, 3: 0.35, 4: 0.40, 5: 0.42, 6: 0.48, 7: 0.60,
    8: 0.95, 9: 2.40, 10: 1.70, 11: 1.05, 12: 0.62, 13: 0.78, 14: 1.10,
    15: 1.95, 16: 0.45, 17: 0.10, 18: 0.30, 19: 0.28, 20: 0.24, 21: 0.22,
    22: 0.20, 23: 0.20,
}

#: Regime transition matrix (per trading day). Regimes persist, which is what
#: makes regime-conditional statistics meaningful.
_REGIME_TRANSITIONS = {
    "trend_up":   {"trend_up": 0.62, "range": 0.26, "trend_down": 0.07, "volatile": 0.05},
    "trend_down": {"trend_down": 0.60, "range": 0.27, "trend_up": 0.08, "volatile": 0.05},
    "range":      {"range": 0.60, "trend_up": 0.17, "trend_down": 0.16, "volatile": 0.07},
    "volatile":   {"volatile": 0.40, "range": 0.30, "trend_up": 0.15, "trend_down": 0.15},
}
_REGIME_PARAMS = {
    #            drift/bar (in vol units)  vol multiplier
    "trend_up":   (0.020, 1.00),
    "trend_down": (-0.020, 1.05),
    "range":      (0.0, 0.85),
    "volatile":   (0.0, 1.85),
}


def _next_regime(current: str, rng: random.Random) -> str:
    r = rng.random()
    acc = 0.0
    for nxt, p in _REGIME_TRANSITIONS[current].items():
        acc += p
        if r <= acc:
            return nxt
    return current


def synthetic_series(
    symbol: str,
    days: int = 120,
    *,
    minutes: int = 1,
    seed: Optional[int] = 7,
    start_price: Optional[float] = None,
    end_date: Optional[date] = None,
    include_overnight: bool = True,
    news_shock_prob: float = 0.06,
) -> BarSeries:
    """Generate a plausible 1-minute (or coarser) series for ``symbol``.

    Properties the generator reproduces: regime persistence across days, the
    intraday volatility and volume smile, fat-tailed returns, occasional news
    gaps, overnight sessions with thin volume, and volume delta that leads
    price only weakly and noisily.

    Properties it deliberately does NOT contain: any repeating pattern a
    strategy could exploit mechanically. Backtest results on synthetic data
    should hover around break-even before costs and clearly negative after
    them - if they do not, the backtester has a look-ahead bug.
    """
    spec = get_contract(symbol)
    rng = random.Random(seed)
    px = float(start_price if start_price is not None else _default_price(spec))
    # Per-bar volatility in points, scaled off the contract's typical daily ATR.
    daily_atr = spec.typical_atr_points or (px * 0.011)
    base_sigma = daily_atr / math.sqrt(390.0) * 0.72

    end = end_date or date.today()
    day_list: List[date] = []
    d = end
    while len(day_list) < days:
        if d.weekday() < 5 and not is_market_holiday(d):
            day_list.append(d)
        d -= timedelta(days=1)
    day_list.reverse()

    series = BarSeries(symbol, minutes)
    regime = rng.choice(list(_REGIME_PARAMS))

    for day in day_list:
        regime = _next_regime(regime, rng)
        drift_unit, vol_mult = _REGIME_PARAMS[regime]
        day_vol_shock = math.exp(rng.gauss(0.0, 0.28))    # day-to-day vol clustering

        # Session window: 18:00 previous evening -> 17:00, or RTH only.
        if include_overnight:
            cursor = datetime.combine(day - timedelta(days=1),
                                      datetime.min.time(), tzinfo=ET).replace(hour=18)
            n_minutes = 23 * 60
        else:
            cursor = datetime.combine(day, datetime.min.time(),
                                      tzinfo=ET).replace(hour=9, minute=30)
            n_minutes = 390

        # Overnight gap at the session open.
        px *= math.exp(rng.gauss(0.0, 0.0012))

        shock_minute = (rng.randrange(n_minutes)
                        if rng.random() < news_shock_prob else None)

        for m in range(0, n_minutes, minutes):
            ts = cursor + timedelta(minutes=m)
            if ts.hour == 17:                     # daily maintenance break
                continue
            hour_vol = _VOL_BY_HOUR.get(ts.hour, 0.6)
            hour_volume = _VOLUME_BY_HOUR.get(ts.hour, 0.4)
            sigma = base_sigma * hour_vol * vol_mult * day_vol_shock * math.sqrt(minutes)

            # Student-t-like tails via a random variance mixture.
            tail = 1.0 if rng.random() > 0.035 else rng.uniform(2.5, 6.0)
            step = rng.gauss(drift_unit * sigma, sigma) * tail
            if shock_minute is not None and shock_minute <= m < shock_minute + minutes:
                step += rng.choice([-1.0, 1.0]) * sigma * rng.uniform(5.0, 14.0)

            o = px
            c = px + step
            wick = abs(rng.gauss(0.0, sigma * 0.85))
            h = max(o, c) + wick * rng.uniform(0.25, 1.0)
            l = min(o, c) - wick * rng.uniform(0.25, 1.0)

            o, h, l, c = (spec.round_to_tick(x) for x in (o, h, l, c))
            h = max(h, o, c)
            l = min(l, o, c)

            base_volume = max(1.0, 900.0 * hour_volume * (1.0 + abs(step) / max(sigma, 1e-9) * 0.35))
            volume = float(int(base_volume * rng.uniform(0.6, 1.5)) + 1)

            # Delta: correlated with the bar's direction but with heavy noise,
            # so order-flow signals must actually be discovered, not assumed.
            direction_bias = 0.5 + 0.22 * math.tanh(step / max(sigma, 1e-9))
            direction_bias = min(0.88, max(0.12, direction_bias + rng.gauss(0, 0.11)))
            ask_v = round(volume * direction_bias, 2)
            bid_v = round(volume - ask_v, 2)

            series.append(Bar(ts=ts, open=o, high=h, low=l, close=c, volume=volume,
                              minutes=minutes, bid_volume=bid_v, ask_volume=ask_v))
            px = c

    return series


def _default_price(spec: ContractSpec) -> float:
    return {
        "MNQ": 21_800.0, "NQ": 21_800.0, "MES": 6_050.0, "ES": 6_050.0,
        "MYM": 44_500.0, "YM": 44_500.0, "M2K": 2_320.0, "RTY": 2_320.0,
        "MGC": 2_650.0, "GC": 2_650.0, "SIL": 31.50,
        "MCL": 71.50, "CL": 71.50, "MNG": 3.10,
        "ZN": 111.50, "M6E": 1.0850,
    }.get(spec.symbol, 1_000.0)


def load_symbol(symbol: str, *, data_dir: str = "data", minutes: int = 1,
                days: int = 120, seed: Optional[int] = 7,
                allow_synthetic: bool = True) -> BarSeries:
    """Load ``symbol`` from ``data_dir``, falling back to synthetic data.

    Looks for ``{data_dir}/{SYMBOL}_{minutes}m.csv`` then ``{data_dir}/{SYMBOL}.csv``.
    """
    for name in (f"{symbol.upper()}_{minutes}m.csv", f"{symbol.upper()}.csv"):
        path = os.path.join(data_dir, name)
        if os.path.exists(path):
            return load_csv(path, symbol, minutes)
    if not allow_synthetic:
        raise FileNotFoundError(
            f"No CSV for {symbol} in {data_dir!r} and synthetic data is disabled")
    return synthetic_series(symbol, days=days, minutes=minutes, seed=seed)
