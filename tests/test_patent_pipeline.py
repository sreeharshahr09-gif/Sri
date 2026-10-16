from analyst_agent.llm import Completion
from analyst_agent.patents.data import detect_columns
from analyst_agent.patents.pipeline import PipelineOptions, run_landscape
from analyst_agent.patents.plan import AnalysisPlan

from .patent_fixtures import make_export, name_clusters_reply

TFIDF = PipelineOptions(embedding_model="tfidf")


class NamerLLM:
    def __init__(self, plan):
        self.groups = [b.name for b in plan.tech_buckets]
        self.calls = 0

    def chat(self, messages):
        self.calls += 1
        assert "You name topic clusters" in messages[0]["content"]
        return Completion(content=name_clusters_reply(messages[-1]["content"], self.groups), latency_s=0.0)


def _run(n=160, **kwargs):
    raw = make_export(n, seed=11)
    events = []
    plan = kwargs.pop("plan", AnalysisPlan(title="Uneven wear"))
    result = run_landscape(raw, detect_columns(list(raw.columns)), plan, options=kwargs.pop("options", TFIDF),
                           on_event=lambda k, p: events.append((k, p)), **kwargs)
    return result, events


def test_full_run_builds_map_and_topic_table():
    plan = AnalysisPlan(title="Uneven wear")
    namer = NamerLLM(plan)
    result, events = _run(plan=plan, llm=namer)
    assert result.status == "complete" and result.error is None
    phases = [p for k, p in events if k == "phase"]
    assert phases == ["Cleaning the export", "Embedding patent texts", "Building the 2-D map",
                      "Finding topic clusters", "Naming the clusters"]
    std = result.enriched
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
                                   "Cluster names"}
    assert result.methods["Cluster names"].startswith("Cluster names written by the model for")


def test_without_model_clusters_are_named_by_keywords_and_no_hierarchy():
    result, _ = _run()
    titles = [b.title for b in result.blocks]
    assert "Topic clusters" in titles and "Topic hierarchy" not in titles
    table = next(b.table for b in result.blocks if b.title == "Topic clusters")
    assert "Technology group" not in table.columns  # empty columns are left out
    assert "no model available" in result.methods["Cluster names"]


def test_trial_sample_keeps_rows_aligned():
    result, _ = _run(options=PipelineOptions(sample_limit=40, embedding_model="tfidf"))
    assert len(result.enriched) == 40
    assert result.corpus.flow[-1].step == "Trial run: random sample of 40"
    assert result.enriched["map_x"].notna().all() and result.enriched.index.equals(result.corpus.raw.index)


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
    assert "cluster_id" not in result.enriched


def test_tiny_sets_skip_the_map_with_a_note():
    result, _ = _run(n=6)
    assert result.semantic is None and "Patent map" not in [b.title for b in result.blocks]
    assert any("Too few patents for a semantic map" in w for w in result.warnings)


def test_map_can_be_switched_off():
    result, events = _run(options=PipelineOptions(semantic_map=False))
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
