"""Candlestick patterns: normalised, and blind to the next bar.

Two properties matter more than any individual pattern definition. A form must
mean the same thing on a 0.25-tick index future and a 0.01-tick crude
contract, which requires every threshold to be a fraction of the bar's own
range. And a pattern must be judged on the bar it completes - a definition
that needs a confirmation bar is reporting yesterday's news as today's signal.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from futures_agents.data.bars import Bar
from futures_agents.data.loader import load_csv
from futures_agents.indicators.candles import (body_fraction, classify_candle,
                                               close_location_value,
                                               detect_patterns)
from futures_agents.timeutil import ET

T0 = datetime(2026, 3, 17, 10, 0, tzinfo=ET)


def _bar(o, h, l, c, i=0, vol=100.0):
    return Bar(ts=T0 + timedelta(minutes=i), open=o, high=h, low=l, close=c,
               volume=vol, minutes=1)


def _names(bars, i):
    return {p.name for p in classify_candle(bars, i)}


def test_hammer_is_recognised():
    bars = [_bar(100, 101, 99, 100, i) for i in range(10)]
    bars.append(_bar(100.0, 100.2, 96.0, 99.8, 10))   # long lower wick
    assert "hammer" in _names(bars, 10)


def test_shooting_star_is_recognised():
    bars = [_bar(100, 101, 99, 100, i) for i in range(10)]
    bars.append(_bar(100.0, 104.0, 99.9, 100.2, 10))  # long upper wick
    assert "shooting_star" in _names(bars, 10)


def test_engulfing_requires_a_sign_reversal_and_a_bigger_body():
    bars = [_bar(100, 101, 99, 100, i) for i in range(9)]
    bars.append(_bar(101.0, 101.2, 100.0, 100.2, 9))   # small down body
    bars.append(_bar(100.0, 102.0, 99.9, 101.8, 10))   # bigger up body
    assert "bullish_engulfing" in _names(bars, 10)
    # Same shapes, same direction: not engulfing.
    same = bars[:9] + [_bar(100.0, 100.5, 99.8, 100.4, 9),
                       _bar(100.4, 102.0, 100.3, 101.8, 10)]
    assert "bullish_engulfing" not in _names(same, 10)


def test_inside_bar_is_contained_by_its_predecessor():
    bars = [_bar(100, 101, 99, 100, i) for i in range(9)]
    bars.append(_bar(100.0, 103.0, 97.0, 100.0, 9))
    bars.append(_bar(100.0, 102.0, 98.0, 101.0, 10))
    assert "inside_bar" in _names(bars, 10)


def test_thresholds_are_scale_free():
    """The same shape at two price scales and two tick sizes must classify
    identically. A threshold in points would make a pattern mean one thing on
    gold and nothing on the Nasdaq."""
    small = [_bar(100, 101, 99, 100, i) for i in range(10)]
    small.append(_bar(100.0, 100.2, 96.0, 99.8, 10))
    big = [_bar(b.open * 200, b.high * 200, b.low * 200, b.close * 200, i)
           for i, b in enumerate(small)]
    assert _names(small, 10) == _names(big, 10)


def test_a_pattern_never_reads_the_next_bar():
    """Classification on a truncated series must match the full series. If it
    does not, the form is being decided by a bar that had not printed."""
    bars = load_csv("csv/raw/MNQ_1h.csv", "MNQ", 60).bars
    for cut in (500, 1500, 3000):
        full = {p.name for p in classify_candle(bars, cut - 1)}
        part = {p.name for p in classify_candle(bars[:cut], cut - 1)}
        assert full == part, f"classification at {cut-1} changed with future bars"


def test_close_location_value_spans_the_range():
    assert close_location_value(_bar(100, 102, 98, 102)) == pytest.approx(1.0)
    assert close_location_value(_bar(100, 102, 98, 98)) == pytest.approx(-1.0)
    assert close_location_value(_bar(100, 102, 98, 100)) == pytest.approx(0.0)
    assert close_location_value(_bar(100, 100, 100, 100)) is None   # no range


def test_body_fraction_separates_doji_from_marubozu():
    assert body_fraction(_bar(100, 102, 98, 100.05)) < 0.05
    assert body_fraction(_bar(100, 102, 99.9, 101.9)) > 0.85


def test_patterns_fire_at_comparable_rates_across_contracts():
    """Normalisation, measured rather than asserted: MNQ and MGC are very
    different instruments and a scale-free definition should fire on them at
    similar rates."""
    rates = {}
    for sym in ("MNQ", "MGC"):
        bars = load_csv(f"csv/raw/{sym}_1h.csv", sym, 60).bars
        pats = detect_patterns(bars)
        rates[sym] = {n: sum(1 for p in pats if p.name == n) / len(bars)
                      for n in ("hammer", "shooting_star", "inside_bar",
                                "bullish_engulfing")}
    for name in rates["MNQ"]:
        a, b = rates["MNQ"][name], rates["MGC"][name]
        assert abs(a - b) < 0.05, (
            f"{name} fires {a:.1%} on MNQ and {b:.1%} on MGC - not scale-free")
