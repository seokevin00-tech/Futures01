"""The condition library, the confluence coverage it owes the specification,
and the look-ahead safety of the structures the new conditions read.

Two of these tests exist because of a defect they would have caught.

``Condition.evaluate`` swallows ``TypeError``/``ValueError``/``KeyError`` and
returns "no signal", which is right for a four-thousand-strategy sweep - one
unlucky bar must not abort the run - and disastrous for a programming error:
fifteen freshly written conditions called ``fmt_price(price, snap.spec)``
instead of a decimal count, every call raised inside the f-string, every
condition reported "did not fire", and a sweep would have recorded them as
confluences that simply never trigger. So the suite evaluates every condition
*raw*, outside the guard, and separately insists that each one fires at least
once on representative data. A condition that never fires has never been
tested, whatever the backtest says about it.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from futures_agents.data.bars import BarSeries
from futures_agents.data.loader import synthetic_series
from futures_agents.features import SymbolFrame
from futures_agents.indicators.structure import supply_demand_zones
from futures_agents.strategies.base import ConditionKind
from futures_agents.strategies.combinator import (TEMPLATES, _filter_sets,
                                                  generate_strategies)
from futures_agents.strategies.coverage import (SPEC_CONFLUENCES,
                                                coverage_report, uncovered)
from futures_agents.strategies.library import CONDITIONS, CONDITION_GROUPS

PRIMARY_TF = 5
TIMEFRAMES = (1, 5, 15, 60)


@pytest.fixture(scope="module")
def oi_series() -> BarSeries:
    """A multi-day series that also carries open interest.

    Several conditions need more than one session (a prior-session volume
    profile) or a column the synthetic generator does not produce (open
    interest). Without both, those conditions would be "tested" against data
    on which they cannot fire, which tests nothing.
    """
    src = synthetic_series("MNQ", days=10, seed=5)
    oi = 100_000.0
    bars = []
    for b in src.bars:
        oi += (b.close - b.open) * 8.0 + 3.0
        bars.append(replace(b, open_interest=round(oi)))
    return BarSeries("MNQ", src.minutes, bars)


@pytest.fixture(scope="module")
def sym_frame(oi_series: BarSeries) -> SymbolFrame:
    return SymbolFrame(oi_series, TIMEFRAMES)


@pytest.fixture(scope="module")
def snapshots(sym_frame: SymbolFrame):
    return [sym_frame.snapshot(i) for i in range(0, len(sym_frame.base), 7)]


# --------------------------------------------------------------------------
# The library itself
# --------------------------------------------------------------------------

def test_every_condition_evaluates_without_raising(snapshots):
    """Call the raw function, not ``evaluate``.

    ``evaluate``'s exception guard is what turns a broken condition into a
    silent one, so the guard is exactly what this test has to step around.
    """
    failures = []
    for name, cond in sorted(CONDITIONS.items()):
        for snap in snapshots:
            if snap.tf(PRIMARY_TF) is None:
                continue
            try:
                cond.fn(snap, PRIMARY_TF)
            except Exception as exc:                  # noqa: BLE001 - that is the point
                failures.append(f"{name}: {type(exc).__name__}: {exc}")
                break
    assert not failures, "conditions raised (evaluate() would hide this):\n" + "\n".join(failures)


def test_every_condition_fires_at_least_once(snapshots):
    silent = []
    for name, cond in sorted(CONDITIONS.items()):
        if not any(cond.evaluate(s, PRIMARY_TF).triggered for s in snapshots):
            silent.append(name)
    assert not silent, ("conditions that never fired - untested, and dead in any "
                        f"confluence containing them: {silent}")


def test_signal_conditions_produce_both_directions(snapshots):
    """A signal condition that only ever says LONG is a bias, not a signal."""
    one_sided = []
    for name, cond in sorted(CONDITIONS.items()):
        if cond.kind is not ConditionKind.SIGNAL:
            continue
        seen = {r.direction for r in (cond.evaluate(s, PRIMARY_TF) for s in snapshots)
                if r.triggered}
        if len(seen) < 2:
            one_sided.append((name, sorted(d.value for d in seen)))
    assert not one_sided, f"signal conditions that only fire one way: {one_sided}"


def test_filter_conditions_are_direction_agnostic(snapshots):
    """A FILTER gates a setup; it must not smuggle in a direction."""
    for name, cond in sorted(CONDITIONS.items()):
        if cond.kind is not ConditionKind.FILTER:
            continue
        for snap in snapshots:
            res = cond.evaluate(snap, PRIMARY_TF)
            if res.triggered:
                assert res.direction.value == "NEUTRAL", (
                    f"filter {name} returned direction {res.direction}")


def test_condition_names_and_groups_are_consistent():
    for group, names in CONDITION_GROUPS.items():
        for name in names:
            assert CONDITIONS[name].group == group


# --------------------------------------------------------------------------
# Coverage of the specification's confluence list
# --------------------------------------------------------------------------

def test_spec_confluence_coverage_is_complete():
    gaps = uncovered()
    assert not gaps, coverage_report()


def test_spec_list_is_the_full_thirty_eight():
    assert len(SPEC_CONFLUENCES) == 38


def test_combinator_draws_from_every_condition_group():
    """Coverage in the library is not coverage in the generated strategies.

    A condition nothing references is still a variable the research never
    tests, which is the same gap one layer down.
    """
    strategies = generate_strategies("MNQ", list(TIMEFRAMES), max_total=1500, seed=1)
    used = {c.group for s in strategies for c in s.conditions}
    missing = sorted(set(CONDITION_GROUPS) - used)
    assert not missing, f"condition groups no generated strategy uses: {missing}"


def test_optional_filters_always_include_a_control():
    """The bare base-filter set must survive, or "with the news filter" has
    nothing to be compared against and stops being a tested variable."""
    for template in TEMPLATES:
        sets = _filter_sets(template)
        assert sets[0] == tuple(template.base_filters)
        assert len(set(sets)) == len(sets)
        if template.optional_filters:
            assert len(sets) > 1


# --------------------------------------------------------------------------
# Supply and demand zones: look-ahead safety
# --------------------------------------------------------------------------

def test_zone_as_of_hides_future_touches_and_invalidation(oi_series):
    zones = supply_demand_zones(oi_series.bars)
    assert zones, "no zones found - the rest of this test would prove nothing"
    for z in zones:
        for probe in (z.index, z.index + 5, z.index + 40):
            seen = z.as_of(probe)
            assert all(t <= probe for t in seen.touch_indices)
            assert seen.invalidated_index is None or seen.invalidated_index <= probe


def test_zone_geometry_is_prefix_stable(oi_series):
    """A zone found on a prefix must have the same geometry as on the full
    series. If it does not, the detector is reading bars it should not see."""
    bars = oi_series.bars
    full = {z.index: (z.kind, z.top, z.bottom) for z in supply_demand_zones(bars)}
    checked = 0
    for n in range(800, len(bars), 1301):
        for z in supply_demand_zones(bars[:n]):
            assert z.index in full, f"zone at {z.index} exists on a prefix but not in full"
            assert full[z.index] == (z.kind, z.top, z.bottom)
            checked += 1
    assert checked > 50


def test_active_zones_are_masked_to_the_asking_bar(sym_frame):
    frame = sym_frame.frames[PRIMARY_TF]
    n = len(frame.series)
    for i in range(0, n, max(1, n // 40)):
        for z in frame.active_zones(i):
            assert z.index <= i, "zone visible before its departure bar closed"
            assert all(t <= i for t in z.touch_indices)
            assert z.invalidated_index is None


def test_zone_direction_matches_its_side():
    """Demand zones are bought, supply zones sold - and the proximal edge is
    the one price reaches first from the side it is approached from."""
    from futures_agents.indicators.structure import SDZone
    from datetime import datetime
    from futures_agents.timeutil import ET
    ts = datetime(2026, 3, 17, 10, 0, tzinfo=ET)
    demand = SDZone(10, ts, "DEMAND", 100.0, 95.0, 8, 9, 2.5)
    supply = SDZone(10, ts, "SUPPLY", 110.0, 105.0, 8, 9, 2.5)
    assert (demand.proximal, demand.distal) == (100.0, 95.0)
    assert (supply.proximal, supply.distal) == (105.0, 110.0)
    assert demand.fresh and supply.fresh
    assert not replace(demand, touch_indices=(12,)).fresh


# --------------------------------------------------------------------------
# Fibonacci: measured from confirmed swings only
# --------------------------------------------------------------------------

def test_fib_leg_uses_only_confirmed_swings(sym_frame):
    frame = sym_frame.frames[PRIMARY_TF]
    n = len(frame.series)
    for i in range(0, n, max(1, n // 40)):
        snap = frame.snapshot(i)
        leg = snap.swing_leg()
        if leg is None:
            continue
        lo, hi, direction = leg
        assert hi > lo
        assert snap.last_swing_high_index is not None
        assert snap.last_swing_low_index is not None
        # Both swings must already be confirmed at this bar, not merely present
        # in the series. A retracement drawn from an unconfirmed swing is drawn
        # from a point the market had not yet made.
        visible = {s.index for s in frame.visible_swings(i)}
        assert snap.last_swing_high_index in visible
        assert snap.last_swing_low_index in visible
        expected = "UP" if snap.last_swing_high_index > snap.last_swing_low_index else "DOWN"
        assert direction == expected


def test_fib_zone_ordering(sym_frame):
    """0.618 of an up leg sits above 0.786 - the band must come back sorted
    whichever way the leg ran."""
    frame = sym_frame.frames[PRIMARY_TF]
    for i in range(0, len(frame.series), 137):
        snap = frame.snapshot(i)
        band = snap.fib_zone(0.618, 0.786)
        if band is None:
            continue
        lo, hi, _ = band
        assert lo <= hi
        leg_lo, leg_hi, _ = snap.swing_leg()
        assert leg_lo <= lo <= hi <= leg_hi


# --------------------------------------------------------------------------
# News conditions: rule-derived, so identical whatever the series around them
# --------------------------------------------------------------------------

def test_news_proximity_does_not_depend_on_series_length(oi_series):
    """The calendar is projected from recurrence rules, so the answer at a
    given timestamp cannot change because more bars were loaded after it."""
    bars = oi_series.bars
    short = SymbolFrame(BarSeries("MNQ", oi_series.minutes, bars[:4000]), (1, 5))
    long = SymbolFrame(BarSeries("MNQ", oi_series.minutes, bars), (1, 5))
    checked = 0
    for i in range(0, 4000, 211):
        a, b = short.snapshot(i), long.snapshot(i)
        assert a.ts == b.ts
        assert a.in_news_blackout == b.in_news_blackout
        assert a.minutes_to_high_impact == b.minutes_to_high_impact
        assert a.minutes_since_high_impact == b.minutes_since_high_impact
        checked += 1
    assert checked > 10


def test_every_bar_sees_a_future_release(sym_frame):
    """The projection horizon has to outrun the series.

    At a five-day horizon the last bars of a run reported "no high-impact event
    ahead" purely because CPI, payrolls and PCE all sat outside the window - a
    quiet calendar that was an artefact of the lookahead limit, not of the
    calendar.
    """
    n = len(sym_frame.base)
    finite = sum(1 for i in range(0, n, 97)
                 if sym_frame.snapshot(i).minutes_to_high_impact != float("inf"))
    total = len(range(0, n, 97))
    assert finite == total, f"{total - finite} bars saw no upcoming release"
