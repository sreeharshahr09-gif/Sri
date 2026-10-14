"""End-to-end daily graph with fixture search/LLM and fake web: dedup,
quarantine, injection handling, background labelling, edition persistence,
preview delivery, rerun idempotency."""
import json
from datetime import date

from tire_scout.graphs.daily import build_daily_graph

EDITION = date(2026, 10, 11)


def _run(deps, thread="daily-test-1"):
    graph = build_daily_graph(deps)  # no checkpointer needed for single-process test
    return graph.invoke({"edition_date": str(EDITION), "mode": "preview",
                         "errors": [], "limitations": [], "excluded": [],
                         "developments": []},
                        config={"configurable": {"thread_id": thread}})


def test_daily_end_to_end(cfg, db, deps, tmp_path):
    final = _run(deps)
    assert final["status"] == "research_complete", final.get("errors")

    # edition persisted with coverage summary
    row = db.daily_edition(EDITION)
    assert row and row["status"] == "research_complete"
    coverage = json.loads(row["coverage_summary"])
    assert coverage["queries"] == 4 and coverage["fetched"] >= 6

    devs = db.developments_for_edition(EDITION)
    urls = {d["canonical_url"] for d in devs}

    # syndicated copy merged - only one of the two Evonik URLs survives
    evonik = [d for d in devs if "evonik.com" in d["canonical_url"]]
    rubbernews = [d for d in devs if "rubbernews.com" in d["canonical_url"]]
    assert len(evonik) == 1 and not rubbernews

    # old review labeled background, not presented as a new event
    mdpi = [d for d in devs if "mdpi.com" in d["canonical_url"]]
    assert mdpi and mdpi[0]["is_background"]
    assert mdpi[0]["id"] not in final["included_ids"]
    report = json.loads(open(final["render_paths"]["json"], encoding="utf-8").read())
    assert all(d["id"] != mdpi[0]["id"] for d in report["developments"])
    assert len(report["developments"]) == coverage["included"]

    # unsupported media never analyzed
    assert not any("youtube.com" in u for u in urls)

    # malformed model output quarantined, not silently guessed
    all_devs = db.developments_for_edition(EDITION, include_quarantined=True)
    quarantined = [d for d in all_devs if d["status"] == "quarantined"]
    assert any("bad-vendor" in d["canonical_url"] for d in quarantined)

    # injected instructions flagged; analysis still completed
    blog = [d for d in devs if "tiretech-blog" in d["canonical_url"]]
    assert blog and blog[0]["injection_flag"]

    # scoring computed in trusted code with recorded versions
    assert all(d["final_score"] is not None and d["rubric_version"] == "rubric-1.0"
               for d in devs)
    assert evonik[0]["final_score"] == round(9*.35 + 7*.25 + 6*.20 + 7*.10 + 8*.10, 3)

    # report rendered, preview email written, journal recorded
    assert final["send_state"] == "preview"
    outbox = list((tmp_path / "data" / "outbox").glob("*.eml"))
    assert outbox, "preview EML missing"
    eml = outbox[0].read_text(errors="replace")
    assert "attacker@gmail.com" not in eml
    journal = db._conn.execute("SELECT * FROM send_journal").fetchall()
    assert len(journal) == 1 and journal[0]["state"] == "preview"
    assert "apollotyres.com" in journal[0]["recipients"]


def test_daily_rerun_is_idempotent(cfg, db, deps):
    _run(deps, thread="t1")
    n1 = len(db.developments_for_edition(EDITION, include_quarantined=True))
    final = _run(deps, thread="t2")
    n2 = len(db.developments_for_edition(EDITION, include_quarantined=True))
    assert n2 == n1  # dedup against prior index blocks re-inserts
    journal = db._conn.execute("SELECT COUNT(*) c FROM send_journal").fetchone()["c"]
    assert journal == 1  # same send identity, no second row


def test_zero_update_day_is_distinct_from_failure(cfg, db, deps):
    # no search results at all -> genuine no-news day still gets a valid edition
    deps.search_fixture = None
    class EmptySearch:
        name = "fixture"
        def search(self, query, *, max_results, recency_days=None):
            return []
    deps.tools.search = EmptySearch()
    final = _run(deps, thread="empty-day")
    assert final["status"] == "no_updates"
    row = db.daily_edition(EDITION)
    assert row["status"] == "no_updates"
    cov = db.edition_coverage(date(2026, 10, 5))  # week containing EDITION (Sun)
    assert date(2026, 10, 11) in cov["present"]


def test_all_analysis_failures_are_not_a_no_news_day(cfg, db, deps):
    from tire_scout.llm import LLMContractError
    def invalid(**kwargs):
        raise LLMContractError("deliberate malformed output")
    deps.llm.analyze = invalid
    cfg.email.live_sending = cfg.email.daily_enabled = True
    final = build_daily_graph(deps).invoke({"edition_date": str(EDITION), "mode": "production"})
    assert final["status"] == "failed"
    assert final["send_state"] == "blocked:research failed"
    assert db.daily_edition(EDITION)["status"] == "failed"
    assert "Research failed" in open(final["render_paths"]["html"], encoding="utf-8").read()


def test_useful_not_new_material_is_retained_as_background(cfg, db, deps):
    original = deps.llm.analyze
    def background(**kwargs):
        result = original(**kwargs)
        if "evonik.com" in kwargs["doc_meta"]["canonical_url"]:
            result.is_new_development = False
        return result
    deps.llm.analyze = background
    final = _run(deps)
    dev = next(d for d in db.developments_for_edition(EDITION) if "evonik.com" in d["canonical_url"])
    assert dev["is_background"] and dev["id"] in final["background_ids"]
    assert dev["id"] not in final["included_ids"]
