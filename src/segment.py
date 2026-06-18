"""
Split patents into passages for embedding.

Embedding a whole patent as one vector is meaningless — they are thousands of
words covering many ideas. We embed at passage level so a single patent can land
in several technology clusters (e.g. one passage about adhesion, another about
high-speed durability).
"""

from __future__ import annotations

import re

import pandas as pd


def _split_text(text: str, max_chars: int) -> list[str]:
    text = re.sub(r"\s+", " ", str(text)).strip()
    if not text:
        return []
    # Split on sentence boundaries, then greedily pack into <= max_chars chunks.
    sentences = re.split(r"(?<=[.;])\s+", text)
    chunks, cur = [], ""
    for s in sentences:
        if len(cur) + len(s) + 1 <= max_chars:
            cur = f"{cur} {s}".strip()
        else:
            if cur:
                chunks.append(cur)
            cur = s[:max_chars]
    if cur:
        chunks.append(cur)
    return chunks


def to_passages(df: pd.DataFrame, max_chars: int = 1200) -> pd.DataFrame:
    """Explode the corpus into one row per passage, carrying provenance."""
    rows = []
    for _, r in df.iterrows():
        # Abstract + claims + description carry the substance; title is short.
        for section in ("abstract", "claims", "description"):
            for chunk in _split_text(r.get(section, ""), max_chars):
                if len(chunk) < 40:  # drop boilerplate fragments
                    continue
                rows.append({
                    "doc_id": r.get("doc_id", ""),
                    "assignee": r.get("assignee", ""),
                    "year": r.get("year"),
                    "segment": r.get("segment", ""),
                    "section": section,
                    "passage": chunk,
                })
    passages = pd.DataFrame(rows)
    print(f"  produced {len(passages)} passages from {len(df)} patents")
    return passages
