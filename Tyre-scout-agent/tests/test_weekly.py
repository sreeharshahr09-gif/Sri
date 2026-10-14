"""Weekly coverage gate: 7-day completeness, blocking, partial override,
cluster merge, trusted re-ranking, rendered outputs."""
import json
from datetime import date, timedelta

from tire_scout.graphs.weekly import WeeklyDeps, build_weekly_graph

from conftest import make_development, seed_edition

WEEK_START = date(2026, 10, 5)  # Monday


def _seed_full_week(db, with_devs=True):
    for i in range(7):
        seed_edition(db, WEEK_START + timedelta(days=i))
    if with_devs:
        db.insert_development(make_development(
            "dev-week-1", WEEK_START, 9.0, "Evonik next-generation silane system"))
        db.insert_development(make_development(
            "dev-week-2", WEEK_START + timedelta(days=2), 7.0,
            "Bekaert higher-tensile steel cord for EV belts",
            category="Reinforcement and construction"))
        db.insert_development(make_development(
            "dev-week-3", WEEK_START + timedelta(days=5), 6.0,
            "HF tandem mixing energy paper", category="Manufacturing and process"))


def _run(deps_cfg_db, llm, **overrides):
    cfg, db = deps_cfg_db
    graph = build_weekly_graph(WeeklyDeps(cfg=cfg, db=db, llm=llm, tz=None))
    init = {"week_start": str(WEEK_START), "mode": "preview", "errors": [],
            "limitations": [], **overrides}
    return graph.invoke(init, config={"configurable": {"thread_id": "w1"}})


def test_complete_week_passes_and_ranks_by_trusted_score(cfg, db):
    from tire_scout.llm import FixtureLLMClient
    from pathlib import Path
    _seed_full_week(db)
    llm = FixtureLLMClient(Path(__file__).parent / "fixtures" / "llm" / "analyses.json")
    final = _run((cfg, db), llm)
    assert final["status"] == "complete"
    # LLM suggested dev-week-2 first; trusted scores keep dev-week-1 (9.0) first
    assert final["priority_ids"][0] == "dev-week-1"
    assert set(final["priority_ids"]) == {"dev-week-1", "dev-week-2", "dev-week-3"}
    assert len(final["themes"]) == 2 and len(final["team_actions"]) == 2
    row = db._conn.execute("SELECT * FROM weekly_editions WHERE week_start=?",
                           (str(WEEK_START),)).fetchone()
    assert row["status"] == "complete"
    journal = db._conn.execute("SELECT * FROM send_journal WHERE digest_type='weekly'").fetchall()
    assert len(journal) == 1 and journal[0]["state"] == "preview"


def test_missing_one_edition_blocks_and_saves_partial_preview(cfg, db):
    from tire_scout.llm import FixtureLLMClient
    from pathlib import Path
    _seed_full_week(db)
    db._conn.execute("DELETE FROM daily_editions WHERE edition_date=?",
                     (str(WEEK_START + timedelta(days=3)),))
    llm = FixtureLLMClient(Path(__file__).parent / "fixtures" / "llm" / "analyses.json")
    final = _run((cfg, db), llm)
    assert final["status"] == "partial_blocked"
    assert str(WEEK_START + timedelta(days=3)) in final["missing_dates"]
    assert "blocked" in final["send_state"]
    # a clearly identified local partial preview exists on disk
    html = final["render_paths"]["html"]
    content = open(html, encoding="utf-8").read()
    assert "PARTIAL ISSUE" in content
    # journal records the blocked decision; nothing was submitted
    journal = db._conn.execute("SELECT * FROM send_journal WHERE digest_type='weekly'").fetchall()
    assert len(journal) == 1 and journal[0]["state"] == "blocked"


def test_failed_search_day_does_not_count_as_coverage(cfg, db):
    from tire_scout.llm import FixtureLLMClient
    from pathlib import Path
    _seed_full_week(db)
    db._conn.execute("UPDATE daily_editions SET status='failed' WHERE edition_date=?",
                     (str(WEEK_START + timedelta(days=1)),))
    llm = FixtureLLMClient(Path(__file__).parent / "fixtures" / "llm" / "analyses.json")
    final = _run((cfg, db), llm)
    assert final["status"] == "partial_blocked"
    assert str(WEEK_START + timedelta(days=1)) in final["missing_dates"]


def test_no_update_day_counts_toward_coverage(cfg, db):
    from tire_scout.llm import FixtureLLMClient
    from pathlib import Path
    _seed_full_week(db)
    db._conn.execute("UPDATE daily_editions SET status='no_updates' WHERE edition_date=?",
                     (str(WEEK_START),))
    llm = FixtureLLMClient(Path(__file__).parent / "fixtures" / "llm" / "analyses.json")
    final = _run((cfg, db), llm)
    assert final["status"] == "complete"


def test_duplicate_dates_cannot_fill_missing_day(cfg, db):
    for i in range(7):
        seed_edition(db, WEEK_START + timedelta(days=i))
    # trying to re-insert Monday does nothing for Tuesday's absence
    db._conn.execute("DELETE FROM daily_editions WHERE edition_date=?",
                     (str(WEEK_START + timedelta(days=1)),))
    seed_edition(db, WEEK_START)  # upsert on existing Monday
    cov = db.edition_coverage(WEEK_START)
    assert not cov["complete"] and WEEK_START + timedelta(days=1) in cov["missing"]


def test_operator_partial_override_is_labelled(cfg, db):
    from tire_scout.llm import FixtureLLMClient
    from pathlib import Path
    _seed_full_week(db)
    db._conn.execute("DELETE FROM daily_editions WHERE edition_date=?",
                     (str(WEEK_START + timedelta(days=3)),))
    llm = FixtureLLMClient(Path(__file__).parent / "fixtures" / "llm" / "analyses.json")
    final = _run((cfg, db), llm, allow_partial=True)
    assert final["status"] == "partial_sent"
    assert final["send_state"] == "preview"
    outbox_html = next((cfg.general.data_dir / "outbox").glob("*.html"))
    assert "PARTIAL ISSUE" in outbox_html.read_text(encoding="utf-8")


def test_clustered_repeat_coverage_counts_once(cfg, db):
    from tire_scout.llm import FixtureLLMClient
    from pathlib import Path
    _seed_full_week(db)
    cluster = db.ensure_cluster("evonik-silane")
    db.insert_development(make_development(
        "dev-week-4", WEEK_START + timedelta(days=1), 8.5,
        "Evonik silane system - day-2 follow-up", cluster_id=cluster))
    db._conn.execute("UPDATE developments SET cluster_id=? WHERE id='dev-week-1'", (cluster,))
    llm = FixtureLLMClient(Path(__file__).parent / "fixtures" / "llm" / "analyses.json")
    final = _run((cfg, db), llm)
    ids = final["priority_ids"]
    # only the higher-scored member of the cluster is listed
    assert not ({"dev-week-1", "dev-week-4"} <= set(ids))
    assert "dev-week-1" in ids  # 9.0 beats 8.5


def test_weekly_pdf_is_readable(cfg, db, tmp_path):
    from tire_scout.llm import FixtureLLMClient
    from pathlib import Path
    _seed_full_week(db)
    llm = FixtureLLMClient(Path(__file__).parent / "fixtures" / "llm" / "analyses.json")
    final = _run((cfg, db), llm)
    pdf_path = final["render_paths"]["pdf"]
    from pypdf import PdfReader
    reader = PdfReader(pdf_path)
    assert len(reader.pages) >= 2  # content + appendix page break
    text = "\n".join(p.extract_text() or "" for p in reader.pages)
    assert f"Week {WEEK_START}" in text
    assert "Evonik next-generation silane system" in text
    assert "Appendix" in text and "Page 1" in text
    assert reader.outline  # bookmarks present


def test_weekly_cannot_reintroduce_high_score_background_or_undated_items(cfg, db):
    from tire_scout.llm import FixtureLLMClient
    from pathlib import Path
    from datetime import datetime
    _seed_full_week(db)
    old = make_development("old", WEEK_START, 10, "Old patent")
    old.published_at = datetime(2012, 7, 24)
    undated = make_development("undated", WEEK_START, 10, "Undated supplier page")
    undated.published_at = None
    db.insert_development(old)
    db.insert_development(undated)
    llm = FixtureLLMClient(Path(__file__).parent / "fixtures" / "llm" / "analyses.json")
    final = _run((cfg, db), llm)
    assert not {"old", "undated"} & set(final["priority_ids"] + final["additional_ids"])
    html = Path(final["render_paths"]["html"]).read_text(encoding="utf-8")
    assert "Old patent" not in html and "Undated supplier page" not in html
