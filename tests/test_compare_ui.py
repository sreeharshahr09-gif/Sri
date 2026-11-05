from __future__ import annotations

import json
import time

from streamlit.testing.v1 import AppTest

from .test_compare import _report
from .test_drum_template import _book


def test_comparison_view_selects_real_table_and_draws_charts(tmp_path):
    _report(tmp_path / "apollo.xlsx", [2090, 2786, 3483],
            [(-1, [9, 10, 20]), (0, [0, 0, 0]), (1, [-9, -10, -20])])
    _report(tmp_path / "jk.xlsx", [2786, 3483],
            [(-1, [7, 18]), (0, [0, 0]), (1, [-7, -18])])
    source = ("from analyst_agent.workspace import Workspace\n"
              "from ui.compare_files import render_compare_files\n"
              f"render_compare_files(Workspace({str(tmp_path)!r}))")
    app = AppTest.from_string(source).run(timeout=15)
    assert not app.exception
    assert [item.value for item in app.selectbox if item.label == "Worksheet"] == ["Fy", "Fy"]
    assert [item.value for item in app.number_input if item.label == "Header row"] == [35, 35]
    app.button(key="cmp_run").click().run(timeout=15)
    assert not app.exception
    assert len(app.get("plotly_chart")) == 2


def test_ai_help_appears_after_a_calculated_comparison(tmp_path):
    _report(tmp_path / "a.xlsx", [2786], [(-1, [10]), (0, [0]), (1, [-10])])
    _report(tmp_path / "b.xlsx", [2786], [(-1, [8]), (0, [0]), (1, [-8])])
    source = ("from analyst_agent.workspace import Workspace\n"
              "from analyst_agent.config import LLMConfig\n"
              "from ui.compare_files import render_compare_files\n"
              f"render_compare_files(Workspace({str(tmp_path)!r}), LLMConfig())")
    app = AppTest.from_string(source).run(timeout=15)
    app.selectbox(key="ws_compare_template").set_value("General comparison").run(timeout=15)
    assert app.button(key="ws_auto_run")
    assert not any(item.label == "Header row" for item in app.number_input)
    app.checkbox(key="ws_manual_mode").check().run(timeout=15)
    app.button(key="cmp_run").click().run(timeout=15)
    assert not app.exception
    assert any("Ask AI about this comparison" in item.value for item in app.subheader)
    assert app.chat_input


def test_default_ai_flow_creates_graphs_without_table_inputs(tmp_path):
    _report(tmp_path / "a.xlsx", [2786], [(-1, [10]), (0, [0]), (1, [-10])])
    _report(tmp_path / "b.xlsx", [2786], [(-1, [8]), (0, [0]), (1, [-8])])
    files = ["a.xlsx", "b.xlsx"]
    chosen = {"groups": [{"title": "Fy", "sources": [{"file": f, "sheet": "Fy"} for f in files]}]}
    planned = {"comparisons": [{"title": "Fy", "sources": [
        {"file": f, "sheet": "Fy", "candidate": 0, "key": "SLIP", "values": {"2786": "2786"}}
        for f in files]}]}
    answers = [json.dumps(chosen), json.dumps(planned), "The calculated curves differ."]
    source = ("from analyst_agent.workspace import Workspace\n"
              "from analyst_agent.config import LLMConfig\n"
              "from analyst_agent.llm import Completion\n"
              "from ui import compare_files\n"
              "class Fake:\n"
              f"    def __init__(self): self.replies = iter({answers!r})\n"
              "    def chat(self, messages): return Completion(content=next(self.replies))\n"
              "compare_files.LLMClient = lambda cfg: Fake()\n"
              f"compare_files.render_compare_files(Workspace({str(tmp_path)!r}), LLMConfig())")
    app = AppTest.from_string(source).run(timeout=15)
    app.selectbox(key="ws_compare_template").set_value("General comparison").run(timeout=15)
    assert not any(item.label == "Header row" for item in app.number_input)
    app.button(key="ws_auto_run").click().run(timeout=15)
    for _ in range(20):
        if app.get("plotly_chart"):
            break
        time.sleep(0.1)
        app.run(timeout=15)
    assert not app.exception
    assert len(app.get("plotly_chart")) == 1


def test_drum_tab_shows_one_load_selectable_graph_per_variable(tmp_path):
    _book(tmp_path / "a.xlsx", "", 2.0)
    _book(tmp_path / "b.xlsx", "", 1.0)
    source = ("from analyst_agent.workspace import Workspace\n"
              "from analyst_agent.config import LLMConfig\n"
              "from analyst_agent.llm import Completion\n"
              "from ui import compare_files\n"
              "class Fake:\n"
              "    def chat(self, messages): return Completion(content='Checked engineering interpretation')\n"
              "compare_files.LLMClient = lambda cfg: Fake()\n"
              f"compare_files.render_compare_files(Workspace({str(tmp_path)!r}), LLMConfig())")
    app = AppTest.from_string(source).run(timeout=20)
    assert app.selectbox(key="ws_compare_template").value == "Drum / FLAT TRAC R&D"
    app.button(key="ws_drum_run").click().run(timeout=20)
    for _ in range(30):
        if app.session_state.ws_drum_result is not None:
            break
        time.sleep(0.2)
        app.run(timeout=20)
    assert not app.exception
    study = app.session_state.ws_drum_result[1]
    assert study.status == "answered"
    assert len(study.charts) == 9
    app.selectbox(key="ws_drum_graph_family").set_value("Fy").run(timeout=20)
    assert len(app.get("plotly_chart")) == 1
    assert len(next(chart for chart in study.charts if chart.group == "Fy").figure.data) == 4
