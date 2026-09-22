"""Cross-timeframe assembly: alignment lag, completeness, resampling.

The rule that makes multi-timeframe confluence honest is simple to state and
easy to break: **a higher-timeframe bar is visible only once it has closed.**
Concretely, for base bar *i* and timeframe *tf*, the aligned *tf* bar must end
at or before bar *i* ends. If it ends later, the strategy is reading a bar that
had not formed - and every confluence statistic built on it is fiction.

These tests assert that across every base bar and several timeframes at once,
not at a spot check, because the failure mode is an off-by-one that only shows
up at bucket boundaries.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import List

import pytest

from futures_agents.config import get_contract
from futures_agents.data.bars import Bar, BarSeries, align_bucket, resample
from futures_agents.data.providers import MultiTimeframeView
from futures_agents.features import SymbolFrame, build_symbol_frame
from futures_agents.timeutil import ET

from conftest import SESSION_OPEN, make_bars, make_series

TIMEFRAMES = (1, 5, 15, 30)


# --------------------------------------------------------------------------
# Cross-timeframe alignment - the headline invariant
# --------------------------------------------------------------------------

def test_no_aligned_bar_ends_after_the_base_bar_it_is_aligned_to(frame: SymbolFrame):
    """Swept over every base bar and every timeframe in the frame."""
    base_bars = frame.base.bars
    checked = 0
    for i, base_bar in enumerate(base_bars):
        for tf in frame.timeframes:
            idx = frame.tf_index(i, tf)
            if idx < 0:
                continue
            tf_bar = frame.frames[tf].series.bars[idx]
            assert tf_bar.end_ts <= base_bar.end_ts, (
                f"base bar {i} ({base_bar.ts:%H:%M}) is aligned to a {tf}m bar "
                f"ending at {tf_bar.end_ts:%H:%M} - that bar had not closed")
            checked += 1
    assert checked > 1_000, "the sweep did not actually cover many bars"


def test_the_aligned_bar_is_the_newest_closed_one(frame: SymbolFrame):
    """Safety is not enough: alignment must also not be needlessly stale, or a
    strategy silently trades a higher timeframe one bar behind its own chart."""
    base_bars = frame.base.bars
    for i, base_bar in enumerate(base_bars):
        for tf in frame.timeframes:
            tf_bars = frame.frames[tf].series.bars
            idx = frame.tf_index(i, tf)
            if idx + 1 < len(tf_bars):
                assert tf_bars[idx + 1].end_ts > base_bar.end_ts, (
                    f"base bar {i}: the {tf}m bar at {idx + 1} had also closed")


def test_alignment_pointers_are_monotonically_non_decreasing(frame: SymbolFrame):
    """Time only moves forwards; a pointer that went backwards would mean a
    later bar seeing less history than an earlier one."""
    for tf in frame.timeframes:
        pointers = [frame.tf_index(i, tf) for i in range(len(frame.base))]
        assert pointers == sorted(pointers)
        assert all(p >= -1 for p in pointers)


def test_alignment_is_minus_one_until_the_first_higher_bar_closes():
    """A session that starts mid-bucket has no closed 30m bar for 30 minutes."""
    series = BarSeries("MNQ", 1, make_bars(90, start=SESSION_OPEN, seed=3))
    frame = SymbolFrame(series, (1, 30))
    assert frame.tf_index(0, 30) == -1
    first_closed = next(i for i in range(len(series))
                        if frame.tf_index(i, 30) >= 0)
    assert first_closed > 0
    assert series.bars[first_closed].end_ts >= SESSION_OPEN + timedelta(minutes=30)


def test_the_base_timeframe_is_aligned_to_itself(frame: SymbolFrame):
    for i in range(len(frame.base)):
        assert frame.tf_index(i, 1) == i


def test_aligned_bar_contains_no_base_bar_that_had_not_closed(frame: SymbolFrame):
    """The strong form: reconstruct the aligned higher-timeframe bar from only
    the base bars that had closed, and it must match exactly."""
    base_bars = frame.base.bars
    for tf in (5, 15):
        tf_bars = frame.frames[tf].series.bars
        for i in range(0, len(base_bars), 7):     # stride: the check is O(n*m)
            idx = frame.tf_index(i, tf)
            if idx < 0:
                continue
            tf_bar = tf_bars[idx]
            members = [b for b in base_bars
                       if tf_bar.ts <= b.ts < tf_bar.end_ts
                       and b.end_ts <= base_bars[i].end_ts]
            assert members, f"{tf}m bar at {tf_bar.ts} had no constituent bars"
            assert tf_bar.high == pytest.approx(max(b.high for b in members))
            assert tf_bar.low == pytest.approx(min(b.low for b in members))
            assert tf_bar.open == pytest.approx(members[0].open)
            assert tf_bar.close == pytest.approx(members[-1].close)


def test_snapshot_only_exposes_closed_higher_timeframe_bars(frame: SymbolFrame):
    for i in range(0, len(frame.base), 11):
        snap = frame.snapshot(i)
        assert snap is not None
        for tf, tf_snap in snap.tfs.items():
            assert tf_snap.bar.end_ts <= frame.base.bars[i].end_ts
            assert tf_snap.bar.complete is True
            assert tf_snap.index == frame.tf_index(i, tf)


def test_snapshot_price_is_the_base_bar_close(frame: SymbolFrame):
    for i in (0, 37, 199, len(frame.base) - 1):
        snap = frame.snapshot(i)
        assert snap.price == frame.base.bars[i].close
        assert snap.ts == frame.base.bars[i].ts
        assert snap.tf(1).bar is frame.base.bars[i]


def test_snapshots_do_not_change_when_more_data_arrives():
    """End-to-end restatement of the look-ahead rule over the whole feature
    stack: a snapshot computed with 300 bars of history must equal the same
    snapshot computed with 390."""
    full_series = make_series(390, seed=11)
    short_series = BarSeries("MNQ", 1, full_series.bars[:300])
    full = SymbolFrame(full_series, TIMEFRAMES)
    short = SymbolFrame(short_series, TIMEFRAMES)

    for i in (120, 200, 250, 299):
        a, b = short.snapshot(i), full.snapshot(i)
        assert a is not None and b is not None
        assert set(a.tfs) == set(b.tfs), f"timeframe set differs at bar {i}"
        for tf in a.tfs:
            assert a.tfs[tf].bar.ts == b.tfs[tf].bar.ts
            assert a.tfs[tf].values == b.tfs[tf].values, (
                f"bar {i}, {tf}m: feature values changed when later bars arrived")
        assert a.session_levels.to_dict() == b.session_levels.to_dict()
        assert a.regime.to_dict() == b.regime.to_dict()


# --------------------------------------------------------------------------
# Completed vs developing bars
# --------------------------------------------------------------------------

def test_symbol_frame_holds_only_completed_higher_timeframe_bars(frame: SymbolFrame):
    for tf in frame.timeframes:
        if tf == frame.base.minutes:
            continue
        bars = frame.frames[tf].series.bars
        assert bars, f"no {tf}m bars were built"
        assert all(b.complete for b in bars), (
            f"{tf}m series contains a developing bar")


def test_multitimeframe_view_separates_the_developing_bar():
    """Seven 1m bars from 09:30: one closed 5m bar and a two-bar stub."""
    series = BarSeries("MNQ", 1, make_bars(7, start=SESSION_OPEN, seed=4))
    view = MultiTimeframeView(series, (1, 5))

    completed = view.series(5)
    assert len(completed) == 1
    assert all(b.complete for b in completed)
    assert completed.last.ts == SESSION_OPEN

    developing = view.developing(5)
    assert developing is not None
    assert developing.complete is False
    assert developing.ts == SESSION_OPEN + timedelta(minutes=5)
    assert developing.ts not in [b.ts for b in completed]


def test_developing_bar_is_none_on_an_exact_bucket_boundary():
    series = BarSeries("MNQ", 1, make_bars(10, start=SESSION_OPEN, seed=4))
    view = MultiTimeframeView(series, (5,))
    assert len(view.series(5)) == 2
    assert view.developing(5) is None


def test_developing_bar_is_built_from_the_same_bars_it_omits():
    series = BarSeries("MNQ", 1, make_bars(7, start=SESSION_OPEN, seed=4))
    view = MultiTimeframeView(series, (5,))
    stub = view.developing(5)
    tail = series.bars[5:7]
    assert stub.open == pytest.approx(tail[0].open)
    assert stub.close == pytest.approx(tail[-1].close)
    assert stub.high == pytest.approx(max(b.high for b in tail))


def test_completed_property_drops_a_trailing_incomplete_bar():
    bars = make_bars(3, start=SESSION_OPEN, seed=6)
    bars[-1] = Bar(ts=bars[-1].ts, open=bars[-1].open, high=bars[-1].high,
                   low=bars[-1].low, close=bars[-1].close, volume=1.0,
                   minutes=1, complete=False)
    series = BarSeries("MNQ", 1, bars)
    assert len(series) == 3
    assert len(series.completed) == 2
    assert all(b.complete for b in series.completed)


def test_view_as_of_truncates_history():
    """``as_of`` is how replay guarantees nobody reads a bar from the future."""
    series = make_series(30, seed=8)
    cutoff = SESSION_OPEN + timedelta(minutes=10)
    view = MultiTimeframeView(series, (1, 5), as_of=cutoff)
    assert view.base.last.ts <= cutoff
    assert all(b.end_ts <= cutoff for b in view.series(5))


# --------------------------------------------------------------------------
# Resampling refuses to invent resolution
# --------------------------------------------------------------------------

def test_resample_refuses_a_finer_timeframe_than_the_source():
    series = make_series(20, minutes=5, seed=9)
    with pytest.raises(ValueError, match="shorter"):
        resample(series, 1)


def test_resample_refuses_a_non_multiple_of_the_source():
    series = make_series(20, minutes=5, seed=9)
    with pytest.raises(ValueError, match="multiple"):
        resample(series, 7)


def test_symbol_frame_refuses_a_finer_timeframe_than_its_base():
    series = make_series(40, minutes=5, seed=9)
    with pytest.raises(ValueError, match="finer"):
        SymbolFrame(series, (1, 5))


def test_multitimeframe_view_refuses_a_finer_timeframe_than_its_base():
    series = make_series(40, minutes=5, seed=9)
    with pytest.raises(ValueError, match="cannot synthesise"):
        MultiTimeframeView(series, (1, 5))


def test_resample_to_the_same_timeframe_is_a_copy():
    series = make_series(12, minutes=5, seed=9)
    out = resample(series, 5)
    assert len(out) == len(series)
    assert [b.ts for b in out] == [b.ts for b in series]


def test_resample_aggregates_ohlcv_correctly():
    series = BarSeries("MNQ", 1, make_bars(10, start=SESSION_OPEN, seed=2))
    out = resample(series, 5, keep_partial=False)
    assert len(out) == 2
    for k, agg in enumerate(out):
        members = series.bars[k * 5:(k + 1) * 5]
        assert agg.open == pytest.approx(members[0].open)
        assert agg.close == pytest.approx(members[-1].close)
        assert agg.high == pytest.approx(max(b.high for b in members))
        assert agg.low == pytest.approx(min(b.low for b in members))
        assert agg.volume == pytest.approx(sum(b.volume for b in members))
        assert agg.minutes == 5
        assert agg.complete is True


def test_resample_marks_a_short_trailing_bucket_incomplete():
    series = BarSeries("MNQ", 1, make_bars(7, start=SESSION_OPEN, seed=2))
    kept = resample(series, 5, keep_partial=True)
    dropped = resample(series, 5, keep_partial=False)
    assert len(kept) == 2 and kept.last.complete is False
    assert len(dropped) == 1 and dropped.last.complete is True


def test_align_bucket_snaps_to_the_expected_grid():
    ts = datetime(2026, 3, 17, 10, 7, tzinfo=ET)
    assert align_bucket(ts, 5) == datetime(2026, 3, 17, 10, 5, tzinfo=ET)
    assert align_bucket(ts, 15) == datetime(2026, 3, 17, 10, 0, tzinfo=ET)
    assert align_bucket(ts, 60) == datetime(2026, 3, 17, 10, 0, tzinfo=ET)


def test_align_bucket_daily_uses_the_eighteen_hundred_trading_day():
    """A daily bar starts at 18:00 ET the previous evening, not at midnight."""
    ts = datetime(2026, 3, 17, 10, 7, tzinfo=ET)
    assert align_bucket(ts, 1440) == datetime(2026, 3, 16, 18, 0, tzinfo=ET)


# --------------------------------------------------------------------------
# Series ordering guarantees
# --------------------------------------------------------------------------

def test_barseries_rejects_out_of_order_bars():
    bars = make_bars(3, start=SESSION_OPEN, seed=1)
    series = BarSeries("MNQ", 1, bars)
    earlier = Bar(ts=SESSION_OPEN, open=1.0, high=2.0, low=0.5, close=1.5,
                  volume=1.0, minutes=1)
    with pytest.raises(ValueError, match="not time-ordered"):
        series.append(earlier)


def test_barseries_rejects_a_mismatched_duration():
    series = BarSeries("MNQ", 1, make_bars(2, seed=1))
    five = Bar(ts=SESSION_OPEN + timedelta(minutes=5), open=1.0, high=2.0,
               low=0.5, close=1.5, volume=1.0, minutes=5)
    with pytest.raises(ValueError, match="duration"):
        series.append(five)


def test_appending_the_same_timestamp_replaces_the_developing_bar():
    bars = make_bars(2, start=SESSION_OPEN, seed=1)
    series = BarSeries("MNQ", 1, bars)
    finalised = Bar(ts=bars[-1].ts, open=bars[-1].open, high=bars[-1].high + 5,
                    low=bars[-1].low, close=bars[-1].close, volume=999.0,
                    minutes=1)
    series.append(finalised)
    assert len(series) == 2
    assert series.last.volume == 999.0


def test_before_is_exclusive_by_default():
    series = make_series(10, seed=1)
    cutoff = series.bars[5].ts
    assert len(series.before(cutoff)) == 5
    assert len(series.before(cutoff, inclusive=True)) == 6


def test_build_symbol_frame_matches_direct_construction():
    series = make_series(60, seed=12)
    a = build_symbol_frame(series, (1, 5))
    b = SymbolFrame(series, (1, 5))
    assert a.timeframes == b.timeframes
    assert [x.ts for x in a.frames[5].series] == [x.ts for x in b.frames[5].series]


def test_tf_index_rejects_an_unknown_timeframe(frame: SymbolFrame):
    with pytest.raises(KeyError):
        frame.tf_index(10, 240)
