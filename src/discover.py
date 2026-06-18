"""
Discover technology clusters ("hidden technologies") from passage embeddings.

Preferred: BERTopic (UMAP + HDBSCAN + class-based TF-IDF) which both clusters and
names topics. Fallback: KMeans + per-cluster top TF-IDF terms. Either way each
passage gets a cluster id and each cluster gets representative terms.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def _kmeans_discover(passages: pd.DataFrame, emb: np.ndarray, n_clusters: int):
    from sklearn.cluster import KMeans
    from sklearn.feature_extraction.text import TfidfVectorizer

    n_clusters = max(2, min(n_clusters, len(passages) - 1))
    km = KMeans(n_clusters=n_clusters, random_state=42, n_init=10)
    labels = km.fit_predict(emb)

    # Name each cluster by its most distinctive TF-IDF terms.
    vec = TfidfVectorizer(max_features=3000, ngram_range=(1, 2), stop_words="english")
    X = vec.fit_transform(passages["passage"].tolist())
    terms = np.array(vec.get_feature_names_out())
    cluster_terms = {}
    for c in range(n_clusters):
        idx = np.where(labels == c)[0]
        if len(idx) == 0:
            cluster_terms[c] = []
            continue
        mean_tfidf = np.asarray(X[idx].mean(axis=0)).ravel()
        top = mean_tfidf.argsort()[::-1][:12]
        cluster_terms[c] = terms[top].tolist()
    return labels, cluster_terms


def _bertopic_discover(passages: pd.DataFrame, emb: np.ndarray, min_topic_size: int):
    from bertopic import BERTopic

    topic_model = BERTopic(min_topic_size=min_topic_size, calculate_probabilities=False)
    labels, _ = topic_model.fit_transform(passages["passage"].tolist(), embeddings=emb)
    cluster_terms = {}
    for tid in set(labels):
        words = topic_model.get_topic(tid) or []
        cluster_terms[tid] = [w for w, _ in words][:12]
    return np.array(labels), cluster_terms


def discover(passages: pd.DataFrame, emb: np.ndarray, cfg: dict):
    d = cfg["discovery"]
    method = d.get("method", "bertopic")
    if method == "bertopic":
        try:
            labels, terms = _bertopic_discover(passages, emb, d.get("min_topic_size", 8))
            print(f"  BERTopic found {len(set(labels))} topics")
            return labels, terms, "bertopic"
        except Exception as exc:
            print(f"  BERTopic unavailable ({exc}); using KMeans fallback")
    labels, terms = _kmeans_discover(passages, emb, d.get("n_clusters_fallback", 15))
    print(f"  KMeans produced {len(set(labels))} clusters")
    return labels, terms, "kmeans"
