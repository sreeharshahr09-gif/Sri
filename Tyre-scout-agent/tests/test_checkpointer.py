"""Persistent checkpoints: SqliteSaver (not in-memory) survives a rebuild."""
import sqlite3
from datetime import date

from langgraph.checkpoint.sqlite import SqliteSaver

from tire_scout.graphs.daily import build_daily_graph

EDITION = date(2026, 10, 11)


def test_graph_runs_with_sqlite_checkpointer(deps, tmp_path):
    conn = sqlite3.connect(str(tmp_path / "checkpoints.sqlite"), check_same_thread=False)
    saver = SqliteSaver(conn)
    graph = build_daily_graph(deps, checkpointer=saver)
    cfg = {"configurable": {"thread_id": f"daily-{EDITION}"}}
    final = graph.invoke({"edition_date": str(EDITION), "mode": "preview",
                          "errors": [], "limitations": [], "excluded": [], "developments": []},
                         config=cfg)
    assert final["status"] == "research_complete"
    state = graph.get_state(cfg)
    assert state.values["edition_date"] == str(EDITION)
    # checkpoint rows persisted in the file
    n = conn.execute("SELECT COUNT(*) FROM checkpoints").fetchone()[0]
    assert n > 0
