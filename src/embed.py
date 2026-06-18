"""
Embed passages.

Backends, in priority order:
  1. PatentBERT (transformers mean-pooling) — a raw patent BERT such as
     `anferico/bert-for-patents`; token embeddings are mean-pooled to a sentence
     vector. This is the "PatentBERT" path.
  2. sentence-transformers — a purpose-built embedding model (e.g. PatentSBERTa).
  3. TF-IDF — always-available fallback so the pipeline runs with no model
     download / GPU / internet (lower semantic quality).

Network note: the model weights are fetched from HuggingFace on first use. In an
environment where HuggingFace is blocked, only the TF-IDF fallback is available.
"""

from __future__ import annotations

import numpy as np


class Embedder:
    def __init__(self, model: str, fallback_model: str, batch_size: int = 32,
                 use_mean_pooling: bool = True):
        self.batch_size = batch_size
        self.backend = None
        self._st_model = None        # sentence-transformers model
        self._hf_tok = None          # transformers tokenizer
        self._hf_model = None        # transformers model (PatentBERT)
        self._vectorizer = None

        # 1. Try PatentBERT (transformers + mean pooling).
        if use_mean_pooling and self._try_patentbert(model):
            return
        # 2. Try sentence-transformers (model, then fallback).
        if self._try_sentence_transformer(model, fallback_model):
            return
        # 3. TF-IDF fallback.
        self.backend = "tfidf"
        print("  falling back to TF-IDF embeddings (no transformer model "
              "available — install transformers + reachable HuggingFace for "
              "PatentBERT-quality embeddings)")

    # -- backend 1: PatentBERT via transformers mean pooling -----------------
    def _try_patentbert(self, model: str) -> bool:
        try:
            import torch  # noqa: F401
            from transformers import AutoTokenizer, AutoModel
            self._hf_tok = AutoTokenizer.from_pretrained(model)
            self._hf_model = AutoModel.from_pretrained(model)
            self._hf_model.eval()
            self.backend = f"patentbert:{model}"
            print(f"  using PatentBERT (mean-pooled): {model}")
            return True
        except Exception as exc:
            print(f"  PatentBERT unavailable for {model}: {str(exc)[:140]}")
            return False

    # -- backend 2: sentence-transformers ------------------------------------
    def _try_sentence_transformer(self, model: str, fallback_model: str) -> bool:
        try:
            from sentence_transformers import SentenceTransformer
        except Exception:
            return False
        for name in (model, fallback_model):
            try:
                self._st_model = SentenceTransformer(name)
                self.backend = f"sentence-transformers:{name}"
                print(f"  using sentence-transformers: {name}")
                return True
            except Exception as exc:
                print(f"  could not load {name}: {str(exc)[:140]}")
        return False

    # -- encoding ------------------------------------------------------------
    def fit_transform(self, texts: list[str]) -> np.ndarray:
        if self.backend.startswith("patentbert"):
            return self._encode_patentbert(texts)
        if self.backend.startswith("sentence-transformers"):
            return np.asarray(self._st_model.encode(
                texts, batch_size=self.batch_size, show_progress_bar=True,
                normalize_embeddings=True,
            ))
        # TF-IDF
        from sklearn.feature_extraction.text import TfidfVectorizer
        self._vectorizer = TfidfVectorizer(
            max_features=4096, ngram_range=(1, 2), stop_words="english",
            sublinear_tf=True,
        )
        return self._vectorizer.fit_transform(texts).toarray()

    def _encode_patentbert(self, texts: list[str]) -> np.ndarray:
        import torch
        from tqdm import tqdm

        def mean_pool(last_hidden, mask):
            mask = mask.unsqueeze(-1).float()
            summed = (last_hidden * mask).sum(1)
            counts = mask.sum(1).clamp(min=1e-9)
            return summed / counts

        vecs = []
        with torch.no_grad():
            for i in tqdm(range(0, len(texts), self.batch_size), desc="PatentBERT"):
                batch = texts[i:i + self.batch_size]
                enc = self._hf_tok(batch, padding=True, truncation=True,
                                   max_length=256, return_tensors="pt")
                out = self._hf_model(**enc)
                emb = mean_pool(out.last_hidden_state, enc["attention_mask"])
                emb = torch.nn.functional.normalize(emb, p=2, dim=1)
                vecs.append(emb.cpu().numpy())
        return np.vstack(vecs)
