"""Evidence validation, bounded repair, and deterministic report eligibility."""
from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
import uuid
from datetime import date, datetime
from pathlib import Path
from urllib.parse import urlsplit

from .schemas import ConfidenceLabel, DevelopmentAnalysis
from .timeutil import get_tz

QUALITY_VERSION = "quality-2.0"

STAGE_SIGNALS = {
    'prototype': r'\bprototype\b',
    'pilot': r'\b(?:pilot|field trial|field test)\b',
    'commercial': r'commercially available|\bon sale\b|\bin production\b|\blaunched\b|\bsells?\b|\boffers?\b',
    'lab_result': r'\b(?:laboratory|lab|experiment\w*|bench|testing|tested|measurements?)\b',
    'simulation': r'\b(?:simulat\w*|finite.element|modelling|modeling)\b',
    'patent_disclosure': r'\b(?:patent|claim\w*|disclos\w*)\b',
}


def claim_problems(analysis, rec):
    """Necessary support checks, not a proof of every narrative inference."""
    problems = []
    for field in ('novelty_quote', 'maturity_quote', 'benefit_quote'):
        quote = getattr(analysis, field)
        if quote and (len(quote.split()) < 4 or not quote_match(quote, rec.get('text', ''))):
            problems.append(f'{field} must be an exact contextual source excerpt (4-40 words)')
    if analysis.is_new_development:
        if rec.get('source_type') in ('reference', 'market_report'):
            problems.append('Reference/market report is background, not a new technical development')
        if not analysis.novelty_quote:
            problems.append('Novelty requires novelty_quote naming the actual technical development; otherwise set is_new_development=false')
        elif not re.search(r'\b(?:new|first|introduc\w*|launch\w*|unveil\w*|develop\w*|propos\w*|present\w*|breakthrough)\b', analysis.novelty_quote, re.I):
            problems.append('novelty_quote does not identify an introduced/developed technology; classify as background if not established')
    stage = analysis.maturity.value
    if stage in STAGE_SIGNALS:
        quote = analysis.maturity_quote or ''
        if not re.search(STAGE_SIGNALS[stage], quote, re.I) or re.search(r'\b(?:no|not|without)\b.{0,24}(?:' + STAGE_SIGNALS[stage] + ')', quote, re.I):
            problems.append(f'Maturity {stage} requires an explicit supporting maturity_quote; use supplier_claim/speculation when unestablished')
        elif stage == 'lab_result' and not re.search(r'\b(?:results?|measured|showed|achieved|observed|compared|demonstrated)\b', quote, re.I):
            problems.append('lab_result requires a reported experimental result, not merely availability of testing equipment')
    if analysis.claimed_benefit:
        if not analysis.benefit_quote:
            problems.append('claimed_benefit requires benefit_quote; remove unsupported benefit assertions')
        conditions = (analysis.baseline_conditions or '').strip()
        unspecified = not conditions or re.fullmatch(r'(?:unknown|none|n/a|not (?:stated|specified|reported|provided)|no (?:test )?conditions (?:were )?(?:published|reported))\.?', conditions, re.I)
        if re.search(r'\d', analysis.claimed_benefit) and unspecified:
            problems.append('Quantitative claimed benefit requires baseline/test conditions or must be removed')
    narrative = ' '.join(filter(None, [analysis.what_changed, analysis.technical_explanation, analysis.confidence_reasons]))
    if re.search(r'no (?:evidence of fabrication|unsupported claims)|no evidence of .*unverified claims', narrative, re.I):
        problems.append('Absence of fabrication/unsupported claims is not established by source plausibility; state evidence limitations')
    return problems


def calibrate_claims(analysis, rec):
    adjustments = []
    if rec.get('source_type') not in ('paper', 'patent') and analysis.confidence_label == 'high':
        analysis.confidence_label = ConfidenceLabel.medium
        adjustments.append('Confidence capped at medium: this single source does not establish independent technical validation.')
    basis = [q for q in (analysis.novelty_quote, analysis.maturity_quote, analysis.benefit_quote) if q]
    analysis.evidence_refs = list(dict.fromkeys(basis + analysis.evidence_refs))[:6]
    for note in adjustments + rec.get('date_notes', []):
        analysis.limitations = ((analysis.limitations or '') + ' ' + note).strip()
    return adjustments


def _fold(text: str, typography: bool = False) -> str:
    if typography:
        text = unicodedata.normalize("NFC", text).translate(str.maketrans(
            {"\u2018": "'", "\u2019": "'", "\u201c": '"', "\u201d": '"'}))
    return " ".join(text.split())


def quote_match(quote: str, source: str) -> str | None:
    """No fuzzy matching, unit folding, hyphen removal, or number rewriting."""
    q = _fold(quote)
    if not q:
        return None
    if q in _fold(source):
        return "verbatim"
    q, text = _fold(quote, True), _fold(source, True)
    if q in text:
        return "quotation_typography"
    # An enclosing pair added by the model is presentation, not source content.
    if len(q) > 2 and q[0] == q[-1] and q[0] in ('"', "'"):
        q = q[1:-1].strip()
        if q and q in text:
            return "outer_quotation_marks"
    return None


def validate_grounding(analysis: DevelopmentAnalysis, rec: dict, cfg) -> list[str]:
    problems = []
    if analysis.category not in cfg.topic_names:
        problems.append(f"category {analysis.category!r} not in configured topics")
    for field in ("what_changed", "technical_explanation", "confidence_reasons"):
        value = getattr(analysis, field)
        if not value or value.strip().lower() in ("", "n/a", "none", "unknown"):
            problems.append(f"{field} requires a substantive explanation, including for background material")
    # Repetition is not additional evidence; the unmodified provider output is
    # still retained in the audit record.
    analysis.evidence_refs = list(dict.fromkeys(analysis.evidence_refs))
    if analysis.is_new_development and not analysis.evidence_refs:
        problems.append("a proposed development requires at least one source quote")
    for quote in analysis.evidence_refs:
        if quote_match(quote, rec.get("text") or "") is None:
            problems.append(f"evidence quote not found in source: {quote!r}")
    if rec.get('interpretation_policy'):
        problems.extend(claim_problems(analysis, rec))
    access = rec.get("extraction_quality")
    if access in ("partial", "abstract_only", "ocr_low") or rec.get("text_truncated"):
        limitation = ("source text truncated before analysis" if rec.get("text_truncated")
                      else f"only {access} text accessible")
        if not analysis.content_limitation:
            analysis.content_limitation = limitation
        if limitation not in (analysis.limitations or ""):
            analysis.limitations = ((analysis.limitations or "") + " [access: " + limitation + "]").strip()
    return problems


def prepare_source(rec: dict) -> dict:
    """Accurate analyzed length and conservative non-content rejection."""
    rec = dict(rec)
    text = rec.get("text") or ""
    original_len = max(rec.get("original_text_len") or 0, rec.get("text_len") or 0, len(text))
    text = text[:30000]  # Also bound direct replay/helper callers to the actual model input.
    rec["text"] = text
    rec["original_text_len"] = original_len
    rec["text_len"] = len(text)
    rec["text_truncated"] = bool(rec.get("text_truncated") or original_len > len(text))
    if rec["text_truncated"] and rec.get("extraction_quality") == "full":
        rec["extraction_quality"] = "partial"
    return rec


def unusable_source(rec: dict) -> str | None:
    text = rec.get("text") or ""
    host = (urlsplit(rec.get("url") or "").hostname or "").lower()
    if host in ("youtube.com", "www.youtube.com", "youtu.be", "m.youtube.com") \
            and not rec.get("transcript_available"):
        return "video transcript unavailable; page HTML is not video evidence"
    if not text.strip() or rec.get("extraction_quality") == "none":
        return "no substantive extracted source text"
    if len(text.strip()) < 200 and sum(term in text.lower() for term in
            ("privacy", "terms", "copyright", "advertise", "contact us")) >= 3:
        return "navigation/footer only; no substantive extracted source text"
    return None


def evidence_options(text: str, analysis: DevelopmentAnalysis | None) -> list[str]:
    """Retrieve exact source slices for repair, never approve approximate quotes.

    Token overlap only ranks passages the model may select. Every returned
    option is an unchanged contiguous slice; the full source stays in context.
    """
    words = list(re.finditer(r"\S+", text))
    if not words:
        return []
    windows = [text[words[i].start():words[min(i + 23, len(words) - 1)].end()]
               for i in range(0, len(words), 12)]
    def tokens(s):
        return set(re.findall(r"[\w/-]+", s.lower()))
    queries = (analysis.evidence_refs + [q for q in (analysis.novelty_quote, analysis.maturity_quote, analysis.benefit_quote) if q]) if analysis else []
    if not queries:
        return windows[:12]
    options = []
    for query in queries:
        q = tokens(query)
        ranked = sorted(enumerate(windows), key=lambda item: (
            -len(q & tokens(item[1])) / max(1, len(q | tokens(item[1]))), item[0]))
        for _, passage in ranked[:3]:
            if passage not in options:
                options.append(passage)
    return options[:18]


def assess_date(rec: dict, edition_date: date, cfg) -> tuple[str, str]:
    """Uses the edition date (including catch-up), not today's wall clock."""
    raw = rec.get("published_at")
    try:
        published = raw if isinstance(raw, datetime) else datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return "unverified", "publication date unavailable or invalid"
    if published.tzinfo:
        published = published.astimezone(get_tz(cfg.general.timezone))
    age = (edition_date - published.date()).days
    if age < 0:
        return "unverified", "publication date is after this edition"
    provenance = rec.get("published_provenance", "unknown")
    if provenance not in ("page_meta", "page_text"):
        return "unverified", "publication date not verified on the source"
    stype = rec.get("source_type", "news")
    stype = getattr(stype, "value", stype)
    window = cfg.search.recency_windows.get(stype, 30)
    if age > window:
        return "background", f"source is {age} days old; {stype} window is {window} days"
    return "recent", f"source date is within the {window}-day {stype} window"


def report_section(rec: dict, edition_date: date, cfg) -> tuple[str, str]:
    if rec.get('source_type') in ('reference', 'market_report'):
        return 'background', 'Reference or market context; not a new technical development'
    if rec.get('source_type') == 'unknown':
        return 'unverified', 'Source type is unresolved; requires review'
    section, reason = assess_date(rec, edition_date, cfg)
    if section == "recent":
        if rec.get("is_background"):
            return "background", "source was identified as background material"
        if rec.get("confidence_label") in (None, "low") or rec.get("maturity") in (None, "speculation"):
            return "unverified", "evidence confidence or maturity requires review"
        if not rec.get("evidence_refs"):
            return "unverified", "no retained source quotations"
    return section, reason


def select_report_items(devs: list[dict], edition_date: date, cfg) -> dict[str, list[dict]]:
    groups = {"recent": [], "background": [], "unverified": []}
    eligible = []
    for dev in devs:
        if dev.get("status", "active") != "active" or (dev.get("final_score") or 0) < cfg.scoring.inclusion_threshold:
            continue
        section, reason = report_section(dev, edition_date, cfg)
        eligible.append({**dev, "report_section": section, "review_reason": reason})
    # Keep the existing TOTAL daily item cap; recent material has priority.
    eligible.sort(key=lambda d: (d["report_section"] != "recent", -(d.get("final_score") or 0), d["id"]))
    for dev in eligible[:cfg.scoring.max_items_daily]:
        groups[dev["report_section"]].append(dev)
    return groups


def _save_audit(path: Path, payload: dict, cfg) -> None:
    encoded = json.dumps(payload, indent=2, ensure_ascii=False, default=str)
    # Never record configured credentials, even if accidentally echoed by a source/model.
    names = [cfg.search.api_key_env, cfg.email.smtp.username_env,
             cfg.email.smtp.password_env, cfg.email.graph.client_secret_env]
    for name in names:
        value = os.environ.get(name)
        if value:
            encoded = encoded.replace(value, "[REDACTED]").replace(
                json.dumps(value, ensure_ascii=False)[1:-1], "[REDACTED]")
    temporary = path.with_suffix(".tmp")
    temporary.write_text(encoded + "\n", encoding="utf-8")
    temporary.replace(path)


def analyze_with_quality(llm, cfg, rec: dict, edition_date: date, prior_titles: list[str]):
    """Two calls maximum: initial analysis, then one evidence/schema correction.

    Returns (analysis or None, full problems, audit path). Audit write failure is
    deliberately fatal: rejected evidence must not silently disappear.
    """
    rec = prepare_source(rec)
    audit_dir = Path(cfg.general.data_dir) / "quality" / str(edition_date)
    audit_dir.mkdir(parents=True, exist_ok=True)
    path = audit_dir / f"{uuid.uuid4().hex}.json"
    metadata = {k: rec.get(k) for k in ("published_at", "published_provenance",
                "extraction_quality", "source_type", "text_len", "original_text_len", "text_truncated",
                "fetch_url", "final_url", "source_origin", "classification_reason", "interpretation_policy",
                "date_candidates", "date_notes", "modified_at", "provider_published_at")}
    metadata.update(canonical_url=rec["url"], edition_date=str(edition_date),
                    date_assessment=assess_date(rec, edition_date, cfg))
    from .llm import load_prompt
    payload = {"quality_version": QUALITY_VERSION, "model": cfg.llm.model,
               "prompt_version": load_prompt("analyze_development")[0],
               "metadata": metadata, "source_text": rec.get("text", ""),
               "source_sha256": hashlib.sha256(rec.get("text", "").encode()).hexdigest(),
               "attempts": [], "outcome": "started"}
    reason = unusable_source(rec)
    if reason:
        payload.update(outcome="unreadable", problems=[reason])
        _save_audit(path, payload, cfg)
        return None, [reason], str(path)
    _save_audit(path, payload, cfg)
    feedback = None
    for attempt in range(2):
        analysis = None
        if hasattr(llm, "last_attempts"):
            llm.last_attempts = []
        try:
            args = dict(doc_meta=metadata, doc_text=rec.get("text", ""),
                        prior_titles=prior_titles, valid_categories=cfg.topic_names)
            if feedback is not None:
                args["feedback"] = feedback
            analysis = llm.analyze(**args)
            problems = validate_grounding(analysis, rec, cfg)
            adjustments = calibrate_claims(analysis, rec) if not problems and rec.get('interpretation_policy') else []
            entry = {"attempt": attempt + 1, "analysis": analysis.model_dump(mode="json"),
                     "problems": problems, "policy_adjustments": adjustments,
                     "quote_matches": [quote_match(q, rec.get("text", "")) for q in analysis.evidence_refs]}
        except Exception as exc:
            problems = [f"analysis error: {type(exc).__name__}: {exc}"]
            entry = {"attempt": attempt + 1, "analysis": None, "problems": problems}
        entry["provider_attempts"] = getattr(llm, "last_attempts", [])
        payload["attempts"].append(entry)
        payload.update(outcome="accepted" if not problems else "quarantined", problems=problems)
        _save_audit(path, payload, cfg)
        if not problems:
            return analysis, [], str(path)
        feedback = {"problems": problems,
                    "previous_analysis": analysis.model_dump(mode="json") if analysis else None,
                    "provider_attempts": entry["provider_attempts"] if analysis is None else [],
                    "source_excerpt_options": evidence_options(rec.get("text", ""), analysis)}
        payload["repair_excerpt_options"] = feedback["source_excerpt_options"]
    return None, problems, str(path)
