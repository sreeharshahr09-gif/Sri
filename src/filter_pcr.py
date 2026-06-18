"""
Screen the corpus down to: PCR tires + zero-degree belt + (ideally) hybrid cord.

Belt/overlay patents are dominated by truck and off-road tires, so the single
most important precision step is segment disambiguation. We score each document
for every segment and keep it as PCR only when PCR is the winning (or tied-top)
segment and there is a genuine zero-degree signal.
"""

from __future__ import annotations

import pandas as pd

from . import domain


def _text_blob(row: pd.Series) -> str:
    parts = [str(row.get(c, "")) for c in ("title", "abstract", "claims", "description")]
    return " ".join(parts).lower()


def _count_hits(text: str, terms: list[str]) -> int:
    return sum(text.count(t) for t in terms)


def _segment_scores(text: str) -> dict:
    return {seg: _count_hits(text, terms) for seg, terms in domain.SEGMENT_LEXICON.items()}


def annotate(df: pd.DataFrame) -> pd.DataFrame:
    """Add relevance/segment columns without dropping anything yet."""
    df = df.copy()
    blobs = df.apply(_text_blob, axis=1)

    df["zero_degree_hits"] = blobs.map(lambda t: _count_hits(t, domain.ZERO_DEGREE_LEXICON))
    df["hybrid_hits"] = blobs.map(lambda t: _count_hits(t, domain.HYBRID_LEXICON))

    seg = blobs.map(_segment_scores).apply(pd.Series)
    seg.columns = [f"seg_{c}" for c in seg.columns]
    df = pd.concat([df, seg], axis=1)

    def _winning_segment(r):
        scores = {s: r[f"seg_{s}"] for s in domain.SEGMENT_LEXICON}
        best = max(scores.values())
        if best == 0:
            return "UNKNOWN"
        # If PCR ties for the top score, treat as PCR (passenger is the default).
        if scores["PCR"] == best:
            return "PCR"
        return max(scores, key=scores.get)

    df["segment"] = df.apply(_winning_segment, axis=1)
    return df


def filter_corpus(df: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Apply the year window and topical/segment gates from config."""
    f = cfg["filtering"]
    y0, y1 = cfg["project"]["year_start"], cfg["project"]["year_end"]

    df = annotate(df)
    keep = pd.Series(True, index=df.index)

    # Year window (keep rows with missing year — better recall, flagged later).
    has_year = df["year"].notna()
    keep &= (~has_year) | df["year"].between(y0, y1)

    if f.get("require_zero_degree_signal", True):
        keep &= df["zero_degree_hits"] > 0

    if f.get("require_pcr_signal", True):
        excluded = set(f.get("exclude_segments", []))
        keep &= ~df["segment"].isin(excluded)

    result = df[keep].reset_index(drop=True)
    print(f"  screened {len(df)} -> {len(result)} relevant PCR zero-degree records")
    print(f"  of which {int((result['hybrid_hits'] > 0).sum())} mention hybrid-cord terms")
    return result
