"""Semantic map of the patents: embeddings, a 2-D map, topic clusters, keywords and names.

Follows the reference notebook (PatentSBERTa -> UMAP -> HDBSCAN, c-TF-IDF keywords, seed 42),
with fallbacks so it also runs where those libraries or the model download are unavailable.
The methods actually used are recorded for the report's methodology section.
"""

from __future__ import annotations

import hashlib
import os
import warnings
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

import numpy as np
import pandas as pd

from .llmjson import JSONReplyError, LLMError, ask_json
from .plan import AnalysisPlan

DEFAULT_MODEL = os.environ.get("PATENT_EMBEDDING_MODEL", "AI-Growth-Lab/PatentSBERTa")
CACHE_DIR = Path(os.environ.get("PATENT_CACHE_DIR", Path.home() / ".research_agent" / "patent_cache"))
# Words that carry no technical meaning in patent text.
PATENT_STOPWORDS = {
    "tire", "tires", "tyre", "tyres", "pneumatic", "comprising", "comprises", "comprise", "wherein", "said",
    "according", "claim", "claims", "claimed", "invention", "present", "provided", "provides", "providing",
    "method", "having", "thereof", "therein", "least", "portion", "portions", "includes", "including", "first",
    "second", "third", "plurality", "configured", "disclosed", "embodiment", "example", "examples", "unit",
    "member", "members", "part", "parts", "side", "relative", "respect", "range", "ratio", "mass", "parts mass",
    "ii", "iii", "formula", "le", "ge", "mm", "degrees", "degree",
}

ProgressFn = Callable[[int, int], None]


@dataclass
class SemanticResult:
    embeddings: np.ndarray
    xy: np.ndarray
    clusters: np.ndarray  # cluster id per patent (outliers reassigned, see `outlier`)
    outlier: np.ndarray  # True where the clusterer called the patent an outlier
    topics: pd.DataFrame  # one row per cluster
    info: dict = field(default_factory=dict)  # methods and parameters, for the methodology section


# --------------------------------------------------------------------------- embeddings


def _cache_path(texts: list[str], model: str) -> Path:
    digest = hashlib.sha256(("\x1f".join(texts) + "\x1e" + model).encode("utf-8")).hexdigest()[:24]
    return CACHE_DIR / f"emb_{digest}.npy"


def embed(texts: list[str], model_name: str = DEFAULT_MODEL, seed: int = 42,
          on_progress: ProgressFn | None = None) -> tuple[np.ndarray, dict]:
    """Embeddings for `texts`: PatentSBERTa when available, else TF-IDF + LSA. Returns (matrix, info)."""
    if model_name.lower() not in ("tfidf", "tf-idf", "none"):
        cache = _cache_path(texts, model_name)
        if cache.exists():
            return np.load(cache), {"embedding": f"{model_name} (sentence-transformers, cached)"}
        try:
            from sentence_transformers import SentenceTransformer

            model = SentenceTransformer(model_name)
        except ImportError:
            reason = "sentence-transformers is not installed"
        except Exception as exc:  # model download blocked, offline, corrupt cache, ...
            reason = f"the model could not be loaded ({type(exc).__name__}: {str(exc)[:120]})"
        else:
            chunks = []
            step = 32
            if on_progress:
                on_progress(0, len(texts))
            for start in range(0, len(texts), step):
                chunks.append(model.encode(texts[start : start + step], convert_to_numpy=True, show_progress_bar=False))
                if on_progress:
                    on_progress(min(start + step, len(texts)), len(texts))
            matrix = np.vstack(chunks).astype(np.float32) if chunks else np.zeros((0, 1), dtype=np.float32)
            try:
                CACHE_DIR.mkdir(parents=True, exist_ok=True)
                np.save(cache, matrix)
            except OSError:
                pass
            return matrix, {"embedding": f"{model_name} (sentence-transformers)"}
        fallback_note = f"TF-IDF + LSA, because {reason}. Install sentence-transformers for PatentSBERTa."
    else:
        fallback_note = "TF-IDF + LSA (chosen in settings)."
    return _tfidf_lsa(texts, seed), {"embedding": fallback_note}


def _tfidf_lsa(texts: list[str], seed: int) -> np.ndarray:
    from sklearn.decomposition import TruncatedSVD
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.preprocessing import normalize

    n = len(texts)
    vec = TfidfVectorizer(stop_words="english", ngram_range=(1, 2), min_df=2 if n >= 20 else 1,
                          max_features=20000, sublinear_tf=True)
    tfidf = vec.fit_transform(texts)
    k = max(2, min(100, tfidf.shape[1] - 1, n - 1))
    if tfidf.shape[1] <= 2 or n <= 3:
        return normalize(tfidf.toarray()).astype(np.float32)
    return normalize(TruncatedSVD(n_components=k, random_state=seed).fit_transform(tfidf)).astype(np.float32)


# --------------------------------------------------------------------------- projection and clustering


def reduce(emb: np.ndarray, dims: int, seed: int, min_dist: float) -> tuple[np.ndarray, str]:
    n = len(emb)
    if n < 6:
        from sklearn.decomposition import PCA

        return PCA(n_components=min(dims, n, emb.shape[1]), random_state=seed).fit_transform(emb), "PCA"
    try:
        import umap

        model = umap.UMAP(n_components=dims, n_neighbors=min(15, n - 1), min_dist=min_dist, metric="cosine",
                          random_state=seed)
        with warnings.catch_warnings():  # "n_jobs overridden by setting random_state" and similar notices
            warnings.simplefilter("ignore")
            return model.fit_transform(emb), "UMAP"
    except ImportError:
        from sklearn.manifold import TSNE

        if dims > 3:
            from sklearn.decomposition import PCA

            return PCA(n_components=min(dims, emb.shape[1]), random_state=seed).fit_transform(emb), "PCA"
        return TSNE(n_components=dims, perplexity=min(30, n - 1), random_state=seed, init="pca").fit_transform(emb), "t-SNE"


def cluster(space: np.ndarray, seed: int, max_clusters: int = 25) -> tuple[np.ndarray, np.ndarray, str]:
    """Cluster ids (outliers attached to the nearest cluster) and the outlier mask."""
    n = len(space)
    min_size = max(5, n // 40)
    labels = None
    method = ""
    if n >= 3 * min_size:
        from sklearn.cluster import HDBSCAN

        while True:
            with warnings.catch_warnings():  # FutureWarning about the `copy` default in newer scikit-learn
                warnings.simplefilter("ignore", FutureWarning)
                labels = HDBSCAN(min_cluster_size=min_size, min_samples=3).fit_predict(space)
            if len(set(labels) - {-1}) <= max_clusters or 3 * min_size > n:
                break
            min_size = int(min_size * 1.5) + 1  # too fine-grained to read: ask for bigger clusters
        method = f"HDBSCAN (min cluster size {min_size}, min samples 3)"
        if len(set(labels) - {-1}) < 3:
            labels = None
    if labels is None:
        from sklearn.cluster import KMeans
        from sklearn.metrics import silhouette_score

        best = (None, -1.0, 0)
        for k in range(3, min(12, max(3, n // 8)) + 1):
            if k >= n:
                break
            km = KMeans(n_clusters=k, n_init=10, random_state=seed).fit(space)
            score = silhouette_score(space, km.labels_) if k < n else -1
            if score > best[1]:
                best = (km.labels_, score, k)
        labels = best[0] if best[0] is not None else np.zeros(n, dtype=int)
        method = f"KMeans (k={best[2]}, chosen by silhouette)" if best[0] is not None else "single group"
    outlier = labels == -1
    if outlier.any() and (~outlier).any():
        ids = sorted(set(labels[~outlier]))
        centroids = np.vstack([space[labels == i].mean(axis=0) for i in ids])
        dist = ((space[outlier][:, None, :] - centroids[None, :, :]) ** 2).sum(axis=2)
        labels = labels.copy()
        labels[outlier] = np.array(ids)[dist.argmin(axis=1)]
    # Renumber by size: cluster 0 is the largest.
    order = pd.Series(labels).value_counts().index.tolist()
    remap = {old: new for new, old in enumerate(order)}
    return np.array([remap[x] for x in labels]), outlier, method


def topic_keywords(texts: list[str], labels: np.ndarray, top_n: int = 10) -> dict[int, list[str]]:
    """c-TF-IDF: words frequent in one cluster and rare across clusters."""
    from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS, CountVectorizer

    ids = sorted(set(labels))
    docs = [" ".join(t for t, lab in zip(texts, labels) if lab == i) for i in ids]
    stop = list(ENGLISH_STOP_WORDS | PATENT_STOPWORDS)
    vec = CountVectorizer(stop_words=stop, ngram_range=(1, 2), min_df=1, token_pattern=r"(?u)\b[a-zA-Z][a-zA-Z-]+\b")
    try:
        counts = vec.fit_transform(docs).toarray().astype(float)
    except ValueError:
        return {i: [] for i in ids}
    terms = np.array(vec.get_feature_names_out())
    tf = counts / np.maximum(counts.sum(axis=1, keepdims=True), 1)
    avg_words = counts.sum() / max(len(ids), 1)
    idf = np.log(1 + avg_words / np.maximum(counts.sum(axis=0), 1))
    scores = tf * idf
    out = {}
    for row, i in enumerate(ids):
        picked: list[str] = []
        for j in scores[row].argsort()[::-1]:
            term = terms[j]
            if scores[row, j] <= 0:
                break
            if any(term in p or p in term for p in picked):  # skip "silica" when "silica filler" is there
                continue
            picked.append(term)
            if len(picked) == top_n:
                break
        out[i] = picked
    return out


def representatives(emb: np.ndarray, labels: np.ndarray, k: int = 3) -> dict[int, list[int]]:
    """Positions of the patents closest to each cluster's centre."""
    reps = {}
    for i in sorted(set(labels)):
        idx = np.where(labels == i)[0]
        centre = emb[idx].mean(axis=0)
        dist = ((emb[idx] - centre) ** 2).sum(axis=1)
        reps[i] = idx[dist.argsort()[:k]].tolist()
    return reps


# --------------------------------------------------------------------------- naming

NAMING_SYSTEM = """\
You name topic clusters in a patent landscape. For each cluster you get its keywords and the titles \
of its most typical patents. Reply with ONLY a JSON list, one object per cluster, in the same order:
{"id": cluster id, "label": a specific technical name of at most 6 words, "description": one sentence \
on what these patents do, "tech_group": the best matching technology group from the list given, \
"relevance": "high", "partial" or "low" relevance to the study scope}
Use the patents' own technical terms. Do not repeat the same label for two clusters.\
"""


def name_topics(llm, topics: pd.DataFrame, plan: AnalysisPlan, batch: int = 10) -> tuple[pd.DataFrame, str]:
    """Adds label/description/tech_group/relevance columns. Returns (topics, note)."""
    groups = [b.name for b in plan.tech_buckets]
    header = [f"Study: {plan.title}. Scope: {plan.in_scope or plan.objective}",
              f"Technology groups: {'; '.join(groups)}", ""]
    topics = topics.copy()
    topics["label"] = [", ".join(k[:3]).capitalize() or f"Cluster {i}" for i, k in zip(topics["id"], topics["keywords"])]
    topics["description"] = ""
    topics["tech_group"] = ""
    topics["relevance"] = ""
    if llm is None:
        return topics, "Cluster names are their top keywords (no model available)."
    lower_groups = {g.lower(): g for g in groups}
    used: set[str] = set()
    named, failures = 0, []
    for start in range(0, len(topics), batch):  # small batches keep each reply short and complete
        part = topics.iloc[start : start + batch]
        lines = list(header)
        for row in part.itertuples():
            lines.append(f"Cluster {row.id} ({row.patents} patents) | keywords: {', '.join(row.keywords[:10])}")
            for title in row.sample_titles[:3]:
                lines.append(f"  - {str(title)[:140]}")
        try:
            data = ask_json(llm, NAMING_SYSTEM, "\n".join(lines), retries=1, max_tokens=3000)
        except (LLMError, JSONReplyError) as exc:
            failures.append(str(exc))
            continue
        items = data if isinstance(data, list) else data.get("clusters", []) if isinstance(data, dict) else []
        items = [item for item in items if isinstance(item, dict)]
        by_id = {}
        for item in items:
            try:
                by_id[int(item.get("id"))] = item
            except (TypeError, ValueError):
                continue
        for pos, cid in enumerate(part["id"]):
            item = by_id.get(int(cid)) or (items[pos] if not by_id and pos < len(items) else None)
            if not item:
                continue
            named += 1
            where = topics["id"] == cid
            label = str(item.get("label") or "").strip()[:60]
            if label and label.lower() not in used:
                topics.loc[where, "label"] = label
                used.add(label.lower())
            topics.loc[where, "description"] = str(item.get("description") or "").strip()[:300]
            group = str(item.get("tech_group") or "").strip()
            topics.loc[where, "tech_group"] = lower_groups.get(group.lower(), "")
            rel = str(item.get("relevance") or "").strip().lower()
            topics.loc[where, "relevance"] = rel if rel in ("high", "partial", "low") else ""
    if not named:
        reason = f" ({failures[0]})" if failures else ""
        return topics, f"Cluster names are their top keywords: the model could not name them{reason}."
    note = f"Cluster names written by the model for {named} of {len(topics)} clusters"
    return topics, note + ("; the rest are named by their top keywords." if named < len(topics) else ".")


# --------------------------------------------------------------------------- all together


def semantic_map(texts: list[str], titles: list[str], plan: AnalysisPlan, llm=None, seed: int = 42,
                 model_name: str = DEFAULT_MODEL, on_progress: ProgressFn | None = None,
                 on_phase: Callable[[str], None] | None = None) -> SemanticResult:
    phase = on_phase or (lambda _p: None)
    phase("Embedding patent texts")
    emb, info = embed(texts, model_name=model_name, seed=seed, on_progress=on_progress)
    phase("Building the 2-D map")
    xy, proj = reduce(emb, 2, seed, min_dist=0.1)
    phase("Finding topic clusters")
    space, space_method = reduce(emb, min(5, max(2, len(emb) - 2)), seed, min_dist=0.0)
    labels, outlier, cluster_method = cluster(space, seed)
    keywords = topic_keywords(texts, labels)
    reps = representatives(emb, labels)
    topics = pd.DataFrame({
        "id": sorted(set(labels)),
        "patents": [int((labels == i).sum()) for i in sorted(set(labels))],
        "keywords": [keywords.get(i, []) for i in sorted(set(labels))],
        "sample_titles": [[titles[j] for j in reps[i]] for i in sorted(set(labels))],
        "representatives": [reps[i] for i in sorted(set(labels))],
    })
    phase("Naming the clusters")
    topics, naming_note = name_topics(llm, topics, plan)
    info.update({
        "map": f"{proj} to 2-D (seed {seed})",
        "clustering": f"{cluster_method} on a {space.shape[1]}-D {space_method} projection (seed {seed}); "
                      f"{int(outlier.sum())} outliers attached to their nearest cluster",
        "keywords": "c-TF-IDF over each cluster's titles, abstracts and claims",
        "naming": naming_note,
    })
    return SemanticResult(embeddings=emb, xy=np.asarray(xy), clusters=labels, outlier=outlier, topics=topics, info=info)
