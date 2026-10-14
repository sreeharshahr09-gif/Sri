"""Schema validation, grounding checks, quarantine path."""
import pytest
from pydantic import ValidationError

from tire_scout.graphs.daily import _validate_grounding
from tire_scout.schemas import DevelopmentAnalysis

VALID = {
    "what_changed": "A compound result was reported.",
    "technical_explanation": "The compound was assessed in laboratory testing.",
    "confidence_reasons": "Only laboratory evidence is available.",
    "maturity": "lab_result", "confidence_label": "medium", "is_new_development": True, "title": "T", "category": "Rubber compounds and materials",
    "scores": {d: {"score": 5, "rationale": "r"} for d in
               ["relevance", "novelty", "evidence_quality", "impact", "actionability"]}}


def test_malformed_model_output_rejected():
    with pytest.raises(ValidationError):
        DevelopmentAnalysis.model_validate({"title": "x"})  # missing scores
    bad = {**VALID, "scores": {**VALID["scores"],
                               "relevance": {"score": "high", "rationale": "r"}}}
    with pytest.raises(ValidationError):
        DevelopmentAnalysis.model_validate(bad)


def test_missing_dimension_rejected():
    bad = {**VALID}
    bad["scores"] = dict(VALID["scores"])
    del bad["scores"]["novelty"]
    with pytest.raises(ValidationError):
        DevelopmentAnalysis.model_validate(bad)


def test_overlong_evidence_quote_rejected():
    bad = {**VALID, "evidence_refs": [" ".join(["word"] * 60)]}
    with pytest.raises(ValidationError):
        DevelopmentAnalysis.model_validate(bad)


def test_fabricated_quote_flagged(cfg):
    a = DevelopmentAnalysis.model_validate(
        {**VALID, "evidence_refs": ["98 percent improvement in every metric"]})
    rec = {"text": "The compound showed a modest change in laboratory testing.",
           "extraction_quality": "full"}
    problems = _validate_grounding(a, rec, cfg)
    assert any("not found in source" in p for p in problems)


def test_wrong_category_flagged(cfg):
    a = DevelopmentAnalysis.model_validate({**VALID, "category": "Space elevators"})
    rec = {"text": "", "extraction_quality": "full"}
    problems = _validate_grounding(a, rec, cfg)
    assert any("category" in p for p in problems)


def test_partial_access_limitation_surfaced(cfg):
    a = DevelopmentAnalysis.model_validate({**VALID, "evidence_refs": ["abstract text"]})
    rec = {"text": "abstract text", "extraction_quality": "abstract_only"}
    problems = _validate_grounding(a, rec, cfg)
    assert problems == []  # not fatal
    assert a.content_limitation and "abstract_only" in a.content_limitation
