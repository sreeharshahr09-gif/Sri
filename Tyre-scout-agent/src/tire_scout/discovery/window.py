"""The date range a daily edition is responsible for.

A run covers the last `lookback_days` before its edition date and, when the
previous successful run is older than that, everything since that run (capped
at `max_catchup_days`) so a PC that was off over a weekend leaves no gap.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta


@dataclass(frozen=True)
class EditionWindow:
    start: date          # inclusive, local calendar date
    end: date            # inclusive, the edition date

    @property
    def days(self) -> int:
        return (self.end - self.start).days + 1

    def contains(self, day: date) -> bool:
        return self.start <= day <= self.end

    def as_dict(self) -> dict:
        return {"window_start": self.start.isoformat(), "window_end": self.end.isoformat()}


def edition_window(edition: date, *, lookback_days: int, max_catchup_days: int,
                   last_success: date | None = None) -> EditionWindow:
    start = edition - timedelta(days=max(0, lookback_days))
    if last_success is not None and last_success < start:
        start = last_success
    floor = edition - timedelta(days=max(lookback_days, max_catchup_days))
    return EditionWindow(max(start, floor), edition)


def stored_window(record: dict, edition: date, lookback_days: int) -> EditionWindow:
    """Window saved with a development or edition; legacy rows use the default."""
    details = record.get("source_details") or {}
    raw = record.get("window_start") or details.get("window_start")
    try:
        start = date.fromisoformat(str(raw)[:10]) if raw else None
    except ValueError:
        start = None
    return EditionWindow(start or edition - timedelta(days=lookback_days), edition)


def local_date(value, tz) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        dt = value
    else:
        try:
            dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except ValueError:
            return None
    if dt.tzinfo is not None:
        dt = dt.astimezone(tz)
    return dt.date()
