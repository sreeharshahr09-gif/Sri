"""Migration from the legacy Tyre_Tech_Watcher.

Read-only by default: inspects the old project, lists what would be reused,
and imports the existing weekly send history into the new journal so already-
sent weeks can never be resent. The old program, reports, journals and
scheduled tasks are left untouched. After import, only ONE production sending
authority should remain - disable the old task
("Tire Technology Watcher - Weekly Brief") before enabling this agent's.

Verified against the real legacy package (release 2026-09-13):
  * config  tire_watcher_weekly_config.json  (mailbox, to, cc, send switches)
  * journal weekly_send_history.sqlite3      (table: sends(week_key, state, updated))
  * week key = sha256(f"{mailbox.lower()}|{monday}|{sunday+1}|{prefix.lower()}")[:24]
  * legacy states: preparing -> uncertain -> submitted (submission != delivery)
"""
from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

EXPECTED_FILES = ["tire_watcher_weekly.py", "tire_watcher_draft.py",
                  "tire_watcher_weekly_config.json", "weekly_send_history.sqlite3",
                  "WEEKLY_SETUP.md"]
LEGACY_TASK_NAME = "Tire Technology Watcher - Weekly Brief"
KEY_SCAN_START = date(2015, 1, 5)  # Monday; bound for brute-forcing week keys

# Legacy journal states -> new journal states. The legacy program itself
# states "a successful Send() call means submitted to Outlook, not confirmed
# delivery", so imported rows are never marked confirmed_delivered. A stale
# 'preparing' hold means a crash mid-preparation: treat as uncertain so the
# week stays blocked until a human reconciles Sent Items / Outbox.
STATE_MAP = {"submitted": "submitted", "uncertain": "uncertain",
             "preparing": "uncertain"}


@dataclass
class MigrationReport:
    root: str
    found: bool
    files: dict = field(default_factory=dict)
    file_integrity: dict = field(default_factory=dict)
    recipients: dict = field(default_factory=dict)
    legacy_settings: dict = field(default_factory=dict)
    history_rows: int = 0
    history_weeks: list = field(default_factory=list)  # decoded (week, state, updated)
    holds: list = field(default_factory=list)          # weeks blocking a resend
    notes: list = field(default_factory=list)


def _legacy_base(root: Path) -> Path:
    pkg = root / "Tire_Watcher_Complete"
    return pkg if pkg.exists() else root


def _read_legacy_config(base: Path) -> dict:
    cfg_path = base / "tire_watcher_weekly_config.json"
    if not cfg_path.exists():
        return {}
    try:
        return json.loads(cfg_path.read_text(encoding="utf-8-sig", errors="replace"))
    except json.JSONDecodeError:
        return {}


def week_key(mailbox: str, start: date, prefix: str) -> str:
    """Legacy week key (tire_watcher_draft.compile_digest)."""
    end = start + timedelta(days=7)
    raw = f"{mailbox.lower()}|{start}|{end}|{prefix.lower()}"
    return hashlib.sha256(raw.encode()).hexdigest()[:24]


def decode_week_key(key: str, mailbox: str, prefix: str,
                    *, not_after: date | None = None) -> date | None:
    """Recover the Monday of the week a legacy key belongs to by scanning
    candidate Mondays. Exact and cheap (~600 sha256 evaluations)."""
    if not mailbox or not prefix:
        return None
    latest = (not_after or date.today()) + timedelta(days=7)
    day = KEY_SCAN_START
    while day <= latest:
        if week_key(mailbox, day, prefix) == key:
            return day
        day += timedelta(days=7)
    return None


def _legacy_sends(hist: Path) -> list[dict]:
    """Read the legacy journal. Handles the real schema sends(week_key, state,
    updated); any other table is returned with raw rows for the generic path."""
    conn = sqlite3.connect(f"file:{hist}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'")]
        rows = []
        for table in tables:
            cols = [c[1] for c in conn.execute(f"PRAGMA table_info('{table}')")]
            for row in conn.execute(f"SELECT * FROM '{table}'"):
                rows.append({"table": table, "cols": cols, "row": dict(row)})
        return rows
    finally:
        conn.close()


def inspect_legacy(root: str | Path) -> MigrationReport:
    root = Path(root)
    report = MigrationReport(root=str(root), found=root.exists())
    if not root.exists():
        report.notes.append("legacy directory not found on this machine")
        return report
    base = _legacy_base(root)
    for name in EXPECTED_FILES:
        report.files[name] = (base / name).exists()

    # Integrity: compare file hashes against the released package manifest.
    manifest = base / "PACKAGE_CONTENTS.json"
    if manifest.exists():
        try:
            entries = json.loads(manifest.read_text(encoding="utf-8"))["files"]
            for e in entries:
                p = base / e["path"]
                if p.exists() and p.is_file():
                    ok = hashlib.sha256(p.read_bytes()).hexdigest() == e["sha256"]
                    report.file_integrity[e["path"]] = "matches release" if ok else "MODIFIED since release"
        except (json.JSONDecodeError, KeyError) as exc:
            report.notes.append(f"PACKAGE_CONTENTS.json unreadable: {exc}")

    cfg = _read_legacy_config(base)
    if cfg:
        report.recipients = {"mailbox": cfg.get("mailbox", ""),
                             "to": cfg.get("to", []), "cc": cfg.get("cc", []),
                             "source_sender": cfg.get("source_sender", "")}
        report.legacy_settings = {
            "subject_prefix": cfg.get("subject_prefix", ""),
            "automatic_sending_enabled": bool(cfg.get("automatic_sending_enabled")),
            "require_complete_week_before_send": cfg.get("require_complete_week_before_send"),
            "weekly_send_time": cfg.get("weekly_send_time", ""),
            "first_scheduled_run": cfg.get("first_scheduled_run", "")}
        report.notes.append("review recipients above; copy intentionally into config.yaml")
        if report.legacy_settings["automatic_sending_enabled"]:
            report.notes.append(
                f"WARNING: legacy automatic sending is ENABLED "
                f"(first_scheduled_run={report.legacy_settings['first_scheduled_run']}). "
                f"Disable the legacy task {LEGACY_TASK_NAME!r} before enabling this "
                "agent - only ONE production sending authority may remain.")
    else:
        report.notes.append("legacy config not found or not parseable; "
                            "week keys cannot be decoded without mailbox/subject_prefix")

    hist = base / "weekly_send_history.sqlite3"
    if hist.exists():
        try:
            sends = _legacy_sends(hist)
            report.history_rows = len(sends)
            real = [s for s in sends if "week_key" in s["cols"]]
            other = [s for s in sends if "week_key" not in s["cols"]]
            for s in real:
                start = decode_week_key(s["row"].get("week_key", ""),
                                        cfg.get("mailbox", ""),
                                        cfg.get("subject_prefix", ""))
                state = str(s["row"].get("state", ""))
                entry = {"week_start": start.isoformat() if start else None,
                         "state": state, "updated": s["row"].get("updated", ""),
                         "week_key": s["row"].get("week_key", "")}
                report.history_weeks.append(entry)
                if state in ("preparing", "uncertain"):
                    report.holds.append(entry)
            if other:
                report.notes.append(
                    f"{len(other)} history row(s) use an unrecognized schema "
                    "(no week_key column) - import falls back to the generic path")
            if report.holds:
                report.notes.append(
                    f"{len(report.holds)} week(s) hold a legacy 'preparing'/'uncertain' "
                    "record: check Sent Items and Outbox before any action on them")
        except sqlite3.Error as exc:
            report.notes.append(f"legacy history unreadable: {exc}")
    return report


def import_legacy_history(root: str | Path, db, *, sender: str,
                          mailbox: str | None = None,
                          subject_prefix: str | None = None) -> dict:
    """Copy legacy weekly send records into the new journal so past weeks can
    never be resent. Never deletes or modifies the source.

    `sender` is THIS agent's configured from_address: imported identities are
    computed with it, so a new production send for an already-sent legacy week
    is refused by prepare_send(). `mailbox`/`subject_prefix` override the
    legacy config values used to decode week keys.

    Returns {"imported": n, "skipped": n, "weeks": [...]}."""
    base = _legacy_base(Path(root))
    hist = base / "weekly_send_history.sqlite3"
    if not hist.exists():
        return {"imported": 0, "skipped": 0, "weeks": []}
    cfg = _read_legacy_config(base)
    mailbox = mailbox or cfg.get("mailbox", "")
    prefix = subject_prefix if subject_prefix is not None else cfg.get("subject_prefix", "")
    recipients = json.dumps({"to": cfg.get("to", []), "cc": cfg.get("cc", [])})

    from .emailer.journal import send_identity
    imported, skipped, weeks = 0, 0, []
    for s in _legacy_sends(hist):
        cols, row = s["cols"], s["row"]
        if "week_key" in cols:  # the real legacy schema
            key = str(row.get("week_key", ""))
            start = decode_week_key(key, mailbox, prefix)
            if start is None:
                skipped += 1
                continue
            period = start.isoformat()
            legacy_state = str(row.get("state", "")).lower()
            state = STATE_MAP.get(legacy_state, "uncertain")
            end = start + timedelta(days=6)
            subject = f"[Weekly] Tire Technology Watcher | {start:%d %b} - {end:%d %b %Y}"
            provider_id = f"legacy://sends/{key}"
            created = str(row.get("updated") or "") or None
        else:  # generic fallback for forward-compatible schemas
            period = next((str(row[c])[:10] for c in
                           ("week_start", "week_of", "issue_date", "period", "sent_date")
                           if c in cols and row[c]), None)
            if not period:
                skipped += 1
                continue
            state, subject = "submitted", f"legacy weekly {period}"
            provider_id = f"legacy://{s['table']}/{period}"
            created = None
        identity = send_identity("weekly", period, sender)
        with db.tx() as c:
            cur = c.execute(
                """INSERT OR IGNORE INTO send_journal(identity, digest_type, period,
                   sender, recipients, subject, state, provider, provider_id,
                   created_at, submitted_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (identity, "weekly", period, sender, recipients, subject, state,
                 "legacy-outlook-com", provider_id,
                 created or period,
                 created if state == "submitted" else None))
            if cur.rowcount:
                imported += 1
                weeks.append({"week_start": period, "state": state,
                              "identity": identity[:16] + "..."})
            else:
                skipped += 1
    return {"imported": imported, "skipped": skipped, "weeks": weeks}
