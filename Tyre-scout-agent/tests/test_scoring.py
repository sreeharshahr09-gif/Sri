"""Scoring arithmetic, weight validation, threshold behaviour, reproducibility."""
import json
from pathlib import Path

import pytest

from tire_scout.schemas import DevelopmentAnalysis
from tire_scout.scoring import is_included, validate_weights, weighted_score

WEIGHTS = {"relevance": 0.35, "novelty": 0.25, "evidence_quality": 0.20,
           "impact": 0.10, "actionability": 0.10}


def _analysis(scores: dict[str, float]) -> DevelopmentAnalysis:
    return DevelopmentAnalysis.model_validate({
        "maturity": "lab_result", "confidence_label": "medium", "is_new_development": True, "title": "T", "category": "C",
        "scores": {d: {"score": s, "rationale": "r"} for d, s in scores.items()}})


def test_weights_must_sum_to_one():
    with pytest.raises(ValueError):
        validate_weights({**WEIGHTS, "relevance": 0.5})
    with pytest.raises(ValueError):
        validate_weights({"relevance": 1.0})  # missing dims


def test_weighted_score_is_exact_python_arithmetic():
    a = _analysis({"relevance": 9, "novelty": 7, "evidence_quality": 6,
                   "impact": 7, "actionability": 8})
    total, breakdown = weighted_score(a, WEIGHTS)
    assert total == pytest.approx(9 * .35 + 7 * .25 + 6 * .20 + 7 * .10 + 8 * .10)
    assert breakdown.rationales["relevance"] == "r"


def test_dimension_range_enforced_by_schema():
    with pytest.raises(Exception):
        _analysis({"relevance": 11, "novelty": 5, "evidence_quality": 5,
                   "impact": 5, "actionability": 5})
    with pytest.raises(Exception):
        _analysis({"relevance": -1, "novelty": 5, "evidence_quality": 5,
                   "impact": 5, "actionability": 5})


def test_threshold_behaviour():
    assert is_included(5.0, 5.0)
    assert not is_included(4.999, 5.0)


def test_human_reviewed_eval_set():
    """Small reviewed set: scores must land in the agreed bands."""
    cases = json.loads((Path(__file__).parent / "fixtures" / "eval_set.json").read_text())
    for case in cases:
        a = _analysis(case["scores"])
        total, _ = weighted_score(a, WEIGHTS)
        lo, hi = case["expected_band"]
        assert lo <= total <= hi, f"{case['name']}: {total} not in [{lo}, {hi}]"
