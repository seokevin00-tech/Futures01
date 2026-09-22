"""Eastern-Time handling: DST correctness and session boundaries.

A hard-coded -05:00 offset is wrong for roughly 34 weeks a year. Every session
boundary, every economic-release time and every trading-day roll would be an
hour out for two thirds of the calendar, so these are the first tests in the
suite rather than an afterthought.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta, timezone

import pytest

from futures_agents.timeutil import (ET, UTC, classify_session, day_of_week_name,
                                     et_label, et_stamp, et_stamp_short, is_rth,
                                     is_market_holiday, minutes_since_open,
                                     parse_hhmm, rth_bounds, session_of,
                                     time_bucket, to_et, trading_day)


# --------------------------------------------------------------------------
# Daylight saving
# --------------------------------------------------------------------------

def test_january_is_est_and_july_is_edt():
    """The single fact a fixed -05:00 offset gets wrong."""
    jan = datetime(2026, 1, 15, 12, 0, tzinfo=ET)
    jul = datetime(2026, 7, 15, 12, 0, tzinfo=ET)
    assert et_label(jan) == "EST"
    assert et_label(jul) == "EDT"
    assert jan.utcoffset() == timedelta(hours=-5)
    assert jul.utcoffset() == timedelta(hours=-4)


def test_stamp_carries_the_offset_actually_in_force():
    jan = datetime(2026, 1, 15, 9, 30, 0, tzinfo=ET)
    jul = datetime(2026, 7, 15, 9, 30, 0, tzinfo=ET)
    assert et_stamp(jan) == "2026-01-15 09:30:00 EST (Thu)"
    assert et_stamp(jul) == "2026-07-15 09:30:00 EDT (Wed)"
    assert et_stamp_short(jul) == "09:30:00 EDT"


@pytest.mark.parametrize("month,utc_hour,label", [
    (1, 14, "EST"),    # winter: cash open is 14:30 UTC
    (7, 13, "EDT"),    # summer: the same cash open is 13:30 UTC
])
def test_the_cash_open_is_a_different_utc_hour_in_each_half_of_the_year(
        month, utc_hour, label):
    """The concrete cost of a hard-coded offset: 09:30 ET is 14:30 UTC in
    January and 13:30 UTC in July."""
    et_dt = to_et(datetime(2026, month, 15, utc_hour, 30, tzinfo=UTC))
    assert (et_dt.hour, et_dt.minute) == (9, 30)
    assert et_label(et_dt) == label


def test_utc_conversion_in_winter_is_an_hour_different():
    """Same UTC instant, different ET wall clock across the DST boundary."""
    winter = to_et(datetime(2026, 1, 15, 14, 30, tzinfo=UTC))
    summer = to_et(datetime(2026, 7, 15, 14, 30, tzinfo=UTC))
    assert winter.hour == 9 and winter.minute == 30
    assert summer.hour == 10 and summer.minute == 30
    assert et_label(winter) == "EST"


def test_dst_transition_days_are_handled_by_zoneinfo():
    """2026 transitions: 08 Mar (spring forward), 01 Nov (fall back)."""
    before = datetime(2026, 3, 8, 1, 0, tzinfo=ET)
    after = datetime(2026, 3, 8, 3, 0, tzinfo=ET)
    assert et_label(before) == "EST"
    assert et_label(after) == "EDT"
    assert et_label(datetime(2026, 11, 1, 3, 0, tzinfo=ET)) == "EST"


def test_naive_datetimes_are_treated_as_eastern():
    naive = datetime(2026, 7, 15, 9, 30)
    assert to_et(naive).tzinfo is ET
    assert to_et(naive).hour == 9


# --------------------------------------------------------------------------
# Session classification
# --------------------------------------------------------------------------

@pytest.mark.parametrize("hh,mm,expected", [
    (18, 0, "ASIA"),          # Globex reopen - inclusive lower bound
    (23, 59, "ASIA"),
    (2, 59, "ASIA"),          # wraps past midnight
    (3, 0, "LONDON"),
    (7, 59, "LONDON"),
    (8, 0, "PRE_MARKET"),
    (9, 29, "PRE_MARKET"),
    (9, 30, "RTH_OPEN"),      # cash open - inclusive
    (10, 29, "RTH_OPEN"),
    (10, 30, "RTH_MORNING"),
    (11, 59, "RTH_MORNING"),
    (12, 0, "LUNCH"),
    (13, 29, "LUNCH"),
    (13, 30, "RTH_AFTERNOON"),
    (14, 59, "RTH_AFTERNOON"),
    (15, 0, "RTH_CLOSE"),
    (15, 59, "RTH_CLOSE"),
    (16, 0, "POST_CLOSE"),    # cash close - exclusive upper bound
    (17, 59, "POST_CLOSE"),
])
def test_session_boundaries_are_half_open(hh, mm, expected):
    """Every window is [start, end): the boundary minute belongs to the session
    that is opening, never to the one that just closed."""
    dt = datetime(2026, 7, 15, hh, mm, tzinfo=ET)
    assert classify_session(dt) == expected
    assert session_of(dt).name == expected


def test_every_minute_of_the_day_is_classified():
    """No gaps: an unclassified minute would silently drop bars out of every
    per-session statistic in the system."""
    base = datetime(2026, 7, 15, 0, 0, tzinfo=ET)
    for i in range(24 * 60):
        assert classify_session(base + timedelta(minutes=i)) != "UNKNOWN"


def test_sessions_classify_identically_in_est_and_edt():
    """Sessions are defined in wall-clock ET, so 09:45 is RTH_OPEN in both."""
    assert classify_session(datetime(2026, 1, 15, 9, 45, tzinfo=ET)) == "RTH_OPEN"
    assert classify_session(datetime(2026, 7, 15, 9, 45, tzinfo=ET)) == "RTH_OPEN"


@pytest.mark.parametrize("hh,mm,expected", [
    (9, 29, False), (9, 30, True), (15, 59, True), (16, 0, False),
])
def test_is_rth_boundaries(hh, mm, expected):
    assert is_rth(datetime(2026, 7, 15, hh, mm, tzinfo=ET)) is expected


def test_rth_bounds_and_minutes_since_open():
    o, c = rth_bounds(date(2026, 7, 15))
    assert (o.hour, o.minute) == (9, 30) and (c.hour, c.minute) == (16, 0)
    assert o.tzinfo is ET
    assert minutes_since_open(datetime(2026, 7, 15, 9, 30, tzinfo=ET)) == 0.0
    assert minutes_since_open(datetime(2026, 7, 15, 10, 30, tzinfo=ET)) == 60.0
    # Negative before the open: opening-range code relies on the sign.
    assert minutes_since_open(datetime(2026, 7, 15, 9, 0, tzinfo=ET)) == -30.0


def test_parse_hhmm():
    t = parse_hhmm("09:30")
    assert (t.hour, t.minute) == (9, 30)


# --------------------------------------------------------------------------
# The 18:00 ET trading-day roll
# --------------------------------------------------------------------------

def test_sunday_evening_bar_belongs_to_monday():
    """The invariant named in the module docstring: the CME session opens at
    18:00 ET the previous evening, so a Sunday 18:30 bar is Monday's."""
    sunday_1830 = datetime(2026, 3, 15, 18, 30, tzinfo=ET)   # a Sunday
    assert sunday_1830.strftime("%A") == "Sunday"
    assert trading_day(sunday_1830) == date(2026, 3, 16)     # Monday


@pytest.mark.parametrize("hh,mm,expected_day", [
    (17, 59, 17),   # still the 17th's session
    (18, 0, 18),    # the roll, inclusive
    (18, 30, 18),
    (23, 59, 18),
    (0, 1, 17),     # after midnight, still the 17th's calendar date
    (9, 30, 17),
])
def test_trading_day_rolls_at_eighteen_hundred(hh, mm, expected_day):
    dt = datetime(2026, 3, 17, hh, mm, tzinfo=ET)
    assert trading_day(dt) == date(2026, 3, expected_day)


def test_trading_day_roll_is_not_the_calendar_date():
    """A regression guard: using ``.date()`` instead of ``trading_day()``
    smears overnight highs and lows into the wrong session."""
    evening = datetime(2026, 3, 17, 20, 0, tzinfo=ET)
    assert evening.date() == date(2026, 3, 17)
    assert trading_day(evening) == date(2026, 3, 18)


def test_trading_day_roll_survives_the_dst_transition():
    """18:00 ET is 18:00 ET on both sides of the clock change."""
    est_evening = datetime(2026, 3, 7, 18, 30, tzinfo=ET)    # EST
    edt_evening = datetime(2026, 3, 9, 18, 30, tzinfo=ET)    # EDT
    assert et_label(est_evening) == "EST" and et_label(edt_evening) == "EDT"
    assert trading_day(est_evening) == date(2026, 3, 8)
    assert trading_day(edt_evening) == date(2026, 3, 10)


def test_trading_day_accepts_utc_input():
    """22:30 UTC in July is 18:30 EDT, which is already the next trading day."""
    utc_dt = datetime(2026, 7, 14, 22, 30, tzinfo=timezone.utc)
    assert trading_day(utc_dt) == date(2026, 7, 15)


# --------------------------------------------------------------------------
# Buckets, day names, holidays
# --------------------------------------------------------------------------

@pytest.mark.parametrize("hh,mm,expected", [
    (9, 30, "09:30-10:00"), (9, 59, "09:30-10:00"), (10, 0, "10:00-10:30"),
    (23, 45, "23:30-00:00"),
])
def test_time_bucket(hh, mm, expected):
    assert time_bucket(datetime(2026, 7, 15, hh, mm, tzinfo=ET), 30) == expected


def test_day_of_week_name():
    assert day_of_week_name(datetime(2026, 3, 17, 10, 0, tzinfo=ET)) == "TUESDAY"


def test_known_market_holidays():
    assert is_market_holiday(date(2026, 7, 3)) is True       # observed 4 July
    assert is_market_holiday(date(2026, 12, 25)) is True
    assert is_market_holiday(date(2026, 3, 17)) is False
