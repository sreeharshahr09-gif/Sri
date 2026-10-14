"""Timezone and edition-date handling.

IST is used consistently. Edition date is a *research coverage* date - it is
deliberately separate from publication dates, discovery times and email
receipt times, which the database stores in their own columns.
"""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

TZ_ALIASES = {"Asia/Calcutta": "Asia/Kolkata", "Asia/Kolkata": "Asia/Kolkata"}


def get_tz(name: str) -> ZoneInfo:
    canonical = TZ_ALIASES.get(name, name)
    return ZoneInfo(canonical)


def now_local(tz: ZoneInfo) -> datetime:
    return datetime.now(tz)


def edition_date_for(dt: datetime) -> date:
    """The edition date a run covers: the local calendar date of the run."""
    return dt.date()


def week_window(any_day: date) -> tuple[date, date]:
    """Monday..Sunday window containing `any_day`."""
    monday = any_day - timedelta(days=any_day.weekday())
    return monday, monday + timedelta(days=6)


def prior_week_window(ref: date) -> tuple[date, date]:
    """The completed Monday..Sunday week before the week containing `ref`."""
    monday, _ = week_window(ref)
    prev_monday = monday - timedelta(days=7)
    return prev_monday, prev_monday + timedelta(days=6)


def expected_edition_dates(week_start: date) -> list[date]:
    """The seven edition dates a weekly digest must cover."""
    return [week_start + timedelta(days=i) for i in range(7)]


def parse_hhmm(value: str) -> time:
    hh, mm = value.split(":")
    return time(int(hh), int(mm))


def next_daily_run(now: datetime, daily_time: str) -> datetime:
    t = parse_hhmm(daily_time)
    candidate = datetime.combine(now.date(), t, tzinfo=now.tzinfo)
    if candidate <= now:
        candidate += timedelta(days=1)
    return candidate


def next_weekly_run(now: datetime, weekly_day: str, weekly_time: str) -> datetime:
    days = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
    target = days.index(weekly_day.lower())
    t = parse_hhmm(weekly_time)
    delta = (target - now.weekday()) % 7
    candidate = datetime.combine(now.date() + timedelta(days=delta), t, tzinfo=now.tzinfo)
    if candidate <= now:
        candidate += timedelta(days=7)
    return candidate


def is_within_catchup(scheduled: datetime, now: datetime, window_hours: int) -> bool:
    """Bounded late-start catch-up: only catch up inside the configured window."""
    return scheduled <= now <= scheduled + timedelta(hours=window_hours)


def iso(dt: datetime | date | None) -> str | None:
    return dt.isoformat() if dt is not None else None
