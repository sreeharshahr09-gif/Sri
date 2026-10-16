import io

import pandas as pd
import pytest

from analyst_agent.llm import Completion
from analyst_agent.patents import enrich
from analyst_agent.patents.data import detect_columns
from analyst_agent.patents.pipeline import PipelineOptions, run_landscape
from analyst_agent.patents.plan import AnalysisPlan

from .patent_fixtures import extract_reply, make_export, name_clusters_reply
from .test_patent_plan import MODEL_PLAN

TFIDF = PipelineOptions(embedding_model="tfidf")


@pytest.fixture(autouse=True)
def cache_dir(monkeypatch, tmp_path):
    monkeypatch.setattr(enrich, "CACHE_DIR", tmp_path)


class FakeLLM:
    """Names clusters and reads patents like a well-behaved local model."""

    def __init__(self, plan):
        self.groups = [b.name for b in plan.tech_buckets]
        self.calls = {"names": 0, "extract": 0}

    def chat(self, messages):
        system, user = messages[0]["content"], messages[-1]["content"]
        if "You name topic clusters" in system:
            self.calls["names"] += 1
            return Completion(content=name_clusters_reply(user, self.groups), latency_s=0.0)
        assert "You extract structured facts from patents" in system
        self.calls["extract"] += 1
        return Completion(content=extract_reply(system, user), latency_s=0.0)


def _run(n=160, **kwargs):
    raw = make_export(n, seed=11)
    events = []
    plan = kwargs.pop("plan", AnalysisPlan(title="Uneven wear"))
    result = run_landscape(raw, detect_columns(list(raw.columns)), plan, options=kwargs.pop("options", TFIDF),
                           on_event=lambda k, p: events.append((k, p)), **kwargs)
    return result, events


def test_full_run_builds_map_and_topic_table():
    plan = AnalysisPlan(title="Uneven wear")
    model = FakeLLM(plan)
    result, events = _run(plan=plan, llm=model)
    assert result.status == "complete" and result.error is None
    phases = [p for k, p in events if k == "phase"]
    assert phases == ["Cleaning the export", "Embedding patent texts", "Building the 2-D map",
                      "Finding topic clusters", "Naming the clusters", "Reading every patent"]
    std = result.corpus.std
    for col in ("map_x", "map_y", "cluster_id", "cluster_label", "cluster_tech_group", "cluster_outlier"):
        assert col in std and std[col].notna().all(), col
    assert std["cluster_label"].str.startswith("Topic ").all()
    assert set(std["cluster_tech_group"]) <= {b.name for b in plan.tech_buckets}
    titles = [b.title for b in result.blocks]
    assert titles[:3] == ["Patent landscape: Uneven wear", "", "How the patent set was built"]
    assert {"Technology clusters (semantic map)", "Patent map", "Topic clusters", "Topic hierarchy"} <= set(titles)
    topic_table = next(b.table for b in result.blocks if b.title == "Topic clusters")
    assert topic_table["Patents"].sum() == len(std)
    assert list(topic_table.columns) == ["#", "Topic cluster", "Patents", "Share", "Technology group", "Relevance",
                                         "Key terms", "What these patents do"]
    fig = next(b.figure for b in result.blocks if b.title == "Patent map")
    assert sum(len(t.x) for t in fig.data) == len(std)
    assert len(fig.layout.annotations) == len(topic_table)  # one numbered tag per cluster
    assert all(t.name.split(" · ")[0].isdigit() for t in fig.data)
    assert set(result.methods) == {"Year basis", "Text embedding", "Patent map", "Topic clusters", "Cluster keywords",
                                   "Cluster names", "Patent details"}
    assert result.methods["Cluster names"].startswith("Cluster names written by the model for")


def test_without_model_clusters_are_named_by_keywords_and_no_hierarchy():
    result, _ = _run()
    assert any("No model connected" in w for w in result.warnings)
    assert "Topic" not in result.enriched and "Topic cluster" in result.enriched
    titles = [b.title for b in result.blocks]
    assert "Topic clusters" in titles and "Topic hierarchy" not in titles
    table = next(b.table for b in result.blocks if b.title == "Topic clusters")
    assert "Technology group" not in table.columns  # empty columns are left out
    assert "no model available" in result.methods["Cluster names"]


def test_trial_sample_keeps_rows_aligned():
    result, _ = _run(options=PipelineOptions(sample_limit=40, embedding_model="tfidf"))
    assert len(result.enriched) == 40
    assert result.corpus.flow[-1].step == "Trial run: random sample of 40"
    std = result.corpus.std
    assert std["map_x"].notna().all() and std.index.equals(result.corpus.raw.index)
    assert result.enriched["Patent ID"].tolist() == std["id"].tolist()


def test_stop_request_keeps_what_was_finished():
    state = {"stop": False}

    def on_event(kind, payload):
        if payload == "Building the 2-D map":
            state["stop"] = True

    raw = make_export(80, seed=2)
    result = run_landscape(raw, detect_columns(list(raw.columns)), AnalysisPlan(), options=TFIDF, on_event=on_event,
                           should_stop=lambda: state["stop"])
    assert result.status == "cancelled"
    titles = [b.title for b in result.blocks]
    assert "How the patent set was built" in titles and "Patent map" not in titles
    assert "Topic cluster" not in result.enriched
    assert "Download the enriched patent table (CSV)" in titles  # what was finished can still be downloaded


def test_tiny_sets_skip_the_map_with_a_note():
    result, _ = _run(n=6)
    assert result.semantic is None and "Patent map" not in [b.title for b in result.blocks]
    assert any("Too few patents for a semantic map" in w for w in result.warnings)


def test_map_can_be_switched_off():
    result, events = _run(options=PipelineOptions(semantic_map=False, extract_details=False))
    assert [p for k, p in events if k == "phase"] == ["Cleaning the export"]
    assert result.semantic is None


def test_missing_scikit_learn_skips_the_map_with_advice(monkeypatch):
    import sys

    for name in [m for m in sys.modules if m == "sklearn" or m.startswith("sklearn.")]:
        monkeypatch.delitem(sys.modules, name)
    monkeypatch.setitem(sys.modules, "sklearn", None)
    result, _ = _run(n=40)
    assert result.status == "complete" and result.semantic is None
    assert any("skipped: scikit-learn is not installed" in w and "requirements.txt" in w for w in result.warnings)
    assert "How the patent set was built" in [b.title for b in result.blocks]


def test_enriched_csv_has_study_columns_then_clusters_then_the_export():
    plan = AnalysisPlan.from_dict({**MODEL_PLAN, "extra_fields": [{"name": "Test method", "description": "x"}]})
    model = FakeLLM(plan)
    result, events = _run(plan=plan, llm=model)
    table = result.enriched
    cols = list(table.columns)
    assert cols[:6] == ["Patent ID", "Title", "Year", "Assignee (normalised)", "Co-assignees", "Office"]
    study = ["Topic", "Segment", "Wear type", "Technology group", "Complexity", "Component", "Performance target",
             "Design lever", "Mechanism", "Core claim", "Key parameters", "Trade-offs", "Relevance", "Confidence",
             "Test method", "Extraction"]
    assert cols[6 : 6 + len(study)] == study
    assert cols[6 + len(study) : 11 + len(study)] == ["Topic cluster", "Cluster no.", "Cluster technology group",
                                                      "Map X", "Map Y"]
    raw_cols = [c for c in result.corpus.raw.columns if not c.startswith("_")]
    assert cols[11 + len(study) :] == [c if c not in cols[:6] else f"{c} (export)" for c in raw_cols]
    assert "Title (export)" in cols and "_theme" not in cols
    assert table["Extraction"].eq("extracted").all()
    assert model.calls["extract"] == -(-len(table) // 6)
    assert any(k == "progress" and p == (len(table), len(table)) for k, p in events)

    dl = next(b for b in result.blocks if b.kind == "download")
    assert dl.file_name == "uneven_wear_in_tbr_and_pcr_tires_patents.csv"
    assert dl.data.startswith(b"\xef\xbb\xbf")  # BOM: Excel reads the accents right
    back = pd.read_csv(io.BytesIO(dl.data), encoding="utf-8-sig")
    assert list(back.columns) == cols and len(back) == len(table)
    shown = next(b for b in result.blocks if b.title == "Every patent with the study's columns").table
    assert list(shown.columns) == ["Patent ID", "Title", "Year", "Assignee (normalised)", "Topic", "Segment",
                                   "Wear type", "Technology group", "Complexity", "Performance target",
                                   "Design lever", "Relevance"]
    metrics = dict(next(b for b in result.blocks if b.kind == "metrics" and b.metrics[0][0] == "Read by the model").metrics)
    assert metrics["Read by the model"] == f"{len(table)} of {len(table)}"
    assert int(metrics["Low (off-topic)"]) == (table["Relevance"] == "Low").sum() > 0


def test_stop_during_extraction_keeps_partial_details():
    plan = AnalysisPlan.from_dict(MODEL_PLAN)
    model = FakeLLM(plan)
    raw = make_export(80, seed=2)
    result = run_landscape(raw, detect_columns(list(raw.columns)), plan, llm=model,
                           options=PipelineOptions(embedding_model="tfidf", semantic_map=False),
                           should_stop=lambda: model.calls["extract"] >= 2)
    assert result.status == "cancelled"
    status = result.enriched["Extraction"]
    assert (status == "extracted").sum() == 12 and (status == "not run (stopped)").sum() == len(status) - 12
    assert any(b.kind == "download" for b in result.blocks)
