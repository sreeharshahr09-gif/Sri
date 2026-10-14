"""tire-scout command line: setup-check, daily, weekly, catch-up, preview,
self-test, schedule, coverage, delivery-status, feedback, migrate, disable.
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path

from .config import apply_telemetry_policy, load_config
from .timeutil import get_tz, next_daily_run, next_weekly_run, now_local


def _cfg(args):
    path = Path(args.config)
    if not path.exists():
        example = Path(__file__).resolve().parents[2] / "config" / "config.example.yaml"
        if args.config == "config/config.yaml" and example.exists():
            print(f"[..] {args.config} not found; using {example} (copy and edit it)")
            path = example
        else:
            raise FileNotFoundError(f"config not found: {args.config}")
    cfg = load_config(path)
    apply_telemetry_policy(cfg)
    return cfg


def cmd_setup_check(args) -> int:
    cfg = _cfg(args)
    print("== Tire Scout setup check ==")
    ok_all = True
    # config + scoring
    try:
        from .scoring import validate_weights
        validate_weights(cfg.scoring.weights)
        print(f"[ok] config + rubric weights (sum=1.0, threshold {cfg.scoring.inclusion_threshold})")
    except ValueError as exc:
        ok_all = False
        print(f"[FAIL] scoring config: {exc}")
    # recipients
    try:
        from .emailer.journal import validate_recipients
        validate_recipients(cfg, cfg.recipients.daily_to, [])
        print(f"[ok] recipients within allowlist {cfg.recipients.allowed_domains}")
    except ValueError as exc:
        ok_all = False
        print(f"[FAIL] recipients: {exc}")
    # search
    print(f"[..] search provider: {cfg.search.provider}"
          + (" (offline fixture)" if cfg.search.provider == "fixture" else ""))
    # LLM capabilities
    from .llm import build_llm
    try:
        report = build_llm(cfg).capabilities()
        if report.reachable:
            print(f"[ok] llm ({cfg.llm.provider}) model {report.model} reachable; "
                  f"structured_output={report.supports_structured_output}, "
                  f"tools={report.supports_tools}")
        else:
            ok_all = False
            print(f"[FAIL] llm ({cfg.llm.provider}): {report.notes[-1] if report.notes else 'unreachable'}")
        for n in report.notes:
            print(f"       note: {n}")
    except Exception as exc:
        ok_all = False
        print(f"[FAIL] llm check: {exc}")
    # sandbox
    from .sandbox import SandboxExecutor
    ex = SandboxExecutor(cfg.sandbox)
    ok, msg = ex.ensure_image() if cfg.sandbox.enabled else (False, "disabled in config")
    print(f"[{'ok' if ok else 'FAIL'}] sandbox: {msg}")
    if cfg.sandbox.enabled and not ok:
        ok_all = False
        print("       generated-code execution will stay DISABLED until this passes")
    # scheduler
    from .scheduler import DAILY_TASK, WEEKLY_TASK, inspect_task, is_windows
    if is_windows():
        for name in (DAILY_TASK, WEEKLY_TASK):
            info = inspect_task(name)
            print(f"[{'ok' if info.exists else '..'}] task {name}: "
                  f"{info.status}, next-run boundary {info.next_run}")
    else:
        print("[..] scheduler: non-Windows host; use 'schedule install' on the target PC")
    tz = get_tz(cfg.general.timezone)
    now = now_local(tz)
    print(f"[..] next daily (config):  {next_daily_run(now, cfg.schedule.daily_time)}")
    print(f"[..] next weekly (config): {next_weekly_run(now, cfg.schedule.weekly_day, cfg.schedule.weekly_time)}")
    print(f"[..] live_sending={cfg.email.live_sending} daily_enabled={cfg.email.daily_enabled} "
          f"weekly_enabled={cfg.email.weekly_enabled} adapter={cfg.email.adapter}")
    return 0 if ok_all else 1


def cmd_daily(args) -> int:
    cfg = _cfg(args)
    from .runners import run_daily
    d = date.fromisoformat(args.date) if args.date else None
    res = run_daily(cfg, mode=args.mode, edition_date=d,
                    llm_fixture=args.llm_fixture, retrospective=bool(args.date))
    print(json.dumps({"ok": res.ok, "status": res.status, **res.detail}, indent=2, default=str))
    return 0 if res.ok else 1


def cmd_weekly(args) -> int:
    cfg = _cfg(args)
    from .runners import run_weekly
    ws = date.fromisoformat(args.week_start) if args.week_start else None
    res = run_weekly(cfg, mode=args.mode, week_start=ws,
                     allow_partial=args.allow_partial, llm_fixture=args.llm_fixture)
    print(json.dumps({"ok": res.ok, "status": res.status, **res.detail}, indent=2, default=str))
    return 0 if res.ok else 1


def cmd_coverage(args) -> int:
    cfg = _cfg(args)
    from .runners import coverage_report
    ws = date.fromisoformat(args.week_start) if args.week_start else None
    print(json.dumps(coverage_report(cfg, ws), indent=2))
    return 0


def cmd_delivery_status(args) -> int:
    cfg = _cfg(args)
    from .runners import delivery_status
    status = delivery_status(cfg)
    print(json.dumps(status, indent=2, default=str))
    if status["uncertain"]:
        print("\nUNCERTAIN SENDS require reconciliation before any retry "
              "(check provider Sent Items / Graph).", file=sys.stderr)
    return 0


def cmd_schedule(args) -> int:
    cfg = _cfg(args)
    from . import scheduler
    project_dir = str(Path(__file__).resolve().parent.parent.parent)
    if args.action == "install":
        for line in scheduler.install(cfg, project_dir, str(Path(args.config).resolve())):
            print(line)
    elif args.action == "inspect":
        for name in (scheduler.DAILY_TASK, scheduler.WEEKLY_TASK):
            info = scheduler.inspect_task(name)
            print(f"{name}: exists={info.exists} status={info.status} next={info.next_run}")
    elif args.action == "remove":
        for line in scheduler.remove_all():
            print(line)
    return 0


def cmd_self_test(args) -> int:
    """Offline end-to-end smoke test with the fixture provider and fixture LLM."""
    cfg = _cfg(args)
    fixture_dir = Path(__file__).resolve().parent.parent.parent / "tests" / "fixtures"
    cfg.search.provider = "fixture"
    cfg.search.fixture_path = str(fixture_dir / "search_results.json")
    from .runners import run_daily
    res = run_daily(cfg, mode="preview",
                    llm_fixture=str(fixture_dir / "llm" / "analyses.json"))
    print(json.dumps({"ok": res.ok, "status": res.status, **res.detail},
                     indent=2, default=str))
    return 0 if res.ok else 1


def cmd_feedback(args) -> int:
    cfg = _cfg(args)
    from .db import Database
    import uuid
    db = Database(Path(cfg.general.data_dir) / "tire_scout.sqlite3")
    try:
        with db.tx() as c:
            c.execute("INSERT INTO feedback(id, development_id, signal, note, created_at) "
                      "VALUES (?,?,?,?,?)",
                      (uuid.uuid4().hex, args.development_id, args.signal, args.note,
                       datetime.utcnow().isoformat()))
        print("recorded. Feedback informs FUTURE manual rubric edits; scoring rules "
              "never self-modify.")
    finally:
        db.close()
    return 0


def cmd_migrate(args) -> int:
    cfg = _cfg(args)
    from .migrate import import_legacy_history, inspect_legacy
    report = inspect_legacy(args.legacy_root)
    print(json.dumps({"root": report.root, "found": report.found, "files": report.files,
                      "file_integrity": report.file_integrity,
                      "recipients": report.recipients,
                      "legacy_settings": report.legacy_settings,
                      "history_rows": report.history_rows,
                      "history_weeks": report.history_weeks,
                      "holds": report.holds,
                      "notes": report.notes}, indent=2))
    if args.apply and report.found:
        from .db import Database
        sender = args.sender or cfg.email.from_address
        db = Database(Path(cfg.general.data_dir) / "tire_scout.sqlite3")
        try:
            result = import_legacy_history(
                args.legacy_root, db, sender=sender,
                mailbox=args.mailbox, subject_prefix=args.subject_prefix)
            print(f"imported {result['imported']} legacy weekly record(s) "
                  f"({result['skipped']} skipped/duplicate) for sender {sender}")
            for w in result["weeks"]:
                print(f"  {w['week_start']}  {w['state']:<10} {w['identity']}")
            print("IMPORTANT: disable the legacy watcher's scheduled task "
                  "('Tire Technology Watcher - Weekly Brief') so only ONE "
                  "production sender remains.")
        finally:
            db.close()
    return 0


def cmd_disable_sending(args) -> int:
    print("Edit config.yaml: email.live_sending=false (master), "
          "email.daily_enabled=false, email.weekly_enabled=false. "
          "Also run: schedule remove  (to uninstall Task Scheduler entries).")
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="tire-scout",
                                description="Local tire technology scouting agent")
    p.add_argument("--config", default="config/config.yaml")
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("setup-check").set_defaults(fn=cmd_setup_check)

    d = sub.add_parser("daily")
    d.add_argument("--mode", choices=["preview", "production"], default="preview")
    d.add_argument("--date", help="retrospective edition date YYYY-MM-DD (catch-up)")
    d.add_argument("--llm-fixture", help=argparse.SUPPRESS)
    d.set_defaults(fn=cmd_daily)

    w = sub.add_parser("weekly")
    w.add_argument("--mode", choices=["preview", "production"], default="preview")
    w.add_argument("--week-start", help="Monday YYYY-MM-DD (default: prior week)")
    w.add_argument("--allow-partial", action="store_true",
                   help="OPERATOR OVERRIDE: send visibly-labelled partial issue")
    w.add_argument("--llm-fixture", help=argparse.SUPPRESS)
    w.set_defaults(fn=cmd_weekly)

    c = sub.add_parser("coverage")
    c.add_argument("--week-start")
    c.set_defaults(fn=cmd_coverage)

    sub.add_parser("delivery-status").set_defaults(fn=cmd_delivery_status)
    sub.add_parser("self-test").set_defaults(fn=cmd_self_test)
    sub.add_parser("disable-sending").set_defaults(fn=cmd_disable_sending)

    s = sub.add_parser("schedule")
    s.add_argument("action", choices=["install", "inspect", "remove"])
    s.set_defaults(fn=cmd_schedule)

    f = sub.add_parser("feedback")
    f.add_argument("development_id")
    f.add_argument("signal", choices=["relevant", "not_relevant", "repeated"])
    f.add_argument("--note", default="")
    f.set_defaults(fn=cmd_feedback)

    m = sub.add_parser("migrate")
    m.add_argument("legacy_root",
                   help=r"e.g. C:\Users\...\Tyre_Tech_Watcher")
    m.add_argument("--apply", action="store_true", help="import legacy send history")
    m.add_argument("--sender",
                   help="from_address for imported identities (default: config email.from_address)")
    m.add_argument("--mailbox", help="legacy mailbox, if the legacy config is unavailable")
    m.add_argument("--subject-prefix", dest="subject_prefix",
                   help="legacy daily subject prefix, if the legacy config is unavailable")
    m.set_defaults(fn=cmd_migrate)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    raise SystemExit(main())
