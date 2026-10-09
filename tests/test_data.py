import io

import pandas as pd
import pytest

from analyst_agent.data import DataLoadError, column_summary, describe_for_llm, list_sheets, load_dataset


def test_semicolon_delimited_latin1_csv():
    raw = "Stadt;Umsatz;Datum\nKöln;1.234;2024-01-01\nMünchen;99;2024-02-01\n".encode("cp1252")
    ds = load_dataset(raw, "DATA.CSV")
    assert list(ds.df.columns) == ["Stadt", "Umsatz", "Datum"]
    assert ds.df.loc[0, "Stadt"] == "Köln"
    assert any("semicolon" in n for n in ds.notes)
    assert len(ds.sha256) == 64


def test_tsv_and_header_cleanup():
    raw = b" a \ta\t\n1\t2\t3\n\t\t\n4\t5\t6\n"
    ds = load_dataset(raw, "x.tsv")
    assert list(ds.df.columns) == ["a", "a_1", "column_3"]
    assert len(ds.df) == 2  # the blank row is dropped
    assert any("Renamed" in n for n in ds.notes)
    assert any("empty rows" in n for n in ds.notes)


def test_excel_with_sheets():
    pytest.importorskip("openpyxl")
    buf = io.BytesIO()
    with pd.ExcelWriter(buf) as writer:
        pd.DataFrame({"a": [1, 2]}).to_excel(writer, sheet_name="first", index=False)
        pd.DataFrame({"b": [3, 4, 5]}).to_excel(writer, sheet_name="second", index=False)
    raw = buf.getvalue()
    assert list_sheets(raw, "book.xlsx") == ["first", "second"]
    ds = load_dataset(raw, "book.xlsx", sheet="second")
    assert list(ds.df.columns) == ["b"] and len(ds.df) == 3 and ds.sheet == "second"


def test_json_records_and_lines():
    assert len(load_dataset(b'[{"a": 1}, {"a": 2}]', "x.json").df) == 2
    assert len(load_dataset(b'{"a": 1}\n{"a": 2}\n{"a": 3}\n', "x.jsonl").df) == 3


@pytest.mark.parametrize("raw,name", [(b"", "x.csv"), (b"a,b\n1,2", "x.docx")])
def test_bad_inputs_raise_clear_errors(raw, name):
    with pytest.raises(DataLoadError):
        load_dataset(raw, name)


def test_column_summary_hints():
    df = pd.DataFrame(
        {
            "amount": ["$1,200", "$350", "$75", "$1,000"] * 10,
            "when": ["2024-01-05", "2024-02-11", "2024-03-01", "2024-04-20"] * 10,
            "id": [f"id{i}" for i in range(40)],
            "x": range(40),
        }
    )
    summary = column_summary(df).set_index("column")
    assert "numbers stored as text" in summary.loc["amount", "hint"]
    assert "dates stored as text" in summary.loc["when", "hint"]
    assert "identifier" in summary.loc["id", "hint"]
    assert "mean" in summary.loc["x", "summary"]


def test_profile_mentions_every_column_and_sample(dataset):
    text = describe_for_llm(dataset, sample_rows=3)
    for col in dataset.df.columns:
        assert repr(col) in text
    assert "6 rows × 4 columns" in text
    assert "First 3 rows" in text


def test_profile_truncates_wide_frames(dataset):
    wide = dataset.df.copy()
    for i in range(20):
        wide[f"extra_{i}"] = i
    dataset.df = wide
    text = describe_for_llm(dataset, max_columns=5)
    assert "plus 19 more columns" in text and "'extra_19'" in text


def test_two_row_header_with_labels_and_units_is_merged():
    raw = (
        "Tyre Size Details,Load,,Peak\n"
        ",[kN],SR @ \u00b5peak,\u00b5peak\n"
        "225/50R19 96V,4.0,0.15,1.10\n"
        "205/65R16 95H,4.5,0.11,1.05\n"
        "225/50R19 96V,5.0,0.16,1.08\n"
    ).encode()
    ds = load_dataset(raw, "tyres.csv")
    assert list(ds.df.columns) == ["Tyre Size Details", "Load [kN]", "SR @ \u00b5peak", "Peak \u00b5peak"]
    assert pd.api.types.is_float_dtype(ds.df["SR @ \u00b5peak"]) and len(ds.df) == 3
    assert any("header continuation" in n for n in ds.notes)


@pytest.mark.parametrize(
    "raw",
    [
        b"name,score\nAlice,5\nBob,7\nCara,9\n",  # first row is ordinary data
        b",name,city\n0,Alice,Paris\n1,Bob,Rome\n2,Cara,Oslo\n",  # saved index column
        b"a,b\nx,y\nz,w\nq,r\n",  # all-text table
    ],
)
def test_normal_files_are_not_treated_as_multi_row_headers(raw):
    ds = load_dataset(raw, "x.csv")
    assert not any("header continuation" in n for n in ds.notes)
    assert len(ds.df) == 3
