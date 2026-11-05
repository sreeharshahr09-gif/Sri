from __future__ import annotations

import pandas as pd
import pytest
from openpyxl import Workbook

from analyst_agent.compare import (
    CompareError, ComparisonSource, TableRange, compare_numeric, discover_table_ranges, guess_table_range,
    read_table, sheet_names,
)
from analyst_agent.workspace import Workspace


def _report(path, loads, values):
    book = Workbook()
    book.active.title = "_OPC_CONFIG_"
    sheet = book.create_sheet("Fy")
    sheet.cell(35, 15, "SLIP")
    for column, load in enumerate(loads, 16):
        sheet.cell(35, column, load)
    for row, (slip, observations) in enumerate(values, 36):
        sheet.cell(row, 15, slip)
        for column, value in enumerate(observations, 16):
            if value is not None:
                sheet.cell(row, column, value)
    book.save(path)


def test_report_header_detection_and_shared_loads(tmp_path):
    _report(tmp_path / "apollo.xlsx", [2090, 2786, 3483],
            [(-1, [9, 10, 20]), (0, [0, 0, 0]), (1, [-9, -10, -20])])
    _report(tmp_path / "jk.xlsx", [2786, 3483],
            [(-1, [7, 18]), (0, [None, 0]), (1, [-7, -18])])
    workspace = Workspace(tmp_path)
    assert sheet_names(workspace, "apollo.xlsx") == ["_OPC_CONFIG_", "Fy"]
    apollo_range = guess_table_range(workspace, "apollo.xlsx", "Fy")
    jk_range = guess_table_range(workspace, "jk.xlsx", "Fy")
    assert (apollo_range.header_row, apollo_range.excel_columns) == (35, "O:R")
    assert (jk_range.header_row, jk_range.excel_columns) == (35, "O:Q")
    apollo = read_table(workspace, "apollo.xlsx", "Fy", apollo_range)
    jk = read_table(workspace, "jk.xlsx", "Fy", jk_range)
    assert list(apollo) == ["SLIP", "2090", "2786", "3483"]
    assert list(jk) == ["SLIP", "2786", "3483"]
    result = compare_numeric(
        [ComparisonSource("Apollo", apollo, "SLIP"), ComparisonSource("JK", jk, "SLIP")],
        {"2786": {"Apollo": "2786", "JK": "2786"}},
    )
    assert result.curves["2786"].loc[-1, "Apollo"] == 10
    assert result.curves["2786"].loc[-1, "JK"] == 7
    assert pd.isna(result.curves["2786"].loc[0, "JK"])
    assert len(result.differences) == 2  # the missing point is not invented
    assert result.diagnostics.loc[1, "Missing values"] == 1


def test_unit_conversion_and_explicit_duplicate_policy():
    apollo = pd.DataFrame({"Load N": [100, 200], "CS": [4, 8]})
    jk = pd.DataFrame({"Load daN": [10, 20], "CS daN label": [0.3, 0.7]})
    sources = [ComparisonSource("Apollo", apollo, "Load N", 0.1),
               ComparisonSource("JK", jk, "Load daN", value_factor=10)]
    result = compare_numeric(sources, {"CS": {"Apollo": "CS", "JK": "CS daN label"}})
    assert list(result.curves["CS"].index) == [10, 20]
    assert list(result.differences["Difference"]) == [-1, -1]

    repeated = pd.DataFrame({"x": [1, 1], "y": [2, 4]})
    with pytest.raises(CompareError, match="repeated keys"):
        compare_numeric([ComparisonSource("A", repeated, "x"),
                         ComparisonSource("B", pd.DataFrame({"x": [1], "y": [3]}), "x")],
                        {"y": {"A": "y", "B": "y"}})
    aggregated = compare_numeric([ComparisonSource("A", repeated, "x"),
                                  ComparisonSource("B", pd.DataFrame({"x": [1], "y": [3]}), "x")],
                                 {"y": {"A": "y", "B": "y"}}, duplicate_policy="mean")
    assert aggregated.curves["y"].loc[1, "A"] == 3


def test_file_access_stays_inside_workspace(tmp_path):
    workspace = Workspace(tmp_path)
    with pytest.raises(CompareError, match="outside the workspace"):
        sheet_names(workspace, "../elsewhere.xlsx")


def test_csv_range_uses_actual_column_count(tmp_path):
    (tmp_path / "a.csv").write_text("angle,force\n-1,10\n1,-10\n")
    workspace = Workspace(tmp_path)
    selection = guess_table_range(workspace, "a.csv", None)
    assert selection.excel_columns == "A:B"
    frame = read_table(workspace, "a.csv", None, selection)
    assert list(frame.columns) == ["angle", "force"]


def test_multiple_tables_are_explicit_candidates(tmp_path):
    book = Workbook()
    sheet = book.active
    for row in [("Month", "Sales"), ("Jan", 2), ("Feb", 3)]:
        sheet.append(row)
    sheet.cell(10, 4, "Date")
    sheet.cell(10, 5, "Cost")
    sheet.cell(11, 4, "2026-01-01")
    sheet.cell(11, 5, 7)
    book.save(tmp_path / "tables.xlsx")
    choices = discover_table_ranges(Workspace(tmp_path), "tables.xlsx", "Sheet")
    assert (1, "A:B") in [(r.header_row, r.excel_columns) for r in choices]
    assert (10, "D:E") in [(r.header_row, r.excel_columns) for r in choices]


def test_text_date_and_unmatched_keys():
    a = pd.DataFrame({"id": ["001", "002"], "y": [3, 4]})
    b = pd.DataFrame({"id": ["001", "003"], "y": [2, 5]})
    result = compare_numeric([ComparisonSource("A", a, "id", key_type="text"),
                              ComparisonSource("B", b, "id", key_type="text")],
                             {"y": {"A": "y", "B": "y"}})
    assert list(result.differences["Key"]) == ["001"]
    assert "002" in result.curves["y"].index
    dates = [pd.DataFrame({"date": ["2026-01-01"], "y": [value]}) for value in (3, 2)]
    result = compare_numeric([ComparisonSource(label, frame, "date", key_type="date")
                              for label, frame in zip(("A", "B"), dates)],
                             {"y": {"A": "y", "B": "y"}})
    assert len(result.differences) == 1
    with pytest.raises(CompareError, match="no key with a numeric value"):
        compare_numeric([ComparisonSource("A", a, "id", key_type="text"),
                         ComparisonSource("B", pd.DataFrame({"id": ["004"], "y": [2]}), "id", key_type="text")],
                        {"y": {"A": "y", "B": "y"}})


def test_missing_formula_cache_is_not_plotted(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.append(["x", "y"])
    sheet.append([1, "=1+1"])
    book.save(tmp_path / "formula.xlsx")
    ws = Workspace(tmp_path)
    with pytest.raises(CompareError, match="formula cells may also lack cached results"):
        read_table(ws, "formula.xlsx", "Sheet", TableRange(1, 1, 2))


def test_last_data_row_excludes_a_second_table(tmp_path):
    book = Workbook()
    sheet = book.active
    for row in [("x", "y"), (1, 2), (2, 3), (None, None), ("x", "y"), (3, 8)]:
        sheet.append(row)
    book.save(tmp_path / "stacked.xlsx")
    frame = read_table(Workspace(tmp_path), "stacked.xlsx", "Sheet", TableRange(1, 1, 2, 3))
    assert list(frame["x"]) == [1, 2]


def test_repeated_headers_are_blocked_instead_of_auto_renamed(tmp_path):
    book = Workbook()
    sheet = book.active
    sheet.append(["x", "y", "y"])
    sheet.append([1, 2, 3])
    book.save(tmp_path / "duplicate.xlsx")
    with pytest.raises(CompareError, match="repeated column names"):
        read_table(Workspace(tmp_path), "duplicate.xlsx", "Sheet", TableRange(1, 1, 3))


def test_legacy_excel_dependency_failure_is_explicit(tmp_path, monkeypatch):
    (tmp_path / "legacy.xls").write_bytes(b"placeholder")
    monkeypatch.setattr("analyst_agent.compare.importlib.util.find_spec", lambda name: None)
    with pytest.raises(CompareError, match="requires python-calamine"):
        sheet_names(Workspace(tmp_path), "legacy.xls")
