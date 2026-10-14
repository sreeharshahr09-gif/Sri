"""Freshness scorecard for daily editions: is the agent reporting NEW news?

Read-only. Point it at the application database (default data/tire_scout.sqlite3)
and it prints, per edition: the window, how many reported items it has, how
many have a source-stated publication date, how old the oldest reported item
is, and how discovery filtered the candidates.

    python scripts/freshness_scorecard.py
    python scripts/freshness_scorecard.py --db data/runs/<run>/tire_scout.sqlite3 --days 14
"""
from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

DATED = ("page_meta", "page_text", "feed", "api", "url", "provider")


def _day(raw):
    try:
        return datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
    except (TypeError, ValueError):
        return None


def scorecard(db_path: Path, days: int) -> list[dict]:
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    since = str(date.today() - timedelta(days=days))
    rows = []
    for ed in conn.execute("SELECT * FROM daily_editions WHERE edition_date >= ? ORDER BY edition_date", (since,)):
        cov = json.loads(ed["coverage_summary"] or "{}")
        devs = conn.execute("SELECT * FROM developments WHERE edition_date=? AND status='active'",
                            (ed["edition_date"],)).fetchall()
        edition = date.fromisoformat(ed["edition_date"])
        ages = [(edition - d).days for d in (_day(r["published_at"]) for r in devs) if d]
        strategy = cov.get("discovery_strategy") or {}
        rows.append({
            "edition": ed["edition_date"], "status": ed["status"],
            "window": "{window_start}..{window_end}".format(**cov["window"]) if cov.get("window") else "legacy",
            "reported": cov.get("included", 0),
            "analysed": len(devs),
            "dated": sum(r["published_provenance"] in DATED for r in devs),
            "oldest_days": max(ages) if ages else None,
            "listings": strategy.get("raw_candidates"),
            "dropped": strategy.get("dropped", {}),
            "sources_failed": sum(s.get("status") == "failed" for s in cov.get("discovery_sources", [])),
            "sources": len(cov.get("discovery_sources", [])),
        })
    conn.close()
    return rows


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--db", default="data/tire_scout.sqlite3")
    ap.add_argument("--days", type=int, default=30)
    args = ap.parse_args(argv)
    path = Path(args.db)
    if not path.exists():
        print(f"database not found: {path}", file=sys.stderr)
        return 2
    rows = scorecard(path, args.days)
    if not rows:
        print("no editions in range")
        return 0
    print(f"{'edition':10}  {'status':17}  {'window':22}  rep  anl  dated  oldest  listings  src_fail")
    for r in rows:
        oldest = "-" if r["oldest_days"] is None else f"{r['oldest_days']}d"
        print(f"{r['edition']:10}  {r['status']:17}  {r['window']:22}  {r['reported']:3}  {r['analysed']:3}  "
              f"{r['dated']:3}/{r['analysed']:<3} {oldest:>6}  {str(r['listings'] or '-'):>8}  "
              f"{r['sources_failed']}/{r['sources']}")
        if r["dropped"]:
            print("            dropped: " + ", ".join(f"{k}={v}" for k, v in list(r["dropped"].items())[:6]))
    print("\nTargets: every analysed item dated; oldest reported item no older than the window; "
          "few failed sources.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
