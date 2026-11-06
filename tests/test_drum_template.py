from __future__ import annotations

import json

import pytest
from openpyxl import Workbook
from openpyxl import load_workbook

from analyst_agent.drum_report import drum_report_html
from analyst_agent.drum_template import (analyze_drum_study, answer_drum_followup,
                                         build_drum_study, interpret_drum_study, _drum_model,
                                         study_evidence, study_findings)
from analyst_agent.config import LLMConfig
from analyst_agent.llm import Completion
from analyst_agent.llm import LLMClient, LLMError
from analyst_agent.workspace import Workspace


def _book(path, suffix: str, factor: float):
    book = Workbook()
    summary = book.active
    summary.title = "Stiffness Report" + suffix
    summary["A12"], summary["G12"], summary["H12"], summary["I12"] = "Test IP", "123 PSI", "Speed", "40kmph"
    summary["A13"], summary["B13"] = "Load(W) in daN", "CS (N/deg)"
    for row, load in enumerate((1000, 1500), 14):
        summary.cell(row, 1, load)
        summary.cell(row, 2, factor * load / 10)
    for output in ("Fy", "Fx", "Mx", "Mz"):
        sheet = book.create_sheet(output + suffix)
        for column, value in ((15, "SLIP"), (16, 1000), (17, 1500)):
            sheet.cell(35, column, value)
        for row, slip in enumerate((-1, 0, 1, 3), 36):
            sheet.cell(row, 15, slip)
            for column, load in ((16, 1000), (17, 1500)):
                value = -factor * load / 10 * slip
                if output == "Fx":
                    value /= 10
                if output == "Mx":
                    value *= 2
                if output == "Mz":
                    value /= 5
                sheet.cell(row, column, value)
    book.save(path)


class Model:
    def __init__(self):
        self.calls = []

    def chat(self, messages):
        self.calls.append(messages)
        return Completion(content="The measured near-zero stiffness differs. Field response needs validation.")


def test_drum_template_matches_suffix_loads_and_preserves_evidence(tmp_path):
    _book(tmp_path / "Apollo.xlsx", "_APL", 2.0)
    _book(tmp_path / "JK.xlsx", "", 1.0)
    workspace = Workspace(tmp_path)
    files = ["Apollo.xlsx", "JK.xlsx"]
    study = build_drum_study(workspace, files, "Compare steering response")
    assert study.status == "calculated"
    assert study.common_loads == [1000, 1500]
    assert study.sheets[files[0]]["Fy"] == "Fy_APL"
    assert len(study.metrics) == 16
    assert len(study.charts) == 9
    fy = next(chart for chart in study.charts if chart.group == "Fy")
    menu = fy.figure.layout.updatemenus[0]
    assert [button.label for button in menu.buttons] == ["1000 daN", "1500 daN"]
    assert [trace.visible for trace in fy.figure.data] == [True, True, False, False]
    assert list(menu.buttons[1].args[0]["visible"]) == [False, False, True, True]
    assert menu.buttons[1].args[1]["title.text"] == "Lateral force Fy at 1500 daN"
    assert len(study.comparison) == 12
    assert "100.0% higher" in study_findings(study)[0]
    assert study.curves["Fy"][files[0]][1000].loc[-1] == 200
    assert study.curves["Fy"][files[1]][1000].loc[-1] == 100


def test_drum_model_followup_and_offline_html(tmp_path):
    _book(tmp_path / "a.xlsx", "", 2.0)
    _book(tmp_path / "b.xlsx", "", 1.0)
    model = Model()
    study = analyze_drum_study(model, Workspace(tmp_path), ["a.xlsx", "b.xlsx"],
                               "Compare Fy and steering feel")
    assert study.status == "answered"
    assert len(model.calls) == 2
    assert "Compare Fy and steering feel" in model.calls[0][-1]["content"]
    assert "draft" in model.calls[1][-1]["content"]
    answer = answer_drum_followup(model, study, "What about 1500 daN?")
    assert "near-zero stiffness" in answer
    report = drum_report_html(study, [("What about 1500 daN?", answer)])
    assert b"What about 1500 daN?" in report
    assert b"Fy/Fz" in report
    assert report.count(b"plotly.js v") == 1
    assert b"<script" in report
    assert b'"updatemenus"' in report


def test_drum_report_formats_model_tables_and_field_validation(tmp_path):
    _book(tmp_path / "a.xlsx", "", 2.0)
    _book(tmp_path / "b.xlsx", "", 1.0)
    study = build_drum_study(Workspace(tmp_path), ["a.xlsx", "b.xlsx"], "Compare")
    study.status = "answered"
    study.answer = ("### Measured results\n\n| Load | Peak |Fy| | Result |\n"
                    "|---|---|---|\n| 1000 | 200 | higher |")
    report = drum_report_html(study).decode()
    assert '<h3>Measured results</h3>' in report
    assert '<th scope="col">Peak |Fy|</th>' in report
    assert '<td>higher</td>' in report
    assert 'class="chartwrap"' in report
    assert 'width: min(100%, 820px)' in report
    from analyst_agent.branding import Branding

    branded = drum_report_html(study, branding=Branding(organisation="Apollo Tyres Ltd", author="Dr A",
                                                        notice="For internal use only.")).decode()
    assert "Generated at Apollo Tyres Ltd by Dr A on" in branded and "For internal use only." in branded
    assert "Initial steering response" in report
    assert "Validation sequence and decision rule" in report
    assert "paired vehicle measurements" in report


def test_drum_interpretation_reserves_reasoning_time_and_tokens():
    model = LLMClient(LLMConfig(max_tokens=1024, read_timeout=180, context_tokens=32768,
                                no_think=True))
    chosen = _drum_model(model, [{"role": "user", "content": "Compare the measured tyres."}])
    assert chosen.config.max_tokens >= 12288
    assert chosen.config.read_timeout >= 1800
    assert chosen.config.no_think is False
    assert model.config.max_tokens == 1024


def test_model_evidence_is_compact_and_keeps_matched_load_facts(tmp_path):
    _book(tmp_path / "a.xlsx", "", 2.0)
    _book(tmp_path / "b.xlsx", "", 1.0)
    study = build_drum_study(Workspace(tmp_path), ["a.xlsx", "b.xlsx"], "Compare Fy")
    evidence = json.loads(study_evidence(study, "What happens at 1500 daN?"))
    assert evidence["question"] == "What happens at 1500 daN?"
    assert evidence["files"][0]["sheets"]["Fy"] == "Fy"
    assert {row["load_daN"] for row in evidence["metrics_at_matched_loads"]} == {1000, 1500}
    assert any(row.get("fy_stiffness_source_force_per_deg") == 300 for row in
               evidence["metrics_at_matched_loads"])
    stiffness = next(row for row in evidence["paired_differences"]
                     if row["metric"].startswith("Cornering stiffness") and row["load_daN"] == 1000)
    assert stiffness["baseline_vs_comparison_percent"] == 100.0
    assert stiffness["comparison_vs_baseline_percent"] == -50.0
    assert all("peak_is_sweep_boundary" in row for row in evidence["metrics_at_matched_loads"]
               if row["output"] == "Fy")
    assert all("File" not in row and "Source sheet" not in row for row in
               evidence["metrics_at_matched_loads"])
    old_size = len(study.metrics.to_json(orient="records")) + len(study.comparison.to_json(orient="records"))
    assert len(study_evidence(study)) < old_size


def test_optional_output_can_be_missing_and_unverified_load_is_not_normalized(tmp_path):
    _book(tmp_path / "a.xlsx", "", 2.0)
    _book(tmp_path / "b.xlsx", "", 1.0)
    book = load_workbook(tmp_path / "b.xlsx")
    del book["Mx"]
    del book["Stiffness Report"]
    book.save(tmp_path / "b.xlsx")
    study = build_drum_study(Workspace(tmp_path), ["a.xlsx", "b.xlsx"], "Compare available outputs")
    assert study.status == "calculated"
    assert all(chart.group != "Normalized Fy" for chart in study.charts)
    assert all(chart.group != "Mx" for chart in study.charts)
    assert any("Fy/Fz normalization is omitted" in check for check in study.checks)


def test_model_failure_keeps_calculated_result(tmp_path):
    _book(tmp_path / "a.xlsx", "", 2.0)
    _book(tmp_path / "b.xlsx", "", 1.0)

    class Offline:
        def chat(self, messages):
            raise LLMError("Model unavailable")

    study = analyze_drum_study(Offline(), Workspace(tmp_path), ["a.xlsx", "b.xlsx"], "Compare")
    assert study.status == "calculated"
    assert len(study.charts) == 9
    assert "Model unavailable" in study.model_error
    with pytest.raises(ValueError, match="model interpretation"):
        drum_report_html(study)
    charts = study.charts
    interpret_drum_study(Model(), study)
    assert study.status == "answered"
    assert study.model_error == ""
    assert study.charts is charts


def test_failed_interpretation_review_blocks_complete_report(tmp_path):
    _book(tmp_path / "a.xlsx", "", 2.0)
    _book(tmp_path / "b.xlsx", "", 1.0)

    class ReviewFails:
        calls = 0

        def chat(self, messages):
            self.calls += 1
            if self.calls == 2:
                raise LLMError("Review unavailable")
            return Completion(content="Draft interpretation")

    study = analyze_drum_study(ReviewFails(), Workspace(tmp_path), ["a.xlsx", "b.xlsx"], "Compare")
    assert study.status == "calculated"
    assert study.answer == ""
    assert study.model_error == "Review unavailable"
    with pytest.raises(ValueError, match="model interpretation"):
        drum_report_html(study)


def _combined_long_book(path, factor):
    book = Workbook()
    sheet = book.active
    sheet.title = "F&M measured results"
    headers = ["Vertical Load (N)", "Slip Angle (deg)", "Lateral Force Fy (N)",
               "Longitudinal Force Fx (N)", "Overturning Moment Mx", "Aligning Moment Mz"]
    for column, header in enumerate(headers, 1):
        sheet.cell(9, column, header)
    row = 10
    for load in (10000, 15000):
        for slip in (-1, 0, 1, 3):
            for column, value in enumerate((load, slip, -factor * load / 100 * slip,
                                            factor * slip, factor * load / 200 * slip,
                                            -factor * load / 500 * slip), 1):
                sheet.cell(row, column, value)
            row += 1
    book.save(path)


def test_combined_long_format_and_new_sheet_names(tmp_path):
    _combined_long_book(tmp_path / "a.xlsx", 2.0)
    _combined_long_book(tmp_path / "b.xlsx", 1.0)
    study = build_drum_study(Workspace(tmp_path), ["a.xlsx", "b.xlsx"], "Compare all outputs")
    assert study.common_loads == [1000, 1500]
    assert all(study.sheets["a.xlsx"][output] == "F&M measured results" for output in ("Fy", "Fx", "Mx", "Mz"))
    assert len(study.charts) == 9
    assert study.load_unit_verified == {"a.xlsx": True, "b.xlsx": True}
    assert study.curves["Fy"]["a.xlsx"][1000].loc[-1] == 200


def test_wide_format_accepts_load_units_and_slip_column_in_middle(tmp_path):
    for name, factor in (("a.xlsx", 2), ("b.xlsx", 1)):
        book = Workbook()
        sheet = book.active
        sheet.title = "Lateral force analysis"
        for column, header in enumerate(("Run", "Slip angle (deg)", "Fz=10000 N", "Load 1500 daN"), 1):
            sheet.cell(12, column, header)
        for row, slip in enumerate((-1, 0, 1, 3), 13):
            for column, value in enumerate(("A", slip, -factor * 100 * slip,
                                            -factor * 150 * slip), 1):
                sheet.cell(row, column, value)
        book.save(tmp_path / name)
    study = build_drum_study(Workspace(tmp_path), ["a.xlsx", "b.xlsx"], "Compare Fy")
    assert study.common_loads == [1000, 1500]
    assert study.load_unit_verified == {"a.xlsx": True, "b.xlsx": True}
    assert study.curves["Fy"]["a.xlsx"][1000].loc[1] == -200


def test_model_can_resolve_opaque_sheet_name_then_python_checks_table(tmp_path):
    for name, factor in (("a.xlsx", 2), ("b.xlsx", 1)):
        book = Workbook()
        sheet = book.active
        sheet.title = "Sheet Alpha"
        sheet.append(["Slip angle (deg)", "1000 daN"])
        for slip in (-1, 0, 1, 3):
            sheet.append([slip, -factor * 100 * slip])
        book.save(tmp_path / name)

    class Mapper:
        def __init__(self):
            self.calls = 0

        def chat(self, messages):
            self.calls += 1
            return Completion(content='{"Fy":"Sheet Alpha","Fx":null,"Mx":null,"Mz":null,"Stiffness":null}')

    model = Mapper()
    study = build_drum_study(Workspace(tmp_path), ["a.xlsx", "b.xlsx"], "Compare Fy", model=model)
    assert model.calls == 2
    assert study.common_loads == [1000]
    assert study.curves["Fy"]["a.xlsx"][1000].loc[-1] == 200
    assert any("was selected as" in check for check in study.checks)


def test_stiffness_cross_check_can_move_to_other_columns_and_sheet(tmp_path):
    for name, factor in (("a.xlsx", 2.0), ("b.xlsx", 1.0)):
        _book(tmp_path / name, "", factor)
        book = load_workbook(tmp_path / name)
        summary = book["Stiffness Report"]
        summary.title = "Overview"
        for row in (13, 14, 15):
            summary.cell(row, 3, summary.cell(row, 1).value)
            summary.cell(row, 4, summary.cell(row, 2).value)
            summary.cell(row, 1).value = None
            summary.cell(row, 2).value = None
        book.save(tmp_path / name)
    study = build_drum_study(Workspace(tmp_path), ["a.xlsx", "b.xlsx"], "Compare")
    assert study.sheets["a.xlsx"]["Stiffness"] == "Overview"
    assert study.load_unit_verified == {"a.xlsx": True, "b.xlsx": True}
    row = study.metrics[(study.metrics["Output"] == "Fy") & (study.metrics["Tyre"] == "a")].iloc[0]
    assert row["Reported CS (source value)"] == row["Cornering stiffness from Fy (source force/deg)"]


def test_multiple_wide_output_blocks_on_one_worksheet_do_not_mix(tmp_path):
    for name, factor in (("a.xlsx", 2.0), ("b.xlsx", 1.0)):
        book = Workbook()
        sheet = book.active
        sheet.title = "Results"
        for block, output in enumerate(("Fy", "Fx", "Mx", "Mz")):
            title_row = 5 + block * 12
            sheet.cell(title_row, 1, f"{output} measured sweep")
            sheet.cell(title_row + 2, 1, "Slip angle (deg)")
            sheet.cell(title_row + 2, 2, "1000 daN")
            sheet.cell(title_row + 2, 3, "1500 daN")
            scale = {"Fy": 1, "Fx": 2, "Mx": 3, "Mz": 4}[output]
            for row, slip in enumerate((-1, 0, 1, 3), title_row + 3):
                sheet.cell(row, 1, slip)
                sheet.cell(row, 2, -factor * scale * 100 * slip)
                sheet.cell(row, 3, -factor * scale * 150 * slip)
        book.save(tmp_path / name)
    study = build_drum_study(Workspace(tmp_path), ["a.xlsx", "b.xlsx"], "Compare four sweeps")
    assert all(study.sheets["a.xlsx"][output] == "Results" for output in ("Fy", "Fx", "Mx", "Mz"))
    assert study.curves["Fy"]["a.xlsx"][1000].loc[-1] == 200
    assert study.curves["Mz"]["a.xlsx"][1000].loc[-1] == 800
    assert len(study.charts) == 9
