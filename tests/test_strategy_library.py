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
from datetime import date

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

#: A Tuesday well clear of a DST transition, matching conftest's convention.
#: Fixed so the suite's news-dependent assertions do not drift with the clock.
FIXTURE_END = date(2026, 3, 17)


@pytest.fixture(scope="module")
def oi_series() -> BarSeries:
    """A multi-day series that also carries open interest.

    Several conditions need more than one session (a prior-session volume
    profile) or a column the synthetic generator does not produce (open
    interest). Without both, those conditions would be "tested" against data
    on which they cannot fire, which tests nothing.
    """
    # end_date pinned: synthetic_series defaults to date.today(), and while
    # the bar VALUES are seeded the timestamps are not - so which scheduled
    # releases fall inside the fixture changes from one day to the next, and
    # any assertion about the news columns would pass or fail by calendar.
    src = synthetic_series("MNQ", days=10, seed=5, end_date=FIXTURE_END)
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


def test_spec_list_covers_the_specification_and_what_was_added_since():
    """Thirty-eight variables come from the specification. Anything beyond
    that was added because a gap was found in use - candlestick patterns, for
    instance, after an audit showed no condition anywhere read a bar's body or
    wicks. The count is pinned so an entry cannot be dropped silently."""
    assert len(SPEC_CONFLUENCES) == 39
    assert "candlestick patterns" in SPEC_CONFLUENCES


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


def test_blackout_is_the_window_the_risk_config_asks_for(sym_frame):
    """Before means before. The sign convention is the trap.

    ``ts - event`` is *negative* ahead of a release, so the window is
    ``[-before, +after]``. Written ``[-after, +before]`` it stands the system
    aside for the wrong half of the event: it left the ten minutes into an
    08:30 print open for business - the minutes the filter exists to close -
    and then blocked for twice as long afterwards as the risk config says.
    """
    from datetime import timedelta

    from futures_agents.econ_calendar import Impact, project_events
    from futures_agents.features import (NEWS_BLACKOUT_AFTER_MIN,
                                         NEWS_BLACKOUT_BEFORE_MIN)

    bars = sym_frame.base.bars
    events = [e for e in project_events(bars[0].ts - timedelta(days=2),
                                        bars[-1].ts + timedelta(days=45))
              if e.impact.rank >= Impact.HIGH.rank
              and bars[0].ts <= e.when <= bars[-1].ts]
    assert events, "no high-impact release inside the series to test against"

    # Compare the whole flagged set against the union of the configured
    # windows. Asserting per event would break on the FOMC statement and its
    # press conference, which are thirty minutes apart and legitimately merge.
    flagged = {b.ts for i, b in enumerate(bars) if sym_frame._news[i][2]}
    expected = {b.ts for b in bars
                if any(-NEWS_BLACKOUT_BEFORE_MIN
                       <= (b.ts - ev.when).total_seconds() / 60.0
                       <= NEWS_BLACKOUT_AFTER_MIN for ev in events)}
    assert expected, "no bar fell inside a configured blackout window"
    early = sorted(t for t in expected - flagged)
    late = sorted(t for t in flagged - expected)
    assert not early, (
        f"{len(early)} bars inside the configured window were not blacked out, "
        f"first {early[0]} - the minutes ahead of a release are exactly what "
        f"this filter exists to close")
    assert not late, (
        f"{len(late)} bars outside the configured window were blacked out, "
        f"first {late[0]}")


def test_blackout_window_matches_the_account_config():
    """One source of truth. A backtest that stands aside for a different window
    than the live risk manager has measured a different strategy."""
    from futures_agents.config import AccountConfig
    from futures_agents.features import (NEWS_BLACKOUT_AFTER_MIN,
                                         NEWS_BLACKOUT_BEFORE_MIN)
    cfg = AccountConfig()
    assert NEWS_BLACKOUT_BEFORE_MIN == cfg.news_blackout_before_min
    assert NEWS_BLACKOUT_AFTER_MIN == cfg.news_blackout_after_min


def test_event_proximity_blacks_out_before_the_release():
    """The same inversion, in the function the live path would call."""
    from datetime import timedelta

    from futures_agents.econ_calendar import (Impact, event_proximity,
                                              project_events)
    from futures_agents.timeutil import ET
    from datetime import datetime

    anchor = datetime(2026, 3, 1, 0, 0, tzinfo=ET)
    events = [e for e in project_events(anchor, anchor + timedelta(days=20))
              if e.impact.rank >= Impact.HIGH.rank]
    assert events
    ev = events[0].when
    for minutes, expected in ((-11, False), (-10, True), (-1, True),
                              (0, True), (5, True), (6, False), (20, False)):
        _, blackout = event_proximity(ev + timedelta(minutes=minutes))
        assert blackout is expected, (
            f"{minutes:+d}m from the release: blackout={blackout}, expected {expected}")


def test_event_proximity_sees_the_next_release_between_monthly_prints():
    """A 72-hour horizon answers "nothing ahead" for most of every month, and
    a caller reads that as a quiet calendar rather than as a horizon artefact.
    """
    from datetime import datetime, timedelta

    from futures_agents.econ_calendar import (Impact, event_proximity,
                                              project_events)
    from futures_agents.timeutil import ET

    anchor = datetime(2026, 3, 1, 0, 0, tzinfo=ET)
    events = [e for e in project_events(anchor, anchor + timedelta(days=20))
              if e.impact.rank >= Impact.HIGH.rank]
    minutes, _ = event_proximity(events[0].when + timedelta(minutes=30))
    assert minutes != float("inf"), "no release ahead 30 minutes after one landed"


# --------------------------------------------------------------------------
# Fibonacci levels are measured the way they are drawn
# --------------------------------------------------------------------------

def test_fib_sr_confluence_measures_retracements_from_the_end_of_the_leg(sym_frame):
    """A retracement runs from the end of the leg back towards its start.

    Measuring every ratio from the leg's low regardless of direction mirrors
    the whole set on an up leg: it tested the 0.618 level and reported it as
    0.382, and it tested the 0.214 level - which nobody draws - in place of
    the 0.786.
    """
    from futures_agents.strategies.library import CONDITIONS

    cond = CONDITIONS["fib_sr_confluence"]
    checked = 0
    for i in range(0, len(sym_frame.base), 23):
        snap = sym_frame.snapshot(i)
        s = snap.tf(PRIMARY_TF)
        if s is None:
            continue
        leg = s.swing_leg()
        atr_v = s.get("atr")
        if leg is None or not atr_v or not s.sr_levels:
            continue
        lo, hi, direction = leg
        span = hi - lo
        if span <= 0:
            continue
        res = cond.evaluate(snap, PRIMARY_TF)
        if not res.triggered:
            continue
        checked += 1
        ratio = float(res.detail.split()[0])
        expected = hi - ratio * span if direction == "UP" else lo + ratio * span
        assert res.value == pytest.approx(round(expected, 4)), (
            f"bar {i}: reported the {ratio} retracement of a {direction} leg "
            f"at {res.value}, but that level is {round(expected, 4)}")
    assert checked > 20, f"only {checked} firings examined - proves little"


# --------------------------------------------------------------------------
# The exception guard counts what it swallows
# --------------------------------------------------------------------------

def test_the_guard_counts_what_it_swallows():
    """Silence is the bug. A condition that raises on every bar and is recorded
    as "0% trigger rate" is indistinguishable, in a sweep report, from one that
    simply never found its setup."""
    from futures_agents.strategies.base import (Condition, condition_errors,
                                                reset_condition_errors)

    def broken(snap, tf):
        raise TypeError("unsupported format string passed to ContractSpec.__format__")

    cond = Condition(name="deliberately_broken", group="test", fn=broken)

    class _Snap:
        def tf(self, _timeframe):
            return object()

    reset_condition_errors()
    try:
        res = cond.evaluate(_Snap(), 5)
        assert res.triggered is False, "the guard must still absorb the raise"
        assert condition_errors() == {("deliberately_broken", "TypeError"): 1}
        cond.evaluate(_Snap(), 5)
        assert condition_errors()[("deliberately_broken", "TypeError")] == 2
    finally:
        reset_condition_errors()


def test_no_condition_in_the_library_raises_through_the_guard(snapshots):
    """The counting version of ``test_every_condition_evaluates_without_raising``.

    That test calls ``cond.fn`` directly and so only covers the timeframe it
    picks. This one goes through ``evaluate`` on every timeframe in the frame
    and reads the tally afterwards, which is what a real sweep does.
    """
    from futures_agents.strategies.base import (condition_errors,
                                                reset_condition_errors)

    reset_condition_errors()
    try:
        for snap in snapshots:
            for tf in TIMEFRAMES:
                if snap.tf(tf) is None:
                    continue
                for cond in CONDITIONS.values():
                    cond.evaluate(snap, tf)
        assert condition_errors() == {}, (
            "conditions raised inside the guard and reported 'no signal': "
            f"{condition_errors()}")
    finally:
        reset_condition_errors()


# --------------------------------------------------------------------------
# Reachability: the invariant three reviewers found broken
# --------------------------------------------------------------------------

def test_every_condition_can_reach_a_strategy():
    """Registration is not reachability.

    The combinator draws SIGNAL conditions from a template's condition groups
    and FILTER conditions only from its filter lists, so a filter no template
    lists cannot appear in any strategy however well it is written. Fourteen
    of seventy-three conditions were in that state and nothing reported it -
    the coverage report counted them as proof a specification variable was
    tradeable.
    """
    from futures_agents.strategies.coverage import unreachable_conditions
    dead = unreachable_conditions()
    assert not dead, f"conditions no template can draw: {dead}"


def test_every_condition_appears_in_a_generated_strategy():
    """The end-to-end version of the same claim, which is the one that counts:
    reachable in principle is not the same as generated in practice.

    Asked of the whole template catalogue, not of one contract. Per-symbol
    profiles narrow generation on purpose - MGC is not tested with an
    opening-range template calibrated to the equity open - so a condition only
    that family can draw is legitimately absent from MGC's universe while still
    being reachable by the system. Passing the full group list keeps this test
    about the library-to-catalogue invariant it was written for; the per-symbol
    question is tested in test_symbol_profiles.py.
    """
    from futures_agents.strategies.combinator import TEMPLATES
    all_groups = [t.group for t in TEMPLATES]
    strategies = generate_strategies("MNQ", list(TIMEFRAMES), groups=all_groups,
                                     max_total=4000, seed=1)
    from futures_agents.strategies.coverage import NOT_GENERATED
    used = {c.name for s in strategies for c in s.conditions}
    missing = sorted(set(CONDITIONS) - used - set(NOT_GENERATED))
    assert not missing, f"conditions that reach no generated strategy: {missing}"


def test_coverage_entries_do_not_quote_dead_conditions():
    from futures_agents.strategies.coverage import weakly_covered
    weak = weakly_covered()
    assert not weak, ("spec variables quoting a condition the generator will "
                      f"never build: {weak}")


# --------------------------------------------------------------------------
# Diversity: a confluence must not count one observation twice
# --------------------------------------------------------------------------

def test_no_strategy_holds_a_globally_exclusive_pair(snapshots):
    """`value_area_breakout` and `prior_day_breakout` sit in different groups,
    so the diversity rule let them share a confluence - while co-firing on
    thousands of bars and never once disagreeing on direction."""
    from futures_agents.strategies.combinator import GLOBAL_EXCLUSIVE
    strategies = generate_strategies("MNQ", list(TIMEFRAMES), max_total=4000, seed=1)
    offenders = []
    for s in strategies:
        names = {c.name for c in s.conditions}
        for pair in GLOBAL_EXCLUSIVE:
            if len(names & set(pair)) > 1:
                offenders.append((s.name, pair))
    assert not offenders, f"confluences double-counting one observation: {offenders[:5]}"


def test_globally_exclusive_pairs_really_are_near_duplicates(snapshots):
    """Guard the list itself. A pair that does NOT duplicate should not be in
    here - banning genuinely different evidence costs real hypotheses."""
    from futures_agents.strategies.combinator import GLOBAL_EXCLUSIVE
    for a, b in GLOBAL_EXCLUSIVE:
        ca, cb = CONDITIONS[a], CONDITIONS[b]
        fa, fb = {}, {}
        for i, snap in enumerate(snapshots):
            ra, rb = ca.evaluate(snap, PRIMARY_TF), cb.evaluate(snap, PRIMARY_TF)
            if ra.triggered:
                fa[i] = ra.direction
            if rb.triggered:
                fb[i] = rb.direction
        inter = set(fa) & set(fb)
        if len(inter) < 30:
            continue                     # too few co-fires to judge on this data
        agree = sum(1 for i in inter if fa[i] == fb[i]) / len(inter)
        assert agree >= 0.90, (
            f"{a} / {b} are listed as duplicates but agree only "
            f"{agree:.2f} of the time over {len(inter)} co-fires")


# --------------------------------------------------------------------------
# The news windows must not contradict each other
# --------------------------------------------------------------------------

def test_post_news_window_starts_where_the_blackout_ends(sym_frame):
    """A strategy carrying both filters must not be asking to trade inside a
    window the risk manager has already closed. The only legitimate overlap is
    a second event: standing in the reaction window of one release while
    inside the run-up blackout of the next, which is what FOMC 14:00 and its
    14:30 press conference produce."""
    from futures_agents.features import NEWS_BLACKOUT_BEFORE_MIN
    cond = CONDITIONS["post_news_window"]
    spurious = []
    n = len(sym_frame.base)
    for i in range(0, n, 3):
        snap = sym_frame.snapshot(i)
        if (snap.in_news_blackout and cond.evaluate(snap, PRIMARY_TF).triggered
                and snap.minutes_to_high_impact > NEWS_BLACKOUT_BEFORE_MIN):
            spurious.append(snap.ts)
    assert not spurious, (
        f"{len(spurious)} bars are inside the blackout AND the post-news "
        f"window with no upcoming event to explain it, e.g. {spurious[:3]}")


def test_blackout_is_before_the_event_not_after_it():
    """The sign convention, pinned. Written the other way round the window
    blacks out `after_min` AHEAD of the print and `before_min` past it, which
    leaves the minutes immediately before an 08:30 release open for business -
    the exact opposite of the point."""
    from datetime import timedelta
    from futures_agents.econ_calendar import Impact, event_proximity, project_events
    from futures_agents.timeutil import ET
    from datetime import datetime
    start = datetime(2026, 1, 5, tzinfo=ET)
    events = [e for e in project_events(start, start + timedelta(days=40))
              if e.impact.rank >= Impact.HIGH.rank]
    assert events, "no high-impact events projected - the rest proves nothing"
    ev = events[0].when
    # 5 minutes BEFORE the release: must be blacked out at -10/+15.
    assert event_proximity(ev - timedelta(minutes=5), before_min=10, after_min=15)[1]
    # 12 minutes after: still inside the post-event half.
    assert event_proximity(ev + timedelta(minutes=12), before_min=10, after_min=15)[1]
    # 30 minutes before and 30 after: outside both halves.
    assert not event_proximity(ev - timedelta(minutes=30), before_min=10, after_min=15)[1]
    assert not event_proximity(ev + timedelta(minutes=30), before_min=10, after_min=15)[1]


def test_a_textbook_base_is_detected(oi_series):
    """Balance, then a decisive departure. The simplest case the detector
    exists for, and the one it used to reject.

    The base test was ``range <= 0.8 x`` an average computed over a window that
    contained the base bars. In a uniform consolidation every bar equals that
    average, so no bar could be 0.8 of it: the flatter the base, the more
    certainly it was thrown away. Zones only formed where the preceding twenty
    bars happened to be uneven - close to the opposite of the pattern.
    """
    from datetime import timedelta
    from futures_agents.data.bars import Bar
    from futures_agents.indicators.structure import supply_demand_zones

    t0 = oi_series.bars[0].ts
    seq = [Bar(ts=t0 + timedelta(minutes=k), open=100.0, high=100.5, low=99.5,
               close=100.0, volume=100.0) for k in range(25)]
    seq.append(Bar(ts=t0 + timedelta(minutes=25), open=100.0, high=110.0,
                   low=99.9, close=109.5, volume=400.0))
    zones = supply_demand_zones(seq)
    assert len(zones) == 1, f"expected one demand zone, got {zones}"
    z = zones[0]
    assert z.kind == "DEMAND"
    assert (z.bottom, z.top) == (99.5, 100.5)
    assert z.fresh


def test_a_wide_bar_alone_is_not_a_zone(oi_series):
    """Guard the other side: without preceding balance, a large bar is just a
    large bar, and taking every one of them would paint the whole chart."""
    from datetime import timedelta
    from futures_agents.data.bars import Bar
    from futures_agents.indicators.structure import supply_demand_zones

    t0 = oi_series.bars[0].ts
    # Every bar wide and trending - no balance anywhere to depart from.
    seq = [Bar(ts=t0 + timedelta(minutes=k), open=100.0 + k * 5, high=106.0 + k * 5,
               low=99.0 + k * 5, close=105.0 + k * 5, volume=300.0)
           for k in range(30)]
    assert supply_demand_zones(seq) == []


def test_oi_confirmation_pairs_the_same_window(snapshots):
    """The OI change spans 20 bars, so the price move it is confirmed against
    must span the same 20 bars. Position-in-range is a different statement - a
    bar can sit high in its range while the net move over the window is down -
    and it disagreed with the actual move on hundreds of bars, emitting the
    opposite direction to this condition's own thesis."""
    cond = CONDITIONS["oi_price_confirmation"]
    fired = 0
    for snap in snapshots:
        res = cond.evaluate(snap, PRIMARY_TF)
        if not res.triggered:
            continue
        fired += 1
        move = snap.tf(PRIMARY_TF).get("roc20")
        assert move is not None
        expected = "LONG" if move > 0 else "SHORT"
        assert res.direction.value == expected, (
            f"OI condition said {res.direction.value} on a {move:+.3f}% move")
    assert fired > 0, "condition never fired - the assertion proved nothing"


def test_no_signal_condition_is_vacuously_true(snapshots):
    """A guard that never fires is not a guard.

    Six conditions declined only when a floating-point difference was exactly
    zero - `cvd_directional` declined on 0 of 24,960 bars. They are *bias*
    conditions by nature and that is legitimate: measured, they split 42/58 to
    50/50 by direction, so inside a confluence requiring agreement they do
    gate. What was wrong is that a close one tick from its own EMA counted as
    "above" it. This asserts the deadband exists, not that the condition is
    selective - a firing rate alone would have condemned conditions that are
    working as designed.
    """
    offenders = []
    for name, cond in sorted(CONDITIONS.items()):
        if cond.kind is not ConditionKind.SIGNAL:
            continue
        fired = sum(1 for s in snapshots if cond.evaluate(s, PRIMARY_TF).triggered)
        rate = fired / len(snapshots)
        if rate > 0.97:
            offenders.append((name, round(rate, 4)))
    assert not offenders, (
        f"signal conditions with no effective deadband: {offenders}")


def test_no_signal_condition_is_directionally_stuck(snapshots):
    """The brokenness that a firing rate cannot see: a condition that fires
    constantly but almost always the same way is a bias with no information,
    whatever its trigger rate looks like."""
    offenders = []
    for name, cond in sorted(CONDITIONS.items()):
        if cond.kind is not ConditionKind.SIGNAL:
            continue
        longs = shorts = 0
        for snap in snapshots:
            res = cond.evaluate(snap, PRIMARY_TF)
            if not res.triggered:
                continue
            if res.direction.value == "LONG":
                longs += 1
            elif res.direction.value == "SHORT":
                shorts += 1
        total = longs + shorts
        if total < 50:
            continue
        skew = max(longs, shorts) / total
        if skew > 0.90:
            offenders.append((name, round(skew, 3)))
    assert not offenders, f"signal conditions stuck in one direction: {offenders}"


def test_no_optional_filter_is_a_total_veto(snapshots):
    """An optional filter exists so the desk can measure "with it" against
    "without it". A filter that passes under 2% of bars makes the treatment arm
    an empty set, which is not a control. `relative_volume_high` passed 0.68%
    of 15-minute bars while two templates offered it as an optional filter."""
    from futures_agents.strategies.combinator import TEMPLATES
    optional = {n for t in TEMPLATES for n in t.optional_filters}
    offenders = []
    for name in sorted(optional):
        cond = CONDITIONS.get(name)
        if cond is None or cond.kind is not ConditionKind.FILTER:
            continue
        passed = sum(1 for s in snapshots if cond.evaluate(s, PRIMARY_TF).triggered)
        rate = passed / len(snapshots)
        if rate < 0.02:
            offenders.append((name, round(rate, 4)))
    assert not offenders, f"optional filters that veto almost everything: {offenders}"


def test_declared_exclusions_are_enforced_including_filters():
    """`exclusive` was checked against signal names only, then filter sets were
    appended with no further check - so any declared pair naming a FILTER was
    decoration. VWAP declared above_vwap and vwap_proximity mutually exclusive
    and 25 of 400 generated strategies held both."""
    from futures_agents.strategies.combinator import (TEMPLATES_BY_GROUP,
                                                      generate_combinations)
    offenders = []
    for group, template in TEMPLATES_BY_GROUP.items():
        if not template.exclusive:
            continue
        for spec in generate_combinations("MNQ", [5, 15, 60], groups=[group],
                                          max_total=400, seed=1):
            names = set(spec.signal_conditions) | set(spec.filter_conditions)
            for pair in template.exclusive:
                if len(names & set(pair)) > 1:
                    offenders.append((group, pair))
    assert not offenders, f"declared exclusions not enforced: {sorted(set(offenders))}"


def test_every_non_generated_exemption_is_justified():
    """An exemption without a reason is indistinguishable from an oversight,
    which is precisely the blind spot the reachability check exists to close."""
    from futures_agents.strategies.coverage import NOT_GENERATED
    for name, reason in NOT_GENERATED.items():
        assert name in CONDITIONS, f"{name} is exempted but not registered"
        assert len(reason) > 40, f"{name}'s exemption has no real reason"
