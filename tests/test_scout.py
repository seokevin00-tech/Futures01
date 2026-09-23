"""The scout, and the degeneracy it was built on top of.

The first two tests here guard a defect that cost nothing to introduce and
would have been invisible forever: ``mtf_aligned`` aggregating a single
timeframe's vote and thereby returning exactly what ``structure_trend``
returns, on every bar, while the strategy template required both and called
the pair "confluence".
"""

from datetime import datetime, timedelta, timezone

import pytest

from futures_agents.data.bars import Bar, BarSeries
from futures_agents.features import build_symbol_frame
from futures_agents.strategies.base import ConditionKind
from futures_agents.strategies.library import CONDITIONS, get_condition
from futures_agents.scout import CHART_CUES, FRAMES, cue


def _series(n: int = 900, symbol: str = "MGC", tf: int = 60) -> BarSeries:
    """A trending series long enough to build a daily timeframe on top of."""
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    bars = []
    price = 100.0
    for i in range(n):
        # A slow drift with an oscillation, so structure is neither a straight
        # line nor noise: the timeframes must be able to disagree.
        price += 0.05 + 0.9 * ((i // 37) % 3 - 1)
        o = price
        h = price + 1.2
        lo = price - 1.2
        c = price + 0.3
        bars.append(Bar(ts=start + timedelta(minutes=tf * i), open=o, high=h,
                        low=lo, close=c, volume=1000 + (i % 17) * 50,
                        minutes=tf))
    return BarSeries(symbol, tf, bars)


def test_mtf_aligned_refuses_to_fire_with_a_single_voter():
    """At the top of the frame there is nothing to align WITH.

    Before this guard, ``alignment(from_tf=240)`` in a ``[60, 240]`` frame
    aggregated one vote and the condition reported "aligned" off it.
    """
    frame = build_symbol_frame(_series(), [60, 240])
    mtf = get_condition("mtf_aligned")
    fired = 0
    checked = 0
    for i in range(len(frame)):
        snap = frame.snapshot(i)
        if snap is None or 240 not in snap.tfs:
            continue
        checked += 1
        if mtf.evaluate(snap, 240).triggered:
            fired += 1
    assert checked > 50, "test series too short to be meaningful"
    assert fired == 0, (
        f"mtf_aligned fired {fired} times at the frame's top timeframe, where "
        "only one timeframe can vote")


def test_mtf_aligned_is_not_a_copy_of_structure_trend():
    """With timeframes above it, alignment must be able to disagree.

    100% agreement is the signature of the bug: it means the condition is
    reading one timeframe's structure and nothing else.
    """
    frame = build_symbol_frame(_series(), [60, 240, 1440])
    mtf, struct = get_condition("mtf_aligned"), get_condition("structure_trend")
    same = total = 0
    for i in range(len(frame)):
        snap = frame.snapshot(i)
        if snap is None or 1440 not in snap.tfs:
            continue
        total += 1
        m, s = mtf.evaluate(snap, 60), struct.evaluate(snap, 60)
        md = m.direction if m.triggered else None
        sd = s.direction if s.triggered else None
        same += (md == sd)
    assert total > 50
    assert same < total, (
        "mtf_aligned agreed with structure_trend on every one of "
        f"{total} bars - it is not reading the higher timeframes")


def test_frames_never_put_the_primary_at_the_top():
    """The frame must carry something above the timeframe being ranked.

    This is the structural half of the fix: the guard above stops the
    condition lying, and this stops the frame from silencing it.
    """
    for primary, tfs in FRAMES.items():
        assert tfs[0] == primary, f"{primary}: primary must be the lowest member"
        assert len(tfs) >= 2, f"{primary}: nothing above it to aggregate"
        assert max(tfs) > primary, f"{primary}: frame tops out at the primary"


def test_every_condition_has_chart_language():
    """A ranking is only usable if every rule can be found on a chart."""
    missing = sorted(name for name in CONDITIONS if name not in CHART_CUES)
    assert not missing, (
        "conditions with no chart cue - a trader cannot act on these: "
        + ", ".join(missing))


def test_cue_falls_back_rather_than_returning_nothing():
    assert cue("definitely_not_a_condition", "the fallback") == "the fallback"
    assert cue("structure_trend") == CHART_CUES["structure_trend"]
    # Never empty: an empty cue would render a blank checklist line.
    assert cue("definitely_not_a_condition") == "definitely_not_a_condition"


def test_chart_cues_are_written_for_a_human():
    """Cues describe what is seen, not what is computed."""
    for name, text in CHART_CUES.items():
        assert len(text) > 15, f"{name}: cue too terse to act on"
        assert not text.startswith(name), f"{name}: cue just repeats the id"


@pytest.mark.parametrize("kind", [ConditionKind.SIGNAL, ConditionKind.FILTER])
def test_both_condition_kinds_are_covered(kind):
    named = [n for n, c in CONDITIONS.items() if c.kind is kind]
    assert named, f"no conditions registered with kind {kind}"
    assert all(n in CHART_CUES for n in named)
