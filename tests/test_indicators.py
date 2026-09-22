"""Indicator correctness - above all, the absence of look-ahead.

The contract declared in ``futures_agents/indicators/core.py`` is:

1. output length == input length,
2. ``None`` in warm-up positions,
3. index *i* uses only inputs at indices <= *i*.

Rule 3 is the one that decides whether the whole research pipeline is worth
anything, so it is tested by construction rather than by inspection: compute an
indicator over the first *k* bars, compute it again over the first *k+1*, and
assert the first *k* values are byte-for-byte identical. An indicator that
peeks at a future bar cannot survive that, and the test sweeps *k* across the
entire series so the warm-up boundary is covered too, not just the steady state.
"""

from __future__ import annotations

import math
from typing import Callable, List, Optional, Sequence, Tuple

import pytest

from futures_agents.data.bars import Bar
from futures_agents.features import TimeframeFrame
from futures_agents.config import get_contract
from futures_agents.data.bars import BarSeries
from futures_agents.indicators.core import (adx, atr, bollinger, ema,
                                            linreg_slope, macd, percent_rank,
                                            rma, rolling_max, rolling_min, roc,
                                            rsi, sma, stdev, stochastic,
                                            true_range, wma, zscore)
from futures_agents.indicators.structure import find_swings

from conftest import SESSION_OPEN, make_bars

Cols = Tuple[List[Optional[float]], ...]

# A short series is enough: the invariant is structural. 70 bars covers the
# warm-up of every period used below with room to spare.
_BARS = make_bars(70, seed=5)
HIGHS = [b.high for b in _BARS]
LOWS = [b.low for b in _BARS]
CLOSES = [b.close for b in _BARS]


def _cols(result) -> Cols:
    """Normalise a single-column or multi-column indicator result."""
    return tuple(result) if isinstance(result, tuple) else (list(result),)


# Each entry is (name, callable(k) -> columns computed over the first k bars).
INDICATORS: Tuple[Tuple[str, Callable[[int], Cols]], ...] = (
    ("sma", lambda k: _cols(sma(CLOSES[:k], 5))),
    ("ema", lambda k: _cols(ema(CLOSES[:k], 10))),
    ("rsi", lambda k: _cols(rsi(CLOSES[:k], 14))),
    ("stdev", lambda k: _cols(stdev(CLOSES[:k], 10))),
    ("percent_rank", lambda k: _cols(percent_rank(CLOSES[:k], 20))),
    ("macd", lambda k: _cols(macd(CLOSES[:k], 5, 13, 4))),
    ("atr", lambda k: _cols(atr(HIGHS[:k], LOWS[:k], CLOSES[:k], 14))),
    ("adx", lambda k: _cols(adx(HIGHS[:k], LOWS[:k], CLOSES[:k], 7))),
    # Not named in the brief, but the same rule binds them.
    ("bollinger", lambda k: _cols(bollinger(CLOSES[:k], 20, 2.0))),
    ("stochastic", lambda k: _cols(stochastic(HIGHS[:k], LOWS[:k], CLOSES[:k],
                                              14, 3, 3))),
    ("zscore", lambda k: _cols(zscore(CLOSES[:k], 10))),
    ("rolling_max", lambda k: _cols(rolling_max(HIGHS[:k], 10))),
    ("linreg_slope", lambda k: _cols(linreg_slope(CLOSES[:k], 10))),
    ("true_range", lambda k: _cols(true_range(HIGHS[:k], LOWS[:k], CLOSES[:k]))),
    ("wma", lambda k: _cols(wma(CLOSES[:k], 8))),
    ("rma", lambda k: _cols(rma(CLOSES[:k], 8))),
    ("roc", lambda k: _cols(roc(CLOSES[:k], 5))),
)

_ADX_DEFECT = (
    "adx() suppresses +DI/-DI (and ADX at its first valid bar) until the series "
    "holds 2*period+1 bars, although they are computable from period+1 bars. "
    "Appending a bar therefore turns historical Nones into numbers. "
    "See the comment on test_adx_published_values_never_change."
)


def _lookahead_violations(fn: Callable[[int], Cols], n: int,
                          start: int = 1) -> List[str]:
    """Every position where appending one bar changed an earlier value."""
    bad: List[str] = []
    for k in range(start, n):
        short, long_ = fn(k), fn(k + 1)
        for ci, (sc, lc) in enumerate(zip(short, long_)):
            for i in range(k):
                a, b = sc[i], lc[i]
                if a is None and b is None:
                    continue
                if a is None or b is None:
                    bad.append(f"k={k} col={ci} i={i}: {a!r} -> {b!r}")
                elif abs(a - b) > 1e-12:
                    bad.append(f"k={k} col={ci} i={i}: {a!r} -> {b!r}")
    return bad


# --------------------------------------------------------------------------
# The headline invariant
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name,fn", [
    pytest.param(
        n, f,
        marks=pytest.mark.xfail(strict=False, reason=_ADX_DEFECT) if n == "adx"
        else (),
        id=n)
    for n, f in INDICATORS
])
def test_appending_a_future_bar_never_changes_history(name, fn):
    """THE test. Appending bar *k+1* must leave values 0..k-1 untouched.

    Swept across every *k*, so the warm-up boundary is covered as well as the
    steady state - that is where off-by-one warm-up guards hide.
    """
    violations = _lookahead_violations(fn, len(CLOSES))
    assert not violations, (
        f"{name}: appending a bar changed {len(violations)} historical "
        f"value(s); first: {violations[:5]}")


@pytest.mark.parametrize("name,fn", [
    pytest.param(
        n, f,
        marks=pytest.mark.xfail(strict=False, reason=_ADX_DEFECT) if n == "adx"
        else (),
        id=n)
    for n, f in INDICATORS
])
def test_every_prefix_agrees_with_the_full_series(name, fn):
    """Stronger restatement: the value at bar *i* must not depend on how much
    data came after it, at any distance."""
    n = len(CLOSES)
    full = fn(n)
    for k in range(1, n):
        short = fn(k)
        for ci, (sc, fc) in enumerate(zip(short, full)):
            assert sc == fc[:k], (
                f"{name}: prefix of {k} bars disagrees with the full series "
                f"in column {ci}")


def test_adx_published_values_never_change():
    """ADX's defect is asymmetric, and the asymmetry matters.

    ``adx()`` never lets a *future* bar alter a value it has already published -
    the dangerous direction. What it does do is withhold values it could have
    computed (guard at ``indicators/core.py:295`` requires ``2*period+1`` bars
    where ``period+1`` suffices for +DI/-DI), so a historical ``None`` can turn
    into a number later. This test pins the half that is safe, so a future fix
    to the warm-up guard cannot silently introduce real look-ahead.
    """
    n = len(CLOSES)
    full = adx(HIGHS, LOWS, CLOSES, 7)
    for k in range(1, n):
        short = adx(HIGHS[:k], LOWS[:k], CLOSES[:k], 7)
        for sc, fc in zip(short, full):
            for i in range(k):
                if sc[i] is not None:
                    assert fc[i] is not None and abs(sc[i] - fc[i]) < 1e-12, (
                        f"adx published {sc[i]!r} at bar {i} with {k} bars of "
                        f"data but {fc[i]!r} with {n}")


def test_indicators_do_not_mutate_their_input():
    """A look-ahead bug can also arrive as an in-place edit of the price list."""
    closes = list(CLOSES)
    highs, lows = list(HIGHS), list(LOWS)
    for _, fn in INDICATORS:
        fn(len(CLOSES))
    assert closes == CLOSES and highs == HIGHS and lows == LOWS


# --------------------------------------------------------------------------
# Shape: length and warm-up
# --------------------------------------------------------------------------

@pytest.mark.parametrize("name,fn", [(n, f) for n, f in INDICATORS], ids=[n for n, _ in INDICATORS])
@pytest.mark.parametrize("n", [0, 1, 5, 40, 70])
def test_output_length_always_equals_input_length(name, fn, n):
    """Index *i* of any output corresponds to bar *i* of the input. That is the
    whole reason alignment errors are impossible in this codebase."""
    for col in fn(n):
        assert len(col) == n, f"{name}: {len(col)} values for {n} bars"


@pytest.mark.parametrize("fn,period,first_valid", [
    (lambda v, p: sma(v, p), 5, 4),
    (lambda v, p: ema(v, p), 10, 9),
    (lambda v, p: wma(v, p), 8, 7),
    (lambda v, p: rma(v, p), 8, 7),
    (lambda v, p: stdev(v, p), 10, 9),
    (lambda v, p: percent_rank(v, p), 20, 19),
    (lambda v, p: rolling_max(v, p), 10, 9),
    (lambda v, p: rolling_min(v, p), 10, 9),
    (lambda v, p: linreg_slope(v, p), 10, 9),
    (lambda v, p: roc(v, p), 5, 5),
    (lambda v, p: rsi(v, p), 14, 14),
])
def test_warm_up_positions_are_none(fn, period, first_valid):
    out = fn(CLOSES, period)
    assert all(v is None for v in out[:first_valid]), (
        f"a value appeared before index {first_valid}")
    assert out[first_valid] is not None, (
        f"no value at the first computable index {first_valid}")


def test_true_range_first_bar_is_none():
    """There is no previous close for bar 0, so there is no true range."""
    tr = true_range(HIGHS, LOWS, CLOSES)
    assert tr[0] is None and tr[1] is not None


def test_atr_is_offset_by_one_for_the_missing_true_range():
    a = atr(HIGHS, LOWS, CLOSES, 14)
    assert a[13] is None          # 14 true ranges need 15 bars
    assert a[14] is not None


def test_empty_and_short_inputs_do_not_raise():
    assert sma([], 5) == []
    assert ema([1.0, 2.0], 10) == [None, None]
    assert rsi([1.0, 2.0], 14) == [None, None]
    assert atr([1.0], [1.0], [1.0], 14) == [None]
    assert all(c == [None] * 3 for c in adx([3.0] * 3, [1.0] * 3, [2.0] * 3, 14))


@pytest.mark.parametrize("fn", [sma, ema, wma, rma, stdev, percent_rank,
                                rolling_max, rolling_min])
def test_non_positive_period_is_rejected(fn):
    """A zero or negative period is a programming error, not a warm-up state."""
    with pytest.raises(ValueError):
        fn(CLOSES, 0)


def test_macd_rejects_a_fast_period_that_is_not_faster():
    with pytest.raises(ValueError):
        macd(CLOSES, 26, 12, 9)


# --------------------------------------------------------------------------
# Known values
# --------------------------------------------------------------------------

def test_sma_of_a_linear_ramp():
    """For values 1..n, the mean of the window ending at *i* is
    ``i + 1 - (period - 1) / 2`` exactly."""
    ramp = [float(i + 1) for i in range(20)]
    period = 5
    out = sma(ramp, period)
    assert out[:period - 1] == [None] * (period - 1)
    for i in range(period - 1, len(ramp)):
        assert out[i] == pytest.approx(i + 1 - (period - 1) / 2)
    assert out[4] == pytest.approx(3.0)      # mean(1,2,3,4,5)
    assert out[-1] == pytest.approx(18.0)    # mean(16..20)


def test_sma_of_a_constant_series_is_the_constant():
    out = sma([7.5] * 12, 4)
    assert all(v == pytest.approx(7.5) for v in out[3:])


def test_ema_of_a_constant_series_is_the_constant():
    """The SMA seed means there is no initialisation transient to decay."""
    out = ema([42.0] * 30, 10)
    assert out[9] == pytest.approx(42.0)
    assert all(v == pytest.approx(42.0) for v in out[9:])


def test_ema_first_value_is_the_seed_sma():
    values = [float(i) for i in range(1, 21)]
    assert ema(values, 5)[4] == pytest.approx(sma(values, 5)[4])


def test_ema_recurrence_is_the_textbook_one():
    values = [float(i) for i in range(1, 21)]
    out = ema(values, 5)
    k = 2.0 / 6.0
    assert out[5] == pytest.approx(values[5] * k + out[4] * (1 - k))


def test_rsi_is_100_on_a_monotonic_rise():
    """With no down closes there is no average loss, so RSI pins at 100."""
    rising = [float(100 + i) for i in range(40)]
    out = rsi(rising, 14)
    assert out[:14] == [None] * 14
    assert all(v == pytest.approx(100.0) for v in out[14:])


def test_rsi_is_0_on_a_monotonic_fall():
    falling = [float(200 - i) for i in range(40)]
    out = rsi(falling, 14)
    assert all(v == pytest.approx(0.0) for v in out[14:])


def test_rsi_of_a_flat_series_is_100_by_convention():
    """No losses at all, so the zero-division branch is taken. Documented here
    because a flat series is a real input (an illiquid overnight session)."""
    assert rsi([50.0] * 30, 14)[14] == pytest.approx(100.0)


def test_rsi_stays_within_bounds():
    assert all(0.0 <= v <= 100.0 for v in rsi(CLOSES, 14) if v is not None)


def test_stdev_of_a_constant_series_is_zero():
    assert all(v == pytest.approx(0.0) for v in stdev([3.0] * 20, 5)[4:])


def test_stdev_population_vs_sample():
    values = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
    assert stdev(values, 8)[-1] == pytest.approx(2.0)                 # population
    assert stdev(values, 8, sample=True)[-1] == pytest.approx(2.13809, abs=1e-5)


def test_percent_rank_of_a_rising_series_is_the_maximum():
    """Every earlier value in the window is below the current one, so the rank
    is ``(period - 1) / period`` - not 1.0, because the comparison is strict."""
    rising = [float(i) for i in range(30)]
    out = percent_rank(rising, 10)
    assert all(v == pytest.approx(0.9) for v in out[9:])


def test_percent_rank_of_a_falling_series_is_zero():
    assert all(v == pytest.approx(0.0)
               for v in percent_rank([float(-i) for i in range(30)], 10)[9:])


def test_percent_rank_is_bounded():
    assert all(0.0 <= v <= 1.0 for v in percent_rank(CLOSES, 20) if v is not None)


def test_macd_line_is_the_difference_of_the_two_emas():
    fast, slow, signal = 5, 13, 4
    line, sig, hist = macd(CLOSES, fast, slow, signal)
    ef, es = ema(CLOSES, fast), ema(CLOSES, slow)
    for i in range(len(CLOSES)):
        if ef[i] is None or es[i] is None:
            assert line[i] is None
        else:
            assert line[i] == pytest.approx(ef[i] - es[i])
    for i in range(len(CLOSES)):
        if line[i] is None or sig[i] is None:
            assert hist[i] is None
        else:
            assert hist[i] == pytest.approx(line[i] - sig[i])


def test_macd_of_a_constant_series_is_zero():
    line, sig, hist = macd([100.0] * 60, 5, 13, 4)
    assert line[-1] == pytest.approx(0.0)
    assert sig[-1] == pytest.approx(0.0)
    assert hist[-1] == pytest.approx(0.0)


def test_true_range_uses_the_previous_close():
    highs = [10.0, 12.0]
    lows = [9.0, 11.5]
    closes = [9.5, 11.8]
    # max(12 - 11.5, |12 - 9.5|, |11.5 - 9.5|) = 2.5, the gap-aware reading.
    assert true_range(highs, lows, closes)[1] == pytest.approx(2.5)


def test_atr_of_constant_range_bars_is_that_range():
    n = 40
    highs = [101.0] * n
    lows = [99.0] * n
    closes = [100.0] * n
    out = atr(highs, lows, closes, 14)
    assert out[-1] == pytest.approx(2.0)


def test_adx_of_a_clean_uptrend_has_plus_di_above_minus_di():
    n = 60
    closes = [100.0 + i for i in range(n)]
    highs = [c + 0.5 for c in closes]
    lows = [c - 0.5 for c in closes]
    a, pdi, mdi = adx(highs, lows, closes, 14)
    assert pdi[-1] is not None and mdi[-1] is not None
    assert pdi[-1] > mdi[-1]
    assert a[-1] == pytest.approx(100.0)     # perfectly directional


def test_bollinger_bands_straddle_the_middle_band():
    up, mid, lo = bollinger(CLOSES, 20, 2.0)
    for u, m, l in zip(up, mid, lo):
        if m is None:
            assert u is None and l is None
        else:
            assert l <= m <= u


def test_rolling_max_and_min_bound_the_series():
    hi = rolling_max(CLOSES, 10)
    lo = rolling_min(CLOSES, 10)
    for i in range(9, len(CLOSES)):
        window = CLOSES[i - 9: i + 1]
        assert hi[i] == pytest.approx(max(window))
        assert lo[i] == pytest.approx(min(window))


def test_stochastic_is_bounded():
    k, d = stochastic(HIGHS, LOWS, CLOSES, 14, 3, 3)
    assert all(0.0 <= v <= 100.0 for v in k if v is not None)
    assert all(0.0 <= v <= 100.0 for v in d if v is not None)


def test_linreg_slope_of_a_ramp_is_the_step():
    ramp = [float(3 * i) for i in range(20)]
    assert linreg_slope(ramp, 10)[-1] == pytest.approx(3.0)


# --------------------------------------------------------------------------
# Swings: the confirmation lag
# --------------------------------------------------------------------------

def _pivot_bars() -> List[Bar]:
    """Eleven bars with an unambiguous swing high at index 5.

    Highs: 1 2 3 4 5 9 5 4 3 2 1 - bar 5 dominates two bars on each side, so a
    (left=2, right=2) fractal confirms it at index 7.
    """
    highs = [101, 102, 103, 104, 105, 109, 105, 104, 103, 102, 101]
    rows = [(float(h - 2), float(h), float(h - 3), float(h - 1)) for h in highs]
    return [Bar(ts=SESSION_OPEN.replace(minute=30) , open=o, high=h, low=l,
                close=c, volume=100.0, minutes=1)
            for (o, h, l, c) in rows]


def _pivot_series() -> BarSeries:
    from datetime import timedelta
    bars = _pivot_bars()
    return BarSeries("MNQ", 1, [
        Bar(ts=SESSION_OPEN + timedelta(minutes=i), open=b.open, high=b.high,
            low=b.low, close=b.close, volume=b.volume, minutes=1)
        for i, b in enumerate(bars)])


def test_swing_confirmation_index_is_i_plus_right():
    bars = list(_pivot_series())
    swings = find_swings(bars, left=2, right=2)
    highs = [s for s in swings if s.is_high]
    assert highs, "the constructed pivot high was not detected at all"
    top = max(highs, key=lambda s: s.price)
    assert top.index == 5
    assert top.confirmed_index == 7 == top.index + 2


def test_a_swing_is_not_visible_before_its_confirmation_bar():
    """The invariant: a fractal needs its right-hand bars before it exists.
    Treating it as visible at bar *i* is the classic unreproducible backtest."""
    bars = list(_pivot_series())
    for k in range(len(bars)):
        visible = [s for s in find_swings(bars[:k + 1], left=2, right=2)
                   if s.index == 5 and s.is_high]
        if k < 7:
            assert not visible, f"swing at index 5 leaked at bar {k}"
        else:
            assert visible, f"swing at index 5 still missing at bar {k}"
            assert visible[0].confirmed_index == 7


@pytest.mark.parametrize("right", [1, 2, 3, 4])
def test_confirmation_lag_scales_with_the_right_window(right):
    bars = list(_pivot_series())
    for s in find_swings(bars, left=2, right=right):
        assert s.confirmed_index == s.index + right
        assert not s.visible_at(s.confirmed_index - 1)
        assert s.visible_at(s.confirmed_index)


def test_frame_exposes_only_confirmed_swings():
    """``TimeframeFrame`` is what strategies actually read, so the lag has to
    survive the precompute-once-slice-per-bar optimisation."""
    frame = TimeframeFrame(_pivot_series(), get_contract("MNQ"),
                           swing_left=2, swing_right=2)
    for i in range(len(_pivot_series())):
        for s in frame.visible_swings(i):
            assert s.confirmed_index <= i, (
                f"bar {i} could see a swing confirmed at {s.confirmed_index}")
    idx_at_6 = {s.index for s in frame.visible_swings(6)}
    idx_at_7 = {s.index for s in frame.visible_swings(7)}
    assert 5 not in idx_at_6
    assert 5 in idx_at_7


def test_find_swings_needs_a_full_window():
    bars = list(_pivot_series())
    assert find_swings(bars[:4], left=2, right=2) == []


# --------------------------------------------------------------------------
# Fair value gaps carry the same one-bar lag
# --------------------------------------------------------------------------

def _fvg_series() -> BarSeries:
    """A bullish three-bar gap: bar 2's low (105) is above bar 0's high (100),
    so the gap's middle bar is index 1 and it is knowable only at index 2."""
    from datetime import timedelta
    rows = [
        (98.0, 100.0, 97.0, 99.0),      # 0
        (99.0, 106.0, 98.5, 105.5),     # 1  displacement
        (105.5, 108.0, 105.0, 107.0),   # 2  low 105 > bar 0 high 100
        (107.0, 109.0, 106.0, 108.0),   # 3
        (108.0, 110.0, 107.0, 109.0),   # 4
    ]
    return BarSeries("MNQ", 1, [
        Bar(ts=SESSION_OPEN + timedelta(minutes=i), open=o, high=h, low=l,
            close=c, volume=100.0, minutes=1)
        for i, (o, h, l, c) in enumerate(rows)])


def test_fair_value_gap_is_indexed_on_its_middle_bar():
    from futures_agents.indicators.structure import fair_value_gaps
    gaps = fair_value_gaps(list(_fvg_series()), track_fills=True)
    assert len(gaps) == 1
    gap = gaps[0]
    assert gap.direction == "BULLISH"
    assert gap.index == 1
    assert gap.bottom == pytest.approx(100.0) and gap.top == pytest.approx(105.0)


def test_a_fair_value_gap_is_not_visible_before_its_third_bar():
    """A three-bar pattern needs its third bar. The frame's visibility pointer
    must therefore release it at ``index + 1``, not at ``index``."""
    frame = TimeframeFrame(_fvg_series(), get_contract("MNQ"),
                           swing_left=1, swing_right=1)
    assert [g.index for g in frame.active_fvgs(0)] == []
    assert [g.index for g in frame.active_fvgs(1)] == [], (
        "the gap leaked on its own middle bar")
    assert [g.index for g in frame.active_fvgs(2)] == [1]
    for i in range(len(_fvg_series())):
        for g in frame.active_fvgs(i):
            assert g.index + 1 <= i
