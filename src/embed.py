"""
Embed passages.

Preferred: a domain sentence-transformer (PatentSBERTa) which understands patent
+ materials language far better than base BERT. If sentence-transformers is not
installed (no GPU / restricted env), we fall back to TF-IDF so the pipeline still
runs end-to-end and produces a report — just with weaker semantics.
"""

from __future__ import annotations

import numpy as np


class Embedder:
    def __init__(self, model: str, fallback_model: str, batch_size: int = 32):
        self.batch_size = batch_size
        self.backend = None
        self._model = None
        self._vectorizer = None
        self._try_transformer(model, fallback_model)

    def _try_transformer(self, model: str, fallback_model: str):
        try:
            from sentence_transformers import SentenceTransformer
            for name in (model, fallback_model):
                try:
                    self._model = SentenceTransformer(name)
                    self.backend = f"sentence-transformers:{name}"
                    return
                except Exception as exc:
                    print(f"  could not load {name}: {exc}")
        except Exception:
            pass
        self.backend = "tfidf"
        print("  falling back to TF-IDF embeddings (install sentence-transformers "
              "for semantic embeddings)")

    def fit_transform(self, texts: list[str]) -> np.ndarray:
        if self.backend != "tfidf":
            return np.asarray(self._model.encode(
                texts, batch_size=self.batch_size, show_progress_bar=True,
                normalize_embeddings=True,
            ))
        from sklearn.feature_extraction.text import TfidfVectorizer
        self._vectorizer = TfidfVectorizer(
            max_features=4096, ngram_range=(1, 2), stop_words="english",
            sublinear_tf=True,
        )
        return self._vectorizer.fit_transform(texts).toarray()
