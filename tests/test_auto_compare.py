from __future__ import annotations

import json

from analyst_agent.auto_compare import run_auto_compare
from analyst_agent.llm import Completion
from analyst_agent.workspace import Workspace
from openpyxl import Workbook
from .test_compare import _report


class ScriptedModel:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.messages = []

    def chat(self, messages):
        self.messages.append(messages)
        return Completion(content=next(self.replies))


def test_auto_compare_selects_sheets_and_draws_without_manual_mapping(tmp_path):
    _report(tmp_path / "apollo.xlsx", [2786], [(-1, [10]), (0, [0]), (1, [-10])])
    _report(tmp_path / "jk.xlsx", [2786], [(-1, [8]), (0, [0]), (1, [-8])])
    files = ["apollo.xlsx", "jk.xlsx"]
    chosen = {"groups": [{"title": "Cornering force", "sources":
                          [{"file": file, "sheet": "Fy"} for file in files]}]}
    planned = {"comparisons": [{"title": "Fy at 2786", "key_type": "numeric",
                                "x_label": "Slip angle", "y_label": "Fy",
                                "sources": [{"file": file, "sheet": "Fy", "candidate": 0,
                                             "key": "SLIP", "values": {"2786 daN": "2786"}}
                                            for file in files]}]}
    model = ScriptedModel([json.dumps(chosen), json.dumps(planned),
                           "At matched slip angles, Apollo has greater absolute Fy at ±1°. "])
    run = run_auto_compare(model, Workspace(tmp_path), files, "Compare tyre performance")
    assert run.status == "answered", run.error
    assert len(model.messages) == 3
    assert len(run.charts) == 1
    assert len(run.charts[0].result.differences) == 3
    assert run.charts[0].result.curves["2786 daN"].loc[-1, "apollo.xlsx"] == 10


def test_invalid_model_mapping_gets_feedback_and_retry(tmp_path):
    _report(tmp_path / "a.xlsx", [2786], [(-1, [10]), (0, [0]), (1, [-10])])
    _report(tmp_path / "b.xlsx", [2786], [(-1, [8]), (0, [0]), (1, [-8])])
    files = ["a.xlsx", "b.xlsx"]
    chosen = {"groups": [{"title": "Fy", "sources": [{"file": file, "sheet": "Fy"} for file in files]}]}
    bad = {"comparisons": [{"title": "Bad", "sources": [
        {"file": file, "sheet": "Fy", "candidate": 0, "key": "wrong", "values": {"Fy": "2786"}}
        for file in files]}]}
    good = {"comparisons": [{"title": "Good", "sources": [
        {"file": file, "sheet": "Fy", "candidate": 0, "key": "SLIP", "values": {"Fy": "2786"}}
        for file in files]}]}
    model = ScriptedModel([json.dumps(chosen), json.dumps(bad), json.dumps(good), "Compared."])
    run = run_auto_compare(model, Workspace(tmp_path), files, "Compare Fy")
    assert run.status == "answered"
    assert len(model.messages) == 4
    assert "failed validation" in model.messages[2][1]["content"]
    assert run.notes


def test_suspicious_unit_conversion_is_not_graphed(tmp_path):
    for name, key_header, value_header, loads, values in (
        ("a.xlsx", "Load (N)", "CS (N/deg)", [100, 200], [1000, 1200]),
        ("b.xlsx", "Load (daN)", "CS (daN/deg)", [10, 20], [900, 1100]),
    ):
        book = Workbook()
        sheet = book.active
        sheet.title = "Stiffness"
        sheet.append([key_header, value_header])
        for load, value in zip(loads, values):
            sheet.append([load, value])
        book.save(tmp_path / name)
    files = ["a.xlsx", "b.xlsx"]
    chosen = {"groups": [{"title": "Stiffness", "sources":
                          [{"file": f, "sheet": "Stiffness"} for f in files]}]}
    planned = {"comparisons": [{"title": "Stiffness", "sources": [
        {"file": "a.xlsx", "sheet": "Stiffness", "candidate": 0, "key": "Load (N)",
         "key_factor": 0.1, "values": {"CS": "CS (N/deg)"}},
        {"file": "b.xlsx", "sheet": "Stiffness", "candidate": 0, "key": "Load (daN)",
         "value_factor": 10, "values": {"CS": "CS (daN/deg)"}},
    ]}]}
    model = ScriptedModel([json.dumps(chosen)] + [json.dumps(planned)] * 3)
    run = run_auto_compare(model, Workspace(tmp_path), files, "Compare stiffness")
    assert run.status == "error"
    assert not run.charts
    assert "unit conversion creates a greater than 5x gap" in run.error
