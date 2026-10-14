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
from datetime import date, datetime, timezone
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
from ..sources import SourceRegistry, classify_source, discovery_background_reason
from .states import DailyState

HISTORY_DAYS = 60


@dataclass
class Deps:
    cfg: object
    db: object
    llm: LLMClient
    tools: AgentTools
    tz: object


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def build_daily_graph(deps: Deps, checkpointer=None):
    cfg, db, llm, tools = deps.cfg, deps.db, deps.llm, deps.tools
    registry = SourceRegistry(cfg.source_registry_path)

    # ------------------------------------------------------------------ #
    def determine_edition_date(state: DailyState) -> dict:
        if not state.get("edition_date"):
            d = edition_date_for(now_local(deps.tz))
            state = {**state, "edition_date": d.isoformat()}
        base = {"edition_date": state["edition_date"], "errors": [], "limitations": list(registry.warnings),
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
        prior_titles = [r["title"] for r in db.prior_index(HISTORY_DAYS)]
        topics = [{"name": t.name, "priority": t.priority, "keywords": t.keywords}
                  for t in cfg.topics]
        max_q = min(8, cfg.budgets.max_search_calls)
        try:
            plan = llm.plan_queries(topics=topics, recency_windows=cfg.search.recency_windows,
                                    prior_titles=prior_titles, max_queries=max_q)
            queries = [q.model_dump() for q in plan.queries][:max_q]
        except Exception as exc:
            # trusted deterministic fallback: research must not silently die
            queries = []
            for t in cfg.topics:
                if t.priority == "high" and t.keywords:
                    queries.append({"query": f"{t.keywords[0]} tire",
                                    "source_type": "news", "max_results": 6})
            return {"queries": queries[:max_q],
                    "limitations": state.get("limitations", []) +
                    [f"LLM planning failed ({exc}); used deterministic fallback queries"]}
        return {"queries": queries}

    def discover(state: DailyState) -> dict:
        from itertools import zip_longest
        results, seen, batches = [], set(), []
        excluded = list(state.get("excluded", []))
        strategy = {"preferred_queries": 0, "open_fallback_queries": 0}
        cursor_overlap = cfg.search.retrieval_overlap_days
        for q in state.get("queries", []):
            if not tools.budget.check("search"):
                break
            stype = q.get("source_type", "news")
            window = cfg.search.recency_windows.get(stype, 30) + cursor_overlap
            domains = registry.preferred(stype) if cfg.search.provider != "fixture" else []
            hits = tools.public_search(SearchIn(query=q["query"],
                                                max_results=q.get("max_results", 8),
                                                recency_days=window, include_domains=domains))
            strategy["preferred_queries"] += bool(domains)
            if domains and not hits and tools.budget.check("search"):
                hits = tools.public_search(SearchIn(query=q["query"],
                                                    max_results=q.get("max_results", 8), recency_days=window))
                strategy["open_fallback_queries"] += 1
            batches.append((q, hits))
        # Interleave query results so one topic cannot consume the fetch budget.
        for rank in zip_longest(*(hits for _, hits in batches)):
            for (q, _), h in zip(batches, rank):
                if h is None:
                    continue
                if "error" in h:
                    continue
                canon = canonicalize_url(h["url"])
                if canon in seen:
                    continue
                seen.add(canon)
                h["canonical_url"] = canon
                h["planned_source_type"] = q.get("source_type", "news")
                if cfg.search.provider != "fixture":
                    reason = discovery_background_reason(h)
                    if reason:
                        h["discovery_skip_reason"] = reason
                        excluded.append({"ref": canon, "reason": reason})
                        db.record_exclusion(canon, reason)
                results.append(h)
        return {"search_results": results, "excluded": excluded, "discovery_strategy": strategy}

    def fetch_extract(state: DailyState) -> dict:
        fetched = []
        limitations = list(state.get("limitations", []))
        for r in state.get("search_results", []):
            if r.get("discovery_skip_reason"):
                continue
            if not tools.budget.check("fetch"):
                limitations.append("fetch budget exhausted")
                break
            out = tools.fetch_source(FetchIn(url=r["url"]))
            doc = FetchedDocument.model_validate({**out, "url": r["canonical_url"]})
            classification = classify_source({**doc.model_dump(), "search_title": r.get("title")}, registry)
            provenance = doc.published_provenance
            if provenance == "unknown" and r.get("published_at"):
                provenance = "provider"
            sid = db.upsert_source(
                canonical_url=doc.url,
                domain=(doc.url.split("/")[2] if "://" in doc.url else None),
                source_type=classification["source_type"],
                title=doc.title or r.get("title"),
                published_at=doc.published_at.isoformat() if doc.published_at
                else (r.get("published_at") or None),
                published_provenance=provenance,
                content_hash=doc.content_hash, stable_ids=doc.stable_ids,
                extraction_quality=doc.extraction_quality, status=doc.status)
            rec = doc.model_dump(mode="json")
            rec["source_id"] = sid
            rec.update(classification)
            rec["fetch_url"] = r["url"]
            rec["provider_published_at"] = r.get("published_at")
            if not doc.published_at and provenance == "provider":
                rec["published_at"], rec["published_provenance"] = r["published_at"], "provider"
            rec["planned_source_type"] = r.get("planned_source_type")
            rec["search_title"] = r.get("title")  # provider title is more stable for dedup
            if doc.status != "fetched":
                db.record_exclusion(doc.url, f"fetch {doc.status}: {doc.error}")
            elif doc.extraction_quality == "none":
                db.record_exclusion(doc.url, "no extractable text; not analyzed")
                rec["skip_reason"] = "unreadable"
            fetched.append(rec)
        return {"fetched": fetched, "limitations": limitations}

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
                    'source_origin', 'date_candidates', 'date_notes', 'modified_at', 'interpretation_policy')},
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
        if errors or (state.get("quarantined") and not developments):
            status = "failed"
        elif included:
            status = "research_complete"
        else:
            status = "no_updates"
        coverage = {
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
