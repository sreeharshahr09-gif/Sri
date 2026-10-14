#!/usr/bin/env python3
"""Generate example outputs (daily HTML/JSON, weekly HTML/PDF/JSON) using the
OFFLINE fixture provider, fake web, and fixture LLM. No network, no Docker,
no email - this demonstrates the preview path end to end.

Run from the project root:  python examples/generate_examples.py
"""
from __future__ import annotations

import json
import sys
import tempfile
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests"))

import yaml  # noqa: E402

import conftest  # noqa: E402  (test fixtures: fake web + DNS)
from tire_scout.config import load_config  # noqa: E402
from tire_scout.db import Database  # noqa: E402
from tire_scout.fetch import Fetcher  # noqa: E402
from tire_scout.graphs.daily import Deps, build_daily_graph  # noqa: E402
from tire_scout.graphs.weekly import WeeklyDeps, build_weekly_graph  # noqa: E402
from tire_scout.llm import FixtureLLMClient  # noqa: E402
from tire_scout.sandbox import SandboxExecutor  # noqa: E402
from tire_scout.search.fixture import FixtureSearchAdapter  # noqa: E402
from tire_scout.timeutil import get_tz  # noqa: E402
from tire_scout.tools import AgentTools  # noqa: E402

OUT = Path(tempfile.mkdtemp(prefix="tire-scout-examples-"))
WEEK_START = date(2026, 10, 5)


class ExampleLLM(FixtureLLMClient):
    """Fixture LLM whose weekly synthesis references the real generated IDs."""

    def synthesize_week(self, *, developments):
        ids = [d["id"] for d in developments]
        by_cat = {}
        for d in developments:
            by_cat.setdefault(d["category"], d["id"])
        from tire_scout.schemas import WeeklySynthesis
        return WeeklySynthesis(
            themes=[
                {"theme": "Filler-interface chemistry keeps advancing on rolling resistance",
                 "development_ids": [i for i in ids if by_cat.get(
                     "Rubber compounds and materials") == i][:2]},
                {"theme": "EV-driven lightweighting across reinforcement and process",
                 "development_ids": ids[1:3]},
            ],
            team_actions=[
                {"action": "Request the Evonik silane technical data sheet and plan a lab mixing trial",
                 "development_ids": ids[:1]},
                {"action": "Ask Bekaert for the mega-tensile cord datasheet and fatigue data",
                 "development_ids": ids[1:2]},
                {"action": "Benchmark tandem-mixing energy figures against internal mixing-room baselines",
                 "development_ids": ids[2:3]},
            ],
            weekly_rank=ids)


def main() -> None:
    raw = yaml.safe_load((ROOT / "config" / "config.example.yaml").read_text(encoding="utf-8"))
    raw["general"]["data_dir"] = str(OUT / "data")
    raw["search"]["provider"] = "fixture"
    raw["search"]["fixture_path"] = str(ROOT / "tests" / "fixtures" / "search_results.json")
    cfg_path = OUT / "config.yaml"
    cfg_path.write_text(yaml.safe_dump(raw), encoding="utf-8")
    cfg = load_config(cfg_path)

    db = Database(OUT / "data" / "tire_scout.sqlite3")
    fetcher = Fetcher(resolve=conftest.fake_resolve,
                      transport=conftest.fake_transport(conftest.make_pages()))
    tools = AgentTools(cfg=cfg, db=db, search=FixtureSearchAdapter(cfg),
                       fetcher=fetcher, sandbox=SandboxExecutor(cfg.sandbox))
    llm = ExampleLLM(ROOT / "tests" / "fixtures" / "llm" / "analyses.json")
    tz = get_tz("Asia/Kolkata")

    # --- seven daily editions: day 1 has news; later days dedupe to no_updates
    daily = build_daily_graph(Deps(cfg=cfg, db=db, llm=llm, tools=tools, tz=tz))
    for i in range(7):
        day = WEEK_START + timedelta(days=i)
        daily.invoke({"edition_date": str(day), "mode": "preview",
                      "errors": [], "limitations": [], "excluded": [], "developments": []},
                     config={"configurable": {"thread_id": f"ex-daily-{day}"}})
    cov = db.edition_coverage(WEEK_START)
    print("daily coverage:", [str(d) for d in cov["present"]], "complete:", cov["complete"])

    # --- weekly digest
    weekly = build_weekly_graph(WeeklyDeps(cfg=cfg, db=db, llm=llm, tz=tz))
    final = weekly.invoke({"week_start": str(WEEK_START), "mode": "preview",
                           "errors": [], "limitations": []},
                          config={"configurable": {"thread_id": "ex-weekly"}})
    print("weekly:", final["status"], "send:", final.get("send_state"))

    # --- copy showcase artifacts to examples/ root
    import shutil
    daily_html = OUT / "data" / "reports" / "daily" / f"daily-{WEEK_START}.html"
    daily_json = OUT / "data" / "reports" / "daily" / f"daily-{WEEK_START}.json"
    weekly_html = OUT / "data" / "reports" / "weekly" / f"weekly-{WEEK_START}.html"
    weekly_json = OUT / "data" / "reports" / "weekly" / f"weekly-{WEEK_START}.json"
    weekly_pdf = OUT / "data" / "reports" / "weekly" / f"weekly-{WEEK_START}.pdf"
    for src, name in [(daily_html, "example-daily-2026-10-05.html"),
                      (daily_json, "example-daily-2026-10-05.json"),
                      (weekly_html, "example-weekly-2026-10-05.html"),
                      (weekly_json, "example-weekly-2026-10-05.json"),
                      (weekly_pdf, "example-weekly-2026-10-05.pdf")]:
        shutil.copy(src, ROOT / "examples" / name)
    journal = db._conn.execute(
        "SELECT digest_type, period, state, provider_id FROM send_journal").fetchall()
    (ROOT / "examples" / "example-send-journal.json").write_text(
        json.dumps([dict(r) for r in journal], indent=2), encoding="utf-8")
    db.close()
    print("examples written to", ROOT / "examples")


if __name__ == "__main__":
    main()
