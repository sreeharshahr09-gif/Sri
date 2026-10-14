"""Edition rendering: HTML (Jinja2), JSON, and PDF (ReportLab)."""
from __future__ import annotations

import json
from datetime import date, datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader

from ..timeutil import get_tz
from ..version import __version__
from ..quality import QUALITY_VERSION, select_report_items, weekly_items

TEMPLATE_DIR = Path(__file__).parent / "templates"
_env = Environment(loader=FileSystemLoader(str(TEMPLATE_DIR)),
                   autoescape=True)  # Templates end in .html.j2; extension guessing misses them.


def _reports_dir(cfg, kind: str) -> Path:
    d = Path(cfg.general.data_dir) / "reports" / kind
    d.mkdir(parents=True, exist_ok=True)
    return d


def _score_of(dev: dict) -> float:
    return dev.get("final_score") or 0.0


def render_daily(cfg, db, edition_date: date, *, retrospective: bool = False) -> dict:
    devs = db.developments_for_edition(edition_date)
    threshold = cfg.scoring.inclusion_threshold
    groups = select_report_items(devs, edition_date, cfg)
    included = groups["recent"]
    row = db.daily_edition(edition_date) or {}
    coverage = json.loads(row.get("coverage_summary") or "{}")
    coverage.update(included=len(included), background=len(groups["background"]),
                    unverified=len(groups["unverified"]), quality_version=QUALITY_VERSION)
    now = datetime.now(get_tz(cfg.general.timezone))
    html = _env.get_template("daily.html.j2").render(
        edition_date=str(edition_date), retrospective=retrospective, items=included, groups=groups,
        status=row.get("status", "unknown"), coverage=coverage,
        limitations=coverage.get("limitations", []),
        rubric_version=cfg.scoring.rubric_version,
        model_version=row.get("model_version") or cfg.llm.model,
        prompt_version=row.get("prompt_version") or "analyze-1.0",
        generated_at=now.strftime("%Y-%m-%d %H:%M"), version=__version__)
    outdir = _reports_dir(cfg, "daily")
    html_path = outdir / f"daily-{edition_date}.html"
    json_path = outdir / f"daily-{edition_date}.json"
    html_path.write_text(html, encoding="utf-8")
    json_path.write_text(json.dumps(
        {"edition_date": str(edition_date), "status": row.get("status"),
         "retrospective": retrospective, "coverage": coverage,
         "threshold": threshold, "developments": included,
         "background": groups["background"], "unverified": groups["unverified"]}, indent=2, default=str),
        encoding="utf-8")
    paths = {"html": str(html_path), "json": str(json_path)}
    if cfg.email.daily_pdf:
        from .pdf import build_daily_pdf
        pdf_path = outdir / f"daily-{edition_date}.pdf"
        build_daily_pdf(pdf_path, edition_date=str(edition_date), items=included,
                        generated_at=now.strftime("%Y-%m-%d %H:%M"))
        paths["pdf"] = str(pdf_path)
    return paths


def _published_day(dev: dict, tz):
    raw = dev.get("published_at")
    try:
        dt = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return dt.astimezone(tz).date() if dt.tzinfo else dt.date()


def render_weekly(cfg, db, week_start: date, *, priority_ids, additional_ids,
                  themes, actions, partial, missing_dates) -> dict:
    we = week_start.fromisoformat(str(week_start))  # normalize
    devs = weekly_items(db, cfg, week_start)
    by_id = {d["id"]: d for d in devs}
    priority = [by_id[i] for i in priority_ids if i in by_id]
    additional = [by_id[i] for i in additional_ids if i in by_id]
    priority_ids = [d["id"] for d in priority]
    additional_ids = [d["id"] for d in additional]
    # appendix: per-day index by publication date
    tz = get_tz(cfg.general.timezone)
    coverage = db.edition_coverage(week_start, cfg.discovery.lookback_days)
    appendix = []
    for i in range(7):
        d = week_start.fromordinal(week_start.toordinal() + i)
        day_devs = [x for x in devs if _published_day(x, tz) == d]
        appendix.append({"date": str(d), "status": "covered" if d in coverage["present"] else "missing",
                         "titles": [x["title"] for x in day_devs]})
    now = datetime.now(get_tz(cfg.general.timezone))
    html = _env.get_template("weekly.html.j2").render(
        week_start=str(week_start), week_end=str(week_start.fromordinal(week_start.toordinal() + 6)),
        priority=priority, additional=additional, themes=themes, actions=actions,
        appendix=appendix, partial=partial, missing_dates=missing_dates,
        rubric_version=cfg.scoring.rubric_version, model_version=cfg.llm.model,
        generated_at=now.strftime("%Y-%m-%d %H:%M"))
    outdir = _reports_dir(cfg, "weekly")
    html_path = outdir / f"weekly-{week_start}.html"
    json_path = outdir / f"weekly-{week_start}.json"
    pdf_path = outdir / f"weekly-{week_start}.pdf"
    html_path.write_text(html, encoding="utf-8")
    json_path.write_text(json.dumps(
        {"week_start": str(week_start), "partial": partial, "missing_dates": missing_dates,
         "priority_ids": priority_ids, "additional_ids": additional_ids,
         "themes": themes, "team_actions": actions,
         "developments": priority + additional}, indent=2, default=str), encoding="utf-8")
    from .pdf import build_weekly_pdf
    build_weekly_pdf(pdf_path, week_start=str(week_start),
                     week_end=str(week_start.fromordinal(week_start.toordinal() + 6)),
                     priority=priority, additional=additional, themes=themes,
                     actions=actions, appendix=appendix, partial=partial,
                     missing_dates=missing_dates,
                     generated_at=now.strftime("%Y-%m-%d %H:%M"))
    return {"html": str(html_path), "json": str(json_path), "pdf": str(pdf_path)}
