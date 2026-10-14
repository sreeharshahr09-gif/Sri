"""Remove a daily edition that was produced from offline TEST data.

The production database contains a 2026-10-12 edition built from the fixture
search results (example.com and canned press-release URLs). It counts as a
real research day for weekly coverage and its links feed duplicate detection.

Dry run by default: it only lists what would be removed. With --apply it first
copies the database to data/backups/ and then deletes, in one transaction:
the edition row, its developments and their source links, its preview send
records and jobs, and exclusion/source rows whose URL appears in the fixture.

    python scripts/remove_fixture_edition.py                  # dry run
    python scripts/remove_fixture_edition.py --apply          # back up, then delete

Close any running tire-scout process first.
"""
from __future__ import annotations

import argparse
import json
import shutil
import sqlite3
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _canonical(url: str) -> str:
    sys.path.insert(0, str(ROOT / "src"))
    from tire_scout.dedup import canonicalize_url
    return canonicalize_url(url)


def plan(conn: sqlite3.Connection, edition: str, fixture_urls: set[str]) -> dict:
    q = lambda sql, *a: [tuple(r) for r in conn.execute(sql, a).fetchall()]
    dev_ids = [r[0] for r in q("SELECT id FROM developments WHERE edition_date=?", edition)]
    marks = ",".join("?" * len(fixture_urls)) or "''"
    return {
        "edition": q("SELECT edition_date, status FROM daily_editions WHERE edition_date=?", edition),
        "developments": dev_ids,
        "send_journal": q("SELECT identity, state FROM send_journal WHERE digest_type='daily' AND period=?", edition),
        "jobs": q("SELECT id, status FROM jobs WHERE edition_date=?", edition),
        "sources": q(f"SELECT id, canonical_url FROM sources WHERE canonical_url IN ({marks}) "
                     f"OR domain LIKE '%example.com'", *fixture_urls),
        "exclusions": q(f"SELECT id, ref FROM exclusions WHERE ref IN ({marks}) OR ref LIKE '%example.com%'",
                        *fixture_urls),
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Remove a fixture-built daily edition (dry run by default)")
    ap.add_argument("--db", default=str(ROOT / "data" / "tire_scout.sqlite3"))
    ap.add_argument("--edition", default="2026-10-12")
    ap.add_argument("--fixture", default=str(ROOT / "tests" / "fixtures" / "search_results.json"))
    ap.add_argument("--apply", action="store_true")
    args = ap.parse_args(argv)
    db = Path(args.db)
    if not db.exists():
        print(f"database not found: {db}")
        return 2
    fixture = json.loads(Path(args.fixture).read_text(encoding="utf-8"))
    urls = {_canonical(r["url"]) for r in fixture.get("results", [])}
    conn = sqlite3.connect(str(db))
    found = plan(conn, args.edition, urls)
    for key, rows in found.items():
        print(f"{key:14} {len(rows):3}  " + "; ".join(str(r) for r in rows[:4]) + (" ..." if len(rows) > 4 else ""))
    if not found["edition"]:
        print(f"no edition {args.edition} in this database; nothing to do")
        return 0
    coverage = conn.execute("SELECT coverage_summary FROM daily_editions WHERE edition_date=?",
                            (args.edition,)).fetchone()[0] or ""
    real_urls = [s for s in found["sources"] if s[1] not in urls and "example.com" not in s[1]]
    if real_urls:
        print("refusing: matched sources that are not fixture URLs:", real_urls)
        return 3
    if not args.apply:
        print("\nDry run only. Re-run with --apply to back up the database and delete these rows.")
        return 0
    backup_dir = db.parent / "backups"
    backup_dir.mkdir(exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%dT%H%M%S")
    backup = backup_dir / f"{db.stem}-before-fixture-removal-{stamp}.sqlite3"
    conn.execute("PRAGMA wal_checkpoint(FULL)")
    with sqlite3.connect(str(backup)) as dst:
        conn.backup(dst)
    print(f"backup written: {backup}")
    try:
        conn.execute("BEGIN IMMEDIATE")
        for dev in found["developments"]:
            conn.execute("DELETE FROM development_sources WHERE development_id=?", (dev,))
            conn.execute("DELETE FROM feedback WHERE development_id=?", (dev,))
            conn.execute("DELETE FROM developments WHERE id=?", (dev,))
        conn.execute("DELETE FROM send_journal WHERE digest_type='daily' AND period=?", (args.edition,))
        conn.execute("DELETE FROM jobs WHERE edition_date=?", (args.edition,))
        for sid, _ in found["sources"]:
            conn.execute("DELETE FROM development_sources WHERE source_id=?", (sid,))
            conn.execute("DELETE FROM sources WHERE id=?", (sid,))
        for eid, _ in found["exclusions"]:
            conn.execute("DELETE FROM exclusions WHERE id=?", (eid,))
        conn.execute("DELETE FROM daily_editions WHERE edition_date=?", (args.edition,))
        conn.execute("COMMIT")
    except Exception:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    print(f"removed edition {args.edition} (coverage was: {coverage[:120]})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
