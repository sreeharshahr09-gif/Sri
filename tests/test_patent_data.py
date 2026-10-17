import numpy as np
import pandas as pd
import pytest

from analyst_agent.patents.data import (
    build_corpus,
    country_of,
    detect_columns,
    first_claim,
    normalize_assignee,
    parse_year,
    split_assignees,
)

from .patent_fixtures import make_export


def test_detects_the_users_export_format():
    cols = ["Record Number", "Publication/Issue Date", "Title", "Abstract", "Assignee",
            "Problem Being Solved (AI Sum.)", "Method Used (AI Sum.)", "Advantages (AI Sum.)", "Claims"]
    m = detect_columns(cols)
    assert m["id"] == "Record Number" and m["pub_date"] == "Publication/Issue Date"
    assert m["title"] == "Title" and m["abstract"] == "Abstract" and m["claims"] == "Claims"
    assert m["assignee"] == "Assignee"
    assert m["problem"] == "Problem Being Solved (AI Sum.)"
    assert m["method"] == "Method Used (AI Sum.)" and m["advantage"] == "Advantages (AI Sum.)"
    assert m["family"] is None and m["priority_date"] is None


@pytest.mark.parametrize(
    "cols,expect",
    [
        # Lens.org
        (["Lens ID", "Title", "Abstract", "Applicants", "Earliest Priority Date", "Publication Date",
          "CPC Classifications", "Simple Family Size", "Cited by Patent Count", "Jurisdiction"],
         {"id": "Lens ID", "assignee": "Applicants", "priority_date": "Earliest Priority Date",
          "pub_date": "Publication Date", "cpc": "CPC Classifications", "country": "Jurisdiction"}),
        # Lens.org with the publication number: that one identifies the patent
        (["Lens ID", "Jurisdiction", "Kind", "Display Key", "Publication Date", "Title", "Applicants"],
         {"id": "Display Key", "country": "Jurisdiction", "assignee": "Applicants"}),
        # Espacenet / generic
        (["Publication number", "Title", "Applicants", "Priority numbers", "Publication date", "IPC", "CPC",
          "Family number"],
         {"id": "Publication number", "assignee": "Applicants", "pub_date": "Publication date",
          "ipc": "IPC", "cpc": "CPC", "family": "Family number", "priority_date": None}),
    ],
)
def test_detects_other_database_formats(cols, expect):
    m = detect_columns(cols)
    for role, col in expect.items():
        assert m[role] == col, (role, m[role])


@pytest.mark.parametrize(
    "raw,expected",
    [
        ("Compagnie Generale Des Etablissements Michelin", "Michelin"),
        ("MICHELIN RECHERCHE ET TECHNIQUE S.A.", "Michelin"),
        ("BRIDGESTONE CORP", "Bridgestone"),
        ("The Goodyear Tire & Rubber Company", "Goodyear"),
        ("Continental Reifen Deutschland GmbH", "Continental"),
        ("Sumitomo Rubber Industries, Ltd.", "Sumitomo Rubber"),
        ("Hankook Tire & Technology Co., Ltd.", "Hankook"),
        ("Zhongce Rubber Group Co Ltd", "Zhongce (ZC Rubber)"),
        ("Apollo Tyres Ltd", "Apollo Tyres"),
        ("Technical University of Munich", "Technical University of Munich"),
        ("ACME Widgets GmbH & Co. KG", "ACME Widgets"),
        ("", "Unknown"),
    ],
)
def test_assignee_normalisation(raw, expected):
    assert normalize_assignee(raw) == expected


def test_split_assignees():
    assert split_assignees("Michelin; Some University") == ["Michelin", "Some University"]
    assert split_assignees("A | B ;; C") == ["A", "B", "C"]
    assert split_assignees(np.nan) == []


def test_parse_year_mixed_formats():
    s = pd.Series(["2019-05-12", "20200131", "March 03, 2021", 20180505, "2017", None, "garbage", "1800-01-01"])
    assert parse_year(s).tolist()[:5] == [2019, 2020, 2021, 2018, 2017]
    assert parse_year(s).iloc[5:].isna().all()


def test_parse_year_numbers_are_never_epoch_timestamps():
    # A "Priority Year" column of whole numbers once came out as 1970 for every patent.
    assert parse_year(pd.Series([2018, 2014, 2023], dtype="int64")).tolist() == [2018, 2014, 2023]
    assert parse_year(pd.Series([2018.0, None])).tolist()[0] == 2018
    assert parse_year(pd.Series([43294, 45000])).tolist() == [2018, 2023]  # Excel serial dates
    assert parse_year(pd.Series([20180713, 1700000000])).iloc[0] == 2018
    assert parse_year(pd.Series([1700000000])).isna().all()  # a number that is no date is missing, not 1970
    dates = pd.Series(pd.to_datetime(["2020-01-16", "2015-06-12"]))
    assert parse_year(dates).tolist() == [2020, 2015]


def test_parse_year_cells_with_several_dates_take_the_earliest():
    s = pd.Series(["15-Mar-2023\n15-Mar-2023\n13-Mar-2022", "13-Jul-2018", "2016-01-02; 2015-05-06", "Q3 2019"])
    assert parse_year(s).tolist() == [2022, 2018, 2015, 2019]


def test_country_and_first_claim():
    assert country_of("EP3456789A1") == "EP" and country_of("US 10234567 B2") == "US"
    assert country_of("1234") is None
    text = "1. A tire comprising a tread with grooves arranged so that wear is even. 2. The tire of claim 1."
    assert first_claim(text) == "1. A tire comprising a tread with grooves arranged so that wear is even."


def test_build_corpus_record_flow_and_fields():
    raw = make_export(n=200, seed=1)
    mapping = detect_columns(list(raw.columns))
    corpus = build_corpus(raw, mapping, years=(2014, 2024))
    steps = [f.step for f in corpus.flow]
    assert steps[0] == "Records in the export" and corpus.flow[0].remaining == len(raw)
    assert any("duplicates" in s for s in steps) and any("2014–2024" in s for s in steps)
    # Flow arithmetic is consistent.
    for prev, cur in zip(corpus.flow, corpus.flow[1:]):
        assert prev.remaining - cur.removed == cur.remaining
    assert corpus.flow[-1].remaining == len(corpus) == len(corpus.raw)
    std = corpus.std
    assert std["year"].between(2014, 2024).all()
    assert corpus.year_basis == "publication date"
    assert set(std["country"]) <= {"EP", "US", "CN", "JP", "WO", "KR"}
    assert "Michelin" in set(std["assignee"]) and "Compagnie Generale Des Etablissements Michelin" not in set(std["assignee"])
    assert (std["first_claim"].str.startswith("1.")).all()
    assert std["id"].is_unique
    assert any("No family ID" in n for n in corpus.notes)
    # The mapping table shows which raw names were merged.
    michelin = corpus.assignee_map[corpus.assignee_map["assignee"] == "Michelin"]
    assert len(michelin) == 2


def test_family_dedup_counts_each_invention_once():
    raw = make_export(n=100, seed=2, with_family=True, duplicate_share=0.2)
    corpus = build_corpus(raw, detect_columns(list(raw.columns)))
    assert len(corpus) == raw["Family ID"].nunique()
    assert "family ID" in corpus.flow[-1].step


def test_requires_text():
    with pytest.raises(ValueError, match="Title or an Abstract"):
        build_corpus(pd.DataFrame({"x": [1]}), {"title": None, "abstract": None})


def test_assignee_variants_are_merged_carefully():
    from analyst_agent.patents.data import merge_variants

    names = pd.Series(["Sika Technology"] * 5 + ["Sika Tech"] * 3 + ["Prc Desoto International"] * 7 + ["Prc Desoto Int"]
                      + ["Kordsa Global Endustriyel Iplik Ve Kord Bezi Sanayi Ve Ticaret",
                         "Kordsa Global Endustriyel Iplik Ve Kord Bezi Sanay"]
                      + ["Eve Rubber Research Institute"] * 4 + ["Eve Rubber Institute"] * 2
                      + ["Continental"] * 9 + ["Continental Carbon"]
                      + ["Beijing Univ of Chemical Technology"] * 3 + ["Beijing Univ of Technology"] * 2
                      + ["Sumitomo Chem"] + ["Sumitomo Chem Advanced Technology"] * 2 + ["Kordsa Teknik Tekstil"] * 7)
    merged = merge_variants(names)
    assert merged == {
        "Sika Tech": "Sika Technology",
        "Prc Desoto Int": "Prc Desoto International",
        "Kordsa Global Endustriyel Iplik Ve Kord Bezi Sanay": "Kordsa Global Endustriyel Iplik Ve Kord Bezi Sanayi Ve Ticaret",
        "Eve Rubber Institute": "Eve Rubber Research Institute",
    }


def test_assignees_in_other_scripts_and_separate_companies():
    assert normalize_assignee("한국타이어앤테크놀로지 주식회사") == "Hankook"
    assert normalize_assignee("住友ゴム工業株式会社") == "Sumitomo Rubber"
    assert normalize_assignee("SUMITOMO ELECTRIC INDUSTRIES LTD") == "Sumitomo Electric"  # not Sumitomo Rubber
    assert normalize_assignee("ASAHI CHEM IND") == "Asahi Kasei"
