"""Edition dates, week windows, timezone handling."""
from datetime import date, datetime
from zoneinfo import ZoneInfo

from tire_scout.timeutil import (expected_edition_dates, get_tz, is_within_catchup,
                                 next_weekly_run, prior_week_window, week_window)


def test_calcutta_alias_maps_to_kolkata():
    assert get_tz("Asia/Calcutta").key == "Asia/Kolkata"


def test_week_window_monday_to_sunday():
    mon, sun = week_window(date(2026, 10, 8))  # a Thursday
    assert mon == date(2026, 10, 5) and sun == date(2026, 10, 11)


def test_prior_week_window():
    mon, sun = prior_week_window(date(2026, 10, 12))  # Monday
    assert mon == date(2026, 10, 5) and sun == date(2026, 10, 11)


def test_expected_edition_dates_are_seven_distinct_days():
    days = expected_edition_dates(date(2026, 10, 5))
    assert len(days) == 7 and len(set(days)) == 7
    assert days[0].weekday() == 0 and days[-1].weekday() == 6


def test_next_weekly_run_is_monday_1100_ist():
    tz = ZoneInfo("Asia/Kolkata")
    now = datetime(2026, 10, 12, 10, 0, tzinfo=tz)  # Monday 10:00
    nxt = next_weekly_run(now, "monday", "11:00")
    assert nxt.weekday() == 0 and nxt.hour == 11 and nxt.date() == date(2026, 10, 12)
    now2 = datetime(2026, 10, 12, 11, 30, tzinfo=tz)  # after 11:00
    assert next_weekly_run(now2, "monday", "11:00").date() == date(2026, 10, 19)


def test_catchup_window_is_bounded():
    tz = ZoneInfo("Asia/Kolkata")
    sched = datetime(2026, 10, 12, 7, 30, tzinfo=tz)
    assert is_within_catchup(sched, datetime(2026, 10, 12, 12, 0, tzinfo=tz), 6)
    assert not is_within_catchup(sched, datetime(2026, 10, 12, 20, 0, tzinfo=tz), 6)
