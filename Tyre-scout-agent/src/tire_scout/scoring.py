"""Trusted weighted scoring. The LLM proposes 0-10 dimension scores with
rationales; all arithmetic happens here, in Python, with validation.
"""
from __future__ import annotations

from .schemas import DevelopmentAnalysis, ScoreBreakdown

DIMENSIONS = ("relevance", "novelty", "evidence_quality", "impact", "actionability")


def validate_weights(weights: dict[str, float]) -> None:
    unknown = set(weights) - set(DIMENSIONS)
    missing = set(DIMENSIONS) - set(weights)
    if unknown:
        raise ValueError(f"unknown scoring dimensions: {sorted(unknown)}")
    if missing:
        raise ValueError(f"missing scoring dimensions: {sorted(missing)}")
    for dim, w in weights.items():
        if not (0.0 <= w <= 1.0):
            raise ValueError(f"weight for {dim} out of range: {w}")
    total = sum(weights.values())
    if abs(total - 1.0) > 1e-6:
        raise ValueError(f"scoring weights must sum to 1.0, got {total:.6f}")


def weighted_score(analysis: DevelopmentAnalysis, weights: dict[str, float]) -> tuple[float, ScoreBreakdown]:
    """Compute the weighted 0-10 score from a validated analysis."""
    validate_weights(weights)
    breakdown = ScoreBreakdown(
        relevance=analysis.scores["relevance"].score,
        novelty=analysis.scores["novelty"].score,
        evidence_quality=analysis.scores["evidence_quality"].score,
        impact=analysis.scores["impact"].score,
        actionability=analysis.scores["actionability"].score,
        rationales={dim: analysis.scores[dim].rationale for dim in DIMENSIONS},
    )
    total = sum(getattr(breakdown, dim) * weights[dim] for dim in DIMENSIONS)
    return round(total, 3), breakdown


def is_included(final_score: float, threshold: float) -> bool:
    return final_score >= threshold
