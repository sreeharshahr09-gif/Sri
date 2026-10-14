"""SQLite application database: schema migrations, records, journal, locks.

The application DB - not LangGraph checkpoints - is the source of truth for
editions and delivery state. All writes use transactions; uniqueness
constraints and a job-lock table coordinate overlapping runs.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import date, datetime, timezone
from pathlib import Path

from .schemas import Development
from .timeutil import expected_edition_dates

SCHEMA_VERSION = 2

MIGRATIONS: list[str] = [
    # v1
    """
    CREATE TABLE schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL);

    CREATE TABLE sources (
        id TEXT PRIMARY KEY,
        canonical_url TEXT NOT NULL UNIQUE,
        domain TEXT,
        source_type TEXT,
        title TEXT,
        published_at TEXT,
        published_provenance TEXT DEFAULT 'unknown',
        first_seen_at TEXT NOT NULL,
        content_hash TEXT,
        stable_ids TEXT DEFAULT '{}',
        extraction_quality TEXT DEFAULT 'none',
        status TEXT DEFAULT 'fetched',
        raw_path TEXT
    );

    CREATE TABLE clusters (
        id TEXT PRIMARY KEY,
        canonical_key TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE developments (
        id TEXT PRIMARY KEY,
        title TEXT NOT NULL,
        organization TEXT,
        category TEXT,
        application TEXT,
        source_type TEXT,
        canonical_url TEXT NOT NULL,
        published_at TEXT,
        published_provenance TEXT DEFAULT 'unknown',
        event_date TEXT,
        discovered_at TEXT NOT NULL,
        edition_date TEXT NOT NULL,
        what_changed TEXT,
        technical_explanation TEXT,
        claimed_benefit TEXT,
        baseline_conditions TEXT,
        maturity TEXT,
        limitations TEXT,
        relevance_rationale TEXT,
        suggested_followup TEXT,
        confidence_label TEXT,
        confidence_reasons TEXT,
        evidence_refs TEXT DEFAULT '[]',
        score_breakdown TEXT,
        final_score REAL,
        is_background INTEGER DEFAULT 0,
        injection_flag INTEGER DEFAULT 0,
        model_version TEXT,
        prompt_version TEXT,
        rubric_version TEXT,
        cluster_id TEXT REFERENCES clusters(id),
        status TEXT DEFAULT 'active',
        quarantine_reason TEXT,
        created_at TEXT NOT NULL
    );
    CREATE INDEX idx_dev_edition ON developments(edition_date);
    CREATE INDEX idx_dev_score ON developments(final_score);
    CREATE UNIQUE INDEX idx_dev_url_active ON developments(canonical_url) WHERE status='active';

    CREATE TABLE development_sources (
        development_id TEXT NOT NULL REFERENCES developments(id),
        source_id TEXT NOT NULL REFERENCES sources(id),
        PRIMARY KEY (development_id, source_id)
    );

    CREATE TABLE daily_editions (
        edition_date TEXT PRIMARY KEY,
        status TEXT NOT NULL,           -- research_complete | no_updates | failed | partial
        coverage_summary TEXT DEFAULT '{}',
        counts TEXT DEFAULT '{}',
        html_path TEXT, json_path TEXT, pdf_path TEXT,
        model_version TEXT, prompt_version TEXT, rubric_version TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE weekly_editions (
        week_start TEXT PRIMARY KEY,
        status TEXT NOT NULL,           -- complete | partial_blocked | partial_sent
        missing_dates TEXT DEFAULT '[]',
        html_path TEXT, pdf_path TEXT, json_path TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE jobs (
        id TEXT PRIMARY KEY,
        job_type TEXT NOT NULL,         -- daily | weekly | catchup
        edition_date TEXT,
        status TEXT NOT NULL,           -- running | succeeded | failed
        attempts INTEGER DEFAULT 1,
        error TEXT,
        started_at TEXT NOT NULL,
        finished_at TEXT
    );

    CREATE TABLE send_journal (
        identity TEXT PRIMARY KEY,      -- sha256(digest|period|sender)
        digest_type TEXT NOT NULL,      -- daily | weekly
        period TEXT NOT NULL,           -- edition date or week start
        sender TEXT NOT NULL,
        recipients TEXT NOT NULL,
        subject TEXT,
        state TEXT NOT NULL,            -- preparing|preview|blocked|submitted|failed_before_submit|uncertain|confirmed_delivered
        provider TEXT,
        provider_id TEXT,
        attachments TEXT DEFAULT '[]',
        attempts INTEGER DEFAULT 0,
        last_error TEXT,
        created_at TEXT NOT NULL,
        submitted_at TEXT,
        confirmed_at TEXT
    );

    CREATE TABLE exclusions (
        id TEXT PRIMARY KEY,
        ref TEXT NOT NULL,              -- source url or development id
        reason TEXT NOT NULL,
        created_at TEXT NOT NULL
    );

    CREATE TABLE feedback (
        id TEXT PRIMARY KEY,
        development_id TEXT NOT NULL,
        signal TEXT NOT NULL,           -- relevant | not_relevant | repeated
        note TEXT,
        created_at TEXT NOT NULL
    );

    CREATE TABLE locks (
        name TEXT PRIMARY KEY,
        owner TEXT NOT NULL,
        acquired_at TEXT NOT NULL
    );
    """,
    # v2: additive provenance, original URL and claim support; old records retained.
    "ALTER TABLE developments ADD COLUMN source_details TEXT DEFAULT '{}';",
]

RESEARCH_OK = ("research_complete", "no_updates")


def _utcnow() -> str:
    return datetime.now(timezone.utc).isoformat()


class Database:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        self.migrate()

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def tx(self):
        self._conn.execute("BEGIN IMMEDIATE")
        try:
            yield self._conn
            self._conn.execute("COMMIT")
        except Exception:
            self._conn.execute("ROLLBACK")
            raise

    def migrate(self) -> None:
        cur = self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='schema_migrations'")
        applied: set[int] = set()
        if cur.fetchone():
            applied = {r[0] for r in self._conn.execute("SELECT version FROM schema_migrations")}
        for i, sql in enumerate(MIGRATIONS, start=1):
            if i in applied:
                continue
            # executescript manages its own transaction semantics; the version
            # insert runs in autocommit mode (isolation_level=None)
            self._conn.executescript(sql)
            self._conn.execute(
                "INSERT OR IGNORE INTO schema_migrations(version, applied_at) VALUES (?, ?)",
                (i, _utcnow()))

    # -- job locks ----------------------------------------------------------- #
    def acquire_lock(self, name: str, owner: str | None = None) -> bool:
        owner = owner or uuid.uuid4().hex
        try:
            with self.tx() as c:
                c.execute("INSERT INTO locks(name, owner, acquired_at) VALUES (?, ?, ?)",
                          (name, owner, _utcnow()))
            return True
        except sqlite3.IntegrityError:
            return False

    def release_lock(self, name: str) -> None:
        with self.tx() as c:
            c.execute("DELETE FROM locks WHERE name = ?", (name,))

    # -- jobs ----------------------------------------------------------------- #
    def job_start(self, job_type: str, edition_date: str | None = None) -> str:
        job_id = uuid.uuid4().hex
        with self.tx() as c:
            c.execute(
                "INSERT INTO jobs(id, job_type, edition_date, status, started_at) VALUES (?,?,?,?,?)",
                (job_id, job_type, edition_date, "running", _utcnow()))
        return job_id

    def job_finish(self, job_id: str, ok: bool, error: str | None = None) -> None:
        with self.tx() as c:
            c.execute(
                "UPDATE jobs SET status=?, error=?, finished_at=? WHERE id=?",
                ("succeeded" if ok else "failed", error, _utcnow(), job_id))

    # -- sources / developments ----------------------------------------------- #
    def upsert_source(self, *, canonical_url: str, domain: str | None, source_type: str | None,
                      title: str | None, published_at: str | None, published_provenance: str,
                      content_hash: str | None, stable_ids: dict, extraction_quality: str,
                      status: str, raw_path: str | None = None) -> str:
        with self.tx() as c:
            row = c.execute("SELECT id FROM sources WHERE canonical_url=?", (canonical_url,)).fetchone()
            if row:
                return row["id"]
            sid = uuid.uuid4().hex
            c.execute(
                """INSERT INTO sources(id, canonical_url, domain, source_type, title, published_at,
                   published_provenance, first_seen_at, content_hash, stable_ids,
                   extraction_quality, status, raw_path)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (sid, canonical_url, domain, source_type, title, published_at,
                 published_provenance, _utcnow(), content_hash, json.dumps(stable_ids),
                 extraction_quality, status, raw_path))
            return sid

    def insert_development(self, dev: Development) -> None:
        with self.tx() as c:
            c.execute(
                """INSERT INTO developments(id, title, organization, category, application,
                   source_type, canonical_url, published_at, published_provenance, event_date,
                   discovered_at, edition_date, what_changed, technical_explanation,
                   claimed_benefit, baseline_conditions, maturity, limitations,
                   relevance_rationale, suggested_followup, confidence_label, confidence_reasons,
                   evidence_refs, score_breakdown, final_score, is_background, injection_flag,
                   model_version, prompt_version, rubric_version, cluster_id, status,
                   quarantine_reason, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (dev.id, dev.title, dev.organization, dev.category, dev.application,
                 dev.source_type.value, dev.canonical_url,
                 dev.published_at.isoformat() if dev.published_at else None,
                 dev.published_provenance,
                 dev.event_date.isoformat() if dev.event_date else None,
                 dev.discovered_at.isoformat(), dev.edition_date.isoformat(),
                 dev.what_changed, dev.technical_explanation, dev.claimed_benefit,
                 dev.baseline_conditions, dev.maturity.value, dev.limitations,
                 dev.relevance_rationale, dev.suggested_followup, dev.confidence_label.value,
                 dev.confidence_reasons, json.dumps(dev.evidence_refs),
                 dev.score_breakdown.model_dump_json() if dev.score_breakdown else None,
                 dev.final_score, int(dev.is_background), int(dev.injection_flag),
                 dev.model_version, dev.prompt_version, dev.rubric_version, dev.cluster_id,
                 dev.status, dev.quarantine_reason, _utcnow()))
            c.execute("UPDATE developments SET source_details=? WHERE id=?",
                      (json.dumps(dev.source_details, default=str), dev.id))
            for sid in dev.source_ids:
                c.execute(
                    "INSERT OR IGNORE INTO development_sources(development_id, source_id) VALUES (?,?)",
                    (dev.id, sid))

    def quarantine_development(self, dev: Development, reason: str) -> None:
        dev.status = "quarantined"
        dev.quarantine_reason = reason
        self.insert_development(dev)
        self.record_exclusion(dev.canonical_url, f"quarantined: {reason}")

    def developments_for_edition(self, edition_date: date | str, include_quarantined: bool = False) -> list[dict]:
        q = "SELECT * FROM developments WHERE edition_date=?"
        if not include_quarantined:
            q += " AND status='active'"
        rows = self._conn.execute(q, (str(edition_date),)).fetchall()
        return [self._dev_row(r) for r in rows]

    def developments_between(self, start: date, end: date) -> list[dict]:
        rows = self._conn.execute(
            "SELECT * FROM developments WHERE edition_date BETWEEN ? AND ? AND status='active' "
            "ORDER BY final_score DESC", (str(start), str(end))).fetchall()
        return [self._dev_row(r) for r in rows]

    def prior_index(self, history_days: int = 60) -> list[dict]:
        """Compact index of prior developments/sources for dedup + novelty."""
        rows = self._conn.execute(
            """SELECT d.id, d.title, d.what_changed, d.cluster_id, d.canonical_url, d.published_at
               FROM developments d WHERE d.status='active' AND
               d.discovered_at >= datetime('now', ?)""", (f"-{history_days} days",)).fetchall()
        out = []
        for r in rows:
            hashes = self._conn.execute(
                """SELECT s.content_hash, s.stable_ids FROM development_sources ds
                   JOIN sources s ON s.id = ds.source_id WHERE ds.development_id=?""",
                (r["id"],)).fetchall()
            stable: dict = {}
            content_hash = None
            for h in hashes:
                if h["content_hash"]:
                    content_hash = h["content_hash"]
                stable.update(json.loads(h["stable_ids"] or "{}"))
            out.append({
                "id": r["id"], "title": r["title"], "what_changed": r["what_changed"],
                "cluster_id": r["cluster_id"], "canonical_url": r["canonical_url"],
                "published_at": r["published_at"], "content_hash": content_hash,
                "stable_ids": stable,
            })
        return out

    def ensure_cluster(self, canonical_key: str | None = None) -> str:
        cid = uuid.uuid4().hex
        with self.tx() as c:
            c.execute("INSERT INTO clusters(id, canonical_key, created_at) VALUES (?,?,?)",
                      (cid, canonical_key, _utcnow()))
        return cid

    def record_exclusion(self, ref: str, reason: str) -> None:
        with self.tx() as c:
            c.execute("INSERT INTO exclusions(id, ref, reason, created_at) VALUES (?,?,?,?)",
                      (uuid.uuid4().hex, ref, reason, _utcnow()))

    # -- editions -------------------------------------------------------------- #
    def upsert_daily_edition(self, edition_date: date, status: str, coverage_summary: dict,
                             counts: dict, paths: dict, versions: dict) -> None:
        with self.tx() as c:
            c.execute(
                """INSERT INTO daily_editions(edition_date, status, coverage_summary, counts,
                   html_path, json_path, pdf_path, model_version, prompt_version, rubric_version, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(edition_date) DO UPDATE SET
                   status=excluded.status, coverage_summary=excluded.coverage_summary,
                   counts=excluded.counts, html_path=excluded.html_path,
                   json_path=excluded.json_path, pdf_path=excluded.pdf_path,
                   model_version=excluded.model_version, prompt_version=excluded.prompt_version,
                   rubric_version=excluded.rubric_version""",
                (str(edition_date), status, json.dumps(coverage_summary), json.dumps(counts),
                 paths.get("html"), paths.get("json"), paths.get("pdf"),
                 versions.get("model"), versions.get("prompt"), versions.get("rubric"), _utcnow()))

    def daily_edition(self, edition_date: date | str) -> dict | None:
        row = self._conn.execute(
            "SELECT * FROM daily_editions WHERE edition_date=?", (str(edition_date),)).fetchone()
        return dict(row) if row else None

    def edition_coverage(self, week_start: date) -> dict:
        """Which of the 7 expected edition dates are present and complete.
        Duplicates cannot fill gaps: dates are unique by primary key."""
        expected = expected_edition_dates(week_start)
        rows = self._conn.execute(
            "SELECT edition_date, status FROM daily_editions WHERE edition_date BETWEEN ? AND ?",
            (str(expected[0]), str(expected[-1]))).fetchall()
        by_date = {r["edition_date"]: r["status"] for r in rows}
        present = [d for d in expected if by_date.get(str(d)) in RESEARCH_OK]
        missing = [d for d in expected if d not in present]
        return {"expected": expected, "present": present, "missing": missing,
                "complete": not missing}

    def upsert_weekly_edition(self, week_start: date, status: str, missing: list[date], paths: dict) -> None:
        with self.tx() as c:
            c.execute(
                """INSERT INTO weekly_editions(week_start, status, missing_dates, html_path,
                   pdf_path, json_path, created_at) VALUES (?,?,?,?,?,?,?)
                   ON CONFLICT(week_start) DO UPDATE SET status=excluded.status,
                   missing_dates=excluded.missing_dates, html_path=excluded.html_path,
                   pdf_path=excluded.pdf_path, json_path=excluded.json_path""",
                (str(week_start), status, json.dumps([str(d) for d in missing]),
                 paths.get("html"), paths.get("pdf"), paths.get("json"), _utcnow()))

    # -- static helpers --------------------------------------------------------- #
    @staticmethod
    def _dev_row(r: sqlite3.Row) -> dict:
        d = dict(r)
        d["evidence_refs"] = json.loads(d.get("evidence_refs") or "[]")
        d["source_details"] = json.loads(d.get("source_details") or "{}")
        d["score_breakdown"] = json.loads(d["score_breakdown"]) if d.get("score_breakdown") else None
        d["is_background"] = bool(d.get("is_background"))
        d["injection_flag"] = bool(d.get("injection_flag"))
        return d
