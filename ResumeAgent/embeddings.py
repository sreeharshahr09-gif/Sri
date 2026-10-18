"""Optional semantic search over the CV with a local embedding model (llama-server --embedding).

Used only to *find* candidate lines (retrieval, "possibly related" suggestions, a second look at
gaps). Similarity is never evidence by itself: the model must still judge each line and its quote
must still be found verbatim in the CV.
"""
from __future__ import annotations

import math
import re
from typing import Any

import requests

from llm_client import LLMError, safe_server

_CONTACT = re.compile(r"(?i)\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b|\b(?:https?://|www\.)\S+|"
                      r"\+\d[\d ()-]{7,}\d|\b\d{3,}[ -]\d{3,}[ -]?\d{3,}\b")
QUERY_INSTRUCTION = ("Instruct: Given a job requirement, retrieve résumé lines that show evidence "
                     "of it\nQuery: ")


class EmbeddingClient:
    """OpenAI-compatible /v1/embeddings on a loopback llama-server (e.g. Qwen3-Embedding-0.6B)."""

    def __init__(self, base_url: str = "http://127.0.0.1:8081/v1", batch: int = 32, timeout: float = 120):
        self.base_url = safe_server(base_url)
        self.batch = batch
        self.timeout = timeout
        self.session = requests.Session()
        self.session.trust_env = False
        self._cache: dict[str, list[float]] = {}
        self.calls = 0

    def available(self) -> bool:
        try:
            return bool(self.embed(["ping"]))
        except Exception:
            return False

    def embed(self, texts: list[str]) -> list[list[float]]:
        todo = [t for t in dict.fromkeys(texts) if t not in self._cache]
        for start in range(0, len(todo), self.batch):
            part = todo[start:start + self.batch]
            try:
                r = self.session.post(self.base_url + "/embeddings", json={"input": part},
                                      timeout=(5, self.timeout))
            except requests.RequestException as exc:
                raise LLMError(f"Embedding server not reachable at {self.base_url}") from exc
            if r.status_code >= 400:
                raise LLMError(f"Embedding server returned HTTP {r.status_code}: {r.text[:200]}")
            self.calls += 1
            rows = sorted(r.json().get("data", []), key=lambda d: d.get("index", 0))
            if len(rows) != len(part):
                raise LLMError("Embedding server returned the wrong number of vectors.")
            for text, row in zip(part, rows):
                self._cache[text] = _normalise(row["embedding"])
        return [self._cache[t] for t in texts]


def _normalise(vec: Any) -> list[float]:
    if vec and isinstance(vec[0], list):  # some servers return one vector per token; pool them
        vec = [sum(col) / len(vec) for col in zip(*vec)]
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def cosine(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b))


def evidence_lines(source: str) -> list[str]:
    """Candidate evidence units: CV lines, with long paragraphs split into sentences."""
    units: list[str] = []
    for line in source.splitlines():
        line = line.strip()
        if len(line) < 15 or _CONTACT.search(line):
            continue  # contact details are never evidence
        if len(line) > 400:
            units += [s.strip() for s in re.split(r"(?<=[.;])\s+(?=[A-Z])", line) if len(s.strip()) >= 15]
        else:
            units.append(line)
    return list(dict.fromkeys(units))


class Retriever:
    """Ranks the CV's lines by meaning against each requirement."""

    def __init__(self, client: EmbeddingClient, source: str):
        self.client = client
        self.lines = evidence_lines(source)
        self.vectors = client.embed(self.lines) if self.lines else []

    def top(self, query: str, k: int = 6) -> list[tuple[str, float]]:
        if not self.lines:
            return []
        q = self.client.embed([QUERY_INSTRUCTION + query])[0]
        scored = sorted(((cosine(q, v), line) for line, v in zip(self.lines, self.vectors)), reverse=True)
        return [(line, round(score, 3)) for score, line in scored[:k]]
