"""Regression cases from the saved-source audit: preserve scientific claims."""
import json
from datetime import date, timedelta
from pathlib import Path

import httpx
import pytest
from pydantic import ValidationError

from tire_scout.llm import OpenAICompatClient, OllamaClient, _analysis_schema
from tire_scout.quality import (analyze_with_quality, assess_date, prepare_source,
                                quote_match, report_section, select_report_items, evidence_options)
from tire_scout.schemas import DevelopmentAnalysis

DAY = date(2026, 10, 13)
SOURCE = "Steel cords improve durability. IR-Si/urea/Si-75 showed 12 percent improvement."


def analysis(quote="Steel cords improve durability.", **overrides):
    return DevelopmentAnalysis.model_validate({
        "is_new_development": True, "title": "Cord development",
        "category": "Rubber compounds and materials", "maturity": "lab_result",
        "what_changed": "A cord formulation was tested.",
        "technical_explanation": "Steel cords improve durability in the reported test.",
        "confidence_reasons": "Laboratory evidence; independent validation is absent.",
        "confidence_label": "medium", "evidence_refs": [quote],
        "scores": {k: {"score": 7, "rationale": "Test evidence"} for k in
                   ("relevance", "novelty", "evidence_quality", "impact", "actionability")},
        **overrides})


def source(**overrides):
    return {"url": "https://example.com/article", "text": SOURCE,
            "extraction_quality": "full", "published_at": str(DAY),
            "published_provenance": "page_meta", "source_type": "paper", **overrides}


@pytest.mark.parametrize("quote,text,method", [
    ("Steel cords improve durability.", "Steel cords\n improve durability.", "verbatim"),
    ('The "new" material', "The \u201cnew\u201d material", "quotation_typography"),
    ('"Steel cords improve durability."', SOURCE, "outer_quotation_marks"),
])
def test_only_presentation_equivalences_are_allowed(quote, text, method):
    assert quote_match(quote, text) == method


@pytest.mark.parametrize("quote,text", [
    ("Tire cords improve durability.", SOURCE),
    ("IR-Si/Si-75/urea", SOURCE),
    ("13 percent improvement", SOURCE),
    ("No significant improvement", "Significant improvement"),
    ("10 m2/g", "10 m\n2\n/g"),
    ("min-1", "min\u22121"),
    ("10 pa", "10 Pa"),
    ("dependent", "depend-\nent"),
    ("Improved cord durability", SOURCE),
    ("", SOURCE),
])
def test_scientific_and_wording_changes_remain_invalid(quote, text):
    assert quote_match(quote, text) is None


class Scripted:
    def __init__(self, *responses):
        self.responses, self.calls = responses, []

    def analyze(self, **kwargs):
        self.calls.append(kwargs)
        result = self.responses[min(len(self.calls) - 1, len(self.responses) - 1)]
        if isinstance(result, Exception):
            raise result
        return result.model_copy(deep=True)


def test_one_grounding_correction_saves_both_full_responses(cfg):
    invalid = "Tire cords improve durability and demonstrate a claim that is deliberately longer than sixty characters."
    llm = Scripted(analysis(invalid), analysis())
    result, errors, path = analyze_with_quality(llm, cfg, source(), DAY, [])
    assert result and not errors and len(llm.calls) == 2
    assert llm.calls[0]["doc_text"] == llm.calls[1]["doc_text"] == SOURCE
    assert invalid in str(llm.calls[1]["feedback"])
    assert all(q in SOURCE for q in llm.calls[1]["feedback"]["source_excerpt_options"])
    audit = json.loads(Path(path).read_text(encoding="utf-8"))
    assert audit["source_text"] == SOURCE
    assert audit["attempts"][0]["analysis"]["evidence_refs"] == [invalid]
    assert invalid in audit["attempts"][0]["problems"][0]
    assert audit["attempts"][1]["quote_matches"] == ["verbatim"]
    assert audit["metadata"]["edition_date"] == str(DAY) and audit["prompt_version"] == "analyze-1.3"


@pytest.mark.parametrize("bad", [analysis("Tire cords improve durability."),
                                   analysis(evidence_refs=[]),
                                   analysis(category="None")])
def test_invalid_evidence_cannot_escape_by_retrying_forever(cfg, bad):
    llm = Scripted(bad)
    result, errors, path = analyze_with_quality(llm, cfg, source(), DAY, [])
    assert result is None and errors and len(llm.calls) == 2
    assert json.loads(Path(path).read_text())["outcome"] == "quarantined"


def test_transcriptless_video_never_calls_model(cfg):
    llm = Scripted(AssertionError("must not run"))
    result, errors, path = analyze_with_quality(llm, cfg,
        source(url="https://www.youtube.com/watch?v=sample", text="About Privacy Terms Copyright"), DAY, [])
    assert not result and not llm.calls and "transcript" in errors[0]
    assert json.loads(Path(path).read_text())["outcome"] == "unreadable"


def test_truncation_is_visible_in_metadata_and_limitations(cfg):
    rec = prepare_source(source(text_len=len(SOURCE) + 1000))
    assert rec["text_len"] == len(SOURCE) and rec["text_truncated"]
    assert rec["extraction_quality"] == "partial"
    llm = Scripted(analysis())
    result, _, _ = analyze_with_quality(llm, cfg, rec, DAY, [])
    assert "truncated" in result.limitations
    assert llm.calls[0]["doc_meta"]["original_text_len"] == len(SOURCE) + 1000


def test_maturity_and_confidence_cannot_silently_default():
    data = analysis().model_dump()
    for field in ("maturity", "confidence_label"):
        with pytest.raises(ValidationError):
            DevelopmentAnalysis.model_validate({k: v for k, v in data.items() if k != field})


def test_provider_schema_requires_technical_explanations():
    data = analysis().model_dump()
    for field in ("what_changed", "technical_explanation", "confidence_reasons"):
        with pytest.raises(ValidationError):
            _analysis_schema(None).model_validate({k: v for k, v in data.items() if k != field})


def test_valid_quotes_with_missing_explanation_still_require_correction(cfg):
    llm = Scripted(analysis(technical_explanation=None), analysis())
    result, errors, _ = analyze_with_quality(llm, cfg, source(), DAY, [])
    assert result and not errors and len(llm.calls) == 2
    assert any("technical_explanation" in p for p in llm.calls[1]["feedback"]["problems"])


@pytest.mark.parametrize("changes,section", [
    ({}, "recent"),
    ({"published_at": "2012-07-24"}, "background"),
    ({"published_at": None}, "unverified"),
    ({"published_at": "bad date"}, "unverified"),
    ({"published_at": "2026-10-14"}, "unverified"),
    ({"published_provenance": "provider"}, "unverified"),
    ({"confidence_label": "low"}, "unverified"),
    ({"maturity": "speculation"}, "unverified"),
    ({"evidence_refs": []}, "unverified"),
    ({"is_background": True}, "background"),
])
def test_date_and_evidence_are_independent_of_high_relevance(cfg, changes, section):
    rec = {**source(), **analysis().model_dump(mode="json"), "final_score": 10, **changes}
    assert report_section(rec, DAY, cfg)[0] == section


def test_date_window_is_relative_to_edition_and_source_type(cfg):
    window = cfg.search.recency_windows.get("paper", 30)
    rec = source(published_at=str(DAY - timedelta(days=window)))
    assert assess_date(rec, DAY, cfg)[0] == "recent"
    assert assess_date(rec, DAY + timedelta(days=1), cfg)[0] == "background"


def test_daily_cap_is_shared_by_all_sections_with_recent_first(cfg):
    cfg.scoring.max_items_daily = 2
    template = {**source(), **analysis().model_dump(mode="json"), "status": "active"}
    records = [{**template, "id": "old", "final_score": 10, "published_at": "2012-07-24"},
               {**template, "id": "recent", "final_score": 7},
               {**template, "id": "undated", "final_score": 9, "published_at": None},
               {**template, "id": "quarantined", "final_score": 10, "status": "quarantined"}]
    groups = select_report_items(records, DAY, cfg)
    assert [d["id"] for d in groups["recent"]] == ["recent"]
    assert [d["id"] for d in groups["background"]] == ["old"]
    assert not groups["unverified"]


def test_audit_redacts_configured_secret_values(cfg, monkeypatch):
    secret = 'fixture-secret-"quoted"-value'
    monkeypatch.setenv(cfg.search.api_key_env, secret)
    _, _, path = analyze_with_quality(Scripted(analysis()), cfg,
                                      source(text=SOURCE + secret), DAY, [])
    audit = json.loads(Path(path).read_text(encoding="utf-8"))
    assert secret not in audit["source_text"] and "[REDACTED]" in audit["source_text"]


def test_invalid_openai_json_gets_one_correction_total_and_raw_is_saved(cfg, monkeypatch):
    calls = []
    def post(url, **kwargs):
        calls.append(kwargs)
        return httpx.Response(200, json={"choices": [{"message": {"content": "INVALID RAW JSON"}}]},
                              request=httpx.Request("POST", url))
    monkeypatch.setattr(httpx, "post", post)
    cfg.llm.repair_attempts = 8  # Analysis has its own hard total of two calls.
    result, _, path = analyze_with_quality(OpenAICompatClient(cfg), cfg, source(), DAY, [])
    assert result is None and len(calls) == 2
    audit = json.loads(Path(path).read_text())
    for attempt in audit["attempts"]:
        assert attempt["provider_attempts"][0]["raw_response"]["choices"][0]["message"]["content"] == "INVALID RAW JSON"


def test_ollama_raw_invalid_response_is_retained(cfg):
    calls = []
    class Chat:
        def with_structured_output(self, schema, **kwargs):
            assert kwargs == {"include_raw": True}
            return self
        def invoke(self, messages):
            calls.append(messages)
            return {"raw": {"content": "INVALID OLLAMA"}, "parsed": None,
                    "parsing_error": ValueError("wrong schema")}
    client = object.__new__(OllamaClient)
    client.cfg, client._chat, client.guard = cfg, Chat(), "guard"
    result, _, path = analyze_with_quality(client, cfg, source(), DAY, [])
    assert result is None and len(calls) == 2
    audit = json.loads(Path(path).read_text())
    assert audit["attempts"][0]["provider_attempts"][0]["raw_response"]["content"] == "INVALID OLLAMA"


def test_http_failure_retains_server_diagnostic(cfg, monkeypatch):
    monkeypatch.setattr(httpx, "post", lambda url, **kwargs:
        httpx.Response(503, json={"error": "model loading"}, request=httpx.Request("POST", url)))
    result, _, path = analyze_with_quality(OpenAICompatClient(cfg), cfg, source(), DAY, [])
    assert result is None
    audit = json.loads(Path(path).read_text())
    raw = audit["attempts"][0]["provider_attempts"][0]["raw_response"]
    assert raw["http_status"] == 503 and "model loading" in raw["body"]


def test_report_sections_and_internal_links_agree_with_json(cfg, db):
    from bs4 import BeautifulSoup
    from conftest import make_development, seed_edition
    from tire_scout.report.render import render_daily
    from datetime import datetime
    seed_edition(db, DAY)
    recent = make_development("recent", DAY, 8, "Recent development")
    old = make_development("old", DAY, 10, "Historical patent")
    old.published_at = datetime(2012, 7, 24)
    undated = make_development("undated", DAY, 9, "Undated source")
    undated.published_at = None
    undated.evidence_refs = ['<script>alert("source text")</script>']
    for dev in (recent, old, undated):
        db.insert_development(dev)
    paths = render_daily(cfg, db, DAY)
    data = json.loads(Path(paths["json"]).read_text())
    assert [d["id"] for d in data["developments"]] == ["recent"]
    assert [d["id"] for d in data["background"]] == ["old"]
    assert [d["id"] for d in data["unverified"]] == ["undated"]
    html = BeautifulSoup(Path(paths["html"]).read_text(encoding="utf-8"), "html.parser")
    assert html.select_one("#item-recent-1 h2").text == "1. Recent development"
    assert html.select_one("#item-background-1 h2").text == "1. Historical patent"
    assert html.select_one("#item-unverified-1 h2").text == "1. Undated source"
    for link in html.select('a[href^="#"]'):
        assert html.select_one(link["href"]) is not None
    assert "Reported development:" not in html.select_one("#item-background-1").text
    assert "Source summary:" in html.select_one("#item-background-1").text
    assert not html.find("script")
    assert '<script>alert("source text")</script>' in html.select_one("#item-unverified-1").text


def test_repair_choices_are_exact_source_slices_and_schema_rejects_rewriting():
    text = "The material uses IR-Si/urea/Si-75 and has a surface area of 69 m\n2\n/g. " * 6
    choices = evidence_options(text, analysis("IR-Si/Si-75/urea with 69 m2/g"))
    assert choices and all(q in text and len(q.split()) <= 24 for q in choices)
    schema = _analysis_schema({"source_excerpt_options": choices})
    data = analysis().model_dump()
    corrected = schema.model_validate({**data, "evidence_refs": [choices[0]]})
    assert quote_match(corrected.evidence_refs[0], text)
    with pytest.raises(ValidationError):
        schema.model_validate({**data, "evidence_refs": ["IR-Si/Si-75/urea with 69 m2/g"]})
    assert schema.model_json_schema()["properties"]["evidence_refs"]["items"]["enum"] == choices
