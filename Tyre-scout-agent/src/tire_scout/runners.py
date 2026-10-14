"""Runners: assemble dependencies, guard with job locks, execute graphs,
handle bounded retries/catch-up, and report honestly.

Failed stages are preserved: a crash never rewrites history, and a failed
research run never masquerades as a "no news" day.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path

from .config import AppConfig
from .db import Database, RESEARCH_OK
from .emailer import journal as send_journal
from .fetch import Fetcher
from .graphs.daily import Deps, build_daily_graph
from .graphs.weekly import WeeklyDeps, build_weekly_graph
from .llm import build_llm
from .sandbox import SandboxExecutor
from .search import build_search_adapter
from .timeutil import get_tz, prior_week_window
from .tools import AgentTools


@dataclass
class RunResult:
    ok: bool
    status: str
    detail: dict


def _checkpointer(cfg: AppConfig):
    from langgraph.checkpoint.sqlite import SqliteSaver
    path = Path(cfg.general.data_dir) / "checkpoints.sqlite"
    path.parent.mkdir(parents=True, exist_ok=True)
    import sqlite3
    conn = sqlite3.connect(str(path), check_same_thread=False)
    return SqliteSaver(conn)


def assemble(cfg: AppConfig, *, llm_fixture: str | None = None):
    db = Database(Path(cfg.general.data_dir) / "tire_scout.sqlite3")
    search = build_search_adapter(cfg)
    fetcher = Fetcher(allow_ocr=False)
    sandbox = SandboxExecutor(cfg.sandbox)
    tools = AgentTools(cfg=cfg, db=db, search=search, fetcher=fetcher, sandbox=sandbox)
    llm = build_llm(cfg, fixture_path=llm_fixture)
    return db, tools, llm


def run_daily(cfg: AppConfig, *, mode: str = "preview", edition_date: date | None = None,
              llm_fixture: str | None = None, retrospective: bool = False) -> RunResult:
    tz = get_tz(cfg.general.timezone)
    db, tools, llm = assemble(cfg, llm_fixture=llm_fixture)
    try:
        lock_name = f"daily-{edition_date or 'today'}"
        if not db.acquire_lock(lock_name):
            return RunResult(False, "locked", {"reason": "another daily run holds the lock"})
        try:
            job_id = db.job_start("catchup" if retrospective else "daily",
                                  str(edition_date) if edition_date else None)
            deps = Deps(cfg=cfg, db=db, llm=llm, tools=tools, tz=tz)
            graph = build_daily_graph(deps, checkpointer=_checkpointer(cfg))
            init = {"mode": mode, "job_id": job_id, "retrospective": retrospective,
                    "errors": [], "limitations": [], "excluded": [], "developments": []}
            if edition_date:
                init["edition_date"] = str(edition_date)
            thread = f"daily-{edition_date or date.today()}"
            try:
                final = graph.invoke(init, config={"configurable": {"thread_id": thread}})
            except Exception as exc:
                db.job_finish(job_id, ok=False, error=str(exc))
                return RunResult(False, "crashed", {"error": str(exc)})
            ok = final.get("status") in RESEARCH_OK
            db.job_finish(job_id, ok=ok,
                          error=None if ok else "; ".join(final.get("errors", [])))
            return RunResult(ok, final.get("status", "unknown"),
                             {"send_state": final.get("send_state"),
                              "render_paths": final.get("render_paths", {}),
                              "coverage": final.get("coverage_summary", {})})
        finally:
            db.release_lock(lock_name)
    finally:
        db.close()


def run_weekly(cfg: AppConfig, *, mode: str = "preview", week_start: date | None = None,
               allow_partial: bool = False, llm_fixture: str | None = None) -> RunResult:
    tz = get_tz(cfg.general.timezone)
    db, tools, llm = assemble(cfg, llm_fixture=llm_fixture)
    try:
        ws = week_start or prior_week_window(datetime.now(tz).date())[0]
        # bounded recovery of missing daily editions BEFORE the graph's gate
        recovered, recovery_notes = [], []
        cov = db.edition_coverage(ws)
        if not cov["complete"] and cfg.weekly.recovery_enabled and not allow_partial:
            for d in cov["missing"][: cfg.weekly.max_recovery_dates]:
                res = run_daily(cfg, mode="preview", edition_date=d,
                                llm_fixture=llm_fixture, retrospective=True)
                if res.ok:
                    recovered.append(str(d))
                else:
                    recovery_notes.append(f"{d}: {res.status}")
        db.close()

        db, tools, llm = assemble(cfg, llm_fixture=llm_fixture)
        if not db.acquire_lock("weekly"):
            return RunResult(False, "locked", {"reason": "another weekly run holds the lock"})
        try:
            job_id = db.job_start("weekly", str(ws))
            deps = WeeklyDeps(cfg=cfg, db=db, llm=llm, tz=tz)
            graph = build_weekly_graph(deps, checkpointer=_checkpointer(cfg))
            init = {"mode": mode, "job_id": job_id, "week_start": str(ws),
                    "allow_partial": allow_partial, "errors": [], "limitations": []}
            try:
                final = graph.invoke(init, config={"configurable": {"thread_id": f"weekly-{ws}"}})
            except Exception as exc:
                db.job_finish(job_id, ok=False, error=str(exc))
                return RunResult(False, "crashed", {"error": str(exc)})
            ok = final.get("status") in ("complete", "partial_sent")
            db.job_finish(job_id, ok=ok,
                          error=None if ok else final.get("send_state"))
            detail = {"send_state": final.get("send_state"),
                      "render_paths": final.get("render_paths", {}),
                      "missing_dates": final.get("missing_dates", []),
                      "recovered": recovered, "recovery_notes": recovery_notes}
            return RunResult(ok, final.get("status", "unknown"), detail)
        finally:
            db.release_lock("weekly")
    finally:
        db.close()


def delivery_status(cfg: AppConfig, limit: int = 20) -> dict:
    db = Database(Path(cfg.general.data_dir) / "tire_scout.sqlite3")
    try:
        return {"journal": send_journal.journal_status(db, limit),
                "uncertain": send_journal.uncertain_sends(db)}
    finally:
        db.close()


def coverage_report(cfg: AppConfig, week_start: date | None = None) -> dict:
    tz = get_tz(cfg.general.timezone)
    ws = week_start or prior_week_window(datetime.now(tz).date())[0]
    db = Database(Path(cfg.general.data_dir) / "tire_scout.sqlite3")
    try:
        cov = db.edition_coverage(ws)
        return {"week_start": str(ws),
                "present": [str(d) for d in cov["present"]],
                "missing": [str(d) for d in cov["missing"]],
                "complete": cov["complete"]}
    finally:
        db.close()
