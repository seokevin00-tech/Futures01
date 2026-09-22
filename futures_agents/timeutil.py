"""Eastern-Time handling, session classification and timestamp formatting.

Futures sessions are defined in US/Eastern, so every timestamp the system
displays or reasons about is normalised here. The module deliberately uses
``zoneinfo`` rather than a fixed -05:00 offset: a hard-coded EST offset is
silently wrong for the ~34 weeks a year that New York is on EDT, which would
shift every session boundary and every economic-release time by an hour.

``ET_LABEL`` resolves to the correct abbreviation ("EST" or "EDT") for the
instant being formatted, so a stamp is never mislabelled.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from typing import Iterable, Optional, Tuple
from zoneinfo import ZoneInfo

ET = ZoneInfo("America/New_York")
UTC = timezone.utc

__all__ = [
    "ET", "UTC", "now_et", "to_et", "et_stamp", "et_stamp_short", "et_label",
    "Session", "classify_session", "session_of", "minutes_since_open",
    "is_rth", "parse_hhmm", "rth_bounds", "trading_day", "day_of_week_name",
    "time_bucket", "MARKET_HOLIDAYS_2025_2027", "is_market_holiday",
]


# --------------------------------------------------------------------------
# Core conversion + formatting
# --------------------------------------------------------------------------

def now_et() -> datetime:
    """Current wall-clock time in US/Eastern, timezone-aware."""
    return datetime.now(tz=ET)


def to_et(dt: datetime) -> datetime:
    """Normalise any datetime to US/Eastern.

    Naive datetimes are assumed to already be Eastern (the convention used by
    every CSV the loader ingests); aware datetimes are converted.
    """
    if dt.tzinfo is None:
        return dt.replace(tzinfo=ET)
    return dt.astimezone(ET)


def et_label(dt: Optional[datetime] = None) -> str:
    """"EST" or "EDT" - whichever is actually in force at ``dt``."""
    d = to_et(dt or now_et())
    return d.tzname() or "ET"


def et_stamp(dt: Optional[datetime] = None, *, with_date: bool = True,
             with_seconds: bool = True) -> str:
    """The canonical timestamp prefix used in front of every alert and response.

    Example: ``2026-09-22 14:37:05 EDT (Mon)``
    """
    d = to_et(dt or now_et())
    fmt = "%Y-%m-%d %H:%M:%S" if with_seconds else "%Y-%m-%d %H:%M"
    if not with_date:
        fmt = fmt.split(" ", 1)[1]
    return f"{d.strftime(fmt)} {et_label(d)} ({d.strftime('%a')})"


def et_stamp_short(dt: Optional[datetime] = None) -> str:
    """Compact stamp for table rows: ``14:37:05 EDT``."""
    d = to_et(dt or now_et())
    return f"{d.strftime('%H:%M:%S')} {et_label(d)}"


def parse_hhmm(value: str) -> time:
    """Parse ``"09:30"`` into a :class:`datetime.time`."""
    hh, mm = value.split(":")
    return time(hour=int(hh), minute=int(mm))


# --------------------------------------------------------------------------
# Sessions
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class Session:
    """A named intraday session window, in Eastern Time."""

    name: str
    start: time
    end: time
    description: str = ""

    def contains(self, t: time) -> bool:
        if self.start <= self.end:
            return self.start <= t < self.end
        # Window wraps past midnight (overnight sessions).
        return t >= self.start or t < self.end


#: Session map used by every per-session statistic in the system. These windows
#: are behaviourally distinct for US index and commodity futures: liquidity,
#: volatility and mean-reversion characteristics differ sharply between them.
SESSIONS: Tuple[Session, ...] = (
    Session("ASIA", time(18, 0), time(3, 0),
            "Globex reopen through Tokyo; thin, range-prone"),
    Session("LONDON", time(3, 0), time(8, 0),
            "European cash open; first real volume of the day"),
    Session("PRE_MARKET", time(8, 0), time(9, 30),
            "US pre-market; economic releases at 08:30 land here"),
    Session("RTH_OPEN", time(9, 30), time(10, 30),
            "Opening drive; highest volume and widest ranges"),
    Session("RTH_MORNING", time(10, 30), time(12, 0),
            "Morning trend or first reversal"),
    Session("LUNCH", time(12, 0), time(13, 30),
            "Midday lull; breakouts fail disproportionately here"),
    Session("RTH_AFTERNOON", time(13, 30), time(15, 0),
            "Afternoon trend resumption; 14:00 FOMC slot"),
    Session("RTH_CLOSE", time(15, 0), time(16, 0),
            "Closing imbalance and MOC flow"),
    Session("POST_CLOSE", time(16, 0), time(18, 0),
            "Post-settlement; includes the 17:00-18:00 maintenance break"),
)


def classify_session(dt: datetime) -> str:
    """Name of the session containing ``dt``."""
    t = to_et(dt).time()
    for s in SESSIONS:
        if s.contains(t):
            return s.name
    return "UNKNOWN"


def session_of(dt: datetime) -> Session:
    t = to_et(dt).time()
    for s in SESSIONS:
        if s.contains(t):
            return s
    return SESSIONS[0]


def is_rth(dt: datetime, open_hhmm: str = "09:30", close_hhmm: str = "16:00") -> bool:
    """Whether ``dt`` falls inside the contract's regular trading hours."""
    t = to_et(dt).time()
    return parse_hhmm(open_hhmm) <= t < parse_hhmm(close_hhmm)


def rth_bounds(day: date, open_hhmm: str = "09:30",
               close_hhmm: str = "16:00") -> Tuple[datetime, datetime]:
    """(open, close) datetimes for a calendar day, in Eastern Time."""
    o = datetime.combine(day, parse_hhmm(open_hhmm), tzinfo=ET)
    c = datetime.combine(day, parse_hhmm(close_hhmm), tzinfo=ET)
    return o, c


def minutes_since_open(dt: datetime, open_hhmm: str = "09:30") -> float:
    """Minutes elapsed since the RTH open. Negative before the open.

    Opening-range and time-of-day statistics key off this value, so it is
    computed from the contract's own open rather than a global 09:30.
    """
    d = to_et(dt)
    o = datetime.combine(d.date(), parse_hhmm(open_hhmm), tzinfo=ET)
    return (d - o).total_seconds() / 60.0


def trading_day(dt: datetime) -> date:
    """The trading date a bar belongs to.

    The CME session runs 18:00 ET through 17:00 ET the next day, so an 18:30 ET
    Sunday bar belongs to Monday's trading day. Getting this wrong smears
    overnight highs/lows into the wrong session and corrupts every ONH/ONL and
    previous-day statistic downstream.
    """
    d = to_et(dt)
    if d.time() >= time(18, 0):
        return (d + timedelta(days=1)).date()
    return d.date()


def day_of_week_name(dt: datetime) -> str:
    return to_et(dt).strftime("%A").upper()


def time_bucket(dt: datetime, minutes: int = 30) -> str:
    """Coarse time-of-day bucket label, e.g. ``"09:30-10:00"``.

    Used to slice strategy performance by time of day without creating a
    separate statistic for every minute of the session.
    """
    d = to_et(dt)
    total = d.hour * 60 + d.minute
    start = (total // minutes) * minutes
    end = start + minutes
    return f"{start // 60:02d}:{start % 60:02d}-{(end // 60) % 24:02d}:{end % 60:02d}"


# --------------------------------------------------------------------------
# Holidays
# --------------------------------------------------------------------------

#: US equity-market full holidays. Sessions on these dates have no RTH and are
#: excluded from time-of-day statistics so a handful of 09:30 closures cannot
#: masquerade as a low-volatility regime.
MARKET_HOLIDAYS_2025_2027 = frozenset({
    date(2025, 1, 1), date(2025, 1, 20), date(2025, 2, 17), date(2025, 4, 18),
    date(2025, 5, 26), date(2025, 6, 19), date(2025, 7, 4), date(2025, 9, 1),
    date(2025, 11, 27), date(2025, 12, 25),
    date(2026, 1, 1), date(2026, 1, 19), date(2026, 2, 16), date(2026, 4, 3),
    date(2026, 5, 25), date(2026, 6, 19), date(2026, 7, 3), date(2026, 9, 7),
    date(2026, 11, 26), date(2026, 12, 25),
    date(2027, 1, 1), date(2027, 1, 18), date(2027, 2, 15), date(2027, 3, 26),
    date(2027, 5, 31), date(2027, 6, 18), date(2027, 7, 5), date(2027, 9, 6),
    date(2027, 11, 25), date(2027, 12, 24),
})


def is_market_holiday(day: date) -> bool:
    return day in MARKET_HOLIDAYS_2025_2027
