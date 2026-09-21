#!/usr/bin/env python3
"""Read-only report of the weekly send journal. Sends nothing, changes nothing.

    python check_send_history.py
"""
from pathlib import Path
import sqlite3
import sys

import tire_watcher_draft as core
import tire_watcher_weekly as weekly

BASE = Path(__file__).resolve().parent
JOURNAL = BASE / 'weekly_send_history.sqlite3'


def main():
    cfg = weekly.read_config(weekly.CONFIG)
    print(f'Mailbox: {cfg["mailbox"]}')
    print(f'Automatic sending enabled: {bool(cfg.get("automatic_sending_enabled"))}')
    print(f'First scheduled run: {cfg.get("first_scheduled_run") or "(not installed)"}')
    print(f'Send time: {weekly.send_time(cfg):%H:%M} IST')
    if not JOURNAL.is_file():
        print(f'\nNo journal file yet ({JOURNAL.name}). No week has ever reached the send step.')
        return 0
    connection = sqlite3.connect(f'file:{JOURNAL}?mode=ro', uri=True, timeout=10)
    rows = connection.execute('SELECT week_key, state, updated FROM sends ORDER BY updated').fetchall()
    connection.close()
    if not rows:
        print('\nJournal exists but holds no records. No week has reached the send step.')
        return 0
    # Name the weeks this mailbox could have sent, so a key is readable.
    monday = core.previous_week()
    known = {}
    for back in range(12):
        start, end = core.week_bounds(monday - core.timedelta(days=7 * back))
        key = weekly.sha256(f"{cfg['mailbox'].lower()}|{start.date()}|{end.date()}|"
                            f"{cfg['subject_prefix'].lower()}".encode()).hexdigest()[:24]
        known[key] = f'{start:%d %b} - {end - core.timedelta(days=1):%d %b %Y}'
    print(f'\n{len(rows)} journal record(s):')
    for key, state, updated in rows:
        label = known.get(key, '(week outside the last 12)')
        flag = '  <-- blocks a resend of this week' if state in ('preparing', 'uncertain') else ''
        print(f'  {label:<26} {state:<10} {updated}{flag}')
    print('\nA "submitted" record means the brief reached Outlook for that week.')
    print('"preparing" or "uncertain" holds the week; check Sent Items and Outbox before acting.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
