"""A rule-based economic calendar for the deterministic core.

The point of this module is that **news conditions must be backtestable**. The
specification lists "news conditions" as a testable variable alongside VWAP and
RSI, which means a strategy has to be able to ask "was a high-impact release
imminent?" at any historical bar - not just at the live edge.

Scraped headlines cannot answer that question for the past without look-ahead:
you would be reading an article written after the bar. Recurrence *rules* can.
US macro releases follow fixed schedules - CPI on a business day near the
middle of the month at 08:30 ET, the Employment Situation on the third Friday
after the reference week, FOMC on a published meeting schedule - so the
projection is deterministic, reproducible, and identical whether computed for
last year or for tomorrow.

This is a deliberately coarse calendar. It knows *when* a release lands and how
hard it usually hits, not what it said. That is exactly the granularity a
strategy filter needs: "do not hold through the 08:30 print" is a rule you can
test, whereas "was CPI hot" is not knowable from a rule.

Two things the rules have to get right that a naive version does not.

*Business days are counted on the FEDERAL holiday calendar, not the exchange
one.* BLS, BEA and Census publish on Good Friday - a market holiday, not a
federal one - and are closed on Columbus Day and Veterans Day, which the
futures market trades straight through. Counting exchange holidays moves every
business-day rule in April, October and November. The worked example is the
April 2026 Employment Situation: BLS published on Good Friday, 3 April 2026,
and CME opened an abbreviated equity-index session around it. Using
``timeutil.is_market_holiday`` also disables holiday handling entirely before
2025, because that table only covers 2025-2027.

*The FOMC is a published schedule, not an arithmetic pattern.* The Committee
fixes its dates by vote and no nth-weekday rule reproduces them: measured
against the published 2019-2027 calendars, the pattern below covers 52 of 72
decisions and invents 20 that never happened. The verified dates are therefore
stored, and the pattern survives only as the fallback outside that range.

The news agent maintains a richer calendar for live use, with actuals,
forecasts and measured reactions. This one exists so the *backtester* can
condition on event proximity without importing an agent.
"""

from __future__ import annotations

import calendar as _calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Dict, Iterable, List, Optional, Sequence, Set, Tuple

from .timeutil import ET, to_et

__all__ = ["Impact", "EconEvent", "CalendarRule", "ECON_RULES",
           "project_events", "minutes_to_next_event", "event_proximity",
           "is_federal_holiday"]


class Impact(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"

    @property
    def rank(self) -> int:
        return {Impact.HIGH: 3, Impact.MEDIUM: 2, Impact.LOW: 1}[self]


@dataclass(frozen=True)
class EconEvent:
    """One projected release."""

    name: str
    category: str
    impact: Impact
    when: datetime          # Eastern Time

    def to_dict(self) -> dict:
        return {"name": self.name, "category": self.category,
                "impact": self.impact.value, "when_et": self.when.isoformat()}


@dataclass(frozen=True)
class CalendarRule:
    """A recurrence rule, not a stored date.

    Storing dates means the calendar silently goes stale the moment it leaves
    the year it was written in. A rule projects forwards and backwards
    indefinitely, which is what makes historical conditioning possible.
    """

    name: str
    category: str
    impact: Impact
    at: time                       # release time, Eastern
    #: "weekly" | "nth_weekday" | "business_day" | "empsit" | "eia_weekly"
    #: | "fomc" | "fomc_presser"
    pattern: str
    weekday: Optional[int] = None  # 0=Mon .. 4=Fri
    nth: Optional[int] = None      # 1-based; -1 means last
    business_day: Optional[int] = None
    months: Tuple[int, ...] = tuple(range(1, 13))
    #: Which way to move when the projected date is a *federal* holiday. Claims
    #: and payrolls are published earlier in a holiday week - Thanksgiving-week
    #: claims print on the Wednesday, and the July 2026 payroll report moved to
    #: Thursday 2 July for the Friday 3 July holiday. Most others slip later.
    holiday_shift: int = 1
    #: Contracts this release is scheduled for. Empty means market-wide. A
    #: scoped rule only projects when ``project_events`` is given a matching
    #: ``symbol``, so crude inventories cannot black out MNQ.
    symbols: Tuple[str, ...] = ()


# --------------------------------------------------------------------------
# Federal holidays - the calendar statistical agencies actually close on
# --------------------------------------------------------------------------

def _nth_weekday(year: int, month: int, weekday: int, nth: int) -> Optional[date]:
    matches = [date(year, month, day)
               for day in range(1, _calendar.monthrange(year, month)[1] + 1)
               if date(year, month, day).weekday() == weekday]
    if not matches:
        return None
    try:
        return matches[nth - 1] if nth > 0 else matches[nth]
    except IndexError:
        return None


def _observed(d: date) -> date:
    """Saturday holidays are observed on the Friday, Sunday ones on the Monday."""
    if d.weekday() == 5:
        return d - timedelta(days=1)
    if d.weekday() == 6:
        return d + timedelta(days=1)
    return d


def _federal_holidays(year: int) -> Set[date]:
    out = {
        _observed(date(year, 1, 1)),
        _nth_weekday(year, 1, 0, 3),             # Martin Luther King Jr. Day
        _nth_weekday(year, 2, 0, 3),             # Washington's Birthday
        _nth_weekday(year, 5, 0, -1),            # Memorial Day
        _observed(date(year, 7, 4)),
        _nth_weekday(year, 9, 0, 1),             # Labor Day
        _nth_weekday(year, 10, 0, 2),            # Columbus Day
        _observed(date(year, 11, 11)),           # Veterans Day
        _nth_weekday(year, 11, 3, 4),            # Thanksgiving
        _observed(date(year, 12, 25)),
    }
    if year >= 2021:                             # Juneteenth, Public Law 117-17
        out.add(_observed(date(year, 6, 19)))
    nyd = _observed(date(year + 1, 1, 1))        # 1 Jan observed on 31 Dec
    if nyd.year == year:
        out.add(nyd)
    return {d for d in out if d is not None}


_HOLIDAY_CACHE: Dict[int, Set[date]] = {}


def is_federal_holiday(day: date) -> bool:
    """True on a US federal holiday - the day an agency does not publish.

    Deliberately *not* :func:`timeutil.is_market_holiday`. That one answers
    "was the exchange shut", which is a different question with different
    answers on Good Friday, Columbus Day and Veterans Day, and which is only
    populated for 2025-2027.
    """
    if day.year not in _HOLIDAY_CACHE:
        _HOLIDAY_CACHE[day.year] = _federal_holidays(day.year)
    return day in _HOLIDAY_CACHE[day.year]


#: The releases that actually move US futures. Deliberately short: a calendar
#: nobody can audit is worse than a small one that is right. The business-day
#: indices are fitted to published 2024-2026 release dates, not guessed - the
#: per-rule error measurement is in research/confluence/news_macro.md.
ECON_RULES: Tuple[CalendarRule, ...] = (
    # CPI landed on federal business day 8 in 20 of the 25 undisrupted
    # releases published over 2024-2026 (observed range 7-10). Business day 10
    # was right 3 times in 26 and ran a median 2 days late.
    CalendarRule("US CPI", "CPI", Impact.HIGH, time(8, 30), "business_day",
                 business_day=8),
    # PPI landed on business day 9 more often than any other index over the
    # same window (7 of 15). Business day 11 was right twice in 20.
    CalendarRule("US PPI", "PPI", Impact.MEDIUM, time(8, 30), "business_day",
                 business_day=9),
    # BLS schedules the Employment Situation for the third Friday after the
    # conclusion of the reference week - the week containing the 12th. That is
    # usually the first Friday of the month, which is where the folklore comes
    # from, but it is the second Friday whenever the 12th falls early in its
    # week (8 March 2024, 10 January 2025, 8 May 2026).
    CalendarRule("US Nonfarm Payrolls", "NFP", Impact.HIGH, time(8, 30),
                 "empsit", holiday_shift=-1),
    CalendarRule("US Initial Jobless Claims", "CLAIMS", Impact.MEDIUM,
                 time(8, 30), "weekly", weekday=3, holiday_shift=-1),
    CalendarRule("US Retail Sales", "RETAIL", Impact.MEDIUM, time(8, 30),
                 "business_day", business_day=11),
    # One BEA GDP estimate lands every month - advance, second and third in
    # rotation - near month end, not on business day 18.
    CalendarRule("US GDP", "GDP", Impact.MEDIUM, time(8, 30), "business_day",
                 business_day=-2),
    # BEA re-cut the Personal Income and Outlays schedule for 2026, so no one
    # business-day index fits both eras; the last business day is the best
    # single approximation and is still wrong about half the time.
    CalendarRule("US Core PCE", "PCE", Impact.HIGH, time(8, 30), "business_day",
                 business_day=-1),
    CalendarRule("ISM Manufacturing", "ISM", Impact.MEDIUM, time(10, 0),
                 "business_day", business_day=1),
    CalendarRule("FOMC Statement", "FOMC", Impact.HIGH, time(14, 0), "fomc"),
    CalendarRule("FOMC Press Conference", "FOMC", Impact.HIGH, time(14, 30),
                 "fomc_presser"),
    # Crude only. The EIA Weekly Petroleum Status Report is the largest
    # scheduled mover of WTI and was invisible to a calendar built for equity
    # indices. Scoped by symbol so it never blacks out MNQ or MES.
    CalendarRule("EIA Crude Oil Inventories", "EIA", Impact.HIGH, time(10, 30),
                 "eia_weekly", symbols=("MCL", "CL", "MNG", "NG")),
)


#: Published FOMC decision days - the second day of each scheduled meeting,
#: when the statement goes out at 14:00 ET. Verified against the Federal
#: Reserve's meeting calendars and per-meeting minutes and press-conference
#: pages. Two 2020 exceptions the table cannot express: the scheduled 17-18
#: March meeting was cancelled after the emergency Sunday action of 15 March,
#: and the 3 March intermeeting cut was announced at 10:00 ET.
_FOMC_DECISION_DAYS: Dict[int, Tuple[date, ...]] = {
    2019: (date(2019, 1, 30), date(2019, 3, 20), date(2019, 5, 1),
           date(2019, 6, 19), date(2019, 7, 31), date(2019, 9, 18),
           date(2019, 10, 30), date(2019, 12, 11)),
    2020: (date(2020, 1, 29), date(2020, 3, 18), date(2020, 4, 29),
           date(2020, 6, 10), date(2020, 7, 29), date(2020, 9, 16),
           date(2020, 11, 5), date(2020, 12, 16)),
    2021: (date(2021, 1, 27), date(2021, 3, 17), date(2021, 4, 28),
           date(2021, 6, 16), date(2021, 7, 28), date(2021, 9, 22),
           date(2021, 11, 3), date(2021, 12, 15)),
    2022: (date(2022, 1, 26), date(2022, 3, 16), date(2022, 5, 4),
           date(2022, 6, 15), date(2022, 7, 27), date(2022, 9, 21),
           date(2022, 11, 2), date(2022, 12, 14)),
    2023: (date(2023, 2, 1), date(2023, 3, 22), date(2023, 5, 3),
           date(2023, 6, 14), date(2023, 7, 26), date(2023, 9, 20),
           date(2023, 11, 1), date(2023, 12, 13)),
    2024: (date(2024, 1, 31), date(2024, 3, 20), date(2024, 5, 1),
           date(2024, 6, 12), date(2024, 7, 31), date(2024, 9, 18),
           date(2024, 11, 7), date(2024, 12, 18)),
    2025: (date(2025, 1, 29), date(2025, 3, 19), date(2025, 5, 7),
           date(2025, 6, 18), date(2025, 7, 30), date(2025, 9, 17),
           date(2025, 10, 29), date(2025, 12, 10)),
    2026: (date(2026, 1, 28), date(2026, 3, 18), date(2026, 4, 29),
           date(2026, 6, 17), date(2026, 7, 29), date(2026, 9, 16),
           date(2026, 10, 28), date(2026, 12, 9)),
    2027: (date(2027, 1, 27), date(2027, 3, 17), date(2027, 4, 28),
           date(2027, 6, 9), date(2027, 7, 28), date(2027, 9, 15),
           date(2027, 10, 27), date(2027, 12, 8)),
}

#: Fallback for years outside the published table: last Wednesday of
#: Jan/Apr/Jul/Oct, third of Mar/Jun/Sep, second of Dec. It reproduces 2026
#: exactly, which is how it got written, and only 3 of 8 meetings in 2023.
#: Good enough as a degraded default, not good enough to be the source.
_FOMC_PATTERN: Dict[int, int] = {1: -1, 3: 3, 4: -1, 6: 3, 7: -1, 9: 3, 10: -1, 12: 2}

#: The Chair has held a press conference after *every* meeting since the
#: 30 January 2019 FOMC. Before that they followed only the meetings carrying a
#: Summary of Economic Projections. That earlier subset is not modelled, so a
#: pre-2019 backtest sees the 14:00 statement and no 14:30 event - an
#: acknowledged gap rather than a guess.
_PRESS_CONFERENCE_FROM = date(2019, 1, 30)


def _business_days(year: int, month: int) -> List[date]:
    """Federal business days in a month, in order."""
    days = []
    for day in range(1, _calendar.monthrange(year, month)[1] + 1):
        d = date(year, month, day)
        if d.weekday() < 5 and not is_federal_holiday(d):
            days.append(d)
    return days


def _shift_off_holiday(d: date, direction: int) -> date:
    for _ in range(7):
        if d.weekday() < 5 and not is_federal_holiday(d):
            return d
        d = d + timedelta(days=direction)
    return d


def _empsit_date(year: int, month: int) -> date:
    """The Employment Situation released in ``(year, month)``.

    Third Friday after the conclusion of the reference week, where the
    reference week is the Sunday-to-Saturday week containing the 12th of the
    month being reported on.
    """
    ry, rm = (year, month - 1) if month > 1 else (year - 1, 12)
    twelfth = date(ry, rm, 12)
    sat = twelfth + timedelta(days=(5 - twelfth.weekday()) % 7)
    d = sat
    for _ in range(3):
        d += timedelta(days=(4 - d.weekday()) % 7 or 7)
    if d.month == 1 and d.day <= 3:
        # The New Year week compresses processing and BLS slips a week:
        # December 2024 data went out on 10 January 2025, December 2025 data on
        # 9 January 2026, both a week after the arithmetic date.
        d += timedelta(days=7)
    return _shift_off_holiday(d, -1)


def _eia_dates(year: int, month: int) -> List[date]:
    """Weekly Petroleum Status Report: Wednesdays at 10:30 ET, a day later in a
    week that contains a federal holiday."""
    out: List[date] = []
    for day in range(1, _calendar.monthrange(year, month)[1] + 1):
        d = date(year, month, day)
        if d.weekday() != 2:                       # Wednesday
            continue
        monday = d - timedelta(days=2)
        if any(is_federal_holiday(monday + timedelta(days=i)) for i in range(3)):
            d = d + timedelta(days=1)
        out.append(d)
    return out


def _fomc_decision_days(year: int, month: int) -> List[date]:
    published = _FOMC_DECISION_DAYS.get(year)
    if published is not None:
        return [d for d in published if d.month == month]
    nth = _FOMC_PATTERN.get(month)
    if nth is None:
        return []
    d = _nth_weekday(year, month, 2, nth)          # Wednesday
    return [_shift_off_holiday(d, 1)] if d else []


def _rule_dates(rule: CalendarRule, year: int, month: int) -> List[date]:
    if rule.months and month not in rule.months:
        return []
    out: List[date] = []
    if rule.pattern == "weekly":
        for day in range(1, _calendar.monthrange(year, month)[1] + 1):
            d = date(year, month, day)
            if d.weekday() == rule.weekday:
                out.append(_shift_off_holiday(d, rule.holiday_shift))
    elif rule.pattern == "nth_weekday":
        d = _nth_weekday(year, month, rule.weekday or 0, rule.nth or 1)
        if d:
            out.append(_shift_off_holiday(d, rule.holiday_shift))
    elif rule.pattern == "empsit":
        out.append(_empsit_date(year, month))
    elif rule.pattern == "eia_weekly":
        out.extend(_eia_dates(year, month))
    elif rule.pattern == "business_day":
        days = _business_days(year, month)
        if days:
            idx = rule.business_day or 1
            # Bounds-check before indexing. The old form evaluated
            # ``days[idx - 1]`` first, so any rule past the shortest month's
            # business-day count raised IndexError instead of clamping.
            if idx > 0:
                out.append(days[idx - 1] if idx <= len(days) else days[-1])
            else:
                out.append(days[idx] if -idx <= len(days) else days[0])
    elif rule.pattern in ("fomc", "fomc_presser"):
        for d in _fomc_decision_days(year, month):
            if rule.pattern == "fomc_presser" and d < _PRESS_CONFERENCE_FROM:
                continue
            out.append(d)
    if rule.pattern in ("weekly", "eia_weekly", "empsit"):
        # A weekly release shifted backwards off a holiday can land in the
        # previous month. Filtering on ``month`` there deletes that week's
        # event outright - the calendar goes quiet on exactly the weeks it
        # should not.
        return out
    return [d for d in out if d.month == month]


def project_events(start: datetime, end: datetime,
                   rules: Sequence[CalendarRule] = ECON_RULES,
                   *, symbol: Optional[str] = None) -> List[EconEvent]:
    """Every projected release between ``start`` and ``end``, Eastern Time.

    ``symbol`` opts into the symbol-scoped rules - EIA inventories for crude.
    Left at ``None`` only the market-wide rules project, which is what an
    index-futures caller wants and what every existing caller gets.
    """
    s, e = to_et(start), to_et(end)
    if e < s:
        return []
    sym = (symbol or "").upper()
    active = [r for r in rules if not r.symbols or sym in r.symbols]
    out: List[EconEvent] = []
    seen: Set[Tuple[str, datetime]] = set()
    year, month = s.year, s.month
    while (year, month) <= (e.year, e.month):
        for rule in active:
            for d in _rule_dates(rule, year, month):
                when = datetime.combine(d, rule.at, tzinfo=ET)
                if s <= when <= e and (rule.name, when) not in seen:
                    seen.add((rule.name, when))
                    out.append(EconEvent(rule.name, rule.category, rule.impact, when))
        month += 1
        if month > 12:
            month, year = 1, year + 1
    out.sort(key=lambda ev: ev.when)
    return out


def minutes_to_next_event(when: datetime, *, min_impact: Impact = Impact.HIGH,
                          horizon_hours: int = 72,
                          symbol: Optional[str] = None) -> Optional[float]:
    """Minutes from ``when`` to the next release at or above ``min_impact``.

    Returns ``None`` when nothing qualifies inside the horizon.
    """
    start = to_et(when)
    events = [ev for ev in project_events(start, start + timedelta(hours=horizon_hours),
                                          symbol=symbol)
              if ev.impact.rank >= min_impact.rank]
    if not events:
        return None
    return (events[0].when - start).total_seconds() / 60.0


#: Forward horizon for "when is the next release". Long enough that the gap
#: between two high-impact prints always fits inside it: CPI, payrolls, PCE and
#: FOMC are monthly, so a 72-hour horizon answers "nothing ahead" for most of
#: every month, and a caller reads that as a quiet calendar rather than as a
#: horizon artefact.
PROJECTION_DAYS: int = 45


def event_proximity(when: datetime, *, before_min: int = 10, after_min: int = 5,
                    min_impact: Impact = Impact.HIGH,
                    symbol: Optional[str] = None) -> Tuple[float, bool]:
    """``(minutes_to_next, inside_blackout)`` for one instant.

    ``inside_blackout`` is the window a strategy must not initiate inside -
    ``before_min`` ahead of a qualifying release through ``after_min`` past it.
    Computed from rules only, so it is identical in a backtest and live.

    Note the sign convention: ``start - ev.when`` is *negative* before the
    event, so the window is ``[-before_min, +after_min]``. Written the other
    way round it blacks out ``after_min`` ahead of the print and ``before_min``
    past it - the mirror image of what the risk config asks for, and it leaves
    the minutes immediately before an 08:30 release open for business.
    """
    start = to_et(when)
    # The lookback has to cover the whole post-event half of the blackout, or
    # the window is silently clipped to however far back the projection reaches.
    window = project_events(start - timedelta(minutes=after_min + 1),
                            start + timedelta(days=PROJECTION_DAYS),
                            symbol=symbol)
    qualifying = [ev for ev in window if ev.impact.rank >= min_impact.rank]
    if not qualifying:
        return float("inf"), False

    blackout = any(
        -before_min <= (start - ev.when).total_seconds() / 60.0 <= after_min
        for ev in qualifying)
    ahead = [ev for ev in qualifying if ev.when >= start]
    minutes = ((ahead[0].when - start).total_seconds() / 60.0
               if ahead else float("inf"))
    return minutes, blackout
