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


# Per-section chunk caps. High-signal sections (abstract, claims, the PatSeer
# AI summaries) are kept in full; the verbose full description is capped so a few
# huge patents cannot dominate the corpus or blow up runtime.
_SECTION_CAPS = {
    "abstract": None,
    "claims": 4,
    "ai_advantages": None,
    "ai_method": None,
    "ai_problem": None,
    "description": 3,
}


def to_passages(df: pd.DataFrame, max_chars: int = 1200) -> pd.DataFrame:
    """Explode the corpus into one row per passage, carrying provenance."""
    rows = []
    for _, r in df.iterrows():
        for section, cap in _SECTION_CAPS.items():
            chunks = _split_text(r.get(section, ""), max_chars)
            if cap is not None:
                chunks = chunks[:cap]
            for chunk in chunks:
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
    # Drop near-identical text repeated across sections of the same patent
    # (abstract/claims/description often restate each other).
    before = len(passages)
    passages["_key"] = (passages["doc_id"].astype(str) + "|"
                        + passages["passage"].str.slice(0, 200).str.lower())
    passages = passages.drop_duplicates("_key").drop(columns="_key").reset_index(drop=True)
    by_sec = passages["section"].value_counts().to_dict()
    print(f"  produced {len(passages)} passages ({before - len(passages)} dup "
          f"removed) from {len(df)} patents  {by_sec}")
    return passages
