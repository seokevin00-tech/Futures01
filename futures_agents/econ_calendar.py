"""A rule-based economic calendar for the deterministic core.

The point of this module is that **news conditions must be backtestable**. The
specification lists "news conditions" as a testable variable alongside VWAP and
RSI, which means a strategy has to be able to ask "was a high-impact release
imminent?" at any historical bar - not just at the live edge.

Scraped headlines cannot answer that question for the past without look-ahead:
you would be reading an article written after the bar. Recurrence *rules* can.
US macro releases follow fixed schedules - CPI on a business day near the
middle of the month at 08:30 ET, payrolls on the first Friday, FOMC on a known
meeting pattern - so the projection is deterministic, reproducible, and
identical whether computed for last year or for tomorrow.

This is a deliberately coarse calendar. It knows *when* a release lands and how
hard it usually hits, not what it said. That is exactly the granularity a
strategy filter needs: "do not hold through the 08:30 print" is a rule you can
test, whereas "was CPI hot" is not knowable from a rule.

The news agent maintains a richer calendar for live use, with actuals,
forecasts and measured reactions. This one exists so the *backtester* can
condition on event proximity without importing an agent.
"""

from __future__ import annotations

import calendar as _calendar
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from enum import Enum
from typing import Dict, Iterable, List, Optional, Sequence, Tuple

from .timeutil import ET, is_market_holiday, to_et

__all__ = ["Impact", "EconEvent", "CalendarRule", "ECON_RULES",
           "project_events", "minutes_to_next_event", "event_proximity"]


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
    pattern: str                   # "weekly" | "nth_weekday" | "business_day" | "fomc"
    weekday: Optional[int] = None  # 0=Mon .. 4=Fri
    nth: Optional[int] = None      # 1-based; -1 means last
    business_day: Optional[int] = None
    months: Tuple[int, ...] = tuple(range(1, 13))
    #: Which way to move when the projected date is a market holiday. Claims
    #: and payrolls are published *earlier* in a holiday week; most others slip
    #: later.
    holiday_shift: int = 1


#: The releases that actually move US futures. Deliberately short: a calendar
#: nobody can audit is worse than a small one that is right.
ECON_RULES: Tuple[CalendarRule, ...] = (
    CalendarRule("US CPI", "CPI", Impact.HIGH, time(8, 30), "business_day",
                 business_day=10),
    CalendarRule("US PPI", "PPI", Impact.MEDIUM, time(8, 30), "business_day",
                 business_day=11),
    CalendarRule("US Nonfarm Payrolls", "NFP", Impact.HIGH, time(8, 30),
                 "nth_weekday", weekday=4, nth=1, holiday_shift=-1),
    CalendarRule("US Initial Jobless Claims", "CLAIMS", Impact.MEDIUM,
                 time(8, 30), "weekly", weekday=3, holiday_shift=-1),
    CalendarRule("US Retail Sales", "RETAIL", Impact.MEDIUM, time(8, 30),
                 "business_day", business_day=12),
    CalendarRule("US GDP", "GDP", Impact.MEDIUM, time(8, 30), "business_day",
                 business_day=18, months=(1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12)),
    CalendarRule("US Core PCE", "PCE", Impact.HIGH, time(8, 30), "business_day",
                 business_day=-1),
    CalendarRule("ISM Manufacturing", "ISM", Impact.MEDIUM, time(10, 0),
                 "business_day", business_day=1),
    CalendarRule("FOMC Statement", "FOMC", Impact.HIGH, time(14, 0), "fomc"),
    CalendarRule("FOMC Press Conference", "FOMC", Impact.HIGH, time(14, 30), "fomc"),
)

#: FOMC meeting pattern: last Wednesday of Jan/Apr/Jul/Oct, third of
#: Mar/Jun/Sep, second of Dec. Reproduces the published 2026 schedule exactly.
_FOMC_PATTERN: Dict[int, int] = {1: -1, 3: 3, 4: -1, 6: 3, 7: -1, 9: 3, 10: -1, 12: 2}


def _business_days(year: int, month: int) -> List[date]:
    days = []
    for day in range(1, _calendar.monthrange(year, month)[1] + 1):
        d = date(year, month, day)
        if d.weekday() < 5 and not is_market_holiday(d):
            days.append(d)
    return days


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


def _shift_off_holiday(d: date, direction: int) -> date:
    for _ in range(7):
        if d.weekday() < 5 and not is_market_holiday(d):
            return d
        d = d + timedelta(days=direction)
    return d


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
    elif rule.pattern == "business_day":
        days = _business_days(year, month)
        if days:
            idx = rule.business_day or 1
            pick = days[idx - 1] if idx > 0 else days[idx]
            out.append(pick if 0 <= abs(idx) - 1 < len(days) else days[-1])
    elif rule.pattern == "fomc":
        nth = _FOMC_PATTERN.get(month)
        if nth is not None:
            d = _nth_weekday(year, month, 2, nth)      # Wednesday
            if d:
                out.append(_shift_off_holiday(d, 1))
    return [d for d in out if d.month == month]


def project_events(start: datetime, end: datetime,
                   rules: Sequence[CalendarRule] = ECON_RULES) -> List[EconEvent]:
    """Every projected release between ``start`` and ``end``, Eastern Time."""
    s, e = to_et(start), to_et(end)
    if e < s:
        return []
    out: List[EconEvent] = []
    year, month = s.year, s.month
    while (year, month) <= (e.year, e.month):
        for rule in rules:
            for d in _rule_dates(rule, year, month):
                when = datetime.combine(d, rule.at, tzinfo=ET)
                if s <= when <= e:
                    out.append(EconEvent(rule.name, rule.category, rule.impact, when))
        month += 1
        if month > 12:
            month, year = 1, year + 1
    out.sort(key=lambda ev: ev.when)
    return out


def minutes_to_next_event(when: datetime, *, min_impact: Impact = Impact.HIGH,
                          horizon_hours: int = 72) -> Optional[float]:
    """Minutes from ``when`` to the next release at or above ``min_impact``.

    Returns ``None`` when nothing qualifies inside the horizon.
    """
    start = to_et(when)
    events = [ev for ev in project_events(start, start + timedelta(hours=horizon_hours))
              if ev.impact.rank >= min_impact.rank]
    if not events:
        return None
    return (events[0].when - start).total_seconds() / 60.0


def event_proximity(when: datetime, *, before_min: int = 10, after_min: int = 5,
                    min_impact: Impact = Impact.HIGH) -> Tuple[float, bool]:
    """``(minutes_to_next, inside_blackout)`` for one instant.

    ``inside_blackout`` is the window a strategy must not initiate inside -
    ``before_min`` ahead of a qualifying release through ``after_min`` past it.
    Computed from rules only, so it is identical in a backtest and live.
    """
    start = to_et(when)
    window = project_events(start - timedelta(minutes=after_min + 1),
                            start + timedelta(hours=72))
    qualifying = [ev for ev in window if ev.impact.rank >= min_impact.rank]
    if not qualifying:
        return float("inf"), False

    blackout = any(
        -after_min <= (start - ev.when).total_seconds() / 60.0 <= before_min
        for ev in qualifying)
    ahead = [ev for ev in qualifying if ev.when >= start]
    minutes = ((ahead[0].when - start).total_seconds() / 60.0
               if ahead else float("inf"))
    return minutes, blackout
