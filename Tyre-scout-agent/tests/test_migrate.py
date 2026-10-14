"""Legacy watcher migration: read-only inspection and journal reconciliation.

Fixtures reproduce the REAL legacy schema (release 2026-09-13):
sends(week_key, state, updated) with
week_key = sha256(f"{mailbox.lower()}|{monday}|{monday+7d}|{prefix.lower()}")[:24]
"""
import hashlib
import json
import sqlite3
from datetime import date, timedelta
from pathlib import Path

from tire_scout.emailer.journal import send_identity
from tire_scout.migrate import (decode_week_key, import_legacy_history,
                                inspect_legacy, week_key)

MAILBOX = "shreeharsha.r@apollotyres.com"
PREFIX = "Tire Technology Watcher"


def _key(monday: date) -> str:
    end = monday + timedelta(days=7)
    raw = f"{MAILBOX.lower()}|{monday}|{end}|{PREFIX.lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def _make_legacy(root: Path, states=("submitted", "submitted", "uncertain")) -> Path:
    pkg = root / "Tire_Watcher_Complete"
    pkg.mkdir(parents=True)
    (pkg / "tire_watcher_weekly.py").write_text("# legacy script", encoding="utf-8")
    (pkg / "WEEKLY_SETUP.md").write_text("# setup", encoding="utf-8")
    (pkg / "tire_watcher_weekly_config.json").write_text(json.dumps({
        "mailbox": MAILBOX, "subject_prefix": PREFIX,
        "to": [MAILBOX], "cc": ["teammate@apollotyres.com"],
        "automatic_sending_enabled": True,
        "first_scheduled_run": "2026-10-12T11:00:00+05:30",
        "weekly_send_time": "11:00"}), encoding="utf-8")
    conn = sqlite3.connect(pkg / "weekly_send_history.sqlite3")
    conn.execute("CREATE TABLE sends (week_key TEXT PRIMARY KEY, state TEXT NOT NULL,"
                 " updated TEXT NOT NULL)")
    mondays = [date(2026, 9, 14), date(2026, 9, 21), date(2026, 9, 28)]
    for monday, state in zip(mondays, states):
        conn.execute("INSERT INTO sends VALUES (?,?,?)",
                     (_key(monday), state, f"{monday + timedelta(days=7)}T09:45:00+05:30"))
    conn.commit()
    conn.close()
    return root


def test_week_key_roundtrip():
    monday = date(2026, 10, 5)
    assert week_key(MAILBOX, monday, PREFIX) == _key(monday)
    assert decode_week_key(_key(monday), MAILBOX, PREFIX) == monday
    assert decode_week_key("0" * 24, MAILBOX, PREFIX) is None
    assert decode_week_key(_key(monday), "", PREFIX) is None


def test_inspect_finds_files_recipients_and_decodes_history(tmp_path):
    root = _make_legacy(tmp_path / "legacy")
    report = inspect_legacy(root)
    assert report.found
    assert report.files["tire_watcher_weekly.py"]
    assert report.recipients["cc"] == ["teammate@apollotyres.com"]
    assert report.history_rows == 3
    weeks = {w["week_start"]: w["state"] for w in report.history_weeks}
    assert weeks == {"2026-09-14": "submitted", "2026-09-21": "submitted",
                     "2026-09-28": "uncertain"}
    assert len(report.holds) == 1 and report.holds[0]["week_start"] == "2026-09-28"
    # auto-send warning present
    assert any("WARNING" in n and "Tire Technology Watcher - Weekly Brief" in n
               for n in report.notes)


def test_inspect_missing_dir_is_graceful(tmp_path):
    report = inspect_legacy(tmp_path / "nope")
    assert not report.found and report.notes


def test_import_reconciles_history_and_blocks_resend(tmp_path, db):
    root = _make_legacy(tmp_path / "legacy")
    r1 = import_legacy_history(root, db, sender=MAILBOX)
    assert r1["imported"] == 3 and r1["skipped"] == 0
    r2 = import_legacy_history(root, db, sender=MAILBOX)  # idempotent
    assert r2["imported"] == 0 and r2["skipped"] == 3

    rows = db._conn.execute(
        "SELECT * FROM send_journal WHERE provider='legacy-outlook-com'").fetchall()
    assert len(rows) == 3
    by_period = {r["period"]: r for r in rows}
    # submitted stays submitted (never upgraded to confirmed_delivered)
    assert by_period["2026-09-14"]["state"] == "submitted"
    assert by_period["2026-09-14"]["submitted_at"]
    # a stale legacy hold maps to uncertain so the week stays blocked
    assert by_period["2026-09-28"]["state"] == "uncertain"
    assert by_period["2026-09-28"]["submitted_at"] is None
    # identities use the NEW sender, so prepare_send refuses a resend
    assert by_period["2026-09-21"]["identity"] == send_identity("weekly", "2026-09-21", MAILBOX)

    from tire_scout.emailer.journal import OutboundMessage, prepare_send
    msg = OutboundMessage(subject="x", from_address=MAILBOX, to=[MAILBOX],
                          html="<p>x</p>", text="x")
    decision = prepare_send(db, digest_type="weekly", period="2026-09-14",
                            cfg=None, msg=msg)
    assert not decision.allowed and "submitted" in decision.reason

    # source untouched
    conn = sqlite3.connect(Path(root) / "Tire_Watcher_Complete" / "weekly_send_history.sqlite3")
    assert conn.execute("SELECT COUNT(*) FROM sends").fetchone()[0] == 3
    conn.close()


def test_import_skips_undecodable_keys(tmp_path, db):
    root = _make_legacy(tmp_path / "legacy")
    pkg = Path(root) / "Tire_Watcher_Complete"
    conn = sqlite3.connect(pkg / "weekly_send_history.sqlite3")
    conn.execute("INSERT INTO sends VALUES (?,?,?)",
                 ("f" * 24, "submitted", "2026-10-12T09:00:00+05:30"))
    conn.commit()
    conn.close()
    result = import_legacy_history(root, db, sender=MAILBOX)
    assert result["imported"] == 3 and result["skipped"] == 1
