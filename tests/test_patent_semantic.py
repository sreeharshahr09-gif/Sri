import json
import sys
import types

import numpy as np
import pandas as pd
import pytest

from analyst_agent.llm import LLMError
from analyst_agent.patents import semantic
from analyst_agent.patents.data import build_corpus, detect_columns
from analyst_agent.patents.plan import AnalysisPlan

from .conftest import ScriptedLLM
from .patent_fixtures import make_export


@pytest.fixture(scope="module")
def corpus():
    raw = make_export(240, seed=3)
    return build_corpus(raw, detect_columns(list(raw.columns)))


@pytest.fixture(scope="module")
def mapped(corpus):
    std = corpus.std
    return semantic.semantic_map(std["text"].tolist(), std["title"].tolist(), AnalysisPlan(), model_name="tfidf")


def _blobs(n_per=20, centres=((0, 0), (10, 0), (0, 10)), seed=0):
    rng = np.random.default_rng(seed)
    return np.vstack([rng.normal(c, 0.4, size=(n_per, 2)) for c in centres])


# --------------------------------------------------------------------------- the whole map


def test_map_shapes_and_methods(mapped, corpus):
    n = len(corpus)
    assert mapped.xy.shape == (n, 2)
    assert len(mapped.clusters) == len(mapped.outlier) == n
    assert mapped.clusters.min() == 0  # outliers were attached to a cluster
    assert mapped.topics["patents"].sum() == n
    assert list(mapped.topics["id"]) == sorted(set(mapped.clusters))
    # cluster 0 is the largest
    assert mapped.topics["patents"].iloc[0] == mapped.topics["patents"].max()
    for key in ("embedding", "map", "clustering", "keywords", "naming"):
        assert mapped.info[key]
    assert "TF-IDF" in mapped.info["embedding"]
    assert mapped.info["map"].startswith("UMAP")
    assert "HDBSCAN" in mapped.info["clustering"]


def test_clusters_follow_the_technical_themes(mapped, corpus):
    themes = corpus.raw["_theme"].values
    table = pd.crosstab(mapped.clusters, themes)
    purity = table.max(axis=1).sum() / table.values.sum()
    assert purity >= 0.9, table
    assert 4 <= len(table) <= 25


def test_topics_carry_keywords_and_typical_titles(mapped, corpus):
    titles = set(corpus.std["title"])
    for row in mapped.topics.itertuples():
        assert row.keywords, row
        assert not (set(row.keywords) & semantic.PATENT_STOPWORDS)
        assert row.sample_titles and set(row.sample_titles) <= titles
        assert all(mapped.clusters[i] == row.id for i in row.representatives)
    words = " ".join(" ".join(k) for k in mapped.topics["keywords"])
    for term in ("silica", "belt", "sensor"):
        assert term in words


# --------------------------------------------------------------------------- embeddings


class _FakeModel:
    loads = 0

    def __init__(self, name):
        type(self).loads += 1
        self.name = name

    def encode(self, texts, convert_to_numpy=True, show_progress_bar=False):
        return np.array([[len(t), t.count("a"), 1.0] for t in texts], dtype=np.float64)


def test_sentence_transformers_used_with_progress_and_cache(monkeypatch, tmp_path):
    monkeypatch.setattr(semantic, "CACHE_DIR", tmp_path)
    monkeypatch.setitem(sys.modules, "sentence_transformers", types.SimpleNamespace(SentenceTransformer=_FakeModel))
    _FakeModel.loads = 0
    texts = [f"patent text {'a' * i}" for i in range(70)]
    seen = []
    matrix, info = semantic.embed(texts, model_name="fake/PatentSBERTa", on_progress=lambda d, t: seen.append((d, t)))
    assert matrix.shape == (70, 3) and matrix.dtype == np.float32
    assert seen == [(0, 70), (32, 70), (64, 70), (70, 70)]
    assert info["embedding"] == "fake/PatentSBERTa (sentence-transformers)"
    assert len(list(tmp_path.glob("emb_*.npy"))) == 1

    again, info2 = semantic.embed(texts, model_name="fake/PatentSBERTa")
    assert np.array_equal(again, matrix)
    assert "cached" in info2["embedding"]
    assert _FakeModel.loads == 1  # the model was not loaded again

    _, info3 = semantic.embed(texts[:-1], model_name="fake/PatentSBERTa")
    assert "cached" not in info3["embedding"]  # different texts, different cache entry


def test_unloadable_model_falls_back_with_reason(monkeypatch, tmp_path):
    monkeypatch.setattr(semantic, "CACHE_DIR", tmp_path)

    def refuse(name):
        raise OSError("couldn't connect to huggingface.co")

    monkeypatch.setitem(sys.modules, "sentence_transformers", types.SimpleNamespace(SentenceTransformer=refuse))
    texts = [f"tread compound silica {i}" for i in range(10)] + [f"belt layer cord angle {i}" for i in range(10)]
    matrix, info = semantic.embed(texts, model_name="AI-Growth-Lab/PatentSBERTa")
    assert matrix.shape[0] == 20
    assert "could not be loaded" in info["embedding"] and "OSError" in info["embedding"]
    assert not list(tmp_path.iterdir())


def test_missing_library_falls_back(monkeypatch):
    monkeypatch.setitem(sys.modules, "sentence_transformers", None)  # import raises ImportError
    _, info = semantic.embed(["silica tread compound", "belt cord angle", "sensor wear model"])
    assert "not installed" in info["embedding"]


def test_tfidf_vectors_are_normalised():
    texts = [f"silica compound tread rubber {i}" for i in range(15)] + [f"belt cord layer angle {i}" for i in range(15)]
    matrix, info = semantic.embed(texts, model_name="tfidf")
    assert info["embedding"].startswith("TF-IDF + LSA (chosen")
    assert np.allclose(np.linalg.norm(matrix, axis=1), 1.0, atol=1e-5)
    assert matrix[0] @ matrix[1] > matrix[0] @ matrix[20]  # same theme is closer


# --------------------------------------------------------------------------- projection and clustering


def test_tiny_sets_use_pca():
    out, method = semantic.reduce(np.random.default_rng(0).normal(size=(5, 8)), 2, 42, 0.1)
    assert method == "PCA" and out.shape == (5, 2)


def test_small_sets_cluster_with_kmeans():
    labels, outlier, method = semantic.cluster(_blobs(n_per=4), seed=42)
    assert method.startswith("KMeans (k=3")
    assert not outlier.any()
    assert len(set(labels)) == 3
    assert all(len(set(labels[i * 4 : (i + 1) * 4])) == 1 for i in range(3))


def test_outliers_attach_to_nearest_cluster():
    noise = np.random.default_rng(5).uniform(-6, 16, size=(40, 2))
    space = np.vstack([_blobs(n_per=40), noise])
    labels, outlier, method = semantic.cluster(space, seed=42)
    assert method.startswith("HDBSCAN")
    assert outlier.any() and not outlier[:120].any()
    assert labels.min() >= 0
    centres = {lab: space[(labels == lab) & ~outlier].mean(axis=0) for lab in set(labels)}
    for point, label in zip(space[outlier], labels[outlier]):
        assert label == min(centres, key=lambda k: np.linalg.norm(point - centres[k]))


def test_cluster_count_is_capped():
    rng = np.random.default_rng(1)
    centres = [(x * 10, y * 10) for x in range(6) for y in range(6)]  # 36 tight groups
    space = np.vstack([rng.normal(c, 0.3, size=(12, 2)) for c in centres])
    labels, _, _ = semantic.cluster(space, seed=42, max_clusters=25)
    assert 3 <= len(set(labels)) <= 25


def test_keywords_skip_stopwords_and_overlaps():
    texts = ["A pneumatic tire comprising a silica filler compound wherein the silica filler is dispersed"] * 3 + \
            ["A tire comprising a belt layer wherein the belt cord angle is small"] * 3
    labels = np.array([0, 0, 0, 1, 1, 1])
    keywords = semantic.topic_keywords(texts, labels, top_n=5)
    assert set(keywords) == {0, 1}
    flat = keywords[0] + keywords[1]
    assert not ({"tire", "pneumatic", "comprising", "wherein"} & set(flat))
    assert any("silica" in k for k in keywords[0]) and not any("belt" in k for k in keywords[0])
    assert any("belt" in k for k in keywords[1])
    # no keyword is contained in another one of the same cluster
    for words in keywords.values():
        assert not any(a != b and a in b for a in words for b in words)


# --------------------------------------------------------------------------- naming


def _topics():
    return pd.DataFrame({
        "id": [0, 1, 2],
        "patents": [40, 25, 10],
        "keywords": [["shoulder", "groove"], ["silica", "compound"], ["sensor", "model"]],
        "sample_titles": [["Tread with shoulder grooves"], ["Silica tread compound"], ["Wear prediction model"]],
        "representatives": [[0], [1], [2]],
    })


def test_name_topics_from_model():
    plan = AnalysisPlan(title="Uneven wear")
    reply = [
        {"id": 0, "label": "Shoulder groove geometry", "description": "Grooves that even out shoulder wear.",
         "tech_group": "structural / DESIGN", "relevance": "High"},
        {"id": 1, "label": "Shoulder groove geometry", "description": "Silica compounds.",
         "tech_group": "Chemistry", "relevance": "maybe"},
        {"id": 2, "label": "Sensor-based wear prediction", "tech_group": "Active / electronic", "relevance": "partial"},
    ]
    llm = ScriptedLLM(["Here you go:\n```json\n" + json.dumps(reply) + "\n```"])
    topics, note = semantic.name_topics(llm, _topics(), plan)
    assert topics["label"].tolist() == ["Shoulder groove geometry", "Silica, compound", "Sensor-based wear prediction"]
    assert topics["tech_group"].tolist() == ["Structural / design", "", "Active / electronic"]
    assert topics["relevance"].tolist() == ["high", "", "partial"]
    assert note == "Cluster names written by the model for 3 of 3 clusters."
    prompt = llm.calls[0][1]["content"]
    assert "Structural / design; Material science" in prompt
    assert "Cluster 2 (10 patents) | keywords: sensor, model" in prompt and "Wear prediction model" in prompt


def test_name_topics_in_batches():
    reply1 = [{"id": 0, "label": "A"}, {"id": 1, "label": "B"}]
    llm = ScriptedLLM([json.dumps(reply1), "not json", "still not json"])
    topics, note = semantic.name_topics(llm, _topics(), AnalysisPlan(), batch=2)
    assert len(llm.calls) == 3  # one batch of two, then one retry for the second batch
    assert topics["label"].tolist() == ["A", "B", "Sensor, model"]
    assert note.startswith("Cluster names written by the model for 2 of 3 clusters; the rest")


def test_name_topics_without_model():
    class Down:
        def chat(self, messages):
            raise LLMError("Cannot reach the model server")

    topics, note = semantic.name_topics(Down(), _topics(), AnalysisPlan())
    assert topics["label"].tolist() == ["Shoulder, groove", "Silica, compound", "Sensor, model"]
    assert "could not name them (Cannot reach the model server)" in note
    topics, note = semantic.name_topics(None, _topics(), AnalysisPlan())
    assert "no model available" in note


def test_semantic_map_reports_phases_and_names(corpus):
    std = corpus.std.head(120)
    phases = []

    class Namer:
        def __init__(self):
            self.calls = 0

        def chat(self, messages):
            from analyst_agent.llm import Completion

            self.calls += 1
            ids = [int(line.split()[1]) for line in messages[-1]["content"].splitlines() if line.startswith("Cluster ")]
            body = [{"id": i, "label": f"Topic {i}", "tech_group": "Material science", "relevance": "high"} for i in ids]
            return Completion(content=json.dumps(body), latency_s=0.0)

    namer = Namer()
    res = semantic.semantic_map(std["text"].tolist(), std["title"].tolist(), AnalysisPlan(), llm=namer,
                                model_name="tfidf", on_phase=phases.append)
    assert phases == ["Embedding patent texts", "Building the 2-D map", "Finding topic clusters", "Naming the clusters"]
    assert res.topics["label"].tolist() == [f"Topic {i}" for i in res.topics["id"]]
    assert set(res.topics["tech_group"]) == {"Material science"}
    assert namer.calls == -(-len(res.topics) // 10)
