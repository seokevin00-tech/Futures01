"""Anchored VWAP, and the look-ahead trap that makes it worth guarding.

The useful anchors are chosen retrospectively: the bar a major move departed
from is only identifiable once the move has happened. Anchoring there is
standard practice. *Reading* the line before the anchor was recognisable is
look-ahead of the purest kind, and it would be invisible - the line would
simply appear to have been there all along.
"""

from __future__ import annotations

import pytest

from futures_agents.data.loader import load_csv, synthetic_series
from futures_agents.indicators.structure import find_swings
from futures_agents.indicators.volume import (anchored_vwap, build_anchor_vwap,
                                              major_move_anchors, swing_anchors)


@pytest.fixture(scope="module")
def bars():
    return load_csv("csv/raw/MNQ_1h.csv", "MNQ", 60).bars


def test_line_is_hidden_until_the_anchor_was_knowable(bars):
    anchors = major_move_anchors(bars)
    assert anchors, "no displacement anchors found - the rest proves nothing"
    for a in anchors:
        assert a.knowable_index >= a.index
        assert a.value_at(a.knowable_index - 1) is None, (
            f"{a.kind} anchor at {a.index} is readable at "
            f"{a.knowable_index - 1}, before it could have been drawn")
        assert a.value_at(a.knowable_index) is not None


def test_swing_anchors_inherit_the_fractal_confirmation_lag(bars):
    """A swing is not knowable until its fractal completes, and that lag is
    exactly the right knowable point for a line anchored to it."""
    swings = find_swings(bars)[-20:]
    anchors = swing_anchors(bars, swings)
    assert anchors
    by_index = {s.index: s for s in swings}
    for a in anchors:
        swing = by_index.get(a.index)
        assert swing is not None
        assert a.knowable_index == swing.confirmed_index
        assert a.knowable_index > a.index, "a swing knowable at its own bar is a fractal that never confirmed"


def test_values_match_the_plain_implementation(bars):
    """The guarded object and the bare list must be the same arithmetic - the
    difference is only in what may be READ, never in what is computed."""
    plain = anchored_vwap(bars, 100)
    guarded = build_anchor_vwap(bars, 100)
    for i in (150, 500, 2000, len(bars) - 1):
        assert guarded.value_at(i) == pytest.approx(plain[i])


def test_anchor_is_placed_before_the_move_not_on_it(bars):
    """The anchor belongs at the last bar of balance - where the participants
    who got run over were positioned - not on the displacement bar itself."""
    for a in major_move_anchors(bars):
        assert a.knowable_index - a.index >= 1


def test_anchors_do_not_cluster_into_false_confluence(bars):
    """One impulse producing five near-identical lines would look like
    confluence and be a single observation."""
    idx = sorted(a.index for a in major_move_anchors(bars))
    gaps = [b - a for a, b in zip(idx, idx[1:])]
    assert all(g >= 5 for g in gaps), f"anchors closer than the minimum gap: {gaps[:5]}"


def test_prefix_stability(bars):
    """An anchor computed on a prefix must match the full series up to that
    point. If it does not, the line is being shaped by bars after it."""
    cut = 3000
    full = build_anchor_vwap(bars, 2000)
    part = build_anchor_vwap(bars[:cut], 2000)
    for i in range(2000, cut):
        a, b = full.value_at(i), part.value_at(i)
        if a is None or b is None:
            continue
        assert a == pytest.approx(b, rel=1e-12)


def test_no_anchor_on_an_empty_or_out_of_range_index():
    series = synthetic_series("MNQ", days=2, seed=1)
    assert build_anchor_vwap(series.bars, -1) is None
    assert build_anchor_vwap(series.bars, len(series) + 5) is None
