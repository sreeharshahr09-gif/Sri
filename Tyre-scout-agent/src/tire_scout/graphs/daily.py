"""Daily workflow: determine edition date -> discover -> fetch/extract ->
deduplicate -> analyze -> score -> validate -> persist edition -> render ->
delivery gate -> email submission -> reconcile.

Routing is explicit. The LLM is consulted only inside bounded nodes; all
budgets, dates, scores, gates and submission live in trusted Python.
"""
from __future__ import annotations

import json
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from langgraph.graph import END, START, StateGraph

from .. import dedup as dedup_mod
from ..dedup import canonicalize_url
from ..emailer import journal as send_journal
from ..emailer.base import SubmissionError, build_adapter
from ..llm import LLMClient, LLMContractError, load_prompt
from ..schemas import (Development, DevelopmentAnalysis, FetchedDocument,
                       Maturity, ConfidenceLabel, SourceType)
from ..scoring import weighted_score
from ..quality import (QUALITY_VERSION, analyze_with_quality, assess_date, prepare_source,
                       select_report_items, unusable_source,
                       validate_grounding as _validate_grounding)
from ..security import scan_for_injection, scrub_report
from ..timeutil import edition_date_for, now_local
from ..tools import AgentTools, SearchIn, FetchIn
from ..sources import SourceRegistry, classify_source
from ..discovery.http import DiscoveryHttp
from ..discovery.run import discover_candidates
from ..discovery.window import EditionWindow, edition_window, local_date
from .states import DailyState

HISTORY_DAYS = 60
SUMMARY_MIN_CHARS = 120     # shortest feed/news summary worth analysing when a page is blocked


@dataclass
class Deps:
    cfg: object
    db: object
    llm: LLMClient
    tools: AgentTools
    tz: object
    discovery_http: object = None   # injectable for offline tests


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_daily_graph(deps: Deps, checkpointer=None):
    cfg, db, llm, tools = deps.cfg, deps.db, deps.llm, deps.tools
    registry = SourceRegistry(cfg.source_registry_path)
    http = deps.discovery_http or DiscoveryHttp(policy=tools.fetcher)

    def _window(state) -> EditionWindow:
        w = state.get("window") or {}
        return EditionWindow(date.fromisoformat(w["window_start"]), date.fromisoformat(w["window_end"]))

    # ------------------------------------------------------------------ #
    def determine_edition_date(state: DailyState) -> dict:
        if not state.get("edition_date"):
            d = edition_date_for(now_local(deps.tz))
            state = {**state, "edition_date": d.isoformat()}
        edition = date.fromisoformat(state["edition_date"])
        window = edition_window(edition, lookback_days=cfg.discovery.lookback_days,
                                max_catchup_days=cfg.discovery.max_catchup_days,
                                last_success=db.last_successful_edition(edition))
        base = {"edition_date": state["edition_date"], "window": window.as_dict(),
                "errors": [], "limitations": list(registry.warnings),
                "excluded": [], "quarantined": [], "developments": [],
                "already_done": False, "included_ids": [], "background_ids": [], "unverified_ids": []}
        # idempotent rerun: a completed edition is not re-researched; the gate
        # still runs so preview rendering is available, but sends stay blocked
        existing = db.daily_edition(date.fromisoformat(state["edition_date"]))
        if existing and existing["status"] in ("research_complete", "no_updates") \
                and not state.get("retrospective"):
            devs = db.developments_for_edition(date.fromisoformat(state["edition_date"]))
            groups = select_report_items(devs, date.fromisoformat(state["edition_date"]), cfg)
            coverage = json.loads(existing["coverage_summary"] or "{}")
            coverage.update(included=len(groups["recent"]), background=len(groups["background"]),
                            unverified=len(groups["unverified"]), quality_version=QUALITY_VERSION)
            return {**base, "already_done": True,
                    "developments": devs,
                    "included_ids": [d["id"] for d in groups["recent"]],
                    "background_ids": [d["id"] for d in groups["background"]],
                    "unverified_ids": [d["id"] for d in groups["unverified"]],
                    "status": existing["status"],
                    "coverage_summary": coverage}
        return base

    def plan_queries(state: DailyState) -> dict:
        """Deterministic discovery plan. The model no longer writes search
        queries: its topic phrases found evergreen pages, not news."""
        plan = []
        if cfg.search.provider == "fixture":
            return {"queries": [{"channel": "fixture", "query": q} for q in cfg.discovery.news_search.queries]}
        if cfg.discovery.feeds_enabled:
            plan += [{"channel": "feed", "query": f.name} for f in registry.feeds if f.enabled]
        if cfg.discovery.news_search.enabled:
            plan += [{"channel": p, "query": q} for p in cfg.discovery.news_search.providers
                     for q in cfg.discovery.news_search.queries]
        if cfg.discovery.papers.enabled:
            plan.append({"channel": "papers", "query": cfg.discovery.papers.query})
        return {"queries": plan}

    def discover(state: DailyState) -> dict:
        window = _window(state)
        result = discover_candidates(cfg=cfg, registry=registry, http=http, tools=tools, db=db,
                                     window=window, tz=deps.tz)
        excluded = list(state.get("excluded", []))
        for c in result["dropped"]:
            excluded.append({"ref": c.canonical_url or c.url, "reason": c.skip_reason,
                             "stage": "discovery", "title": c.title, "channel": c.channel})
        limitations = list(state.get("limitations", []))
        failed = [s for s in result["sources"] if s.get("status") == "failed"]
        if failed:
            limitations.append(f"{len(failed)} of {len(result['sources'])} discovery sources failed "
                               "(see coverage.discovery_sources)")
        if result["deferred"]:
            limitations.append(f"{len(result['deferred'])} fresh candidates deferred by the analysis cap; "
                               "they remain eligible tomorrow while inside the window")
        chosen = []
        for c in result["chosen"]:
            rec = c.to_dict()
            rec["canonical_url"] = c.canonical_url
            chosen.append(rec)
        return {"search_results": chosen, "excluded": excluded, "limitations": limitations,
                "discovery_strategy": result["stats"],
                "discovery_sources": result["sources"],
                "seen_pending": [(c.canonical_url, "dropped: " + (c.skip_reason or "")[:80])
                                 for c in result["dropped"] if c.canonical_url and _permanent(c.skip_reason)]}

    def fetch_extract(state: DailyState) -> dict:
        fetched = []
        limitations = list(state.get("limitations", []))
        excluded = list(state.get("excluded", []))
        seen = list(state.get("seen_pending", []))
        window = _window(state)
        for r in state.get("search_results", []):
            if r.get("text"):
                # The channel supplied the full analysable text (paper abstract).
                out = {"url": r["url"], "final_url": r["url"], "status": "fetched",
                       "title": r["title"], "text": r["text"], "text_len": len(r["text"]),
                       "extraction_quality": "abstract_only", "content_type": "application/json"}
                from ..dedup import content_hash, extract_stable_ids
                out["content_hash"] = content_hash(r["text"])
                out["stable_ids"] = extract_stable_ids(r["text"])
            elif not tools.budget.check("fetch"):
                limitations.append("fetch budget exhausted")
                break
            else:
                out = tools.fetch_source(FetchIn(url=r["url"]))
                snippet = (r.get("snippet") or "").strip()
                weak = out.get("status") != "fetched" or out.get("extraction_quality") in ("none", "partial")
                if weak and len(snippet) >= SUMMARY_MIN_CHARS and len(snippet) > len(out.get("text") or ""):
                    # Page blocked (many trade publishers refuse automated
                    # readers) or thin: analyse the headline and the summary
                    # the feed or news index published, and say so.
                    text = f"{r['title']}\n\n{snippet}"
                    out.update(status="fetched", text=text, text_len=len(text),
                               extraction_quality="partial", title=out.get("title") or r["title"],
                               error=None, content_hash=None)
                    out.setdefault("date_notes", []).append(
                        "Analysed from the headline and published summary only; the full article "
                        "page was not accessible to the agent")
            doc = FetchedDocument.model_validate({**out, "url": r["canonical_url"]})
            classification = classify_source({**doc.model_dump(), "search_title": r.get("title")}, registry,
                                              channel_type=r.get("source_type"))
            # Date reconciliation: a date stated on the page wins; otherwise the
            # feed / API / URL / news-index date found at discovery stands.
            published, provenance = r.get("published_at"), r.get("date_provenance", "unknown")
            date_note = None
            if doc.published_at and doc.published_provenance in ("page_meta", "page_text"):
                page_day = local_date(doc.published_at, deps.tz)
                if page_day < window.start - timedelta(days=1):
                    reason = (f"page states publication date {page_day}, before this edition's window "
                              f"({window.start} to {window.end}); the listing date was {str(published)[:10]}")
                    excluded.append({"ref": doc.url, "reason": reason, "stage": "fetch"})
                    db.record_exclusion(doc.url, reason)
                    seen.append((doc.url, "old page"))
                    continue
                published, provenance = doc.published_at.isoformat(), doc.published_provenance
            elif doc.published_provenance == "conflicting":
                date_note = "Page shows conflicting dates; the listing date was used"
            sid = db.upsert_source(
                canonical_url=doc.url,
                domain=(doc.url.split("/")[2] if "://" in doc.url else None),
                source_type=classification["source_type"],
                title=doc.title or r.get("title"),
                published_at=published, published_provenance=provenance,
                content_hash=doc.content_hash, stable_ids=doc.stable_ids,
                extraction_quality=doc.extraction_quality, status=doc.status)
            rec = doc.model_dump(mode="json")
            rec["source_id"] = sid
            rec.update(classification)
            rec["fetch_url"] = r["url"]
            rec["published_at"], rec["published_provenance"] = published, provenance
            rec["listing_published_at"] = r.get("published_at")
            rec["discovery_channel"] = r.get("channel")
            rec["publisher"] = r.get("source_name")
            rec["relevance_triage"] = r.get("relevance")
            rec.update(window.as_dict())
            if date_note:
                rec["date_notes"] = list(rec.get("date_notes") or []) + [date_note]
            rec["planned_source_type"] = r.get("source_type")
            rec["search_title"] = r.get("title")  # listing title is more stable for dedup
            if doc.status != "fetched":
                db.record_exclusion(doc.url, f"fetch {doc.status}: {doc.error}")
                seen.append((doc.url, "fetch_failed"))
            elif doc.extraction_quality == "none":
                db.record_exclusion(doc.url, "no extractable text; not analyzed")
                rec["skip_reason"] = "unreadable"
                seen.append((doc.url, "unreadable"))
            fetched.append(rec)
        return {"fetched": fetched, "limitations": limitations, "excluded": excluded,
                "seen_pending": seen}

    def deduplicate(state: DailyState) -> dict:
        prior = db.prior_index(HISTORY_DAYS)
        kept, excluded = [], list(state.get("excluded", []))
        for rec in state.get("fetched", []):
            if rec.get("status") != "fetched" or rec.get("skip_reason"):
                continue
            decision = dedup_mod.decide(
                canonical_url=rec["url"], text_hash=rec.get("content_hash"),
                stable_ids=rec.get("stable_ids") or {},
                title=rec.get("search_title") or rec.get("title") or "",
                what_changed=None,  # title-level match at fetch time; content judgement comes later
                published_at=rec.get("published_at"), history_window_days=45,
                prior=prior)
            rec["dedup_kind"] = decision.kind
            rec["dedup_reason"] = decision.reason
            rec["cluster_id"] = decision.cluster_id
            rec["is_background"] = decision.is_background
            if decision.kind == "duplicate":
                excluded.append({"ref": rec["url"], "reason": decision.reason})
                db.record_exclusion(rec["url"], f"duplicate: {decision.reason}")
            else:
                kept.append(rec)
                # within-run dedup: later candidates are compared against earlier keeps
                prior.append({"title": rec.get("search_title") or rec.get("title") or "",
                              "what_changed": None,
                              "cluster_id": decision.cluster_id, "canonical_url": rec["url"],
                              "content_hash": rec.get("content_hash"),
                              "stable_ids": rec.get("stable_ids") or {},
                              "published_at": rec.get("published_at")})
        return {"kept": kept, "excluded": excluded}

    def analyze(state: DailyState) -> dict:
        developments, quarantined = [], []
        excluded = list(state.get("excluded", []))
        prior_titles = [r["title"] for r in db.prior_index(HISTORY_DAYS)]
        try:
            prompt_version, _ = load_prompt("analyze_development")
        except FileNotFoundError:
            prompt_version = "missing"
        for rec in state.get("kept", []):
            rec = prepare_source(rec)
            analysis, problems, audit_path = analyze_with_quality(
                llm, cfg, rec, date.fromisoformat(state["edition_date"]), prior_titles)
            if analysis is None:
                reason = "; ".join(problems)
                if unusable_source(rec):
                    db.record_exclusion(rec["url"], reason)
                    excluded.append({"ref": rec["url"], "reason": reason, "audit_path": audit_path})
                else:
                    quarantined.append({**_quarantine(db, rec, reason, cfg, state),
                                        "audit_path": audit_path})
                continue
            useful_background = (bool(analysis.evidence_refs)
                                 and bool(analysis.technical_explanation or analysis.what_changed)
                                 and analysis.scores["relevance"].score >= cfg.scoring.inclusion_threshold)
            if not analysis.is_new_development and not useful_background:
                db.record_exclusion(rec["url"], analysis.content_limitation
                                    or "model: not a new development")
                excluded.append({"ref": rec["url"],
                                 "reason": analysis.content_limitation or "not new"})
                continue
            final, breakdown = weighted_score(analysis, cfg.scoring.weights)
            injection_hits = scan_for_injection(rec.get("text", ""))
            pub = None
            if rec.get("published_at"):
                try:
                    pub = datetime.fromisoformat(rec["published_at"])
                except ValueError:
                    pub = None
            dev = Development(
                id=uuid.uuid4().hex[:12], title=analysis.title[:300],
                organization=analysis.organization, category=analysis.category,
                application=analysis.application,
                source_type=SourceType(rec.get("source_type", "news")),
                canonical_url=rec["url"], source_ids=[rec["source_id"]],
                source_details={**{k: rec.get(k) for k in ('fetch_url', 'final_url', 'classification_reason',
                    'source_origin', 'date_candidates', 'date_notes', 'modified_at', 'interpretation_policy',
                    'window_start', 'window_end', 'discovery_channel', 'publisher', 'listing_published_at',
                    'relevance_triage')},
                    'claim_support': {k: getattr(analysis, k) for k in ('novelty_quote', 'maturity_quote', 'benefit_quote')}},
                published_at=pub,
                published_provenance=rec.get("published_provenance", "unknown"),
                discovered_at=datetime.now(timezone.utc),
                edition_date=date.fromisoformat(state["edition_date"]),
                what_changed=analysis.what_changed,
                technical_explanation=analysis.technical_explanation,
                claimed_benefit=analysis.claimed_benefit,
                baseline_conditions=analysis.baseline_conditions,
                maturity=analysis.maturity, limitations=analysis.limitations,
                relevance_rationale=analysis.relevance_rationale,
                suggested_followup=analysis.suggested_followup,
                confidence_label=analysis.confidence_label,
                confidence_reasons=analysis.confidence_reasons,
                evidence_refs=analysis.evidence_refs,
                score_breakdown=breakdown, final_score=final,
                is_background=(not analysis.is_new_development or
                               assess_date(rec, date.fromisoformat(state["edition_date"]), cfg)[0] == "background"),
                injection_flag=analysis.injection_flag or bool(injection_hits),
                model_version=getattr(llm, "model", None) or cfg.llm.model,
                prompt_version=prompt_version, rubric_version=cfg.scoring.rubric_version,
                cluster_id=rec.get("cluster_id"))
            if not dev.cluster_id:
                dev.cluster_id = db.ensure_cluster(dev.canonical_url)
            developments.append(dev.model_dump(mode="json"))
        return {"developments": developments, "quarantined": quarantined,
                "excluded": excluded}

    def score_validate(state: DailyState) -> dict:
        threshold = cfg.scoring.inclusion_threshold
        excluded = list(state.get("excluded", []))
        groups = select_report_items(state.get("developments", []),
                                     date.fromisoformat(state["edition_date"]), cfg)
        selected = {d["id"] for items in groups.values() for d in items}
        for d in state.get("developments", []):
            if d["id"] not in selected:
                reason = (f"score {d.get('final_score')} below threshold {threshold}"
                          if (d.get("final_score") or 0) < threshold else "daily item cap")
                excluded.append({"ref": d["canonical_url"], "reason": reason})
                db.record_exclusion(d["canonical_url"], reason)
        return {"included_ids": [d["id"] for d in groups["recent"]],
                "background_ids": [d["id"] for d in groups["background"]],
                "unverified_ids": [d["id"] for d in groups["unverified"]], "excluded": excluded}

    def persist_edition(state: DailyState) -> dict:
        if state.get("already_done"):
            return {}
        developments = state.get("developments", [])
        errors = list(state.get("errors", []))
        for d in developments:
            try:
                db.insert_development(Development.model_validate(d))
            except Exception as exc:
                errors.append(f"insert {d['id']}: {exc}")
        included = [d for d in developments if d["id"] in set(state.get("included_ids", []))]
        sources = state.get("discovery_sources", [])
        if sources and all(src.get("status") == "failed" for src in sources):
            errors.append("every discovery source failed (network or proxy problem?)")
        if errors or (state.get("quarantined") and not developments):
            status = "failed"
        elif included:
            status = "research_complete"
        else:
            status = "no_updates"
        if status != "failed":
            # Remember every handled link so it is never re-analysed. Links
            # deferred by the analysis cap are not marked and stay eligible.
            pending = {url: outcome for url, outcome in state.get("seen_pending", [])}
            for rec in state.get("fetched", []):
                pending.setdefault(rec["url"], "analysed")
            db.mark_seen(list(pending.items()), edition_date=state["edition_date"])
        coverage = {
            "window": state.get("window", {}),
            "discovery_sources": state.get("discovery_sources", []),
            "queries": len(state.get("queries", [])),
            "search_results": len(state.get("search_results", [])),
            "fetched": len(state.get("fetched", [])),
            "fetch_attempts": len(state.get("fetched", [])),
            "fetched_successfully": sum(r.get("status") == "fetched" for r in state.get("fetched", [])),
            "kept_after_dedup": len(state.get("kept", [])),
            "included": len(included),
            "background": len(state.get("background_ids", [])),
            "unverified": len(state.get("unverified_ids", [])),
            "quality_version": QUALITY_VERSION,
            "discovery_strategy": state.get("discovery_strategy", {}),
            "excluded": len(state.get("excluded", [])),
            "quarantined": len(state.get("quarantined", [])),
            "limitations": state.get("limitations", []),
            "budgets": {"searches": tools.budget.searches, "fetches": tools.budget.fetches,
                        "budget_hits": tools.budget.exceeded},
            "retrospective": bool(state.get("retrospective")),
        }
        db.upsert_daily_edition(
            date.fromisoformat(state["edition_date"]), status, coverage,
            {"developments": len(included)}, {},
            {"model": getattr(llm, "model", None) or cfg.llm.model,
             "prompt": load_prompt("analyze_development")[0], "rubric": cfg.scoring.rubric_version})
        return {"status": status, "coverage_summary": coverage, "errors": errors}

    def render(state: DailyState) -> dict:
        from ..report.render import render_daily
        row = db.daily_edition(date.fromisoformat(state["edition_date"])) or {}
        paths = render_daily(cfg, db, date.fromisoformat(state["edition_date"]),
                             retrospective=bool(state.get("retrospective")))
        db.upsert_daily_edition(date.fromisoformat(state["edition_date"]),
                                state.get("status", "no_updates"),
                                state.get("coverage_summary", {}),
                                {"developments": len(state.get("included_ids", []))}, paths,
                                {"model": row.get("model_version") or cfg.llm.model,
                                 "prompt": row.get("prompt_version") or load_prompt("analyze_development")[0],
                                 "rubric": row.get("rubric_version") or cfg.scoring.rubric_version})
        return {"render_paths": paths}

    def delivery_gate(state: DailyState) -> dict:
        """Trusted send policy. The LLM cannot reach or alter this node."""
        from ..emailer.journal import OutboundMessage, prepare_send, validate_message, mark
        paths = state.get("render_paths", {})
        html_path = Path(paths.get("html", ""))
        if not html_path.exists():
            return {"send_state": "no-report", "errors": state.get("errors", []) + ["render missing"]}
        html = scrub_report(html_path.read_text(encoding="utf-8"))
        n = len(state.get("included_ids", []))
        subject = (f"[Tire Scout] Daily {state['edition_date']} - {n} update"
                   + ("s" if n != 1 else ""))
        if state.get("retrospective"):
            subject += " (retrospective catch-up)"
        msg = OutboundMessage(subject=subject, from_address=cfg.email.from_address,
                              to=list(cfg.recipients.daily_to), cc=[],
                              html=html, text=_plain_fallback(state), attachments=[])
        try:
            validate_message(cfg, msg)
        except ValueError as exc:
            return {"send_state": f"blocked:{exc}"}
        production = (state.get("mode") == "production" and cfg.email.live_sending
                      and cfg.email.daily_enabled)
        if production and state.get("status") == "failed":
            return {"send_state": "blocked:research failed"}
        if not production:
            decision = prepare_send(db, digest_type="daily", period=state["edition_date"],
                                    cfg=cfg, msg=msg, state="preview")
            if not decision.allowed:
                return {"send_state": f"skipped:{decision.reason}",
                        "send_identity": decision.identity}
            from ..emailer.preview import PreviewAdapter
            provider_id = PreviewAdapter(cfg).submit(msg)
            mark(db, decision.identity, "preview", provider="preview", provider_id=provider_id)
            return {"send_state": "preview", "send_identity": decision.identity}
        decision = prepare_send(db, digest_type="daily", period=state["edition_date"],
                                cfg=cfg, msg=msg)
        if not decision.allowed:
            return {"send_state": f"skipped:{decision.reason}", "send_identity": decision.identity}
        try:
            provider_id = build_adapter(cfg).submit(msg)
            mark(db, decision.identity, "submitted", provider=cfg.email.adapter,
                 provider_id=provider_id)
            return {"send_state": "submitted", "send_identity": decision.identity}
        except SubmissionError as exc:
            mark(db, decision.identity,
                 "uncertain" if exc.ambiguous else "failed_before_submit", error=str(exc))
            return {"send_state": "uncertain" if exc.ambiguous else f"failed:{exc}",
                    "send_identity": decision.identity}

    def reconcile(state: DailyState) -> dict:
        # Submission is not proof of delivery. Confirmation requires provider
        # evidence (Sent Items / Graph message lookup) via `tire-scout delivery-status`.
        return {}

    # ------------------------------------------------------------------ #
    g = StateGraph(DailyState)
    g.add_node("determine_edition_date", determine_edition_date)
    g.add_node("plan_queries", plan_queries)
    g.add_node("discover", discover)
    g.add_node("fetch_extract", fetch_extract)
    g.add_node("deduplicate", deduplicate)
    g.add_node("analyze", analyze)
    g.add_node("score_validate", score_validate)
    g.add_node("persist_edition", persist_edition)
    g.add_node("render", render)
    g.add_node("delivery_gate", delivery_gate)
    g.add_node("reconcile", reconcile)

    g.add_edge(START, "determine_edition_date")
    g.add_conditional_edges("determine_edition_date",
                            lambda s: "render" if s.get("already_done") else "plan_queries")
    g.add_edge("plan_queries", "discover")
    g.add_edge("discover", "fetch_extract")
    g.add_edge("fetch_extract", "deduplicate")
    g.add_conditional_edges("deduplicate",
                            lambda s: "analyze" if s.get("kept") else "persist_edition")
    g.add_edge("analyze", "score_validate")
    g.add_edge("score_validate", "persist_edition")
    g.add_edge("persist_edition", "render")
    g.add_edge("render", "delivery_gate")
    g.add_edge("delivery_gate", "reconcile")
    g.add_edge("reconcile", END)
    return g.compile(checkpointer=checkpointer)


# --------------------------------------------------------------------------- #
def _permanent(reason: str | None) -> bool:
    """Drop reasons that will not change on a later day. Temporary ones (an
    unresolved news link, a date after the edition) must not be remembered."""
    reason = reason or ""
    return not (reason.startswith(("already handled", "Google News link"))
                or "after the edition date" in reason)


# --------------------------------------------------------------------------- #
def _quarantine(db, rec: dict, reason: str, cfg, state) -> dict:
    dev = Development(
        id=uuid.uuid4().hex[:12], title=(rec.get("title") or "unparseable source")[:300],
        category=cfg.topics[0].name if cfg.topics else "uncategorized",
        source_type=SourceType(rec.get("source_type", "news")),
        canonical_url=rec["url"], source_ids=[rec.get("source_id", "")],
        discovered_at=datetime.now(timezone.utc),
        edition_date=date.fromisoformat(state["edition_date"]),
        maturity=Maturity.speculation, confidence_label=ConfidenceLabel.low,
        status="quarantined", quarantine_reason=reason)
    try:
        db.insert_development(dev)
        db.record_exclusion(rec["url"], f"quarantined: {reason}")
    except Exception:
        pass
    return {"ref": rec["url"], "reason": reason}


def _plain_fallback(state: DailyState) -> str:
    lines = [f"Tire Scout daily edition {state.get('edition_date')}"]
    for d in state.get("developments", []):
        if d["id"] in set(state.get("included_ids", [])):
            lines.append(f"- {d['title']} (score {d.get('final_score')}) {d['canonical_url']}")
    if not state.get("included_ids"):
        lines.append("No qualifying recent, date-verified developments in this edition.")
    if state.get("status") == "failed":
        lines.append("Research failed; this edition cannot establish whether qualifying updates exist.")
    quarantines = state.get("coverage_summary", {}).get("quarantined", len(state.get("quarantined", [])))
    if quarantines:
        lines.append(f"Evidence validation failed for {quarantines} sources; coverage is limited.")
    return "\n".join(lines)
